"""Checks are explicit, state-aware, and independent from commit durability."""

import fcntl
import hashlib
import os
from pathlib import Path

import pytest

from asterstore import (
    Capabilities,
    Declaration,
    FileSet,
    HistoryAccess,
    Locator,
    Member,
    Object,
    PublicationNotFoundError,
    PublicationRetiredError,
    Repository,
    RepositoryBusyError,
    ResourceNotBoundError,
    StoreCorruptionError,
)
from asterstore.errors import DataCheckError
from asterstore.publishing import _candidate
from asterstore.publishing.transactions import _commit
from asterstore.storage.registry import managed_data_root, token


def repository(path):
    repo = Repository(path)
    repo.initialize(resource_ids=["owned", "external"], managed_resource_id="owned", lifecycle=True)
    return repo


def prepare(repo, op="p1", generation=0, durable=False):
    return repo.prepare(
        "data",
        publication_id=op,
        operation_id=op,
        expected_generation=generation,
        history=HistoryAccess.VERSIONED,
        durable=durable,
    )


def publish(repo, op="p1", generation=0):
    with prepare(repo, op, generation) as writer:
        writer.write_bytes("key", b"abc", relative_path="nested/part.bin")
        return writer.commit()


def fail_format(path):
    raise ValueError("invalid format")


def external_declaration():
    return Declaration(
        "external",
        "p1",
        FileSet(
            [Object("external-id", Locator("external", "part.bin"))], [Member("key", "external-id")]
        ),
        Capabilities.registered(),
    )


@pytest.mark.parametrize("durable", [False, True])
def test_sealed_checks_upgrade_and_recovery(tmp_path, durable):
    repo = repository(tmp_path)
    with prepare(repo, durable=durable) as writer:
        writer.write_bytes("key", b"abc", relative_path="part.bin")
        record = writer.seal()
        oid = record.declaration.files.objects[0].object_id
        with pytest.raises(DataCheckError) as error:
            writer.commit(check="checksum", checksums={oid: "0" * 64})
        assert error.value.code == "checksum_mismatch"
        assert error.value.commit_state == "not_attempted"
        assert error.value.durability == ("not_completed" if durable else "not_requested")
        with pytest.raises(PublicationNotFoundError):
            repo.describe("data")
    with repo.resume("p1", durable=durable) as writer:
        with pytest.raises(DataCheckError):
            writer.commit(check="format", validator=fail_format)
        assert (
            writer.commit(check="checksum", checksums={oid: hashlib.sha256(b"abc").hexdigest()})
            == record
        )


