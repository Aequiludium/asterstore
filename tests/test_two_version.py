"""Behavioral and failure regressions for the replacement format-2 protocol."""

import json
import multiprocessing
import os
from pathlib import Path
from uuid import uuid4

import pytest

from asterstore import (
    CandidateStateError,
    Manifest,
    Member,
    PublicationConflictError,
    PublicationNotFoundError,
    Repository,
    StoreCorruptionError,
    UnsupportedFormatError,
)
from asterstore import _storage as fs
from asterstore._models import decode_head, decode_manifest


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    value = Repository(tmp_path / "store")
    value.initialize()
    return value


def publish(repo, value, dataset="daily", *, durable=False):
    with repo.prepare(dataset, durable=durable) as draft:
        draft.allocate("data", relative_path="date=2026-10-14/data.bin").write_bytes(value)
        binding = draft.seal()
        assert binding.revision is None
        result = draft.commit()
        assert draft.commit() == result
        return result


def test_construction_and_old_format_rejection(tmp_path):
    root = tmp_path / "missing"
    Repository(root)
    assert not root.exists()
    (root / ".asterstore").mkdir(parents=True)
    marker = root / ".asterstore/format.json"
    old = b'{"format_version":1,"store_id":"old"}'
    marker.write_bytes(old)
    for call in [
        lambda: Repository(root).initialize(),
        lambda: Repository(root).open("a"),
        lambda: Repository(root).prepare("a").__enter__(),
    ]:
        with pytest.raises(UnsupportedFormatError):
            call()
    assert marker.read_bytes() == old
    assert list((root / ".asterstore").iterdir()) == [marker]


def test_nonempty_initialization_rejected(tmp_path):
    (tmp_path / "data").write_text("do not adopt")
    with pytest.raises(StoreCorruptionError):
        Repository(tmp_path).initialize()
    assert not (tmp_path / ".asterstore").exists()


def test_publish_read_rollback(repo):
    first = publish(repo, b"one")
    pinned = repo.open("daily")
    second = publish(repo, b"two", durable=True)
    assert second.rollback == first.current
    assert repo.open("daily", version="rollback").files()[0].read_bytes() == b"one"
    assert pinned.files()[0].read_bytes() == b"one"
    with pytest.raises(PublicationConflictError):
        repo.rollback("daily", expected_revision=first.revision)
    restored = repo.rollback("daily", expected_revision=second.revision)
    assert restored.current == first.current and restored.rollback is None
    assert restored.revision == 3
    with pytest.raises(PublicationNotFoundError):
        repo.rollback("daily", expected_revision=3)
    third = publish(repo, b"three")
    assert third.rollback == first.current
    assert repo.list_datasets() == ("daily",)


def test_reuse_alias_order_replacement_and_remove(repo):
    publish(repo, b"one")
    old_file = repo.open("daily").files()[0]
    with repo.prepare("daily", expected_revision=1) as draft:
        draft.reuse_current()
        draft.alias("alias", "data")
        draft.allocate("data").write_bytes(b"two")
        draft.allocate("discard").write_bytes(b"garbage")
        draft.remove("discard")
        binding = draft.seal()
        assert [m.key for m in binding.manifest.members] == ["data", "alias"]
        assert binding.files(keys=["alias"]) == (old_file,)
        with pytest.raises(CandidateStateError):
            draft.allocate("late")
        draft.commit()
    repo.clean("daily", quiescent=True)
    assert old_file.exists()
    assert repo.open("daily").files(keys=["data"])[0].read_bytes() == b"two"


def test_failed_business_check_never_publishes(repo):
    first = publish(repo, b"one")
    with pytest.raises(RuntimeError), repo.prepare("daily") as draft:
        draft.allocate("data").write_bytes(b"bad")
        draft.seal()
        raise RuntimeError("business check failed")
    assert repo.describe("daily") == first
    report = repo.clean("daily", quiescent=True)
    assert report.deleted_files == 2
    assert repo.open("daily").files()[0].read_bytes() == b"one"


def test_revision_conflict_does_not_install(repo):
    publish(repo, b"one")
    directory = repo._directory("daily")
    before = set(directory.rglob("*"))
    with pytest.raises(PublicationConflictError), repo.prepare("daily", expected_revision=0):
        pass
    assert set(directory.rglob("*")) == before


