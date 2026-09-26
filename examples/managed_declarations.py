"""Independent simulation output: managed bytes and explicit logical members."""

from pathlib import Path
from tempfile import TemporaryDirectory

from asterstore import HistoryAccess, Repository


def main() -> None:
    with TemporaryDirectory(prefix="asterstore-managed-") as temporary:
        repo = Repository(Path(temporary))
        repo.initialize(
            store_id="simulation", resource_ids=["results"], managed_resource_id="results"
        )
        with repo.prepare_managed(
            "heat",
            publication_id="run:1",
            operation_id="produce:1",
            expected_generation=0,
            history=HistoryAccess.VERSIONED,
        ) as writer:
            writer.write_bytes("initial", b"initial temperature", relative_path="initial.bin")
            writer.commit()
        initial = repo.open("heat").files()[0]
        with repo.prepare_managed(
            "heat",
            publication_id="run:2",
            operation_id="produce:2",
            expected_generation=1,
            history=HistoryAccess.VERSIONED,
        ) as writer:
            writer.reuse("run:1", keys=["initial"])
            writer.write_bytes("final", b"final temperature", relative_path="final.bin")
            writer.seal()
        assert repo.managed_status("produce:2").state == "prepared"
        with Repository(repo.root).resume_managed("produce:2") as recovery:
            recovery.commit()
        result = repo.open("heat")
        assert result.files(keys=["initial"]) == (initial,)
        assert result.files(keys=["final"])[0].read_bytes() == b"final temperature"
        assert repo.open("heat", publication_id="run:1").files() == (initial,)
        print("Managed declarations: reused original bytes; recovered sealed publication.")


if __name__ == "__main__":
    main()
