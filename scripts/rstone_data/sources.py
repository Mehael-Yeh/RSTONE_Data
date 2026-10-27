"""Allowlisted, commit-pinned ingestion using Git blobs, not mutable working-tree bytes."""

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .protocol import COMMIT, ROOTS, DataError, logical_path, require
from .price_gate import check_price, check_metadata

MAX_FILE_SIZE = 16 * 1024 * 1024
SOURCE_SPECS = (("Mehael-Yeh/Rstone", "master"), ("Mehael-Yeh/RstoneTDS", "main"))
SENSITIVE_NAME = re.compile(r"(^|[._-])(credentials?|secrets?|passwords?|id_rsa|token|private[._-]?key)([._-]|$)", re.I)
SENSITIVE_CONTENT = re.compile(
    r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----|"
    r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[A-Z0-9]{16})\b|"
    r"(?im:^\s*(?:api[_-]?key|access[_-]?token|password|secret[_-]?key)\s*[:=]\s*\S+)"
)


@dataclass
class Snapshot:
    files: dict[str, bytes]
    commits: dict[str, str]
    warnings: list[dict] = field(default_factory=list)


class UniqueLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        require(not self.check_event(yaml.AliasEvent), "YAML aliases are not allowed")
        return super().compose_node(parent, index)


def _mapping(loader, node):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        require(isinstance(key, str) and key not in mapping, "invalid or duplicate YAML key")
        mapping[key] = loader.construct_object(value_node)
    return mapping


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def validate_markdown(path, raw):
    check_price(path, path)
    logical_path(path)
    require(0 < len(raw) <= MAX_FILE_SIZE, f"empty or oversized Markdown: {path}")
    require(not SENSITIVE_NAME.search(Path(path).name), f"sensitive file name: {path}")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeError as exc:
        raise DataError(f"invalid UTF-8: {path}") from exc
    check_price(path, text)
    require("\x00" not in text, f"NUL in Markdown: {path}")
    require(not text.startswith("version https://git-lfs.github.com/spec/v1"), f"unresolved Git LFS pointer: {path}")
    require(not SENSITIVE_CONTENT.search(text), f"sensitive content detected: {path}")
    lines = text.splitlines()
    required = path.split("/")[0] in ("产品列表", "产品应用")
    has_frontmatter = bool(lines) and lines[0].strip() == "---"
    require(not required or has_frontmatter, f"missing YAML frontmatter: {path}")
    if path.startswith("产品列表/"):
        name = Path(path).stem
        # Keep legacy RD/RS names verbatim. External identifiers must look like
        # numbered grades, not arbitrary Markdown/control document titles.
        control_name = re.match(
            r"(?:readme|tags?|synonyms?|rules?|index|changelog)(?:[0-9 ._()-]|$)", name, re.I)
        external_grade = (not control_name
                          and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._()+-]*", name)
                          and re.search(r"[0-9]", name))
        pigment_grade = re.fullmatch(r"色浆-[\u4e00-\u9fff]+[0-9][A-Za-z0-9._()+-]*", name)
        require(name.upper().startswith(("RD", "RS")) or external_grade or pigment_grade,
                f"invalid product name: {path}")
    if not has_frontmatter:
        return text
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
        header = "\n".join(lines[1:end])
        require(len(header.encode()) <= 64 * 1024, f"oversized frontmatter: {path}")
        metadata = yaml.load(header, Loader=UniqueLoader)
        check_metadata(path, metadata)
        require(isinstance(metadata, dict), f"frontmatter must be mapping: {path}")
        tags = metadata.get("tags")
        require(tags is None or isinstance(tags, list) and all(isinstance(v, str) for v in tags),
                f"tags must be string array: {path}")
        for key, value in metadata.items():
            if key != "tags":
                require(value is None or isinstance(value, (str, int, float, bool)),
                        f"non-scalar business field: {path}")
    except (StopIteration, yaml.YAMLError, RecursionError) as exc:
        raise DataError(f"invalid YAML frontmatter: {path}") from exc
    return text


