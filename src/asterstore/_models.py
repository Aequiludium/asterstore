"""Small immutable models and strict wire decoding; no filesystem access."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .errors import StoreCorruptionError, UnknownMemberError

FORMAT_VERSION = 3


def identifier(value: str) -> str:
    if not isinstance(value, str) or not value or not value.isprintable():
        raise ValueError("identifier must be nonempty printable text")
    if len(value.encode("utf-8")) > 4096:
        raise ValueError("identifier is too long")
    return value


def unique_id(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{32}", value) is None:
        raise ValueError("expected a 32-character hexadecimal ID")
    return value


def relative(value: str) -> str:
    identifier(value)
    if "\\" in value or any(x in {"", ".", ".."} for x in value.split("/")):
        raise ValueError("expected a canonical relative path")
    return value


def revision(value: int, *, zero: bool = False) -> int:
    if type(value) is not int or not (0 if zero else 1) <= value <= 2**63 - 1:
        raise ValueError("invalid revision")
    return value


@dataclass(frozen=True, slots=True)
class Head:
    dataset_id: str
    revision: int
    current: str
    rollback: str | None = None

    def __post_init__(self) -> None:
        identifier(self.dataset_id)
        revision(self.revision)
        unique_id(self.current)
        if self.rollback is not None:
            unique_id(self.rollback)
            if self.rollback == self.current:
                raise ValueError("current and rollback must differ")


@dataclass(frozen=True, slots=True)
class Member:
    key: str
    path: str

    def __post_init__(self) -> None:
        identifier(self.key)
        relative(self.path[1:] if self.path.startswith("/") else self.path)


@dataclass(frozen=True, slots=True)
class Manifest:
    dataset_id: str
    version_id: str
    members: tuple[Member, ...]

    def __post_init__(self) -> None:
        identifier(self.dataset_id)
        unique_id(self.version_id)
        object.__setattr__(self, "members", tuple(self.members))
        if any(not isinstance(m, Member) for m in self.members):
            raise ValueError("invalid member")
        if len({m.key for m in self.members}) != len(self.members):
            raise ValueError("duplicate member key")
        paths = {m.path for m in self.members}
        for path in paths:
            parts = path.split("/")
            if any("/".join(parts[:i]) in paths for i in range(1, len(parts))):
                raise ValueError("file and directory paths overlap")


@dataclass(frozen=True, slots=True)
class Binding:
    """A fixed file selection; validity ends at the next coordinated cleanup.

    Candidates have revision=None and an explicit base_revision. Published
    bindings carry the revision of the head observed by open().
    """

    root: Path
    manifest: Manifest
    revision: int | None
    base_revision: int
    _paths: Mapping[str, Path] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_paths",
            MappingProxyType({m.key: self.root / m.path for m in self.manifest.members}),
        )

    @property
    def version_id(self) -> str:
        return self.manifest.version_id

    @property
    def dataset_id(self) -> str:
        return self.manifest.dataset_id

    def files(self, *, keys: Sequence[str] | None = None) -> tuple[Path, ...]:
        if isinstance(keys, str):
            raise TypeError("keys must be a sequence, not a string")
        selected = self._paths if keys is None else keys
        try:
            return tuple(self._paths[key] for key in selected)
        except KeyError as exc:
            raise UnknownMemberError(exc.args[0]) from exc


@dataclass(frozen=True, slots=True)
class CleanResult:
    dataset_id: str
    dry_run: bool
    scanned_files: int = 0
    candidate_files: int = 0
    candidate_bytes: int = 0
    deleted_files: int = 0
    deleted_bytes: int = 0
    skipped: str | None = None


def encode(value: Head | Manifest) -> bytes:
    return dump(asdict(value))


def dump(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def load(data: bytes) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs)
        if not isinstance(value, dict):
            raise ValueError("expected a JSON object")
        return value
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise StoreCorruptionError(f"invalid JSON: {exc}") from exc


def decode_head(data: bytes) -> Head:
    try:
        value = load(data)
        if set(value) != {"dataset_id", "revision", "current", "rollback"}:
            raise ValueError("unexpected or missing head fields")
        return Head(**value)
    except (TypeError, ValueError) as exc:
        raise StoreCorruptionError(f"invalid head: {exc}") from exc


def decode_manifest(data: bytes) -> Manifest:
    try:
        value = load(data)
        members = value.pop("members")
        if not isinstance(members, list):
            raise ValueError("members must be an array")
        return Manifest(members=tuple(Member(**m) for m in members), **value)
    except (KeyError, TypeError, ValueError) as exc:
        raise StoreCorruptionError(f"invalid manifest: {exc}") from exc
