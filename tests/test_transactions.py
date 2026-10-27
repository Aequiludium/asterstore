"""Directory publication semantics and observable recovery guarantees."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from asterstore import (
    Dataset,
    NoRollbackError,
    PublicationConflictError,
    RecoveryRequiredError,
    Repository,
    StoreCorruptionError,
    UnsupportedFormatError,
)
from asterstore import _storage as fs


@pytest.fixture
def dataset(tmp_path: Path) -> Dataset:
    repo = Repository(tmp_path / "registry")
    repo.initialize()
    return repo.create_dataset("prices", tmp_path / "prices")


def publish(ds: Dataset, values: dict[str, bytes], meta: int = 1, deletes: tuple[str, ...] = ()):
    with ds.write() as batch:
        for path, value in values.items():
            batch.stage(path).write_bytes(value)
        for path in deletes:
            batch.delete(path)
        return batch.commit(metadata={"batch": meta}, quiescent=True)


def files(ds: Dataset) -> dict[str, bytes]:
    return {
        p.relative_to(ds.root).as_posix(): p.read_bytes()
        for p in ds.root.rglob("*")
        if p.is_file() and ".asterstore" not in p.relative_to(ds.root).parts
    }


def test_five_days_and_previous_undo(dataset: Dataset) -> None:
    initial = {f"date=10-{day}/data": b"old" for day in range(8, 11)}
    first = publish(dataset, initial)
    untouched = dataset.path("date=10-8/data").stat().st_ino
    with dataset.write() as batch:
        assert dataset.current() == first  # preparation does not block reads
        batch.stage("discarded").write_bytes(b"no commit")
    assert not dataset.path("discarded").exists()
    assert dataset.path("date=10-8/data").stat().st_ino == untouched
    second = publish(dataset, {f"date=10-{day}/data": b"new" for day in range(8, 13)}, 2)
    dataset.clean()
    assert len(list((dataset.control / "work").iterdir())) == 1
    restored = dataset.rollback(quiescent=True, expected_revision=second.revision)
    assert files(dataset) == initial
    assert restored.publication == first.publication
    assert restored.metadata == first.metadata
    assert restored.revision == 3 and restored.undo is None
    dataset.clean()
    assert files(dataset) == initial
    with pytest.raises(NoRollbackError):
        dataset.rollback(quiescent=True)


def test_delete_metadata_noop_first_rollback(dataset: Dataset) -> None:
    first = publish(dataset, {"a": b"A", "b": b"B"})
    with dataset.write() as batch:
        assert batch.commit(metadata=first.metadata, quiescent=True) == first
    dataset.rollback(quiescent=True)
    assert files(dataset) == {} and dataset.current().publication is None
    publish(dataset, {"a": b"A", "b": b"B"})
    publish(dataset, {"c": b"C"}, 2, deletes=("a", "b"))
    assert files(dataset) == {"c": b"C"}
    dataset.rollback(quiescent=True)
    assert files(dataset) == {"a": b"A", "b": b"B"}
    before = dataset.current()
    with dataset.write() as batch:
        batch.commit(metadata={"only": "metadata"}, quiescent=True)
    dataset.rollback(quiescent=True)
    assert dataset.current().metadata == before.metadata


def test_factor_append_recompute_and_definition(dataset: Dataset) -> None:
    publish(dataset, {"version=v1/date=1/data": b"1"})
    publish(dataset, {"version=v1/date=2/data": b"2"}, 2)
    publish(dataset, {"version=v1/date=1/data": b"R1", "version=v1/date=2/data": b"R2"}, 3)
    dataset.rollback(quiescent=True)
    assert files(dataset) == {"version=v1/date=1/data": b"1", "version=v1/date=2/data": b"2"}
    with dataset.write() as batch:
        batch.stage("version=v2/date=1/data").write_bytes(b"v2")
        batch.commit(metadata={"definition": "v2"}, quiescent=True)
    dataset.clean()
    assert dataset.path("version=v1/date=1/data").read_bytes() == b"1"
    dataset.rollback(quiescent=True)
    assert not dataset.path("version=v2/date=1/data").exists()


def test_conflict_window_and_closed_batch(dataset: Dataset) -> None:
    publish(dataset, {"a": b"A"})
    with pytest.raises(PublicationConflictError), dataset.write(expected_revision=0):
        pass
    with dataset.write() as batch:
        batch.stage("b").write_bytes(b"B")
        with pytest.raises(ValueError):
            batch.commit(metadata={}, quiescent=False)
    with pytest.raises(RuntimeError):
        batch.stage("c")
    with pytest.raises(ValueError):
        dataset.rollback(quiescent=False)
    with pytest.raises(ValueError):
        dataset.recover(quiescent=False)
    assert files(dataset) == {"a": b"A"}


@pytest.mark.parametrize(
    "path", ["../x", "/tmp/x", "a/../b", ".asterstore/head.json", "x/.asterstore/y", "a//b", "a\\b"]
)
def test_path_rejection(dataset: Dataset, path: str) -> None:
    with dataset.write() as batch, pytest.raises(ValueError):
        batch.stage(path)


def test_duplicates_aliases_and_nonfiles(dataset: Dataset, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside")
    (dataset.root / "link").symlink_to(outside)
    with dataset.write() as batch, pytest.raises(StoreCorruptionError):
        batch.stage("link")
    (dataset.root / "link").unlink()
    os.link(outside, dataset.root / "hard")
    with dataset.write() as batch:
        batch.stage("hard").write_bytes(b"new")
        with pytest.raises(StoreCorruptionError):
            batch.commit(metadata={}, quiescent=True)
    assert outside.read_bytes() == b"outside"
    with dataset.write() as batch:
        batch.stage("a/b")
        with pytest.raises(ValueError):
            batch.stage("a")
        with pytest.raises(ValueError):
            batch.stage("a/b")
        with pytest.raises(FileNotFoundError):
            batch.delete("missing")


def test_list_datasets(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "registry")
    repo.initialize()
    assert repo.list_datasets() == ()
    for index, name in enumerate(
        ("其他数据集", "factor:barra/size/Size", "factor:basic/capital/Capital")
    ):
        repo.create_dataset(name, tmp_path / f"data-{index}")
    (tmp_path / "unregistered").mkdir()
    assert Repository(repo.root).list_datasets() == (
        "factor:barra/size/Size",
        "factor:basic/capital/Capital",
        "其他数据集",
    )


def test_registry_rejects_overlap_nonempty_and_old_format(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "repo")
    repo.initialize()
    ds = repo.create_dataset("first", tmp_path / "data")
    assert repo.dataset("first").root == ds.root
    for target in (ds.root, ds.root / "nested", tmp_path, repo.control / "x"):
        with pytest.raises(ValueError):
            repo.create_dataset("other", target)
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "data").write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        repo.create_dataset("nonempty", existing)
    (repo.control / "registry.json").write_text('{"format":3,"kind":"registry"}')
    with pytest.raises(UnsupportedFormatError):
        repo.dataset("first")
    old = tmp_path / "old"
    (old / ".asterstore").mkdir(parents=True)
    (old / ".asterstore/format.json").write_text('{"format":3}')
    with pytest.raises(FileExistsError):
        Repository(old).initialize()


class Crash(BaseException):
    pass


# Interrupt each durable step, on both sides of the actual operation.
STEPS = ("write", "copy", "install", "unlink", "sync_directory", "sync_file")


def inject(monkeypatch, counter: list[int], at: int | None, after: bool):
    for name in STEPS:
        original = getattr(fs, name)

        def call(*args, _original=original, **kwargs):
            counter[0] += 1
            fail = counter[0] == at
            if fail and not after:
                raise Crash()
            result = _original(*args, **kwargs)
            if fail and after:
                raise Crash()
            return result

        monkeypatch.setattr(fs, name, call)


def baseline(root: Path) -> Dataset:
    repo = Repository(root / "registry")
    repo.initialize()
    ds = repo.create_dataset("test", root / "data")
    publish(ds, {"a": b"original", "b": b"original", "d": b"delete"})
    publish(ds, {"a": b"old", "b": b"old"}, 2)
    return ds


def update(ds: Dataset):
    return publish(ds, {"a": b"new", "b": b"new", "c": b"added"}, 3, deletes=("d",))


@pytest.mark.parametrize("after", [False, True])
def test_every_publish_failure_boundary(tmp_path: Path, monkeypatch, after: bool) -> None:
    sample = baseline(tmp_path / "sample")
    count = [0]
    with monkeypatch.context() as patch:
        inject(patch, count, None, after)
        update(sample)
    for index in range(1, count[0] + 1):
        ds = baseline(tmp_path / str(index))
        before = ds.current()
        old = files(ds)
        with monkeypatch.context() as patch:
            inject(patch, [0], index, after)
            with pytest.raises(Crash):
                update(ds)
        if (ds.control / "active.json").exists():
            with pytest.raises(RecoveryRequiredError):
                ds.current()
            with pytest.raises(RecoveryRequiredError):
                ds.clean()
        state = ds.recover(quiescent=True)
        assert ds.recover(quiescent=True) == state
        if state.publication == before.publication:
            assert state == before and files(ds) == old, index
            ds.clean()
            ds.rollback(quiescent=True)
            assert files(ds) == {"a": b"original", "b": b"original", "d": b"delete"}
        else:
            assert state.metadata == {"batch": 3}
            assert files(ds) == {"a": b"new", "b": b"new", "c": b"added"}, index
            ds.clean()
            ds.rollback(quiescent=True)
            assert files(ds) == old


@pytest.mark.parametrize("after", [False, True])
def test_every_rollback_failure_boundary(tmp_path: Path, monkeypatch, after: bool) -> None:
    sample = baseline(tmp_path / "sample")
    update(sample)
    count = [0]
    with monkeypatch.context() as patch:
        inject(patch, count, None, after)
        sample.rollback(quiescent=True)
    for index in range(1, count[0] + 1):
        ds = baseline(tmp_path / str(index))
        old = files(ds)
        update(ds)
        with monkeypatch.context() as patch:
            inject(patch, [0], index, after)
            with pytest.raises(Crash):
                ds.rollback(quiescent=True)
        ds.recover(quiescent=True)
        if ds.current().undo is not None:  # Intent was not yet persisted.
            ds.rollback(quiescent=True)
        assert files(ds) == old, index
        assert ds.current().metadata == {"batch": 2}
        assert ds.current().undo is None
        ds.recover(quiescent=True)
        ds.clean()
        assert files(ds) == old


def test_actual_process_exit_mid_install(dataset: Dataset) -> None:
    publish(dataset, {"a": b"old", "b": b"old"})
    script = """
