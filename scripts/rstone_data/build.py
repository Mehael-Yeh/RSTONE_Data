"""Deterministic inventories, full-state differences, and scoped deletion approval."""

from .protocol import ROOTS, canonical_json, require, sha256, validate_manifest


def inventory(files):
    return [{"path": path, "sha256": sha256(raw), "size": len(raw),
             "contentType": "text/markdown; charset=utf-8"}
            for path, raw in sorted(files.items(), key=lambda item: item[0].encode("utf-8"))]


def mapping_digest(records):
    return sha256(canonical_json(records))


def changes(previous, records):
    old = {r["path"]: r["sha256"] for r in (previous or {}).get("files", [])}
    new = {r["path"]: r["sha256"] for r in records}
    return {
        "added": sorted(new.keys() - old.keys()),
        "modified": sorted(p for p in new.keys() & old.keys() if new[p] != old[p]),
        "deleted": sorted(old.keys() - new.keys()),
        "unchanged": sorted(p for p in new.keys() & old.keys() if new[p] == old[p]),
    }


def deletion_approval(previous, records):
    return f"{previous['sequence']}:{mapping_digest(records)}"


def check_deletions(previous, records, maximum=0.25, approval=None):
    require(isinstance(maximum, (int, float)) and 0 <= maximum < 1, "invalid deletion threshold")
    require(records, "empty database cannot be approved")
    if not previous:
        for root in ROOTS:
            require(any(r["path"].startswith(root + "/") for r in records), f"initial data namespace empty: {root}")
        return
    old = {r["path"]: r["sha256"] for r in previous["files"]}
    new = {r["path"]: r["sha256"] for r in records}
    retained_hashes = set(new.values())
    excessive = False
    for root in (None, *ROOTS):
        old_group = {p for p in old if root is None or p.startswith(root + "/")}
        new_group = {p for p in new if root is None or p.startswith(root + "/")}
        # Moves preserve content; a true count collapse or lost content still needs approval.
        lost = {p for p in old_group - new_group if old[p] not in retained_hashes}
        if old_group and max(len(lost), len(old_group) - len(new_group)) / len(old_group) > maximum:
            excessive = True
    if excessive:
        expected = deletion_approval(previous, records)
        require(approval == expected, "abnormal bulk deletion; exact approval required: " + expected)


def make_manifest(snapshot, sequence, data_version, published_at, key_id, min_app_version="1.0.0",
                  rollback_of=None):
    manifest = {
        "protocolVersion": 1, "schemaVersion": 1, "sequence": sequence,
        "dataVersion": data_version, "publishedAt": published_at, "keyId": key_id,
        "minAppVersion": min_app_version, "sourceCommits": snapshot.commits,
        "files": inventory(snapshot.files),
    }
    if rollback_of is not None:
        manifest["rollbackOf"] = rollback_of
    return validate_manifest(manifest)
