import pytest

from asterstore import (
    HistoryUnavailableError,
    InvalidDeclarationError,
    PhysicalHistory,
    ReferenceConflictError,
    ReferenceNotFoundError,
    Repository,
    StoreCorruptionError,
)
from asterstore.retention.references import _operations
from asterstore.storage import reference_path


def test_current_only_reference_keeps_selected_publication_and_retries(tmp_path, publish):
    first = publish("p1")
    service = Repository(tmp_path).retention
    held = service.retain("run/one", "data", durable=False)
    publish("p2")
    assert held.revision == 1
    assert held.binding.publication == first
    assert service.retain("run/one", "data", durable=False) == held
    assert service.open("run/one", expected_revision=1) == held
    with pytest.raises(HistoryUnavailableError):
        Repository(tmp_path).open("data", publication_id="p1")
    with pytest.raises(HistoryUnavailableError):
        service.retain("run/two", "data", publication_id="p1", durable=False)
    assert service.get("run/two") is None


def test_versioned_reference_can_choose_history(tmp_path, publish):
    first = publish("p1", history=PhysicalHistory.VERSIONED)
    publish("p2", history=PhysicalHistory.VERSIONED)
    held = Repository(tmp_path).retention.retain("historic", "data", publication_id="p1")
    assert held.binding.publication == first
    assert held.binding.files()[0].read_bytes() == b"p1"


def test_released_name_requires_revision_and_stale_requests_cannot_touch_reuse(tmp_path, publish):
    publish("p1")
    service = Repository(tmp_path).retention
    held = service.retain("run", "data", durable=False)
    released = service.release("run", expected_revision=held.revision, durable=False)
    assert released.state == "released" and released.revision == 2
    assert service.release("run", expected_revision=1, durable=False) == released
    with pytest.raises(ReferenceConflictError):
        service.retain("run", "data", durable=False)
    publish("p2")
    newer = service.retain("run", "data", expected_revision=2, durable=False)
    assert newer.revision == 3 and newer.reference.publication_id == "p2"
    with pytest.raises(ReferenceConflictError):
        service.release("run", expected_revision=1, durable=False)
    with pytest.raises(ReferenceConflictError):
        service.retain("run", "data", durable=False)
    with pytest.raises(ReferenceConflictError):
        service.open("run", expected_revision=1)
    assert service.open("run", expected_revision=3) == newer


def test_create_retry_requires_same_dataset_selection_and_explicit_target(tmp_path, publish):
    publish("p1", history=PhysicalHistory.VERSIONED)
    service = Repository(tmp_path).retention
    service.retain("run", "data", durable=False)
    with pytest.raises(ReferenceConflictError):
        service.retain("run", "data", publication_id="p1", durable=False)
    with pytest.raises(ReferenceConflictError):
        service.retain("run", "another", durable=False)
    service.retain("explicit", "data", publication_id="p1", durable=False)
    publish("p2", history=PhysicalHistory.VERSIONED)
    with pytest.raises(ReferenceConflictError):
        service.retain("explicit", "data", publication_id="p2", durable=False)


@pytest.mark.parametrize("after_write", [False, True])
@pytest.mark.parametrize("operation", ["retain", "release"])
def test_reference_write_ambiguity_is_recoverable(
    tmp_path, publish, monkeypatch, after_write, operation
):
    publish("p1")
    service = Repository(tmp_path).retention
    if operation == "release":
        service.retain("run", "data")
    original = _operations.atomic_write

    def interrupted(path, data, *, durable=True):
        if after_write:
            original(path, data, durable=durable)
        raise OSError("injected response loss")

    def invoke():
        if operation == "retain":
            return service.retain("run", "data")
        return service.release("run", expected_revision=1)

    with monkeypatch.context() as patch:
        patch.setattr(_operations, "atomic_write", interrupted)
        with pytest.raises(OSError):
            invoke()
    result = invoke()
    assert result.revision == (1 if operation == "retain" else 2)
    assert service.get("run").state == ("active" if operation == "retain" else "released")


def test_durable_retry_syncs_reference_and_selected_history_not_data(
    tmp_path, publish, monkeypatch
):
    publish("p1")
    service = Repository(tmp_path).retention
    held = service.retain("run", "data", durable=False)
    publish("p2")
    synced = []
    original = _operations.sync_control_files

    def observe(root, paths):
        synced.extend(paths)
        original(root, paths)

    monkeypatch.setattr(_operations, "sync_control_files", observe)
    assert service.retain("run", "data", durable=True) == held
    assert reference_path(tmp_path, "run") in synced
    assert any(path.parent.name == "history" for path in synced)
    assert not any(path in held.binding.files() for path in synced)


def test_reference_is_not_a_file_integrity_certificate(tmp_path, publish):
    publication = publish("p1")
    path = Repository(tmp_path).bind(publication).files()[0]
    path.unlink()
    held = Repository(tmp_path).retention.retain("run", "data", durable=False)
    assert held.binding.files() == (path,)
    with pytest.raises(FileNotFoundError):
        held.binding.files()[0].read_bytes()


def test_reference_target_corruption_is_not_silently_recreated(tmp_path, publish):
    publish("p1")
    service = Repository(tmp_path).retention
    service.retain("run", "data", durable=False)
    head = next((tmp_path / ".asterstore/datasets").glob("*/current.json"))
    head.unlink()
    with pytest.raises(StoreCorruptionError):
        service.retain("run", "data", durable=False)
    with pytest.raises(StoreCorruptionError):
        service.open("run", expected_revision=1)


@pytest.mark.parametrize("revision", [True, -1, 1.5, "0"])
def test_invalid_reference_revision_has_no_side_effects(tmp_path, revision):
    root = tmp_path / "uncreated"
    with pytest.raises(InvalidDeclarationError):
        Repository(root).retention.retain("run", "data", expected_revision=revision)
    assert not root.exists()


def test_absent_and_released_reference_queries(tmp_path, publish):
    publish("p1")
    service = Repository(tmp_path).retention
    assert service.get("absent") is None
    with pytest.raises(ReferenceNotFoundError):
        service.open("absent", expected_revision=1)
    with pytest.raises(ReferenceNotFoundError):
        service.release("absent", expected_revision=1)
    service.retain("run", "data", durable=False)
    service.release("run", expected_revision=1, durable=False)
    with pytest.raises(ReferenceConflictError):
        service.open("run", expected_revision=1)
