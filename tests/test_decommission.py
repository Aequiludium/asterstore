"""Offline visibility, independent retention, fixed batches and interruption recovery."""

from pathlib import Path

import pytest

from asterstore import (
    DatasetDecommissionedError,
    PublicationConflictError,
    PublicationNotFoundError,
    Repository,
    RetentionScope,
    StoreCorruptionError,
)
from asterstore.governance import _decommission
from asterstore.storage.registry import head_path, marker_path, publication_path


def create(root):
    repo = Repository(root)
    repo.initialize(
        store_id="store", resource_ids=["owned"], managed_resource_id="owned", lifecycle=True
    )
    return repo


def publish(repo, dataset, generation=0):
    pid = f"{dataset}-{generation}"
    with repo.prepare(
        dataset, publication_id=pid, operation_id=pid, expected_generation=generation
    ) as writer:
        writer.write_bytes("part", b"payload", relative_path="part.bin")
        return writer.commit()


def test_batch_offline_then_explicit_gc(tmp_path):
    repo = create(tmp_path)
    a = publish(repo, "a")
    publish(repo, "b")
    binding = repo.open("a")
    paths = binding.files()
    result = repo.governance.decommission("expire", datasets={"b": 1, "a": 1})
    assert result.complete and result.decommissioned == ("a", "b")
    assert not result.conflicts
    assert repo.list_datasets() == ()
    assert repo.list_datasets(include_decommissioned=True) == ("a", "b")
    assert repo.governance.dataset_status("a").operation_id == "expire"
    assert repo.governance.decommission_status("expire") == result
    assert repo.governance.resume_decommission("expire") == result
    assert repo.describe("a", publication_id="a-0") == a
    assert binding.files() == paths and paths[0].exists()
    for kwargs in ({}, {"publication_id": "a-0"}):
        with pytest.raises(DatasetDecommissionedError):
            repo.open("a", **kwargs)
    with pytest.raises(DatasetDecommissionedError):
        repo.describe("a")
    with (
        pytest.raises(DatasetDecommissionedError),
        repo.prepare("a", publication_id="new", operation_id="new", expected_generation=1),
    ):
        pass
    with repo.resume("a-0") as writer:
        assert writer.commit() == a
    assert len(repo.governance.collect("gc").deleted_objects) == 2
    assert not paths[0].exists()
    assert repo.describe("a", publication_id="a-0") == a
    assert repo.governance.resume_decommission("expire") == result
    repo.governance.check()


def test_retention_survives_and_new_object_retention_is_blocked(tmp_path):
    repo = create(tmp_path)
    publish(repo, "a")
    ref = repo.governance.retain("reader", "a", "a-0", scope=RetentionScope.OBJECTS)
    repo.governance.decommission("expire", datasets={"a": 1})
    assert repo.governance.retain("reader", "a", "a-0", scope=RetentionScope.OBJECTS) == ref
    with pytest.raises(DatasetDecommissionedError):
        repo.governance.retain("new", "a", "a-0", scope=RetentionScope.OBJECTS)
    assert not repo.governance.collect("gc1").deleted_objects
    assert repo.governance.open("reader", expected_revision=1).files()[0].exists()
    repo.governance.retain("audit", "a", "a-0", scope=RetentionScope.METADATA)
    repo.governance.release("reader", expected_revision=1)
    assert len(repo.governance.collect("gc2").deleted_objects) == 1


@pytest.mark.parametrize(
    "targets,error",
    [
        ({"a": 2}, PublicationConflictError),
        ({"a": 1, "missing": 1}, PublicationNotFoundError),
        ({}, ValueError),
        ({"a": True}, ValueError),
    ],
)
def test_invalid_batch_has_no_visibility_or_feature_effect(tmp_path, targets, error):
    repo = create(tmp_path)
    publish(repo, "a")
    marker = marker_path(tmp_path).read_bytes()
    with pytest.raises(error):
        repo.governance.decommission("expire", datasets=targets)
    assert marker_path(tmp_path).read_bytes() == marker
    assert repo.list_datasets() == ("a",)


