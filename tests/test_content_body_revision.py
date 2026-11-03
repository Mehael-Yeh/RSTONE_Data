"""Synthetic-only regressions for location-neutral qualitative product content."""
import pytest

from rstone_data import quality_gate as q
from rstone_data.sources import Snapshot


FACT = "用于合成基材的喷涂体系，固化后涂膜柔韧且耐磨。"
PATH = "产品列表/RS90001.md"


def findings(body, fields=""):
    raw = ("---\ntags: [油性, UV]\n工程师: 合成工程师\n" + fields + "---\n" + body).encode()
    return q.check_content(q.snapshot_corpus(Snapshot({PATH: raw}, {})))


def test_same_qualitative_fact_is_not_empty_only_because_it_is_plain_body():
    assert not q.FACT_UNIT.search(FACT)
    assert q._has_fact(FACT)
    plain = findings(FACT)
    note = findings("##### 特别说明\n" + FACT)
    expected = [{"path": PATH, "category": "content-without-property", "severity": "review"}]
    assert plain == note == expected


@pytest.mark.parametrize("body", [
    "未验证：" + FACT,
    "尚未实测：" + FACT,
    "待验证：" + FACT,
    "推测：" + FACT,
    "示例：" + FACT,
    "来源：合成喷涂体系与固化记录，文件已归档。",
    "分类：油性、UV、喷涂体系、耐磨、高光、面漆。",
    "体系：水性、UV、喷涂、耐磨、高光、面漆。",
    "标签：水性、UV、喷涂、耐磨、高光、面漆。",
    "采编记录：固化和附着测试文档已经整理完成。",
    "合成固化与喷涂技术资料.pdf，已存档。",
])
def test_new_plain_fact_path_does_not_admit_nonassertional_text(body):
    assert findings(body) == [{"path": PATH, "category": "empty_product", "severity": "failure"}]


@pytest.mark.parametrize("body", [
    "```text\n" + FACT + "\n```",
    "~~~~\n" + FACT + "\n~~~~",
    "```\n" + FACT,
    "    " + FACT,
    "\t" + FACT,
    "`" + FACT + "`",
    "> " + FACT,
    "<!-- " + FACT + " -->",
    "<!--\n" + FACT + "\n-->",
    "[" + FACT + "](https://example.invalid/source)",
    "# " + FACT,
    "## 示例\n" + FACT,
    "## 来源\n" + FACT,
    "## 未验证\n" + FACT,
    "待验证：\n" + FACT,
    "来源：\n" + FACT,
])
def test_new_plain_fact_path_ignores_non_prose_and_excluded_sections(body):
    assert findings(body) == [{"path": PATH, "category": "empty_product", "severity": "failure"}]


@pytest.mark.parametrize("body", [
    "水性 / UV / 喷涂 / 耐磨 / 高光 / 面漆",
    "耐磨、喷涂、高光、面漆、UV体系",
    "本产品施工方便，品质优良，是您理想的选择，值得信赖。",
    "可能具有优良的固化和附着表现，尚需核验。",
    "假设在合成基材上用于喷涂体系。",
    "预计在合成基材上的固化与附着表现良好。",
    "这是一个供采编使用的模板正文，涉及固化与喷涂体系。",
    "样品仅供测试，合成基材喷涂体系尚未确认。",
    "固化和附着良好。\n未验证：\n" + FACT,
])
def test_new_plain_fact_path_does_not_promote_keyword_lists_or_marketing(body):
    assert findings(body) == [{"path": PATH, "category": "empty_product", "severity": "failure"}]


@pytest.mark.parametrize("body", ["", "\n", "待补充", "TBD", "暂无", "-", "油性 UV 面漆",
                                        "本产品品质优良，性能稳定，值得信赖。"])
def test_empty_placeholder_and_classification_controls_stay_empty(body):
    assert findings(body) == [{"path": PATH, "category": "empty_product", "severity": "failure"}]


@pytest.mark.parametrize("body", [FACT, "- " + FACT, "# RS90001\n\n" + FACT,
                                        "## 技术记录\n" + FACT,
                                        "```\nnot a fact\n```\n\n" + FACT,
                                        "## 示例\nsynthetic\n## 正文\n" + FACT])
def test_real_prose_remains_eligible_without_a_property(body):
    assert findings(body) == [{"path": PATH, "category": "content-without-property", "severity": "review"}]


def test_qualitative_body_reaches_the_real_prepare_and_package(store, signing):
    from test_release_quality import clean_snapshot
    snapshot = clean_snapshot()
    snapshot.files["产品列表/RS100.md"] = ("---\ntags: [油性, UV]\n---\n" + FACT).encode()
    result = store.prepare(snapshot, signing[0], public_scope_approved=True)
    assert result["qualityGate"]["summary"]["ok"]
    assert any(w["category"] == "content-without-property" for w in result["qualityGate"]["warnings"])
    assert (store.public / store.package(result["sequence"])).is_file()


@pytest.mark.parametrize("body", ["", "来源：合成固化与喷涂测试文档已经归档。",
                                        "未验证：" + FACT,
                                        "```text\n" + FACT + "\n```",
                                        "分类：油性、UV、喷涂体系、耐磨、高光、面漆。"])
@pytest.mark.parametrize("operation", ["prepare", "resume", "promote", "complete", "package", "rollback"])
def test_ineligible_plain_body_stays_blocked_at_every_store_exit(store, signing, body, operation):
    from rstone_data.protocol import DataError
    from test_quality_parent_review import RecordingSigner
    from test_release_quality import clean_snapshot, install_legacy
    snapshot = clean_snapshot()
    snapshot.files["产品列表/RS100.md"] = ("---\ntags: [油性, UV]\n---\n" + body).encode()
    if operation != "prepare":
        install_legacy(store, signing[0], snapshot, pending=operation in ("resume", "promote", "complete"))
    before = {p.relative_to(store.root).as_posix(): p.read_bytes()
              for d in (store.public, store.state) if d.exists() for p in d.rglob("*") if p.is_file()}
    signer = RecordingSigner(signing[0])
    verifier_calls = []
    with pytest.raises(DataError, match="quality gate"):
        if operation == "prepare":
            store.prepare(snapshot, signer, public_scope_approved=True)
        elif operation == "resume":
            store.prepare(None, signer, public_scope_approved=True)
        elif operation == "promote":
            store.promote(lambda pointer: verifier_calls.append(pointer))
        elif operation == "complete":
            store.complete(lambda pointer: verifier_calls.append(pointer))
        elif operation == "package":
            store.package(1)
        else:
            store.rollback(1, signer, public_scope_approved=True)
    after = {p.relative_to(store.root).as_posix(): p.read_bytes()
             for d in (store.public, store.state) if d.exists() for p in d.rglob("*") if p.is_file()}
    assert signer.calls == 0 and verifier_calls == []
    assert after == before  # zero new/changed objects, signatures, pending or ZIP
