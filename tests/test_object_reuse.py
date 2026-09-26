import io
import json
import os
from pathlib import Path

import pytest

from asterstore import (
    CandidateStateError,
    CollectionPolicy,
    Dataset,
    HistoryUnavailableError,
    InvalidDeclarationError,
    PhysicalHistory,
    PublicationConflictError,
    ReferenceConflictError,
    Repository,
    UnknownObjectError,
)
from asterstore.storage import candidate_directory, objects_directory


def initial(repository, history=PhysicalHistory.CURRENT_ONLY):
    with repository.prepare(Dataset("data", physical_history=history), publication_id="p1") as c:
        c.write_bytes("a.bin", b"a")
        c.write_bytes("b.bin", b"b")
        return c.commit()


def test_partial_reuse_preserves_paths_and_only_reclaims_unshared_members(tmp_path):
    repo = Repository(tmp_path)
    first = initial(repo)
    old_paths = repo.bind(first).files()
    with repo.prepare(first.dataset, publication_id="p2") as c:
        assert c.reuse(keys=[first.objects[1].key]) == (first.objects[1],)
        c.write_bytes("a.bin", b"changed-a")
        second = c.commit()
    paths = repo.open("data").files()
    assert paths[1] == old_paths[1]
    assert paths[0].read_bytes() == b"changed-a"
    assert old_paths[0].read_bytes() == b"a"
    assert list(objects_directory(tmp_path, c.candidate_id).iterdir()) == [paths[0]]
    report = repo.retention.preview(CollectionPolicy(("data",)))
    assert report.status == "complete"
    assert report.retiring_publications == (first,)
    assert report.reclaimable_objects == (first.objects[0],)
    assert second.objects[1] == first.objects[1]
    # A third generation can share an object created two generations earlier.
    with repo.prepare(first.dataset, publication_id="p3") as c:
        assert c.reuse() == second.objects
        third = c.commit()
    assert third.objects == second.objects
    assert repo.retention.preview(CollectionPolicy(("data",))).reclaimable_objects == (
        first.objects[0],
    )


def test_duplicate_and_invalid_selection_is_atomic(tmp_path):
    repo = Repository(tmp_path)
    first = initial(repo)
    a, b = (obj.key for obj in first.objects)
    with repo.prepare(first.dataset) as c:
        assert c.reuse(keys=[]) == ()
        with pytest.raises(InvalidDeclarationError):
            c.reuse(keys=[a, a])
        with pytest.raises(UnknownObjectError):
            c.reuse(keys=[a, "unknown.bin"])
        with pytest.raises(InvalidDeclarationError):
            c.reuse(keys=a)
        assert c.reuse(keys=[a]) == (first.objects[0],)
        with pytest.raises(InvalidDeclarationError):
            c.reuse(keys=[b, a])
        c.seal()
        with pytest.raises(CandidateStateError):
            c.reuse(keys=[b])
        assert c.commit().objects == (first.objects[0],)
    with pytest.raises(CandidateStateError):
        c.reuse()


@pytest.mark.parametrize("history", list(PhysicalHistory))
def test_historical_selection_respects_dataset_capability(tmp_path, history):
    repo = Repository(tmp_path)
    first = initial(repo, history)
    held = repo.retention.retain("old", "data")
    with repo.prepare(first.dataset, publication_id="p2") as c:
        c.commit()
    with repo.prepare(first.dataset, publication_id="p3") as c:
        if history is PhysicalHistory.CURRENT_ONLY:
            with pytest.raises(HistoryUnavailableError):
                c.reuse("p1")
            assert c.reuse_reference("old", expected_revision=held.revision) == first.objects
        else:
            assert c.reuse("p1") == first.objects
        assert c.commit().objects == first.objects


def test_reference_release_and_revision_reuse_cannot_select_a_new_target(tmp_path):
    repo = Repository(tmp_path)
    first = initial(repo)
    repo.retention.retain("run", "data")
    repo.retention.release("run", expected_revision=1)
    with repo.prepare(first.dataset) as c:
        with pytest.raises(ReferenceConflictError):
            c.reuse_reference("run", expected_revision=1)
    repo.retention.retain("run", "data", expected_revision=2)
    with repo.prepare(first.dataset) as c:
        with pytest.raises(ReferenceConflictError):
            c.reuse_reference("run", expected_revision=1)
        assert c.reuse_reference("run", expected_revision=3) == first.objects


