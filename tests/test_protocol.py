import json
from pathlib import Path

import pytest

from asterstore import Dataset, Repository, StoreCorruptionError
from asterstore.metadata import (
    CandidateManifest,
    PublishedRecord,
    decode_candidate,
    decode_publication,
    encode_candidate,
    encode_publication,
)


def test_control_records_round_trip() -> None:
    candidate = CandidateManifest("a" * 32, Dataset("市场/prices"), "p1", 0, ("日/part.bin",))
    assert decode_candidate(encode_candidate(candidate)) == candidate
    record = PublishedRecord(1, candidate.candidate_id, candidate.publication())
    assert decode_publication(encode_publication(record)) == record


def test_version_one_golden_records() -> None:
    fixture = Path(__file__).parent / "fixtures/protocol/v1"
    candidate_data = (fixture / "candidate.json").read_bytes()
    current_data = (fixture / "current.json").read_bytes()
    candidate = decode_candidate(candidate_data)
    current = decode_publication(current_data)
    assert candidate.dataset.dataset_id == "market/prices"
    assert candidate.keys == ("part.bin",)
    assert current.generation == 1
    assert current.publication == candidate.publication()
    assert encode_candidate(candidate) == candidate_data
    assert encode_publication(current) == current_data


@pytest.mark.parametrize("replacement", [True, -1, 0, 1.5, "1"])
def test_generation_is_a_positive_integer(replacement: object) -> None:
    candidate = CandidateManifest("a" * 32, Dataset("prices"), "p1", 0, ())
    payload = json.loads(
        encode_publication(PublishedRecord(1, candidate.candidate_id, candidate.publication()))
    )
    payload["generation"] = replacement
    with pytest.raises(StoreCorruptionError):
        decode_publication(json.dumps(payload).encode())


@pytest.mark.parametrize(
    "data",
    [
        b'{"format_version":1,"format_version":1}',
        b'{"x":NaN}',
        b"\xff",
        b"[]",
        b'{"format_version":2,"kind":"candidate"}',
    ],
)
def test_malformed_or_unsupported_records_fail_closed(data: bytes) -> None:
    with pytest.raises(StoreCorruptionError):
        decode_candidate(data)


def test_malformed_current_is_not_treated_as_an_empty_dataset(tmp_path: Path) -> None:
    repository = Repository(tmp_path)
    with repository.prepare(Dataset("prices"), durable=False) as candidate:
        candidate.commit()
    current = next((tmp_path / ".asterstore/datasets").glob("*/current.json"))
    current.write_text('{"format_version": 99}', encoding="utf-8")
    with pytest.raises(StoreCorruptionError):
        repository.open("prices")
    with pytest.raises(StoreCorruptionError):
        with repository.prepare(Dataset("prices"), durable=False):
            pass


def test_missing_marker_does_not_adopt_a_nonempty_control_directory(tmp_path: Path) -> None:
    control = tmp_path / ".asterstore"
    control.mkdir()
    (control / "unknown-data").write_bytes(b"keep")
    with pytest.raises(StoreCorruptionError):
        with Repository(tmp_path).prepare(Dataset("prices"), durable=False):
            pass
    assert (control / "unknown-data").read_bytes() == b"keep"
    assert not (control / "format.json").exists()


