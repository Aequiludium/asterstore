"""Local collection sample with shared objects; no NFS or throughput qualification."""

import json
import platform
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from asterstore import CollectionPolicy, Dataset, Publication, Repository


def main() -> None:
    with TemporaryDirectory(prefix="asterstore-collection-benchmark-") as temporary:
        root = Path(temporary)
        repo = Repository(root)
        dataset = Dataset("partitions")
        with repo.prepare(dataset) as candidate:
            for index in range(20):
                candidate.write_bytes(f"part-{index}.bin", b"x" * 128)
            candidate.commit()
        for _ in range(5):
            current = repo.open("partitions").publication
            assert isinstance(current, Publication)
            with repo.prepare(dataset) as candidate:
                candidate.reuse(keys=[obj.key for obj in current.objects[1:]])
                candidate.write_bytes("part-0.bin", b"y" * 128)
                candidate.commit()
        policy = CollectionPolicy(("partitions",))
        started = perf_counter()
        preview = repo.retention.preview(policy)
        preview_seconds = perf_counter() - started
        started = perf_counter()
        result = repo.retention.collect(policy)
        collect_seconds = perf_counter() - started
        assert result.status == "complete" and result.operation_id is not None
        assert len(result.retired_publications) == len(preview.retiring_publications) == 5
        assert len(result.deleted_objects) == 5
        assert all(path.read_bytes() for path in repo.open("partitions").files())
        started = perf_counter()
        assert repo.retention.resume_collection(result.operation_id) == result
        retry_seconds = perf_counter() - started
        record = {
            "python": sys.version,
            "platform": platform.platform(),
            "storage": "local temporary directory; device not isolated",
            "cache": "warm; source just written; no flushing",
            "durable_publications": True,
            "collection_durable": True,
            "published_generations": 6,
            "current_files": 20,
            "bytes_per_file": 128,
            "reused_files_per_update": 19,
            "retired_publications": len(result.retired_publications),
            "deleted_objects": len(result.deleted_objects),
            "deleted_declared_data_bytes": 5 * 128,
            "preview_seconds": preview_seconds,
            "collect_seconds_including_sync": collect_seconds,
            "completed_retry_seconds": retry_seconds,
            "measurement": "one uncontended sample; no claims about cold cache, power loss or NFS",
        }
        print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
