"""Catalogue semantics for canonical recipes; never interpret arbitrary Markdown."""
import html
import re
from decimal import Decimal
from pathlib import PurePosixPath
from . import recipe_codec as codec
from .protocol import DataError, require, sha256

AMOUNT = re.compile(r"([0-9]+(?:\.[0-9]+)?)(?:\s*[-–~]\s*([0-9]+(?:\.[0-9]+)?))?")
UNKNOWN = re.compile(r"(?:适量|少量|若干|另加|按当量另加|开稀至可喷粘度|按NCO/OH=[0-9]+(?:\.[0-9]+)?另加|适量（NCO/OH=[0-9]+(?:\.[0-9]+)?-[0-9]+(?:\.[0-9]+)?）)")
CONTACT = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?<![0-9])1[3-9][0-9]{9}(?![0-9])|(?:客户|联系人|工程师|手机|电话)\s*[:：]")


def business_strings(value):
    if type(value) is dict:
        for key, item in value.items():
            yield key
            yield from business_strings(item)
    elif type(value) is list:
        for item in value:
            yield from business_strings(item)
    elif type(value) is str:
        yield value


def scan_model(path, model):
    from .sources import SENSITIVE_CONTENT
    from .price_gate import check_metadata, check_price
    check_metadata(path, model)
    values = list(business_strings(model))
    # Also inspect assembled business-only views, not escaped JSON repr.
    # Structure keys/kind enums are never inserted between business text: an
    # adversary must not hide a secret by splitting it over adjacent notes.
    ordered = business_text(model)
    variants = [ordered, [v.replace(" ", "") for v in ordered],
                [v for v in ordered if v.strip()]]
    views = values + ["".join(ordered), "\n".join(ordered)]
    views += ["".join(v) for v in variants] + ["\n".join(v) for v in variants]
    # The deterministic visible body is a third reader: decode its entities so
    # entity encoding cannot separate strings that a human reads together.
    views.append(html.unescape(codec.body(model)))
    for value in views:
        check_price(path, value)
        decoded = html.unescape(value)
        assignment = re.search(r"(?:api[_-]?key|access[_-]?token|password|secret[_-]?key)\s*[:=]\s*\S+", decoded, re.I)
        require(not SENSITIVE_CONTENT.search(decoded) and not assignment, "sensitive structured content")
        require(not CONTACT.search(decoded), "personal structured content")


def business_text(model):
    """Ordered business strings only; no schema keys or block discriminators."""
    values = [model["title"], *model["preTable"], *model["columns"]]
    for row in model["rows"]:
        values.extend(row)
    for block in model["content"]:
        if block["kind"] == "note":
            values.append(block["text"])
        else:
            values.append(block["label"])
            values.extend(block["steps"])
    return values


def model_for(doc):
    from .sources import structured_marker
    if doc.namespace != "产品配方" or not structured_marker(doc.raw):
        return None
    try:
        return codec.parse(doc.raw)
    except ValueError:
        return None


def facts(model):
    # Display title/path and explicit association union can change in a reviewed
    # editorial merge. Every original cell and ordered narrative must survive.
    return sha256(codec.header({k: v for k, v in model.items()
                               if k not in {"title", "associatedProducts", "recipeId"}}).encode("ascii"))


def preserves_associations(prior, current):
    remaining = iter(current["associatedProducts"])
    return all(any(candidate == name for candidate in remaining) for name in prior["associatedProducts"])


def check_stability(before, after):
    if before is None:
        return []
    by_path = {d.path: model_for(d) for d in before.by_namespace("产品配方")}
    by_id = {m["recipeId"]: m for m in by_path.values() if m is not None}
    findings = []
    for doc in after.by_namespace("产品配方"):
        current = model_for(doc)
        if current is None:
            continue
        prior = by_path.get(doc.path)
        if prior is not None and prior["recipeId"] != current["recipeId"]:
            findings.append({"path": doc.path, "category": "structured-recipe-id-changed"})
        prior_id = by_id.get(current["recipeId"])
        if prior_id is not None and prior_id["ownerProduct"] != current["ownerProduct"]:
            findings.append({"path": doc.path, "category": "structured-recipe-id-owner-changed"})
    return findings


def amounts_valid(model):
    """Every row amount is a numeric value/range or the closed unknown set."""
    for row in model["rows"]:
        amount = row[1]
        match = AMOUNT.fullmatch(amount)
        if match:
            low, high = Decimal(match[1]), Decimal(match[2] or match[1])
            if low > high:
                return False
        elif not UNKNOWN.fullmatch(amount):
            return False
        elif amount.startswith("适量（NCO/OH="):
            ratio = re.fullmatch(r"适量（NCO/OH=([0-9]+(?:\.[0-9]+)?)-([0-9]+(?:\.[0-9]+)?)）", amount)
            if not ratio or not 0 < Decimal(ratio[1]) <= Decimal(ratio[2]):
                return False
    return True


def positive_owner(model):
    """A real positive owner row; only `main` may contribute identity credit."""
    owner = model["ownerProduct"]
    positive = False
    for row in model["rows"]:
        material, amount = row[:2]
        match = AMOUNT.fullmatch(amount)
        if match:
            low, high = Decimal(match[1]), Decimal(match[2] or match[1])
            if low > high:
                return False
            actual = material[2:-2] if material.startswith("[[") and material.endswith("]]") else material
            wrapped = material == "主体树脂（[[" + owner + "]]）"
            concentration = re.fullmatch(re.escape("[[" + owner + "]]") + r"\(([0-9]+(?:\.[0-9]+)?)%\)", material)
            wrapped |= bool(concentration and 0 < Decimal(concentration[1]) <= 100)
            positive |= (actual == owner or wrapped) and low > 0
    return positive


def quantitative(model):
    """Catalogue validity: amount syntax for all roles, positive owner for main."""
    return amounts_valid(model) and (
        model["classification"] != "main" or positive_owner(model))


def info(doc, corpus):
    model = model_for(doc)
    if model is None:
        return None
    from .catalog_revisions import _recipe_owner
    products = corpus.stems("产品列表")
    if (len(set(products)) != len(products) or len({p.casefold() for p in products}) != len(products)
            or model["title"] != doc.stem or _recipe_owner(doc, corpus) != model["ownerProduct"]
            or not set(model["associatedProducts"]) <= set(products)
            or not quantitative(model)):
        return None
    return model["ownerProduct"], facts(model), frozenset(model["associatedProducts"])


def check_catalog(corpus, required=False):
    from .sources import structured_marker
    findings = []
    ids = set()
    for doc in corpus.by_namespace("产品配方"):
        if not required and not structured_marker(doc.raw):
            continue
        model = model_for(doc)
        if model is None or info(doc, corpus) is None:
            findings.append({"path": doc.path, "category": "structured-recipe-invalid"})
            continue
        if model["recipeId"] in ids:
            findings.append({"path": doc.path, "category": "structured-recipe-id-duplicate"})
        ids.add(model["recipeId"])
    return findings
