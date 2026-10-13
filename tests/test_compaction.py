"""Archive migration preserves logical state and fails closed on broken authority."""

import io
import json
import os
from pathlib import Path

import pytest

from asterstore import (
    CandidateStateError,
    HistoryAccess,
    PublicationRetiredError,
    Repository,
    StoreCorruptionError,
)
from asterstore.governance import _compaction
from asterstore.storage.registry import token


def create(root, count=3):
    repo = Repository(root)
    repo.initialize(
        store_id="archive-test", resource_ids=["owned"], managed_resource_id="owned", lifecycle=True
    )
    for number in range(count):
        with repo.prepare(
            "data",
            publication_id=f"p{number}",
            operation_id=f"op{number}",
            expected_generation=number,
            history=HistoryAccess.VERSIONED,
            durable=False,
        ) as writer:
            for index in range(8):
                writer.write_bytes(str(index), b"payload", relative_path=f"{index}.bin")
            writer.commit()
    return repo


def test_compact_history_retry_reuse_gc(tmp_path):
    repo = create(tmp_path)
    before = [repo.describe("data", publication_id=f"p{i}") for i in range(3)]
    result = repo.governance.compact("archive1", quiescent=True)
    assert result.complete and result.records > 0
    assert repo.governance.compact("archive1", quiescent=True) == result
    assert [repo.describe("data", publication_id=f"p{i}") for i in range(3)] == before
    with repo.resume("op0", durable=True) as writer:
        assert writer.commit() == before[0]
    with repo.prepare(
        "data",
        publication_id="p3",
        operation_id="op3",
        expected_generation=3,
        history=HistoryAccess.VERSIONED,
        durable=False,
    ) as writer:
        writer.reuse("p0")
        writer.commit()
    repo.governance.inspect()
    assert repo.governance.collect("gc").complete
    assert repo.describe("data", publication_id="p0") == before[0]
    with pytest.raises(PublicationRetiredError):
        repo.open("data", publication_id="p0")
    assert all(p.exists() for p in repo.open("data").files())
    repo.governance.compact("archive2", quiescent=True)
    assert repo.governance.resume_collection("gc").complete
    repo.governance.inspect()


def test_current_open_still_two_reads_without_probes(tmp_path, monkeypatch):
    repo = create(tmp_path)
    repo.governance.compact("archive", quiescent=True)
    reads = []
    original = io.open

    def observe(file, *args, **kwargs):
        reads.append(Path(file))
        return original(file, *args, **kwargs)

    def fail(*args, **kwargs):
        raise AssertionError("current read added governance I/O")

    with monkeypatch.context() as patch:
        patch.setattr(io, "open", observe)
        for name in ("stat", "lstat", "scandir", "listdir", "mkdir"):
            patch.setattr(os, name, fail)
        binding = repo.open("data")
        assert len(binding.files()) == 8
        assert binding.files() is binding.files()
    assert len(reads) == 2
    assert reads[0].name == "format.json" and reads[1].name == "current.json"


@pytest.mark.parametrize("boundary", ["marker", "pack", "checkpoint", "reference"])
def test_resume_after_write_boundary(tmp_path, monkeypatch, boundary):
    repo = create(tmp_path)
    original_atomic = _compaction.atomic_write
    original_immutable = _compaction.immutable_write
    fired = False

    def write(original, path, data, **kwargs):
        nonlocal fired
        result = original(path, data, **kwargs)
        match = {
            "marker": path.name == "format.json",
            "pack": path.suffix == ".pack",
            "checkpoint": path.parent.name == "archives" and path.suffix == ".json",
            "reference": b'"archive_reference"' in data and path.parent.name != "archives",
        }
        if match[boundary] and not fired:
            fired = True
            raise OSError("injected after durable write")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(
            _compaction, "atomic_write", lambda p, d, **kw: write(original_atomic, p, d, **kw)
        )
        patch.setattr(
            _compaction, "immutable_write", lambda p, d, **kw: write(original_immutable, p, d, **kw)
        )
        with pytest.raises(OSError, match="injected"):
            repo.governance.compact("archive", quiescent=True)
    assert fired
    repo.governance.inspect()
    assert repo.governance.compact("archive", quiescent=True).complete
    assert repo.describe("data", publication_id="p0").operation_id == "op0"


@pytest.mark.parametrize("damage", ["missing", "truncate", "symlink"])
def test_broken_archive_never_becomes_missing_operation(tmp_path, damage):
    repo = create(tmp_path)
    repo.governance.compact("archive", quiescent=True)
    path = next((tmp_path / ".asterstore/archives").glob("*.pack"))
    if damage == "truncate":
        path.write_bytes(b"bad")
    else:
        path.unlink()
        if damage == "symlink":
            path.symlink_to(tmp_path / "outside")
    with pytest.raises(StoreCorruptionError):
        repo.registration_status("op0")
    with pytest.raises(StoreCorruptionError):
        repo.governance.collect("must-not-delete")


