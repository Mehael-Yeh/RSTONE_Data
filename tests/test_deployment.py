import base64
import contextlib
import functools
import http.server
import shutil
import threading
from urllib.parse import urlsplit

import pytest
import yaml
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption

from conftest import run_git
from rstone_data.cli import main
from rstone_data.protocol import DataError, canonical_json, decode_json, require, sha256
from rstone_data.release import Store
from rstone_data.sources import collect
from rstone_data.transport import fetch_release
from test_release import changed, publish


@pytest.fixture
def serve():
    @contextlib.contextmanager
    def server(directory, cors=True, faults=None):
        class Handler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def end_headers(self):
                if cors:
                    self.send_header("Access-Control-Allow-Origin", "*")
                super().end_headers()

            def do_GET(self):
                if urlsplit(self.path).path in (faults or set()):
                    self.send_error(404)
                else:
                    super().do_GET()

        service = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(directory)))
        thread = threading.Thread(target=service.serve_forever, daemon=True)
        thread.start()
        try:
            yield "http://127.0.0.1:" + str(service.server_port)
        finally:
            service.shutdown()
            service.server_close()
            thread.join(timeout=5)
    return server


def test_staging_verification_pointer_last_and_final_deploy_recovery(store, sources, signing, tmp_path, serve):
    first = collect(*sources)
    publish(store, first, signing[0])
    remote = tmp_path / "remote"
    shutil.copytree(store.public, remote)
    second = changed(first, "产品列表/RS300.md", b"---\ntags: []\n---\nnew\n")
    store.prepare(second, signing[0], public_scope_approved=True)
    shutil.copytree(store.public, remote, dirs_exist_ok=True)
    new_obj = "objects/" + sha256(second.files["产品列表/RS300.md"])
    baseline = store.latest_bytes()
    assert decode_json((remote / "latest.json").read_bytes())["sequence"] == 1
    faults = {"/" + new_obj}
    with serve(remote, faults=faults) as base:
        def verify_candidate(pointer):
            fetch_release(base, signing[1], pointer, allow_local_http=True, require_cors=True)

        with pytest.raises(DataError, match="unavailable"):
            store.promote(verify_candidate)
        assert store.latest_bytes() == baseline and store.pending()
        faults.clear()
        store.promote(verify_candidate, keep_pending=True)
        assert store.read()["sequence"] == 2 and store.pending()
        assert decode_json((remote / "latest.json").read_bytes())["sequence"] == 1

        def verify_live(pointer):
            latest, *_ = fetch_release(base, signing[1], allow_local_http=True)
            require(latest == pointer, "public latest not reached target")

        with pytest.raises(DataError, match="not reached"):
            store.complete(verify_live)
        assert store.pending()
        # Retry starts from durable state even if the source repos are no longer available.
        assert store.prepare(None, signing[0], public_scope_approved=True)["resumed"]
        shutil.copytree(store.public, remote, dirs_exist_ok=True)
        store.complete(verify_live)
        assert store.pending() is None


def test_cors_validation_and_http_restriction(store, sources, signing, serve):
    publish(store, collect(*sources), signing[0])
    with serve(store.public, cors=False) as base:
        with pytest.raises(DataError, match="CORS"):
            fetch_release(base, signing[1], allow_local_http=True, require_cors=True)
        with pytest.raises(DataError, match="HTTPS"):
            fetch_release(base, signing[1])


def test_cli_production_missing_key_fails_with_structured_report(store, sources, signing, tmp_path, monkeypatch):
    keys = tmp_path / "keys.json"
    keys.write_bytes(canonical_json({"keys": signing[1]}))
    report = tmp_path / "report.json"
    monkeypatch.delenv("DATA_SIGNING_PRIVATE_KEY", raising=False)
    assert main(["prepare", "--store", str(store.root), "--keys", str(keys), "--key-id", signing[0].key_id,
                 "--rstone", str(sources[0]), "--tds", str(sources[1]), "--public-scope-approved",
                 "--report", str(report)]) == 1
    assert decode_json(report.read_bytes())["status"] == "failed"
    assert store.latest_bytes() is None


