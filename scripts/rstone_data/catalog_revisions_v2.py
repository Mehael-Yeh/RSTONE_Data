"""Publisher-owned, all-byte-bound conversion proofs and independent v2 grants.

No source/CLI/environment overrides. A proof is a trusted migration fact, NOT
publication authority. Legacy parsing is never used to qualify new content.
"""
from pathlib import Path
from .protocol import SHA256, canonical_json, decode_json, require, sha256, logical_path, DataError
from . import recipe_codec as codec
from .recipe_catalog import check_catalog, model_for

SCHEMA = "rstone-catalog-revisions/2"
WITNESSES_SCHEMA = "rstone-recipe-migration-witnesses/1"
WITNESS_SCHEMA = "rstone-recipe-migration-witness/1"
WITNESS_KEYS = {"schema", "sourceCatalogApprovalFingerprint", "baselineManifestSha256",
                "beforeInventorySha256", "approvedLegacyInventorySha256", "typedInventorySha256",
                "modelFactsSha256", "protocol", "renderer", "records"}
RECORD_KEYS = {"pathSha256", "legacySha256", "legacySize", "modelSha256", "typedSha256", "typedSize"}
MIGRATION = {"category": "recipe-protocol-migration", "path": "产品配方"}


def config_root():
    return Path(__file__).resolve().parents[2] / "config"


def _read(path):
    root = config_root()
    require(not root.is_symlink() and not path.is_symlink() and not path.parent.is_symlink(),
            "invalid publisher configuration")
    require(path.stat().st_size <= 4 * 1024 * 1024, "oversized publisher configuration")
    return path.read_bytes()


def _policy(filename, schema, key):
    value = decode_json(_read(config_root() / filename))
    require(type(value) is dict and set(value) == {"schema", key} and value["schema"] == schema,
            "invalid publisher configuration")
    values = value[key]
    require(type(values) is list and all(type(v) is str and SHA256.fullmatch(v) for v in values)
            and len(set(values)) == len(values), "invalid publisher configuration")
    return values


def load_approvals():
    try:
        return frozenset(_policy("catalog_revisions_v2.json", SCHEMA, "approvals"))
    except (OSError, DataError) as exc:
        raise DataError("public quality gate configuration unavailable") from exc


def validate_witness(witness):
    require(type(witness) is dict and set(witness) == WITNESS_KEYS, "invalid migration witness")
    require(witness["schema"] == WITNESS_SCHEMA
            and type(witness["protocol"]) is str
            and witness["protocol"] in codec.VERSION_PAIRS
            and witness["renderer"] == codec.VERSION_PAIRS[witness["protocol"]],
            "invalid migration witness version")
    for key in WITNESS_KEYS - {"schema", "protocol", "renderer", "records"}:
        require(type(witness[key]) is str and SHA256.fullmatch(witness[key]), "invalid migration witness digest")
    records = witness["records"]
    require(type(records) is list and 0 < len(records) <= 100000, "invalid migration witness records")
    seen = set()
    for record in records:
        require(type(record) is dict and set(record) == RECORD_KEYS, "invalid migration witness record")
        for key in RECORD_KEYS - {"legacySize", "typedSize"}:
            require(type(record[key]) is str and SHA256.fullmatch(record[key]), "invalid migration witness digest")
        for key in ("legacySize", "typedSize"):
            require(type(record[key]) is int and 0 < record[key] <= codec.MAX_FILE, "invalid migration witness size")
        require(record["pathSha256"] not in seen, "duplicate migration witness path")
        seen.add(record["pathSha256"])
    return witness


def load_witnesses():
    try:
        result = []
        for digest in _policy("recipe_migration_witnesses.json", WITNESSES_SCHEMA, "witnesses"):
            raw = _read(config_root() / "migration_witnesses" / (digest + ".json"))
            require(sha256(raw) == digest, "migration witness integrity failure")
            result.append((digest, validate_witness(decode_json(raw))))
        return tuple(result)
    except (OSError, DataError) as exc:
        raise DataError("public quality gate configuration unavailable") from exc


