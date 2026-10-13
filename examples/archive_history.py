"""Explicit offline control compaction; all writers are closed before maintenance."""

from pathlib import Path
from tempfile import TemporaryDirectory

from asterstore import HistoryAccess, Repository

with TemporaryDirectory(prefix="asterstore-archive-example-") as temporary:
    repo = Repository(Path(temporary))
    repo.initialize(
        store_id="archive-example",
        resource_ids=["owned"],
        managed_resource_id="owned",
        lifecycle=True,
    )
    for generation in range(3):
        with repo.prepare(
            "data",
            publication_id=f"p{generation}",
            operation_id=f"op{generation}",
            expected_generation=generation,
            history=HistoryAccess.VERSIONED,
            durable=False,
        ) as writer:
            for index in range(8):
                writer.write_bytes(str(index), b"data", relative_path=f"{index}.bin")
            writer.commit()
    original = repo.describe("data", publication_id="p0")
    result = repo.governance.compact("archive1", quiescent=True)
    assert result == repo.governance.compact("archive1", quiescent=True)
    assert repo.describe("data", publication_id="p0") == original
    assert len(repo.open("data").files()) == 8
    repo.governance.inspect()
    print(f"Archived {result.records} control records; scope savings {result.saved_bytes} bytes")
