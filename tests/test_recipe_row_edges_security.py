"""Synthetic recipe edge columns must not grant unapproved identity credit."""
import zipfile

import pytest

from rstone_data import quality_gate as q
from rstone_data.protocol import DataError, sha256
from rstone_data.sources import Snapshot
from test_quality_parent_review import RecordingSigner

GRADE = "RS87654321-Q7"  # Synthetic identity, not a business product.
RECIPE_PATH = f"产品配方/{GRADE}.md"
HEADER = "| 原料编号 | 投入数(g) | 百分比(%) | 供应商 | 备注 |"
SEPARATOR = "| --- | --- | --- | --- | --- |"
OWNER_ROW = f"| [[{GRADE}]] | 40 | 80 | | |"
WATER_ROW = "| 水 | 10 | 20 | | |"


@pytest.fixture(autouse=True)
def no_catalog_permission_for_synthetic_rows(monkeypatch, tmp_path):
    import json
    from rstone_data.catalog_revisions import SCHEMA
    # Deployment may hold an unrelated exact approved transaction. These inputs
    # explicitly test the no-permission boundary without changing that policy.
    path = tmp_path / "no-row-test-permission.json"
    path.write_text(json.dumps({"schema": SCHEMA, "approvals": []}), encoding="utf-8")
    monkeypatch.setattr(q, "default_catalog_revisions", lambda: path)


def recipe_lines():
    return [HEADER, SEPARATOR, OWNER_ROW, WATER_ROW]


def snapshot(lines=None, name=GRADE):
    body = "\n".join(recipe_lines() if lines is None else lines)
    raw = (f"---\n关联产品: '[[{GRADE}]]'\n---\n# {GRADE}\n\n" + body + "\n").encode()
    return Snapshot({
        f"产品列表/{GRADE}.md": "---\ntags: [水性, UV]\n固含: 77\n---\n".encode(),
        "产品应用/合成涂层.md": "---\ntags: [PC]\n---\n".encode(),
        f"产品TDS/{GRADE}.TDS.md": b"# synthetic TDS\n",
        f"产品配方/{name}.md": raw,
    }, {"Mehael-Yeh/Rstone": "a" * 40, "Mehael-Yeh/RstoneTDS": "b" * 40})


def product_ids(candidate):
    corpus = q.snapshot_corpus(candidate)
    doc, = corpus.by_namespace("产品配方")
    return q._recipe_product_ids(doc, corpus)


def fingerprints(store):
    return {path.relative_to(store.root).as_posix(): sha256(path.read_bytes())
            for path in store.root.rglob("*") if path.is_file()}


def assert_unapproved_rejection(store, signing, candidate):
    # Public-scope consent is not a catalogue or identity approval.
    assert q.load_catalog_revisions() == frozenset()
    corpus = q.snapshot_corpus(candidate)
    findings = q.check_identity(corpus, None)
    # Existing unrelated RD/RS receipts grant no credit without a baseline.
    assert findings == q.check_identity(corpus, None, q.load_identity_migrations())
    assert {"path": RECIPE_PATH, "category": "stem-collision-cross-namespace"} in findings
    with pytest.raises(DataError, match="quality gate.*identity"):
        q.validate_snapshot(candidate)
    before = fingerprints(store)
    signer = RecordingSigner(signing[0])
    with pytest.raises(DataError, match="quality gate.*identity"):
        store.prepare(candidate, signer, public_scope_approved=True)
    assert signer.calls == 0
    assert store.pending() is None
    assert not (store.public / "objects").exists()
    assert not list(store.public.rglob("*.sig"))
    assert not list(store.public.rglob("*.zip"))
    assert fingerprints(store) == before


def test_empty_first_column_never_credits_shifted_owner():
    lines = recipe_lines()
    lines[2] = "|" + lines[2]
    assert product_ids(snapshot(lines)) == set()


def test_empty_first_column_rejected_at_real_prepare_before_signing(store, signing):
    lines = recipe_lines()
    lines[2] = "|" + lines[2]
    candidate = snapshot(lines)
    signer = RecordingSigner(signing[0])
    with pytest.raises(DataError, match="quality gate.*identity"):
        store.prepare(candidate, signer, public_scope_approved=True)
    assert signer.calls == 0
    assert store.pending() is None
    assert not (store.public / "objects").exists()
    assert not list(store.public.rglob("*.sig"))
    assert not list(store.public.rglob("*.zip"))


@pytest.mark.parametrize("position", range(4), ids=["header", "separator", "owner", "accessory"])
@pytest.mark.parametrize("edge", ["left", "right", "both"])
@pytest.mark.parametrize("space", [False, True], ids=["adjacent-pipes", "spaced-pipes"])
@pytest.mark.parametrize("count", [1, 2], ids=["six-plus-columns", "seven-plus-columns"])
def test_extra_edge_empty_columns_never_grant_identity_credit(
        store, signing, position, edge, space, count):
    lines = recipe_lines()
    prefix, suffix = ("| ", " |") if space else ("|", "|")
    if edge in ("left", "both"):
        lines[position] = prefix * count + lines[position]
    if edge in ("right", "both"):
        lines[position] += suffix * count
    assert len(lines[position][1:-1].split("|")) > 5
    candidate = snapshot(lines)
    assert_unapproved_rejection(store, signing, candidate)
    assert product_ids(candidate) == set()