import os, sys
from pathlib import Path
from asterstore import Dataset
from asterstore import _storage as fs
ds = Dataset(Path(sys.argv[1]), 'prices')
original = fs.install
def interrupted(source, target):
    original(source, target)
    os._exit(73)
fs.install = interrupted
with ds.write() as batch:
    batch.stage('a').write_bytes(b'new')
    batch.stage('b').write_bytes(b'new')
    batch.commit(metadata={'batch': 2}, quiescent=True)
"""
    result = subprocess.run([sys.executable, "-c", script, str(dataset.root)], check=False)
    assert result.returncode == 73
    assert dataset.path("a").read_bytes() == b"new"
    with pytest.raises(RecoveryRequiredError):
        dataset.current()
    dataset.recover(quiescent=True)
    assert files(dataset) == {"a": b"old", "b": b"old"}


def test_missing_backup_stops_recovery(dataset: Dataset, monkeypatch) -> None:
    publish(dataset, {"a": b"old", "b": b"old"})
    with monkeypatch.context() as patch:
        patch.setattr(fs, "install", lambda *args: (_ for _ in ()).throw(Crash()))
        with pytest.raises(Crash):
            publish(dataset, {"a": b"new", "b": b"new"}, 2)
    active = json.loads((dataset.control / "active.json").read_text())
    (dataset.control / "work" / active["source"] / "backup" / "b").unlink()
    with pytest.raises(FileNotFoundError):
        dataset.recover(quiescent=True)
    assert (dataset.control / "active.json").exists()
    assert files(dataset) == {"a": b"old", "b": b"old"}


def test_cleanup_interruption_never_touches_data_or_live_undo(
    dataset: Dataset, monkeypatch
) -> None:
    publish(dataset, {"a": b"1"})
    publish(dataset, {"a": b"2"}, 2)
    publish(dataset, {"a": b"3"}, 3)
    before = files(dataset)
    candidates = dataset.clean(dry_run=True)
    assert len(candidates) == 2
    original = fs.remove_tree
    with monkeypatch.context() as patch:

        def interrupted(path):
            original(path)
            raise Crash()

        patch.setattr(fs, "remove_tree", interrupted)
        with pytest.raises(Crash):
            dataset.clean()
    dataset.clean()
    assert files(dataset) == before
    assert len(list((dataset.control / "work").iterdir())) == 1
    dataset.rollback(quiescent=True)
    assert files(dataset) == {"a": b"2"}


def test_writer_lock_is_dataset_local(dataset: Dataset, tmp_path: Path) -> None:
    repo = Repository(tmp_path / "registry")
    other = repo.create_dataset("other", tmp_path / "other")
    probe = """