def inventory(corpus):
    return {doc.path: {"sha256": doc.sha256, "size": len(doc.raw)} for doc in corpus.documents}


def migration_findings(before, after):
    if before is not None and any(model_for(doc) is None for doc in before.by_namespace("产品配方")):
        return [dict(MIGRATION)]
    return []


def _safe_candidate(before, after):
    from .catalog_revisions import BASE
    from .quality_gate import _numbered_identity
    if (before is None or before.unreadable or after.unreadable or before.stray or after.stray
            or check_catalog(after, required=True)):
        return False
    for corpus in (before, after):
        paths = [d.path for d in corpus.documents]
        products = corpus.stems("产品列表")
        if len({p.casefold() for p in paths}) != len(paths) or len({p.casefold() for p in products}) != len(products):
            return False
        try:
            for path in paths: logical_path(path)
        except DataError:
            return False
    old, new = set(before.stems("产品列表")), set(after.stems("产品列表"))
    if new - old: return False
    for name in old - new:
        if not BASE.fullmatch(name): return False
        suffixes = {stem for stem in old if (m := _numbered_identity(stem)) is not None
                    and m[1] == name and m[2]}
        if not suffixes or not suffixes <= new: return False
    return True


def document_versions(corpus):
    """Highest required document version; all-v1 payloads retain historical bytes."""
    models = [model_for(doc) for doc in corpus.by_namespace("产品配方")]
    schema = (codec.SCHEMA_V2 if any(m is not None and m["schema"] == codec.SCHEMA_V2
                                   for m in models) else codec.SCHEMA)
    return schema, codec.VERSION_PAIRS[schema]


def _matching_witness(before, after, witnesses, prior_approvals):
    before_digest = sha256(canonical_json(inventory(before)))
    after_inventory = inventory(after)
    after_digest = sha256(canonical_json(after_inventory))
    for digest, proof in witnesses:
        if ((proof["protocol"], proof["renderer"]) != document_versions(after)
                or proof["sourceCatalogApprovalFingerprint"] not in prior_approvals
                or proof["baselineManifestSha256"] != before.manifest_sha256
                or proof["beforeInventorySha256"] != before_digest
                or proof["typedInventorySha256"] != after_digest):
            continue
        records = {record["pathSha256"]: record for record in proof["records"]}
        recipes = after.by_namespace("产品配方")
        if set(records) != {sha256(doc.path.encode("utf-8")) for doc in recipes}: continue
        approved_legacy = dict(after_inventory)
        model_records = []
        matched = True
        for doc in recipes:
            path_digest = sha256(doc.path.encode("utf-8")); record = records[path_digest]
            model_digest = sha256(codec.header(model_for(doc)).encode("ascii"))
            if (record["typedSha256"] != doc.sha256 or record["typedSize"] != len(doc.raw)
                    or record["modelSha256"] != model_digest):
                matched = False; break
            approved_legacy[doc.path] = {"sha256": record["legacySha256"], "size": record["legacySize"]}
            model_records.append({"pathSha256": path_digest, "modelSha256": model_digest})
        if not matched: continue
        if sha256(canonical_json(approved_legacy)) != proof["approvedLegacyInventorySha256"]: continue
        if sha256(canonical_json(sorted(model_records, key=lambda r: r["pathSha256"]))) != proof["modelFactsSha256"]: continue
        return digest
    return None


def eligible_scope(before, after, findings, witnesses, prior_approvals):
    if not _safe_candidate(before, after): return [], None
    migrating = bool(migration_findings(before, after))
    if migrating:
        witness = _matching_witness(before, after, witnesses, prior_approvals)
        if witness is None: return [], None
        recipes = {doc.path: model_for(doc) for doc in after.by_namespace("产品配方")}
        old, new = set(before.stems("产品列表")), set(after.stems("产品列表"))
        from .quality_gate import _identity_family
        retirement = {"产品列表/" + _identity_family(name) for name in old - new}
        scope = []
        for finding in findings:
            category, path = finding["category"], finding["path"]
            if finding == MIGRATION or (category == "identity-suffix-merged" and path in retirement):
                scope.append(finding)
            elif category in {"stem-collision-cross-namespace", "identity-suffix-dropped"}:
                if path in recipes and recipes[path]["classification"] == "main": scope.append(finding)
        return scope, witness
    # Typed-to-typed editorial qualification includes all five columns, stable
    # recipe ID and the complete ordered preTable/content, never two-column BOMs.
    from .catalog_revisions import eligible_scope as typed_scope
    return typed_scope(before, after, findings), None


