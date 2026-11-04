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
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .price_gate import LABEL as PRICE_LABEL
from .price_gate import MONEY as PRICE_MONEY
from .price_gate import check_payload, check_price
from .protocol import SHA256, DataError, canonical_json, decode_json, require

SCHEMA = "rstone-quality-gate/1"
NAMESPACES = ("产品列表", "产品应用", "产品配方", "产品TDS")
SOURCE_NAMESPACES = ("产品列表", "产品应用", "产品配方")
MAX_ITEMS = 400

FIRST_TAGS = ("水性", "油性", "粉末")
SECOND_TAGS = ("UV", "PU", "PUD", "双固化", "聚烯烃")
FUNCTIONALITY_TAG = re.compile(r"^(\d+(?:\.\d+)?)官$")
FUNCTIONALITY_FIELDS = ("官能度",)
PLAIN_NUMBER = re.compile(r"\d+(?:\.\d+)?")
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
    r"^[<>≤≥＜＞]?\s*\d+(?:\.\d+)?(?:\s*[-–~—]\s*\d+(?:\.\d+)?)?"
    r"\s*(?:±\s*\d+(?:\.\d+)?)?\s*%?$")
EXTERNAL_LINK = re.compile(r"^(?:https?://|mailto:|tel:|ftp://|//|www\.)", re.I)
WIKI_LINK = re.compile(r"\[\[([^\]]+)\]\]")
# Frontmatter fields that name a product or recipe. Application records also
# carry engineer and provenance wiki links; docs/SOURCE_AUDIT.md reports only
# product references, so metadata scanning stays on product-bearing fields.
APPLICATION_REFERENCE_FIELDS = ("底漆", "中漆", "面漆", "产品", "推荐产品", "相关产品",
                                "对标", "技术源")
PRODUCT_REFERENCE_FIELDS = ("技术源", "对标")

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
    r"\d\s*(?:℃|°C|%|％|h|min|s|秒|分钟|小时|μm|um|mm|mJ|mPa·s|cP|wt%|次|遍|kg|g/L|°|度|B|H)"
    r"|\d+\s*[–~-]\s*\d+")
TECHNICAL_WORDS = re.compile(
    r"体系|固化|耐[水热酸碱黄变污湿]|附着|硬度|光泽|施工|喷涂|涂布|辊涂|淋涂|基材|底材|"
    r"官能|粘度|固含|水煮|上镀|流平|丰满|柔韧|耐磨|耐钢丝|消光|哑光|银粉|色浆|固化剂")
USAGE_HEADINGS = re.compile(r"用途|特性|应用|概述|物理性能|物性|使用|施工|说明")
# Qualitative prose is an additional content path, not permission to publish
# editorial metadata, examples or assertions explicitly marked as unverified.
NONASSERTIONAL_BODY = re.compile(
    r"未(?:经)?(?:验证|核验|核实|确认|实测|测试)|待(?:验证|核验|核实|确认|实测|测试)|"
    r"尚未|推测|假设|预计|可能|尚需|示例|样例|范例|例文|例如|模板|品质优良|理想的?选择|值得信赖")
BODY_LABEL = re.compile(
    r"^(?:tags|标签|分类|体系|工程师|来源|资料来源|技术源|参考资料|采编(?:记录)?)\s*[:：]", re.I)
# Ordinary prose needs an asserted use, property/result, or process constraint;
# merely naming a technical field (however long) is not such a relation.
BODY_ASSERTION = re.compile(
    r"(?:^|[，,。；;])\s*(?:本(?:品|产品|号)|该(?:树脂|产品)|产品)?"
    r"(?:可|不|不可|不宜)?(?:用于|适用于|用作|搭配|配合)"
    r"[^，,。；;\n]{0,48}(?:基材|底材|底漆|面漆|涂层|涂膜|树脂|体系|固化剂|喷涂|涂布)"
    r"|(?:涂膜|涂层)\s*(?:较|很|偏|过|不|无|为|是)?"
    r"(?:柔韧|耐磨|开裂|脱落|发白|失光|发黏)"
    r"|(?:附着|硬度|光泽|耐水|耐热|耐磨|耐湿热|耐黄变|柔韧)"
    r"(?:表现|性能|性|力)?\s*(?:较|很|偏|过|不|无|为|是)?"
    r"(?:良好|优异|优良|不足|好|佳|差|高|低)"
    r"|(?:需要|必须|不能|不可|不宜)[^，,。；;\n]{0,32}"
    r"(?:固化剂|底漆|面漆|喷涂|涂布|固化)")
BODY_NEGATIVE_RESULT = re.compile(
    r"(?:未通过|未能通过|不通过)[^，,。；;\n]{0,24}(?:水煮|附着|耐水|耐磨|固化)"
    r"|(?:附着(?:力|表现)?|粘接力|耐水性|耐磨性)(?:不足|差|不佳|不合格)")
