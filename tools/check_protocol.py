"""Validate the current wire schema and examples; no historical API freeze."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import jsonschema  # type: ignore[import-untyped]  # Development-only schema validator.

from asterstore import StoreCorruptionError
from asterstore.metadata import protocol

CODECS: dict[str, tuple[Callable[[bytes], Any], Callable[[Any], bytes]]] = {
    "store": (protocol.decode_store, protocol.encode_store),
    "publication": (protocol.decode_declaration_record, protocol.encode_declaration_record),
    "object": (protocol.decode_object_record, protocol.encode_object_record),
    "managed_request": (protocol.decode_managed_request, protocol.encode_managed_request),
    "fixed_retention": (protocol.decode_retention, protocol.encode_retention),
    "retired_declaration": (protocol.decode_retired, protocol.encode_retired),
    "governance_plan": (protocol.decode_governance_plan, protocol.encode_governance_plan),
    "governance_progress": (
        protocol.decode_governance_progress,
        protocol.encode_governance_progress,
    ),
    "managed_abandonment": (
        protocol.decode_managed_abandonment,
        protocol.encode_managed_abandonment,
    ),
    "managed_cleanup_plan": (
        protocol.decode_managed_cleanup_plan,
        protocol.encode_managed_cleanup_plan,
    ),
    "managed_cleanup_progress": (
        protocol.decode_managed_cleanup_progress,
        protocol.encode_managed_cleanup_progress,
    ),
}


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    schema = json.loads((root / "src/asterstore/metadata/schemas/store.json").read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(schema)
    kinds: set[str] = set()
    for path in sorted((root / "tests/fixtures/protocol").rglob("*.json")):
        data = path.read_bytes()
        value = json.loads(data)
        if "invalid" in path.parts:
            try:
                CODECS[value["kind"]][0](data)
            except (ValueError, StoreCorruptionError):
                continue
            raise AssertionError(f"invalid fixture accepted: {path}")
        validator.validate(value)
        decode, encode = CODECS[value["kind"]]
        assert json.loads(encode(decode(data))) == value, path
        kinds.add(value["kind"])
    assert kinds == set(CODECS), (kinds, set(CODECS))
    print(f"Current protocol: PASS ({len(kinds)} record kinds)")


if __name__ == "__main__":
    main()
