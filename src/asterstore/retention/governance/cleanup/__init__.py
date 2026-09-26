"""One fixed physical cleanup per abandoned managed candidate."""

from ._service import ManagedCleanupPreview, ManagedCleanupResult, cleanup, preview

__all__ = ["ManagedCleanupPreview", "ManagedCleanupResult", "cleanup", "preview"]
