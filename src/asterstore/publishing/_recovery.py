"""Explicit candidate status; no implicit recovery on reads."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from asterstore.errors import StoreCorruptionError
from asterstore.metadata import AbandonedCandidate, PublishedRecord, RetiredRecord
from asterstore.storage import (
    check_repository,
    control_directory,
    dataset_directory,
    file_lock,
    find_publication_record,
    read_candidate_state,
    read_current,
)


@dataclass(frozen=True, slots=True)
class CandidateStatus:
    candidate_id: str
    dataset_id: str
    publication_id: str
    state: Literal[
        "writing", "prepared", "conflict", "current", "historical", "retired", "abandoned"
    ]
    current_generation: int


def candidate_status(root: Path, candidate_id: str) -> CandidateStatus:
    """Observe metadata under commit coordination; never inspect data contents."""
    version = check_repository(root)
    with file_lock(control_directory(root) / "gc.lock", exclusive=False):
        version = check_repository(root)
        record = read_candidate_state(root, candidate_id, format_version=version)
        manifest = record.manifest if isinstance(record, AbandonedCandidate) else record
        dataset_id = manifest.dataset.dataset_id
        with file_lock(dataset_directory(root, dataset_id) / "commit.lock", exclusive=True):
            # A writer can seal and commit while we wait for the dataset lock.
            record = read_candidate_state(root, candidate_id, format_version=version)
            manifest = record.manifest if isinstance(record, AbandonedCandidate) else record
            current = read_current(root, dataset_id, format_version=version)
            committed = find_publication_record(
                root, dataset_id, manifest.publication_id, format_version=version
            )
            generation = current.generation if current is not None else 0
            state: Literal[
                "writing", "prepared", "conflict", "current", "historical", "retired", "abandoned"
            ]
            if isinstance(record, AbandonedCandidate):
                if committed is not None and committed.candidate_id == candidate_id:
                    raise StoreCorruptionError("committed candidate was marked abandoned")
                state = "abandoned"
            elif isinstance(committed, RetiredRecord) and committed.candidate_id == candidate_id:
                if (
                    committed.dataset != manifest.dataset
                    or committed.generation != manifest.expected_generation + 1
                    or manifest.keys is None
                ):
                    raise StoreCorruptionError("retired candidate evidence disagrees")
                state = "retired"
            elif committed is not None and committed.candidate_id == candidate_id:
                if manifest.keys is None or committed != PublishedRecord(
                    manifest.expected_generation + 1,
                    candidate_id,
                    manifest.publication(),
                    format_version=version,
                ):
                    raise StoreCorruptionError("committed candidate and control record disagree")
                state = "current" if committed == current else "historical"
            elif (
                committed is not None
                or generation != manifest.expected_generation
                or (current is not None and current.publication.dataset != manifest.dataset)
            ):
                state = "conflict"
            else:
                state = "writing" if manifest.keys is None else "prepared"
            return CandidateStatus(
                candidate_id, dataset_id, manifest.publication_id, state, generation
            )
