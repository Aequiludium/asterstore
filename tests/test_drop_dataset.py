"""Permanent dataset removal, recovery boundaries and unaffected neighbours."""

import json
import subprocess
import sys

import pytest

from asterstore import (
    PublicationConflictError,
    RecoveryRequiredError,
    Repository,
    StoreCorruptionError,
)
from asterstore import _storage as fs


@pytest.fixture
def repo(tmp_path):
    repository = Repository(tmp_path / "registry")
    repository.initialize()
    for name in ("one", "two"):
        dataset = repository.create_dataset(name, tmp_path / name)
        for value in (b"old", b"new"):
            with dataset.write() as batch:
                batch.stage("partition/data").write_bytes(value)
                batch.commit(metadata={"value": value.decode()}, quiescent=True)
    return repository


def registry(repo):
    return json.loads((repo.control / "registry.json").read_bytes())


def test_drop_removes_all_material_and_allows_explicit_recreation(repo):
    dataset = repo.dataset("one")
    other = repo.dataset("two")
    before = {p: p.read_bytes() for p in other.root.rglob("*") if p.is_file()}
    repo.drop_dataset("one", expected_revision=2, quiescent=True)
    assert not dataset.root.exists()
    assert repo.list_datasets() == ("two",)
    assert "dropping" not in registry(repo)
    with pytest.raises(KeyError):
        repo.dataset("one")
    repo.drop_dataset("one", expected_revision=2, quiescent=True)
    assert all(p.read_bytes() == value for p, value in before.items())
    assert repo.create_dataset("one", dataset.root).current().revision == 0


def test_preconditions_leave_dataset_unchanged(repo):
    before = registry(repo)
    with pytest.raises(ValueError, match="quiescent"):
        repo.drop_dataset("one", quiescent=False)
    with pytest.raises(PublicationConflictError):
        repo.drop_dataset("one", expected_revision=1, quiescent=True)
    dataset = repo.dataset("one")
    fs.write(dataset.control / "active.json", {})
    with pytest.raises(RecoveryRequiredError):
        repo.drop_dataset("one", quiescent=True)
    assert registry(repo) == before
    assert dataset.path("partition/data").read_bytes() == b"new"


@pytest.mark.parametrize(
    "failure", ["intent_sync", "partial_tree", "tree_sync", "final_write", "final_sync"]
)
def test_retry_after_each_deletion_boundary(repo, monkeypatch, failure):
    dataset = repo.dataset("one")
    original_write, original_remove, original_sync = fs.write, fs.remove_tree, fs.sync_directory
    final = False

    def write(path, value):
        nonlocal final
        if path == repo.control / "registry.json":
            final = "one" not in value["datasets"]
            if final and failure == "final_write":
                raise OSError("injected")
        original_write(path, value)

    def sync(path):
        if path == repo.control and (
            (failure == "intent_sync" and "dropping" in registry(repo))
            or (failure == "final_sync" and final)
        ):
            raise OSError("injected")
        if failure == "tree_sync" and path == dataset.root.parent and not dataset.root.exists():
            raise OSError("injected")
        original_sync(path)

    def remove(path):
        if failure == "partial_tree":
            fs.unlink(dataset.control / "head.json")
            raise OSError("injected")
        original_remove(path)

    with monkeypatch.context() as patch:
        patch.setattr(fs, "write", write)
        patch.setattr(fs, "sync_directory", sync)
        patch.setattr(fs, "remove_tree", remove)
        with pytest.raises(OSError, match="injected"):
            repo.drop_dataset("one", expected_revision=2, quiescent=True)
    reopened = Repository(repo.root)
    assert reopened.list_datasets() == ("two",)
    if "one" in registry(repo)["datasets"]:
        with pytest.raises(RecoveryRequiredError, match="deletion"):
            reopened.dataset("one")
        with pytest.raises(FileExistsError):
            reopened.create_dataset("one", dataset.root)
        with pytest.raises(ValueError, match="overlap"):
            reopened.create_dataset("overlap", dataset.root / "child")
        # Unrelated registration must retain the outstanding deletion intent.
        reopened.create_dataset("three", repo.root / "three")
        assert "one" in registry(repo)["dropping"]
    # Retry must not require head.json or recheck the old revision.
    reopened.drop_dataset("one", expected_revision=0, quiescent=True)
    assert not dataset.root.exists()
    assert "one" not in registry(repo)["datasets"]
    assert "dropping" not in registry(repo)
    assert reopened.dataset("two").current().revision == 2


def test_process_exit_after_intent_can_resume(repo):
    code = """
import os, sys
from asterstore import Repository, _storage as fs
fs.remove_tree = lambda path: os._exit(73)
Repository(sys.argv[1]).drop_dataset('one', quiescent=True)
"""
    result = subprocess.run([sys.executable, "-c", code, str(repo.root)], check=False)
    assert result.returncode == 73
    assert "one" in registry(repo)["dropping"]
    repo.drop_dataset("one", quiescent=True)
    assert repo.list_datasets() == ("two",)


def test_drop_waits_for_writer_then_checks_revision(repo):
    dataset = repo.dataset("one")
    code = """
import sys
from asterstore import Repository, PublicationConflictError
print('ready', flush=True)
try:
    Repository(sys.argv[1]).drop_dataset('one', expected_revision=2, quiescent=True)
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
    assert dataset.current().revision == 3
    assert "dropping" not in registry(repo)


def test_drop_rejects_redirect_and_overlapping_registry(repo, tmp_path):
    dataset = repo.dataset("one")
    moved = tmp_path / "moved"
    dataset.root.rename(moved)
    dataset.root.symlink_to(moved, target_is_directory=True)
    with pytest.raises(StoreCorruptionError):
        repo.drop_dataset("one", quiescent=True)
    dataset.root.unlink()
    moved.rename(dataset.root)
    document = registry(repo)
    document["datasets"]["one"] = str(repo.root)
    fs.write(repo.control / "registry.json", document)
    with pytest.raises(StoreCorruptionError, match="boundary"):
        repo.drop_dataset("one", quiescent=True)
    assert repo.root.exists() and dataset.root.exists()


def test_drop_does_not_follow_internal_symlink(repo, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    keep = outside / "keep"
    keep.write_bytes(b"untouched")
    link = repo.dataset("one").root / "link"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(StoreCorruptionError):
        repo.drop_dataset("one", quiescent=True)
    assert keep.read_bytes() == b"untouched"
    link.unlink()
    repo.drop_dataset("one", quiescent=True)
    assert keep.read_bytes() == b"untouched"


def test_normal_operations_do_not_read_registry_or_take_registry_lock(repo, monkeypatch):
    dataset = repo.dataset("one")
    original_read, original_lock = fs.read, fs.lock

    def read(path):
        assert not path.is_relative_to(repo.control)
        return original_read(path)

    def lock(path):
        assert path == dataset.control / "writer.lock"
        return original_lock(path)

    monkeypatch.setattr(fs, "read", read)
    monkeypatch.setattr(fs, "lock", lock)
    assert dataset.current().revision == 2
    assert dataset.path("partition/data").read_bytes() == b"new"
    with dataset.write() as batch:
        batch.stage("partition/data").write_bytes(b"third")
        batch.commit(metadata={}, quiescent=True)
    dataset.rollback(quiescent=True)
    dataset.recover(quiescent=True)
    dataset.clean()
