import builtins
import io
import os
from pathlib import Path

import pytest

from asterstore import Repository


def test_disk_binding_only_reads_control_records_and_does_not_require_data(tmp_path, monkeypatch):
    repo = Repository(tmp_path)
    repo.initialize(resource_ids=["data"], managed_resource_id="data", lifecycle=True)
    for generation in range(2):
        with repo.prepare(
            "dataset",
            publication_id=str(generation),
            operation_id=str(generation),
            expected_generation=generation,
            durable=False,
        ) as writer:
            writer.write_bytes("part", b"data", relative_path="part.bin")
            writer.commit()
    assert repo.governance.collect("gc").complete
    data_path = repo.open("dataset").files()[0]
    data_path.unlink()
    reads = []
    original = io.open

    def observe(file, *args, **kwargs):
        reads.append(Path(file))
        return original(file, *args, **kwargs)

    def forbidden(*args, **kwargs):
        raise AssertionError("ordinary open must not probe or enumerate data")

    with monkeypatch.context() as patch:
        patch.setattr(io, "open", observe)
        for name in ("stat", "lstat", "listdir", "scandir", "mkdir"):
            patch.setattr(os, name, forbidden)
        binding = repo.open("dataset")
        assert binding.files() == (data_path,)
    assert len(reads) == 2
    assert reads[0] == tmp_path / ".asterstore/format.json"
    assert reads[1].name == "current.json"
    with pytest.raises(FileNotFoundError):
        binding.files()[0].read_bytes()


def test_governance_service_construction_has_no_io(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("service construction performed I/O")

    with monkeypatch.context() as patch:
        for module, names in ((io, ("open",)), (os, ("open", "stat", "lstat", "mkdir", "scandir"))):
            for name in names:
                patch.setattr(module, name, forbidden)
        assert Repository(tmp_path).governance.root == tmp_path


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
