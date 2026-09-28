"""Structural cost bounds and recovery evidence, independent of machine speed."""

from collections import Counter
from pathlib import Path

import pytest

from asterstore import PublicationNotFoundError, StoreCorruptionError
from asterstore.storage import _files, file_lock
from asterstore.storage.registry import (
    collection_directory,
    managed_directory,
    object_record_path,
    retired_path,
)
from tests.test_inspection import publish, repository


def test_metadata_reads_each_record_once_and_does_not_cache_between_calls(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    oid = publish(repo, "p1", 0)
    publish(repo, "p2", 1)
    repo.governance.collect("gc")
    read = Path.read_bytes
    counts = Counter()

    def counted(path):
        counts[path] += 1
        return read(path)

    monkeypatch.setattr(Path, "read_bytes", counted)
    assert repo.governance.check().ok
    assert counts and set(counts.values()) == {1}
    object_record_path(tmp_path, oid).write_bytes(b"{")
    counts.clear()
    assert not repo.governance.check().ok
    assert counts[object_record_path(tmp_path, oid)] == 1


def test_collection_status_has_bounded_io_and_obeys_read_only_lock(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    for i in range(6):
        publish(repo, f"p{i}", i)
    repo.governance.collect("gc")
    expected = repo.governance.inspect().maintenance[0]
    read = Path.read_bytes
    observed = []
    allowed = {
        tmp_path / ".asterstore/format.json",
        collection_directory(tmp_path, "gc") / "plan.json",
        collection_directory(tmp_path, "gc") / "progress.json",
    }

    def counted(path):
        assert path in allowed
        observed.append(path)
        return read(path)

    def no_scan(path):
        raise AssertionError(f"task status scanned a directory: {path}")

    monkeypatch.setattr(Path, "read_bytes", counted)
    monkeypatch.setattr(Path, "iterdir", no_scan)
    assert repo.governance.collection_status("gc") == expected
    assert len(observed) == 3
    with file_lock(tmp_path / ".asterstore/gc.lock", exclusive=False):
        with pytest.raises(BlockingIOError):
            repo.governance.collection_status("gc")


def test_task_status_absence_and_cleanup_agree_with_full_snapshot(tmp_path):
    repo = repository(tmp_path)
    for method in (repo.governance.collection_status, repo.governance.cleanup_status):
        with pytest.raises(PublicationNotFoundError):
            method("missing")
    assert not (tmp_path / ".asterstore/gc.lock").exists()
    with repo.prepare("data", publication_id="p1", operation_id="pending", expected_generation=0):
        pass
    repo.governance.abandon("pending")
    repo.governance.cleanup("pending")
    assert repo.governance.cleanup_status("pending") == repo.governance.inspect().maintenance[0]
    path = managed_directory(tmp_path, "pending") / "cleanup-progress.json"
    path.write_bytes(b"{")
    with pytest.raises(StoreCorruptionError):
        repo.governance.cleanup_status("pending")


def test_completed_replay_only_reads_its_evidence_and_syncs_checkpoint(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    oid = publish(repo, "p1", 0)
    publish(repo, "p2", 1)
    outcome = repo.governance.collect("gc")
    # Later unrelated history must not expand this completed operation's work.
    for i in range(2, 8):
        publish(repo, f"p{i + 1}", i)
    directory = collection_directory(tmp_path, "gc")
    allowed = {
        tmp_path / ".asterstore/format.json",
        directory / "plan.json",
        directory / "progress.json",
        retired_path(tmp_path, "data", "p1"),
        object_record_path(tmp_path, oid),
    }
    read = Path.read_bytes
    synced = []
    sync = _files.sync_file

    def bounded(path):
        assert path in allowed
        return read(path)

    def observe(path):
        synced.append(path)
        return sync(path)

    monkeypatch.setattr(Path, "read_bytes", bounded)
    monkeypatch.setattr(_files, "sync_file", observe)
    assert repo.governance.resume_collection("gc") == outcome
    assert set(synced) == {
        tmp_path / ".asterstore/format.json",
        directory / "plan.json",
        directory / "progress.json",
    }


@pytest.mark.parametrize("evidence", ["retirement", "object"])
def test_completed_replay_still_rejects_broken_target_evidence(tmp_path, evidence):
    repo = repository(tmp_path)
    oid = publish(repo, "p1", 0)
    publish(repo, "p2", 1)
    repo.governance.collect("gc")
    path = (
        retired_path(tmp_path, "data", "p1")
        if evidence == "retirement"
        else object_record_path(tmp_path, oid)
    )
    path.write_bytes(b"{")
    with pytest.raises(StoreCorruptionError):
        repo.governance.resume_collection("gc")
