"""Real local-process coordination; not a qualification test for NFS or power loss."""

import multiprocessing
from multiprocessing.connection import Connection
from pathlib import Path

import pytest

from asterstore import Dataset, PublicationConflictError, Repository


@pytest.fixture(autouse=True)
def _importable_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    # pytest's importlib mode does not add the test root to child interpreter paths.
    # Add only the test root, not src/: the library still comes from its installation.
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))


def _competing_writer(root: str, pipe: Connection, label: str) -> None:
    try:
        repository = Repository(root)
        with repository.prepare(Dataset("data"), publication_id=label, durable=False) as candidate:
            candidate.write_bytes("part.bin", label.encode())
            pipe.send(("ready", candidate.expected_generation))
            pipe.recv()
            try:
                candidate.commit()
            except PublicationConflictError:
                pipe.send(("conflict", candidate.candidate_id))
            else:
                pipe.send(("committed", candidate.candidate_id))
    except BaseException as exc:
        pipe.send(("error", repr(exc)))
    finally:
        pipe.close()


def _sealed_writer(root: str, pipe: Connection) -> None:
    with Repository(root).prepare(Dataset("data"), publication_id="recover-me") as candidate:
        candidate.write_bytes("nested/part.bin", b"survives-process-death")
        candidate.seal()
        pipe.send(candidate.candidate_id)
        pipe.recv()  # Parent kills the process while both cooperative locks are held.


def test_two_processes_from_the_same_generation_do_not_lose_updates(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    channels = [context.Pipe() for _ in range(2)]
    processes = [
        context.Process(target=_competing_writer, args=(str(tmp_path), child, f"p{index}"))
        for index, (_, child) in enumerate(channels)
    ]
    try:
        for process in processes:
            process.start()
        for parent, child in channels:
            child.close()
            assert parent.poll(15), "writer did not finish preparing"
            assert parent.recv() == ("ready", 0)
        for parent, _ in channels:
            parent.send("commit")
        outcomes = []
        for parent, _ in channels:
            assert parent.poll(15), "writer did not finish committing"
            outcomes.append(parent.recv())
        assert sorted(state for state, _ in outcomes) == ["committed", "conflict"]
        repository = Repository(tmp_path)
        assert repository.open("data").files()[0].read_bytes() in {b"p0", b"p1"}
        for state, candidate_id in outcomes:
            report = repository.candidate_status(candidate_id)
            assert report.current_generation == 1
            assert report.state == ("current" if state == "committed" else "conflict")
        for process in processes:
            process.join(15)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.kill()
            process.join(15)
        for parent, child in channels:
            parent.close()
            child.close()


def test_process_death_releases_locks_and_sealed_candidate_can_resume(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=_sealed_writer, args=(str(tmp_path), child))
    try:
        process.start()
        child.close()
        assert parent.poll(15), "writer did not seal its output"
        candidate_id = parent.recv()
        process.kill()
        process.join(15)
        assert not process.is_alive()
        repository = Repository(tmp_path)
        assert repository.candidate_status(candidate_id).state == "prepared"
        with repository.resume(candidate_id) as resumed:
            resumed.commit()
        assert repository.open("data").files()[0].read_bytes() == b"survives-process-death"
    finally:
        if process.is_alive():
            process.kill()
        process.join(15)
        parent.close()
        child.close()


def _competing_reference(root: str, pipe: Connection, publication_id: str) -> None:
    from asterstore import ReferenceConflictError

    service = Repository(root).retention
    pipe.send("ready")
    pipe.recv()
    try:
        held = service.retain("shared-name", "data", publication_id=publication_id, durable=False)
    except ReferenceConflictError:
        pipe.send(("conflict", publication_id))
    else:
        pipe.send(("retained", held.reference.publication_id))
    pipe.close()


def _preview_waiter(root: str, pipe: Connection) -> None:
    from contextlib import contextmanager

    from asterstore import CollectionPolicy
    from asterstore.retention.collection import _preview

    original = _preview.file_lock

    @contextmanager
    def observe_lock(path, *, exclusive):
        pipe.send("waiting-for-lock")
        with original(path, exclusive=exclusive):
            yield

    _preview.file_lock = observe_lock
    report = Repository(root).retention.preview(CollectionPolicy(("data",)))
    pipe.send(report.status)
    pipe.close()


def test_reference_creation_serializes_competing_processes(tmp_path, publish):
    from asterstore import PhysicalHistory

    publish("p1", history=PhysicalHistory.VERSIONED)
    publish("p2", history=PhysicalHistory.VERSIONED)
    context = multiprocessing.get_context("spawn")
    channels = [context.Pipe() for _ in range(2)]
    processes = [
        context.Process(target=_competing_reference, args=(str(tmp_path), child, f"p{index + 1}"))
        for index, (_, child) in enumerate(channels)
    ]
    try:
        for process in processes:
            process.start()
        for parent, child in channels:
            child.close()
            assert parent.poll(15) and parent.recv() == "ready"
        for parent, _ in channels:
            parent.send("retain")
        outcomes = []
        for parent, _ in channels:
            assert parent.poll(15)
            outcomes.append(parent.recv())
        assert sorted(state for state, _ in outcomes) == ["conflict", "retained"]
        winner = next(pid for state, pid in outcomes if state == "retained")
        record = Repository(tmp_path).retention.get("shared-name")
        assert record.revision == 1 and record.reference.publication_id == winner
        for process in processes:
            process.join(15)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.kill()
            process.join(15)
        for parent, child in channels:
            parent.close()
            child.close()


def test_preview_waits_for_active_writer_but_ordinary_reads_continue(tmp_path, publish):
    publish("current")
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=_preview_waiter, args=(str(tmp_path), child))
    repository = Repository(tmp_path)
    try:
        with repository.prepare(Dataset("data"), durable=False) as candidate:
            candidate.write_bytes("pending.bin", b"pending")
            candidate.seal()
            process.start()
            child.close()
            assert parent.poll(15) and parent.recv() == "waiting-for-lock"
            assert not parent.poll(0.2), "preview bypassed an active writer's root lock"
            assert repository.open("data").files()[0].read_bytes() == b"current"
        assert parent.poll(15) and parent.recv() == "complete"
        process.join(15)
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.kill()
        process.join(15)
        parent.close()
        child.close()


