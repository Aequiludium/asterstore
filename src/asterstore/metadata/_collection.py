"""Persistent retirement evidence and bounded collection journals."""

from dataclasses import dataclass
from typing import Literal

from asterstore.errors import InvalidDeclarationError

from ._models import Dataset, ObjectRef, Publication
from ._records import PublishedRecord, object_creator, validate_candidate_id, validate_generation
from ._retention import CollectionPolicy, PreviewIssue
from ._versions import CURRENT_FORMAT_VERSION, validate_governance_version
from .identity import validate_record_identifiers


@dataclass(frozen=True, slots=True)
class RetiredRecord:
    dataset: Dataset
    publication_id: str
    generation: int
    candidate_id: str
    collection_id: str
    format_version: int = CURRENT_FORMAT_VERSION

    def __post_init__(self) -> None:
        validate_governance_version(self.format_version)
        Publication(self.dataset, self.publication_id, ())
        validate_record_identifiers(
            self.format_version,
            dataset_id=self.dataset.dataset_id,
            publication_id=self.publication_id,
        )
        validate_generation(self.generation, minimum=1)
        validate_candidate_id(self.candidate_id)
        validate_candidate_id(self.collection_id)

    @classmethod
    def from_record(cls, record: PublishedRecord, collection_id: str) -> "RetiredRecord":
        return cls(
            record.publication.dataset,
            record.publication.publication_id,
            record.generation,
            record.candidate_id,
            collection_id,
            format_version=record.format_version,
        )


@dataclass(frozen=True, slots=True)
class CollectionPlan:
    operation_id: str
    policy: CollectionPolicy
    targets: tuple[PublishedRecord, ...]
    objects: tuple[ObjectRef, ...]
    format_version: int = CURRENT_FORMAT_VERSION

    def __post_init__(self) -> None:
        validate_governance_version(self.format_version)
        validate_candidate_id(self.operation_id)
        if not isinstance(self.policy, CollectionPolicy):
            raise InvalidDeclarationError("expected collection policy")
        for name in self.policy.datasets:
            validate_record_identifiers(self.format_version, dataset_id=name)
        identities = set()
        for target in self.targets:
            identity = (target.publication.dataset.dataset_id, target.publication.publication_id)
            if (
                target.format_version != self.format_version
                or identity[0] not in self.policy.datasets
            ):
                raise InvalidDeclarationError(
                    "collection target is outside its versioned policy scope"
                )
            if identity in identities:
                raise InvalidDeclarationError("duplicate collection target")
            identities.add(identity)
        keys = [obj.key for obj in self.objects]
        if len(set(keys)) != len(keys):
            raise InvalidDeclarationError("duplicate collection object")
        for key in keys:
            object_creator(key)


@dataclass(frozen=True, slots=True)
class CollectionProgress:
    operation_id: str
    state: Literal["planned", "running", "blocked", "complete"] = "planned"
    retired: tuple[str, ...] = ()  # Candidate identities of targets retired by this operation.
    deleted: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    protected: tuple[str, ...] = ()
    issues: tuple[PreviewIssue, ...] = ()
    format_version: int = CURRENT_FORMAT_VERSION

    def __post_init__(self) -> None:
        validate_governance_version(self.format_version)
        validate_candidate_id(self.operation_id)
        if self.state not in ("planned", "running", "blocked", "complete"):
            raise InvalidDeclarationError("unknown collection state")
        for candidate_id in self.retired:
            validate_candidate_id(candidate_id)
        keys = self.deleted + self.missing + self.protected
        if len(set(keys)) != len(keys) or len(set(self.retired)) != len(self.retired):
            raise InvalidDeclarationError("duplicate collection progress membership")
        for key in keys:
            object_creator(key)
        if self.state == "complete" and self.issues:
            raise InvalidDeclarationError("completed collection cannot contain issues")
