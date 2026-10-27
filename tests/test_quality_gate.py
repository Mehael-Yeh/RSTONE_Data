"""Synthetic-only regression tests for the pre-publication content gate.

Each check has a red case (the gate must reject the defect) and a green case
(the same corpus without the defect must pass). All fixtures are synthetic and
live in tmp_path; no business file is read or written here.
"""

import json

import pytest

from rstone_data.quality_gate import (
    InputError,
    input_digests,
    main,
    scan,
    verify_read_only,
)

VOCAB = {
    "水性", "油性", "粉末", "UV", "PU", "PUD", "双固化", "聚烯烃",
    "底漆", "中漆", "面漆", "一涂", "PC", "ABS", "PBT", "金属", "玻璃",
    "耐热", "耐化", "耐水煮", "快固", "PVD", "车灯", "准分子", "肤感",
    "2官", "3官", "4官", "6官", "8官", "10官",
}

DEFAULT_FIELDS = {
    "固含": "77",
    "羟值": "0",
    "官能度": "3",
    "硬度": "",
    "光泽(60°)": "",
    "水煮": "",
    "水接触角": "",
    "技术源": "",
    "对标": "",
    "粘度": "≤600 mPa·s（25℃）",
}


def product(tags=("水性", "UV"), fields=None, body=""):
    """Render a product document with a normal YAML frontmatter block."""
    values = dict(DEFAULT_FIELDS if fields is None else fields)
    lines = ["---", "tags:"]
    lines += [f"  - {tag}" for tag in tags]
    for key, value in values.items():
        lines.append(f"{key}:" + (f" {value}" if value != "" else ""))
    lines.append("---")
    text = "\n".join(lines) + "\n"
    return text + (body if body else "")


def write(root, relative, content):
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def corpus(root, products=None, apps=None, recipes=None, tds=None):
    """Materialise a two-repository source layout (Rstone/ + RstoneTDS/)."""
    (root / "Rstone").mkdir(parents=True, exist_ok=True)
    (root / "RstoneTDS").mkdir(parents=True, exist_ok=True)
    for name, content in (products or {"RS100": product()}).items():
        write(root, f"Rstone/产品列表/{name}.md", content)
    for name, content in (apps or {}).items():
        write(root, f"Rstone/产品应用/{name}.md", content)
    for name, content in (recipes or {}).items():
        write(root, f"Rstone/产品配方/{name}.md", content)
    for name, content in (tds or {}).items():
        write(root, f"RstoneTDS/{name}.TDS.md", content)
    return root


def write_vocabulary(tmp_path):
    path = tmp_path / "tag_vocabulary.json"
    path.write_text(json.dumps({"tags": {word: {"count": 1} for word in VOCAB}},
                              ensure_ascii=False), encoding="utf-8")
    return path


def case_insensitive_fs(root):
    """Report whether ``root`` aliases names that differ only in case (Windows, macOS)."""
    probe = root / "CaseProbe.tmp"
    probe.write_text("", encoding="utf-8")
    try:
        return (root / "caseprobe.tmp").exists()
    finally:
        probe.unlink()


def run(root, **options):
    options.setdefault("vocabulary", VOCAB)
    options.setdefault("expect_applications", 0)
    options.setdefault("expect_recipes", 0)
    return scan(root, **options)


def categories(report, check):
    return {violation["category"] for violation in report.check(check)["violations"]}


def reviews(report, check):
    return {warning["category"] for warning in report.check(check)["warnings"]}


# ---------------------------------------------------------------- 1 identity

def test_identity_duplicate_stem_rejected(tmp_path):
    if case_insensitive_fs(tmp_path):
        pytest.skip("case-only duplicate stems cannot coexist on a case-insensitive filesystem")
    root = corpus(tmp_path, {"RS100": product()})
    write(root, "Rstone/产品列表/rs100.md", product())
    report = run(root)
    assert not report.ok
    assert "stem-duplicate" in categories(report, "identity")


