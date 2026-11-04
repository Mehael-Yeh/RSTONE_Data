"""Recoverable prepare/promote publisher. Only public/ is exposed by static hosting."""

import io
import re
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .build import changes, check_deletions, inventory, make_manifest, mapping_digest
from .protocol import (
    DataError,
    canonical_json,
    decode_json,
    manifest_name,
    require,
    sha256,
    validate_manifest,
)
from .signing import verify_release
from .price_gate import check_tree, check_payload
from .sources import Snapshot, validate_markdown
from .quality_gate import validate_snapshot
from .storage import ReleaseLock, atomic_write, immutable_write, safe_file


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0)


# Distinguishes "the caller did not record a baseline" from "the baseline was the
# first release, i.e. none".
_INFER = object()


def _pending_baseline(pending):
    """Baseline sequence recorded when the pending release was prepared.

    Pending markers written before baselines were recorded fall back to
    inference, so an in-flight release survives an upgrade.
    """
    return pending.get("qualityBaselineSequence", _INFER)


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
        check_tree(self.public)
        check_tree(self.state)
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
                    validate_markdown(record["path"], self.object(record))
                if manifest["schemaVersion"] == 2:
                    self._require_structured(self._snapshot(manifest))
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

    def _snapshot(self, manifest):
        return Snapshot({r["path"]: self.object(r) for r in manifest["files"]},
                        manifest["sourceCommits"], manifest_sha256=sha256(
                            safe_file(self.public, manifest_name(manifest["sequence"])).read_bytes()))

    def _quality_baseline_manifest(self, sequence):
        """Read signed evidence, not another outgoing generation.

        GC may move a baseline's manifest/signature to private state. Objects
        remain content-addressed in public and are retained only while needed.
        Never fall back from a damaged public release to a private copy.
        """
        check_tree(self.public)
        check_tree(self.state)
        root, relative = self.public, manifest_name(sequence)
        if not safe_file(root, relative).exists():
            root, relative = self.state, f"quality-baselines/{sequence:06d}.json"
        try:
            raw = safe_file(root, relative).read_bytes()
            signature = safe_file(root, relative.removesuffix(".json") + ".sig").read_bytes()
        except OSError as exc:
            raise DataError("quality baseline release missing") from exc
        manifest = validate_manifest(decode_json(raw))
        require(manifest["sequence"] == sequence, "quality baseline sequence mismatch")
        verify_release(pointer_for(manifest, raw), raw, signature, self.keys)
        return manifest, raw, signature

    def _signed_snapshot(self, sequence):
        """Verify exact signed baseline bytes, including private GC evidence."""
        manifest, raw, _ = self._quality_baseline_manifest(sequence)
        snapshot = Snapshot({r["path"]: self.object(r) for r in manifest["files"]},
                            manifest["sourceCommits"], manifest_sha256=sha256(raw))
        for path, content in snapshot.files.items():
            validate_markdown(path, content)
        if manifest["schemaVersion"] == 2:
            self._require_structured(snapshot)
        return snapshot

    def _require_structured(self, snapshot):
        from .recipe_codec import parse
        for path, raw in snapshot.files.items():
            if path.startswith("产品配方/"):
                try:
                    parse(raw)
                except ValueError as exc:
                    raise DataError("public quality gate rejected candidate: checks=identity; structured recipe required") from exc

    def _bound_quality_baseline(self, manifest, baseline_sequence, earlier, control):
        """A pending marker cannot turn an existing signed baseline into none."""
        message = "public quality gate rejected candidate: checks=identity; baseline binding mismatch"
        if control and control["pointer"]["sequence"] == manifest["sequence"]:
            current = self.read()
            previous_hash = control.get("previousLatestSha256")
            if current is not None and current["sequence"] < manifest["sequence"]:
                require(previous_hash == sha256(self.latest_bytes()), message)
                expected = current["sequence"]
            elif previous_hash is not None:
                matches = []
                for sequence in earlier:
                    raw = safe_file(self.public, manifest_name(sequence)).read_bytes()
                    prior = validate_manifest(decode_json(raw))
                    if sha256(canonical_json(pointer_for(prior, raw))) == previous_hash:
                        self.read(pointer_for(prior, raw))
                        matches.append(sequence)
                require(len(matches) == 1, message)
                expected = matches[0]
            else:
                expected = None
            if baseline_sequence is not _INFER:
                require(baseline_sequence == expected, message)
            baseline_sequence = expected
        if baseline_sequence is None and earlier:
            # A failed initial typed attempt may leave signed orphans before
            # its retry, but legacy or previously-published history is never
            # evidence for a fresh, baseline-free schema2 initial release.
            for sequence in earlier:
                raw = safe_file(self.public, manifest_name(sequence)).read_bytes()
                prior = validate_manifest(decode_json(raw))
                require(prior["schemaVersion"] == 2 and not safe_file(
                    self.state, f"reports/{sequence:06d}.json").exists(), message)
        return baseline_sequence

    def _quality_context(self, manifest, baseline_sequence=_INFER):
        # Only an earlier signed release can grandfather historical broken links.
        # Never compare a pending/restored release against itself. The baseline
        # actually used by prepare() is persisted with the pending release: an
        # abandoned sequence (manifest written, pending.json never written) is
        # published nowhere and must not become the reference.
        sequences = self.sequences()
        validation = self.pending()
        if validation is None or validation["pointer"]["sequence"] != manifest["sequence"]:
            report_path = safe_file(self.state, f"reports/{manifest['sequence']:06d}.json")
            validation = {"summary": decode_json(report_path.read_bytes())} if report_path.exists() else {}
        summary = validation.get("summary", {})
        manifest_digest = sha256(safe_file(self.public, manifest_name(manifest["sequence"])).read_bytes())
        bound = summary.get("manifestSha256") == manifest_digest
        if baseline_sequence is _INFER and bound:
            baseline_sequence = summary.get("qualityBaselineSequence", _INFER)
        earlier = [seq for seq in sequences if seq < manifest["sequence"]]
        baseline_sequence = self._bound_quality_baseline(manifest, baseline_sequence, earlier, self.pending())
        if baseline_sequence is _INFER:
            # Old signed history may predate bound reports. Preserve its same
            # nearest-earlier inference when GC relocates signed evidence;
            # absence of a public predecessor must not silently mean "initial".
            evidence = safe_file(self.state, "quality-baselines")
            if evidence.exists():
                for path in evidence.iterdir():
                    require(re.fullmatch(r"[0-9]{6,}\.(json|sig)", path.name) and not path.is_symlink(),
                            "unexpected quality baseline evidence")
                    if int(path.stem) < manifest["sequence"]:
                        earlier.append(int(path.stem))
            baseline_sequence = max(earlier) if earlier else None
        elif baseline_sequence is None:
            baseline_sequence = None
        else:
            require(type(baseline_sequence) is int and 0 < baseline_sequence < manifest["sequence"],
                    "quality baseline is not an earlier release")
        return baseline_sequence, summary, bound

    def _release_quality(self, manifest, baseline_sequence=_INFER):
        baseline_sequence, summary, bound = self._quality_context(manifest, baseline_sequence)
        baseline = self._signed_snapshot(baseline_sequence) if baseline_sequence is not None else None
        links_baseline = baseline
        if manifest.get("rollbackOf") is not None:
            # A rollback republishes already-signed bytes, so its restored links
            # must be grandfathered against the snapshot it restores, exactly as
            # prepare() did; revalidating against the newer sequence would call a
            # link the newer release fixed a brand-new violation.
            links_baseline = self._rollback_baseline(manifest["rollbackOf"],
                                                     self._snapshot(manifest))
            target = validate_manifest(decode_json(safe_file(
                self.public, manifest_name(manifest["rollbackOf"])).read_bytes()))
            require(manifest["schemaVersion"] == target["schemaVersion"], "restore schema mismatch")
            require(manifest["minAppVersion"] == target["minAppVersion"], "restore client requirement mismatch")
        if manifest["schemaVersion"] == 2:
            self._require_structured(self._snapshot(manifest))
        else:
            require(manifest.get("rollbackOf") is not None,
                    "public quality gate rejected candidate: checks=identity; legacy release requires exact signed restore")
        quality = validate_snapshot(self._snapshot(manifest), baseline,
                                    identity_baseline=manifest.get("rollbackOf") is None,
                                    references_baseline=links_baseline)
        previous_manifest = self._quality_baseline_manifest(baseline_sequence)[0] \
            if baseline_sequence is not None else None
        check_deletions(previous_manifest, manifest["files"], 0.25,
                        summary.get("deletionApproval") if bound else None)
        return quality

    def _rollback_baseline(self, sequence, snapshot):
        """Re-admit only the exact signed bytes of the release being restored.

        A rollback republishes content that was already signed and public, so its
        own historical broken links cannot block disaster recovery. The snapshot
        must match the signed manifest byte-for-byte, otherwise the marker would
        whitelist arbitrary new content.
        """
        path = safe_file(self.public, manifest_name(sequence))
        raw = path.read_bytes()
        manifest = validate_manifest(decode_json(raw))
        self.read(pointer_for(manifest, raw))
        if manifest["schemaVersion"] == 2:
            pending = self.pending()
            baseline = _pending_baseline(pending) if pending and pending["pointer"]["sequence"] == sequence else _INFER
            self._release_quality(manifest, baseline)
        signed = self._snapshot(manifest)
        require(snapshot.commits == manifest["sourceCommits"], "rollback provenance does not match signed release")
        require(set(signed.files) == set(snapshot.files), "rollback snapshot does not match signed release")
        for name, data in signed.files.items():
            require(len(data) == len(snapshot.files[name]) and sha256(data) == sha256(snapshot.files[name]),
                    "rollback snapshot does not match signed release")
        return signed

    def prepare(self, snapshot, signer, *, public_scope_approved=False, min_app_version="2.0.2",
                max_deletion_fraction=0.25, approval=None, now=None, rollback_of=None, failpoint=None):
        require(public_scope_approved, "public data scope must be explicitly approved before publishing")
        require(signer is not None, "production signing key required")
        require(type(max_deletion_fraction) in (float, int) and 0 <= max_deletion_fraction <= 0.25,
                "deletion threshold cannot exceed 0.25")
        if snapshot is not None:
            check_payload("snapshot-metadata.json", canonical_json({
                "sourceCommits": snapshot.commits, "warnings": snapshot.warnings}))
        self.public.mkdir(parents=True, exist_ok=True)
        with ReleaseLock(self.root):
            pending = self.pending()
            if pending:
                manifest = self.read(pending["pointer"])
                quality = self._release_quality(manifest, _pending_baseline(pending))
                return dict(pending["summary"], qualityGate=quality, status="pending", resumed=True)
            previous = self.read()
            # Even programmatic ingestion is validated. Rollback data also passes present-day secret checks.
            for path, raw in snapshot.files.items():
                validate_markdown(path, raw)
            links_baseline = self._snapshot(previous) if previous else None
            restored_manifest = None
            if rollback_of is not None:
                links_baseline = self._rollback_baseline(rollback_of, snapshot)
                restored_manifest = validate_manifest(decode_json(
                    safe_file(self.public, manifest_name(rollback_of)).read_bytes()))
            quality = validate_snapshot(snapshot, self._snapshot(previous) if previous else None,
                                        identity_baseline=rollback_of is None,
                                        references_baseline=links_baseline)
            if rollback_of is None:
                self._require_structured(snapshot)
            records = inventory(snapshot.files)
            check_deletions(previous, records, max_deletion_fraction, approval)
            diff = changes(previous, records)
            summary = {"added": len(diff["added"]), "modified": len(diff["modified"]),
                       "deleted": len(diff["deleted"]), "unchanged": len(diff["unchanged"]),
                       "newObjects": 0, "uploadedBytes": 0, "sourceCommits": snapshot.commits,
                       "mappingSha256": mapping_digest(records), "warnings": snapshot.warnings,
                       "qualityGate": quality,
                       "changes": diff}
            if previous and not (summary["added"] or summary["modified"] or summary["deleted"]):
                return dict(summary, status="unchanged", sequence=previous["sequence"])
            sequence = max(self.sequences(), default=0) + 1
            moment = now or utc_now()
            manifest = make_manifest(snapshot, sequence, moment.strftime("%Y.%m.%d") + f".{sequence:03d}",
                                     moment.strftime("%Y-%m-%dT%H:%M:%SZ"), signer.key_id,
                                     min_app_version, rollback_of)
            if restored_manifest is not None:
                manifest["schemaVersion"] = restored_manifest["schemaVersion"]
                manifest["minAppVersion"] = restored_manifest["minAppVersion"]
            else:
                manifest["schemaVersion"] = 2
                from .client import version_tuple
                if version_tuple(min_app_version) < (2, 0, 2):
                    manifest["minAppVersion"] = "2.0.2"
            validate_manifest(manifest)
            raw_manifest = canonical_json(manifest)
            summary.update(manifestSha256=sha256(raw_manifest), deletionApproval=approval,
                           qualityBaselineSequence=previous["sequence"] if previous else None)
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
            pending["qualityBaselineSequence"] = previous["sequence"] if previous else None
            atomic_write(self.state / "pending.json", canonical_json(pending))
            if failpoint:
                failpoint("pending")
            return summary

    def promote(self, remote_verifier=None, failpoint=None, keep_pending=False):
        with ReleaseLock(self.root):
            pending = self.pending()
            require(pending is not None, "no pending release")
            pointer = pending["pointer"]
            self._release_quality(self.read(pointer), _pending_baseline(pending))
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
            self._release_quality(self.read(pending["pointer"]), _pending_baseline(pending))
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
        pending = self.pending()
        baseline = _pending_baseline(pending) if pending and \
            pending["pointer"]["sequence"] == sequence else _INFER
        self._release_quality(self.read(pointer), baseline)
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
            # A retained release is revalidated against the signed release it
            # inherited links from. Removing that baseline turns grandfathered
            # links into new violations, so keep one level of that evidence.
            for sequence in sorted(protected):
                if sequence not in manifests:
                    continue
                earlier = [seq for seq in manifests if seq < sequence]
                if earlier:
                    protected.add(max(earlier))
            cutoff = (now or utc_now()) - timedelta(days=grace_days)
            for sequence, manifest in manifests.items():
                published = datetime.strptime(manifest["publishedAt"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                if published > cutoff:
                    protected.add(sequence)
            # A rollback manifest is revalidated against the release it restores,
            # including rollbacks retained only by grace. Keep those exact
            # restores (and their own rollback sources, transitively).
            evidence = list(protected)
            while evidence:
                manifest = manifests.get(evidence.pop())
                if manifest is None:
                    continue
                target = manifest.get("rollbackOf")
                if target is not None and target not in protected:
                    protected.add(target)
                    evidence.append(target)
            # Private, one-hop signed evidence must not become outgoing history:
            # doing so would make each baseline demand its own baseline forever.
            # Resolve the same bound context used by preflight, never merely the
            # nearest remaining sequence (which can be an abandoned attempt).
            baselines = {}
            for sequence in sorted(protected):
                manifest = manifests[sequence]
                if manifest["schemaVersion"] != 2 and manifest.get("rollbackOf") is None:
                    continue
                self._release_quality(manifest)
                baseline, _, _ = self._quality_context(manifest)
                if baseline is not None and baseline not in protected:
                    baselines[baseline] = self._quality_baseline_manifest(baseline)
            referenced = {r["sha256"] for seq in protected for r in manifests[seq]["files"]}
            referenced.update(r["sha256"] for manifest, _, _ in baselines.values()
                              for r in manifest["files"])
            evidence_removals = []
            evidence_root = safe_file(self.state, "quality-baselines")
            if evidence_root.exists():
                for path in evidence_root.iterdir():
                    require(re.fullmatch(r"[0-9]{6,}\.(json|sig)", path.name) and not path.is_symlink(),
                            "unexpected quality baseline evidence")
                    if int(path.stem) not in baselines:
                        evidence_removals.append("quality-baselines/" + path.name)
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
                # Durable evidence precedes every destructive operation. A crash
                # may leave extra evidence, but never intentionally deletes the
                # only signed baseline before its replacement is durable.
                for sequence, (_, raw, signature) in baselines.items():
                    immutable_write(self.state, f"quality-baselines/{sequence:06d}.json", raw)
                    immutable_write(self.state, f"quality-baselines/{sequence:06d}.sig", signature)
                for relative in removals:
                    safe_file(self.public, relative).unlink()
                for relative in evidence_removals:
                    safe_file(self.state, relative).unlink()
            return {"protectedSequences": sorted(protected), "remove": sorted(removals), "applied": apply,
                    "baselineEvidenceSequences": sorted(baselines),
                    "removeBaselineEvidence": sorted(evidence_removals)}
