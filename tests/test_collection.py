"""Real managed-file collection, exact ownership and bounded recovery."""

import json

import pytest

from asterstore import (
    CollectionPolicy,
    Dataset,
    PhysicalHistory,
    PublicationConflictError,
    PublicationRetiredError,
    Repository,
)
from asterstore.retention.collection import _execution
from asterstore.storage import history_path

POLICY = CollectionPolicy(("data",))


def publish(repo, pid, keys=("a", "b"), reuse=()):
    dataset = Dataset("data", physical_history=PhysicalHistory.VERSIONED)
    with repo.prepare(dataset, publication_id=pid, durable=False) as c:
        for key in keys:
            c.write_bytes(key, key.encode())
        if reuse:
            c.reuse(keys=list(reuse))
        return c.commit(), c.candidate_id


def test_collect_keeps_shared_files_and_independent_references(tmp_path):
    repo = Repository(tmp_path)
    p1, first_id = publish(repo, "p1")
    repo.retention.retain("a", "data", durable=False)
    repo.retention.retain("b", "data", durable=False)
    p2, _ = publish(repo, "p2", ("c",), (p1.objects[1].key,))
    assert not repo.retention.collect(POLICY).retired_publications
    repo.retention.release("a", expected_revision=1, durable=False)
    assert not repo.retention.collect(POLICY).retired_publications
    repo.retention.release("b", expected_revision=1, durable=False)
    report = repo.retention.collect(POLICY)
    assert report.status == "complete" and report.retired_publications == (p1,)
    assert report.deleted_objects == p1.objects[:1]
    assert not (tmp_path / p1.objects[0].key).exists()
    assert (tmp_path / p1.objects[1].key).read_bytes() == b"b"
    assert repo.open("data").publication == p2
    assert repo.candidate_status(first_id).state == "retired"
    assert repo.retention.resume_collection(report.operation_id) == report
    assert not repo.retention.preview(POLICY).reclaimable_objects
    with pytest.raises(PublicationRetiredError):
        repo.open("data", publication_id="p1")
    with pytest.raises(PublicationRetiredError):
        repo.retention.retain("late", "data", publication_id="p1")
    with repo.resume(first_id) as candidate:
        with pytest.raises(PublicationRetiredError):
            candidate.commit()
    with repo.prepare(p1.dataset, publication_id="p1") as candidate:
        with pytest.raises(PublicationConflictError):
            candidate.commit()
    with repo.prepare(p1.dataset) as candidate:
        with pytest.raises(PublicationRetiredError):
            candidate.reuse("p1")


def test_later_collection_finds_objects_of_previously_retired_creators(tmp_path):
    repo = Repository(tmp_path)
    p1, _ = publish(repo, "p1")
    p2, _ = publish(repo, "p2", ("c",), (p1.objects[1].key,))
    assert repo.retention.collect(POLICY).status == "complete"
    publish(repo, "p3", ("d",))
    preview = repo.retention.preview(POLICY)
    assert set(preview.reclaimable_objects) == set(p2.objects)
    result = repo.retention.collect(POLICY)
    assert result.status == "complete" and set(result.deleted_objects) == set(p2.objects)
    assert not repo.retention.preview(POLICY).reclaimable_objects
    assert repo.retention.collect(POLICY).deleted_objects == ()


def test_sealed_candidate_survives_source_retirement_and_can_resume(tmp_path):
    repo = Repository(tmp_path)
    first, _ = publish(repo, "p1")
    publish(repo, "p2", ("c",))
    with repo.prepare(first.dataset, publication_id="p3") as candidate:
        candidate.reuse("p1", keys=[first.objects[0].key])
        candidate.seal()
    result = repo.retention.collect(POLICY)
    assert result.status == "complete" and result.retired_publications == (first,)
    assert result.deleted_objects == first.objects[1:]
    with repo.resume(candidate.candidate_id) as resumed:
        third = resumed.commit()
    assert third.objects == first.objects[:1]
    assert repo.retention.preview(POLICY).status == "complete"


