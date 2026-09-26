"""Explicit persistent binding. No data-file probing or retention side effects."""

from collections.abc import Mapping
from pathlib import Path

from asterstore.errors import (
    HistoryUnavailableError,
    InvalidDeclarationError,
    PublicationNotFoundError,
    ResourceNotBoundError,
)
from asterstore.metadata import Declaration, PhysicalHistory, Publication
from asterstore.metadata.capabilities import HistoryAccess, Management
from asterstore.metadata.protocol import DeclarationRecord, StoreRecord
from asterstore.storage import absolute_root, find_committed, read_current
from asterstore.storage.registry import (
    find_record,
    managed_data_root,
    read_head,
    read_repository_metadata,
    read_store,
    require_available,
)

from ._binding import Binding


def describe(
    root: Path, dataset_id: str, *, publication_id: str | None = None
) -> DeclarationRecord:
    store = read_store(root)
    result = (
        read_head(root, store, dataset_id)
        if publication_id is None
        else find_record(root, store, dataset_id, publication_id)
    )
    if result is None:
        raise PublicationNotFoundError(f"no committed declaration: {dataset_id}/{publication_id}")
    return result


def open_binding(
    root: Path,
    dataset_id: str,
    *,
    publication_id: str | None = None,
    resources: Mapping[str, str | Path] | None = None,
) -> Binding[Publication] | Binding[Declaration]:
    metadata = read_repository_metadata(root)
    if isinstance(metadata, StoreRecord):
        head = read_head(root, metadata, dataset_id)
        result = (
            head
            if publication_id is None
            or (head is not None and head.declaration.publication_id == publication_id)
            else find_record(root, metadata, dataset_id, publication_id)
        )
        if result is None:
            raise PublicationNotFoundError(
                f"no committed declaration: {dataset_id}/{publication_id}"
            )
        if result != head:
            require_available(root, metadata, result)
        if result != head and result.declaration.capabilities.history is HistoryAccess.CURRENT_ONLY:
            raise HistoryUnavailableError("current_only declarations cannot open historical data")
        if result.declaration.capabilities.management is Management.MANAGED:
            assert metadata.managed_resource_id is not None
            bindings = dict(resources or {})
            managed_root = managed_data_root(root)
            given = bindings.get(metadata.managed_resource_id)
            if given is not None and absolute_root(given) != managed_root:
                raise InvalidDeclarationError("managed resource root cannot be redirected")
            bindings[metadata.managed_resource_id] = managed_root
            return Binding(root, result.declaration, resources=bindings)
        if resources is None:
            raise ResourceNotBoundError("registered binding requires explicit resource roots")
        return Binding(root, result.declaration, resources=resources)
    if resources is not None:
        raise InvalidDeclarationError("v1-v3 publications do not support resource mappings")
    version = metadata
    if publication_id is None:
        record = read_current(root, dataset_id, format_version=version)
    else:
        record = find_committed(root, dataset_id, publication_id, format_version=version)
        if (
            record is not None
            and record.publication.dataset.physical_history is PhysicalHistory.CURRENT_ONLY
        ):
            current = read_current(root, dataset_id, format_version=version)
            if current != record:
                raise HistoryUnavailableError(
                    f"dataset does not promise physical history: {dataset_id}"
                )
    if record is None:
        raise PublicationNotFoundError(
            f"publication is not committed: {dataset_id}/{publication_id or 'current'}"
        )
    return Binding(root, record.publication)
