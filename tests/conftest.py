import base64
import contextlib
import functools
import http.server
import subprocess
import threading
from urllib.parse import urlsplit

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from rstone_data.release import Store
from rstone_data.signing import Signer


def run_git(path, *arguments):
    return subprocess.run(["git", "-C", str(path), *arguments], check=True, capture_output=True).stdout


def commit_all(path):
    run_git(path, "add", ".")
    run_git(path, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture")


@pytest.fixture
def sources(tmp_path):
    rstone, tds = tmp_path / "Rstone", tmp_path / "RstoneTDS"
    rstone.mkdir()
    tds.mkdir()
    for path, branch in ((rstone, "master"), (tds, "main")):
        run_git(path, "init", "--initial-branch=" + branch)
        run_git(path, "config", "core.autocrlf", "false")
        repository = "Rstone" if path == rstone else "RstoneTDS"
        run_git(path, "remote", "add", "origin", f"https://github.com/Mehael-Yeh/{repository}.git")
    data = {
        "产品列表/RS100.md": b"---\ntags: [UV]\n---\n# RS100\r\n",
        "产品列表/RD200.md": b"---\ntags: [PU]\n---\n# RD200\n",
        "产品应用/方案.md": '---\ntags: [PC]\n底漆: "[[RS100]]"\n---\n'.encode(),
        "产品配方/RS100-配方.md": b"| A | B |\n| - | - |\n| RS100 | 100 |\n",
    }
    for name, raw in data.items():
        target = rstone / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    (tds / "RS100.TDS.md").write_bytes(b"# RS100\nTDS fixture only\n")
    (rstone / ".env").write_text("not public")
    commit_all(rstone)
    commit_all(tds)
    return rstone, tds


@pytest.fixture
def signing():
    private = Ed25519PrivateKey.generate()
    keys = {"test-ephemeral": base64.b64encode(private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()}
    return Signer("test-ephemeral", private), keys


@pytest.fixture
def store(tmp_path, signing):
    return Store(tmp_path / "store", signing[1])


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
