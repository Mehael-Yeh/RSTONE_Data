"""Synthetic-only notes regressions exercising actual publication boundaries."""
import zipfile

import pytest

from rstone_data.protocol import DataError

from rstone_data import quality_gate as q
from rstone_data.sources import Snapshot


PATH = "产品列表/RS990001-TABLE.md"
HEADER = "---\ntags: [油性, UV]\n固含: 77\n---\n##### 特别说明\n"
TABLE_HEAD = "| 项目 | 基材/对象 | 测试条件 | 结果 |\n| --- | --- | --- | --- |\n"


def snapshot(notes):
    return Snapshot({
        PATH: (HEADER + notes).encode(),
        "产品应用/合成方案.md": "---\ntags: [PC]\n---\n".encode(),
        "产品配方/合成基方.md": "| 原料 | 投入g |\n| --- | --- |\n| 合成树脂 | 100 |\n".encode(),
        "产品TDS/RS990001-TABLE.TDS.md": b"# RS990001-TABLE\n",
    }, {"Mehael-Yeh/Rstone": "a" * 40, "Mehael-Yeh/RstoneTDS": "b" * 40})


def test_two_performance_tables_preserve_each_test_cell_in_package(store, signing):
    notes = ("###### 初始测试组\n" + TABLE_HEAD
             + "| 初始附着 | PC | 常温24h | 5B |\n\n"
             + "###### 水煮测试组\n" + TABLE_HEAD
             + "| 水煮附着 | PC | 100℃/2h | 5B |\n")
    candidate = snapshot(notes)
    corpus = q.snapshot_corpus(candidate)
    report = q.evaluate(corpus, None, q.load_vocabulary(None), 0, 0, False)
    result = store.prepare(candidate, signing[0], public_scope_approved=True)
    assert report.check("notes")["ok"]
    assert result["qualityGate"]["summary"]["ok"]
    with zipfile.ZipFile(store.public / store.package(result["sequence"])) as archive:
        assert archive.read("files/" + PATH) == candidate.files[PATH]


def test_performance_rows_are_complete_semantic_units_not_sentence_fragments(store, signing):
    notes = (TABLE_HEAD
             + "| 初始附着 | 合成PC试片 | 条件A；相同的长测试条件描述且只适用于合成基材；常温24h | 5B |\n"
             + "| 水煮附着 | 合成PC试片 | 条件B；相同的长测试条件描述且只适用于合成基材；100℃/2h | 5B |\n")
    candidate = snapshot(notes)
    result = store.prepare(candidate, signing[0], public_scope_approved=True)
    with zipfile.ZipFile(store.public / store.package(result["sequence"])) as archive:
        assert archive.read("files/" + PATH) == candidate.files[PATH]


@pytest.mark.parametrize("contexts", [
    ("###### 工艺甲", "###### 工艺乙"),
    ("**未加固化剂**", "**添加固化剂**"),
    ("工艺：60℃/10min", "工艺：80℃/30min"),
    ("配方：合成甲", "配方：合成乙"),
])
def test_same_result_row_in_distinct_test_contexts_is_preserved(store, signing, contexts):
    row = "| 初始附着 | PC | 常温24h | 5B |\n"
    candidate = snapshot(contexts[0] + "\n" + TABLE_HEAD + row + "\n"
                         + contexts[1] + "\n" + TABLE_HEAD + row)
    result = store.prepare(candidate, signing[0], public_scope_approved=True)
    with zipfile.ZipFile(store.public / store.package(result["sequence"])) as archive:
        assert archive.read("files/" + PATH) == candidate.files[PATH]


class CountingSigner:
    def __init__(self, delegate):
        self.delegate = delegate
        self.key_id = delegate.key_id
        self.calls = 0

    def sign(self, raw):
        self.calls += 1
        return self.delegate.sign(raw)


def rejected_without_artifacts(store, signing, candidate, check):
    signer = CountingSigner(signing[0])
    with pytest.raises(DataError, match="checks=" + check + "$"):
        store.prepare(candidate, signer, public_scope_approved=True)
    assert signer.calls == 0
    assert store.pending() is None
    assert not (store.public / "objects").exists()
    assert not list(store.public.rglob("*.sig"))
    assert not list(store.public.rglob("*.zip"))


PROSE = "合成试片在相同施工条件下固化后出现明显开裂和附着力下降。\n"
ROW = "| 附着 | PC | 24h | 5B |\n"