BODY_COMPOSITION = re.compile(r"(?:复配|混合)树脂|不含卤素|粘接力(?:好|佳|良好)"
                              r"|(?:验证过|通过)[^，,。；;\n]{0,12}水煮")
BODY_RECOMMENDATION = re.compile(
    r"^工程师(?:明确)?(?:推荐|建议)(?:用于|适用于|用作)"
    r"[^，,。；;\n]{1,64}?(?:基材|底材|底漆|面漆|涂层|固化剂)(?=[，,。；;]|但|$)")
BODY_PENCIL = re.compile(r"(?:铅笔)?硬度\s*[:：]?\s*((?:\d{1,2}\s*[BH]|HB|[BHF])"
                         r"(?:\s*[（(]\s*\d+\s*g\s*[)）])?)(?=[。；;，,\s]|$)", re.I)

PRODUCT_PREFIX = re.compile(r"^(?:RS|RD)(?=\d)")
PREFIX_ANY = re.compile(r"^(?:RS|RD)", re.I)
IDENTITY_MIGRATIONS_SCHEMA = "rstone-identity-migrations/1"
# Approval eligibility is deliberately narrower than RD/RS detection below:
# canonical uppercase prefix + immediate ASCII number, then verbatim suffix.
# Ingestible nonnumeric/space-prefixed forms are detected but fail closed when
# requesting migration credit; they are never silently exempted from review.
MIGRATABLE_RD = re.compile(r"RD[0-9]+.*", re.S)
NUMBERED_IDENTITY = re.compile(r"([^0-9]*[0-9]+)(.*)", re.S)


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
    manifest_sha256: str | None = None

    def by_namespace(self, namespace: str) -> list[Document]:
        return [doc for doc in self.documents if doc.namespace == namespace]

    def stems(self, namespace: str) -> list[str]:
        return [doc.stem for doc in self.by_namespace(namespace)]


def _normalise_field(key: str) -> str:
    return re.sub(r"[（(].*?[)）]", "", key).strip()


def _parse(raw: bytes, path: str) -> tuple[dict | None, str, str]:
    from .sources import structured_marker
    if structured_marker(raw):
        from .recipe_codec import parse
        model = parse(raw)
        return model, raw.split(b"\n---\n", 1)[1].decode("utf-8"), raw.decode("utf-8")
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
        for entry, suffix in _files_under(directory):
            relative = f"{namespace}/{suffix}"
            if entry.name.endswith(".md"):
                _load_document(corpus, entry, relative, namespace)
            elif "/" in suffix and not entry.name.lower().endswith(".md"):
                continue  # nested non-Markdown is not part of a publication snapshot
            else:
                corpus.stray.append({"path": relative, "category": "identity-suffix"})
    if tds is not None:
        for entry, suffix in _files_under(tds):
            relative = f"产品TDS/{suffix}"
            if "/" in suffix:
                # sources only publishes TDS files at the top level of RstoneTDS.
                corpus.stray.append({"path": relative, "category": "identity-tds-nested"})
            elif not entry.name.endswith(".TDS.md"):
                corpus.stray.append({"path": relative, "category": "identity-tds-suffix"})
            else:
                _load_document(corpus, entry, relative, "产品TDS")
    return corpus


def _files_under(directory: Path):
    """Yield (path, posix-suffix) for published files, recursively, hiding dotted entries.

    sources._blobs() publishes nested Markdown below a source namespace, so the
    gate must read the same set or those files ship unvalidated.
    """
    for entry in sorted(directory.rglob("*")):
        suffix = entry.relative_to(directory).as_posix()
        if any(part.startswith(".") for part in suffix.split("/")):
            continue
        if entry.is_dir():
            continue
        yield entry, suffix


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


def default_identity_migrations() -> Path:
    # Publisher-owned policy, never read from source documents, Snapshot metadata,
    # dispatch payloads or an operator-controlled source-directory argument.
    return Path(__file__).resolve().parents[2] / "config" / "identity_migrations.json"


def load_identity_migrations() -> frozenset[str]:
    try:
        payload = decode_json(default_identity_migrations().read_bytes())
        require(isinstance(payload, dict) and set(payload) == {"schema", "approvals"}
                and payload["schema"] == IDENTITY_MIGRATIONS_SCHEMA,
                "invalid identity migration policy")
        approvals = payload["approvals"]
        require(isinstance(approvals, list) and all(isinstance(item, str)
                and SHA256.fullmatch(item) for item in approvals)
                and len(set(approvals)) == len(approvals), "invalid identity migration approvals")
        return frozenset(approvals)
    except (OSError, DataError) as exc:
        raise InputError("identity migration policy unavailable") from exc


def default_catalog_revisions() -> Path:
    # Publisher code-repository policy only: no source, environment or CLI override.
    return Path(__file__).resolve().parents[2] / "config" / "catalog_revisions.json"


