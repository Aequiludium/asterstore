"""Compare identical Polars queries at increasing explicit-manifest sizes."""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import time
import tracemalloc
from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import polars as pl

from asterstore import Binding, Repository
from asterstore.integrations.polars import scan_parquet


def elapsed(action: Callable[[], object]) -> float:
    start = time.perf_counter_ns()
    action()
    return (time.perf_counter_ns() - start) / 1_000_000


def batch_ms(action: Callable[[], object], iterations: int) -> float:
    start = time.perf_counter_ns()
    for _ in range(iterations):
        action()
    return (time.perf_counter_ns() - start) / iterations / 1_000_000


def summary(samples: list[float]) -> dict[str, Any]:
    return {"median_ms": statistics.median(samples), "samples_ms": samples}


def query(scan: pl.LazyFrame) -> pl.DataFrame:
    return scan.filter(pl.col("row") % 2 == 0).select(pl.col("value").sum()).collect()


def compare(binding: Binding, keys: list[str] | None, repeats: int) -> dict[str, Any]:
    paths = binding.files(keys=keys)

    def direct() -> pl.DataFrame:
        return query(pl.scan_parquet(list(paths), glob=False, hive_partitioning=False))

    def managed() -> pl.DataFrame:
        return query(scan_parquet(binding, keys=keys))

    expected = direct()
    assert managed().equals(expected)
    samples: dict[str, list[float]] = {"direct": [], "bound": []}
    actions = {"direct": direct, "bound": managed}
    # Both execute first to warm caches. Alternate order to reduce ordering bias.
    for repeat in range(repeats):
        order = ("direct", "bound") if repeat % 2 == 0 else ("bound", "direct")
        for name in order:
            start = time.perf_counter_ns()
            result = actions[name]()
            duration = (time.perf_counter_ns() - start) / 1_000_000
            assert result.equals(expected)
            samples[name].append(duration)
    return {
        "selected_files": len(paths),
        "value_sum": expected.item(),
        "direct": summary(samples["direct"]),
        "bound": summary(samples["bound"]),
    }


def measure(root: Path, files: int, args: argparse.Namespace) -> dict[str, Any]:
    repository = Repository(root)
    repository.initialize(resource_ids=["data"], managed_resource_id="data")
    dataset = "numbers"
    frame = pl.DataFrame({"row": range(args.rows), "value": range(args.rows)})
    with repository.prepare(
        dataset, publication_id="p1", operation_id="p1", expected_generation=0, durable=args.durable
    ) as candidate:
        start = time.perf_counter_ns()
        for index in range(files):
            frame.write_parquet(
                candidate.path(str(index), relative_path=f"part-{index:06d}.parquet"),
                compression="zstd",
            )
        write_ms = (time.perf_counter_ns() - start) / 1_000_000
        commit_ms = elapsed(candidate.commit)
    binding = repository.open("numbers")
    tracemalloc.start()
    allocation_probe = repository.open("numbers")
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert allocation_probe.publication == binding.publication
    one = [binding.publication.files.members[0].key]
    subset = [obj.key for obj in binding.publication.files.members[: max(1, files // 4)]]
    return {
        "files": files,
        "rows_per_file": args.rows,
        "total_rows": files * args.rows,
        "parquet_bytes": sum(path.stat().st_size for path in binding.files()),
        "write_parquet_ms": write_ms,
        "seal_and_commit_ms": commit_ms,
        "open_python_peak_bytes": peak,
        "open": summary([elapsed(lambda: repository.open("numbers")) for _ in range(args.repeats)]),
        "selection": {
            label: summary(
                [
                    batch_ms(partial(binding.files, keys=keys), args.selections)
                    for _ in range(args.repeats)
                ]
            )
            for label, keys in [("all_cached", None), ("one", one), ("quarter", subset)]
        },
        "queries": {
            label: compare(binding, keys, args.repeats)
            for label, keys in [("all", None), ("one", one), ("quarter", subset)]
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", type=int, nargs="+", default=[1, 16, 128])
    parser.add_argument("--rows", type=int, default=4096)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--selections", type=int, default=1000)
    parser.add_argument("--durable", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--parent", type=Path)
    parser.add_argument("--storage-label", default="unspecified local temporary directory")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if min(*args.files, args.rows, args.repeats, args.selections) < 1:
        parser.error("file counts, rows, repeats, and selections must be positive")
    with TemporaryDirectory(prefix="asterstore-parquet-bench-", dir=args.parent) as directory:
        root = Path(directory)
        result = {
            "benchmark": "parquet-read-v1",
            "recorded_at": datetime.now(UTC).isoformat(),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "polars": pl.__version__,
            "polars_threads": pl.thread_pool_size(),
            "storage_label": args.storage_label,
            "temporary_parent": str(root.parent),
            "durable": args.durable,
            "compression": "zstd",
            "repeats": args.repeats,
            "selections_per_sample": args.selections,
            "cache": "freshly written data, warm-up both queries; OS caches uncontrolled",
            "query": "filter(row % 2 == 0).select(value.sum()).collect()",
            "limits": (
                "Sequential local reads; no cold-cache, concurrent GC, NFS, or large-data claim. "
                "Binding/open memory is Python tracemalloc peak, not native engine RSS. "
                "One current publication per scale; no history or governance scaling claim."
            ),
            "scales": [
                measure(root / str(index), size, args) for index, size in enumerate(args.files)
            ],
        }
    encoded = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded)
    print(encoded, end="")


if __name__ == "__main__":
    main()
