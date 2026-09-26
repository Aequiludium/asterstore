"""Retention scopes, exact destructive plans, conservative recovery and creation rights."""

import io
import json
from pathlib import Path

import pytest

from asterstore import (
    CandidateAbandonedError,
    CandidateStateError,
    Capabilities,
    Declaration,
    FileSet,
    HistoryAccess,
    HistoryUnavailableError,
    InvalidDeclarationError,
    Locator,
    Member,
    Object,
    PublicationNotFoundError,
    PublicationRetiredError,
    ReferenceConflictError,
    Repository,
    RetentionScope,
    StoreCorruptionError,
    UnsupportedCapabilityError,
)
from asterstore.governance import _collection
from asterstore.metadata.protocol import MAX_GENERATION
from asterstore.storage.registry import (
    managed_directory,
    object_record_path,
)


def create(root, *, managed=True):
    repo = Repository(root)
    repo.initialize(
        store_id="store",
        resource_ids=["owned", "external"] if managed else ["external"],
        managed_resource_id="owned" if managed else None,
        lifecycle=True,
    )
    return repo


def prepare(repo, pid, gen, *, history=HistoryAccess.VERSIONED):
    return repo.prepare(
        "data",
        publication_id=pid,
        operation_id=pid,
        expected_generation=gen,
        history=history,
        durable=False,
    )


def publish(repo, pid, gen, *, history=HistoryAccess.VERSIONED):
    with prepare(repo, pid, gen, history=history) as writer:
        writer.write_bytes("member", pid.encode(), relative_path="part.bin")
        return writer.commit()


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_metadata_retention_does_not_protect_bytes_and_retirement_is_irreversible(tmp_path):
    repo = create(tmp_path)
    first = publish(repo, "p1", 0)
    original = repo.open("data").files()[0]
    ref = repo.governance.retain("audit", "data", "p1", scope=RetentionScope.METADATA)
    publish(repo, "p2", 1)
    before = snapshot(tmp_path)
    preview = repo.governance.preview()
    assert ("data", "p1") in preview.retained_metadata
    assert preview.retiring == (first,)
    assert snapshot(tmp_path) == before
    outcome = repo.governance.collect("gc")
    assert len(outcome.deleted_objects) == 1 and not original.exists()
    assert repo.describe("data", publication_id="p1") == first
    assert repo.governance.get("audit") == ref
    with pytest.raises(UnsupportedCapabilityError):
        repo.governance.open("audit", expected_revision=1)
    with pytest.raises(PublicationRetiredError):
        repo.open("data", publication_id="p1")
    with pytest.raises(PublicationRetiredError):
        repo.governance.retain("late", "data", "p1", scope=RetentionScope.OBJECTS)
    with repo.resume("p1") as writer, pytest.raises(PublicationRetiredError):
        writer.commit()
    assert repo.candidate_status("p1").state == "retired"
    assert not repo.governance.preview().reclaimable
    assert repo.governance.resume_collection("gc") == outcome


def test_fixed_independent_retentions_and_current_only_access(tmp_path):
    repo = create(tmp_path)
    publish(repo, "p1", 0, history=HistoryAccess.CURRENT_ONLY)
    first = repo.open("data").files()[0]
    for name in ("alice", "bob"):
        ref = repo.governance.retain(name, "data", "p1", scope=RetentionScope.OBJECTS)
        assert repo.governance.retain(name, "data", "p1", scope=RetentionScope.OBJECTS) == ref
    publish(repo, "p2", 1, history=HistoryAccess.CURRENT_ONLY)
    with pytest.raises(HistoryUnavailableError):
        repo.open("data", publication_id="p1")
    assert repo.governance.open("alice", expected_revision=1).files() == (first,)
    with pytest.raises(InvalidDeclarationError):
        repo.governance.open("alice", expected_revision=True)
    with pytest.raises(ReferenceConflictError):
        repo.governance.open("alice", expected_revision=2)
    with pytest.raises(ReferenceConflictError):
        repo.governance.retain(
            "alice", "data", "p2", scope=RetentionScope.OBJECTS, expected_revision=1
        )
    released = repo.governance.release("alice", expected_revision=1)
    assert repo.governance.release("alice", expected_revision=1) == released
    assert repo.governance.collect("still-held").deleted_objects == ()
    assert first.exists()
    repo.governance.release("bob", expected_revision=1)
    assert repo.governance.collect("released").deleted_objects
    assert not first.exists()