@pytest.mark.parametrize("version", [1, 2])
def test_legacy_is_readable_but_governance_writes_require_v3(tmp_path: Path, version: int):
    from asterstore import CollectionPolicy, RepositoryUpgradeRequiredError
    from asterstore.metadata import encode_repository
    from asterstore.storage import candidate_directory, dataset_directory

    fixtures = Path(__file__).parent / f"fixtures/protocol/v{version}"
    candidate = decode_candidate((fixtures / "candidate.json").read_bytes())
    head = dataset_directory(tmp_path, candidate.dataset.dataset_id) / "current.json"
    head.parent.mkdir(parents=True)
    head.write_bytes((fixtures / "current.json").read_bytes())
    state = candidate_directory(tmp_path, candidate.candidate_id) / "state.json"
    state.parent.mkdir(parents=True)
    state.write_bytes((fixtures / "candidate.json").read_bytes())
    marker = tmp_path / ".asterstore/format.json"
    marker.write_bytes(encode_repository(format_version=version))
    repository = Repository(tmp_path)
    assert repository.open(candidate.dataset.dataset_id).publication == candidate.publication()
    before = {
        str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()
    }
    with pytest.raises(RepositoryUpgradeRequiredError):
        with repository.prepare(candidate.dataset):
            pass
    with pytest.raises(RepositoryUpgradeRequiredError):
        with repository.resume(candidate.candidate_id):
            pass
    with pytest.raises(RepositoryUpgradeRequiredError):
        repository.retention.retain("run", candidate.dataset.dataset_id)
    with pytest.raises(RepositoryUpgradeRequiredError):
        repository.retention.preview(CollectionPolicy((candidate.dataset.dataset_id,)))
    with pytest.raises(RepositoryUpgradeRequiredError):
        repository.retention.collect(CollectionPolicy((candidate.dataset.dataset_id,)))
    with pytest.raises(RepositoryUpgradeRequiredError):
        repository.retention.resume_collection("b" * 32)
    with pytest.raises(RepositoryUpgradeRequiredError):
        repository.abandon(candidate.candidate_id)
    with pytest.raises(RepositoryUpgradeRequiredError):
        repository.retention.cleanup_candidate(candidate.candidate_id)
    with pytest.raises(RepositoryUpgradeRequiredError):
        repository.inspection.candidates()
    after = {
        str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()
    }
    assert after == before
    assert repository.candidate_status(candidate.candidate_id).state == "current"


def test_v3_marker_and_mixed_record_versions_are_checked(tmp_path: Path) -> None:
    repository = Repository(tmp_path)
    with repository.prepare(Dataset("data"), durable=False) as candidate:
        candidate.commit()
    marker = json.loads((tmp_path / ".asterstore/format.json").read_bytes())
    assert marker["format_version"] == 3
    current = next((tmp_path / ".asterstore/datasets").glob("*/current.json"))
    payload = json.loads(current.read_bytes())
    payload["format_version"] = 1
    current.write_text(json.dumps(payload))
    with pytest.raises(StoreCorruptionError):
        repository.open("data")


@pytest.mark.parametrize(
    "field,value",
    [
        ("revision", True),
        ("revision", 0),
        ("revision", 2),
        ("state", "gone"),
        ("selection", "latest"),
        ("format_version", 1),
        ("extra", 0),
    ],
)
def test_reference_records_reject_invalid_versions_and_states(field, value):
    from asterstore import Reference, ReferenceRecord
    from asterstore.metadata import decode_reference, encode_reference

    payload = json.loads(
        encode_reference(ReferenceRecord(Reference("run", "data", "p1"), 1, "active", "current"))
    )
    payload[field] = value
    with pytest.raises(StoreCorruptionError):
        decode_reference(json.dumps(payload).encode())


def test_reuse_metadata_requires_a_managed_key():
    payload = json.loads(
        encode_candidate(CandidateManifest("a" * 32, Dataset("data"), "p1", 0, ()))
    )
    payload["reused_objects"] = [{"key": "part.bin", "publication_id": "old"}]
    with pytest.raises(StoreCorruptionError, match="managed object key"):
        decode_candidate(json.dumps(payload).encode())


def test_v2_golden_records():
    from asterstore.metadata import decode_reference, encode_reference

    fixture = Path(__file__).parent / "fixtures/protocol/v2"
    candidate_bytes = (fixture / "candidate.json").read_bytes()
    publication_bytes = (fixture / "current.json").read_bytes()
    reference_bytes = (fixture / "reference.json").read_bytes()
    candidate = decode_candidate(candidate_bytes, expected_version=2)
    publication = decode_publication(publication_bytes, expected_version=2)
    reference = decode_reference(reference_bytes, expected_version=2)
    assert encode_candidate(candidate) == candidate_bytes
    assert encode_publication(publication) == publication_bytes
    assert encode_reference(reference) == reference_bytes
    assert candidate.publication() == publication.publication
    assert reference.reference.publication_id == publication.publication.publication_id
    assert reference.revision == 1 and reference.selection == "current"


def test_v2_reuse_golden_records_and_v1_namespace_boundary():
    fixture = Path(__file__).parent / "fixtures/protocol/v2"
    candidate_data = (fixture / "reuse-candidate.json").read_bytes()
    current_data = (fixture / "reuse-current.json").read_bytes()
    candidate = decode_candidate(candidate_data)
    current = decode_publication(current_data)
    assert candidate.keys == ("new.bin",)
    assert candidate.reused_objects[0].publication_id == "p1"
    assert candidate.publication() == current.publication
    assert encode_candidate(candidate) == candidate_data
    assert encode_publication(current) == current_data
    v1 = json.loads(current_data)
    v1["format_version"] = 1
    with pytest.raises(StoreCorruptionError, match="namespace"):
        decode_publication(json.dumps(v1).encode())