def test_cross_dataset_reference_and_capability_mismatch_are_rejected(tmp_path):
    repo = Repository(tmp_path)
    initial(repo)
    repo.retention.retain("run", "data")
    with repo.prepare(Dataset("other")) as c:
        with pytest.raises(PublicationConflictError):
            c.reuse_reference("run", expected_revision=1)
    with repo.prepare(Dataset("data", physical_history=PhysicalHistory.VERSIONED)) as c:
        with pytest.raises(PublicationConflictError):
            c.reuse()


def test_recovery_keeps_sealed_members_after_reference_release(tmp_path):
    repo = Repository(tmp_path)
    first = initial(repo)
    repo.retention.retain("old", "data")
    with repo.prepare(first.dataset, publication_id="p2") as c:
        c.commit()
    with repo.prepare(first.dataset, publication_id="p3") as c:
        c.reuse_reference("old", expected_revision=1)
        c.write_bytes("c.bin", b"c")
        c.seal()
    repo.retention.release("old", expected_revision=1)
    report = repo.retention.preview(CollectionPolicy(("data",)))
    assert report.status == "complete" and report.retiring_publications == (first,)
    assert not report.reclaimable_objects
    with repo.resume(c.candidate_id) as resumed:
        result = resumed.commit()
    assert result.objects[1:] == first.objects
    assert [p.read_bytes() for p in repo.bind(result).files()] == [b"c", b"a", b"b"]


def test_conflicting_sealed_candidate_protects_shared_objects(tmp_path):
    repo = Repository(tmp_path)
    first = initial(repo)
    with repo.prepare(first.dataset, publication_id="pending") as pending:
        pending.reuse(keys=[first.objects[0].key])
        pending.seal()
    with repo.prepare(first.dataset, publication_id="p2") as current:
        current.commit()
    report = repo.retention.preview(CollectionPolicy(("data",)))
    assert report.status == "complete" and report.retiring_publications == (first,)
    assert report.reclaimable_objects == (first.objects[1],)
    with repo.resume(pending.candidate_id) as resumed:
        with pytest.raises(PublicationConflictError):
            resumed.commit()


@pytest.mark.parametrize("damage", ["missing", "directory"])
def test_explicit_recovery_checks_shared_files_before_switching_current(tmp_path, damage):
    repo = Repository(tmp_path)
    first = initial(repo)
    with repo.prepare(first.dataset) as c:
        c.reuse()
        c.seal()
    path = repo.bind(first).files()[0]
    path.unlink()
    if damage == "directory":
        from asterstore import StoreCorruptionError

        path.mkdir()
        error = StoreCorruptionError
    else:
        error = FileNotFoundError
    with repo.resume(c.candidate_id) as resumed:
        with pytest.raises(error):
            resumed.commit()
    assert repo.open("data").publication == first


def test_normal_reuse_never_probes_reads_copies_or_syncs_shared_files(tmp_path, monkeypatch):
    repo = Repository(tmp_path)
    first = initial(repo)
    shared = set(repo.bind(first).files())

    def checked(original):
        def wrapper(path, *args, **kwargs):
            if isinstance(path, (str, bytes, os.PathLike)):
                assert Path(os.fsdecode(path)) not in shared, f"shared data I/O: {path}"
            return original(path, *args, **kwargs)

        return wrapper

    with monkeypatch.context() as patch:
        patch.setattr(io, "open", checked(io.open))
        for name in ("open", "stat", "lstat"):
            patch.setattr(os, name, checked(getattr(os, name)))
        with repo.prepare(first.dataset) as c:
            c.reuse()
            c.write_bytes("new.bin", b"new")
            c.commit()
            c.commit()  # Durable retry also must not resync shared data.
        report = repo.retention.preview(CollectionPolicy(("data",)))
        assert report.status == "complete" and not report.reclaimable_objects


