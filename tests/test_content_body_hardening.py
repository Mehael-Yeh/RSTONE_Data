"""Synthetic-only regressions for the new ordinary-body content admission path."""
import pytest

from rstone_data import quality_gate as q
from rstone_data.sources import Snapshot


FACT = "用于合成基材的喷涂体系，固化后涂膜柔韧且耐磨。"
NEGATIVE = "本号在合成基材上固化后涂膜开裂，附着表现差。"
PATH = "产品列表/RS990001.md"
HEADER = "---\ntags: [油性, UV]\n工程师: 合成工程师\n---\n"
EMPTY = [{"path": PATH, "category": "empty_product", "severity": "failure"}]
REVIEW = [{"path": PATH, "category": "content-without-property", "severity": "review"}]


def findings(body):
    snapshot = Snapshot({PATH: (HEADER + body).encode()}, {})
    before = dict(snapshot.files)
    result = q.check_content(q.snapshot_corpus(snapshot))
    assert snapshot.files == before  # detection normalization must not edit payload bytes
    return result


@pytest.mark.parametrize("body", [
    "**来源**：合成喷涂与固化记录，文件已归档。",
    "__来源__：合成涂布与固化记录，文件已归档。",
    "*资料来源*：合成固化和附着测试记录，文件已归档。",
    "***技术源***：合成喷涂与固化记录，文件已归档。",
    "**参考资料：**合成喷涂与固化记录，文件已归档。",
    "- **来源**：合成喷涂与固化记录，文件已归档。",
    "1. __来源__：合成喷涂与固化记录，文件已归档。",
    "## **来源**\n" + FACT,
    "## __资料来源__\n" + NEGATIVE,
    "**来源**：\n" + FACT,
    "**来**源：合成喷涂与固化记录，文件已归档。",
])
def test_r1_markdown_label_is_still_provenance_not_a_fact(body):
    assert findings(body) == EMPTY


@pytest.mark.parametrize("body", [
    "**来源**：合成记录。\n\n" + FACT,
    "__来源__：合成记录。\n\n" + NEGATIVE,
    "## **来源**\n合成记录。\n## 正文\n" + FACT,
    "**用于合成基材的喷涂体系，固化后涂膜柔韧且耐磨。**",
])
def test_r1_emphasis_and_independent_facts_remain_eligible(body):
    assert findings(body) == REVIEW


R2_ONLY_BODIES = [
    "未经核验：" + FACT,
    "样例正文：" + FACT,
    "深耕喷涂与固化领域，携手伙伴共创美好未来。",
    "未核实：" + NEGATIVE,
    "未经确认：" + FACT,
    "待核验：" + FACT,
    "**未经核验**：" + FACT,
    "__样例正文__：" + FACT,
    "范例：" + NEGATIVE,
    "例文：" + FACT,
    "## 样例正文\n" + FACT,
    "## **未经核验**\n" + FACT,
    "样例正文：\n" + FACT,
    "未经核验：\n" + NEGATIVE,
    "聚焦耐磨与附着事业，凝聚伙伴共享明天。",
    "扎根涂布固化行业，共筑品质新篇章。",
    "喷涂固化领域一路同行，携手合作伙伴共赢。",
    "长期专注耐水耐磨与附着技术，携手伙伴共创辉煌。",
    "深耕用于合成基材的喷涂领域，携手伙伴共创未来。",
]


@pytest.mark.parametrize("body", R2_ONLY_BODIES)
def test_r2_explicit_nonassertions_and_domain_slogans_stay_empty(body):
    assert findings(body) == EMPTY


VALID_BODIES = [
    FACT,
    NEGATIVE,
    "该树脂可用于合成底材的喷涂体系。",
    "本品不适用于合成基材的底漆。",
    "本号在合成底材上附着力不足，固化后涂膜发白。",
    "本号在合成基材上未通过水煮试验，涂膜发白。",
    "本品需要搭配固化剂使用，不能直接喷涂。",
    "固化后涂层光泽低，耐水性差。",
    "用于合成内饰高光黑面漆。产品描述硬度高、耐磨性好。",
    "来源：合成记录。\n\n" + FACT,
    "**来源**：合成记录。\n\n" + NEGATIVE,
    "未经核验：这是历史样例。\n\n" + FACT,
    "样例正文：历史样例。\n\n" + NEGATIVE,
    "## 样例正文\n历史文本。\n## 正文\n" + FACT,
    "## 未经核验\n历史文本。\n## 正文\n" + NEGATIVE,
    "本号的喷涂体系专用于合成基材，固化后涂膜柔韧。",
    "合成基材上固化后的附着表现差，不宜直接喷涂。",
]


