"""Explicit metadata-only managed candidate status."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from asterstore.errors import PublicationNotFoundError, StoreCorruptionError
from asterstore.metadata.protocol import decode_declaration_record
from asterstore.storage import file_lock
from asterstore.storage.registry import (
    find_record,
    managed_directory,
    read_abandoned,
    read_head,
    read_managed_request,
    read_retired,
    read_store,
)


@dataclass(frozen=True, slots=True)
class ManagedCandidateStatus:
    operation_id: str
    state: Literal[
        "writing", "prepared", "conflict", "current", "historical", "retired", "abandoned"
    ]


def managed_status(root: Path, operation_id: str) -> ManagedCandidateStatus:
    read_store(root)
    with file_lock(root / ".asterstore/gc.lock", exclusive=False):
        store = read_store(root)
        request = read_managed_request(root, store, operation_id)
        if request is None:
            raise PublicationNotFoundError("managed candidate does not exist")
        if read_abandoned(root, store, operation_id) is not None:
            return ManagedCandidateStatus(operation_id, "abandoned")
        try:
            sealed = decode_declaration_record(
                (managed_directory(root, operation_id) / "sealed.json").read_bytes()
            )
        except FileNotFoundError:
            sealed = None
        if sealed is not None and sealed != request.declaration_record(sealed.declaration.files):
            raise StoreCorruptionError("candidate seal and request disagree")
        current = read_head(root, store, request.dataset_id)
        committed = find_record(root, store, request.dataset_id, request.publication_id)
        state: Literal[
            "writing", "prepared", "conflict", "current", "historical", "retired", "abandoned"
        ]
        if committed is not None and committed.operation_id == operation_id:
            if sealed != committed:
                raise StoreCorruptionError("committed candidate lacks matching seal")
            state = (
                "retired"
                if read_retired(root, store, committed) is not None
                else "current"
                if current == committed
                else "historical"
            )
        elif (
            committed is not None
            or (0 if current is None else current.generation) != request.expected_generation
            or (
                current is not None
                and current.declaration.capabilities
                != request.declaration_record(current.declaration.files).declaration.capabilities
            )
        ):
            state = "conflict"
        else:
            state = "writing" if sealed is None else "prepared"
        return ManagedCandidateStatus(operation_id, state)
