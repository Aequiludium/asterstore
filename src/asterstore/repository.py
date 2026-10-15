"""Directory registration and dataset-local change transactions."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from . import _storage as fs
from ._models import EMPTY, FORMAT, State, identity, relative
from .errors import (
    NoRollbackError,
    PublicationConflictError,
    RecoveryRequiredError,
    StoreCorruptionError,
    UnsupportedFormatError,
)

if TYPE_CHECKING:
    from .publishing import Batch


def _window(quiescent: bool) -> None:
    if quiescent is not True:
        raise ValueError("pause new readers and finish existing reads; pass quiescent=True")


def _format(path: Path, kind: str) -> dict[str, Any]:
    record = fs.read(path)
    if record.get("format") != FORMAT or record.get("kind") != kind:
        raise UnsupportedFormatError(f"expected format {FORMAT} {kind}: {path}")
    return record


class Repository:
    """A registry of non-overlapping dataset directories; no global file inventory."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.control = self.root / ".asterstore"

    def initialize(self) -> None:
        fs.checked(self.root, missing=True)
        fs.mkdir(self.root)
        if any(self.root.iterdir()):
            raise FileExistsError("repository initialization requires an empty directory")
        # Claim initialization exclusively: another initializer may have passed the
        # empty-directory check too. Never overwrite a registry it already created.
        self.control.mkdir()
        fs.sync_directory(self.root)
        fs.write(
            self.control / "registry.json", {"format": FORMAT, "kind": "registry", "datasets": {}}
        )

    def _registry(self) -> dict[str, str]:
        record = _format(self.control / "registry.json", "registry")
        datasets = record.get("datasets")
        if not isinstance(datasets, dict):
            raise StoreCorruptionError("invalid dataset registry")
        result: dict[str, str] = {}
        for name, path in datasets.items():
            if not isinstance(name, str) or not name or not isinstance(path, str):
                raise StoreCorruptionError("invalid directory binding")
            if not Path(path).is_absolute() or os.path.abspath(path) != path:
                raise StoreCorruptionError("noncanonical directory binding")
            result[name] = path
        return result

    def create_dataset(self, name: str, path: str | Path) -> Dataset:
        if not isinstance(name, str) or not name or "\x00" in name:
            raise ValueError("dataset name must be a nonempty string")
        self._registry()
        root = Path(path).resolve()
        fs.checked(root, missing=True)
        with fs.lock(self.control / "registry.lock"):
            registry = self._registry()
            if name in registry:
                raise FileExistsError(name)
            for occupied in [self.control, *(Path(p) for p in registry.values())]:
                if root.is_relative_to(occupied) or occupied.is_relative_to(root):
                    raise ValueError(
                        "dataset directories must not overlap or contain registry control"
                    )
            # Also reject roots nested inside datasets belonging to another registry.
            for parent in root.parents:
                if (parent / ".asterstore" / "dataset.json").exists():
                    raise ValueError("nested dataset directory")
            fs.mkdir(root)
            if any(root.iterdir()):
                raise FileExistsError("dataset initialization requires an empty directory")
            control = root / ".asterstore"
            fs.mkdir(control)
            fs.mkdir(control / "work")
            fs.write(control / "head.json", EMPTY.payload())
            fs.write(control / "dataset.json", {"format": FORMAT, "kind": "dataset", "name": name})
            registry[name] = str(root)
            fs.write(
                self.control / "registry.json",
                {
                    "format": FORMAT,
                    "kind": "registry",
                    "datasets": registry,
                },
            )
        return Dataset(root, name)

    def dataset(self, name: str) -> Dataset:
        # Registered roots are already resolved. Do not follow a later redirect
        # of their physical directory to a different dataset.
        root = fs.checked(Path(self._registry()[name]))
        return Dataset(root, name)


