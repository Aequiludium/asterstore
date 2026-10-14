"""Explicit candidate namespace inventory; observes bytes' names, never deletes them."""

import os
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from asterstore.errors import PublicationNotFoundError, StoreCorruptionError
from asterstore.metadata.protocol import DatasetTombstone, decode_declaration_record
from asterstore.publishing._status import CandidateStatus, status_from_records
from asterstore.storage._archive import read_control
from asterstore.storage.registry import (
    find_record,
    head_path,
    managed_data_root,
    managed_directory,
    marker_path,
    publication_path,
    read_abandoned,
    read_head_state,
    read_managed_request,
    read_retired,
    read_store,
    retired_path,
    token,
)

from ._locking import existing_lock, inspection_lock


@dataclass(frozen=True, slots=True)
class CandidateFileIssue:
    path: str
    kind: str


@dataclass(frozen=True, slots=True)
class CandidateInspection:
    status: CandidateStatus
    location: Literal["private", "installed", "absent"]
    observed_at: datetime
    declaration_known: bool
    declared_files: tuple[str, ...]
    observed_files: tuple[str, ...]
    missing_files: tuple[str, ...]
    undeclared_entries: tuple[str, ...]
    issues: tuple[CandidateFileIssue, ...]


def ordinary_directory(root: Path, path: Path) -> bool:
    current = root
    for part in ("", *path.relative_to(root).parts):
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            return False
        if not stat.S_ISDIR(mode):
            raise StoreCorruptionError(f"candidate parent is not an ordinary directory: {current}")
    return True


def control_entry(root: Path, path: Path) -> None:
    """Reject unsafe scoped controls before legacy registry readers open them."""
    if not ordinary_directory(root, path.parent):
        return
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return
    if not stat.S_ISREG(mode):
        raise StoreCorruptionError(f"candidate control is not an ordinary file: {path}")


def inspect_candidate(root: Path, operation_id: str) -> CandidateInspection:
    control_entry(root, marker_path(root))
    read_store(root)
    directory = managed_directory(root, operation_id)
    if not ordinary_directory(root, directory):
        raise PublicationNotFoundError("managed candidate does not exist")
    with existing_lock(directory / "writer.lock") as candidate_locked:
        if not candidate_locked:
            raise StoreCorruptionError("candidate has no coordination lock")
        with inspection_lock(root) as coordinated:
            if not coordinated:
                raise StoreCorruptionError("candidate lacks repository coordination lock")
            control_entry(root, marker_path(root))
            for name in ("request.json", "sealed.json", "abandoned.json"):
                control_entry(root, directory / name)
            store = read_store(root)
            request = read_managed_request(root, store, operation_id)
            if request is None:
                raise PublicationNotFoundError("managed candidate does not exist")
            try:
                sealed = decode_declaration_record(read_control(directory / "sealed.json"))
            except FileNotFoundError:
                sealed = None
            if sealed is not None:
                sealed.check_store(store)
                if sealed != request.declaration_record(sealed.declaration.files):
                    raise StoreCorruptionError("seal differs from candidate request")
            for path in (
                head_path(root, request.dataset_id),
                publication_path(root, request.dataset_id, request.publication_id),
                retired_path(root, request.dataset_id, request.publication_id),
            ):
                control_entry(root, path)
            head = read_head_state(root, store, request.dataset_id)
            current = None if isinstance(head, DatasetTombstone) else head
            committed = find_record(root, store, request.dataset_id, request.publication_id)
            status = status_from_records(
                request,
                current=current,
                decommissioned=isinstance(head, DatasetTombstone),
                committed=committed,
                sealed=sealed,
                abandoned=read_abandoned(root, store, operation_id) is not None,
                retired=(
                    committed is not None
                    and committed.operation_id == operation_id
                    and read_retired(root, store, committed) is not None
                ),
            )
        # The candidate lock excludes writers/cleanup. GC can still change installed
        # objects: this is an observation, not implicit retention or a GC authorization.
        private = directory / "files"
        installed = managed_data_root(root) / token(operation_id)
        has_private = ordinary_directory(root, private)
        has_installed = ordinary_directory(root, installed)
        if has_private and has_installed:
            raise StoreCorruptionError("both private and installed namespaces exist")
        location: Literal["private", "installed", "absent"] = (
            "private" if has_private else "installed" if has_installed else "absent"
        )
        base = private if has_private else installed
        prefix = token(operation_id) + "/"
        declared = (
            set()
            if sealed is None
            else {
                obj.locator.relative_path.removeprefix(prefix)
                for obj in sealed.declaration.files.objects
                if obj.locator.relative_path.startswith(prefix)
            }
        )
        expected_dirs = {
            str(parent) for name in declared for parent in Path(name).parents if str(parent) != "."
        }
        found: set[str] = set()
        entries: set[str] = set()
        issues: list[CandidateFileIssue] = []

        def scan(path: Path) -> None:
            with os.scandir(path) as children:
                for entry in children:
                    child = Path(entry.path)
                    relative = child.relative_to(base).as_posix()
                    entries.add(relative)
                    try:
                        mode = entry.stat(follow_symlinks=False).st_mode
                        if stat.S_ISDIR(mode):
                            scan(child)
                        elif stat.S_ISREG(mode):
                            found.add(relative)
                        else:
                            issues.append(CandidateFileIssue(relative, "invalid_type"))
                    except OSError as exc:
                        issues.append(CandidateFileIssue(relative, f"io_error: {exc}"))

        if location != "absent":
            try:
                scan(base)
            except OSError as exc:
                issues.append(CandidateFileIssue(".", f"io_error: {exc}"))
        return CandidateInspection(
            status,
            location,
            datetime.now(UTC),
            sealed is not None,
            tuple(sorted(declared)),
            tuple(sorted(found)),
            tuple(sorted(declared - found)),
            tuple(sorted(entries - declared - expected_dirs)) if sealed is not None else (),
            tuple(issues),
        )
