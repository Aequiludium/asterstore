"""Errors for the dataset-scoped two-version protocol."""


class AsterStoreError(Exception):
    """Base error."""


class StoreCorruptionError(AsterStoreError):
    """Invalid persistent state; never guess a replacement head."""


class UnsupportedFormatError(AsterStoreError):
    """This client cannot operate on the store's format."""


class RepositoryNotInitializedError(AsterStoreError, FileNotFoundError):
    """No store marker exists."""


class PublicationNotFoundError(AsterStoreError, FileNotFoundError):
    """No publication exists in the requested slot."""


class PublicationConflictError(AsterStoreError):
    """The expected dataset revision no longer matches."""


class CandidateStateError(AsterStoreError):
    """The draft is closed, failed, or in the wrong phase."""


class UnknownMemberError(AsterStoreError, KeyError):
    """The manifest does not contain the requested logical member."""
