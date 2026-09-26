"""Durable bounded collection under the exclusive repository lock."""

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from asterstore.errors import StoreCorruptionError
from asterstore.metadata import (
    CollectionPlan,
    CollectionPolicy,
    CollectionProgress,
    ObjectRef,
    PreviewIssue,
    RetiredRecord,
    encode_collection_plan,
    encode_collection_progress,
    encode_retired,
    validate_candidate_id,
)
from asterstore.storage import (
    ControlInventory,
    atomic_write,
    check_repository,
    control_directory,
    delete_object,
    file_lock,
    history_path,
    immutable_write,
    scan_controls,
    sync_control_files,
)

from ._models import CollectionResult
from ._reachability import calculate


def _issues(inventory: ControlInventory, policy: CollectionPolicy) -> tuple[PreviewIssue, ...]:
    return inventory.issues + tuple(
        PreviewIssue(".asterstore/datasets", f"unknown dataset in scope: {dataset}")
        for dataset in policy.datasets
        if dataset not in inventory.dataset_ids
    )


def _result(plan: CollectionPlan, state: CollectionProgress) -> CollectionResult:
    handled = set(state.deleted + state.missing + state.protected)
    return CollectionResult(
        plan.operation_id,
        plan.policy,
        "complete" if state.state == "complete" else "blocked",
        tuple(
            record.publication for record in plan.targets if record.candidate_id in state.retired
        ),
        tuple(ObjectRef(key) for key in state.deleted),
        tuple(ObjectRef(key) for key in state.missing),
        tuple(ObjectRef(key) for key in state.protected),
        tuple(obj for obj in plan.objects if obj.key not in handled),
        state.issues,
    )


def _save(root: Path, state: CollectionProgress) -> None:
    atomic_write(
        control_directory(root) / "collections" / state.operation_id / "progress.json",
        encode_collection_progress(state),
    )


def _surviving(inventory: ControlInventory) -> set[str]:
    # During deletion every still-available publication is a root, including targets
    # skipped on recovery. A policy never authorizes deleting before retirement.
    keys = {obj.key for record in inventory.publications for obj in record.publication.objects}
    committed = {
        record.candidate_id for record in inventory.publications + inventory.retired_publications
    }
    keys.update(
        obj.key
        for manifest in inventory.candidates
        if manifest.candidate_id not in committed and manifest.keys is not None
        for obj in manifest.publication().objects
    )
    return keys


def _execute(root: Path, plan: CollectionPlan, previous: CollectionProgress) -> CollectionResult:
    state = replace(previous, state="running", issues=())
    try:
        inventory = scan_controls(root)
        issues = _issues(inventory, plan.policy)
        if issues:
            return _result(plan, replace(previous, state="blocked", issues=issues))
        # Do this even on resume after an ambiguous retirement/sync failure.
        sync_control_files(root, inventory.control_paths)
        if previous.state == "complete":
            return _result(plan, previous)
        retired = {
            record.candidate_id
            for record in inventory.retired
            if record.collection_id == plan.operation_id
        }
        state = replace(state, retired=tuple(sorted(retired)))
        _save(root, state)
        report = calculate(inventory, plan.policy)
        eligible = {item.publication for item in report.publications if item.retire}
        for target in plan.targets:
            if target.candidate_id in retired or target.publication not in eligible:
                continue
            tombstone = RetiredRecord.from_record(target, plan.operation_id)
            atomic_write(
                history_path(
                    root, target.publication.dataset.dataset_id, target.publication.publication_id
                ),
                encode_retired(tombstone),
            )
            retired.add(target.candidate_id)
            state = replace(state, retired=tuple(sorted(retired)))
        # All retirement writes must be acknowledged before any physical deletion.
        inventory = scan_controls(root)
        issues = _issues(inventory, plan.policy)
        if issues:
            state = replace(state, state="blocked", issues=issues)
            _save(root, state)
            return _result(plan, state)
        sync_control_files(root, inventory.control_paths)
        surviving = _surviving(inventory)
        deleted = set(previous.deleted)
        missing: set[str] = set()
        protected: set[str] = set()
        # Revisit every original key; progress may lag unlink, or roots may have changed.
        state = replace(state, deleted=(), missing=(), protected=())
        for obj in plan.objects:
            if obj.key in surviving:
                if obj.key in deleted:
                    raise StoreCorruptionError("a previously deleted object is now referenced")
                protected.add(obj.key)
            elif delete_object(root, obj.key):
                deleted.add(obj.key)
            elif obj.key not in deleted:
                missing.add(obj.key)
            state = replace(
                state,
                deleted=tuple(sorted(deleted)),
                missing=tuple(sorted(missing)),
                protected=tuple(sorted(protected)),
            )
        state = replace(state, state="complete")
        _save(root, state)
        return _result(plan, state)
    except (OSError, StoreCorruptionError) as exc:
        state = replace(
            state,
            state="blocked",
            issues=(PreviewIssue(f".asterstore/collections/{plan.operation_id}", str(exc)),),
        )
        # If journal persistence itself fails, propagate: preview still exposes the plan.
        _save(root, state)
        return _result(plan, state)


def collect(root: Path, policy: CollectionPolicy) -> CollectionResult:
    if not isinstance(policy, CollectionPolicy):
        raise TypeError("policy must be a CollectionPolicy")
    check_repository(root, writable=True)
    with file_lock(control_directory(root) / "gc.lock", exclusive=True):
        check_repository(root, writable=True)
        inventory = scan_controls(root)
        issues = _issues(inventory, policy)
        if inventory.pending_operations:
            issues += (
                PreviewIssue(
                    ".asterstore/collections",
                    "resume pending collection: " + ", ".join(inventory.pending_operations),
                ),
            )
        if issues:
            return CollectionResult(None, policy, "blocked", issues=issues)
        report = calculate(inventory, policy)
        targets = {item.publication for item in report.publications if item.retire}
        if not targets and not report.reclaimable_objects:
            return CollectionResult(None, policy, "complete")
        plan = CollectionPlan(
            uuid4().hex,
            policy,
            tuple(record for record in inventory.publications if record.publication in targets),
            report.reclaimable_objects,
        )
        sync_control_files(root, inventory.control_paths)
        immutable_write(
            control_directory(root) / "collections" / plan.operation_id / "plan.json",
            encode_collection_plan(plan),
        )
        return _execute(root, plan, CollectionProgress(plan.operation_id))


def resume_collection(root: Path, operation_id: str) -> CollectionResult:
    validate_candidate_id(operation_id)
    check_repository(root, writable=True)
    with file_lock(control_directory(root) / "gc.lock", exclusive=True):
        check_repository(root, writable=True)
        inventory = scan_controls(root)
        plan = next((item for item in inventory.plans if item.operation_id == operation_id), None)
        if plan is None:
            if inventory.issues:
                raise StoreCorruptionError("cannot load collection plan from corrupt controls")
            raise FileNotFoundError(f"collection plan does not exist: {operation_id}")
        previous = next(item for item in inventory.progress if item.operation_id == operation_id)
        others = tuple(item for item in inventory.pending_operations if item != operation_id)
        if others:
            return _result(
                plan,
                replace(
                    previous,
                    state="blocked",
                    issues=(
                        PreviewIssue(
                            ".asterstore/collections", "multiple pending operations require repair"
                        ),
                    ),
                ),
            )
        return _execute(root, plan, previous)
