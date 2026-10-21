"""Executable client reference: complete generations, verified shared object cache, atomic activation."""

import re
import uuid
import zipfile
from pathlib import Path

from .protocol import (
    SEMVER,
    DataError,
    canonical_json,
    decode_json,
    require,
    sha256,
    validate_manifest,
)
from .release import pointer_for
from .signing import verify_release
from .storage import ReleaseLock, atomic_write, safe_file
from .transport import MAX_MANIFEST, fetch_release, get_bytes, validate_base


def version_tuple(version):
    require(isinstance(version, str) and SEMVER.fullmatch(version), "invalid client app version")
    return tuple(int(part) for part in version.split("."))


def _active(cache, keys):
    path = safe_file(cache, "active.json")
    if not path.exists():
        return None
    control = decode_json(path.read_bytes())
    generation = control.get("generation")
    require(isinstance(generation, str) and re.fullmatch(r"g-[0-9]+-[0-9a-f]{64}-[0-9a-f]{32}", generation),
            "invalid active generation")
    directory = safe_file(cache, "generations/" + generation)
    latest = decode_json(safe_file(directory, "latest.json").read_bytes())
    manifest = verify_release(latest, safe_file(directory, "manifest.json").read_bytes(),
                              safe_file(directory, "manifest.sig").read_bytes(), keys)
    require(control.get("highestSequence") == manifest["sequence"] and
            control.get("manifestSha256") == latest["manifestSha256"], "local high-water mark mismatch")
    return control, latest, manifest, directory


def _complete_tree(directory, manifest):
    try:
        for record in manifest["files"]:
            raw = safe_file(directory, "files/" + record["path"]).read_bytes()
            if len(raw) != record["size"] or sha256(raw) != record["sha256"]:
                return False
        return True
    except OSError:
        return False


def active_directory(cache, keys):
    active = _active(cache, keys)
    require(active is not None and _complete_tree(active[3], active[2]), "no complete official generation")
    return active[3] / "files"


def _accept(cache, keys, latest, manifest, raw, signature, provider, app_version, failpoint=None):
    require(version_tuple(app_version) >= version_tuple(manifest["minAppVersion"]), "app upgrade required")
    previous = _active(cache, keys)
    if previous:
        require(latest["sequence"] >= previous[0]["highestSequence"], "stale mirror sequence rejected")
        if latest["sequence"] == previous[0]["highestSequence"]:
            require(latest["manifestSha256"] == previous[0]["manifestSha256"], "same-sequence manifest conflict")
            if _complete_tree(previous[3], previous[2]):
                return {"status": "unchanged", "sequence": manifest["sequence"], "downloadedObjects": 0, "downloadedBytes": 0}
    downloaded = 0
    downloaded_bytes = 0
    checked = set()
    for record in manifest["files"]:
        digest = record["sha256"]
        if digest in checked:
            continue
        path = safe_file(cache, "objects/" + digest)
        cached = path.read_bytes() if path.exists() else None
        if cached is None or len(cached) != record["size"] or sha256(cached) != digest:
            content = provider(record)
            require(len(content) == record["size"] and sha256(content) == digest, "downloaded object integrity failure")
            atomic_write(path, content)
            downloaded += 1
            downloaded_bytes += len(content)
        checked.add(digest)
    if failpoint:
        failpoint("objects")
    generation = f"g-{manifest['sequence']}-{latest['manifestSha256']}-{uuid.uuid4().hex}"
    directory = safe_file(cache, "generations/" + generation)
    directory.mkdir(parents=True)
    for record in manifest["files"]:
        content = safe_file(cache, "objects/" + record["sha256"]).read_bytes()
        require(len(content) == record["size"] and sha256(content) == record["sha256"], "cache changed during activation")
        atomic_write(safe_file(directory, "files/" + record["path"]), content)
    atomic_write(directory / "manifest.json", raw)
    atomic_write(directory / "manifest.sig", signature)
    atomic_write(directory / "latest.json", canonical_json(latest))
    if failpoint:
        failpoint("generation")
    require(_complete_tree(directory, manifest), "incomplete generation")
    control = {"generation": generation, "highestSequence": manifest["sequence"],
               "manifestSha256": latest["manifestSha256"]}
    if failpoint:
        failpoint("before-activate")
    # One atomic record commits both active data and the accepted high-water mark.
    atomic_write(safe_file(cache, "active.json"), canonical_json(control))
    if failpoint:
        failpoint("after-activate")
    return {"status": "synchronized", "sequence": manifest["sequence"], "fileCount": len(manifest["files"]),
            "downloadedObjects": downloaded, "downloadedBytes": downloaded_bytes}


