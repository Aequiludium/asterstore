from dataclasses import FrozenInstanceError

import pytest

from asterstore import (
    Dataset,
    InvalidDeclarationError,
    ObjectRef,
    PhysicalHistory,
    Publication,
    Reference,
)


@pytest.mark.parametrize(
    "key",
    ["", "/absolute", "../outside", "a/../b", "a/./b", "a//b", "a/", "C:/x", "a\\b", "a\x00b"],
)
def test_reject_noncanonical_object_keys(key: str) -> None:
    with pytest.raises(InvalidDeclarationError):
        ObjectRef(key)


def test_publication_copies_membership_and_rejects_duplicates() -> None:
    objects = [ObjectRef("part-1.parquet")]
    publication = Publication(Dataset("data"), "p1", objects)
    objects.append(ObjectRef("part-2.parquet"))
    assert publication.objects == (ObjectRef("part-1.parquet"),)
    with pytest.raises(InvalidDeclarationError, match="unique"):
        Publication(publication.dataset, "p2", [objects[0], objects[0]])


def test_declarations_are_immutable_and_history_is_explicit() -> None:
    dataset = Dataset("prices")
    assert dataset.physical_history is PhysicalHistory.CURRENT_ONLY
    with pytest.raises(FrozenInstanceError):
        dataset.dataset_id = "changed"  # type: ignore[misc]
    historical = Dataset("historical", PhysicalHistory.VERSIONED)
    assert historical.physical_history is PhysicalHistory.VERSIONED


def test_named_references_are_independent_values() -> None:
    first = Reference("research/run-1", "prices", "p1")
    second = Reference("training/model-1", "prices", "p1")
    assert first != second
    assert first.publication_id == second.publication_id


@pytest.mark.parametrize(
    "identity",
    [
        "batch:table",
        "../source",
        "/absolute",
        "a\\b",
        " a ",
        "市场/价格",
        "é",
        "e\u0301",
        "a\u200db",
    ],
)
def test_logical_identifiers_preserve_source_text(identity):
    dataset = Dataset(identity)
    publication = Publication(dataset, identity, ())
    reference = Reference(identity, identity, identity)
    assert dataset.dataset_id == publication.publication_id == reference.name == identity


@pytest.mark.parametrize("identity", ["", None, 42, "x\n", "a\x00b", "a\x85b", "\ud800"])
def test_logical_identifiers_reject_nontext_controls_and_invalid_unicode(identity):
    for construct in (
        Dataset,
        lambda x: Publication(Dataset("data"), x, ()),
        lambda x: Reference(x, "data", "p1"),
    ):
        with pytest.raises(InvalidDeclarationError):
            construct(identity)


def test_v3_wire_length_is_utf8_bytes_and_legacy_lengths_stay_readable(tmp_path):
    from asterstore import Repository
    from asterstore.metadata import CandidateManifest, decode_candidate, encode_candidate

    boundary = "界" * 1365 + "a"
    assert len(boundary.encode()) == 4096
    record = CandidateManifest("a" * 32, Dataset(boundary), boundary, 0)
    assert decode_candidate(encode_candidate(record)) == record
    # In-memory declarations are format-neutral; the bound belongs to the v3 wire format.
    longer = Dataset(boundary + "a")
    legacy = CandidateManifest("a" * 32, longer, "p1", 0, format_version=2)
    assert decode_candidate(encode_candidate(legacy), expected_version=2) == legacy
    with pytest.raises(InvalidDeclarationError, match="4096"):
        Repository(tmp_path / "new").prepare(longer)
    assert not (tmp_path / "new").exists()