def git(repo, *arguments, input_bytes=None):
    try:
        result = subprocess.run(["git", "-C", str(repo), *arguments], input=input_bytes,
                                capture_output=True, check=False, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DataError("source repository acquisition failed") from exc
    require(result.returncode == 0, "source repository command failed (no partial-data fallback)")
    return result.stdout


def fetch_sources(directory):
    """Uses existing local Git credential helper; Actions uses explicit-ref checkout steps."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for repository, branch in SOURCE_SPECS:
        target = directory / repository.split("/")[1]
        require(not target.exists(), "fetch destination already exists; use a new snapshot directory")
        try:
            subprocess.run(["git", "-c", "core.autocrlf=false", "clone", "--depth=1", "--branch",
                            branch, "--single-branch", f"https://github.com/{repository}.git", str(target)],
                           capture_output=True, check=True, timeout=180)
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise DataError(f"source fetch failed: {repository}@{branch}") from exc
    return collect(directory / "Rstone", directory / "RstoneTDS")


def _blobs(repo, tds):
    require(Path(repo).is_dir(), "missing source repository")
    expected = SOURCE_SPECS[1 if tds else 0][0].lower()
    origin = git(repo, "remote", "get-url", "origin").decode("utf-8").strip().lower().removesuffix(".git")
    require(origin in (f"https://github.com/{expected}", f"git@github.com:{expected}"),
            "source origin does not match the expected private repository")
    commit = git(repo, "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
    require(COMMIT.fullmatch(commit), "invalid source HEAD")
    tree = git(repo, "ls-tree", "-rz", "--full-tree", commit)
    selected = []
    directories = set()
    for item in tree.split(b"\0"):
        if not item:
            continue
        metadata, name = item.split(b"\t", 1)
        try:
            name = name.decode("utf-8")
        except UnicodeError as exc:
            raise DataError("invalid UTF-8 source path") from exc
        mode, kind, oid = metadata.decode("ascii").split()
        if tds:
            include = "/" not in name and name.endswith(".TDS.md")
            if "/" not in name and name.lower().endswith(".tds.md"):
                require(include, "TDS suffix must be exactly .TDS.md")
            logical = f"产品TDS/{name}"
        else:
            directory = name.split("/", 1)[0]
            directories.add(directory)
            include = directory in ROOTS[:3] and name.endswith(".md")
            if directory in ROOTS[:3] and name.lower().endswith(".md"):
                require(include, "Markdown suffix must be exactly .md")
            logical = name
        if include:
            require(mode in ("100644", "100755") and kind == "blob", "symlink/submodule in allowlist")
            logical_path(logical)
            selected.append((logical, oid))
    if not tds:
        require(set(ROOTS[:3]) <= directories, "expected source data directory missing")
    if not selected:
        return commit, {}
    request = "".join(f"{oid}\n" for _, oid in selected).encode("ascii")
    sizes = git(repo, "cat-file", "--batch-check", input_bytes=request).splitlines()
    require(len(sizes) == len(selected), "incomplete source blob metadata")
    for line in sizes:
        fields = line.split()
        require(len(fields) == 3 and fields[1] == b"blob", "source object missing")
        require(0 < int(fields[2]) <= MAX_FILE_SIZE, "source blob empty or oversized")
    output = git(repo, "cat-file", "--batch", input_bytes=request)
    offset = 0
    files = {}
    for logical, oid in selected:
        end = output.index(b"\n", offset)
        header_oid, kind, size = output[offset:end].split()
        require(header_oid.decode() == oid and kind == b"blob", "unexpected source blob")
        length = int(size)
        raw = output[end + 1:end + 1 + length]
        require(len(raw) == length, "incomplete source blob")
        files[logical] = raw
        offset = end + length + 2
    return commit, files


def collect(rstone, tds):
    commit1, files = _blobs(rstone, False)
    commit2, tds_files = _blobs(tds, True)
    files.update(tds_files)
    texts = {}
    warnings = []
    require(len({p.casefold() for p in files}) == len(files), "conflicting paths")
    for path, raw in files.items():
        texts[path] = validate_markdown(path, raw)
    require(files, "empty database")
    for root in ROOTS:
        if not any(p.startswith(root + "/") for p in files):
            warnings.append({"path": root, "code": "empty_namespace_requires_deletion_review"})
    known = {Path(p).stem.casefold() for p in files if not p.startswith("产品应用/")}
    for path, text in texts.items():
        if "工程师:" in text or re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text):
            warnings.append({"path": path, "code": "public_personal_field_review"})
        if path.startswith(("产品应用/", "产品配方/")):
            for reference in re.findall(r"\[\[([^\]]+)\]\]", text):
                target = reference.split("|", 1)[0].split("#", 1)[0].split("/")[-1]
                target = target.removesuffix(".md")
                if target.upper().startswith(("RD", "RS")) and target.casefold() not in known:
                    warnings.append({"path": path, "code": "unresolved_product_reference", "target": target})
    return Snapshot(files, {SOURCE_SPECS[0][0]: commit1, SOURCE_SPECS[1][0]: commit2}, warnings)
