"""Only synthetic identities; exercise the real signed publication boundary."""
import copy
import hashlib
import json

import pytest

from rstone_data import quality_gate as q
from rstone_data.build import deletion_approval, inventory
from rstone_data.protocol import DataError
from rstone_data.sources import Snapshot
from test_quality_parent_review import RecordingSigner
from test_release_quality import install_legacy


SCHEMA = "rstone-identity-migrations/1"


def digest(value):
    """Independent implementation of the documented approval wire format."""
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")) + "\n").encode()
    return hashlib.sha256(raw).hexdigest()


def approval_for(before, after, source, target):
    return digest({
        "schema": SCHEMA,
        "baselineSha256": digest({p: hashlib.sha256(raw).hexdigest()
                                   for p, raw in before.files.items()}),
        "source": {"path": source, "sha256": hashlib.sha256(before.files[source]).hexdigest()},
        "target": {"path": target, "sha256": hashlib.sha256(after.files[target]).hexdigest()},
    })


def product(alias=None):
    field = "" if alias is None else f"实验牌号: {alias}\n"
    return ("---\ntags: [油性, UV]\n固含: 75\n" + field + "---\n").encode()


def migration_pair():
    files = {f"产品列表/RD9401-{suffix}.md": product()
             for suffix in ("2B", "6B", "8B")}
    files.update({f"产品列表/RS9401-{suffix}.md": product(f"RD9401-{suffix}")
                  for suffix in ("2B", "8B")})
    files["产品列表/RS9900.md"] = product()
    before = Snapshot(files, {"Mehael-Yeh/Rstone": "a" * 40,
                              "Mehael-Yeh/RstoneTDS": "b" * 40})
    after = copy.deepcopy(before)
    for suffix in ("2B", "6B", "8B"):
        del after.files[f"产品列表/RD9401-{suffix}.md"]
        after.files[f"产品列表/RS9401-{suffix}.md"] = product(f"RD9401-{suffix}")
    return before, after


def approve(monkeypatch, tmp_path, before, after, suffixes=("2B", "6B", "8B")):
    policy = tmp_path / "publisher-policy.json"
    policy.write_text(json.dumps({"schema": SCHEMA, "approvals": [
        approval_for(before, after, f"产品列表/RD9401-{suffix}.md",
                     f"产品列表/RS9401-{suffix}.md") for suffix in suffixes
    ]}))
    # Isolate the reviewed publisher's policy file, not the gate or its findings.
    # Upstream Markdown has no API for selecting this path.
    monkeypatch.setattr(q, "default_identity_migrations", lambda: policy, raising=False)
    return policy


def test_exact_approved_mixed_rd_rs_migration_survives_all_exits(
        store, signing, monkeypatch, tmp_path):
    before, after = migration_pair()
    install_legacy(store, signing[0], before)
    approve(monkeypatch, tmp_path, before, after)
    signer = RecordingSigner(signing[0])
    deletion = deletion_approval(store.read(), inventory(after.files))
    result = store.prepare(after, signer, public_scope_approved=True, approval=deletion)
    assert result["qualityGate"]["summary"]["ok"]
    assert signer.calls == 1
    assert store.prepare(None, signer, public_scope_approved=True)["resumed"]
    assert signer.calls == 1
    assert (store.public / store.package(result["sequence"])).is_file()
    assert store.promote(keep_pending=True)["status"] == "published"
    assert store.complete(lambda pointer: store.read(pointer))["status"] == "published"
    assert store.pending() is None
    assert set(store._snapshot(store.read()).files) == set(after.files)


def test_different_number_needs_its_own_exact_owner_approval(
        store, signing, monkeypatch, tmp_path):
    before = Snapshot({"产品列表/RD9402-5.md": product(),
                       "产品列表/RS9900.md": product()}, migration_pair()[0].commits)
    after = copy.deepcopy(before)
    del after.files["产品列表/RD9402-5.md"]
    after.files["产品列表/RS9502.md"] = product("RD9402-5")
    install_legacy(store, signing[0], before)
    policy = approve(monkeypatch, tmp_path, before, after, suffixes=())
    policy.write_text(json.dumps({"schema": SCHEMA, "approvals": [
        approval_for(before, after, "产品列表/RD9402-5.md", "产品列表/RS9502.md")]}))
    result = store.prepare(after, signing[0], public_scope_approved=True,
                           approval=deletion_approval(store.read(), inventory(after.files)))
    assert result["qualityGate"]["summary"]["ok"]
    assert (store.public / store.package(result["sequence"])).is_file()


def test_padding_a_family_does_not_hide_a_lost_exact_suffix(
        store, signing, monkeypatch, tmp_path):
    before, _ = migration_pair()
    after = copy.deepcopy(before)
    del after.files["产品列表/RD9401-2B.md"]
    after.files["产品列表/RD9401-9B.md"] = product()
    install_legacy(store, signing[0], before)
    approve(monkeypatch, tmp_path, before, after, suffixes=())
    signer = RecordingSigner(signing[0])
    with pytest.raises(DataError, match="quality gate"):
        store.prepare(after, signer, public_scope_approved=True,
                      approval=deletion_approval(store.read(), inventory(after.files)))
    assert signer.calls == 0
    assert store.pending() is None


@pytest.mark.parametrize("target", ["RS9401-2B", "RS9501-2B", "RS9401"])
def test_single_source_alias_is_not_its_own_migration_authority(
        store, signing, monkeypatch, tmp_path, target):
    before = Snapshot({"产品列表/RD9401-2B.md": product(),
                       "产品列表/RS9900.md": product()}, migration_pair()[0].commits)
    after = copy.deepcopy(before)
    del after.files["产品列表/RD9401-2B.md"]
    after.files[f"产品列表/{target}.md"] = product("RD9401-2B")
    install_legacy(store, signing[0], before)
    approve(monkeypatch, tmp_path, before, after, suffixes=())
    signer = RecordingSigner(signing[0])
    with pytest.raises(DataError, match="quality gate"):
        store.prepare(after, signer, public_scope_approved=True,
                      approval=deletion_approval(store.read(), inventory(after.files)))
    assert signer.calls == 0
    assert store.pending() is None
