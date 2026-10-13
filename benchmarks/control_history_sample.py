"""Reproduce D1 attribution on temporary synthetic histories; output JSON to stdout."""

import hashlib
import json
import platform
from pathlib import Path
from tempfile import TemporaryDirectory

from control_history import analyze

from asterstore import HistoryAccess, Repository


def fingerprints() -> dict[str, str]:
    root = Path(__file__).resolve().parents[1]
    paths = sorted((root / "src/asterstore").rglob("*.py")) + [
        root / "benchmarks/control_history.py",
        Path(__file__).resolve(),
    ]
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def main() -> None:
    sources = fingerprints()
    reports = []
    for history, objects, state in [
        (4, 8, "committed"),
        (32, 8, "committed"),
        (32, 64, "committed"),
        (32, 8, "collected"),
        (32, 8, "pending"),
        (4, 8, "cleaned"),
    ]:
        with TemporaryDirectory(prefix="asterstore-d1-") as directory:
            root = Path(directory)
            repo = Repository(root)
            repo.initialize(
                store_id="d1-sample",
                resource_ids=["owned"],
                managed_resource_id="owned",
                lifecycle=True,
            )
            for number in range(history):
                with repo.prepare(
                    "data",
                    publication_id=f"p{number}",
                    operation_id=f"op{number}",
                    expected_generation=number,
                    history=HistoryAccess.VERSIONED,
                    durable=False,
                ) as writer:
                    for index in range(objects):
                        writer.write_bytes(str(index), b"synthetic", relative_path=f"{index}.bin")
                    writer.commit()
            if state == "collected":
                repo.governance.collect("gc")
            if state in {"pending", "cleaned"}:
                with repo.prepare(
                    "data",
                    publication_id="pending",
                    operation_id="pending",
                    expected_generation=history,
                    history=HistoryAccess.VERSIONED,
                    durable=False,
                ) as writer:
                    writer.reuse(f"p{history - 1}")
                    writer.seal()
                if state == "cleaned":
                    repo.governance.abandon("pending")
                    repo.governance.cleanup("pending")
            reports.append(
                {
                    "history": history,
                    "objects_per_publication": objects,
                    "state": state,
                    "analysis": analyze(root, quiescent=True),
                }
            )
    if sources != fingerprints():
        raise RuntimeError("source changed during measurement")
    print(
        json.dumps(
            {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "source_sha256": sources,
                "cases": reports,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