def test_identity_legacy_prefix_is_preserved(tmp_path):
    root = corpus(tmp_path, {"rs100": product()})
    report = run(root)
    assert report.check("identity")["ok"]


def test_identity_case_rewrite_rejected_against_baseline(tmp_path):
    baseline = corpus(tmp_path / "baseline", {"RS7915v02": product()})
    root = corpus(tmp_path / "candidate", {"RS7915V02": product()})
    report = run(root, baseline=baseline)
    assert "identity-case-rewritten" in categories(report, "identity")


def test_identity_dropped_suffix_rejected_against_baseline(tmp_path):
    baseline = corpus(tmp_path / "baseline", {"RS1170-2B": product()})
    root = corpus(tmp_path / "candidate", {"RS1170": product()})
    report = run(root, baseline=baseline)
    assert "identity-suffix-dropped" in categories(report, "identity")


def test_identity_merged_suffixes_rejected_against_baseline(tmp_path):
    baseline = corpus(tmp_path / "baseline", {"RS1170-2B": product(), "RS1170-8B": product()})
    root = corpus(tmp_path / "candidate", {"RS1170-2B": product()})
    report = run(root, baseline=baseline)
    assert "identity-suffix-merged" in categories(report, "identity")


def test_identity_preserved_names_pass(tmp_path):
    names = {"RS1173": product(), "RS7915v02": product(), "RD1011-5": product()}
    root = corpus(tmp_path, names)
    report = run(root, baseline=root)
    assert report.ok, report.json()["summary"]


def test_identity_tds_suffix_rejected(tmp_path):
    # Only the wrongly-cased file exists: the assertion holds on a
    # case-insensitive filesystem too, where a correctly-cased sibling would
    # collapse into the same directory entry.
    root = corpus(tmp_path)
    write(root, "RstoneTDS/RS100.TDS.MD", "# RS100\n")
    report = run(root)
    assert "identity-tds-suffix" in categories(report, "identity")


def test_nested_markdown_is_scanned(tmp_path):
    """sources._blobs() publishes nested Markdown, so the gate must validate it."""
    root = corpus(tmp_path, {"RS100": product()})
    write(root, "Rstone/产品列表/归档/RS200.md", product(tags=("双组分", "UV")))
    assert "tag-first" in categories(run(root), "tags")


def test_nested_non_markdown_and_dotted_directories_are_ignored(tmp_path):
    root = corpus(tmp_path, {"RS100": product()})
    write(root, "Rstone/产品列表/资源/示意图.png", "not markdown")
    write(root, "Rstone/产品列表/.cache/RS300.md", product(tags=("双组分", "UV")))
    report = run(root)
    assert report.check("tags")["ok"]
    assert report.check("identity")["ok"]


def test_nested_tds_file_is_rejected(tmp_path):
    """RstoneTDS publishes top-level files only; a nested TDS would ship unsigned by the gate."""
    root = corpus(tmp_path, {"RS100": product()})
    write(root, "RstoneTDS/归档/RS100.TDS.md", "# RS100\n")
    assert "identity-tds-nested" in categories(run(root), "identity")


# -------------------------------------------------------------------- 2 tags

def test_tags_first_position_rejected(tmp_path):
    root = corpus(tmp_path, {"RS100": product(tags=("双组分", "UV"))})
    assert "tag-first" in categories(run(root), "tags")


def test_tags_second_position_rejected(tmp_path):
    root = corpus(tmp_path, {"RS100": product(tags=("水性", "环氧"))})
    assert "tag-second" in categories(run(root), "tags")


def test_tags_duplicate_rejected(tmp_path):
    root = corpus(tmp_path, {"RS100": product(tags=("水性", "UV", "UV"))})
    assert "tag-duplicate" in categories(run(root), "tags")


def test_tags_empty_string_rejected(tmp_path):
    root = corpus(tmp_path, {"RS100": product(tags=("水性", "UV", ""))})
    assert "tag-empty" in categories(run(root), "tags")


