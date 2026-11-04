"""父独立反例：组装视图拆词漏检，以及统一数量语义。先红后修。"""
import pytest
from rstone_data.protocol import DataError
from rstone_data.recipe_catalog import check_catalog, model_for
from rstone_data.sources import Snapshot
from rstone_data import recipe_codec as codec
from test_structured_publication import model, snapshot, PATH, OWNER, RecordingSigner, artifacts


@pytest.mark.parametrize("chunks", [
    ["api_", "key=synthetic_forbidden_token"],
    ["pass", "word: SYNTHETIC_SECRET"],
    ["报", "价"],
])
@pytest.mark.parametrize("where", ["notes", "label-and-step", "steps"])
def test_split_business_text_in_content_cannot_pass_before_signature(store, signing, chunks, where):
    m = model()
    if where == "notes":
        m["content"] = [{"kind": "note", "text": chunks[0]}, {"kind": "note", "text": chunks[1]}]
    elif where == "steps":
        m["content"] = [{"kind": "process", "label": "工艺", "steps": [chunks[0], chunks[1]]}]
    else:
        m["content"] = [{"kind": "process", "label": chunks[0], "steps": [chunks[1]]}]
    signer = RecordingSigner(signing[0]); before = artifacts(store)
    with pytest.raises(DataError):
        store.prepare(snapshot(m), signer, public_scope_approved=True)
    assert signer.calls == 0 and artifacts(store) == before


def test_text_separated_by_a_visible_heading_is_not_a_hidden_adjacency(store, signing):
    """A heading between chunks is visible text; the scanner must not delete it."""
    m = model(); m["content"] = [{"kind": "note", "text": "api_"},
                                 {"kind": "process", "label": "工艺", "steps": ["key=synthetic"]}]
    signer = RecordingSigner(signing[0])
    store.prepare(snapshot(m), signer, public_scope_approved=True)
    assert signer.calls == 1


@pytest.mark.parametrize("amount", ["-1", "10-2", "任意垃圾量", "-10--1", "1.5.5"])
def test_main_with_invalid_nonowner_amount_is_not_a_valid_catalog(amount):
    m = model(); m["rows"][1][1] = amount
    corpus = _corpus(m)
    assert check_catalog(corpus, required=True) == [
        {"path": PATH, "category": "structured-recipe-invalid"}]


@pytest.mark.parametrize("role", ["example", "reference", "unverified"])
def test_non_main_without_positive_owner_is_still_a_readable_catalog(role):
    m = model(); m["classification"] = role; m["rows"][0][1] = "0"
    corpus = _corpus(m)
    assert check_catalog(corpus, required=True) == []
    assert model_for(corpus.by_namespace("产品配方")[0]) is not None


def test_non_main_still_rejects_invalid_amounts():
    m = model(); m["classification"] = "example"; m["rows"][1][1] = "10-2"
    assert check_catalog(_corpus(m), required=True) == [
        {"path": PATH, "category": "structured-recipe-invalid"}]


def test_valid_unknown_and_range_amounts_remain_accepted():
    m = model()
    m["rows"] = [["[[" + OWNER + "]]", "40", "", "", ""],
                 ["水", "10-12", "", "", ""],
                 ["固化剂", "按NCO/OH=1.1另加", "", "", ""],
                 ["助剂", "适量", "", "", ""],
                 ["溶剂", "0", "", "", ""]]
    assert check_catalog(_corpus(m), required=True) == []


def _corpus(m):
    from rstone_data import quality_gate as q
    return q.snapshot_corpus(snapshot(m))