def transaction_payload(before, after, scope, witness_sha256):
    protocol, renderer = document_versions(after)
    return {"schema": SCHEMA, "before": inventory(before), "after": inventory(after),
            "baselineManifestSha256": before.manifest_sha256,
            "protocol": protocol, "renderer": renderer,
            "migrationWitnessSha256": witness_sha256,
            "scope": sorted([{"category": f["category"], "path": f["path"]} for f in scope],
                            key=lambda f: (f["category"], f["path"]))}


def proposal(before, after, findings):
    from .quality_gate import load_catalog_revisions
    witnesses, _ = load_witnesses(), load_approvals()
    prior = load_catalog_revisions()
    if before is None or not isinstance(before.manifest_sha256, str) or not SHA256.fullmatch(before.manifest_sha256):
        return None
    scope, witness = eligible_scope(before, after, findings, witnesses, prior)
    if not scope: return None
    payload = transaction_payload(before, after, scope, witness)
    return {"schema": SCHEMA, "status": "proposed-only", "approved": False,
            "fingerprint": sha256(canonical_json(payload)),
            "scopeSha256": sha256(canonical_json(payload["scope"])),
            "migrationWitnessSha256": witness, "exemptedCount": len(scope),
            "unexemptedCount": len(findings)-len(scope)}


def apply(corpus, baseline, findings):
    approvals = load_approvals()
    load_witnesses()  # Missing/malformed fixed configuration fails closed even on an initial publish.
    proposed = proposal(baseline, corpus, findings)
    if proposed is None or proposed["fingerprint"] not in approvals:
        return findings, None
    from .quality_gate import load_catalog_revisions
    scope, _ = eligible_scope(baseline, corpus, findings, load_witnesses(), load_catalog_revisions())
    keys = {(f["category"], f["path"]) for f in scope}
    remaining = [f for f in findings if (f["category"], f["path"]) not in keys]
    return remaining, {"used": True, **{k: proposed[k] for k in (
        "fingerprint", "scopeSha256", "migrationWitnessSha256", "exemptedCount")}}


def main(argv=None):
    """Read-only proposal. Supplied baseline bytes grant no release authority."""
    import argparse
    import json
    from . import quality_gate as q
    from .protocol import validate_manifest
    from .recipe_catalog import check_stability
    parser = argparse.ArgumentParser(description="Propose a v2 digest; never approve or install a proof")
    parser.add_argument("--before", required=True, type=Path)
    parser.add_argument("--after", required=True, type=Path)
    parser.add_argument("--baseline-manifest", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        before, after = q.load_corpus(args.before), q.load_corpus(args.after)
        raw = args.baseline_manifest.read_bytes()
        manifest = validate_manifest(decode_json(raw))
        expected = {record["path"]: {"sha256": record["sha256"], "size": record["size"]}
                    for record in manifest["files"]}
        require(expected == inventory(before), "baseline inventory mismatch")
        before.manifest_sha256 = sha256(raw)
        findings = (check_catalog(after, required=True) + check_stability(before, after)
                    + migration_findings(before, after)
                    + q.check_identity(after, before, q.load_identity_migrations()))
        result = proposal(before, after, findings)
        if result is None:
            result = {"schema": SCHEMA, "status": "ineligible", "approved": False}
        print(json.dumps(result, sort_keys=True))
        return 0 if result["status"] == "proposed-only" and result["unexemptedCount"] == 0 else 1
    except (OSError, DataError, q.InputError):
        print(json.dumps({"schema": SCHEMA, "status": "unavailable", "approved": False}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
