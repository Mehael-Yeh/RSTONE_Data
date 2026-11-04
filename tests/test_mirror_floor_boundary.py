"""Signed synthetic histories, actual loopback mirror, no admission mocks."""
import pytest
from rstone_data.build import make_manifest
from rstone_data.mirrors import deploy_mirror
from rstone_data.protocol import DataError, canonical_json, sha256
from rstone_data.release import pointer_for
from rstone_data.sources import structured_marker
from rstone_data.storage import atomic_write
from rstone_data.transport import fetch_release
from test_recipe_v2_release import snap2
from test_structured_publication import PATH, snapshot


def tree(root):
    return {str(p.relative_to(root)): ("dir" if p.is_dir() else p.read_bytes())
            for p in root.rglob("*")} if root.exists() else {}


def install(store, signer, snap, sequence=1, outer=1, minimum="2.0.5"):
    # Inventory a valid snapshot, then bind exact supplied document bytes.
    manifest = make_manifest(snap2(), sequence, f"2026.01.01.{sequence:03d}",
                             "2026-01-01T00:00:00Z", signer.key_id, "2.0.5")
    manifest.update(schemaVersion=outer, minAppVersion=minimum)
    manifest["files"] = [{"path": p, "sha256": sha256(b), "size": len(b)}
                         for p, b in sorted(snap.files.items())]
    raw = canonical_json(manifest)
    pointer = pointer_for(manifest, raw)
    for data in snap.files.values():
        atomic_write(store.public / "objects" / sha256(data), data)
    atomic_write(store.public / pointer["manifestPath"], raw)
    atomic_write(store.public / pointer["signaturePath"], signer.sign(raw))
    atomic_write(store.public / "latest.json", canonical_json(pointer))
    return manifest


@pytest.mark.parametrize("damage", ["none", "display", "unknown", "escaped"])
@pytest.mark.parametrize("historical", [False, True])
def test_outer1_structured_high_floor_preflight(store, signing, serve, tmp_path, monkeypatch, damage, historical):
    snap = snap2()
    if damage == "display":
        snap.files[PATH] += b"\ncorrupt display\n"
    elif damage == "unknown":
        snap.files[PATH] = snap.files[PATH].replace(b"rstone-recipe/2", b"rstone-recipe/9")
    elif damage == "escaped":
        snap.files[PATH] = snap.files[PATH].replace(b"rstone-recipe/2", b"rstone-recipe\\u002f2")
    assert structured_marker(snap.files[PATH])
    install(store, signing[0], snap, sequence=2 if historical else 1)
    if historical:
        old = snapshot(); old.files[PATH] = b"| resin | 40 |\n"
        install(store, signing[0], old, sequence=1, minimum="1.0.0")
        install(store, signing[0], old, sequence=3, minimum="1.0.0")
    target = tmp_path / "mirror"
    target.mkdir(); (target / "sentinel").write_bytes(b"untouched")
    before, source_before = tree(target), tree(store.root)
    calls = []
    signer_type = type(signing[0])
    original_sign = signer_type.sign
    def observed_sign(self, raw):
        calls.append(raw)
        return original_sign(self, raw)
    monkeypatch.setattr(signer_type, "sign", observed_sign)
    with serve(target) as url:
        with pytest.raises(DataError):
            deploy_mirror(store.root, target, url, signing[1], allow_local_http=True)
    assert tree(target) == before
    assert tree(store.root) == source_before
    assert calls == []


@pytest.mark.parametrize("kind", ["legacy", "typed1"])
def test_read_and_mirror_genuine_history(store, signing, serve, tmp_path, kind):
    snap = snapshot()
    if kind == "legacy":
        snap.files[PATH] = b"| resin | 40 |\n"
    manifest = install(store, signing[0], snap, minimum="1.0.0")
    assert store.read() == manifest
    if kind == "typed1":
        return  # Reading historical /1 does not apply outgoing quality policy.
    target = tmp_path / "mirror"
    with serve(target) as url:
        assert deploy_mirror(store.root, target, url, signing[1], allow_local_http=True)["status"] == "mirrored"
        fetch_release(url, signing[1], allow_local_http=True, require_cors=True)
    for path in store.public.rglob("*"):
        if path.is_file():
            assert (target / path.relative_to(store.public)).read_bytes() == path.read_bytes()


@pytest.mark.parametrize("outer", [1, 2])
@pytest.mark.parametrize("minimum", ["1.0.0", "2.0.2", "2.0.4"])
def test_low_floor_default_read_not_metadata_only(store, signing, outer, minimum):
    manifest = install(store, signing[0], snap2(), outer=outer, minimum=minimum)
    assert store.read(verify_objects=False) == manifest
    before = tree(store.root)
    with pytest.raises(DataError, match="2.0.5"):
        store.read()
    assert tree(store.root) == before