def interrupt_retirement(monkeypatch, *, after=False, index=1):
    original = _execution.atomic_write
    count = 0

    def interrupted(path, data, *, durable=True):
        nonlocal count
        if path.parent.name == "history":
            count += 1
            if count == index:
                if after:
                    original(path, data, durable=durable)
                raise OSError("retirement interrupted")
        original(path, data, durable=durable)

    monkeypatch.setattr(_execution, "atomic_write", interrupted)


@pytest.mark.parametrize("after", [False, True])
def test_retirement_failure_stops_all_deletion_then_resumes(tmp_path, monkeypatch, after):
    repo = Repository(tmp_path)
    first, _ = publish(repo, "p1")
    second, _ = publish(repo, "p2")
    publish(repo, "p3")
    with monkeypatch.context() as patch:
        interrupt_retirement(patch, after=after, index=2)
        result = repo.retention.collect(POLICY)
    assert result.status == "blocked" and result.operation_id
    assert all((tmp_path / obj.key).exists() for obj in first.objects + second.objects)
    preview = repo.retention.preview(POLICY)
    assert preview.status == "blocked" and preview.pending_operations == (result.operation_id,)
    assert repo.retention.collect(POLICY).status == "blocked"
    resumed = repo.retention.resume_collection(result.operation_id)
    assert resumed.status == "complete"
    assert set(resumed.deleted_objects) == set(first.objects + second.objects)
    assert repo.retention.preview(POLICY).status == "complete"


def test_recovery_rechecks_new_reference_and_stays_within_original_scope(tmp_path, monkeypatch):
    repo = Repository(tmp_path)
    first, _ = publish(repo, "p1")
    second, _ = publish(repo, "p2")
    with monkeypatch.context() as patch:
        interrupt_retirement(patch)
        result = repo.retention.collect(POLICY)
    repo.retention.retain("rescued", "data", publication_id="p1")
    publish(repo, "p3")  # p2 is now eligible, but outside the pending operation's target list.
    resumed = repo.retention.resume_collection(result.operation_id)
    assert resumed.status == "complete" and not resumed.retired_publications
    assert set(resumed.protected_objects) == set(first.objects)
    assert repo.open("data", publication_id="p2").publication == second
    assert all(path.exists() for path in repo.bind(first).files())


@pytest.mark.parametrize("after_unlink", [False, True])
def test_delete_failure_and_lagging_progress_resume_exact_original_keys(
    tmp_path, monkeypatch, after_unlink
):
    repo = Repository(tmp_path)
    first, _ = publish(repo, "p1")
    publish(repo, "p2")
    original = _execution.delete_object
    calls = 0

    def interrupted(root, key):
        nonlocal calls
        calls += 1
        if calls == 2:
            if after_unlink:
                original(root, key)
            raise OSError("unlink response lost")
        return original(root, key)

    with monkeypatch.context() as patch:
        patch.setattr(_execution, "delete_object", interrupted)
        result = repo.retention.collect(POLICY)
    assert result.status == "blocked" and len(result.deleted_objects) == 1
    resumed = repo.retention.resume_collection(result.operation_id)
    assert resumed.status == "complete"
    assert set(resumed.deleted_objects + resumed.missing_objects) == set(first.objects)
    assert len(resumed.missing_objects) == int(after_unlink)
    assert repo.retention.resume_collection(result.operation_id) == resumed


