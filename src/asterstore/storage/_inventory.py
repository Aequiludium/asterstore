"""Coordinated control-only inventory. Never enumerate or probe object files."""

import stat
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from string import hexdigits
from typing import TypeVar

from asterstore.errors import StoreCorruptionError
from asterstore.metadata import (
    CURRENT_FORMAT_VERSION,
    AbandonedCandidate,
    CandidateManifest,
    CleanupPlan,
    CleanupProgress,
    CollectionPlan,
    CollectionProgress,
    PreviewIssue,
    PublishedRecord,
    ReferenceRecord,
    RetiredRecord,
    decode_candidate_state,
    decode_cleanup_plan,
    decode_cleanup_progress,
    decode_collection_plan,
    decode_collection_progress,
    decode_history,
    decode_publication,
    decode_reference,
)

from ._layout import (
    candidate_directory,
    control_directory,
    dataset_directory,
    history_path,
    reference_path,
)

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ControlInventory:
    publications: tuple[PublishedRecord, ...]
    current: tuple[tuple[str, str], ...]
    candidates: tuple[CandidateManifest, ...]
    references: tuple[ReferenceRecord, ...]
    dataset_ids: tuple[str, ...]
    issues: tuple[PreviewIssue, ...]
    pending_operations: tuple[str, ...]
    retired: tuple[RetiredRecord, ...]
    retired_publications: tuple[PublishedRecord, ...]
    plans: tuple[CollectionPlan, ...]
    progress: tuple[CollectionProgress, ...]
    control_paths: tuple[Path, ...]
    abandoned: tuple[AbandonedCandidate, ...]
    cleanups: tuple[CleanupPlan, ...]
    cleanup_progress: tuple[CleanupProgress, ...]


