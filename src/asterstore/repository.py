"""One declaration model, lightweight reads, and explicit lifecycle operations."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from .errors import InvalidDeclarationError, RepositoryNotInitializedError
from .governance import Governance
from .metadata import Declaration, HistoryAccess
from .metadata.protocol import DeclarationRecord, ManagedRequest, StoreRecord
from .publishing import Candidate, CandidateStatus, candidate_status
from .reading import Binding, describe, list_datasets, open_binding
from .registration import RegistrationStatus, register, registration_status, resume_registration
from .storage import absolute_root
from .storage.registry import initialize_store, read_store


@dataclass(frozen=True, slots=True, init=False)
class Repository:
    """A repository root; construction and bind() do not create or inspect files.

    The root is not a security sandbox; callers cooperate on filesystem ownership.
    """

    root: Path

    def __init__(self, root: str | Path) -> None:
        object.__setattr__(self, "root", absolute_root(root))

    def bind(
        self, publication: Declaration, *, resources: Mapping[str, str | Path] | None = None
    ) -> Binding:
        """Capture declared paths without registration, ownership or retention."""
        return Binding(self.root, publication, resources=resources)

    def open(
        self,
        dataset_id: str,
        *,
        publication_id: str | None = None,
        resources: Mapping[str, str | Path] | None = None,
    ) -> Binding:
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
        """Explicitly create a store; managed storage requires an explicit resource."""
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
        """Persist a registered declaration without accessing its external data."""
        return register(
            self.root,
            declaration,
            operation_id=operation_id,
            expected_generation=expected_generation,
            durable=durable,
        )

    def describe(self, dataset_id: str, *, publication_id: str | None = None) -> DeclarationRecord:
        return describe(self.root, dataset_id, publication_id=publication_id)

    def list_datasets(self) -> tuple[str, ...]:
        """Explicitly enumerate committed dataset IDs; never inspect data files."""
        return list_datasets(self.root)

    def resume_registration(self, operation_id: str, *, durable: bool = True) -> DeclarationRecord:
        return resume_registration(self.root, operation_id, durable=durable)

    def registration_status(self, operation_id: str) -> RegistrationStatus:
        return registration_status(self.root, operation_id)

    def prepare(
        self,
        dataset_id: str,
        *,
        publication_id: str,
        operation_id: str,
        expected_generation: int,
        history: HistoryAccess = HistoryAccess.CURRENT_ONLY,
        durable: bool = True,
    ) -> Candidate:
        """Create a managed writer with fixed operation identity and generation."""
        store = read_store(self.root)
        request = ManagedRequest(
            store.store_id, operation_id, dataset_id, publication_id, expected_generation, history
        )
        return Candidate(self.root, operation_id, request=request, durable=durable)

    def resume(self, operation_id: str, *, durable: bool = True) -> Candidate:
        """Reopen sealed work without rebasing or reproducing unsealed data."""
        return Candidate(self.root, operation_id, durable=durable)

    def candidate_status(self, operation_id: str) -> CandidateStatus:
        return candidate_status(self.root, operation_id)

    @property
    def governance(self) -> Governance:
        """Explicit lifecycle service; construction creates no protection and performs no I/O."""
        return Governance(self.root)
