"""Explicit persistent binding. No data-file probing or retention side effects."""

from collections.abc import Callable, Mapping
from pathlib import Path

from asterstore._data_checks import Check, CheckOptions
from asterstore.errors import (
    HistoryUnavailableError,
    InvalidDeclarationError,
    PublicationNotFoundError,
    ResourceNotBoundError,
)
from asterstore.metadata.capabilities import HistoryAccess, Management
from asterstore.metadata.protocol import DeclarationRecord
from asterstore.storage import absolute_root
from asterstore.storage.registry import (
    find_record,
    managed_data_root,
    read_head,
    read_store,
    require_available,
)

from ._binding import Binding
from .resources import ResourceMap


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
    check: Check = "none",
    validator: Callable[[Path], None] | None = None,
    checksums: Mapping[str, str] | None = None,
) -> Binding:
    options = CheckOptions(check, validator, checksums)
    metadata = read_store(root)
    head = read_head(root, metadata, dataset_id)
    result = (
        head
        if publication_id is None
        or (head is not None and head.declaration.publication_id == publication_id)
        else find_record(root, metadata, dataset_id, publication_id)
    )
    if result is None:
        raise PublicationNotFoundError(f"no committed declaration: {dataset_id}/{publication_id}")
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
        binding = Binding(root, result.declaration, resources=bindings)
    else:
        if resources is None:
            raise ResourceNotBoundError("registered binding requires explicit resource roots")
        binding = Binding(root, result.declaration, resources=resources)
    if check != "none":
        assert binding.resources is not None
        mapping = ResourceMap(binding.resources)
        options.run(
            result,
            {
                obj.object_id: mapping.locate(obj.locator)
                for obj in result.declaration.files.objects
            },
            managed_root=root
            if result.declaration.capabilities.management is Management.MANAGED
            else None,
        )
    return binding
