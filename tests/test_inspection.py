"""Public diagnostic coverage, provenance, non-mutation, and explicit I/O levels."""

import hashlib
from pathlib import Path

import pytest

from asterstore import (
    Capabilities,
    Declaration,
    FileSet,
    HistoryAccess,
    Locator,
    Member,
    Object,
    Repository,
    RetentionScope,
)
from asterstore.storage import file_lock
from asterstore.storage.registry import object_record_path


def repository(root):
    repo = Repository(root)
    repo.initialize(resource_ids=["owned", "external"], managed_resource_id="owned", lifecycle=True)
    return repo


def publish(repo, pid, generation):
    with repo.prepare(
        "data",
        publication_id=pid,
        operation_id=pid,
        expected_generation=generation,
        history=HistoryAccess.VERSIONED,
    ) as writer:
        writer.write_bytes("key", pid.encode(), relative_path="part.bin")
        writer.commit()
    return repo.describe("data").declaration.files.objects[0].object_id


def files(root):
    return {
        str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in root.rglob("*")
        if p.is_file()
    }


def test_snapshot_explains_all_protection_roots_and_retirement(tmp_path):
    repo = repository(tmp_path)
    first = publish(repo, "p1", 0)
    second = publish(repo, "p2", 1)
    ref = repo.governance.retain("reader", "data", "p1", scope=RetentionScope.OBJECTS)
    repo.governance.retain("record", "data", "p1", scope=RetentionScope.METADATA)
    with repo.prepare(
        "data",
        publication_id="p3",
        operation_id="pending",
        expected_generation=2,
        history=HistoryAccess.VERSIONED,
    ) as writer:
        writer.reuse("p1")
        snapshot = repo.governance.inspect()
        reasons = snapshot.explain_object(first).reasons
        assert [(r.kind, r.reference_name, r.operation_id) for r in reasons] == [
            ("retention", "reader", None),
            ("candidate", None, "pending"),
        ]
        assert snapshot.explain_object(second).reasons[0].kind == "current"
        assert {c.operation_id: c for c in snapshot.candidates}["pending"] == repo.candidate_status(
            "pending"
        )
    repo.governance.release("reader", expected_revision=ref.revision)
    assert not repo.governance.inspect().explain_object(first).reclaimable
    repo.governance.abandon("pending")
    assert repo.governance.inspect().explain_object(first).reclaimable
    repo.governance.collect("gc")
    snapshot = repo.governance.inspect()
    assert snapshot.coordinated
    assert snapshot.explain_object(first).collected
    assert not snapshot.explain_object(first).reclaimable
    old = next(p for p in snapshot.publications if p.record.declaration.publication_id == "p1")
    assert old.state == "retired" and old.metadata_retained and not old.objects_protected
    assert snapshot.maintenance[0].complete
    assert snapshot.maintenance[0].planned_objects == snapshot.maintenance[0].processed_objects == 1
    assert repo.governance.check(level="existence").ok  # Retired bytes may be absent.


def test_installed_abandoned_candidate_requires_separate_cleanup(tmp_path):
    repo = repository(tmp_path)
    with repo.prepare(
        "data", publication_id="p1", operation_id="failed", expected_generation=0
    ) as writer:
        writer.write_bytes("key", b"bytes", relative_path="part.bin")
        writer.seal()
    repo.governance.abandon("failed")
    snapshot = repo.governance.inspect()
    assert snapshot.candidates[0].state == "abandoned"
    # Sealing may leave files private; committed object evidence starts at commit.
    repo.governance.cleanup("failed")
    snapshot = repo.governance.inspect()
    assert snapshot.maintenance[0].kind == "cleanup"
    assert snapshot.maintenance[0].complete