import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_CREAT | os.O_RDWR, 0o600)
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    sys.exit(23)
"""
    with dataset.write():
        held = subprocess.run(
            [sys.executable, "-c", probe, str(dataset.control / "writer.lock")], check=False
        )
        free = subprocess.run(
            [sys.executable, "-c", probe, str(other.control / "writer.lock")], check=False
        )
        assert held.returncode == 23 and free.returncode == 0
        publish(other, {"independent": b"OK"})
    released = subprocess.run(
        [sys.executable, "-c", probe, str(dataset.control / "writer.lock")], check=False
    )
    assert released.returncode == 0


def test_backup_space_failure_preserves_old_undo(dataset: Dataset, monkeypatch) -> None:
    import errno

    publish(dataset, {"a": b"first"})
    before = publish(dataset, {"a": b"second"}, 2)
    with monkeypatch.context() as patch:

        def no_space(*args):
            raise OSError(errno.ENOSPC, "test backup space exhaustion")

        patch.setattr(fs, "copy", no_space)
        with pytest.raises(OSError, match="space exhaustion"):
            publish(dataset, {"a": b"third"}, 3)
    assert dataset.current() == before
    assert dataset.path("a").read_bytes() == b"second"
    dataset.clean()
    dataset.rollback(quiescent=True)
    assert dataset.path("a").read_bytes() == b"first"


@pytest.mark.parametrize("mode", ["after_head", "rollback", "staging"])
def test_process_exit_committed_rollback_and_staging(dataset: Dataset, mode: str) -> None:
    first = publish(dataset, {"a": b"old", "b": b"old"})
    script = """
