"""Publish, reuse, roll back, and clean an isolated dataset."""

from tempfile import TemporaryDirectory

from asterstore import Repository

with TemporaryDirectory() as temporary:
    repo = Repository(temporary)
    repo.initialize()
    with repo.prepare("prices", expected_revision=0) as draft:
        draft.allocate("day-1").write_bytes(b"first day")
        candidate = draft.seal()
        assert candidate.files()[0].read_bytes() == b"first day"
        draft.commit()
    with repo.prepare("prices", expected_revision=1) as draft:
        draft.reuse_current()
        draft.allocate("day-2").write_bytes(b"second day")
        draft.seal()
        head = draft.commit()
    assert len(repo.open("prices").files()) == 2
    repo.rollback("prices", expected_revision=head.revision)
    assert len(repo.open("prices").files()) == 1
    # No readers or writers remain; do not use old bindings after this point.
    assert repo.clean("prices", quiescent=True).deleted_files == 2
