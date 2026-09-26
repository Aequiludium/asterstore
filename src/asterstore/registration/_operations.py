"""Registered entry points cannot acquire managed creation or deletion rights."""

from pathlib import Path

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
from asterstore.storage.registry import read_operation, read_store


def register(
    root: Path,
    declaration: Declaration,
    *,
    operation_id: str,
    expected_generation: int,
    durable: bool = True,
) -> DeclarationRecord:
    if not isinstance(declaration, Declaration):
        raise InvalidDeclarationError("expected a Declaration")
    if type(durable) is not bool:
        raise TypeError("durable must be a boolean")
    if declaration.capabilities.management is not Management.REGISTERED:
        raise UnsupportedCapabilityError("register accepts external declarations only")
    store = read_store(root)
    return commit_record(
        root,
        DeclarationRecord(store.store_id, operation_id, expected_generation, declaration),
        durable=durable,
    )


def resume_registration(
    root: Path, operation_id: str, *, durable: bool = True
) -> DeclarationRecord:
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
    )


def registration_status(root: Path, operation_id: str) -> RegistrationStatus:
    result = status(root, operation_id)
    if result.record.declaration.capabilities.management is not Management.REGISTERED:
        raise UnsupportedCapabilityError("use managed candidate status")
    return result
