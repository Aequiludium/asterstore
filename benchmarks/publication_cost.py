"""Independent timing, Python allocation and operation-count baselines for Issue #21."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
import time
import tracemalloc
from collections import defaultdict
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

from asterstore import HistoryAccess, Repository
from asterstore._data_checks import Check
from asterstore.governance import _collection
from asterstore.publishing import _candidate
from asterstore.publishing.transactions import _commit
from asterstore.storage import _files


@contextmanager
def observe(root: Path) -> Iterator[dict[str, float]]:
    """Python call counts; inclusive timings overlap and must not be summed."""
    counts: dict[str, float] = defaultdict(float)
    acquired: dict[int, tuple[float, str]] = {}
    original_flock = fcntl.flock

    def flock(fd: int, operation: int) -> None:
        start = time.perf_counter()
        original_flock(fd, operation)
        end = time.perf_counter()
        if operation == fcntl.LOCK_UN:
            if fd in acquired:
                when, kind = acquired.pop(fd)
                counts[kind + "_hold_seconds"] += start - when
        else:
            name = os.readlink(f"/proc/self/fd/{fd}")
            kind = "gc_lock" if name.endswith("/gc.lock") else "other_lock"
            counts[kind + "_wait_seconds"] += end - start
            counts[kind + "_acquisitions"] += 1
            acquired[fd] = (end, kind)

    def wrap(fn: Callable[..., Any], label: str) -> Callable[..., Any]:
        def call(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            counts[label + "_calls"] += 1
            try:
                result = fn(*args, **kwargs)
                if isinstance(result, bytes):
                    counts[label + "_bytes"] += len(result)
                return result
            finally:
                counts[label + "_seconds"] += time.perf_counter() - start

        return call

    read_bytes = Path.read_bytes

    def read(path: Path) -> bytes:
        control = root / ".asterstore"
        label = "control_read" if path.is_relative_to(control) else "other_read"
        if path.is_relative_to(control / "managed-data") or (
            path.is_relative_to(control / "managed-candidates")
            and "files" in path.relative_to(control).parts[2:4]
        ):
            label = "payload_read"
        return bytes(wrap(read_bytes, label)(path))

    original_write = _files._write

    def write(path: Path, data: bytes, *, durable: bool, exclusive: bool) -> None:
        counts["control_write_attempt_bytes"] += len(data)
        counts["control_write_attempt_calls"] += 1
        original_write(path, data, durable=durable, exclusive=exclusive)

    with ExitStack() as stack:
        stack.enter_context(patch.object(_files, "_write", write))
        stack.enter_context(patch.object(fcntl, "flock", flock))
        stack.enter_context(patch.object(Path, "read_bytes", read))
        for owner, name, label in (
            (os, "fsync", "fsync"),
            (_collection, "delete_owned_file", "delete_owned_file"),
            (os, "scandir", "scandir"),
            (Path, "stat", "path_stat"),
            (_candidate.Candidate, "_verify_files", "candidate_verify"),
            (_candidate, "encode_declaration_record", "candidate_encode"),
            (_commit, "encode_declaration_record", "commit_encode"),
            (_commit, "encode_object_record", "object_encode"),
        ):
            stack.enter_context(patch.object(owner, name, wrap(getattr(owner, name), label)))
        yield counts
    assert not acquired, "lock escaped measurement"


def build_case(
    root: Path, case: str, members: int, history: int, durable: bool, check: Check = "none"
) -> tuple[Callable[[], Any], Callable[[], None]]:
    repo = Repository(root)
    repo.initialize(
        store_id="bench", resource_ids=["owned"], managed_resource_id="owned", lifecycle=True
    )

    def publish(generation: int, count: int, strength: bool) -> None:
        with repo.prepare(
            "data",
            publication_id=f"p{generation}",
            operation_id=f"op{generation}",
            expected_generation=generation,
            history=HistoryAccess.VERSIONED,
            durable=strength,
        ) as writer:
            for i in range(count):
                writer.write_bytes(str(i), b"x" * 128, relative_path=f"parts/{i}.bin")
            writer.commit()

    def checked_publish(candidate: _candidate.Candidate) -> Any:
        if check == "checksum":
            sealed = candidate.seal()
            baselines = {
                obj.object_id: hashlib.sha256(
                    (b"y" if obj.locator.relative_path.endswith("/new.bin") else b"x") * 128
                ).hexdigest()
                for obj in sealed.declaration.files.objects
            }
            return candidate.commit(check=check, checksums=baselines)
        if check == "format":

            def validate(path: Path) -> None:
                if path.read_bytes() not in (b"x" * 128, b"y" * 128):
                    raise ValueError("unexpected synthetic payload")

            return candidate.commit(check=check, validator=validate)
        return candidate.commit(check=check)

    reuse_cases = {"incremental", "reuse_all", "reuse_batches"}
    if case in reuse_cases:
        publish(0, members, durable)
    if case in {"gc_first", "gc_retry"}:
        for generation in range(history):
            publish(generation, members, durable)
        if case == "gc_retry":
            repo.governance.collect("gc")

        def collect() -> Any:
            result = (
                repo.governance.collect("gc")
                if case == "gc_first"
                else repo.governance.resume_collection("gc")
            )
            assert result.complete
            assert len(result.deleted_objects) == (history - 1) * members
            return result

        return collect, lambda: None

    stack = ExitStack()
    generation = 1 if case in reuse_cases else 0
    writer = stack.enter_context(
        repo.prepare(
            "data",
            publication_id=f"p{generation}",
            operation_id=f"op{generation}",
            expected_generation=generation,
            history=HistoryAccess.VERSIONED,
            durable=False if case == "weak_to_strong" else durable,
        )
    )
    count = 0 if case in reuse_cases or case == "empty" else members
    for i in range(count):
        writer.write_bytes(str(i), b"x" * 128, relative_path=f"parts/{i}.bin")
    if case == "incremental":
        writer.write_bytes("new", b"y" * 128, relative_path="parts/new.bin")
    if case in {"sealed_resume", "installed_resume", "weak_to_strong"}:
        writer.seal()
        if case == "installed_resume":
            original = _candidate.Candidate._install

            def interrupt(candidate: _candidate.Candidate) -> None:
                original(candidate)
                raise InterruptedError("benchmark: installed before current")

            with patch.object(_candidate.Candidate, "_install", interrupt):
                try:
                    writer.commit()
                except InterruptedError:
                    pass
                else:
                    raise AssertionError("interruption not reached")
        if case == "weak_to_strong":
            writer.commit()
        stack.close()

        def resume() -> Any:
            with repo.resume("op0", durable=True if case == "weak_to_strong" else durable) as r:
                result = checked_publish(r)
                assert result.generation == 1
                return result

        return resume, stack.close

    def commit() -> Any:
        if case in reuse_cases:
            if case == "reuse_batches":
                for start in range(0, members, 16):
                    writer.reuse(keys=[str(i) for i in range(start, min(start + 16, members))])
            else:
                writer.reuse()
        result = checked_publish(writer)
        expected = members + 1 if case == "incremental" else count or members
        if case == "empty":
            expected = 0
        assert len(result.declaration.files.objects) == expected
        return result

    return commit, stack.close


def measure(
    parent: Path,
    case: str,
    members: int,
    history: int,
    durable: bool,
    mode: str,
    check: Check = "none",
) -> dict[str, Any]:
    with TemporaryDirectory(prefix="publication-cost-", dir=parent) as temporary:
        root = Path(temporary)
        operation, close = build_case(root, case, members, history, durable, check)
        try:
            if mode == "probe":
                with observe(root) as counts:
                    operation()
                return {"counts": dict(counts)}
            if mode == "memory":
                tracemalloc.start()
                try:
                    result = operation()
                    _, peak = tracemalloc.get_traced_memory()
                    del result
                    return {"python_peak_bytes": peak}
                finally:
                    tracemalloc.stop()
            start = time.perf_counter()
            operation()
            return {"seconds": time.perf_counter() - start}
        finally:
            close()


def fingerprint() -> dict[str, str]:
    root = Path(__file__).resolve().parents[1]
    paths = sorted((root / "src").rglob("*.py")) + [Path(__file__).resolve()]
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    cases = (
        "new",
        "incremental",
        "reuse_all",
        "reuse_batches",
        "empty",
        "sealed_resume",
        "installed_resume",
        "weak_to_strong",
        "gc_first",
        "gc_retry",
    )
    parser.add_argument(
        "--check", choices=("none", "existence", "format", "checksum"), default="none"
    )
    parser.add_argument("--cases", nargs="+", choices=cases, default=cases)
    parser.add_argument("--members", nargs="+", type=int, default=[16, 128])
    parser.add_argument("--history", type=int, default=8)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--parent", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if min(*args.members, args.history, args.repeats) < 1:
        parser.error("counts must be positive")
    started_at = datetime.now(UTC).isoformat()
    before = fingerprint()
    results = []
    for members in args.members:
        for durable in (False, True):
            for case in args.cases:
                if case == "weak_to_strong" and not durable:
                    continue
                samples = [
                    measure(args.parent, case, members, args.history, durable, "time", args.check)[
                        "seconds"
                    ]
                    for _ in range(args.repeats)
                ]
                result = dict(
                    case=case,
                    members=members,
                    history=args.history,
                    durable=durable,
                    check=args.check if not case.startswith("gc_") else "not_applicable",
                    seconds=samples,
                    median_seconds=statistics.median(samples),
                )
                result.update(
                    measure(args.parent, case, members, args.history, durable, "probe", args.check)
                )
                result.update(
                    measure(args.parent, case, members, args.history, durable, "memory", args.check)
                )
                results.append(result)
                print(case, members, durable, flush=True)
    assert before == fingerprint(), "source changed during benchmark"
    report = dict(
        started_at=started_at,
        completed_at=datetime.now(UTC).isoformat(),
        asterstore_version=version("asterstore"),
        command=sys.argv,
        python=sys.version,
        platform=platform.platform(),
        source_sha=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        source_sha256=before,
        filesystem=subprocess.check_output(
            ["findmnt", "-T", str(args.parent), "-no", "SOURCE,FSTYPE,TARGET"], text=True
        ).strip(),
        payload_bytes_per_new_object=128,
        check=args.check,
        notes=[
            "Independent fresh repository per timing sample, probe, and memory measurement.",
            "No cache eviction; recently produced data/control, uncontrolled OS cache and load.",
            "Setup excluded; incremental timing includes reuse protection + commit.",
            "Recovery injects an exception after install; not process kill or power loss.",
            "Python call counts; inclusive timings overlap; tracemalloc memory is not RSS.",
            "GC durable flag applies to setup; GC retains its existing sync policy.",
            "Setup writer lock excluded; gc lock intervals counted; no contention injected.",
            "Storage device/cache durability not qualified by this benchmark.",
            "Explicit checks apply only to measured commit; setup retains default checks.",
            "Checksum baselines derive from known produced bytes, not reads of target data.",
            "Payload read counts cover Path.read_bytes (format), not checksum streaming reads.",
        ],
        results=results,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
