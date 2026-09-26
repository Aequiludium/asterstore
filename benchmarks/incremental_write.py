"""Measure new data bytes for one-partition updates; timings are local samples."""

import argparse
import json
import platform
import sys
import tempfile
from pathlib import Path
from time import perf_counter

from asterstore import CollectionPolicy, Dataset, Repository


def measure(root: Path, count: int, size: int, *, reuse: bool) -> dict[str, object]:
    repo = Repository(root)
    dataset = Dataset("partitions")
    content = b"x" * size
    with repo.prepare(dataset, publication_id="base") as candidate:
        for index in range(count):
            candidate.write_bytes(f"part-{index}.bin", content)
        first = candidate.commit()
    started = perf_counter()
    with repo.prepare(dataset, publication_id="update") as candidate:
        if reuse:
            candidate.reuse("base", keys=[obj.key for obj in first.objects[1:]])
        for index in range(1 if reuse else count):
            candidate.write_bytes(f"part-{index}.bin", b"y" * size if index == 0 else content)
        second = candidate.commit()
    elapsed = perf_counter() - started
    # Inventory after the timed operation. This is benchmark instrumentation,
    # not the library's ordinary read/preview behavior.
    new_paths = [path for path in repo.bind(second).files() if path not in repo.bind(first).files()]
    shared_count = len(second.objects) - len(new_paths)
    assert shared_count == (count - 1 if reuse else 0)
    assert len(second.objects) == count
    preview = repo.retention.preview(CollectionPolicy(("partitions",)))
    assert preview.status == "complete"
    assert len(preview.reclaimable_objects) == (1 if reuse else count)
    return {
        "durable": True,
        "update_seconds": elapsed,
        "new_data_files": len(new_paths),
        "new_data_bytes": sum(path.stat().st_size for path in new_paths),
        "shared_original_paths": shared_count,
        "reclaimable_objects": len(preview.reclaimable_objects),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", type=int, default=20)
    parser.add_argument("--bytes-per-file", type=int, default=1024)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.files < 2 or args.bytes_per_file < 1:
        parser.error("requires at least two files and positive bytes-per-file")
    with tempfile.TemporaryDirectory(prefix="asterstore-reuse-benchmark-") as temporary:
        root = Path(temporary)
        result = {
            "python": sys.version,
            "platform": platform.platform(),
            "storage": "local temporary directory; device/cache not isolated or qualified",
            "cache": "source just written; warm cache; no cache flushing",
            "files": args.files,
            "bytes_per_file": args.bytes_per_file,
            "changed_partitions": 1,
            "measurement": "one sample per mode; new data bytes exclude metadata and fsync I/O",
            "full_rewrite": measure(root / "full", args.files, args.bytes_per_file, reuse=False),
            "reuse_unchanged": measure(root / "reuse", args.files, args.bytes_per_file, reuse=True),
        }
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload)
    print(payload, end="")


if __name__ == "__main__":
    main()
