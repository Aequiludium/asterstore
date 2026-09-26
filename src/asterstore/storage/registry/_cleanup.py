"""Cleanup records share the abandoned candidate's permanent identity."""

from pathlib import Path

from asterstore.errors import StoreCorruptionError
from asterstore.metadata.protocol import (
    ManagedCleanupPlan,
    ManagedCleanupProgress,
    StoreRecord,
    decode_managed_cleanup_plan,
    decode_managed_cleanup_progress,
)

from ._governance import read_abandoned
from ._managed import managed_directory


def read_cleanup_plan(
    root: Path, store: StoreRecord, operation_id: str
) -> ManagedCleanupPlan | None:
    path = managed_directory(root, operation_id) / "cleanup-plan.json"
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    value = decode_managed_cleanup_plan(data)
    if (
        value.request.store_id != store.store_id
        or value.request.operation_id != operation_id
        or read_abandoned(root, store, operation_id) != value.request
    ):
        raise StoreCorruptionError("cleanup plan lacks matching abandonment authority")
    return value


def read_cleanup_progress(root: Path, plan: ManagedCleanupPlan) -> ManagedCleanupProgress:
    path = managed_directory(root, plan.request.operation_id) / "cleanup-progress.json"
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return ManagedCleanupProgress(plan.request.store_id, plan.request.operation_id)
    value = decode_managed_cleanup_progress(data)
    value.check_plan(plan)
    return value
