"""Measure control costs with independently varied history, datasets, refs and GC logs."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import platform
import statistics
import sys
import time
import tracemalloc
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TypeVar
from unittest.mock import patch

from asterstore import HistoryAccess, Repository, RetentionScope
from asterstore.governance import _collection
from asterstore.governance._inventory import Inventory, inventory
from asterstore.metadata.protocol import StoreRecord
from asterstore.storage import delete_owned_file, sync_control_files

T = TypeVar("T")


@dataclass(frozen=True)
class Case:
    axis: str
    datasets: int = 1
    publications_per_dataset: int = 1
    references: int = 0
    completed_collections: int = 0


@dataclass
class Probe:
    """Python-level operation counts; not a syscall tracer or a device I/O counter."""

    read_attempts: int = 0
    successful_reads: int = 0
    control_bytes_read: int = 0
    directory_enumerations: int = 0
    fsync_calls: int = 0
    fsync_seconds: float = 0.0
    lock_acquisitions: int = 0
    lock_acquire_seconds: float = 0.0
    deletion_calls: int = 0
    inventory_seconds: float = 0.0
    control_sync_seconds: float = 0.0
    deletion_seconds: float = 0.0
    read_paths: set[str] = field(default_factory=set)

    def report(self) -> dict[str, object]:
        result = asdict(self)
        result.pop("read_paths")
        result["unique_control_read_paths"] = len(self.read_paths)
        return result


@contextmanager
def observe(root: Path) -> Iterator[Probe]:
    probe = Probe()
    control = root / ".asterstore"
    read_bytes = Path.read_bytes
    iterdir = Path.iterdir
    fsync = os.fsync
    flock = fcntl.flock

    def read(path: Path) -> bytes:
        if not path.is_relative_to(control) or path.is_relative_to(control / "managed-data"):
            raise AssertionError(f"governance attempted to read payload: {path}")
        probe.read_attempts += 1
        data = read_bytes(path)
        probe.successful_reads += 1
        probe.control_bytes_read += len(data)
        probe.read_paths.add(str(path.relative_to(control)))
        return data

    def directories(path: Path) -> Iterator[Path]:
        probe.directory_enumerations += 1
        yield from iterdir(path)

    def synchronize(fd: int) -> None:
        start = time.perf_counter()
        try:
            fsync(fd)
        finally:
            probe.fsync_calls += 1
            probe.fsync_seconds += time.perf_counter() - start

    def lock(fd: int, operation: int) -> None:
        start = time.perf_counter()
        try:
            flock(fd, operation)
        finally:
            if operation != fcntl.LOCK_UN:
                probe.lock_acquisitions += 1
                probe.lock_acquire_seconds += time.perf_counter() - start

    def scan(root: Path, store: StoreRecord) -> Inventory:
        start = time.perf_counter()
        try:
            return inventory(root, store)
        finally:
            probe.inventory_seconds += time.perf_counter() - start

    def sync_controls(root: Path, paths: tuple[Path, ...]) -> None:
        start = time.perf_counter()
        try:
            sync_control_files(root, paths)
        finally:
            probe.control_sync_seconds += time.perf_counter() - start

    def delete_file(root: Path, relative_path: str) -> bool:
        start = time.perf_counter()
        try:
            return delete_owned_file(root, relative_path)
        finally:
            probe.deletion_calls += 1
            probe.deletion_seconds += time.perf_counter() - start

    with ExitStack() as stack:
        for owner, name, replacement in (
            (Path, "read_bytes", read),
            (Path, "iterdir", directories),
            (os, "fsync", synchronize),
            (fcntl, "flock", lock),
            (_collection, "inventory", scan),
            (_collection, "sync_control_files", sync_controls),
            (_collection, "delete_owned_file", delete_file),
        ):
            stack.enter_context(patch.object(owner, name, replacement))
        yield probe


def profiled(root: Path, operation: Callable[[], T]) -> tuple[T, dict[str, object]]:
    with observe(root) as probe:
        start = time.perf_counter()
        result = operation()
        elapsed = time.perf_counter() - start
    return result, {"instrumented_seconds": elapsed, **probe.report()}


def read_measurement(root: Path, operation: Callable[[], T], repeats: int) -> dict[str, object]:
    operation()  # Warm the same control state before timing.
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        operation()
        samples.append(time.perf_counter() - start)
    _, counts = profiled(root, operation)
    tracemalloc.start()
    try:
        value = operation()  # Keep the returned object alive through the peak measurement.
        _, peak = tracemalloc.get_traced_memory()
        del value
    finally:
        tracemalloc.stop()
    return {
        "seconds": samples,
        "median_seconds": statistics.median(samples),
        "python_peak_bytes": peak,
        "probe": counts,
    }


def footprint(root: Path) -> dict[str, int]:
    control_files = control_bytes = data_files = data_bytes = 0
    for path in (root / ".asterstore").rglob("*"):
        if path.is_file():
            size = path.stat().st_size
            if path.is_relative_to(root / ".asterstore/managed-data"):
                data_files += 1
                data_bytes += size
            else:
                control_files += 1
                control_bytes += size
    return dict(
        control_files=control_files,
        control_bytes=control_bytes,
        data_files=data_files,
        data_bytes=data_bytes,
    )


def measure(case: Case, parent: Path | None, repeats: int, durable: bool) -> dict[str, object]:
    with TemporaryDirectory(prefix="asterstore-governance-scale-", dir=parent) as temporary:
        root = Path(temporary)
        repo = Repository(root)
        start = time.perf_counter()
        repo.initialize(
            store_id="benchmark",
            resource_ids=["owned"],
            managed_resource_id="owned",
            lifecycle=True,
        )
        for dataset in range(case.datasets):
            for generation in range(case.publications_per_dataset):
                with repo.prepare(
                    f"data:{dataset}",
                    publication_id=f"p:{generation}",
                    operation_id=f"write:{dataset}:{generation}",
                    expected_generation=generation,
                    history=HistoryAccess.VERSIONED,
                    durable=durable,
                ) as writer:
                    writer.write_bytes("value", b"x" * 128, relative_path="part.bin")
                    writer.commit()
        # References alternate scopes but all target p:0, so root count and graph size differ.
        for index in range(case.references):
            repo.governance.retain(
                f"task:{index}",
                "data:0",
                "p:0",
                scope=RetentionScope.OBJECTS if index % 2 == 0 else RetentionScope.METADATA,
            )
        # The log axis has only current publications: each setup GC has an empty fixed plan.
        for index in range(case.completed_collections):
            result = repo.governance.collect(f"prior:{index}")
            assert result.complete and not result.deleted_objects
        setup_seconds = time.perf_counter() - start
        before = footprint(root)
        opened = read_measurement(root, lambda: repo.open("data:0"), repeats)
        binding = repo.open("data:0")
        selection = read_measurement(root, binding.files, repeats)
        with observe(root) as reads:
            repo.open("data:0")
        assert reads.successful_reads == 2 and reads.directory_enumerations == 0
        assert reads.read_paths == {
            "format.json",
            "datasets/" + hashlib.sha256(b"data:0").hexdigest() + "/current.json",
        }
        with observe(root) as selections:
            binding.files()
        assert selections.report() == Probe().report()
        inspection = read_measurement(root, repo.governance.inspect, repeats)
        metadata_check = read_measurement(root, repo.governance.check, repeats)
        assert repo.governance.check().ok
        preview = read_measurement(root, repo.governance.preview, repeats)
        expected = case.datasets * (case.publications_per_dataset - 1)
        if case.references and case.publications_per_dataset > 1:
            expected -= 1  # At least one object reference protects data:0 / p:0.
        assert len(repo.governance.preview().reclaimable) == expected
        retained, retain_probe = profiled(
            root,
            lambda: repo.governance.retain(
                "measurement",
                "data:0",
                f"p:{case.publications_per_dataset - 1}",
                scope=RetentionScope.OBJECTS,
            ),
        )
        _, release_probe = profiled(
            root,
            lambda: repo.governance.release(retained.name, expected_revision=retained.revision),
        )
        collected, collection = profiled(root, lambda: repo.governance.collect("measurement"))
        assert collected.complete and len(collected.deleted_objects) == expected
        assert not collected.missing_objects and not collected.protected_objects
        resumed, retry = profiled(root, lambda: repo.governance.resume_collection("measurement"))
        assert resumed == collected and retry["deletion_calls"] == 0
        task_status = read_measurement(
            root, lambda: repo.governance.collection_status("measurement"), repeats
        )
        after_preview = read_measurement(root, repo.governance.preview, repeats)
        assert not repo.governance.preview().reclaimable
        after = footprint(root)
        assert before["data_files"] - after["data_files"] == expected
        assert binding.files()[0].read_bytes() == b"x" * 128
        print(f"Measured {asdict(case)}", file=sys.stderr, flush=True)
        return {
            "case": asdict(case),
            "setup_seconds": setup_seconds,
            "before": before,
            "open_current": opened,
            "cached_selection": selection,
            "inspection": inspection,
            "metadata_check": metadata_check,
            "preview_before": preview,
            "retain": retain_probe,
            "release": release_probe,
            "collect": collection,
            "completed_retry": retry,
            "collection_status": task_status,
            "preview_after": after_preview,
            "after": after,
            "deleted_objects": expected,
            "post_collect_reference_records": case.references + 1,
            "post_collect_completed_plans": case.completed_collections + 1,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", nargs="+", type=int, default=[1, 100, 1000])
    parser.add_argument("--datasets", nargs="+", type=int, default=[1, 32, 100])
    parser.add_argument("--references", nargs="+", type=int, default=[0, 32, 128])
    parser.add_argument("--logs", nargs="+", type=int, default=[0, 10, 100])
    parser.add_argument("--mixed-datasets", type=int, default=0)
    parser.add_argument("--mixed-history", type=int, default=100)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--durable", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--parent", type=Path)
    parser.add_argument("--storage-label", default="unspecified local temporary filesystem")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if min(*args.history, *args.datasets, args.repeats) < 1:
        parser.error("history, datasets and repeats must be positive")
    if min(*args.references, *args.logs) < 0:
        parser.error("reference and log counts must be nonnegative")
    if args.mixed_datasets < 0 or args.mixed_history < 1:
        parser.error("mixed datasets must be nonnegative and history positive")
    if args.parent is not None:
        args.parent = args.parent.absolute()
    cases = [Case("history", publications_per_dataset=n) for n in args.history]
    cases += [Case("datasets", datasets=n) for n in args.datasets]
    cases += [Case("references", publications_per_dataset=3, references=n) for n in args.references]
    cases += [Case("logs", completed_collections=n) for n in args.logs]
    if args.mixed_datasets:
        cases.append(
            Case("mixed", datasets=args.mixed_datasets, publications_per_dataset=args.mixed_history)
        )
    project = Path(__file__).resolve().parents[1]
    sources = [Path(__file__).resolve(), *sorted((project / "src/asterstore").rglob("*.py"))]
    report = {
        "python": sys.version,
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "processes": 1,
        "storage_label": args.storage_label,
        "parent": str(args.parent) if args.parent else "system temporary directory",
        "publication_durable": args.durable,
        "governance_durable": True,
        "repeats_read_operations": args.repeats,
        "repeats_mutations": 1,
        "scope": "explicit members, one new 128-byte object per publication; no reuse or deps",
        "caveats": (
            "Warm uncontrolled OS cache and host load; no device identification or RSS. "
            "Read wall samples and tracemalloc peaks run separately from probes. "
            "Mutation timings include instrumentation. Phase timings overlap: fsync is nested "
            "inside control sync/deletion, so do not add phase totals. Lock timing is acquisition "
            "overhead without contention. Counters cover Path.read_bytes/iterdir and os.fsync, "
            "not os.walk/scandir, all syscalls or physical device traffic. "
            "Setup uses real public APIs. "
            "Empty-plan log buildup does not represent large historical deletion plans."
        ),
        "source_sha256": {
            str(p.relative_to(project)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources
        },
        "results": [measure(case, args.parent, args.repeats, args.durable) for case in cases],
    }
    actual_sources = {
        str(p.relative_to(project)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources
    }
    if actual_sources != report["source_sha256"]:
        raise RuntimeError(
            "benchmark sources changed during measurement; rerun on a stable checkout"
        )
    data = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(data, encoding="utf-8")
    print(data, end="")


if __name__ == "__main__":
    main()
