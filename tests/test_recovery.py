from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from asterstore import CandidateStateError, Dataset, PublicationNotFoundError, Repository
from asterstore.publishing import _candidate, _commit, _recovery
from asterstore.storage import _files, history_path


@pytest.mark.parametrize("after_replace", [False, True])
def test_commit_failure_before_or_after_visibility_is_recoverable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    after_replace: bool,
) -> None:
    repository = Repository(tmp_path)
    with repository.prepare(Dataset("data"), publication_id="old") as first:
        first.write_bytes("part.bin", b"old")
        first.commit()
    original = _commit.atomic_write

    def interrupted(path: Path, data: bytes, *, durable: bool = True) -> None:
        if after_replace:
            original(path, data, durable=durable)
        raise OSError("injected lost commit response")

    with repository.prepare(Dataset("data"), publication_id="new") as candidate:
        candidate.write_bytes("part.bin", b"new")
        with monkeypatch.context() as patch:
            patch.setattr(_commit, "atomic_write", interrupted)
            with pytest.raises(OSError):
                candidate.commit()
        candidate_id = candidate.candidate_id
    expected = "new" if after_replace else "old"
    assert repository.open("data").publication.publication_id == expected
    assert repository.candidate_status(candidate_id).state == (
        "current" if after_replace else "prepared"
    )
    with repository.resume(candidate_id) as retry:
        result = retry.commit()
    assert result.publication_id == "new"
    assert repository.candidate_status(candidate_id).current_generation == 2
    assert repository.open("data").files()[0].read_bytes() == b"new"


def test_uncommitted_sealed_record_is_not_a_publication(tmp_path: Path) -> None:
    repository = Repository(tmp_path)
    with repository.prepare(Dataset("data"), publication_id="pending", durable=False) as candidate:
        candidate.write_bytes("part.bin", b"data")
        candidate.seal()
        candidate_id = candidate.candidate_id
    assert repository.candidate_status(candidate_id).state == "prepared"
    with pytest.raises(PublicationNotFoundError):
        repository.open("data", publication_id="pending")
    with repository.resume(candidate_id) as resumed:
        resumed.commit()
    assert repository.open("data").files()[0].read_bytes() == b"data"


def test_lost_seal_response_disallows_new_members_and_can_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = Repository(tmp_path)
    original = _candidate.atomic_write
    with repository.prepare(Dataset("data"), durable=False) as candidate:
        candidate.write_bytes("part.bin", b"data")

        def interrupted(path: Path, data: bytes, *, durable: bool = True) -> None:
            original(path, data, durable=durable)
            raise OSError("seal response lost")

        with monkeypatch.context() as patch:
            patch.setattr(_candidate, "atomic_write", interrupted)
            with pytest.raises(OSError):
                candidate.seal()
        with pytest.raises(CandidateStateError):
            candidate.path("extra.bin")
        candidate.commit()
    assert len(repository.open("data").files()) == 1


def test_resume_detects_missing_sealed_output_before_publication(tmp_path: Path) -> None:
    repository = Repository(tmp_path)
    with repository.prepare(Dataset("data"), durable=False) as candidate:
        output = candidate.write_bytes("part.bin", b"data")
        candidate.seal()
        candidate_id = candidate.candidate_id
    output.unlink()
    with repository.resume(candidate_id) as retry:
        with pytest.raises(FileNotFoundError):
            retry.commit()
    with pytest.raises(PublicationNotFoundError):
        repository.open("data")


@pytest.mark.parametrize("already_committed", [False, True])
def test_durable_resume_syncs_data_and_metadata_before_acknowledging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    already_committed: bool,
) -> None:
    repository = Repository(tmp_path)
    with repository.prepare(Dataset("data"), durable=False) as candidate:
        candidate.write_bytes("nested/part.bin", b"data")
        candidate.seal()
        if already_committed:
            candidate.commit()
        candidate_id = candidate.candidate_id
    synced_files: list[Path] = []
    synced_directories: list[Path] = []
    real_sync_file = _commit.sync_file
    real_sync_directory = _commit.sync_directory

    def record_file(path: Path) -> None:
        synced_files.append(path)
        real_sync_file(path)

    def record_directory(path: Path) -> None:
        synced_directories.append(path)
        real_sync_directory(path)

    monkeypatch.setattr(_commit, "sync_file", record_file)
    monkeypatch.setattr(_commit, "sync_directory", record_directory)
    with repository.resume(candidate_id, durable=True) as retry:
        retry.commit()
    control = tmp_path / ".asterstore"
    assert control / "format.json" in synced_files
    assert control / "candidates" / candidate_id / "state.json" in synced_files
    assert any(path.name == "part.bin" for path in synced_files)
    assert {tmp_path, tmp_path.parent, control, control / "candidates"} <= set(synced_directories)
    assert repository.open("data").files()[0].read_bytes() == b"data"


def test_status_reloads_candidate_after_waiting_for_commit_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = Repository(tmp_path)
    original = _recovery.file_lock
    with repository.prepare(Dataset("data"), durable=False) as candidate:
        candidate.write_bytes("part.bin", b"data")

        @contextmanager
        def concurrent_commit(path: Path, *, exclusive: bool) -> Iterator[None]:
            if path.name == "commit.lock":
                candidate.commit()
            with original(path, exclusive=exclusive):
                yield

        monkeypatch.setattr(_recovery, "file_lock", concurrent_commit)
        assert repository.candidate_status(candidate.candidate_id).state == "current"


def test_durable_recovery_syncs_an_existing_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = Repository(tmp_path)
    with repository.prepare(Dataset("data"), publication_id="old", durable=False) as first:
        first.commit()
    with repository.prepare(Dataset("data"), durable=False) as candidate:
        candidate_id = candidate.candidate_id

        def fail_before_current(*args: object, **kwargs: object) -> None:
            raise OSError("before current replacement")

        with monkeypatch.context() as patch:
            patch.setattr(_commit, "atomic_write", fail_before_current)
            with pytest.raises(OSError):
                candidate.commit()
    archive = history_path(tmp_path, "data", "old")
    assert archive.exists()
    synced: list[Path] = []
    original = _files.sync_file

    def record_sync(path: Path) -> None:
        synced.append(path)
        original(path)

    monkeypatch.setattr(_files, "sync_file", record_sync)
    with repository.resume(candidate_id, durable=True) as retry:
        retry.commit()
    assert archive in synced
