"""Dataset-scoped current/rollback repository. Format 1 is deliberately unsupported."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from . import _storage as fs
from ._models import (
    FORMAT_VERSION,
    Binding,
    CleanResult,
    Head,
    Manifest,
    decode_head,
    decode_manifest,
    dump,
    encode,
    load,
    revision,
    unique_id,
)
from .errors import (
    PublicationConflictError,
    PublicationNotFoundError,
    RepositoryNotInitializedError,
    StoreCorruptionError,
    UnsupportedFormatError,
)

if TYPE_CHECKING:
    from .publishing import Draft


class Repository:
    """Capture a root without creating it. Mutations require explicit initialization."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()

    def _marker(self) -> dict[str, object]:
        try:
            value = load(fs.read(self.root, ".asterstore/format.json"))
        except FileNotFoundError as exc:
            raise RepositoryNotInitializedError(str(self.root)) from exc
        if (
            type(value.get("format_version")) is not int
            or value["format_version"] != FORMAT_VERSION
        ):
            raise UnsupportedFormatError("requires format 2; no automatic migration is performed")
        try:
            if set(value) != {"format_version", "store_id"}:
                raise ValueError("unexpected marker fields")
            unique_id(value["store_id"])
        except (ValueError, TypeError) as exc:
            raise StoreCorruptionError(f"invalid marker: {exc}") from exc
        return value

    def initialize(self, *, durable: bool = True) -> None:
        """Initialize an empty root, or verify an existing format-2 marker."""
        if type(durable) is not bool:
            raise TypeError("durable must be bool")
        self.root.mkdir(parents=True, exist_ok=True)
        marker = fs.checked(self.root, ".asterstore/format.json", missing=True)
        if marker.exists():
            self._marker()
            return
        # Never claim existing data, including an interrupted initialization.
        if any(self.root.iterdir()):
            raise StoreCorruptionError("initialization requires an empty root")
        with fs.lock(self.root, ".asterstore/init.lock"):
            if marker.exists():
                self._marker()
                return
            fs.mkdir(self.root, ".asterstore/datasets")
            fs.write(
                self.root,
                ".asterstore/format.json",
                dump({"format_version": FORMAT_VERSION, "store_id": uuid4().hex}),
                exclusive=True,
                durable=durable,
            )

    def _directory(self, dataset_id: str) -> Path:
        suffix = f".asterstore/datasets/{fs.dataset_key(dataset_id)}"
        return fs.checked(self.root, suffix, missing=True)

    def _head(self, dataset_id: str) -> Head | None:
        directory = self._directory(dataset_id)
        try:
            head = decode_head(fs.read(self.root, f"{directory.relative_to(self.root)}/head.json"))
        except FileNotFoundError:
            return None
        if head.dataset_id != dataset_id:
            raise StoreCorruptionError("head dataset identity differs from its directory")
        return head

    def _manifest(self, dataset_id: str, version_id: str) -> Manifest:
        unique_id(version_id)
        path = self._directory(dataset_id) / "versions" / f"{version_id}.json"
        try:
            manifest = decode_manifest(fs.read(self.root, path.relative_to(self.root).as_posix()))
        except FileNotFoundError as exc:
            raise StoreCorruptionError("head references a missing manifest") from exc
        if (manifest.dataset_id, manifest.version_id) != (dataset_id, version_id):
            raise StoreCorruptionError("manifest identity mismatch")
        return manifest

    def describe(self, dataset_id: str) -> Head:
        self._marker()
        head = self._head(dataset_id)
        if head is None:
            raise PublicationNotFoundError(dataset_id)
        return head

    def open(
        self, dataset_id: str, *, version: Literal["current", "rollback"] = "current"
    ) -> Binding:
        if version not in {"current", "rollback"}:
            raise ValueError("version must be current or rollback")
        head = self.describe(dataset_id)
        selected = head.current if version == "current" else head.rollback
        if selected is None:
            raise PublicationNotFoundError(f"no rollback: {dataset_id}")
        return Binding(
            self.root,
            self._manifest(dataset_id, selected),
            head.revision,
            head.revision,
            self._directory(dataset_id) / "objects",
        )

    def list_datasets(self) -> tuple[str, ...]:
        self._marker()
        base = fs.checked(self.root, ".asterstore/datasets")
        result = []
        for directory in sorted(base.iterdir()):
            fs.checked(self.root, directory.relative_to(self.root).as_posix())
            if not directory.is_dir():
                raise StoreCorruptionError("unexpected file in datasets directory")
            path = directory / "head.json"
            try:
                head = decode_head(fs.read(self.root, path.relative_to(self.root).as_posix()))
            except FileNotFoundError:
                continue
            if fs.dataset_key(head.dataset_id) != directory.name:
                raise StoreCorruptionError("dataset directory identity mismatch")
            result.append(head.dataset_id)
        return tuple(result)

    def prepare(
        self, dataset_id: str, *, expected_revision: int | None = None, durable: bool = True
    ) -> Draft:
        from .publishing import Draft

        return Draft(self, dataset_id, expected_revision=expected_revision, durable=durable)

    def _sync_retained(self, head: Head) -> None:
        directory = self._directory(head.dataset_id)
        paths = {self.root / ".asterstore/format.json"}
        for version_id in (head.current, head.rollback):
            if version_id is None:
                continue
            manifest = self._manifest(head.dataset_id, version_id)
            paths.add(directory / "versions" / f"{version_id}.json")
            paths.update(directory / "objects" / m.path for m in manifest.members)
        fs.sync_paths(self.root, paths)

    def _replace_head(self, head: Head, *, durable: bool) -> None:
        if durable:
            self._sync_retained(head)
        path = self._directory(head.dataset_id) / "head.json"
        fs.write(
            self.root,
            path.relative_to(self.root).as_posix(),
            encode(head),
            exclusive=False,
            durable=durable,
        )

    def rollback(self, dataset_id: str, *, expected_revision: int, durable: bool = True) -> Head:
        revision(expected_revision)
        if type(durable) is not bool:
            raise TypeError("durable must be bool")
        self._marker()
        directory = self._directory(dataset_id)
        if not directory.exists():
            raise PublicationNotFoundError(dataset_id)
        with fs.lock(self.root, (directory / "writer.lock").relative_to(self.root).as_posix()):
            head = self.describe(dataset_id)
            if head.revision != expected_revision:
                raise PublicationConflictError("head changed before rollback")
            if head.rollback is None:
                raise PublicationNotFoundError("no safe rollback version")
            self._manifest(dataset_id, head.rollback)
            result = Head(dataset_id, head.revision + 1, head.rollback)
            self._replace_head(result, durable=durable)
            return result

    def clean(self, dataset_id: str, *, quiescent: bool, dry_run: bool = False) -> CleanResult:
        """Caller must stop new readers and drain old bindings through their last I/O."""
        from .cleanup import clean

        return clean(self, dataset_id, quiescent=quiescent, dry_run=dry_run)