def test_fixed_batch_resume_skips_new_generation(tmp_path, monkeypatch):
    repo = create(tmp_path)
    publish(repo, "a")
    publish(repo, "b")
    original = _decommission.atomic_write

    def interrupted(path, data, **kwargs):
        if path == head_path(tmp_path, "b"):
            raise OSError("simulated interruption")
        return original(path, data, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(_decommission, "atomic_write", interrupted)
        with pytest.raises(OSError):
            repo.governance.decommission("expire", datasets={"a": 1, "b": 1})
    status = repo.governance.decommission_status("expire")
    assert status.decommissioned == ("a",) and status.pending == ("b",)
    publish(repo, "b", 1)
    result = repo.governance.resume_decommission("expire")
    assert result.complete and result.conflicts == ("b",)
    assert repo.list_datasets() == ("b",)
    with pytest.raises(PublicationConflictError):
        repo.governance.decommission("expire", datasets={"b": 2})


def test_pending_writer_cannot_publish_after_offline(tmp_path):
    repo = create(tmp_path)
    publish(repo, "a")
    with repo.prepare(
        "a", publication_id="pending", operation_id="pending", expected_generation=1
    ) as writer:
        writer.write_bytes("part", b"new", relative_path="part.bin")
        repo.governance.decommission("expire", datasets={"a": 1})
        with pytest.raises(DatasetDecommissionedError):
            writer.commit()
    assert repo.candidate_status("pending").state == "decommissioned"
    assert (
        next(c for c in repo.governance.inspect().candidates if c.operation_id == "pending").state
        == "decommissioned"
    )
    repo.governance.inspect_candidate("pending")
    repo.governance.abandon("pending")
    repo.governance.cleanup("pending")


def test_missing_original_history_prevents_collection(tmp_path):
    repo = create(tmp_path)
    publish(repo, "a")
    data = repo.open("a").files()[0]
    repo.governance.decommission("expire", datasets={"a": 1})
    publication_path(tmp_path, "a", "a-0").unlink()
    with pytest.raises(StoreCorruptionError):
        repo.governance.collect("gc")
    assert data.exists()


def test_offline_open_has_only_two_control_reads(tmp_path, monkeypatch):
    repo = create(tmp_path)
    publish(repo, "a")
    repo.governance.decommission("expire", datasets={"a": 1})
    read = Path.read_bytes
    paths = []

    def counted(path):
        paths.append(path)
        return read(path)

    monkeypatch.setattr(Path, "read_bytes", counted)
    with pytest.raises(DatasetDecommissionedError):
        repo.open("a")
    assert paths == [marker_path(tmp_path), head_path(tmp_path, "a")]


@pytest.mark.parametrize("boundary", ["marker", "plan", "history", "tombstone"])
def test_process_exit_at_durable_boundaries(tmp_path, boundary):
    import subprocess
    import sys
    import textwrap

    repo = create(tmp_path)
    publish(repo, "a")
    script = textwrap.dedent("""
        import os
        import sys
        from asterstore import Repository
        from asterstore.governance import _decommission

        repo = Repository(sys.argv[1])
        boundary = sys.argv[2]
        atomic, immutable = _decommission.atomic_write, _decommission.immutable_write
        def write(original, path, data, **kwargs):
            original(path, data, **kwargs)
            matches = {
                "marker": path.name == "format.json",
                "plan": path.parent.name == "decommissions",
                "history": path.parent.name == "history",
                "tombstone": path.name == "current.json",
            }
            if matches[boundary]:
                os._exit(73)
        _decommission.atomic_write = lambda p, d, **kw: write(atomic, p, d, **kw)
        _decommission.immutable_write = lambda p, d, **kw: write(immutable, p, d, **kw)
        repo.governance.decommission("expire", datasets={"a": 1})
    """)
    child = subprocess.run([sys.executable, "-c", script, str(tmp_path), boundary], timeout=20)
    assert child.returncode == 73
    assert repo.governance.check().ok
    result = repo.governance.decommission("expire", datasets={"a": 1})
    assert result.decommissioned == ("a",) and not result.conflicts
    assert repo.governance.check().ok


def test_registered_offline_never_deletes_external_bytes(tmp_path):
    from asterstore import Capabilities, Declaration, FileSet, Locator, Member, Object

    external = tmp_path / "external.bin"
    external.write_bytes(b"external")
    repo = Repository(tmp_path / "repo")
    repo.initialize(store_id="store", resource_ids=["external"], lifecycle=True)
    declaration = Declaration(
        "data",
        "p1",
        FileSet([Object("o1", Locator("external", "external.bin"))], [Member("part", "o1")]),
        Capabilities.registered(),
    )
    original = repo.register(
        declaration, operation_id="publish", expected_generation=0, durable=False
    )
    repo.governance.decommission("expire", datasets={"data": 1})
    assert repo.resume_registration("publish", durable=True) == original
    assert not repo.governance.collect("gc").deleted_objects
    assert external.read_bytes() == b"external"
    with pytest.raises(DatasetDecommissionedError):
        repo.register(
            Declaration("data", "p2", declaration.files, declaration.capabilities),
            operation_id="new",
            expected_generation=1,
        )


def test_archive_before_and_after_offline(tmp_path):
    from tests.test_compaction import create as create_archive

    repo = create_archive(tmp_path)
    repo.governance.compact("before", quiescent=True)
    original = repo.describe("data")
    repo.governance.decommission("expire", datasets={"data": 3})
    assert repo.governance.inspect().decommissioned[0].publication == original
    with repo.resume("op2", durable=True) as writer:
        assert writer.commit() == original
    assert len(repo.governance.collect("gc").deleted_objects) == 24
    repo.governance.compact("after", quiescent=True)
    assert repo.describe("data", publication_id="p2") == original
    assert repo.governance.resume_decommission("expire").decommissioned == ("data",)
    assert b"dataset_tombstone" in head_path(tmp_path, "data").read_bytes()
    assert repo.governance.inspect().decommissioned
    assert repo.governance.check().ok


def test_pending_decommission_blocks_compaction(tmp_path, monkeypatch):
    from asterstore import CandidateStateError

    repo = create(tmp_path)
    publish(repo, "a")
    original = _decommission.atomic_write

    def stop(path, data, **kwargs):
        if path.name == "current.json":
            raise OSError("stop")
        return original(path, data, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(_decommission, "atomic_write", stop)
        with pytest.raises(OSError):
            repo.governance.decommission("expire", datasets={"a": 1})
    with pytest.raises(CandidateStateError):
        repo.governance.compact("archive", quiescent=True)


def test_pending_reuse_still_protects_offline_objects(tmp_path):
    repo = create(tmp_path)
    publish(repo, "a")
    path = repo.open("a").files()[0]
    with repo.prepare(
        "a", publication_id="pending", operation_id="pending", expected_generation=1
    ) as writer:
        writer.reuse("a-0")
        writer.seal()
    repo.governance.decommission("expire", datasets={"a": 1})
    assert not repo.governance.collect("gc1").deleted_objects
    assert path.exists()
    repo.governance.abandon("pending")
    repo.governance.cleanup("pending")
    assert len(repo.governance.collect("gc2").deleted_objects) == 1


def test_other_batch_conflict_and_empty_dataset(tmp_path, monkeypatch):
    repo = create(tmp_path)
    with repo.prepare(
        "a", publication_id="empty", operation_id="empty", expected_generation=0
    ) as writer:
        writer.commit()
    original = _decommission.atomic_write

    def stop(path, data, **kwargs):
        if path.name == "current.json":
            raise OSError("stop")
        return original(path, data, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(_decommission, "atomic_write", stop)
        with pytest.raises(OSError):
            repo.governance.decommission("first", datasets={"a": 1})
    repo.governance.decommission("second", datasets={"a": 1})
    result = repo.governance.resume_decommission("first")
    assert result.complete and result.conflicts == ("a",)
    assert not repo.governance.collect("gc").deleted_objects
    assert repo.governance.check().ok


@pytest.mark.parametrize(
    "damage", ["missing_plan", "wrong_operation", "feature", "plan_generation"]
)
def test_corrupt_offline_authority_fails_before_deletion(tmp_path, damage):
    import json

    from asterstore.storage.registry import token

    repo = create(tmp_path)
    publish(repo, "a")
    data = repo.open("a").files()[0]
    repo.governance.decommission("expire", datasets={"a": 1})
    plan = tmp_path / ".asterstore/decommissions" / (token("expire") + ".json")
    if damage == "missing_plan":
        plan.unlink()
    else:
        path = {
            "wrong_operation": head_path(tmp_path, "a"),
            "feature": marker_path(tmp_path),
            "plan_generation": plan,
        }[damage]
        payload = json.loads(path.read_bytes())
        if damage == "wrong_operation":
            payload["operation_id"] = "different"
        elif damage == "feature":
            payload["required_features"].remove("dataset_decommission")
        else:
            payload["targets"][0]["generation"] = 2
        path.write_text(json.dumps(payload))
    with pytest.raises(StoreCorruptionError):
        repo.governance.collect("gc")
    assert data.exists()


def test_status_is_read_only_and_busy_is_explicit(tmp_path):
    from asterstore import RepositoryBusyError
    from asterstore.storage import file_lock

    repo = create(tmp_path)
    publish(repo, "a")
    repo.governance.decommission("expire", datasets={"a": 1})
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    repo.governance.dataset_status("a")
    repo.governance.decommission_status("expire")
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with file_lock(tmp_path / ".asterstore/gc.lock", exclusive=True):
        with pytest.raises(RepositoryBusyError):
            repo.governance.decommission_status("expire")


@pytest.mark.parametrize("query", ["open", "describe", "status"])
def test_first_decommission_between_marker_and_head_reads(tmp_path, monkeypatch, query):
    repo = create(tmp_path)
    original = publish(repo, "a")
    read = Path.read_bytes
    fired = False

    def interleave(path):
        nonlocal fired
        data = read(path)
        if path == marker_path(tmp_path) and not fired:
            fired = True
            repo.governance.decommission("expire", datasets={"a": 1})
        return data

    monkeypatch.setattr(Path, "read_bytes", interleave)
    if query == "open":
        with pytest.raises(DatasetDecommissionedError):
            repo.open("a")
    elif query == "describe":
        assert repo.describe("a", publication_id="a-0") == original
    else:
        assert repo.governance.dataset_status("a").state == "decommissioned"
    assert fired