def test_installed_check_targets_and_extra_cleanup(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    original = _candidate.Candidate._install

    def interrupt(writer):
        original(writer)
        raise InterruptedError("installed")

    with prepare(repo) as writer:
        path = writer.path("key", relative_path="part.bin")
        path.write_bytes(b"abc")
        path.with_name("extra.bin").write_bytes(b"unknown")
        with monkeypatch.context() as m:
            m.setattr(_candidate.Candidate, "_install", interrupt)
            with pytest.raises(InterruptedError):
                writer.commit()
    calls = []
    with repo.resume("p1", durable=False) as writer:

        def reject(path):
            calls.append(path)
            fail_format(path)

        with pytest.raises(DataCheckError):
            writer.commit(check="format", validator=reject)
    assert calls == [managed_data_root(tmp_path) / token("p1") / "part.bin"]
    repo.governance.abandon("p1")
    view = repo.governance.inspect_candidate("p1")
    assert view.location == "installed" and view.status.state == "abandoned"
    assert view.undeclared_entries == ("extra.bin",)
    with pytest.raises(StoreCorruptionError, match="without creator evidence"):
        repo.governance.cleanup("p1")
    calls[0].with_name("extra.bin").rename(tmp_path / "quarantined.bin")
    assert repo.governance.cleanup("p1").complete
    assert (tmp_path / "quarantined.bin").read_bytes() == b"unknown"


@pytest.mark.parametrize("durable", [False, True])
def test_committed_retry_failure_does_not_roll_back_or_upgrade(tmp_path, durable):
    repo = repository(tmp_path)
    first = publish(repo)
    second = publish(repo, "p2", 1)
    with repo.resume("p1", durable=durable) as writer:
        with pytest.raises(DataCheckError) as error:
            writer.commit(check="format", validator=fail_format)
    assert error.value.commit_state == "committed"
    assert error.value.durability == ("not_completed" if durable else "not_requested")
    assert repo.describe("data") == second
    assert repo.describe("data", publication_id="p1") == first
    repo.governance.collect("gc")
    with repo.resume("p1", durable=False) as writer:
        with pytest.raises(PublicationRetiredError):
            writer.commit(check="existence")


def test_callback_runs_without_gc_lock_and_alias_is_checked_once(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    calls = []

    def validate(path):
        with (tmp_path / ".asterstore/gc.lock").open("rb") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        calls.append(path)

    with prepare(repo) as writer:
        writer.write_bytes("key", b"abc", relative_path="part.bin")
        writer.alias("alias", member="key")
        writer.commit(check="format", validator=validate)
    assert len(calls) == 1
    binding = repo.open("data", check="format", validator=validate)
    assert len(calls) == 2

    def forbidden(*args, **kwargs):
        raise AssertionError("binding selection attempted I/O")

    with monkeypatch.context() as m:
        m.setattr(Path, "stat", forbidden)
        m.setattr(Path, "read_bytes", forbidden)
        assert binding.files(keys=["alias"]) == binding.files()


def test_open_none_has_no_data_checks_but_explicit_open_detects_missing(tmp_path):
    repo = repository(tmp_path)
    publish(repo)
    repo.open("data").files()[0].unlink()
    assert repo.open("data").files()
    with pytest.raises(DataCheckError) as error:
        repo.open("data", check="existence")
    assert error.value.code == "missing"
    assert error.value.commit_state == "not_applicable"


def test_reuse_is_in_explicit_check_scope(tmp_path):
    repo = repository(tmp_path)
    publish(repo)
    original = repo.open("data").files()[0]
    with prepare(repo, "p2", 1) as writer:
        writer.reuse()
        original.unlink()
        with pytest.raises(DataCheckError) as error:
            writer.commit(check="existence")
        assert error.value.path == original
        assert error.value.commit_state == "not_attempted"
        writer.commit()  # No optional health checks of reused bytes.


def test_registered_default_and_concurrent_same_request_failure_state(tmp_path):
    repo = repository(tmp_path / "repo")
    declaration = external_declaration()
    ext = tmp_path / "external"
    ext.mkdir()
    (ext / "part.bin").write_bytes(b"abc")
    with pytest.raises(ResourceNotBoundError):
        repo.register(declaration, operation_id="op", expected_generation=0, check="existence")

    def concurrent_publish(path):
        # A second invocation commits while the first runs its callback outside coordination.
        repo.register(declaration, operation_id="op", expected_generation=0)
        fail_format(path)

    with pytest.raises(DataCheckError) as error:
        repo.register(
            declaration,
            operation_id="op",
            expected_generation=0,
            check="format",
            validator=concurrent_publish,
            resources={"external": ext},
        )
    assert error.value.commit_state == "committed"
    assert repo.resume_registration("op", check="existence", resources={"external": ext})
    (ext / "part.bin").unlink()
    assert repo.resume_registration("op", durable=False)  # Default never probes external bytes.
    with pytest.raises(DataCheckError) as error:
        repo.resume_registration("op", check="existence", resources={"external": ext})
    assert error.value.commit_state == "committed"


def test_check_failure_preserved_when_state_query_fails(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    with prepare(repo) as writer:
        writer.write_bytes("key", b"abc", relative_path="part.bin")

        def reject(path):
            def broken(*args):
                raise StoreCorruptionError("state unavailable")

            monkeypatch.setattr(_commit, "_committed", broken)
            fail_format(path)

        with pytest.raises(DataCheckError) as error:
            writer.commit(check="format", validator=reject)
    assert error.value.commit_state == "unknown"
    assert error.value.state_error == "state unavailable"
    assert isinstance(error.value.__cause__, ValueError)


@pytest.mark.parametrize(
    "params",
    [
        {"check": "bad"},
        {"check": "format"},
        {"check": "none", "validator": fail_format},
        {"check": "checksum"},
        {"check": "existence", "checksums": {}},
        {"check": "checksum", "checksums": {"x": "bad"}},
    ],
)
def test_invalid_options_rejected_before_repository_io(tmp_path, params):
    with pytest.raises(ValueError):
        Repository(tmp_path / "missing").open("data", **params)


def test_checksum_exact_keys_and_empty_declaration(tmp_path):
    repo = repository(tmp_path)
    with prepare(repo) as writer:
        with pytest.raises(ValueError, match="exactly match"):
            writer.commit(check="checksum", checksums={"extra": "0" * 64})
        writer.commit(check="checksum", checksums={})
    assert not repo.open("data", check="checksum", checksums={}).files()


@pytest.mark.parametrize("durable", [False, True])
@pytest.mark.parametrize("kind", ["missing", "fifo", "parent_symlink"])
def test_none_retains_minimum_install_constraints(tmp_path, durable, kind):
    repo = repository(tmp_path / "repo")
    with prepare(repo, durable=durable) as writer:
        path = writer.path("key", relative_path="nested/part.bin")
        if kind == "fifo":
            os.mkfifo(path)
        elif kind == "parent_symlink":
            outside = tmp_path / "outside"
            outside.mkdir()
            (outside / "part.bin").write_bytes(b"abc")
            path.parent.rmdir()
            path.parent.symlink_to(outside, target_is_directory=True)
        with pytest.raises(StoreCorruptionError):
            writer.commit()


def test_extra_commits_but_is_not_deleted_by_gc(tmp_path):
    repo = repository(tmp_path)
    with prepare(repo) as writer:
        path = writer.path("key", relative_path="part.bin")
        path.write_bytes(b"abc")
        path.with_name("extra.bin").write_bytes(b"keep")
        writer.commit()
    view = repo.governance.inspect_candidate("p1")
    assert view.declaration_known and view.status.state == "current"
    assert view.undeclared_entries == ("extra.bin",)
    publish(repo, "p2", 1)
    repo.governance.collect("gc")
    view = repo.governance.inspect_candidate("p1")
    assert view.status.state == "retired"
    assert view.missing_files == ("part.bin",)
    assert view.undeclared_entries == ("extra.bin",)
    assert (managed_data_root(tmp_path) / token("p1") / "extra.bin").read_bytes() == b"keep"


def test_candidate_inventory_is_readonly_busy_and_does_not_follow_links(tmp_path):
    repo = repository(tmp_path / "repo")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "hidden").write_bytes(b"private")
    with prepare(repo) as writer:
        path = writer.path("key", relative_path="part.bin")
        path.write_bytes(b"abc")
        path.with_name("link").symlink_to(outside, target_is_directory=True)
        writer.seal()
        with pytest.raises(RepositoryBusyError):
            repo.governance.inspect_candidate("p1")
    before = sorted(p.relative_to(repo.root) for p in repo.root.rglob("*"))
    view = repo.governance.inspect_candidate("p1")
    assert view.location == "private"
    assert view.undeclared_entries == ("link",)
    assert view.issues[0].kind == "invalid_type"
    assert "link/hidden" not in view.observed_files
    assert before == sorted(p.relative_to(repo.root) for p in repo.root.rglob("*"))
    with pytest.raises(PublicationNotFoundError):
        repo.governance.inspect_candidate("missing")
    assert before == sorted(p.relative_to(repo.root) for p in repo.root.rglob("*"))


def test_object_evidence_cache_is_scoped_to_each_coordinated_commit(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    first = publish(repo)
    oid = first.declaration.files.objects[0].object_id
    calls = []
    original = _commit.read_object_record

    def observe(root, store, object_id):
        calls.append(object_id)
        return original(root, store, object_id)

    with prepare(repo, "p2", 1) as writer:
        writer.reuse()
        monkeypatch.setattr(_commit, "read_object_record", observe)
        writer.commit()
        assert calls == [oid]
        writer.commit()
        assert calls == [oid, oid]  # Retrying must re-read rather than reuse stale evidence.


def test_inventory_unsealed_membership_is_explicitly_unknown(tmp_path):
    repo = repository(tmp_path)
    with prepare(repo) as writer:
        writer.write_bytes("key", b"abc", relative_path="part.bin")
    view = repo.governance.inspect_candidate("p1")
    assert view.status.state == "writing"
    assert not view.declaration_known
    assert view.observed_files == ("part.bin",)
    assert view.undeclared_entries == ()


def test_keyboard_interrupt_from_validator_is_not_wrapped(tmp_path):
    repo = repository(tmp_path)

    def interrupt(path):
        raise KeyboardInterrupt

    with prepare(repo) as writer:
        writer.write_bytes("key", b"abc", relative_path="part.bin")
        with pytest.raises(KeyboardInterrupt):
            writer.commit(check="format", validator=interrupt)
    with repo.resume("p1", durable=False) as writer:
        writer.commit(check="existence")


@pytest.mark.parametrize("record", ["request.json", "sealed.json", "format.json", "current.json"])
def test_candidate_inventory_rejects_special_controls_before_open(tmp_path, monkeypatch, record):
    repo = repository(tmp_path)
    publish(repo)
    if record == "format.json":
        path = tmp_path / ".asterstore/format.json"
    elif record == "current.json":
        path = tmp_path / ".asterstore/datasets" / token("data") / record
    else:
        path = tmp_path / ".asterstore/managed-candidates" / token("p1") / record
    path.unlink()
    os.mkfifo(path)
    original = Path.read_bytes

    def guard(candidate):
        if candidate == path:
            raise AssertionError("would block reading a FIFO")
        return original(candidate)

    monkeypatch.setattr(Path, "read_bytes", guard)
    with pytest.raises(StoreCorruptionError, match="not an ordinary file"):
        repo.governance.inspect_candidate("p1")
