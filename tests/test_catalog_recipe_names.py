"""Synthetic-only recipe catalogue naming; exercise the real publication seam."""
import zipfile

import pytest

from rstone_data import quality_gate as q
from rstone_data.protocol import DataError, sha256
from rstone_data.sources import Snapshot
from test_quality_parent_review import RecordingSigner

GRADE = "RS9402-2B"
PRODUCT = "---\ntags: [水性, UV]\n固含: 77\n---\n".encode()


def recipe(grade=GRADE):
    return (f"---\n关联产品: '[[{grade}]]'\n---\n# {grade}\n\n"
            "| 原料编号 | 投入数(g) | 百分比(%) | 供应商 | 备注 |\n"
            "| --- | --- | --- | --- | --- |\n"
            f"| [[{grade}]] | 40 | 80 | | |\n"
            "| 水 | 10 | 20 | | |\n").encode()


def snapshot(name=GRADE, raw=None, products=None):
    files = {
        f"产品列表/{grade}.md": PRODUCT for grade in (products or [GRADE])
    }
    files.update({
        "产品应用/涂层.md": "---\ntags: [PC]\n---\n".encode(),
        "产品TDS/RS9402-2B.TDS.md": b"# synthetic TDS\n",
        f"产品配方/{name}.md": __import__("recipe_fixtures").known_table(recipe(), name) if raw is None else raw,
    })
    return Snapshot(files, {"Mehael-Yeh/Rstone": "a" * 40,
                            "Mehael-Yeh/RstoneTDS": "b" * 40})


def test_same_name_real_recipe_can_prepare_and_package(store, signing):
    candidate = snapshot()
    assert q.validate_snapshot(candidate)["summary"]["ok"]
    result = store.prepare(candidate, signing[0], public_scope_approved=True)
    package = store.public / store.package(result["sequence"])
    with zipfile.ZipFile(package) as archive:
        for path, raw in candidate.files.items():
            assert archive.read("files/" + path) == raw


def test_editorial_suffix_removal_preserves_full_identity(store, signing):
    baseline = snapshot(GRADE + "-参考配方-方案02")
    candidate = snapshot()
    assert q.validate_snapshot(candidate, baseline)["summary"]["ok"]
    store.prepare(baseline, signing[0], public_scope_approved=True)
    store.promote()
    from rstone_data.build import deletion_approval, inventory
    result = store.prepare(candidate, signing[0], public_scope_approved=True,
        approval=deletion_approval(store.read(),inventory(candidate.files)))
    with zipfile.ZipFile(store.public / store.package(result["sequence"])) as archive:
        assert archive.read(f"files/产品配方/{GRADE}.md") == candidate.files[f"产品配方/{GRADE}.md"]


def assert_rejected_without_artifacts(store, signing, candidate, baseline=None):
    if baseline is not None:
        from test_release_quality import install_legacy
        install_legacy(store, signing[0], baseline)
    before = {str(p.relative_to(store.root)): sha256(p.read_bytes())
              for p in store.root.rglob("*") if p.is_file()}
    signer = RecordingSigner(signing[0])
    with pytest.raises(DataError, match="quality gate"):
        q.validate_snapshot(candidate, baseline)
    with pytest.raises(DataError, match="quality gate"):
        store.prepare(candidate, signer, public_scope_approved=True)
    assert signer.calls == 0
    assert store.pending() is None
    after = {str(p.relative_to(store.root)): sha256(p.read_bytes())
             for p in store.root.rglob("*") if p.is_file()}
    assert before == after
    assert not list(store.public.rglob("*.zip"))


