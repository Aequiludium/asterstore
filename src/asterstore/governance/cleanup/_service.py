"""Explicit physical cleanup, after abandonment, under writer then root locks."""

import stat
from dataclasses import dataclass, replace
from pathlib import Path

from asterstore.errors import CandidateStateError, PublicationNotFoundError, StoreCorruptionError
from asterstore.metadata.protocol import (
    CleanupLocation,
    CleanupOutcome,
    ManagedCleanupPlan,
    ManagedCleanupProgress,
    ManagedRequest,
    StoreRecord,
    decode_declaration_record,
    encode_managed_cleanup_plan,
    encode_managed_cleanup_progress,
)
from asterstore.storage import (
    atomic_write,
    delete_owned_file,
    file_lock,
    immutable_write,
    sync_control_files,
)
from asterstore.storage.registry import (
    lifecycle_store,
    managed_data_root,
    managed_directory,
    read_abandoned,
    read_cleanup_plan,
    read_cleanup_progress,
    read_managed_request,
    token,
)

from .._inventory import Inventory, inventory


@dataclass(frozen=True, slots=True)
class CleanupPreview:
    operation_id: str
    location: CleanupLocation
    files: tuple[str, ...]
    planned: bool


@dataclass(frozen=True, slots=True)
class CleanupResult:
    operation_id: str
    location: CleanupLocation
    deleted_files: tuple[str, ...]
    missing_files: tuple[str, ...]
    complete: bool


def require_abandoned(root: Path, store: StoreRecord, operation_id: str) -> ManagedRequest:
    request = read_managed_request(root, store, operation_id)
    if request is None:
        raise PublicationNotFoundError("managed candidate does not exist")
    if read_abandoned(root, store, operation_id) is None:
        raise CandidateStateError("explicitly abandon the candidate before physical cleanup")
    return request


def check_users(view: Inventory, operation_id: str) -> None:
    if any(p.operation_id == operation_id for p in view.publications.values()):
        raise CandidateStateError("committed candidate cannot be cleaned")
    for obj in view.objects.values():
        if obj.creator_operation_id == operation_id and (
            obj.object_id in view.pending_objects or obj.object_id in view.object_users
        ):
            raise StoreCorruptionError("candidate's created object has another user")


def directory_exists(path: Path) -> bool:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    if not stat.S_ISDIR(mode):
        raise StoreCorruptionError(f"cleanup directory is not ordinary: {path}")
    return True


def scan_files(base: Path) -> tuple[str, ...]:
    """Walk only one proven namespace, rejecting links and special files before deletion."""
    if not directory_exists(base):
        return ()
    found = []
    pending = [base]
    while pending:
        for path in pending.pop().iterdir():
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                pending.append(path)
            elif stat.S_ISREG(mode):
                found.append(path.relative_to(base).as_posix())
            else:
                raise StoreCorruptionError(f"cleanup refuses non-regular file: {path}")
    return tuple(sorted(found))


def choose_plan(root: Path, request: ManagedRequest, view: Inventory) -> ManagedCleanupPlan:
    private = managed_directory(root, request.operation_id) / "files"
    installed = managed_data_root(root) / token(request.operation_id)
    # Inspect every directory from the control root before traversal; never follow a
    # substituted managed-data directory. The candidate chain was checked by inventory.
    directory_exists(managed_data_root(root))
    has_private, has_installed = directory_exists(private), directory_exists(installed)
    if has_private and has_installed:
        raise StoreCorruptionError("both private and installed candidate namespaces exist")
    if has_installed or (not has_private and (private.parent / "sealed.json").is_file()):
        try:
            seal = decode_declaration_record((private.parent / "sealed.json").read_bytes())
        except FileNotFoundError as exc:
            raise StoreCorruptionError(
                "installed cleanup requires sealed creation evidence"
            ) from exc
        prefix = token(request.operation_id) + "/"
        paths = tuple(
            sorted(
                obj.locator.relative_path.removeprefix(prefix)
                for obj in seal.declaration.files.objects
                if obj.locator.relative_path.startswith(prefix)
            )
        )
        if not set(scan_files(installed)) <= set(paths):
            raise StoreCorruptionError(
                "installed namespace contains files without creator evidence"
            )
        return ManagedCleanupPlan(request, "installed", paths)
    if any(o.creator_operation_id == request.operation_id for o in view.objects.values()):
        raise StoreCorruptionError("installed object evidence exists but namespace is missing")
    return ManagedCleanupPlan(request, "private", scan_files(private))