def test_no_mutations_and_no_payload_io_at_metadata_level(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    first = publish(repo, "p1", 0)
    before = files(tmp_path)
    payload = repo.open("data").files()[0]
    read, lstat = Path.read_bytes, Path.lstat

    def guarded_read(path):
        assert path != payload
        return read(path)

    def guarded_stat(path):
        assert path != payload
        return lstat(path)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_bytes", guarded_read)
        patch.setattr(Path, "lstat", guarded_stat)
        snapshot = repo.governance.inspect()
        assert repo.governance.check().ok
        assert snapshot.explain_object(first).record.object_id == first
    assert files(tmp_path) == before


def test_empty_and_missing_repository_never_create_lock(tmp_path):
    root = tmp_path / "missing"
    repo = Repository(root)
    assert not repo.governance.check().ok
    assert not root.exists()
    repo = repository(root)
    before = files(root)
    snapshot = repo.governance.inspect()
    assert not snapshot.coordinated and snapshot.publications == ()
    assert repo.governance.check().ok
    assert files(root) == before
    assert not (root / ".asterstore/gc.lock").exists()


def test_busy_diagnostics_fail_promptly_without_reentrant_deadlock(tmp_path):
    repo = repository(tmp_path)
    publish(repo, "p1", 0)
    with file_lock(tmp_path / ".asterstore/gc.lock", exclusive=False):
        with pytest.raises(BlockingIOError):
            repo.governance.inspect()
        report = repo.governance.check()
        assert not report.ok and not report.metadata_complete
        assert [i.code for i in report.issues] == ["busy"]


def test_collect_multiple_malformed_control_records(tmp_path):
    repo = repository(tmp_path)
    a = publish(repo, "p1", 0)
    b = publish(repo, "p2", 1)
    for oid in (a, b):
        object_record_path(tmp_path, oid).write_bytes(b"{")
    report = repo.governance.check(level="existence")
    assert not report.ok and not report.metadata_complete
    assert report.target_objects == report.checked_objects == ()
    assert len(report.issues) == 2
    assert {i.path for i in report.issues} == {
        object_record_path(tmp_path, a),
        object_record_path(tmp_path, b),
    }


def test_all_live_history_checked_and_failures_aggregated(tmp_path):
    repo = repository(tmp_path)
    a = publish(repo, "p1", 0)
    old = repo.open("data").files()[0]
    b = publish(repo, "p2", 1)
    current = repo.open("data").files()[0]
    old.unlink()
    current.unlink()
    report = repo.governance.check(level="existence")
    assert report.metadata_complete and not report.ok
    assert set(report.target_objects) == {a, b}
    assert len(report.issues) == 2
    assert report.checked_objects == ()


def test_checksum_requires_baseline_and_detects_same_size_tampering(tmp_path):
    repo = repository(tmp_path)
    oid = publish(repo, "p1", 0)
    path = repo.open("data").files()[0]
    baseline = {oid: hashlib.sha256(b"p1").hexdigest()}
    assert repo.governance.check(level="checksum", checksums=baseline).ok
    report = repo.governance.check(level="checksum", checksums={})
    assert report.issues[0].code == "checksum_unavailable"
    path.write_bytes(b"p2")
    assert repo.governance.check(level="existence").ok
    report = repo.governance.check(level="checksum", checksums=baseline)
    assert report.issues[0].code == "checksum_mismatch" and not report.ok
    for kwargs in (
        {"level": "checksum"},
        {"level": "format"},
        {"level": "bogus"},
        {"level": "checksum", "checksums": {oid: "invalid"}},
    ):
        with pytest.raises(ValueError):
            repo.governance.check(**kwargs)


def test_format_callback_is_explicit_and_cannot_mutate_store_reentrantly(tmp_path):
    repo = repository(tmp_path)
    oid = publish(repo, "p1", 0)
    seen = []

    def validate(path):
        seen.append(path)
        assert repo.governance.check().issues[0].code == "busy"
        if path.read_bytes() != b"valid":
            raise ValueError("invalid example format")

    assert repo.governance.check().ok and not seen
    report = repo.governance.check(level="format", validator=validate)
    assert len(seen) == 1 and report.issues[0].code == "format_error"
    assert report.issues[0].object_id == oid


def test_registered_data_needs_binding_but_gains_no_deletion_rights(tmp_path):
    repo = repository(tmp_path / "store")
    declaration = Declaration(
        "external",
        "p1",
        FileSet(
            [Object("external:object", Locator("external", "part.bin"))],
            [Member("key", "external:object")],
        ),
        Capabilities.registered(),
    )
    repo.register(declaration, operation_id="register", expected_generation=0)
    external = tmp_path / "external"
    external.mkdir()
    (external / "part.bin").write_bytes(b"bytes")
    assert repo.governance.check().ok
    assert not repo.governance.check(level="existence").ok
    assert repo.governance.check(level="existence", resources={"external": external}).ok
    obj = repo.governance.inspect().explain_object("external:object")
    assert not obj.reclaimable and not obj.reasons


def test_object_symlink_is_reported_without_following_it(tmp_path):
    repo = repository(tmp_path / "store")
    publish(repo, "p1", 0)
    path = repo.open("data").files()[0]
    path.unlink()
    path.symlink_to(tmp_path / "outside")
    report = repo.governance.check(level="existence")
    assert not report.ok and report.issues[0].code == "object_unavailable"


def test_interrupted_installed_candidate_reports_cleanup_protection(tmp_path, monkeypatch):
    from tests.test_cleanup import installed_failure

    repo = repository(tmp_path)
    seal, _ = installed_failure(repo, monkeypatch)
    oid = seal.declaration.files.objects[0].object_id
    obj = repo.governance.inspect().explain_object(oid)
    assert not obj.reclaimable and not obj.collected and not obj.cleaned
    assert [r.kind for r in obj.reasons] == ["candidate_cleanup"]
    assert obj.reasons[0].operation_id == "pending"
    repo.governance.cleanup("pending")
    obj = repo.governance.inspect().explain_object(oid)
    assert obj.cleaned and not obj.reclaimable and not obj.reasons


def test_interrupted_collection_reports_fixed_progress(tmp_path, monkeypatch):
    from asterstore.governance import _collection

    repo = repository(tmp_path)
    publish(repo, "p1", 0)
    publish(repo, "p2", 1)

    def fail(*args, **kwargs):
        raise OSError("interrupted deletion")

    with monkeypatch.context() as patch:
        patch.setattr(_collection, "delete_owned_file", fail)
        with pytest.raises(OSError):
            repo.governance.collect("interrupted")
    task = repo.governance.inspect().maintenance[0]
    assert task.operation_id == "interrupted" and not task.complete
    assert task.planned_objects == 1 and task.processed_objects == 0
    assert repo.governance.check().ok
    repo.governance.resume_collection("interrupted")
    assert repo.governance.inspect().maintenance[0].complete


def test_candidate_snapshot_states_match_existing_queries(tmp_path):
    repo = repository(tmp_path)
    publish(repo, "p1", 0)
    with repo.prepare(
        "data",
        publication_id="stale",
        operation_id="stale",
        expected_generation=1,
        history=HistoryAccess.VERSIONED,
    ) as writer:
        writer.reuse("p1")
        writer.seal()
    assert (
        next(c for c in repo.governance.inspect().candidates if c.operation_id == "stale").state
        == "prepared"
    )
    publish(repo, "p2", 1)
    snapshot = repo.governance.inspect()
    assert next(c for c in snapshot.candidates if c.operation_id == "stale").state == "conflict"
    assert all(c == repo.candidate_status(c.operation_id) for c in snapshot.candidates)


def test_snapshot_explanation_stays_in_memory_and_readonly_data_check(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    oid = publish(repo, "p1", 0)
    snapshot = repo.governance.inspect()
    before = files(tmp_path)
    assert repo.governance.check(level="existence").ok
    assert files(tmp_path) == before

    def no_io(*args, **kwargs):
        raise AssertionError("unexpected I/O")

    monkeypatch.setattr(Path, "read_bytes", no_io)
    monkeypatch.setattr(Path, "stat", no_io)
    assert snapshot.explain_object(oid).reasons[0].kind == "current"
    with pytest.raises(KeyError):
        snapshot.explain_object("unknown")


def test_parquet_validation_remains_an_explicit_optional_engine_call(tmp_path):
    pl = pytest.importorskip("polars")
    repo = repository(tmp_path)
    with repo.prepare(
        "table", publication_id="p1", operation_id="write", expected_generation=0
    ) as writer:
        path = writer.path("part", relative_path="part.parquet")
        pl.DataFrame({"value": [1, 2]}).write_parquet(path)
        writer.commit()

    def parquet_metadata(path):
        assert pl.read_parquet_schema(path) == {"value": pl.Int64}

    assert repo.governance.check(level="format", validator=parquet_metadata).ok
    repo.open("table").files()[0].write_bytes(b"not parquet")
    assert repo.governance.check().ok
    assert (
        repo.governance.check(level="format", validator=parquet_metadata).issues[0].code
        == "format_error"
    )
