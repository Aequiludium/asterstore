"""Check the reviewed 0.1 API and wire baseline; never rewrite it implicitly."""

from __future__ import annotations

import argparse
import dataclasses
import difflib
import hashlib
import inspect
import json
from enum import Enum
from pathlib import Path
from typing import get_overloads

import asterstore
from asterstore.integrations.polars import scan_parquet
from asterstore.metadata.protocol import FORMAT_VERSION, MAX_GENERATION, StoreRecord, encode_store

# Factory-created services/writers are public return types, not public constructors.
FACTORY_TYPES = {"Candidate", "ManagedCandidate", "Governance", "Retention", "Inspection"}
LEGACY_TYPES = {
    "Candidate",
    "CandidateCleanupResult",
    "CandidateInspection",
    "CandidateResidual",
    "CandidateStatus",
    "CollectionPolicy",
    "CollectionPreview",
    "CollectionResult",
    "Dataset",
    "Inspection",
    "ObjectDecision",
    "ObjectRef",
    "PhysicalHistory",
    "PreviewIssue",
    "Publication",
    "PublicationDecision",
    "Reference",
    "ReferenceRecord",
    "RetainedPublication",
    "Retention",
}


def signatures(value: object) -> list[str]:
    if not callable(value):
        raise TypeError(f"not callable: {value}")
    return [str(inspect.signature(item)) for item in (get_overloads(value) or [value])]


def class_contract(cls: type[object]) -> dict[str, object]:
    if issubclass(cls, Enum):
        return {"enum": {name: value.value for name, value in cls.__members__.items()}}
    result: dict[str, object] = {"bases": [base.__name__ for base in cls.__bases__]}
    if issubclass(cls, BaseException):
        if "__init__" in cls.__dict__:
            result["constructor"] = signatures(cls.__init__)
        return result
    if cls.__name__ not in FACTORY_TYPES:
        result["constructor"] = signatures(cls)
    else:
        result["construction"] = "repository factory"
    if dataclasses.is_dataclass(cls):
        result["fields"] = [
            {"name": f.name, "type": str(f.type), "init": f.init, "kw_only": f.kw_only}
            for f in dataclasses.fields(cls)
            if not f.name.startswith("_")
        ]
        result["frozen"] = vars(cls)["__dataclass_params__"].frozen
    methods: dict[str, object] = {}
    for name, member in sorted(vars(cls).items()):
        if name.startswith("_") and name not in {"__enter__", "__exit__"}:
            continue
        if isinstance(member, property):
            methods[name] = {
                "kind": "property",
                "get": signatures(member.fget),
                "set": None if member.fset is None else signatures(member.fset),
            }
        elif isinstance(member, (classmethod, staticmethod)) or inspect.isfunction(member):
            methods[name] = {
                "kind": type(member).__name__,
                "signatures": signatures(getattr(cls, name)),
            }
    result["methods"] = methods
    return result


def snapshot(root: Path) -> dict[str, object]:
    exports = sorted(asterstore.__all__)
    if len(exports) != len(set(exports)):
        raise ValueError("duplicate public export")
    classes = {}
    for name in exports:
        cls = getattr(asterstore, name)
        if not inspect.isclass(cls):
            raise TypeError(f"class export expected: {name}")
        classes[name] = class_contract(cls)
    files = sorted((root / "tests/fixtures/protocol").rglob("*.json"))
    files += sorted((root / "src/asterstore/metadata/schemas").glob("*.json"))
    wire = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    features = []
    for managed in (False, True):
        for lifecycle in (False, True):
            value = StoreRecord("contract", ("owned",), "owned" if managed else None, lifecycle)
            features.append(json.loads(encode_store(value))["required_features"])
    return {
        "baseline": "0.1",
        "api": classes,
        "legacy_exports": sorted(LEGACY_TYPES),
        "integrations": {"asterstore.integrations.polars.scan_parquet": signatures(scan_parquet)},
        "wire": {
            "format_version": FORMAT_VERSION,
            "max_generation": MAX_GENERATION,
            "feature_combinations": features,
            "file_sha256": wire,
        },
    }


def formatted(value: object) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    if Path(asterstore.__file__).resolve() != root / "src/asterstore/__init__.py":
        raise SystemExit(
            "Use the project environment: imported asterstore is outside this source tree"
        )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=root / "docs/contracts/0.1.json")
    args = parser.parse_args()
    expected = json.loads(args.contract.read_text(encoding="utf-8"))
    actual = snapshot(root)
    if expected != actual:
        diff = difflib.unified_diff(
            formatted(expected).splitlines(),
            formatted(actual).splitlines(),
            fromfile=str(args.contract),
            tofile="current API/protocol",
            lineterm="",
        )
        print("\n".join(diff))
        raise SystemExit("Contract changed: review compatibility before updating the baseline.")
    print("0.1 API/protocol contract: PASS")


if __name__ == "__main__":
    main()