def preview(root: Path, operation_id: str) -> CleanupPreview:
    lifecycle_store(root)
    # Avoid manufacturing a lock-only directory for an unknown candidate.
    require_abandoned(root, lifecycle_store(root), operation_id)
    with file_lock(managed_directory(root, operation_id) / "writer.lock", exclusive=True):
        with file_lock(root / ".asterstore/gc.lock", exclusive=False):
            store = lifecycle_store(root)
            view = inventory(root, store)
            request = require_abandoned(root, store, operation_id)
            check_users(view, operation_id)
            existing = read_cleanup_plan(root, store, operation_id)
            plan = existing or choose_plan(root, request, view)
            return CleanupPreview(operation_id, plan.location, plan.files, existing is not None)


def result(plan: ManagedCleanupPlan, progress: ManagedCleanupProgress) -> CleanupResult:
    return CleanupResult(
        plan.request.operation_id,
        plan.location,
        tuple(p for p, s in progress.outcomes if s == "deleted"),
        tuple(p for p, s in progress.outcomes if s == "missing"),
        progress.complete,
    )


def execute(root: Path, plan: ManagedCleanupPlan, view: Inventory) -> CleanupResult:
    progress = read_cleanup_progress(root, plan)
    directory = managed_directory(root, plan.request.operation_id)
    # Deletion never outruns the durable request, abandonment, plan and ownership records.
    sync_control_files(root, tuple(view.control_paths | {directory / "cleanup-plan.json"}))
    if progress.complete:
        # A prior final replace may have succeeded before its directory fsync failed.
        # Retrying completion must establish durability, not merely observe the new bytes.
        return result(plan, progress)
    base = (
        directory / "files"
        if plan.location == "private"
        else managed_data_root(root) / token(plan.request.operation_id)
    )
    outcomes = dict(progress.outcomes)
    for path in plan.files:
        if path in outcomes:
            continue
        # Start at the control root so every owned ancestor is checked without following links.
        state: CleanupOutcome = (
            "deleted"
            if delete_owned_file(
                root / ".asterstore", (base / path).relative_to(root / ".asterstore").as_posix()
            )
            else "missing"
        )
        outcomes[path] = state
        count = len(outcomes)
        if count & (count - 1) == 0:
            progress = replace(progress, outcomes=tuple(outcomes.items()))
            atomic_write(
                directory / "cleanup-progress.json",
                encode_managed_cleanup_progress(progress),
                durable=True,
            )
    progress = replace(progress, outcomes=tuple(outcomes.items()), complete=True)
    atomic_write(
        directory / "cleanup-progress.json", encode_managed_cleanup_progress(progress), durable=True
    )
    return result(plan, progress)


def cleanup(root: Path, operation_id: str, *, resume: bool = False) -> CleanupResult:
    store = lifecycle_store(root)
    require_abandoned(root, store, operation_id)
    with file_lock(managed_directory(root, operation_id) / "writer.lock", exclusive=True):
        with file_lock(root / ".asterstore/gc.lock", exclusive=True):
            store = lifecycle_store(root)
            view = inventory(root, store)
            request = require_abandoned(root, store, operation_id)
            check_users(view, operation_id)
            plan = read_cleanup_plan(root, store, operation_id)
            if plan is None:
                if resume:
                    raise PublicationNotFoundError("candidate cleanup plan does not exist")
                plan = choose_plan(root, request, view)
                immutable_write(
                    managed_directory(root, operation_id) / "cleanup-plan.json",
                    encode_managed_cleanup_plan(plan),
                    durable=True,
                )
            return execute(root, plan, view)
