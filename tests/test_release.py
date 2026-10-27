import base64
import copy
from datetime import datetime, timedelta, timezone

import pytest
from conftest import commit_all
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from rstone_data.build import inventory
from rstone_data.protocol import DataError, decode_json, sha256
from rstone_data.signing import Signer, verify_release
from rstone_data.sources import Snapshot, collect


def publish(store, snapshot, signer, **kwargs):
    result = store.prepare(snapshot, signer, public_scope_approved=True, max_deletion_fraction=0.99, **kwargs)
    if result["status"] != "unchanged":
        result = store.promote()
    return result


def changed(snapshot, path, value=None):
    new = copy.deepcopy(snapshot)
    if value is None:
        del new.files[path]
    else:
        new.files[path] = value
    return new


def test_first_publication(store, sources, signing):
    snapshot = collect(*sources)
    summary = publish(store, snapshot, signing[0])
    assert summary["sequence"] == 1
    assert (summary["added"], summary["modified"], summary["deleted"]) == (5, 0, 0)
    assert summary["newObjects"] == 5
    assert summary["uploadedBytes"] == sum(map(len, snapshot.files.values()))
    assert store.read()["files"] == inventory(snapshot.files)


def test_add_modify_delete_and_full_latest(store, sources, signing):
    first = collect(*sources)
    publish(store, first, signing[0])
    second = changed(first, "产品列表/RS300.md", b"---\ntags: [\"\\u6c34\\u6027\", UV]\n\"\\u56fa\\u542b\": 77\n---\nnew\n")
    added = publish(store, second, signing[0])
    assert (added["added"], added["newObjects"]) == (1, 1)
    third = changed(second, "产品列表/RS300.md", b"---\ntags: [\"\\u6c34\\u6027\", UV]\n\"\\u56fa\\u542b\": 77\n---\nmodified\n")
    modified = publish(store, third, signing[0])
    assert (modified["modified"], modified["newObjects"]) == (1, 1)
    fourth = changed(third, "产品列表/RS300.md")
    deleted = publish(store, fourth, signing[0])
    assert (deleted["deleted"], deleted["newObjects"]) == (1, 0)
    assert store.read()["sequence"] == 4
    assert store.read()["files"] == inventory(first.files)
    assert len(list((store.public / "objects").iterdir())) == 7


@pytest.mark.parametrize("old,new", [("产品列表/RS100.md", "产品列表/RS101.md"),
                                     ("产品应用/方案.md", "产品应用/PC/方案.md")])
def test_rename_move_reuses_objects(store, sources, signing, old, new):
    first = collect(*sources)
    # Rename an unreferenced product; breaking a real attachment is now rejected.
    first.files["产品应用/方案.md"] = first.files["产品应用/方案.md"].replace(b"RS100", b"RD200")
    publish(store, first, signing[0])
    second = changed(first, new, first.files[old])
    second = changed(second, old)
    result = publish(store, second, signing[0])
    assert (result["added"], result["deleted"], result["newObjects"], result["uploadedBytes"]) == (1, 1, 0, 0)
    assert {r["path"] for r in store.read()["files"]} == set(second.files)


def test_same_content_multiple_paths_and_noop_new_commit(store, sources, signing):
    first = collect(*sources)
    first.files["产品列表/RS101.md"] = first.files["产品列表/RS100.md"]
    result = publish(store, first, signing[0])
    assert result["newObjects"] == 5 and result["added"] == 6
    first.commits["Mehael-Yeh/Rstone"] = "a" * 40
    unchanged = publish(store, first, signing[0])
    assert unchanged["status"] == "unchanged"
    assert unchanged["newObjects"] == 0
    assert unchanged["sourceCommits"]["Mehael-Yeh/Rstone"] == "a" * 40
    assert store.sequences() == [1]


