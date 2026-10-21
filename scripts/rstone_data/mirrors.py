"""Filesystem mirror deployment for mounted static sites; preserve canonical signed bytes."""

from .protocol import decode_json, manifest_name, require
from .release import Store, pointer_for
from .storage import ReleaseLock, atomic_write, immutable_write
from .transport import fetch_release


def deploy_mirror(source, target, base_url, keys, allow_local_http=False):
    store = Store(source, keys)
    require(store.pending() is None, "canonical deployment must complete before mirroring")
    current = store.read()
    require(current is not None, "canonical store has no release")
    latest = store.latest_bytes()
    pointer = decode_json(latest)
    new_objects = 0
    new_bytes = 0
    # target is already the public directory, not a publisher store.
    with ReleaseLock(target):
        from pathlib import Path
        old_latest = Path(target) / "latest.json"
        if old_latest.exists():
            old = decode_json(old_latest.read_bytes())
            require(old["sequence"] <= pointer["sequence"], "mirror cannot decrease sequence")
            require(old["sequence"] != pointer["sequence"] or old == pointer, "mirror sequence conflict")
        for sequence in store.sequences():
            if sequence > current["sequence"]:
                continue
            path = manifest_name(sequence)
            raw = (store.public / path).read_bytes()
            manifest = decode_json(raw)
            historical_pointer = pointer_for(manifest, raw)
            store.read(historical_pointer)
            for record in manifest["files"]:
                content = store.object(record)
                if immutable_write(target, "objects/" + record["sha256"], content):
                    new_objects += 1
                    new_bytes += len(content)
            immutable_write(target, path, raw)
            immutable_write(target, historical_pointer["signaturePath"],
                            (store.public / historical_pointer["signaturePath"]).read_bytes())
        # No manifest regeneration, key changes, or local-only completeness assumptions.
        fetch_release(base_url, keys, pointer, allow_local_http=allow_local_http, require_cors=True)
        from .storage import safe_file
        atomic_write(safe_file(target, "latest.json"), latest)
    return {"status": "mirrored", "sequence": current["sequence"], "newObjects": new_objects,
            "uploadedBytes": new_bytes}
