"""Explicit v4 initialization; never reinterpret an existing repository."""

from pathlib import Path

from asterstore.errors import (
    PublicationConflictError,
    RepositoryNotInitializedError,
    StoreCorruptionError,
)
from asterstore.metadata.protocol import StoreRecord, encode_store
from asterstore.storage import atomic_write, ensure_directory, file_lock, sync_control_files

from ._records import marker_path, read_store


def initialize_store(root: Path, desired: StoreRecord, *, durable: bool = True) -> StoreRecord:
    if type(durable) is not bool:
        raise TypeError("durable must be a boolean")
    try:
        current = read_store(root)
    except RepositoryNotInitializedError:
        pass
    else:
        if current != desired:
            raise PublicationConflictError("store identity or resource namespace already exists")
        if durable:
            sync_control_files(root, (marker_path(root),))
        return current
    control = marker_path(root).parent
    ensure_directory(control, durable=durable)
    with file_lock(control / ".init.lock", exclusive=True):
        try:
            current = read_store(root)
        except RepositoryNotInitializedError:
            if any(
                p.name != ".init.lock" and not p.name.startswith(".format.json.")
                for p in control.iterdir()
            ):
                raise StoreCorruptionError(
                    "control directory has no marker but is not empty"
                ) from None
            atomic_write(marker_path(root), encode_store(desired), durable=durable)
            current = desired
        if current != desired:
            raise PublicationConflictError("store identity or resource namespace already exists")
        if durable:
            sync_control_files(root, (marker_path(root),))
        return current