@pytest.mark.parametrize("damage", ["symlink", "parent_symlink", "directory"])
def test_delete_refuses_unexpected_types_and_preserves_unmanaged_files(tmp_path, damage):
    repo = Repository(tmp_path / "repo")
    first, _ = publish(repo, "p1", ("nested/a",))
    publish(repo, "p2")
    target = repo.bind(first).files()[0]
    unknown = target.parent / "unknown"
    unknown.write_bytes(b"keep")
    target.unlink()
    external = tmp_path / "external"
    external.mkdir()
    (external / "a").write_bytes(b"external")
    if damage == "symlink":
        target.symlink_to(external / "a")
    elif damage == "directory":
        target.mkdir()
    else:
        target.parent.rename(target.parent.with_name("saved"))
        target.parent.symlink_to(external, target_is_directory=True)
    result = repo.retention.collect(POLICY)
    assert result.status == "blocked" and result.remaining_objects == first.objects
    assert (external / "a").read_bytes() == b"external"
    if damage != "parent_symlink":
        assert unknown.read_bytes() == b"keep"


def test_missing_file_and_empty_parents_are_recoverable_without_recursive_delete(tmp_path):
    repo = Repository(tmp_path)
    first, _ = publish(repo, "p1", ("nested/a",))
    publish(repo, "p2")
    path = repo.bind(first).files()[0]
    path.unlink()
    path.parent.rmdir()
    result = repo.retention.collect(POLICY)
    assert result.status == "complete" and result.missing_objects == first.objects


def test_sync_failure_before_plan_never_retires_or_deletes(tmp_path, monkeypatch):
    repo = Repository(tmp_path)
    first, _ = publish(repo, "p1")
    publish(repo, "p2")

    def fail(*args):
        raise OSError("sync failed")

    monkeypatch.setattr(_execution, "sync_control_files", fail)
    with pytest.raises(OSError):
        repo.retention.collect(POLICY)
    assert repo.open("data", publication_id="p1").publication == first
    assert all(path.exists() for path in repo.bind(first).files())


def test_corrupt_plan_cannot_expand_deletion(tmp_path, monkeypatch):
    repo = Repository(tmp_path)
    first, _ = publish(repo, "p1")
    current, _ = publish(repo, "p2")
    with monkeypatch.context() as patch:
        interrupt_retirement(patch)
        result = repo.retention.collect(POLICY)
    plan = tmp_path / ".asterstore/collections" / result.operation_id / "plan.json"
    data = json.loads(plan.read_bytes())
    data["objects"].append(current.objects[0].key)
    plan.write_text(json.dumps(data))
    resumed = repo.retention.resume_collection(result.operation_id)
    assert resumed.status == "blocked"
    assert all(path.exists() for path in repo.bind(first).files() + repo.bind(current).files())
    assert repo.open("data", publication_id="p1").publication == first


def test_deleted_objects_leave_small_retirement_records(tmp_path):
    repo = Repository(tmp_path)
    first, _ = publish(repo, "p1")
    publish(repo, "p2")
    result = repo.retention.collect(POLICY)
    payload = json.loads(history_path(tmp_path, "data", "p1").read_bytes())
    assert payload["kind"] == "retired_publication" and "objects" not in payload
    assert payload["collection_id"] == result.operation_id
    assert result.retired_publications == (first,)


def test_recovery_protects_new_sealed_user_of_an_original_delete_target(tmp_path, monkeypatch):
    repo = Repository(tmp_path)
    first, _ = publish(repo, "p1")
    publish(repo, "p2")
    with monkeypatch.context() as patch:
        interrupt_retirement(patch)
        result = repo.retention.collect(POLICY)
    with repo.prepare(first.dataset) as candidate:
        candidate.reuse("p1", keys=[first.objects[0].key])
        candidate.seal()
    resumed = repo.retention.resume_collection(result.operation_id)
    assert resumed.status == "complete" and resumed.retired_publications == (first,)
    assert resumed.protected_objects == first.objects[:1]
    assert resumed.deleted_objects == first.objects[1:]
    with repo.resume(candidate.candidate_id) as candidate:
        assert candidate.commit().objects == first.objects[:1]