class Dataset:
    """A mutable directory; readers must be excluded during installation/recovery."""

    def __init__(self, root: Path, name: str):
        self.root = Path(root).resolve()
        self.control = self.root / ".asterstore"
        self.name = name
        record = _format(self.control / "dataset.json", "dataset")
        if record.get("name") != name:
            raise StoreCorruptionError("dataset identity mismatch")

    def _head(self) -> State:
        return State.parse(fs.read(self.control / "head.json"))

    def current(self) -> State:
        """Read small current metadata, not a pinned file snapshot."""
        if fs.checked(self.control / "active.json", missing=True).exists():
            raise RecoveryRequiredError("installation or recovery is in progress")
        return self._head()

    def path(self, relative_path: str) -> Path:
        target = fs.checked(self.root / relative(relative_path), missing=True)
        ancestor = target
        while not ancestor.exists():
            ancestor = ancestor.parent
        if ancestor.stat().st_dev != self.root.stat().st_dev:
            raise ValueError("data paths must be on the dataset control filesystem")
        return target

    def _revision(self, expected: int | None) -> State:
        state = self.current()
        if expected is not None and (type(expected) is not int or state.revision != expected):
            raise PublicationConflictError(f"expected {expected}, found {state.revision}")
        return state

    @contextmanager
    def write(self, *, expected_revision: int | None = None) -> Iterator[Batch]:
        """Prepare one batch under the dataset writer lock. Explicit commit is required."""
        from .publishing import Batch

        with fs.lock(self.control / "writer.lock"):
            state = self._revision(expected_revision)
            batch = Batch(self, state)
            try:
                yield batch
            finally:
                batch._closed = True
                # If commit started, only recover() may classify its outcome.
                if not (self.control / "active.json").exists() and self._head().undo != batch.id:
                    if batch.work.exists():
                        fs.remove_tree(batch.work)

    def _record(self, operation: str) -> dict[str, Any]:
        directory = self.control / "work" / identity(operation)
        record = fs.read(directory / "record.json")
        if set(record) != {"id", "before", "entries"} or record["id"] != operation:
            raise StoreCorruptionError("invalid change record")
        State.parse(record["before"])
        entries = record["entries"]
        if not isinstance(entries, list):
            raise StoreCorruptionError("invalid change entries")
        paths = []
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {"path", "existed"}:
                raise StoreCorruptionError("invalid change entry")
            try:
                paths.append(relative(entry["path"]))
            except (ValueError, TypeError) as exc:
                raise StoreCorruptionError("unsafe change path") from exc
            if type(entry["existed"]) is not bool:
                raise StoreCorruptionError("invalid existence flag")
        if len(set(paths)) != len(paths):
            raise StoreCorruptionError("duplicate change paths")
        return record

    def _restore(self, operation: str, record: dict[str, Any]) -> None:
        work = self.control / "work" / operation
        # Validate all backups before touching any formal data.
        for entry in record["entries"]:
            self.path(entry["path"])
            if entry["existed"]:
                fs.file(work / "backup" / entry["path"])
        for entry in record["entries"]:
            target = self.path(entry["path"])
            if entry["existed"]:
                temporary = work / "restore"
                fs.copy(work / "backup" / entry["path"], temporary)
                fs.install(temporary, target)
            else:
                fs.unlink(target)

    def recover(self, *, quiescent: bool) -> State:
        _window(quiescent)
        with fs.lock(self.control / "writer.lock"):
            return self._recover()

    def _sync_control(self) -> None:
        # A prior unlink/replace can be visible but not durable after a failed fsync.
        fs.sync_file(self.control / "head.json")
        fs.sync_directory(self.control)

    def _recover(self) -> State:
        active_path = self.control / "active.json"
        if not fs.checked(active_path, missing=True).exists():
            head = self._head()
            self._sync_control()
            return head
        active = fs.read(active_path)
        if set(active) != {"kind", "id", "source", "before"}:
            raise StoreCorruptionError("invalid active transaction")
        operation, source = identity(active["id"]), identity(active["source"])
        before = State.parse(active["before"])
        record = self._record(source)
        head = self._head()
        kind = active["kind"]
        if kind not in ("publish", "rollback"):
            raise StoreCorruptionError("unknown transaction kind")
        if kind == "publish" and (operation != source or record["before"] != before.payload()):
            raise StoreCorruptionError("publication transaction mismatch")
        if kind == "rollback" and before.undo != source:
            raise StoreCorruptionError("rollback source mismatch")
        if head.operation == operation:
            if head.revision != before.revision + 1:
                raise StoreCorruptionError("committed operation revision mismatch")
            if kind == "publish" and (head.publication != source or head.undo != source):
                raise StoreCorruptionError("committed publication mismatch")
            if kind == "rollback":
                previous = State.parse(record["before"])
                expected = State(
                    before.revision + 1, operation, previous.publication, previous.metadata, None
                )
                if head != expected:
                    raise StoreCorruptionError("committed rollback mismatch")
        if head.operation != operation:
            if head != before:
                raise StoreCorruptionError("transaction base no longer matches current state")
            self._restore(source, record)
            if kind == "rollback":
                previous = State.parse(record["before"])
                head = State(
                    before.revision + 1, operation, previous.publication, previous.metadata, None
                )
                fs.write(self.control / "head.json", head.payload())
        # Settle an earlier head replacement before releasing intent.
        self._sync_control()
        fs.unlink(active_path)
        return head

    def rollback(self, *, quiescent: bool, expected_revision: int | None = None) -> State:
        _window(quiescent)
        with fs.lock(self.control / "writer.lock"):
            before = self._revision(expected_revision)
            if before.undo is None:
                raise NoRollbackError("no publication to undo")
            self._record(before.undo)
            fs.write(
                self.control / "active.json",
                {
                    "kind": "rollback",
                    "id": uuid4().hex,
                    "source": before.undo,
                    "before": before.payload(),
                },
            )
            return self._recover()

    def clean(self, *, dry_run: bool = False) -> tuple[str, ...]:
        """Remove only obsolete control work, never formal dataset files."""
        with fs.lock(self.control / "writer.lock"):
            head = self.current()  # Never delete or guess the outcome of an active transaction.
            if head.undo is not None:
                self._record(head.undo)
            work = fs.checked(self.control / "work")
            obsolete = []
            for directory in sorted(work.iterdir()):
                identity(directory.name)
                fs.checked(directory)
                if not directory.is_dir():
                    raise StoreCorruptionError("invalid work directory")
                if directory.name != head.undo:
                    obsolete.append(directory.name)
            if not dry_run:
                # Persist the absence of active before its recovery materials can disappear.
                self._sync_control()
                for operation in obsolete:
                    fs.remove_tree(work / operation)
            return tuple(obsolete)