def test_tags_unapproved_word_rejected(tmp_path):
    root = corpus(tmp_path, {"RS100": product(tags=("水性", "UV", "宇航级"))})
    assert "tag-unapproved" in categories(run(root), "tags")


@pytest.mark.parametrize("word,approved", [("4官", True), ("12官", False)])
def test_tags_numeric_functionality_needs_explicit_vocabulary_entry(tmp_path, word, approved):
    """A pattern like N官 must not auto-admit every numeral; only reviewed words do."""
    root = corpus(tmp_path, {"RS100": product(tags=("水性", "UV", word),
                                             fields={**DEFAULT_FIELDS,
                                                     "官能度": word.removesuffix("官")})})
    unapproved = "tag-unapproved" in categories(run(root), "tags")
    assert unapproved is not approved


def test_tags_price_like_word_rejected(tmp_path):
    root = corpus(tmp_path, {"RS100": product(tags=("水性", "UV", "30元/kg"))})
    report = run(root)
    assert "tag-forbidden" in categories(report, "tags")
    assert "30元/kg" not in json.dumps(report.json(), ensure_ascii=False)


def test_tags_customer_or_supplier_name_rejected(tmp_path):
    root = corpus(tmp_path, {"RS100": product(tags=("水性", "UV", "德固赛"))})
    assert "tag-forbidden" in categories(run(root), "tags")


def test_tags_clean_pass(tmp_path):
    root = corpus(tmp_path, {"RS100": product(tags=("水性", "UV", "3官", "金属"))})
    assert run(root).check("tags")["ok"], run(root).json()["checks"]


# -------------------------------------------------------- 3 tag vs. property

def test_tag_functionality_conflict_rejected(tmp_path):
    root = corpus(tmp_path, {"RS100": product(tags=("水性", "UV", "4官"),
                                             fields={**DEFAULT_FIELDS, "官能度": "6"})})
    assert "tag-field-conflict" in categories(run(root), "tag_field")


def test_quoted_functionality_value_conflict_rejected(tmp_path):
    """官能度: "6" is a quoted scalar the ingestion still accepts, so it must be compared."""
    root = corpus(tmp_path, {"RS100": product(tags=("水性", "UV", "3官"),
                                             fields={**DEFAULT_FIELDS, "官能度": '"6"'})})
    assert "tag-field-conflict" in categories(run(root), "tag_field")


def test_quoted_functionality_value_consistent_passes(tmp_path):
    root = corpus(tmp_path, {"RS100": product(tags=("水性", "UV", "3官"),
                                             fields={**DEFAULT_FIELDS, "官能度": '"3"'})})
    assert run(root).check("tag_field")["ok"]


def test_tag_functionality_without_value_is_a_backfill_review(tmp_path):
    """A tag carrying the only value is missing data, not a contradiction."""
    root = corpus(tmp_path, {"RS100": product(tags=("水性", "UV", "4官"),
                                             fields={**DEFAULT_FIELDS, "官能度": ""})})
    report = run(root)
    assert "tag-field-missing" in reviews(report, "tag_field")
    assert report.check("tag_field")["ok"]


def test_tag_functionality_consistent_pass(tmp_path):
    root = corpus(tmp_path, {"RS100": product(tags=("水性", "UV", "4官"),
                                             fields={**DEFAULT_FIELDS, "官能度": "4"})})
    assert run(root).check("tag_field")["ok"]


# ------------------------------------------------------- 4 field types / dregs

def test_field_list_value_rejected(tmp_path):
    root = corpus(tmp_path, {"RS100": product(fields={**DEFAULT_FIELDS, "固含": "[77, 78]"})})
    assert "field-type" in categories(run(root), "field_types")


def test_field_python_none_literal_rejected(tmp_path):
    root = corpus(tmp_path, {"RS100": product(fields={**DEFAULT_FIELDS, "技术源": "'None'"})})
    assert "field-residue" in categories(run(root), "field_types")


