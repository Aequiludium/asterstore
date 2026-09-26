"""Strict JSON primitives shared only within the codec."""

import json
from typing import cast

from ._models import FORMAT_VERSION


def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in items:
        if name in result:
            raise ValueError(f"duplicate JSON key: {name}")
        result[name] = value
    return result


def constant(value: str) -> object:
    raise ValueError(f"non-finite constant: {value}")


def load(data: bytes) -> dict[str, object]:
    value = json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return cast(dict[str, object], value)


def dump(value: dict[str, object]) -> bytes:
    return (
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        + "\n"
    ).encode("utf-8")


def mapping(value: object, fields: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f"expected exactly these fields: {sorted(fields)}")
    return cast(dict[str, object], value)


def text(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("expected string")
    return value


def integer(value: object) -> int:
    if type(value) is not int:
        raise ValueError("expected integer, not bool or float")
    return value


def array(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError("expected array")
    return cast(list[object], value)


def record(data: bytes, kind: str, fields: set[str]) -> dict[str, object]:
    value = mapping(load(data), fields | {"format_version", "kind"})
    if integer(value["format_version"]) != FORMAT_VERSION or value["kind"] != kind:
        raise ValueError("unsupported record version or kind")
    return value