@pytest.mark.parametrize("body", VALID_BODIES)
def test_r2_preserves_asserted_use_negative_result_and_independent_fact(body):
    assert findings(body) == REVIEW


EXACT_REGRESSIONS = [
    "**来源**：合成喷涂与固化记录，文件已归档。",
    "未经核验：用于合成基材的喷涂体系，固化后涂膜柔韧且耐磨。",
    "样例正文：用于合成基材的喷涂体系，固化后涂膜柔韧且耐磨。",
    "深耕喷涂与固化领域，携手伙伴共创美好未来。",
]


def artifact_hashes(store):
    import hashlib
    return {p.relative_to(store.root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for folder in (store.public, store.state) if folder.exists()
            for p in folder.rglob("*") if p.is_file()}


@pytest.mark.parametrize("body", EXACT_REGRESSIONS)
@pytest.mark.parametrize("operation", ["prepare", "resume", "promote", "complete", "package", "rollback"])
def test_exact_regressions_rejected_at_six_real_exits_with_no_effect(store, signing, body, operation):
    from rstone_data.protocol import DataError
    from test_quality_parent_review import RecordingSigner
    from test_release_quality import clean_snapshot, install_legacy
    snapshot = clean_snapshot()
    snapshot.files["产品列表/RS100.md"] = (HEADER + body).encode()
    report = q.evaluate(q.snapshot_corpus(snapshot), None, q.load_vocabulary(None), 0, 0, False).json()
    # No early identity, namespace, price or retention failure may mask this check.
    assert report["summary"]["failed_checks"] == ["content"]
    assert q.check_content(q.snapshot_corpus(snapshot)) == [
        {"path": "产品列表/RS100.md", "category": "empty_product", "severity": "failure"}]
    if operation != "prepare":
        install_legacy(store, signing[0], snapshot, pending=operation in ("resume", "promote", "complete"))
    before = artifact_hashes(store)
    signer, verified = RecordingSigner(signing[0]), []
    with pytest.raises(DataError, match="checks=content"):
        if operation == "prepare":
            store.prepare(snapshot, signer, public_scope_approved=True)
        elif operation == "resume":
            store.prepare(None, signer, public_scope_approved=True)
        elif operation == "promote":
            store.promote(lambda pointer: verified.append(pointer))
        elif operation == "complete":
            store.complete(lambda pointer: verified.append(pointer))
        elif operation == "package":
            store.package(1)
        else:
            store.rollback(1, signer, public_scope_approved=True)
    assert signer.calls == 0 and verified == []
    assert artifact_hashes(store) == before


@pytest.mark.parametrize("body", [FACT, NEGATIVE, "**来源**：合成记录。\n\n" + FACT])
def test_eligible_body_packages_its_original_markdown_bytes(store, signing, body):
    import zipfile
    from test_release_quality import clean_snapshot
    snapshot = clean_snapshot()
    snapshot.files["产品列表/RS100.md"] = (HEADER + body).encode()
    result = store.prepare(snapshot, signing[0], public_scope_approved=True)
    assert result["qualityGate"]["summary"]["ok"]
    with zipfile.ZipFile(store.public / store.package(result["sequence"])) as zipped:
        assert {p.removeprefix("files/"): zipped.read(p) for p in zipped.namelist()
                if p.startswith("files/")} == snapshot.files


@pytest.mark.parametrize("body", [
    "##### 特别说明\n未验证：" + FACT,
    "## 产品特性\n本产品品质优良，表现卓越，值得信赖。",
    "```text\n示例硬度 2H\n```",
    "##### 特别说明\n```text\n" + FACT + "\n```",
])
def test_former_legacy_gaps_are_now_content_rejections(store, signing, body):
    # Owner-authorized repair: the four inherited gaps that used to be
    # characterized as publishable must now be rejected as content at the
    # real store exit, with no artifacts and no signing side effects.
    from rstone_data.protocol import DataError
    from test_quality_parent_review import RecordingSigner
    from test_release_quality import clean_snapshot
    assert findings(body) == EMPTY
    snapshot = clean_snapshot()
    snapshot.files["产品列表/RS100.md"] = (HEADER + body).encode()
    before = artifact_hashes(store)
    signer = RecordingSigner(signing[0])
    with pytest.raises(DataError, match="checks=content$"):
        store.prepare(snapshot, signer, public_scope_approved=True)
    assert signer.calls == 0
    assert artifact_hashes(store) == before
