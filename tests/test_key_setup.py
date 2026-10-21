import base64
from types import SimpleNamespace

import pytest
from rstone_data.protocol import canonical_json, decode_json
from rstone_data.signing import Signer


def test_key_bootstrap_only_sends_private_seed_to_secret_stdin(tmp_path, monkeypatch, capsys):
    import setup_signing
    keys = tmp_path / "keys.json"
    keys.write_bytes(canonical_json({"keys": {}}))
    received = []

    def github(command, **kwargs):
        received.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=b"[]")

    monkeypatch.setattr(setup_signing.subprocess, "run", github)
    monkeypatch.setattr("sys.argv", ["setup_signing", "--repository", "example/data", "--key-id", "next-key",
                                     "--secret-name", "DATA_SIGNING_PRIVATE_KEY_NEXT", "--keys", str(keys)])
    setup_signing.main()
    assert len(received) == 2
    seed = received[1][1]["input"]
    assert len(base64.b64decode(seed, validate=True)) == 32
    assert seed.decode() not in capsys.readouterr().out
    assert seed not in keys.read_bytes()
    assert list(tmp_path.iterdir()) == [keys]
    public_keys = decode_json(keys.read_bytes())["keys"]
    assert Signer.load("next-key", seed.decode(), public_keys)
    assert received[1][0][3] == "DATA_SIGNING_PRIVATE_KEY_NEXT"


def test_active_secret_is_never_overwritten_during_rotation(tmp_path, monkeypatch):
    import setup_signing
    keys = tmp_path / "keys.json"
    original = canonical_json({"keys": {}})
    keys.write_bytes(original)
    called = []

    def github(command, **kwargs):
        called.append(command)
        return SimpleNamespace(returncode=0, stdout=b'[{"name":"DATA_SIGNING_PRIVATE_KEY"}]')

    monkeypatch.setattr(setup_signing.subprocess, "run", github)
    monkeypatch.setattr("sys.argv", ["setup_signing", "--repository", "example/data", "--key-id", "next-key", "--keys", str(keys)])
    with pytest.raises(SystemExit) as error:
        setup_signing.main()
    assert error.value.code == 1
    assert len(called) == 1
    assert keys.read_bytes() == original