def test_registered_never_gets_object_retention_or_data_deletion(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    file = outside / "data.bin"
    file.write_bytes(b"external writer owns this")
    repo = create(tmp_path / "control", managed=False)
    for i in range(2):
        declaration = Declaration(
            "registered",
            f"p{i}",
            FileSet(
                [Object("external:object", Locator("external", "data.bin"))],
                [Member("k", "external:object")],
            ),
            Capabilities.registered(),
        )
        repo.register(declaration, operation_id=f"r{i}", expected_generation=i)
    repo.governance.retain("audit", "registered", "p0", scope=RetentionScope.METADATA)
    with pytest.raises(UnsupportedCapabilityError):
        repo.governance.retain("cannot", "registered", "p0", scope=RetentionScope.OBJECTS)
    assert not repo.governance.preview().reclaimable
    assert not repo.governance.collect("gc").deleted_objects
    assert file.read_bytes() == b"external writer owns this"


@pytest.mark.parametrize("sealed", [False, True])
def test_pending_reuse_protection_survives_context_exit_until_explicit_abandon(tmp_path, sealed):
    repo = create(tmp_path)
    first = publish(repo, "p1", 0)
    old = repo.open("data").files()[0]
    with prepare(repo, "pending", 1) as writer:
        writer.reuse("p1")
        if sealed:
            writer.seal()
    publish(repo, "p2", 1)
    assert repo.governance.collect("while-pending").deleted_objects == ()
    assert old.exists()
    assert repo.candidate_status("p1").state == "retired"
    # The publication may retire while the object stays protected by pending reuse.
    repo.governance.abandon("pending")
    repo.governance.abandon("pending")
    assert repo.candidate_status("pending").state == "abandoned"
    with pytest.raises(CandidateAbandonedError), repo.resume("pending"):
        pass
    assert repo.governance.collect("after-abandon").deleted_objects == (
        first.declaration.files.objects[0].object_id,
    )
    assert not old.exists()


def test_shared_objects_survive_origin_retirement(tmp_path):
    repo = create(tmp_path)
    first = publish(repo, "p1", 0)
    old = repo.open("data").files()[0]
    with prepare(repo, "p2", 1) as writer:
        writer.reuse("p1")
        writer.write_bytes("new", b"new", relative_path="new.bin")
        writer.commit()
    result = repo.governance.collect("gc1")
    assert result.retired_publications == (("data", "p1"),)
    assert not result.deleted_objects and old.exists()
    assert repo.open("data").files()[0] == old
    with prepare(repo, "p3", 2) as writer:
        with pytest.raises(PublicationRetiredError):
            writer.reuse("p1")
        writer.reuse("p2", keys=["member"])
        writer.commit()
    result = repo.governance.collect("gc2")
    assert len(result.deleted_objects) == 1 and old.exists()
    assert object_record_path(tmp_path, first.declaration.files.objects[0].object_id).exists()


@pytest.mark.parametrize("point", ["plan", "retire", "unlink", "progress", "complete"])
@pytest.mark.parametrize("after", [False, True])
def test_fixed_collection_recovers_at_each_destructive_boundary(
    tmp_path, monkeypatch, point, after
):
    repo = create(tmp_path)
    publish(repo, "p1", 0)
    path = repo.open("data").files()[0]
    publish(repo, "p2", 1)
    name = (
        "delete_owned_file"
        if point == "unlink"
        else "immutable_write"
        if point in ("plan", "retire")
        else "atomic_write"
    )
    original = getattr(_collection, name)
    fired = False

    def fault(*args, **kwargs):
        nonlocal fired
        matches = (
            point == "unlink"
            or (point == "plan" and args[0].name == "plan.json")
            or (point == "retire" and "retired" in args[0].parts)
            or (
                point in ("progress", "complete")
                and args[0].name == "progress.json"
                and json.loads(args[1])["complete"] == (point == "complete")
            )
        )
        if matches and not fired:
            fired = True
            if after:
                original(*args, **kwargs)
            raise OSError("interrupted")
        return original(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(_collection, name, fault)
        with pytest.raises(OSError, match="interrupted"):
            repo.governance.collect("gc")
    assert fired
    if point == "plan" and not after:
        with pytest.raises(PublicationNotFoundError):
            repo.governance.resume_collection("gc")
        outcome = repo.governance.collect("gc")
    else:
        outcome = repo.governance.resume_collection("gc")
    assert outcome.complete and not path.exists()
    assert len(outcome.deleted_objects) + len(outcome.missing_objects) == 1
    assert repo.open("data").files()[0].read_bytes() == b"p2"
    assert repo.governance.resume_collection("gc") == outcome


def pause_after_plan(repo, monkeypatch):
    original = _collection.immutable_write

    def stop(path, data, *, durable=True):
        original(path, data, durable=durable)
        if path.name == "plan.json":
            raise OSError("plan only")

    with monkeypatch.context() as patch:
        patch.setattr(_collection, "immutable_write", stop)
        with pytest.raises(OSError):
            repo.governance.collect("gc")


def test_recovery_shrinks_for_new_pin_and_never_expands_to_new_history(tmp_path, monkeypatch):
    repo = create(tmp_path)
    first = publish(repo, "p1", 0)
    first_path = repo.open("data").files()[0]
    second = publish(repo, "p2", 1)
    second_path = repo.open("data").files()[0]
    pause_after_plan(repo, monkeypatch)
    repo.governance.retain("new reader", "data", "p1", scope=RetentionScope.OBJECTS)
    publish(repo, "p3", 2)
    result = repo.governance.resume_collection("gc")
    assert result.protected_objects == (first.declaration.files.objects[0].object_id,)
    assert not result.retired_publications and not result.deleted_objects
    assert first_path.exists() and second_path.exists()
    repo.governance.release("new reader", expected_revision=1)
    assert repo.governance.resume_collection("gc") == result
    assert second in repo.governance.preview().retiring


def test_recovery_respects_new_current_reusing_planned_objects(tmp_path, monkeypatch):
    repo = create(tmp_path)
    publish(repo, "p1", 0)
    first_path = repo.open("data").files()[0]
    publish(repo, "p2", 1)
    pause_after_plan(repo, monkeypatch)
    with prepare(repo, "p3", 2) as writer:
        writer.reuse("p1")
        writer.commit()
    result = repo.governance.resume_collection("gc")
    assert result.protected_objects and not result.deleted_objects
    assert first_path.exists()
    assert repo.open("data").files() == (first_path,)


@pytest.mark.parametrize("damage", ["object", "operation", "reference", "seal", "unknown"])
def test_damaged_control_authority_blocks_all_deletion(tmp_path, damage):
    repo = create(tmp_path)
    first = publish(repo, "p1", 0)
    first_path = repo.open("data").files()[0]
    publish(repo, "p2", 1)
    if damage == "object":
        object_record_path(tmp_path, first.declaration.files.objects[0].object_id).unlink()
    elif damage == "operation":
        from asterstore.storage.registry import operation_path

        operation_path(tmp_path, "p1").write_bytes(b"{}")
    elif damage == "reference":
        from asterstore.storage.registry import retention_path

        repo.governance.retain("audit", "data", "p1", scope=RetentionScope.METADATA)
        retention_path(tmp_path, "audit").write_bytes(b"{}")
    elif damage == "seal":
        (managed_directory(tmp_path, "p1") / "sealed.json").write_bytes(b"{}")
    else:
        (tmp_path / ".asterstore/object-records/unknown.json").write_bytes(b"{}")
    before = snapshot(tmp_path)
    with pytest.raises(StoreCorruptionError):
        repo.governance.collect("gc")
    assert snapshot(tmp_path) == before and first_path.exists()


@pytest.mark.parametrize("symlink_parent", [False, True])
def test_collection_never_follows_data_symlinks(tmp_path, symlink_parent):
    repo = create(tmp_path / "control")
    publish(repo, "p1", 0)
    old = repo.open("data").files()[0]
    publish(repo, "p2", 1)
    outside = tmp_path / "outside"
    outside.mkdir()
    protected = outside / "part.bin"
    protected.write_bytes(b"foreign")
    old.unlink()
    if symlink_parent:
        old.parent.rmdir()
        old.parent.symlink_to(outside, target_is_directory=True)
    else:
        old.symlink_to(protected)
    with pytest.raises(StoreCorruptionError):
        repo.governance.collect("gc")
    assert protected.read_bytes() == b"foreign"


def test_feature_gate_and_read_cost_remain_explicit(tmp_path, monkeypatch):
    plain = Repository(tmp_path / "plain")
    plain.initialize(store_id="plain", resource_ids=["owned"], managed_resource_id="owned")
    before = snapshot(plain.root)
    with pytest.raises(UnsupportedCapabilityError):
        plain.governance.collect("gc")
    assert snapshot(plain.root) == before
    repo = create(tmp_path / "governed")
    publish(repo, "p1", 0)
    original = io.open
    opened = []

    def track(path, *args, **kwargs):
        opened.append(Path(path).name)
        return original(path, *args, **kwargs)

    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected path probe")

    with monkeypatch.context() as patch:
        patch.setattr(io, "open", track)
        patch.setattr(Path, "stat", forbidden)
        patch.setattr(Path, "iterdir", forbidden)
        binding = repo.open("data")
        assert opened == ["format.json", "current.json"]
        opened.clear()
        assert binding.files() and not opened


@pytest.mark.parametrize("revision", [True, -1, MAX_GENERATION, 1.5])
def test_invalid_reference_revision_does_not_mutate(tmp_path, revision):
    repo = create(tmp_path)
    publish(repo, "p1", 0)
    before = snapshot(tmp_path)
    with pytest.raises(InvalidDeclarationError):
        repo.governance.retain(
            "reader", "data", "p1", scope=RetentionScope.OBJECTS, expected_revision=revision
        )
    assert snapshot(tmp_path) == before


def test_abandon_never_deletes_private_output_or_committed_data(tmp_path):
    repo = create(tmp_path)
    publish(repo, "p1", 0)
    with pytest.raises(CandidateStateError):
        repo.governance.abandon("p1")
    with prepare(repo, "failed", 1) as writer:
        private = writer.write_bytes("private", b"partial", relative_path="partial.bin")
    repo.governance.abandon("failed")
    assert private.read_bytes() == b"partial"
    assert not repo.governance.collect("gc").deleted_objects


def test_missing_candidate_protection_is_not_treated_as_empty(tmp_path):
    repo = create(tmp_path)
    publish(repo, "p1", 0)
    old = repo.open("data").files()[0]
    with prepare(repo, "pending", 1) as writer:
        writer.reuse("p1")
    publish(repo, "p2", 1)
    (managed_directory(tmp_path, "pending") / "protection.json").unlink()
    with pytest.raises(StoreCorruptionError):
        repo.governance.collect("gc")
    assert old.exists()
    repo.governance.abandon("pending")
    assert repo.governance.collect("gc").deleted_objects


def test_unknown_data_files_are_never_inferred_to_be_owned(tmp_path):
    repo = create(tmp_path)
    publish(repo, "p1", 0)
    old = repo.open("data").files()[0]
    unknown = old.with_name("unknown.bin")
    unknown.write_bytes(b"not in any publication")
    publish(repo, "p2", 1)
    assert repo.governance.collect("gc").deleted_objects
    assert unknown.read_bytes() == b"not in any publication"


@pytest.mark.parametrize(
    "name", ["repository", "retention", "retired", "plan", "progress", "abandonment"]
)
def test_governance_wire_goldens_and_strict_damage_rejection(name):
    import jsonschema

    from asterstore.metadata import protocol as codec

    pairs = {
        "repository": (codec.encode_store, codec.decode_store),
        "retention": (codec.encode_retention, codec.decode_retention),
        "retired": (codec.encode_retired, codec.decode_retired),
        "plan": (codec.encode_governance_plan, codec.decode_governance_plan),
        "progress": (codec.encode_governance_progress, codec.decode_governance_progress),
        "abandonment": (codec.encode_managed_abandonment, codec.decode_managed_abandonment),
    }
    encode, decode = pairs[name]
    data = (Path(__file__).parent / f"fixtures/protocol/governance/{name}.json").read_bytes()
    assert encode(decode(data)) == data
    schema = json.loads(
        (Path(__file__).parents[1] / "src/asterstore/metadata/schemas/store.json").read_text()
    )
    jsonschema.Draft202012Validator(schema).validate(json.loads(data))
    for field, value in [("format_version", 3), ("unknown", True), ("kind", "future")]:
        raw = json.loads(data)
        raw[field] = value
        with pytest.raises(StoreCorruptionError):
            decode(json.dumps(raw).encode())
    if name == "retention":
        for value in [True, 0, 2**63, 1.5]:
            raw = json.loads(data)
            raw["revision"] = value
            with pytest.raises(StoreCorruptionError):
                decode(json.dumps(raw).encode())


def test_missing_retirement_evidence_blocks_further_collection(tmp_path):
    from asterstore.storage.registry import retired_path

    repo = create(tmp_path)
    publish(repo, "p1", 0)
    publish(repo, "p2", 1)
    repo.governance.collect("gc1")
    retired_path(tmp_path, "data", "p1").unlink()
    second = repo.open("data").files()[0]
    publish(repo, "p3", 2)
    with pytest.raises(StoreCorruptionError):
        repo.governance.collect("gc2")
    assert second.read_bytes() == b"p2"


def test_progress_checkpoints_avoid_quadratic_cumulative_serialization(tmp_path, monkeypatch):
    repo = create(tmp_path)
    with prepare(repo, "p1", 0) as writer:
        for index in range(16):
            writer.write_bytes(str(index), b"old", relative_path=f"{index}.bin")
        writer.commit()
    publish(repo, "p2", 1)
    original = _collection.atomic_write
    sizes = []

    def checkpoint(path, data, *, durable=True):
        if path.name == "progress.json":
            sizes.append(len(json.loads(data)["outcomes"]))
        original(path, data, durable=durable)

    monkeypatch.setattr(_collection, "atomic_write", checkpoint)
    result = repo.governance.collect("gc")
    assert len(result.deleted_objects) == 16
    assert sizes == [1, 2, 4, 8, 16, 16]


def test_recovery_between_checkpoints_preserves_recorded_work(tmp_path, monkeypatch):
    repo = create(tmp_path)
    with prepare(repo, "p1", 0) as writer:
        for index in range(5):
            writer.write_bytes(str(index), b"old", relative_path=f"{index}.bin")
        writer.commit()
    publish(repo, "p2", 1)
    original = _collection.delete_owned_file
    count = 0

    def interrupted(root, relative_path):
        nonlocal count
        deleted = original(root, relative_path)
        count += 1
        if count == 3:
            raise OSError("after third unlink")
        return deleted

    with monkeypatch.context() as patch:
        patch.setattr(_collection, "delete_owned_file", interrupted)
        with pytest.raises(OSError):
            repo.governance.collect("gc")
    result = repo.governance.resume_collection("gc")
    assert len(result.deleted_objects) == 4
    assert len(result.missing_objects) == 1
    assert repo.open("data").files()[0].read_bytes() == b"p2"
