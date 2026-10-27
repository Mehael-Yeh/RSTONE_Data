"""Non-waivable public price barrier; never include matched values in diagnostics."""
import re
import unicodedata

from .protocol import DataError, sha256

LABEL = re.compile(
    r"价格|價格|報價|报价|單價|单价|成交价|成交價|供货价|供貨價|采购价|採購價|"
    r"成本价|成本價|售价|售價|税价|稅價|折扣|返利|成本(?![\u4e00-\u9fff])|"
    r"(?<![a-z])(?:prices?|quotation|unit[_ -]?price|discount|rebate|cost)(?![a-z])", re.I)
NUMBER = r"[+-]?\d[\d,]*(?:\.\d+)?"
CODE = r"(?:CNY|RMB|USD|EUR|GBP|JPY|HKD|AUD|CAD|CHF)"
CURRENCY = r"(?:人民币|人民幣|美元|欧元|歐元|港元|日元|英镑|英鎊|[¥$€£])"
MONEY = re.compile(
    rf"(?<![a-z0-9]){CODE}\s*{NUMBER}(?![a-z0-9_])|{CURRENCY}\s*{NUMBER}|"
    rf"(?<![a-z0-9]){NUMBER}\s*(?:万|萬|亿|億)?(?:{CODE}(?![a-z0-9])|{CURRENCY}|元)", re.I)


def check_price(path, text):
    # Scan a normalized view only: original technical bytes and signed hashes never change.
    import html
    text = unicodedata.normalize("NFKC", html.unescape(text))
    text = "".join(c for c in text if unicodedata.category(c) != "Cf")
    category = "price-label" if LABEL.search(text) else "currency-amount" if MONEY.search(text) else None
    if category:
        # Even a filename can contain an amount. Use a stable path fingerprint, not its value.
        raise DataError(f"public price gate: path-sha256={sha256(path.encode('utf-8'))}; category={category}")


def check_metadata(path, value):
    """Inspect decoded scalar strings directly, never their escaped repr."""
    if isinstance(value, dict):
        for key, item in value.items():
            check_metadata(path, key)
            check_metadata(path, item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            check_metadata(path, item)
    elif isinstance(value, str):
        check_price(path, value)


def check_payload(path, raw):
    """Scan decoded metadata too; binary or unreadable payloads cannot pass."""
    from .protocol import decode_json
    from .sources import validate_markdown
    from .signing import unbase64
    check_price(path, path)
    if path.endswith(".sig"):
        try:
            unbase64(raw.rstrip(b"\n").decode("ascii"), 64)
        except (UnicodeError, DataError):
            raise DataError("public price gate: category=uninspectable-signature") from None
        return
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeError:
        raise DataError("public price gate: category=uninspectable-payload") from None
    check_price(path, text)
    if path.endswith(".json"):
        check_metadata(path, decode_json(raw))
    elif text.splitlines() and text.splitlines()[0].strip() == "---":
        # Object hashes do not carry their logical names; apply the same YAML decoder.
        validate_markdown("产品TDS/public-object.TDS.md", raw)


def check_tree(root):
    """Check the WHOLE outgoing tree, including unreferenced objects and old ZIPs."""
    import zipfile
    from pathlib import Path
    from .protocol import require
    from .storage import safe_file
    root = Path(root)
    if not root.exists():
        return
    for target in sorted(root.rglob("*")):
        relative = target.relative_to(root).as_posix()
        check_price(relative, relative)
        require(not target.is_symlink(), "public price gate: category=symlink")
        if target.is_dir():
            continue
        target = safe_file(root, relative)
        if target.suffix != ".zip":
            require(target.stat().st_size <= 32 * 1024 * 1024, "public price gate: category=oversized-payload")
            check_payload(relative, target.read_bytes())
            continue
        try:
            with zipfile.ZipFile(target) as archive:
                entries = archive.infolist()
                require(sum(e.file_size for e in entries) <= 544 * 1024 * 1024,
                        "public price gate: category=oversized-archive")
                for entry in entries:
                    require(entry.file_size <= 32 * 1024 * 1024,
                            "public price gate: category=oversized-payload")
                    check_payload(relative + "/" + entry.filename, archive.read(entry))
        except (zipfile.BadZipFile, RuntimeError, OSError):
            raise DataError("public price gate: category=uninspectable-archive") from None
