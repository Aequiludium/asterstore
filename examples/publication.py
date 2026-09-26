"""Publish, reopen, and resume a sealed candidate in a temporary local repository."""

from pathlib import Path
from tempfile import TemporaryDirectory

from asterstore import Dataset, PhysicalHistory, Repository


def main() -> None:
    with TemporaryDirectory(prefix="asterstore-example-") as temporary:
        repository = Repository(Path(temporary))
        dataset = Dataset("example/prices", PhysicalHistory.VERSIONED)
        with repository.prepare(dataset, publication_id="first") as candidate:
            candidate.write_bytes("part.csv", b"asset,value\nA,1\n")
            candidate.commit()
        first = repository.open(dataset.dataset_id)

        with repository.prepare(dataset, publication_id="second") as candidate:
            candidate.write_bytes("part.csv", b"asset,value\nA,2\n")
            candidate.seal()
            candidate_id = candidate.candidate_id
        print("Before recovery:", repository.candidate_status(candidate_id).state)
        with repository.resume(candidate_id) as resumed:
            resumed.commit()

        latest = repository.open(dataset.dataset_id)
        historical = repository.open(dataset.dataset_id, publication_id="first")
        assert historical.publication == first.publication
        print("First:", first.files()[0].read_text(encoding="utf-8").strip())
        print("Latest:", latest.files()[0].read_text(encoding="utf-8").strip())


if __name__ == "__main__":
    main()
