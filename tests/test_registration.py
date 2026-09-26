"""Persistent registered declarations: identity, conflicts and precise recovery."""

import io
import json
import os
from dataclasses import replace
from functools import partial
from pathlib import Path

import pytest

from asterstore import (
    ByteStability,
    Capabilities,
    Declaration,
    FileSet,
    HistoryAccess,
    HistoryUnavailableError,
    InvalidDeclarationError,
    Locator,
    Member,
    Object,
    PublicationConflictError,
    PublicationNotFoundError,
    Repository,
    ResourceNotBoundError,
    StoreCorruptionError,
    UnsupportedCapabilityError,
)
from asterstore.metadata.protocol import MAX_GENERATION
from asterstore.publishing.transactions import _commit as _operations
from asterstore.storage.registry import (
    head_path,
    marker_path,
    object_record_path,
    operation_path,
    publication_path,
)


def declaration(
    pid="p1",
    *,
    dataset="simulation",
    oid="object:1",
    path="part.bin",
    history=HistoryAccess.CURRENT_ONLY,
):
    return Declaration(
        dataset,
        pid,
        FileSet([Object(oid, Locator("external", path))], [Member("phase:initial", oid)]),
        Capabilities.registered(history=history),
    )


def create(root):
    repo = Repository(root)
    repo.initialize(store_id="store:test", resource_ids=["external"], durable=False)
    return repo


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_register_reopen_resource_rebinding_and_external_bytes(tmp_path):
    root, external = tmp_path / "control", tmp_path / "external"
    external.mkdir()
    file = external / "part.bin"
    file.write_bytes(b"first")
    repo = create(root)
    record = repo.register(declaration(), operation_id="op:1", expected_generation=0)
    reopened = Repository(root)
    assert reopened.describe("simulation") == record
    resources = {"external": external}
    binding = reopened.open("simulation", resources=resources)
    assert binding.publication == record.declaration
    assert binding.files(keys=["phase:initial"]) == (file,)
    file.write_bytes(b"external update")
    assert binding.files()[0].read_bytes() == b"external update"
    resources["external"] = tmp_path / "different"
    assert binding.files() == (file,)
    assert reopened.open("simulation", resources=resources).files()[0] != file
    assert reopened.registration_status("op:1").state == "committed"
    assert list(external.iterdir()) == [file]


def test_initialize_is_idempotent_and_does_not_adopt_existing_protocols(tmp_path):
    repo = create(tmp_path / "new")
    assert repo.initialize(resource_ids=["external"]).store_id == "store:test"
    before = snapshot(repo.root)
    with pytest.raises(PublicationConflictError):
        repo.initialize(store_id="different", resource_ids=["external"])
    with pytest.raises(PublicationConflictError):
        repo.initialize(resource_ids=["different"])
    assert snapshot(repo.root) == before


def test_history_discovery_and_idempotency_do_not_reset_current(tmp_path):
    repo = create(tmp_path)
    first = repo.register(declaration(), operation_id="op1", expected_generation=0, durable=False)
    second = repo.register(
        declaration("p2"), operation_id="op2", expected_generation=1, durable=False
    )
    assert repo.resume_registration("op1") == first
    assert repo.describe("simulation") == second
    assert repo.describe("simulation", publication_id="p1") == first
    with pytest.raises(HistoryUnavailableError):
        repo.open("simulation", publication_id="p1", resources={"external": tmp_path / "data"})
    with pytest.raises(ResourceNotBoundError):
        repo.open("simulation")


def test_versioned_registered_history_is_metadata_not_byte_snapshots(tmp_path):
    repo = create(tmp_path)
    for i in range(2):
        repo.register(
            declaration(f"p{i}", history=HistoryAccess.VERSIONED),
            operation_id=f"op{i}",
            expected_generation=i,
            durable=False,
        )
    binding = repo.open(
        "simulation", publication_id="p0", resources={"external": tmp_path / "absent"}
    )
    assert binding.files() == (tmp_path / "absent/part.bin",)
    assert binding.capabilities.byte_stability is ByteStability.MUTABLE_OR_UNKNOWN


@pytest.mark.parametrize(
    "change", ["operation", "publication", "generation", "capability", "object"]
)
def test_conflicts_do_not_change_existing_authority(tmp_path, change):
    repo = create(tmp_path)
    repo.register(declaration(), operation_id="op1", expected_generation=0, durable=False)
    before = snapshot(tmp_path)
    value, op, expected = declaration("p2"), "op2", 1
    if change == "operation":
        op = "op1"
    elif change == "publication":
        value = declaration()
    elif change == "generation":
        expected = 0
    elif change == "capability":
        value = declaration("p2", history=HistoryAccess.VERSIONED)
    else:
        value = declaration("p2", path="different.bin")
    with pytest.raises(PublicationConflictError):
        repo.register(value, operation_id=op, expected_generation=expected, durable=False)
    assert snapshot(tmp_path) == before


