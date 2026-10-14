"""Explicit metadata-only managed candidate status."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from asterstore.errors import PublicationNotFoundError, StoreCorruptionError
from asterstore.metadata.protocol import (
    DatasetTombstone,
    DeclarationRecord,
    ManagedRequest,
    decode_declaration_record,
)
from asterstore.storage import file_lock
from asterstore.storage._archive import read_control
from asterstore.storage.registry import (
    find_record,
    managed_directory,
    read_abandoned,
    read_head_state,
    read_managed_request,
    read_retired,
    read_store,
)


@dataclass(frozen=True, slots=True)
class CandidateStatus:
    operation_id: str
    state: Literal[
        "writing",
        "prepared",
        "conflict",
        "current",
        "historical",
        "retired",
        "abandoned",
        "decommissioned",
    ]
    store_id: str
    dataset_id: str
    publication_id: str
    expected_generation: int


def candidate_status(root: Path, operation_id: str) -> CandidateStatus:
    read_store(root)
    with file_lock(root / ".asterstore/gc.lock", exclusive=False):
        store = read_store(root)
        request = read_managed_request(root, store, operation_id)
        if request is None:
            raise PublicationNotFoundError("managed candidate does not exist")
        if read_abandoned(root, store, operation_id) is not None:
            return CandidateStatus(
                operation_id,
                "abandoned",
                request.store_id,
                request.dataset_id,
                request.publication_id,
                request.expected_generation,
            )
        try:
            sealed = decode_declaration_record(
                read_control(managed_directory(root, operation_id) / "sealed.json")
            )
        except FileNotFoundError:
            sealed = None
        if sealed is not None and sealed != request.declaration_record(sealed.declaration.files):
            raise StoreCorruptionError("candidate seal and request disagree")
        head = read_head_state(root, store, request.dataset_id)
        current = None if isinstance(head, DatasetTombstone) else head
        committed = find_record(root, store, request.dataset_id, request.publication_id)
        return status_from_records(
            request,
            current=current,
            decommissioned=isinstance(head, DatasetTombstone),
            committed=committed,
            sealed=sealed,
            abandoned=False,
            retired=(
                committed is not None
                and committed.operation_id == operation_id
                and read_retired(root, store, committed) is not None
            ),
        )


def status_from_records(
    request: ManagedRequest,
    *,
    current: DeclarationRecord | None,
    committed: DeclarationRecord | None,
    sealed: DeclarationRecord | None,
    abandoned: bool,
    retired: bool,
    decommissioned: bool = False,
) -> CandidateStatus:
    """Derive the same state for a single query and a coordinated inventory."""
    state: Literal[
        "writing",
        "prepared",
        "conflict",
        "current",
        "historical",
        "retired",
        "abandoned",
        "decommissioned",
    ]
    if abandoned:
        state = "abandoned"
    elif committed is not None and committed.operation_id == request.operation_id:
        if sealed != committed:
            raise StoreCorruptionError("committed candidate lacks matching seal")
        state = "retired" if retired else "current" if current == committed else "historical"
    elif decommissioned:
        state = "decommissioned"
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
    return CandidateStatus(
        request.operation_id,
        state,
        request.store_id,
        request.dataset_id,
        request.publication_id,
        request.expected_generation,
    )
