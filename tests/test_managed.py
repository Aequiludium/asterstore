"""Managed v4 ownership, atomic visibility, precise recovery and read costs."""

import io
import json
from dataclasses import replace
from pathlib import Path

import pytest

from asterstore import (
    CandidateStateError,
    Capabilities,
    CollectionPolicy,
    Declaration,
    FileSet,
    HistoryAccess,
    InvalidDeclarationError,
    Locator,
    Member,
    Object,
    PublicationConflictError,
    Repository,
    StoreCorruptionError,
    UnsupportedCapabilityError,
)
from asterstore.metadata.protocol import (
    MAX_GENERATION,
    ObjectRecord,
    decode_declaration_record,
    decode_managed_request,
    decode_object_record,
    decode_store,
    encode_declaration_record,
    encode_managed_request,
    encode_object_record,
    encode_store,
)
from asterstore.publishing.managed import _candidate
from asterstore.publishing.transactions import _commit
from asterstore.storage.registry import (
    managed_data_root,
    object_record_path,
    token,
)


def create(root):
    repo = Repository(root)
    repo.initialize(
        store_id="store", resource_ids=["owned", "external"], managed_resource_id="owned"
    )
    return repo


def prepare(repo, pid="p1", generation=0, *, op=None, durable=False):
    return repo.prepare_managed(
        "simulation",
        publication_id=pid,
        operation_id=op or pid,
        expected_generation=generation,
        history=HistoryAccess.VERSIONED,
        durable=durable,
    )


def publish(repo, pid="p1", generation=0):
    with prepare(repo, pid, generation) as candidate:
        candidate.write_bytes("logical:../member", pid.encode(), relative_path="parts/one.bin")
        return candidate.commit()


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_managed_publication_and_incremental_reuse(tmp_path):
    repo = create(tmp_path)
    first = publish(repo)
    old = repo.open("simulation")
    assert old.publication == first.declaration
    assert old.files()[0].read_bytes() == b"p1"
    obj = first.declaration.files.objects[0]
    proof = decode_object_record(object_record_path(tmp_path, obj.object_id).read_bytes())
    assert proof.creator_operation_id == "p1" and proof.locator == obj.locator
    with prepare(repo, "p2", 1) as candidate:
        candidate.reuse("p1")
        candidate.alias("same bytes", member="logical:../member")
        candidate.write_bytes("new", b"second", relative_path="new.bin")
        second = candidate.commit()
    bound = Repository(tmp_path).open("simulation")
    assert bound.files(keys=["same bytes", "logical:../member"]) == old.files() * 2
    assert second.declaration.files.objects[0] == obj
    assert bound.files()[1].read_bytes() == b"second"
    assert repo.open("simulation", publication_id="p1").files() == old.files()
    with repo.resume_managed("p1") as retry:
        assert retry.commit() == first
    assert repo.describe("simulation") == second
    assert repo.managed_status("p1").state == "historical"
    assert repo.managed_status("p2").state == "current"
    assert len(list(managed_data_root(tmp_path).rglob("*.bin"))) == 2


def test_registered_and_managed_share_one_store_but_not_ownership(tmp_path):
    repo = create(tmp_path)
    external = Declaration(
        "external-data",
        "e1",
        FileSet([Object("e", Locator("external", "file.bin"))], [Member("key", "e")]),
        Capabilities.registered(),
    )
    repo.register(external, operation_id="e", expected_generation=0)
    first = publish(repo)
    with pytest.raises(UnsupportedCapabilityError):
        repo.register(first.declaration, operation_id="adopt", expected_generation=1)
    bad = replace(
        external, files=FileSet([Object("e2", Locator("owned", "x"))], [Member("k", "e2")])
    )
    with pytest.raises(InvalidDeclarationError):
        repo.register(bad, operation_id="adopt", expected_generation=1)
    with pytest.raises(InvalidDeclarationError):
        repo.open("simulation", resources={"owned": tmp_path / "foreign"})
    with pytest.raises(UnsupportedCapabilityError):
        repo.resume_registration("p1")
    with pytest.raises(UnsupportedCapabilityError):
        repo.registration_status("p1")
    assert repo.open("external-data", resources={"external": tmp_path / "external"})
    assert not (tmp_path / "external").exists()


