"""A single in-process draft, not a persistent transaction journal."""

from __future__ import annotations

import os
from contextlib import ExitStack
from pathlib import Path
from types import TracebackType
from uuid import uuid4

from . import _storage as fs
from ._models import Binding, Head, Manifest, Member, encode, identifier, relative, revision
from .errors import CandidateStateError, PublicationConflictError, UnknownMemberError
from .repository import Repository


class Draft:
    def __init__(
        self,
        repository: Repository,
        dataset_id: str,
        *,
        expected_revision: int | None,
        durable: bool,
    ) -> None:
        identifier(dataset_id)
        if expected_revision is not None:
            revision(expected_revision, zero=True)
        if type(durable) is not bool:
            raise TypeError("durable must be bool")
        self.repository = repository
        self.dataset_id = dataset_id
        self._version_id = uuid4().hex
        self.base_revision = 0
        self._expected = expected_revision
        self._durable = durable
        self._state = "new"
        self._stack = ExitStack()
        self._base: Head | None = None
        self._members: dict[str, str] = {}
        self._reused = False
        self._new: dict[str, Path] = {}
        self._binding: Binding | None = None
        self._result: Head | None = None
        self._directory = repository._directory(dataset_id)

    @property
    def version_id(self) -> str:
        """The library-assigned immutable identity, known before publication."""
        return self._version_id

    def __enter__(self) -> Draft:
        if self._state != "new":
            raise CandidateStateError("drafts are single-use")
        repo = self.repository
        repo._marker()
        try:
            self._stack.enter_context(
                fs.lock(
                    repo.root, (self._directory / "writer.lock").relative_to(repo.root).as_posix()
                )
            )
            repo._marker()
            self._base = repo._head(self.dataset_id)
            self.base_revision = self._base.revision if self._base else 0
            if self._expected is not None and self._expected != self.base_revision:
                raise PublicationConflictError("head differs from expected revision")
            if self._base:
                repo._manifest(self.dataset_id, self._base.current)
                if self._base.rollback:
                    repo._manifest(self.dataset_id, self._base.rollback)
            for name in ("objects", "versions", f"staging/{self.version_id}"):
                fs.mkdir(repo.root, (self._directory / name).relative_to(repo.root).as_posix())
            self._state = "editing"
            return self
        except BaseException:
            self._state = "failed"
            self._stack.close()
            raise

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._state = "closed"
        self._stack.close()

    def _require(self, state: str) -> None:
        if self._state != state:
            raise CandidateStateError(f"expected {state}, got {self._state}")

    def reuse_current(self) -> None:
        self._require("editing")
        if self._reused or self._members or self._new:
            raise CandidateStateError("reuse_current requires an empty draft")
        self._reused = True
        if self._base:
            manifest = self.repository._manifest(self.dataset_id, self._base.current)
            self._members.update((m.key, m.path) for m in manifest.members)

    def allocate(self, key: str, *, relative_path: str = "data.bin") -> Path:
        self._require("editing")
        identifier(key)
        relative(relative_path)
        path = f"{uuid4().hex}/{relative_path}"
        temporary = self._directory / "staging" / self.version_id / path
        fs.mkdir(
            self.repository.root, temporary.parent.relative_to(self.repository.root).as_posix()
        )
        self._members[key] = path
        self._new[path] = temporary
        return temporary

    def remove(self, key: str) -> None:
        self._require("editing")
        try:
            del self._members[key]
        except KeyError as exc:
            raise UnknownMemberError(key) from exc

    def alias(self, new_key: str, existing_key: str) -> None:
        self._require("editing")
        identifier(new_key)
        if new_key in self._members:
            raise ValueError("alias key already exists")
        try:
            self._members[new_key] = self._members[existing_key]
        except KeyError as exc:
            raise UnknownMemberError(existing_key) from exc

    def seal(self) -> Binding:
        if self._state == "sealed":
            assert self._binding is not None
            return self._binding
        self._require("editing")
        repo = self.repository
        try:
            manifest = Manifest(
                self.dataset_id,
                self.version_id,
                tuple(Member(k, v) for k, v in self._members.items()),
            )
            # Preflight all declared data, including reuse, before installing files.
            for path in set(self._members.values()):
                source = self._new.get(path, self._directory / "objects" / path)
                fs.checked(repo.root, source.relative_to(repo.root).as_posix())
                if not source.is_file():
                    raise ValueError(f"declared member is not a file: {source}")
            for path in set(self._members.values()) & self._new.keys():
                source = self._new[path]
                destination = self._directory / "objects" / path
                fs.mkdir(repo.root, destination.parent.relative_to(repo.root).as_posix())
                os.link(source, destination)  # Exclusive installation; never overwrite.
                source.unlink()
            manifest_path = self._directory / "versions" / f"{self.version_id}.json"
            fs.write(
                repo.root,
                manifest_path.relative_to(repo.root).as_posix(),
                encode(manifest),
                exclusive=True,
                durable=False,
            )
            self._binding = Binding(
                repo.root, manifest, None, self.base_revision, self._directory / "objects"
            )
            self._state = "sealed"
            return self._binding
        except BaseException:
            self._state = "failed"
            raise

    def commit(self) -> Head:
        if self._state == "committed":
            assert self._result is not None
            return self._result
        self._require("sealed")
        try:
            if self.repository._head(self.dataset_id) != self._base:
                raise PublicationConflictError("head changed during draft")
            result = Head(
                self.dataset_id,
                self.base_revision + 1,
                self.version_id,
                self._base.current if self._base else None,
            )
            self.repository._replace_head(result, durable=self._durable)
            self._result = result
            self._state = "committed"
            return result
        except BaseException:
            # An error after replace may mean publication succeeded. Never replay it.
            self._state = "failed"
            raise