@pytest.mark.parametrize("damage", ["extra", "duplicate", "own_creator", "own_source", "unsealed"])
def test_reuse_record_rejects_invalid_membership(damage):
    fixture = Path(__file__).parent / "fixtures/protocol/v2/reuse-candidate.json"
    payload = json.loads(fixture.read_bytes())
    if damage == "extra":
        payload["reused_objects"][0]["arbitrary"] = True
    elif damage == "duplicate":
        payload["reused_objects"] *= 2
    elif damage == "own_creator":
        payload["reused_objects"][0]["key"] = ".asterstore/objects/" + "b" * 32 + "/part.bin"
    elif damage == "own_source":
        payload["reused_objects"][0]["publication_id"] = "p2"
    else:
        payload["keys"] = None
    with pytest.raises(StoreCorruptionError):
        decode_candidate(json.dumps(payload).encode())


def test_retirement_and_collection_golden_records():
    from asterstore.metadata import (
        decode_collection_plan,
        decode_collection_progress,
        decode_retired,
        encode_collection_plan,
        encode_collection_progress,
        encode_retired,
    )

    fixture = Path(__file__).parent / "fixtures/protocol/v2"
    for name, decoder, encoder in (
        ("retired.json", decode_retired, encode_retired),
        ("collection-plan.json", decode_collection_plan, encode_collection_plan),
        ("collection-progress.json", decode_collection_progress, encode_collection_progress),
    ):
        data = (fixture / name).read_bytes()
        assert encoder(decoder(data, expected_version=2)) == data
    retired = decode_retired((fixture / "retired.json").read_bytes(), expected_version=2)
    plan = decode_collection_plan(
        (fixture / "collection-plan.json").read_bytes(), expected_version=2
    )
    progress = decode_collection_progress(
        (fixture / "collection-progress.json").read_bytes(), expected_version=2
    )
    assert retired.collection_id == plan.operation_id == progress.operation_id
    assert progress.deleted == tuple(obj.key for obj in plan.objects)


@pytest.mark.parametrize(
    "name,field,value",
    [
        ("retired.json", "generation", True),
        ("retired.json", "collection_id", "../escape"),
        ("retired.json", "format_version", 1),
        ("collection-plan.json", "operation_id", "bad"),
        ("collection-plan.json", "objects", ["outside/file"]),
        ("collection-plan.json", "policy", {"datasets": [], "keep_last": 1}),
        ("collection-progress.json", "state", "ignored"),
        ("collection-progress.json", "deleted", ["../escape"]),
        ("collection-progress.json", "extra", True),
    ],
)
def test_invalid_retirement_journals_are_rejected(name, field, value):
    from asterstore.metadata import (
        decode_collection_plan,
        decode_collection_progress,
        decode_retired,
    )

    fixture = Path(__file__).parent / "fixtures/protocol/v2"
    payload = json.loads((fixture / name).read_bytes())
    payload[field] = value
    decoder = {
        "retired.json": decode_retired,
        "collection-plan.json": decode_collection_plan,
        "collection-progress.json": decode_collection_progress,
    }[name]
    with pytest.raises(StoreCorruptionError):
        decoder(json.dumps(payload).encode(), expected_version=2)


def test_abandonment_and_cleanup_golden_records():
    from asterstore.metadata import (
        AbandonedCandidate,
        decode_candidate_state,
        decode_cleanup_plan,
        decode_cleanup_progress,
        encode_abandoned,
        encode_cleanup_plan,
        encode_cleanup_progress,
    )

    fixture = Path(__file__).parent / "fixtures/protocol/v2"
    data = (fixture / "abandoned-candidate.json").read_bytes()
    record = decode_candidate_state(data, expected_version=2)
    assert isinstance(record, AbandonedCandidate) and record.manifest.keys is None
    assert encode_abandoned(record) == data
    for name, decoder, encoder in (
        ("cleanup-plan.json", decode_cleanup_plan, encode_cleanup_plan),
        ("cleanup-progress.json", decode_cleanup_progress, encode_cleanup_progress),
    ):
        payload = (fixture / name).read_bytes()
        assert encoder(decoder(payload, expected_version=2)) == payload
    with pytest.raises(StoreCorruptionError):
        decode_candidate(data)


