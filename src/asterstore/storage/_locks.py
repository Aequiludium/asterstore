"""POSIX cooperative locks. Lock files are persistent and must not be removed."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from asterstore.errors import UnsupportedPlatformError

from ._files import ensure_directory


@contextmanager
def file_lock(path: Path, *, exclusive: bool) -> Iterator[None]:
    try:
        import fcntl
    except ImportError as exc:
        raise UnsupportedPlatformError("persistent publishing requires POSIX flock") from exc
    ensure_directory(path.parent)
    with path.open("a+b") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
