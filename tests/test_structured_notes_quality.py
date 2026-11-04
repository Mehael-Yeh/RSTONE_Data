"""Notes quality follows canonical block semantics, never escaped display Markdown."""
import pytest

from rstone_data import quality_gate as gate
from rstone_data.protocol import DataError
from test_identity_migration_security import artifacts
from test_quality_parent_review import RecordingSigner
from test_structured_publication import PATH, model, snapshot


def notes_snapshot(steps):
    value = model()
    value["content"] = [{"kind": "process", "label": "特别说明", "steps": steps}]
    return snapshot(value)


def findings(snap):
    return gate.check_notes(gate.snapshot_corpus(snap))


def test_structured_internal_notes_rejected_before_signing(store, signing):
    snap = notes_snapshot(["仅内部参考"])
    legacy = notes_snapshot(["仅内部参考"])
    legacy.files[PATH] = "## 特别说明\n- 仅内部参考\n".encode()
    assert {item["category"] for item in findings(legacy)} == {"notes-internal-phrase"}
    signer = RecordingSigner(signing[0])
    before = artifacts(store)
    with pytest.raises(DataError, match="quality gate.*notes"):
        store.prepare(snap, signer, public_scope_approved=True)
    assert signer.calls == 0 and artifacts(store) == before
    assert {item["category"] for item in findings(snap)} == {"notes-internal-phrase"}


DUPLICATE = "PC底材在100℃水煮3h后，上镀性5B、无掉面，面漆15μm。"


@pytest.mark.parametrize("content", [
    [{"kind": "process", "label": "特别说明", "steps": [DUPLICATE, DUPLICATE]}],
    [{"kind": "process", "label": "特别说明", "steps": [DUPLICATE]},
     {"kind": "note", "text": DUPLICATE}],
    [{"kind": "process", "label": "特别说明", "steps": [DUPLICATE]},
     {"kind": "process", "label": "工艺", "steps": ["先搅拌"]},
     {"kind": "process", "label": "特别说明", "steps": [DUPLICATE]}],
])
def test_structured_duplicate_notes_rejected(store, signing, content):
    value = model()
    value["content"] = content
    snap = snapshot(value)
    signer = RecordingSigner(signing[0])
    before = artifacts(store)
    with pytest.raises(DataError, match="quality gate.*notes"):
        store.prepare(snap, signer, public_scope_approved=True)
    assert signer.calls == 0 and artifacts(store) == before
    assert "notes-duplicated" in {item["category"] for item in findings(snap)}


@pytest.mark.parametrize("text,category", [
    ("不采用该册物理参数", "notes-internal-phrase"),
    ("不继承至量产配方", "notes-internal-phrase"),
    ("研发阶段数据", "notes-internal-phrase"),
    ("内部资料", "notes-internal-phrase"),
    ("采编记录", "notes-internal-phrase"),
    ("初稿", "notes-internal-phrase"),
    ("待核实", "notes-internal-phrase"),
    ("见画册", "notes-brochure"),
    ("页码3", "notes-brochure"),
    ("第 3 页", "notes-brochure"),
    ("见 P 3", "notes-brochure"),
    ("reference.pdf", "notes-brochure"),
    ("reference.indd", "notes-brochure"),
    ("reference.ai", "notes-brochure"),
    ("reference.psd", "notes-brochure"),
    ("reference.cdr", "notes-brochure"),
    ("", "notes-empty"),
])
def test_all_existing_notes_categories_block_before_signing(store, signing, text, category):
    snap = notes_snapshot([text])
    signer = RecordingSigner(signing[0])
    before = artifacts(store)
    assert category in {item["category"] for item in findings(snap)}
    with pytest.raises(DataError, match="quality gate.*notes"):
        store.prepare(snap, signer, public_scope_approved=True)
    assert signer.calls == 0 and artifacts(store) == before


@pytest.mark.parametrize("text", [
    "PC底材：100℃水煮3h后，上镀性5B、无掉面。",
    "PC底材：80℃水煮3h后未通过附着测试，涂膜脱落。",
    "测试条件：PC底材，100℃水煮3h。",
    "仅适用于PC底材，不适用于金属基材。",
])
def test_facts_failures_and_conditions_still_publish(store, signing, text):
    from rstone_data import recipe_codec as codec
    import zipfile

    snap = notes_snapshot([text])
    before = snap.files[PATH]
    signer = RecordingSigner(signing[0])
    result = store.prepare(snap, signer, public_scope_approved=True)
    package = store.public / store.package(result["sequence"])
    assert store.promote()["status"] == "published"
    assert signer.calls == 1
    with zipfile.ZipFile(package) as archive:
        assert archive.read("files/" + PATH) == before
    manifest = store.read()
    record = next(item for item in manifest["files"] if item["path"] == PATH)
    assert store.object(record) == before
    assert codec.emit(codec.parse(before)) == before


