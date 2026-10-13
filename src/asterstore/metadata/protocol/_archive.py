"""Control archive wire records. References never target data or current records."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TypedDict, cast

from asterstore.errors import StoreCorruptionError

from ._json import load
from ._models import identity


class ArchiveReference(TypedDict):
    format_version: int
    kind: str
    archive: str
    offset: int
    length: int
    size: int
    sha256: str


def encode_reference(value: ArchiveReference) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def decode_reference(data: bytes) -> ArchiveReference:
    try:
        value = load(data)
        if set(value) != {
            "format_version",
            "kind",
            "archive",
            "offset",
            "length",
            "size",
            "sha256",
        }:
            raise ValueError("invalid reference fields")
        if (
            type(value["format_version"]) is not int
            or value["format_version"] != 1
            or value["kind"] != "archive_reference"
        ):
            raise ValueError("invalid reference version or kind")
        for name in ("archive", "sha256"):
            digest = value[name]
            if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
                raise ValueError("invalid reference digest")
        for name in ("offset", "length", "size"):
            field = value[name]
            if type(field) is not int or not 0 <= field <= 2**63 - 1:
                raise ValueError("invalid reference extent")
        if not value["length"] or not value["size"]:
            raise ValueError("empty archive block")
        return cast(ArchiveReference, value)
    except (ValueError, TypeError) as exc:
        raise StoreCorruptionError(f"invalid archive reference: {exc}") from exc


def eligible(path: str) -> bool:
    digest = r"[0-9a-f]{64}"
    return (
        re.fullmatch(
            rf"(?:registrations/{digest}\.json|datasets/{digest}/history/{digest}\.json|"
            rf"managed-candidates/{digest}/(?:request|sealed|protection|abandoned|cleanup-plan|"
            rf"cleanup-progress)\.json|retired/{digest}/{digest}\.json|"
            rf"collections/{digest}/(?:plan|progress)\.json)",
            path,
        )
        is not None
    )


@dataclass(frozen=True)
class ControlCheckpoint:
    store_id: str
    operation_id: str
    records: dict[str, ArchiveReference]


def encode_checkpoint(value: ControlCheckpoint) -> bytes:
    return json.dumps(
        {
            "format_version": 1,
            "kind": "control_checkpoint",
            "store_id": value.store_id,
            "operation_id": value.operation_id,
            "records": value.records,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def decode_checkpoint(data: bytes) -> ControlCheckpoint:
    try:
        plan = load(data)
        if (
            set(plan) != {"format_version", "kind", "store_id", "operation_id", "records"}
            or type(plan["format_version"]) is not int
            or plan["format_version"] != 1
            or plan["kind"] != "control_checkpoint"
            or not isinstance(plan["records"], dict)
        ):
            raise ValueError("checkpoint identity or fields differ")
        store_id, operation_id = plan["store_id"], plan["operation_id"]
        if not isinstance(store_id, str) or not isinstance(operation_id, str):
            raise ValueError("invalid checkpoint identity")
        identity(store_id, "store_id")
        identity(operation_id, "operation_id")
        records = {}
        for name, payload in plan["records"].items():
            if not eligible(name):
                raise ValueError("checkpoint targets a forbidden path")
            value = decode_reference(json.dumps(payload).encode())
            records[name] = value
        if not records or len({v["archive"] for v in records.values()}) != 1:
            raise ValueError("checkpoint must reference exactly one nonempty archive")
        return ControlCheckpoint(store_id, operation_id, records)
    except (ValueError, TypeError, KeyError) as exc:
        raise StoreCorruptionError(f"invalid compaction checkpoint: {exc}") from exc
