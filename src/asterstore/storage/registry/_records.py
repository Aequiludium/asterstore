"""control paths and reads. Object data is never opened here."""

from hashlib import sha256
from pathlib import Path

from asterstore.errors import (
    RepositoryNotInitializedError,
    StoreCorruptionError,
)
from asterstore.metadata.identity import MAX_IDENTIFIER_BYTES, validate_identifier
from asterstore.metadata.protocol import (
    DeclarationRecord,
    ObjectRecord,
    StoreRecord,
    decode_declaration_record,
    decode_object_record,
    decode_store,
)


def token(value: str) -> str:
    validate_identifier(value, "control identity", max_bytes=MAX_IDENTIFIER_BYTES)
    return sha256(value.encode("utf-8")).hexdigest()


def marker_path(root: Path) -> Path:
    return root / ".asterstore/format.json"


def head_path(root: Path, dataset_id: str) -> Path:
    return root / ".asterstore/datasets" / token(dataset_id) / "current.json"


def publication_path(root: Path, dataset_id: str, publication_id: str) -> Path:
    return head_path(root, dataset_id).parent / "history" / (token(publication_id) + ".json")


def operation_path(root: Path, operation_id: str) -> Path:
    return root / ".asterstore/registrations" / (token(operation_id) + ".json")


def object_record_path(root: Path, object_id: str) -> Path:
    return root / ".asterstore/object-records" / (token(object_id) + ".json")


def read_store(root: Path) -> StoreRecord:
    try:
        data = marker_path(root).read_bytes()
    except FileNotFoundError as exc:
        raise RepositoryNotInitializedError(f"repository is not initialized: {root}") from exc
    return decode_store(data)


def check_record(value: DeclarationRecord, store: StoreRecord) -> DeclarationRecord:
    try:
        value.check_store(store)
    except ValueError as exc:
        raise StoreCorruptionError(str(exc)) from exc
    return value


def read_head(root: Path, store: StoreRecord, dataset_id: str) -> DeclarationRecord | None:
    try:
        data = head_path(root, dataset_id).read_bytes()
    except FileNotFoundError:
        return None
    value = check_record(decode_declaration_record(data), store)
    if value.declaration.dataset_id != dataset_id:
        raise StoreCorruptionError("current identity differs from its path")
    return value


def find_record(
    root: Path, store: StoreRecord, dataset_id: str, publication_id: str
) -> DeclarationRecord | None:
    head = read_head(root, store, dataset_id)
    if head is not None and head.declaration.publication_id == publication_id:
        return head
    try:
        data = publication_path(root, dataset_id, publication_id).read_bytes()
    except FileNotFoundError:
        return None
    value = check_record(decode_declaration_record(data), store)
    if (
        value.declaration.dataset_id != dataset_id
        or value.declaration.publication_id != publication_id
    ):
        raise StoreCorruptionError("history identity differs from its path")
    if head is None or value.generation >= head.generation:
        raise StoreCorruptionError("history has no later current record")
    return value


def read_operation(root: Path, store: StoreRecord, operation_id: str) -> DeclarationRecord | None:
    try:
        data = operation_path(root, operation_id).read_bytes()
    except FileNotFoundError:
        return None
    value = check_record(decode_declaration_record(data), store)
    if value.operation_id != operation_id:
        raise StoreCorruptionError("operation identity differs from its path")
    return value


def read_object_record(root: Path, store: StoreRecord, object_id: str) -> ObjectRecord | None:
    try:
        data = object_record_path(root, object_id).read_bytes()
    except FileNotFoundError:
        return None
    value = decode_object_record(data)
    if (
        value.store_id != store.store_id
        or value.object_id != object_id
        or value.locator.resource_id not in store.resource_ids
        or (
            (value.creator_operation_id is not None)
            != (value.locator.resource_id == store.managed_resource_id)
        )
    ):
        raise StoreCorruptionError("object record identity or resource differs from its store")
    return value
