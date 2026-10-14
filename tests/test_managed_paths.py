"""Real-path adoption and cleanup ownership boundaries."""

import multiprocessing
import os
from pathlib import Path

import pytest

from asterstore import Repository, StoreCorruptionError
from asterstore import _storage as fs


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    result = Repository(tmp_path / "control")
    result.initialize()
    return result


def publish(repo, data=b"next", dataset="table"):
    with repo.prepare(dataset) as draft:
        draft.allocate("data").write_bytes(data)
        draft.seal()
        return draft.commit()


def test_adopt_preserves_exact_path_inode_and_bytes(repo, tmp_path):
    source = tmp_path / "existing" / "date=2026-10-14" / "data.parquet"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"existing bytes")
    before = source.stat()
    with repo.prepare("table") as draft:
        draft.adopt("data", source)
        candidate = draft.seal()
        assert candidate.files() == (source,)
        draft.commit()
    after = source.stat()
    assert (after.st_dev, after.st_ino, after.st_nlink) == (
        before.st_dev,
        before.st_ino,
        before.st_nlink,
    )
    assert source.read_bytes() == b"existing bytes"
    assert repo.open("table").files() == (source,)
    publish(repo)
    assert repo.open("table", version="rollback").files() == (source,)
    repo.rollback("table", expected_revision=2)
    repo.clean("table", quiescent=True)
    assert source.read_bytes() == b"existing bytes"


@pytest.mark.parametrize("external", [True, False])
def test_allocate_exact_destination_and_reuse(repo, tmp_path, external):
    destination = (tmp_path if external else repo.root) / "chosen" / "specific.bin"
    argument = destination if external else destination.relative_to(repo.root)
    with repo.prepare("table") as draft:
        staging = draft.allocate("data", path=argument)
        staging.write_bytes(b"result")
        assert staging == destination
        candidate = draft.seal()
        assert candidate.files() == (destination,)
        draft.commit()
    with repo.prepare("table") as draft:
        draft.reuse_current()
        draft.alias("same", "data")
        draft.seal()
        draft.commit()
    assert repo.open("table").files() == (destination, destination)
    repo.clean("table", quiescent=True)
    assert destination.read_bytes() == b"result"
    assert destination.stat().st_nlink == 1


def test_cleanup_only_declared_files_in_shared_directory(repo, tmp_path):
    old = tmp_path / "old"
    unrelated = tmp_path / "unrelated"
    other = tmp_path / "other-table"
    for path in (old, unrelated, other):
        path.write_bytes(b"keep unless owned and obsolete")
    with repo.prepare("table") as draft:
        draft.adopt("data", old)
        draft.seal()
        draft.commit()
    with repo.prepare("other") as draft:
        draft.adopt("data", other)
        draft.seal()
        draft.commit()
    publish(repo)
    repo.clean("table", quiescent=True)
    assert old.exists()  # rollback
    publish(repo)
    preview = repo.clean("table", quiescent=True, dry_run=True)
    assert preview.candidate_files == 2
    assert old.exists()
    repo.clean("table", quiescent=True)
    assert not old.exists()
    assert unrelated.exists() and other.exists()
    assert repo.clean("table", quiescent=True).candidate_files == 0


def test_reject_cross_dataset_adoption_and_controls(repo, tmp_path):
    source = tmp_path / "file"
    source.write_bytes(b"owned")
    with repo.prepare("first") as draft:
        draft.adopt("data", source)
        draft.seal()
        draft.commit()
    with repo.prepare("second") as draft:
        draft.adopt("data", source)
        with pytest.raises(ValueError, match="another dataset"):
            draft.seal()
    # Ownership survives leaving both slots until cleanup.
    publish(repo, dataset="first")
    publish(repo, dataset="first")
    with repo.prepare("second") as draft:
        draft.adopt("data", source)
        with pytest.raises(ValueError, match="another dataset"):
            draft.seal()
    with repo.prepare("first") as draft:
        with pytest.raises(StoreCorruptionError):
            draft.adopt("marker", repo.root / ".asterstore/format.json")
        with pytest.raises(StoreCorruptionError):
            draft.allocate("head", path=repo._directory("first") / "head.json")


