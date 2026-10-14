"""D1 measurement must preserve controls and avoid payload access."""

import runpy
from pathlib import Path

import pytest

from asterstore import HistoryAccess, Repository, StoreCorruptionError

ANALYZE = runpy.run_path(str(Path(__file__).parents[1] / "benchmarks/control_history.py"))[
    "analyze"
]


def create(root):
    repo = Repository(root)
    repo.initialize(
        store_id="d1", resource_ids=["owned"], managed_resource_id="owned", lifecycle=True
    )
    return repo


def publish(repo, number):
    with repo.prepare(
        "data",
        publication_id=f"p{number}",
        operation_id=f"op{number}",
        expected_generation=number,
        history=HistoryAccess.VERSIONED,
        durable=False,
    ) as writer:
        writer.write_bytes("key", b"payload", relative_path="file.bin")
        writer.commit()


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_exact_duplicates_and_no_mutation_or_payload_read(tmp_path, monkeypatch):
    repo = create(tmp_path)
    publish(repo, 0)
    publish(repo, 1)
    before = snapshot(tmp_path)
    original = Path.read_bytes

    def read(path):
        assert "managed-data" not in path.parts
        return original(path)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_bytes", read)
        result = ANALYZE(tmp_path, quiescent=True)
    assert snapshot(tmp_path) == before
    assert result["duplicate_bytes_upper_bound"] > 0
    for group in result["exact_duplicate_groups"]:
        values = [before[f".asterstore/{p}"] for p in group["paths"]]
        assert len(set(values)) == 1
        assert group["duplicate_bytes"] == sum(map(len, values[1:]))
        assert not any(p.endswith("current.json") for p in group["paths"])
    assert result["authority_bytes"] == sum(r["bytes"] for r in result["records"])
    assert result["net_archive_savings_bytes"] is None


def test_pending_candidate_pins_all_evidence(tmp_path):
    repo = create(tmp_path)
    publish(repo, 0)
    with repo.prepare(
        "data",
        publication_id="pending",
        operation_id="pending",
        expected_generation=1,
        history=HistoryAccess.VERSIONED,
        durable=False,
    ) as writer:
        writer.reuse("p0")
        writer.seal()
    result = ANALYZE(tmp_path, quiescent=True)
    assert result["unfinished_operations"] == [
        {"kind": "candidate_or_cleanup", "operation_id": "pending"}
    ]
    assert len(result["pinned_records"]) == result["authority_files"]
    assert result["duplicate_bytes_upper_bound"] == 0


def test_empty_store_does_not_create_lock(tmp_path):
    create(tmp_path)
    before = snapshot(tmp_path)
    result = ANALYZE(tmp_path, quiescent=True)
    assert result["authority_files"] == 1
    assert snapshot(tmp_path) == before
    assert not (tmp_path / ".asterstore/gc.lock").exists()


def test_requires_quiescence(tmp_path):
    with pytest.raises(ValueError, match="stop all writers"):
        ANALYZE(tmp_path, quiescent=False)


def test_corruption_is_not_a_savings_opportunity(tmp_path):
    repo = create(tmp_path)
    publish(repo, 0)
    path = next((tmp_path / ".asterstore/object-records").iterdir())
    path.unlink()
    with pytest.raises(StoreCorruptionError):
        ANALYZE(tmp_path, quiescent=True)


def test_incomplete_collection_pins_history(tmp_path):
    import json

    repo = create(tmp_path)
    publish(repo, 0)
    publish(repo, 1)
    repo.governance.collect("gc")
    completed = ANALYZE(tmp_path, quiescent=True)
    assert not completed["unfinished_operations"]
    progress = next((tmp_path / ".asterstore/collections").glob("*/progress.json"))
    data = json.loads(progress.read_text())
    data["complete"] = False
    progress.write_text(json.dumps(data))
    result = ANALYZE(tmp_path, quiescent=True)
    assert result["unfinished_operations"] == [{"kind": "collection", "operation_id": "gc"}]
    assert result["duplicate_record_count"] == 0


def test_busy_lock_and_symlink_rejected(tmp_path):
    import fcntl

    from asterstore import RepositoryBusyError

    repo = create(tmp_path)
    publish(repo, 0)
    with (tmp_path / ".asterstore/gc.lock").open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RepositoryBusyError):
            ANALYZE(tmp_path, quiescent=True)
    path = next((tmp_path / ".asterstore/object-records").iterdir())
    target = tmp_path / "evidence.json"
    path.rename(target)
    path.symlink_to(target)
    with pytest.raises(StoreCorruptionError):
        ANALYZE(tmp_path, quiescent=True)


def test_pending_registration_pins_inventory(tmp_path, monkeypatch):
    from asterstore import Capabilities, Declaration, FileSet
    from asterstore.publishing.transactions import _commit

    repo = create(tmp_path)

    def fail(*args, **kwargs):
        raise OSError("before current")

    with monkeypatch.context() as patch:
        patch.setattr(_commit, "atomic_write", fail)
        with pytest.raises(OSError, match="before current"):
            repo.register(
                Declaration("external", "p", FileSet([], []), Capabilities.registered()),
                operation_id="registered",
                expected_generation=0,
                durable=False,
            )
    result = ANALYZE(tmp_path, quiescent=True)
    assert result["unfinished_operations"] == [
        {"kind": "registration", "operation_id": "registered"}
    ]
    assert result["duplicate_record_count"] == 0


def test_abandoned_cleanup_must_complete_before_unpinning(tmp_path):
    repo = create(tmp_path)
    with repo.prepare(
        "data",
        publication_id="pending",
        operation_id="pending",
        expected_generation=0,
        history=HistoryAccess.VERSIONED,
        durable=False,
    ) as writer:
        writer.write_bytes("key", b"partial", relative_path="file.bin")
        writer.seal()
    repo.governance.abandon("pending")
    assert ANALYZE(tmp_path, quiescent=True)["unfinished_operations"]
    repo.governance.cleanup("pending")
    assert not ANALYZE(tmp_path, quiescent=True)["unfinished_operations"]


def test_released_reference_and_retired_evidence_remain_in_report(tmp_path):
    from asterstore import RetentionScope

    repo = create(tmp_path)
    publish(repo, 0)
    repo.governance.retain("audit", "data", "p0", scope=RetentionScope.METADATA)
    repo.governance.release("audit", expected_revision=1)
    publish(repo, 1)
    repo.governance.collect("gc")
    result = ANALYZE(tmp_path, quiescent=True)
    assert any(p["path"].startswith("fixed-retentions/") for p in result["pinned_records"])
    assert any(p["path"].startswith("retired/") for p in result["records"])
    assert not result["unfinished_operations"]