def scan_controls(root: Path) -> ControlInventory:
    """Caller holds the repository exclusive GC lock and has checked the current writable format."""
    control = control_directory(root)
    issues: list[PreviewIssue] = []
    control_paths = [control / "format.json"]

    def issue(path: Path, message: str) -> None:
        issues.append(PreviewIssue(str(path.relative_to(root)), message))

    def children(path: Path) -> list[Path]:
        try:
            if not stat.S_ISDIR(path.lstat().st_mode):
                issue(path, "control directory is not a real directory")
                return []
            return sorted(path.iterdir())
        except FileNotFoundError:
            return []
        except OSError as exc:
            issue(path, str(exc))
            return []

    def names(path: Path, allowed: set[str], temporaries: tuple[str, ...] = ()) -> list[Path]:
        entries = children(path)
        for entry in entries:
            if entry.name not in allowed and not entry.name.startswith(temporaries):
                issue(entry, "unexpected control entry")
        return entries

    def read(path: Path, decode: Callable[[bytes], T]) -> T | None:
        try:
            if not stat.S_ISREG(path.lstat().st_mode):
                issue(path, "control record is not a regular file")
                return None
            result = decode(path.read_bytes())
            control_paths.append(path)
            return result
        except (OSError, StoreCorruptionError) as exc:
            issue(path, str(exc))
            return None

    names(
        control,
        {
            "format.json",
            ".init.lock",
            "gc.lock",
            "locks",
            "objects",
            "datasets",
            "candidates",
            "references",
            "collections",
        },
        (".format.json.",),
    )
    publications: dict[tuple[str, str], PublishedRecord] = {}
    retired: dict[tuple[str, str], RetiredRecord] = {}
    currents: dict[str, str] = {}
    candidates: dict[str, CandidateManifest] = {}
    abandoned: list[AbandonedCandidate] = []
    cleanups: list[CleanupPlan] = []
    cleanup_progress: list[CleanupProgress] = []
    references: list[ReferenceRecord] = []
    dataset_ids: set[str] = set()

    def add(record: PublishedRecord, path: Path) -> None:
        key = (record.publication.dataset.dataset_id, record.publication.publication_id)
        previous = publications.get(key)
        if previous is not None and previous != record:
            issue(path, "conflicting records for one publication identity")
        publications[key] = record
        dataset_ids.add(key[0])

    def publication(data: bytes) -> PublishedRecord:
        return decode_publication(data, expected_version=CURRENT_FORMAT_VERSION)

    def candidate(data: bytes) -> CandidateManifest | AbandonedCandidate:
        return decode_candidate_state(data, expected_version=CURRENT_FORMAT_VERSION)

    def is_hex(value: str, length: int) -> bool:
        return len(value) == length and all(c in hexdigits and not c.isupper() for c in value)

    for directory in children(control / "datasets"):
        if not is_hex(directory.name, 64):
            issue(directory, "invalid dataset directory name")
            continue
        entries = names(directory, {"current.json", "commit.lock", "history"}, (".current.json.",))
        head = None
        if any(entry.name == "current.json" for entry in entries):
            head = read(directory / "current.json", publication)
            if head is not None:
                did = head.publication.dataset.dataset_id
                if dataset_directory(root, did) != directory:
                    issue(directory, "dataset identity differs from its directory")
                else:
                    currents[did] = head.publication.publication_id
                    add(head, directory / "current.json")
        history_entries = children(directory / "history")
        for path in history_entries:
            if path.name.startswith(".") and ".json." in path.name:
                continue
            if path.suffix != ".json" or not is_hex(path.stem, 64):
                issue(path, "invalid history record name")
                continue
            record = read(path, decode_history)
            if record is None:
                continue
            did = (
                record.dataset if isinstance(record, RetiredRecord) else record.publication.dataset
            ).dataset_id
            pid = (
                record.publication_id
                if isinstance(record, RetiredRecord)
                else record.publication.publication_id
            )
            if history_path(root, did, pid) != path:
                issue(path, "history identity differs from its path")
                continue
            if isinstance(record, RetiredRecord):
                retired[(did, pid)] = record
                dataset_ids.add(did)
                if (did, pid) in publications:
                    issue(path, "current publication is retired")
            else:
                add(record, path)
        if head is None and history_entries:
            issue(directory, "history exists without a valid current publication")

    for directory in children(control / "candidates"):
        if not is_hex(directory.name, 32):
            issue(directory, "invalid candidate directory name")
            continue
        entries = names(
            directory,
            {"state.json", "files", "cleanup-plan.json", "cleanup-progress.json"},
            (".state.json.", ".cleanup-plan.json.", ".cleanup-progress.json."),
        )
        state_record = read(directory / "state.json", candidate)
        if state_record is None:
            continue
        candidate_manifest = (
            state_record.manifest if isinstance(state_record, AbandonedCandidate) else state_record
        )
        if candidate_manifest.candidate_id != directory.name:
            issue(directory, "candidate identity differs from its path")
            continue
        if isinstance(state_record, AbandonedCandidate):
            abandoned.append(state_record)
        else:
            candidates[candidate_manifest.candidate_id] = candidate_manifest
        dataset_ids.add(candidate_manifest.dataset.dataset_id)
        entry_names = {entry.name for entry in entries}
        if "cleanup-plan.json" in entry_names:
            cleanup_plan = read(directory / "cleanup-plan.json", decode_cleanup_plan)
            if (
                cleanup_plan is None
                or cleanup_plan.candidate_id != directory.name
                or not isinstance(state_record, AbandonedCandidate)
            ):
                issue(directory, "cleanup plan requires matching abandoned candidate")
                continue
            cleanups.append(cleanup_plan)
            progress_record = None
            if "cleanup-progress.json" in entry_names:
                progress_record = read(directory / "cleanup-progress.json", decode_cleanup_progress)
            if progress_record is None:
                progress_record = CleanupProgress(directory.name)
            handled = set(progress_record.deleted + progress_record.missing)
            if (
                progress_record.candidate_id != directory.name
                or not handled <= set(cleanup_plan.keys)
                or (progress_record.complete and handled != set(cleanup_plan.keys))
            ):
                issue(directory, "cleanup progress exceeds its plan")
                progress_record = CleanupProgress(directory.name)
            cleanup_progress.append(progress_record)
        elif "cleanup-progress.json" in entry_names:
            issue(directory, "cleanup progress has no plan")

    for path in children(control / "references"):
        if path.name.startswith(".") and ".json." in path.name:
            continue
        if path.suffix != ".json" or not is_hex(path.stem, 64):
            issue(path, "invalid reference record name")
            continue
        reference_record = read(path, decode_reference)
        if reference_record is None:
            continue
        if reference_path(root, reference_record.reference.name) != path:
            issue(path, "reference identity differs from its path")
            continue
        references.append(reference_record)
        if reference_record.state == "active":
            key = (reference_record.reference.dataset_id, reference_record.reference.publication_id)
            if key not in publications:
                issue(path, "active reference has no committed target")

    plans: dict[str, CollectionPlan] = {}
    progress: dict[str, CollectionProgress] = {}
    pending: list[str] = []
    for directory in children(control / "collections"):
        if not is_hex(directory.name, 32):
            issue(directory, "invalid collection operation identity")
            pending.append(directory.name)
            continue
        entries = names(
            directory, {"plan.json", "progress.json"}, (".plan.json.", ".progress.json.")
        )
        if not any(entry.name == "plan.json" for entry in entries):
            if any(entry.name == "progress.json" for entry in entries):
                issue(directory, "collection progress has no plan")
                pending.append(directory.name)
            # Empty/pre-plan temporary directories have no authorized mutations.
            continue
        plan = read(directory / "plan.json", decode_collection_plan)
        if plan is None or plan.operation_id != directory.name:
            issue(directory, "collection plan is invalid or has a different identity")
            pending.append(directory.name)
            continue
        plans[plan.operation_id] = plan
        state = None
        if any(entry.name == "progress.json" for entry in entries):
            state = read(directory / "progress.json", decode_collection_progress)
            if state is None or state.operation_id != directory.name:
                issue(directory, "collection progress is invalid or has a different identity")
                state = None
        if state is None:
            state = CollectionProgress(plan.operation_id)
        progress[plan.operation_id] = state
        if state.state != "complete":
            pending.append(plan.operation_id)

    originals = dict(publications)
    for identity, tombstone in retired.items():
        manifest = candidates.get(tombstone.candidate_id)
        path = history_path(root, *identity)
        if (
            manifest is None
            or manifest.keys is None
            or manifest.dataset != tombstone.dataset
            or manifest.publication_id != tombstone.publication_id
            or manifest.expected_generation + 1 != tombstone.generation
        ):
            issue(path, "retirement and candidate evidence disagree or are missing")
            continue
        original = PublishedRecord(
            tombstone.generation, tombstone.candidate_id, manifest.publication()
        )
        plan = plans.get(tombstone.collection_id)
        if plan is None or original not in plan.targets:
            issue(path, "retirement has no matching durable collection plan")
        originals[identity] = original

    for operation_id, plan in plans.items():
        path = control / "collections" / operation_id / "plan.json"
        for target in plan.targets:
            identity = (target.publication.dataset.dataset_id, target.publication.publication_id)
            if originals.get(identity) != target:
                issue(path, "collection target differs from committed evidence")
        eligible = {obj.key for target in plan.targets for obj in target.publication.objects}
        eligible.update(
            obj.key
            for identity, record in originals.items()
            if identity in retired and identity[0] in plan.policy.datasets
            for obj in record.publication.objects
        )
        keys = {obj.key for obj in plan.objects}
        if not keys <= eligible or any(did not in dataset_ids for did in plan.policy.datasets):
            issue(path, "collection objects or policy lack committed scope evidence")
        state = progress[operation_id]
        handled = set(state.deleted + state.missing + state.protected)
        target_ids = {target.candidate_id for target in plan.targets}
        actual_retired = {
            item.candidate_id for item in retired.values() if item.collection_id == operation_id
        }
        if (
            not handled <= keys
            or not set(state.retired) <= target_ids & actual_retired
            or (
                state.state == "complete"
                and (handled != keys or set(state.retired) != actual_retired)
            )
        ):
            issue(path, "collection progress exceeds its plan or retirement evidence")

    by_dataset: dict[str, list[PublishedRecord]] = defaultdict(list)
    for record in originals.values():
        by_dataset[record.publication.dataset.dataset_id].append(record)
        path = candidate_directory(root, record.candidate_id) / "state.json"
        manifest = candidates.get(record.candidate_id)
        if (
            manifest is None
            or manifest.keys is None
            or PublishedRecord(
                manifest.expected_generation + 1, manifest.candidate_id, manifest.publication()
            )
            != record
        ):
            issue(path, "committed publication and its candidate evidence disagree or are missing")

    members = {
        identity: {obj.key for obj in record.publication.objects}
        for identity, record in originals.items()
    }
    for manifest in candidates.values():
        path = candidate_directory(root, manifest.candidate_id) / "state.json"
        own = originals.get((manifest.dataset.dataset_id, manifest.publication_id))
        for reused in manifest.reused_objects:
            identity = (manifest.dataset.dataset_id, reused.publication_id)
            source = originals.get(identity)
            if (
                source is None
                or source.publication.dataset != manifest.dataset
                or reused.key not in members[identity]
                or (
                    own is not None
                    and own.candidate_id == manifest.candidate_id
                    and source.generation >= own.generation
                )
            ):
                issue(path, "reused object has no valid earlier source membership")

    for did, pid in currents.items():
        head = publications[(did, pid)]
        records = by_dataset[did]
        generations = {p.generation for p in records}
        if (
            len(generations) != len(records)
            or len(records) != head.generation
            or max(generations) != head.generation
        ):
            issue(
                dataset_directory(root, did),
                "publication generations are incomplete or inconsistent",
            )
        if any(record.publication.dataset != head.publication.dataset for record in records):
            issue(dataset_directory(root, did), "dataset capability changes across publications")

    return ControlInventory(
        tuple(publications.values()),
        tuple(sorted(currents.items())),
        tuple(candidates.values()),
        tuple(references),
        tuple(sorted(dataset_ids)),
        tuple(issues),
        tuple(pending),
        tuple(retired.values()),
        tuple(originals[identity] for identity in retired if identity in originals),
        tuple(plans.values()),
        tuple(progress.values()),
        tuple(control_paths),
        tuple(abandoned),
        tuple(cleanups),
        tuple(cleanup_progress),
    )
