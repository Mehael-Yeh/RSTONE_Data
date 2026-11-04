"""Synthetic Markdown boundaries must never authorize a partial or example BOM."""
import copy
import json
import zipfile

import pytest

from rstone_data import catalog_revisions as cr, quality_gate as q
from rstone_data.build import deletion_approval, inventory
from rstone_data.protocol import DataError, canonical_json, sha256
from rstone_data.sources import Snapshot
from test_quality_parent_review import RecordingSigner
from test_release_quality import install_legacy

GRADE = "RS87654321-Q9"  # Synthetic; never an installed business approval.
TARGET = f"产品配方/{GRADE}.md"
SOURCE = f"产品配方/{GRADE}-底-参考配方-方案02.md"
HEADER = "| 原料编号 | 投入数(g) | 百分比(%) | 供应商 | 备注 |"
SEPARATOR = "| --- | --- | --- | --- | --- |"
OWNER = f"| [[{GRADE}]] | 40 | 80 | | |"
WATER = "| 水 | 10-12 | | | |"
AUX = "| 合成助剂 | 少量 | | | |"
ROWS = [HEADER, SEPARATOR, OWNER, WATER, AUX]


def recipe(lines=ROWS):
    return (f"---\n关联产品: '[[{GRADE}]]'\n---\n# {GRADE}\n\n"
            + "\n".join(lines) + "\n").encode()


def snapshot(lines=ROWS, path=TARGET):
    return Snapshot({
        f"产品列表/{GRADE}.md": "---\ntags: [水性, UV]\n固含: 77\n---\n".encode(),
        "产品应用/合成涂层.md": "---\ntags: [PC]\n---\n".encode(),
        f"产品TDS/{GRADE}.TDS.md": b"# synthetic TDS\n",
        path: recipe(lines),
    }, {"Mehael-Yeh/Rstone": "a" * 40, "Mehael-Yeh/RstoneTDS": "b" * 40})


def pair(lines=ROWS):
    before = snapshot(lines, SOURCE)
    after = copy.deepcopy(before)
    after.files[TARGET] = after.files.pop(SOURCE)
    return before, after


def parsed(snap):
    corpus = q.snapshot_corpus(snap)
    doc, = corpus.by_namespace("产品配方")
    return doc, corpus


def identity_findings(before, after):
    return q.check_identity(q.snapshot_corpus(after), q.snapshot_corpus(before))


def exact_digest(before, after):
    # Independent canonical transaction, not the production proposal routine.
    scope = sorted(identity_findings(before, after), key=lambda x: (x["category"], x["path"]))
    return sha256(canonical_json({
        "schema": cr.SCHEMA,
        "before": {path: sha256(raw) for path, raw in before.files.items()},
        "after": {path: sha256(raw) for path, raw in after.files.items()},
        "scope": scope,
    }))


def policy(monkeypatch, tmp_path, approvals=()):
    path = tmp_path / "synthetic-boundary-policy.json"
    path.write_text(json.dumps({"schema": cr.SCHEMA, "approvals": list(approvals)}))
    monkeypatch.setattr(q, "default_catalog_revisions", lambda: path)


@pytest.fixture(autouse=True)
def isolated_empty_policy(monkeypatch, tmp_path):
    policy(monkeypatch, tmp_path)


def artifact_hashes(store):
    return {p.relative_to(store.root).as_posix(): sha256(p.read_bytes())
            for directory in (store.public, store.state) for p in directory.rglob("*")
            if p.is_file()}


def assert_rejected(store, signing, candidate, baseline=None):
    with pytest.raises(DataError, match="quality gate.*identity"):
        q.validate_snapshot(candidate, baseline)
    prior = artifact_hashes(store)
    signer = RecordingSigner(signing[0])
    kwargs = {"approval": deletion_approval(store.read(), inventory(candidate.files))} if baseline else {}
    with pytest.raises(DataError, match="quality gate.*identity"):
        store.prepare(candidate, signer, public_scope_approved=True, **kwargs)
    assert signer.calls == 0
    assert store.pending() is None
    assert artifact_hashes(store) == prior  # no new objects or signatures
    assert not list(store.public.rglob("*.zip"))
    if baseline is None:
        assert not (store.public / "objects").exists()
        assert not list(store.public.rglob("*.sig"))


