"""Ownership, fixed-plan recovery and strict records for abandoned v4 work."""

import json
import os
from pathlib import Path

import pytest

from asterstore import (
    CandidateAbandonedError,
    CandidateStateError,
    HistoryAccess,
    PublicationNotFoundError,
    Repository,
    StoreCorruptionError,
    UnsupportedCapabilityError,
)
from asterstore.metadata import protocol as codec
from asterstore.publishing.transactions import _commit
from asterstore.retention.governance.cleanup import _service
from asterstore.storage.registry import managed_data_root, managed_directory, token


def create(root, *, lifecycle=True):
    repo = Repository(root)
    repo.initialize(
        store_id="store", resource_ids=["owned"], managed_resource_id="owned", lifecycle=lifecycle
    )
    return repo


def prepare(repo, pid="pending", gen=0):
    return repo.prepare_managed(
        "data",
        publication_id=pid,
        operation_id=pid,
        expected_generation=gen,
        history=HistoryAccess.VERSIONED,
        durable=False,
    )


def failed(repo, *, count=1, sealed=False):
    with prepare(repo) as writer:
        for i in range(count):
            writer.write_bytes(f"k{i}", b"partial", relative_path=f"sub/{i}.bin")
        if sealed:
            writer.seal()
    repo.governance.abandon("pending")
    return managed_directory(repo.root, "pending")


def installed_failure(repo, monkeypatch, *, after_objects=True, reuse=False):
    with prepare(repo, gen=1 if reuse else 0) as writer:
        if reuse:
            writer.reuse("p1")
        writer.write_bytes("new", b"uncommitted", relative_path="new.bin")
        seal = writer.seal()
        if after_objects:

            def fail(*args, **kwargs):
                raise OSError("before current replacement")

            with monkeypatch.context() as patch:
                patch.setattr(_commit, "atomic_write", fail)
                with pytest.raises(OSError):
                    writer.commit()
        else:
            original = writer._install

            def fail():
                original()
                raise OSError("after installing bytes")

            with monkeypatch.context() as patch:
                patch.setattr(writer, "_install", fail)
                with pytest.raises(OSError):
                    writer.commit()
    repo.governance.abandon("pending")
    return seal, managed_data_root(repo.root) / token("pending")


@pytest.mark.parametrize("sealed", [False, True])
def test_private_cleanup_preserves_authority_and_has_no_implicit_state_change(tmp_path, sealed):
    repo = create(tmp_path)
    directory = failed(repo, sealed=sealed)
    before = {p: p.read_bytes() for p in directory.rglob("*") if p.is_file()}
    preview = repo.governance.preview_cleanup("pending")
    assert preview.location == "private" and preview.files == ("sub/0.bin",)
    assert not preview.planned
    assert before == {p: p.read_bytes() for p in directory.rglob("*") if p.is_file()}
    result = repo.governance.cleanup("pending")
    assert result.complete and result.deleted_files == preview.files
    assert not result.missing_files
    for path, data in before.items():
        if path.is_relative_to(directory / "files"):
            assert not path.exists()
        else:
            assert path.read_bytes() == data
    assert (directory / "files/sub").is_dir()
    assert repo.governance.resume_cleanup("pending") == result
    assert repo.governance.cleanup("pending") == result
    assert repo.governance.preview_cleanup("pending").planned
    assert repo.managed_status("pending").state == "abandoned"
    with pytest.raises(CandidateAbandonedError), repo.resume_managed("pending"):
        pass
    assert repo.governance.collect("gc").complete


def test_unsealed_native_writer_partial_files_are_owned_by_private_namespace(tmp_path):
    repo = create(tmp_path)
    with prepare(repo) as writer:
        path = writer.path("native", relative_path="native.bin")
        path.write_bytes(b"partial native output")
        (path.parent / ".native.tmp").write_bytes(b"unsealed sidecar")
        missing = writer.path("never-created", relative_path="absent.bin")
    repo.governance.abandon("pending")
    result = repo.governance.cleanup("pending")
    assert set(result.deleted_files) == {"native.bin", ".native.tmp"}
    assert not missing.exists()


def test_cleanup_requires_explicit_abandonment_and_existing_plan_for_resume(tmp_path):
    repo = create(tmp_path)
    with pytest.raises(PublicationNotFoundError):
        repo.governance.cleanup("unknown")
    assert not managed_directory(tmp_path, "unknown").exists()
    with prepare(repo) as writer:
        path = writer.write_bytes("k", b"keep", relative_path="data.bin")
    with pytest.raises(CandidateStateError):
        repo.governance.cleanup("pending")
    assert path.read_bytes() == b"keep"
    repo.governance.abandon("pending")
    with pytest.raises(PublicationNotFoundError):
        repo.governance.resume_cleanup("pending")
    assert not (path.parent.parent / "cleanup-plan.json").exists()


