"""Exact publisher-owned catalogue transactions, never a general identity bypass.

The proposal digest is not an approval. Only the separate publisher policy can
approve it. All original findings are classified before any precise exemption.
"""
import re
from decimal import Decimal

from .protocol import canonical_json, sha256

SCHEMA = "rstone-catalog-revisions/1"
BASE = re.compile(r"(?:RS|RD)[0-9]+")
AMOUNT = re.compile(r"([0-9]+(?:\.[0-9]+)?)(?:\s*[-–~]\s*([0-9]+(?:\.[0-9]+)?))?")
UNKNOWN_AMOUNT = re.compile(r"(?:适量|少量|另加|按当量另加|开稀至可喷粘度|按NCO/OH=[0-9]+(?:\.[0-9]+)?另加)")
SOURCE_SCHEME = r"RS[0-9]+(?:-[A-Za-z0-9]+)*"
SCHEME = r"(?:[0-9]+|" + SOURCE_SCHEME + r")"
EDITORIAL = (r"-(?:参考配方(?:-方案(?:[0-9]+|" + SOURCE_SCHEME
             + r"(?:-方案[0-9]+)?))?|方案" + SCHEME + r")")
# This small candidate deliberately refuses unknown suffixes. These are existing
# recipe-use labels, not product identities and not a license to invent a use.
RECIPE_TAIL = re.compile(r"(?:-(?:亮|哑|银|黑|底))?(?:" + EDITORIAL + r")?")


def _scope_key(item):
    return item["category"], item["path"]


def _cells(line):
    # Remove at most one Markdown edge delimiter. strip('|') would hide extra
    # empty columns, letting a six-column row impersonate the five-column form.
    if not line.startswith("|") or not line.endswith("|"):
        return []
    return [cell.strip() for cell in line[1:-1].split("|")]


def transaction_payload(before, after, scope):
    return {"schema": SCHEMA,
            "before": {doc.path: doc.sha256 for doc in before.documents},
            "after": {doc.path: doc.sha256 for doc in after.documents},
            "scope": sorted([{"category": item["category"], "path": item["path"]}
                             for item in scope], key=_scope_key)}


