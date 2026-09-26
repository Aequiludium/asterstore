"""One fixed physical cleanup per abandoned managed candidate."""

from ._service import CleanupPreview, CleanupResult, cleanup, preview

__all__ = ["CleanupPreview", "CleanupResult", "cleanup", "preview"]