@pytest.mark.parametrize("after_objects", [False, True])
@pytest.mark.parametrize("reuse", [False, True])
def test_installed_failed_commit_only_deletes_its_created_objects(
    tmp_path, monkeypatch, after_objects, reuse
):
    repo = create(tmp_path)
    if reuse:
        with prepare(repo, "p1") as writer:
            writer.write_bytes("old", b"source", relative_path="old.bin")
            writer.commit()
        source = repo.open("data").files()[0]
    seal, base = installed_failure(repo, monkeypatch, after_objects=after_objects, reuse=reuse)
    own = {
        o.object_id
        for o in seal.declaration.files.objects
        if o.locator.relative_path.startswith(token("pending") + "/")
    }
    preview = repo.governance.preview_cleanup("pending")
    assert preview.location == "installed" and preview.files == ("new.bin",)
    result = repo.governance.cleanup("pending")
    assert result.deleted_files == ("new.bin",) and not (base / "new.bin").exists()
    assert not own.intersection(repo.governance.preview().protected_objects)
    assert repo.governance.collect("gc").deleted_objects == ()
    if reuse:
        assert source.read_bytes() == b"source"
        assert repo.open("data").files() == (source,)


@pytest.mark.parametrize("state", ["current", "historical", "retired"])
def test_committed_candidate_never_gains_cleanup_rights(tmp_path, state):
    repo = create(tmp_path)
    with prepare(repo, "p1") as writer:
        writer.write_bytes("k", b"committed", relative_path="data.bin")
        writer.commit()
    if state != "current":
        with prepare(repo, "p2", 1) as writer:
            writer.write_bytes("k", b"latest", relative_path="data.bin")
            writer.commit()
    if state == "retired":
        repo.governance.collect("gc")
    with pytest.raises(CandidateStateError):
        repo.governance.abandon("p1")
    with pytest.raises(CandidateStateError):
        repo.governance.cleanup("p1")


