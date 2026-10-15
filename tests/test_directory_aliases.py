"""Resolve directory aliases at entry, without following links inside owned data."""

import json
from pathlib import Path

import pytest

from asterstore import Dataset, PublicationConflictError, Repository, StoreCorruptionError
from asterstore import _storage as fs


@pytest.mark.parametrize("layout", ["root", "parent", "chain", "missing_target"])
def test_alias_publication_rollback_and_cleanup(tmp_path: Path, layout: str) -> None:
    physical = tmp_path / "physical"
    physical.mkdir()
    alias = tmp_path / "AsterStore"
    if layout == "parent":
        alias.symlink_to(physical, target_is_directory=True)
        entry = alias / "registry"
    elif layout == "chain":
        intermediate = tmp_path / "intermediate"
        intermediate.symlink_to("physical/registry", target_is_directory=True)
        alias.symlink_to("intermediate", target_is_directory=True)
        entry = alias
    else:
        alias.symlink_to(physical / "registry", target_is_directory=True)
        entry = alias
    if layout != "missing_target":
        (physical / "registry").mkdir()
    repo = Repository(entry)
    repo.initialize()
    data_alias = tmp_path / "prices"
    data_alias.symlink_to(physical / "data", target_is_directory=True)
    ds = repo.create_dataset("prices", data_alias)
    assert repo.root == physical / "registry"
    assert ds.root == physical / "data"
    registry = json.loads((repo.control / "registry.json").read_text())
    assert registry["datasets"]["prices"] == str(ds.root)
    with ds.write() as batch:
        for day in range(8, 11):
            batch.stage(f"date={day}/data").write_bytes(b"old")
        first = batch.commit(metadata={"through": 10}, quiescent=True)
    reopened = Repository(entry).dataset("prices")
    assert Dataset(data_alias, "prices").current() == first
    with reopened.write(expected_revision=first.revision) as batch:
        for day in range(8, 13):
            batch.stage(f"date={day}/data").write_bytes(b"new")
        batch.commit(metadata={"through": 12}, quiescent=True)
    with pytest.raises(PublicationConflictError), ds.write(expected_revision=first.revision):
        pass
    assert len(reopened.clean()) == 1
    restored = ds.rollback(quiescent=True)
    assert restored.metadata == first.metadata
    for day in range(8, 11):
        assert (data_alias / f"date={day}/data").read_bytes() == b"old"
    for day in (11, 12):
        assert not (data_alias / f"date={day}/data").exists()
    ds.clean()
    assert list((ds.control / "work").iterdir()) == []
    assert alias.is_symlink() and data_alias.is_symlink()


def test_alias_overlap_and_control_directory_rejected(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "registry")
    repo.initialize()
    ds = repo.create_dataset("prices", tmp_path / "data")
    alias = tmp_path / "alias"
    alias.symlink_to(ds.root, target_is_directory=True)
    for path in (alias, alias / "child"):
        with pytest.raises(ValueError, match="overlap"):
            repo.create_dataset("other", path)
    control_alias = tmp_path / "control"
    control_alias.symlink_to(repo.control, target_is_directory=True)
    with pytest.raises(ValueError, match="overlap"):
        repo.create_dataset("other", control_alias / "child")
    with pytest.raises(FileExistsError):
        Repository(repo.root).initialize()


def test_retargeting_entry_alias_does_not_redirect_existing_bindings(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    alias = tmp_path / "alias"
    alias.symlink_to(actual, target_is_directory=True)
    repo = Repository(alias)
    repo.initialize()
    data_alias = tmp_path / "prices"
    data_alias.symlink_to(tmp_path / "data", target_is_directory=True)
    ds = repo.create_dataset("prices", data_alias)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_bytes(b"untouched")
    for link in (alias, data_alias):
        link.unlink()
        link.symlink_to(outside, target_is_directory=True)
    with repo.dataset("prices").write() as batch:
        batch.stage("data").write_bytes(b"new")
        batch.commit(metadata={}, quiescent=True)
    assert ds.path("data").read_bytes() == b"new"
    ds.rollback(quiescent=True)
    ds.clean()
    assert list(outside.iterdir()) == [outside / "keep"]
    assert (outside / "keep").read_bytes() == b"untouched"


def test_registered_physical_root_must_not_silently_follow_a_new_link(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "registry")
    repo.initialize()
    ds = repo.create_dataset("prices", tmp_path / "data")
    moved = tmp_path / "moved"
    ds.root.rename(moved)
    ds.root.symlink_to(moved, target_is_directory=True)
    with pytest.raises(StoreCorruptionError):
        repo.dataset("prices")
    with pytest.raises(StoreCorruptionError):
        ds.current()


def test_internal_partition_symlink_still_rejected(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "registry")
    repo.initialize()
    ds = repo.create_dataset("prices", tmp_path / "data")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_bytes(b"untouched")
    (ds.root / "date=1").symlink_to(outside, target_is_directory=True)
    with ds.write() as batch, pytest.raises(StoreCorruptionError):
        batch.stage("date=1/keep")
    assert (outside / "keep").read_bytes() == b"untouched"


def test_alias_reopen_recovers_interrupted_publication(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "root"
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    repo = Repository(alias)
    repo.initialize()
    ds = repo.create_dataset("prices", alias / "data")
    with ds.write() as batch:
        batch.stage("data").write_bytes(b"old")
        before = batch.commit(metadata={"value": "old"}, quiescent=True)
    original = fs.install
    with monkeypatch.context() as patch:

        def interrupted(source, target):
            original(source, target)
            raise OSError("interrupted installation")

        patch.setattr(fs, "install", interrupted)
        with pytest.raises(OSError, match="interrupted installation"), ds.write() as batch:
            batch.stage("data").write_bytes(b"new")
            batch.commit(metadata={"value": "new"}, quiescent=True)
    reopened = Repository(alias).dataset("prices")
    assert reopened.recover(quiescent=True) == before
    reopened.clean()
    assert reopened.path("data").read_bytes() == b"old"
