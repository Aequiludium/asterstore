"""Administrative discovery exposes facts, without interpreting dataset names."""

from pathlib import Path

import pytest

from asterstore import HistoryAccess, Repository, RetentionScope, StoreCorruptionError
from asterstore.storage.registry import head_path, retention_path


def repository(tmp_path):
    repo = Repository(tmp_path)
    repo.initialize(resource_ids=["owned"], managed_resource_id="owned", lifecycle=True)
    return repo


def publish(repo, dataset="any/table", operation="op", generation=0):
    with repo.prepare(
        dataset,
        publication_id=operation,
        operation_id=operation,
        expected_generation=generation,
        history=HistoryAccess.VERSIONED,
    ) as writer:
        writer.write_bytes("member", b"bytes", relative_path="data.bin")
        writer.commit()


def test_discovery_and_retention_lifecycle(tmp_path):
    repo = repository(tmp_path)
    assert repo.list_datasets() == ()
    assert repo.governance.list_retentions() == ()
    publish(repo)
    publish(repo, "another", "op2")
    assert repo.list_datasets() == ("another", "any/table")
    ref = repo.governance.retain("task", "any/table", "op", scope=RetentionScope.OBJECTS)
    assert repo.governance.list_retentions(active_only=True) == (ref,)
    released = repo.governance.release("task", expected_revision=ref.revision)
    assert repo.governance.list_retentions() == (released,)
    assert repo.governance.list_retentions(active_only=True) == ()


def test_candidate_identity_survives_abandonment(tmp_path):
    repo = repository(tmp_path)
    with repo.prepare(
        "uncommitted", publication_id="version", operation_id="work", expected_generation=0
    ):
        status = repo.candidate_status("work")
        assert status.dataset_id == "uncommitted"
        assert status.publication_id == "version"
        assert status.expected_generation == 0
        assert status.state == "writing"
    assert repo.list_datasets() == ()
    repo.governance.abandon("work")
    abandoned = repo.candidate_status("work")
    assert abandoned.store_id == status.store_id
    assert abandoned.dataset_id == status.dataset_id
    assert abandoned.state == "abandoned"


def test_discovery_rejects_misplaced_records(tmp_path):
    repo = repository(tmp_path)
    publish(repo)
    ref = repo.governance.retain("task", "any/table", "op", scope=RetentionScope.OBJECTS)
    path = retention_path(tmp_path, ref.name)
    path.rename(path.with_name("wrong.json"))
    with pytest.raises(StoreCorruptionError, match="identity"):
        repo.governance.list_retentions()
    path = head_path(tmp_path, "any/table")
    path.parent.rename(path.parent.with_name("wrong"))
    with pytest.raises(StoreCorruptionError, match="identity"):
        repo.list_datasets()


def test_discovery_does_not_open_objects(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    publish(repo)
    files = repo.open("any/table").files()
    read = Path.read_bytes

    def guarded(path):
        assert path not in files
        return read(path)

    monkeypatch.setattr(Path, "read_bytes", guarded)
    assert repo.list_datasets() == ("any/table",)
    assert repo.governance.list_retentions() == ()
