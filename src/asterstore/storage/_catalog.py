"""Control-record I/O; public file bindings never poll this catalog."""

from pathlib import Path

from asterstore.errors import (
    CandidateAbandonedError,
    PublicationRetiredError,
    RepositoryNotInitializedError,
    RepositoryUpgradeRequiredError,
    StoreCorruptionError,
)
from asterstore.metadata import (
    CURRENT_FORMAT_VERSION,
    AbandonedCandidate,
    CandidateManifest,
    PublishedRecord,
    RetiredRecord,
    decode_candidate_state,
    decode_history,
    decode_publication,
    decode_repository,
    encode_repository,
)

from ._files import atomic_write, ensure_directory, sync_directory, sync_file
from ._layout import candidate_directory, control_directory, dataset_directory, history_path
from ._locks import file_lock


def check_repository(root: Path, *, writable: bool = False) -> int:
    marker = control_directory(root) / "format.json"
    try:
        data = marker.read_bytes()
    except FileNotFoundError as exc:
        raise RepositoryNotInitializedError(f"repository is not initialized: {root}") from exc
    version = decode_repository(data)
    if writable and version != CURRENT_FORMAT_VERSION:
        raise RepositoryUpgradeRequiredError(
            "v1/v2 repositories are read-only; writes require v3; no automatic upgrade"
        )
    return version


def initialize_repository(root: Path, *, durable: bool) -> None:
    try:
        check_repository(root, writable=True)
    except RepositoryNotInitializedError:
        pass
    control = control_directory(root)
    ensure_directory(control, durable=durable)
    with file_lock(control / ".init.lock", exclusive=True):
        try:
            check_repository(root, writable=True)
        except RepositoryNotInitializedError:
            unexpected = [
                path.name
                for path in control.iterdir()
                if path.name != ".init.lock" and not path.name.startswith(".format.json.")
            ]
            if unexpected:
                raise StoreCorruptionError(
                    "control directory lacks its marker but is not empty"
                ) from None
            atomic_write(control / "format.json", encode_repository(), durable=durable)
        if durable:
            sync_file(control / "format.json")
            sync_directory(control)
            sync_directory(root)
            sync_directory(root.parent)


def read_current(
    root: Path, dataset_id: str, *, format_version: int = CURRENT_FORMAT_VERSION
) -> PublishedRecord | None:
    path = dataset_directory(root, dataset_id) / "current.json"
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    record = decode_publication(data, expected_version=format_version)
    if record.publication.dataset.dataset_id != dataset_id:
        raise StoreCorruptionError(f"current record belongs to another dataset: {path}")
    return record


def find_publication_record(
    root: Path,
    dataset_id: str,
    publication_id: str,
    *,
    format_version: int = CURRENT_FORMAT_VERSION,
) -> PublishedRecord | RetiredRecord | None:
    path = history_path(root, dataset_id, publication_id)
    current = read_current(root, dataset_id, format_version=format_version)
    if current is not None and current.publication.publication_id == publication_id:
        return current
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    record = decode_history(data, expected_version=format_version)
    if isinstance(record, RetiredRecord):
        if record.dataset.dataset_id != dataset_id or record.publication_id != publication_id:
            raise StoreCorruptionError("retirement identity differs from its path")
        return record
    if (
        record.publication.dataset.dataset_id != dataset_id
        or record.publication.publication_id != publication_id
    ):
        raise StoreCorruptionError(f"history record identity differs from its path: {path}")
    return record


def read_candidate_state(
    root: Path, candidate_id: str, *, format_version: int = CURRENT_FORMAT_VERSION
) -> CandidateManifest | AbandonedCandidate:
    path = candidate_directory(root, candidate_id) / "state.json"
    record = decode_candidate_state(path.read_bytes(), expected_version=format_version)
    manifest = record.manifest if isinstance(record, AbandonedCandidate) else record
    if manifest.candidate_id != candidate_id:
        raise StoreCorruptionError(f"candidate ID differs from its path: {path}")
    return record


def read_candidate(
    root: Path, candidate_id: str, *, format_version: int = CURRENT_FORMAT_VERSION
) -> CandidateManifest:
    record = read_candidate_state(root, candidate_id, format_version=format_version)
    if isinstance(record, AbandonedCandidate):
        raise CandidateAbandonedError(candidate_id)
    return record


def find_committed(
    root: Path,
    dataset_id: str,
    publication_id: str,
    *,
    format_version: int = CURRENT_FORMAT_VERSION,
) -> PublishedRecord | None:
    record = find_publication_record(
        root, dataset_id, publication_id, format_version=format_version
    )
    if isinstance(record, RetiredRecord):
        raise PublicationRetiredError(dataset_id, publication_id, record.candidate_id)
    return record
