"""Unsupported directories are rejected without writes; only one model is accepted."""

import json
from pathlib import Path

import pytest

from asterstore import Repository, StoreCorruptionError
from asterstore.metadata.protocol import StoreRecord, encode_store


@pytest.mark.parametrize("version", [1, 2, 3, 4])
def test_development_repository_headers_are_rejected_without_writes(tmp_path, version):
    control = tmp_path / ".asterstore"
    control.mkdir()
    marker = control / "format.json"
    marker.write_text(json.dumps({"format_version": version, "kind": "repository"}))
    data = tmp_path / "untouched.bin"
    data.write_bytes(b"external bytes")
    before = {
        str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()
    }
    entries = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))
    repo = Repository(tmp_path)
    for action in (
        lambda: repo.open("data"),
        lambda: repo.initialize(resource_ids=["data"], managed_resource_id="data", lifecycle=True),
        lambda: repo.prepare("data", publication_id="p", operation_id="op", expected_generation=0),
        lambda: repo.governance.collect("gc"),
        lambda: repo.governance.cleanup("op"),
    ):
        with pytest.raises(StoreCorruptionError):
            action()
        assert {
            str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()
        } == before
        assert sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*")) == entries


@pytest.mark.parametrize("version", [0, 2, 3, 4, 99, True, 1.0])
def test_store_header_requires_exact_supported_version(tmp_path, version):
    value = json.loads(encode_store(StoreRecord("s", ("data",), "data", True)))
    value["format_version"] = version
    marker = tmp_path / ".asterstore/format.json"
    marker.parent.mkdir()
    marker.write_text(json.dumps(value))
    with pytest.raises(StoreCorruptionError):
        Repository(tmp_path).initialize(resource_ids=["data"])
    assert json.loads(marker.read_text()) == value


def test_current_schema_and_fixtures_agree():
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, str(root / "tools/check_protocol.py")], check=True, cwd=root)
