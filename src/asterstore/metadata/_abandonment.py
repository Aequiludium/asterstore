"""Permanent candidate abandonment and exact private-file cleanup journals."""

import json
from dataclasses import dataclass

from asterstore.errors import InvalidDeclarationError, StoreCorruptionError

from ._codec import (
    _dump,
    _integer,
    _list,
    _load,
    _mapping,
    _text,
    decode_candidate,
    encode_candidate,
)
from ._models import ObjectRef
from ._records import CandidateManifest, validate_candidate_id
from ._retention import PreviewIssue
from ._versions import CURRENT_FORMAT_VERSION, validate_governance_version


@dataclass(frozen=True, slots=True)
class AbandonedCandidate:
    manifest: CandidateManifest

    def __post_init__(self) -> None:
        if not isinstance(self.manifest, CandidateManifest) or self.manifest.format_version not in (
            2,
            3,
        ):
            raise InvalidDeclarationError("abandonment requires a v2/v3 candidate")


def validate_private_key(candidate_id: str, key: str) -> None:
    validate_candidate_id(candidate_id)
    ObjectRef(key)
    prefixes = (
        f".asterstore/candidates/{candidate_id}/files/",
        f".asterstore/objects/{candidate_id}/",
    )
    if not any(key.startswith(prefix) for prefix in prefixes):
        raise InvalidDeclarationError("cleanup key is outside the candidate's private directories")


@dataclass(frozen=True, slots=True)
class CleanupPlan:
    candidate_id: str
    keys: tuple[str, ...]
    format_version: int = CURRENT_FORMAT_VERSION

    def __post_init__(self) -> None:
        validate_governance_version(self.format_version)
        validate_candidate_id(self.candidate_id)
        if len(set(self.keys)) != len(self.keys):
            raise InvalidDeclarationError("duplicate cleanup key")
        for key in self.keys:
            validate_private_key(self.candidate_id, key)


@dataclass(frozen=True, slots=True)
class CleanupProgress:
    candidate_id: str
    complete: bool = False
    deleted: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    issues: tuple[PreviewIssue, ...] = ()
    format_version: int = CURRENT_FORMAT_VERSION

    def __post_init__(self) -> None:
        validate_governance_version(self.format_version)
        validate_candidate_id(self.candidate_id)
        if type(self.complete) is not bool or (self.complete and self.issues):
            raise InvalidDeclarationError("invalid cleanup completion state")
        keys = self.deleted + self.missing
        if len(set(keys)) != len(keys):
            raise InvalidDeclarationError("duplicate cleanup progress key")
        for key in keys:
            validate_private_key(self.candidate_id, key)


def encode_abandoned(record: AbandonedCandidate) -> bytes:
    return _dump(
        {
            "format_version": record.manifest.format_version,
            "kind": "abandoned_candidate",
            "manifest": json.loads(encode_candidate(record.manifest)),
        }
    )


def decode_candidate_state(
    data: bytes, *, expected_version: int = CURRENT_FORMAT_VERSION
) -> CandidateManifest | AbandonedCandidate:
    try:
        return decode_candidate(data, expected_version=expected_version)
    except StoreCorruptionError:
        if expected_version == 1:
            raise
    try:
        value = _load(data, "abandoned_candidate", {"manifest"}, expected_version=expected_version)
        if not isinstance(value["manifest"], dict):
            raise ValueError("expected original candidate manifest")
        return AbandonedCandidate(
            decode_candidate(_dump(value["manifest"]), expected_version=expected_version)
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise StoreCorruptionError(f"invalid abandoned candidate: {exc}") from exc


def encode_cleanup_plan(record: CleanupPlan) -> bytes:
    return _dump(
        {
            "format_version": record.format_version,
            "kind": "candidate_cleanup_plan",
            "candidate_id": record.candidate_id,
            "keys": record.keys,
        }
    )


def decode_cleanup_plan(
    data: bytes, *, expected_version: int = CURRENT_FORMAT_VERSION
) -> CleanupPlan:
    try:
        value = _load(
            data,
            "candidate_cleanup_plan",
            {"candidate_id", "keys"},
            expected_version=expected_version,
        )
        return CleanupPlan(
            _text(value["candidate_id"]),
            tuple(_text(x) for x in _list(value["keys"])),
            format_version=_integer(value["format_version"]),
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise StoreCorruptionError(f"invalid cleanup plan: {exc}") from exc


def encode_cleanup_progress(record: CleanupProgress) -> bytes:
    return _dump(
        {
            "format_version": record.format_version,
            "kind": "candidate_cleanup_progress",
            "candidate_id": record.candidate_id,
            "complete": record.complete,
            "deleted": record.deleted,
            "missing": record.missing,
            "issues": [{"path": x.path, "message": x.message} for x in record.issues],
        }
    )


def decode_cleanup_progress(
    data: bytes, *, expected_version: int = CURRENT_FORMAT_VERSION
) -> CleanupProgress:
    try:
        value = _load(
            data,
            "candidate_cleanup_progress",
            {"candidate_id", "complete", "deleted", "missing", "issues"},
            expected_version=expected_version,
        )
        if type(value["complete"]) is not bool:
            raise ValueError("complete must be a boolean")
        issues = []
        for item in _list(value["issues"]):
            entry = _mapping(item, {"path", "message"})
            issues.append(PreviewIssue(_text(entry["path"]), _text(entry["message"])))
        return CleanupProgress(
            _text(value["candidate_id"]),
            value["complete"],
            tuple(_text(x) for x in _list(value["deleted"])),
            tuple(_text(x) for x in _list(value["missing"])),
            tuple(issues),
            format_version=_integer(value["format_version"]),
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise StoreCorruptionError(f"invalid cleanup progress: {exc}") from exc
