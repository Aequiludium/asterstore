"""Explicit permanent path deletion; ordinary transactions keep their undo semantics."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from . import _storage as fs
from ._models import State, identity, json_copy, relative
from .errors import StoreCorruptionError

if TYPE_CHECKING:
    from .repository import Dataset


def _claim(path: str, claimed: set[str], parents: set[str]) -> None:
    relative(path)
    ancestors = {p.as_posix() for p in Path(path).parents if p != Path(".")}
    if path in claimed or path in parents or claimed & ancestors:
        raise ValueError("duplicate or overlapping deletion paths")
    claimed.add(path)
    parents.update(ancestors)


def _preflight(dataset: Dataset, path: Path, *, business: bool) -> None:
    fs.checked(path, missing=True)
    if not path.exists():
        return
    device = dataset.root.stat().st_dev

    def check(target: Path) -> None:
        if business:
            relative(target.relative_to(dataset.root).as_posix())
        fs.checked(target)
        if target.stat().st_dev != device:
            raise StoreCorruptionError("deletion paths must stay on the dataset filesystem")
        if target.is_file():
            fs.file(target)

    check(path)
    if path.is_dir():
        for parent, dirs, files in os.walk(path, followlinks=False):
            for name in (*dirs, *files):
                check(Path(parent) / name)


def _remove(path: Path) -> None:
    fs.checked(path, missing=True)
    if path.is_dir():
        fs.remove_tree(path)
    elif path.exists():
        fs.unlink(path)
    else:
        # A previous attempt may already have removed several parent directories.
        parent = path.parent
        while not parent.exists():
            parent = parent.parent
        fs.sync_directory(parent)


class PruneBatch:
    """Permanent deletion plan held under Dataset.prune()'s writer lock.

    before and id let the caller build business metadata in the same transaction.
    No staging or backup is created. commit() invalidates the entire undo slot and
    removes all existing work material, including obsolete backups.
    """

    def __init__(self, dataset: Dataset, before: State):
        self.dataset = dataset
        self.before = before
        self.id = uuid4().hex
        self._paths: list[str] = []
        self._claimed: set[str] = set()
        self._parents: set[str] = set()
        self._closed = False

    def _editing(self) -> None:
        if self._closed:
            raise RuntimeError("prune batch is closed")

    def delete(self, path: str) -> None:
        """Select one business file or directory; an absent target is allowed."""
        self._editing()
        self.dataset.path(path)
        _claim(path, self._claimed, self._parents)
        self._paths.append(path)

    def commit(self, *, metadata: dict[str, Any]) -> State:
        """Delete targets and old work, then atomically publish metadata without undo.

        An empty plan with unchanged metadata is a no-op. An empty plan cannot
        change metadata; use a normal write transaction for metadata-only changes.
        Once the active intent is visible, exceptions require Dataset.recover().
        """
        self._editing()
        metadata = json_copy(metadata)
        if not self._paths:
            if metadata != self.before.metadata:
                raise ValueError("metadata changes require deletion targets; use Dataset.write()")
            self._closed = True
            return self.before
        self._closed = True
        for path in self._paths:
            _preflight(self.dataset, self.dataset.path(path), business=True)
        work = fs.checked(self.dataset.control / "work")
        operations = []
        for directory in sorted(work.iterdir()):
            identity(directory.name)
            fs.checked(directory)
            if not directory.is_dir():
                raise StoreCorruptionError("invalid work directory")
            _preflight(self.dataset, directory, business=False)
            operations.append(directory.name)
        active = {
            "kind": "prune",
            "id": self.id,
            "before": self.before.payload(),
            "metadata": metadata,
            "paths": self._paths,
            "work": operations,
        }
        fs.write(self.dataset.control / "active.json", active)
        return recover_prune(self.dataset, active)


def recover_prune(dataset: Dataset, active: dict[str, Any]) -> State:
    """Continue an irreversible operation using only its durable intent."""
    try:
        if set(active) != {"kind", "id", "before", "metadata", "paths", "work"}:
            raise ValueError("invalid prune fields")
        if active["kind"] != "prune":
            raise ValueError("invalid prune kind")
        operation = identity(active["id"])
        before = State.parse(active["before"])
        metadata = json_copy(active["metadata"])
        paths, work = active["paths"], active["work"]
        if not isinstance(paths, list) or not paths or not isinstance(work, list):
            raise ValueError("invalid prune targets")
        claimed: set[str] = set()
        parents: set[str] = set()
        for path in paths:
            _claim(path, claimed, parents)
        if len(set(work)) != len(work):
            raise ValueError("duplicate prune work")
        for source in work:
            identity(source)
    except (KeyError, TypeError, ValueError) as exc:
        raise StoreCorruptionError("invalid permanent deletion intent") from exc
    after = State(before.revision + 1, operation, operation, metadata, None)
    head = dataset._head()
    if head != before and head != after:
        raise StoreCorruptionError("permanent deletion state mismatch")
    # An intent replace may have succeeded before its directory fsync failed.
    fs.sync_directory(dataset.control)
    for path in paths:
        target = dataset.path(path)
        _remove(target)
    for source in work:
        target = dataset.control / "work" / source
        _remove(target)
    if head == before:
        fs.write(dataset.control / "head.json", after.payload())
    dataset._sync_control()
    fs.unlink(dataset.control / "active.json")
    return after
