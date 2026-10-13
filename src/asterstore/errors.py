"""Exceptions exposed by the implemented declaration and binding API."""

from pathlib import Path
from typing import Literal


class AsterStoreError(Exception):
    """Base class for asterstore errors."""


class RepositoryBusyError(AsterStoreError):
    """A diagnostic could not obtain a stable observation; retry later."""


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


class DataCheckError(AsterStoreError):
    """An explicit data check failed; commit state is separate from data health."""

    def __init__(
        self,
        *,
        check: str,
        code: str,
        object_id: str,
        path: Path,
        dataset_id: str,
        publication_id: str,
        operation_id: str,
    ) -> None:
        self.check, self.code = check, code
        self.object_id, self.path = object_id, path
        self.dataset_id, self.publication_id = dataset_id, publication_id
        self.operation_id = operation_id
        self.commit_state: Literal["not_attempted", "committed", "unknown", "not_applicable"] = (
            "not_applicable"
        )
        self.durability: Literal[
            "not_requested", "not_completed", "completed", "unknown", "not_applicable"
        ] = "not_applicable"
        self.state_error: str | None = None
        super().__init__(f"{check} check failed ({code}): {object_id} at {path}")


class DatasetDecommissionedError(AsterStoreError):
    """The dataset is permanently offline; historical metadata remains auditable."""

    def __init__(self, dataset_id: str, operation_id: str) -> None:
        self.dataset_id = dataset_id
        self.operation_id = operation_id
        super().__init__(f"dataset is decommissioned: {dataset_id} ({operation_id})")