def test_field_quoted_residue_rejected(tmp_path):
    root = corpus(tmp_path, {"RS100": product(fields={**DEFAULT_FIELDS, "对标": "\"'RS100'\""})})
    assert "field-residue" in categories(run(root), "field_types")


def test_field_placeholder_text_rejected(tmp_path):
    root = corpus(tmp_path, {"RS100": product(fields={**DEFAULT_FIELDS, "技术源": "待补充"})})
    assert "field-placeholder" in categories(run(root), "field_types")


def test_pencil_hardness_grades_are_legitimate(tmp_path):
    """2H / HB / H(300g) are real hardness expressions: schema review, not an error."""
    for grade in ("2H", "HB", "6H", "H(300g)"):
        root = corpus(tmp_path / grade, {"RS100": product(
            fields={**DEFAULT_FIELDS, "硬度": f"'{grade}'"})})
        report = run(root)
        assert "field-type" not in categories(report, "field_types"), grade
        # A documented pencil grade is protocol-valid: no review item is needed.
        assert "schema-hardness-grade" not in reviews(report, "field_types"), grade


def test_functionality_inequality_is_supported_without_noise(tmp_path):
    root = corpus(tmp_path, {"RS100": product(fields={**DEFAULT_FIELDS, "官能度": "'>6'"})})
    report = run(root)
    assert report.check("field_types")["ok"]
    assert "schema-functionality-expression" not in reviews(report, "field_types")


def test_numeric_tolerance_expression_is_supported_without_noise(tmp_path):
    root = corpus(tmp_path, {"RS100": product(fields={**DEFAULT_FIELDS, "羟值": "'≤2'"})})
    report = run(root)
    assert report.check("field_types")["ok"]
    assert "schema-numeric-expression" not in reviews(report, "field_types")


def test_unparseable_field_text_still_rejected(tmp_path):
    root = corpus(tmp_path, {"RS100": product(fields={**DEFAULT_FIELDS, "固含": "'待补'"})})
    assert "field-type" in categories(run(root), "field_types")


def test_viscosity_missing_temperature_is_a_schema_review(tmp_path):
    """The existing protocol tolerates a bare range; flag it for schema confirmation."""
    root = corpus(tmp_path, {"RS100": product(fields={**DEFAULT_FIELDS, "粘度": "≤500 mPa·s"})})
    report = run(root)
    assert "schema-viscosity-temperature" in reviews(report, "field_types")
    assert report.check("field_types")["ok"]


def test_viscosity_cp_unit_is_a_schema_review(tmp_path):
    root = corpus(tmp_path, {"RS100": product(fields={**DEFAULT_FIELDS, "粘度": "≤500 cP（25℃）"})})
    assert "schema-viscosity-unit" in reviews(run(root), "field_types")


def test_viscosity_non_string_still_rejected(tmp_path):
    root = corpus(tmp_path, {"RS100": product(fields={**DEFAULT_FIELDS, "粘度": "[500, 600]"})})
    assert "field-type" in categories(run(root), "field_types")


def test_viscosity_readable_form_passes(tmp_path):
    root = corpus(tmp_path, {"RS100": product(fields={**DEFAULT_FIELDS, "粘度": "≤600 mPa·s（25℃）"})})
    assert run(root).check("field_types")["ok"]


def test_wiki_link_is_not_treated_as_dregs(tmp_path):
    root = corpus(tmp_path, {"RS100": product(fields={**DEFAULT_FIELDS, "技术源": '"[[RS100]]"'})})
    report = run(root)
    assert "field-residue" not in categories(report, "field_types")
    assert report.check("references")["ok"]


# ------------------------------------------------------------ 5 empty product

def test_empty_product_rejected(tmp_path):
    empty = {key: "" for key in DEFAULT_FIELDS}
    root = corpus(tmp_path, {"RS100": product(tags=("水性", "UV"), fields=empty)})
    assert "empty_product" in categories(run(root), "content")


