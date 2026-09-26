"""Retire old publications and reclaim only files without remaining users."""

from pathlib import Path
from tempfile import TemporaryDirectory

from asterstore import CollectionPolicy, Dataset, PublicationRetiredError, Repository


def main() -> None:
    with TemporaryDirectory(prefix="asterstore-collection-") as temporary:
        repository = Repository(Path(temporary))
        dataset = Dataset("prices")
        with repository.prepare(dataset, publication_id="first") as candidate:
            candidate.write_bytes("day=01/data.csv", b"price\n10\n")
            candidate.write_bytes("day=02/data.csv", b"price\n20\n")
            first = candidate.commit()
        with repository.prepare(dataset, publication_id="second") as candidate:
            candidate.reuse(keys=[first.objects[0].key])
            candidate.write_bytes("day=02/data.csv", b"price\n21\n")
            second = candidate.commit()
        policy = CollectionPolicy(("prices",))
        preview = repository.retention.preview(policy)
        assert preview.reclaimable_objects == first.objects[1:]
        result = repository.retention.collect(policy)
        assert result.status == "complete" and result.operation_id is not None
        assert result.deleted_objects == first.objects[1:]
        assert not repository.bind(first).files()[1].exists()
        assert repository.open("prices").publication == second
        assert repository.bind(first).files()[0].read_bytes() == b"price\n10\n"
        # The same operation can be retried after an uncertain response.
        assert repository.retention.resume_collection(result.operation_id) == result
        try:
            repository.open("prices", publication_id="first")
        except PublicationRetiredError:
            print(
                "First publication retired; its shared partition remains readable through second."
            )
        else:
            raise AssertionError("retired publication was reopened")
        assert not repository.retention.preview(policy).reclaimable_objects


if __name__ == "__main__":
    main()
