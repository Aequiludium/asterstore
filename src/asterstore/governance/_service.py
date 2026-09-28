"""governance facade. Service construction has no I/O."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from asterstore.errors import CandidateStateError, PublicationNotFoundError
from asterstore.metadata.capabilities import RetentionScope
from asterstore.metadata.protocol import FixedRetention, encode_managed_abandonment
from asterstore.reading import Binding
from asterstore.storage import file_lock, immutable_write
from asterstore.storage.registry import (
    find_record,
    lifecycle_store,
    managed_directory,
    read_managed_request,
)

from . import _references, inspection
from . import cleanup as _cleanup
from ._abandonment import sync_candidate_evidence
from ._collection import GovernancePreview, GovernanceResult, collect, preview
from .cleanup import CleanupPreview, CleanupResult
from .inspection import CheckLevel, CheckReport, GovernanceSnapshot, MaintenanceStatus
from .inspection._queries import maintenance_status


@dataclass(frozen=True, slots=True)
class Governance:
    root: Path

    def inspect(self) -> GovernanceSnapshot:
        """Read a coordinated control inventory; fail promptly if a writer holds the lock."""
        return inspection.inspect(self.root)

    def collection_status(self, operation_id: str) -> MaintenanceStatus:
        """Read one fixed collection plan and its progress, without a store inventory."""
        return maintenance_status(self.root, operation_id, "collection")

    def cleanup_status(self, operation_id: str) -> MaintenanceStatus:
        """Read one abandoned candidate's cleanup plan and progress."""
        return maintenance_status(self.root, operation_id, "cleanup")

    def check(
        self,
        *,
        level: CheckLevel = "metadata",
        resources: Mapping[str, str | Path] | None = None,
        validator: Callable[[Path], None] | None = None,
        checksums: Mapping[str, str] | None = None,
    ) -> CheckReport:
        """Explicit validation; never create control files, repair, or grant deletion rights."""
        return inspection.check(
            self.root,
            level=level,
            resources=resources,
            validator=validator,
            checksums=checksums,
        )

    def retain(
        self,
        name: str,
        dataset_id: str,
        publication_id: str,
        *,
        scope: RetentionScope,
        expected_revision: int = 0,
    ) -> FixedRetention:
        return _references.retain(
            self.root,
            name,
            dataset_id,
            publication_id,
            scope=scope,
            expected_revision=expected_revision,
        )

    def get(self, name: str) -> FixedRetention:
        return _references.get(self.root, name)

    def list_retentions(self, *, active_only: bool = False) -> tuple[FixedRetention, ...]:
        """Explicitly enumerate reference identities and revisions, without opening data."""
        return _references.list_retentions(self.root, active_only=active_only)

    def release(self, name: str, *, expected_revision: int) -> FixedRetention:
        return _references.release(self.root, name, expected_revision=expected_revision)

    def open(
        self,
        name: str,
        *,
        expected_revision: int,
        resources: Mapping[str, str | Path] | None = None,
    ) -> Binding:
        return _references.open_retained(
            self.root, name, expected_revision=expected_revision, resources=resources
        )

    def preview(self) -> GovernancePreview:
        return preview(self.root)

    def collect(self, operation_id: str) -> GovernanceResult:
        """Persist an exact plan, retire unprotected managed publications, then reclaim files."""
        return collect(self.root, operation_id)

    def resume_collection(self, operation_id: str) -> GovernanceResult:
        return collect(self.root, operation_id, resume=True)

    def abandon(self, operation_id: str) -> None:
        """Wait for any active writer, then permanently abandon uncommitted work; no deletion."""
        lifecycle_store(self.root)
        directory = managed_directory(self.root, operation_id)
        with file_lock(directory / "writer.lock", exclusive=True):
            with file_lock(self.root / ".asterstore/gc.lock", exclusive=True):
                store = lifecycle_store(self.root)
                request = read_managed_request(self.root, store, operation_id)
                if request is None:
                    raise PublicationNotFoundError("managed candidate does not exist")
                committed = find_record(
                    self.root, store, request.dataset_id, request.publication_id
                )
                if committed is not None and committed.operation_id == operation_id:
                    raise CandidateStateError("committed work cannot be abandoned")
                sync_candidate_evidence(self.root, store, request)
                immutable_write(
                    directory / "abandoned.json", encode_managed_abandonment(request), durable=True
                )

    def preview_cleanup(self, operation_id: str) -> CleanupPreview:
        """List private or installed files belonging to an explicitly abandoned candidate."""
        return _cleanup.preview(self.root, operation_id)

    def cleanup(self, operation_id: str) -> CleanupResult:
        """Persist one fixed cleanup plan for this abandoned candidate and execute it."""
        return _cleanup.cleanup(self.root, operation_id)

    def resume_cleanup(self, operation_id: str) -> CleanupResult:
        """Resume the original file list; never rediscover or add new targets."""
        return _cleanup.cleanup(self.root, operation_id, resume=True)
