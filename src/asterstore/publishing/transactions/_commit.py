"""Shared declaration commit with fixed requests and recoverable metadata writes."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from asterstore.errors import (
    PublicationConflictError,
    PublicationNotFoundError,
    StoreCorruptionError,
    UnsupportedCapabilityError,
)
from asterstore.metadata.capabilities import Management
from asterstore.metadata.protocol import (
    DeclarationRecord,
    ObjectRecord,
    StoreRecord,
    encode_declaration_record,
    encode_object_record,
)
from asterstore.storage import atomic_write, file_lock, immutable_write, sync_control_files
from asterstore.storage.registry import (
    find_record,
    head_path,
    marker_path,
    object_record_path,
    operation_path,
    publication_path,
    read_head,
    read_head_state,
    read_managed_request,
    read_object_record,
    read_operation,
    read_store,
    require_available,
    require_not_abandoned,
    token,
)


@dataclass(frozen=True, slots=True)
class RegistrationStatus:
    state: Literal["planned", "committed", "conflict"]
    record: DeclarationRecord


def _evidence(
    root: Path, store: StoreRecord, object_id: str, cache: dict[str, ObjectRecord | None]
) -> ObjectRecord | None:
    if object_id not in cache:
        cache[object_id] = read_object_record(root, store, object_id)
    return cache[object_id]


def _objects(
    root: Path,
    store: StoreRecord,
    record: DeclarationRecord,
    cache: dict[str, ObjectRecord | None],
) -> tuple[ObjectRecord, ...]:
    result = []
    managed = record.declaration.capabilities.management is Management.MANAGED
    for obj in record.declaration.files.objects:
        creator = None
        if managed:
            if obj.locator.relative_path.startswith(token(record.operation_id) + "/"):
                creator = record.operation_id
            else:
                previous = _evidence(root, store, obj.object_id, cache)
                if previous is None or previous.creator_operation_id is None:
                    raise StoreCorruptionError("reused object lacks managed creator evidence")
                creator = previous.creator_operation_id
        result.append(
            ObjectRecord(
                record.store_id,
                obj.object_id,
                obj.locator,
                record.declaration.capabilities.byte_stability,
                creator,
            )
        )
    return tuple(result)


def _committed(
    root: Path, store: StoreRecord, desired: DeclarationRecord
) -> DeclarationRecord | None:
    value = desired.declaration
    committed = find_record(root, store, value.dataset_id, value.publication_id)
    if committed is not None and committed != desired:
        raise PublicationConflictError("publication identity already belongs to another request")
    return committed


def _sync_result(
    root: Path, store: StoreRecord, desired: DeclarationRecord, *, durable: bool
) -> None:
    # Cache only within this coordinated operation, never across lock boundaries.
    cache: dict[str, ObjectRecord | None] = {}
    objects = _objects(root, store, desired, cache)
    # A committed response must not hide a lost or conflicting identity registry.
    for obj in objects:
        if _evidence(root, store, obj.object_id, cache) != obj:
            raise StoreCorruptionError("committed publication lacks its object identity evidence")
    if durable:
        head = read_head_state(root, store, desired.declaration.dataset_id)
        path = (
            head_path(root, desired.declaration.dataset_id)
            if head == desired
            else publication_path(
                root, desired.declaration.dataset_id, desired.declaration.publication_id
            )
        )
        sync_control_files(
            root,
            (
                marker_path(root),
                operation_path(root, desired.operation_id),
                path,
                *(object_record_path(root, obj.object_id) for obj in objects),
            ),
        )


def commit_record(
    root: Path,
    desired: DeclarationRecord,
    *,
    durable: bool = True,
    install: Callable[[], None] | None = None,
) -> DeclarationRecord:
    if type(durable) is not bool:
        raise TypeError("durable must be a boolean")
    store = read_store(root)
    desired.check_store(store)
    declaration, operation_id = desired.declaration, desired.operation_id
    expected_generation = desired.expected_generation
    managed = declaration.capabilities.management is Management.MANAGED
    if managed != (install is not None):
        raise UnsupportedCapabilityError("managed commits require their candidate installer")
    with file_lock(root / ".asterstore/gc.lock", exclusive=True):
        store = read_store(root)
        desired.check_store(store)
        request = read_managed_request(root, store, operation_id)
        if managed:
            require_not_abandoned(root, store, operation_id)
            if request is None or request.declaration_record(declaration.files) != desired:
                raise StoreCorruptionError("managed commit lacks matching candidate request")
        elif request is not None:
            raise PublicationConflictError("operation ID is reserved by a managed candidate")
        plan = read_operation(root, store, operation_id)
        if plan is not None and plan != desired:
            raise PublicationConflictError("operation ID was used with a different request")
        if _committed(root, store, desired) is not None:
            require_available(root, store, desired)
            if plan != desired:
                raise StoreCorruptionError("committed publication lacks its original operation")
            _sync_result(root, store, desired, durable=durable)
            if install is not None and durable:
                install()
            return desired
        head = read_head(root, store, declaration.dataset_id)
        observed = 0 if head is None else head.generation
        if observed != expected_generation:
            raise PublicationConflictError(
                f"generation changed: expected {expected_generation}, found {observed}"
            )
        if head is not None and head.declaration.capabilities != declaration.capabilities:
            raise PublicationConflictError("dataset capabilities cannot change during publication")
        if head is None:
            history = head_path(root, declaration.dataset_id).parent / "history"
            if history.exists() and any(history.iterdir()):
                raise StoreCorruptionError("current is missing but committed history exists")
        new_objects = []
        existing_paths = []
        cache: dict[str, ObjectRecord | None] = {}
        for obj in _objects(root, store, desired, cache):
            existing = _evidence(root, store, obj.object_id, cache)
            if existing is not None and existing != obj:
                raise PublicationConflictError(
                    f"object identity cannot be rebound: {obj.object_id}"
                )
            if existing is None:
                new_objects.append(obj)
            else:
                existing_paths.append(object_record_path(root, obj.object_id))
        if durable:
            # Sync inherited control evidence, never external object bytes.
            sync_control_files(root, (marker_path(root), *existing_paths))
        immutable_write(
            operation_path(root, operation_id), encode_declaration_record(desired), durable=durable
        )
        if install is not None:
            install()
        for obj in new_objects:
            immutable_write(
                object_record_path(root, obj.object_id), encode_object_record(obj), durable=durable
            )
        if head is not None:
            immutable_write(
                publication_path(root, declaration.dataset_id, head.declaration.publication_id),
                encode_declaration_record(head),
                durable=durable,
            )
        # Sole visibility point. Errors after replace may mean the request committed.
        atomic_write(
            head_path(root, declaration.dataset_id),
            encode_declaration_record(desired),
            durable=durable,
        )
        return desired


def registration_status(root: Path, operation_id: str) -> RegistrationStatus:
    read_store(root)
    with file_lock(root / ".asterstore/gc.lock", exclusive=False):
        store = read_store(root)
        plan = read_operation(root, store, operation_id)
        if plan is None:
            raise PublicationNotFoundError(f"registration operation does not exist: {operation_id}")
        try:
            if _committed(root, store, plan) is not None:
                return RegistrationStatus("committed", plan)
        except PublicationConflictError:
            return RegistrationStatus("conflict", plan)
        from asterstore.metadata.protocol import DatasetTombstone

        head = read_head_state(root, store, plan.declaration.dataset_id)
        if isinstance(head, DatasetTombstone):
            return RegistrationStatus("conflict", plan)
        if (0 if head is None else head.generation) != plan.expected_generation:
            return RegistrationStatus("conflict", plan)
        cache: dict[str, ObjectRecord | None] = {}
        for obj in _objects(root, store, plan, cache):
            existing = _evidence(root, store, obj.object_id, cache)
            if existing is not None and existing != obj:
                return RegistrationStatus("conflict", plan)
        return RegistrationStatus("planned", plan)


def checked_commit(
    root: Path,
    desired: DeclarationRecord,
    check_data: Callable[[], None],
    *,
    durable: bool,
    install: Callable[[], None] | None = None,
) -> DeclarationRecord:
    """Preflight under coordination, check outside it, then perform the normal commit."""
    from asterstore.errors import DataCheckError

    if type(durable) is not bool:
        raise TypeError("durable must be a boolean")
    with file_lock(root / ".asterstore/gc.lock", exclusive=False):
        store = read_store(root)
        desired.check_store(store)
        managed = desired.declaration.capabilities.management is Management.MANAGED
        request = read_managed_request(root, store, desired.operation_id)
        if managed:
            require_not_abandoned(root, store, desired.operation_id)
            if request is None or request.declaration_record(desired.declaration.files) != desired:
                raise StoreCorruptionError("managed commit lacks matching candidate request")
        elif request is not None:
            raise PublicationConflictError("operation ID is reserved by a managed candidate")
        plan = read_operation(root, store, desired.operation_id)
        if plan is not None and plan != desired:
            raise PublicationConflictError("operation ID was used with a different request")
        committed = _committed(root, store, desired)
        if committed is not None:
            require_available(root, store, committed)
            if plan != desired:
                raise StoreCorruptionError("committed publication lacks its original operation")
        else:
            head = read_head(root, store, desired.declaration.dataset_id)
            if (0 if head is None else head.generation) != desired.expected_generation:
                raise PublicationConflictError("generation changed before checking")
            if (
                head is not None
                and head.declaration.capabilities != desired.declaration.capabilities
            ):
                raise PublicationConflictError("dataset capabilities cannot change")
        cache: dict[str, ObjectRecord | None] = {}
        for obj in _objects(root, store, desired, cache):
            existing = _evidence(root, store, obj.object_id, cache)
            if existing is not None and existing != obj:
                raise PublicationConflictError("object identity cannot be rebound")
            if committed is not None and existing != obj:
                raise StoreCorruptionError("committed publication lacks object identity evidence")
    try:
        check_data()
    except DataCheckError as exc:
        exc.durability = "not_completed" if durable else "not_requested"
        try:
            with file_lock(root / ".asterstore/gc.lock", exclusive=False):
                store = read_store(root)
                committed = _committed(root, store, desired)
                exc.commit_state = "committed" if committed is not None else "not_attempted"
        except Exception as state_error:
            exc.commit_state = "unknown"
            exc.state_error = str(state_error)
        raise
    return commit_record(root, desired, durable=durable, install=install)
