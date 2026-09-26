import io
import json
import os
from pathlib import Path

import pytest

from asterstore import (
    CollectionPolicy,
    Dataset,
    InvalidDeclarationError,
    PhysicalHistory,
    Repository,
)
from asterstore.storage import candidate_directory, history_path, reference_path


def test_independent_references_and_current_preserve_publications(tmp_path, publish):
    first = publish("p1")
    repository = Repository(tmp_path)
    service = repository.retention
    service.retain("task/a", "data", durable=False)
    service.retain("task/b", "data", durable=False)
    second = publish("p2")
    policy = CollectionPolicy(("data",))
    assert service.preview(policy).retiring_publications == ()
    service.release("task/a", expected_revision=1, durable=False)
    report = service.preview(policy)
    assert report.status == "complete" and not report.retiring_publications
    service.release("task/b", expected_revision=1, durable=False)
    report = service.preview(policy)
    assert report.retiring_publications == (first,)
    assert report.reclaimable_objects == first.objects
    assert all(path.exists() for path in repository.bind(first).files())
    assert repository.open("data").publication == second


def test_recent_window_adds_to_references_and_respects_dataset_scope(tmp_path, publish):
    for pid in ("p1", "p2", "p3", "p4"):
        publish(pid, history=PhysicalHistory.VERSIONED)
    publish("other-1", "other")
    publish("other-2", "other")
    service = Repository(tmp_path).retention
    service.retain("historic", "data", publication_id="p1", durable=False)
    report = service.preview(CollectionPolicy(("data",), keep_last=2))
    assert report.status == "complete"
    assert [pub.publication_id for pub in report.retiring_publications] == ["p2"]
    reasons = {item.publication.publication_id: item.reasons for item in report.publications}
    assert "reference:historic" in reasons["p1"]
    assert "keep_last" in reasons["p3"]
    assert "outside_scope" in reasons["other-1"]


def test_preview_recomputes_after_new_reference(tmp_path, publish):
    publish("p1", history=PhysicalHistory.VERSIONED)
    publish("p2", history=PhysicalHistory.VERSIONED)
    service = Repository(tmp_path).retention
    policy = CollectionPolicy(("data",))
    assert len(service.preview(policy).retiring_publications) == 1
    service.retain("new", "data", publication_id="p1", durable=False)
    assert not service.preview(policy).retiring_publications


def test_sealed_conflict_candidate_is_protected_but_committed_receipt_is_not(tmp_path, publish):
    first = publish("p1")
    repository = Repository(tmp_path)
    with repository.prepare(Dataset("data"), publication_id="pending", durable=False) as candidate:
        candidate.write_bytes("pending.bin", b"pending")
        candidate.seal()
        candidate_id = candidate.candidate_id
    publish("p2")
    report = repository.retention.preview(CollectionPolicy(("data",)))
    assert report.status == "complete"
    assert report.reclaimable_objects == first.objects
    protected = [item for item in report.objects if f"candidate:{candidate_id}" in item.reasons]
    assert len(protected) == 1 and not protected[0].reclaimable
    assert repository.candidate_status(candidate_id).state == "conflict"


@pytest.mark.parametrize(
    "damage",
    [
        "reference",
        "target",
        "history",
        "candidate",
        "identity",
        "mixed_version",
        "collection",
        "unknown_control",
    ],
)
def test_incomplete_or_corrupt_controls_block_preview(tmp_path, publish, damage):
    publish("p1")
    repository = Repository(tmp_path)
    held = repository.retention.retain("run", "data", durable=False)
    publish("p2")
    if damage == "reference":
        reference_path(tmp_path, "run").write_bytes(b"invalid")
    elif damage == "target":
        record = json.loads(reference_path(tmp_path, "run").read_bytes())
        record["publication_id"] = "missing"
        reference_path(tmp_path, "run").write_text(json.dumps(record))
    elif damage == "history":
        history_path(tmp_path, "data", "p1").unlink()
    elif damage == "candidate":
        candidate_id = held.binding.publication.objects[0].key.split("/")[2]
        (candidate_directory(tmp_path, candidate_id) / "state.json").unlink()
    elif damage == "identity":
        reference_path(tmp_path, "run").rename(reference_path(tmp_path, "another"))
    elif damage == "mixed_version":
        path = history_path(tmp_path, "data", "p1")
        record = json.loads(path.read_bytes())
        record["format_version"] = 1
        path.write_text(json.dumps(record))
    elif damage == "collection":
        (tmp_path / ".asterstore/collections/interrupted").mkdir(parents=True)
    else:
        (tmp_path / ".asterstore/unknown-protocol").write_bytes(b"unknown")
    report = repository.retention.preview(CollectionPolicy(("data",)))
    assert report.status == "blocked" and report.issues
    assert not report.reclaimable_objects
    assert not report.retiring_publications


def test_unknown_dataset_is_reported(tmp_path, publish):
    publish("p1")
    service = Repository(tmp_path).retention
    report = service.preview(CollectionPolicy(("typo",)))
    assert report.status == "blocked"
    assert "unknown dataset" in report.issues[0].message


def test_preview_and_retention_never_probe_data_and_weak_mode_never_fsyncs(
    tmp_path, publish, monkeypatch
):
    first = publish("p1")
    publish("p2")
    repository = Repository(tmp_path)
    with repository.prepare(Dataset("data"), durable=False) as candidate:
        candidate.write_bytes("pending.bin", b"pending")
        candidate.seal()
    objects = tmp_path / ".asterstore/objects"
    staging = tmp_path / ".asterstore/candidates" / candidate.candidate_id / "files"
    original_open = io.open

    def check_path(path):
        if isinstance(path, (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(path))
            assert not path.is_relative_to(objects), f"data object access: {path}"
            assert not path.is_relative_to(staging), f"staged data access: {path}"

    def observe_open(file, *args, **kwargs):
        check_path(file)
        return original_open(file, *args, **kwargs)

    def checked(original):
        def wrapper(path, *args, **kwargs):
            check_path(path)
            return original(path, *args, **kwargs)

        return wrapper

    def forbidden_sync(*args, **kwargs):
        raise AssertionError("durable=False must not fsync")

    with monkeypatch.context() as patch:
        patch.setattr(io, "open", observe_open)
        for name in ("stat", "lstat", "listdir", "scandir"):
            patch.setattr(os, name, checked(getattr(os, name)))
        patch.setattr(os, "fsync", forbidden_sync)
        held = repository.retention.retain("run", "data", durable=False)
        assert repository.retention.open("run", expected_revision=1) == held
        report = repository.retention.preview(CollectionPolicy(("data",)))
        repository.retention.release("run", expected_revision=1, durable=False)
    assert report.status == "complete"
    assert report.reclaimable_objects == first.objects


@pytest.mark.parametrize(
    "datasets,keep_last",
    [((), 1), (("data",), 0), (("data",), True), (("data", "data"), 1), ("data", 1)],
)
def test_policy_requires_explicit_valid_scope(datasets, keep_last):
    with pytest.raises(InvalidDeclarationError):
        CollectionPolicy(datasets, keep_last=keep_last)