def test_operation_identity_reserved_across_entry_points(tmp_path):
    repo = create(tmp_path)
    empty = Declaration("external", "ext", FileSet([], []), Capabilities.registered())
    with prepare(repo):
        with pytest.raises(PublicationConflictError):
            repo.register(empty, operation_id="p1", expected_generation=0)
    repo.register(empty, operation_id="ext", expected_generation=0)
    with pytest.raises(PublicationConflictError), prepare(repo, op="ext"):
        pass
    with pytest.raises(CandidateStateError), repo.resume_managed("p1"):
        pass
    assert repo.managed_status("p1").state == "writing"


@pytest.mark.parametrize("point", ["seal", "plan", "rename", "object", "history", "current"])
@pytest.mark.parametrize("after", [False, True])
def test_every_persistent_boundary_is_recoverable(tmp_path, monkeypatch, point, after):
    repo = create(tmp_path)
    first = publish(repo)
    with prepare(repo, "p2", 1) as candidate:
        candidate.write_bytes("new", b"p2", relative_path="new.bin")
        if point == "rename":
            module, name = _candidate.os, "rename"

            def match(args):
                return True
        elif point == "seal":
            module, name = _candidate, "immutable_write"

            def match(args):
                return args[0].name == "sealed.json"
        else:
            module = _commit
            name = "atomic_write" if point == "current" else "immutable_write"

            def match(args):
                return (
                    (point == "plan" and args[0].parent.name == "registrations")
                    or (point == "object" and args[0].parent.name == "object-records")
                    or (point == "history" and args[0].parent.name == "history")
                    or (point == "current" and args[0].name == "current.json")
                )

        original = getattr(module, name)
        fired = False

        def fault(*args, **kwargs):
            nonlocal fired
            if not fired and match(args):
                fired = True
                if after:
                    original(*args, **kwargs)
                raise OSError("interrupted")
            return original(*args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(module, name, fault)
            with pytest.raises(OSError, match="interrupted"):
                candidate.commit()
        assert fired
        if point == "seal" and not after:
            with pytest.raises(CandidateStateError):
                candidate.path("extra", relative_path="extra.bin")
            candidate.seal()  # Only the original in-memory writer can finish an unsealed request.
    visible = repo.describe("simulation")
    assert visible.declaration.publication_id == ("p2" if point == "current" and after else "p1")
    with repo.resume_managed("p2") as candidate:
        result = candidate.commit()
    assert result.generation == 2
    assert repo.open("simulation").files()[0].read_bytes() == b"p2"
    assert repo.describe("simulation", publication_id="p1") == first


def test_stale_candidate_never_rebases_or_installs_data(tmp_path):
    repo = create(tmp_path)
    with prepare(repo, "loser") as candidate:
        candidate.write_bytes("new", b"loser", relative_path="data.bin")
        candidate.seal()
    winner = publish(repo)
    assert repo.managed_status("loser").state == "conflict"
    with repo.resume_managed("loser") as candidate, pytest.raises(PublicationConflictError):
        candidate.commit()
    assert repo.describe("simulation") == winner
    assert not (managed_data_root(tmp_path) / token("loser")).exists()


@pytest.mark.parametrize("damage", ["missing", "extra", "symlink", "directory"])
def test_seal_checks_declared_files_only_at_write_boundary(tmp_path, damage):
    repo = create(tmp_path)
    with prepare(repo) as candidate:
        path = candidate.path("key", relative_path="data.bin")
        if damage == "extra":
            path.write_bytes(b"a")
            path.with_name("untracked.bin").write_bytes(b"b")
        elif damage == "symlink":
            outside = tmp_path / "external.bin"
            outside.write_bytes(b"unowned")
            path.symlink_to(outside)
        elif damage == "directory":
            path.mkdir()
        with pytest.raises(StoreCorruptionError):
            candidate.seal()
    assert repo.managed_status("p1").state == "writing"


def test_missing_creation_evidence_is_not_reconstructed(tmp_path):
    repo = create(tmp_path)
    first = publish(repo)
    proof = object_record_path(tmp_path, first.declaration.files.objects[0].object_id)
    proof.unlink()
    with repo.resume_managed("p1") as candidate, pytest.raises(StoreCorruptionError):
        candidate.commit()
    assert not proof.exists()


def test_no_data_or_object_registry_io_in_open_and_selection(tmp_path, monkeypatch):
    repo = create(tmp_path)
    publish(repo)
    original = io.open
    opened = []

    def tracked(path, *args, **kwargs):
        opened.append(Path(path))
        return original(path, *args, **kwargs)

    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected metadata probe")

    with monkeypatch.context() as patch:
        patch.setattr(io, "open", tracked)
        patch.setattr(Path, "stat", forbidden)
        patch.setattr(Path, "iterdir", forbidden)
        binding = repo.open("simulation")
        assert [p.name for p in opened] == ["format.json", "current.json"]
        opened.clear()
        assert binding.files(keys=["logical:../member"])
        assert binding.files()
        assert not opened


def test_reuse_never_reads_or_syncs_old_data(tmp_path, monkeypatch):
    repo = create(tmp_path)
    first = publish(repo)
    old_path = repo.open("simulation").files()[0]
    original = io.open

    def guarded(path, *args, **kwargs):
        assert Path(path) != old_path, "reused bytes were read or synchronized"
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(io, "open", guarded)
        with prepare(repo, "p2", 1, durable=True) as candidate:
            assert candidate.reuse("p1") == first.declaration.files.objects
            candidate.commit()
    assert repo.open("simulation").files() == (old_path,)


def test_strong_retry_syncs_weakly_committed_new_bytes(tmp_path, monkeypatch):
    repo = create(tmp_path)
    publish(repo)
    path = repo.open("simulation").files()[0]
    synced = []
    import asterstore.storage._files as storage_files

    original = storage_files.sync_file

    def tracked(file):
        synced.append(file)
        original(file)

    monkeypatch.setattr(storage_files, "sync_file", tracked)
    with repo.resume_managed("p1", durable=True) as candidate:
        candidate.commit()
    assert path in synced


def test_managed_feature_must_be_explicit_and_cannot_be_added_silently(tmp_path):
    repo = Repository(tmp_path)
    repo.initialize(store_id="store", resource_ids=["owned"])
    before = snapshot(tmp_path)
    with pytest.raises(UnsupportedCapabilityError), prepare(repo):
        pass
    with pytest.raises(PublicationConflictError):
        repo.initialize(store_id="store", resource_ids=["owned"], managed_resource_id="owned")
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("generation", [True, -1, MAX_GENERATION, 1.5])
def test_invalid_request_cannot_create_candidate(tmp_path, generation):
    repo = create(tmp_path)
    before = snapshot(tmp_path)
    with pytest.raises(InvalidDeclarationError):
        prepare(repo, generation=generation)
    assert snapshot(tmp_path) == before


def test_v3_governance_cannot_delete_v4_objects(tmp_path):
    repo = create(tmp_path)
    publish(repo)
    before = snapshot(tmp_path)
    with pytest.raises(StoreCorruptionError):
        repo.retention.collect(CollectionPolicy(("simulation",)))
    assert snapshot(tmp_path) == before


def test_managed_protocol_schema_and_creator_validation(tmp_path):
    import jsonschema

    repo = create(tmp_path)
    result = publish(repo)
    from asterstore.storage.registry import read_managed_request, read_store

    store = read_store(tmp_path)
    request = read_managed_request(tmp_path, store, "p1")
    proof = decode_object_record(
        object_record_path(tmp_path, result.declaration.files.objects[0].object_id).read_bytes()
    )
    schema = json.loads(
        (Path(__file__).parents[1] / "src/asterstore/metadata/schemas/v4.json").read_text()
    )
    for value, encode, decode in [
        (store, encode_store, decode_store),
        (request, encode_managed_request, decode_managed_request),
        (proof, encode_object_record, decode_object_record),
        (result, encode_declaration_record, decode_declaration_record),
    ]:
        data = encode(value)
        jsonschema.Draft202012Validator(schema).validate(json.loads(data))
        assert decode(data) == value
    with pytest.raises(InvalidDeclarationError):
        replace(proof, creator_operation_id="forged")
    with pytest.raises(InvalidDeclarationError):
        ObjectRecord(proof.store_id, proof.object_id, proof.locator, proof.byte_stability)
    raw = json.loads(encode_store(store))
    raw["required_features"] = ["registered"]
    with pytest.raises(StoreCorruptionError):
        decode_store(json.dumps(raw).encode())
    raw = json.loads(encode_object_record(proof))
    raw.pop("creator_operation_id")
    with pytest.raises(StoreCorruptionError):
        decode_object_record(json.dumps(raw).encode())


def test_empty_managed_commit_and_alias_validation(tmp_path):
    repo = create(tmp_path)
    with prepare(repo) as candidate:
        empty = candidate.commit()
    assert not empty.declaration.files.objects
    assert repo.open("simulation").files() == ()
    with repo.resume_managed("p1") as candidate:
        assert candidate.commit() == empty
    with prepare(repo, "p2", 1) as candidate:
        candidate.write_bytes("member", b"value", relative_path="part.bin")
        with pytest.raises(InvalidDeclarationError):
            candidate.path("member", relative_path="another.bin")
        with pytest.raises(InvalidDeclarationError):
            candidate.path("new", relative_path="../outside")
        candidate.alias("alias", member="member")
        assert len(candidate.commit().declaration.files.objects) == 1


def test_current_only_historical_reuse_is_not_implicitly_authorized(tmp_path):
    from asterstore import HistoryUnavailableError

    repo = create(tmp_path)
    for i in range(2):
        with repo.prepare_managed(
            "data", publication_id=f"p{i}", operation_id=f"op{i}", expected_generation=i
        ) as candidate:
            candidate.write_bytes("key", b"value", relative_path="data.bin")
            candidate.commit()
    with repo.prepare_managed(
        "data", publication_id="p2", operation_id="op2", expected_generation=2
    ) as candidate:
        with pytest.raises(HistoryUnavailableError):
            candidate.reuse("p0")
        candidate.reuse("p1")
        candidate.commit()


@pytest.mark.parametrize("name", ["repository", "request", "publication", "object"])
def test_managed_golden_records_and_rejected_mutations(name):
    import jsonschema

    fixture = Path(__file__).parent / "fixtures/protocol/v4/managed" / (name + ".json")
    data = fixture.read_bytes()
    encode, decode = {
        "repository": (encode_store, decode_store),
        "request": (encode_managed_request, decode_managed_request),
        "publication": (encode_declaration_record, decode_declaration_record),
        "object": (encode_object_record, decode_object_record),
    }[name]
    schema = json.loads(
        (Path(__file__).parents[1] / "src/asterstore/metadata/schemas/v4.json").read_text()
    )
    jsonschema.Draft202012Validator(schema).validate(json.loads(data))
    assert encode(decode(data)) == data
    for field, value in [("format_version", 3), ("unknown_field", "unknown")]:
        raw = json.loads(data)
        raw[field] = value
        with pytest.raises(StoreCorruptionError):
            decode(json.dumps(raw).encode())
    if name == "request":
        for value in [True, -1, 2**63 - 1, 2**63, 1.5]:
            raw = json.loads(data)
            raw["expected_generation"] = value
            with pytest.raises(StoreCorruptionError):
                decode(json.dumps(raw).encode())


def test_shared_registration_entry_keeps_runtime_input_validation(tmp_path):
    repo = create(tmp_path)
    before = snapshot(tmp_path)
    with pytest.raises(InvalidDeclarationError):
        repo.register(None, operation_id="bad", expected_generation=0)
    assert snapshot(tmp_path) == before


def test_native_parquet_writer_and_engine_use_managed_declaration(tmp_path):
    pl = pytest.importorskip("polars")
    from asterstore.integrations.polars import scan_parquet

    repo = create(tmp_path)
    with prepare(repo) as candidate:
        pl.DataFrame({"temperature": [10, 12]}).write_parquet(
            candidate.path("simulation:initial", relative_path="part.parquet")
        )
        candidate.commit()
    result = scan_parquet(repo.open("simulation"), keys=["simulation:initial"]).collect()
    assert result["temperature"].to_list() == [10, 12]