def test_pending_candidate_blocks_before_feature_change(tmp_path):
    repo = create(tmp_path)
    marker = tmp_path / ".asterstore/format.json"
    before = marker.read_bytes()
    with repo.prepare(
        "data",
        publication_id="pending",
        operation_id="pending",
        expected_generation=3,
        history=HistoryAccess.VERSIONED,
        durable=False,
    ) as writer:
        writer.reuse("p0")
        writer.seal()
    with pytest.raises(CandidateStateError):
        repo.governance.compact("archive", quiescent=True)
    assert marker.read_bytes() == before


def test_checkpoint_cannot_target_data_or_current(tmp_path):
    repo = create(tmp_path)
    repo.governance.compact("archive", quiescent=True)
    plan = tmp_path / ".asterstore/archives" / (token("archive") + ".json")
    data = json.loads(plan.read_bytes())
    value = next(iter(data["records"].values()))
    data["records"] = {"../outside": value}
    plan.write_text(json.dumps(data))
    with pytest.raises(StoreCorruptionError):
        repo.governance.compact("archive", quiescent=True)


def test_no_gain_and_missing_quiescence_do_not_change_store(tmp_path):
    repo = create(tmp_path, count=0)
    marker = tmp_path / ".asterstore/format.json"
    before = marker.read_bytes()
    with pytest.raises(ValueError, match="quiescent"):
        repo.governance.compact("archive", quiescent=False)
    with pytest.raises(ValueError, match="no positive"):
        repo.governance.compact("archive", quiescent=True)
    assert marker.read_bytes() == before
    assert not (tmp_path / ".asterstore/archives").exists()


def test_logical_identity_conflict_survives_compaction(tmp_path):
    from asterstore import PublicationConflictError

    repo = create(tmp_path)
    repo.governance.compact("archive", quiescent=True)
    with pytest.raises(PublicationConflictError):
        with repo.prepare(
            "other",
            publication_id="other",
            operation_id="op0",
            expected_generation=0,
            history=HistoryAccess.VERSIONED,
        ):
            pass


def test_registered_retries_without_external_io(tmp_path):
    from asterstore import Capabilities, Declaration, FileSet, Locator, Member, Object

    repo = Repository(tmp_path)
    repo.initialize(store_id="registered", resource_ids=["external"], lifecycle=True)
    first = None
    for n in range(3):
        value = Declaration(
            "data",
            f"p{n}",
            FileSet(
                [Object(f"o{n}-{i}", Locator("external", f"{n}/{i}.bin")) for i in range(16)],
                [Member(str(i), f"o{n}-{i}") for i in range(16)],
            ),
            Capabilities.registered(),
        )
        result = repo.register(value, operation_id=f"op{n}", expected_generation=n, durable=False)
        if n == 0:
            first = result
    repo.governance.compact("archive", quiescent=True)
    assert repo.resume_registration("op0", durable=True) == first
    assert repo.describe("data", publication_id="p0") == first


def test_released_retention_and_completed_cleanup_survive(tmp_path):
    from asterstore import RetentionScope

    repo = create(tmp_path)
    repo.governance.retain("audit", "data", "p0", scope=RetentionScope.METADATA)
    ref = repo.governance.release("audit", expected_revision=1)
    with repo.prepare(
        "data",
        publication_id="failed",
        operation_id="failed",
        expected_generation=3,
        history=HistoryAccess.VERSIONED,
        durable=False,
    ) as writer:
        writer.reuse("p0")
        writer.seal()
    repo.governance.abandon("failed")
    cleanup = repo.governance.cleanup("failed")
    repo.governance.compact("archive", quiescent=True)
    assert repo.governance.get("audit") == ref
    assert repo.governance.cleanup("failed") == cleanup
    repo.governance.inspect()


@pytest.mark.parametrize(
    "field,value",
    [("offset", True), ("size", -1), ("archive", "../outside"), ("format_version", 2)],
)
def test_reference_rejects_invalid_extents_and_paths(field, value):
    from asterstore.metadata.protocol import decode_reference

    source = Path(__file__).parent / "fixtures/protocol/archive/reference.json"
    data = json.loads(source.read_bytes())
    data[field] = value
    with pytest.raises(StoreCorruptionError):
        decode_reference(json.dumps(data).encode())


def test_reference_duplicate_json_keys_rejected():
    from asterstore.metadata.protocol import decode_reference

    source = Path(__file__).parent / "fixtures/protocol/archive/reference.json"
    data = source.read_bytes().rstrip().removesuffix(b"}") + b', "size": 123}'
    with pytest.raises(StoreCorruptionError, match="duplicate"):
        decode_reference(data)


def test_orphan_pack_retry_after_intervening_publication(tmp_path, monkeypatch):
    repo = create(tmp_path)
    original = _compaction.immutable_write

    def stop_after_pack(path, data, **kwargs):
        original(path, data, **kwargs)
        if path.suffix == ".pack":
            raise OSError("pack durable; no checkpoint yet")

    with monkeypatch.context() as patch:
        patch.setattr(_compaction, "immutable_write", stop_after_pack)
        with pytest.raises(OSError):
            repo.governance.compact("archive", quiescent=True)
    old_pack = next((tmp_path / ".asterstore/archives").glob("*.pack"))
    old_bytes = old_pack.read_bytes()
    with repo.prepare(
        "data",
        publication_id="p3",
        operation_id="op3",
        expected_generation=3,
        history=HistoryAccess.VERSIONED,
        durable=False,
    ) as writer:
        writer.reuse("p0")
        writer.commit()
    result = repo.governance.compact("archive", quiescent=True)
    assert result.complete
    assert old_pack.read_bytes() == old_bytes
    assert len(list((tmp_path / ".asterstore/archives").glob("*.pack"))) == 2
    repo.governance.inspect()


