"""Count storage operations for five changes as historical partition count grows."""

from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

from asterstore import Repository
from asterstore import _storage as fs


@contextmanager
def measured(root: Path, changed: set[Path]) -> Iterator[Counter[str]]:
    counts: Counter[str] = Counter()
    original_check = fs.checked
    original_sync = fs.sync_file
    original_walk = os.walk

    def check(path: Path, *, missing: bool = False) -> Path:
        counts["path_checks"] += 1
        if path.is_relative_to(root) and ".asterstore" not in path.relative_to(root).parts:
            if (
                path != root
                and path not in changed
                and not any(p.is_relative_to(path) for p in changed)
            ):
                raise AssertionError(f"visited unchanged data: {path}")
        return original_check(path, missing=missing)

    def sync(path: Path) -> None:
        counts["file_syncs"] += 1
        original_sync(path)

    def walk(path: Path, **kwargs: Any) -> Iterator[tuple[str, list[str], list[str]]]:
        if not Path(path).is_relative_to(root / ".asterstore"):
            raise AssertionError("walked formal data")
        counts["control_walks"] += 1
        return original_walk(path, **kwargs)

    with (
        patch.object(fs, "checked", check),
        patch.object(fs, "sync_file", sync),
        patch.object(os, "walk", walk),
    ):
        yield counts


def run(members: int) -> dict[str, Any]:
    if members < 5:
        raise ValueError("at least five partitions are required")
    with TemporaryDirectory(prefix="asterstore-scale-") as temporary:
        root = Path(temporary)
        repo = Repository(root / "registry")
        repo.initialize()
        ds = repo.create_dataset("market", root / "market")
        with ds.write() as batch:
            for index in range(members):
                batch.stage(f"date={index:05d}/data").write_bytes(b"old")
            batch.commit(metadata={"ready": members}, quiescent=True)
        changed = {ds.root / f"date={i:05d}/data" for i in range(5)}
        start = time.monotonic()
        with measured(ds.root, changed) as publish_counts:
            with ds.write() as batch:
                for index in range(5):
                    batch.stage(f"date={index:05d}/data").write_bytes(b"new")
                batch.commit(metadata={"ready": members}, quiescent=True)
        elapsed = time.monotonic() - start
        state = ds.current()
        record = ds.control / "work" / str(state.undo) / "record.json"
        assert len(json.loads(record.read_bytes())["entries"]) == 5
        record_bytes = record.stat().st_size
        with measured(ds.root, changed) as rollback_counts:
            ds.rollback(quiescent=True)
        # Cleaning old control material can scale with prior change batches, never formal data.
        with measured(ds.root, changed) as clean_counts:
            ds.clean()
        return {
            "historical_partitions": members,
            "changed_partitions": 5,
            "record_bytes": record_bytes,
            "publish": dict(publish_counts),
            "rollback": dict(rollback_counts),
            "clean": dict(clean_counts),
            "publish_seconds": round(elapsed, 6),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--members", type=int, nargs="+", default=[100, 1000, 10000])
    args = parser.parse_args()
    rows = [run(members) for members in args.members]
    for field in ("publish", "rollback"):
        assert all(row[field] == rows[0][field] for row in rows), field
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
