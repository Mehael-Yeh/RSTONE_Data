"""Exact synthetic, fixed publisher-owned conversion proof and independent grant."""
import copy
import importlib
import json
from pathlib import Path
import pytest
from rstone_data import quality_gate as q, recipe_codec as codec
from rstone_data.protocol import DataError, canonical_json, sha256
from test_structured_publication import snapshot, PATH
from test_quality_parent_review import RecordingSigner
from test_identity_migration_security import artifacts
from test_release_quality import install_legacy


def api(): return importlib.import_module("rstone_data.catalog_revisions_v2")


def legacy(snap):
    old = copy.deepcopy(snap); old.files[PATH] = b"synthetic legacy complete original facts\n"
    return old


def inventory(files):
    return {p: {"sha256": sha256(raw), "size": len(raw)} for p, raw in files.items()}


def proof(before, after, baseline):
    records = []
    for path, raw in after.files.items():
        if not path.startswith("产品配方/"): continue
        records.append({"pathSha256": sha256(path.encode()), "legacySha256": sha256(before.files[path]),
                        "legacySize": len(before.files[path]), "typedSha256": sha256(raw), "typedSize": len(raw),
                        "modelSha256": sha256(codec.header(codec.parse(raw)).encode("ascii"))})
    return {"schema": "rstone-recipe-migration-witness/1", "sourceCatalogApprovalFingerprint": "c"*64,
            "baselineManifestSha256": baseline, "beforeInventorySha256": sha256(canonical_json(inventory(before.files))),
            "approvedLegacyInventorySha256": sha256(canonical_json(inventory(before.files))),
            "typedInventorySha256": sha256(canonical_json(inventory(after.files))),
            "modelFactsSha256": sha256(canonical_json(sorted([
                {"pathSha256": r["pathSha256"], "modelSha256": r["modelSha256"]} for r in records],
                key=lambda r: r["pathSha256"]))),
            "protocol": codec.SCHEMA, "renderer": codec.RENDERER, "records": records}


def config(monkeypatch, tmp_path, witness=None, approvals=(), prior=True):
    v2 = api(); root = tmp_path/"publisher-config"; root.mkdir(exist_ok=True)
    digests = []
    if witness is not None:
        raw = canonical_json(witness); digest = sha256(raw); digests = [digest]
        target = root/"migration_witnesses"/(digest+".json"); target.parent.mkdir(exist_ok=True); target.write_bytes(raw)
    (root/"recipe_migration_witnesses.json").write_bytes(canonical_json({"schema": v2.WITNESSES_SCHEMA, "witnesses": digests}))
    (root/"catalog_revisions_v2.json").write_bytes(canonical_json({"schema": v2.SCHEMA, "approvals": list(approvals)}))
    (root/"catalog_revisions.json").write_bytes(canonical_json({"schema": "rstone-catalog-revisions/1", "approvals": ["c"*64] if prior else []}))
    monkeypatch.setattr(v2, "config_root", lambda: root)
    monkeypatch.setattr(q, "default_catalog_revisions", lambda: root/"catalog_revisions.json")
    return root


def test_protocol_conversion_needs_proof_and_a_new_independent_grant(store, signing, monkeypatch, tmp_path):
    v2 = api(); after = snapshot(); before = legacy(after)
    install_legacy(store, signing[0], before)
    baseline = sha256((store.public/"manifests/000001.json").read_bytes())
    config(monkeypatch, tmp_path)
    signer = RecordingSigner(signing[0]); original = artifacts(store)
    with pytest.raises(DataError, match="quality gate"): store.prepare(after, signer, public_scope_approved=True)
    assert original == artifacts(store) and signer.calls == 0
    config(monkeypatch, tmp_path, proof(before, after, baseline))
    with pytest.raises(DataError, match="quality gate"): store.prepare(after, signer, public_scope_approved=True)
    assert original == artifacts(store) and signer.calls == 0
    b = q.snapshot_corpus(before); b.manifest_sha256 = baseline
    a = q.snapshot_corpus(after)
    findings = v2.migration_findings(b, a) + q.check_identity(a, b)
    proposed = v2.proposal(b, a, findings)
    assert proposed and proposed["approved"] is False and proposed["schema"] == v2.SCHEMA
    config(monkeypatch, tmp_path, proof(before, after, baseline), [proposed["fingerprint"]])
    result = store.prepare(after, signer, public_scope_approved=True)
    assert result["qualityGate"]["catalogRevision"]["fingerprint"] == proposed["fingerprint"]
    store.package(2); store.promote()
    assert store.read()["schemaVersion"] == 2