@pytest.mark.parametrize("location", ["cell", "preTable", "note", "process-label", "step"])
def test_literal_markdown_does_not_create_notes_sections(store, signing, location):
    value = model()
    literal = "## 特别说明\n| 项目 | 条件 |\n| --- | --- |\n仅内部参考"
    if location == "cell":
        value["rows"][0][4] = literal
    elif location == "preTable":
        value["preTable"] = [literal]
    elif location == "note":
        value["content"] = [{"kind": "note", "text": literal}]
    elif location == "process-label":
        value["content"] = [{"kind": "process", "label": literal, "steps": ["先搅拌"]}]
    else:
        value["content"] = [{"kind": "process", "label": "工艺", "steps": [literal]}]
    snap = snapshot(value)
    assert findings(snap) == []
    signer = RecordingSigner(signing[0])
    result = store.prepare(snap, signer, public_scope_approved=True)
    assert (store.public / store.package(result["sequence"])).is_file()
    assert store.promote()["status"] == "published"
    assert signer.calls == 1


@pytest.mark.parametrize("literal", [
    "## 工艺\n仅内部参考", "| 项目 | 条件 |\n仅内部参考",
    "```\n## 工艺\n```\n仅内部参考", "&#35;&#35; 工艺\n仅内部参考",
])
def test_literal_structure_cannot_hide_actual_notes(store, signing, literal):
    snap = notes_snapshot([literal])
    signer = RecordingSigner(signing[0])
    before = artifacts(store)
    assert "notes-internal-phrase" in {item["category"] for item in findings(snap)}
    with pytest.raises(DataError, match="quality gate.*notes"):
        store.prepare(snap, signer, public_scope_approved=True)
    assert signer.calls == 0 and artifacts(store) == before


def test_notes_follow_order_and_real_process_boundary(store, signing):
    value = model()
    value["content"] = [
        {"kind": "process", "label": "特别说明", "steps": ["测试条件：100℃水煮3h。"]},
        {"kind": "process", "label": "工艺", "steps": ["先搅拌"]},
        {"kind": "note", "text": "仅内部参考"},
    ]
    snap = snapshot(value)
    assert findings(snap) == []
    store.prepare(snap, signing[0], public_scope_approved=True)
    value["content"].insert(1, {"kind": "note", "text": "仅内部参考"})
    assert "notes-internal-phrase" in {item["category"] for item in findings(snapshot(value))}


def test_multiple_real_sections_check_second_and_report_review(store, signing):
    value = model()
    value["content"] = [
        {"kind": "process", "label": "特别说明", "steps": ["测试条件：100℃水煮3h。"]},
        {"kind": "process", "label": "特别说明", "steps": ["见画册"]},
    ]
    result = findings(snapshot(value))
    assert {item["category"] for item in result} == {"notes-multiple-blocks", "notes-brochure"}
    assert next(item for item in result if item["category"] == "notes-multiple-blocks")["severity"] == "review"
    with pytest.raises(DataError, match="quality gate.*notes"):
        store.prepare(snapshot(value), signing[0], public_scope_approved=True)


@pytest.mark.parametrize("operation", ["resume", "package", "promote", "rollback", "complete"])
@pytest.mark.parametrize("steps", [["仅内部参考"], ["见画册"], [DUPLICATE, DUPLICATE]])
def test_signed_bad_notes_cannot_escape_other_store_exits(store, signing, operation, steps):
    from test_release_quality import install_legacy

    snap = notes_snapshot(steps)
    install_legacy(store, signing[0], snap, pending=operation != "rollback", schema_version=2)
    signer = RecordingSigner(signing[0])
    before = artifacts(store)
    with pytest.raises(DataError, match="quality gate.*notes"):
        if operation == "resume":
            store.prepare(None, signer, public_scope_approved=True)
        elif operation == "package":
            store.package(1)
        elif operation == "promote":
            store.promote()
        elif operation == "complete":
            store.complete(lambda _: pytest.fail("verifier must not be called"))
        else:
            store.rollback(1, signer, public_scope_approved=True)
    assert signer.calls == 0 and artifacts(store) == before
