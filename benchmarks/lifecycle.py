"""Reproducible local control-operation baseline; not a data-engine benchmark."""

import argparse
import importlib.metadata
import json
import platform
import statistics
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter_ns

from asterstore import CollectionPolicy, Dataset, Repository


def median_ms(operation: Callable[[], object], repeats: int) -> float:
    operation()  # Warm up; do not claim a controlled cold-cache measurement.
    samples = []
    for _ in range(repeats):
        start = perf_counter_ns()
        operation()
        samples.append((perf_counter_ns() - start) / 1_000_000)
    return statistics.median(samples)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publications", type=int, default=10)
    parser.add_argument("--files", type=int, default=20)
    parser.add_argument("--bytes-per-file", type=int, default=128)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--selections", type=int, default=10000)
    parser.add_argument("--durable", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--parent", type=Path)
    parser.add_argument("--storage-label", default="unspecified; not storage-qualified")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if min(args.publications, args.files, args.bytes_per_file, args.repeats, args.selections) < 1:
        parser.error("all workload counts must be positive")
    with tempfile.TemporaryDirectory(prefix="asterstore-baseline-", dir=args.parent) as temporary:
        root = Path(temporary)
        repository = Repository(root)
        data = b"x" * args.bytes_per_file
        publish_times = []
        for _index in range(args.publications):
            start = perf_counter_ns()
            with repository.prepare(Dataset("data"), durable=args.durable) as candidate:
                for file_index in range(args.files):
                    candidate.write_bytes(f"part-{file_index}.bin", data)
                candidate.commit()
            publish_times.append((perf_counter_ns() - start) / 1_000_000)
        binding = repository.open("data")
        direct_paths = binding.files()
        selection_start = perf_counter_ns()
        for _ in range(args.selections):
            assert binding.files() is direct_paths
        selection_ns = (perf_counter_ns() - selection_start) / args.selections
        revision = 0

        def reference_cycle() -> None:
            nonlocal revision
            held = repository.retention.retain(
                "baseline/run", "data", expected_revision=revision, durable=args.durable
            )
            revision = repository.retention.release(
                "baseline/run", expected_revision=held.revision, durable=args.durable
            ).revision

        reference_ms = median_ms(reference_cycle, args.repeats)
        policy = CollectionPolicy(("data",))
        preview = repository.retention.preview(policy)
        assert preview.status == "complete"
        assert len(preview.reclaimable_objects) == (args.publications - 1) * args.files
        timings = {
            "prepare_write_commit_median_ms": statistics.median(publish_times),
            "open_current_median_ms": median_ms(lambda: repository.open("data"), args.repeats),
            "cached_files_mean_ns_including_assert": selection_ns,
            "retain_release_cycle_median_ms": reference_ms,
            "metadata_preview_median_ms": median_ms(
                lambda: repository.retention.preview(policy), args.repeats
            ),
            "direct_read_bytes_median_ms": median_ms(
                lambda: [path.read_bytes() for path in direct_paths], args.repeats
            ),
            "bound_read_bytes_median_ms": median_ms(
                lambda: [path.read_bytes() for path in binding.files()], args.repeats
            ),
        }
        result = {
            "measured_at_utc": datetime.now(UTC).isoformat(),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "asterstore": importlib.metadata.version("asterstore"),
            "storage_parent": str(root.parent),
            "storage_label": args.storage_label,
            "cache": "warm, uncontrolled OS cache; sequential measurements",
            "durable": args.durable,
            "workload": {
                "publications": args.publications,
                "files_per_publication": args.files,
                "bytes_per_file": args.bytes_per_file,
                "repeats": args.repeats,
                "selections": args.selections,
                "reference_names": 1,
            },
            "timings": timings,
            "limitations": [
                "No data engine or business query",
                "No concurrent writers",
                "No physical GC",
                "One local sample, not a throughput claim",
            ],
        }
    output = json.dumps(result, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    print(output, end="")


if __name__ == "__main__":
    main()
