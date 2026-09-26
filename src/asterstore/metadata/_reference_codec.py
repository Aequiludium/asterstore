"""Strict versioned reference serialization."""

from typing import Literal, cast

from asterstore.errors import StoreCorruptionError

from ._codec import _dump, _integer, _load, _text
from ._models import Reference
from ._retention import ReferenceRecord
from ._versions import CURRENT_FORMAT_VERSION


def encode_reference(record: ReferenceRecord) -> bytes:
    return _dump(
        {
            "format_version": record.format_version,
            "kind": "retention_reference",
            "name": record.reference.name,
            "dataset_id": record.reference.dataset_id,
            "publication_id": record.reference.publication_id,
            "revision": record.revision,
            "state": record.state,
            "selection": record.selection,
        }
    )


def decode_reference(
    data: bytes, *, expected_version: int = CURRENT_FORMAT_VERSION
) -> ReferenceRecord:
    try:
        record = _load(
            data,
            "retention_reference",
            {
                "name",
                "dataset_id",
                "publication_id",
                "revision",
                "state",
                "selection",
            },
            expected_version=expected_version,
        )
        return ReferenceRecord(
            Reference(
                _text(record["name"]), _text(record["dataset_id"]), _text(record["publication_id"])
            ),
            _integer(record["revision"]),
            cast(Literal["active", "released"], _text(record["state"])),
            cast(Literal["current", "publication"], _text(record["selection"])),
            format_version=_integer(record["format_version"]),
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise StoreCorruptionError(f"invalid reference record: {exc}") from exc
