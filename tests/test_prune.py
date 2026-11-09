"""Irreversible path maintenance, undo disposal and forward recovery."""

import os
import shutil
import stat
import subprocess
import sys

import pytest

from asterstore import (
    NoRollbackError,
    PruneBatch,
    PublicationConflictError,
    RecoveryRequiredError,
    Repository,
    StoreCorruptionError,
)
from asterstore import _storage as fs


def publish(dataset, changes):
    with dataset.write() as batch:
        for path, value in changes.items():
            batch.stage(path).write_bytes(value)
        return batch.commit(
            metadata={
                "publication_id": batch.id,
                "parent_publication_id": batch.before.publication,
            },
            quiescent=True,
        )


@pytest.fixture
def sample(tmp_path):
    repo = Repository(tmp_path / "registry")
    repo.initialize()
    dataset = repo.create_dataset("owner", tmp_path / "owner")
    other = repo.create_dataset("other", tmp_path / "other")
    publish(other, {"keep": b"other dataset"})
    publish(dataset, {"old/a": b"first", "retired/deep/a": b"old", "keep/a": b"current"})
    publish(dataset, {"old/a": b"second", "retired/deep/b": b"old"})
    publish(dataset, {"keep/a": b"latest"})
    return repo, dataset, other


def commit_prune(dataset):
    with dataset.prune(expected_revision=3, quiescent=True) as batch:
        assert isinstance(batch, PruneBatch)
        batch.delete("old/a")
        batch.delete("retired")
        return batch.commit(
            metadata={
                "publication_id": batch.id,
                "parent_publication_id": batch.before.publication,
            },
        )


def assert_pruned(dataset, before):
    state = dataset.current()
    assert state.revision == before.revision + 1
    assert state.publication == state.operation == state.metadata["publication_id"]
    assert state.metadata["parent_publication_id"] == before.publication
    assert state.undo is None
    assert not (dataset.root / "old/a").exists()
    assert not (dataset.root / "retired").exists()
    assert dataset.path("keep/a").read_bytes() == b"latest"
    assert list((dataset.control / "work").iterdir()) == []
    assert not (dataset.control / "active.json").exists()
    with pytest.raises(NoRollbackError):
        dataset.rollback(quiescent=True)
    return state


def test_permanent_deletion_clears_all_backups_and_preserves_neighbours(sample):
    repo, dataset, other = sample
    before = dataset.current()
    neighbour = {p: p.read_bytes() for p in other.root.rglob("*") if p.is_file()}
    assert len(list((dataset.control / "work").iterdir())) == 3
    state = commit_prune(dataset)
    assert_pruned(dataset, before)
    assert state == dataset.current()
    assert repo.list_datasets() == ("other", "owner")
    assert all(path.read_bytes() == value for path, value in neighbour.items())
    # A subsequent ordinary publish/rollback is still supported, but cannot undo GC.
    publish(dataset, {"keep/a": b"next"})
    restored = dataset.rollback(quiescent=True)
    assert restored.metadata == state.metadata
    assert dataset.path("keep/a").read_bytes() == b"latest"
    assert not (dataset.root / "retired").exists()


def test_empty_abandoned_and_missing_targets(sample):
    _, dataset, _ = sample
    before = dataset.current()
    work = sorted((dataset.control / "work").iterdir())
    with dataset.prune(quiescent=True) as batch:
        assert batch.commit(metadata=before.metadata) == before
        with pytest.raises(RuntimeError, match="closed"):
            batch.delete("old")
    with dataset.prune(quiescent=True) as batch:
        with pytest.raises(ValueError, match="metadata changes"):
            batch.commit(metadata={"different": True})
        batch.delete("old")  # Exiting without commit has no effect.
    with pytest.raises(RuntimeError), dataset.prune(quiescent=True) as batch:
        batch.delete("old")
        raise RuntimeError("caller validation failed")
    assert dataset.current() == before
    assert sorted((dataset.control / "work").iterdir()) == work
    with dataset.prune(quiescent=True) as batch:
        batch.delete("already/missing/file")
        after = batch.commit(metadata=before.metadata)
    assert after.revision == before.revision + 1 and after.undo is None
    assert list((dataset.control / "work").iterdir()) == []
    assert dataset.path("old/a").read_bytes() == b"second"


@pytest.mark.parametrize("paths", [("old", "old"), ("old", "old/a"), ("old/a", "old")])
def test_duplicate_and_overlapping_targets_rejected(sample, paths):
    _, dataset, _ = sample
    before = dataset.current()
    with dataset.prune(quiescent=True) as batch:
        batch.delete(paths[0])
        with pytest.raises(ValueError, match="overlapping"):
            batch.delete(paths[1])
    assert dataset.current() == before


