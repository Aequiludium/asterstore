"""Fixed named retention under the same coordination lock as collection."""

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from asterstore.errors import (
    InvalidDeclarationError,
    PublicationNotFoundError,
    ReferenceConflictError,
    ReferenceNotFoundError,
    UnsupportedCapabilityError,
)
from asterstore.metadata.capabilities import RetentionScope
from asterstore.metadata.protocol import MAX_GENERATION, FixedRetention, encode_retention
from asterstore.reading import Binding
from asterstore.storage import atomic_write, file_lock, sync_control_files
from asterstore.storage.registry import (
    find_record,
    lifecycle_store,
    managed_data_root,
    read_retention,
    require_available,
    retention_path,
)


def revision(value: int) -> None:
    if type(value) is not int or not 0 <= value < MAX_GENERATION:
        raise InvalidDeclarationError("expected_revision must be a non-exhausted integer")


def retain(
    root: Path,
    name: str,
    dataset_id: str,
    publication_id: str,
    *,
    scope: RetentionScope,
    expected_revision: int = 0,
) -> FixedRetention:
    revision(expected_revision)
    store = lifecycle_store(root)
    desired = FixedRetention(
        store.store_id, name, dataset_id, publication_id, scope, expected_revision + 1
    )
    with file_lock(root / ".asterstore/gc.lock", exclusive=True):
        store = lifecycle_store(root)
        previous = read_retention(root, store, name)
        if previous == desired:
            sync_control_files(root, (retention_path(root, name),))
            return desired
        if (0 if previous is None else previous.revision) != expected_revision:
            raise ReferenceConflictError("retention revision changed")
        if previous is not None and (
            previous.dataset_id,
            previous.publication_id,
            previous.scope,
        ) != (dataset_id, publication_id, scope):
            raise ReferenceConflictError("retention target and scope are fixed for this name")
        publication = find_record(root, store, dataset_id, publication_id)
        if publication is None:
            raise PublicationNotFoundError("retention target does not exist")
        publication.declaration.capabilities.require_retention(scope)
        if scope is RetentionScope.OBJECTS:
            require_available(root, store, publication)
        # Strong protection must also preserve a previously weak target declaration.
        from asterstore.storage.registry import head_path, publication_path, read_head

        target = (
            head_path(root, dataset_id)
            if read_head(root, store, dataset_id) == publication
            else publication_path(root, dataset_id, publication_id)
        )
        sync_control_files(root, (root / ".asterstore/format.json", target))
        atomic_write(retention_path(root, name), encode_retention(desired), durable=True)
        return desired


def get(root: Path, name: str) -> FixedRetention:
    store = lifecycle_store(root)
    value = read_retention(root, store, name)
    if value is None:
        raise ReferenceNotFoundError(name)
    return value


def release(root: Path, name: str, *, expected_revision: int) -> FixedRetention:
    revision(expected_revision)
    lifecycle_store(root)
    with file_lock(root / ".asterstore/gc.lock", exclusive=True):
        store = lifecycle_store(root)
        previous = read_retention(root, store, name)
        if previous is None:
            raise ReferenceNotFoundError(name)
        if not previous.active and previous.revision == expected_revision + 1:
            sync_control_files(root, (retention_path(root, name),))
            return previous
        if previous.revision != expected_revision or not previous.active:
            raise ReferenceConflictError("retention is not active at expected revision")
        desired = replace(previous, revision=expected_revision + 1, active=False)
        atomic_write(retention_path(root, name), encode_retention(desired), durable=True)
        return desired


def open_retained(
    root: Path,
    name: str,
    *,
    expected_revision: int,
    resources: Mapping[str, str | Path] | None = None,
) -> Binding:
    if type(expected_revision) is not int or not 1 <= expected_revision <= MAX_GENERATION:
        raise InvalidDeclarationError("expected_revision must be a positive integer")
    lifecycle_store(root)
    with file_lock(root / ".asterstore/gc.lock", exclusive=False):
        store = lifecycle_store(root)
        reference = get(root, name)
        if reference.revision != expected_revision or not reference.active:
            raise ReferenceConflictError("retention is not active at expected revision")
        if reference.scope is not RetentionScope.OBJECTS:
            raise UnsupportedCapabilityError(
                "metadata retention does not provide retained data access"
            )
        publication = find_record(root, store, reference.dataset_id, reference.publication_id)
        if publication is None:
            raise PublicationNotFoundError("retention target metadata is missing")
        require_available(root, store, publication)
        publication.declaration.capabilities.require_managed()
        assert store.managed_resource_id is not None
        bindings = dict(resources or {})
        # Retained access uses the same owned root and rejects remapping it.
        from asterstore.storage import absolute_root

        owned = managed_data_root(root)
        supplied = bindings.get(store.managed_resource_id)
        if supplied is not None and absolute_root(supplied) != owned:
            raise InvalidDeclarationError("managed resource root cannot be redirected")
        bindings[store.managed_resource_id] = owned
        return Binding(root, publication.declaration, resources=bindings)
