"""Coordinated, fixed-plan collection. Only proven owned object files may be deleted."""

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from asterstore.errors import PublicationNotFoundError, StoreCorruptionError
from asterstore.metadata.capabilities import Management
from asterstore.metadata.protocol import (
    DeclarationRecord,
    GovernancePlan,
    GovernanceProgress,
    ObjectRecord,
    RetiredDeclaration,
    encode_governance_plan,
    encode_governance_progress,
    encode_retired,
)
from asterstore.storage import (
    atomic_write,
    delete_owned_file,
    file_lock,
    immutable_write,
    sync_control_files,
)
from asterstore.storage.registry import (
    collection_directory,
    lifecycle_store,
    managed_data_root,
    read_governance_plan,
    read_governance_progress,
    retired_path,
    token,
)

from ._inventory import Inventory, PublicationKey, inventory, key


@dataclass(frozen=True, slots=True)
class GovernancePreview:
    retiring: tuple[DeclarationRecord, ...]
    reclaimable: tuple[ObjectRecord, ...]
    protected_objects: tuple[str, ...]
    retained_metadata: tuple[PublicationKey, ...]


@dataclass(frozen=True, slots=True)
class GovernanceResult:
    operation_id: str
    retired_publications: tuple[PublicationKey, ...]
    deleted_objects: tuple[str, ...]
    missing_objects: tuple[str, ...]
    protected_objects: tuple[str, ...]
    complete: bool


def preview_inventory(view: Inventory) -> GovernancePreview:
    retiring = tuple(
        value
        for identity, value in sorted(view.publications.items())
        if identity not in view.protected_publications
        and identity not in view.retired
        and value.declaration.capabilities.management is Management.MANAGED
    )
    reclaimable = tuple(
        value
        for oid, value in sorted(view.objects.items())
        if value.creator_operation_id is not None
        and oid not in view.protected_objects
        and oid not in view.collected_objects
        and oid not in view.cleaned_objects
    )
    return GovernancePreview(
        retiring,
        reclaimable,
        tuple(sorted(view.protected_objects)),
        tuple(sorted(view.retained_metadata)),
    )


def preview(root: Path) -> GovernancePreview:
    lifecycle_store(root)
    with file_lock(root / ".asterstore/gc.lock", exclusive=False):
        return preview_inventory(inventory(root, lifecycle_store(root)))


def result(value: GovernanceProgress) -> GovernanceResult:
    return GovernanceResult(
        value.operation_id,
        value.retired,
        tuple(oid for oid, state in value.outcomes if state == "deleted"),
        tuple(oid for oid, state in value.outcomes if state == "missing"),
        tuple(oid for oid, state in value.outcomes if state == "protected"),
        value.complete,
    )


def execute(root: Path, plan: GovernancePlan, view: Inventory) -> GovernanceResult:
    progress = read_governance_progress(root, plan)
    # Persist deletion evidence, including weak publications and candidate roots.
    sync_control_files(
        root,
        tuple(view.control_paths | {collection_directory(root, plan.operation_id) / "plan.json"}),
    )
    if progress.complete:
        return result(progress)
    retired = set(progress.retired)
    for publication in plan.retirements:
        identity = key(publication)
        if view.publications.get(identity) != publication:
            raise StoreCorruptionError("planned retirement no longer matches authority")
        if identity in view.protected_publications:
            continue
        if identity not in view.retired:
            value = RetiredDeclaration(plan.operation_id, publication)
            immutable_write(retired_path(root, *identity), encode_retired(value), durable=True)
            view.retired[identity] = value
        retired.add(identity)
    progress = replace(progress, retired=tuple(sorted(retired)))
    outcomes = dict(progress.outcomes)
    for obj in plan.objects:
        if obj.object_id in outcomes:
            continue
        if view.objects.get(obj.object_id) != obj:
            raise StoreCorruptionError("planned object no longer matches creator evidence")
        state: Literal["deleted", "missing", "protected"]
        if obj.object_id in view.protected_objects:
            state = "protected"
        else:
            # No live published declaration may lose its bytes before retirement is durable.
            owners = view.object_users.get(obj.object_id, set())
            if not owners or any(identity not in view.retired for identity in owners):
                raise StoreCorruptionError("object lacks complete durable retirement evidence")
            state = (
                "deleted"
                if delete_owned_file(managed_data_root(root), obj.locator.relative_path)
                else "missing"
            )
        outcomes[obj.object_id] = state
        # Geometric checkpoints keep cumulative progress serialization linear in
        # plan size. A crash between checkpoints is recovered as missing files.
        count = len(outcomes)
        if count & (count - 1) == 0:
            progress = replace(progress, outcomes=tuple(outcomes.items()))
            atomic_write(
                collection_directory(root, plan.operation_id) / "progress.json",
                encode_governance_progress(progress),
                durable=True,
            )
    progress = replace(progress, outcomes=tuple(outcomes.items()), complete=True)
    atomic_write(
        collection_directory(root, plan.operation_id) / "progress.json",
        encode_governance_progress(progress),
        durable=True,
    )
    return result(progress)


def collect(root: Path, operation_id: str, *, resume: bool = False) -> GovernanceResult:
    token(operation_id)
    lifecycle_store(root)
    with file_lock(root / ".asterstore/gc.lock", exclusive=True):
        store = lifecycle_store(root)
        plan = read_governance_plan(root, store, operation_id)
        if plan is None and resume:
            raise PublicationNotFoundError("collection operation does not exist")
        view = inventory(root, store)
        if plan is None:
            selection = preview_inventory(view)
            plan = GovernancePlan(
                store.store_id, operation_id, selection.retiring, selection.reclaimable
            )
            immutable_write(
                collection_directory(root, operation_id) / "plan.json",
                encode_governance_plan(plan),
                durable=True,
            )
        return execute(root, plan, view)
