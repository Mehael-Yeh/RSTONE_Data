"""Reproducible simulated end-to-end release/sync on localhost. No upstream or APK mutation."""

import argparse
import base64
import copy
import functools
import http.server
import json
import threading
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from generate_examples import snapshots
from rstone_data.client import active_directory, synchronize
from rstone_data.protocol import canonical_json, require
from rstone_data.release import Store
from rstone_data.signing import Signer
from rstone_data.storage import atomic_write
from rstone_data.transport import fetch_release


def demonstrate(output):
    require(not output.exists(), "demo output already exists; choose a fresh directory")
    private = Ed25519PrivateKey.generate()
    keys = {"demo-ephemeral": base64.b64encode(private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()}
    signer = Signer("demo-ephemeral", private)
    store = Store(output / "store", keys)
    store.public.mkdir(parents=True)
    atomic_write(output / "trusted_keys.json", canonical_json({"keys": keys}))

    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def end_headers(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            super().end_headers()

    service = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(store.public)))
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{service.server_port}"
    releases = []
    try:
        def publish(snapshot):
            result = store.prepare(snapshot, signer, public_scope_approved=True)
            if result["status"] != "unchanged":
                store.promote(lambda target: fetch_release(base, keys, target, allow_local_http=True, require_cors=True),
                              keep_pending=True)
                def verify_live(pointer):
                    current, *_ = fetch_release(base, keys, allow_local_http=True, require_cors=True)
                    require(pointer == current, "demo live pointer mismatch")
                result = store.complete(verify_live)
            releases.append(result)
            return result

        first, second = snapshots()
        publish(first)
        initial = synchronize([base], output / "client", keys, allow_local_http=True)
        publish(second)
        third = copy.deepcopy(second)
        # A same-byte duplicate needs no new object.
        third.files["产品列表/RS-COPY.md"] = first.files["产品列表/RS-DEMO1.md"]
        del third.files["产品列表/RD-DEMO2.md"]
        # Two-product demo is a 50% namespace reduction, so demonstrate exact bounded approval.
        from rstone_data.build import deletion_approval, inventory
        approval = deletion_approval(store.read(), inventory(third.files))
        store.prepare(third, signer, public_scope_approved=True, approval=approval)
        third_result = store.promote(lambda target: fetch_release(base, keys, target, allow_local_http=True))
        releases.append(third_result)
        fourth = copy.deepcopy(third)
        fourth.files["产品应用/PC/示例方案.md"] = fourth.files.pop("产品应用/示例方案.md")
        publish(fourth)
        skipped = synchronize([base], output / "client", keys, allow_local_http=True)
        require(skipped["sequence"] == 4 and skipped["downloadedObjects"] == 1, "demo delta mismatch")
        actual = active_directory(output / "client", keys)
        require({p.relative_to(actual).as_posix(): p.read_bytes() for p in actual.rglob("*.md")} == fourth.files,
                "demo full-state mismatch")
        store.rollback(1, signer, public_scope_approved=True)
        rollback = store.promote(lambda target: fetch_release(base, keys, target, allow_local_http=True))
        releases.append(rollback)
        restored = synchronize([base], output / "client", keys, allow_local_http=True)
        require(restored["sequence"] == 5 and restored["downloadedObjects"] == 0, "demo rollback mismatch")
        no_change = publish(first)
        require(no_change["status"] == "unchanged", "demo no-op mismatch")
        return {"mode": "simulated-signed-local-http", "officialPublication": False, "apkBuilt": False,
                "sourceCommitsAreSimulated": True, "initialSync": initial,
                "skipVersionsSync": skipped, "rollbackRelease": rollback, "rollbackSync": restored,
                "unchangedStatus": no_change["status"], "retainedSequences": store.sequences(),
                "releasedObjectCounts": [r.get("newObjects") for r in releases if r["status"] != "unchanged"]}
    finally:
        service.shutdown()
        service.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("work/demo"))
    parser.add_argument("--report", type=Path)
    arguments = parser.parse_args()
    report = demonstrate(arguments.output.resolve())
    if arguments.report:
        atomic_write(arguments.report, canonical_json(report))
    print(json.dumps(report, ensure_ascii=False, indent=2))
