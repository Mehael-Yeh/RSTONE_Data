"""Recoverable prepare/promote publisher. Only public/ is exposed by static hosting."""

import io
import re
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .build import changes, check_deletions, inventory, make_manifest, mapping_digest
from .protocol import DataError, canonical_json, decode_json, manifest_name, require, sha256, validate_manifest
from .signing import verify_release
from .sources import Snapshot, validate_markdown
from .storage import ReleaseLock, atomic_write, immutable_write, safe_file


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def pointer_for(manifest, raw):
    name = manifest_name(manifest["sequence"])
    return {"protocolVersion": 1, "sequence": manifest["sequence"], "dataVersion": manifest["dataVersion"],
            "manifestPath": name, "manifestSha256": sha256(raw),
            "signaturePath": name.removesuffix(".json") + ".sig", "keyId": manifest["keyId"]}


class Store:
    def __init__(self, root, keys):
        self.root = Path(root)
        self.public = self.root / "public"
        self.state = self.root / "state"
        require(not any(p.is_symlink() for p in (self.root, self.public, self.state)), "symlink in store root")
        self.keys = keys

    def latest_bytes(self):
        path = safe_file(self.public, "latest.json")
        return path.read_bytes() if path.exists() else None

    def pending(self):
        path = safe_file(self.state, "pending.json")
        return decode_json(path.read_bytes()) if path.exists() else None

    def read(self, pointer=None, verify_objects=True):
        if pointer is None:
            raw_latest = self.latest_bytes()
            if raw_latest is None:
                return None
            pointer = decode_json(raw_latest)
        # verify_release validates relative names; do this before using them in filesystem IO.
        from .protocol import validate_latest
        validate_latest(pointer)
        try:
            raw = safe_file(self.public, pointer["manifestPath"]).read_bytes()
            sig = safe_file(self.public, pointer["signaturePath"]).read_bytes()
            manifest = verify_release(pointer, raw, sig, self.keys)
            if verify_objects:
                for record in manifest["files"]:
                    self.object(record)
            return manifest
        except OSError as exc:
            raise DataError("release resource missing or unreadable") from exc

    def object(self, record):
        try:
            raw = safe_file(self.public, "objects/" + record["sha256"]).read_bytes()
        except OSError as exc:
            raise DataError("referenced object missing") from exc
        require(len(raw) == record["size"] and sha256(raw) == record["sha256"], "object integrity failure")
        return raw

    def sequences(self):
        directory = self.public / "manifests"
        if not directory.exists():
            return []
        return sorted({int(p.stem) for p in directory.iterdir()
                       if re.fullmatch(r"[0-9]{6,}\.(json|sig)", p.name)})

    def prepare(self, snapshot, signer, *, public_scope_approved=False, min_app_version="1.0.0",
                max_deletion_fraction=0.25, approval=None, now=None, rollback_of=None, failpoint=None):
        require(public_scope_approved, "public data scope must be explicitly approved before publishing")
        require(signer is not None, "production signing key required")
        self.public.mkdir(parents=True, exist_ok=True)
        with ReleaseLock(self.root):
            pending = self.pending()
            if pending:
                self.read(pending["pointer"])
                return dict(pending["summary"], status="pending", resumed=True)
            previous = self.read()
            # Even programmatic ingestion is validated. Rollback data also passes present-day secret checks.
            for path, raw in snapshot.files.items():
                validate_markdown(path, raw)
            records = inventory(snapshot.files)
            check_deletions(previous, records, max_deletion_fraction,
                            approval if rollback_of is None else
                            f"{previous['sequence']}:{mapping_digest(records)}" if previous else None)
            diff = changes(previous, records)
            summary = {"added": len(diff["added"]), "modified": len(diff["modified"]),
                       "deleted": len(diff["deleted"]), "unchanged": len(diff["unchanged"]),
                       "newObjects": 0, "uploadedBytes": 0, "sourceCommits": snapshot.commits,
                       "mappingSha256": mapping_digest(records), "warnings": snapshot.warnings,
                       "changes": diff}
            if previous and not (summary["added"] or summary["modified"] or summary["deleted"]):
                return dict(summary, status="unchanged", sequence=previous["sequence"])
            sequence = max(self.sequences(), default=0) + 1
            moment = now or utc_now()
            manifest = make_manifest(snapshot, sequence, moment.strftime("%Y.%m.%d") + f".{sequence:03d}",
                                     moment.strftime("%Y-%m-%dT%H:%M:%SZ"), signer.key_id,
                                     min_app_version, rollback_of)
            raw_manifest = canonical_json(manifest)
            signature = signer.sign(raw_manifest)
            pointer = pointer_for(manifest, raw_manifest)
            verify_release(pointer, raw_manifest, signature, self.keys)
            for raw in snapshot.files.values():
                if immutable_write(self.public, "objects/" + sha256(raw), raw):
                    summary["newObjects"] += 1
                    summary["uploadedBytes"] += len(raw)
            if failpoint:
                failpoint("objects")
            immutable_write(self.public, pointer["manifestPath"], raw_manifest)
            immutable_write(self.public, pointer["signaturePath"], signature)
            if failpoint:
                failpoint("manifest")
            self.read(pointer)
            summary.update(sequence=sequence, status="pending", dataVersion=manifest["dataVersion"])
            pending = {"pointer": pointer, "previousLatestSha256": sha256(self.latest_bytes())
                       if self.latest_bytes() is not None else None, "summary": summary}
            atomic_write(self.state / "pending.json", canonical_json(pending))
            if failpoint:
                failpoint("pending")
            return summary

    def promote(self, remote_verifier=None, failpoint=None, keep_pending=False):
        with ReleaseLock(self.root):
            pending = self.pending()
            require(pending is not None, "no pending release")
            pointer = pending["pointer"]
            self.read(pointer)
            new_latest = canonical_json(pointer)
            current = self.latest_bytes()
            already_committed = current == new_latest
            require(already_committed or (sha256(current) if current is not None else None) ==
                    pending["previousLatestSha256"], "release baseline changed; refusing pointer promotion")
            if remote_verifier is not None:
                remote_verifier(pointer)
            if failpoint:
                failpoint("before-pointer")
            atomic_write(safe_file(self.public, "latest.json"), new_latest)
            if failpoint:
                failpoint("after-pointer")
            summary = dict(pending["summary"], status="published")
            atomic_write(self.state / "reports" / f"{pointer['sequence']:06d}.json", canonical_json(summary))
            if not keep_pending:
                (self.state / "pending.json").unlink()
            return summary

    def complete(self, remote_verifier):
        """Clear durable recovery marker only after live latest and its objects are verified."""
        with ReleaseLock(self.root):
            pending = self.pending()
            require(pending is not None, "no pending deployment to acknowledge")
            self.read(pending["pointer"])
            require(self.latest_bytes() == canonical_json(pending["pointer"]), "local latest not promoted")
            remote_verifier(pending["pointer"])
            (self.state / "pending.json").unlink()
            return dict(pending["summary"], status="published")

    def rollback(self, sequence, signer, **options):
        require(sequence in self.sequences(), "rollback sequence unavailable")
        path = safe_file(self.public, manifest_name(sequence))
        manifest = validate_manifest(decode_json(path.read_bytes()))
        self.read(pointer_for(manifest, path.read_bytes()))
        snapshot = Snapshot({r["path"]: self.object(r) for r in manifest["files"]}, manifest["sourceCommits"])
        return self.prepare(snapshot, signer, rollback_of=sequence,
                            min_app_version=manifest["minAppVersion"], **options)

    def package(self, sequence):
        path = safe_file(self.public, manifest_name(sequence))
        raw = path.read_bytes()
        manifest = validate_manifest(decode_json(raw))
        pointer = pointer_for(manifest, raw)
        self.read(pointer)
        entries = {"metadata/manifest.json": raw,
                   "metadata/manifest.sig": safe_file(self.public, pointer["signaturePath"]).read_bytes()}
        entries.update({"files/" + r["path"]: self.object(r) for r in manifest["files"]})
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in sorted(entries.items()):
                info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, content)
        relative = f"packages/{sequence:06d}.zip"
        immutable_write(self.public, relative, buffer.getvalue())
        return relative

    def gc(self, retain=30, grace_days=7, apply=False, now=None):
        require(type(retain) is int and retain >= 1, "retain must be positive")
        require(type(grace_days) is int and grace_days >= 0, "invalid GC grace period")
        with ReleaseLock(self.root):
            current = self.read()
            require(current is not None, "cannot GC uninitialized store")
            manifests = {}
            for sequence in self.sequences():
                path = safe_file(self.public, manifest_name(sequence))
                raw = path.read_bytes()
                manifest = validate_manifest(decode_json(raw))
                self.read(pointer_for(manifest, raw))
                manifests[sequence] = manifest
            protected = set(sorted(manifests)[-retain:]) | {current["sequence"]}
            pending = self.pending()
            if pending:
                self.read(pending["pointer"])
                protected.add(pending["pointer"]["sequence"])
            cutoff = (now or utc_now()) - timedelta(days=grace_days)
            for sequence, manifest in manifests.items():
                published = datetime.strptime(manifest["publishedAt"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                if published > cutoff:
                    protected.add(sequence)
            referenced = {r["sha256"] for seq in protected for r in manifests[seq]["files"]}
            removals = []
            for sequence in manifests.keys() - protected:
                removals.extend([manifest_name(sequence), manifest_name(sequence).removesuffix(".json") + ".sig"])
                if safe_file(self.public, f"packages/{sequence:06d}.zip").exists():
                    removals.append(f"packages/{sequence:06d}.zip")
            for obj in (self.public / "objects").iterdir():
                require(re.fullmatch(r"[0-9a-f]{64}", obj.name) and not obj.is_symlink(), "unexpected object in store")
                if obj.name not in referenced and datetime.fromtimestamp(obj.stat().st_mtime, timezone.utc) <= cutoff:
                    removals.append("objects/" + obj.name)
            if apply:
                for relative in removals:
                    safe_file(self.public, relative).unlink()
            return {"protectedSequences": sorted(protected), "remove": sorted(removals), "applied": apply}