def synchronize(base_urls, cache, keys, app_version="1.0.0", allow_local_http=False, failpoint=None):
    cache = Path(cache)
    require(base_urls, "at least one mirror required")
    bases = list(dict.fromkeys(validate_base(base, allow_local_http) for base in base_urls))
    with ReleaseLock(cache):
        previous = _active(cache, keys)
        candidates = []
        failures = []
        for base in bases:
            try:
                candidate = fetch_release(base, keys, allow_local_http=allow_local_http, verify_objects=False)
                candidates.append(candidate)
            except DataError as error:
                failures.append(str(error))
        require(candidates, "no mirror supplied a verifiable manifest: " + "; ".join(failures))
        hashes = {}
        for latest, *_ in candidates:
            sequence = latest["sequence"]
            require(sequence not in hashes or hashes[sequence] == latest["manifestSha256"], "mirror same-sequence conflict")
            hashes[sequence] = latest["manifestSha256"]
        compatible = [c for c in candidates if version_tuple(app_version) >= version_tuple(c[1]["minAppVersion"])]
        require(compatible, "app upgrade required for all verifiable mirror versions")
        latest, manifest, raw, signature = max(compatible, key=lambda c: c[0]["sequence"])
        if previous:
            require(latest["sequence"] >= previous[0]["highestSequence"], "all compatible mirrors are stale")

        def provider(record):
            for base in bases:
                try:
                    content = get_bytes(base, "objects/" + record["sha256"], record["size"],
                                        allow_local_http=allow_local_http, fresh=True)
                    if len(content) == record["size"] and sha256(content) == record["sha256"]:
                        return content
                except DataError:
                    continue
            raise DataError("target object unavailable from all mirrors; previous generation retained")

        return _accept(cache, keys, latest, manifest, raw, signature, provider, app_version, failpoint)


def import_package(package, cache, keys, app_version="1.0.0", failpoint=None):
    with zipfile.ZipFile(package) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        require(len(set(names)) == len(names), "duplicate ZIP entries")
        require(all((entry.external_attr >> 16) & 0o170000 != 0o120000 and not entry.flag_bits & 1
                    for entry in entries), "encrypted or symlink ZIP entry")
        require(sum(e.file_size for e in entries) <= 544 * 1024 * 1024, "ZIP exceeds import size limit")

        def read(name, limit):
            require(name in names, "required ZIP entry missing")
            with archive.open(name) as stream:
                content = stream.read(limit + 1)
            require(len(content) <= limit, "ZIP entry exceeds size limit")
            return content

        raw = read("metadata/manifest.json", MAX_MANIFEST)
        signature = read("metadata/manifest.sig", 512)
        manifest = validate_manifest(decode_json(raw))
        latest = pointer_for(manifest, raw)
        verify_release(latest, raw, signature, keys)
        expected = {"metadata/manifest.json", "metadata/manifest.sig"} | {"files/" + r["path"] for r in manifest["files"]}
        require(set(names) == expected, "extra or missing ZIP entry")
        content_by_hash = {}
        for record in manifest["files"]:
            content = read("files/" + record["path"], record["size"])
            require(len(content) == record["size"] and sha256(content) == record["sha256"], "ZIP object integrity failure")
            content_by_hash[record["sha256"]] = content
    with ReleaseLock(cache):
        return _accept(cache, keys, latest, manifest, raw, signature,
                       lambda record: content_by_hash[record["sha256"]], app_version, failpoint)