@pytest.mark.parametrize("bad_source", ["absent", "p2"])
def test_invalid_source_membership_blocks_preview(tmp_path, bad_source):
    repo = Repository(tmp_path)
    first = initial(repo, PhysicalHistory.VERSIONED)
    with repo.prepare(first.dataset, publication_id="p2") as c:
        c.commit()
    with repo.prepare(first.dataset, publication_id="pending") as c:
        c.reuse("p1")
        c.seal()
    path = candidate_directory(tmp_path, c.candidate_id) / "state.json"
    payload = json.loads(path.read_bytes())
    payload["reused_objects"][0]["publication_id"] = bad_source
    path.write_text(json.dumps(payload))
    report = repo.retention.preview(CollectionPolicy(("data",)))
    assert report.status == "blocked"
    assert not report.reclaimable_objects and not report.retiring_publications
    assert any("source membership" in issue.message for issue in report.issues)


@pytest.mark.parametrize("after_replace", [False, True])
def test_reuse_commit_response_loss_recovers_without_reselecting(
    tmp_path, monkeypatch, after_replace
):
    from asterstore.publishing import _commit

    repo = Repository(tmp_path)
    first = initial(repo)
    original = _commit.atomic_write

    def interrupted(path, data, *, durable=True):
        if after_replace:
            original(path, data, durable=durable)
        raise OSError("lost commit response")

    with repo.prepare(first.dataset, publication_id="p2") as c:
        c.reuse(keys=[first.objects[1].key])
        c.write_bytes("new.bin", b"new")
        with monkeypatch.context() as patch:
            patch.setattr(_commit, "atomic_write", interrupted)
            with pytest.raises(OSError):
                c.commit()
    assert repo.open("data").publication.publication_id == ("p2" if after_replace else "p1")
    with repo.resume(c.candidate_id) as resumed:
        second = resumed.commit()
    assert second.objects[1:] == first.objects[1:]
    with repo.prepare(first.dataset, publication_id="p3") as latest:
        latest.reuse()
        latest.commit()
    with repo.resume(c.candidate_id) as resumed:
        assert resumed.commit() == second
    assert repo.open("data").publication.publication_id == "p3"


def test_lost_seal_response_also_freezes_reused_members(tmp_path, monkeypatch):
    from asterstore.publishing import _candidate

    repo = Repository(tmp_path)
    first = initial(repo)
    original = _candidate.atomic_write
    with repo.prepare(first.dataset) as c:
        c.reuse(keys=[first.objects[0].key])

        def interrupted(path, data, *, durable=True):
            original(path, data, durable=durable)
            raise OSError("lost seal response")

        with monkeypatch.context() as patch:
            patch.setattr(_candidate, "atomic_write", interrupted)
            with pytest.raises(OSError):
                c.seal()
        with pytest.raises(CandidateStateError):
            c.reuse(keys=[first.objects[1].key])
        assert c.commit().objects == first.objects[:1]


@pytest.mark.parametrize("committed", [False, True])
def test_durable_resume_does_not_upgrade_shared_source_durability(tmp_path, monkeypatch, committed):
    from asterstore.publishing import _commit

    repo = Repository(tmp_path)
    with repo.prepare(Dataset("data"), durable=False) as source:
        source.write_bytes("shared.bin", b"weak persistence")
        first = source.commit()
    with repo.prepare(first.dataset, durable=False) as c:
        c.reuse()
        c.write_bytes("new.bin", b"new")
        c.seal()
        if committed:
            c.commit()
    synced = []
    original = _commit.sync_file

    def observed(path):
        synced.append(path)
        original(path)

    monkeypatch.setattr(_commit, "sync_file", observed)
    with repo.resume(c.candidate_id, durable=True) as resumed:
        resumed.commit()
    assert tmp_path / first.objects[0].key not in synced
    assert any(path.name == "new.bin" for path in synced)
    assert any(path.name == "state.json" for path in synced)


def test_foreign_repository_member_and_uncommitted_source_are_rejected(tmp_path):
    from asterstore import PublicationNotFoundError

    local = Repository(tmp_path / "local")
    foreign = Repository(tmp_path / "foreign")
    first = initial(local)
    other = initial(foreign)
    with local.prepare(first.dataset, publication_id="pending") as pending:
        pending.seal()
    with local.prepare(first.dataset) as c:
        with pytest.raises(UnknownObjectError):
            c.reuse(keys=[other.objects[0].key])
        with pytest.raises(PublicationNotFoundError):
            c.reuse("pending")
        assert c.commit().objects == ()