def test_draft_state_and_missing_file(repo):
    with repo.prepare("daily") as draft:
        draft.allocate("unwritten")
        with pytest.raises(CandidateStateError):
            draft.commit()
        with pytest.raises(FileNotFoundError):
            draft.seal()
        with pytest.raises(CandidateStateError):
            draft.allocate("later")
    with pytest.raises(CandidateStateError):
        draft.__enter__()
    assert repo.list_datasets() == ()
    assert repo.clean("daily", quiescent=True).skipped.startswith("no head")


@pytest.mark.parametrize("stage", ["before_replace", "after_replace"])
def test_uncertain_commit(repo, monkeypatch, stage):
    first = publish(repo, b"one")
    original = fs.os.replace

    def fail(source, destination):
        if Path(destination).name == "head.json":
            if stage == "after_replace":
                original(source, destination)
            raise OSError("injected head failure")
        return original(source, destination)

    with repo.prepare("daily", durable=False) as draft:
        draft.allocate("data").write_bytes(b"two")
        draft.seal()
        with monkeypatch.context() as patch:
            patch.setattr(fs.os, "replace", fail)
            with pytest.raises(OSError):
                draft.commit()
        with pytest.raises(CandidateStateError):
            draft.commit()
        current = repo.describe("daily")
        assert current.current == (draft.version_id if stage == "after_replace" else first.current)
    assert repo.open("daily").files()[0].read_bytes() == (
        b"two" if stage == "after_replace" else b"one"
    )


def test_cleanup_only_two_versions_and_dataset_scope(repo):
    other = publish(repo, b"other", "other")
    versions = [publish(repo, str(i).encode()) for i in range(8)]
    with pytest.raises(ValueError):
        repo.clean("daily", quiescent=False)
    preview = repo.clean("daily", quiescent=True, dry_run=True)
    assert preview.candidate_files == 12 and preview.deleted_files == 0
    result = repo.clean("daily", quiescent=True)
    assert result.deleted_files == preview.candidate_files
    assert repo.describe("other") == other
    assert repo.open("other").files()[0].read_bytes() == b"other"
    directory = repo._directory("daily")
    assert len(list((directory / "versions").glob("*.json"))) == 2
    assert {p.stem for p in (directory / "versions").glob("*.json")} == {
        versions[-1].current,
        versions[-2].current,
    }
    assert repo.clean("daily", quiescent=True).candidate_files == 0


def test_growth_bounded_after_each_cleanup(repo):
    sizes = []
    for _ in range(30):
        publish(repo, b"x")
        repo.clean("daily", quiescent=True)
        directory = repo._directory("daily")
        sizes.append(sum(p.stat().st_size for p in directory.rglob("*.json")))
        assert len(list((directory / "versions").glob("*.json"))) <= 2
    assert max(sizes[10:]) == min(sizes[10:])


def test_cleanup_preflights_corruption_before_deletion(repo):
    publish(repo, b"old")
    publish(repo, b"previous")
    current = publish(repo, b"current")
    directory = repo._directory("daily")
    all_files = {p for p in directory.rglob("*") if p.is_file()}
    (directory / "versions" / f"{current.current}.json").write_text("{}")
    with pytest.raises(StoreCorruptionError):
        repo.clean("daily", quiescent=True)
    assert all(p.exists() for p in all_files)


def test_missing_rollback_blocks_cleanup(repo):
    publish(repo, b"old")
    publish(repo, b"new")
    path = repo.open("daily", version="rollback").files()[0]
    path.unlink()
    with pytest.raises(FileNotFoundError):
        repo.clean("daily", quiescent=True)
    assert repo.open("daily").files()[0].read_bytes() == b"new"


def test_cleanup_interrupted_can_restart(repo, monkeypatch):
    for i in range(4):
        publish(repo, str(i).encode())
    original = Path.unlink
    count = 0

    def fail(path, *args, **kwargs):
        nonlocal count
        if path.is_relative_to(repo._directory("daily")):
            count += 1
            if count == 2:
                raise OSError("injected unlink failure")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail)
        with pytest.raises(OSError):
            repo.clean("daily", quiescent=True)
    assert repo.open("daily").files()[0].read_bytes() == b"3"
    assert repo.open("daily", version="rollback").files()[0].read_bytes() == b"2"
    repo.clean("daily", quiescent=True)
    assert repo.clean("daily", quiescent=True).deleted_files == 0


