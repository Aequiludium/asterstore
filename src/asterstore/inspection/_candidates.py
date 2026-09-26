"""Explicit residual diagnosis; never infer abandonment from age or delete files."""

import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from asterstore.metadata import ObjectRef, PreviewIssue
from asterstore.storage import (
    check_repository,
    control_directory,
    file_lock,
    scan_controls,
    scan_private_files,
)


@dataclass(frozen=True, slots=True)
class CandidateResidual:
    candidate_id: str
    dataset_id: str
    publication_id: str
    state: Literal["writing", "prepared", "conflict", "abandoned"]
    sealed: bool
    locations: tuple[str, ...]
    files: tuple[str, ...]
    total_bytes: int
    shared_objects: tuple[ObjectRef, ...]
    protects_shared: bool
    cleanup_state: Literal["not_started", "pending", "complete"]


@dataclass(frozen=True, slots=True)
class CandidateInspection:
    status: Literal["complete", "blocked"]
    candidates: tuple[CandidateResidual, ...]
    unknown_locations: tuple[str, ...]
    issues: tuple[PreviewIssue, ...]


def inspect_candidates(root: Path) -> CandidateInspection:
    check_repository(root, writable=True)
    with file_lock(control_directory(root) / "gc.lock", exclusive=True):
        check_repository(root, writable=True)
        inventory = scan_controls(root)
        issues = list(inventory.issues)
        committed = {
            item.candidate_id for item in inventory.publications + inventory.retired_publications
        }
        heads = {did: pid for did, pid in inventory.current}
        current = {
            item.publication.dataset.dataset_id: item
            for item in inventory.publications
            if heads.get(item.publication.dataset.dataset_id) == item.publication.publication_id
        }
        abandoned = {item.manifest.candidate_id for item in inventory.abandoned}
        records = inventory.candidates + tuple(item.manifest for item in inventory.abandoned)
        plans = {item.candidate_id: item for item in inventory.cleanups}
        progress = {item.candidate_id: item for item in inventory.cleanup_progress}
        rows = []
        for manifest in sorted(records, key=lambda item: item.candidate_id):
            cid = manifest.candidate_id
            if cid in committed:
                continue
            head = current.get(manifest.dataset.dataset_id)
            state: Literal["writing", "prepared", "conflict", "abandoned"]
            if cid in abandoned:
                state = "abandoned"
            elif (
                (head.generation if head is not None else 0) != manifest.expected_generation
                or any(
                    item.publication.dataset.dataset_id == manifest.dataset.dataset_id
                    and item.publication.publication_id == manifest.publication_id
                    for item in inventory.publications + inventory.retired_publications
                )
                or (head is not None and head.publication.dataset != manifest.dataset)
            ):
                state = "conflict"
            else:
                state = "writing" if manifest.keys is None else "prepared"
            files = scan_private_files(root, cid)
            issues.extend(files.issues)
            cleanup_state: Literal["not_started", "pending", "complete"] = "not_started"
            if cid in plans:
                cleanup_state = "complete" if progress[cid].complete else "pending"
                if progress[cid].complete and files.keys:
                    issues.append(
                        PreviewIssue(
                            f".asterstore/candidates/{cid}",
                            "private files remain or reappeared after cleanup completion",
                        )
                    )
                extra = set(files.keys) - set(plans[cid].keys)
                if extra:
                    issues.extend(
                        PreviewIssue(key, "file is outside the fixed cleanup plan")
                        for key in sorted(extra)
                    )
            rows.append(
                CandidateResidual(
                    cid,
                    manifest.dataset.dataset_id,
                    manifest.publication_id,
                    state,
                    manifest.keys is not None,
                    files.locations,
                    files.keys,
                    files.total_bytes,
                    tuple(ObjectRef(item.key) for item in manifest.reused_objects),
                    state != "abandoned" and manifest.keys is not None,
                    cleanup_state,
                )
            )
        known = {item.candidate_id for item in records}
        unknown = []
        objects = control_directory(root) / "objects"
        try:
            if not stat.S_ISDIR(objects.lstat().st_mode):
                raise ValueError("objects is not a real directory")
            for entry in sorted(objects.iterdir()):
                if entry.name not in known:
                    unknown.append(str(entry.relative_to(root)))
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            issues.append(PreviewIssue(str(objects.relative_to(root)), str(exc)))
        issues.extend(
            PreviewIssue(key, "object location lacks candidate evidence; not cleanup-authorized")
            for key in unknown
        )
        return CandidateInspection(
            "blocked" if issues else "complete", tuple(rows), tuple(unknown), tuple(issues)
        )


@dataclass(frozen=True, slots=True)
class Inspection:
    root: Path

    def candidates(self) -> CandidateInspection:
        return inspect_candidates(self.root)