@pytest.mark.parametrize(
    "bad_key",
    [
        ".asterstore/format.json",
        ".asterstore/candidates/" + "c" * 32 + "/state.json",
        ".asterstore/objects/" + "a" * 32 + "/part.bin",
        ".asterstore/candidates/" + "c" * 32 + "/files/../state.json",
    ],
)
def test_cleanup_plan_cannot_authorize_control_or_foreign_paths(bad_key):
    from asterstore.metadata import decode_cleanup_plan

    fixture = Path(__file__).parent / "fixtures/protocol/v2/cleanup-plan.json"
    data = json.loads(fixture.read_bytes())
    data["keys"] = [bad_key]
    with pytest.raises(StoreCorruptionError):
        decode_cleanup_plan(json.dumps(data).encode(), expected_version=2)


def protocol_codec(kind):
    from asterstore import metadata as m

    return {
        "candidate": (m.decode_candidate, m.encode_candidate),
        "publication": (m.decode_publication, m.encode_publication),
        "retention_reference": (m.decode_reference, m.encode_reference),
        "retired_publication": (m.decode_retired, m.encode_retired),
        "collection_plan": (m.decode_collection_plan, m.encode_collection_plan),
        "collection_progress": (m.decode_collection_progress, m.encode_collection_progress),
        "abandoned_candidate": (m.decode_candidate_state, m.encode_abandoned),
        "candidate_cleanup_plan": (m.decode_cleanup_plan, m.encode_cleanup_plan),
        "candidate_cleanup_progress": (m.decode_cleanup_progress, m.encode_cleanup_progress),
    }[kind]


