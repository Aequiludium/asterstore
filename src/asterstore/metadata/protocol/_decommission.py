"""Permanent dataset heads and fixed batch decommission authority."""

import json
from dataclasses import dataclass

from asterstore.errors import StoreCorruptionError

from ._codec import decode_declaration_record, encode_declaration_record
from ._json import array, dump, load, record, text
from ._models import DeclarationRecord, identity


@dataclass(frozen=True, slots=True)
class DatasetTombstone:
    operation_id: str
    publication: DeclarationRecord

    def __post_init__(self) -> None:
        identity(self.operation_id, "decommission operation_id")


@dataclass(frozen=True, slots=True)
class DecommissionPlan:
    store_id: str
    operation_id: str
    targets: tuple[DeclarationRecord, ...]

    def __post_init__(self) -> None:
        identity(self.store_id, "store_id")
        identity(self.operation_id, "operation_id")
        ids = [p.declaration.dataset_id for p in self.targets]
        if not ids or ids != sorted(set(ids)):
            raise ValueError("decommission targets must be nonempty, unique and sorted")
        if any(p.store_id != self.store_id for p in self.targets):
            raise ValueError("decommission target belongs to another store")


def encode_tombstone(value: DatasetTombstone) -> bytes:
    return dump(
        {
            "format_version": 1,
            "kind": "dataset_tombstone",
            "operation_id": value.operation_id,
            "publication": load(encode_declaration_record(value.publication)),
        }
    )


def decode_tombstone(data: bytes) -> DatasetTombstone:
    try:
        value = record(data, "dataset_tombstone", {"operation_id", "publication"})
        return DatasetTombstone(
            text(value["operation_id"]),
            decode_declaration_record(json.dumps(value["publication"]).encode()),
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise StoreCorruptionError(f"invalid dataset tombstone: {exc}") from exc


def encode_decommission_plan(value: DecommissionPlan) -> bytes:
    return dump(
        {
            "format_version": 1,
            "kind": "decommission_plan",
            "store_id": value.store_id,
            "operation_id": value.operation_id,
            "targets": [load(encode_declaration_record(p)) for p in value.targets],
        }
    )


def decode_decommission_plan(data: bytes) -> DecommissionPlan:
    try:
        value = record(data, "decommission_plan", {"store_id", "operation_id", "targets"})
        return DecommissionPlan(
            text(value["store_id"]),
            text(value["operation_id"]),
            tuple(
                decode_declaration_record(json.dumps(p).encode()) for p in array(value["targets"])
            ),
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise StoreCorruptionError(f"invalid decommission plan: {exc}") from exc


def decode_head(data: bytes) -> DeclarationRecord | DatasetTombstone:
    try:
        return decode_declaration_record(data)
    except StoreCorruptionError:
        return decode_tombstone(data)