@pytest.mark.parametrize("boundary", ["marker", "pack", "checkpoint", "reference"])
def test_process_exit_recovery(tmp_path, boundary):
    import subprocess
    import sys

    repo = create(tmp_path)
    script = """
import os,sys
from pathlib import Path
from asterstore import Repository
from asterstore.governance import _compaction as c
boundary=sys.argv[2]
def wrap(original):
    def write(path,data,**kwargs):
        original(path,data,**kwargs)
        match={"marker":path.name=="format.json", "pack":path.suffix==".pack",
               "checkpoint":path.parent.name=="archives" and path.suffix==".json",
               "reference":b'"archive_reference"' in data and path.parent.name!="archives"}
        if match[boundary]: os._exit(71)
    return write
c.atomic_write=wrap(c.atomic_write)
c.immutable_write=wrap(c.immutable_write)
Repository(Path(sys.argv[1])).governance.compact("process",quiescent=True)
"""
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path), boundary], check=False)
    assert result.returncode == 71
    assert repo.governance.compact("process", quiescent=True).complete
    repo.governance.inspect()
    assert repo.describe("data", publication_id="p0").operation_id == "op0"


def test_old_decoder_rejects_new_feature(tmp_path):
    import runpy

    repo = create(tmp_path)
    repo.governance.compact("archive", quiescent=True)
    namespace = runpy.run_path(
        str(Path(__file__).parent / "fixtures/protocol/archive/legacy_store_decoder.py")
    )
    with pytest.raises(StoreCorruptionError, match="unsupported required features"):
        namespace["decode_store"]((tmp_path / ".asterstore/format.json").read_bytes())


def test_existing_writer_handle_blocks_compaction(tmp_path):
    from asterstore import RepositoryBusyError

    repo = create(tmp_path)
    marker = (tmp_path / ".asterstore/format.json").read_bytes()
    with repo.resume("op0"):
        with pytest.raises(RepositoryBusyError):
            repo.governance.compact("archive", quiescent=True)
    assert (tmp_path / ".asterstore/format.json").read_bytes() == marker


def test_inherited_controls_synced_before_feature_activation(tmp_path, monkeypatch):
    repo = create(tmp_path)
    synced = set()
    sync = _compaction.sync_control_files
    write = _compaction.atomic_write

    def observe_sync(root, paths):
        synced.update(paths)
        return sync(root, paths)

    def observe_write(path, data, **kwargs):
        if path.name == "format.json":
            controls = tmp_path / ".asterstore"
            assert set((controls / "object-records").glob("*.json")) <= synced
            assert set((controls / "datasets").glob("*/current.json")) <= synced
        return write(path, data, **kwargs)

    monkeypatch.setattr(_compaction, "sync_control_files", observe_sync)
    monkeypatch.setattr(_compaction, "atomic_write", observe_write)
    repo.governance.compact("archive", quiescent=True)
    assert not any("managed-data" in path.parts for path in synced)


def test_archive_block_cache_is_local_to_one_inventory(tmp_path, monkeypatch):
    from asterstore.storage import _archive

    repo = create(tmp_path)
    repo.governance.compact("archive", quiescent=True)
    references = []
    for path in (tmp_path / ".asterstore").rglob("*.json"):
        value = _archive.reference(path.read_bytes())
        if value is not None:
            references.append((value["archive"], value["offset"], value["length"], value["sha256"]))
    unique = len(set(references))
    assert len(references) > unique
    calls = []
    original = _archive.resolve

    def resolve(path, value):
        calls.append(path)
        return original(path, value)

    monkeypatch.setattr(_archive, "resolve", resolve)
    repo.governance.inspect()
    assert len(calls) == unique
    repo.governance.inspect()
    assert len(calls) == 2 * unique


@pytest.mark.parametrize("damage", [None, "unknown", "symlink", "bad_checkpoint", "bad_pack"])
def test_governance_check_after_compaction(tmp_path, damage):
    repo = create(tmp_path)
    assert repo.governance.check().ok
    repo.governance.compact("archive", quiescent=True)
    directory = tmp_path / ".asterstore/archives"
    if damage == "unknown":
        (directory / "unknown.pack").write_bytes(b"unknown")
    elif damage == "symlink":
        pack = next(directory.glob("*.pack"))
        pack.unlink()
        pack.symlink_to(tmp_path / "outside")
    elif damage == "bad_checkpoint":
        next(directory.glob("*.json")).write_bytes(b"{}")
    elif damage == "bad_pack":
        next(directory.glob("*.pack")).write_bytes(b"corrupted")
    report = repo.governance.check()
    assert report.ok == (damage is None)
    assert report.metadata_complete == (damage is None)
