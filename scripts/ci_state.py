"""Persistent distribution branch operations; never force push or execute branch contents."""

import argparse
import os
import subprocess
from pathlib import Path

from rstone_data.protocol import DataError, require


def git(path, *arguments):
    command = ["git", "-c", "core.hooksPath=" + os.devnull, "-c", "core.autocrlf=false"]
    if os.environ.get("GH_TOKEN"):
        command += ["-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential"]
    result = subprocess.run([*command, "-C", str(path), *arguments], check=False, capture_output=True)
    require(result.returncode == 0, "distribution Git operation failed (no empty-state fallback)")
    return result.stdout.decode("utf-8").strip()


def checkout(repository, destination):
    repository, destination = Path(repository).resolve(), Path(destination).resolve()
    require(not destination.exists(), "state destination already exists")
    exists = git(repository, "ls-remote", "--heads", "origin", "refs/heads/distribution")
    if exists:
        git(repository, "fetch", "--no-tags", "origin", "refs/heads/distribution")
        git(repository, "worktree", "add", "--detach", str(destination), "FETCH_HEAD")
    else:
        git(repository, "worktree", "add", "--detach", str(destination), "HEAD")
        git(destination, "switch", "--orphan", "distribution-bootstrap")
    (destination / "public").mkdir(exist_ok=True)
    (destination / "state").mkdir(exist_ok=True)
    (destination / ".gitattributes").write_text("public/** -text\nstate/** -text\n", encoding="utf-8", newline="\n")
    (destination / "public/.nojekyll").touch()
    if "GITHUB_OUTPUT" in os.environ:
        pending = (destination / "state/pending.json").exists()
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write("pending=" + ("true" if pending else "false") + "\n")


def checkpoint(store, message):
    require((Path(store) / ".git").exists(), "state must be a Git worktree")
    git(store, "add", "--all", "--", ".gitattributes", "public", "state")
    staged = git(store, "diff", "--cached", "--name-only")
    if staged:
        git(store, "-c", "user.name=github-actions[bot]", "-c",
            "user.email=41898282+github-actions[bot]@users.noreply.github.com", "commit", "-m", message)
        git(store, "push", "origin", "HEAD:refs/heads/distribution")


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    opening = commands.add_parser("checkout")
    opening.add_argument("--repository", type=Path, required=True)
    opening.add_argument("--destination", type=Path, required=True)
    saving = commands.add_parser("checkpoint")
    saving.add_argument("--store", type=Path, required=True)
    saving.add_argument("--message", required=True)
    args = parser.parse_args()
    try:
        if args.command == "checkout":
            checkout(args.repository, args.destination)
        else:
            checkpoint(args.store, args.message)
    except DataError as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
