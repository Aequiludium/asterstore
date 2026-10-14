"""Adopt existing bytes in place and publish to a caller-selected path."""

from pathlib import Path
from tempfile import TemporaryDirectory

from asterstore import Repository

with TemporaryDirectory() as temporary:
    base = Path(temporary)
    existing = base / "data" / "original.bin"
    existing.parent.mkdir()
    existing.write_bytes(b"existing bytes")
    original_inode = existing.stat().st_ino
    repo = Repository(base / "control")
    repo.initialize()
    with repo.prepare("table") as draft:
        draft.adopt("data", existing)
        draft.seal()
        draft.commit()
    assert repo.open("table").files() == (existing,)
    assert existing.stat().st_ino == original_inode

    destination = base / "data" / "next.bin"
    with repo.prepare("table", expected_revision=1) as draft:
        draft.allocate("data", path=destination).write_bytes(b"new bytes")
        draft.seal()
        head = draft.commit()
    assert repo.open("table").files() == (destination,)
    repo.rollback("table", expected_revision=head.revision)
    # No readers are active in this isolated example.
    repo.clean("table", quiescent=True)
    assert existing.read_bytes() == b"existing bytes"
    assert not destination.exists()