@pytest.mark.parametrize("point", ["plan", "unlink", "checkpoint", "complete", "sync"])
@pytest.mark.parametrize("after", [False, True])
def test_failed_cleanup_resumes_fixed_plan_at_each_persistence_boundary(
    tmp_path, monkeypatch, point, after
):
    repo = create(tmp_path)
    directory = failed(repo, count=5)
    name = {
        "plan": "immutable_write",
        "unlink": "delete_owned_file",
        "sync": "sync_control_files",
        "checkpoint": "atomic_write",
        "complete": "atomic_write",
    }[point]
    original = getattr(_service, name)
    triggered = False

    def fail(*args, **kwargs):
        nonlocal triggered
        matches = point not in ("checkpoint", "complete") or (
            json.loads(args[1])["complete"] == (point == "complete")
        )
        if not triggered and matches:
            triggered = True
            if after:
                original(*args, **kwargs)
            raise OSError("injected persistence failure")
        return original(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(_service, name, fail)
        with pytest.raises(OSError):
            repo.governance.cleanup("pending")
    assert triggered
    if point != "plan" or after:
        # Files introduced after the fixed plan are never retroactively authorized.
        extra = directory / "files/late.bin"
        extra.write_bytes(b"outside original plan")
        result = repo.governance.resume_cleanup("pending")
        assert extra.read_bytes() == b"outside original plan"
    else:
        assert all((directory / f"files/sub/{i}.bin").exists() for i in range(5))
        result = repo.governance.cleanup("pending")
    assert result.complete
    assert set(result.deleted_files) | set(result.missing_files) == {
        f"sub/{i}.bin" for i in range(5)
    }
    assert repo.governance.resume_cleanup("pending") == result


def test_checkpoint_cost_and_unrecorded_deletion_are_bounded(tmp_path, monkeypatch):
    repo = create(tmp_path)
    failed(repo, count=33)
    sizes = []
    original = _service.atomic_write

    def record(path, data, **kwargs):
        sizes.append(len(json.loads(data)["outcomes"]))
        return original(path, data, **kwargs)

    monkeypatch.setattr(_service, "atomic_write", record)
    assert len(repo.governance.cleanup("pending").deleted_files) == 33
    assert sizes == [1, 2, 4, 8, 16, 32, 33]


@pytest.mark.parametrize("location", ["private", "installed"])
@pytest.mark.parametrize("damage", ["symlink-file", "symlink-directory", "fifo", "both"])
def test_unsafe_namespace_blocks_deletion_before_first_unlink(
    tmp_path, monkeypatch, location, damage
):
    repo = create(tmp_path)
    if location == "private":
        directory = failed(repo)
        base = directory / "files"
        kept = base / "sub/0.bin"
    else:
        _, base = installed_failure(repo, monkeypatch)
        directory = managed_directory(tmp_path, "pending")
        kept = base / "new.bin"
    outside = tmp_path / "external"
    outside.mkdir()
    (outside / "keep").write_bytes(b"external")
    if damage == "symlink-file":
        (base / "z-link").symlink_to(outside / "keep")
    elif damage == "symlink-directory":
        (base / "z-link").symlink_to(outside, target_is_directory=True)
    elif damage == "fifo":
        os.mkfifo(base / "z-pipe")
    else:
        other = (
            managed_data_root(tmp_path) / token("pending")
            if location == "private"
            else directory / "files"
        )
        other.mkdir(parents=True)
    with pytest.raises(StoreCorruptionError):
        repo.governance.cleanup("pending")
    assert kept.exists() and (outside / "keep").read_bytes() == b"external"
    assert not (directory / "cleanup-plan.json").exists()


def test_installed_undeclared_file_is_never_deleted(tmp_path, monkeypatch):
    repo = create(tmp_path)
    _, base = installed_failure(repo, monkeypatch)
    (base / "unknown.bin").write_bytes(b"unproven")
    with pytest.raises(StoreCorruptionError):
        repo.governance.cleanup("pending")
    assert (base / "new.bin").exists() and (base / "unknown.bin").exists()


def test_symlink_substituted_after_plan_is_not_followed_on_resume(tmp_path, monkeypatch):
    repo = create(tmp_path)
    directory = failed(repo)
    original = _service.immutable_write

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("after plan")

    with monkeypatch.context() as patch:
        patch.setattr(_service, "immutable_write", fail)
        with pytest.raises(OSError):
            repo.governance.cleanup("pending")
    original_files = directory / "files"
    relocated = tmp_path / "relocated"
    original_files.rename(relocated)
    original_files.symlink_to(relocated, target_is_directory=True)
    with pytest.raises(StoreCorruptionError):
        repo.governance.resume_cleanup("pending")
    assert (relocated / "sub/0.bin").exists()


def test_installed_missing_bytes_recorded_without_expanding_creator_authority(
    tmp_path, monkeypatch
):
    repo = create(tmp_path)
    _, base = installed_failure(repo, monkeypatch)
    (base / "new.bin").unlink()
    base.rmdir()
    result = repo.governance.cleanup("pending")
    assert result.location == "installed" and result.missing_files == ("new.bin",)
    assert not repo.governance.preview().reclaimable


def test_lifecycle_gate_is_required(tmp_path):
    repo = create(tmp_path, lifecycle=False)
    with pytest.raises(UnsupportedCapabilityError):
        repo.governance.cleanup("anything")


@pytest.mark.parametrize("record", ["plan", "progress"])
def test_cleanup_wire_goldens_and_strict_damage_rejection(record):
    import jsonschema

    encode = getattr(codec, f"encode_managed_cleanup_{record}")
    decode = getattr(codec, f"decode_managed_cleanup_{record}")
    data = (Path(__file__).parent / f"fixtures/protocol/v4/cleanup/{record}.json").read_bytes()
    assert encode(decode(data)) == data
    schema = json.loads(
        (Path(__file__).parents[1] / "src/asterstore/metadata/schemas/v4.json").read_text()
    )
    jsonschema.Draft202012Validator(schema).validate(json.loads(data))
    for field, value in [("format_version", True), ("unknown", True), ("kind", "future")]:
        raw = json.loads(data)
        raw[field] = value
        with pytest.raises(StoreCorruptionError):
            decode(json.dumps(raw).encode())
    with pytest.raises(StoreCorruptionError):
        decode(data.replace(b'"format_version":4', b'"format_version":4,"format_version":4'))
    for path in ["../outside", "/absolute", "a/../escape", "a\\b", "a//b", ""]:
        raw = json.loads(data)
        if record == "plan":
            raw["files"] = [path]
        else:
            raw["outcomes"][0]["path"] = path
        with pytest.raises(StoreCorruptionError):
            decode(json.dumps(raw).encode())


@pytest.mark.parametrize("damage", ["request", "abandonment", "plan", "progress", "creator"])
def test_corrupt_authority_blocks_cleanup_and_governance(tmp_path, monkeypatch, damage):
    repo = create(tmp_path)
    _, base = installed_failure(repo, monkeypatch)
    directory = managed_directory(tmp_path, "pending")
    original = _service.immutable_write

    def stop(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("plan persisted")

    with monkeypatch.context() as patch:
        patch.setattr(_service, "immutable_write", stop)
        with pytest.raises(OSError):
            repo.governance.cleanup("pending")
    if damage == "request":
        p = directory / "cleanup-plan.json"
        raw = json.loads(p.read_bytes())
        raw["request"]["operation_id"] = "different"
        p.write_text(json.dumps(raw))
    elif damage == "abandonment":
        (directory / "abandoned.json").unlink()
    elif damage == "plan":
        p = directory / "cleanup-plan.json"
        raw = json.loads(p.read_bytes())
        raw["files"] = ["outside.bin"]
        p.write_text(json.dumps(raw))
    elif damage == "progress":
        (directory / "cleanup-progress.json").write_bytes(
            codec.encode_managed_cleanup_progress(
                codec.ManagedCleanupProgress("store", "pending", (), True)
            )
        )
    else:
        (directory / "sealed.json").unlink()
    with pytest.raises((StoreCorruptionError, CandidateStateError)):
        repo.governance.resume_cleanup("pending")
    with pytest.raises(StoreCorruptionError):
        repo.governance.collect("gc")
    assert (base / "new.bin").exists()


def test_ambiguous_final_write_retry_syncs_saved_completion(tmp_path, monkeypatch):
    repo = create(tmp_path)
    directory = failed(repo)
    original = _service.atomic_write

    def ambiguous(path, data, **kwargs):
        if json.loads(data)["complete"]:
            original(path, data, durable=False)
            raise OSError("replace succeeded, directory sync failed")
        return original(path, data, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(_service, "atomic_write", ambiguous)
        with pytest.raises(OSError):
            repo.governance.cleanup("pending")
    synced = set()
    original_sync = _service.sync_control_files

    def sync(root, paths):
        synced.update(paths)
        return original_sync(root, paths)

    monkeypatch.setattr(_service, "sync_control_files", sync)
    assert repo.governance.resume_cleanup("pending").complete
    assert {
        directory / "cleanup-progress.json",
        directory / "cleanup-plan.json",
        directory / "abandoned.json",
        directory / "request.json",
    } <= synced


def test_pending_user_of_orphan_object_blocks_cleanup(tmp_path, monkeypatch):
    repo = create(tmp_path)
    seal, base = installed_failure(repo, monkeypatch)
    with prepare(repo, "consumer"):
        pass
    directory = managed_directory(tmp_path, "consumer")
    request = codec.decode_managed_request((directory / "request.json").read_bytes())
    # This cannot be produced by public reuse (which requires a committed source).
    # Persisted cross-record damage must still block deletion rather than trust absence of current.
    (directory / "protection.json").write_bytes(
        codec.encode_declaration_record(request.declaration_record(seal.declaration.files))
    )
    with pytest.raises(StoreCorruptionError, match="another user"):
        repo.governance.cleanup("pending")
    assert (base / "new.bin").exists()


@pytest.mark.parametrize("paths", [["same", "same"], ["parent", "parent/child"]])
def test_cleanup_plan_rejects_duplicate_and_ancestor_file_targets(paths):
    fixture = Path(__file__).parent / "fixtures/protocol/v4/cleanup/plan.json"
    raw = json.loads(fixture.read_bytes())
    raw["files"] = paths
    with pytest.raises(StoreCorruptionError):
        codec.decode_managed_cleanup_plan(json.dumps(raw).encode())


def test_new_cleanup_control_records_do_not_add_io_to_current_open(tmp_path, monkeypatch):
    repo = create(tmp_path)
    with prepare(repo, "p1") as writer:
        writer.write_bytes("k", b"current", relative_path="part.bin")
        writer.commit()
    failed(repo)
    repo.governance.cleanup("pending")
    original = Path.read_bytes
    reads = []

    def read(path):
        reads.append(path.name)
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    binding = repo.open("data")
    assert reads == ["format.json", "current.json"]
    before = list(reads)
    assert len(binding.files()) == 1
    assert reads == before
