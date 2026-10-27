"""Publication-boundary tests: real prepare/sign/store/package, no gate mocks."""
import json
from pathlib import Path

import pytest

from rstone_data.protocol import DataError
from rstone_data.sources import Snapshot


def clean_snapshot():
    return Snapshot({
        "产品列表/RS100.md": "---\ntags: [水性, UV, 3官]\n官能度: 3\n固含: 77\n---\n".encode(),
        "产品应用/方案.md": "---\ntags: [PC]\n底漆: '[[RS100]]'\n---\n".encode(),
        "产品配方/配方.md": b"| RS100 | 100 |\n",
        "产品TDS/RS100.TDS.md": b"# RS100\n",
    }, {"Mehael-Yeh/Rstone": "a" * 40, "Mehael-Yeh/RstoneTDS": "b" * 40})


def test_prepare_blocks_conflict_before_any_distributable(store, signing):
    snapshot = clean_snapshot()
    snapshot.files["产品列表/RS100.md"] = snapshot.files["产品列表/RS100.md"].replace(
        "官能度: 3".encode(), "官能度: 987654".encode())
    with pytest.raises(DataError, match="quality gate") as error:
        store.prepare(snapshot, signing[0], public_scope_approved=True)
    assert "987654" not in str(error.value)
    assert not list(store.public.rglob("*.zip"))
    assert not list(store.public.rglob("*.sig"))
    assert not (store.public / "objects").exists()
    assert store.pending() is None


def test_clean_release_builds_package(store, signing):
    result = store.prepare(clean_snapshot(), signing[0], public_scope_approved=True)
    assert result["qualityGate"]["summary"]["ok"]
    assert (store.public / store.package(result["sequence"])).is_file()


@pytest.mark.parametrize("raw", [
    "---\ntags: [水性, UV]\n---\n",
    "---\ntags: [水性, UV]\n固含: 77\n技术源: 'None'\n---\n",
    "---\ntags: [水性, UV]\n固含: 77\n---\n##### 特别说明\n- 仅内部参考 SENSITIVE_MARKER。\n",
    "---\ntags: [水性, UV]\n固含: 77\n---\n[[RS999999]]\n",
    "---\ntags: [水性, UV]\n固含: 77\n---\n报价 987654 元/kg\n",
    "---\ntags: [水性, UV, 999官]\n官能度: 999\n---\n",
])
def test_bad_payload_cannot_build(store, signing, raw):
    snapshot = clean_snapshot()
    snapshot.files["产品列表/RS100.md"] = raw.encode()
    with pytest.raises(DataError) as error:
        store.prepare(snapshot, signing[0], public_scope_approved=True)
    assert "SENSITIVE_MARKER" not in str(error.value)
    assert "987654" not in str(error.value)
    assert not (store.public / "objects").exists()
    assert not list(store.public.rglob("*.sig"))
    assert not list(store.public.rglob("*.zip"))


def test_new_application_frontmatter_link_blocks(store, signing):
    snapshot = clean_snapshot()
    snapshot.files["产品应用/方案.md"] = snapshot.files["产品应用/方案.md"].replace(b"RS100", b"RS999999")
    with pytest.raises(DataError, match="quality gate"):
        store.prepare(snapshot, signing[0], public_scope_approved=True)


def install_legacy(store, signer, snapshot, sequence=1, pending=False):
    """Construct a real signed pre-gate release; no validation function is mocked."""
    from datetime import datetime, timezone
    from rstone_data.build import make_manifest
    from rstone_data.protocol import canonical_json, sha256
    from rstone_data.release import pointer_for
    from rstone_data.storage import atomic_write
    manifest = make_manifest(snapshot, sequence, "2026.01.01.001", "2026-01-01T00:00:00Z", signer.key_id)
    raw = canonical_json(manifest)
    pointer = pointer_for(manifest, raw)
    for data in snapshot.files.values():
        atomic_write(store.public / "objects" / sha256(data), data)
    atomic_write(store.public / pointer["manifestPath"], raw)
    atomic_write(store.public / pointer["signaturePath"], signer.sign(raw))
    if pending:
        atomic_write(store.state / "pending.json", canonical_json({"pointer": pointer,
                     "previousLatestSha256": None, "summary": {"sequence": sequence}}))
    else:
        atomic_write(store.public / "latest.json", canonical_json(pointer))
    return pointer


