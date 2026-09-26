"""Control-only collection preview under repository-wide mutation coordination."""

from pathlib import Path

from asterstore.metadata import CollectionPolicy, PreviewIssue
from asterstore.storage import check_repository, control_directory, file_lock, scan_controls

from ._models import CollectionPreview
from ._reachability import calculate


def preview(root: Path, policy: CollectionPolicy) -> CollectionPreview:
    if not isinstance(policy, CollectionPolicy):
        raise TypeError("policy must be a CollectionPolicy")
    check_repository(root, writable=True)
    with file_lock(control_directory(root) / "gc.lock", exclusive=True):
        check_repository(root, writable=True)
        inventory = scan_controls(root)
        issues = list(inventory.issues)
        for dataset in policy.datasets:
            if dataset not in inventory.dataset_ids:
                issues.append(
                    PreviewIssue(".asterstore/datasets", f"unknown dataset in scope: {dataset}")
                )
        if inventory.pending_operations:
            issues.append(
                PreviewIssue(
                    ".asterstore/collections", "resume pending collection before planning another"
                )
            )
        if issues:
            return CollectionPreview(
                policy, "blocked", (), (), tuple(issues), inventory.pending_operations
            )
        return calculate(inventory, policy)
