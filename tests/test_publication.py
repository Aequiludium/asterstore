from pathlib import Path

import pytest

from asterstore import (
    CandidateStateError,
    Dataset,
    HistoryUnavailableError,
    InvalidDeclarationError,
    PhysicalHistory,
    PublicationConflictError,
    PublicationNotFoundError,
    Repository,
    RepositoryNotInitializedError,
)


def test_publish_reopen_and_keep_old_binding(tmp_path: Path) -> None:
    repository = Repository(tmp_path)
    dataset = Dataset("market/prices", PhysicalHistory.VERSIONED)
    with repository.prepare(dataset, publication_id="p1") as candidate:
        output = candidate.path("day=2026-09-01/data.bin")
        output.write_bytes(b"old")
        with pytest.raises(PublicationNotFoundError):
            repository.open(dataset.dataset_id)
        declaration = candidate.commit()
        assert candidate.commit() == declaration
        first_id = candidate.candidate_id
    old = repository.open(dataset.dataset_id)
    with repository.prepare(dataset, publication_id="p2") as candidate:
        candidate.write_bytes("day=2026-09-01/data.bin", b"new")
        assert candidate.expected_generation == 1
        candidate.commit()
    reopened = Repository(tmp_path).open(dataset.dataset_id)
    assert old.files()[0].read_bytes() == b"old"
    assert reopened.files()[0].read_bytes() == b"new"
    historical = repository.open(dataset.dataset_id, publication_id="p1")
    assert historical.publication == declaration
    assert repository.candidate_status(first_id).state == "historical"
    with repository.resume(first_id) as original:
        assert original.commit() == declaration
    assert repository.open(dataset.dataset_id).publication.publication_id == "p2"


def test_current_only_does_not_advertise_history(tmp_path: Path) -> None:
    repository = Repository(tmp_path)
    for name in ("p1", "p2"):
        with repository.prepare(Dataset("prices"), publication_id=name, durable=False) as candidate:
            candidate.commit()
    assert repository.open("prices", publication_id="p2").files() == ()
    with pytest.raises(HistoryUnavailableError):
        repository.open("prices", publication_id="p1")


def test_publication_ids_and_dataset_capabilities_cannot_be_reused(tmp_path: Path) -> None:
    repository = Repository(tmp_path)
    with repository.prepare(Dataset("prices"), publication_id="p1", durable=False) as candidate:
        candidate.commit()
    with repository.prepare(Dataset("prices"), publication_id="p1", durable=False) as candidate:
        with pytest.raises(PublicationConflictError):
            candidate.commit()
    with repository.prepare(
        Dataset("prices", PhysicalHistory.VERSIONED), durable=False
    ) as candidate:
        with pytest.raises(PublicationConflictError):
            candidate.commit()
    assert repository.open("prices").publication.publication_id == "p1"


def test_explicit_stale_generation_fails_without_advancing_current(tmp_path: Path) -> None:
    repository = Repository(tmp_path)
    with repository.prepare(Dataset("prices"), durable=False) as first:
        first.commit()
    with repository.prepare(Dataset("prices"), expected_generation=0, durable=False) as stale:
        stale.write_bytes("part.bin", b"stale")
        with pytest.raises(PublicationConflictError, match="generation"):
            stale.commit()
        stale_id = stale.candidate_id
    assert repository.candidate_status(stale_id).state == "conflict"
    with repository.resume(stale_id, durable=False) as retry:
        with pytest.raises(PublicationConflictError):
            retry.commit()


def test_copy_is_independent_of_external_mutation(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"original")
    repository = Repository(tmp_path / "repository")
    with repository.prepare(Dataset("data")) as candidate:
        candidate.copy_file("part.bin", source)
        candidate.commit()
    source.write_bytes(b"changed")
    assert repository.open("data").files()[0].read_bytes() == b"original"


def test_context_exit_does_not_publish_and_unsealed_work_cannot_resume(tmp_path: Path) -> None:
    repository = Repository(tmp_path)
    with repository.prepare(Dataset("data"), durable=False) as candidate:
        candidate.write_bytes("part.bin", b"uncommitted")
        candidate_id = candidate.candidate_id
    assert repository.candidate_status(candidate_id).state == "writing"
    with pytest.raises(PublicationNotFoundError):
        repository.open("data")
    with pytest.raises(CandidateStateError, match="unsealed"):
        with repository.resume(candidate_id):
            pass
    with pytest.raises(CandidateStateError):
        candidate.commit()


def test_sealing_requires_closed_regular_outputs_and_freezes_membership(tmp_path: Path) -> None:
    repository = Repository(tmp_path)
    with repository.prepare(Dataset("data"), durable=False) as candidate:
        candidate.path("missing.bin")
        with pytest.raises(FileNotFoundError):
            candidate.seal()
        candidate.write_bytes("missing.bin", b"completed")
        candidate.seal()
        with pytest.raises(CandidateStateError, match="sealed"):
            candidate.write_bytes("extra.bin", b"extra")
        candidate.commit()


def test_invalid_options_fail_before_repository_creation(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "not-created")
    with pytest.raises(InvalidDeclarationError):
        repository.prepare(Dataset("data"), publication_id="")
    with pytest.raises(InvalidDeclarationError):
        repository.prepare(Dataset("data"), expected_generation=True)
    with pytest.raises(RepositoryNotInitializedError):
        repository.open("data")
    assert not repository.root.exists()


def test_nondurable_mode_does_not_call_fsync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    def forbidden(fd: int) -> None:
        raise AssertionError("durable=False must not request fsync")

    monkeypatch.setattr(os, "fsync", forbidden)
    repository = Repository(tmp_path)
    with repository.prepare(Dataset("data"), durable=False) as candidate:
        candidate.write_bytes("part.bin", b"data")
        candidate.commit()
    assert repository.open("data").files()[0].read_bytes() == b"data"
