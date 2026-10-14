"""Fixed-batch dataset decommissioning, independent of payload collection."""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from asterstore.errors import (
    DatasetDecommissionedError,
    PublicationConflictError,
    PublicationNotFoundError,
)
from asterstore.metadata.protocol import (
    MAX_GENERATION,
    DatasetTombstone,
    DecommissionPlan,
    encode_declaration_record,
    encode_decommission_plan,
    encode_store,
    encode_tombstone,
)
from asterstore.storage import atomic_write, file_lock, immutable_write, sync_control_files
from asterstore.storage.registry import (
    head_path,
    lifecycle_store,
    marker_path,
    publication_path,
    read_head_state,
    token,
)

from ._inventory import Inventory, inventory
from .inspection._locking import inspection_lock
from .inspection._service import read_inventory


@dataclass(frozen=True, slots=True)
class DatasetStatus:
    dataset_id: str
    state: Literal["active", "decommissioned"]
    publication_id: str
    generation: int
    operation_id: str | None = None


@dataclass(frozen=True, slots=True)
class DecommissionResult:
    operation_id: str
    decommissioned: tuple[str, ...]
    pending: tuple[str, ...]
    conflicts: tuple[str, ...]

    @property
    def complete(self) -> bool:
        """All targets were classified; conflicts are terminal skips, not success."""
        return not self.pending


def result(view: Inventory, plan: DecommissionPlan) -> DecommissionResult:
    done, pending, conflicts = [], [], []
    for target in plan.targets:
        dataset = target.declaration.dataset_id
        tombstone = view.decommissioned.get(dataset)
        if tombstone == DatasetTombstone(plan.operation_id, target):
            done.append(dataset)
        elif (dataset, target.declaration.publication_id) in view.current:
            pending.append(dataset)
        else:
            conflicts.append(dataset)
    return DecommissionResult(plan.operation_id, tuple(done), tuple(pending), tuple(conflicts))


def dataset_status(root: Path, dataset_id: str) -> DatasetStatus:
    store = lifecycle_store(root)
    head = read_head_state(root, store, dataset_id)
    if head is None:
        raise PublicationNotFoundError("dataset does not exist")
    if isinstance(head, DatasetTombstone):
        return DatasetStatus(
            dataset_id,
            "decommissioned",
            head.publication.declaration.publication_id,
            head.publication.generation,
            head.operation_id,
        )
    return DatasetStatus(dataset_id, "active", head.declaration.publication_id, head.generation)


def decommission_status(root: Path, operation_id: str) -> DecommissionResult:
    token(operation_id)
    with inspection_lock(root) as coordinated:
        view = read_inventory(root, coordinated)
        plan = view.decommission_plans.get(operation_id)
        if plan is None:
            raise PublicationNotFoundError("decommission plan does not exist")
        return result(view, plan)


def decommission(
    root: Path, operation_id: str, datasets: Mapping[str, int] | None
) -> DecommissionResult:
    identity = token(operation_id)
    if datasets is not None:
        if not isinstance(datasets, Mapping) or not datasets:
            raise ValueError("datasets must be a nonempty mapping of dataset IDs to generations")
        datasets = dict(datasets)
        for dataset, generation in datasets.items():
            token(dataset)
            if type(generation) is not int or not 1 <= generation <= MAX_GENERATION:
                raise ValueError("expected generation must be a positive protocol integer")
    lifecycle_store(root)
    with file_lock(root / ".asterstore/gc.lock", exclusive=True):
        store = lifecycle_store(root)
        view = inventory(root, store)
        plan = view.decommission_plans.get(operation_id)
        path = root / ".asterstore/decommissions" / (identity + ".json")
        if plan is None:
            if datasets is None:
                raise PublicationNotFoundError("decommission plan does not exist")
            targets = []
            for dataset, generation in sorted(datasets.items()):
                head = read_head_state(root, store, dataset)
                if isinstance(head, DatasetTombstone):
                    raise DatasetDecommissionedError(dataset, head.operation_id)
                if head is None:
                    raise PublicationNotFoundError(f"dataset does not exist: {dataset}")
                if head.generation != generation:
                    raise PublicationConflictError(f"dataset generation changed: {dataset}")
                targets.append(head)
            plan = DecommissionPlan(store.store_id, operation_id, tuple(targets))
            sync_control_files(root, tuple(sorted(view.control_paths)))
            if not store.decommissioning:
                store = replace(store, decommissioning=True)
                atomic_write(marker_path(root), encode_store(store))
            immutable_write(path, encode_decommission_plan(plan))
        elif datasets is not None and dict(datasets) != {
            p.declaration.dataset_id: p.generation for p in plan.targets
        }:
            raise PublicationConflictError("decommission operation has a different fixed batch")
        sync_control_files(root, (marker_path(root), path))
        for target in plan.targets:
            dataset = target.declaration.dataset_id
            head = read_head_state(root, store, dataset)
            tombstone = DatasetTombstone(operation_id, target)
            original = publication_path(root, dataset, target.declaration.publication_id)
            if head == target:
                immutable_write(original, encode_declaration_record(target))
                atomic_write(head_path(root, dataset), encode_tombstone(tombstone))
            elif head == tombstone:
                sync_control_files(root, (original, head_path(root, dataset)))
        return result(inventory(root, store), plan)
