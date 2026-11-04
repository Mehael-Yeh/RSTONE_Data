"""Synthetic render contexts cannot manufacture a real or a prefix recipe."""
import pytest

from rstone_data import catalog_revisions as cr, quality_gate as q
from rstone_data.protocol import DataError
from test_quality_parent_review import RecordingSigner
from test_release_quality import install_legacy
from test_recipe_markdown_boundary_security import (
    GRADE, HEADER, SEPARATOR, OWNER, WATER, ROWS, TARGET, SOURCE,
    snapshot, pair, parsed, policy, exact_digest, assert_rejected,
    identity_findings, artifact_hashes,
)


@pytest.fixture(autouse=True)
def isolated_policy(monkeypatch, tmp_path):
    policy(monkeypatch, tmp_path)


CONTEXTS = ["fence-comment", "fence-comment-space", "fence-comment-multiline",
            "setext-one", "setext-two", "html-pre", "html-script", "html-style",
            "html-textarea", "html-div", "html-table", "html-custom",
            "html-instruction", "html-declaration", "html-cdata",
            "html-opener-comment", "setext-comment-reset", "setext-comment-label"]


def surround(kind, rows):
    if kind == "html-opener-comment":
        return ["<pre><!-- note -->", *rows]
    if kind == "setext-comment-reset":
        return ["## 示例", "真实配方", "---<!-- note -->", "", *rows]
    if kind == "setext-comment-label":
        return ["示例<!-- note -->", "---", "", *rows]
    opaque = {"html-instruction": ("<?synthetic", "?>"),
              "html-declaration": ("<!SYNTHETIC", ">"),
              "html-cdata": ("<![CDATA[", "]]>")}
    if kind in opaque:
        return [opaque[kind][0], *rows, opaque[kind][1]]
    if kind.startswith("fence-comment"):
        tail = {"fence-comment": "<!-- note -->",
                "fence-comment-space": " <!-- note -->",
                "fence-comment-multiline": "<!--\nnote\n-->"}[kind]
        return ["```markdown", "```" + tail, *rows]
    if kind.startswith("setext"):
        return ["示例", "===" if kind == "setext-one" else "---", "", *rows]
    tag = kind.removeprefix("html-")
    return [f"<{tag}>", *rows, f"</{tag}>"]


@pytest.mark.parametrize("opening", ["```<!-- note -->", "~~~<!-- note -->"])
def test_comment_in_opening_info_cannot_remove_unclosed_fence(opening):
    doc, corpus = parsed(snapshot([opening, *ROWS]))
    assert not q._recipe_product_ids(doc, corpus)
    assert cr._recipe_data(doc, corpus) is None


@pytest.mark.parametrize("kind", CONTEXTS)
def test_nonassertional_context_cannot_automatically_credit_identity(kind):
    doc, corpus = parsed(snapshot(surround(kind, ROWS)))
    assert not q._recipe_product_ids(doc, corpus)
    assert cr._recipe_data(doc, corpus) is None


@pytest.mark.parametrize("kind", CONTEXTS)
def test_nonassertional_context_rejected_at_real_unsigned_entry(kind, store, signing):
    assert_rejected(store, signing, snapshot(surround(kind, ROWS)))


@pytest.mark.parametrize("kind", CONTEXTS)
def test_context_cannot_gain_catalog_permission_from_its_exact_hash(
        kind, store, signing, monkeypatch, tmp_path):
    quantitative = [HEADER, SEPARATOR, OWNER,
                    "| 固化剂 | 按NCO/OH=1.1另加 | | | |", WATER]
    before, after = pair(surround(kind, quantitative))
    policy(monkeypatch, tmp_path, [exact_digest(before, after)])
    install_legacy(store, signing[0], before)
    assert_rejected(store, signing, after, before)
    assert cr.proposal(q.snapshot_corpus(before), q.snapshot_corpus(after),
                       identity_findings(before, after)) is None


@pytest.mark.parametrize("comment", ["<!-- note -->", " <!-- note -->",
                                     "<!-- first --><!-- second -->"])
def test_inline_comment_cannot_make_a_blank_boundary_before_water(comment):
    lines = [HEADER, SEPARATOR, OWNER,
             "| 固化剂 | 按NCO/OH=1.1另加 | | | |" + comment, WATER]
    doc, corpus = parsed(snapshot(lines))
    data = cr._recipe_data(doc, corpus)
    assert data is None or ("水", "10-12") in data[1]
    before, after = pair(lines)
    after.files[TARGET] = after.files[TARGET].replace((WATER + "\n").encode(), b"")
    assert cr.proposal(q.snapshot_corpus(before), q.snapshot_corpus(after),
                       identity_findings(before, after)) is None


