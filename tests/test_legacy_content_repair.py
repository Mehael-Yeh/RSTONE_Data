"""Synthetic-only, location-neutral visible-fact admission regressions."""
import pytest

from rstone_data import quality_gate as q
from rstone_data.protocol import DataError
from rstone_data.sources import Snapshot

PATH = "产品列表/RS99123401.md"
HEADER = "---\ntags: [油性, UV]\n工程师: 合成工程师\n实验牌号: RD99123401-2\n---\n"
FACT = "用于合成基材的喷涂体系，固化后涂膜柔韧且耐磨。"
EMPTY = [{"path": PATH, "category": "empty_product", "severity": "failure"}]
REVIEW = [{"path": PATH, "category": "content-without-property", "severity": "review"}]
EXACT = [
    "##### 特别说明\n未验证：" + FACT,
    "## 产品特性\n本产品品质优良，表现卓越，值得信赖。",
    "```text\n示例硬度 2H\n```",
    "##### 特别说明\n```text\n" + FACT + "\n```",
]
VARIANTS = [
    "#####特别说明\n未验证：" + FACT,
    "##### 特别说明\n**未经核验**：" + FACT,
    "## 产品用途\n深耕喷涂与固化领域，携手伙伴共创美好未来。",
    "## 产品特性\n来源：硬度测试 2H，合成喷涂记录已归档。",
    "## 特别说明\n~~~text\n硬度 2H\n~~~",
    "## 特别说明\n````text\n硬度 2H\n```\n" + FACT + "\n````",
    "##### 特别说明\n    硬度 2H",
    "##### 特别说明\n\t" + FACT,
    "##### 特别说明\n> " + FACT,
    "## 物理性能\n<!-- 硬度 2H -->",
    "## 产品用途\n<!--\n" + FACT + "\n-->",
    "## 产品特性\n`硬度 2H`",
    "## 产品特性\n[硬度 2H](https://example.invalid)",
    "## **来源**\n### 产品特性\n硬度 2H",
    "## 样例正文\n### 产品用途\n硬度 2H",
    "##### 特别说明\n样例正文：" + FACT,
]


def findings(body, fields=""):
    raw = (HEADER.replace("---\n", "---\n" + fields, 1) + body).encode()
    snapshot = Snapshot({PATH: raw}, {})
    before = dict(snapshot.files)
    result = q.check_content(q.snapshot_corpus(snapshot))
    assert snapshot.files == before
    return result


def full_snapshot(body):
    return Snapshot({
        PATH: (HEADER + body).encode(),
        "产品应用/synthetic-application.md": "---\ntags: [PC]\n---\nSynthetic application.\n".encode(),
        "产品配方/RS99123401-底.md": __import__("recipe_fixtures").typed("RS99123401", "RS99123401-底"),
        "产品TDS/RS99123401.TDS.md": b"# RS99123401\nSynthetic technical data.\n",
    }, {"Mehael-Yeh/Rstone": "a" * 40, "Mehael-Yeh/RstoneTDS": "b" * 40})


def artifact_bytes(store):
    return {p.relative_to(store.root).as_posix(): p.read_bytes()
            for d in (store.public, store.state) if d.exists()
            for p in d.rglob("*") if p.is_file()}


@pytest.mark.parametrize("body", EXACT + VARIANTS)
def test_r1_non_facts_cannot_use_legacy_early_true(body):
    assert findings(body) == EMPTY


@pytest.mark.parametrize("operation", ["prepare", "resume", "promote", "complete", "package", "rollback"])
@pytest.mark.parametrize("body", EXACT)
def test_r1_exact_four_reject_content_at_real_six_exits(store, signing, operation, body):
    from test_quality_parent_review import RecordingSigner
    from test_release_quality import install_legacy
    snapshot = full_snapshot(body)
    report = q.evaluate(q.snapshot_corpus(snapshot), None, q.load_vocabulary(None), 0, 0, False).json()
    assert report["summary"]["failed_checks"] == ["content"]
    if operation != "prepare":
        install_legacy(store, signing[0], snapshot, pending=operation in ("resume", "promote", "complete"), schema_version=2)
    before = artifact_bytes(store)
    signer, verified = RecordingSigner(signing[0]), []
    with pytest.raises(DataError, match="checks=content$"):
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
    assert artifact_bytes(store) == before