@pytest.mark.parametrize("kind", [
    "empty", "metadata-only", "performance-table", "missing-separator",
    "empty-amount", "negative-amount", "reversed-range", "zero-identity",
    "fenced", "quoted", "comment", "example-section", "wrong-ingredient",
    "wrong-claim", "case-ingredient", "partial-ingredient", "wrong-columns",
])
def test_same_name_impostors_never_sign(store, signing, kind):
    raw = recipe()
    if kind == "empty":
        raw = b"# empty\n"
    elif kind == "metadata-only":
        raw = f"---\n关联产品: '[[{GRADE}]]'\n---\n".encode()
    elif kind == "performance-table":
        raw = "| 项目 | 条件 | 结果 | 供应商 | 备注 |\n| --- | --- | --- | --- | --- |\n| 附着 | 80 | 5B | | |\n".encode()
    elif kind == "missing-separator":
        raw = raw.replace(b"| --- | --- | --- | --- | --- |\n", b"")
    elif kind in ("empty-amount", "negative-amount", "reversed-range", "zero-identity"):
        value = {"empty-amount": "", "negative-amount": "-40",
                 "reversed-range": "40-10", "zero-identity": "0"}[kind]
        raw = raw.replace(b"| 40 |", f"| {value} |".encode())
    elif kind == "fenced":
        raw = b"```markdown\n" + raw + b"```\n"
    elif kind == "quoted":
        raw = b"\n".join(b"> " + line for line in raw.splitlines())
    elif kind == "comment":
        raw = b"<!--\n" + raw + b"-->\n"
    elif kind == "example-section":
        raw = raw.replace(f"# {GRADE}".encode(), "## 示例".encode())
    elif kind == "wrong-ingredient":
        raw = raw.replace(f"| [[{GRADE}]]".encode(), b"| water")
    elif kind == "wrong-claim":
        raw = raw.replace(f"关联产品: '[[{GRADE}]]'".encode(), b"association: false")
        raw = raw.replace(b"association: false", "关联产品: '[[RS9499]]'".encode())
    elif kind == "case-ingredient":
        raw = raw.replace(f"| [[{GRADE}]]".encode(), f"| [[{GRADE.lower()}]]".encode())
    elif kind == "partial-ingredient":
        raw = raw.replace(f"| [[{GRADE}]]".encode(), b"| [[RS9402]]")
    elif kind == "wrong-columns":
        raw = raw.replace("投入数(g)".encode(), "硬度".encode())
    assert_rejected_without_artifacts(store, signing, snapshot(raw=raw))


def test_application_same_name_still_rejected(store, signing):
    candidate = snapshot("配方")
    candidate.files[f"产品应用/{GRADE}.md"] = candidate.files.pop("产品应用/涂层.md")
    assert_rejected_without_artifacts(store, signing, candidate)


@pytest.mark.parametrize("old_name,new_name,grade", [
    ("RS9402-2B", "RS9402", "RS9402"),
    ("RS9402v02", "RS9402", "RS9402"),
    ("RS9402.3", "RS9402", "RS9402"),
    ("RS9402-2B-未知后缀", "RS9402-2B", GRADE),
    ("RS9402-2B-参考配方-方案02", "rs9402-2b", GRADE),
    ("RS9402-2B-参考配方-方案02", "RS9402", "RS9402"),
    ("Rſ9402-A-参考配方", "RS9402-A", "RS9402-A"),
    ("rs9402v02-参考配方", "RS9402v02", "RS9402v02"),
])
def test_product_suffixes_never_gain_recipe_rename_credit(store, signing, old_name, new_name, grade):
    products = list({GRADE, grade, old_name} - {GRADE + "-参考配方-方案02"})
    baseline = snapshot(old_name, recipe(grade), products)
    candidate = snapshot(new_name, recipe(grade), products)
    assert_rejected_without_artifacts(store, signing, candidate, baseline)


@pytest.mark.parametrize("kind", ["metadata-only", "unknown", "unregistered-suffix", "wrong-full-ingredient"])
def test_editorial_rename_requires_real_registered_identity(store, signing, kind):
    raw = recipe()
    products = [GRADE]
    name = GRADE
    if kind == "metadata-only":
        raw = f"---\n关联产品: '[[{GRADE}]]'\n---\n".encode()
    elif kind == "unknown":
        products = ["RS9499"]
    elif kind == "unregistered-suffix":
        name = GRADE + ".3"
    else:
        raw = recipe("RS9402")
        products.append("RS9402")
    baseline = snapshot(name + "-参考配方-方案02", raw, products)
    candidate = snapshot(name, raw, products)
    assert_rejected_without_artifacts(store, signing, candidate, baseline)


