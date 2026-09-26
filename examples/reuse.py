"""Publish one changed partition while keeping another partition's original path."""

from pathlib import Path
from tempfile import TemporaryDirectory

from asterstore import CollectionPolicy, Dataset, Repository


def main() -> None:
    with TemporaryDirectory(prefix="asterstore-reuse-") as directory:
        repository = Repository(Path(directory))
        dataset = Dataset("prices")
        with repository.prepare(dataset, publication_id="p1") as candidate:
            candidate.write_bytes("day=01/prices.csv", b"price\n10\n")
            candidate.write_bytes("day=02/prices.csv", b"price\n20\n")
            first = candidate.commit()
        unchanged = first.objects[0]
        with repository.prepare(dataset, publication_id="p2") as candidate:
            candidate.reuse("p1", keys=[unchanged.key])
            candidate.write_bytes("day=02/prices.csv", b"price\n21\n")
            candidate.seal()
            recovery_id = candidate.candidate_id
        # A separate task can resume the exact sealed member list.
        with repository.resume(recovery_id) as candidate:
            second = candidate.commit()
        binding = repository.open("prices")
        assert binding.files(keys=[unchanged.key]) == repository.bind(first).files(
            keys=[unchanged.key]
        )
        assert second.objects[1] == unchanged
        assert binding.files()[0].read_bytes() == b"price\n21\n"
        report = repository.retention.preview(CollectionPolicy(("prices",)))
        assert report.status == "complete"
        assert report.retiring_publications == (first,)
        assert report.reclaimable_objects == (first.objects[1],)
        print(
            "One new partition; one shared original path; only the replaced object is reclaimable."
        )


if __name__ == "__main__":
    main()