def test_object_identity_is_store_wide_and_cannot_change_between_datasets(tmp_path):
    repo = create(tmp_path)
    repo.register(declaration(), operation_id="op1", expected_generation=0, durable=False)
    with pytest.raises(PublicationConflictError, match="rebound"):
        repo.register(
            declaration(dataset="other", path="different.bin"),
            operation_id="op2",
            expected_generation=0,
            durable=False,
        )
    repo.register(
        declaration(dataset="other"), operation_id="op3", expected_generation=0, durable=False
    )
    assert (
        repo.describe("other").declaration.files.objects
        == repo.describe("simulation").declaration.files.objects
    )


@pytest.mark.parametrize("stage", ["plan", "object", "archive", "current"])
@pytest.mark.parametrize("after", [False, True])
def test_failure_at_each_persistent_boundary_is_recoverable(tmp_path, monkeypatch, stage, after):
    repo = create(tmp_path)
    repo.register(declaration(), operation_id="first", expected_generation=0, durable=False)
    second = declaration("p2", oid="object:2", path="second.bin")
    original_immutable, original_atomic = _operations.immutable_write, _operations.atomic_write

    def interrupted(path, data, *, durable=True):
        match = (
            (stage == "plan" and path.parent.name == "registrations")
            or (stage == "object" and path.parent.name == "object-records")
            or (stage == "archive" and path.parent.name == "history")
            or (stage == "current" and path.name == "current.json")
        )
        writer = original_atomic if path.name == "current.json" else original_immutable
        if match and not after:
            raise OSError("injected interruption")
        writer(path, data, durable=durable)
        if match:
            raise OSError("injected response loss")

    with monkeypatch.context() as patch:
        patch.setattr(_operations, "immutable_write", interrupted)
        patch.setattr(_operations, "atomic_write", interrupted)
        with pytest.raises(OSError):
            repo.register(second, operation_id="second", expected_generation=1)
    current = repo.describe("simulation").declaration.publication_id
    assert current == ("p2" if stage == "current" and after else "p1")
    if stage == "plan" and not after:
        with pytest.raises(PublicationNotFoundError):
            repo.registration_status("second")
        result = repo.register(second, operation_id="second", expected_generation=1)
    else:
        status = repo.registration_status("second")
        assert status.state == ("committed" if stage == "current" and after else "planned")
        result = Repository(tmp_path).resume_registration("second")
    assert result.generation == 2
    assert repo.resume_registration("second") == result
    assert repo.describe("simulation").declaration == second


def test_stale_pending_operation_reports_conflict_without_rebasing(tmp_path, monkeypatch):
    repo = create(tmp_path)

    def fail(*args, **kwargs):
        raise OSError("before current")

    with monkeypatch.context() as patch:
        patch.setattr(_operations, "atomic_write", fail)
        with pytest.raises(OSError):
            repo.register(declaration(), operation_id="pending", expected_generation=0)
    repo.register(declaration("winner"), operation_id="winner", expected_generation=0)
    assert repo.registration_status("pending").state == "conflict"
    with pytest.raises(PublicationConflictError):
        repo.resume_registration("pending")
    assert repo.describe("simulation").declaration.publication_id == "winner"