def test_product_with_property_passes(tmp_path):
    empty = {key: "" for key in DEFAULT_FIELDS}
    root = corpus(tmp_path, {"RS100": product(fields={**empty, "固含": "77"})})
    assert run(root).check("content")["ok"]


def test_condition_qualified_property_counts_as_substance(tmp_path):
    """光泽(60°) is the same physical property as 光泽, as check_field_types() assumes."""
    empty = {key: "" for key in DEFAULT_FIELDS}
    root = corpus(tmp_path, {"RS100": product(fields={**empty, "光泽(60°)": "80"})})
    assert run(root).check("content")["ok"]


def test_product_with_technical_note_passes(tmp_path):
    empty = {key: "" for key in DEFAULT_FIELDS}
    body = "\n##### 特别说明\n- 喷涂型准分子体系；耐湿热和耐黄变性能佳。\n"
    root = corpus(tmp_path, {"RS100": product(fields=empty, body=body)})
    assert run(root).check("content")["ok"]


def test_product_with_body_fact_passes(tmp_path):
    empty = {key: "" for key in DEFAULT_FIELDS}
    body = "\n## 产品特性\n\n- 固化速度快，耐钢丝绒 5000 次。\n"
    root = corpus(tmp_path, {"RS100": product(fields=empty, body=body)})
    assert run(root).check("content")["ok"]


def test_marketing_only_body_is_not_substance(tmp_path):
    empty = {key: "" for key in DEFAULT_FIELDS}
    fluff = "本产品品质优良，性能稳定，深受广大客户好评，是您理想的选择，值得信赖。" * 4
    root = corpus(tmp_path, {"RS100": product(fields=empty, body="\n" + fluff + "\n")})
    assert "empty_product" in categories(run(root), "content")


# ------------------------------------------------------------- 6 references

def test_broken_reference_rejected(tmp_path):
    recipe = "| 原料编号 | 投入数(g) | 百分比(%) | 供应商 |\n| --- | --- | --- | --- |\n| [[RS9999]] | 10 | 10% | 锐石 |\n"
    root = corpus(tmp_path, recipes={"坏配方": recipe})
    assert "reference-broken" in categories(run(root), "references")


def test_reference_with_alias_and_heading_passes(tmp_path):
    app = '---\ntags: [PC]\n底漆: "[[RS100|底漆]]"\n面漆: "[[RS100#概述]]"\n---\n'
    root = corpus(tmp_path, apps={"方案": app})
    assert run(root).check("references")["ok"]


def test_external_link_is_not_an_internal_wiki_link(tmp_path):
    app = '---\ntags: [PC]\n资料: "[[https://example.com/tds|TDS 下载]]"\n---\n'
    root = corpus(tmp_path, apps={"方案": app})
    assert run(root).check("references")["ok"]


def test_non_product_metadata_links_are_not_references(tmp_path):
    """docs/SOURCE_AUDIT.md: engineer and provenance wiki links are not product references."""
    app = ('---\ntags: [PC]\n底漆: "[[RS100]]"\n工程师: "[[张三]]"\n'
           '来源: "[[微信记录]]"\n---\n')
    root = corpus(tmp_path, apps={"方案": app})
    assert run(root).check("references")["ok"]


def test_preexisting_broken_link_is_separated_from_new_ones(tmp_path):
    broken = "| 原料编号 | 投入数(g) | 百分比(%) | 供应商 |\n| --- | --- | --- | --- |\n| [[RS9999]] | 1 | 1% | 锐石 |\n"
    baseline = corpus(tmp_path / "baseline", recipes={"旧配方": broken})
    new_only = "| 原料编号 | 投入数(g) | 百分比(%) | 供应商 |\n| --- | --- | --- | --- |\n| [[RS8888]] | 1 | 1% | 锐石 |\n"
    root = corpus(tmp_path / "candidate", recipes={"旧配方": broken, "新配方": new_only})
    report = run(root, baseline=baseline)
    assert "reference-broken-preexisting" in reviews(report, "references")
    assert "reference-broken" in categories(report, "references")
    assert [item["field"] for item in report.check("references")["violations"]] == ["RS8888"]