@pytest.mark.parametrize("grade", ["RS9402-2B", "RD9403-8b", "rs9404v02", "RS9405.3", "Rſ9406-A"])
@pytest.mark.parametrize("tail", ["-参考配方", "-参考配方-方案02", "-方案02"])
def test_exact_registered_complete_spelling_is_preserved(grade, tail):
    raw = recipe(grade)
    baseline = snapshot(grade + tail, raw, [grade])
    candidate = snapshot(grade, raw, [grade])
    from recipe_fixtures import known_table
    for snap in (baseline, candidate):
        p = next(p for p in snap.files if p.startswith("产品配方/"))
        snap.files[p] = known_table(snap.files[p], p.split("/")[-1][:-3])
    assert q.validate_snapshot(candidate, baseline)["summary"]["ok"]
    assert candidate.files[f"产品配方/{grade}.md"] == known_table(raw, grade)


def test_usage_title_and_plain_ingredient_are_legal(store, signing):
    raw = recipe().replace(f"[[{GRADE}]] |".encode(), f"{GRADE} |".encode())
    baseline = snapshot(GRADE + "-底-参考配方-方案02", raw)
    candidate = snapshot(GRADE + "-底", raw)
    from recipe_fixtures import known_table
    for snap in (baseline, candidate):
        p = next(p for p in snap.files if p.startswith("产品配方/"))
        snap.files[p] = known_table(snap.files[p], p.split("/")[-1][:-3])
    assert q.validate_snapshot(candidate, baseline)["summary"]["ok"]
    store.prepare(baseline, signing[0], public_scope_approved=True)
    store.promote()
    from rstone_data.build import deletion_approval, inventory
    result = store.prepare(candidate, signing[0], public_scope_approved=True,
        approval=deletion_approval(store.read(),inventory(candidate.files)))
    assert (store.public / store.package(result["sequence"])).is_file()


def test_bulk_archive_still_requires_exact_independent_approval(store, signing):
    from rstone_data.build import deletion_approval, inventory
    baseline = snapshot()
    for number in range(4):
        title = GRADE + "-亮-方案" + str(number)
        baseline.files["产品配方/"+title+".md"] = __import__("recipe_fixtures").typed(GRADE, title, recipe_id="SYNTH-ARCHIVE-"+str(number))
    store.prepare(baseline, signing[0], public_scope_approved=True)
    store.promote()
    candidate = snapshot()
    assert candidate.files[f"产品配方/{GRADE}.md"] == baseline.files[f"产品配方/{GRADE}.md"]
    signer = RecordingSigner(signing[0])
    before = {str(p): sha256(p.read_bytes()) for p in store.root.rglob("*") if p.is_file()}
    exact = deletion_approval(store.read(), inventory(candidate.files))
    candidate.warnings = [{"approval": exact}]
    with pytest.raises(DataError, match="exact approval required"):
        store.prepare(candidate, signer, public_scope_approved=True)
    assert signer.calls == 0
    assert store.pending() is None
    assert before == {str(p): sha256(p.read_bytes()) for p in store.root.rglob("*") if p.is_file()}
    assert not list(store.public.rglob("*.zip"))


@pytest.mark.parametrize("raw", [
    b"# empty\n",
    f"---\n关联产品: '[[{GRADE}]]'\n---\n".encode(),
    recipe().replace("投入数(g)".encode(), "附着力".encode()),
])
def test_signed_legacy_impostor_cannot_package(store, signing, raw):
    from test_release_quality import install_legacy
    install_legacy(store, signing[0], snapshot(raw=raw))
    before = {str(p): sha256(p.read_bytes()) for p in store.root.rglob("*") if p.is_file()}
    with pytest.raises(DataError, match="quality gate"):
        store.package(1)
    assert not list(store.public.rglob("*.zip"))
    assert before == {str(p): sha256(p.read_bytes()) for p in store.root.rglob("*") if p.is_file()}


def test_real_recipe_does_not_excuse_empty_product(store, signing):
    candidate = snapshot()
    candidate.files[f"产品列表/{GRADE}.md"] = "---\ntags: [水性, UV]\n---\n".encode()
    assert_rejected_without_artifacts(store, signing, candidate)


def test_recipe_rename_does_not_approve_rd_to_rs(store, signing):
    baseline = snapshot("RD9403-2B-参考配方", recipe("RD9403-2B"), ["RD9403-2B"])
    candidate = snapshot("RS9403-2B", recipe("RS9403-2B"), ["RS9403-2B"])
    candidate.files["产品列表/RS9403-2B.md"] = PRODUCT.replace(
        b"---\n", "---\n实验牌号: RD9403-2B\n".encode(), 1)
    assert_rejected_without_artifacts(store, signing, candidate, baseline)
