"""Source selection under an already active candidate's root shared lock."""

from collections.abc import Sequence
from pathlib import Path

from asterstore.errors import (
    HistoryUnavailableError,
    InvalidDeclarationError,
    PublicationConflictError,
    PublicationNotFoundError,
    ReferenceConflictError,
    ReferenceNotFoundError,
    StoreCorruptionError,
    UnknownObjectError,
)
from asterstore.metadata import (
    Dataset,
    ObjectRef,
    PhysicalHistory,
    PublishedRecord,
    Reference,
    ReusedObject,
    validate_generation,
)
from asterstore.storage import (
    dataset_directory,
    file_lock,
    find_committed,
    read_current,
    read_reference,
    reference_lock_path,
)


def _select(
    source: PublishedRecord, dataset: Dataset, keys: Sequence[str] | None
) -> tuple[ReusedObject, ...]:
    if source.publication.dataset != dataset:
        raise PublicationConflictError("reuse requires the same dataset and capabilities")
    if isinstance(keys, str):
        raise InvalidDeclarationError("keys must be a sequence of full object keys, not a string")
    members = {obj.key: obj for obj in source.publication.objects}
    selected = tuple(members) if keys is None else tuple(keys)
    result = []
    for key in selected:
        ObjectRef(key)
        if key not in members:
            raise UnknownObjectError(key)
        result.append(ReusedObject(key, source.publication.publication_id))
    return tuple(result)


def select_reuse(
    root: Path, dataset: Dataset, publication_id: str | None, keys: Sequence[str] | None
) -> tuple[ReusedObject, ...]:
    if publication_id is not None:
        Reference("reuse", dataset.dataset_id, publication_id)
    with file_lock(dataset_directory(root, dataset.dataset_id) / "commit.lock", exclusive=False):
        current = read_current(root, dataset.dataset_id)
        source = (
            current
            if publication_id is None
            else find_committed(
                root,
                dataset.dataset_id,
                publication_id,
            )
        )
        if source is None:
            raise PublicationNotFoundError(
                f"no committed reuse source: {dataset.dataset_id}/{publication_id}"
            )
        if (
            source.publication.dataset.physical_history is PhysicalHistory.CURRENT_ONLY
            and source != current
        ):
            raise HistoryUnavailableError(
                "current_only reuse requires current or an active reference"
            )
        return _select(source, dataset, keys)


def select_reference_reuse(
    root: Path, dataset: Dataset, name: str, *, expected_revision: int, keys: Sequence[str] | None
) -> tuple[ReusedObject, ...]:
    Reference(name, dataset.dataset_id, "lookup")
    validate_generation(expected_revision, minimum=1)
    with file_lock(reference_lock_path(root, name), exclusive=False):
        record = read_reference(root, name)
        if record is None:
            raise ReferenceNotFoundError(name)
        if record.state != "active" or record.revision != expected_revision:
            raise ReferenceConflictError("reuse reference is not active at the expected revision")
        if record.reference.dataset_id != dataset.dataset_id:
            raise PublicationConflictError("cannot reuse across datasets")
        with file_lock(
            dataset_directory(root, dataset.dataset_id) / "commit.lock", exclusive=False
        ):
            source = find_committed(root, dataset.dataset_id, record.reference.publication_id)
            if source is None:
                raise StoreCorruptionError("reuse reference has no committed target")
            return _select(source, dataset, keys)