def _paused_current_retainer(root: str, pipe: Connection) -> None:
    from asterstore.retention.references import _operations

    original = _operations.atomic_write

    def pause_before_reference(path, data, *, durable=True):
        pipe.send("selected-current")
        pipe.recv()
        original(path, data, durable=durable)

    _operations.atomic_write = pause_before_reference
    held = Repository(root).retention.retain("current-reader", "data", durable=False)
    pipe.send(held.reference.publication_id)
    pipe.close()


def test_selecting_current_and_persisting_reference_hold_the_commit_lock(tmp_path, publish):
    publish("p1")
    context = multiprocessing.get_context("spawn")
    writer_parent, writer_child = context.Pipe()
    reader_parent, reader_child = context.Pipe()
    writer = context.Process(target=_competing_writer, args=(str(tmp_path), writer_child, "p2"))
    reader = context.Process(target=_paused_current_retainer, args=(str(tmp_path), reader_child))
    try:
        writer.start()
        writer_child.close()
        assert writer_parent.poll(15) and writer_parent.recv() == ("ready", 1)
        reader.start()
        reader_child.close()
        assert reader_parent.poll(15) and reader_parent.recv() == "selected-current"
        writer_parent.send("commit")
        assert not writer_parent.poll(0.2), "publication bypassed reference target coordination"
        reader_parent.send("persist-reference")
        assert reader_parent.poll(15) and reader_parent.recv() == "p1"
        assert writer_parent.poll(15) and writer_parent.recv()[0] == "committed"
        repository = Repository(tmp_path)
        assert repository.open("data").publication.publication_id == "p2"
        assert (
            repository.retention.open(
                "current-reader", expected_revision=1
            ).reference.publication_id
            == "p1"
        )
        for process in (writer, reader):
            process.join(15)
            assert process.exitcode == 0
    finally:
        for process in (writer, reader):
            if process.is_alive():
                process.kill()
            if process.pid is not None:
                process.join(15)
        for pipe in (writer_parent, writer_child, reader_parent, reader_child):
            pipe.close()


