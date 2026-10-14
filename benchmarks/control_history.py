"""Read-only D1 control-history attribution; never a compaction/deletion plan."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from asterstore.governance._control import ControlReader
from asterstore.governance.inspection._locking import inspection_lock
from asterstore.governance.inspection._service import read_inventory

T = TypeVar("T")


class RecordingReader(ControlReader):
    def __init__(self) -> None:
        super().__init__()
        self.contents: dict[Path, bytes] = {}

    def read(self, path: Path, decode: Callable[[bytes], T]) -> T:
        def capture(data: bytes) -> T:
            self.contents[path] = path.read_bytes()
            return decode(data)

        return super().read(path, capture)


def analyze(root: Path, *, quiescent: bool) -> dict[str, object]:
    """Caller must stop writers, including candidate builders outside gc.lock."""
    if not quiescent:
        raise ValueError("stop all writers and governance mutations before analysis")
    root = root.absolute()
    reader = RecordingReader()
    with inspection_lock(root) as coordinated:
        view = read_inventory(root, coordinated, reader=reader)
        committed = {p.operation_id for p in view.publications.values()}
        blockers = []
        for op in sorted(set(view.requests) - committed):
            progress = view.cleanup_progress.get(op)
            if op not in view.abandoned or progress is None or not progress.complete:
                blockers.append({"kind": "candidate_or_cleanup", "operation_id": op})
        for op, collection in sorted(view.collection_progress.items()):
            if not collection.complete:
                blockers.append({"kind": "collection", "operation_id": op})
        for path, data in reader.contents.items():
            if path.parent.name == "registrations":
                op = json.loads(data)["operation_id"]
                if op not in committed and op not in view.requests:
                    blockers.append({"kind": "registration", "operation_id": op})

        for op, plan in sorted(view.decommission_plans.items()):
            if any(
                (p.declaration.dataset_id, p.declaration.publication_id) in view.current
                for p in plan.targets
            ):
                blockers.append({"kind": "decommission", "operation_id": op})

        families: dict[str, dict[str, int]] = defaultdict(lambda: {"files": 0, "bytes": 0})
        groups: dict[bytes, list[str]] = defaultdict(list)
        pinned = []
        records = []
        for path, data in sorted(reader.contents.items()):
            relative = path.relative_to(root / ".asterstore")
            name = relative.as_posix()
            family = relative.parts[0]
            families[family]["files"] += 1
            families[family]["bytes"] += len(data)
            # First slice leaves current, identity and reference records in place.
            reasons = []
            if blockers:
                reasons.append("unfinished_operation_conservative_closure")
            if path.name == "current.json" or family == "format.json":
                reasons.append("current_read_contract")
            if family in {"object-records", "fixed-retentions", "decommissions"}:
                reasons.append("identity_or_reference_authority")
            if reasons:
                pinned.append({"path": name, "reasons": reasons})
            else:
                groups[data].append(name)
            records.append(
                {"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            )
        duplicates = [
            {
                "paths": paths,
                "record_bytes": len(data),
                "duplicate_bytes": len(data) * (len(paths) - 1),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
            for data, paths in groups.items()
            if len(paths) > 1
        ]
        return {
            "schema_version": 1,
            "store_id": view.store.store_id,
            "quiescence": "caller_asserted; gc lock is not sufficient for candidate builders",
            "scope": "validated authority only; excludes payloads, locks, temp and unknown files",
            "authority_files": len(records),
            "authority_bytes": sum(map(len, reader.contents.values())),
            "families": dict(sorted(families.items())),
            "unfinished_operations": sorted(blockers, key=lambda x: (x["kind"], x["operation_id"])),
            "pinned_records": pinned,
            "exact_duplicate_groups": duplicates,
            "duplicate_bytes_upper_bound": sum(
                len(data) * (len(paths) - 1) for data, paths in groups.items()
            ),
            "duplicate_record_count": sum(len(paths) - 1 for paths in groups.values()),
            "net_archive_savings_bytes": None,
            "net_archive_file_reduction": None,
            "limitations": [
                "No deletion authorization; all existing authority records remain required.",
                "Upper bound omits archive indexes, references, framing and checkpoint overhead.",
                "No nested-record deduplication or compression estimate; no latency prediction.",
                "Any unfinished operation pins the entire authority inventory in this first slice.",
                "No data bytes read; no data health or unknown-file inventory performed.",
            ],
            "records": records,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument(
        "--quiescent",
        action="store_true",
        required=True,
        help="assert all writers and governance mutations have stopped",
    )
    args = parser.parse_args()
    print(json.dumps(analyze(args.root, quiescent=args.quiescent), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
