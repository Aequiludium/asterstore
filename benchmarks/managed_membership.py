"""Measure candidate membership construction separately from data production/commit."""

import argparse
import hashlib
import json
import platform
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from asterstore import FileSet, Repository


def measure(count: int) -> dict[str, object]:
    with TemporaryDirectory(prefix="asterstore-membership-") as temporary:
        repo = Repository(Path(temporary))
        repo.initialize(store_id="benchmark", resource_ids=["owned"], managed_resource_id="owned")
        scans: list[int] = []
        original = FileSet.__post_init__

        def observe(value: FileSet) -> None:
            scans.append(len(value.objects))
            original(value)

        with repo.prepare(
            "data",
            publication_id="candidate",
            operation_id="candidate",
            expected_generation=0,
            durable=False,
        ) as writer:
            with patch.object(FileSet, "__post_init__", observe):
                started = time.perf_counter()
                for index in range(count):
                    writer.path(str(index), relative_path=f"parts/{index}.bin")
                build_seconds = time.perf_counter() - started
                build_scans = list(scans)
                started = time.perf_counter()
                # Internal measurement point: immutable metadata materialization only,
                # without the file inventory/fsync/commit work performed by seal().
                files = writer._members.snapshot()
                freeze_seconds = time.perf_counter() - started
                assert len(files.objects) == count
                assert not build_scans and scans == [count]
        return {
            "members": count,
            "build_seconds": build_seconds,
            "freeze_seconds": freeze_seconds,
            "build_fileset_validations": len(build_scans),
            "freeze_objects_validated": scans[-1],
            "data_files_written": 0,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--members", nargs="+", type=int, default=[1024, 4096, 25000, 100000])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if any(value < 1 for value in args.members):
        parser.error("member counts must be positive")
    root = Path(__file__).resolve().parents[1]
    sources = [
        "src/asterstore/publishing/_candidate.py",
        "src/asterstore/publishing/_membership.py",
        "src/asterstore/metadata/membership/_models.py",
    ]
    report = {
        "python": sys.version,
        "platform": platform.platform(),
        "durable": False,
        "repeats": 1,
        "scope": "path declarations and immutable membership; no data production/seal/commit",
        "caveats": "local temp directory, uncontrolled cache/load; no RSS measurement",
        "source_sha256": {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in sources},
        "results": [measure(value) for value in args.members],
    }
    data = json.dumps(report, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(data, encoding="utf-8")
    print(data, end="")


if __name__ == "__main__":
    main()