@pytest.mark.parametrize(
    "path",
    ["", ".", "/tmp", "../outside", "old/../keep", ".asterstore", "old/.asterstore", "old//a"],
)
def test_unsafe_target_names_rejected(sample, path):
    _, dataset, _ = sample
    with dataset.prune(quiescent=True) as batch, pytest.raises(ValueError):
        batch.delete(path)


@pytest.mark.parametrize("kind", ["symlink", "fifo", "hardlink", "control"])
def test_preflight_rejects_unsafe_subtrees_before_intent(sample, tmp_path, kind):
    _, dataset, _ = sample
    before = dataset.current()
    outside = tmp_path / "outside"
    outside.write_bytes(b"preserved")
    target = dataset.root / "retired/unsafe"
    if kind == "symlink":
        target.symlink_to(outside)
    elif kind == "fifo":
        os.mkfifo(target)
    elif kind == "hardlink":
        os.link(outside, target)
    else:
        (dataset.root / "retired/.asterstore").mkdir()
    with dataset.prune(quiescent=True) as batch:
        batch.delete("old/a")
        batch.delete("retired")
        with pytest.raises((StoreCorruptionError, ValueError)):
            batch.commit(metadata=before.metadata)
    assert dataset.current() == before
    assert dataset.path("old/a").read_bytes() == b"second"
    assert outside.read_bytes() == b"preserved"
    assert not (dataset.control / "active.json").exists()


def test_maintenance_preconditions(sample):
    _, dataset, _ = sample
    before = dataset.current()
    with pytest.raises(ValueError), dataset.prune(quiescent=False):
        pass
    with (
        pytest.raises(PublicationConflictError),
        dataset.prune(expected_revision=2, quiescent=True),
    ):
        pass
    assert dataset.current() == before


@pytest.mark.parametrize(
    "failure",
    [
        "intent_before",
        "intent_file_sync",
        "intent_sync",
        "file_after",
        "target_sync",
        "tree_partial",
        "work_partial",
        "work_sync",
        "head_before",
        "head_file_sync",
        "head_after",
        "head_sync",
        "active_before",
        "active_after",
        "active_sync",
    ],
)
def test_forward_recovery_at_durable_boundaries(sample, monkeypatch, failure):
    repo, dataset, _ = sample
    before = dataset.current()
    original_write, original_unlink = fs.write, fs.unlink
    original_sync, original_sync_file = fs.sync_directory, fs.sync_file
    original_rmtree = shutil.rmtree
    original_fsync = os.fsync

    def write(path, value):
        if failure == "intent_before" and path.name == "active.json":
            raise OSError("injected")
        if failure == "head_before" and path == dataset.control / "head.json":
            raise OSError("injected")
        original_write(path, value)
        if failure == "head_after" and path == dataset.control / "head.json":
            raise OSError("injected")

    def unlink(path):
        if failure == "active_before" and path == dataset.control / "active.json":
            raise OSError("injected")
        original_unlink(path)
        if (failure == "file_after" and path == dataset.root / "old/a") or (
            failure == "active_after" and path == dataset.control / "active.json"
        ):
            raise OSError("injected")

    def rmtree(path, *args, **kwargs):
        if failure == "tree_partial" and path == dataset.root / "retired":
            original_unlink(path / "deep/a")
            raise OSError("injected")
        if failure == "work_partial" and path.parent == dataset.control / "work":
            # Recovery must not need to decode the old transaction record.
            original_unlink(path / "record.json")
            raise OSError("injected")
        return original_rmtree(path, *args, **kwargs)

    def sync(path):
        if (
            failure == "target_sync"
            and path == dataset.root / "old"
            and not (dataset.root / "old/a").exists()
        ):
            raise OSError("injected")
        if (
            failure == "work_sync"
            and path == dataset.control / "work"
            and len(list(path.iterdir())) < 3
        ):
            raise OSError("injected")
        if path == dataset.control:
            active = (dataset.control / "active.json").exists()
            if failure == "intent_sync" and active:
                raise OSError("injected")
            if failure == "active_sync" and not active and dataset._head().revision == 4:
                raise OSError("injected")
        return original_sync(path)

    def sync_file(path):
        if failure == "head_sync" and path == dataset.control / "head.json":
            raise OSError("injected")
        return original_sync_file(path)

    def fsync(fd):
        if stat.S_ISREG(os.fstat(fd).st_mode):
            if failure == "intent_file_sync":
                raise OSError("injected")
            if failure == "head_file_sync" and (dataset.control / "active.json").exists():
                raise OSError("injected")
        return original_fsync(fd)

    with monkeypatch.context() as patch:
        patch.setattr(fs, "write", write)
        patch.setattr(fs, "unlink", unlink)
        patch.setattr(fs, "sync_directory", sync)
        patch.setattr(fs, "sync_file", sync_file)
        patch.setattr(shutil, "rmtree", rmtree)
        patch.setattr(os, "fsync", fsync)
        with pytest.raises(OSError, match="injected"):
            commit_prune(dataset)
    if failure in {"intent_before", "intent_file_sync"}:
        assert dataset.current() == before
        assert dataset.path("old/a").read_bytes() == b"second"
        assert len(list((dataset.control / "work").iterdir())) == 3
        return
    if (dataset.control / "active.json").exists():
        with pytest.raises(RecoveryRequiredError):
            dataset.current()
        with pytest.raises(RecoveryRequiredError), dataset.write():
            pass
        with pytest.raises(RecoveryRequiredError), dataset.prune(quiescent=True):
            pass
        with pytest.raises(RecoveryRequiredError):
            dataset.rollback(quiescent=True)
        with pytest.raises(RecoveryRequiredError):
            dataset.clean()
        with pytest.raises(RecoveryRequiredError):
            repo.drop_dataset("owner", quiescent=True)
    reopened = repo.dataset("owner")
    recovered = reopened.recover(quiescent=True)
    assert recovered == assert_pruned(reopened, before)
    assert reopened.recover(quiescent=True) == recovered


