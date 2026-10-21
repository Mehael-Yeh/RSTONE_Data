from pathlib import Path

import pytest

from conftest import commit_all
from rstone_data.build import changes, check_deletions, deletion_approval, inventory, mapping_digest
from rstone_data.protocol import DataError, sha256
from rstone_data.sources import collect, validate_markdown


def test_ingest_original_git_bytes_and_ignore_working_tree(sources):
    rstone, tds = sources
    before = collect(rstone, tds)
    (rstone / "产品列表/RS100.md").write_bytes(b"uncommitted content must not ship")
    after = collect(rstone, tds)
    assert after.files == before.files
    assert len(after.files) == 5
    assert after.files["产品列表/RS100.md"].endswith(b"\r\n")
    assert not any(".env" in path for path in after.files)


def test_add_modify_delete_rename_move_and_deduplicate(sources):
    rstone, tds = sources
    first = collect(rstone, tds)
    old = {"sequence": 1, "files": inventory(first.files)}
    raw = first.files["产品列表/RS100.md"]
    (rstone / "产品列表/RS100.md").rename(rstone / "产品列表/RS101.md")
    target = rstone / "产品应用/新目录"
    target.mkdir()
    (rstone / "产品应用/方案.md").rename(target / "方案.md")
    (rstone / "产品列表/RD200.md").write_bytes(b"---\ntags: [UV]\n---\nchanged\n")
    (rstone / "产品列表/RS300.md").write_bytes(raw)
    commit_all(rstone)
    second = collect(rstone, tds)
    diff = changes(old, inventory(second.files))
    assert len(diff["added"]) == 3
    assert len(diff["deleted"]) == 2
    assert len(diff["modified"]) == 1
    assert len(diff["unchanged"]) == 2
    assert sha256(second.files["产品列表/RS101.md"]) == sha256(raw)
    assert mapping_digest(inventory(first.files)) != mapping_digest(inventory(second.files))
    approval = deletion_approval(old, inventory(second.files))
    with pytest.raises(DataError, match="bulk deletion"):
        check_deletions(old, inventory(second.files))
    check_deletions(old, inventory(second.files), approval=approval)
    with pytest.raises(DataError):
        check_deletions(old, inventory(second.files), approval="2:" + approval.split(":")[1])


def test_source_failure_and_empty_directory(sources, tmp_path):
    rstone, tds = sources
    with pytest.raises(DataError):
        collect(tmp_path / "absent", tds)
    (rstone / "产品应用/方案.md").unlink()
    commit_all(rstone)
    with pytest.raises(DataError, match="directory missing"):
        collect(rstone, tds)


@pytest.mark.parametrize("raw", [b"\xff", b"---\ntags: [UV\n---\n", b"---\ntags: 7\n---\n",
                                b"---\ntags: []\ntags: []\n---\n", b"no header",
                                b"---\nx: &a foo\ny: *a\n---\n"])
def test_bad_markdown(raw):
    with pytest.raises(DataError):
        validate_markdown("产品列表/RS100.md", raw)


def test_secret_scanner_does_not_report_secret():
    raw = b"# secret\n" + b"ghp_" + b"x" * 36
    with pytest.raises(DataError, match="sensitive content") as error:
        validate_markdown("产品TDS/RS1.TDS.md", raw)
    assert "xxxx" not in str(error.value)


def test_reference_warning_keeps_application(sources):
    rstone, tds = sources
    (rstone / "产品应用/方案.md").write_bytes('---\ntags: []\n底漆: "[[RS999]]"\n---\n'.encode())
    commit_all(rstone)
    snapshot = collect(rstone, tds)
    assert "产品应用/方案.md" in snapshot.files
    assert any(w["code"] == "unresolved_product_reference" for w in snapshot.warnings)
