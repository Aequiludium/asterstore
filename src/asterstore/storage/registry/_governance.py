"""Governance record locations and small read-side guards."""

from pathlib import Path

from asterstore.errors import (
    CandidateAbandonedError,
    PublicationRetiredError,
    StoreCorruptionError,
    UnsupportedCapabilityError,
)
from asterstore.metadata.protocol import (
    DeclarationRecord,
    FixedRetention,
    GovernancePlan,
    GovernanceProgress,
    ManagedRequest,
    RetiredDeclaration,
    StoreRecord,
    decode_governance_plan,
    decode_governance_progress,
    decode_managed_abandonment,
    decode_retention,
    decode_retired,
)

from ._managed import managed_directory, read_managed_request
from ._records import read_store, token


def lifecycle_store(root: Path) -> StoreRecord:
    store = read_store(root)
    if not store.lifecycle:
        raise UnsupportedCapabilityError("store must explicitly enable lifecycle governance")
    return store


def retention_path(root: Path, name: str) -> Path:
    return root / ".asterstore/fixed-retentions" / (token(name) + ".json")


def retired_path(root: Path, dataset_id: str, publication_id: str) -> Path:
    return root / ".asterstore/retired" / token(dataset_id) / (token(publication_id) + ".json")


def collection_directory(root: Path, operation_id: str) -> Path:
    return root / ".asterstore/collections-v4" / token(operation_id)


def read_retention(root: Path, store: StoreRecord, name: str) -> FixedRetention | None:
    try:
        data = retention_path(root, name).read_bytes()
    except FileNotFoundError:
        return None
    value = decode_retention(data)
    if value.store_id != store.store_id or value.name != name:
        raise StoreCorruptionError("retention identity does not match its store/path")
    return value


def read_retired(
    root: Path, store: StoreRecord, publication: DeclarationRecord
) -> RetiredDeclaration | None:
    if not store.lifecycle:
        return None
    declaration = publication.declaration
    try:
        data = retired_path(root, declaration.dataset_id, declaration.publication_id).read_bytes()
    except FileNotFoundError:
        return None
    value = decode_retired(data)
    if value.publication != publication or publication.store_id != store.store_id:
        raise StoreCorruptionError("retirement does not match original publication")
    return value


def require_available(root: Path, store: StoreRecord, publication: DeclarationRecord) -> None:
    if read_retired(root, store, publication) is not None:
        value = publication.declaration
        raise PublicationRetiredError(
            value.dataset_id, value.publication_id, publication.operation_id
        )


def read_abandoned(root: Path, store: StoreRecord, operation_id: str) -> ManagedRequest | None:
    if not store.lifecycle:
        return None
    try:
        data = (managed_directory(root, operation_id) / "abandoned.json").read_bytes()
    except FileNotFoundError:
        return None
    value = decode_managed_abandonment(data)
    if value != read_managed_request(root, store, operation_id):
        raise StoreCorruptionError("abandonment differs from candidate request")
    return value


def require_not_abandoned(root: Path, store: StoreRecord, operation_id: str) -> None:
    if read_abandoned(root, store, operation_id) is not None:
        raise CandidateAbandonedError(operation_id)


def read_governance_plan(
    root: Path, store: StoreRecord, operation_id: str
) -> GovernancePlan | None:
    try:
        data = (collection_directory(root, operation_id) / "plan.json").read_bytes()
    except FileNotFoundError:
        return None
    value = decode_governance_plan(data)
    if value.store_id != store.store_id or value.operation_id != operation_id:
        raise StoreCorruptionError("collection identity mismatch")
    return value


def read_governance_progress(root: Path, plan: GovernancePlan) -> GovernanceProgress:
    try:
        data = (collection_directory(root, plan.operation_id) / "progress.json").read_bytes()
    except FileNotFoundError:
        return GovernanceProgress(plan.store_id, plan.operation_id)
    value = decode_governance_progress(data)
    value.check_plan(plan)
    return value
