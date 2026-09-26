"""Local write-side primitives. Ordinary binding does not use these operations."""

import os
import tempfile
from pathlib import Path

from asterstore.errors import StoreCorruptionError


def sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def ensure_directory(path: Path, *, durable: bool = False) -> None:
    try:
        path.mkdir()
    except FileNotFoundError:
        ensure_directory(path.parent, durable=durable)
        ensure_directory(path, durable=durable)
        return
    except FileExistsError:
        if not path.is_dir():
            raise
        if durable:
            sync_directory(path.parent)
        return
    if durable:
        sync_directory(path.parent)


def sync_file(path: Path) -> None:
    with path.open("rb") as stream:
        os.fsync(stream.fileno())


def _write(path: Path, data: bytes, *, durable: bool, exclusive: bool) -> None:
    ensure_directory(path.parent, durable=durable)
    with tempfile.NamedTemporaryFile(
        prefix=f".{path.name}.", dir=path.parent, delete=False
    ) as file:
        temporary = Path(file.name)
        try:
            file.write(data)
            file.flush()
            if durable:
                os.fsync(file.fileno())
        except BaseException:
            file.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        if exclusive:
            try:
                os.link(temporary, path)
            except FileExistsError:
                if path.read_bytes() != data:
                    raise StoreCorruptionError(
                        f"immutable control record differs: {path}"
                    ) from None
                if durable:
                    sync_file(path)
        else:
            os.replace(temporary, path)
        if durable:
            sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write(path: Path, data: bytes, *, durable: bool = True) -> None:
    """Replace one complete file; an error after replacement can mean it took effect."""
    _write(path, data, durable=durable, exclusive=False)


def immutable_write(path: Path, data: bytes, *, durable: bool = True) -> None:
    """Create a complete immutable record, or accept identical existing contents."""
    _write(path, data, durable=durable, exclusive=True)


def sync_control_files(root: Path, paths: tuple[Path, ...]) -> None:
    """Sync control records and their ancestor entries; never inspect data objects."""
    directories = {root.parent}
    for path in paths:
        if not path.is_relative_to(root):
            raise ValueError("control file must be inside the repository")
        sync_file(path)
        parent = path.parent
        while parent != root.parent:
            directories.add(parent)
            parent = parent.parent
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        sync_directory(directory)
