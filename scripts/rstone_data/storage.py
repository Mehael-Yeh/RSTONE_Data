"""Filesystem primitives: immutable writes and an atomic mutable pointer."""

import os
import tempfile
from pathlib import Path

from .protocol import require


def safe_file(root, relative):
    root = Path(root).resolve()
    parts = relative.split("/")
    require(all(p not in ("", ".", "..") and "\\" not in p and ":" not in p for p in parts),
            "unsafe storage path")
    path = root.joinpath(*parts)
    require(path.resolve().is_relative_to(root), "storage path escapes root")
    cursor = path
    while cursor != root:
        require(not cursor.is_symlink(), "symlink in storage")
        cursor = cursor.parent
    return path


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def immutable_write(root, relative, data):
    path = safe_file(root, relative)
    if path.exists():
        require(path.read_bytes() == data, f"immutable resource conflict: {relative}")
        return False
    atomic_write(path, data)
    return True


class ReleaseLock:
    """Crash leaves a fail-closed lock; CLI documents manual stale-lock recovery."""

    def __init__(self, root):
        self.path = Path(root) / ".release.lock"

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            require(False, "release locked; confirm no publisher is running before clearing stale lock")
        with os.fdopen(descriptor, "w") as stream:
            stream.write(str(os.getpid()))
        return self

    def __exit__(self, *_):
        self.path.unlink()