def test_scope_key_and_signature_fail_closed(store, sources, signing):
    snapshot = collect(*sources)
    with pytest.raises(DataError, match="scope"):
        store.prepare(snapshot, signing[0])
    with pytest.raises(DataError, match="signing key"):
        store.prepare(snapshot, None, public_scope_approved=True)
    with pytest.raises(DataError, match="required"):
        Signer.load(signing[0].key_id, None, signing[1])
    seed = signing[0].private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    assert Signer.load(signing[0].key_id, base64.b64encode(seed).decode(), signing[1])
    with pytest.raises(DataError, match="does not match"):
        Signer.load(signing[0].key_id, base64.b64encode(b"x" * 32).decode(), signing[1])
    publish(store, snapshot, signing[0])
    pointer = decode_json(store.latest_bytes())
    raw = (store.public / pointer["manifestPath"]).read_bytes()
    signature = (store.public / pointer["signaturePath"]).read_bytes()
    assert verify_release(pointer, raw, signature, signing[1])["sequence"] == 1
    tampered = raw.replace(b'"schemaVersion":1', b'"schemaVersion":2')
    with pytest.raises(DataError):
        verify_release(pointer, tampered, signature, signing[1])
    tampered = raw.replace(b'"1.0.0"', b'"1.0.1"')
    changed_pointer = dict(pointer, manifestSha256=sha256(tampered))
    with pytest.raises(DataError, match="signature"):
        verify_release(changed_pointer, tampered, signature, signing[1])
    with pytest.raises(DataError, match="sequence mismatch"):
        verify_release(dict(pointer, sequence=2, manifestPath="manifests/000002.json",
                            signaturePath="manifests/000002.sig"), raw, signature, signing[1])
    assert store.read()["sequence"] == 1


def test_bulk_deletion_and_empty_are_never_silently_published(store, sources, signing):
    first = collect(*sources)
    publish(store, first, signing[0])
    second = changed(first, "产品列表/RD200.md")
    before = store.latest_bytes()
    with pytest.raises(DataError, match="bulk deletion") as error:
        store.prepare(second, signing[0], public_scope_approved=True)
    assert store.latest_bytes() == before
    approval = str(error.value).split("required: ")[1]
    store.prepare(second, signing[0], public_scope_approved=True, approval=approval)
    store.promote()
    empty = Snapshot({}, first.commits)
    with pytest.raises(DataError, match="empty database"):
        store.prepare(empty, signing[0], public_scope_approved=True, approval=approval)
    assert store.read()["sequence"] == 2


@pytest.mark.parametrize("step", ["objects", "manifest", "pending", "before-pointer", "after-pointer"])
def test_interruption_and_recovery(store, sources, signing, step):
    first = collect(*sources)
    publish(store, first, signing[0])
    baseline = store.latest_bytes()
    second = changed(first, "产品列表/RS300.md", b"---\ntags: [\"\\u6c34\\u6027\", UV]\n\"\\u56fa\\u542b\": 77\n---\nadded\n")

    def interrupt(event):
        if event == step:
            raise DataError("simulated interruption")

    with pytest.raises(DataError, match="simulated"):
        store.prepare(second, signing[0], public_scope_approved=True, failpoint=interrupt)
        store.promote(failpoint=interrupt)
    if step != "after-pointer":
        assert store.latest_bytes() == baseline
    result = store.prepare(second, signing[0], public_scope_approved=True)
    if result["status"] != "unchanged":
        result = store.promote()
    assert store.read()["files"] == inventory(second.files)
    assert result["sequence"] >= 2
    assert store.pending() is None


def test_corrupt_object_never_promotes(store, sources, signing):
    first = collect(*sources)
    publish(store, first, signing[0])
    baseline = store.latest_bytes()
    second = changed(first, "产品列表/RS300.md", b"---\ntags: [\"\\u6c34\\u6027\", UV]\n\"\\u56fa\\u542b\": 77\n---\nadded\n")
    store.prepare(second, signing[0], public_scope_approved=True)
    (store.public / "objects" / sha256(second.files["产品列表/RS300.md"])).write_bytes(b"bad")
    with pytest.raises(DataError, match="integrity"):
        store.promote()
    assert store.latest_bytes() == baseline