def test_cli_end_to_end_actual_http(store, sources, signing, tmp_path, monkeypatch, serve):
    keys = tmp_path / "keys.json"
    keys.write_bytes(canonical_json({"keys": signing[1]}))
    seed = signing[0].private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    monkeypatch.setenv("DATA_SIGNING_PRIVATE_KEY", base64.b64encode(seed).decode())
    monkeypatch.setenv("DATA_SIGNING_KEY_ID", signing[0].key_id)
    common = ["--store", str(store.root), "--keys", str(keys)]
    assert main(["prepare", *common, "--rstone", str(sources[0]), "--tds", str(sources[1]),
                 "--public-scope-approved"]) == 0
    assert store.latest_bytes() is None
    remote = tmp_path / "remote"
    shutil.copytree(store.public, remote)
    with serve(remote) as base:
        http_args = ["--base-url", base, "--allow-local-http", "--require-cors"]
        assert main(["promote", *common, *http_args, "--keep-pending"]) == 0
        assert store.pending()
        shutil.copytree(store.public, remote, dirs_exist_ok=True)
        assert main(["complete", *common, *http_args]) == 0
        assert main(["verify", "--keys", str(keys), *http_args]) == 0
    assert store.pending() is None and store.read()["sequence"] == 1


def test_durable_git_state_bootstrap_checkpoint_and_resume(tmp_path, sources, signing, monkeypatch):
    from ci_state import checkout, checkpoint
    monkeypatch.delenv("GH_TOKEN", raising=False)
    origin, publisher = tmp_path / "origin.git", tmp_path / "publisher"
    origin.mkdir()
    publisher.mkdir()
    run_git(origin, "init", "--bare")
    run_git(publisher, "init", "--initial-branch=main")
    (publisher / "publisher-code.txt").write_text("must not enter distribution")
    run_git(publisher, "add", ".")
    run_git(publisher, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "init")
    run_git(publisher, "remote", "add", "origin", str(origin))
    run_git(publisher, "push", "origin", "main")
    first_root = tmp_path / "state1"
    checkout(publisher, first_root)
    first = Store(first_root, signing[1])
    first.prepare(collect(*sources), signing[0], public_scope_approved=True)
    checkpoint(first_root, "stage")
    paths = run_git(origin, "ls-tree", "-r", "--name-only", "distribution").decode()
    assert "state/pending.json" in paths
    assert "publisher-code.txt" not in paths
    second_root = tmp_path / "state2"
    checkout(publisher, second_root)
    second = Store(second_root, signing[1])
    assert second.pending() and second.latest_bytes() is None
    second.promote(keep_pending=True)
    checkpoint(second_root, "promote")
    third_root = tmp_path / "state3"
    checkout(publisher, third_root)
    third = Store(third_root, signing[1])
    assert third.read()["sequence"] == 1 and third.pending()


def test_workflow_boundaries():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.load((root / ".github/workflows/publish-data.yml").read_text(), Loader=yaml.BaseLoader)
    assert workflow["concurrency"]["cancel-in-progress"] == "false"
    steps = workflow["jobs"]["publish"]["steps"]
    refs = {s.get("with", {}).get("repository"): s.get("with", {}).get("ref") for s in steps}
    assert refs["Mehael-Yeh/Rstone"] == "master"
    assert refs["Mehael-Yeh/RstoneTDS"] == "main"
    assert all(len(s["uses"].split("@")[1]) == 40 for s in steps if "uses" in s)
    assert not any("flutter build" in s.get("run", "").lower() for s in steps)


def test_mirror_copies_signed_bytes_and_keeps_pointer_on_failure(store, sources, signing, tmp_path, serve):
    from rstone_data.mirrors import deploy_mirror
    first = collect(*sources)
    publish(store, first, signing[0])
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    with serve(mirror) as base:
        result = deploy_mirror(store.root, mirror, base, signing[1], allow_local_http=True)
        assert result["newObjects"] == 5
        assert (mirror / "latest.json").read_bytes() == store.latest_bytes()
        result = deploy_mirror(store.root, mirror, base, signing[1], allow_local_http=True)
        assert result["newObjects"] == 0
    old = (mirror / "latest.json").read_bytes()
    second = changed(first, "产品列表/RS300.md", b"---\ntags: []\n---\nnew\n")
    publish(store, second, signing[0])
    faults = {"/objects/" + sha256(second.files["产品列表/RS300.md"])}
    with serve(mirror, faults=faults) as base:
        with pytest.raises(DataError):
            deploy_mirror(store.root, mirror, base, signing[1], allow_local_http=True)
    assert (mirror / "latest.json").read_bytes() == old
    assert (mirror / "manifests/000002.json").read_bytes() == (store.public / "manifests/000002.json").read_bytes()
