"""Repository entry point for lightweight bindings and explicit local publication."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import overload
from uuid import uuid4

from .errors import InvalidDeclarationError, RepositoryNotInitializedError
from .inspection import Inspection
from .metadata import Dataset, Declaration, Publication
from .metadata.capabilities import HistoryAccess
from .metadata.protocol import DeclarationRecord, ManagedRequest, StoreRecord
from .publishing import Candidate, CandidateStatus, abandon, candidate_status
from .publishing.managed import ManagedCandidate, ManagedCandidateStatus, managed_status
from .reading import Binding, describe, open_binding
from .registration import RegistrationStatus, register, registration_status, resume_registration
from .retention import Retention
from .retention.governance import Governance
from .storage import absolute_root
from .storage.registry import initialize_store, read_store


@dataclass(frozen=True, slots=True, init=False)
class Repository:
    """A repository root; construction and bind() do not create or inspect files.

    Persistent state is created only by explicit initialization or write operations.
    The root is not a security sandbox; callers cooperate on filesystem ownership.
    """

    root: Path

    def __init__(self, root: str | Path) -> None:
        object.__setattr__(self, "root", absolute_root(root))

    @overload
    def bind(self, publication: Publication) -> Binding[Publication]: ...

    @overload
    def bind(
        self, publication: Declaration, *, resources: Mapping[str, str | Path]
    ) -> Binding[Declaration]: ...

    def bind(
        self,
        publication: Publication | Declaration,
        *,
        resources: Mapping[str, str | Path] | None = None,
    ) -> Binding[Publication] | Binding[Declaration]:
        """Capture declared paths; no registration, ownership or retention is acquired."""
        if isinstance(publication, Declaration):
            return Binding(self.root, publication, resources=resources)
        return Binding(self.root, publication, resources=resources)

    @overload
    def open(
        self, dataset_id: str, *, publication_id: str | None = None, resources: None = None
    ) -> Binding[Publication] | Binding[Declaration]: ...

    @overload
    def open(
        self,
        dataset_id: str,
        *,
        publication_id: str | None = None,
        resources: Mapping[str, str | Path],
    ) -> Binding[Declaration]: ...

    def open(
        self,
        dataset_id: str,
        *,
        publication_id: str | None = None,
        resources: Mapping[str, str | Path] | None = None,
    ) -> Binding[Publication] | Binding[Declaration]:
        """Read a committed declaration once; existing bindings remain unchanged."""
        return open_binding(
            self.root, dataset_id, publication_id=publication_id, resources=resources
        )

    def initialize(
        self,
        *,
        resource_ids: Sequence[str],
        store_id: str | None = None,
        managed_resource_id: str | None = None,
        lifecycle: bool = False,
        durable: bool = True,
    ) -> StoreRecord:
        """Explicitly create a v4 store; managed storage requires an explicit resource."""
        if isinstance(resource_ids, str):
            raise InvalidDeclarationError("resource_ids must be a sequence of identifiers")
        if store_id is None:
            try:
                store_id = read_store(self.root).store_id
            except RepositoryNotInitializedError:
                store_id = uuid4().hex
        return initialize_store(
            self.root,
            StoreRecord(store_id, tuple(resource_ids), managed_resource_id, lifecycle),
            durable=durable,
        )

    def register(
        self,
        declaration: Declaration,
        *,
        operation_id: str,
        expected_generation: int,
        durable: bool = True,
    ) -> DeclarationRecord:
        """Persist a registered declaration; never inspect or modify its data files."""
        return register(
            self.root,
            declaration,
            operation_id=operation_id,
            expected_generation=expected_generation,
            durable=durable,
        )

    def describe(self, dataset_id: str, *, publication_id: str | None = None) -> DeclarationRecord:
        """Read committed v4 metadata, including current_only historical declarations."""
        return describe(self.root, dataset_id, publication_id=publication_id)

    def resume_registration(self, operation_id: str, *, durable: bool = True) -> DeclarationRecord:
        return resume_registration(self.root, operation_id, durable=durable)

    def registration_status(self, operation_id: str) -> RegistrationStatus:
        return registration_status(self.root, operation_id)

    def prepare_managed(
        self,
        dataset_id: str,
        *,
        publication_id: str,
        operation_id: str,
        expected_generation: int,
        history: HistoryAccess = HistoryAccess.CURRENT_ONLY,
        durable: bool = True,
    ) -> ManagedCandidate:
        """Create a v4 writer; operation identity and generation are fixed before staging."""
        store = read_store(self.root)
        request = ManagedRequest(
            store.store_id, operation_id, dataset_id, publication_id, expected_generation, history
        )
        return ManagedCandidate(self.root, operation_id, request=request, durable=durable)

    def resume_managed(self, operation_id: str, *, durable: bool = True) -> ManagedCandidate:
        """Reopen sealed v4 work; never rebase or reproduce unsealed data implicitly."""
        return ManagedCandidate(self.root, operation_id, durable=durable)

    def managed_status(self, operation_id: str) -> ManagedCandidateStatus:
        return managed_status(self.root, operation_id)

    def prepare(
        self,
        dataset: Dataset,
        *,
        publication_id: str | None = None,
        expected_generation: int | None = None,
        durable: bool = True,
    ) -> Candidate:
        """Create a context-managed candidate; no publication is committed implicitly."""
        return Candidate(
            self.root,
            dataset=dataset,
            publication_id=publication_id,
            expected_generation=expected_generation,
            durable=durable,
        )

    def resume(self, candidate_id: str, *, durable: bool = True) -> Candidate:
        """Reopen a sealed candidate under coordination, preserving its base generation."""
        return Candidate(self.root, candidate_id=candidate_id, durable=durable)

    def candidate_status(self, candidate_id: str) -> CandidateStatus:
        """Explicit status query; acquiring coordination can create lock files."""
        return candidate_status(self.root, candidate_id)

    @property
    def retention(self) -> Retention:
        """Explicit retention and preview operations; service creation performs no I/O."""
        return Retention(self.root)

    def abandon(self, candidate_id: str) -> CandidateStatus:
        """Permanently abandon an uncommitted candidate, without deleting files."""
        return abandon(self.root, candidate_id)

    @property
    def inspection(self) -> Inspection:
        """Explicit diagnostics; constructing the service performs no I/O."""
        return Inspection(self.root)

    @property
    def governance(self) -> Governance:
        """Explicit v4 lifecycle service; does not create protection until requested."""
        return Governance(self.root)
