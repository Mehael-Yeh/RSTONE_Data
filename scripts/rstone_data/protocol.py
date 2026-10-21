"""Byte-level wire contract. Clients verify received bytes, never reserialize to verify."""

import hashlib
import json
import re
import unicodedata
from datetime import datetime
from pathlib import PurePosixPath

PROTOCOL = 1
SCHEMA = 1
MAX_INT = 2**53 - 1
ROOTS = ("产品列表", "产品应用", "产品配方", "产品TDS")
REPOSITORIES = ("Mehael-Yeh/Rstone", "Mehael-Yeh/RstoneTDS")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


class DataError(Exception):
    """Expected validation or release failure; safe message contains no file contents."""


def require(condition, message):
    if not condition:
        raise DataError(message)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def canonical_json(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode("utf-8")


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def decode_json(data):
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(DataError("nonfinite JSON")))
        stack = [(value, 0)]
        while stack:
            item, depth = stack.pop()
            require(depth <= 64, "JSON nesting limit exceeded")
            if isinstance(item, dict):
                stack.extend((child, depth + 1) for pair in item.items() for child in pair)
            elif isinstance(item, list):
                stack.extend((child, depth + 1) for child in item)
            elif isinstance(item, str):
                require(not any(0xD800 <= ord(c) <= 0xDFFF for c in item), "unpaired JSON surrogate")
        return value
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise DataError("invalid UTF-8 JSON") from exc


def integer(value, minimum=0):
    return type(value) is int and minimum <= value <= MAX_INT


def logical_path(path):
    require(isinstance(path, str), "invalid path")
    try:
        require(0 < len(path.encode("utf-8")) <= 1024, "invalid path")
    except UnicodeError as exc:
        raise DataError("invalid Unicode path") from exc
    require(path == unicodedata.normalize("NFC", path), "path must already be NFC")
    require(not path.startswith("/") and "\\" not in path, "absolute or non-POSIX path")
    parts = path.split("/")
    require(len(parts) >= 2 and parts[0] in ROOTS, "path outside public namespace")
    for part in parts:
        require(part not in ("", ".", "..") and not part.startswith("."), "unsafe path component")
        require(not any(ord(c) < 32 or ord(c) == 127 or c in '<>:"|?*' for c in part),
                "nonportable path character")
        require(not part.endswith((".", " ")) and len(part.encode("utf-8")) <= 240,
                "nonportable path component")
        require(part.split(".")[0].upper() not in
                {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                 *(f"LPT{i}" for i in range(1, 10))}, "reserved path component")
    require(PurePosixPath(path).as_posix() == path and path.endswith(".md"), "invalid Markdown path")
    if parts[0] == "产品TDS":
        require(parts[-1].endswith(".TDS.md") and len(parts[-1]) > 7, "invalid TDS name")
    return path


def manifest_name(sequence):
    require(integer(sequence, 1), "invalid sequence")
    return f"manifests/{sequence:06d}.json"


def validate_manifest(manifest):
    require(isinstance(manifest, dict), "manifest must be an object")
    require(type(manifest.get("protocolVersion")) is int and manifest["protocolVersion"] == PROTOCOL,
            "unsupported protocol")
    require(integer(manifest.get("sequence"), 1), "invalid sequence")
    require(type(manifest.get("schemaVersion")) is int and manifest["schemaVersion"] == SCHEMA,
            "unsupported schema")
    require(isinstance(manifest.get("dataVersion"), str) and
            re.fullmatch(r"[0-9]{4}\.[0-9]{2}\.[0-9]{2}\.[0-9]{3,}", manifest["dataVersion"]),
            "invalid dataVersion")
    require(isinstance(manifest.get("minAppVersion"), str) and
            SEMVER.fullmatch(manifest["minAppVersion"]), "invalid minAppVersion")
    require(isinstance(manifest.get("publishedAt"), str) and
            re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z",
                         manifest["publishedAt"]), "invalid publishedAt")
    try:
        datetime.strptime(manifest["publishedAt"], "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise DataError("invalid publishedAt calendar value") from exc
    require(isinstance(manifest.get("keyId"), str) and
            re.fullmatch(r"[A-Za-z0-9_-]{1,64}", manifest["keyId"]), "invalid keyId")
    commits = manifest.get("sourceCommits")
    require(isinstance(commits, dict) and set(commits) == set(REPOSITORIES), "source commits required")
    require(all(isinstance(v, str) and COMMIT.fullmatch(v) for v in commits.values()),
            "invalid source commit SHA")
    files = manifest.get("files")
    require(isinstance(files, list) and 0 < len(files) <= 100_000, "empty or oversized manifest")
    paths = []
    sizes = {}
    for record in files:
        require(isinstance(record, dict), "invalid file record")
        path = logical_path(record.get("path"))
        digest = record.get("sha256")
        require(isinstance(digest, str) and SHA256.fullmatch(digest), "invalid object hash")
        require(integer(record.get("size"), 1) and record["size"] <= 16 * 1024 * 1024,
                "invalid object size")
        require(digest not in sizes or sizes[digest] == record["size"], "conflicting object size")
        sizes[digest] = record["size"]
        require(record.get("contentType", "text/markdown; charset=utf-8") ==
                "text/markdown; charset=utf-8", "unsupported contentType")
        paths.append(path)
    require(paths == sorted(paths, key=lambda p: p.encode("utf-8")), "manifest files not sorted")
    require(len({p.casefold() for p in paths}) == len(paths), "duplicate or case-colliding paths")
    require(sum(r["size"] for r in files) <= 512 * 1024 * 1024, "dataset size limit exceeded")
    return manifest


def validate_latest(latest):
    require(isinstance(latest, dict), "latest must be an object")
    require(type(latest.get("protocolVersion")) is int and latest["protocolVersion"] == PROTOCOL,
            "unsupported protocol")
    name = manifest_name(latest.get("sequence"))
    require(latest.get("manifestPath") == name, "unexpected manifest path")
    require(latest.get("signaturePath") == name.removesuffix(".json") + ".sig",
            "unexpected signature path")
    require(isinstance(latest.get("manifestSha256"), str) and
            SHA256.fullmatch(latest["manifestSha256"]), "invalid manifest hash")
    require(isinstance(latest.get("keyId"), str), "missing keyId")
    require(isinstance(latest.get("dataVersion"), str), "missing dataVersion")
    return latest


def bind_latest(latest, manifest_bytes):
    validate_latest(latest)
    require(sha256(manifest_bytes) == latest["manifestSha256"], "manifest digest mismatch")
    manifest = validate_manifest(decode_json(manifest_bytes))
    for field in ("protocolVersion", "sequence", "dataVersion", "keyId"):
        require(latest[field] == manifest[field], f"latest/manifest {field} mismatch")
    return manifest