@pytest.mark.parametrize("body", [FACT, "硬度 2H", "固含 77±2%", "粘度 100-200 mPa·s（25℃）", "本号在合成基材上固化后涂膜开裂，附着表现差。", "喷涂型准分子体系；耐湿热和耐黄变性能佳。"])
@pytest.mark.parametrize("heading", ["", "##### 特别说明\n", "## 产品用途\n"])
def test_r1_eligible_numeric_and_qualitative_facts_are_location_neutral(body, heading):
    assert findings(heading + body) == REVIEW


VALID_R2 = [
    "本号未通过水煮。",
    "附着差。",
    "合成基材上附着力不足。",
    "复配树脂，不是单一原树脂的直接换名；仅在合成板材上验证过水煮。",
    "低表面能聚烯烃体系的配方选材；粘接力好，不含卤素。",
    "工程师推荐用于合成基材的底漆，但尚未实测。",
    "工程师建议用于合成基材的面漆，未验证附着表现。",
    "未验证：" + FACT + "\n\n本号未通过水煮。",
    "未验证：" + FACT + "本号在合成基材上固化后涂膜开裂，附着表现差。",
    "来源：合成记录。" + FACT,
    "## 来源\n合成记录。\n## 产品特性\n硬度 2H",
    "```text\n硬度 2H\n```\n" + FACT,
    "<!-- 合成例文 -->\n" + FACT,
    "## 正式技术数据\n| 性能 | 指标 |\n| --- | --- |\n| 固含 | 77±2% |",
    "铅笔硬度 HB。",
    "粘度 200 cP。",
]


@pytest.mark.parametrize("body", VALID_R2)
@pytest.mark.parametrize("heading", ["", "##### 特别说明\n", "## 产品用途\n"])
def test_r2_independent_facts_negative_results_and_recommendation_status(body, heading):
    assert findings(heading + body) == REVIEW


@pytest.mark.parametrize("body", EXACT + ["未验证：硬度 2H", "未验证：工程师推荐用于合成基材的底漆。"])
def test_r2_valid_physical_field_is_not_poisoned_by_an_unverified_body(body):
    assert findings(body, '固含: "77±2"\n') == []


@pytest.mark.parametrize("body", VALID_R2)
def test_r2_valid_body_and_experimental_field_package_byte_for_byte(store, signing, body):
    import zipfile
    snapshot = full_snapshot(body)
    before = dict(snapshot.files)
    ready = store.prepare(snapshot, signing[0], public_scope_approved=True)
    assert ready['qualityGate']['summary']['ok']
    with zipfile.ZipFile(store.public / store.package(ready['sequence'])) as z:
        actual = {n.removeprefix('files/'): z.read(n) for n in z.namelist() if n.startswith('files/')}
    assert actual == before == snapshot.files
    assert '实验牌号: RD99123401-2'.encode() in actual[PATH]


@pytest.mark.parametrize('body', [
    '> 合成引用\n' + FACT,
    '## 产品特性\n> 合成引用\n硬度 2H',
    '## 特别说明\n`合成代码\n' + FACT + '\n`',
    '## 特别说明\n``合成代码\n硬度 2H\n``',
    '##来源\n硬度 2H',
    '##未验证\n' + FACT,
])
def test_r3_multiline_quoted_code_and_compact_excluded_headings_are_not_facts(body):
    assert findings(body) == EMPTY


@pytest.mark.parametrize('body', [
    '> 合成引用\n\n' + FACT,
    '`合成代码\n硬度 2H\n`\n\n' + FACT,
    '##来源\n合成记录。\n##正文\n' + FACT,
])
def test_r3_independent_prose_after_quoted_or_compact_excluded_sections_survives(body):
    assert findings(body) == REVIEW
