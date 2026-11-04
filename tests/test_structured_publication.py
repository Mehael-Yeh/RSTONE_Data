"""Synthetic real Store paths: no production gate mocking."""
import copy
import json
import pytest
from rstone_data import recipe_codec as codec
from rstone_data.protocol import DataError
from rstone_data.sources import Snapshot
from test_quality_parent_review import RecordingSigner
from test_identity_migration_security import artifacts
from test_release_quality import install_legacy

OWNER = "RS990001-Q5"
TITLE = OWNER + "-底"
PATH = "产品配方/" + TITLE + ".md"


def model():
    return {"schema": codec.SCHEMA, "recipeId": "SYNTH-STABLE-001", "title": TITLE,
            "classification": "main", "ownerProduct": OWNER, "associatedProducts": [OWNER],
            "columns": codec.COLUMNS[0][:],
            "rows": [["[[" + OWNER + "]]", "40", "80%", "合成厂商", ""],
                     ["水", "10-12", "", "", "原文"], ["固化剂", "按NCO/OH=1.1另加", "", "", ""]],
            "preTable": ["投料前说明。"], "content": [
                {"kind": "note", "text": "百分比按主剂小计，未含另加部分。"},
                {"kind": "process", "label": "工艺分支1", "steps": ["60℃/30min", "先搅拌再加水。"]},
                {"kind": "note", "text": "尾段说明。"}]}


def snapshot(m=None):
    return Snapshot({"产品列表/" + OWNER + ".md": "---\ntags: [水性, UV]\n固含: 77\n---\n".encode(),
                     "产品应用/合成涂层.md": "---\ntags: [PC]\n---\n".encode(),
                     "产品TDS/" + OWNER + ".TDS.md": b"# synthetic TDS\n",
                     PATH: codec.emit(m or model())},
                    {"Mehael-Yeh/Rstone": "a" * 40, "Mehael-Yeh/RstoneTDS": "b" * 40})


def test_new_prepare_is_schema2_complete_and_roundtrips_store(store, signing):
    snap = snapshot()
    result = store.prepare(snap, signing[0], public_scope_approved=True)
    manifest = store.read(store.pending()["pointer"])
    assert manifest["schemaVersion"] == 2 and manifest["protocolVersion"] == 1
    assert manifest["minAppVersion"] == "2.0.2"
    store.package(result["sequence"])
    store.promote()
    assert store.read()["schemaVersion"] == 2


@pytest.mark.parametrize("legacy", [b"| RS990001-Q5 | 40 |\n",
    "---\n关联产品: '[[RS990001-Q5]]'\n---\n## 示*例*\n".encode(),
    b"| A | B |\n| --- | --- |\n| resin | 40 |\n\n| water | 10 |\n"])
def test_ordinary_prepare_never_creates_legacy_credit(store, signing, legacy):
    snap = snapshot(); snap.files[PATH] = legacy
    signer = RecordingSigner(signing[0]); before = artifacts(store)
    with pytest.raises(DataError, match="structured|quality gate"):
        store.prepare(snap, signer, public_scope_approved=True)
    assert signer.calls == 0 and artifacts(store) == before


def test_signed_legacy_only_exact_restore_preserves_schema1(store, signing):
    old = snapshot(); old.files[PATH] = b"| resin | 40 |\n"
    install_legacy(store, signing[0], old)
    # Change another namespace without republishing arbitrary Markdown.
    install_legacy(store, signing[0], snapshot(), sequence=2, schema_version=2)
    result = store.rollback(1, signing[0], public_scope_approved=True)
    assert store.read(store.pending()["pointer"])["schemaVersion"] == 1
    store.package(result["sequence"]); store.promote()
    assert store.read()["rollbackOf"] == 1
