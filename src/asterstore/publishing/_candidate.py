"""Explicit staging sessions, held under root and candidate coordination."""

import shutil
import stat
from collections.abc import Sequence
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from types import TracebackType
from uuid import uuid4

from asterstore.errors import CandidateStateError, InvalidDeclarationError
from asterstore.metadata import (
    CandidateManifest,
    Dataset,
    ObjectRef,
    Publication,
    ReusedObject,
    encode_candidate,
    validate_candidate_id,
    validate_generation,
)
from asterstore.storage import (
    absolute_root,
    atomic_write,
    candidate_directory,
    candidate_lock_path,
    check_repository,
    control_directory,
    ensure_directory,
    file_lock,
    initialize_repository,
    read_candidate,
    read_current,
    sync_directory,
    sync_file,
)

from ._commit import commit_candidate
from ._reuse import select_reference_reuse, select_reuse


class Candidate:
    """A single-use staging context. Exiting never implicitly commits or deletes.

    Obtain sessions through Repository.prepare() or Repository.resume(). Writers
    must close their handles and stop modifying files before seal()/commit().
    """

    def __init__(
        self,
        root: Path,
        *,
        dataset: Dataset | None = None,
        publication_id: str | None = None,
        expected_generation: int | None = None,
        candidate_id: str | None = None,
        durable: bool = True,
    ) -> None:
        if type(durable) is not bool:
            raise TypeError("durable must be a boolean")
        if expected_generation is not None:
            validate_generation(expected_generation)
        self._root = absolute_root(root)
        self._dataset = dataset
        self._publication_id = uuid4().hex if publication_id is None else publication_id
        self._requested_generation = expected_generation
        self._resume = candidate_id is not None
        self._candidate_id = candidate_id if candidate_id is not None else uuid4().hex
        validate_candidate_id(self._candidate_id)
        if not self._resume:
            if dataset is None:
                raise InvalidDeclarationError("a new candidate requires a dataset")
            CandidateManifest(self._candidate_id, dataset, self._publication_id, 0)
        self._durable = durable
        self._stack: ExitStack | None = None
        self._manifest: CandidateManifest | None = None
        self._pending_seal: CandidateManifest | None = None
        self._keys: dict[str, None] = {}
        self._reused: dict[str, ReusedObject] = {}
        self._used = False

    @property
    def candidate_id(self) -> str:
        """Stable recovery identity, available before entering the context."""
        return self._candidate_id

    @property
    def expected_generation(self) -> int:
        return self._active_manifest().expected_generation

    @property
    def publication_id(self) -> str:
        return self._active_manifest().publication_id

    def __enter__(self) -> "Candidate":
        if self._used:
            raise CandidateStateError("candidate contexts are single-use; create a resume session")
        self._used = True
        stack = ExitStack()
        try:
            if self._resume:
                check_repository(self._root, writable=True)
            else:
                initialize_repository(self._root, durable=self._durable)
            stack.enter_context(
                file_lock(control_directory(self._root) / "gc.lock", exclusive=False)
            )
            check_repository(self._root, writable=True)
            stack.enter_context(
                file_lock(candidate_lock_path(self._root, self.candidate_id), exclusive=True)
            )
            if self._resume:
                manifest = read_candidate(self._root, self.candidate_id)
                if manifest.keys is None:
                    raise CandidateStateError(
                        "unsealed candidate cannot be resumed; reproduce its data"
                    )
                self._keys = dict.fromkeys(manifest.keys)
                self._reused = {item.key: item for item in manifest.reused_objects}
            else:
                assert self._dataset is not None
                current = read_current(self._root, self._dataset.dataset_id)
                generation = current.generation if current is not None else 0
                manifest = CandidateManifest(
                    self.candidate_id,
                    self._dataset,
                    self._publication_id,
                    generation
                    if self._requested_generation is None
                    else self._requested_generation,
                )
                directory = candidate_directory(self._root, self.candidate_id)
                if directory.exists():
                    raise CandidateStateError("candidate directory already exists")
                ensure_directory(directory / "files", durable=self._durable)
                atomic_write(
                    directory / "state.json", encode_candidate(manifest), durable=self._durable
                )
            self._manifest = manifest
            self._stack = stack
            return self
        except BaseException:
            stack.close()
            raise

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._stack is not None:
            stack, self._stack = self._stack, None
            stack.close()

    def _active_manifest(self) -> CandidateManifest:
        if self._stack is None or self._manifest is None:
            raise CandidateStateError("operation requires an active candidate context")
        return self._manifest

    def _writable_manifest(self) -> CandidateManifest:
        manifest = self._active_manifest()
        if manifest.keys is not None or self._pending_seal is not None:
            raise CandidateStateError("sealed candidates cannot accept more writes")
        return manifest

    def _include(self, selected: tuple[ReusedObject, ...]) -> tuple[ObjectRef, ...]:
        manifest = self._writable_manifest()
        combined = tuple(self._reused.values()) + selected
        # Validate the entire addition before mutating in-memory membership.
        replace(manifest, keys=tuple(self._keys), reused_objects=combined)
        self._reused.update((item.key, item) for item in selected)
        return tuple(ObjectRef(item.key) for item in selected)

    def reuse(
        self,
        publication_id: str | None = None,
        *,
        keys: Sequence[str] | None = None,
    ) -> tuple[ObjectRef, ...]:
        """Reuse committed members from this dataset; keys are full managed keys.

        None selects current under coordination. No file copies, data probes or
        content synchronization: shared objects inherit their original durability.
        """
        manifest = self._writable_manifest()
        return self._include(select_reuse(self._root, manifest.dataset, publication_id, keys))

    def reuse_reference(
        self,
        name: str,
        *,
        expected_revision: int,
        keys: Sequence[str] | None = None,
    ) -> tuple[ObjectRef, ...]:
        """Reuse members selected by an active named reference at an exact revision."""
        manifest = self._writable_manifest()
        return self._include(
            select_reference_reuse(
                self._root,
                manifest.dataset,
                name,
                expected_revision=expected_revision,
                keys=keys,
            )
        )

    def path(self, key: str) -> Path:
        """Register an output and return its staging path for a native writer."""
        self._writable_manifest()
        obj = ObjectRef(key)
        path = (candidate_directory(self._root, self.candidate_id) / "files").joinpath(
            *obj.key.split("/")
        )
        ensure_directory(path.parent)
        self._keys[obj.key] = None
        return path

    def write_bytes(self, key: str, data: bytes) -> Path:
        path = self.path(key)
        path.write_bytes(data)
        return path

    def copy_file(self, key: str, source: str | Path) -> Path:
        """Copy external bytes; no hardlink to a subsequently mutable source."""
        path = self.path(key)
        shutil.copyfile(source, path)
        return path

    def seal(self) -> None:
        """Fix membership after producers close files; enable restart recovery."""
        manifest = self._active_manifest()
        if manifest.keys is not None:
            return
        sealed = self._pending_seal or replace(
            manifest, keys=tuple(self._keys), reused_objects=tuple(self._reused.values())
        )
        directory = candidate_directory(self._root, self.candidate_id)
        files = directory / "files"
        parents = {files}
        for key in self._keys:
            path = files.joinpath(*key.split("/"))
            if not stat.S_ISREG(path.lstat().st_mode):
                raise CandidateStateError(f"candidate output is not a regular file: {key}")
            if self._durable:
                sync_file(path)
                parent = path.parent
                while parent != directory:
                    parents.add(parent)
                    parent = parent.parent
        if self._durable:
            for parent in sorted(parents, key=lambda item: len(item.parts), reverse=True):
                sync_directory(parent)
        # Once a seal write is attempted, an ambiguous failure must not permit new members.
        self._pending_seal = sealed
        atomic_write(directory / "state.json", encode_candidate(sealed), durable=self._durable)
        self._manifest = sealed
        self._pending_seal = None

    def commit(self) -> Publication:
        """Commit once; retries of the same sealed candidate are idempotent."""
        self.seal()
        return commit_candidate(
            self._root, self._active_manifest(), durable=self._durable, recovering=self._resume
        )
