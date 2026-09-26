"""Two independent consumers and a metadata-only collection preview."""

from pathlib import Path
from tempfile import TemporaryDirectory

from asterstore import CollectionPolicy, Dataset, Repository


def main() -> None:
    with TemporaryDirectory(prefix="asterstore-retention-example-") as temporary:
        repository = Repository(Path(temporary))
        dataset = Dataset("example/prices")
        with repository.prepare(dataset, publication_id="first") as candidate:
            candidate.write_bytes("part.csv", b"asset,value\nA,1\n")
            first = candidate.commit()
        alice = repository.retention.retain("research/alice", dataset.dataset_id)
        bob = repository.retention.retain("training/bob", dataset.dataset_id)
        with repository.prepare(dataset, publication_id="second") as candidate:
            candidate.write_bytes("part.csv", b"asset,value\nA,2\n")
            candidate.commit()

        policy = CollectionPolicy((dataset.dataset_id,))
        repository.retention.release("research/alice", expected_revision=alice.revision)
        protected = repository.retention.preview(policy)
        assert protected.status == "complete" and not protected.retiring_publications
        reopened = repository.retention.open("training/bob", expected_revision=bob.revision)
        assert reopened.binding.publication == first
        print("Bob still reads:", reopened.binding.files()[0].read_text().strip())

        repository.retention.release("training/bob", expected_revision=bob.revision)
        report = repository.retention.preview(policy)
        assert report.status == "complete" and report.retiring_publications == (first,)
        print("Would retire:", [pub.publication_id for pub in report.retiring_publications])
        print("Would reclaim:", len(report.reclaimable_objects), "file(s)")
        assert all(path.exists() for path in repository.bind(first).files())
        print("Preview left every file intact.")


if __name__ == "__main__":
    main()
