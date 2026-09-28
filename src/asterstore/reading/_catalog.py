"""Explicit administrative queries; normal binding never enumerates metadata."""

from pathlib import Path

from asterstore.errors import StoreCorruptionError
from asterstore.metadata.protocol import decode_declaration_record
from asterstore.storage import file_lock
from asterstore.storage.registry import read_store, token


def list_datasets(root: Path) -> tuple[str, ...]:
    """List committed dataset identities, reading heads only, never object files.

    Uncommitted first-publication directories have no head and are omitted. This
    is a discovery query, not an audit or an atomic snapshot of all dataset heads.
    """
    read_store(root)
    with file_lock(root / ".asterstore/gc.lock", exclusive=False):
        store = read_store(root)
        result = []
        for path in (root / ".asterstore/datasets").glob("*/current.json"):
            value = decode_declaration_record(path.read_bytes())
            try:
                value.check_store(store)
            except ValueError as exc:
                raise StoreCorruptionError(str(exc)) from exc
            identity = value.declaration.dataset_id
            if path.parent.name != token(identity):
                raise StoreCorruptionError("dataset path identity mismatch")
            result.append(identity)
        return tuple(sorted(result))
