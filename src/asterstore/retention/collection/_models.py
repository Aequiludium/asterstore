"""Immutable preview results. A preview is never an executable deletion plan."""

from dataclasses import dataclass
from typing import Literal

from asterstore.metadata import CollectionPolicy, ObjectRef, PreviewIssue, Publication


@dataclass(frozen=True, slots=True)
class PublicationDecision:
    publication: Publication
    generation: int
    retire: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ObjectDecision:
    object: ObjectRef
    reclaimable: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CollectionPreview:
    policy: CollectionPolicy
    status: Literal["complete", "blocked"]
    publications: tuple[PublicationDecision, ...]
    objects: tuple[ObjectDecision, ...]
    issues: tuple[PreviewIssue, ...] = ()
    pending_operations: tuple[str, ...] = ()

    @property
    def retiring_publications(self) -> tuple[Publication, ...]:
        return tuple(item.publication for item in self.publications if item.retire)

    @property
    def reclaimable_objects(self) -> tuple[ObjectRef, ...]:
        return tuple(item.object for item in self.objects if item.reclaimable)


@dataclass(frozen=True, slots=True)
class CollectionResult:
    operation_id: str | None
    policy: CollectionPolicy
    status: Literal["complete", "blocked"]
    retired_publications: tuple[Publication, ...] = ()
    deleted_objects: tuple[ObjectRef, ...] = ()
    missing_objects: tuple[ObjectRef, ...] = ()
    protected_objects: tuple[ObjectRef, ...] = ()
    remaining_objects: tuple[ObjectRef, ...] = ()
    issues: tuple[PreviewIssue, ...] = ()