def load_catalog_revisions() -> frozenset[str]:
    from .catalog_revisions import SCHEMA as CATALOG_SCHEMA
    try:
        path = default_catalog_revisions()
        require(not path.is_symlink(), "invalid catalogue policy")
        payload = decode_json(path.read_bytes())
        require(isinstance(payload, dict) and set(payload) == {"schema", "approvals"}
                and payload["schema"] == CATALOG_SCHEMA, "invalid catalogue policy")
        approvals = payload["approvals"]
        require(isinstance(approvals, list) and all(isinstance(item, str)
                and SHA256.fullmatch(item) for item in approvals)
                and len(set(approvals)) == len(approvals), "invalid catalogue approvals")
        return frozenset(approvals)
    except (OSError, DataError) as exc:
        raise InputError("catalogue revision policy unavailable") from exc


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
        self.catalog_revision = None
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
            **({"catalogRevision": self.catalog_revision} if self.catalog_revision else {}),
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
    """Functionality from a numeric scalar, quoted or not.

    sources.validate_markdown() and _typed_violations() accept a quoted scalar
    such as ``官能度: "6"``, so it must be comparable with the 3官 tag; tolerance
    or inequality spellings stay unverifiable rather than silently conflicting.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and PLAIN_NUMBER.fullmatch(value.strip()):
        return float(value.strip())
    return None


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

def _retained_migrations(corpus: Corpus, baseline: Corpus,
                        approvals: frozenset[str]) -> set[str]:
    """Credit only individually approved, byte-bound, injective RD -> RS moves.

    The experimental field is a consistency check, NOT an authorization. The
    reviewed publisher policy must also approve the exact source/target bytes
    against the entire current baseline inventory. No business IDs live here.
    """
    if not approvals:
        return set()
    before = baseline.by_namespace("产品列表")
    after = corpus.by_namespace("产品列表")
    baseline_digest = hashlib.sha256(canonical_json(
        {doc.path: doc.sha256 for doc in baseline.documents})).hexdigest()
    retained: set[str] = set()
    for source in before:
        if not MIGRATABLE_RD.fullmatch(source.stem):
            continue
        if any(doc.stem.casefold() == source.stem.casefold() for doc in after):
            continue  # Retaining/moving an RD is not a disappearing identity.
        if sum(doc.stem.casefold() == source.stem.casefold() for doc in before) != 1:
            continue
        claims = [doc for doc in after
                  if (doc.frontmatter or {}).get("实验牌号") == source.stem]
        if len(claims) != 1:
            continue  # No missing/list/approximate aliases, conflicting claims or many-to-one.
        target = claims[0]
        if not MIGRATABLE_RD.fullmatch("RD" + target.stem[2:]) \
                or not target.stem.startswith("RS") \
                or Path(source.path).parent != Path(target.path).parent \
                or sum(doc.stem.casefold() == target.stem.casefold() for doc in after) != 1:
            continue
        # Changing only the prefix must keep EVERY suffix byte. A different
        # number is never inferred: it needs its own exact owner fingerprint.
        if re.match(r"\d+", source.stem[2:])[0] == re.match(r"\d+", target.stem[2:])[0] \
                and source.stem[2:] != target.stem[2:]:
            continue
        fingerprint = hashlib.sha256(canonical_json({
            "schema": IDENTITY_MIGRATIONS_SCHEMA,
            "baselineSha256": baseline_digest,
            "source": {"path": source.path, "sha256": source.sha256},
            "target": {"path": target.path, "sha256": target.sha256},
        })).hexdigest()
        if fingerprint in approvals:
            retained.add(source.stem)
    return retained


def _numbered_identity(stem: str):
    # Use exactly the ingress prefix boundary, including Unicode upper-case
    # expansions. Extract from the ORIGINAL spelling; never normalize its bytes.
    return NUMBERED_IDENTITY.fullmatch(stem) if stem.upper().startswith(("RD", "RS")) else None


def _identity_family(stem: str) -> str:
    numbered = _numbered_identity(stem)
    return (numbered[1] if numbered is not None else stem.split("-")[0]).casefold()


def _suffix_shortened(original: str, candidate: str) -> bool:
    """Compare suffixes only after matching the entire numbered base.

    RD94 and RD9402 are distinct bases. Dots, letters, versions, spaces and
    symbols are suffix bytes, not a reason to skip the loss check. Unnumbered
    legacy names retain their existing hyphen boundary.
    """
    old = _numbered_identity(original)
    new = _numbered_identity(candidate)
    if old is not None and new is not None:
        old_suffix, new_suffix = old[2].casefold(), new[2].casefold()
        return (old[1].casefold() == new[1].casefold()
                and old_suffix != new_suffix and old_suffix.startswith(new_suffix))
    return original.casefold().startswith(candidate.casefold() + "-")


def _recipe_visible_lines(doc):
    """Keep excluded Markdown distinct from a real blank table boundary."""
    lines = []
    fence = ""
    html_end = None
    excluded_level = 0
    # Preserve physical line boundaries, and retain raw fence syntax. Replacing
    # a comment by a newline could silently truncate the BOM; stripping a
    # comment before checking a closer could manufacture a valid closing fence.
    body = re.sub(r"<!--.*?(?:-->|$)",
                  lambda match: re.sub(r"[^\r\n]", " ", match[0]), doc.body, flags=re.S)
    physical_lines = doc.body.splitlines()
    visible_lines = body.splitlines()
    for index, (raw, line) in enumerate(zip(physical_lines, visible_lines)):
        text = line.strip()
        # Only a raw 0-3-space marker may close a fence. Preserve the prior
        # fail-closed treatment of uncertain/indented openers; fixing closure
        # must not expand the recipe exception's accepted input surface.
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", raw)
        if fence:
            if (marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence)
                    and re.fullmatch(r"[ \t]*", marker[2])):
                fence = ""
            lines.append(None)
            continue
        if html_end is not None:
            # Raw-text HTML ends at its actual closing tag; other HTML blocks
            # remain opaque until a physical blank line, not a masked comment.
            if html_end == "" and not raw.strip(" \t"):
                html_end = None
                lines.append("")
            else:
                if html_end and re.search(html_end, raw, re.I):
                    html_end = None
                lines.append(None)
            continue
        opening = re.match(r"(`{3,}|~{3,})", raw.strip())
        if opening:
            fence = opening[1]
            lines.append(None)
            continue
        if line.expandtabs(4).startswith("    ") or text.startswith(">"):
            lines.append(None)
            continue
        opaque = re.match(r"^ {0,3}(<\?|<!\[CDATA\[|<![A-Z])", raw)
        if opaque:
            html_end = {"<?": r"\?>", "<![CDATA[": r"\]\]>"}.get(opaque[1], ">")
            if re.search(html_end, raw):
                html_end = None
            lines.append(None)
            continue
        html = re.match(r"^ {0,3}<(/?)([A-Za-z][A-Za-z0-9:-]*)(?=[\t />]|$)", raw)
        if html:
            tag = html[2].lower()
            html_end = (r"</" + tag + ">" if not html[1]
                        and tag in ("pre", "script", "style", "textarea") else "")
            if html_end and re.search(html_end, raw, re.I):
                html_end = None
            lines.append(None)
            continue
        if raw != line:
            # A comment cannot erase an opaque block opener or the visible
            # example label, and cannot create a blank boundary in a BOM.
            if NONASSERTIONAL_BODY.search(text):
                excluded_level = excluded_level or 1
            lines.append(None)
            continue
        heading = HEADING.match(text)
        heading_label = text
        if not heading and text and index + 1 < len(physical_lines):
            underline = re.fullmatch(r" {0,3}(=+|-+)[ \t]*", physical_lines[index + 1])
            if underline:
                # A Setext heading may span a whole paragraph. Do not lose an
                # example/unverified marker merely because it is on line one.
                for previous in reversed(lines):
                    if not previous or HEADING.match(previous) or previous.startswith("|"):
                        break
                    heading_label = previous + "\n" + heading_label
                # Parser-local heading marker; source and BOM bytes stay intact.
                text = ("# " if underline[1][0] == "=" else "## ") + text
                heading = HEADING.match(text)
        if heading:
            level = len(heading[1])
            if excluded_level and level <= excluded_level:
                excluded_level = 0
            if NONASSERTIONAL_BODY.search(heading_label):
                excluded_level = excluded_level or level
            lines.append(text)  # a real heading ends the preceding table
        else:
            lines.append(None if excluded_level else text)
    return lines


def _recipe_table_rows(lines, start):
    """Return a whole supported table, never an ingredient-prefix approval.

    A missing left edge is legal Markdown but outside this narrow recipe
    exception: refuse that table rather than silently dropping the row. Plain
    trailing prose is fine; resumed pipe rows without a block boundary are not.
    """
    rows = []
    prose = False
    for line in lines[start:]:
        if line is None:
            return None  # hidden continuation must not approve the prefix
        if not line or HEADING.match(line):
            break
        if "|" in line:
            if prose or not line.startswith("|"):
                return None
            rows.append(line)
        else:
            prose = True
    return rows


def _recipe_product_ids(doc: Document, corpus: Corpus) -> set[str]:
    from .recipe_catalog import info, model_for
    model = model_for(doc)
    if model is None or model["classification"] != "main" or info(doc, corpus) is None:
        return set()
    return {model["ownerProduct"]}


def _legacy_recipe_product_ids(doc: Document, corpus: Corpus) -> set[str]:
    """Credit a registered identity only in a real, quantified ingredient row.

    Association metadata alone is not evidence. This intentionally recognizes
    the existing five-column recipe format, not arbitrary Markdown tables.
    Detection never changes document bytes or folds a product identity.
    """
    if doc.namespace != "产品配方":
        return set()
    products = set(corpus.stems("产品列表"))
    claim = (doc.frontmatter or {}).get("关联产品")
    if claim is not None:
        if not isinstance(claim, str):
            return set()
        products &= {_reference_target(link) for link in WIKI_LINK.findall(claim)}
    lines = _recipe_visible_lines(doc)
    identities: set[str] = set()
    for index, line in enumerate(lines[:-2]):
        if line is None or lines[index + 1] is None:
            continue
        # Consume at most one edge delimiter; adjacent pipes are empty cells.
        header = [cell.strip() for cell in line.removeprefix("|").removesuffix("|").split("|")]
        if len(header) != 5 or header[0] != "原料编号" \
                or header[1] not in ("投入数", "投入数(g)") \
                or header[2:] != ["百分比(%)", "供应商", "备注"]:
            continue
        separator = lines[index + 1].removeprefix("|").removesuffix("|").split("|")
        if len(separator) != 5 or not all(re.fullmatch(r"\s*:?-{3,}:?\s*", cell)
                                          for cell in separator):
            continue
        rows = _recipe_table_rows(lines, index + 2)
        if rows is None:
            continue
        table_ids: set[str] = set()
        valid = True
        for row in rows:
            # Wiki aliases contain pipes: require an exact, unaliased identity
            # for this narrow exception rather than guessing column positions.
            cells = [cell.strip() for cell in row.removeprefix("|").removesuffix("|").split("|")]
            if len(cells) != 5 or not cells[0]:
                valid = False
                break
            amount = re.fullmatch(r"(\d+(?:\.\d+)?)(?:\s*[-–~]\s*(\d+(?:\.\d+)?))?", cells[1])
            if not amount:
                if cells[1] not in ("适量", "少量"):
                    valid = False
                    break
                continue
            low = float(amount[1])
            high = float(amount[2] or amount[1])
            if low > high:
                valid = False
                break
            material = cells[0]
            link = WIKI_LINK.fullmatch(material)
            identity = link[1] if link else material
            if identity in products and low > 0:
                table_ids.add(identity)
        if valid:
            identities.update(table_ids)
    return identities


def _recipe_editorial_rename(original: Document, candidate: Document,
                             baseline: Corpus, corpus: Corpus) -> bool:
    """Only a known editorial tail may disappear, never a product suffix."""
    if candidate.namespace != "产品配方" or not original.stem.startswith(candidate.stem):
        return False
    tail = original.stem[len(candidate.stem):]
    scheme = r"(?:[0-9]+|RS[0-9]+(?:-[A-Za-z0-9]+)*)"
    if not re.fullmatch(r"-(?:参考配方(?:-方案" + scheme + r")?|方案" + scheme + r")", tail):
        return False
    from .recipe_catalog import info, model_for, preserves_associations
    prior, current = info(original, baseline), info(candidate, corpus)
    if prior is None or current is None or prior[:2] != current[:2] or not prior[2] <= current[2]:
        return False
    old_model, new_model = model_for(original), model_for(candidate)
    if old_model["recipeId"] != new_model["recipeId"] or not preserves_associations(old_model, new_model):
        return False
    named_ids = []
    for doc, tree in ((original, baseline), (candidate, corpus)):
        # Longest complete registered identity wins; a base cannot stand in for
        # a more specific suffix even when both appear in the ingredient table.
        matches = [stem for stem in tree.stems("产品列表")
                   if doc.stem == stem or doc.stem.startswith(stem + "-")]
        identity = max(matches, key=len) if matches else None
        if identity is None or identity not in _recipe_product_ids(doc, tree):
            return False
        named_ids.append(identity)
    return named_ids[0] == named_ids[1]


def check_identity(corpus: Corpus, baseline: Corpus | None,
                   approvals: frozenset[str] = frozenset()) -> list[dict]:
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
            if doc.stem.casefold() in product_stems \
                    and not (namespace == "产品配方"
                             and doc.stem in _recipe_product_ids(doc, corpus)):
                violations.append({"path": doc.path,
                                   "category": "stem-collision-cross-namespace"})
    for doc in corpus.by_namespace("产品列表"):
        stem = doc.stem
        if stem != stem.strip() or stem.endswith("-"):
            violations.append({"path": doc.path, "category": "identity-malformed"})
    if baseline is not None:
        migrated = _retained_migrations(corpus, baseline, approvals)
        current_products = corpus.by_namespace("产品列表")
        current_stems = {doc.stem for doc in current_products}
        baseline_stems = set(baseline.stems("产品列表"))
        # Ingestion accepts every RD/RS-prefixed spelling, not only the subset
        # eligible for an approval. Classification must never hide a migration;
        # case folding here does not rewrite any identity or receipt bytes.
        added_formal = [doc for doc in current_products
                        if doc.stem not in baseline_stems and doc.stem.upper().startswith("RS")]
        deleted_formal = [doc for doc in baseline.by_namespace("产品列表")
                          if doc.stem not in current_stems and doc.stem.upper().startswith("RS")]
        added_hashes = Counter(doc.sha256 for doc in added_formal)
        deleted_hashes = Counter(doc.sha256 for doc in deleted_formal)
        # A provable one-to-one byte-identical RS move is not a fresh RD -> RS
        # replacement. This preserves existing ordinary moves/deletions without
        # granting RD migration credit or bypassing suffix-family checks.
        new_formal = any(added_hashes[doc.sha256] != 1 or deleted_hashes[doc.sha256] != 1
                         for doc in added_formal)
        for source in baseline.by_namespace("产品列表"):
            if not source.stem.upper().startswith("RD") or source.stem in current_stems:
                continue
            # Even a singleton must not self-authorize by declaring its old RD
            # as an alias. Same-number replacements also require the field and
            # policy, so omitting or corrupting the field cannot bypass approval.
            # An undeclared RD deletion + fresh RS addition is ambiguous, not
            # evidence of an unrelated change. Require explicit review instead
            # of allowing the alias to be omitted to hide a renumbering.
            if source.stem not in migrated and (new_formal or any(
                    (doc.frontmatter or {}).get("实验牌号") == source.stem
                    or doc.stem == "RS" + source.stem[2:]
                    for doc in current_products)):
                violations.append({"path": source.path,
                                   "category": "identity-migration-unapproved"})
        for namespace in NAMESPACES:
            before = baseline.stems(namespace)
            after = {doc.stem for doc in corpus.by_namespace(namespace)}
            folded = {stem.casefold(): stem for stem in before}
            # Adding an independent base is not a suffix deletion if every old
            # identity still exists verbatim or has its own exact RD -> RS
            # receipt. Credit is product-only; other namespaces get no migration
            # exemption. Never infer coverage from counts or a shorter prefix.
            uncovered = set(before) - after - (migrated if namespace == "产品列表" else set())
            before_docs = {doc.stem: doc for doc in baseline.by_namespace(namespace)}
            for doc in corpus.by_namespace(namespace):
                stem = doc.stem
                if stem in before:
                    continue
                original = folded.get(stem.casefold())
                if original is not None:
                    violations.append({"path": doc.path,
                                       "category": "identity-case-rewritten"})
                    continue
                if any(_suffix_shortened(other, stem) and not (
                        namespace == "产品配方" and _recipe_editorial_rename(
                            before_docs[other], doc, baseline, corpus)) for other in uncovered):
                    violations.append({"path": doc.path,
                                       "category": "identity-suffix-dropped"})
            if namespace == "产品列表":
                families: dict[str, set[str]] = {}
                for stem in before:
                    families.setdefault(_identity_family(stem), set()).add(stem)
                after_families = {_identity_family(stem) for stem in after}
                for base, members in families.items():
                    # Count equality is not identity coverage: unrelated new
                    # suffixes must not mask loss of an old complete suffix.
                    # Even a singleton cannot be replaced by another suffix of
                    # the same numbered base. Pure singleton deletions and
                    # unrelated one-to-one RS moves keep their existing boundary.
                    if members - after - migrated and (len(members) > 1 or base in after_families):
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
        if not tags:
            violations.append({"path": doc.path, "category": "tag-empty"})
            continue
        if doc.namespace != "产品列表":
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
    if RESIDUE_LITERALS.match(value) or value.strip() in PLACEHOLDERS:
        return [_failure(path, "field-residue", VISCOSITY_FIELD)]
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
    # Every body position shares the same structural and evidence filters.
    # A heading or a unit must not bypass code/provenance/nonassertion checks.
    return _plain_body_has_fact(body)


def _body_detection_text(text: str) -> str:
    """Remove paired Markdown emphasis only in a disposable detection string."""
    return re.sub(r"(\*{1,3}|_{1,3})(?=\S)(.*?\S)\1", r"\2", text)


def _visible_fact_segments(body: str):
    """Yield detection-only prose, excluding examples and non-product evidence.

    Exclusions apply to their section/paragraph, not every fact in a product.
    Sentence-separated independent facts remain usable; recommendations prove
    only that recommendation, never the untested result that follows it.
    """
    body = re.sub(r"<!--.*?(?:-->|$)", "\n", body, flags=re.S)
    fence = ""
    inline_ticks = 0
    quoted_paragraph = False
    excluded_level = 0
    excluded_paragraph = False
    for line in body.splitlines():
        stripped = line.strip()
        if fence:
            if re.fullmatch(re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*", stripped):
                fence = ""
            continue
        if line.startswith(("    ", "\t")):
            continue
        opening = re.match(r"(`{3,}|~{3,})", stripped)
        if opening:
            fence = opening.group(1)
            continue
        if not stripped:
            excluded_paragraph = False
            quoted_paragraph = False
            continue
        # Compact headings are accepted by the existing notes reader too.
        heading = re.match(r"^(#{1,6})(?!#)\s*", stripped)
        if heading:
            level = len(heading.group(1))
            title = _body_detection_text(stripped[heading.end():].strip())
            if excluded_level and level <= excluded_level:
                excluded_level = 0
            if NONASSERTIONAL_BODY.search(title) or BODY_LABEL.match(title + ":"):
                excluded_level = excluded_level or level
            excluded_paragraph = False
            quoted_paragraph = False
            continue
        if stripped.startswith(">"):
            quoted_paragraph = True
        runs = re.findall(r"`+", stripped)
        was_inline_code = bool(inline_ticks)
        for run in runs:
            if not inline_ticks:
                inline_ticks = len(run)
            elif len(run) == inline_ticks:
                inline_ticks = 0
        if excluded_level or quoted_paragraph or was_inline_code or runs \
                or "[[" in stripped or re.search(r"\[[^]]*\]\(", stripped):
            continue
        content = re.sub(r"^(?:[-*+]\s+|\d+[.)]\s+)", "", _body_detection_text(stripped)).strip()
        if re.fullmatch(r"[\w+-]+(?:\s*[/、|]\s*[\w+-]+)+[。.]?", content):
            continue  # a bare taxonomy/keyword list, not a technical assertion
        if excluded_paragraph:
            continue
        for segment in re.split(r"[。!?！？]", content):
            segment = segment.strip()
            if not segment:
                continue
            if BODY_LABEL.match(segment) or any(pattern.search(segment)
                    for pattern in BROCHURE_PHRASES + INTERNAL_PHRASES):
                excluded_paragraph = True
                continue
            recommendation = BODY_RECOMMENDATION.match(segment)
            if NONASSERTIONAL_BODY.search(segment):
                if recommendation and not NONASSERTIONAL_BODY.search(recommendation[0]):
                    yield recommendation[0]
                excluded_paragraph = True
                continue
            yield segment


def _plain_body_has_fact(body: str) -> bool:
    """All positions use the same eligible prose and factual predicates."""
    return any(FACT_UNIT.search(segment) or BODY_PENCIL.search(segment)
               or BODY_NEGATIVE_RESULT.search(segment) or BODY_COMPOSITION.search(segment)
               or BODY_RECOMMENDATION.match(segment)
               or _has_fact(segment) and BODY_ASSERTION.search(segment)
               for segment in _visible_fact_segments(body))


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
        # Condition-qualified spellings such as 光泽(60°) are the same physical
        # property as 光泽; check_field_types() already normalises them.
        properties: dict[str, object] = {}
        for key, value in metadata.items():
            if not isinstance(key, str) or key == "tags":
                continue
            name = _normalise_field(key)
            if properties.get(name) in (None, ""):
                properties[name] = value
        substantive = False
        property_value = False
        for name in TECHNICAL_FIELDS:
            value = properties.get(name)
            if value is None or value == "":
                continue
            if _numeric(value) or (isinstance(value, str) and value.strip()):
                substantive = True
                property_value = True
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
        from .recipe_catalog import model_for, business_strings
        model = model_for(doc)
        body = "\n".join(business_strings(model)) if model is not None else doc.body
        references = [(reference, "reference-broken")
                      for reference in WIKI_LINK.findall(body)]
        if doc.namespace == "产品列表":
            metadata = doc.frontmatter or {}
            for key in PRODUCT_REFERENCE_FIELDS:
                value = metadata.get(key)
                if isinstance(value, str):
                    references += [(reference, "reference-broken-techsource")
                                   for reference in WIKI_LINK.findall(value)]
        if doc.namespace == "产品应用":
            for key, value in (doc.frontmatter or {}).items():
                if not isinstance(value, str) or not isinstance(key, str):
                    continue
                if _normalise_field(key) not in APPLICATION_REFERENCE_FIELDS:
                    continue  # engineer / provenance wiki links are not product references
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


def _notes_table_cells(line: str) -> tuple[str, ...]:
    """Split real pipe delimiters, retaining escaped pipes as cell content."""
    if line.expandtabs(4).startswith("    "):
        return ()  # indented code is not a table
    cells = re.split(r"(?<!\\)\|", line.strip())
    if len(cells) < 2:
        return ()
    if not cells[0].strip():
        cells.pop(0)
    if cells and not cells[-1].strip():
        cells.pop()
    return tuple(cell.strip() for cell in cells)


def _notes_duplicate_units(body: str) -> list[object]:
    """Keep prose global, but compare complete performance rows in test context."""
    # _notes_blocks intentionally drops headings; preserve them here as table
    # context only. They must never reset the global ordinary-prose comparison.
    blocks: list[list[str]] = []
    level = 0
    for raw in body.splitlines():
        line = raw.strip()
        note = NOTES_HEADING.match(line)
        heading = HEADING.match(line)
        if note:
            level = len(note.group(1))
            blocks.append([])
        elif heading and len(heading.group(1)) <= level:
            level = 0
        elif level:
            blocks[-1].append(raw.rstrip())
    prose: list[str] = []
    rows: list[object] = []
    for block in blocks:
        fence = ""
        headings: dict[int, str] = {}
        label = ""
        context: dict[str, str] = {}
        index = 0
        while index < len(block):
            line = block[index]
            # CommonMark permits at most three leading spaces on fences.
            # A closing fence cannot carry a language/name suffix.
            marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
            if marker:
                token, tail = marker.group(1), marker.group(2)
                if not fence:
                    if token[0] != "`" or "`" not in tail:
                        fence = token
                elif (token[0] == fence[0] and len(token) >= len(fence)
                      and re.fullmatch(r"[ \t]*", tail)):
                    fence = ""
                prose.append(line)
                index += 1
                continue
            heading = HEADING.match(line)
            if not fence and heading:
                depth = len(heading.group(1))
                headings = {key: value for key, value in headings.items() if key < depth}
                headings[depth] = line[heading.end():].strip()
                label, context = "", {}
                index += 1
                continue
            if not fence:
                if re.fullmatch(r"(?:\*\*.+\*\*|__.+__)", line):
                    label = line
                condition = re.match(r"^(?:[-*]\s*)?(配方|基方|工艺|施工|测试条件|固化|条件)\s*[:：]", line)
                if condition:
                    context[condition.group(1)] = line[condition.end():].strip()
            header = _notes_table_cells(line)
            delimiter = (_notes_table_cells(block[index + 1])
                         if index + 1 < len(block) else ())
            if (not fence and len(header) >= 2 and len(header) == len(delimiter)
                    and all(re.fullmatch(r":?-{3,}:?", cell) for cell in delimiter)):
                index += 2
                performance = (len(header) == 4 and header[0] == "项目"
                               and header[1] in ("基材或对象", "基材/对象", "对象", "基材")
                               and header[2] in ("测试条件", "条件") and header[3] == "结果")
                while performance and index < len(block):
                    cells = _notes_table_cells(block[index])
                    if len(cells) != len(header):
                        break
                    rows.append((tuple(sorted(headings.items())), label,
                                 tuple(sorted(context.items())), cells))
                    index += 1
                continue
            prose.append(line)
            index += 1
    return rows + [sentence.strip() for sentence in SENTENCE.findall("\n".join(prose))
                   if len(sentence.strip()) >= 20]


def _structured_notes_blocks(model: dict) -> list[list[str]]:
    """Select real ordered notes sections, not Markdown inside literal values.

    A process label is the only section boundary in rstone-recipe/1. Following
    note blocks belong to that section until the next process, just as they do
    in the frozen renderer. Cells, preTable, embedded hashes/pipes/newlines and
    entity-looking scalar text never create or terminate a section here.
    """
    blocks: list[list[str]] = []
    active: list[str] | None = None
    for block in model["content"]:
        if block["kind"] == "process":
            active = None
            if block["label"].strip() == "特别说明":
                active = list(block["steps"])
                blocks.append(active)
        elif active is not None:
            active.append(block["text"])
    return blocks


def check_notes(corpus: Corpus) -> list[dict]:
    from .recipe_catalog import model_for

    violations: list[dict] = []
    for doc in corpus.documents:
        model = model_for(doc)
        blocks = (_structured_notes_blocks(model) if model is not None
                  else _notes_blocks(doc.body))
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
        # Canonical values are literal text, not nested Markdown. Only the
        # legacy path may interpret tables, headings and fences for context.
        sentences = ([sentence.strip() for item in items
                      for sentence in SENTENCE.findall(item)
                      if len(sentence.strip()) >= 20] if model is not None
                     else _notes_duplicate_units(doc.body))
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

    from .catalog_revisions_v2 import apply as apply_catalog_revision, migration_findings
    from .recipe_catalog import check_catalog, check_stability
    # Always validate both policy generations; the installed old approval is a
    # historical precondition only, never authority for new typed bytes.
    load_catalog_revisions()
    findings = (check_catalog(corpus) + check_stability(base_corpus, corpus) + migration_findings(base_corpus, corpus)
                + check_identity(corpus, base_corpus, load_identity_migrations()))
    findings, report.catalog_revision = apply_catalog_revision(corpus, base_corpus, findings)
    report.add(1, "identity", len(corpus.documents), findings)
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
    corpus.manifest_sha256 = snapshot.manifest_sha256
    for path, raw in sorted(snapshot.files.items()):
        namespace = path.split("/", 1)[0]
        try:
            metadata, body, text = _parse(raw, path)
        except (UnicodeError, ValueError, yaml.YAMLError, RecursionError, DataError):
            metadata, body, text = None, "", ""
            corpus.unreadable.append({"path": path, "category": "frontmatter-invalid"})
        corpus.documents.append(Document(path, Path(path), raw, text, metadata, body, namespace))
    return corpus


_UNSET = object()


def validate_snapshot(snapshot, baseline=None, identity_baseline=True, references_baseline=_UNSET):
    """Mandatory publication check; deletion approvals remain governed by release.py."""
    corpus = snapshot_corpus(snapshot)
    base = snapshot_corpus(baseline) if baseline is not None else None
    links_base = base if references_baseline is _UNSET else (
        snapshot_corpus(references_baseline) if references_baseline is not None else None)
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
    try:
        report = evaluate(corpus, base_identity, vocabulary, 0, 0, True,
                          references_baseline=links_base)
    except InputError as exc:
        raise DataError("public quality gate configuration unavailable") from exc
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
