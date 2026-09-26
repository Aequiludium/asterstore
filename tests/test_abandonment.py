import io
import json
import os
from pathlib import Path

import pytest

from asterstore import (
    CandidateAbandonedError,
    CandidateStateError,
    CollectionPolicy,
    Dataset,
    Repository,
    StoreCorruptionError,
)
from asterstore.publishing import _abandon, _commit
from asterstore.retention.cleanup import _operations
from asterstore.storage import candidate_directory, objects_directory


def pending(repo, *, seal=True):
    with repo.prepare(Dataset("data"), publication_id="pending", durable=False) as candidate:
        candidate.write_bytes("nested/a", b"a")
        candidate.write_bytes("b", b"bb")
        if seal:
            candidate.seal()
    return candidate.candidate_id


@pytest.mark.parametrize("seal", [False, True])
def test_abandonment_is_permanent_and_cleanup_is_explicit(tmp_path, seal):
    repo = Repository(tmp_path)
    cid = pending(repo, seal=seal)
    files = tuple((candidate_directory(tmp_path, cid) / "files").rglob("*"))
    before = repo.inspection.candidates()
    assert before.status == "complete"
    row = before.candidates[0]
    assert row.state == ("prepared" if seal else "writing")
    assert row.sealed == seal and len(row.files) == 2 and row.total_bytes == 3
    with pytest.raises(CandidateStateError):
        repo.retention.cleanup_candidate(cid)
    status = repo.abandon(cid)
    assert status.state == repo.candidate_status(cid).state == "abandoned"
    assert repo.abandon(cid) == status
    assert all(path.exists() for path in files)
    with pytest.raises(CandidateAbandonedError):
        with repo.resume(cid):
            pass
    result = repo.retention.cleanup_candidate(cid)
    assert result.status == "complete" and result.planned
    assert set(result.deleted_files) == set(row.files)
    assert repo.retention.cleanup_candidate(cid) == result
    assert repo.inspection.candidates().candidates[0].files == ()
    assert repo.inspection.candidates().candidates[0].cleanup_state == "complete"
    assert (candidate_directory(tmp_path, cid) / "state.json").exists()


def test_abandonment_releases_shared_protection_without_cleanup_touching_source(tmp_path, publish):
    repo = Repository(tmp_path)
    first = publish("first")
    with repo.prepare(first.dataset, publication_id="pending", durable=False) as c:
        c.reuse()
        c.write_bytes("private", b"private")
        c.seal()
    publish("second")
    policy = CollectionPolicy(("data",))
    assert repo.inspection.candidates().candidates[0].state == "conflict"
    assert not repo.retention.preview(policy).reclaimable_objects
    repo.abandon(c.candidate_id)
    assert repo.retention.preview(policy).reclaimable_objects == first.objects
    cleaned = repo.retention.cleanup_candidate(c.candidate_id)
    assert len(cleaned.deleted_files) == 1
    assert repo.bind(first).files()[0].read_bytes() == b"first"
    result = repo.retention.collect(policy)
    assert result.status == "complete" and result.deleted_objects == first.objects
    assert repo.candidate_status(c.candidate_id).state == "abandoned"
    assert repo.inspection.candidates().status == "complete"


def test_installed_but_unpublished_candidate_can_be_abandoned_and_cleaned(tmp_path, monkeypatch):
    repo = Repository(tmp_path)
    with repo.prepare(Dataset("data"), durable=False) as candidate:
        candidate.write_bytes("part", b"never committed")
        with monkeypatch.context() as patch:
            patch.setattr(
                _commit, "atomic_write", lambda *a, **kw: (_ for _ in ()).throw(OSError("stop"))
            )
            with pytest.raises(OSError):
                candidate.commit()
    report = repo.inspection.candidates()
    assert report.candidates[0].locations == ("installed",)
    repo.abandon(candidate.candidate_id)
    result = repo.retention.cleanup_candidate(candidate.candidate_id)
    assert result.status == "complete"
    assert result.deleted_files == (f".asterstore/objects/{candidate.candidate_id}/part",)