@pytest.mark.parametrize("header_amount", ["投入数", "投入数(g)"])
@pytest.mark.parametrize("supplier,note", [
    ("", ""), ("合成供应商", ""), ("", "合成备注"), ("合成供应商", "合成备注"),
])
@pytest.mark.parametrize("compact", [False, True], ids=["spaced", "compact"])
def test_five_actual_columns_preserve_legal_empty_tail_and_signed_bytes(
        store, signing, header_amount, supplier, note, compact):
    lines = recipe_lines()
    lines[0] = HEADER.replace("投入数(g)", header_amount)
    values = [f"[[{GRADE}]]", "40", "80", supplier, note]
    lines[2] = "|" + "|".join(values) + "|" if compact else "| " + " | ".join(values) + " |"
    assert len(lines[2][1:-1].split("|")) == 5
    candidate = snapshot(lines)
    assert product_ids(candidate) == {GRADE}
    assert q.validate_snapshot(candidate)["summary"]["ok"]
    signer = RecordingSigner(signing[0])
    result = store.prepare(candidate, signer, public_scope_approved=True)
    assert result["status"] == "pending"
    assert signer.calls == 1
    assert store.pending() is not None
    package = store.public / store.package(result["sequence"])
    with zipfile.ZipFile(package) as archive:
        for path, raw in candidate.files.items():
            assert archive.read("files/" + path) == raw
    assert store.promote()["status"] == "published"
    verified = store.read()
    assert verified["sequence"] == result["sequence"]
    assert store._snapshot(verified).files == candidate.files
    assert store.pending() is None


@pytest.mark.parametrize("kind", [
    "swapped-header", "shifted-header", "wrong-header", "shifted-ingredient",
    "owner-in-supplier", "owner-in-note", "alias-pipe", "nonseparator-cell",
])
def test_pseudo_headers_and_wrong_positions_do_not_claim_owner(store, signing, kind):
    lines = recipe_lines()
    if kind == "swapped-header":
        lines[0] = "| 投入数(g) | 原料编号 | 百分比(%) | 供应商 | 备注 |"
    elif kind == "shifted-header":
        lines[0] = "| | 原料编号 | 投入数(g) | 百分比(%) | 供应商 |"
    elif kind == "wrong-header":
        lines[0] = HEADER.replace("投入数(g)", "硬度")
    elif kind == "shifted-ingredient":
        lines[2] = f"| | [[{GRADE}]] | 40 | 80 | |"
    elif kind == "owner-in-supplier":
        lines[2] = f"| 水 | 40 | 80 | [[{GRADE}]] | |"
    elif kind == "owner-in-note":
        lines[2] = f"| 水 | 40 | 80 | | [[{GRADE}]] |"
    elif kind == "alias-pipe":
        lines[2] = OWNER_ROW.replace(f"[[{GRADE}]]", f"[[{GRADE}|合成别名]]")
    else:
        lines[1] = SEPARATOR.replace("---", "not-a-separator", 1)
    candidate = snapshot(lines)
    assert_unapproved_rejection(store, signing, candidate)
    assert product_ids(candidate) == set()


@pytest.mark.parametrize("position", range(4), ids=["header", "separator", "owner", "accessory"])
@pytest.mark.parametrize("edge", ["left", "right"])
def test_edge_columns_do_not_authorize_editorial_rename(store, signing, position, edge):
    baseline = snapshot(name=GRADE + "-底-参考配方")
    store.prepare(baseline, signing[0], public_scope_approved=True)
    store.promote()
    before = fingerprints(store)
    lines = recipe_lines()
    lines[position] = "|" + lines[position] if edge == "left" else lines[position] + "|"
    candidate = snapshot(lines, name=GRADE + "-底")
    findings = q.check_identity(q.snapshot_corpus(candidate), q.snapshot_corpus(baseline))
    assert {"path": f"产品配方/{GRADE}-底.md", "category": "identity-suffix-dropped"} in findings
    with pytest.raises(DataError, match="quality gate.*identity"):
        q.validate_snapshot(candidate, baseline)
    signer = RecordingSigner(signing[0])
    with pytest.raises(DataError, match="quality gate.*identity"):
        store.prepare(candidate, signer, public_scope_approved=True)
    assert signer.calls == 0
    assert store.pending() is None
    assert fingerprints(store) == before
    assert not list(store.public.rglob("*.zip"))


@pytest.mark.parametrize("operation", ["resume", "promote", "package", "rollback"])
def test_already_signed_shifted_owner_stays_blocked_on_revalidation(store, signing, operation):
    from test_release_quality import install_legacy

    lines = recipe_lines()
    lines[2] = "|" + lines[2]
    candidate = snapshot(lines)
    install_legacy(store, signing[0], candidate, pending=operation in ("resume", "promote"))
    before = fingerprints(store)
    signer = RecordingSigner(signing[0])
    with pytest.raises(DataError, match="quality gate.*identity"):
        if operation == "resume":
            store.prepare(None, signer, public_scope_approved=True)
        elif operation == "promote":
            store.promote()
        elif operation == "package":
            store.package(1)
        else:
            store.rollback(1, signer, public_scope_approved=True)
    assert signer.calls == 0
    assert fingerprints(store) == before
    assert not list(store.public.rglob("*.zip"))
