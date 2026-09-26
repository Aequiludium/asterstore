"""Explicit governance facade; creating it has no filesystem side effects."""

from dataclasses import dataclass
from pathlib import Path

from asterstore.metadata import CollectionPolicy, ReferenceRecord

from . import collection, references
from .cleanup import CandidateCleanupResult, cleanup_candidate
from .collection import CollectionPreview, CollectionResult
from .references import RetainedPublication


@dataclass(frozen=True, slots=True)
class Retention:
    root: Path

    def retain(
        self,
        name: str,
        dataset_id: str,
        *,
        publication_id: str | None = None,
        expected_revision: int = 0,
        durable: bool = True,
    ) -> RetainedPublication:
        return references.retain(
            self.root,
            name,
            dataset_id,
            publication_id=publication_id,
            expected_revision=expected_revision,
            durable=durable,
        )

    def get(self, name: str) -> ReferenceRecord | None:
        return references.get(self.root, name)

    def open(self, name: str, *, expected_revision: int) -> RetainedPublication:
        return references.open_reference(self.root, name, expected_revision=expected_revision)

    def release(
        self, name: str, *, expected_revision: int, durable: bool = True
    ) -> ReferenceRecord:
        return references.release(
            self.root, name, expected_revision=expected_revision, durable=durable
        )

    def preview(self, policy: CollectionPolicy) -> CollectionPreview:
        return collection.preview(self.root, policy)

    def collect(self, policy: CollectionPolicy) -> CollectionResult:
        return collection.collect(self.root, policy)

    def resume_collection(self, operation_id: str) -> CollectionResult:
        return collection.resume_collection(self.root, operation_id)

    def cleanup_candidate(self, candidate_id: str) -> CandidateCleanupResult:
        return cleanup_candidate(self.root, candidate_id)
