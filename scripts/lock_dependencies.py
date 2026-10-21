"""Refresh exact-version hashes from official PyPI metadata; review lock changes before commit."""

import json
import urllib.request
from pathlib import Path

RUNTIME = {"cryptography": "50.0.2", "PyYAML": "6.0.3", "cffi": "2.1.1", "pycparser": "3.11"}
TEST = {"pytest": "9.1.1", "iniconfig": "2.3.1", "packaging": "26.3", "pluggy": "1.7.0",
        "pygments": "2.21.0", "colorama": "0.4.6"}


def lock(packages):
    lines = ["# Generated from official PyPI JSON; exact pins plus SHA-256; do not remove hashes."]
    for package, version in packages.items():
        with urllib.request.urlopen(f"https://pypi.org/pypi/{package}/{version}/json", timeout=30) as response:
            metadata = json.load(response)
        hashes = sorted({r["digests"]["sha256"] for r in metadata["urls"] if not r["yanked"]})
        if not hashes:
            raise RuntimeError("no supported release artifacts")
        lines.append(f"{package}=={version} \\")
        lines.extend("    --hash=sha256:" + digest + (" \\" if i < len(hashes) - 1 else "")
                     for i, digest in enumerate(hashes))
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    (root / "requirements.txt").write_text(lock(RUNTIME), encoding="utf-8", newline="\n")
    (root / "requirements-ci.txt").write_text("-r requirements.txt\n" + lock(TEST), encoding="utf-8", newline="\n")
    print("Updated runtime and CI dependency hashes from PyPI")