def test_post_retirement_sync_failure_stops_deletion(tmp_path, monkeypatch):
    repo = Repository(tmp_path)
    first, _ = publish(repo, "p1")
    publish(repo, "p2")
    original = _execution.sync_control_files
    calls = 0

    def interrupted(root, paths):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("retired controls not synced")
        original(root, paths)

    with monkeypatch.context() as patch:
        patch.setattr(_execution, "sync_control_files", interrupted)
        result = repo.retention.collect(POLICY)
    assert result.status == "blocked"
    assert all(path.exists() for path in repo.bind(first).files())
    assert repo.retention.resume_collection(result.operation_id).status == "complete"


def test_gc_syncs_weak_control_facts_before_delete_but_never_reads_data(tmp_path, monkeypatch):
    import io
    import os
    from pathlib import Path

    repo = Repository(tmp_path)
    first, first_id = publish(repo, "p1")
    repo.retention.retain("weak", "data", durable=False)
    _, current_id = publish(repo, "p2")
    repo.retention.release("weak", expected_revision=1, durable=False)
    synced = set()
    original_sync = _execution.sync_control_files
    original_delete = _execution.delete_object
    original_open = io.open

    def checked_open(file, *args, **kwargs):
        if isinstance(file, (str, bytes, os.PathLike)):
            assert not Path(os.fsdecode(file)).is_relative_to(tmp_path / ".asterstore/objects")
        return original_open(file, *args, **kwargs)

    def observe_sync(root, paths):
        synced.update(paths)
        original_sync(root, paths)

    def observe_delete(root, key):
        from asterstore.storage import candidate_directory, dataset_directory, reference_path

        assert history_path(root, "data", "p1") in synced
        assert reference_path(root, "weak") in synced
        assert dataset_directory(root, "data") / "current.json" in synced
        assert candidate_directory(root, first_id) / "state.json" in synced
        assert candidate_directory(root, current_id) / "state.json" in synced
        assert any(path.name == "plan.json" for path in synced)
        return original_delete(root, key)

    monkeypatch.setattr(io, "open", checked_open)
    monkeypatch.setattr(_execution, "sync_control_files", observe_sync)
    monkeypatch.setattr(_execution, "delete_object", observe_delete)
    result = repo.retention.collect(POLICY)
    assert result.status == "complete" and set(result.deleted_objects) == set(first.objects)


@pytest.mark.parametrize("after_write", [False, True])
def test_plan_failure_has_no_retirement_and_persisted_plan_can_resume(
    tmp_path, monkeypatch, after_write
):
    repo = Repository(tmp_path)
    first, _ = publish(repo, "p1")
    publish(repo, "p2")
    original = _execution.immutable_write

    def fail(path, data):
        if after_write:
            original(path, data)
        raise OSError("plan response lost")

    with monkeypatch.context() as patch:
        patch.setattr(_execution, "immutable_write", fail)
        with pytest.raises(OSError):
            repo.retention.collect(POLICY)
    assert repo.open("data", publication_id="p1").publication == first
    pending = repo.retention.preview(POLICY).pending_operations
    if after_write:
        assert len(pending) == 1
        assert repo.retention.resume_collection(pending[0]).status == "complete"
    else:
        assert not pending
        assert repo.retention.collect(POLICY).status == "complete"


def test_completion_response_loss_retries_without_redeleting(tmp_path, monkeypatch):
    repo = Repository(tmp_path)
    first, _ = publish(repo, "p1")
    publish(repo, "p2")
    original = _execution.atomic_write

    def lose_response(path, data, *, durable=True):
        original(path, data, durable=durable)
        if json.loads(data).get("state") == "complete":
            raise RuntimeError("process stopped after completion")

    with monkeypatch.context() as patch:
        patch.setattr(_execution, "atomic_write", lose_response)
        with pytest.raises(RuntimeError):
            repo.retention.collect(POLICY)
    operation = next((tmp_path / ".asterstore/collections").iterdir()).name

    def forbidden(*args):
        raise AssertionError("completed collection must not unlink again")

    monkeypatch.setattr(_execution, "delete_object", forbidden)
    result = repo.retention.resume_collection(operation)
    assert result.status == "complete" and set(result.deleted_objects) == set(first.objects)


