"""Regressions from the independent recovery and construction audit."""

import json
from pathlib import Path

import pytest

from asterstore import FileSet, HistoryAccess, Repository, StoreCorruptionError
from asterstore.governance import _service
from asterstore.publishing import _candidate
from asterstore.publishing.transactions import _commit
from asterstore.storage import _files
from asterstore.storage.registry import (
    collection_directory,
    head_path,
    managed_directory,
    object_record_path,
    operation_path,
)


def create(root):
    repo = Repository(root)
    repo.initialize(
        store_id="store", resource_ids=["owned"], managed_resource_id="owned", lifecycle=True
    )
    return repo


def prepare(repo, pid, gen=0, *, dataset="data", durable=False):
    return repo.prepare(
        dataset,
        publication_id=pid,
        operation_id=pid,
        expected_generation=gen,
        history=HistoryAccess.VERSIONED,
        durable=durable,
    )


def publish(repo, pid, gen=0, *, dataset="data"):
    with prepare(repo, pid, gen, dataset=dataset) as writer:
        writer.write_bytes("k", pid.encode(), relative_path="part.bin")
        return writer.commit()


@pytest.mark.parametrize("point", ["temporary", "replace"])
def test_first_current_failure_can_abandon_clean_and_govern_other_dataset(
    tmp_path, monkeypatch, point
):
    repo = create(tmp_path)
    publish(repo, "old", dataset="other")
    old = repo.open("other").files()[0]
    publish(repo, "new", 1, dataset="other")
    name = "NamedTemporaryFile" if point == "temporary" else "replace"
    module = _files.tempfile if point == "temporary" else _files.os
    original = getattr(module, name)

    def fail(*args, **kwargs):
        matches = (
            kwargs.get("prefix") == ".current.json."
            if point == "temporary"
            else (Path(args[1]).name == "current.json")
        )
        if matches:
            raise OSError("inside first current write")
        return original(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(module, name, fail)
        with pytest.raises(OSError):
            publish(repo, "failed")
    assert head_path(tmp_path, "data").parent.is_dir()
    assert not head_path(tmp_path, "data").exists()
    repo.governance.abandon("failed")
    assert repo.governance.cleanup("failed").deleted_files == ("part.bin",)
    assert repo.governance.collect("gc").complete and not old.exists()
    assert repo.open("other").files()[0].read_bytes() == b"new"


def test_missing_current_with_real_history_still_blocks_deletion(tmp_path):
    repo = create(tmp_path)
    publish(repo, "p1")
    publish(repo, "p2", 1)
    current = repo.open("data").files()[0]
    head_path(tmp_path, "data").unlink()
    with pytest.raises(StoreCorruptionError):
        repo.governance.collect("gc")
    assert current.read_bytes() == b"p2"


def test_completed_gc_retry_resyncs_after_final_directory_fsync_failure(tmp_path, monkeypatch):
    repo = create(tmp_path)
    publish(repo, "p1")
    publish(repo, "p2", 1)
    directory = collection_directory(tmp_path, "gc")
    progress = directory / "progress.json"
    original = _files.sync_directory

    def fail(path):
        if (
            path == directory
            and progress.exists()
            and json.loads(progress.read_bytes())["complete"]
        ):
            raise OSError("final directory fsync failed")
        return original(path)

    with monkeypatch.context() as patch:
        patch.setattr(_files, "sync_directory", fail)
        with pytest.raises(OSError):
            repo.governance.collect("gc")
    synced = []

    def observe(path):
        synced.append(path)
        return original(path)

    monkeypatch.setattr(_files, "sync_directory", observe)
    result = repo.governance.resume_collection("gc")
    assert result.complete and directory in synced
    assert repo.governance.collect("gc") == result


@pytest.mark.parametrize("state", ["writing", "sealed", "installed"])
def test_abandon_syncs_existing_weak_evidence_before_publishing_tombstone(
    tmp_path, monkeypatch, state
):
    repo = create(tmp_path)
    with prepare(repo, "failed") as writer:
        writer.write_bytes("k", b"partial", relative_path="part.bin")
        seal = writer.seal() if state != "writing" else None
        if state == "installed":

            def fail(*args, **kwargs):
                raise OSError("before current")

            with monkeypatch.context() as patch:
                patch.setattr(_commit, "atomic_write", fail)
                with pytest.raises(OSError):
                    writer.commit()
    directory = managed_directory(tmp_path, "failed")
    required = {
        tmp_path / ".asterstore/format.json",
        directory / "request.json",
        directory / "protection.json",
    }
    if seal is not None:
        required.add(directory / "sealed.json")
    if state == "installed":
        required.add(operation_path(tmp_path, "failed"))
        required.update(
            object_record_path(tmp_path, obj.object_id) for obj in seal.declaration.files.objects
        )
    synced = set()
    original_sync = _files.sync_file
    original_write = _service.immutable_write

    def sync(path):
        synced.add(path)
        return original_sync(path)

    def tombstone(path, *args, **kwargs):
        assert required <= synced
        return original_write(path, *args, **kwargs)

    monkeypatch.setattr(_files, "sync_file", sync)
    monkeypatch.setattr(_service, "immutable_write", tombstone)
    repo.governance.abandon("failed")
    assert repo.governance.cleanup("failed").complete


@pytest.mark.parametrize("entry", ["future-retentions", "future.json"])
def test_unknown_top_level_control_prevents_collection(tmp_path, entry):
    repo = create(tmp_path)
    publish(repo, "p1")
    old = repo.open("data").files()[0]
    publish(repo, "p2", 1)
    unknown = tmp_path / ".asterstore" / entry
    if entry.endswith(".json"):
        unknown.write_text("{}")
    else:
        unknown.mkdir()
    with pytest.raises(StoreCorruptionError):
        repo.governance.preview()
    with pytest.raises(StoreCorruptionError):
        repo.governance.collect("gc")
    assert old.exists()


def test_path_and_alias_build_incrementally_then_validate_once_at_seal(tmp_path, monkeypatch):
    repo = create(tmp_path)
    validations = []
    original = FileSet.__post_init__

    def inspect(value):
        validations.append(len(value.objects))
        return original(value)

    with prepare(repo, "many") as writer:

        def no_marker(*args):
            raise AssertionError("per-member marker I/O")

        with monkeypatch.context() as patch:
            patch.setattr(_candidate, "read_store", no_marker)
            patch.setattr(FileSet, "__post_init__", inspect)
            for i in range(128):
                writer.write_bytes(f"k{i}", b"x", relative_path=f"parts/{i}.bin")
                writer.alias(f"alias{i}", member=f"k{i}")
            assert validations == []
            seal = writer.seal()
            assert validations == [128]
        assert len(seal.declaration.files.members) == 256
        writer.commit()
    assert len(repo.open("data").files()) == 128


@pytest.mark.parametrize("new_path", ["part.bin", "part.bin/child", "nested"])
def test_incremental_path_conflicts_reject_before_io_and_keep_member_available(tmp_path, new_path):
    from asterstore import InvalidDeclarationError

    repo = create(tmp_path)
    with prepare(repo, "p1") as writer:
        writer.write_bytes("first", b"first", relative_path="part.bin")
        writer.write_bytes("nested", b"nested", relative_path="nested/leaf.bin")
        with pytest.raises(InvalidDeclarationError):
            writer.path("retry", relative_path=new_path)
        writer.write_bytes("retry", b"retry", relative_path="valid.bin")
        writer.alias("alias", member="retry")
        record = writer.commit()
    assert len(record.declaration.files.objects) == 3
    assert len(record.declaration.files.members) == 4


def test_incremental_member_failure_does_not_reserve_paths_or_keys(tmp_path, monkeypatch):
    from asterstore import InvalidDeclarationError, UnknownMemberError

    repo = create(tmp_path)
    with prepare(repo, "p1") as writer:
        writer.write_bytes("first", b"first", relative_path="part.bin")
        with pytest.raises(InvalidDeclarationError):
            writer.path("first", relative_path="uncreated/duplicate.bin")
        assert not (managed_directory(tmp_path, "p1") / "files/uncreated").exists()
        with pytest.raises(UnknownMemberError):
            writer.alias("free", member="absent")
        with pytest.raises(InvalidDeclarationError):
            writer.alias("first", member="first")

        def fail(*args, **kwargs):
            raise OSError("directory creation failed")

        with monkeypatch.context() as patch:
            patch.setattr(_candidate, "ensure_directory", fail)
            with pytest.raises(OSError):
                writer.path("free", relative_path="available.bin")
        writer.write_bytes("free", b"ok", relative_path="available.bin")
        record = writer.commit()
    assert [m.key for m in record.declaration.files.members] == ["first", "free"]


@pytest.mark.parametrize("after", [False, True])
def test_reuse_protection_failure_leaves_incremental_membership_unchanged(
    tmp_path, monkeypatch, after
):
    from asterstore import UnknownMemberError

    repo = create(tmp_path)
    publish(repo, "p1")
    original = _candidate.atomic_write
    with prepare(repo, "p2", 1) as writer:
        writer.write_bytes("new", b"new", relative_path="new.bin")

        def fail(*args, **kwargs):
            if after:
                original(*args, **kwargs)
            raise OSError("protection persistence failed")

        with monkeypatch.context() as patch:
            patch.setattr(_candidate, "atomic_write", fail)
            with pytest.raises(OSError):
                writer.reuse("p1")
        with pytest.raises(UnknownMemberError):
            writer.alias("alias", member="k")
        writer.write_bytes("k", b"own", relative_path="own.bin")
        record = writer.commit()
    assert [m.key for m in record.declaration.files.members] == ["new", "k"]
    assert len(repo.governance.collect("gc").deleted_objects) == 1
    assert [p.read_bytes() for p in repo.open("data").files()] == [b"new", b"own"]


def test_reusing_distinct_aliases_preserves_one_object_and_member_order(tmp_path):
    repo = create(tmp_path)
    with prepare(repo, "p1") as writer:
        writer.write_bytes("one", b"one", relative_path="part.bin")
        writer.alias("two", member="one")
        first = writer.commit()
    with prepare(repo, "p2", 1) as writer:
        writer.reuse("p1", keys=["two"])
        writer.alias("three", member="two")
        writer.reuse("p1", keys=["one"])
        second = writer.commit()
    assert second.declaration.files.objects == first.declaration.files.objects
    assert [m.key for m in second.declaration.files.members] == ["two", "three", "one"]
    assert len(repo.open("data").files()) == 1


def test_abandon_sync_failure_does_not_publish_tombstone_or_release_reuse(tmp_path, monkeypatch):
    repo = create(tmp_path)
    publish(repo, "p1")
    old = repo.open("data").files()[0]
    with prepare(repo, "pending", 1) as writer:
        writer.reuse("p1")
    publish(repo, "p2", 1)
    original = _files.sync_file

    def fail(path):
        if path == managed_directory(tmp_path, "pending") / "request.json":
            raise OSError("cannot persist request")
        return original(path)

    with monkeypatch.context() as patch:
        patch.setattr(_files, "sync_file", fail)
        with pytest.raises(OSError):
            repo.governance.abandon("pending")
    assert not (managed_directory(tmp_path, "pending") / "abandoned.json").exists()
    assert repo.governance.collect("still-protected").deleted_objects == ()
    assert old.exists()
    repo.governance.abandon("pending")
    assert repo.governance.collect("released").deleted_objects


def test_failed_initial_protection_does_not_prevent_abandonment(tmp_path, monkeypatch):
    repo = create(tmp_path)

    def fail(*args, **kwargs):
        raise OSError("protection write failed")

    with monkeypatch.context() as patch:
        patch.setattr(_candidate, "atomic_write", fail)
        with pytest.raises(OSError), prepare(repo, "partial"):
            pass
    repo.governance.abandon("partial")
    assert repo.governance.cleanup("partial").complete


def test_completed_gc_retry_preserves_protected_outcome_after_reference_release(
    tmp_path, monkeypatch
):
    from asterstore import RetentionScope
    from asterstore.governance import _collection

    repo = create(tmp_path)
    publish(repo, "p1")
    old = repo.open("data").files()[0]
    publish(repo, "p2", 1)
    original_plan = _collection.immutable_write

    def fail_plan(*args, **kwargs):
        original_plan(*args, **kwargs)
        raise OSError("plan persisted")

    with monkeypatch.context() as patch:
        patch.setattr(_collection, "immutable_write", fail_plan)
        with pytest.raises(OSError):
            repo.governance.collect("gc")
    held = repo.governance.retain("task", "data", "p1", scope=RetentionScope.OBJECTS)
    directory = collection_directory(tmp_path, "gc")
    original_sync = _files.sync_directory

    def fail_sync(path):
        if path == directory:
            progress = directory / "progress.json"
            if progress.exists() and json.loads(progress.read_bytes())["complete"]:
                raise OSError("completion directory sync failed")
        return original_sync(path)

    with monkeypatch.context() as patch:
        patch.setattr(_files, "sync_directory", fail_sync)
        with pytest.raises(OSError):
            repo.governance.resume_collection("gc")
    outcome = repo.governance.resume_collection("gc")
    assert outcome.protected_objects
    repo.governance.release("task", expected_revision=held.revision)
    assert repo.governance.resume_collection("gc") == outcome
    assert old.exists()
    assert repo.governance.collect("new-gc").deleted_objects


@pytest.mark.parametrize("damage", ["unexplained-empty", "history", "unknown"])
def test_missing_current_requires_explained_empty_namespace(tmp_path, damage):
    repo = create(tmp_path)
    directory = head_path(tmp_path, "unpublished").parent
    directory.mkdir(parents=True)
    if damage == "history":
        (directory / "history").mkdir()
        (directory / "history/evidence.json").write_text("{}")
    elif damage == "unknown":
        (directory / "unknown.json").write_text("{}")
    with pytest.raises(StoreCorruptionError):
        repo.governance.collect("gc")


def test_expected_atomic_temporaries_are_ignored_but_not_removed(tmp_path):
    repo = create(tmp_path)
    publish(repo, "p1")
    publish(repo, "p2", 1)
    temporary = tmp_path / ".asterstore/.format.json.abcdefgh"
    temporary.write_bytes(b"interrupted")
    assert repo.governance.collect("gc").deleted_objects
    assert temporary.read_bytes() == b"interrupted"


def test_completed_gc_does_not_bypass_unknown_control_guard(tmp_path):
    repo = create(tmp_path)
    repo.governance.collect("gc")
    (tmp_path / ".asterstore/unknown.json").write_bytes(b"{}")
    with pytest.raises(StoreCorruptionError):
        repo.governance.resume_collection("gc")