@pytest.mark.parametrize("middle", [
    "", "###### 第二小节\n", TABLE_HEAD + ROW + "\n",
    "```text\n", "~~~text\n", "**来源**：合成记录\n",
    "###### 来源\n", "\n##### 特别说明\n",
])
def test_ordinary_duplicate_prose_is_global_despite_markup(store, signing, middle):
    candidate = snapshot(PROSE + middle + PROSE)
    assert any(f["category"] == "notes-duplicated"
               for f in q.check_notes(q.snapshot_corpus(candidate)))
    rejected_without_artifacts(store, signing, candidate, "notes")


@pytest.mark.parametrize("notes", [
    TABLE_HEAD + ROW + ROW,
    TABLE_HEAD + ROW + "\n" + TABLE_HEAD + ROW,
    "###### 同一测试组\n" + TABLE_HEAD + ROW + "\n###### 同一测试组\n" + TABLE_HEAD + ROW,
    TABLE_HEAD + ROW + "\n来源：合成记录\n" + TABLE_HEAD + ROW,
    "| 备注 | 说明 |\n| --- | --- |\n| A | " + PROSE.strip() + " |\n"
    + "###### 第二组\n| 备注 | 说明 |\n| --- | --- |\n| A | " + PROSE.strip() + " |\n",
    "```text\n" + TABLE_HEAD + ROW + TABLE_HEAD + ROW + "```\n",
    TABLE_HEAD.replace("---", "--") + ROW + ROW,
    TABLE_HEAD.replace("| --- | --- | --- | --- |", "| --- | --- |") + ROW + ROW,
])
def test_table_content_and_fake_structures_are_not_blanket_exempt(store, signing, notes):
    candidate = snapshot(notes)
    assert any(f["category"] == "notes-duplicated"
               for f in q.check_notes(q.snapshot_corpus(candidate)))
    rejected_without_artifacts(store, signing, candidate, "notes")


@pytest.mark.parametrize("notes, category", [
    (TABLE_HEAD + "| 附着 | PC | 24h | 仅内部参考 |\n", "notes-internal-phrase"),
    (TABLE_HEAD + "| 附着 | PC | 24h | 见画册 |\n", "notes-brochure"),
    ("", "notes-empty"),
])
def test_original_notes_guards_still_inspect_tables(store, signing, notes, category):
    candidate = snapshot(notes)
    assert any(f["category"] == category for f in q.check_notes(q.snapshot_corpus(candidate)))
    rejected_without_artifacts(store, signing, candidate, "notes")


def test_empty_content_is_not_rescued_by_table_structure(store, signing):
    candidate = snapshot(TABLE_HEAD)
    candidate.files[PATH] = candidate.files[PATH].replace("固含: 77\n".encode(), b"")
    rejected_without_artifacts(store, signing, candidate, "content")


def test_price_in_performance_table_stays_pre_signing_rejected(store, signing):
    candidate = snapshot(TABLE_HEAD + "| 附着 | PC | 24h | 报价 987654 元/kg |\n")
    signer = CountingSigner(signing[0])
    with pytest.raises(DataError, match="price"):
        store.prepare(candidate, signer, public_scope_approved=True)
    assert signer.calls == 0 and store.pending() is None
    assert not (store.public / "objects").exists()
    assert not list(store.public.rglob("*.zip"))


def test_indented_code_is_not_a_markdown_table(store, signing):
    table = "\n".join("    " + line for line in (TABLE_HEAD + ROW).splitlines()) + "\n"
    candidate = snapshot("###### 甲\n" + table + "\n###### 乙\n" + table)
    rejected_without_artifacts(store, signing, candidate, "notes")


@pytest.mark.parametrize("table", [
    "项目 | 对象 | 条件 | 结果\n:--- | ---: | :---: | ---\n附着 | PC | 24h | 5B\n",
    TABLE_HEAD + "| 附着 | 合成PC\\|ABS | 24h | 5B |\n",
])
def test_valid_markdown_table_variants_remain_byte_exact(store, signing, table):
    candidate = snapshot("###### 工艺甲\n" + table + "\n###### 工艺乙\n" + table)
    result = store.prepare(candidate, signing[0], public_scope_approved=True)
    with zipfile.ZipFile(store.public / store.package(result["sequence"])) as archive:
        assert archive.read("files/" + PATH) == candidate.files[PATH]