def _visible_lines(doc):
    from . import quality_gate as q
    fence = ""
    excluded_level = 0
    body = re.sub(r"<!--.*?(?:-->|$)", "\n", doc.body, flags=re.S)
    lines = []
    for line in body.splitlines():
        text = line.strip()
        if fence:
            if re.fullmatch(re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*", text):
                fence = ""
            continue
        opening = re.match(r"(`{3,}|~{3,})", text)
        if opening:
            fence = opening[1]
            lines.append("")
            continue
        heading = q.HEADING.match(text)
        if heading:
            level = len(heading[1])
            if excluded_level and level <= excluded_level:
                excluded_level = 0
            if q.NONASSERTIONAL_BODY.search(text):
                excluded_level = excluded_level or level
        lines.append("" if excluded_level or line.startswith(("    ", "\t"))
                     or text.startswith(">") else text)
    return lines


def _recipe_owner(doc, tree):
    """Resolve the longest registered full name before testing a closed tail.

    Retirement is a path-identity decision, not a claim that the discarded BOM
    is quantitative or that its auxiliaries belong to the retained formula.
    """
    if doc.namespace != "产品配方":
        return None
    matches = [stem for stem in tree.stems("产品列表")
               if doc.stem == stem or doc.stem.startswith(stem + "-")]
    if not matches:
        return None
    owner = max(matches, key=len)
    return owner if RECIPE_TAIL.fullmatch(doc.stem[len(owner):]) else None


def _recipe_data(doc, tree):
    """Require a real quantitative owner and preserve every raw BOM tuple."""
    from . import quality_gate as q
    owner = _recipe_owner(doc, tree)
    if owner is None:
        return None
    products = set(tree.stems("产品列表"))
    claim = (doc.frontmatter or {}).get("关联产品")
    if not isinstance(claim, str):
        return None
    claimed = {q._reference_target(link) for link in q.WIKI_LINK.findall(claim)}
    if owner not in claimed or not claimed <= products:
        return None
    tables = []
    lines = _visible_lines(doc)
    for index, line in enumerate(lines[:-2]):
        header = _cells(line)
        if len(header) != 5 or header[0] != "原料编号":
            continue
        if header[1] not in ("投入数", "投入数(g)") or header[2:] != ["百分比(%)", "供应商", "备注"]:
            return None
        separator = _cells(lines[index + 1])
        if len(separator) != 5 or not all(re.fullmatch(r"\s*:?-{3,}:?\s*", cell) for cell in separator):
            return None
        rows = []
        owner_positive = False
        for row in lines[index + 2:]:
            if not row.startswith("|"):
                break
            cells = _cells(row)
            if len(cells) != 5 or not cells[0]:
                return None
            material, amount = cells[:2]
            numeric = AMOUNT.fullmatch(amount)
            if numeric:
                low, high = Decimal(numeric[1]), Decimal(numeric[2] or numeric[1])
                if low > high:
                    return None
                link = q.WIKI_LINK.fullmatch(material)
                actual = link[1] if link else material
                concentration = re.fullmatch(
                    re.escape("[[" + owner + "]]") + r"\(([0-9]+(?:\.[0-9]+)?)%\)", material)
                wrapped_owner = material == "主体树脂（[[" + owner + "]]）"
                if concentration and 0 < Decimal(concentration[1]) <= 100:
                    wrapped_owner = True
                owner_positive |= (actual == owner or wrapped_owner) and low > 0
            elif not UNKNOWN_AMOUNT.fullmatch(amount):
                ratio = re.fullmatch(
                    r"适量（NCO/OH=([0-9]+(?:\.[0-9]+)?)-([0-9]+(?:\.[0-9]+)?)）", amount)
                if not ratio or not 0 < Decimal(ratio[1]) <= Decimal(ratio[2]):
                    return None
            rows.append((material, amount))
        if not owner_positive:
            return None
        tables.append(tuple(rows))
    return (owner, tables[0], frozenset(claimed)) if len(tables) == 1 else None


def eligible_scope(before, after, findings):
    """Return only individually qualified findings; never clear identity wholesale."""
    from . import quality_gate as q
    if before is None or before.unreadable or after.unreadable or before.stray or after.stray:
        return []
    old_products = before.stems("产品列表")
    new_products = after.stems("产品列表")
    if len(set(old_products)) != len(old_products) or len(set(new_products)) != len(new_products):
        return []
    if len({name.casefold() for name in old_products}) != len(old_products) or \
            len({name.casefold() for name in new_products}) != len(new_products):
        return []
    old, new = set(old_products), set(new_products)
    # No new product identity (including a swallowing base), renumbering or
    # cross-suffix substitution can benefit from this policy, even with a hash.
    if new - old:
        return []
    retired = old - new
    retirement_paths = set()
    for name in retired:
        if not BASE.fullmatch(name):
            return []
        suffixes = {stem for stem in old if (numbered := q._numbered_identity(stem))
                    is not None and numbered[1] == name and numbered[2]}
        if not suffixes or not suffixes <= new:
            return []
        retirement_paths.add("产品列表/" + q._identity_family(name))
    before_recipes = before.by_namespace("产品配方")
    after_recipes = {doc.path: doc for doc in after.by_namespace("产品配方")}
    after_stems = {doc.stem for doc in after_recipes.values()}
    removed = [doc for doc in before_recipes if doc.path not in after_recipes]
    source_data = [info for doc in removed if (info := _recipe_data(doc, before))]
    after_data = {path: _recipe_data(doc, after) for path, doc in after_recipes.items()}
    retained_boms = set()
    for info in after_data.values():
        if info:
            sources = [prior for prior in source_data if prior[:2] == info[:2]]
            if sources and frozenset().union(*(prior[2] for prior in sources)) <= info[2]:
                retained_boms.add(info[:2])
    retained_owners = {owner for owner, _ in retained_boms}
    eligible = []
    for finding in findings:
        category, path = _scope_key(finding)
        if category == "identity-suffix-merged" and path in retirement_paths:
            eligible.append(finding)
            continue
        if category not in ("stem-collision-cross-namespace", "identity-suffix-dropped"):
            continue
        target = after_recipes.get(path)
        if target is None or not (data := after_data[path]):
            continue
        owner, bom, _claimed = data
        if (owner, bom) not in retained_boms:
            continue  # every merged member's registered associations survive
        if category == "identity-suffix-dropped":
            contributors = [doc for doc in before_recipes if doc.stem not in after_stems
                            and q._suffix_shortened(doc.stem, target.stem)
                            and not q._recipe_editorial_rename(doc, target, before, after)]
            # Partition by the longest full owner. Another owner's independent
            # retained main (with its own exact source and association union)
            # permits its own old variants to retire, never to merge into target.
            if not contributors or any((prior_owner := _recipe_owner(doc, before)) != owner
                                       and prior_owner not in retained_owners
                                       for doc in contributors):
                continue
        eligible.append(finding)
    return eligible


def proposal(before, after, findings):
    scope = eligible_scope(before, after, findings)
    if not scope:
        return None
    payload = transaction_payload(before, after, scope)
    return {"schema": SCHEMA, "status": "proposed-only", "approved": False,
            "fingerprint": sha256(canonical_json(payload)),
            "scopeSha256": sha256(canonical_json(payload["scope"])),
            "exemptedCount": len(scope), "unexemptedCount": len(findings) - len(scope)}


def apply(corpus, baseline, findings, approvals):
    if not approvals or baseline is None:
        return findings, None
    proposed = proposal(baseline, corpus, findings)
    if proposed is None or proposed["fingerprint"] not in approvals:
        return findings, None
    keys = {_scope_key(item) for item in eligible_scope(baseline, corpus, findings)}
    remaining = [item for item in findings if _scope_key(item) not in keys]
    return remaining, {"used": True, "fingerprint": proposed["fingerprint"],
                       "scopeSha256": proposed["scopeSha256"],
                       "exemptedCount": proposed["exemptedCount"]}


def main(argv=None):
    """Read local final byte trees; print a proposal, never write any policy."""
    import argparse
    import json
    from pathlib import Path
    from . import quality_gate as q
    from .protocol import DataError
    parser = argparse.ArgumentParser(description="Propose an exact catalogue digest; never approve")
    parser.add_argument("--before", required=True)
    parser.add_argument("--after", required=True)
    args = parser.parse_args(argv)
    try:
        before, after = q.load_corpus(Path(args.before)), q.load_corpus(Path(args.after))
        findings = q.check_identity(after, before, q.load_identity_migrations())
        result = proposal(before, after, findings)
        if result is None:
            result = {"schema": SCHEMA, "status": "ineligible", "approved": False}
        print(json.dumps(result, sort_keys=True))
        return 0 if result["status"] == "proposed-only" and not result["unexemptedCount"] else 1
    except (q.InputError, OSError, DataError):
        # Never echo internal paths, product names or parser-provided values.
        print(json.dumps({"schema": SCHEMA, "status": "unavailable", "approved": False}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
