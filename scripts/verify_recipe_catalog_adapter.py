"""Local-only parent replay: no network, policy edits, approvals or signing.

Run with the selected interpreter. Supply --old-code and --baseline explicitly;
private data stays external and the receipt records only hashes and counts.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

WORKER = r"""
import hashlib, json, sys
from pathlib import Path
from rstone_data import quality_gate as q
from rstone_data.protocol import canonical_json
from rstone_data.sources import Snapshot
root = Path(sys.argv[1])
corpus = q.load_corpus(root)
assert not corpus.unreadable and not corpus.stray
files = {doc.path: doc.raw for doc in corpus.documents}
snapshot = Snapshot(files, {"Mehael-Yeh/Rstone": "a" * 40,
                            "Mehael-Yeh/RstoneTDS": "b" * 40})
report = q.validate_snapshot(snapshot, snapshot)
result = {
    "module_sha256": hashlib.sha256(Path(q.__file__).read_bytes()).hexdigest(),
    "inventory_sha256": hashlib.sha256(canonical_json(q.input_digests(root))).hexdigest(),
    "counts": report["counts"],
    "ok": report["summary"]["ok"],
    "warning_count": len(report["warnings"]),
    "warnings_sha256": hashlib.sha256(canonical_json(report["warnings"])).hexdigest(),
}
assert files == {doc.path: doc.raw for doc in q.load_corpus(root).documents}
print(json.dumps(result, sort_keys=True))
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-code", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--suite", action="store_true")
    args = parser.parse_args()
    candidate = Path(__file__).resolve().parents[1]
    assert args.old_code.resolve() != candidate
    assert args.output.resolve().is_relative_to(candidate)
    args.output.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    results = {}
    for label, code in (("old", args.old_code), ("candidate", candidate)):
        env["PYTHONPATH"] = str(code / "scripts")
        run = subprocess.run([sys.executable, "-c", WORKER, str(args.baseline)],
                             env=env, text=True, capture_output=True, check=False)
        (args.output / (label + "-replay.stdout")).write_text(run.stdout)
        (args.output / (label + "-replay.stderr")).write_text(run.stderr)
        assert run.returncode == 0, (label, run.returncode)
        results[label] = json.loads(run.stdout)
    for key in ("inventory_sha256", "counts", "ok", "warning_count", "warnings_sha256"):
        assert results["old"][key] == results["candidate"][key], key
    # Reconstruct RED on unchanged old code using the exact delivered tests.
    old_red = subprocess.run([
        sys.executable, "-m", "pytest", "tests/test_catalog_recipe_names.py", "-q",
        "-k", "same_name_real_recipe or editorial_suffix_removal",
        "-o", "pythonpath=" + str(args.old_code / "scripts"),
        "-o", "cache_dir=" + str(args.output / "cache"),
        "--basetemp=" + str(args.output / "red-tmp"),
    ], cwd=candidate, env=env, text=True, capture_output=True, check=False)
    (args.output / "old-RED.log").write_text(old_red.stdout + old_red.stderr)
    assert old_red.returncode == 1 and "2 failed" in old_red.stdout
    results["old_red_exit"] = old_red.returncode
    env["PYTHONPATH"] = str(candidate / "scripts")
    command = [sys.executable, "-B", "-m", "pytest", "-q",
               "-o", "cache_dir=" + str(args.output / "cache"),
               "--basetemp=" + str(args.output / "green-tmp")]
    if not args.suite:
        command.insert(4, "tests/test_catalog_recipe_names.py")
    green = subprocess.run(command, cwd=candidate, env=env,
                           text=True, capture_output=True, check=False)
    (args.output / "candidate-GREEN.log").write_text(green.stdout + green.stderr)
    assert green.returncode == 0, green.returncode
    results["green_exit"] = green.returncode
    results["suite"] = args.suite
    (args.output / "receipt.json").write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
