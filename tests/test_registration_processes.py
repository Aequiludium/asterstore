"""Local multiprocess commit races and process death; no NFS qualification claim."""

import multiprocessing
from pathlib import Path

import pytest

from asterstore import (
    Capabilities,
    Declaration,
    FileSet,
    Locator,
    Member,
    Object,
    PublicationConflictError,
    Repository,
)


@pytest.fixture(autouse=True)
def importable_workers(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))


def value(pid):
    return Declaration(
        "simulation",
        pid,
        FileSet(
            [Object("object:1", Locator("external", "data.bin"))], [Member("sample", "object:1")]
        ),
        Capabilities.registered(),
    )


def writer(root, pipe, pid, crash_at=None):
    from asterstore.publishing.transactions import _commit as _operations

    try:
        repo = Repository(root)
        if crash_at:
            original = (
                _operations.atomic_write if crash_at == "current" else _operations.immutable_write
            )

            def stopped(path, data, *, durable=True):
                original(path, data, durable=durable)
                if (crash_at == "current" and path.name == "current.json") or (
                    crash_at == "object" and path.parent.name == "object-records"
                ):
                    pipe.send("persisted")
                    pipe.recv()

            if crash_at == "current":
                _operations.atomic_write = stopped
            else:
                _operations.immutable_write = stopped
        else:
            pipe.send("ready")
            pipe.recv()
        try:
            repo.register(value(pid), operation_id=pid, expected_generation=0)
        except PublicationConflictError:
            pipe.send("conflict")
        else:
            pipe.send("committed")
    except BaseException as exc:
        pipe.send(("error", repr(exc)))
    finally:
        pipe.close()


@pytest.mark.parametrize("same_request", [False, True])
def test_same_generation_race_has_one_winner(tmp_path, same_request):
    repo = Repository(tmp_path)
    repo.initialize(store_id="s", resource_ids=["external"])
    context = multiprocessing.get_context("spawn")
    channels = [context.Pipe() for _ in range(2)]
    processes = [
        context.Process(
            target=writer, args=(str(tmp_path), child, "same" if same_request else f"p{i}")
        )
        for i, (_, child) in enumerate(channels)
    ]
    try:
        for process in processes:
            process.start()
        for parent, child in channels:
            child.close()
            assert parent.poll(15)
            assert parent.recv() == "ready"
        for parent, _ in channels:
            parent.send("commit")
        outcomes = []
        for parent, _ in channels:
            assert parent.poll(15)
            outcomes.append(parent.recv())
        assert sorted(outcomes) == (
            ["committed", "committed"] if same_request else ["committed", "conflict"]
        )
        assert repo.describe("simulation").generation == 1
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


@pytest.mark.parametrize("crash_at", ["object", "current"])
def test_process_death_releases_lock_and_fixed_operation_resumes(tmp_path, crash_at):
    repo = Repository(tmp_path)
    repo.initialize(store_id="s", resource_ids=["external"])
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=writer, args=(str(tmp_path), child, "recover", crash_at))
    try:
        process.start()
        child.close()
        assert parent.poll(15)
        assert parent.recv() == "persisted"
        process.kill()
        process.join(15)
        assert process.exitcode is not None
        assert repo.registration_status("recover").state == (
            "committed" if crash_at == "current" else "planned"
        )
        result = repo.resume_registration("recover")
        assert result.declaration == value("recover")
        assert result.generation == 1
        assert repo.resume_registration("recover") == result
    finally:
        if process.is_alive():
            process.kill()
        process.join(15)
        parent.close()
        child.close()