@pytest.mark.parametrize("indent", [" \t", "  \t", "   \t", " \t ", "\t "])
def test_mixed_indent_code_cannot_claim_registered_identity(indent):
    doc, corpus = parsed(snapshot([indent + line for line in ROWS]))
    assert not q._recipe_product_ids(doc, corpus)
    assert cr._recipe_data(doc, corpus) is None


def test_mixed_indent_code_rejected_by_real_prepare_without_permission(store, signing):
    assert_rejected(store, signing, snapshot([" \t" + line for line in ROWS]))


def test_exact_policy_cannot_license_mixed_indent_example(store, signing, monkeypatch, tmp_path):
    before, after = pair([" \t" + line for line in ROWS])
    policy(monkeypatch, tmp_path, [exact_digest(before, after)])
    install_legacy(store, signing[0], before)
    assert_rejected(store, signing, after, before)
    assert not cr.eligible_scope(q.snapshot_corpus(before), q.snapshot_corpus(after),
                                 identity_findings(before, after))


def test_last_optional_left_water_cannot_be_deleted_under_exact_policy(
        store, signing, monkeypatch, tmp_path):
    # Only the final water row is removed; the owner and unknown NCO/OH addition
    # stay byte-identical. An owner-only or owner+addition prefix is not a BOM.
    lines = [*ROWS[:3], "| 固化剂 | 按NCO/OH=1.1另加 | | | |", WATER.removeprefix("|")]
    before, after = pair(lines)
    after.files[TARGET] = after.files[TARGET].replace(
        (WATER.removeprefix("|") + "\n").encode(), b"")
    policy(monkeypatch, tmp_path, [exact_digest(before, after)])
    install_legacy(store, signing[0], before)
    assert_rejected(store, signing, after, before)
    assert not cr.eligible_scope(q.snapshot_corpus(before), q.snapshot_corpus(after),
                                 identity_findings(before, after))


def test_nonprefix_row_and_following_ingredients_are_not_a_prefix_bom():
    lines = [*ROWS[:3], WATER.removeprefix("|"), AUX.removeprefix("|")]
    doc, corpus = parsed(snapshot(lines))
    data = cr._recipe_data(doc, corpus)
    assert data is None or data[1] == ((f"[[{GRADE}]]", "40"), ("水", "10-12"), ("合成助剂", "少量"))


@pytest.mark.parametrize("amount", ["", "-1", "12-10", "未知投入"])
def test_nonprefix_invalid_row_cannot_leave_quantified_owner_credit(amount):
    lines = [*ROWS[:3], f"水 | {amount} | | | |", AUX]
    doc, corpus = parsed(snapshot(lines))
    assert not q._recipe_product_ids(doc, corpus)
    assert cr._recipe_data(doc, corpus) is None


FALSE_CLOSERS = ["    ```", " \t```", "  \t```", "\t```", "```not-a-close",
                 "``", "~~~", "> ```", "    ~~~", " \t~~~", "~~~not-a-close"]


def fenced_example(closer, opening="```markdown"):
    return [opening, closer, *ROWS]  # deliberately no actual closing fence


@pytest.mark.parametrize("reader", ["identity", "catalog"])
@pytest.mark.parametrize("closer", FALSE_CLOSERS)
def test_false_fence_closers_never_expose_example_recipe(reader, closer):
    opening = "~~~markdown" if "~" in closer and closer != "~~~" else "```markdown"
    doc, corpus = parsed(snapshot(fenced_example(closer, opening)))
    if reader == "identity":
        assert not q._recipe_product_ids(doc, corpus)
    else:
        assert cr._recipe_data(doc, corpus) is None


