"""Lexical paths; no probing or implicit directory creation."""

from pathlib import Path


def absolute_root(root: str | Path) -> Path:
    """Capture the current directory without resolving filesystem symlinks."""
    return Path(root).expanduser().absolute()