def test_sync_failure_prevents_cleanup(repo, monkeypatch):
    for i in range(3):
        publish(repo, str(i).encode())
    before = set(repo._directory("daily").rglob("*"))

    def fail(*args):
        raise OSError("sync failed")

    monkeypatch.setattr(fs, "sync_paths", fail)
    with pytest.raises(OSError):
        repo.clean("daily", quiescent=True)
    assert before == set(repo._directory("daily").rglob("*"))


@pytest.mark.parametrize("suffix", ["../escape", "/absolute", "a//b", "a/./b", "a\\b"])
def test_reject_path_escape(repo, suffix):
    with repo.prepare("daily") as draft, pytest.raises(ValueError):
        draft.allocate("key", relative_path=suffix)


def test_symlink_in_garbage_aborts_before_unlink(repo, tmp_path):
    publish(repo, b"1")
    publish(repo, b"2")
    publish(repo, b"3")
    outside = tmp_path / "outside"
    outside.write_bytes(b"safe")
    (repo._directory("daily") / "objects" / "bad").symlink_to(outside)
    before = set(repo._directory("daily").rglob("*"))
    with pytest.raises(StoreCorruptionError):
        repo.clean("daily", quiescent=True)
    assert before == set(repo._directory("daily").rglob("*"))
    assert outside.read_bytes() == b"safe"


def test_symlink_control_rejected(repo, tmp_path):
    publish(repo, b"1")
    path = repo._directory("daily") / "head.json"
    other = tmp_path / "head"
    path.rename(other)
    path.symlink_to(other)
    with pytest.raises(StoreCorruptionError):
        repo.describe("daily")


def test_read_does_not_probe_data_or_write(repo, monkeypatch):
    publish(repo, b"1")
    original = fs.checked

    def check(base, suffix="", **kwargs):
        assert "/objects/" not in suffix
        return original(base, suffix, **kwargs)

    monkeypatch.setattr(fs, "checked", check)

    def forbidden(*args, **kwargs):
        raise AssertionError("read attempted mutation or lock")

    monkeypatch.setattr(fs, "write", forbidden)
    monkeypatch.setattr(fs, "lock", forbidden)
    binding = repo.open("daily")
    assert binding.revision == 1
    binding.files()


def test_strict_codec():
    identifier = uuid4().hex
    for payload in [
        b'{"dataset_id":"a","dataset_id":"b"}',
        b"[]",
        b"{}",
        json.dumps(dict(dataset_id="a", revision=True, current=identifier)).encode(),
    ]:
        with pytest.raises(StoreCorruptionError):
            decode_head(payload)
    with pytest.raises(StoreCorruptionError):
        decode_manifest(
            json.dumps(
                dict(
                    dataset_id="a",
                    version_id=identifier,
                    members=[dict(key="a", path="../outside")],
                )
            ).encode()
        )
    with pytest.raises(ValueError):
        Manifest("a", identifier, (Member("a", identifier + "/x"), Member("a", identifier + "/y")))


def _worker(root, expected, queue):
    try:
        with Repository(root).prepare("daily", expected_revision=expected, durable=False) as draft:
            draft.allocate("data").write_bytes(b"child")
            draft.seal()
            draft.commit()
        queue.put("published")
    except PublicationConflictError:
        queue.put("conflict")


