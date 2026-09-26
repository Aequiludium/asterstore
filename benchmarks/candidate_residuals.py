"""One local sample of explicit candidate diagnosis, abandonment and cleanup."""

import json
import platform
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from asterstore import Dataset, Repository


def main() -> None:
    with TemporaryDirectory(prefix="asterstore-residuals-benchmark-") as temporary:
        repo = Repository(Path(temporary))
        ids = []
        for index in range(4):
            with repo.prepare(Dataset("data"), publication_id=f"failed-{index}") as candidate:
                for part in range(4):
                    candidate.write_bytes(f"part-{part}.bin", b"x" * 256)
                if index % 2:
                    candidate.seal()
                ids.append(candidate.candidate_id)
        started = perf_counter()
        report = repo.inspection.candidates()
        inspection_seconds = perf_counter() - started
        assert report.status == "complete"
        assert sum(len(row.files) for row in report.candidates) == 16
        assert sum(row.total_bytes for row in report.candidates) == 4096
        started = perf_counter()
        for candidate_id in ids:
            repo.abandon(candidate_id)
        abandon_seconds = perf_counter() - started
        started = perf_counter()
        results = [repo.retention.cleanup_candidate(candidate_id) for candidate_id in ids]
        cleanup_seconds = perf_counter() - started
        assert all(result.status == "complete" for result in results)
        assert sum(len(result.deleted_files) for result in results) == 16
        assert not any(row.files for row in repo.inspection.candidates().candidates)
        print(
            json.dumps(
                {
                    "python": sys.version,
                    "platform": platform.platform(),
                    "storage": "local temporary directory; device not isolated",
                    "cache": "warm; source just written; no cache flushing",
                    "candidates": 4,
                    "files": 16,
                    "data_bytes": 4096,
                    "durable_candidate_writes": True,
                    "governance_always_durable": True,
                    "inspection_seconds": inspection_seconds,
                    "abandon_seconds": abandon_seconds,
                    "cleanup_seconds": cleanup_seconds,
                    "measurement": (
                        "single uncontended sample; not throughput, power-loss or NFS qualification"
                    ),
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
