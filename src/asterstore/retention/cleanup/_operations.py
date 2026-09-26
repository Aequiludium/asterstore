"""Cleanup of abandoned private files, bounded by an immutable snapshot."""

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from asterstore.errors import CandidateStateError, StoreCorruptionError
from asterstore.metadata import (
    AbandonedCandidate,
    CleanupPlan,
    CleanupProgress,
    PreviewIssue,
    encode_cleanup_plan,
    encode_cleanup_progress,
    validate_candidate_id,
)
from asterstore.storage import (
    atomic_write,
    candidate_directory,
    check_repository,
    control_directory,
    delete_candidate_file,
    file_lock,
    immutable_write,
    read_candidate_state,
    scan_controls,
    scan_private_files,
    sync_control_files,
)


@dataclass(frozen=True, slots=True)
class CandidateCleanupResult:
    candidate_id: str
    status: Literal["complete", "blocked"]
    planned: bool
    deleted_files: tuple[str, ...] = ()
    missing_files: tuple[str, ...] = ()
    remaining_files: tuple[str, ...] = ()
    issues: tuple[PreviewIssue, ...] = ()


def _result(plan: CleanupPlan, state: CleanupProgress) -> CandidateCleanupResult:
    done = set(state.deleted + state.missing)
    return CandidateCleanupResult(
        plan.candidate_id,
        "complete" if state.complete else "blocked",
        True,
        state.deleted,
        state.missing,
        tuple(key for key in plan.keys if key not in done),
        state.issues,
    )


def cleanup_candidate(root: Path, candidate_id: str) -> CandidateCleanupResult:
    validate_candidate_id(candidate_id)
    check_repository(root, writable=True)
    with file_lock(control_directory(root) / "gc.lock", exclusive=True):
        check_repository(root, writable=True)
        inventory = scan_controls(root)
        if inventory.issues:
            return CandidateCleanupResult(
                candidate_id,
                "blocked",
                any(item.candidate_id == candidate_id for item in inventory.cleanups),
                issues=inventory.issues,
            )
        record = read_candidate_state(root, candidate_id)
        if not isinstance(record, AbandonedCandidate):
            raise CandidateStateError("explicit abandonment is required before cleanup")
        directory = candidate_directory(root, candidate_id)
        plan = next(
            (item for item in inventory.cleanups if item.candidate_id == candidate_id), None
        )
        sync_control_files(root, inventory.control_paths)
        if plan is None:
            files = scan_private_files(root, candidate_id)
            if files.issues:
                return CandidateCleanupResult(candidate_id, "blocked", False, issues=files.issues)
            plan = CleanupPlan(candidate_id, files.keys)
            immutable_write(directory / "cleanup-plan.json", encode_cleanup_plan(plan))
            previous = CleanupProgress(candidate_id)
        else:
            previous = next(
                item for item in inventory.cleanup_progress if item.candidate_id == candidate_id
            )
        if previous.complete:
            return _result(plan, previous)
        state = replace(previous, issues=())
        try:
            # This also covers a plan installed before an interrupted response.
            sync_control_files(root, (directory / "state.json", directory / "cleanup-plan.json"))
            deleted = set(previous.deleted)
            missing: set[str] = set()
            for key in plan.keys:
                if delete_candidate_file(root, candidate_id, key):
                    deleted.add(key)
                elif key not in deleted:
                    missing.add(key)
                state = CleanupProgress(
                    candidate_id, False, tuple(sorted(deleted)), tuple(sorted(missing))
                )
            state = replace(state, complete=True)
            atomic_write(directory / "cleanup-progress.json", encode_cleanup_progress(state))
        except (OSError, StoreCorruptionError) as exc:
            state = replace(
                state,
                complete=False,
                issues=(PreviewIssue(str(directory.relative_to(root)), str(exc)),),
            )
            atomic_write(directory / "cleanup-progress.json", encode_cleanup_progress(state))
        return _result(plan, state)
