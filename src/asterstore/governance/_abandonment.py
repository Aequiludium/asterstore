"""Persist the control evidence needed to interpret irreversible abandonment."""

from pathlib import Path

from asterstore.errors import StoreCorruptionError
from asterstore.metadata.membership import Object
from asterstore.metadata.protocol import (
    DeclarationRecord,
    ManagedRequest,
    StoreRecord,
    decode_declaration_record,
)
from asterstore.storage import sync_control_files
from asterstore.storage.registry import (
    managed_directory,
    object_record_path,
    operation_path,
    read_object_record,
    read_operation,
)

from ._inventory import checked, control_bytes, entries


def sync_candidate_evidence(root: Path, store: StoreRecord, request: ManagedRequest) -> None:
    """Caller holds writer and root locks; missing partial-commit records are allowed.

    This deliberately does not require full inventory: abandonment must remain
    available after interruption before the initial protection write succeeds.
    Existing candidate/creator evidence is checked and synced; data bytes are not.
    """
    directory = managed_directory(root, request.operation_id)
    children = set(entries(directory))
    paths = {root / ".asterstore/format.json", directory / "request.json"}
    records: dict[str, DeclarationRecord] = {}
    objects: dict[str, Object] = {}
    for name in ("sealed.json", "protection.json"):
        path = directory / name
        if path not in children:
            continue
        record = checked(decode_declaration_record(control_bytes(path, paths)), store)
        if record != request.declaration_record(record.declaration.files):
            raise StoreCorruptionError("candidate evidence differs from abandonment request")
        records[name] = record
        for obj in record.declaration.files.objects:
            if obj.object_id in objects and objects[obj.object_id] != obj:
                raise StoreCorruptionError("candidate evidence has conflicting objects")
            objects[obj.object_id] = obj
    operation = read_operation(root, store, request.operation_id)
    if operation is not None:
        if operation != records.get("sealed.json"):
            raise StoreCorruptionError("managed operation lacks matching sealed evidence")
        paths.add(operation_path(root, request.operation_id))
    for obj in objects.values():
        proof = read_object_record(root, store, obj.object_id)
        if proof is not None:
            if proof.locator != obj.locator or proof.creator_operation_id is None:
                raise StoreCorruptionError("candidate object proof conflicts with its declaration")
            paths.add(object_record_path(root, obj.object_id))
    sync_control_files(root, tuple(paths))