def schema_validator():
    from importlib.resources import files

    from jsonschema import Draft202012Validator

    schema = json.loads(files("asterstore.metadata").joinpath("schemas/v3.json").read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


@pytest.mark.parametrize(
    "fixture",
    sorted((Path(__file__).parent / "fixtures/protocol/v3").glob("*.json")),
    ids=lambda p: p.stem,
)
def test_v3_schema_and_exact_golden_roundtrip(fixture):
    from asterstore.metadata import decode_repository, encode_repository

    data = fixture.read_bytes()
    payload = json.loads(data)
    schema_validator().validate(payload)
    if payload["kind"] == "repository":
        assert encode_repository(format_version=decode_repository(data)) == data
    else:
        decode, encode = protocol_codec(payload["kind"])
        assert encode(decode(data, expected_version=3)) == data
        with pytest.raises(StoreCorruptionError):
            decode(data, expected_version=2)


@pytest.mark.parametrize(
    "fixture",
    sorted((Path(__file__).parent / "fixtures/protocol/v3/invalid").glob("*.json")),
    ids=lambda p: p.stem,
)
def test_v3_negative_fixtures_fail_schema_and_codec(fixture):
    from jsonschema import ValidationError

    data = fixture.read_bytes()
    payload = json.loads(data)
    with pytest.raises(ValidationError):
        schema_validator().validate(payload)
    decode, _ = protocol_codec(payload["kind"])
    with pytest.raises(StoreCorruptionError):
        decode(data, expected_version=3)


@pytest.mark.parametrize("name", ["collection-plan.json", "abandoned-candidate.json"])
def test_nested_records_cannot_cross_protocol_versions(name):
    path = Path(__file__).parent / "fixtures/protocol/v3" / name
    payload = json.loads(path.read_bytes())
    nested = payload["targets"][0] if "targets" in payload else payload["manifest"]
    nested["format_version"] = 2
    decode, _ = protocol_codec(payload["kind"])
    with pytest.raises(StoreCorruptionError):
        decode(json.dumps(payload).encode(), expected_version=3)


@pytest.mark.parametrize("version", [1, 2])
def test_old_protocol_does_not_reinterpret_opaque_ids(version):
    fixture = Path(__file__).parent / f"fixtures/protocol/v{version}/candidate.json"
    payload = json.loads(fixture.read_bytes())
    payload["publication_id"] = "batch:table"
    with pytest.raises(StoreCorruptionError):
        decode_candidate(json.dumps(payload).encode(), expected_version=version)


def test_v2_retained_history_remains_readable_without_changing_records(tmp_path):
    from asterstore import Reference, ReferenceRecord, RepositoryUpgradeRequiredError
    from asterstore.metadata import encode_reference, encode_repository
    from asterstore.storage import dataset_directory, history_path, reference_path

    fixtures = Path(__file__).parent / "fixtures/protocol/v2"
    older = (fixtures / "current.json").read_bytes()
    current = json.loads(older)
    current["publication"]["publication_id"] = "newer"
    current["generation"] = 2
    dataset_id = current["publication"]["dataset"]["dataset_id"]
    directory = dataset_directory(tmp_path, dataset_id)
    directory.mkdir(parents=True)
    (directory / "current.json").write_text(json.dumps(current))
    history = history_path(tmp_path, dataset_id, "batch-001")
    history.parent.mkdir()
    history.write_bytes(older)
    marker = tmp_path / ".asterstore/format.json"
    marker.write_bytes(encode_repository(format_version=2))
    ref = ReferenceRecord(Reference("run", dataset_id, "batch-001"), 1, "active", "current", 2)
    path = reference_path(tmp_path, "run")
    path.parent.mkdir()
    path.write_bytes(encode_reference(ref))
    repo = Repository(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    assert repo.retention.get("run") == ref
    held = repo.retention.open("run", expected_revision=1)
    assert held.binding.publication == decode_publication(older).publication
    with pytest.raises(RepositoryUpgradeRequiredError):
        repo.retention.release("run", expected_revision=1)
    assert {p: p.read_bytes() for p in tmp_path.rglob("*.json")} == before


def test_identity_hash_paths_preserve_case_and_unicode_normalization(tmp_path):
    from asterstore.storage import dataset_directory

    names = ("Prices", "prices", "é", "e\u0301")
    repo = Repository(tmp_path)
    for name in names:
        with repo.prepare(Dataset(name), publication_id=name, durable=False) as candidate:
            candidate.commit()
    assert len({dataset_directory(tmp_path, name) for name in names}) == len(names)
    assert [repo.open(name).publication.publication_id for name in names] == list(names)


def v4_codec(kind):
    from asterstore.metadata import protocol as p

    return {
        "repository": (p.decode_store, p.encode_store),
        "publication": (p.decode_declaration_record, p.encode_declaration_record),
        "object": (p.decode_object_record, p.encode_object_record),
    }[kind]


def v4_schema():
    from importlib.resources import files

    from jsonschema import Draft202012Validator

    schema = json.loads(files("asterstore.metadata").joinpath("schemas/v4.json").read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


@pytest.mark.parametrize(
    "fixture",
    sorted((Path(__file__).parent / "fixtures/protocol/v4").glob("*.json")),
    ids=lambda p: p.stem,
)
def test_v4_golden_roundtrip_and_schema(fixture):
    data = fixture.read_bytes()
    payload = json.loads(data)
    v4_schema().validate(payload)
    decode, encode = v4_codec(payload["kind"])
    assert encode(decode(data)) == data


@pytest.mark.parametrize(
    "fixture",
    sorted((Path(__file__).parent / "fixtures/protocol/v4/invalid").glob("*.json")),
    ids=lambda p: p.stem,
)
def test_v4_negative_fixtures(fixture):
    from jsonschema import ValidationError

    payload = json.loads(fixture.read_bytes())
    decode, _ = v4_codec(payload["kind"])
    with pytest.raises(StoreCorruptionError):
        decode(fixture.read_bytes())
    # Membership closure is a codec semantic check, not structural JSON Schema.
    if fixture.stem != "unclosed-membership":
        with pytest.raises(ValidationError):
            v4_schema().validate(payload)


@pytest.mark.parametrize("name", ["repository", "publication", "object"])
@pytest.mark.parametrize("damage", ["version", "kind", "extra", "duplicate", "utf8"])
def test_v4_codec_rejects_mixed_versions_and_noncanonical_records(name, damage):
    payload = json.loads((Path(__file__).parent / f"fixtures/protocol/v4/{name}.json").read_bytes())
    if damage == "version":
        payload["format_version"] = 3
    elif damage == "kind":
        payload["kind"] = "unknown"
    elif damage == "extra":
        payload["unexpected"] = True
    data = json.dumps(payload).encode()
    if damage == "duplicate":
        data = b'{"format_version":4,' + data[1:]
    elif damage == "utf8":
        data = b"\xff"
    decode, _ = v4_codec(name)
    with pytest.raises(StoreCorruptionError):
        decode(data)
