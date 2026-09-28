"""Explain the exact protection roots used by the existing collector."""

from datetime import UTC, datetime
from pathlib import Path

from asterstore.errors import StoreCorruptionError
from asterstore.metadata.capabilities import RetentionScope
from asterstore.publishing._status import status_from_records
from asterstore.storage.registry import lifecycle_store

from .._inventory import Inventory, inventory, key
from ._locking import inspection_lock
from ._models import (
    GovernanceSnapshot,
    MaintenanceStatus,
    ObjectExplanation,
    ProtectionReason,
    PublicationStatus,
)


def read_inventory(root: Path, coordinated: bool) -> Inventory:
    view = inventory(root, lifecycle_store(root))
    if not coordinated:
        if (root / ".asterstore/gc.lock").exists():
            raise BlockingIOError("first writer started during inspection; retry")
        if view.publications or view.requests or view.objects or view.plans:
            raise StoreCorruptionError("nonempty repository lacks its coordination lock")
    return view


def snapshot(view: Inventory, coordinated: bool) -> GovernanceSnapshot:
    reasons: dict[str, list[ProtectionReason]] = {oid: [] for oid in view.objects}
    for identity in sorted(view.current):
        if identity not in view.protected_publications:
            continue
        for obj in view.publications[identity].declaration.files.objects:
            reasons[obj.object_id].append(ProtectionReason("current", *identity))
    for ref in sorted(view.references, key=lambda r: r.name):
        if not ref.active or ref.scope is not RetentionScope.OBJECTS:
            continue
        identity = ref.dataset_id, ref.publication_id
        for obj in view.publications[identity].declaration.files.objects:
            reasons[obj.object_id].append(
                ProtectionReason("retention", *identity, reference_name=ref.name)
            )
    committed = {p.operation_id for p in view.publications.values()}
    for oid, object_record in sorted(view.objects.items()):
        for op in sorted(view.pending_users.get(oid, ())):
            request = view.requests[op]
            reasons[oid].append(
                ProtectionReason(
                    "candidate",
                    request.dataset_id,
                    request.publication_id,
                    operation_id=op,
                )
            )
        creator = object_record.creator_operation_id
        if creator is not None and creator not in committed and oid not in view.cleaned_objects:
            request = view.requests[creator]
            reasons[oid].append(
                ProtectionReason(
                    "candidate_cleanup",
                    request.dataset_id,
                    request.publication_id,
                    operation_id=creator,
                )
            )
    current = {
        view.publications[k].declaration.dataset_id: view.publications[k] for k in view.current
    }
    candidates = tuple(
        status_from_records(
            request,
            current=current.get(request.dataset_id),
            committed=view.publications.get((request.dataset_id, request.publication_id)),
            sealed=view.seals.get(op),
            abandoned=op in view.abandoned,
            retired=(request.dataset_id, request.publication_id) in view.retired,
        )
        for op, request in sorted(view.requests.items())
    )
    maintenance = [
        MaintenanceStatus(
            op,
            "collection",
            len(plan.objects),
            len(view.collection_progress[op].outcomes),
            view.collection_progress[op].complete,
        )
        for op, plan in sorted(view.plans.items())
    ]
    maintenance += [
        MaintenanceStatus(
            op,
            "cleanup",
            len(plan.files),
            len(view.cleanup_progress[op].outcomes),
            view.cleanup_progress[op].complete,
        )
        for op, plan in sorted(view.cleanup_plans.items())
    ]
    return GovernanceSnapshot(
        view.store.store_id,
        datetime.now(UTC),
        coordinated,
        tuple(
            PublicationStatus(
                p,
                "retired"
                if key(p) in view.retired
                else "current"
                if key(p) in view.current
                else "historical",
                key(p) in view.protected_publications,
                key(p) in view.retained_metadata,
            )
            for _, p in sorted(view.publications.items())
        ),
        candidates,
        tuple(sorted(view.references, key=lambda r: r.name)),
        tuple(maintenance),
        tuple(
            ObjectExplanation(
                obj,
                tuple(reasons[oid]),
                obj.creator_operation_id is not None
                and oid not in view.protected_objects
                and oid not in view.collected_objects
                and oid not in view.cleaned_objects,
                oid in view.collected_objects,
                oid in view.cleaned_objects,
            )
            for oid, obj in sorted(view.objects.items())
        ),
    )


def inspect(root: Path) -> GovernanceSnapshot:
    with inspection_lock(root) as coordinated:
        return snapshot(read_inventory(root, coordinated), coordinated)
