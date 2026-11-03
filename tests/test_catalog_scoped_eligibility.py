"""Synthetic-only exact catalog eligibility; this file grants no real approval."""
import pytest

from rstone_data import catalog_revisions as cr, quality_gate as q
from test_catalog_revisions import GRADE, PRODUCT, pair, recipe, fingerprint


def scope(before, after):
    old, new = q.snapshot_corpus(before), q.snapshot_corpus(after)
    findings = q.check_identity(new, old)
    return findings, cr.eligible_scope(old, new, findings)


@pytest.mark.parametrize("material", [
    f"[[{GRADE}]](45%)", f"[[{GRADE}]](100%)",
    f"主体树脂（[[{GRADE}]]）",
])
def test_finite_owner_wrappers_preserve_original_bom(material):
    before, after = pair()
    for snap in (before, after):
        for path in list(snap.files):
            if path.startswith("产品配方/"):
                snap.files[path] = snap.files[path].replace(
                    f"| [[{GRADE}]] |".encode(), f"| {material} |".encode())
    findings, eligible = scope(before, after)
    assert findings and eligible == findings
    data = cr._recipe_data(q.snapshot_corpus(after).by_namespace("产品配方")[0],
                           q.snapshot_corpus(after))
    assert data[1][0] == (material, "40")


@pytest.mark.parametrize("material", [
    f"[[{GRADE}]](0%)", f"[[{GRADE}]](101%)", f"[[{GRADE}]](45%）",
    f"主体树脂（[[{GRADE}]])", f"主体树脂（[[{GRADE}]]", f"非[[{GRADE}]](45%)",
    f"[[{GRADE}]](45%)不使用", f"[[{GRADE}]][[RS9698]](45%)",
    f"假名[[{GRADE}]]", "主体树脂（[[RS9607]]）", f"主体树脂（[[{GRADE.lower()}]]）",
])
def test_partial_negated_aliased_or_unpaired_owner_cells_stay_ineligible(material):
    before, after = pair()
    for snap in (before, after):
        for path in list(snap.files):
            if path.startswith("产品配方/"):
                snap.files[path] = snap.files[path].replace(
                    f"| [[{GRADE}]] |".encode(), f"| {material} |".encode())
    findings, eligible = scope(before, after)
    assert findings and not eligible


@pytest.mark.parametrize("amount,accepted", [
    ("开稀至可喷粘度", True), ("开稀至可喷", False),
    ("适量（NCO/OH=1-1.15）", True),
    ("适量（NCO/OH=1.15-1）", False), ("适量（NCO/OH=0-1）", False),
    ("适量（NCO/OH=1-1.15）追加", False), ("适量（NCO/OH=1-1.15)", False),
    ("随意加水", False), ("开稀至可喷粘度后按需调整", False),
    ("开稀至可喷粘度 123元", False), ("", False),
])
def test_bounded_explicit_unknown_dilution_stays_in_bom(amount, accepted):
    before, after = pair()
    for snap in (before, after):
        for path in list(snap.files):
            if path.startswith("产品配方/"):
                snap.files[path] = snap.files[path].replace(
                    "| 水 | 10-12 |".encode(), f"| 水 | {amount} |".encode())
    findings, eligible = scope(before, after)
    assert findings
    assert (eligible == findings) if accepted else not eligible
    if accepted:
        corpus = q.snapshot_corpus(after)
        data = cr._recipe_data(corpus.by_namespace("产品配方")[0], corpus)
        assert ("水", amount) in data[1]
        after.files[f"产品配方/{GRADE}.md"] = after.files[f"产品配方/{GRADE}.md"].replace(
            f"| 水 | {amount} |  | | |\n".encode(), b"")
        assert not scope(before, after)[1]


@pytest.mark.parametrize("kind", ["different", "incomplete"])
def test_retired_old_bom_does_not_become_the_selected_main(kind):
    before, after = pair()
    auxiliary = "RD9698-4B"
    for snap in (before, after):
        snap.files[f"产品列表/{auxiliary}.md"] = PRODUCT
    retired = recipe().replace(
        f"关联产品: '[[{GRADE}]]'".encode(),
        f"关联产品: '[[{GRADE}]][[{auxiliary}]]'".encode())
    retired = retired.replace(b"| 40 |", b"| 20 |")
    if kind == "incomplete":
        retired = retired.replace("| 水 | 10-12 |".encode(), "| 水 | |".encode())
    before.files[f"产品配方/{GRADE}-参考配方-方案03.md"] = retired
    findings, eligible = scope(before, after)
    assert findings and eligible == findings
    old, new = q.snapshot_corpus(before), q.snapshot_corpus(after)
    assert cr.apply(new, old, findings, frozenset())[0] == findings
    exact = fingerprint(before, after)
    assert not cr.apply(new, old, findings, {exact})[0]
    before.files[f"产品配方/{GRADE}-参考配方-方案03.md"] += b"\nchanged\n"
    assert cr.apply(new, q.snapshot_corpus(before), findings, {exact})[0] == findings