@pytest.mark.parametrize("retired", [False, True])
def test_committed_and_retired_candidates_cannot_be_abandoned(tmp_path, retired):
    repo = Repository(tmp_path)
    with repo.prepare(Dataset("data"), durable=False) as c:
        c.write_bytes("part", b"committed")
        c.commit()
    with repo.prepare(Dataset("data"), durable=False) as current:
        current.commit()
    if retired:
        repo.retention.collect(CollectionPolicy(("data",)))
    with pytest.raises(CandidateStateError):
        repo.abandon(c.candidate_id)
    with pytest.raises(CandidateStateError):
        repo.retention.cleanup_candidate(c.candidate_id)
    assert repo.inspection.candidates().candidates == ()


@pytest.mark.parametrize("after", [False, True])
def test_abandon_response_loss_is_retryable_and_never_deletes(tmp_path, monkeypatch, after):
    repo = Repository(tmp_path)
    cid = pending(repo)
    original = _abandon.atomic_write

    def fail(path, data):
        if after:
            original(path, data)
        raise OSError("lost response")

    with monkeypatch.context() as patch:
        patch.setattr(_abandon, "atomic_write", fail)
        with pytest.raises(OSError):
            repo.abandon(cid)
    assert repo.candidate_status(cid).state == ("abandoned" if after else "prepared")
    assert repo.abandon(cid).state == "abandoned"
    assert len(repo.inspection.candidates().candidates[0].files) == 2


@pytest.mark.parametrize("after", [False, True])
def test_cleanup_plan_failure_does_not_delete_and_retry_stays_bounded(tmp_path, monkeypatch, after):
    repo = Repository(tmp_path)
    cid = pending(repo, seal=False)
    repo.abandon(cid)
    original = _operations.immutable_write

    def fail(path, data):
        if after:
            original(path, data)
        raise OSError("plan response lost")

    with monkeypatch.context() as patch:
        patch.setattr(_operations, "immutable_write", fail)
        with pytest.raises(OSError):
            repo.retention.cleanup_candidate(cid)
    assert len(repo.inspection.candidates().candidates[0].files) == 2
    extra = candidate_directory(tmp_path, cid) / "files/extra"
    extra.write_bytes(b"after plan")
    result = repo.retention.cleanup_candidate(cid)
    assert result.status == "complete"
    assert extra.exists() == after
    assert len(result.deleted_files) == (2 if after else 3)
    if after:
        assert repo.inspection.candidates().status == "blocked"


@pytest.mark.parametrize("after", [False, True])
def test_cleanup_partial_delete_recovers_with_lagging_progress(tmp_path, monkeypatch, after):
    repo = Repository(tmp_path)
    cid = pending(repo)
    repo.abandon(cid)
    original = _operations.delete_candidate_file
    calls = 0

    def fail(root, candidate_id, key):
        nonlocal calls
        calls += 1
        if calls == 2:
            if after:
                original(root, candidate_id, key)
            raise OSError("unlink interrupted")
        return original(root, candidate_id, key)

    with monkeypatch.context() as patch:
        patch.setattr(_operations, "delete_candidate_file", fail)
        result = repo.retention.cleanup_candidate(cid)
    assert result.status == "blocked" and len(result.deleted_files) == 1
    assert repo.inspection.candidates().candidates[0].cleanup_state == "pending"
    resumed = repo.retention.cleanup_candidate(cid)
    assert resumed.status == "complete"
    assert len(resumed.deleted_files) + len(resumed.missing_files) == 2
    assert len(resumed.missing_files) == int(after)


@pytest.mark.parametrize("damage", ["symlink", "parent_symlink", "fifo", "both_locations"])
def test_unsafe_private_locations_block_cleanup_without_following_links(tmp_path, damage):
    repo = Repository(tmp_path / "repo")
    cid = pending(repo, seal=False)
    repo.abandon(cid)
    base = candidate_directory(repo.root, cid) / "files"
    external = tmp_path / "external"
    external.mkdir()
    (external / "a").write_bytes(b"keep")
    if damage == "symlink":
        (base / "link").symlink_to(external / "a")
    elif damage == "parent_symlink":
        (base / "nested/a").unlink()
        (base / "nested").rmdir()
        (base / "nested").symlink_to(external, target_is_directory=True)
    elif damage == "fifo":
        os.mkfifo(base / "fifo")
    else:
        objects_directory(repo.root, cid).mkdir(parents=True)
    result = repo.retention.cleanup_candidate(cid)
    assert result.status == "blocked" and not result.planned
    assert (base / "b").exists() and (external / "a").read_bytes() == b"keep"
    assert repo.inspection.candidates().status == "blocked"


