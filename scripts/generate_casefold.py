"""Generate Dart's full default case-fold mapping from the publisher's Unicode tables."""

import argparse
import json
import unicodedata
from pathlib import Path


def source():
    lines = [f"// Generated from Python unicodedata {unicodedata.unidata_version}; full default case folding.",
             "// Regenerate with scripts/generate_casefold.py; review Unicode-version changes.",
             "String protocolCaseFold(String value) => value.runes",
             "    .map((rune) => _mapping[rune] ?? String.fromCharCode(rune)).join();", "",
             "const _mapping = <int, String>{"]
    for codepoint in range(0x110000):
        text = chr(codepoint)
        if text.casefold() != text:
            lines.append(f"  {codepoint}: {json.dumps(text.casefold(), ensure_ascii=True)},")
    return "\n".join(lines + ["};", ""])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    path = Path(__file__).resolve().parents[1] / "examples/flutter_protocol/lib/src/casefold.dart"
    expected = source()
    if arguments.check:
        if path.read_text(encoding="utf-8") != expected:
            raise SystemExit("Dart/Python case-fold table drift")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(expected, encoding="utf-8", newline="\n")
