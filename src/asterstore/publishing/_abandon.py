"""Permanently release an uncommitted candidate; never implicitly delete files."""

from pathlib import Path

from asterstore.errors import CandidateStateError, StoreCorruptionError
from asterstore.metadata import AbandonedCandidate, encode_abandoned, validate_candidate_id
from asterstore.storage import (
    atomic_write,
    candidate_directory,
    check_repository,
    control_directory,
    file_lock,
    read_candidate_state,
    read_current,
    scan_controls,
    sync_control_files,
)

from ._recovery import CandidateStatus


def abandon(root: Path, candidate_id: str) -> CandidateStatus:
    validate_candidate_id(candidate_id)
    check_repository(root, writable=True)
    with file_lock(control_directory(root) / "gc.lock", exclusive=True):
        check_repository(root, writable=True)
        inventory = scan_controls(root)
        if inventory.issues:
            raise StoreCorruptionError(
                "cannot abandon with incomplete control evidence: "
                + "; ".join(issue.message for issue in inventory.issues)
            )
        state = read_candidate_state(root, candidate_id)
        manifest = state.manifest if isinstance(state, AbandonedCandidate) else state
        if any(
            record.candidate_id == candidate_id
            for record in inventory.publications + inventory.retired_publications
        ):
            raise CandidateStateError(
                "committed candidates require publication retirement, not abandonment"
            )
        sync_control_files(root, inventory.control_paths)
        if not isinstance(state, AbandonedCandidate):
            atomic_write(
                candidate_directory(root, candidate_id) / "state.json",
                encode_abandoned(AbandonedCandidate(manifest)),
            )
        current = read_current(root, manifest.dataset.dataset_id)
        return CandidateStatus(
            candidate_id,
            manifest.dataset.dataset_id,
            manifest.publication_id,
            "abandoned",
            current.generation if current is not None else 0,
        )