import os, sys
from pathlib import Path
from asterstore import Dataset
from asterstore import _storage as fs
ds = Dataset(Path(sys.argv[1]), 'prices')
mode = sys.argv[2]
if mode == 'after_head':
    original = fs.write
    def interrupted(path, value):
        original(path, value)
        if path.name == 'head.json':
            os._exit(73)
    fs.write = interrupted
elif mode == 'rollback':
    original = fs.install
    def interrupted(source, target):
        original(source, target)
        os._exit(73)
    fs.install = interrupted
    ds.rollback(quiescent=True)
with ds.write() as batch:
    batch.stage('a').write_bytes(b'new')
    batch.stage('b').write_bytes(b'new')
    if mode == 'staging':
        os._exit(73)
    batch.commit(metadata={'batch': 2}, quiescent=True)
"""
    if mode == "rollback":
        publish(dataset, {"a": b"new", "b": b"new"}, 2)
    result = subprocess.run([sys.executable, "-c", script, str(dataset.root), mode], check=False)
    assert result.returncode == 73
    state = dataset.recover(quiescent=True)
    assert dataset.recover(quiescent=True) == state
    dataset.clean()
    if mode == "after_head":
        assert state.metadata == {"batch": 2}
        assert files(dataset) == {"a": b"new", "b": b"new"}
        dataset.rollback(quiescent=True)
        assert files(dataset) == {"a": b"old", "b": b"old"}
    else:
        assert files(dataset) == {"a": b"old", "b": b"old"}
        assert state.publication == first.publication
        assert (state.undo is None) == (mode == "rollback")


def test_invalid_staging_and_metadata_do_not_modify_current(dataset: Dataset) -> None:
    before = publish(dataset, {"a": b"old"})
    with dataset.write() as batch:
        source = batch.stage("a")
        source.unlink()
        source.mkdir()
        with pytest.raises(StoreCorruptionError):
            batch.commit(metadata={}, quiescent=True)
    assert dataset.current() == before and dataset.path("a").read_bytes() == b"old"
    with dataset.write() as batch:
        batch.stage("a").write_bytes(b"new")
        with pytest.raises(ValueError):
            batch.commit(metadata={"bad": float("nan")}, quiescent=True)
    assert dataset.current() == before


def test_control_corruption_and_cleanup_symlink_fail_closed(
    dataset: Dataset, tmp_path: Path
) -> None:
    publish(dataset, {"a": b"old"})
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_bytes(b"keep")
    work = dataset.control / "work" / ("a" * 32)
    work.mkdir()
    (work / "alias").symlink_to(outside, target_is_directory=True)
    with pytest.raises(StoreCorruptionError):
        dataset.clean()
    assert (outside / "keep").read_bytes() == b"keep"
    (work / "alias").unlink()
    (dataset.control / "active.json").write_text('{"kind":"unknown"}')
    with pytest.raises(StoreCorruptionError):
        dataset.recover(quiescent=True)
    with pytest.raises(RecoveryRequiredError):
        dataset.clean()
    assert dataset.path("a").read_bytes() == b"old"


def test_update_only_touches_changed_files_and_no_global_registry(
    dataset: Dataset, monkeypatch
) -> None:
    publish(dataset, {f"date={day}/data": b"old" for day in range(100)})
    original = fs.checked
    allowed = {dataset.root / f"date={day}/data" for day in range(5)}

    def checked(path, *, missing=False):
        if (
            path.is_relative_to(dataset.root)
            and ".asterstore" not in path.relative_to(dataset.root).parts
        ):
            assert (
                path == dataset.root
                or path in allowed
                or any(p.is_relative_to(path) for p in allowed)
            )
        assert "registry.json" != path.name
        return original(path, missing=missing)

    with monkeypatch.context() as patch:
        patch.setattr(fs, "checked", checked)
        publish(dataset, {f"date={day}/data": b"new" for day in range(5)}, 2)
        dataset.rollback(quiescent=True)
    assert files(dataset) == {f"date={day}/data": b"old" for day in range(100)}


@pytest.mark.parametrize("after", [False, True])
def test_recovery_itself_can_be_interrupted(tmp_path: Path, monkeypatch, after: bool) -> None:
    def pending(root: Path) -> Dataset:
        ds = baseline(root)
        original = fs.install
        with monkeypatch.context() as patch:

            def interrupted(source, target):
                original(source, target)
                raise Crash()

            patch.setattr(fs, "install", interrupted)
            with pytest.raises(Crash):
                update(ds)
        return ds

    sample = pending(tmp_path / "sample")
    count = [0]
    with monkeypatch.context() as patch:
        inject(patch, count, None, after)
        sample.recover(quiescent=True)
    for index in range(1, count[0] + 1):
        ds = pending(tmp_path / str(index))
        with monkeypatch.context() as patch:
            inject(patch, [0], index, after)
            with pytest.raises(Crash):
                ds.recover(quiescent=True)
        ds.recover(quiescent=True)
        assert files(ds) == {"a": b"old", "b": b"old", "d": b"delete"}
        assert ds.current().metadata == {"batch": 2}
        ds.clean()
        ds.rollback(quiescent=True)
        assert files(ds) == {"a": b"original", "b": b"original", "d": b"delete"}


@pytest.mark.parametrize("origin", ["rollback", "aborted_publish"])
@pytest.mark.parametrize("settle", ["recover", "clean"])
def test_active_unlink_must_be_durable_before_work_is_removed(
    dataset: Dataset, monkeypatch, origin: str, settle: str
) -> None:
    """Model the directory's durable image, not just current-process path visibility."""
    publish(dataset, {"a": b"first"})
    publish(dataset, {"a": b"second"}, 2)
    if origin == "aborted_publish":
        with monkeypatch.context() as patch:

            def failed_install(*args):
                raise OSError("interrupted installation")

            patch.setattr(fs, "install", failed_install)
            with pytest.raises(OSError, match="interrupted installation"):
                publish(dataset, {"a": b"third"}, 3)
    active = dataset.control / "active.json"
    durable_active = [active.read_bytes() if active.exists() else None]
    fail_absent_sync = [True]
    original_sync = fs.sync_directory

    def sync_directory(path):
        if path == dataset.control:
            if not active.exists() and fail_absent_sync[0]:
                raise OSError("active deletion was not synced")
            original_sync(path)
            durable_active[0] = active.read_bytes() if active.exists() else None
        else:
            original_sync(path)

    with monkeypatch.context() as patch:
        patch.setattr(fs, "sync_directory", sync_directory)
        with pytest.raises(OSError, match="active deletion was not synced"):
            if origin == "rollback":
                dataset.rollback(quiescent=True)
            else:
                dataset.recover(quiescent=True)
        assert not active.exists() and durable_active[0] is not None
        source = json.loads(durable_active[0])["source"]
        source_directory = dataset.control / "work" / source
        assert source_directory.exists()
        expected = dataset.current()
        expected_files = files(dataset)
        # Continuing to report sync failure must not report successful recovery or delete work.
        with pytest.raises(OSError, match="active deletion was not synced"):
            if settle == "recover":
                dataset.recover(quiescent=True)
            else:
                dataset.clean()
        assert source_directory.exists()
        fail_absent_sync[0] = False
        if settle == "recover":
            assert dataset.recover(quiescent=True) == expected
        else:
            dataset.clean()
        assert durable_active[0] is None
        dataset.clean()
        assert not source_directory.exists()
    # Simulated crash: only a durable active entry could reappear here.
    if durable_active[0] is not None:
        active.write_bytes(durable_active[0])
    assert dataset.recover(quiescent=True) == expected
    assert files(dataset) == expected_files


def test_racing_initializer_cannot_erase_registered_datasets(tmp_path: Path, monkeypatch) -> None:
    repo = Repository(tmp_path / "registry")
    original = Path.iterdir
    interleaved = [False]

    def list_then_initialize(path):
        entries = list(original(path))
        if path == repo.root and not interleaved[0]:
            interleaved[0] = True
            assert entries == []
            # Another initializer finishes after this caller observed an empty root.
            other = Repository(repo.root)
            other.initialize()
            other.create_dataset("retained", tmp_path / "data")
        return iter(entries)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "iterdir", list_then_initialize)
        with pytest.raises(FileExistsError):
            repo.initialize()
    assert repo.dataset("retained").root == tmp_path / "data"
