"""A single batch of relative file replacements and removals."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from . import _storage as fs
from ._models import State, json_copy, relative
from .repository import Dataset, _window


class Batch:
    def __init__(self, dataset: Dataset, before: State):
        self.dataset = dataset
        self.before = before
        self.id = uuid4().hex
        self.work = dataset.control / "work" / self.id
        self._changes: dict[str, Path | None] = {}
        self._parents: set[str] = set()
        self._closed = False
        fs.mkdir(self.work)

    def _editing(self) -> None:
        if self._closed:
            raise RuntimeError("batch is closed")

    def _claim(self, path: str) -> str:
        self._editing()
        relative(path)
        self.dataset.path(path)
        parents = [p.as_posix() for p in Path(path).parents if p != Path(".")]
        if (
            path in self._changes
            or path in self._parents
            or any(p in self._changes for p in parents)
        ):
            raise ValueError("duplicate or overlapping change paths")
        return path

    def stage(self, path: str) -> Path:
        """Return an empty temporary file for a complete replacement."""
        path = self._claim(path)
        staged = self.work / "staged" / path
        fs.mkdir(staged.parent)
        staged.touch(exist_ok=False)
        self._parents.update(p.as_posix() for p in Path(path).parents if p != Path("."))
        self._changes[path] = staged
        return staged

    def delete(self, path: str) -> None:
        path = self._claim(path)
        fs.file(self.dataset.path(path))
        self._parents.update(p.as_posix() for p in Path(path).parents if p != Path("."))
        self._changes[path] = None

    def commit(self, *, metadata: dict[str, Any], quiescent: bool) -> State:
        self._editing()
        _window(quiescent)
        metadata = json_copy(metadata)
        if not self._changes and metadata == self.before.metadata:
            self._closed = True
            return self.before
        self._closed = True  # Errors require a new batch or explicit recovery, never commit retry.
        entries = []
        for name, source in self._changes.items():
            target = self.dataset.path(name)
            existed = target.exists()
            if existed:
                fs.file(target)
            if source is not None:
                fs.sync_file(source)
            elif not existed:
                raise FileNotFoundError(target)
            entries.append({"path": name, "existed": existed})
        for entry in entries:
            if entry["existed"]:
                name = str(entry["path"])
                fs.copy(self.dataset.path(name), self.work / "backup" / name)
        fs.write(
            self.work / "record.json",
            {
                "id": self.id,
                "before": self.before.payload(),
                "entries": entries,
            },
        )
        fs.write(
            self.dataset.control / "active.json",
            {
                "kind": "publish",
                "id": self.id,
                "source": self.id,
                "before": self.before.payload(),
            },
        )
        for name, source in self._changes.items():
            target = self.dataset.path(name)
            if source is None:
                fs.unlink(target)
            else:
                fs.install(source, target)
        state = State(self.before.revision + 1, self.id, self.id, metadata, self.id)
        fs.write(self.dataset.control / "head.json", state.payload())
        fs.unlink(self.dataset.control / "active.json")
        return state
