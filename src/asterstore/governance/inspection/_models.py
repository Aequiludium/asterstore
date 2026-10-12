"""Immutable diagnostic observations, never new persistence authorities."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from asterstore.metadata.protocol import DeclarationRecord, FixedRetention, ObjectRecord
from asterstore.publishing import CandidateStatus

CheckLevel = Literal["metadata", "existence", "format", "checksum"]


@dataclass(frozen=True, slots=True)
class ProtectionReason:
    kind: Literal["current", "retention", "candidate", "candidate_cleanup"]
    dataset_id: str
    publication_id: str
    reference_name: str | None = None
    operation_id: str | None = None


@dataclass(frozen=True, slots=True)
class ObjectExplanation:
    record: ObjectRecord
    reasons: tuple[ProtectionReason, ...]
    reclaimable: bool
    collected: bool
    cleaned: bool


@dataclass(frozen=True, slots=True)
class PublicationStatus:
    record: DeclarationRecord
    state: Literal["current", "historical", "retired"]
    objects_protected: bool
    metadata_retained: bool


@dataclass(frozen=True, slots=True)
class MaintenanceStatus:
    operation_id: str
    kind: Literal["collection", "cleanup"]
    planned_objects: int
    processed_objects: int
    complete: bool


@dataclass(frozen=True, slots=True)
class GovernanceSnapshot:
    store_id: str
    observed_at: datetime
    coordinated: bool
    publications: tuple[PublicationStatus, ...]
    candidates: tuple[CandidateStatus, ...]
    retentions: tuple[FixedRetention, ...]
    maintenance: tuple[MaintenanceStatus, ...]
    objects: tuple[ObjectExplanation, ...]

    def explain_object(self, object_id: str) -> ObjectExplanation:
        """Look up a registered object in this observation, without filesystem I/O."""
        for value in self.objects:
            if value.record.object_id == object_id:
                return value
        raise KeyError(object_id)


@dataclass(frozen=True, slots=True)
class CheckIssue:
    code: str
    message: str
    path: Path | None = None
    object_id: str | None = None


@dataclass(frozen=True, slots=True)
class CheckReport:
    requested_level: CheckLevel
    started_at: datetime
    finished_at: datetime
    coordinated: bool
    metadata_complete: bool
    target_objects: tuple[str, ...]
    checked_objects: tuple[str, ...]
    issues: tuple[CheckIssue, ...]

    @property
    def ok(self) -> bool:
        return self.metadata_complete and not self.issues
