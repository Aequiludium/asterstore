"""Exact, one-shot cleanup records for an explicitly abandoned managed candidate."""

from dataclasses import dataclass
from typing import Literal, cast

from asterstore.errors import InvalidDeclarationError, StoreCorruptionError
from asterstore.metadata.identity import validate_object_key

from ._codec import decode_managed_request, encode_managed_request
from ._json import array, dump, load, mapping, record, text
from ._models import FORMAT_VERSION, ManagedRequest, identity

CleanupLocation = Literal["private", "installed"]
CleanupOutcome = Literal["deleted", "missing"]


@dataclass(frozen=True, slots=True)
class ManagedCleanupPlan:
    request: ManagedRequest
    location: CleanupLocation
    files: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.location not in ("private", "installed"):
            raise InvalidDeclarationError("unknown cleanup location")
        object.__setattr__(self, "files", tuple(self.files))
        paths = set(self.files)
        if len(paths) != len(self.files):
            raise InvalidDeclarationError("duplicate cleanup file")
        for path in self.files:
            validate_object_key(path)
            parts = path.split("/")
            if any("/".join(parts[:i]) in paths for i in range(1, len(parts))):
                raise InvalidDeclarationError("cleanup file is another file's ancestor")


@dataclass(frozen=True, slots=True)
class ManagedCleanupProgress:
    store_id: str
    operation_id: str
    outcomes: tuple[tuple[str, CleanupOutcome], ...] = ()
    complete: bool = False

    def __post_init__(self) -> None:
        identity(self.store_id, "store_id")
        identity(self.operation_id, "operation_id")
        object.__setattr__(self, "outcomes", tuple(tuple(x) for x in self.outcomes))
        if type(self.complete) is not bool:
            raise InvalidDeclarationError("complete must be boolean")
        if len({p for p, _ in self.outcomes}) != len(self.outcomes):
            raise InvalidDeclarationError("duplicate cleanup outcome")
        for path, state in self.outcomes:
            validate_object_key(path)
            if state not in ("deleted", "missing"):
                raise InvalidDeclarationError("unknown cleanup outcome")

    def check_plan(self, plan: ManagedCleanupPlan) -> None:
        actual = {p for p, _ in self.outcomes}
        if (
            self.store_id != plan.request.store_id
            or self.operation_id != plan.request.operation_id
            or not actual <= set(plan.files)
            or (self.complete and actual != set(plan.files))
        ):
            raise StoreCorruptionError("cleanup progress does not match fixed plan")


def encode_managed_cleanup_plan(value: ManagedCleanupPlan) -> bytes:
    return dump(
        {
            "format_version": FORMAT_VERSION,
            "kind": "managed_cleanup_plan",
            "request": load(encode_managed_request(value.request)),
            "location": value.location,
            "files": list(value.files),
        }
    )


def decode_managed_cleanup_plan(data: bytes) -> ManagedCleanupPlan:
    try:
        v = record(data, "managed_cleanup_plan", {"request", "location", "files"})
        if not isinstance(v["request"], dict):
            raise ValueError("expected managed request")
        location = text(v["location"])
        if location not in ("private", "installed"):
            raise ValueError("unknown cleanup location")
        return ManagedCleanupPlan(
            decode_managed_request(dump(v["request"])),
            cast(CleanupLocation, location),
            tuple(text(p) for p in array(v["files"])),
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise StoreCorruptionError(f"invalid managed cleanup plan: {exc}") from exc


def encode_managed_cleanup_progress(value: ManagedCleanupProgress) -> bytes:
    return dump(
        {
            "format_version": FORMAT_VERSION,
            "kind": "managed_cleanup_progress",
            "store_id": value.store_id,
            "operation_id": value.operation_id,
            "outcomes": [{"path": p, "status": s} for p, s in value.outcomes],
            "complete": value.complete,
        }
    )


def decode_managed_cleanup_progress(data: bytes) -> ManagedCleanupProgress:
    try:
        v = record(
            data,
            "managed_cleanup_progress",
            {
                "store_id",
                "operation_id",
                "outcomes",
                "complete",
            },
        )
        if type(v["complete"]) is not bool:
            raise ValueError("complete must be boolean")
        outcomes = []
        for item in array(v["outcomes"]):
            pair = mapping(item, {"path", "status"})
            status = text(pair["status"])
            if status not in ("deleted", "missing"):
                raise ValueError("unknown cleanup outcome")
            outcomes.append((text(pair["path"]), cast(CleanupOutcome, status)))
        return ManagedCleanupProgress(
            text(v["store_id"]),
            text(v["operation_id"]),
            tuple(outcomes),
            v["complete"],
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise StoreCorruptionError(f"invalid managed cleanup progress: {exc}") from exc
