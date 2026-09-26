"""Independent simulation: keep explanations separately from reusable result bytes."""

from pathlib import Path
from tempfile import TemporaryDirectory

from asterstore import HistoryAccess, Repository, RetentionScope


def main() -> None:
    with TemporaryDirectory(prefix="asterstore-governance-") as temporary:
        repo = Repository(Path(temporary))
        repo.initialize(
            store_id="simulation",
            resource_ids=["results"],
            managed_resource_id="results",
            lifecycle=True,
        )
        for index in range(2):
            with repo.prepare_managed(
                "heat",
                publication_id=f"run:{index}",
                operation_id=f"produce:{index}",
                expected_generation=index,
                history=HistoryAccess.VERSIONED,
            ) as writer:
                writer.write_bytes(
                    "temperature", str(index).encode(), relative_path="temperature.bin"
                )
                writer.commit()
            if index == 0:
                task = repo.governance.retain(
                    "task:analysis", "heat", "run:0", scope=RetentionScope.OBJECTS
                )
                repo.governance.retain(
                    "audit:run:0", "heat", "run:0", scope=RetentionScope.METADATA
                )
        assert (
            repo.governance.open(task.name, expected_revision=task.revision).files()[0].read_bytes()
            == b"0"
        )
        assert not repo.governance.preview().reclaimable
        repo.governance.release(task.name, expected_revision=task.revision)
        preview = repo.governance.preview()
        assert len(preview.reclaimable) == 1
        result = repo.governance.collect("collect:old-run")
        assert result.complete and len(result.deleted_objects) == 1
        assert repo.governance.resume_collection(result.operation_id) == result
        assert repo.describe("heat", publication_id="run:0").declaration.publication_id == "run:0"
        assert repo.open("heat").files()[0].read_bytes() == b"1"
        print("Released task bytes reclaimed; audit declaration and current data remain.")


if __name__ == "__main__":
    main()
