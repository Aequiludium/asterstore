"""Internal filesystem primitives; no I/O on import."""

from ._deletion import delete_owned_file
from ._files import (
    atomic_write,
    ensure_directory,
    immutable_write,
    sync_control_files,
    sync_directory,
    sync_file,
)
from ._layout import absolute_root
from ._locks import file_lock

__all__ = [
    "delete_owned_file",
    "atomic_write",
    "ensure_directory",
    "immutable_write",
    "sync_control_files",
    "sync_directory",
    "sync_file",
    "absolute_root",
    "file_lock",
]
