import io
import shutil
import zipfile

import pytest
from rstone_data.client import active_directory, import_package, synchronize
from rstone_data.protocol import DataError, decode_json, sha256
from rstone_data.release import Store
from rstone_data.sources import collect
from test_release import changed, publish


def test_skip_versions_only_download_changed_objects_and_keep_overlay(store, sources, signing, tmp_path, serve):
    first = collect(*sources)
    cache = tmp_path / "client"
    publish(store, first, signing[0])
    with serve(store.public) as base:
        result = synchronize([base], cache, signing[1], allow_local_http=True)
        assert result["downloadedObjects"] == 5
        overlay = cache / "overlay/用户产品.md"
        overlay.parent.mkdir()
        overlay.write_bytes(b"user data")
        second = changed(first, "产品列表/RS100.md", b"---\ntags: []\n---\nchanged once\n")
        second = changed(second, "产品列表/RS300.md", first.files["产品列表/RS100.md"])
        publish(store, second, signing[0])
        third = changed(second, "产品列表/RD200.md")
        third = changed(third, "产品列表/RS301.md", third.files["产品列表/RS300.md"])
        third = changed(third, "产品列表/RS300.md")
        publish(store, third, signing[0])
        fourth = changed(third, "产品应用/PC/方案.md", third.files["产品应用/方案.md"])
        fourth = changed(fourth, "产品应用/方案.md")
        fourth = changed(fourth, "产品列表/RS400.md", second.files["产品列表/RS100.md"])
        publish(store, fourth, signing[0])
        # A retained current full manifest must work after intermediate versions have expired.
        for sequence in (2, 3):
            (store.public / f"manifests/{sequence:06d}.json").unlink()
            (store.public / f"manifests/{sequence:06d}.sig").unlink()
        result = synchronize([base], cache, signing[1], allow_local_http=True)
        assert result["sequence"] == 4 and result["downloadedObjects"] == 1
        assert result["downloadedBytes"] == len(second.files["产品列表/RS100.md"])
        directory = active_directory(cache, signing[1])
        actual = {p.relative_to(directory).as_posix(): p.read_bytes() for p in directory.rglob("*.md")}
        assert actual == fourth.files
        assert not (directory / "产品列表/RD200.md").exists()
        assert not (directory / "产品应用/方案.md").exists()
        assert overlay.read_bytes() == b"user data"
        assert synchronize([base], cache, signing[1], allow_local_http=True)["downloadedObjects"] == 0


def test_mirrors_supply_same_target_objects_even_with_stale_latest(store, sources, signing, tmp_path, serve):
    first = collect(*sources)
    publish(store, first, signing[0])
    old_latest = store.latest_bytes()
    cache = tmp_path / "client"
    with serve(store.public) as base:
        synchronize([base], cache, signing[1], allow_local_http=True)
    second = changed(first, "产品列表/RS100.md", b"---\ntags: []\n---\nversion2\n")
    publish(store, second, signing[0])
    mirror1, mirror2 = tmp_path / "mirror1", tmp_path / "mirror2"
    shutil.copytree(store.public, mirror1)
    shutil.copytree(store.public, mirror2)
    (mirror2 / "latest.json").write_bytes(old_latest)
    digest = sha256(second.files["产品列表/RS100.md"])
    (mirror1 / "objects" / digest).unlink()
    with serve(mirror1) as base1, serve(mirror2) as base2:
        result = synchronize([base1, base2], cache, signing[1], allow_local_http=True)
        assert result["sequence"] == 2 and result["downloadedObjects"] == 1


def test_all_mirrors_missing_object_retains_previous_generation(store, sources, signing, tmp_path, serve):
    first = collect(*sources)
    publish(store, first, signing[0])
    cache = tmp_path / "client"
    with serve(store.public) as base:
        synchronize([base], cache, signing[1], allow_local_http=True)
    baseline = (cache / "active.json").read_bytes()
    second = changed(first, "产品列表/RS100.md", b"---\ntags: []\n---\nmissing\n")
    publish(store, second, signing[0])
    faults = {"/objects/" + sha256(second.files["产品列表/RS100.md"])}
    with serve(store.public, faults=faults) as base:
        with pytest.raises(DataError, match="unavailable from all"):
            synchronize([base], cache, signing[1], allow_local_http=True)
    assert (cache / "active.json").read_bytes() == baseline
    assert (active_directory(cache, signing[1]) / "产品列表/RS100.md").read_bytes() == first.files["产品列表/RS100.md"]


