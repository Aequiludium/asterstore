"""Nonblocking coordination that never creates a directory or lock file."""

import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from asterstore.errors import StoreCorruptionError, UnsupportedPlatformError


@contextmanager
def inspection_lock(root: Path) -> Iterator[bool]:
    try:
        import fcntl
    except ImportError as exc:
        raise UnsupportedPlatformError("inspection requires POSIX flock") from exc
    try:
        fd = os.open(root / ".asterstore/gc.lock", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        # Initialization does not create gc.lock. Callers must accept only an empty
        # inventory and verify no first writer created the lock during observation.
        yield False
        return
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise StoreCorruptionError("coordination lock is not an ordinary file")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield True
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
