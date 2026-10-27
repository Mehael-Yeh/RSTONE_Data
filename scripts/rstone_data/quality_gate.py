"""Deterministic pre-publication content gate for the RSTONE public dataset.

Design rules:

* Read-only. Documents are opened once, hashed, parsed; nothing is written back.
  ``scan`` re-hashes every input at the end and fails if any byte changed.
* Fail-closed. An unreadable payload, an unknown tag word, a missing label
  vocabulary or a broken reference is a violation, never a silent skip.
* No value echo. Diagnostics carry a category, a logical path and a field name.
  Tag text is included only when it cannot be a price; price-gate diagnostics
  keep the path hashed when the path itself carries a price.
* Deterministic. Sorted file order, sorted violations, no clock-dependent
  decisions beyond the report timestamp.

The nine checks mirror ``audit/quality_gate.md``; each one is exercised by a
red/green test pair in ``tests/test_quality_gate.py``.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .price_gate import LABEL as PRICE_LABEL
from .price_gate import MONEY as PRICE_MONEY
from .price_gate import check_payload, check_price
from .protocol import DataError

SCHEMA = "rstone-quality-gate/1"
NAMESPACES = ("产品列表", "产品应用", "产品配方", "产品TDS")
SOURCE_NAMESPACES = ("产品列表", "产品应用", "产品配方")
MAX_ITEMS = 400

FIRST_TAGS = ("水性", "油性", "粉末")
SECOND_TAGS = ("UV", "PU", "PUD", "双固化", "聚烯烃")
FUNCTIONALITY_TAG = re.compile(r"^(\d+(?:\.\d+)?)官$")
FUNCTIONALITY_FIELDS = ("官能度",)
NUMERIC_FIELDS = ("固含", "羟值", "官能度", "硬度", "光泽", "水接触角")
VISCOSITY_FIELD = "粘度"
TECHNICAL_FIELDS = NUMERIC_FIELDS + (VISCOSITY_FIELD,)

PLACEHOLDERS = frozenset({"-", "—", "–", "/", "N/A", "NA", "n/a", "无", "暂无", "待定",
                          "待补充", "TBD", "?", "？", "null", "None", "NONE"})
RESIDUE_LITERALS = re.compile(
    r"^\s*(?:None|null|NaN|True|False|undefined|<class\b.*|.*\bobject at 0x[0-9a-f]+)\s*$")
RESIDUE_CHARS = re.compile(r"[\[\]{}]")
# Legitimate technical spellings that are not plain numbers: pencil hardness
# (H, HB, 2H, H(300g)) and tolerance / inequality values (77±2, 100–120, ≤2, >6).
PENCIL_HARDNESS = re.compile(r"^(?:\d{1,2}\s*[BH]|[BH]|F|HB)(?:\s*[（(]\s*\d+\s*g\s*[)）])?$", re.I)
TECHNICAL_NUMBER = re.compile(
    r"^[<>≤≥＜＞]?\s*\d+(?:\.\d+)?(?:\s*[–~—]\s*\d+(?:\.\d+)?)?"
    r"\s*(?:±\s*\d+(?:\.\d+)?)?\s*%?$")
EXTERNAL_LINK = re.compile(r"^(?:https?://|mailto:|tel:|ftp://|//|www\.)", re.I)
WIKI_LINK = re.compile(r"\[\[([^\]]+)\]\]")

# Supplier / competitor brand words. A brand must never be used as a product tag.
BRAND_TAGS = frozenset({
    "德固赛", "毕克", "海名斯", "陶氏", "罗门哈斯", "东曹", "湛新", "长兴", "三木",
    "万华", "拜耳", "巴斯夫", "科思创", "赢创", "迪高", "荒川", "共荣社", "三菱",
    "信越", "卡博特", "伊士曼", "埃克森", "旭化成", "大赛璐", "昭和", "TEGO", "BYK",
    "BASF", "Covestro", "Evonik", "Allnex", "Dow", "DIC",
})
INTERNAL_TAG_MARKERS = re.compile(r"客户|竞品|对手|友商|供应商|报价|成交|返利")

NOTES_HEADING = re.compile(r"^(#{1,6})\s*特别说明\s*$")
HEADING = re.compile(r"^(#{1,6})\s+")
BROCHURE_PHRASES = (
    re.compile(r"画册"),
    re.compile(r"页码|第\s*\d+\s*页|见\s*P\s*\d+"),
    re.compile(r"\.(?:pdf|indd|ai|psd|cdr)\b", re.I),
)
INTERNAL_PHRASES = (
    re.compile(r"不采用该册物理参数"),
    re.compile(r"不继承至"),
    re.compile(r"研发阶段"),
    re.compile(r"内部资料|仅内部参考|采编|初稿|待核实"),
)
SENTENCE = re.compile(r"[^。；;!?！？\n]+[。；;!?！？]?")

FACT_UNIT = re.compile(
    r"\d\s*(?:℃|°C|%|％|h|min|s|秒|分钟|小时|μm|um|mm|mJ|mPa·s|wt%|次|遍|kg|g/L|°|度|B|H)"
    r"|\d+\s*[–~-]\s*\d+")
TECHNICAL_WORDS = re.compile(
    r"体系|固化|耐[水热酸碱黄变污湿]|附着|硬度|光泽|施工|喷涂|涂布|辊涂|淋涂|基材|底材|"
    r"官能|粘度|固含|水煮|上镀|流平|丰满|柔韧|耐磨|耐钢丝|消光|哑光|银粉|色浆|固化剂")
USAGE_HEADINGS = re.compile(r"用途|特性|应用|概述|物理性能|物性|使用|施工|说明")

PRODUCT_PREFIX = re.compile(r"^(?:RS|RD)(?=\d)")
PREFIX_ANY = re.compile(r"^(?:RS|RD)", re.I)


class InputError(Exception):
    """The candidate tree or the label vocabulary cannot be read."""


@dataclass
class Document:
    path: str
    file: Path
    raw: bytes
    text: str
    frontmatter: dict | None
    body: str
    namespace: str

    @property
    def stem(self) -> str:
        name = Path(self.path).name
        return name[:-3] if name.endswith(".md") else name

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.raw).hexdigest()


@dataclass
class Corpus:
    root: Path
    base: Path
    tds: Path | None
    documents: list[Document] = field(default_factory=list)
    unreadable: list[dict] = field(default_factory=list)
    stray: list[dict] = field(default_factory=list)

    def by_namespace(self, namespace: str) -> list[Document]:
        return [doc for doc in self.documents if doc.namespace == namespace]

    def stems(self, namespace: str) -> list[str]:
        return [doc.stem for doc in self.by_namespace(namespace)]


def _normalise_field(key: str) -> str:
    return re.sub(r"[（(].*?[)）]", "", key).strip()


def _parse(raw: bytes, path: str) -> tuple[dict | None, str, str]:
    text = raw.decode("utf-8-sig")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, text, text
    end = next((index for index in range(1, len(lines)) if lines[index].strip() == "---"), None)
    if end is None:
        raise ValueError("unterminated frontmatter")
    from .sources import UniqueLoader  # duplicate-key rejection shared with ingestion
    metadata = yaml.load("\n".join(lines[1:end]), Loader=UniqueLoader) or {}
    if not isinstance(metadata, dict):
        raise ValueError("frontmatter is not a mapping")
    return metadata, "\n".join(lines[end + 1:]), text


def resolve_layout(root: Path) -> tuple[Path, Path | None]:
    root = Path(root)
    for base in (root, root / "files"):
        if (base / "产品列表").is_dir():
            tds = base / "产品TDS"
            return base, tds if tds.is_dir() else None
    if (root / "Rstone" / "产品列表").is_dir():
        tds = root / "RstoneTDS"
        return root / "Rstone", tds if tds.is_dir() else None
    raise InputError(f"no 产品列表 directory under {root}")


def load_corpus(root: Path) -> Corpus:
    base, tds = resolve_layout(root)
    corpus = Corpus(Path(root), base, tds)
    for namespace in SOURCE_NAMESPACES:
        directory = base / namespace
        if not directory.is_dir():
            continue
        for entry in sorted(directory.iterdir()):
            if entry.is_dir() or entry.name.startswith("."):
                continue
            relative = f"{namespace}/{entry.name}"
            if not entry.name.endswith(".md"):
                corpus.stray.append({"path": relative, "category": "identity-suffix"})
                continue
            _load_document(corpus, entry, relative, namespace)
    if tds is not None:
        for entry in sorted(tds.iterdir()):
            if entry.is_dir() or entry.name.startswith("."):
                continue
            relative = f"产品TDS/{entry.name}"
            if not entry.name.endswith(".TDS.md"):
                corpus.stray.append({"path": relative, "category": "identity-tds-suffix"})
                continue
            _load_document(corpus, entry, relative, "产品TDS")
    return corpus


def _load_document(corpus: Corpus, entry: Path, relative: str, namespace: str) -> None:
    if entry.is_symlink():
        corpus.stray.append({"path": relative, "category": "input-symlink"})
        return
    try:
        raw = entry.read_bytes()
    except OSError:
        corpus.unreadable.append({"path": relative, "category": "input-unreadable"})
        return
    try:
        frontmatter, body, text = _parse(raw, relative)
    except (UnicodeError, ValueError, yaml.YAMLError, RecursionError):
        corpus.documents.append(Document(relative, entry, raw, "", None, "", namespace))
        corpus.unreadable.append({"path": relative, "category": "frontmatter-invalid"})
        return
    corpus.documents.append(Document(relative, entry, raw, text, frontmatter, body, namespace))


def default_vocabulary() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "quality_vocabulary.json"


def load_vocabulary(source) -> frozenset[str]:
    if isinstance(source, (set, frozenset)):
        return frozenset(source)
    if isinstance(source, dict):
        return frozenset(source)
    path = Path(source) if source is not None else default_vocabulary()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InputError(f"tag vocabulary unreadable: {path}") from exc
    tags = payload.get("tags") if isinstance(payload, dict) else None
    if not isinstance(tags, dict) or not tags:
        raise InputError(f"tag vocabulary malformed: {path}")
    return frozenset(tags)


def _verified(value: str) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    return True


class Report:
    def __init__(self, label: str | None, root: Path):
        self.label = label
        self.root = Path(root)
        self.checks: list[dict] = []
        self.counts: dict[str, int] = {}
        self.baseline: dict | None = None
        self.read_only = True
        self.warnings: list[dict] = []
        self.generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def add(self, ident: int, name: str, checked: int, findings: list[dict]) -> None:
        def ordered(items):
            return sorted(items, key=lambda item: (item.get("path", ""),
                                                  item.get("category", ""),
                                                  item.get("field", "")))
        failures = ordered([item for item in findings if item.get("severity") != "review"])
        reviews = ordered([item for item in findings if item.get("severity") == "review"])
        self.checks.append({"id": ident, "name": name, "checked": checked,
                            "ok": not failures, "violation_count": len(failures),
                            "violations": failures[:MAX_ITEMS],
                            "truncated": max(0, len(failures) - MAX_ITEMS),
                            "warning_count": len(reviews),
                            "warnings": reviews[:MAX_ITEMS]})
        for review in reviews:
            self.warnings.append({**review, "check": name})

    def check(self, name: str) -> dict:
        for check in self.checks:
            if check["name"] == name:
                return check
        raise KeyError(name)

    @property
    def ok(self) -> bool:
        return all(check["ok"] for check in self.checks) and self.read_only

    def json(self) -> dict:
        failed = [check["name"] for check in self.checks if not check["ok"]]
        if not self.read_only:
            failed.append("read_only")
        return {
            "schema": SCHEMA,
            "label": self.label,
            "root": str(self.root),
            "generated_at": self.generated_at,
            "read_only": self.read_only,
            "counts": self.counts,
            "baseline": self.baseline,
            "checks": self.checks,
            "warnings": self.warnings,
            "summary": {
                "ok": self.ok,
                "failed_checks": failed,
                "violations": sum(check["violation_count"] for check in self.checks),
                "warnings": len(self.warnings),
            },
        }

    def write(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.json(), ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")


def _safe_tag(tag: str) -> str:
    """Return the tag word unless it could be a price, in which case hash it."""
    if PRICE_LABEL.search(tag) or PRICE_MONEY.search(tag):
        return "tag-sha256:" + hashlib.sha256(tag.encode("utf-8")).hexdigest()[:8]
    return tag


def _functionality(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _numeric(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _finding(path: str, category: str, field: str | None = None,
             severity: str = "failure") -> dict:
    item = {"path": path, "category": category, "severity": severity}
    if field is not None:
        item["field"] = field
    return item


def _failure(path: str, category: str, field: str | None = None) -> dict:
    return _finding(path, category, field)


def _review(path: str, category: str, field: str | None = None) -> dict:
    """A schema question, not a business defect: reported, but never blocking."""
    return _finding(path, category, field, "review")


# --------------------------------------------------------------------- checks

def check_identity(corpus: Corpus, baseline: Corpus | None) -> list[dict]:
    violations: list[dict] = list(corpus.stray)
    for namespace in NAMESPACES:
        seen: dict[str, str] = {}
        for doc in corpus.by_namespace(namespace):
            key = doc.stem.casefold()
            if key in seen:
                violations.append({"path": doc.path, "category": "stem-duplicate"})
            seen.setdefault(key, doc.path)
    product_stems = {doc.stem.casefold() for doc in corpus.by_namespace("产品列表")}
    for namespace in ("产品应用", "产品配方"):
        for doc in corpus.by_namespace(namespace):
            if doc.stem.casefold() in product_stems:
                violations.append({"path": doc.path,
                                   "category": "stem-collision-cross-namespace"})
    for doc in corpus.by_namespace("产品列表"):
        stem = doc.stem
        if stem != stem.strip() or stem.endswith("-"):
            violations.append({"path": doc.path, "category": "identity-malformed"})
    if baseline is not None:
        for namespace in NAMESPACES:
            before = baseline.stems(namespace)
            after = {doc.stem for doc in corpus.by_namespace(namespace)}
            folded = {stem.casefold(): stem for stem in before}
            for doc in corpus.by_namespace(namespace):
                stem = doc.stem
                if stem in before:
                    continue
                original = folded.get(stem.casefold())
                if original is not None:
                    violations.append({"path": doc.path,
                                       "category": "identity-case-rewritten"})
                    continue
                if any(other.casefold().startswith(stem.casefold() + "-") for other in before):
                    violations.append({"path": doc.path,
                                       "category": "identity-suffix-dropped"})
            if namespace == "产品列表":
                base_before: dict[str, int] = {}
                base_after: dict[str, int] = {}
                for stem in before:
                    base_before[stem.split("-")[0].casefold()] = \
                        base_before.get(stem.split("-")[0].casefold(), 0) + 1
                for stem in after:
                    base_after[stem.split("-")[0].casefold()] = \
                        base_after.get(stem.split("-")[0].casefold(), 0) + 1
                for base, count in base_before.items():
                    if count > 1 and base_after.get(base, 0) < count:
                        violations.append({"path": f"产品列表/{base}",
                                           "category": "identity-suffix-merged"})
    return violations


def check_tags(corpus: Corpus, vocabulary: frozenset[str]) -> list[dict]:
    violations: list[dict] = []
    for doc in corpus.documents:
        if doc.namespace not in ("产品列表", "产品应用"):
            continue
        tags = (doc.frontmatter or {}).get("tags")
        if tags is None:
            violations.append({"path": doc.path, "category": "tag-missing"})
            continue
        if not isinstance(tags, list):
            violations.append({"path": doc.path, "category": "tag-type"})
            continue
        text = [_safe_tag(tag) if isinstance(tag, str) else "" for tag in tags]
        seen: set[str] = set()
        for index, tag in enumerate(tags):
            if tag is None:
                violations.append({"path": doc.path, "category": "tag-empty",
                                   "field": f"tags[{index}]"})
                continue
            if not isinstance(tag, str):
                violations.append({"path": doc.path, "category": "tag-type",
                                   "field": f"tags[{index}]"})
                continue
            if not tag.strip():
                violations.append({"path": doc.path, "category": "tag-empty",
                                   "field": f"tags[{index}]"})
                continue
            if tag != tag.strip():
                violations.append({"path": doc.path, "category": "tag-whitespace",
                                   "field": text[index]})
            folded = tag.casefold()
            if folded in seen:
                violations.append({"path": doc.path, "category": "tag-duplicate",
                                   "field": text[index]})
            seen.add(folded)
            if PRICE_LABEL.search(tag) or PRICE_MONEY.search(tag) or INTERNAL_TAG_MARKERS.search(tag):
                violations.append({"path": doc.path, "category": "tag-forbidden",
                                   "field": text[index]})
                continue
            if tag in BRAND_TAGS:
                violations.append({"path": doc.path, "category": "tag-forbidden",
                                   "field": text[index]})
                continue
            if tag not in vocabulary:
                violations.append({"path": doc.path, "category": "tag-unapproved",
                                   "field": text[index]})
        if doc.namespace != "产品列表" or not tags:
            continue
        first = tags[0] if isinstance(tags[0], str) else ""
        if first not in FIRST_TAGS:
            violations.append({"path": doc.path, "category": "tag-first",
                               "field": _safe_tag(first)})
        if len(tags) < 2:
            violations.append({"path": doc.path, "category": "tag-second"})
        else:
            second = tags[1] if isinstance(tags[1], str) else ""
            if second not in SECOND_TAGS:
                violations.append({"path": doc.path, "category": "tag-second",
                                   "field": _safe_tag(second)})
    return violations


def check_tag_field(corpus: Corpus) -> list[dict]:
    """Only a real disagreement blocks: tag says 4官 while the field says 6."""
    violations: list[dict] = []
    for doc in corpus.by_namespace("产品列表"):
        metadata = doc.frontmatter or {}
        tags = metadata.get("tags")
        if not isinstance(tags, list):
            continue
        functional = [tag for tag in tags
                      if isinstance(tag, str) and FUNCTIONALITY_TAG.match(tag)]
        if not functional:
            continue
        wanted = {float(FUNCTIONALITY_TAG.match(tag).group(1)) for tag in functional}
        if len(wanted) > 1:
            violations.append(_failure(doc.path, "tag-field-conflict", "tags"))
            continue
        value = metadata.get("官能度")
        if value in (None, ""):
            # Tag carries the only value: missing data to backfill, not a conflict.
            violations.append(_review(doc.path, "tag-field-missing", "官能度"))
            continue
        actual = _functionality(value)
        if actual is None:
            violations.append(_review(doc.path, "tag-field-unverifiable", "官能度"))
            continue
        if actual not in wanted:
            violations.append(_failure(doc.path, "tag-field-conflict", "官能度"))
    return violations


def check_field_types(corpus: Corpus) -> list[dict]:
    """Parsing dregs block; legitimate technical spellings only prompt a schema review.

    Hardness is legitimately a pencil grade (2H/HB/H(300g)), and 固含/羟值/官能度 may
    legitimately carry a tolerance or inequality (77±2, ≤2, >6) — the real ingestion
    contract (``sources.validate_markdown``) only requires scalars, so the stricter
    "number only" reading is a schema question, reported as a review item.
    """
    violations: list[dict] = list(corpus.unreadable)
    for doc in corpus.by_namespace("产品列表"):
        metadata = doc.frontmatter or {}
        for key, value in metadata.items():
            if key == "tags":
                continue
            name = _normalise_field(key)
            if value is None or value == "":
                continue
            if name in NUMERIC_FIELDS:
                violations.extend(_typed_violations(doc.path, name, value))
                continue
            if name == VISCOSITY_FIELD:
                if not isinstance(value, str):
                    violations.append(_failure(doc.path, "field-type", name))
                else:
                    violations.extend(_viscosity_violations(doc.path, value))
                continue
            if isinstance(value, str):
                violations.extend(_residue_violations(doc.path, name, value))
    return violations


def _typed_violations(path: str, name: str, value) -> list[dict]:
    if _numeric(value):
        return []
    if not isinstance(value, str):
        return [_failure(path, "field-type", name)]
    text = value.strip()
    # Pencil hardness and tolerance/inequality expressions are documented
    # scalar contract values (see sources.validate_markdown); only text the
    # protocol cannot parse is a defect. Emitting a review for a known-good
    # spelling is noise that hides real backfill work.
    if name == "硬度" and PENCIL_HARDNESS.match(text):
        return []
    if TECHNICAL_NUMBER.match(text):
        return []
    return [_failure(path, "field-type", name)]


def _viscosity_violations(path: str, value: str) -> list[dict]:
    found: list[dict] = []
    if "cP" in value or "cps" in value.lower() or "mPa·s" not in value:
        found.append(_review(path, "schema-viscosity-unit", VISCOSITY_FIELD))
    if "℃" not in value and "°C" not in value:
        found.append(_review(path, "schema-viscosity-temperature", VISCOSITY_FIELD))
    if RESIDUE_CHARS.search(WIKI_LINK.sub("", value)):
        found.append(_failure(path, "field-residue", VISCOSITY_FIELD))
    return found


def _residue_violations(path: str, name: str, value: str) -> list[dict]:
    found: list[dict] = []
    stripped = WIKI_LINK.sub("", value)
    if RESIDUE_CHARS.search(stripped) or RESIDUE_LITERALS.match(value) \
            or "'" in value or '"' in value or "“" in value:
        found.append(_failure(path, "field-residue", name))
    elif value.strip() in PLACEHOLDERS:
        found.append(_failure(path, "field-placeholder", name))
    return found


def _has_fact(text: str) -> bool:
    if not _verified(text):
        return False
    if FACT_UNIT.search(text):
        return True
    return bool(TECHNICAL_WORDS.search(text)) and len(text.strip()) >= 12


def _body_has_substance(body: str) -> bool:
    heading = ""
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = HEADING.match(stripped)
        if match:
            heading = stripped[match.end():].strip()
            continue
        content = stripped.lstrip("-*+0123456789. ").strip()
        if not content:
            continue
        if FACT_UNIT.search(content):
            return True
        if USAGE_HEADINGS.search(heading) and len(content) >= 8:
            return True
    return False


def _notes_blocks(body: str) -> list[list[str]]:
    """Every 特别说明 block, not just the first one (a second block is a defect too)."""
    lines = body.splitlines()
    blocks: list[list[str]] = []
    for index, line in enumerate(lines):
        match = NOTES_HEADING.match(line.strip())
        if not match:
            continue
        level = len(match.group(1))
        block: list[str] = []
        for following in lines[index + 1:]:
            heading = HEADING.match(following.strip())
            if heading and len(heading.group(1)) <= level:
                break
            if heading:
                block.append("")  # nested headings are not notes content
                continue
            block.append(following.strip())
        blocks.append(block)
    return blocks


def check_content(corpus: Corpus) -> list[dict]:
    violations: list[dict] = []
    for doc in corpus.by_namespace("产品列表"):
        metadata = doc.frontmatter or {}
        if metadata is None:
            continue
        substantive = False
        property_value = False
        for name in TECHNICAL_FIELDS:
            value = metadata.get(name)
            if value is None or value == "":
                continue
            if _numeric(value) or (isinstance(value, str) and value.strip()):
                substantive = True
                property_value = True
                break
        if not substantive:
            for block in _notes_blocks(doc.body):
                if any(_has_fact(item) for item in block if item):
                    substantive = True
                    break
        if not substantive and _body_has_substance(doc.body):
            substantive = True
        if not substantive:
            violations.append(_failure(doc.path, "empty_product"))
        elif not property_value:
            # Passes the hard check but carries no physical property at all.
            violations.append(_review(doc.path, "content-without-property"))
    return violations


def _reference_target(reference: str) -> str:
    target = reference.split("|", 1)[0].split("#", 1)[0].strip()
    target = target.split("/")[-1]
    return target[:-3] if target.endswith(".md") else target


def _reference_targets(corpus: Corpus) -> list[tuple[str, str, str]]:
    """(path, category, target) for every resolvable-attempt wiki link."""
    known = {doc.stem.casefold() for doc in corpus.documents
             if doc.namespace in ("产品列表", "产品配方")}
    found: list[tuple[str, str, str]] = []
    for doc in corpus.documents:
        references = [(reference, "reference-broken")
                      for reference in WIKI_LINK.findall(doc.body)]
        if doc.namespace == "产品列表":
            metadata = doc.frontmatter or {}
            for key in ("技术源", "对标"):
                value = metadata.get(key)
                if isinstance(value, str):
                    references += [(reference, "reference-broken-techsource")
                                   for reference in WIKI_LINK.findall(value)]
        if doc.namespace == "产品应用":
            for value in (doc.frontmatter or {}).values():
                if isinstance(value, str):
                    references += [(reference, "reference-broken")
                                   for reference in WIKI_LINK.findall(value)]
        for reference, category in references:
            if EXTERNAL_LINK.match(reference.strip()):
                continue  # explicit external link, not an internal wiki link
            target = _reference_target(reference)
            if target and target.casefold() not in known:
                found.append((doc.path, category, target))
    return found


def check_references(corpus: Corpus, baseline: Corpus | None = None) -> list[dict]:
    """New broken links block; links already broken in the baseline only prompt review."""
    violations: list[dict] = []
    inherited = {(path, target) for path, _, target in _reference_targets(baseline)} \
        if baseline is not None else set()
    for path, category, target in _reference_targets(corpus):
        if (path, target) in inherited:
            violations.append(_review(path, f"{category}-preexisting", target))
        else:
            violations.append(_failure(path, category, target))
    return violations


def check_retention(corpus: Corpus, baseline: Corpus | None, expect_recipes: int,
                    expect_applications: int, allow_deletions: bool,
                    report: Report) -> list[dict]:
    violations: list[dict] = []
    counts = {namespace: len(corpus.by_namespace(namespace)) for namespace in NAMESPACES}
    for namespace, expected in (("产品配方", expect_recipes), ("产品应用", expect_applications)):
        report.counts[f"expect_{namespace}"] = expected
        if counts.get(namespace, 0) < expected:
            violations.append({"path": namespace, "category": "retention-underflow",
                               "field": str(counts.get(namespace, 0))})
    if baseline is None:
        report.baseline = None
        return violations
    diff = {"added": 0, "deleted": 0, "modified": 0, "by_namespace": {}}
    for namespace in NAMESPACES:
        before = {doc.path: doc.sha256 for doc in baseline.by_namespace(namespace)}
        after = {doc.path: doc.sha256 for doc in corpus.by_namespace(namespace)}
        added = sorted(set(after) - set(before))
        deleted = sorted(set(before) - set(after))
        modified = sorted(path for path in set(before) & set(after) if before[path] != after[path])
        diff["by_namespace"][namespace] = {"added": added, "deleted": deleted,
                                           "modified": modified}
        diff["added"] += len(added)
        diff["deleted"] += len(deleted)
        diff["modified"] += len(modified)
        for path in deleted:
            if allow_deletions:
                violations.append(_review(path, "baseline-deleted-allowed"))
            else:
                violations.append(_failure(path, "baseline-deleted"))
    report.baseline = {"root": str(baseline.root), "diff": diff}
    return violations


def check_notes(corpus: Corpus) -> list[dict]:
    violations: list[dict] = []
    for doc in corpus.documents:
        blocks = _notes_blocks(doc.body)
        if not blocks:
            continue
        items = [item for block in blocks for item in block if item]
        if not items:
            violations.append(_failure(doc.path, "notes-empty"))
            continue
        if len(blocks) > 1:
            violations.append(_review(doc.path, "notes-multiple-blocks"))
        joined = "\n".join(items)
        if any(pattern.search(joined) for pattern in BROCHURE_PHRASES):
            violations.append(_failure(doc.path, "notes-brochure"))
        if any(pattern.search(joined) for pattern in INTERNAL_PHRASES):
            violations.append(_failure(doc.path, "notes-internal-phrase"))
        sentences = [sentence.strip() for sentence in SENTENCE.findall(joined)
                     if len(sentence.strip()) >= 20]
        if len(sentences) != len(set(sentences)):
            violations.append(_failure(doc.path, "notes-duplicated"))
    return violations


def check_price_gate(corpus: Corpus) -> list[dict]:
    violations: list[dict] = []
    for doc in corpus.documents:
        try:
            check_price(doc.path, doc.path)
            target = doc.path
        except DataError:
            # The path itself carries a price; never echo it.
            target = "path-sha256:" + hashlib.sha256(doc.path.encode("utf-8")).hexdigest()[:16]
        try:
            check_payload(doc.path, doc.raw)
        except (ImportError, ModuleNotFoundError):
            # A price gate that cannot run must fail the gate, never pass silently.
            violations.append({"path": "price-gate", "category": "price-gate-unavailable"})
            return violations
        except DataError as exc:
            message = str(exc)
            match = re.search(r"category=([a-z-]+)", message)
            category = match.group(1) if match else "uninspectable"
            if message.startswith("public price gate"):
                name = category if category.startswith("price") else f"price-{category}"
                violations.append({"path": target, "category": name})
            else:
                violations.append({"path": target, "category": "payload-rejected"})
    return violations


def input_digests(root: Path) -> dict[str, str]:
    corpus = load_corpus(Path(root))
    return {doc.path: doc.sha256 for doc in corpus.documents}


def verify_read_only(before: dict[str, str], after: dict[str, str]) -> bool:
    return before == after


def evaluate(corpus, base_corpus, allowed, expect_recipes, expect_applications,
             allow_deletions, label=None, references_baseline=None):
    root = corpus.root
    report = Report(label, root)
    report.counts = {namespace: len(corpus.by_namespace(namespace)) for namespace in NAMESPACES}
    report.counts["documents"] = len(corpus.documents)

    report.add(1, "identity", len(corpus.documents),
               check_identity(corpus, base_corpus))
    report.add(2, "tags", sum(1 for doc in corpus.documents
                              if doc.namespace in ("产品列表", "产品应用")),
               check_tags(corpus, allowed))
    report.add(3, "tag_field", len(corpus.by_namespace("产品列表")),
               check_tag_field(corpus))
    report.add(4, "field_types", len(corpus.by_namespace("产品列表")),
               check_field_types(corpus))
    report.add(5, "content", len(corpus.by_namespace("产品列表")),
               check_content(corpus))
    report.add(6, "references", len(corpus.by_namespace("产品列表"))
               + len(corpus.by_namespace("产品应用")) + len(corpus.by_namespace("产品配方")),
               check_references(corpus, references_baseline if references_baseline is not None
                                else base_corpus))
    report.add(7, "retention", len(corpus.documents),
               check_retention(corpus, base_corpus, expect_recipes,
                               expect_applications, allow_deletions, report))
    report.add(8, "notes", len(corpus.documents), check_notes(corpus))
    report.add(9, "price", len(corpus.documents), check_price_gate(corpus))

    return report


def snapshot_corpus(snapshot):
    """Parse the exact commit-pinned bytes being signed, not the mutable source tree."""
    corpus = Corpus(Path("snapshot"), Path("snapshot"), None)
    for path, raw in sorted(snapshot.files.items()):
        namespace = path.split("/", 1)[0]
        try:
            metadata, body, text = _parse(raw, path)
        except (UnicodeError, ValueError, yaml.YAMLError, RecursionError, DataError):
            metadata, body, text = None, "", ""
            corpus.unreadable.append({"path": path, "category": "frontmatter-invalid"})
        corpus.documents.append(Document(path, Path(path), raw, text, metadata, body, namespace))
    return corpus


def validate_snapshot(snapshot, baseline=None, identity_baseline=True):
    """Mandatory publication check; deletion approvals remain governed by release.py."""
    corpus = snapshot_corpus(snapshot)
    base = snapshot_corpus(baseline) if baseline is not None else None
    try:
        vocabulary = load_vocabulary(None)
    except InputError as exc:
        raise DataError("public quality gate configuration unavailable") from exc
    if not identity_baseline:
        # A signed historical release is re-admitted as-is by rollback;
        # retention/deletion approval is a separate, exact gate.
        base_identity = None
    else:
        base_identity = base
    report = evaluate(corpus, base_identity, vocabulary, 0, 0, True,
                      references_baseline=base)
    if not report.ok:
        # Never echo input values, tags, arbitrary field names, or YAML parser text.
        failed = ",".join(report.json()["summary"]["failed_checks"])
        raise DataError("public quality gate rejected candidate: checks=" + failed)
    payload = report.json()
    # Durable state itself is price-scanned; do not write a forbidden English
    # label even as the name of a passing check.
    for check in payload["checks"]:
        if check["name"] == "price":
            check["name"] = "prohibited_content"
    return payload


def scan(root, *, baseline=None, vocabulary=None, expect_recipes: int = 16,
         expect_applications: int = 21, allow_deletions: bool = False,
         label: str | None = None) -> Report:
    root = Path(root)
    if not root.is_dir():
        raise InputError(f"candidate root not found: {root}")
    allowed = load_vocabulary(vocabulary)
    corpus = load_corpus(root)
    base_corpus = load_corpus(Path(baseline)) if baseline is not None else None
    before = {doc.path: doc.sha256 for doc in corpus.documents}
    report = evaluate(corpus, base_corpus, allowed, expect_recipes,
                      expect_applications, allow_deletions, label)

    after = {doc.path: doc.sha256 for doc in load_corpus(root).documents}
    report.read_only = verify_read_only(before, after)
    if not report.read_only:
        retention = report.check("retention")
        retention["ok"] = False
        retention["violations"].append({"path": str(root), "category": "input-mutated"})
        retention["violation_count"] += 1
    return report


def summarise(report: Report) -> str:
    payload = report.json()
    lines = [f"root: {payload['root']}",
             f"documents: {payload['counts'].get('documents', 0)} "
             f"(产品列表 {payload['counts'].get('产品列表', 0)}, "
             f"产品应用 {payload['counts'].get('产品应用', 0)}, "
             f"产品配方 {payload['counts'].get('产品配方', 0)}, "
             f"产品TDS {payload['counts'].get('产品TDS', 0)})",
             f"read_only: {str(payload['read_only']).lower()}"]
    for check in payload["checks"]:
        lines.append(f"[{'PASS' if check['ok'] else 'FAIL'}] {check['id']}. "
                     f"{check['name']}: {check['violation_count']} violation(s), "
                     f"{check.get('warning_count', 0)} schema review(s)")
    lines.append(f"result: {'PASS' if payload['summary']['ok'] else 'FAIL'} "
                 f"({payload['summary']['violations']} violation(s))")
    return "\n".join(lines)


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="RSTONE pre-publication content gate (read-only)")
    parser.add_argument("--root", required=True, help="candidate tree (Rstone/+RstoneTDS/, files/, or a source root)")
    parser.add_argument("--report", required=True, help="JSON report destination")
    parser.add_argument("--baseline", default=None, help="baseline tree for identity/retention diff")
    parser.add_argument("--vocabulary", default=None, help="tag vocabulary JSON (default: audit/tag_vocabulary.json)")
    parser.add_argument("--label", default=None, help="label recorded in the report")
    parser.add_argument("--expect-recipes", type=int, default=16)
    parser.add_argument("--expect-applications", type=int, default=21)
    parser.add_argument("--allow-deletions", action="store_true",
                        help="record baseline deletions as warnings instead of failures")
    arguments = parser.parse_args(argv)
    report = scan(arguments.root, baseline=arguments.baseline,
                  vocabulary=arguments.vocabulary or default_vocabulary(),
                  expect_recipes=arguments.expect_recipes,
                  expect_applications=arguments.expect_applications,
                  allow_deletions=arguments.allow_deletions, label=arguments.label)
    report.write(Path(arguments.report))
    print(summarise(report))
    return 0 if report.ok else 1
