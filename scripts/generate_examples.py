"""Generate/check public interoperability fixtures. Private test key is ephemeral and never saved."""

import argparse
import base64
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from rstone_data.build import inventory
from rstone_data.protocol import canonical_json, decode_json, require
from rstone_data.release import Store, pointer_for
from rstone_data.signing import Signer
from rstone_data.sources import Snapshot
from rstone_data.storage import atomic_write

ROOT = Path(__file__).resolve().parents[1]


def snapshots():
    files = {p.relative_to(ROOT / "examples/sources/Rstone").as_posix(): p.read_bytes()
             for p in (ROOT / "examples/sources/Rstone").rglob("*.md")}
    files.update({"产品TDS/" + p.name: p.read_bytes() for p in (ROOT / "examples/sources/RstoneTDS").glob("*.TDS.md")})
    first = Snapshot(files, {"Mehael-Yeh/Rstone": "1" * 40, "Mehael-Yeh/RstoneTDS": "2" * 40})
    second_files = dict(files)
    second_files["产品列表/RS-DEMO1.md"] += "\n模拟第二版说明。\n".encode()
    second = Snapshot(second_files, {"Mehael-Yeh/Rstone": "3" * 40, "Mehael-Yeh/RstoneTDS": "2" * 40})
    return first, second


def check():
    keys = decode_json((ROOT / "examples/trusted_keys.json").read_bytes())["keys"]
    # The fixture directory itself is a static public tree, so point public to it explicitly.
    store = Store(ROOT / "work/fixture-check", keys)
    store.public = ROOT / "examples/distribution"
    for sequence, snapshot in enumerate(snapshots(), 1):
        raw = (store.public / f"manifests/{sequence:06d}.json").read_bytes()
        manifest = decode_json(raw)
        verified = store.read(pointer_for(manifest, raw))
        require(verified["files"] == inventory(snapshot.files), "fixture content drift")
        require(verified["sourceCommits"] == snapshot.commits, "fixture commit drift")
    require(store.read()["sequence"] == 2, "fixture latest drift")
    require(len(list((store.public / "objects").iterdir())) == 6, "fixture object deduplication drift")
    print("Verified two signed full manifests, six simulated objects, and latest binding")


def generate():
    target = ROOT / "examples/distribution"
    require(not target.exists(), "examples already exist; use --check (test private key was discarded)")
    private = Ed25519PrivateKey.generate()
    public = base64.b64encode(private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    keys = {"example-only": public}
    signer = Signer("example-only", private)
    with tempfile.TemporaryDirectory(prefix="rstone-example-") as directory:
        store = Store(directory, keys)
        for sequence, snapshot in enumerate(snapshots(), 1):
            store.prepare(snapshot, signer, public_scope_approved=True,
                          now=datetime(2026, 10, 21, 0, sequence, tzinfo=timezone.utc))
            store.promote()
            store.package(sequence)
        shutil.copytree(store.public, target)
    atomic_write(ROOT / "examples/trusted_keys.json", canonical_json({"keys": keys}))
    check()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    check() if arguments.check else generate()