def test_broken_techsource_reference_rejected(tmp_path):
    root = corpus(tmp_path, {"RS100": product(fields={**DEFAULT_FIELDS, "技术源": '"[[RS9999]]"'}),
                             "RS200": product()})
    assert "reference-broken-techsource" in categories(run(root), "references")


# -------------------------------------------------------------- 7 retention

def test_recipe_retention_underflow_rejected(tmp_path):
    recipes = {f"配方{i}": "| [[RS100]] | 1 | 100% | 锐石 |\n" for i in range(15)}
    root = corpus(tmp_path, recipes=recipes)
    report = run(root, expect_recipes=16, expect_applications=0)
    assert "retention-underflow" in categories(report, "retention")


def test_application_retention_underflow_rejected(tmp_path):
    apps = {f"方案{i}": '---\ntags: [PC]\n---\n' for i in range(20)}
    root = corpus(tmp_path, apps=apps)
    report = run(root, expect_recipes=0, expect_applications=21)
    assert "retention-underflow" in categories(report, "retention")


def test_baseline_diff_counts_reported(tmp_path):
    baseline = corpus(tmp_path / "baseline", {"RS100": product(), "RD200": product()},
                      recipes={"配方A": "| [[RS100]] | 1 | 100% | 锐石 |\n"})
    root = corpus(tmp_path / "candidate", {"RS100": product(body="\n## 用途\n\n- 塑胶基材 5000 次。\n"),
                                            "RD300": product()},
                  recipes={"配方A": "| [[RS100]] | 2 | 100% | 锐石 |\n"})
    report = run(root, baseline=baseline)
    diff = report.json()["baseline"]["diff"]
    assert diff["added"] >= 1 and diff["modified"] >= 1


def test_baseline_deletion_fails_and_allow_flag_downgrades(tmp_path):
    baseline = corpus(tmp_path / "baseline", {"RS100": product(), "RD200": product()})
    root = corpus(tmp_path / "candidate", {"RS100": product()})
    failing = run(root, baseline=baseline)
    assert "baseline-deleted" in categories(failing, "retention")
    allowed = run(root, baseline=baseline, allow_deletions=True)
    assert allowed.check("retention")["ok"], allowed.check("retention")


def test_input_mutation_detected(tmp_path):
    root = corpus(tmp_path, {"RS100": product()})
    before = input_digests(root)
    write(root, "Rstone/产品列表/RS100.md", product(fields={**DEFAULT_FIELDS, "固含": "78"}))
    after = input_digests(root)
    assert verify_read_only(before, after) is False
    assert verify_read_only(before, before) is True


def test_scan_reports_read_only(tmp_path):
    root = corpus(tmp_path, {"RS100": product()})
    assert run(root).json()["read_only"] is True


# ------------------------------------------------------------------- 8 notes

def test_notes_heading_without_items_rejected(tmp_path):
    root = corpus(tmp_path, {"RS100": product(body="\n##### 特别说明\n")})
    assert "notes-empty" in categories(run(root), "notes")


def test_notes_brochure_reference_rejected(tmp_path):
    body = "\n##### 特别说明\n- 见画册 RS-2019.pdf 第 3 页。\n"
    root = corpus(tmp_path, {"RS100": product(body=body)})
    assert "notes-brochure" in categories(run(root), "notes")


def test_notes_internal_process_phrase_rejected(tmp_path):
    body = "\n##### 特别说明\n- 该数据仅内部参考，不继承至量产配方。\n"
    root = corpus(tmp_path, {"RS100": product(body=body)})
    assert "notes-internal-phrase" in categories(run(root), "notes")


def test_notes_rnd_stage_phrase_rejected(tmp_path):
    body = "\n##### 特别说明\n- 研发阶段数据，水接触角 95°。\n"
    root = corpus(tmp_path, {"RS100": product(body=body)})
    assert "notes-internal-phrase" in categories(run(root), "notes")


