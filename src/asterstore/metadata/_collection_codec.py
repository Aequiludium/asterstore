"""Strict versioned collection/retirement serialization; no file access."""

from typing import Literal, cast

from asterstore.errors import StoreCorruptionError

from ._codec import (
    _dataset,
    _dataset_payload,
    _dump,
    _integer,
    _list,
    _load,
    _mapping,
    _text,
    decode_publication,
    encode_publication,
)
from ._collection import CollectionPlan, CollectionProgress, RetiredRecord
from ._models import ObjectRef
from ._records import PublishedRecord
from ._retention import CollectionPolicy, PreviewIssue
from ._versions import CURRENT_FORMAT_VERSION


def encode_retired(record: RetiredRecord) -> bytes:
    return _dump(
        {
            "format_version": record.format_version,
            "kind": "retired_publication",
            "dataset": _dataset_payload(record.dataset),
            "publication_id": record.publication_id,
            "generation": record.generation,
            "candidate_id": record.candidate_id,
            "collection_id": record.collection_id,
        }
    )


def decode_retired(data: bytes, *, expected_version: int = CURRENT_FORMAT_VERSION) -> RetiredRecord:
    try:
        value = _load(
            data,
            "retired_publication",
            {
                "dataset",
                "publication_id",
                "generation",
                "candidate_id",
                "collection_id",
            },
            expected_version=expected_version,
        )
        return RetiredRecord(
            _dataset(value["dataset"]),
            _text(value["publication_id"]),
            _integer(value["generation"]),
            _text(value["candidate_id"]),
            _text(value["collection_id"]),
            format_version=_integer(value["format_version"]),
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise StoreCorruptionError(f"invalid retirement record: {exc}") from exc


def decode_history(
    data: bytes, *, expected_version: int = CURRENT_FORMAT_VERSION
) -> PublishedRecord | RetiredRecord:
    try:
        return decode_publication(data, expected_version=expected_version)
    except StoreCorruptionError:
        if expected_version == 1:
            raise
        return decode_retired(data, expected_version=expected_version)


def encode_collection_plan(record: CollectionPlan) -> bytes:
    # Use the same publication encoding inside the plan, retaining complete evidence.
    import json

    return _dump(
        {
            "format_version": record.format_version,
            "kind": "collection_plan",
            "operation_id": record.operation_id,
            "policy": {"datasets": record.policy.datasets, "keep_last": record.policy.keep_last},
            "targets": [json.loads(encode_publication(target)) for target in record.targets],
            "objects": [obj.key for obj in record.objects],
        }
    )


def decode_collection_plan(
    data: bytes, *, expected_version: int = CURRENT_FORMAT_VERSION
) -> CollectionPlan:
    try:
        value = _load(
            data,
            "collection_plan",
            {"operation_id", "policy", "targets", "objects"},
            expected_version=expected_version,
        )
        policy = _mapping(value["policy"], {"datasets", "keep_last"})
        targets = []
        for target in _list(value["targets"]):
            if not isinstance(target, dict):
                raise ValueError("expected publication record")
            targets.append(decode_publication(_dump(target), expected_version=expected_version))
        return CollectionPlan(
            _text(value["operation_id"]),
            CollectionPolicy(
                tuple(_text(x) for x in _list(policy["datasets"])),
                keep_last=_integer(policy["keep_last"]),
            ),
            tuple(targets),
            tuple(ObjectRef(_text(x)) for x in _list(value["objects"])),
            format_version=_integer(value["format_version"]),
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise StoreCorruptionError(f"invalid collection plan: {exc}") from exc


def encode_collection_progress(record: CollectionProgress) -> bytes:
    return _dump(
        {
            "format_version": record.format_version,
            "kind": "collection_progress",
            "operation_id": record.operation_id,
            "state": record.state,
            "retired": record.retired,
            "deleted": record.deleted,
            "missing": record.missing,
            "protected": record.protected,
            "issues": [{"path": item.path, "message": item.message} for item in record.issues],
        }
    )


def decode_collection_progress(
    data: bytes, *, expected_version: int = CURRENT_FORMAT_VERSION
) -> CollectionProgress:
    try:
        value = _load(
            data,
            "collection_progress",
            {
                "operation_id",
                "state",
                "retired",
                "deleted",
                "missing",
                "protected",
                "issues",
            },
            expected_version=expected_version,
        )
        groups = [
            tuple(_text(x) for x in _list(value[name]))
            for name in ("retired", "deleted", "missing", "protected")
        ]
        issues = []
        for item in _list(value["issues"]):
            issue = _mapping(item, {"path", "message"})
            issues.append(PreviewIssue(_text(issue["path"]), _text(issue["message"])))
        return CollectionProgress(
            _text(value["operation_id"]),
            cast(Literal["planned", "running", "blocked", "complete"], _text(value["state"])),
            groups[0],
            groups[1],
            groups[2],
            groups[3],
            tuple(issues),
            format_version=_integer(value["format_version"]),
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise StoreCorruptionError(f"invalid collection progress: {exc}") from exc