def test_process_writers_compare_after_lock(repo):
    publish(repo, b"initial")
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    workers = [context.Process(target=_worker, args=(str(repo.root), 1, queue)) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(15)
        if worker.is_alive():
            worker.kill()
            worker.join()
            pytest.fail("writer hung")
        assert worker.exitcode == 0
    assert {queue.get(timeout=3), queue.get(timeout=3)} == {"published", "conflict"}
    assert repo.describe("daily").revision == 2


def _crash(root, phase):
    repo = Repository(root)
    with repo.prepare("daily", durable=False) as draft:
        draft.allocate("data").write_bytes(b"child")
        draft.seal()
        if phase == "committed":
            draft.commit()
        os._exit(12)


@pytest.mark.parametrize("phase", ["sealed", "committed"])
def test_process_death(repo, phase):
    initial = publish(repo, b"initial", durable=True)
    worker = multiprocessing.get_context("spawn").Process(
        target=_crash, args=(str(repo.root), phase)
    )
    worker.start()
    worker.join(15)
    if worker.is_alive():
        worker.kill()
        worker.join()
        pytest.fail("child hung")
    assert worker.exitcode == 12
    assert repo.describe("daily").revision == initial.revision + (phase == "committed")
    repo.clean("daily", quiescent=True)
    assert repo.open("daily").files()[0].read_bytes() == (
        b"child" if phase == "committed" else b"initial"
    )


def test_seal_does_not_install_undeclared_workfiles(repo):
    with repo.prepare("daily", durable=False) as draft:
        path = draft.allocate("data")
        path.write_bytes(b"good")
        raw = path.with_suffix(".raw")
        raw.write_bytes(b"intermediate")
        binding = draft.seal()
        draft.commit()
    assert not binding.files()[0].with_suffix(".raw").exists()
    assert raw.exists()
    repo.clean("daily", quiescent=True)
    assert not raw.exists()


def test_first_failed_draft_can_be_recomputed(repo):
    with repo.prepare("daily", durable=False) as draft:
        draft.allocate("bad").write_bytes(b"failed")
        draft.seal()
    assert repo.clean("daily", quiescent=True).skipped is not None
    publish(repo, b"success")
    repo.clean("daily", quiescent=True)
    assert repo.open("daily").files()[0].read_bytes() == b"success"
    assert len(list((repo._directory("daily") / "versions").glob("*.json"))) == 1


def test_reuse_once_even_for_empty_current(repo):
    with repo.prepare("daily") as draft:
        draft.reuse_current()
        with pytest.raises(CandidateStateError):
            draft.reuse_current()
        draft.seal()
        draft.commit()
    assert repo.open("daily").files() == ()


def test_object_symlink_cannot_be_sealed(repo, tmp_path):
    external = tmp_path / "external"
    external.write_bytes(b"outside")
    with repo.prepare("daily") as draft:
        path = draft.allocate("data")
        path.symlink_to(external)
        with pytest.raises(StoreCorruptionError):
            draft.seal()
    assert repo.list_datasets() == ()
    assert external.read_bytes() == b"outside"


def test_rollback_sync_error_does_not_claim_success(repo, monkeypatch):
    first = publish(repo, b"one")
    publish(repo, b"two")
    original = fs.sync_paths

    def fail(base, paths):
        if any(p.name == "head.json" for p in paths):
            raise OSError("head sync failed after replace")
        return original(base, paths)

    with monkeypatch.context() as patch:
        patch.setattr(fs, "sync_paths", fail)
        with pytest.raises(OSError):
            repo.rollback("daily", expected_revision=2)
    state = repo.describe("daily")
    assert state.current == first.current and state.revision == 3
    assert state.rollback is None
    repo.clean("daily", quiescent=True)
    assert repo.open("daily").files()[0].read_bytes() == b"one"


@pytest.mark.parametrize(
    "change",
    [
        {"revision": True},
        {"revision": -1},
        {"revision": 2**63},
        {"current": "../outside"},
        {"dataset_id": "wrong"},
        {"extra": 1},
    ],
)
def test_invalid_head_never_opens_or_deletes(repo, change):
    publish(repo, b"one")
    path = repo._directory("daily") / "head.json"
    value = json.loads(path.read_text())
    value.update(change)
    path.write_text(json.dumps(value))
    with pytest.raises(StoreCorruptionError):
        repo.open("daily")
    with pytest.raises(StoreCorruptionError):
        repo.clean("daily", quiescent=True)


def test_cleanup_dry_run_does_not_sync_or_unlink(repo, monkeypatch):
    for i in range(3):
        publish(repo, str(i).encode())

    def fail(*args, **kwargs):
        raise AssertionError("dry run attempted a mutation")

    monkeypatch.setattr(fs, "sync_paths", fail)
    monkeypatch.setattr(Path, "unlink", fail)
    assert repo.clean("daily", quiescent=True, dry_run=True).candidate_files == 2


def test_weak_publish_skips_fsync_but_cleanup_syncs_before_delete(repo, monkeypatch):
    for i in range(2):
        publish(repo, str(i).encode())

    def fail(*args, **kwargs):
        raise AssertionError("weak publish attempted fsync")

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", fail)
        publish(repo, b"latest", durable=False)
    synced = set()
    original_sync = fs.sync_paths
    original_unlink = Path.unlink

    def sync(base, paths):
        original_sync(base, paths)
        synced.update(paths)

    def unlink(path, *args, **kwargs):
        assert repo._directory("daily") / "head.json" in synced
        assert set(repo.open("daily").files()).issubset(synced)
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(fs, "sync_paths", sync)
    monkeypatch.setattr(Path, "unlink", unlink)
    repo.clean("daily", quiescent=True)
