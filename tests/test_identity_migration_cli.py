"""Run the actual CLI with commit-pinned synthetic Git blobs and isolated publisher policy."""
import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from conftest import commit_all
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

from rstone_data.build import deletion_approval, inventory
from rstone_data.protocol import canonical_json
from rstone_data.sources import collect
from test_identity_migration_security import artifacts
from test_identity_migrations import SCHEMA, approval_for, product


def test_cli_trusts_only_installed_publisher_policy_and_committed_blobs(
        store, sources, signing, tmp_path):
    rstone, tds = sources
    directory = rstone / "产品列表"
    for suffix in ("2B", "6B", "8B"):
        (directory / f"RD9401-{suffix}.md").write_bytes(product())
    for suffix in ("2B", "8B"):
        (directory / f"RS9401-{suffix}.md").write_bytes(product(f"RD9401-{suffix}"))
    commit_all(rstone)
    before = collect(rstone, tds)
    store.prepare(before, signing[0], public_scope_approved=True)
    store.promote()
    for suffix in ("2B", "6B", "8B"):
        (directory / f"RD9401-{suffix}.md").unlink()
        (directory / f"RS9401-{suffix}.md").write_bytes(product(f"RD9401-{suffix}"))
    commit_all(rstone)
    after = collect(rstone, tds)
    tokens = [approval_for(before, after, f"产品列表/RD9401-{suffix}.md",
                          f"产品列表/RS9401-{suffix}.md") for suffix in ("2B", "6B", "8B")]
    policy = {"schema": SCHEMA, "approvals": tokens}
    project = Path(__file__).resolve().parents[1]
    publisher = tmp_path / "reviewed-publisher"
    for subdirectory in ("scripts", "config"):
        shutil.copytree(project / subdirectory, publisher / subdirectory)
    trusted = tmp_path / "keys.json"
    trusted.write_bytes(canonical_json({"keys": signing[1]}))
    report = tmp_path / "cli-report.json"
    env = os.environ.copy()
    seed = signing[0].private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    env.update(DATA_SIGNING_PRIVATE_KEY=base64.b64encode(seed).decode(),
               PYTHONDONTWRITEBYTECODE="1", DATA_IDENTITY_APPROVALS=json.dumps(policy))
    command = [sys.executable, "-B", str(publisher / "scripts/data.py"), "prepare",
               "--store", str(store.root), "--rstone", str(rstone), "--tds", str(tds),
               "--public-scope-approved", "--key-id", signing[0].key_id,
               "--keys", str(trusted), "--deletion-approval",
               deletion_approval(store.read(), inventory(after.files)), "--report", str(report)]
    # An upstream control file and an invented environment variable cannot grant
    # authority. The policy path is fixed by the reviewed publisher installation.
    (rstone / "config").mkdir()
    (rstone / "config/identity_migrations.json").write_text(json.dumps(policy))
    commit_all(rstone)
    previous = artifacts(store)
    rejected = subprocess.run(command, env=env, capture_output=True, text=True)
    assert rejected.returncode == 1, rejected.stderr
    assert "quality gate" in json.loads(report.read_text())["error"]
    assert artifacts(store) == previous
    (publisher / "config/identity_migrations.json").write_text(json.dumps(policy))
    # A genuinely committed missing target is still denied by the real CLI.
    target = directory / "RS9401-6B.md"
    target.unlink()
    commit_all(rstone)
    rejected = subprocess.run(command, env=env, capture_output=True, text=True)
    assert rejected.returncode == 1, rejected.stderr
    assert artifacts(store) == previous
    target.write_bytes(after.files["产品列表/RS9401-6B.md"])
    commit_all(rstone)
    # Mutable working-tree bytes are not what the publisher signs.
    target.write_bytes(b"uncommitted invalid payload\n")
    accepted = subprocess.run(command, env=env, capture_output=True, text=True)
    assert accepted.returncode == 0, accepted.stderr
    result = json.loads(report.read_text())
    assert result["qualityGate"]["summary"]["ok"] and result["status"] == "pending"
    committed = collect(rstone, tds)
    assert result["sourceCommits"] == committed.commits
    assert store._snapshot(store.read(store.pending()["pointer"])).files == committed.files
