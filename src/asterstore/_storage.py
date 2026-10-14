"""Cooperative POSIX file primitives. No lifecycle records or global inventory."""

from __future__ import annotations

import fcntl
import os
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path

from ._models import identifier, relative
from .errors import StoreCorruptionError


def dataset_key(dataset_id: str) -> str:
    return sha256(identifier(dataset_id).encode()).hexdigest()


def checked(base: Path, suffix: str = "", *, missing: bool = False) -> Path:
    """Reject symlinks/special files anywhere below the captured store root."""
    parts = relative(suffix).split("/") if suffix else []
    path = base
    for i, part in enumerate([None, *parts]):
        if part is not None:
            path = path / part
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            if missing:
                continue
            raise
        if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise StoreCorruptionError(f"unsafe file type: {path}")
        if i < len(parts) and not stat.S_ISDIR(mode):
            raise StoreCorruptionError(f"not a directory: {path}")
    return path


def read(base: Path, suffix: str) -> bytes:
    path = checked(base, suffix)
    if not path.is_file():
        raise StoreCorruptionError(f"not a regular file: {path}")
    return path.read_bytes()


def mkdir(base: Path, suffix: str) -> Path:
    path = checked(base, suffix, missing=True)
    path.mkdir(parents=True, exist_ok=True)
    return checked(base, suffix)


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def sync_paths(base: Path, paths: set[Path]) -> None:
    directories = {base, base.parent}
    for path in paths:
        checked(base, path.relative_to(base).as_posix())
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        parent = path.parent
        while parent != base.parent:
            directories.add(parent)
            parent = parent.parent
    for path in sorted(directories, key=lambda p: len(p.parts), reverse=True):
        sync_directory(path)


def write(base: Path, suffix: str, data: bytes, *, exclusive: bool, durable: bool) -> None:
    path = checked(base, suffix, missing=True)
    mkdir(base, path.parent.relative_to(base).as_posix())
    fd, name = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            if durable:
                os.fsync(stream.fileno())
        if exclusive:
            os.link(temporary, path)  # Never accept a reused version identity.
        else:
            os.replace(temporary, path)
        if durable:
            sync_paths(base, {path})
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def lock(base: Path, suffix: str) -> Iterator[None]:
    path = checked(base, suffix, missing=True)
    mkdir(base, path.parent.relative_to(base).as_posix())
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def inventory(base: Path, directory: Path) -> tuple[list[Path], list[Path]]:
    """Preflight a complete owned subtree before deleting anything."""

    checked(base, directory.relative_to(base).as_posix())
    files, directories = [], []
    for parent, children, names in os.walk(directory, followlinks=False):
        for name in [*children, *names]:
            path = checked(base, (Path(parent) / name).relative_to(base).as_posix())
            if path.is_dir():
                directories.append(path)
            else:
                files.append(path)
    return files, directories
