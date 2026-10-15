"""Errors for directory transactions."""


class AsterStoreError(Exception):
    """Base storage error."""


class StoreCorruptionError(AsterStoreError):
    """Control state or owned paths are unsafe."""


class UnsupportedFormatError(AsterStoreError):
    """The directory uses another protocol."""


class PublicationConflictError(AsterStoreError):
    """The expected revision is stale."""


class RecoveryRequiredError(AsterStoreError):
    """Finish recovery in a read maintenance window first."""


class NoRollbackError(AsterStoreError):
    """No successful publication can be undone."""
