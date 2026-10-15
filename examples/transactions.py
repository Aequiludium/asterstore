"""Five changed partitions, one publication and one undo; temporary data only."""

from pathlib import Path
from tempfile import TemporaryDirectory

from asterstore import Repository

with TemporaryDirectory() as temporary:
    root = Path(temporary)
    repo = Repository(root / "registry")
    repo.initialize()
    data = repo.create_dataset("market", root / "market")
    with data.write(expected_revision=0) as batch:
        for day in range(8, 11):
            batch.stage(f"date=2026-10-{day:02d}/data").write_bytes(b"old")
        batch.commit(metadata={"through": "2026-10-10"}, quiescent=True)
    with data.write(expected_revision=1) as batch:
        for day in range(8, 13):
            batch.stage(f"date=2026-10-{day:02d}/data").write_bytes(b"new")
        batch.commit(metadata={"through": "2026-10-12"}, quiescent=True)
    data.clean()
    data.rollback(quiescent=True, expected_revision=2)
    assert data.path("date=2026-10-08/data").read_bytes() == b"old"
    assert not data.path("date=2026-10-12/data").exists()
    data.clean()
    print("Five-partition publication and rollback: PASS")