def test_data_is_never_probed_and_ordinary_open_reads_two_controls(tmp_path, monkeypatch):
    root, external = tmp_path / "control", tmp_path / "external-not-created"
    repo = create(root)
    original_open, original_os_open = io.open, os.open
    reads = []

    def guarded(file, *args, **kwargs):
        path = Path(file)
        assert not path.is_relative_to(external)
        reads.append(path)
        return original_open(file, *args, **kwargs)

    def guarded_os(file, *args, **kwargs):
        assert not Path(file).is_relative_to(external)
        return original_os_open(file, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(io, "open", guarded)
        patch.setattr(os, "open", guarded_os)
        repo.register(declaration(), operation_id="op", expected_generation=0)
    reads.clear()

    def forbidden(*args, **kwargs):
        raise AssertionError("ordinary open must not stat, scan, resolve or write")

    with monkeypatch.context() as patch:
        patch.setattr(io, "open", guarded)
        for name in ("stat", "lstat", "listdir", "scandir", "mkdir", "readlink", "open"):
            patch.setattr(os, name, forbidden)
        binding = repo.open("simulation", resources={"external": external})
        assert binding.files(keys=["phase:initial"]) == (external / "part.bin",)
    assert reads == [marker_path(root), head_path(root, "simulation")]
    assert not external.exists()


def test_new_record_validation_precedes_any_governance_write(tmp_path):
    repo = create(tmp_path)
    before = snapshot(tmp_path)
    managed = replace(declaration(), capabilities=Capabilities.managed())
    with pytest.raises(UnsupportedCapabilityError):
        repo.register(managed, operation_id="op", expected_generation=0)
    unknown = replace(
        declaration(),
        files=FileSet([Object("x", Locator("unknown", "part.bin"))], [Member("x", "x")]),
    )
    with pytest.raises(InvalidDeclarationError):
        repo.register(unknown, operation_id="op", expected_generation=0)
    for number in (True, -1, 1.0, MAX_GENERATION, MAX_GENERATION + 1):
        with pytest.raises(InvalidDeclarationError):
            repo.register(declaration(), operation_id="op", expected_generation=number)
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize(
    "damage",
    [
        "current_store",
        "current_dataset",
        "history_generation",
        "object_path",
        "operation_id",
        "unknown_feature",
    ],
)
def test_corrupt_control_evidence_is_not_ignored(tmp_path, damage):
    repo = create(tmp_path)
    repo.register(declaration(), operation_id="op1", expected_generation=0)
    repo.register(declaration("p2"), operation_id="op2", expected_generation=1)
    path = head_path(tmp_path, "simulation")
    action = partial(repo.describe, "simulation")
    if damage == "history_generation":
        path = publication_path(tmp_path, "simulation", "p1")
        action = partial(repo.describe, "simulation", publication_id="p1")
    elif damage == "object_path":
        path = object_record_path(tmp_path, "object:1")
        action = partial(repo.resume_registration, "op1")
    elif damage == "operation_id":
        path = operation_path(tmp_path, "op1")
        action = partial(repo.registration_status, "op1")
    elif damage == "unknown_feature":
        path = marker_path(tmp_path)
    payload = json.loads(path.read_bytes())
    if damage == "current_store":
        payload["store_id"] = "different"
    elif damage == "current_dataset":
        payload["declaration"]["dataset_id"] = "different"
    elif damage == "history_generation":
        payload.update(expected_generation=2, generation=3)
    elif damage == "object_path":
        payload["locator"]["relative_path"] = "changed.bin"
    elif damage == "operation_id":
        payload["operation_id"] = "different"
    else:
        payload["required_features"].append("future-governance")
    path.write_text(json.dumps(payload))
    with pytest.raises(StoreCorruptionError):
        action()


def test_strong_registration_syncs_weak_store_before_visibility(tmp_path, monkeypatch):
    repo = create(tmp_path)
    calls = []
    original = _operations.sync_control_files

    def synced(root, paths):
        calls.extend(paths)
        original(root, paths)

    monkeypatch.setattr(_operations, "sync_control_files", synced)
    repo.register(declaration(), operation_id="op", expected_generation=0)
    assert marker_path(tmp_path) in calls


def test_control_sync_failure_before_plan_does_not_publish(tmp_path, monkeypatch):
    repo = create(tmp_path)

    def fail(*args, **kwargs):
        raise OSError("marker not durable")

    monkeypatch.setattr(_operations, "sync_control_files", fail)
    with pytest.raises(OSError):
        repo.register(declaration(), operation_id="op", expected_generation=0)
    assert not operation_path(tmp_path, "op").exists()
    assert not head_path(tmp_path, "simulation").exists()


def test_empty_registration_and_explicit_store_resource_validation(tmp_path):
    repo = Repository(tmp_path / "control")
    with pytest.raises(InvalidDeclarationError):
        repo.initialize(resource_ids="external")
    assert not repo.root.exists()
    repo.initialize(store_id="empty-store", resource_ids=[])
    empty = Declaration("empty", "p", FileSet([], []), Capabilities.registered())
    repo.register(empty, operation_id="op", expected_generation=0)
    assert repo.open("empty", resources={}).files() == ()


def test_missing_committed_identity_evidence_cannot_be_silently_repaired(tmp_path):
    repo = create(tmp_path)
    repo.register(declaration(), operation_id="op", expected_generation=0)
    object_record_path(tmp_path, "object:1").unlink()
    with pytest.raises(StoreCorruptionError, match="identity evidence"):
        repo.resume_registration("op")
    assert not object_record_path(tmp_path, "object:1").exists()
