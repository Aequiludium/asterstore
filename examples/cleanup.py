"""A failed native writer can be abandoned and cleaned without touching reused objects."""

from pathlib import Path
from tempfile import TemporaryDirectory

from asterstore import HistoryAccess, Repository


def main() -> None:
    with TemporaryDirectory(prefix="asterstore-cleanup-example-") as temporary:
        repo = Repository(Path(temporary))
        repo.initialize(
            store_id="example", resource_ids=["owned"], managed_resource_id="owned", lifecycle=True
        )
        with repo.prepare(
            "measurements",
            publication_id="ready",
            operation_id="producer:1",
            expected_generation=0,
            history=HistoryAccess.VERSIONED,
        ) as writer:
            writer.write_bytes("existing", b"published data", relative_path="part.bin")
            writer.commit()
        source = repo.open("measurements").files()[0]
        with repo.prepare(
            "measurements",
            publication_id="failed",
            operation_id="producer:2",
            expected_generation=1,
            history=HistoryAccess.VERSIONED,
        ) as writer:
            writer.reuse("ready")
            partial = writer.path("new", relative_path="native/partial.bin")
            partial.write_bytes(b"incomplete output")
            # Simulate an interrupted native writer; context exit does not commit.
        repo.governance.abandon("producer:2")
        preview = repo.governance.preview_cleanup("producer:2")
        assert preview.location == "private" and preview.files == ("native/partial.bin",)
        outcome = repo.governance.cleanup("producer:2")
        assert outcome.complete and not partial.exists()
        assert repo.governance.resume_cleanup("producer:2") == outcome
        assert source.read_bytes() == b"published data"
        assert repo.open("measurements").files() == (source,)
        print("failed candidate cleanup: PASS")


if __name__ == "__main__":
    main()
