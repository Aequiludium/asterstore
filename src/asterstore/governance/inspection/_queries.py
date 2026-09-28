"""Bounded task status queries; deliberately not a full-store consistency audit."""

import stat
from pathlib import Path
from typing import Literal

from asterstore.errors import PublicationNotFoundError, StoreCorruptionError
from asterstore.storage.registry import (
    collection_directory,
    lifecycle_store,
    managed_directory,
    read_cleanup_plan,
    read_cleanup_progress,
    read_governance_plan,
    read_governance_progress,
)

from .._control import ControlReader
from ._locking import inspection_lock
from ._models import MaintenanceStatus


def maintenance_status(
    root: Path, operation_id: str, kind: Literal["collection", "cleanup"]
) -> MaintenanceStatus:
    directory = (
        collection_directory(root, operation_id)
        if kind == "collection"
        else managed_directory(root, operation_id)
    )
    with inspection_lock(root) as coordinated:
        store = lifecycle_store(root)
        reader = ControlReader()
        # A status query checks only the target's local records and their binding.
        # It does not establish the safety of any deletion or inspect payloads.
        try:
            reader.check_parents(root / ".asterstore", directory / "plan.json")
        except FileNotFoundError as exc:
            raise PublicationNotFoundError(f"{kind} operation does not exist") from exc
        names = (
            ("plan.json", "progress.json")
            if kind == "collection"
            else ("request.json", "abandoned.json", "cleanup-plan.json", "cleanup-progress.json")
        )
        for name in names:
            path = directory / name
            try:
                mode = path.lstat().st_mode
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(mode):
                raise StoreCorruptionError(f"control record is not ordinary: {path}")
        if kind == "collection":
            plan = read_governance_plan(root, store, operation_id)
            if plan is None:
                raise PublicationNotFoundError("collection operation does not exist")
            progress = read_governance_progress(root, plan)
            status = MaintenanceStatus(
                operation_id, kind, len(plan.objects), len(progress.outcomes), progress.complete
            )
        else:
            cleanup = read_cleanup_plan(root, store, operation_id)
            if cleanup is None:
                raise PublicationNotFoundError("cleanup operation does not exist")
            cleanup_progress = read_cleanup_progress(root, cleanup)
            status = MaintenanceStatus(
                operation_id,
                kind,
                len(cleanup.files),
                len(cleanup_progress.outcomes),
                cleanup_progress.complete,
            )
        if not coordinated:
            if (root / ".asterstore/gc.lock").exists():
                raise BlockingIOError("first writer started during inspection; retry")
            raise StoreCorruptionError("nonempty repository lacks its coordination lock")
        return status
