"""Canonical facts, not Markdown rendering or metadata, grant identity credit."""
import copy
import pytest
from rstone_data import quality_gate as q, recipe_codec as codec
from rstone_data.protocol import DataError
from test_structured_publication import model, snapshot, PATH, OWNER
from test_quality_parent_review import RecordingSigner
from test_identity_migration_security import artifacts


@pytest.mark.parametrize("change", ["title", "owner", "association", "case-association", "owner-zero",
                                   "owner-negative", "owner-unknown", "owner-range-reversed", "duplicate-id",
                                   "filename-owner", "unregistered-row-link"])
def test_invalid_catalogue_model_refuses_before_sign(store, signing, change):
    m = model(); snap = snapshot()
    if change == "title": m["title"] += "改名"
    elif change == "owner": m["ownerProduct"] = "RS990001"; m["associatedProducts"].append("RS990001")
    elif change == "association": m["associatedProducts"].append("RS990099-Q5")
    elif change == "case-association": m["associatedProducts"].append(OWNER.lower())
    elif change.startswith("owner-"):
        m["rows"][0][1] = {"owner-zero": "0", "owner-negative": "-40", "owner-unknown": "适量",
                              "owner-range-reversed": "40-10"}[change]
    elif change == "unregistered-row-link": m["rows"][1][0] = "[[RS990099-Q5]]"
    snap.files[PATH] = codec.emit(m)
    if change == "duplicate-id":
        other = copy.deepcopy(m); other["title"] = OWNER + "-亮"
        snap.files["产品配方/" + other["title"] + ".md"] = codec.emit(other)
    if change == "filename-owner": snap.files["产品配方/RS990001-底.md"] = snap.files.pop(PATH)
    signer = RecordingSigner(signing[0]); before = artifacts(store)
    with pytest.raises(DataError, match="quality gate|structured"):
        store.prepare(snap, signer, public_scope_approved=True)
    assert signer.calls == 0 and artifacts(store) == before


@pytest.mark.parametrize("classification", ["main", "example", "reference", "unverified"])
def test_only_main_quantified_model_gets_same_stem_credit(classification):
    m = model(); m["classification"] = classification; m["title"] = OWNER
    snap = snapshot(); snap.files.pop(PATH); snap.files["产品配方/"+OWNER+".md"] = codec.emit(m)
    corpus = q.snapshot_corpus(snap)
    result = q._recipe_product_ids(corpus.by_namespace("产品配方")[0], corpus)
    assert result == ({OWNER} if classification == "main" else set())


def test_legacy_markdown_helper_cannot_grant_new_identity():
    snap = snapshot(); snap.files.pop(PATH)
    snap.files["产品配方/"+OWNER+".md"] = (
        "| 原料编号 | 投入数(g) | 百分比(%) | 供应商 | 备注 |\n"
        "| --- | --- | --- | --- | --- |\n| [["+OWNER+"]] | 40 | | | |\n").encode()
    corpus = q.snapshot_corpus(snap)
    assert q._recipe_product_ids(corpus.by_namespace("产品配方")[0], corpus) == set()


@pytest.mark.parametrize("location", ["preTable", "supplier", "remark", "process-label", "process-step", "note"])
@pytest.mark.parametrize("bad", ["报价", "１２３ 元", "password: SYNTHETIC_SECRET", "engineer@example.invalid"])
def test_decoded_all_business_fields_are_scanned(store, signing, location, bad):
    m = model()
    if location == "preTable": m["preTable"] = [bad]
    elif location == "supplier": m["rows"][0][3] = bad
    elif location == "remark": m["rows"][0][4] = bad
    elif location == "process-label": m["content"][1]["label"] = bad
    elif location == "process-step": m["content"][1]["steps"][0] = bad
    else: m["content"][0]["text"] = bad
    snap = snapshot(m); signer = RecordingSigner(signing[0]); before = artifacts(store)
    with pytest.raises(DataError): store.prepare(snap, signer, public_scope_approved=True)
    assert signer.calls == 0 and artifacts(store) == before


def test_split_business_strings_cannot_hide_a_secret_or_price(store, signing):
    for chunks in [["报", "价"], ["pass", "word: SYNTHETIC_SECRET"]]:
        m = model(); m["preTable"] = chunks
        signer = RecordingSigner(signing[0]); before = artifacts(store)
        with pytest.raises(DataError): store.prepare(snapshot(m), signer, public_scope_approved=True)
        assert signer.calls == 0 and artifacts(store) == before