def test_rollback_new_sequence_history_gc_and_zip(store, sources, signing):
    first = collect(*sources)
    old_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    publish(store, first, signing[0], now=old_time)
    second = changed(first, "产品列表/RS100.md", b"---\ntags: [\"\\u6c34\\u6027\", UV]\n\"\\u56fa\\u542b\": 77\n---\nrevision 2\n")
    publish(store, second, signing[0], now=old_time)
    original_manifest = (store.public / "manifests/000001.json").read_bytes()
    result = store.rollback(1, signing[0], public_scope_approved=True, now=old_time)
    assert result["newObjects"] == 0
    store.promote()
    current = store.read()
    assert current["sequence"] == 3 and current["rollbackOf"] == 1
    assert current["files"] == inventory(first.files)
    assert (store.public / "manifests/000001.json").read_bytes() == original_manifest
    package = store.package(3)
    assert (store.public / package).is_file()
    plan = store.gc(retain=2, grace_days=0, now=datetime.now(timezone.utc) + timedelta(days=1))
    # Sequence 3 restores sequence 1, so its revalidation needs that signed
    # release: the baseline evidence is part of what a retained release costs.
    assert plan["protectedSequences"] == [1, 2, 3]
    assert "manifests/000001.json" not in plan["remove"]
    for record in current["files"]:
        assert "objects/" + record["sha256"] not in plan["remove"]
    assert (store.public / "manifests/000001.json").exists()
    store.gc(retain=2, grace_days=0, apply=True, now=datetime.now(timezone.utc) + timedelta(days=1))
    assert store.sequences() == [1, 2, 3]
    assert store.read()["sequence"] == 3


def test_immutable_object_conflict(store, sources, signing):
    first = collect(*sources)
    obj = store.public / "objects" / sha256(first.files["产品列表/RS100.md"])
    obj.parent.mkdir(parents=True)
    obj.write_bytes(b"incorrect")
    with pytest.raises(DataError, match="immutable"):
        publish(store, first, signing[0])
    assert store.latest_bytes() is None


def test_intentional_namespace_empty_requires_exact_approval(store, sources, signing):
    first = collect(*sources)
    publish(store, first, signing[0])
    rstone, tds = sources
    (rstone / "产品应用/方案.md").unlink()
    (rstone / "产品应用/.gitkeep").touch()
    commit_all(rstone)
    second = collect(rstone, tds)
    with pytest.raises(DataError, match="bulk deletion") as error:
        store.prepare(second, signing[0], public_scope_approved=True)
    approval = str(error.value).split("required: ")[1]
    store.prepare(second, signing[0], public_scope_approved=True, approval=approval)
    store.promote()
    assert not any(r["path"].startswith("产品应用/") for r in store.read()["files"])
    assert store.read()["sequence"] == 2


def test_gc_protects_both_current_and_pending(store, sources, signing):
    first = collect(*sources)
    publish(store, first, signing[0])
    second = changed(first, "产品列表/RS100.md", b"---\ntags: [\"\\u6c34\\u6027\", UV]\n\"\\u56fa\\u542b\": 77\n---\nversion 2\n")
    publish(store, second, signing[0])
    third = changed(first, "产品列表/RS100.md", b"---\ntags: [\"\\u6c34\\u6027\", UV]\n\"\\u56fa\\u542b\": 77\n---\nversion 3\n")
    store.prepare(third, signing[0], public_scope_approved=True)
    plan = store.gc(retain=1, grace_days=0, apply=True,
                    now=datetime.now(timezone.utc) + timedelta(days=1))
    # Sequence 1 is sequence 2's inherited-link baseline, so it is retained too.
    assert plan["protectedSequences"] == [1, 2, 3]
    assert store.read()["sequence"] == 2
    assert store.read(store.pending()["pointer"])["sequence"] == 3
    store.promote()
    assert store.read()["sequence"] == 3
