"""Explicit expiry selection, recoverable offline transition and independent collection."""

from pathlib import Path
from tempfile import TemporaryDirectory

from asterstore import Repository, RetentionScope


def main() -> None:
    with TemporaryDirectory() as temporary:
        repo = Repository(Path(temporary))
        repo.initialize(
            store_id="example", resource_ids=["owned"], managed_resource_id="owned", lifecycle=True
        )
        for dataset in ("batch:2025-01", "batch:2025-02"):
            with repo.prepare(
                dataset,
                publication_id="v1",
                operation_id=f"publish:{dataset}",
                expected_generation=0,
            ) as writer:
                writer.write_bytes("part", b"data", relative_path="part.bin")
                writer.commit()
        repo.governance.retain("running-job", "batch:2025-01", "v1", scope=RetentionScope.OBJECTS)
        result = repo.governance.decommission(
            "expiry:2025", datasets={"batch:2025-01": 1, "batch:2025-02": 1}
        )
        assert result.complete and not result.conflicts
        assert repo.governance.resume_decommission("expiry:2025") == result
        assert repo.list_datasets() == ()
        assert len(repo.governance.collect("gc:unheld").deleted_objects) == 1
        assert repo.governance.open("running-job", expected_revision=1).files()[0].exists()
        repo.governance.release("running-job", expected_revision=1)
        assert len(repo.governance.collect("gc:released").deleted_objects) == 1
        assert repo.describe("batch:2025-01", publication_id="v1").generation == 1
        print("Dataset decommission: PASS")


if __name__ == "__main__":
    main()
