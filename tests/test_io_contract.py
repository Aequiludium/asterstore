import builtins
import io
import os
from pathlib import Path

import pytest

from asterstore import Binding, CollectionPolicy, Dataset, Publication, Repository


def test_bind_and_repeated_selection_do_not_probe_or_write_files(
    tmp_path: Path, publication: Publication, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("unexpected filesystem I/O in binding or selection")

    keys = [publication.objects[0].key]
    expected = (tmp_path / keys[0],)
    with monkeypatch.context() as patch:
        for module, names in (
            (builtins, ("open",)),
            (io, ("open",)),
            (os, ("open", "stat", "lstat", "listdir", "scandir", "mkdir", "readlink")),
        ):
            for name in names:
                patch.setattr(module, name, forbidden)
        binding = Repository(tmp_path).bind(publication)
        for _ in range(3):
            assert binding.files(keys=keys) == expected
            assert len(binding.files()) == len(publication.objects)


def test_selection_does_not_rebind_or_reconstruct_paths(
    tmp_path: Path, publication: Publication, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding = Repository(tmp_path).bind(publication)
    expected = binding.files()

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("selection must reuse the existing binding")

    with monkeypatch.context() as patch:
        patch.setattr(Binding, "__post_init__", forbidden)
        patch.setattr(Path, "joinpath", forbidden)
        assert binding.files() is expected
        assert binding.files(keys=[publication.objects[1].key]) == (expected[1],)


def test_disk_binding_only_reads_control_records_and_does_not_require_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = Repository(tmp_path)
    with repository.prepare(Dataset("data"), durable=False) as old:
        old.write_bytes("old.bin", b"old")
        old.commit()
    with repository.prepare(Dataset("data"), durable=False) as candidate:
        candidate.write_bytes("part.bin", b"data")
        candidate.commit()
    assert repository.retention.collect(CollectionPolicy(("data",))).status == "complete"
    data_path = repository.open("data").files()[0]
    data_path.unlink()
    reads = []
    original = io.open

    def observe(file, *args, **kwargs):
        reads.append(Path(file))
        return original(file, *args, **kwargs)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("ordinary disk binding must not probe files or enumerate directories")

    with monkeypatch.context() as patch:
        patch.setattr(io, "open", observe)
        for name in ("stat", "lstat", "listdir", "scandir", "mkdir"):
            patch.setattr(os, name, forbidden)
        binding = repository.open("data")
        assert binding.files() == (data_path,)
    assert len(reads) == 2
    assert reads[0] == tmp_path / ".asterstore/format.json"
    assert reads[1].name == "current.json"
    with pytest.raises(FileNotFoundError):
        binding.files()[0].read_bytes()


def test_retention_service_construction_has_no_io(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("retention service construction performed I/O")

    with monkeypatch.context() as patch:
        for module, names in ((io, ("open",)), (os, ("open", "stat", "lstat", "mkdir", "scandir"))):
            for name in names:
                patch.setattr(module, name, forbidden)
        service = Repository(tmp_path).retention
        assert service.root == tmp_path
        assert Repository(tmp_path).inspection.root == tmp_path


def test_new_declaration_construction_and_member_selection_have_no_filesystem_io(
    tmp_path, monkeypatch
):
    from asterstore import Capabilities, Declaration, FileSet, Locator, Member, Object

    def forbidden(*args, **kwargs):
        raise AssertionError("explicit declarations must not probe, scan, hash or write files")

    with monkeypatch.context() as patch:
        for module, names in (
            (builtins, ("open",)),
            (io, ("open",)),
            (os, ("open", "stat", "lstat", "listdir", "scandir", "mkdir", "readlink")),
        ):
            for name in names:
                patch.setattr(module, name, forbidden)
        declaration = Declaration(
            "images",
            "batch:1",
            FileSet(
                [Object("object:1", Locator("source", "a.bin"))],
                [Member("label:../a", "object:1")],
            ),
            Capabilities.registered(),
        )
        binding = Repository(tmp_path / "control").bind(
            declaration, resources={"source": tmp_path / "images"}
        )
        cached = binding.files()
        expected = (tmp_path / "images/a.bin",)
        patch.setattr(Path, "joinpath", forbidden)
        for _ in range(5):
            assert binding.files() is cached
            assert binding.files(keys=["label:../a"]) == expected
