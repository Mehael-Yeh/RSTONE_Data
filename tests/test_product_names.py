"""Synthetic product identifiers only; never copy private source documents here."""

import pytest
from conftest import commit_all, run_git
from rstone_data.protocol import DataError
from rstone_data.sources import collect, validate_markdown


@pytest.mark.parametrize("name", [
    "938-1", "PP-9001", "W9003", "RE9002", "DB9001A", "SF 9001R", "RS Z-9001",
    "123", "X-1.2", "X_2", "X3 (A)", "X4+", "RD-DEMO", "rs-demo",
    "RS历史编号（停用）", "RD 1(旧)", "色浆-蓝90001",
])
def test_original_grade_name_is_not_normalized(name):
    raw = "---\ntags: []\n---\n# synthetic product\n"
    assert validate_markdown(f"产品列表/{name}.md", raw.encode()) == raw


@pytest.mark.parametrize("name", [
    "标签", "同义词规则", "标签同义词规则", "README", "readme-v2", "tags", "tags-2",
    "synonyms", "synonyms-v2", "rules", "rules2", "index-1", "changelog-2",
    "notes", "PP", "_X1", "-X1", "X1@misc", "X1=alias",
])
def test_control_documents_and_non_grade_names_are_rejected(name):
    with pytest.raises(DataError, match="invalid product name"):
        validate_markdown(f"产品列表/{name}.md", b"---\ntags: []\n---\n# synthetic control\n")


@pytest.mark.parametrize("path", [
    "产品列表/../X1.md", "产品列表/./X1.md", "产品列表//X1.md",
    "/产品列表/X1.md", "产品列表/X1\\evil.md", "产品列表/X1\x00.md",
    "产品列表/X1\n.md", "产品列表/X1\t.md", "产品列表/X1\x7f.md",
    "产品列表/.X1.md", "产品列表/CON.md", "outside/X1.md", "产品列表/X1.MD",
])
def test_grade_admission_does_not_bypass_logical_path_validation(path):
    with pytest.raises(DataError):
        validate_markdown(path, b"---\ntags: []\n---\n# synthetic product\n")


def test_external_grade_still_requires_valid_frontmatter():
    with pytest.raises(DataError, match="missing YAML frontmatter"):
        validate_markdown("产品列表/PP-9001.md", b"# synthetic product\n")


def test_external_case_collision_fails_closed(sources):
    rstone, tds = sources
    # Build both Git paths directly: Windows cannot hold case-distinct files.
    # collect reads committed blobs, so the fixture must exercise that same seam.
    oid = run_git(rstone, "rev-parse", "HEAD:产品列表/RS100.md").decode().strip()
    run_git(rstone, "config", "core.ignorecase", "false")
    for name in ("X123", "x123"):
        run_git(rstone, "update-index", "--add", "--cacheinfo",
                f"100644,{oid},产品列表/{name}.md")
    run_git(rstone, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
            "commit", "-m", "synthetic case collision")
    with pytest.raises(DataError, match="conflicting paths"):
        collect(rstone, tds)


def test_full_external_identifiers_remain_distinct(sources):
    rstone, tds = sources
    raw = b"---\ntags: []\n---\n# synthetic product\n"
    names = ("X123", "X123-A", "Y123", "RD123", "RS123")
    for name in names:
        (rstone / f"产品列表/{name}.md").write_bytes(raw)
    commit_all(rstone)
    snapshot = collect(rstone, tds)
    assert len(snapshot.files) == 10
    for name in names:
        assert snapshot.files[f"产品列表/{name}.md"] == raw


def test_numbered_rules_document_is_not_an_external_product():
    with pytest.raises(DataError, match="invalid product name"):
        validate_markdown("产品列表/rules-v2.md", b"---\ntags: []\n---\n# control document\n")


def test_collect_keeps_external_original_identifier_and_historical_bytes(sources):
    rstone, tds = sources
    raw = b"---\ntags: []\nstatus: discontinued\n---\n# synthetic historical product\r\n"
    (rstone / "产品列表/938-1.md").write_bytes(raw)
    commit_all(rstone)

    snapshot = collect(rstone, tds)

    assert snapshot.files["产品列表/938-1.md"] == raw
    assert len(snapshot.files) == 6
    assert not any("RS938-1" in path or "RD938-1" in path for path in snapshot.files)
