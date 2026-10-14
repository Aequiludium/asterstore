"""Registered entry points cannot acquire managed creation or deletion rights."""

from collections.abc import Callable, Mapping
from pathlib import Path

from asterstore._data_checks import Check, CheckOptions
from asterstore.errors import (
    InvalidDeclarationError,
    PublicationNotFoundError,
    UnsupportedCapabilityError,
)
from asterstore.metadata.capabilities import Management
from asterstore.metadata.declarations import Declaration
from asterstore.metadata.protocol import DeclarationRecord
from asterstore.publishing.transactions import RegistrationStatus, commit_record
from asterstore.publishing.transactions import registration_status as status
from asterstore.publishing.transactions._commit import checked_commit
from asterstore.reading import Binding
from asterstore.reading.resources import ResourceMap
from asterstore.storage.registry import read_operation, read_store


def register(
    root: Path,
    declaration: Declaration,
    *,
    operation_id: str,
    expected_generation: int,
    durable: bool = True,
    check: Check = "none",
    resources: Mapping[str, str | Path] | None = None,
    validator: Callable[[Path], None] | None = None,
    checksums: Mapping[str, str] | None = None,
) -> DeclarationRecord:
    options = CheckOptions(check, validator, checksums)
    if not isinstance(declaration, Declaration):
        raise InvalidDeclarationError("expected a Declaration")
    if type(durable) is not bool:
        raise TypeError("durable must be a boolean")
    if declaration.capabilities.management is not Management.REGISTERED:
        raise UnsupportedCapabilityError("register accepts external declarations only")
    store = read_store(root)
    record = DeclarationRecord(store.store_id, operation_id, expected_generation, declaration)
    if check == "none":
        return commit_record(root, record, durable=durable)
    binding = Binding(root, declaration, resources=resources)
    assert binding.resources is not None
    mapping = ResourceMap(binding.resources)
    paths = {obj.object_id: mapping.locate(obj.locator) for obj in declaration.files.objects}
    return checked_commit(root, record, lambda: options.run(record, paths), durable=durable)


def resume_registration(
    root: Path,
    operation_id: str,
    *,
    durable: bool = True,
    check: Check = "none",
    resources: Mapping[str, str | Path] | None = None,
    validator: Callable[[Path], None] | None = None,
    checksums: Mapping[str, str] | None = None,
) -> DeclarationRecord:
    options = CheckOptions(check, validator, checksums)
    store = read_store(root)
    plan = read_operation(root, store, operation_id)
    if plan is None:
        raise PublicationNotFoundError(f"registration operation does not exist: {operation_id}")
    return register(
        root,
        plan.declaration,
        operation_id=operation_id,
        expected_generation=plan.expected_generation,
        durable=durable,
        check=options.check,
        resources=resources,
        validator=options.validator,
        checksums=options.checksums,
    )


def registration_status(root: Path, operation_id: str) -> RegistrationStatus:
    result = status(root, operation_id)
    if result.record.declaration.capabilities.management is not Management.REGISTERED:
        raise UnsupportedCapabilityError("use managed candidate status")
    return result
