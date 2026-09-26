"""Exact managed-file deletion. No recursive removal or symlink traversal."""

import stat
from pathlib import Path

from asterstore.errors import StoreCorruptionError
from asterstore.metadata.identity import validate_object_key

from ._files import sync_directory


def _delete_regular(root: Path, key: str) -> bool:
    path = root / key
    ancestors = [
        root,
        *[
            root.joinpath(*path.relative_to(root).parts[:i])
            for i in range(1, len(path.relative_to(root).parts))
        ],
    ]
    previous = root.parent
    for directory in ancestors:
        try:
            mode = directory.lstat().st_mode
        except FileNotFoundError:
            sync_directory(previous)
            return False
        if not stat.S_ISDIR(mode):
            raise StoreCorruptionError(f"object parent is not a real directory: {directory}")
        previous = directory
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        sync_directory(path.parent)
        return False
    if not stat.S_ISREG(mode):
        raise StoreCorruptionError(f"refusing to delete a non-regular object: {key}")
    path.unlink()
    sync_directory(path.parent)
    return True


def delete_owned_file(root: Path, relative_path: str) -> bool:
    """Exact file deletion; caller must first prove ownership and persist retirement."""
    validate_object_key(relative_path)
    return _delete_regular(root, relative_path)