def _sealed_reusing_writer(root: str, pipe: Connection) -> None:
    with Repository(root).prepare(Dataset("data"), publication_id="reuse-recovery") as candidate:
        candidate.reuse()
        candidate.write_bytes("new.bin", b"new")
        candidate.seal()
        pipe.send(candidate.candidate_id)
        pipe.recv()


def test_process_death_preserves_sealed_shared_membership(tmp_path, publish):
    first = publish("p1")
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=_sealed_reusing_writer, args=(str(tmp_path), child))
    try:
        process.start()
        child.close()
        assert parent.poll(15), "writer did not seal shared membership"
        candidate_id = parent.recv()
        process.kill()
        process.join(15)
        assert not process.is_alive()
        repository = Repository(tmp_path)
        with repository.resume(candidate_id) as resumed:
            publication = resumed.commit()
        assert publication.objects[1:] == first.objects
        assert [path.read_bytes() for path in repository.bind(publication).files()] == [
            b"new",
            b"p1",
        ]
    finally:
        if process.is_alive():
            process.kill()
        process.join(15)
        parent.close()
        child.close()


def _paused_collector(root: str, pipe: Connection, point: str) -> None:
    from asterstore import CollectionPolicy
    from asterstore.retention.collection import _execution

    if point == "unlink":
        original_delete = _execution.delete_object

        def pause_after_unlink(root, key):
            removed = original_delete(root, key)
            pipe.send("unlinked")
            pipe.recv()
            return removed

        _execution.delete_object = pause_after_unlink
    else:
        original_write = _execution.atomic_write

        def pause_after_retirement(path, data, *, durable=True):
            original_write(path, data, durable=durable)
            if path.parent.name == "history":
                pipe.send("retired")
                pipe.recv()

        _execution.atomic_write = pause_after_retirement
    result = Repository(root).retention.collect(CollectionPolicy(("data",)))
    pipe.send(result.status)
    pipe.close()


def test_sigkill_after_unlink_recovers_plan_and_lagging_journal(tmp_path, publish):
    from asterstore import CollectionPolicy

    first = publish("p1")
    publish("p2")
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=_paused_collector, args=(str(tmp_path), child, "unlink"))
    try:
        process.start()
        child.close()
        assert parent.poll(15) and parent.recv() == "unlinked"
        process.kill()
        process.join(15)
        repository = Repository(tmp_path)
        preview = repository.retention.preview(CollectionPolicy(("data",)))
        assert preview.status == "blocked" and len(preview.pending_operations) == 1
        result = repository.retention.resume_collection(preview.pending_operations[0])
        assert result.status == "complete" and result.missing_objects == first.objects
        assert repository.open("data").files()[0].read_bytes() == b"p2"
    finally:
        if process.is_alive():
            process.kill()
        process.join(15)
        parent.close()
        child.close()


def _late_retainer(root: str, pipe: Connection) -> None:
    from contextlib import contextmanager

    from asterstore import PublicationRetiredError
    from asterstore.retention.references import _operations

    original = _operations.file_lock

    @contextmanager
    def observe(path, *, exclusive):
        if path.name == "gc.lock":
            pipe.send("waiting")
        with original(path, exclusive=exclusive):
            yield

    _operations.file_lock = observe
    try:
        Repository(root).retention.retain("late", "data", publication_id="p1")
    except PublicationRetiredError:
        pipe.send("retired")
    else:
        pipe.send("unexpected-success")
    pipe.close()


