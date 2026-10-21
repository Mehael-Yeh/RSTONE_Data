import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

from .build import inventory, mapping_digest
from .protocol import DataError, canonical_json, decode_json, require
from .release import Store
from .signing import Signer, validate_keys
from .sources import collect, fetch_sources
from .storage import atomic_write
from .transport import fetch_release

PROJECT = Path(__file__).resolve().parents[2]


def parser():
    result = argparse.ArgumentParser(description="RSTONE signed data publication and synchronization")
    commands = result.add_subparsers(dest="command", required=True)
    for name in ("fetch", "audit", "prepare", "promote", "complete", "verify", "package", "gc", "sync", "import"):
        sub = commands.add_parser(name)
        sub.add_argument("--keys", type=Path, default=PROJECT / "config/trusted_keys.json")
        sub.add_argument("--config", type=Path, default=PROJECT / "config/release.json")
        sub.add_argument("--report", type=Path)
        sub.add_argument("--github-output", action="store_true")
        if name in ("prepare", "promote", "complete", "verify", "package", "gc"):
            sub.add_argument("--store", type=Path, required=name != "verify")
        if name in ("audit", "prepare"):
            sub.add_argument("--rstone", type=Path)
            sub.add_argument("--tds", type=Path)
        if name == "fetch":
            sub.add_argument("--directory", type=Path, required=True)
        if name == "prepare":
            sub.add_argument("--key-id", default=os.environ.get("DATA_SIGNING_KEY_ID", ""))
            sub.add_argument("--public-scope-approved", action="store_true")
            sub.add_argument("--deletion-approval", default=os.environ.get("DATA_DELETION_APPROVAL"))
            sub.add_argument("--rollback", type=int)
        if name in ("promote", "complete", "verify"):
            sub.add_argument("--base-url", required=True)
            sub.add_argument("--allow-local-http", action="store_true")
            sub.add_argument("--require-cors", action="store_true")
            sub.add_argument("--attempts", type=int, default=1)
            sub.add_argument("--delay", type=float, default=5)
        if name == "verify":
            sub.add_argument("--pending", action="store_true")
            sub.add_argument("--pointer-file", type=Path)
        if name == "promote":
            sub.add_argument("--keep-pending", action="store_true")
        if name == "package":
            sub.add_argument("--sequence", type=int, required=True)
        if name == "gc":
            sub.add_argument("--retain", type=int)
            sub.add_argument("--grace-days", type=int)
            sub.add_argument("--apply", action="store_true")
            sub.add_argument("--require-no-pending", action="store_true")
        if name in ("sync", "import"):
            sub.add_argument("--cache", type=Path, required=True)
            sub.add_argument("--app-version", default="1.0.0")
        if name == "sync":
            sub.add_argument("--base-url", action="append", required=True)
            sub.add_argument("--allow-local-http", action="store_true")
        if name == "import":
            sub.add_argument("--package", type=Path, required=True)
    return result


def remote_verify(args, keys, expected=None, live=False):
    require(1 <= args.attempts <= 30 and 0 <= args.delay <= 30, "invalid HTTP retry settings")
    for attempt in range(args.attempts):
        try:
            latest, manifest, *_ = fetch_release(args.base_url, keys, None if live else expected,
                                                 allow_local_http=args.allow_local_http,
                                                 require_cors=args.require_cors)
            require(expected is None or latest == expected, "public latest has not reached target release")
            return {"status": "verified", "sequence": manifest["sequence"], "fileCount": len(manifest["files"])}
        except DataError:
            if attempt + 1 == args.attempts:
                raise
            time.sleep(args.delay)


def execute(args):
    if args.command in ("audit", "fetch"):
        if args.command == "fetch":
            snapshot = fetch_sources(args.directory)
        else:
            require(args.rstone and args.tds, "both source paths required")
            snapshot = collect(args.rstone, args.tds)
        return {"status": "validated", "sourceCommits": snapshot.commits, "fileCount": len(snapshot.files),
                "counts": dict(Counter(p.split("/")[0] for p in snapshot.files)),
                "mappingSha256": mapping_digest(inventory(snapshot.files)), "warnings": snapshot.warnings}
    keys = validate_keys(decode_json(args.keys.read_bytes())["keys"])
    config = decode_json(args.config.read_bytes())
    if args.command in ("sync", "import"):
        from .client import import_package, synchronize
        if args.command == "import":
            return import_package(args.package, args.cache, keys, app_version=args.app_version)
        return synchronize(args.base_url, args.cache, keys, app_version=args.app_version,
                           allow_local_http=args.allow_local_http)
    store = Store(args.store, keys) if args.store else None
    if args.command == "prepare":
        signer = Signer.load(args.key_id, os.environ.get("DATA_SIGNING_PRIVATE_KEY"), keys)
        approved = args.public_scope_approved or os.environ.get("PUBLIC_DATA_APPROVED") == "true"
        if store.pending():
            return store.prepare(None, signer, public_scope_approved=approved)
        if args.rollback is not None:
            return store.rollback(args.rollback, signer, public_scope_approved=approved)
        require(args.rstone and args.tds, "both source paths required")
        snapshot = collect(args.rstone, args.tds)
        return store.prepare(snapshot, signer, public_scope_approved=approved,
                             min_app_version=config["minAppVersion"],
                             max_deletion_fraction=config["maxDeletionFraction"], approval=args.deletion_approval)
    if args.command == "promote":
        return store.promote(lambda target: remote_verify(args, keys, target), keep_pending=args.keep_pending)
    if args.command == "complete":
        return store.complete(lambda target: remote_verify(args, keys, target, live=True))
    if args.command == "verify":
        target = None
        require(not (args.pending and args.pointer_file), "choose pending or pointer-file")
        if args.pending:
            require(store and store.pending(), "--pending requires a store with pending release")
            target = store.pending()["pointer"]
        if args.pointer_file:
            target = decode_json(args.pointer_file.read_bytes())
        return remote_verify(args, keys, target)
    if args.command == "package":
        return {"status": "packaged", "path": store.package(args.sequence)}
    if args.command == "gc":
        require(not args.require_no_pending or store.pending() is None, "finish pending publication before maintenance")
        return store.gc(config["retainVersions"] if args.retain is None else args.retain,
                        config["gcGraceDays"] if args.grace_days is None else args.grace_days, args.apply)
    raise DataError("unsupported command")


def emit(args, report):
    if args.report:
        atomic_write(args.report, canonical_json(report))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.github_output:
        output = os.environ.get("GITHUB_OUTPUT")
        require(output, "GITHUB_OUTPUT missing")
        with open(output, "a", encoding="utf-8") as stream:
            stream.write(f"changed={'true' if report.get('status') == 'pending' else 'false'}\n")
            stream.write(f"sequence={report.get('sequence', '')}\n")
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with open(summary_path, "a", encoding="utf-8") as summary:
                summary.write("\nRSTONE Data: " + report.get("status", "unknown") + "\n\n")
                for field in ("sequence", "added", "modified", "deleted", "unchanged", "newObjects", "uploadedBytes"):
                    if field in report:
                        summary.write(f"- {field}: {report[field]}\n")


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        report = execute(args)
        emit(args, report)
        return 0
    except (DataError, OSError, KeyError, ValueError) as error:
        message = str(error) if isinstance(error, DataError) else "configuration or IO failed; inspect paths and configuration"
        report = {"status": "failed", "error": message}
        if args.report:
            atomic_write(args.report, canonical_json(report))
        print(json.dumps(report, ensure_ascii=False), file=sys.stderr)
        return 1
