"""Managed control paths and creator evidence; no ownership inferred from data presence."""

from pathlib import Path

from asterstore.errors import StoreCorruptionError
from asterstore.metadata.protocol import (
    ManagedRequest,
    StoreRecord,
    decode_managed_request,
)

from ._records import token


def managed_directory(root: Path, operation_id: str) -> Path:
    return root / ".asterstore/managed-candidates" / token(operation_id)


def managed_data_root(root: Path) -> Path:
    return root / ".asterstore/managed-data"


def read_managed_request(
    root: Path, store: StoreRecord, operation_id: str
) -> ManagedRequest | None:
    try:
        data = (managed_directory(root, operation_id) / "request.json").read_bytes()
    except FileNotFoundError:
        return None
    value = decode_managed_request(data)
    if (
        value.store_id != store.store_id
        or value.operation_id != operation_id
        or store.managed_resource_id is None
    ):
        raise StoreCorruptionError("managed request identity or feature mismatch")
    return value
