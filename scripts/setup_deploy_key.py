"""Install one repository-scoped SSH key directly into a protected Actions Secret."""

import argparse
import json
import re
import subprocess

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)
from rstone_data.protocol import DataError, require


def install_key(repository, secret_repository, environment, secret_name, title, *, write=False):
    require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository), "invalid repository")
    require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", secret_repository), "invalid Secret repository")
    require(re.fullmatch(r"[A-Z][A-Z0-9_]+", secret_name), "invalid Secret name")
    require(bool(environment) and bool(title), "environment and title are required")

    def run(command, *, data=None):
        result = subprocess.run(command, input=data, capture_output=True, timeout=60, check=False)
        require(result.returncode == 0, "GitHub configuration operation failed; inspect repository settings")
        return result.stdout

    secrets = json.loads(run(["gh", "secret", "list", "--repo", secret_repository,
                              "--env", environment, "--json", "name"]))
    require(not any(item["name"] == secret_name for item in secrets), "Secret exists; never overwrite a live key")
    existing = json.loads(run(["gh", "api", f"repos/{repository}/keys"]))
    require(not any(item["title"] == title for item in existing), "deploy key title exists; reconcile before retrying")
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(Encoding.OpenSSH, PublicFormat.OpenSSH).decode()
    payload = json.dumps({"title": title, "key": public, "read_only": not write}).encode()
    installed = json.loads(run(["gh", "api", "--method", "POST", f"repos/{repository}/keys",
                                "--input", "-"], data=payload))
    secret = private.private_bytes(Encoding.PEM, PrivateFormat.OpenSSH, NoEncryption())
    try:
        run(["gh", "secret", "set", secret_name, "--repo", secret_repository, "--env", environment], data=secret)
    except (DataError, OSError, subprocess.TimeoutExpired):
        # A retry cannot recover the private key. Revoke the public half when Secret installation fails.
        run(["gh", "api", "--method", "DELETE", f"repos/{repository}/keys/{installed['id']}"])
        raise
    return {"repository": repository, "deployKeyId": installed["id"], "readOnly": not write,
            "secretRepository": secret_repository, "environment": environment, "secretName": secret_name}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--secret-repository", required=True)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--secret-name", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--write", action="store_true", help="Notification-only target; restrict refs with rulesets FIRST")
    args = parser.parse_args()
    try:
        result = install_key(args.repository, args.secret_repository, args.environment,
                             args.secret_name, args.title, write=args.write)
        print(json.dumps(result))
    except (DataError, OSError, subprocess.TimeoutExpired) as error:
        parser.exit(1, str(error) if isinstance(error, DataError) else "Deploy key setup failed\n")


if __name__ == "__main__":
    main()
