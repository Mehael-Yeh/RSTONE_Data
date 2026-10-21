"""Operator-invoked key bootstrap: private seed goes directly to GitHub Secrets via stdin."""

import argparse
import base64
import re
import subprocess
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

from rstone_data.protocol import DataError, canonical_json, decode_json, require
from rstone_data.storage import atomic_write


def main():
    parser = argparse.ArgumentParser(description="Install a newly generated signing key in GitHub Actions Secrets")
    parser.add_argument("--repository", required=True)
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--environment", default="github-pages")
    parser.add_argument("--keys", type=Path, default=Path(__file__).resolve().parents[1] / "config/trusted_keys.json")
    args = parser.parse_args()
    try:
        require(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args.key_id), "invalid keyId")
        keys = decode_json(args.keys.read_bytes())
        require(args.key_id not in keys["keys"], "keyId exists; never overwrite a trusted key")
        private = Ed25519PrivateKey.generate()
        seed = base64.b64encode(private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()))
        public = base64.b64encode(private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
        command = ["gh", "secret", "set", "DATA_SIGNING_PRIVATE_KEY", "--repo", args.repository,
                   "--env", args.environment]
        result = subprocess.run(command, input=seed, capture_output=True, timeout=60)
        require(result.returncode == 0, "GitHub Secret installation failed; no private key written locally")
        keys["keys"][args.key_id] = public
        atomic_write(args.keys, canonical_json(keys))
        print("Installed private key in GitHub Secrets. Review and commit the updated public key table:")
        print(canonical_json({"keyId": args.key_id, "publicKey": public}).decode())
        print("Then set DATA_SIGNING_KEY_ID to this keyId; rotate only after compatible clients ship.")
    except (DataError, OSError, subprocess.TimeoutExpired) as error:
        parser.exit(1, str(error) if isinstance(error, DataError) else "Key bootstrap configuration failed\n")


if __name__ == "__main__":
    main()