def test_notes_duplicate_sentence_rejected(tmp_path):
    sentence = "100℃水煮3h，上镀性5B、无掉面，面漆15μm，UV1000mJ/cm²。"
    body = f"\n##### 特别说明\n- {sentence}\n- {sentence}\n"
    root = corpus(tmp_path, {"RS100": product(body=body)})
    assert "notes-duplicated" in categories(run(root), "notes")


def test_notes_second_block_also_checked(tmp_path):
    body = ("\n##### 特别说明\n- PC底材：100℃水煮3h，上镀性5B。\n"
            "\n##### 特别说明\n- 见画册 RS-2020.pdf 第 7 页。\n")
    root = corpus(tmp_path, {"RS100": product(body=body)})
    report = run(root)
    assert "notes-brochure" in categories(report, "notes")
    assert "notes-multiple-blocks" in reviews(report, "notes")


def test_notes_clean_pass(tmp_path):
    body = "\n##### 特别说明\n- PC底材：100℃水煮3h，上镀性5B、无掉面。\n"
    root = corpus(tmp_path, {"RS100": product(body=body)})
    assert run(root).check("notes")["ok"]


# ------------------------------------------------------------------- 9 price

def test_price_in_content_fails_without_echoing_value(tmp_path):
    root = corpus(tmp_path, {"RS100": product(body="\n##### 特别说明\n- 参考售价 88 元/kg。\n")})
    report = run(root)
    payload = json.dumps(report.json(), ensure_ascii=False)
    assert any(name.startswith("price") for name in categories(report, "price"))
    assert "元/kg" not in payload and "售价" not in payload


def test_price_in_path_reported_as_hash_only(tmp_path):
    root = corpus(tmp_path, {"RS100": product()})
    write(root, "Rstone/产品列表/RS200-88元.md", product())
    report = run(root)
    assert "path-sha256" in json.dumps(report.json(), ensure_ascii=False)
    assert "88元" not in json.dumps(report.json(), ensure_ascii=False)


def test_price_gate_unavailable_fails_closed(tmp_path, monkeypatch):
    root = corpus(tmp_path, {"RS100": product()})
    import rstone_data.quality_gate as module

    def unavailable(path, raw):
        raise ModuleNotFoundError("cryptography")

    monkeypatch.setattr(module, "check_payload", unavailable)
    report = run(root)
    assert "price-gate-unavailable" in categories(report, "price")
    assert not report.ok


def test_price_gate_applies_to_tds_documents(tmp_path):
    root = corpus(tmp_path, tds={"RS100": "# RS100\n\n单价：50 元/kg\n"})
    assert any(name.startswith("price") for name in categories(run(root), "price"))


# --------------------------------------------------------------------- CLI

def test_cli_writes_report_and_returns_non_zero(tmp_path):
    root = corpus(tmp_path, {"RS100": product(tags=("水性", "UV", "宇航级"))})
    report_path = tmp_path / "report.json"
    code = main(["--root", str(root), "--report", str(report_path),
                 "--vocabulary", str(write_vocabulary(tmp_path)),
                 "--expect-applications", "0", "--expect-recipes", "0"])
    assert code == 1
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert [check["id"] for check in payload["checks"]] == list(range(1, 10))
    assert payload["summary"]["ok"] is False


def test_cli_returns_zero_for_clean_corpus(tmp_path):
    root = corpus(tmp_path, {"RS100": product()})
    report_path = tmp_path / "report.json"
    assert main(["--root", str(root), "--report", str(report_path),
                 "--vocabulary", str(write_vocabulary(tmp_path)),
                 "--expect-applications", "0", "--expect-recipes", "0"]) == 0
    assert json.loads(report_path.read_text(encoding="utf-8"))["summary"]["ok"] is True


def test_cli_rejects_missing_root(tmp_path):
    with pytest.raises(InputError):
        main(["--root", str(tmp_path / "absent"), "--report", str(tmp_path / "r.json"),
              "--vocabulary", str(write_vocabulary(tmp_path))])