@pytest.mark.parametrize("step", ["objects", "generation", "before-activate", "after-activate"])
def test_client_interruption_never_activates_partial_data(store, sources, signing, tmp_path, serve, step):
    first = collect(*sources)
    publish(store, first, signing[0])
    cache = tmp_path / "client"
    with serve(store.public) as base:
        synchronize([base], cache, signing[1], allow_local_http=True)
        second = changed(first, "产品列表/RS100.md", b"---\ntags: []\n---\nnew data\n")
        publish(store, second, signing[0])

        def interrupt(event):
            if event == step:
                raise DataError("client interrupted")

        with pytest.raises(DataError, match="client interrupted"):
            synchronize([base], cache, signing[1], allow_local_http=True, failpoint=interrupt)
        active = active_directory(cache, signing[1])
        expected = second if step == "after-activate" else first
        assert (active / "产品列表/RS100.md").read_bytes() == expected.files["产品列表/RS100.md"]
        synchronize([base], cache, signing[1], allow_local_http=True)
        assert decode_json((cache / "active.json").read_bytes())["highestSequence"] == 2


def test_stale_signed_version_and_signature_corruption_rejected(store, sources, signing, tmp_path, serve):
    first = collect(*sources)
    publish(store, first, signing[0])
    stale = tmp_path / "stale"
    shutil.copytree(store.public, stale)
    second = changed(first, "产品列表/RS100.md", b"---\ntags: []\n---\nnew data\n")
    publish(store, second, signing[0])
    cache = tmp_path / "client"
    with serve(store.public) as base:
        synchronize([base], cache, signing[1], allow_local_http=True)
    baseline = (cache / "active.json").read_bytes()
    with serve(stale) as base:
        with pytest.raises(DataError, match="stale"):
            synchronize([base], cache, signing[1], allow_local_http=True)
    (store.public / "manifests/000002.sig").write_bytes(b"bad\n")
    with serve(store.public) as base:
        with pytest.raises(DataError, match="verifiable"):
            synchronize([base], cache, signing[1], allow_local_http=True)
    assert (cache / "active.json").read_bytes() == baseline


def test_signed_same_sequence_conflict_fails_closed(store, sources, signing, tmp_path, serve):
    first = collect(*sources)
    publish(store, first, signing[0])
    fork = Store(tmp_path / "fork", signing[1])
    other = changed(first, "产品列表/RS100.md", b"---\ntags: []\n---\nconflict\n")
    publish(fork, other, signing[0])
    with serve(store.public) as base1, serve(fork.public) as base2:
        with pytest.raises(DataError, match="same-sequence conflict"):
            synchronize([base1, base2], tmp_path / "client", signing[1], allow_local_http=True)


def test_min_app_version_and_same_version_local_repair(store, sources, signing, tmp_path, serve):
    first = collect(*sources)
    publish(store, first, signing[0], min_app_version="2.0.0")
    cache = tmp_path / "client"
    with serve(store.public) as base:
        with pytest.raises(DataError, match="upgrade required"):
            synchronize([base], cache, signing[1], allow_local_http=True)
        synchronize([base], cache, signing[1], app_version="2.0.0", allow_local_http=True)
        directory = active_directory(cache, signing[1])
        (directory / "产品列表/RS100.md").write_bytes(b"corrupt")
        repaired = synchronize([base], cache, signing[1], app_version="2.0.0", allow_local_http=True)
        assert repaired["downloadedObjects"] == 0
        assert (active_directory(cache, signing[1]) / "产品列表/RS100.md").read_bytes() == first.files["产品列表/RS100.md"]


def test_verified_zip_import_rejects_extra_entries_and_tampering(store, sources, signing, tmp_path):
    first = collect(*sources)
    publish(store, first, signing[0])
    package = store.public / store.package(1)
    cache = tmp_path / "client"
    result = import_package(package, cache, signing[1])
    assert result["sequence"] == 1
    baseline = (cache / "active.json").read_bytes()
    second = changed(first, "产品列表/RS100.md", b"---\ntags: []\n---\nnew data\n")
    publish(store, second, signing[0])
    package = store.public / store.package(2)
    tampered = io.BytesIO()
    with zipfile.ZipFile(package) as original, zipfile.ZipFile(tampered, "w") as output:
        for name in original.namelist():
            output.writestr(name, b"wrong" if name.endswith("RS100.md") else original.read(name))
    tampered.seek(0)
    with pytest.raises(DataError, match="integrity"):
        import_package(tampered, cache, signing[1])
    assert (cache / "active.json").read_bytes() == baseline
    extra = io.BytesIO(package.read_bytes())
    with zipfile.ZipFile(extra, "a") as output:
        output.writestr("files/../../escape.md", b"must not extract")
    extra.seek(0)
    with pytest.raises(DataError, match="extra or missing"):
        import_package(extra, cache, signing[1])
    assert not (tmp_path / "escape.md").exists()
    assert import_package(package, cache, signing[1])["sequence"] == 2
