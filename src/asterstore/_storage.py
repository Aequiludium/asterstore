"""Durable POSIX primitives, scoped to explicitly addressed paths."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .errors import StoreCorruptionError


def checked(path: Path, *, missing: bool = False) -> Path:
    path = path.absolute()
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            if missing:
                continue
            raise
        if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise StoreCorruptionError(f"unsafe file: {current}")
        if current != path and not stat.S_ISDIR(mode):
            raise StoreCorruptionError(f"not a directory: {current}")
    return path


def sync_directory(path: Path) -> None:
    checked(path)
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def mkdir(path: Path) -> None:
    checked(path, missing=True)
    if path.exists():
        if not path.is_dir():
            raise StoreCorruptionError(f"not a directory: {path}")
        return
    mkdir(path.parent)
    path.mkdir()
    sync_directory(path.parent)


def sync_file(path: Path) -> None:
    file(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def file(path: Path) -> Path:
    checked(path)
    if not path.is_file() or path.stat().st_nlink != 1:
        raise StoreCorruptionError(f"expected an unaliased regular file: {path}")
    return path


def read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(file(path).read_bytes())
        if not isinstance(value, dict):
            raise ValueError("expected object")
        return value
    except (ValueError, TypeError) as exc:
        raise StoreCorruptionError(f"invalid JSON: {path}") from exc


def write(path: Path, value: dict[str, Any]) -> None:
    data = (
        json.dumps(value, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    checked(path, missing=True)
    mkdir(path.parent)
    fd, name = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def copy(source: Path, target: Path) -> None:
    file(source)
    checked(target, missing=True)
    mkdir(target.parent)
    shutil.copyfile(source, target)
    sync_file(target)
    sync_directory(target.parent)


def install(source: Path, target: Path) -> None:
    file(source)
    checked(target, missing=True)
    mkdir(target.parent)
    os.replace(source, target)
    sync_directory(target.parent)
    if source.parent != target.parent:
        sync_directory(source.parent)


def unlink(path: Path) -> None:
    checked(path, missing=True)
    if path.exists():
        file(path)
        path.unlink()
    # Sync even on retry after an interrupted unlink.
    if path.parent.exists():
        sync_directory(path.parent)


def remove_tree(path: Path) -> None:
    checked(path)
    # Preflight owned temporary material; never traverse the data directory.
    for parent, dirs, files in os.walk(path, followlinks=False):
        for name in (*dirs, *files):
            checked(Path(parent) / name)
    shutil.rmtree(path)
    sync_directory(path.parent)


@contextmanager
def lock(path: Path) -> Iterator[None]:
    checked(path, missing=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)