def test_inline_comment_lost_water_rejected_even_with_exact_hash(
        store, signing, monkeypatch, tmp_path):
    before, after = pair([HEADER, SEPARATOR, OWNER,
                         "| 固化剂 | 按NCO/OH=1.1另加 | | | |<!-- note -->", WATER])
    after.files[TARGET] = after.files[TARGET].replace((WATER + "\n").encode(), b"")
    policy(monkeypatch, tmp_path, [exact_digest(before, after)])
    install_legacy(store, signing[0], before)
    assert_rejected(store, signing, after, before)


@pytest.mark.parametrize("kind", ["fence-comment", "setext-two", "html-pre"])
@pytest.mark.parametrize("operation", ["resume", "promote", "complete", "package", "rollback"])
def test_signed_context_stays_blocked_on_every_revalidation(
        kind, operation, store, signing, monkeypatch, tmp_path):
    before, after = pair(surround(kind, [HEADER, SEPARATOR, OWNER,
                                       "| 固化剂 | 按NCO/OH=1.1另加 | | | |", WATER]))
    policy(monkeypatch, tmp_path, [exact_digest(before, after)])
    install_legacy(store, signing[0], before)
    install_legacy(store, signing[0], after, sequence=2,
                   pending=operation in ("resume", "promote", "complete"))
    previous = artifact_hashes(store)
    signer = RecordingSigner(signing[0])
    with pytest.raises(DataError, match="quality gate.*identity"):
        if operation == "resume":
            store.prepare(None, signer, public_scope_approved=True)
        elif operation == "promote":
            store.promote()
        elif operation == "complete":
            store.complete(lambda _: pytest.fail("must reject before remote verification"))
        elif operation == "package":
            store.package(2)
        else:
            store.rollback(2, signer, public_scope_approved=True)
    assert signer.calls == 0
    assert artifact_hashes(store) == previous


@pytest.mark.parametrize("indent", ["", " ", "  ", "   "])
@pytest.mark.parametrize("rule", ["---", "==="])
def test_setext_nonassertional_heading_and_genuine_reset(indent, rule):
    lines = [indent + "示例", indent + rule, "", *ROWS]
    doc, corpus = parsed(snapshot(lines))
    assert not q._recipe_product_ids(doc, corpus)
    assert cr._recipe_data(doc, corpus) is None
    doc, corpus = parsed(snapshot([*lines, "", "# 真实配方", "", *ROWS]))
    assert not q._recipe_product_ids(doc, corpus)
    assert q._legacy_recipe_product_ids(doc, corpus) == {GRADE}
    assert cr._recipe_data(doc, corpus) is None
    assert cr._legacy_recipe_data(doc, corpus) is not None


@pytest.mark.parametrize("title", ["示例\n合成配方", "合成配方\n示例", "示例|标注\n合成配方"])
def test_multiline_setext_title_keeps_nonassertional_context(title):
    doc, corpus = parsed(snapshot([*title.splitlines(), "---", "", *ROWS]))
    assert not q._recipe_product_ids(doc, corpus)
    assert cr._recipe_data(doc, corpus) is None


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\x85"])
def test_comment_mask_preserves_physical_line_boundaries(separator):
    doc, corpus = parsed(snapshot([HEADER, SEPARATOR, OWNER,
                                  "<!-- note" + separator + "more -->", WATER]))
    assert len(q._recipe_visible_lines(doc)) == len(doc.body.splitlines())
    assert cr._recipe_data(doc, corpus) is None


@pytest.mark.parametrize("kind", ["fence-comment", "setext-two", "html-pre"])
@pytest.mark.parametrize("bom", [False, True])
def test_crlf_and_file_bom_do_not_change_context_qualification(kind, bom):
    candidate = snapshot(surround(kind, ROWS))
    raw = candidate.files[TARGET].replace(b"\n", b"\r\n")
    candidate.files[TARGET] = (b"\xef\xbb\xbf" if bom else b"") + raw
    doc, corpus = parsed(candidate)
    assert not q._recipe_product_ids(doc, corpus)
    assert cr._recipe_data(doc, corpus) is None
