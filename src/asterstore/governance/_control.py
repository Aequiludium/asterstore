"""Operation-local decoded controls; never cache across calls or mutations."""

import stat
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar, cast

from asterstore.errors import StoreCorruptionError

T = TypeVar("T")


class ControlReader:
    def __init__(self) -> None:
        self.paths: set[Path] = set()
        self._parents: set[Path] = set()
        self._records: dict[tuple[Path, Callable[..., object]], object] = {}
        self._contents: dict[tuple[bytes, Callable[..., object]], object] = {}

    def check_parents(self, control: Path, path: Path) -> None:
        parent = control
        for part in ("", *path.relative_to(control).parts[:-1]):
            parent = parent / part
            if parent not in self._parents:
                if not stat.S_ISDIR(parent.lstat().st_mode):
                    raise StoreCorruptionError(f"control directory is not ordinary: {parent}")
                self._parents.add(parent)

    def read(self, path: Path, decode: Callable[[bytes], T]) -> T:
        key = path, decode
        if key not in self._records:
            if not stat.S_ISREG(path.lstat().st_mode):
                raise StoreCorruptionError(f"control record is not ordinary: {path}")
            data = path.read_bytes()
            content_key = data, decode
            # History, fixed operation and seal frequently contain identical records.
            # Protocol models are immutable, so sharing them within one scan is safe.
            if content_key not in self._contents:
                self._contents[content_key] = decode(data)
            self._records[key] = self._contents[content_key]
            self.paths.add(path)
        return cast(T, self._records[key])