def test_unregistered_native_writer_outputs_are_owned_private_files(tmp_path):
    repo = Repository(tmp_path)
    cid = pending(repo, seal=False)
    scratch = candidate_directory(tmp_path, cid) / "files/writer-temp"
    scratch.write_bytes(b"temporary native writer output")
    repo.abandon(cid)
    result = repo.retention.cleanup_candidate(cid)
    assert (
        result.status == "complete" and str(scratch.relative_to(tmp_path)) in result.deleted_files
    )


def test_corrupt_plan_cannot_delete_other_candidate_or_control_files(tmp_path):
    repo = Repository(tmp_path)
    cid = pending(repo)
    repo.abandon(cid)
    result = repo.retention.cleanup_candidate(cid)
    assert result.status == "complete"
    plan = candidate_directory(tmp_path, cid) / "cleanup-plan.json"
    value = json.loads(plan.read_bytes())
    value["keys"].append(".asterstore/format.json")
    plan.write_text(json.dumps(value))
    assert repo.retention.cleanup_candidate(cid).status == "blocked"
    assert (tmp_path / ".asterstore/format.json").exists()
    assert repo.retention.preview(CollectionPolicy(("data",))).status == "blocked"


def test_diagnosis_reports_unknown_locations_and_never_reads_data_contents(tmp_path, monkeypatch):
    repo = Repository(tmp_path)
    cid = pending(repo, seal=False)
    unknown = tmp_path / ".asterstore/objects/unknown"
    unknown.mkdir(parents=True)
    (unknown / "file").write_bytes(b"untouched")
    original = io.open

    def check(file, *args, **kwargs):
        path = Path(file)
        assert not path.is_relative_to(candidate_directory(tmp_path, cid) / "files")
        assert not path.is_relative_to(tmp_path / ".asterstore/objects")
        return original(file, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(io, "open", check)
        report = repo.inspection.candidates()
    assert report.status == "blocked"
    assert report.unknown_locations == (".asterstore/objects/unknown",)
    assert report.candidates[0].total_bytes == 3
    assert (unknown / "file").read_bytes() == b"untouched"


def test_abandonment_sync_failure_leaves_shared_protection_unchanged(tmp_path, monkeypatch):
    repo = Repository(tmp_path)
    cid = pending(repo)

    def fail(*args):
        raise OSError("sync failed")

    monkeypatch.setattr(_abandon, "sync_control_files", fail)
    with pytest.raises(OSError):
        repo.abandon(cid)
    assert repo.candidate_status(cid).state == "prepared"


def test_cleanup_requires_valid_control_evidence(tmp_path):
    repo = Repository(tmp_path)
    cid = pending(repo)
    (candidate_directory(tmp_path, cid) / "state.json").write_bytes(b"invalid")
    with pytest.raises(StoreCorruptionError):
        repo.abandon(cid)
    assert repo.retention.cleanup_candidate(cid).status == "blocked"
    assert repo.inspection.candidates().status == "blocked"


def test_cleanup_cannot_follow_a_parent_replaced_after_the_plan(tmp_path, monkeypatch):
    repo = Repository(tmp_path / "repo")
    cid = pending(repo)
    repo.abandon(cid)
    original = _operations.immutable_write

    def stop_after_plan(path, data):
        original(path, data)
        raise OSError("plan persisted")

    with monkeypatch.context() as patch:
        patch.setattr(_operations, "immutable_write", stop_after_plan)
        with pytest.raises(OSError):
            repo.retention.cleanup_candidate(cid)
    base = candidate_directory(repo.root, cid) / "files"
    (base / "nested/a").unlink()
    (base / "nested").rmdir()
    external = tmp_path / "external"
    external.mkdir()
    (external / "a").write_bytes(b"untouched")
    (base / "nested").symlink_to(external, target_is_directory=True)
    result = repo.retention.cleanup_candidate(cid)
    assert result.status == "blocked"
    assert (external / "a").read_bytes() == b"untouched"


def test_completion_loss_retries_without_deleting_reappeared_files(tmp_path, monkeypatch):
    repo = Repository(tmp_path)
    cid = pending(repo)
    repo.abandon(cid)
    original = _operations.atomic_write

    def stop_after_progress(path, data):
        original(path, data)
        raise RuntimeError("process stopped")

    with monkeypatch.context() as patch:
        patch.setattr(_operations, "atomic_write", stop_after_progress)
        with pytest.raises(RuntimeError):
            repo.retention.cleanup_candidate(cid)
    path = candidate_directory(tmp_path, cid) / "files/b"
    path.write_bytes(b"reappeared")
    assert repo.retention.cleanup_candidate(cid).status == "complete"
    assert path.read_bytes() == b"reappeared"
    assert repo.inspection.candidates().status == "blocked"


def test_gc_syncs_abandonment_before_deleting_released_shared_objects(
    tmp_path, publish, monkeypatch
):
    from asterstore.retention.collection import _execution

    repo = Repository(tmp_path)
    first = publish("first")
    with repo.prepare(first.dataset, durable=False) as c:
        c.reuse()
        c.seal()
    publish("second")
    repo.abandon(c.candidate_id)
    synced = set()
    original_sync = _execution.sync_control_files
    original_delete = _execution.delete_object

    def observe_sync(root, paths):
        synced.update(paths)
        original_sync(root, paths)

    def check_delete(root, key):
        assert candidate_directory(root, c.candidate_id) / "state.json" in synced
        return original_delete(root, key)

    monkeypatch.setattr(_execution, "sync_control_files", observe_sync)
    monkeypatch.setattr(_execution, "delete_object", check_delete)
    assert repo.retention.collect(CollectionPolicy(("data",))).deleted_objects == first.objects


def test_abandon_and_preview_never_probe_candidate_data(tmp_path, monkeypatch):
    repo = Repository(tmp_path)
    cid = pending(repo)
    base = candidate_directory(tmp_path, cid) / "files"

    def checked(original):
        def wrapper(path, *args, **kwargs):
            if isinstance(path, (str, bytes, os.PathLike)):
                assert not Path(os.fsdecode(path)).is_relative_to(base)
                assert not Path(os.fsdecode(path)).is_relative_to(tmp_path / ".asterstore/objects")
            return original(path, *args, **kwargs)

        return wrapper

    with monkeypatch.context() as patch:
        patch.setattr(io, "open", checked(io.open))
        for name in ("open", "stat", "lstat", "scandir", "listdir"):
            patch.setattr(os, name, checked(getattr(os, name)))
        repo.abandon(cid)
        assert repo.retention.preview(CollectionPolicy(("data",))).status == "complete"


@pytest.mark.parametrize("damage", ["identity", "membership", "missing_plan", "committed_state"])
def test_corrupt_cleanup_evidence_is_diagnosable_without_crashing(tmp_path, damage):
    repo = Repository(tmp_path)
    cid = pending(repo)
    repo.abandon(cid)
    repo.retention.cleanup_candidate(cid)
    directory = candidate_directory(tmp_path, cid)
    progress = directory / "cleanup-progress.json"
    payload = json.loads(progress.read_bytes())
    if damage == "identity":
        payload["candidate_id"] = "f" * 32
        payload["deleted"] = []
        progress.write_text(json.dumps(payload))
    elif damage == "membership":
        payload["deleted"].append(f".asterstore/candidates/{cid}/files/unknown")
        progress.write_text(json.dumps(payload))
    elif damage == "missing_plan":
        (directory / "cleanup-plan.json").unlink()
    else:
        state = json.loads((directory / "state.json").read_bytes())
        (directory / "state.json").write_text(json.dumps(state["manifest"]))
    report = repo.inspection.candidates()
    assert report.status == "blocked" and report.issues
    assert repo.retention.cleanup_candidate(cid).status == "blocked"
