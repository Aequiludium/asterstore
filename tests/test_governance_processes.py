"""Local process races, active reuse and collector termination."""

import multiprocessing
from pathlib import Path

import pytest

from asterstore import HistoryAccess, PublicationRetiredError, Repository, RetentionScope


@pytest.fixture(autouse=True)
def importable_workers(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))


def setup(root):
    repo = Repository(root)
    repo.initialize(
        store_id="store", resource_ids=["owned"], managed_resource_id="owned", lifecycle=True
    )
    publish(repo, "p1", 0)
    path = repo.open("data").files()[0]
    return repo, path


def publish(repo, pid, generation):
    with repo.prepare_managed(
        "data",
        publication_id=pid,
        operation_id=pid,
        expected_generation=generation,
        history=HistoryAccess.VERSIONED,
    ) as writer:
        writer.write_bytes("key", pid.encode(), relative_path="data.bin")
        writer.commit()


def collector(root, pipe, point):
    from asterstore.retention.governance import _collection

    repo = Repository(root)
    try:
        name = "delete_owned_file" if point == "unlink" else "immutable_write"
        original = getattr(_collection, name)

        def stop(*args, **kwargs):
            result = original(*args, **kwargs)
            matches = (
                point == "unlink"
                or (point == "plan" and args[0].name == "plan.json")
                or (point == "retire" and "retired" in args[0].parts)
            )
            if matches:
                pipe.send("persisted")
                pipe.recv()
            return result

        setattr(_collection, name, stop)
        repo.governance.collect("gc")
    except BaseException as exc:
        pipe.send(("error", repr(exc)))
    finally:
        pipe.close()


@pytest.mark.parametrize("point", ["plan", "retire", "unlink"])
def test_process_kill_releases_gc_lock_and_resumes_exact_plan(tmp_path, point):
    repo, old = setup(tmp_path)
    publish(repo, "p2", 1)
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=collector, args=(str(tmp_path), child, point))
    try:
        process.start()
        child.close()
        assert parent.poll(15) and parent.recv() == "persisted"
        process.kill()
        process.join(15)
        result = repo.governance.resume_collection("gc")
        assert result.complete and not old.exists()
        assert repo.governance.resume_collection("gc") == result
        assert repo.open("data").files()[0].read_bytes() == b"p2"
    finally:
        if process.is_alive():
            process.kill()
        process.join(15)
        parent.close()
        child.close()


def pending_writer(root, pipe):
    try:
        repo = Repository(root)
        with repo.prepare_managed(
            "data",
            publication_id="pending",
            operation_id="pending",
            expected_generation=1,
            history=HistoryAccess.VERSIONED,
        ) as writer:
            writer.reuse("p1")
            pipe.send("protected")
            pipe.recv()
    except BaseException as exc:
        pipe.send(("error", repr(exc)))
    finally:
        pipe.close()


def test_unsealed_live_and_killed_writer_both_protect_reuse(tmp_path):
    repo, old = setup(tmp_path)
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=pending_writer, args=(str(tmp_path), child))
    try:
        process.start()
        child.close()
        assert parent.poll(15) and parent.recv() == "protected"
        publish(repo, "p2", 1)
        assert not repo.governance.collect("while-live").deleted_objects
        assert old.exists()
        process.kill()
        process.join(15)
        assert not repo.governance.collect("after-death").deleted_objects
        repo.governance.abandon("pending")
        assert repo.governance.collect("after-abandon").deleted_objects
        assert not old.exists()
    finally:
        if process.is_alive():
            process.kill()
        process.join(15)
        parent.close()
        child.close()


def contender(root, pipe, action):
    try:
        repo = Repository(root)
        pipe.send("ready")
        pipe.recv()
        if action == "retain":
            try:
                repo.governance.retain("reader", "data", "p1", scope=RetentionScope.OBJECTS)
            except PublicationRetiredError:
                pipe.send("retired")
            else:
                pipe.send("retained")
        else:
            pipe.send(bool(repo.governance.collect("race").deleted_objects))
    except BaseException as exc:
        pipe.send(("error", repr(exc)))
    finally:
        pipe.close()


def test_retention_acquisition_and_gc_are_serialized(tmp_path):
    repo, old = setup(tmp_path)
    publish(repo, "p2", 1)
    context = multiprocessing.get_context("spawn")
    pipes = [context.Pipe() for _ in range(2)]
    processes = [
        context.Process(target=contender, args=(str(tmp_path), child, action))
        for (_, child), action in zip(pipes, ("retain", "collect"), strict=True)
    ]
    try:
        for process in processes:
            process.start()
        for parent, child in pipes:
            child.close()
            assert parent.poll(15) and parent.recv() == "ready"
        for parent, _ in pipes:
            parent.send("go")
        outcomes = []
        for parent, _ in pipes:
            assert parent.poll(15)
            outcomes.append(parent.recv())
        assert outcomes in (["retained", False], ["retired", True])
        assert old.exists() == (outcomes[0] == "retained")
        for process in processes:
            process.join(15)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.kill()
            process.join(15)
        for parent, child in pipes:
            parent.close()
            child.close()
