"""Strict UTF-8 JSON control records. No data files or filesystem access."""

import json
from typing import cast

from asterstore.errors import StoreCorruptionError

from ._models import Dataset, ObjectRef, PhysicalHistory, Publication
from ._records import CandidateManifest, PublishedRecord, ReusedObject
from ._versions import CURRENT_FORMAT_VERSION, validate_format_version


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _constant(value: str) -> object:
    raise ValueError(f"non-finite JSON constant: {value}")


def _mapping(value: object, fields: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f"expected exactly these fields: {sorted(fields)}")
    return cast(dict[str, object], value)


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("expected a string")
    return value


def _integer(value: object) -> int:
    if type(value) is not int:
        raise ValueError("expected an integer, not a boolean or float")
    return value


def _list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError("expected a list")
    return cast(list[object], value)


def _load(
    data: bytes,
    kind: str,
    fields: set[str],
    *,
    v2_fields: set[str] | None = None,
    expected_version: int | None = None,
) -> dict[str, object]:
    value = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
    if not isinstance(value, dict):
        raise ValueError("expected a record")
    version = _integer(value.get("format_version"))
    validate_format_version(version)
    if expected_version is not None and version != expected_version:
        raise ValueError("record version differs from repository format")
    extra = (v2_fields or set()) if version >= 2 else set()
    record = _mapping(value, fields | extra | {"format_version", "kind"})
    if record["kind"] != kind:
        raise ValueError(f"unsupported {kind} record kind")
    return record


def _dump(record: dict[str, object]) -> bytes:
    return (
        json.dumps(
            record, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        )
        + "\n"
    ).encode("utf-8")


def _dataset(value: object) -> Dataset:
    record = _mapping(value, {"dataset_id", "physical_history"})
    return Dataset(_text(record["dataset_id"]), PhysicalHistory(_text(record["physical_history"])))


def _dataset_payload(dataset: Dataset) -> dict[str, object]:
    return {"dataset_id": dataset.dataset_id, "physical_history": dataset.physical_history.value}


def encode_repository(*, format_version: int = CURRENT_FORMAT_VERSION) -> bytes:
    validate_format_version(format_version)
    return _dump({"format_version": format_version, "kind": "repository"})


def decode_repository(data: bytes) -> int:
    try:
        return _integer(_load(data, "repository", set())["format_version"])
    except (ValueError, TypeError, KeyError) as exc:
        raise StoreCorruptionError(f"invalid repository marker: {exc}") from exc


def encode_candidate(record: CandidateManifest) -> bytes:
    return _dump(
        {
            "format_version": record.format_version,
            "kind": "candidate",
            "candidate_id": record.candidate_id,
            "dataset": _dataset_payload(record.dataset),
            "publication_id": record.publication_id,
            "expected_generation": record.expected_generation,
            "keys": record.keys,
            **(
                {
                    "reused_objects": [
                        {"key": item.key, "publication_id": item.publication_id}
                        for item in record.reused_objects
                    ]
                }
                if record.format_version >= 2
                else {}
            ),
        }
    )


def decode_candidate(data: bytes, *, expected_version: int | None = None) -> CandidateManifest:
    try:
        record = _load(
            data,
            "candidate",
            {"candidate_id", "dataset", "publication_id", "expected_generation", "keys"},
            v2_fields={"reused_objects"},
            expected_version=expected_version,
        )
        reused = []
        for value in _list(record.get("reused_objects", [])):
            entry = _mapping(value, {"key", "publication_id"})
            reused.append(ReusedObject(_text(entry["key"]), _text(entry["publication_id"])))
        keys = record["keys"]
        return CandidateManifest(
            _text(record["candidate_id"]),
            _dataset(record["dataset"]),
            _text(record["publication_id"]),
            _integer(record["expected_generation"]),
            None if keys is None else tuple(_text(key) for key in _list(keys)),
            format_version=_integer(record["format_version"]),
            reused_objects=tuple(reused),
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise StoreCorruptionError(f"invalid candidate manifest: {exc}") from exc


def encode_publication(record: PublishedRecord) -> bytes:
    publication = record.publication
    return _dump(
        {
            "format_version": record.format_version,
            "kind": "publication",
            "generation": record.generation,
            "candidate_id": record.candidate_id,
            "publication": {
                "dataset": _dataset_payload(publication.dataset),
                "publication_id": publication.publication_id,
                "objects": [{"key": obj.key} for obj in publication.objects],
            },
        }
    )


def decode_publication(data: bytes, *, expected_version: int | None = None) -> PublishedRecord:
    try:
        record = _load(
            data,
            "publication",
            {"generation", "candidate_id", "publication"},
            expected_version=expected_version,
        )
        publication = _mapping(record["publication"], {"dataset", "publication_id", "objects"})
        objects = [
            ObjectRef(_text(_mapping(obj, {"key"})["key"])) for obj in _list(publication["objects"])
        ]
        return PublishedRecord(
            _integer(record["generation"]),
            _text(record["candidate_id"]),
            Publication(
                _dataset(publication["dataset"]), _text(publication["publication_id"]), objects
            ),
            format_version=_integer(record["format_version"]),
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise StoreCorruptionError(f"invalid publication record: {exc}") from exc