@pytest.mark.parametrize("closer", ["    ```", " \t```", "```not-a-close", "    ~~~", " \t~~~"])
def test_false_fence_close_rejected_at_exact_policy_publication(
        store, signing, monkeypatch, tmp_path, closer):
    opening = "~~~markdown" if "~" in closer else "```markdown"
    before, after = pair(fenced_example(closer, opening))
    policy(monkeypatch, tmp_path, [exact_digest(before, after)])
    install_legacy(store, signing[0], before)
    assert_rejected(store, signing, after, before)


@pytest.mark.parametrize("hidden", [" \t" + WATER, "    " + WATER, "> " + WATER,
                                    "```markdown", "~~~markdown"])
@pytest.mark.parametrize("reader", ["identity", "catalog"])
def test_excluded_continuation_cannot_authorize_table_prefix(hidden, reader):
    doc, corpus = parsed(snapshot([*ROWS[:3], hidden, AUX]))
    if reader == "identity":
        assert not q._recipe_product_ids(doc, corpus)
    else:
        assert cr._recipe_data(doc, corpus) is None


@pytest.mark.parametrize("indent", ["    ", " \t", "  \t", "\t"])
@pytest.mark.parametrize("reader", ["identity", "catalog"])
def test_indented_heading_cannot_reset_example_section(indent, reader):
    doc, corpus = parsed(snapshot(["## 示例", indent + "# 合成说明", *ROWS]))
    if reader == "identity":
        assert not q._recipe_product_ids(doc, corpus)
    else:
        assert cr._recipe_data(doc, corpus) is None


@pytest.mark.parametrize("operation", ["resume", "promote", "complete", "package", "rollback"])
@pytest.mark.parametrize("defect", ["false-close", "mixed-indent", "missing-water"])
def test_signed_boundary_defect_revalidated_before_any_publication(
        store, signing, monkeypatch, tmp_path, operation, defect):
    if defect == "false-close":
        before, after = pair(fenced_example(" \t```"))
    elif defect == "mixed-indent":
        before, after = pair([" \t" + line for line in ROWS])
    else:
        lines = [*ROWS[:3], "| 固化剂 | 按NCO/OH=1.1另加 | | | |", WATER.removeprefix("|")]
        before, after = pair(lines)
        after.files[TARGET] = after.files[TARGET].replace(
            (WATER.removeprefix("|") + "\n").encode(), b"")
    policy(monkeypatch, tmp_path, [exact_digest(before, after)])
    install_legacy(store, signing[0], before)
    install_legacy(store, signing[0], after, sequence=2,
                   pending=operation in ("resume", "promote", "complete"))
    prior = artifact_hashes(store)
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
    assert artifact_hashes(store) == prior
    assert not list(store.public.rglob("*.zip"))


@pytest.mark.parametrize("indent", ["", " ", "  ", "   "])
@pytest.mark.parametrize("tail", [
    [], ["施工需按既定工艺喷涂。"], ["", "施工需按既定工艺喷涂。"],
    ["## 施工", "施工需按既定工艺喷涂。"], ["## 示例", "以下为非配方说明。"],
    ["", "```text", "合成附注", "```"],
])
def test_legal_table_indentation_and_normal_tails_still_publish(
        store, signing, indent, tail):
    lines = [indent + line for line in ROWS] + tail
    candidate = snapshot(lines)
    doc, corpus = parsed(candidate)
    assert q._legacy_recipe_product_ids(doc, corpus) == {GRADE}
    assert cr._legacy_recipe_data(doc, corpus)[1] == (
        (f"[[{GRADE}]]", "40"), ("水", "10-12"), ("合成助剂", "少量"))
    assert not q._recipe_product_ids(doc, corpus)
    from recipe_fixtures import known_table
    candidate.files[TARGET] = known_table(candidate.files[TARGET], GRADE)
    assert q.validate_snapshot(candidate)["summary"]["ok"]
    signer = RecordingSigner(signing[0])
    result = store.prepare(candidate, signer, public_scope_approved=True)
    assert signer.calls == 1 and result["status"] == "pending"
    with zipfile.ZipFile(store.public / store.package(result["sequence"])) as archive:
        for path, raw in candidate.files.items():
            assert archive.read("files/" + path) == raw
    assert store.promote()["status"] == "published"
    assert store._snapshot(store.read()).files == candidate.files
    assert store.pending() is None


