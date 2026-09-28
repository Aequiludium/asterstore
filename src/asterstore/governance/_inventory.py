"""Explicit full control-record inventory; ordinary reads never run this traversal."""

import re
import stat
from dataclasses import dataclass
from pathlib import Path

from asterstore.errors import StoreCorruptionError
from asterstore.metadata.capabilities import Management, RetentionScope
from asterstore.metadata.protocol import (
    DeclarationRecord,
    FixedRetention,
    GovernancePlan,
    GovernanceProgress,
    ManagedCleanupPlan,
    ManagedCleanupProgress,
    ManagedRequest,
    ObjectRecord,
    RetiredDeclaration,
    StoreRecord,
    decode_declaration_record,
    decode_governance_plan,
    decode_managed_request,
    decode_object_record,
    decode_retention,
    decode_retired,
)
from asterstore.storage.registry import (
    collection_directory,
    object_record_path,
    operation_path,
    read_abandoned,
    read_cleanup_plan,
    read_cleanup_progress,
    read_governance_progress,
    read_object_record,
    retention_path,
    retired_path,
    token,
)

PublicationKey = tuple[str, str]


def key(value: DeclarationRecord) -> PublicationKey:
    return value.declaration.dataset_id, value.declaration.publication_id


def entries(path: Path) -> tuple[Path, ...]:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return ()
    if not stat.S_ISDIR(mode):
        raise StoreCorruptionError(f"control directory is not ordinary: {path}")
    result = []
    for child in sorted(path.iterdir()):
        if re.fullmatch(r"\.[^.]+\.json\.[a-z0-9_]+", child.name):
            if not stat.S_ISREG(child.lstat().st_mode):
                raise StoreCorruptionError("temporary control record is not ordinary")
            continue  # Interrupted atomic write: never an authority source or deletion target.
        result.append(child)
    return tuple(result)


def control_bytes(path: Path, paths: set[Path]) -> bytes:
    if not stat.S_ISREG(path.lstat().st_mode):
        raise StoreCorruptionError(f"control record is not ordinary: {path}")
    paths.add(path)
    return path.read_bytes()


def checked(value: DeclarationRecord, store: StoreRecord) -> DeclarationRecord:
    try:
        value.check_store(store)
    except ValueError as exc:
        raise StoreCorruptionError(str(exc)) from exc
    return value


@dataclass(slots=True)
class Inventory:
    store: StoreRecord
    publications: dict[PublicationKey, DeclarationRecord]
    current: set[PublicationKey]
    objects: dict[str, ObjectRecord]
    retired: dict[PublicationKey, RetiredDeclaration]
    protected_objects: set[str]
    protected_publications: set[PublicationKey]
    retained_metadata: set[PublicationKey]
    plans: dict[str, GovernancePlan]
    control_paths: set[Path]
    collected_objects: set[str]
    object_users: dict[str, set[PublicationKey]]
    pending_objects: set[str]
    cleaned_objects: set[str]
    requests: dict[str, ManagedRequest]
    seals: dict[str, DeclarationRecord]
    abandoned: set[str]
    references: tuple[FixedRetention, ...]
    pending_users: dict[str, set[str]]
    collection_progress: dict[str, GovernanceProgress]
    cleanup_plans: dict[str, ManagedCleanupPlan]
    cleanup_progress: dict[str, ManagedCleanupProgress]