def test_all_same_bom_members_must_preserve_union_of_registered_associations():
    before, after = pair()
    alias = "RD9698-4B"
    for snap in (before, after):
        snap.files[f"产品列表/{alias}.md"] = PRODUCT
    shared = recipe().replace(
        f"关联产品: '[[{GRADE}]]'".encode(),
        f"关联产品: '[[{GRADE}]][[{alias}]]'".encode())
    before.files[f"产品配方/{GRADE}-参考配方-方案03.md"] = shared
    findings, eligible = scope(before, after)
    assert findings and not eligible
    after.files[f"产品配方/{GRADE}.md"] = shared
    assert scope(before, after)[1] == findings


@pytest.mark.parametrize("case", ["different-owner", "missing-full-suffix", "missing-material", "metadata"])
def test_selected_main_cannot_borrow_another_owner_or_incomplete_bom(case):
    before, after = pair()
    if case in ("different-owner", "missing-full-suffix"):
        other = GRADE + "-8B"
        for snap in (before, after):
            snap.files[f"产品列表/{other}.md"] = PRODUCT
        before.files[f"产品配方/{other}-参考配方.md"] = recipe(other)
        if case == "different-owner":
            before.files.pop(f"产品配方/{GRADE}-参考配方-方案02.md")
        else:
            after.files.pop(f"产品列表/{other}.md")
    elif case == "missing-material":
        after.files[f"产品配方/{GRADE}.md"] = recipe().replace(
            "| 水 | 10-12 |  | | |\n".encode(), b"")
    else:
        after.files[f"产品配方/{GRADE}.md"] = f"---\n关联产品: '[[{GRADE}]]'\n---\n".encode()
    findings, eligible = scope(before, after)
    assert findings and not eligible


@pytest.mark.parametrize("tail,accepted", [
    ("-参考配方-方案RS990-2-方案02", True),
    ("-参考配方-方案RS990-2-方案02-方案03", False),
    ("-参考配方-方案02-方案03", False),
    ("-参考配方-方案RS990-2-未知02", False),
    ("-参考配方-方案rs990-2-方案02", False),
    ("-参考配方-方案RS990-2-方案CUSTOM", False),
])
def test_only_bounded_legacy_source_plus_numeric_ordinal_is_eligible(tail, accepted):
    before, after = pair()
    oldpath = f"产品配方/{GRADE}-参考配方-方案02.md"
    before.files[f"产品配方/{GRADE}{tail}.md"] = before.files.pop(oldpath)
    findings, eligible = scope(before, after)
    assert findings
    assert (eligible == findings) if accepted else not eligible
    if accepted:
        full = GRADE + tail
        for snap in (before, after):
            snap.files[f"产品列表/{full}.md"] = PRODUCT
        assert not scope(before, after)[1]


@pytest.mark.parametrize("other_state", ["preserved", "retired-variant", "wrong-bom", "metadata", "missing", "shared-loss"])
def test_independent_full_owner_is_not_swallowed_by_simultaneous_catalog_rename(other_state):
    before, after = pair()
    other = GRADE + "-8B"
    alias = "RD9697-5B"
    for snap in (before, after):
        snap.files[f"产品列表/{other}.md"] = PRODUCT
        snap.files[f"产品列表/{alias}.md"] = PRODUCT
    before.files[f"产品配方/{other}-参考配方-方案02.md"] = recipe(other)
    raw = recipe(other)
    if other_state == "retired-variant":
        before.files[f"产品配方/{other}-参考配方-方案03.md"] = raw.replace(b"| 40 |", b"| 39 |")
    if other_state == "wrong-bom":
        raw = raw.replace(b"| 40 |", b"| 39 |")
    elif other_state == "metadata":
        raw = f"---\n关联产品: '[[{other}]]'\n---\n".encode()
    elif other_state == "shared-loss":
        before.files[f"产品配方/{other}-参考配方-方案03.md"] = recipe(other).replace(
            f"关联产品: '[[{other}]]'".encode(),
            f"关联产品: '[[{other}]][[{alias}]]'".encode())
    if other_state != "missing":
        after.files[f"产品配方/{other}.md"] = raw
    findings, eligible = scope(before, after)
    base_findings = [f for f in findings if f['path'] == f"产品配方/{GRADE}.md"]
    assert base_findings
    if other_state in ("preserved", "retired-variant"):
        assert eligible == findings
    else:
        assert not any(f in eligible for f in base_findings if f['category'] == 'identity-suffix-dropped')
