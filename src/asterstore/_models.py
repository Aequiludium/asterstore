"""Small control records; no inventory of historical data files."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Any

from .errors import StoreCorruptionError

FORMAT = 4


def relative(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or "\x00" in value
        or PurePosixPath(value).is_absolute()
        or any(p in ("", ".", "..") for p in value.split("/"))
        or ".asterstore" in value.split("/")
    ):
        raise ValueError(f"unsafe data path: {value!r}")
    return value


def identity(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{32}", value):
        raise StoreCorruptionError("invalid operation identity")
    return value


def json_copy(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise ValueError("metadata must be a JSON object with string keys")
    # Roundtrip also prevents caller mutation after preparation.
    result: dict[str, Any] = json.loads(json.dumps(value, allow_nan=False))
    return result


@dataclass(frozen=True)
class State:
    revision: int
    operation: str | None
    publication: str | None
    metadata: dict[str, Any]
    undo: str | None

    def payload(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def parse(cls, value: Any) -> State:
        try:
            if not isinstance(value, dict) or set(value) != {
                "revision",
                "operation",
                "publication",
                "metadata",
                "undo",
            }:
                raise ValueError("invalid state fields")
            if type(value["revision"]) is not int or value["revision"] < 0:
                raise ValueError("invalid revision")
            for field in ("operation", "publication", "undo"):
                if value[field] is not None:
                    identity(value[field])
            if (value["revision"] == 0) != (value["operation"] is None):
                raise ValueError("invalid initial state")
            if value["undo"] is not None and value["undo"] != value["publication"]:
                raise ValueError("invalid undo identity")
            return cls(**{**value, "metadata": json_copy(value["metadata"])})
        except (ValueError, TypeError, KeyError) as exc:
            raise StoreCorruptionError("invalid state record") from exc


EMPTY = State(0, None, None, {}, None)
