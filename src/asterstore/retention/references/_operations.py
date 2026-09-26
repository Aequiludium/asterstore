"""Coordinated named references. No data-file validation on this path."""

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from asterstore.errors import (
    HistoryUnavailableError,
    PublicationNotFoundError,
    ReferenceConflictError,
    ReferenceNotFoundError,
    StoreCorruptionError,
)
from asterstore.metadata import (
    CURRENT_FORMAT_VERSION,
    Dataset,
    PhysicalHistory,
    Publication,
    PublishedRecord,
    Reference,
    ReferenceRecord,
    encode_reference,
    validate_generation,
)
from asterstore.metadata.identity import validate_record_identifiers
from asterstore.reading import Binding
from asterstore.storage import (
    atomic_write,
    check_repository,
    control_directory,
    dataset_directory,
    file_lock,
    find_committed,
    history_path,
    read_current,
    read_reference,
    reference_lock_path,
    reference_path,
    sync_control_files,
)


@dataclass(frozen=True, slots=True)
class RetainedPublication:
    reference: Reference
    revision: int
    binding: Binding[Publication]


def _validate(name: str, revision: int, *, durable: bool = True, minimum: int = 0) -> None:
    Reference(name, "validate", "validate")
    validate_generation(revision, minimum=minimum)
    if type(durable) is not bool:
        raise TypeError("durable must be a boolean")


def _binding(root: Path, record: ReferenceRecord) -> RetainedPublication:
    ref = record.reference
    target = find_committed(
        root, ref.dataset_id, ref.publication_id, format_version=record.format_version
    )
    if target is None:
        raise StoreCorruptionError(f"active reference has no committed target: {ref.name}")
    return RetainedPublication(ref, record.revision, Binding(root, target.publication))


def _sync(root: Path, record: ReferenceRecord, *, target: PublishedRecord | None = None) -> None:
    paths = [control_directory(root) / "format.json"]
    if target is not None:
        ref = record.reference
        current = read_current(root, ref.dataset_id)
        paths.append(
            dataset_directory(root, ref.dataset_id) / "current.json"
            if current == target
            else history_path(root, ref.dataset_id, ref.publication_id)
        )
    paths.append(reference_path(root, record.reference.name))
    sync_control_files(root, tuple(paths))


def retain(
    root: Path,
    name: str,
    dataset_id: str,
    *,
    publication_id: str | None = None,
    expected_revision: int = 0,
    durable: bool = True,
) -> RetainedPublication:
    _validate(name, expected_revision, durable=durable)
    validate_record_identifiers(
        CURRENT_FORMAT_VERSION,
        name=name,
        dataset_id=dataset_id,
        publication_id="validate" if publication_id is None else publication_id,
    )
    Dataset(dataset_id)
    if publication_id is not None:
        Reference(name, dataset_id, publication_id)
    selection: Literal["current", "publication"] = (
        "current" if publication_id is None else "publication"
    )
    check_repository(root, writable=True)
    with file_lock(control_directory(root) / "gc.lock", exclusive=False):
        check_repository(root, writable=True)
        with file_lock(reference_lock_path(root, name), exclusive=True):
            with file_lock(dataset_directory(root, dataset_id) / "commit.lock", exclusive=True):
                existing = read_reference(root, name)
                if existing is not None and existing.state == "active":
                    if (
                        existing.revision != expected_revision + 1
                        or existing.reference.dataset_id != dataset_id
                        or existing.selection != selection
                        or (
                            publication_id is not None
                            and existing.reference.publication_id != publication_id
                        )
                    ):
                        raise ReferenceConflictError(f"reference create conflicts: {name}")
                    result = _binding(root, existing)
                    if durable:
                        target = find_committed(root, dataset_id, existing.reference.publication_id)
                        assert target is not None
                        _sync(root, existing, target=target)
                    return result
                observed = existing.revision if existing is not None else 0
                if observed != expected_revision:
                    raise ReferenceConflictError(
                        f"reference revision changed: expected {expected_revision}, "
                        f"found {observed}"
                    )
                current = read_current(root, dataset_id)
                target = (
                    current
                    if publication_id is None
                    else find_committed(root, dataset_id, publication_id)
                )
                if target is None:
                    raise PublicationNotFoundError(
                        f"no committed target: {dataset_id}/{publication_id}"
                    )
                if (
                    target.publication.dataset.physical_history is PhysicalHistory.CURRENT_ONLY
                    and target != current
                ):
                    raise HistoryUnavailableError("new current_only references must select current")
                record = ReferenceRecord(
                    Reference(name, dataset_id, target.publication.publication_id),
                    expected_revision + 1,
                    "active",
                    selection,
                )
                if durable:
                    target_path = (
                        dataset_directory(root, dataset_id) / "current.json"
                        if target == current
                        else history_path(root, dataset_id, target.publication.publication_id)
                    )
                    sync_control_files(root, (control_directory(root) / "format.json", target_path))
                atomic_write(reference_path(root, name), encode_reference(record), durable=durable)
                if durable:
                    _sync(root, record)
                return RetainedPublication(
                    record.reference, record.revision, Binding(root, target.publication)
                )


def get(root: Path, name: str) -> ReferenceRecord | None:
    _validate(name, 0)
    check_repository(root)
    with file_lock(control_directory(root) / "gc.lock", exclusive=False):
        version = check_repository(root)
        return read_reference(root, name, format_version=version)


def open_reference(root: Path, name: str, *, expected_revision: int) -> RetainedPublication:
    _validate(name, expected_revision, minimum=1)
    check_repository(root)
    with file_lock(control_directory(root) / "gc.lock", exclusive=False):
        version = check_repository(root)
        with file_lock(reference_lock_path(root, name), exclusive=False):
            record = read_reference(root, name, format_version=version)
            if record is None:
                raise ReferenceNotFoundError(name)
            if record.state != "active" or record.revision != expected_revision:
                raise ReferenceConflictError(
                    f"reference is no longer active at this revision: {name}"
                )
            with file_lock(
                dataset_directory(root, record.reference.dataset_id) / "commit.lock",
                exclusive=False,
            ):
                return _binding(root, record)


def release(
    root: Path, name: str, *, expected_revision: int, durable: bool = True
) -> ReferenceRecord:
    _validate(name, expected_revision, durable=durable, minimum=1)
    check_repository(root, writable=True)
    with file_lock(control_directory(root) / "gc.lock", exclusive=False):
        check_repository(root, writable=True)
        with file_lock(reference_lock_path(root, name), exclusive=True):
            record = read_reference(root, name)
            if record is None:
                raise ReferenceNotFoundError(name)
            if record.state == "released" and record.revision == expected_revision + 1:
                if durable:
                    _sync(root, record)
                return record
            if record.state != "active" or record.revision != expected_revision:
                raise ReferenceConflictError(f"reference release conflicts: {name}")
            released = replace(record, state="released", revision=record.revision + 1)
            atomic_write(reference_path(root, name), encode_reference(released), durable=durable)
            if durable:
                _sync(root, released)
            return released