@pytest.mark.parametrize("change", ["before", "after", "manifest", "model", "missing-record", "extra-record", "legacy-size", "model-hash", "scope", "prior-grant"])
def test_witness_or_approval_is_never_reusable_for_other_bytes(store, signing, monkeypatch, tmp_path, change):
    v2 = api(); after = snapshot(); before = legacy(after)
    install_legacy(store, signing[0], before)
    baseline = sha256((store.public/"manifests/000001.json").read_bytes())
    w = proof(before, after, baseline); config(monkeypatch, tmp_path, w)
    b = q.snapshot_corpus(before); b.manifest_sha256 = baseline; a = q.snapshot_corpus(after)
    f = v2.migration_findings(b,a)+q.check_identity(a,b)
    proposed = v2.proposal(b,a,f); assert proposed
    grant = proposed["fingerprint"]
    if change == "after": after.files[next(p for p in after.files if p.startswith("产品TDS/"))] += b"drift"
    elif change == "before": w["beforeInventorySha256"] = "0"*64
    elif change == "manifest": w["baselineManifestSha256"] = "0"*64
    elif change == "model":
        m = codec.parse(after.files[PATH]); m["content"].reverse(); after.files[PATH] = codec.emit(m)
    elif change == "missing-record": w["records"].clear()
    elif change == "extra-record": w["records"].append(copy.deepcopy(w["records"][0]))
    elif change == "legacy-size": w["records"][0]["legacySize"] += 1
    elif change == "model-hash": w["records"][0]["modelSha256"] = "0"*64
    elif change == "scope": grant = sha256(canonical_json({"not": grant}))
    config(monkeypatch, tmp_path, w, [grant], prior=change!="prior-grant")
    signer = RecordingSigner(signing[0]); original = artifacts(store)
    with pytest.raises(DataError, match="quality gate|configuration"): store.prepare(after, signer, public_scope_approved=True)
    assert signer.calls == 0 and original == artifacts(store)


def test_installed_new_policies_are_fixed_strict_and_loaded_exactly():
    from rstone_data.protocol import decode_json, SHA256
    v2 = api(); root = Path(v2.__file__).resolve().parents[2]/"config"
    assert v2.config_root() == root
    installed = {}
    for filename, schema, key in [
        ("catalog_revisions.json", "rstone-catalog-revisions/1", "approvals"),
        ("catalog_revisions_v2.json", v2.SCHEMA, "approvals"),
        ("recipe_migration_witnesses.json", v2.WITNESSES_SCHEMA, "witnesses"),
    ]:
        data = decode_json((root / filename).read_bytes())
        assert type(data) is dict and set(data) == {"schema", key}
        assert data["schema"] == schema and type(data[key]) is list
        assert all(type(value) is str and SHA256.fullmatch(value) for value in data[key])
        assert len(set(data[key])) == len(data[key])
        installed[filename] = frozenset(data[key])
    assert q.load_catalog_revisions() == installed["catalog_revisions.json"]
    assert v2.load_approvals() == installed["catalog_revisions_v2.json"]
    witnesses = v2.load_witnesses()
    assert frozenset(digest for digest, _ in witnesses) == installed["recipe_migration_witnesses.json"]
    assert len(witnesses) == len(installed["recipe_migration_witnesses.json"])
    for digest, witness in witnesses:
        raw = (root / "migration_witnesses" / (digest + ".json")).read_bytes()
        assert sha256(raw) == digest
        assert v2.validate_witness(decode_json(raw)) == witness
        assert witness["renderer"] == codec.VERSION_PAIRS[witness["protocol"]]
        assert witness["sourceCatalogApprovalFingerprint"] in q.load_catalog_revisions()
        assert digest not in v2.load_approvals()
