"""Fixed retention, irreversible retirement and exact collection plan records."""

from dataclasses import dataclass
from typing import Literal, cast

from asterstore.errors import InvalidDeclarationError, StoreCorruptionError
from asterstore.metadata.capabilities import Management, RetentionScope

from ._codec import (
    decode_declaration_record,
    decode_managed_request,
    decode_object_record,
    encode_declaration_record,
    encode_managed_request,
    encode_object_record,
)
from ._json import array, dump, integer, load, mapping, record, text
from ._models import DeclarationRecord, ManagedRequest, ObjectRecord, generation, identity


@dataclass(frozen=True, slots=True)
class FixedRetention:
    store_id: str
    name: str
    dataset_id: str
    publication_id: str
    scope: RetentionScope
    revision: int = 1
    active: bool = True

    def __post_init__(self) -> None:
        for name in ("store_id", "name", "dataset_id", "publication_id"):
            identity(getattr(self, name), name)
        if not isinstance(self.scope, RetentionScope) or type(self.active) is not bool:
            raise InvalidDeclarationError("invalid retention scope or state")
        generation(self.revision, minimum=1)


@dataclass(frozen=True, slots=True)
class RetiredDeclaration:
    collection_id: str
    publication: DeclarationRecord

    def __post_init__(self) -> None:
        identity(self.collection_id, "collection_id")
        if self.publication.declaration.capabilities.management is not Management.MANAGED:
            raise InvalidDeclarationError("only managed object availability is retired")


@dataclass(frozen=True, slots=True)
class GovernancePlan:
    store_id: str
    operation_id: str
    retirements: tuple[DeclarationRecord, ...]
    objects: tuple[ObjectRecord, ...]

    def __post_init__(self) -> None:
        identity(self.store_id, "store_id")
        identity(self.operation_id, "operation_id")
        object.__setattr__(self, "retirements", tuple(self.retirements))
        object.__setattr__(self, "objects", tuple(self.objects))
        keys = [(p.declaration.dataset_id, p.declaration.publication_id) for p in self.retirements]
        if len(set(keys)) != len(keys) or len({o.object_id for o in self.objects}) != len(
            self.objects
        ):
            raise InvalidDeclarationError("duplicate collection targets")
        if any(
            p.store_id != self.store_id
            or p.declaration.capabilities.management is not Management.MANAGED
            for p in self.retirements
        ):
            raise InvalidDeclarationError("invalid retirement target")
        if any(o.store_id != self.store_id or o.creator_operation_id is None for o in self.objects):
            raise InvalidDeclarationError("collection requires owned objects")


@dataclass(frozen=True, slots=True)
class GovernanceProgress:
    store_id: str
    operation_id: str
    outcomes: tuple[tuple[str, Literal["deleted", "missing", "protected"]], ...] = ()
    complete: bool = False
    retired: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        identity(self.store_id, "store_id")
        identity(self.operation_id, "operation_id")
        object.__setattr__(self, "outcomes", tuple(tuple(x) for x in self.outcomes))
        object.__setattr__(self, "retired", tuple(tuple(x) for x in self.retired))
        if len(set(self.retired)) != len(self.retired):
            raise InvalidDeclarationError("duplicate retirement outcome")
        for dataset_id, publication_id in self.retired:
            identity(dataset_id, "dataset_id")
            identity(publication_id, "publication_id")
        if type(self.complete) is not bool:
            raise InvalidDeclarationError("complete must be boolean")
        if len({oid for oid, _ in self.outcomes}) != len(self.outcomes):
            raise InvalidDeclarationError("duplicate collection outcome")
        for oid, status in self.outcomes:
            identity(oid, "object_id")
            if status not in ("deleted", "missing", "protected"):
                raise InvalidDeclarationError("unknown collection outcome")

    def check_plan(self, plan: GovernancePlan) -> None:
        ids = {o.object_id for o in plan.objects}
        actual = {oid for oid, _ in self.outcomes}
        targets = {
            (p.declaration.dataset_id, p.declaration.publication_id) for p in plan.retirements
        }
        if not set(self.retired) <= targets:
            raise StoreCorruptionError("retirement outcome outside plan")
        if (
            self.store_id != plan.store_id
            or self.operation_id != plan.operation_id
            or not actual <= ids
            or (self.complete and actual != ids)
        ):
            raise StoreCorruptionError("collection progress does not match fixed plan")


def encode_retention(value: FixedRetention) -> bytes:
    return dump(
        {
            "format_version": 4,
            "kind": "fixed_retention",
            "store_id": value.store_id,
            "name": value.name,
            "dataset_id": value.dataset_id,
            "publication_id": value.publication_id,
            "scope": value.scope.value,
            "revision": value.revision,
            "active": value.active,
        }
    )


