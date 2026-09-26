"""Diagnose a failed write, explicitly abandon it, then clean its private files."""

from pathlib import Path
from tempfile import TemporaryDirectory

from asterstore import CandidateAbandonedError, Dataset, Repository


def main() -> None:
    with TemporaryDirectory(prefix="asterstore-candidate-cleanup-") as temporary:
        repository = Repository(Path(temporary))
        with repository.prepare(Dataset("prices")) as candidate:
            candidate.write_bytes("partial/day.csv", b"incomplete output")
            candidate_id = candidate.candidate_id
            # Simulated producer failure: the context exits without seal or commit.
        report = repository.inspection.candidates()
        assert report.status == "complete"
        residual = report.candidates[0]
        assert residual.state == "writing" and residual.total_bytes > 0
        print("Private files before cleanup:", len(residual.files))
        repository.abandon(candidate_id)
        result = repository.retention.cleanup_candidate(candidate_id)
        assert result.status == "complete" and len(result.deleted_files) == 1
        assert repository.retention.cleanup_candidate(candidate_id) == result
        assert repository.inspection.candidates().candidates[0].files == ()
        try:
            with repository.resume(candidate_id):
                pass
        except CandidateAbandonedError:
            print("Abandoned candidate cannot resume; private bytes reclaimed; identity retained.")
        else:
            raise AssertionError("abandoned candidate resumed")


if __name__ == "__main__":
    main()