def test_collect_serializes_late_retainer_but_current_reads_continue(tmp_path, publish):
    from asterstore import PhysicalHistory

    publish("p1", history=PhysicalHistory.VERSIONED)
    publish("p2", history=PhysicalHistory.VERSIONED)
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    late_parent, late_child = context.Pipe()
    collector = context.Process(target=_paused_collector, args=(str(tmp_path), child, "retirement"))
    retainer = context.Process(target=_late_retainer, args=(str(tmp_path), late_child))
    try:
        collector.start()
        child.close()
        assert parent.poll(15) and parent.recv() == "retired"
        retainer.start()
        late_child.close()
        assert late_parent.poll(15) and late_parent.recv() == "waiting"
        assert not late_parent.poll(0.2)
        assert Repository(tmp_path).open("data").files()[0].read_bytes() == b"p2"
        parent.send("continue")
        assert parent.poll(15) and parent.recv() == "complete"
        assert late_parent.poll(15) and late_parent.recv() == "retired"
        for process in (collector, retainer):
            process.join(15)
            assert process.exitcode == 0
    finally:
        for process in (collector, retainer):
            if process.is_alive():
                process.kill()
            if process.pid is not None:
                process.join(15)
        for pipe in (parent, child, late_parent, late_child):
            pipe.close()


def _waiting_abandoner(root: str, candidate_id: str, pipe: Connection) -> None:
    from contextlib import contextmanager

    from asterstore import CandidateStateError
    from asterstore.publishing import _abandon

    original = _abandon.file_lock

    @contextmanager
    def observe(path, *, exclusive):
        pipe.send("waiting")
        with original(path, exclusive=exclusive):
            yield

    _abandon.file_lock = observe
    try:
        result = Repository(root).abandon(candidate_id)
    except CandidateStateError:
        pipe.send("committed-rejected")
    else:
        pipe.send(result.state)
    pipe.close()


@pytest.mark.parametrize("commit", [False, True])
def test_abandon_waits_for_writer_and_rechecks_commit_result(tmp_path, commit):
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = None
    repository = Repository(tmp_path)
    try:
        with repository.prepare(Dataset("data"), durable=False) as candidate:
            candidate.write_bytes("part.bin", b"data")
            candidate.seal()
            process = context.Process(
                target=_waiting_abandoner, args=(str(tmp_path), candidate.candidate_id, child)
            )
            process.start()
            child.close()
            assert parent.poll(15) and parent.recv() == "waiting"
            assert not parent.poll(0.2)
            if commit:
                candidate.commit()
        assert parent.poll(15)
        assert parent.recv() == ("committed-rejected" if commit else "abandoned")
        process.join(15)
        assert process.exitcode == 0
        assert repository.candidate_status(candidate.candidate_id).state == (
            "current" if commit else "abandoned"
        )
    finally:
        if process is not None:
            if process.is_alive():
                process.kill()
            process.join(15)
        parent.close()
        child.close()


def _paused_candidate_cleaner(root: str, candidate_id: str, pipe: Connection) -> None:
    from asterstore.retention.cleanup import _operations

    original = _operations.delete_candidate_file

    def pause_after_unlink(root, cid, key):
        result = original(root, cid, key)
        pipe.send("unlinked")
        pipe.recv()
        return result

    _operations.delete_candidate_file = pause_after_unlink
    Repository(root).retention.cleanup_candidate(candidate_id)
    pipe.close()


def test_sigkill_after_candidate_unlink_resumes_fixed_cleanup_plan(tmp_path):
    repository = Repository(tmp_path)
    with repository.prepare(Dataset("data"), durable=False) as candidate:
        candidate.write_bytes("a", b"a")
        candidate.write_bytes("b", b"b")
    repository.abandon(candidate.candidate_id)
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(
        target=_paused_candidate_cleaner, args=(str(tmp_path), candidate.candidate_id, child)
    )
    try:
        process.start()
        child.close()
        assert parent.poll(15) and parent.recv() == "unlinked"
        process.kill()
        process.join(15)
        report = repository.inspection.candidates()
        assert report.candidates[0].cleanup_state == "pending"
        result = repository.retention.cleanup_candidate(candidate.candidate_id)
        assert result.status == "complete"
        assert len(result.missing_files) == len(result.deleted_files) == 1
    finally:
        if process.is_alive():
            process.kill()
        process.join(15)
        parent.close()
        child.close()