def test_existing_destination_never_overwritten(repo, tmp_path):
    destination = tmp_path / "data"
    destination.write_bytes(b"original")
    with repo.prepare("table") as draft:
        with pytest.raises(FileExistsError):
            draft.allocate("data", path=destination)
    assert destination.read_bytes() == b"original"


def test_adoption_preflight_failure_does_not_claim_source(repo, tmp_path):
    publish(repo)
    source = tmp_path / "source"
    source.write_bytes(b"not transferred")
    with repo.prepare("table") as draft:
        draft.adopt("source", source)
        draft.allocate("missing")
        with pytest.raises(FileNotFoundError):
            draft.seal()
    repo.clean("table", quiescent=True)
    assert source.read_bytes() == b"not transferred"


def test_failure_after_claim_remains_reclaimable(repo, tmp_path, monkeypatch):
    publish(repo)
    destination = tmp_path / "claimed"
    original = os.link

    def fail(source, target, *args, **kwargs):
        if "objects" in Path(target).parts:
            raise OSError("interrupted installation")
        return original(source, target, *args, **kwargs)

    with repo.prepare("table") as draft:
        draft.allocate("external", path=destination).write_bytes(b"in place")
        draft.allocate("internal").write_bytes(b"staged")
        with monkeypatch.context() as patch:
            patch.setattr(os, "link", fail)
            with pytest.raises(OSError, match="interrupted installation"):
                draft.seal()
    assert destination.exists()
    repo.clean("table", quiescent=True)
    assert not destination.exists()
    assert repo.open("table").files()[0].read_bytes() == b"next"


def test_custom_output_is_never_moved_or_linked(repo, tmp_path, monkeypatch):
    destination = tmp_path / "custom-output"
    original = os.link

    def control_only(source, target, *args, **kwargs):
        assert Path(target) != destination
        return original(source, target, *args, **kwargs)

    with monkeypatch.context() as patch, repo.prepare("table") as draft:
        patch.setattr(os, "link", control_only)
        output = draft.allocate("data", path=destination)
        output.write_bytes(b"in place")
        inode = output.stat().st_ino
        draft.seal()
        draft.commit()
    assert repo.open("table").files() == (destination,)
    assert destination.stat().st_ino == inode
    assert destination.read_bytes() == b"in place"


def test_cleanup_keeps_claim_until_external_unlink_is_synced(repo, tmp_path, monkeypatch):
    source = tmp_path / "obsolete"
    source.write_bytes(b"old")
    with repo.prepare("table") as draft:
        draft.adopt("data", source)
        draft.seal()
        first = draft.commit()
    publish(repo)
    publish(repo)
    manifest = repo._directory("table") / "versions" / f"{first.current}.json"
    original = fs.sync_directory

    def fail(path):
        if path == source.parent and not source.exists():
            raise OSError("unlink sync failed")
        return original(path)

    with monkeypatch.context() as patch:
        patch.setattr(fs, "sync_directory", fail)
        with pytest.raises(OSError, match="unlink sync failed"):
            repo.clean("table", quiescent=True)
    assert not source.exists()
    assert manifest.exists()
    repo.clean("table", quiescent=True)
    assert not manifest.exists()


def test_external_symlink_aborts_cleanup_before_any_delete(repo, tmp_path):
    source = tmp_path / "obsolete"
    outside = tmp_path / "unrelated"
    source.write_bytes(b"old")
    outside.write_bytes(b"safe")
    with repo.prepare("table") as draft:
        draft.adopt("data", source)
        draft.seal()
        draft.commit()
    publish(repo)
    publish(repo)
    source.unlink()
    source.symlink_to(outside)
    manifests = set((repo._directory("table") / "versions").iterdir())
    with pytest.raises(StoreCorruptionError):
        repo.clean("table", quiescent=True)
    assert set((repo._directory("table") / "versions").iterdir()) == manifests
    assert outside.read_bytes() == b"safe"