@pytest.mark.parametrize("indent", ["", " ", "  ", "   "])
@pytest.mark.parametrize("token", ["```", "~~~"])
def test_genuine_close_before_real_formula_restores_full_bom(indent, token):
    # The code and a later actual table are different blocks, not two BOMs.
    lines = [indent + token + "markdown", *ROWS, indent + token + "\t", "", *ROWS]
    doc, corpus = parsed(snapshot(lines))
    assert q._legacy_recipe_product_ids(doc, corpus) == {GRADE}
    assert cr._legacy_recipe_data(doc, corpus)[1] == (
        (f"[[{GRADE}]]", "40"), ("水", "10-12"), ("合成助剂", "少量"))


@pytest.mark.parametrize("amount", ["10-12", "另加", "适量", "按NCO/OH=1.1另加"])
def test_raw_ranges_and_unknown_additions_are_never_averaged_or_dropped(amount):
    lines = [*ROWS[:3], f"| 水 | {amount} | | | |"]
    before, after = pair(lines)
    doc, corpus = parsed(before)
    assert cr._legacy_recipe_data(doc, corpus)[1] == ((f"[[{GRADE}]]", "40"), ("水", amount))
    after.files[TARGET] = recipe(ROWS[:3])
    assert not cr.eligible_scope(q.snapshot_corpus(before), q.snapshot_corpus(after),
                                 identity_findings(before, after))


@pytest.mark.parametrize("edge", ["left", "right", "both"])
@pytest.mark.parametrize("position", range(5))
def test_optional_edges_are_whole_bom_or_ineligible_never_prefix(edge, position):
    lines = list(ROWS)
    # Nonempty final cells make omitted-right forms unambiguous five-cell rows.
    lines[2:] = [line[:-1] + " 合成注 |" for line in lines[2:]]
    if edge in ("left", "both"):
        lines[position] = lines[position].removeprefix("|")
    if edge in ("right", "both"):
        lines[position] = lines[position].removesuffix("|")
    doc, corpus = parsed(snapshot(lines))
    expected = set() if edge in ("left", "both") and position >= 2 else {GRADE}
    assert q._legacy_recipe_product_ids(doc, corpus) == expected
    data = cr._legacy_recipe_data(doc, corpus)
    assert data is None or data[1] == (
        (f"[[{GRADE}]]", "40"), ("水", "10-12"), ("合成助剂", "少量"))


@pytest.mark.parametrize("interruption", ["无管道的损坏行", "水 10-12", "> " + WATER,
                                           " \t" + WATER])
def test_interrupted_body_never_publishes_an_owner_only_prefix(
        store, signing, interruption):
    candidate = snapshot([*ROWS[:3], interruption, AUX])
    doc, corpus = parsed(candidate)
    assert not q._recipe_product_ids(doc, corpus)
    assert cr._recipe_data(doc, corpus) is None
    assert_rejected(store, signing, candidate)


@pytest.mark.parametrize("context", ["quote", "example-section", "comment", "unclosed-backtick", "unclosed-tilde"])
def test_nonassertional_whole_table_rejected_at_real_publication(store, signing, context):
    if context == "quote":
        lines = ["> " + line for line in ROWS]
    elif context == "example-section":
        lines = ["## 示例", *ROWS]
    elif context == "comment":
        lines = ["<!--", *ROWS, "-->"]
    else:
        lines = ["```markdown" if context == "unclosed-backtick" else "~~~markdown", *ROWS]
    candidate = snapshot(lines)
    doc, corpus = parsed(candidate)
    assert not q._recipe_product_ids(doc, corpus)
    assert cr._recipe_data(doc, corpus) is None
    assert_rejected(store, signing, candidate)


