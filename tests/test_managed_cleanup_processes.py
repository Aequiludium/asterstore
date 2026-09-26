"""Real process termination and competing writers/cleanup on a local filesystem."""

import multiprocessing
from pathlib import Path

import pytest

from asterstore import Repository


@pytest.fixture(autouse=True)
def importable_workers(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))


def setup(root):
    repo = Repository(root)
    repo.initialize(
        store_id="store", resource_ids=["owned"], managed_resource_id="owned", lifecycle=True
    )
    with repo.prepare(
        "data", publication_id="failed", operation_id="failed", expected_generation=0
    ) as writer:
        path = writer.write_bytes("k", b"partial", relative_path="data.bin")
    repo.governance.abandon("failed")
    return repo, path


def cleaner(root, pipe, point):
    from asterstore.governance.cleanup import _service

    try:
        name = {
            "plan": "immutable_write",
            "unlink": "delete_owned_file",
            "checkpoint": "atomic_write",
            "complete": "atomic_write",
        }[point]
        original = getattr(_service, name)

        def stop(*args, **kwargs):
            import json

            result = original(*args, **kwargs)
            matches = point in ("plan", "unlink") or (
                json.loads(args[1])["complete"] == (point == "complete")
            )
            if matches:
                pipe.send("persisted")
                pipe.recv()
            return result

        setattr(_service, name, stop)
        Repository(root).governance.cleanup("failed")
    except BaseException as exc:
        pipe.send(("error", repr(exc)))
    finally:
        pipe.close()


@pytest.mark.parametrize("point", ["plan", "unlink", "checkpoint", "complete"])
def test_sigkill_releases_both_locks_and_preserves_fixed_plan(tmp_path, point):
    repo, path = setup(tmp_path)
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=cleaner, args=(str(tmp_path), child, point))
    try:
        process.start()
        child.close()
        assert parent.poll(15) and parent.recv() == "persisted"
        process.kill()
        process.join(15)
        extra = path.with_name("later.bin")
        extra.write_bytes(b"not in plan")
        result = repo.governance.resume_cleanup("failed")
        assert result.complete and not path.exists()
        assert set(result.deleted_files + result.missing_files) == {"data.bin"}
        assert extra.read_bytes() == b"not in plan"
        assert repo.governance.resume_cleanup("failed") == result
        assert repo.governance.collect("gc").complete
    finally:
        if process.is_alive():
            process.kill()
        process.join(15)
        parent.close()
        child.close()


def active_writer(root, pipe):
    try:
        repo = Repository(root)
        with repo.prepare(
            "data", publication_id="active", operation_id="active", expected_generation=0
        ) as writer:
            writer.write_bytes("k", b"active", relative_path="data.bin")
            pipe.send("writing")
            pipe.recv()
        pipe.send("exited")
    except BaseException as exc:
        pipe.send(("error", repr(exc)))
    finally:
        pipe.close()


def abandon_and_cleanup(root, pipe):
    try:
        repo = Repository(root)
        pipe.send("attempting")
        repo.governance.abandon("active")
        result = repo.governance.cleanup("active")
        pipe.send((result.complete, result.deleted_files))
    except BaseException as exc:
        pipe.send(("error", repr(exc)))
    finally:
        pipe.close()


def unrelated_governance(root, pipe):
    try:
        pipe.send(Repository(root).governance.collect("unrelated-gc").complete)
    except BaseException as exc:
        pipe.send(("error", repr(exc)))
    finally:
        pipe.close()


def test_waiting_for_writer_does_not_hold_root_lock(tmp_path):
    setup(tmp_path)
    context = multiprocessing.get_context("spawn")
    wp, wc = context.Pipe()
    cp, cc = context.Pipe()
    gp, gc = context.Pipe()
    writer = context.Process(target=active_writer, args=(str(tmp_path), wc))
    cleaner = context.Process(target=abandon_and_cleanup, args=(str(tmp_path), cc))
    governor = context.Process(target=unrelated_governance, args=(str(tmp_path), gc))
    try:
        writer.start()
        wc.close()
        assert wp.poll(15) and wp.recv() == "writing"
        cleaner.start()
        cc.close()
        assert cp.poll(15) and cp.recv() == "attempting"
        assert not cp.poll(0.1)
        # GC acquires the root lock while the cleaner waits on the writer lock.
        governor.start()
        gc.close()
        assert gp.poll(15) and gp.recv() is True
        governor.join(15)
        assert governor.exitcode == 0
        wp.send("exit")
        assert wp.poll(15) and wp.recv() == "exited"
        assert cp.poll(15) and cp.recv() == (True, ("data.bin",))
        writer.join(15)
        cleaner.join(15)
        assert writer.exitcode == cleaner.exitcode == 0
    finally:
        for process in (writer, cleaner, governor):
            if process.pid is not None:
                if process.is_alive():
                    process.kill()
                process.join(15)
        for pipe in (wp, wc, cp, cc, gp, gc):
            pipe.close()


def concurrent_cleaner(root, pipe):
    try:
        pipe.send("ready")
        pipe.recv()
        pipe.send(Repository(root).governance.cleanup("failed"))
    except BaseException as exc:
        pipe.send(("error", repr(exc)))
    finally:
        pipe.close()


def test_concurrent_cleaners_observe_one_persisted_result(tmp_path):
    repo, path = setup(tmp_path)
    context = multiprocessing.get_context("spawn")
    pipes = [context.Pipe(), context.Pipe()]
    processes = [
        context.Process(target=concurrent_cleaner, args=(str(tmp_path), child))
        for _, child in pipes
    ]
    try:
        for process, (parent, child) in zip(processes, pipes, strict=True):
            process.start()
            child.close()
            assert parent.poll(15) and parent.recv() == "ready"
        for parent, _ in pipes:
            parent.send("start")
        outcomes = []
        for parent, _ in pipes:
            assert parent.poll(15)
            outcomes.append(parent.recv())
        for process in processes:
            process.join(15)
            assert process.exitcode == 0
        assert outcomes == [repo.governance.resume_cleanup("failed")] * 2
        assert not path.exists()
    finally:
        for process in processes:
            if process.pid is not None:
                if process.is_alive():
                    process.kill()
                process.join(15)
        for parent, child in pipes:
            parent.close()
            child.close()