def inventory(root: Path, store: StoreRecord) -> Inventory:
    paths = {root / ".asterstore/format.json"}
    publications: dict[PublicationKey, DeclarationRecord] = {}
    current: set[PublicationKey] = set()
    operations: dict[str, DeclarationRecord] = {}
    objects: dict[str, ObjectRecord] = {}
    retired: dict[PublicationKey, RetiredDeclaration] = {}
    refs: list[FixedRetention] = []
    protected: set[str] = set()
    object_roots: set[PublicationKey] = set()
    metadata_roots: set[PublicationKey] = set()
    control = root / ".asterstore"
    allowed = {
        "format.json",
        ".init.lock",
        "gc.lock",
        "registrations",
        "object-records",
        "datasets",
        "managed-candidates",
        "managed-data",
        "fixed-retentions",
        "retired",
        "collections",
    }
    if {p.name for p in entries(control)} - allowed:
        raise StoreCorruptionError("unknown top-level control record")
    for path in entries(control / "registrations"):
        value = checked(decode_declaration_record(control_bytes(path, paths)), store)
        if path != operation_path(root, value.operation_id):
            raise StoreCorruptionError("operation path identity mismatch")
        operations[value.operation_id] = value
    initial_datasets = {
        token(value.declaration.dataset_id)
        for value in operations.values()
        if value.expected_generation == 0
    }
    for directory in entries(control / "datasets"):
        children = entries(directory)
        if {p.name for p in children} - {"current.json", "history"}:
            raise StoreCorruptionError("unknown dataset control record")
        head_path = directory / "current.json"
        if head_path not in children:
            if directory.name in initial_datasets and not entries(directory / "history"):
                # First current writes create their parent before the visibility point.
                # A fixed initial request explains an empty directory, never real history.
                continue
            raise StoreCorruptionError("dataset is missing current")
        head = checked(decode_declaration_record(control_bytes(head_path, paths)), store)
        if directory.name != token(head.declaration.dataset_id):
            raise StoreCorruptionError("dataset path identity mismatch")
        publications[key(head)] = head
        current.add(key(head))
        generations = {head.generation: key(head)}
        for path in entries(directory / "history"):
            value = checked(decode_declaration_record(control_bytes(path, paths)), store)
            if (
                value.declaration.dataset_id != head.declaration.dataset_id
                or path.name != token(value.declaration.publication_id) + ".json"
                or value.generation > head.generation
                or (value.generation in generations and generations[value.generation] != key(value))
            ):
                raise StoreCorruptionError("history identity/generation mismatch")
            if key(value) in publications and publications[key(value)] != value:
                raise StoreCorruptionError("history differs from current")
            publications[key(value)] = value
            generations[value.generation] = key(value)
    for path in entries(control / "object-records"):
        object_record = decode_object_record(control_bytes(path, paths))
        if (
            path != object_record_path(root, object_record.object_id)
            or read_object_record(root, store, object_record.object_id) != object_record
        ):
            raise StoreCorruptionError("object path identity mismatch")
        objects[object_record.object_id] = object_record
    requests = {}
    seals = {}
    abandoned = set()
    cleaned_paths: set[str] = set()
    private_cleanups: set[str] = set()
    pending_users: dict[str, set[str]] = {}
    cleanup_plans = {}
    cleanup_states = {}
    committed_operations = {v.operation_id for v in publications.values()}
    for directory in entries(control / "managed-candidates"):
        children = entries(directory)
        if {p.name for p in children} - {
            "writer.lock",
            "request.json",
            "sealed.json",
            "protection.json",
            "abandoned.json",
            "cleanup-plan.json",
            "cleanup-progress.json",
            "files",
        }:
            raise StoreCorruptionError("unknown managed candidate record")
        if directory / "request.json" not in children:
            if {p.name for p in children} <= {"writer.lock"}:
                continue
            raise StoreCorruptionError("candidate lacks its request")
        request = decode_managed_request(control_bytes(directory / "request.json", paths))
        if (
            request.store_id != store.store_id
            or directory.name != token(request.operation_id)
            or store.managed_resource_id is None
        ):
            raise StoreCorruptionError("candidate identity or feature mismatch")
        requests[request.operation_id] = request
        is_abandoned = read_abandoned(root, store, request.operation_id) is not None
        if is_abandoned:
            control_bytes(directory / "abandoned.json", paths)
            abandoned.add(request.operation_id)
            if request.operation_id in committed_operations:
                raise StoreCorruptionError("committed candidate is abandoned")
        if not is_abandoned and directory / "protection.json" not in children:
            raise StoreCorruptionError("candidate is missing its mandatory protection record")
        for name in ("sealed.json", "protection.json"):
            path = directory / name
            if path not in children:
                continue
            value = checked(decode_declaration_record(control_bytes(path, paths)), store)
            if value != request.declaration_record(value.declaration.files):
                raise StoreCorruptionError("candidate record differs from request")
            if name == "sealed.json":
                seals[request.operation_id] = value
            if not is_abandoned and request.operation_id not in committed_operations:
                for obj in value.declaration.files.objects:
                    proof = objects.get(obj.object_id)
                    if not obj.locator.relative_path.startswith(token(request.operation_id) + "/"):
                        if (
                            proof is None
                            or proof.locator != obj.locator
                            or proof.creator_operation_id is None
                        ):
                            raise StoreCorruptionError("pending reuse lacks creator evidence")
                    protected.add(obj.object_id)
                    pending_users.setdefault(obj.object_id, set()).add(request.operation_id)
        for name in ("cleanup-plan.json", "cleanup-progress.json"):
            if directory / name in children:
                control_bytes(directory / name, paths)
        cleanup = read_cleanup_plan(root, store, request.operation_id)
        if cleanup is None:
            if directory / "cleanup-progress.json" in children:
                raise StoreCorruptionError("cleanup progress lacks its fixed plan")
        else:
            cleanup_progress = read_cleanup_progress(root, cleanup)
            cleanup_plans[request.operation_id] = cleanup
            cleanup_states[request.operation_id] = cleanup_progress
            if cleanup.location == "installed":
                seal = seals.get(request.operation_id)
                prefix = token(request.operation_id) + "/"
                owned = (
                    set()
                    if seal is None
                    else {
                        o.locator.relative_path.removeprefix(prefix)
                        for o in seal.declaration.files.objects
                        if o.locator.relative_path.startswith(prefix)
                    }
                )
                if seal is None or set(cleanup.files) != owned:
                    raise StoreCorruptionError("installed cleanup differs from sealed creator")
                cleaned_paths.update(prefix + p for p, _ in cleanup_progress.outcomes)
            else:
                private_cleanups.add(request.operation_id)
    pending_objects = set(protected)
    cleaned_objects: set[str] = set()
    sealed_objects = {
        op: {o.object_id: o.locator for o in seal.declaration.files.objects}
        for op, seal in seals.items()
    }
    for object_record in objects.values():
        if object_record.creator_operation_id is None:
            continue
        creator = object_record.creator_operation_id
        seal = seals.get(creator)
        if (
            creator not in requests
            or seal is None
            or sealed_objects[creator].get(object_record.object_id) != object_record.locator
        ):
            raise StoreCorruptionError("managed object lacks matching sealed creator")
        if creator in private_cleanups:
            raise StoreCorruptionError("private cleanup conflicts with installed object evidence")
        if object_record.locator.relative_path in cleaned_paths:
            cleaned_objects.add(object_record.object_id)
        elif creator not in committed_operations:
            protected.add(
                object_record.object_id
            )  # Orphan candidates require separate explicit cleanup.
    object_users: dict[str, set[PublicationKey]] = {}
    for publication in publications.values():
        if operations.get(publication.operation_id) != publication:
            raise StoreCorruptionError("publication lacks matching fixed operation")
        if publication.declaration.capabilities.management is Management.MANAGED:
            if seals.get(publication.operation_id) != publication:
                raise StoreCorruptionError("managed publication lacks matching seal")
        for obj in publication.declaration.files.objects:
            object_users.setdefault(obj.object_id, set()).add(key(publication))
            proof = objects.get(obj.object_id)
            if (
                proof is None
                or proof.locator != obj.locator
                or proof.byte_stability != publication.declaration.capabilities.byte_stability
            ):
                raise StoreCorruptionError("publication object evidence mismatch")
    if cleaned_objects & (pending_objects | set(object_users)):
        raise StoreCorruptionError("cleaned candidate object has a publication or pending user")
    plans = {}
    collected: set[str] = set()
    progress_records = []
    planned_retirements: dict[str, dict[PublicationKey, DeclarationRecord]] = {}
    for directory in entries(control / "collections"):
        children = entries(directory)
        if {p.name for p in children} - {"plan.json", "progress.json"}:
            raise StoreCorruptionError("unknown collection record")
        if not children:
            continue  # A terminated first write may leave an empty plan directory.
        plan = decode_governance_plan(control_bytes(directory / "plan.json", paths))
        if plan.store_id != store.store_id or directory != collection_directory(
            root, plan.operation_id
        ):
            raise StoreCorruptionError("collection path identity mismatch")
        if any(publications.get(key(p)) != p for p in plan.retirements) or any(
            objects.get(o.object_id) != o for o in plan.objects
        ):
            raise StoreCorruptionError("collection plan conflicts with authority records")
        progress = read_governance_progress(root, plan)
        progress_records.append(progress)
        collected.update(oid for oid, outcome in progress.outcomes if outcome != "protected")
        if directory / "progress.json" in children:
            control_bytes(directory / "progress.json", paths)
        plans[plan.operation_id] = plan
        # Preserve full record equality without scanning the entire plan per retirement.
        planned_retirements[plan.operation_id] = {key(p): p for p in plan.retirements}
    for directory in entries(control / "retired"):
        for path in entries(directory):
            retirement = decode_retired(control_bytes(path, paths))
            identity = key(retirement.publication)
            origin_plan = plans.get(retirement.collection_id)
            if (
                path != retired_path(root, *identity)
                or publications.get(identity) != retirement.publication
                or identity in current
                or origin_plan is None
                or planned_retirements[retirement.collection_id].get(identity)
                != retirement.publication
            ):
                raise StoreCorruptionError("retirement lacks matching publication and plan")
            retired[identity] = retirement
    for progress in progress_records:
        if any(identity not in retired for identity in progress.retired):
            raise StoreCorruptionError("collection progress lacks durable retirement evidence")
    for oid in collected:
        if not object_users.get(oid) or any(
            identity not in retired for identity in object_users[oid]
        ):
            raise StoreCorruptionError("collected object lacks all retirement evidence")
    for path in entries(control / "fixed-retentions"):
        reference_record = decode_retention(control_bytes(path, paths))
        if reference_record.store_id != store.store_id or path != retention_path(
            root, reference_record.name
        ):
            raise StoreCorruptionError("retention path identity mismatch")
        if (reference_record.dataset_id, reference_record.publication_id) not in publications:
            raise StoreCorruptionError("retention target metadata is missing")
        refs.append(reference_record)
    for identity in current:
        metadata_roots.add(identity)
        if publications[identity].declaration.capabilities.management is Management.MANAGED:
            object_roots.add(identity)
    for reference in refs:
        if not reference.active:
            continue
        identity = reference.dataset_id, reference.publication_id
        metadata_roots.add(identity)
        if reference.scope is RetentionScope.OBJECTS:
            if (
                identity in retired
                or publications[identity].declaration.capabilities.management
                is not Management.MANAGED
            ):
                raise StoreCorruptionError("object retention targets unavailable or external data")
            object_roots.add(identity)
    for identity in object_roots:
        protected.update(obj.object_id for obj in publications[identity].declaration.files.objects)
    if protected & collected:
        raise StoreCorruptionError("collected object acquired new protection")
    return Inventory(
        store,
        publications,
        current,
        objects,
        retired,
        protected,
        object_roots,
        metadata_roots,
        plans,
        paths,
        collected,
        object_users,
        pending_objects,
        cleaned_objects,
        requests,
        seals,
        abandoned,
        tuple(refs),
        pending_users,
        {p.operation_id: p for p in progress_records},
        cleanup_plans,
        cleanup_states,
    )