def decode_retention(data: bytes) -> FixedRetention:
    try:
        v = record(
            data,
            "fixed_retention",
            {"store_id", "name", "dataset_id", "publication_id", "scope", "revision", "active"},
        )
        if type(v["active"]) is not bool:
            raise ValueError("active must be boolean")
        return FixedRetention(
            text(v["store_id"]),
            text(v["name"]),
            text(v["dataset_id"]),
            text(v["publication_id"]),
            RetentionScope(text(v["scope"])),
            integer(v["revision"]),
            v["active"],
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise StoreCorruptionError(f"invalid fixed retention: {exc}") from exc


def encode_retired(value: RetiredDeclaration) -> bytes:
    return dump(
        {
            "format_version": 4,
            "kind": "retired_declaration",
            "collection_id": value.collection_id,
            "publication": load(encode_declaration_record(value.publication)),
        }
    )


def decode_retired(data: bytes) -> RetiredDeclaration:
    try:
        v = record(data, "retired_declaration", {"collection_id", "publication"})
        if not isinstance(v["publication"], dict):
            raise ValueError("expected publication record")
        return RetiredDeclaration(
            text(v["collection_id"]), decode_declaration_record(dump(v["publication"]))
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise StoreCorruptionError(f"invalid retirement: {exc}") from exc


def encode_governance_plan(value: GovernancePlan) -> bytes:
    return dump(
        {
            "format_version": 4,
            "kind": "governance_plan",
            "store_id": value.store_id,
            "operation_id": value.operation_id,
            "retirements": [load(encode_declaration_record(p)) for p in value.retirements],
            "objects": [load(encode_object_record(o)) for o in value.objects],
        }
    )


def decode_governance_plan(data: bytes) -> GovernancePlan:
    try:
        v = record(data, "governance_plan", {"store_id", "operation_id", "retirements", "objects"})
        publications = []
        for item in array(v["retirements"]):
            if not isinstance(item, dict):
                raise ValueError("expected publication record")
            publications.append(decode_declaration_record(dump(item)))
        objects = []
        for item in array(v["objects"]):
            if not isinstance(item, dict):
                raise ValueError("expected object record")
            objects.append(decode_object_record(dump(item)))
        return GovernancePlan(
            text(v["store_id"]), text(v["operation_id"]), tuple(publications), tuple(objects)
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise StoreCorruptionError(f"invalid collection plan: {exc}") from exc


def encode_governance_progress(value: GovernanceProgress) -> bytes:
    return dump(
        {
            "format_version": 4,
            "kind": "governance_progress",
            "store_id": value.store_id,
            "operation_id": value.operation_id,
            "complete": value.complete,
            "retired": [{"dataset_id": ds, "publication_id": pub} for ds, pub in value.retired],
            "outcomes": [{"object_id": oid, "status": state} for oid, state in value.outcomes],
        }
    )


def decode_governance_progress(data: bytes) -> GovernanceProgress:
    try:
        v = record(
            data,
            "governance_progress",
            {"store_id", "operation_id", "complete", "outcomes", "retired"},
        )
        if type(v["complete"]) is not bool:
            raise ValueError("complete must be boolean")
        outcomes = []
        for item in array(v["outcomes"]):
            pair = mapping(item, {"object_id", "status"})
            status = text(pair["status"])
            if status not in ("deleted", "missing", "protected"):
                raise ValueError("unknown outcome")
            outcomes.append(
                (text(pair["object_id"]), cast(Literal["deleted", "missing", "protected"], status))
            )
        retired = []
        for item in array(v["retired"]):
            target = mapping(item, {"dataset_id", "publication_id"})
            retired.append((text(target["dataset_id"]), text(target["publication_id"])))
        return GovernanceProgress(
            text(v["store_id"]),
            text(v["operation_id"]),
            tuple(outcomes),
            v["complete"],
            tuple(retired),
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise StoreCorruptionError(f"invalid collection progress: {exc}") from exc


def encode_managed_abandonment(request: ManagedRequest) -> bytes:
    return dump(
        {
            "format_version": 4,
            "kind": "managed_abandonment",
            "request": load(encode_managed_request(request)),
        }
    )


def decode_managed_abandonment(data: bytes) -> ManagedRequest:
    try:
        v = record(data, "managed_abandonment", {"request"})
        if not isinstance(v["request"], dict):
            raise ValueError("expected request record")
        return decode_managed_request(dump(v["request"]))
    except (ValueError, TypeError, KeyError) as exc:
        raise StoreCorruptionError(f"invalid abandonment: {exc}") from exc
