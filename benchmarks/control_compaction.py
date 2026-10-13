"""Measure D compaction bytes and metadata latency on independent temporary histories."""

from __future__ import annotations

import hashlib
import json
import platform
import statistics
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

from asterstore import HistoryAccess, Repository


def sources() -> dict[str, str]:
    root = Path(__file__).resolve().parents[1]
    paths = sorted((root / "src/asterstore").rglob("*.py")) + [Path(__file__).resolve()]
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def physical(root: Path) -> dict[str, int]:
    paths = [
        p
        for p in (root / ".asterstore").rglob("*")
        if p.is_file() and "managed-data" not in p.parts and p.suffix != ".lock"
    ]
    return {"files": len(paths), "bytes": sum(p.stat().st_size for p in paths)}


def timings(repo: Repository) -> dict[str, float]:
    results = {}
    actions: list[tuple[str, Callable[[], object]]] = [
        ("current_open", lambda: repo.open("data")),
        ("historical_describe", lambda: repo.describe("data", publication_id="p0")),
        ("inspect", lambda: repo.governance.inspect()),
    ]
    for name, action in actions:
        samples = []
        for _ in range(5):
            start = time.perf_counter()
            action()
            samples.append(time.perf_counter() - start)
        results[name + "_median_seconds"] = statistics.median(samples)
    return results


def main() -> None:
    before_sources = sources()
    rows = []
    for history, objects, collect in [
        (8, 8, False),
        (32, 8, False),
        (32, 64, False),
        (32, 8, True),
    ]:
        with TemporaryDirectory(prefix="asterstore-compaction-") as directory:
            root = Path(directory)
            repo = Repository(root)
            repo.initialize(
                store_id="compaction-sample",
                resource_ids=["owned"],
                managed_resource_id="owned",
                lifecycle=True,
            )
            for n in range(history):
                with repo.prepare(
                    "data",
                    publication_id=f"p{n}",
                    operation_id=f"op{n}",
                    expected_generation=n,
                    history=HistoryAccess.VERSIONED,
                    durable=False,
                ) as writer:
                    for i in range(objects):
                        writer.write_bytes(str(i), b"synthetic", relative_path=f"{i}.bin")
                    writer.commit()
            if collect:
                repo.governance.collect("gc")
            expected = [repo.describe("data", publication_id=f"p{i}") for i in range(history)]
            before, old_times = physical(root), timings(repo)
            start = time.perf_counter()
            result = repo.governance.compact("archive", quiescent=True)
            elapsed = time.perf_counter() - start
            after, new_times = physical(root), timings(repo)
            assert expected == [
                repo.describe("data", publication_id=f"p{i}") for i in range(history)
            ]
            assert repo.governance.compact("archive", quiescent=True) == result
            if collect:
                assert repo.governance.resume_collection("gc").complete
            rows.append(
                {
                    "history": history,
                    "objects_per_publication": objects,
                    "collected": collect,
                    "before": before,
                    "after": after,
                    "actual_saved_bytes": before["bytes"] - after["bytes"],
                    "compaction_seconds": elapsed,
                    "result": asdict(result),
                    "before_timing": old_times,
                    "after_timing": new_times,
                }
            )
    if sources() != before_sources:
        raise RuntimeError("source changed during benchmark")
    print(
        json.dumps(
            {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "cache": "not evicted; five sequential samples per median; no I/O probes",
                "scope": "physical control file lengths; excludes payloads and lock files",
                "object_ids": "runtime uuid4; compressed sizes vary slightly between runs",
                "source_sha256": before_sources,
                "cases": rows,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