@pytest.mark.parametrize("operation", ["resume", "promote", "package", "rollback"])
def test_legacy_signed_defect_cannot_escape(store, signing, operation):
    snapshot = clean_snapshot()
    snapshot.files["产品列表/RS100.md"] = snapshot.files["产品列表/RS100.md"].replace(
        "官能度: 3".encode(), "官能度: 6".encode())
    install_legacy(store, signing[0], snapshot, pending=operation in ("resume", "promote"))
    before = store.latest_bytes()
    with pytest.raises(DataError, match="quality gate"):
        if operation == "resume":
            store.prepare(None, signing[0], public_scope_approved=True)
        elif operation == "promote":
            store.promote()
        elif operation == "package":
            store.package(1)
        else:
            store.rollback(1, signing[0], public_scope_approved=True)
    assert store.latest_bytes() == before
    assert not list(store.public.rglob("*.zip"))


def test_historical_warning_does_not_allow_new_link(store, signing):
    old = clean_snapshot()
    old.files["产品配方/配方.md"] += b"[[RS999999]]\n"
    install_legacy(store, signing[0], old)
    candidate = clean_snapshot()
    candidate.files["产品配方/配方.md"] += b"[[RS999999]]\n[[RS888888]]\n"
    with pytest.raises(DataError, match="quality gate"):
        store.prepare(candidate, signing[0], public_scope_approved=True)
    candidate.files["产品配方/配方.md"] = old.files["产品配方/配方.md"]
    result = store.prepare(candidate, signing[0], public_scope_approved=True)
    assert result["qualityGate"]["summary"]["ok"]
    historical = [w for w in result["qualityGate"]["warnings"]
                  if w["category"] == "reference-broken-preexisting"]
    assert len(historical) == 1  # reported once, never double-counted


def test_complete_refuses_legacy_pending_defect(store, signing):
    snapshot = clean_snapshot()
    snapshot.files["产品列表/RS100.md"] = snapshot.files["产品列表/RS100.md"].replace(
        "官能度: 3".encode(), "官能度: 6".encode())
    install_legacy(store, signing[0], snapshot, pending=True)
    with pytest.raises(DataError, match="quality gate"):
        store.complete(lambda pointer: None)
    assert store.pending() is not None


def test_cli_prepare_is_the_blocking_publication_entry(sources, signing, tmp_path, monkeypatch):
    """The command the workflow actually runs must fail before writing pending."""
    import base64
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
    from rstone_data.cli import main
    from rstone_data.protocol import canonical_json
    rstone, tds = sources
    (rstone / "产品列表/RS100.md").write_bytes(
        "---\ntags: [水性, UV, 3官]\n官能度: 987654\n固含: 77\n---\n".encode())
    from conftest import commit_all
    commit_all(rstone)
    seed = signing[0].private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    monkeypatch.setenv("DATA_SIGNING_PRIVATE_KEY", base64.b64encode(seed).decode())
    monkeypatch.setenv("PUBLIC_DATA_APPROVED", "true")
    store = tmp_path / "cli-store"
    report = tmp_path / "prepare.json"
    keys_file = tmp_path / "trusted_keys.json"
    keys_file.write_bytes(canonical_json({"keys": signing[1]}))
    project = Path(__file__).resolve().parents[1]
    code = main(["prepare", "--store", str(store), "--rstone", str(rstone), "--tds", str(tds),
                 "--key-id", signing[0].key_id, "--config", str(project / "config/release.json"),
                 "--keys", str(keys_file), "--report", str(report)])
    assert code == 1
    payload = json.loads(report.read_text())
    assert payload["status"] == "failed" and "987654" not in json.dumps(payload)
    assert not (store / "state/pending.json").exists()
    assert not list((store / "public").rglob("*.sig")) if (store / "public").exists() else True
