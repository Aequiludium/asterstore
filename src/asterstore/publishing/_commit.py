"""One-dataset commit. Caller owns the candidate and holds the root GC shared lock."""

import os
import stat
from pathlib import Path

from asterstore.errors import (
    PublicationConflictError,
    PublicationRetiredError,
    StoreCorruptionError,
)
from asterstore.metadata import (
    CandidateManifest,
    Publication,
    PublishedRecord,
    RetiredRecord,
    encode_publication,
    object_creator,
)
from asterstore.storage import (
    atomic_write,
    candidate_directory,
    control_directory,
    dataset_directory,
    ensure_directory,
    file_lock,
    find_publication_record,
    history_path,
    immutable_write,
    objects_directory,
    read_current,
    sync_directory,
    sync_file,
)


def _sync_committed(root: Path, record: PublishedRecord) -> None:
    """Re-establish durability before acknowledging an ambiguous commit retry."""
    dataset_id = record.publication.dataset.dataset_id
    current = read_current(root, dataset_id)
    record_path = (
        dataset_directory(root, dataset_id) / "current.json"
        if current == record
        else history_path(root, dataset_id, record.publication.publication_id)
    )
    files = [
        root / obj.key
        for obj in record.publication.objects
        if object_creator(obj.key) == record.candidate_id
    ]
    files.extend(
        (
            record_path,
            control_directory(root) / "format.json",
            candidate_directory(root, record.candidate_id) / "state.json",
        )
    )
    _sync_paths(root, files, {objects_directory(root, record.candidate_id)})


def _sync_paths(root: Path, files: list[Path], directories: set[Path]) -> None:
    """Sync bytes and the directory entries that make them reachable."""
    for path in files:
        sync_file(path)
        directories.add(path.parent)
    for directory in tuple(directories):
        parent = directory
        while parent != root.parent:
            directories.add(parent)
            parent = parent.parent
    directories.add(root.parent)
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        sync_directory(directory)


def _verify_shared_files(root: Path, manifest: CandidateManifest) -> None:
    """Explicit recovery probes shared files once; it never reselects their source."""
    for item in manifest.reused_objects:
        if not stat.S_ISREG((root / item.key).lstat().st_mode):
            raise StoreCorruptionError(f"reused object is not a regular file: {item.key}")


def _verify_recovered_files(
    root: Path,
    manifest: CandidateManifest,
    location: Path,
    *,
    durable: bool,
) -> None:
    """Recovery checks its sealed outputs once, before making them visible."""
    _verify_shared_files(root, manifest)
    files = []
    assert manifest.keys is not None
    for key in manifest.keys:
        path = location.joinpath(*key.split("/"))
        if not stat.S_ISREG(path.lstat().st_mode):
            raise StoreCorruptionError(f"recovered candidate output is not a regular file: {key}")
        files.append(path)
    if durable:
        files.extend(
            (
                candidate_directory(root, manifest.candidate_id) / "state.json",
                control_directory(root) / "format.json",
            )
        )
        _sync_paths(root, files, {location})


def commit_candidate(
    root: Path,
    manifest: CandidateManifest,
    *,
    durable: bool,
    recovering: bool = False,
) -> Publication:
    desired = PublishedRecord(
        manifest.expected_generation + 1, manifest.candidate_id, manifest.publication()
    )
    dataset_id = manifest.dataset.dataset_id
    directory = dataset_directory(root, dataset_id)
    ensure_directory(directory, durable=durable)
    with file_lock(directory / "commit.lock", exclusive=True):
        committed = find_publication_record(root, dataset_id, manifest.publication_id)
        if committed is not None:
            if committed.candidate_id != manifest.candidate_id:
                raise PublicationConflictError("publication ID was used by a different candidate")
            if isinstance(committed, RetiredRecord):
                raise PublicationRetiredError(
                    dataset_id, manifest.publication_id, manifest.candidate_id
                )
            if committed != desired:
                raise StoreCorruptionError("committed record differs from its candidate manifest")
            if recovering:
                _verify_shared_files(root, manifest)
            if durable:
                _sync_committed(root, committed)
            return committed.publication

        current = read_current(root, dataset_id)
        generation = current.generation if current is not None else 0
        if generation != manifest.expected_generation:
            raise PublicationConflictError(
                f"generation changed: expected {manifest.expected_generation}, found {generation}"
            )
        if current is not None and current.publication.dataset != manifest.dataset:
            raise PublicationConflictError("dataset capabilities cannot change during publication")
        if current is None:
            history = directory / "history"
            if history.exists() and any(history.iterdir()):
                raise StoreCorruptionError("current is missing but committed history exists")

        staged = candidate_directory(root, manifest.candidate_id) / "files"
        destination = objects_directory(root, manifest.candidate_id)
        ensure_directory(destination.parent, durable=durable)
        if recovering:
            _verify_recovered_files(
                root, manifest, staged if staged.exists() else destination, durable=durable
            )
        if staged.exists():
            if destination.exists():
                raise StoreCorruptionError("both staged and installed candidate objects exist")
            os.rename(staged, destination)
        elif not destination.is_dir():
            raise StoreCorruptionError("sealed candidate objects are missing")
        if durable:
            # Repeat both directory syncs on recovery after an ambiguous rename.
            sync_directory(destination.parent)
            sync_directory(staged.parent)

        if current is not None:
            immutable_write(
                history_path(root, dataset_id, current.publication.publication_id),
                encode_publication(current),
                durable=durable,
            )
        atomic_write(directory / "current.json", encode_publication(desired), durable=durable)
        return desired.publication
