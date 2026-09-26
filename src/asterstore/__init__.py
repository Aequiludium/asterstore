"""Local data declarations, explicit publication, and lightweight bindings."""

from .errors import (
    AsterStoreError,
    CandidateAbandonedError,
    CandidateStateError,
    HistoryUnavailableError,
    InvalidDeclarationError,
    PublicationConflictError,
    PublicationNotFoundError,
    PublicationRetiredError,
    ReferenceConflictError,
    ReferenceNotFoundError,
    RepositoryNotInitializedError,
    ResourceNotBoundError,
    StoreCorruptionError,
    UnknownMemberError,
    UnsupportedCapabilityError,
    UnsupportedPlatformError,
)
from .governance import (
    CleanupPreview,
    CleanupResult,
    Governance,
    GovernancePreview,
    GovernanceResult,
)
from .metadata.capabilities import (
    ByteStability,
    Capabilities,
    HistoryAccess,
    Management,
    RetentionScope,
)
from .metadata.declarations import Declaration
from .metadata.membership import FileSet, Locator, Member, Object
from .metadata.protocol import DeclarationRecord, FixedRetention, ObjectRecord, StoreRecord
from .publishing import Candidate, CandidateStatus
from .reading import Binding
from .registration import RegistrationStatus
from .repository import Repository

__all__ = [
    "CleanupPreview",
    "CleanupResult",
    "FixedRetention",
    "Governance",
    "GovernancePreview",
    "GovernanceResult",
    "Candidate",
    "CandidateStatus",
    "DeclarationRecord",
    "ObjectRecord",
    "StoreRecord",
    "RegistrationStatus",
    "ResourceNotBoundError",
    "UnknownMemberError",
    "UnsupportedCapabilityError",
    "ByteStability",
    "Capabilities",
    "HistoryAccess",
    "Management",
    "RetentionScope",
    "Declaration",
    "FileSet",
    "Locator",
    "Member",
    "Object",
    "CandidateAbandonedError",
    "PublicationRetiredError",
    "ReferenceConflictError",
    "ReferenceNotFoundError",
    "AsterStoreError",
    "Binding",
    "CandidateStateError",
    "InvalidDeclarationError",
    "HistoryUnavailableError",
    "PublicationConflictError",
    "PublicationNotFoundError",
    "Repository",
    "RepositoryNotInitializedError",
    "StoreCorruptionError",
    "UnsupportedPlatformError",
]
