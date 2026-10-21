import subprocess

import pytest


def run_git(path, *arguments):
    return subprocess.run(["git", "-C", str(path), *arguments], check=True, capture_output=True).stdout


def commit_all(path):
    run_git(path, "add", ".")
    run_git(path, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture")


@pytest.fixture
def sources(tmp_path):
    rstone, tds = tmp_path / "Rstone", tmp_path / "RstoneTDS"
    rstone.mkdir()
    tds.mkdir()
    for path, branch in ((rstone, "master"), (tds, "main")):
        run_git(path, "init", "--initial-branch=" + branch)
        run_git(path, "config", "core.autocrlf", "false")
    data = {
        "产品列表/RS100.md": b"---\ntags: [UV]\n---\n# RS100\r\n",
        "产品列表/RD200.md": b"---\ntags: [PU]\n---\n# RD200\n",
        "产品应用/方案.md": '---\ntags: [PC]\n底漆: "[[RS100]]"\n---\n'.encode(),
        "产品配方/RS100-配方.md": b"| A | B |\n| - | - |\n| RS100 | 100 |\n",
    }
    for name, raw in data.items():
        target = rstone / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    (tds / "RS100.TDS.md").write_bytes(b"# RS100\nTDS fixture only\n")
    (rstone / ".env").write_text("not public")
    commit_all(rstone)
    commit_all(tds)
    return rstone, tds
