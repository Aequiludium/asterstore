"""Explicit cleanup of permanently abandoned candidate private files."""

from ._operations import CandidateCleanupResult, cleanup_candidate

__all__ = ["CandidateCleanupResult", "cleanup_candidate"]
