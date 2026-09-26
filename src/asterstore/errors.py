"""Exceptions exposed by the implemented declaration and binding API."""


class AsterStoreError(Exception):
    """Base class for asterstore errors."""


class InvalidDeclarationError(AsterStoreError, ValueError):
    """A supplied declaration is structurally invalid."""


class StoreCorruptionError(AsterStoreError):
    """Persistent metadata is invalid or inconsistent."""


class RepositoryNotInitializedError(AsterStoreError, FileNotFoundError):
    """The root does not contain an initialized asterstore repository."""


class PublicationNotFoundError(AsterStoreError, FileNotFoundError):
    """The requested committed publication does not exist."""


class PublicationConflictError(AsterStoreError):
    """The publication's base generation or identity conflicts with stored state."""


class CandidateStateError(AsterStoreError):
    """An operation is not valid for the candidate's current state."""


class HistoryUnavailableError(AsterStoreError):
    """A dataset does not promise historical content access."""


class UnsupportedPlatformError(AsterStoreError):
    """The required local filesystem coordination is unavailable."""


class ReferenceConflictError(AsterStoreError):
    """The named reference revision or create request does not match."""


class ReferenceNotFoundError(AsterStoreError, KeyError):
    """The reference name has never been recorded."""


class PublicationRetiredError(AsterStoreError):
    """An identity was committed and irreversibly retired; it cannot be reopened."""

    def __init__(self, dataset_id: str, publication_id: str, operation_id: str) -> None:
        self.dataset_id = dataset_id
        self.publication_id = publication_id
        self.operation_id = operation_id
        super().__init__(f"publication is retired: {dataset_id}/{publication_id} ({operation_id})")


class CandidateAbandonedError(CandidateStateError):
    """The candidate was permanently abandoned and cannot resume or commit."""

    def __init__(self, operation_id: str) -> None:
        self.operation_id = operation_id
        super().__init__(f"candidate is abandoned: {operation_id}")


class UnsupportedCapabilityError(AsterStoreError, ValueError):
    """The declared profile cannot support the requested governance guarantee."""


class ResourceNotBoundError(AsterStoreError, KeyError):
    """A declaration refers to a resource without an explicit root binding."""


class UnknownMemberError(AsterStoreError, KeyError):
    """A requested logical member is absent from the bound declaration."""
