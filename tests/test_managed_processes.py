"""Local processes: staged competing writers and termination around install/current."""

import multiprocessing
from pathlib import Path

import pytest

from asterstore import PublicationConflictError, Repository


@pytest.fixture(autouse=True)
def importable_workers(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))


def writer(root, pipe, op, stop=None):
    from asterstore.publishing.managed import _candidate
    from asterstore.publishing.transactions import _commit

    try:
        repo = Repository(root)
        with repo.prepare_managed(
            "data", publication_id=op, operation_id=op, expected_generation=0
        ) as candidate:
            candidate.write_bytes("member", op.encode(), relative_path="part.bin")
            if stop == "rename":
                original = _candidate.os.rename

                def rename(source, destination):
                    original(source, destination)
                    pipe.send("persisted")
                    pipe.recv()

                _candidate.os.rename = rename
            elif stop == "current":
                original = _commit.atomic_write

                def write(path, data, *, durable=True):
                    original(path, data, durable=durable)
                    if path.name == "current.json":
                        pipe.send("persisted")
                        pipe.recv()

                _commit.atomic_write = write
            else:
                candidate.seal()
                pipe.send("persisted" if stop == "seal" else "ready")
                pipe.recv()
            try:
                candidate.commit()
            except PublicationConflictError:
                pipe.send("conflict")
            else:
                pipe.send("committed")
    except BaseException as exc:
        pipe.send(("error", repr(exc)))
    finally:
        pipe.close()


def create(root):
    repo = Repository(root)
    repo.initialize(store_id="store", resource_ids=["owned"], managed_resource_id="owned")
    return repo


@pytest.mark.parametrize("point", ["seal", "rename", "current"])
def test_killed_writer_resumes_without_reproducing_bytes(tmp_path, point):
    repo = create(tmp_path)
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=writer, args=(str(tmp_path), child, "op", point))
    try:
        process.start()
        child.close()
        assert parent.poll(15)
        assert parent.recv() == "persisted"
        process.kill()
        process.join(15)
        assert process.exitcode is not None
        assert repo.managed_status("op").state == ("current" if point == "current" else "prepared")
        with repo.resume_managed("op") as candidate:
            record = candidate.commit()
        assert record.generation == 1
        assert repo.open("data").files()[0].read_bytes() == b"op"
    finally:
        if process.is_alive():
            process.kill()
        process.join(15)
        parent.close()
        child.close()


def test_competing_staged_writers_have_one_current(tmp_path):
    repo = create(tmp_path)
    context = multiprocessing.get_context("spawn")
    pipes = [context.Pipe() for _ in range(2)]
    processes = [
        context.Process(target=writer, args=(str(tmp_path), child, f"op{i}"))
        for i, (_, child) in enumerate(pipes)
    ]
    try:
        for process in processes:
            process.start()
        for parent, child in pipes:
            child.close()
            assert parent.poll(15)
            assert parent.recv() == "ready"
        for parent, _ in pipes:
            parent.send("commit")
        outcomes = []
        for parent, _ in pipes:
            assert parent.poll(15)
            outcomes.append(parent.recv())
        assert sorted(outcomes) == ["committed", "conflict"]
        record = repo.describe("data")
        assert record.generation == 1
        assert repo.open("data").files()[0].read_bytes() == record.operation_id.encode()
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