def test_unknown_files_and_empty_object_directories_are_left_intact(tmp_path):
    repo = Repository(tmp_path)
    first, _ = publish(repo, "p1", ("nested/a",))
    publish(repo, "p2")
    path = repo.bind(first).files()[0]
    unknown = path.with_name("unknown")
    unknown.write_bytes(b"keep")
    assert repo.retention.collect(POLICY).status == "complete"
    assert path.parent.is_dir() and unknown.read_bytes() == b"keep"


@pytest.mark.parametrize("damage", ["missing_plan", "candidate", "progress", "current_retired"])
def test_corrupt_retirement_evidence_blocks_future_execution(tmp_path, damage):
    from asterstore.storage import candidate_directory, dataset_directory

    repo = Repository(tmp_path)
    first, candidate_id = publish(repo, "p1")
    current, _ = publish(repo, "p2")
    done = repo.retention.collect(POLICY)
    operation = tmp_path / ".asterstore/collections" / done.operation_id
    if damage == "missing_plan":
        (operation / "plan.json").unlink()
    elif damage == "candidate":
        (candidate_directory(tmp_path, candidate_id) / "state.json").unlink()
    elif damage == "progress":
        (operation / "progress.json").write_bytes(b"invalid")
    else:
        (dataset_directory(tmp_path, "data") / "current.json").write_bytes(
            history_path(tmp_path, "data", "p1").read_bytes()
        )
    assert repo.retention.preview(POLICY).status == "blocked"
    assert repo.retention.collect(POLICY).status == "blocked"
    assert all(path.exists() for path in repo.bind(current).files())


def test_opaque_ids_survive_recovery_retention_reuse_and_collection(tmp_path):
    from asterstore.storage import dataset_directory, reference_path

    root = tmp_path / "repository"
    repo = Repository(root)
    dataset = Dataset("market:../原始\\prices")
    first_pid = "sample:run-001:events"
    reference_name = "../training:run/../模型"
    with repo.prepare(dataset, publication_id=first_pid, durable=False) as candidate:
        candidate.write_bytes("part.bin", b"original")
        candidate.seal()
        first_id = candidate.candidate_id
    with repo.resume(first_id, durable=False) as candidate:
        first = candidate.commit()
    held = repo.retention.retain(reference_name, dataset.dataset_id, durable=False)
    with repo.prepare(dataset, publication_id="../second:version", durable=False) as candidate:
        candidate.reuse_reference(reference_name, expected_revision=held.revision)
        second = candidate.commit()
    policy = CollectionPolicy((dataset.dataset_id,))
    assert not repo.retention.collect(policy).deleted_objects
    repo.retention.release(reference_name, expected_revision=held.revision, durable=False)
    result = repo.retention.collect(policy)
    assert result.retired_publications == (first,) and not result.deleted_objects
    assert repo.open(dataset.dataset_id).publication == second
    assert repo.open(dataset.dataset_id).files()[0].read_bytes() == b"original"
    with repo.prepare(dataset, publication_id="/third:version", durable=False) as candidate:
        candidate.write_bytes("latest.bin", b"latest")
        candidate.commit()
    result = repo.retention.collect(policy)
    assert result.deleted_objects == first.objects
    assert repo.retention.resume_collection(result.operation_id) == result
    assert repo.candidate_status(first_id).state == "retired"
    assert dataset_directory(root, dataset.dataset_id).parent == root / ".asterstore/datasets"
    assert reference_path(root, reference_name).parent == root / ".asterstore/references"
    assert len(dataset_directory(root, dataset.dataset_id).name) == 64
    assert list(tmp_path.iterdir()) == [root]