@pytest.mark.parametrize("indent", ["", " ", "  ", "   "])
def test_complete_catalog_transaction_still_signs_exact_bytes(
        store, signing, monkeypatch, tmp_path, indent):
    from recipe_fixtures import known_table
    from test_protocol_migration_policy import config
    from rstone_data import catalog_revisions_v2 as v2
    before, after = pair([indent + line for line in ROWS])
    before.files[SOURCE] = known_table(before.files[SOURCE], SOURCE.split("/")[-1][:-3])
    after.files[TARGET] = known_table(after.files[TARGET], GRADE)
    config(monkeypatch, tmp_path)
    store.prepare(before, signing[0], public_scope_approved=True); store.promote()
    b = q.snapshot_corpus(store._snapshot(store.read())); a = q.snapshot_corpus(after)
    proposed = v2.proposal(b, a, q.check_identity(a,b)); assert proposed
    digest = proposed["fingerprint"]
    config(monkeypatch, tmp_path, approvals=[digest])
    signer = RecordingSigner(signing[0])
    result = store.prepare(after, signer, public_scope_approved=True,
                           approval=deletion_approval(store.read(), inventory(after.files)))
    assert signer.calls == 1
    assert result["qualityGate"]["catalogRevision"]["fingerprint"] == digest
    assert result["qualityGate"]["summary"]["ok"]
    with zipfile.ZipFile(store.public / store.package(result["sequence"])) as archive:
        assert {path: archive.read("files/" + path) for path in after.files} == after.files
    assert store.promote()["status"] == "published"
    assert store._snapshot(store.read()).files == after.files


@pytest.mark.parametrize("indent", ["    ", "\t", " \t", "  \t", "   \t", " \t "])
@pytest.mark.parametrize("token", ["```", "~~~"])
def test_uncertain_indented_openers_do_not_expand_recipe_permission(indent, token):
    # Preserve the prior fail-closed treatment of uncertain fence openers.
    # Correcting closing syntax must not newly authorize any adjacent table.
    doc, corpus = parsed(snapshot([indent + token + "markdown", *ROWS]))
    assert not q._recipe_product_ids(doc, corpus)
    assert cr._recipe_data(doc, corpus) is None


@pytest.mark.parametrize("token", ["```", "~~~"])
def test_uncertain_opener_can_only_end_at_a_genuine_close(token):
    doc, corpus = parsed(snapshot([" \t" + token, *ROWS, token, "", *ROWS]))
    assert q._legacy_recipe_product_ids(doc, corpus) == {GRADE}
    assert cr._legacy_recipe_data(doc, corpus)[1] == (
        (f"[[{GRADE}]]", "40"), ("水", "10-12"), ("合成助剂", "少量"))


@pytest.mark.parametrize("defect", ["mixed-indent", "false-close", "missing-water"])
def test_real_scan_and_proposal_cli_fail_closed_without_input_writes(
        monkeypatch, tmp_path, capsys, defect):
    if defect == "mixed-indent":
        before, after = pair([" \t" + line for line in ROWS])
    elif defect == "false-close":
        before, after = pair(fenced_example(" \t```"))
    else:
        lines = [*ROWS[:3], "| 固化剂 | 按NCO/OH=1.1另加 | | | |", WATER.removeprefix("|")]
        before, after = pair(lines)
        after.files[TARGET] = after.files[TARGET].replace(
            (WATER.removeprefix("|") + "\n").encode(), b"")
    policy(monkeypatch, tmp_path, [exact_digest(before, after)])
    roots = [tmp_path / "before", tmp_path / "after"]
    for root, snap in zip(roots, (before, after)):
        for path, raw in snap.files.items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
    frozen = {str(p): p.read_bytes() for root in roots for p in root.rglob("*") if p.is_file()}
    assert cr.main(["--before", str(roots[0]), "--after", str(roots[1])]) == 1
    output = capsys.readouterr().out
    assert json.loads(output)["status"] == "ineligible"
    assert GRADE not in output and str(tmp_path) not in output
    report = tmp_path / "gate-report.json"
    assert q.main(["--baseline", str(roots[0]), "--root", str(roots[1]), "--report", str(report),
                   "--expect-recipes", "0", "--expect-applications", "0", "--allow-deletions"]) == 1
    assert "identity" in json.loads(report.read_text())["summary"]["failed_checks"]
    assert frozen == {str(p): p.read_bytes() for root in roots for p in root.rglob("*") if p.is_file()}