def _claim(root, path, dataset, queue):
    try:
        with Repository(root).prepare(dataset) as draft:
            draft.adopt("data", path)
            draft.seal()
            draft.commit()
        queue.put("published")
    except ValueError:
        queue.put("conflict")


def test_concurrent_dataset_claims_have_one_owner(repo, tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"single owner")
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    workers = [
        context.Process(target=_claim, args=(repo.root, source, name, queue))
        for name in ("one", "two")
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(15)
        if worker.is_alive():
            worker.kill()
            worker.join()
            pytest.fail("ownership lock deadlocked")
        assert worker.exitcode == 0
    assert {queue.get(timeout=3), queue.get(timeout=3)} == {"published", "conflict"}


def test_unsealed_custom_output_stays_with_caller(repo, tmp_path):
    publish(repo)
    output = tmp_path / "not-sealed"
    with repo.prepare("table") as draft:
        draft.allocate("data", path=output).write_bytes(b"unfinished")
    repo.clean("table", quiescent=True)
    assert output.read_bytes() == b"unfinished"


def test_adopt_rejects_hardlinks_and_file_symlinks(repo, tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"one owner")
    link = tmp_path / "link"
    os.link(source, link)
    with repo.prepare("table") as draft:
        with pytest.raises(ValueError, match="one hard link"):
            draft.adopt("hardlink", source)
        link.unlink()
        link.symlink_to(source)
        with pytest.raises(StoreCorruptionError):
            draft.adopt("symlink", link)


def test_directory_aliases_cannot_bypass_file_ownership(repo, tmp_path):
    actual = tmp_path / "actual"
    actual.mkdir()
    source = actual / "data"
    source.write_bytes(b"owned")
    alias = tmp_path / "alias"
    alias.symlink_to(actual, target_is_directory=True)
    with repo.prepare("one") as draft:
        draft.adopt("data", source)
        draft.seal()
        draft.commit()
    with repo.prepare("two") as draft:
        draft.adopt("data", alias / "data")
        with pytest.raises(ValueError, match="another dataset"):
            draft.seal()


def test_missing_owned_path_cannot_be_reallocated(repo, tmp_path):
    source = tmp_path / "owned"
    source.write_bytes(b"published")
    with repo.prepare("table") as draft:
        draft.adopt("data", source)
        draft.seal()
        draft.commit()
    source.unlink()  # Simulate damage; cannot replace immutable historical bytes.
    with repo.prepare("table") as draft:
        with pytest.raises(FileExistsError, match="already owned"):
            draft.allocate("data", path=source)
    assert not source.exists()


def test_open_path_validation_does_not_add_per_member_io(repo, monkeypatch):
    with repo.prepare("table") as draft:
        for index in range(100):
            draft.allocate(str(index)).write_bytes(b"data")
        draft.seal()
        draft.commit()
    calls = []
    original = fs.checked

    def checked(base, suffix="", **kwargs):
        calls.append(suffix)
        return original(base, suffix, **kwargs)

    monkeypatch.setattr(fs, "checked", checked)
    binding = repo.open("table")
    assert len(binding.files()) == 100
    assert len(calls) == 5  # marker, two directory checks, head and manifest


@pytest.mark.parametrize("name", ["ownership.lock", "init.lock", "format.json"])
def test_control_file_descendants_rejected_before_directory_creation(repo, name):
    reserved = repo.root / ".asterstore" / name
    before = {p: p.read_bytes() for p in repo.root.rglob("*") if p.is_file()}
    with repo.prepare("table") as draft:
        with pytest.raises(StoreCorruptionError, match="repository control"):
            draft.allocate("data", path=reserved / "child.bin")
        assert not reserved.is_dir()
        draft.allocate("normal").write_bytes(b"valid")
        draft.seal()
        draft.commit()
    for path, value in before.items():
        assert path.read_bytes() == value
    assert repo.open("table").files()[0].read_bytes() == b"valid"


def test_custom_paths_cannot_enter_default_storage(repo):
    publish(repo)
    existing = repo.open("table").files()[0]
    output = repo._directory("table") / "objects" / "custom" / "data"
    with repo.prepare("table") as draft:
        with pytest.raises(StoreCorruptionError, match="default managed storage"):
            draft.allocate("custom", path=output)
        with pytest.raises(StoreCorruptionError, match="default managed storage"):
            draft.adopt("custom", existing)
        draft.reuse_current()
        draft.alias("same", "data")
        draft.seal()
        draft.commit()
    assert not output.parent.exists()
    repo.clean("table", quiescent=True)
    assert repo.open("table").files() == (existing, existing)


def test_cleanup_durably_releases_claim_after_manifest_unlink(repo, tmp_path, monkeypatch):
    source = tmp_path / "reusable"
    source.write_bytes(b"old")
    with repo.prepare("table") as draft:
        draft.adopt("data", source)
        draft.seal()
        old = draft.commit()
    publish(repo)
    publish(repo)
    versions = repo._directory("table") / "versions"
    old_manifest = versions / f"{old.current}.json"
    original = fs.sync_directory
    durable_release = []

    def sync(path):
        original(path)
        if path == versions and not old_manifest.exists():
            assert not source.exists()
            durable_release.append(path)

    with monkeypatch.context() as patch:
        patch.setattr(fs, "sync_directory", sync)
        repo.clean("table", quiescent=True)
    assert durable_release == [versions]
    with repo.prepare("other") as draft:
        draft.allocate("data", path=source).write_bytes(b"new owner")
        draft.seal()
        draft.commit()
    repo.clean("table", quiescent=True)
    assert repo.open("other").files()[0].read_bytes() == b"new owner"


@pytest.mark.parametrize("operation", ["allocate", "adopt", "retry_cleanup"])
def test_failed_claim_release_must_sync_before_reuse(repo, tmp_path, monkeypatch, operation):
    source = tmp_path / "reusable"
    source.write_bytes(b"old")
    with repo.prepare("table") as draft:
        draft.adopt("data", source)
        draft.seal()
        old = draft.commit()
    publish(repo)
    publish(repo)
    versions = repo._directory("table") / "versions"
    old_manifest = versions / f"{old.current}.json"
    original_sync = fs.sync_directory
    barriers = []

    def fail(path):
        if path == versions and not old_manifest.exists():
            barriers.append(path)
            raise OSError("ownership release sync failed")
        return original_sync(path)

    with monkeypatch.context() as patch:
        patch.setattr(fs, "sync_directory", fail)
        with pytest.raises(OSError, match="ownership release sync failed"):
            repo.clean("table", quiescent=True)
        assert not source.exists() and not old_manifest.exists()
        if operation == "retry_cleanup":
            with pytest.raises(OSError, match="ownership release sync failed"):
                repo.clean("table", quiescent=True)
        elif operation == "allocate":
            with repo.prepare("other") as draft:
                with pytest.raises(OSError, match="ownership release sync failed"):
                    draft.allocate("data", path=source)
            assert not source.exists()
        else:
            source.write_bytes(b"new externally prepared data")
            with repo.prepare("other") as draft:
                draft.adopt("data", source)
                with pytest.raises(OSError, match="ownership release sync failed"):
                    draft.seal()
            assert not list((repo._directory("other") / "versions").glob("*.json"))
    assert len(barriers) == 2
    repo.clean("table", quiescent=True)
    with repo.prepare("other") as draft:
        if operation == "adopt":
            draft.adopt("data", source)
        else:
            draft.allocate("data", path=source).write_bytes(b"new bytes")
        draft.seal()
        draft.commit()
    repo.clean("table", quiescent=True)
    assert repo.open("other").files() == (source,)
    assert source.exists()