def test_process_exit_after_partial_deletion(sample):
    repo, dataset, _ = sample
    before = dataset.current()
    code = """
import os, sys
from asterstore import Repository, _storage as fs
dataset = Repository(sys.argv[1]).dataset('owner')
original = fs.unlink
def stop(path):
    original(path)
    if path == dataset.root / 'old/a':
        os._exit(73)
fs.unlink = stop
with dataset.prune(expected_revision=3, quiescent=True) as batch:
    batch.delete('old/a')
    batch.delete('retired')
    batch.commit(metadata={
        'publication_id': batch.id, 'parent_publication_id': batch.before.publication,
    })
"""
    result = subprocess.run([sys.executable, "-c", code, str(repo.root)], check=False)
    assert result.returncode == 73
    assert not (dataset.root / "old/a").exists()
    with pytest.raises(RecoveryRequiredError):
        dataset.current()
    repo.dataset("owner").recover(quiescent=True)
    assert_pruned(dataset, before)


def test_corrupt_intent_and_state_mismatch_fail_closed(sample, monkeypatch):
    _, dataset, _ = sample
    with monkeypatch.context() as patch:
        patch.setattr(fs, "unlink", lambda path: (_ for _ in ()).throw(OSError("stop")))
        with pytest.raises(OSError):
            commit_prune(dataset)
    path = dataset.control / "active.json"
    original = fs.read(path)
    for invalid in ("../outside", ".asterstore/head.json"):
        fs.write(path, {**original, "paths": [invalid]})
        with pytest.raises(StoreCorruptionError):
            dataset.recover(quiescent=True)
        assert dataset.path("old/a").exists()
    fs.write(path, original)
    head_path = dataset.control / "head.json"
    head = fs.read(head_path)
    fs.write(head_path, {**head, "revision": head["revision"] + 10})
    with pytest.raises(StoreCorruptionError, match="state mismatch"):
        dataset.recover(quiescent=True)
    fs.write(head_path, head)
    dataset.recover(quiescent=True)


def test_prune_waits_for_writer_then_checks_revision(sample):
    repo, dataset, _ = sample
    code = """
import sys
from asterstore import Repository, PublicationConflictError
print('ready', flush=True)
try:
    with Repository(sys.argv[1]).dataset('owner').prune(
        expected_revision=3, quiescent=True,
    ):
        raise AssertionError('stale revision accepted')
except PublicationConflictError:
    sys.exit(19)
"""
    with dataset.write() as batch:
        process = subprocess.Popen(
            [sys.executable, "-c", code, str(repo.root)], stdout=subprocess.PIPE, text=True
        )
        try:
            assert process.stdout.readline().strip() == "ready"
            batch.commit(metadata={"updated": True}, quiescent=True)
        except BaseException:
            process.kill()
            process.wait()
            raise
    try:
        assert process.wait(timeout=10) == 19
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdout.close()
    assert dataset.current().revision == 4
    assert dataset.path("old/a").read_bytes() == b"second"


def test_ordinary_reads_and_publishing_do_not_scan_or_use_new_locks(sample, monkeypatch):
    _, dataset, _ = sample
    original_lock = fs.lock

    def lock(path):
        assert path == dataset.control / "writer.lock"
        return original_lock(path)

    def scan(*args, **kwargs):
        raise AssertionError("ordinary data access performed a directory scan")

    monkeypatch.setattr(fs, "lock", lock)
    monkeypatch.setattr(os, "walk", scan)
    assert dataset.current().revision == 3
    assert dataset.path("keep/a").read_bytes() == b"latest"
    publish(dataset, {"keep/a": b"ordinary update"})
    dataset.rollback(quiescent=True)
    dataset.recover(quiescent=True)
    assert dataset.path("keep/a").read_bytes() == b"latest"
