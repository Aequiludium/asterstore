"""Managed staging: explicit member maps, fixed requests, and private byte ownership."""

import os
import stat
from collections.abc import Sequence
from contextlib import ExitStack
from pathlib import Path
from types import TracebackType
from uuid import uuid4

from asterstore.errors import (
    CandidateStateError,
    HistoryUnavailableError,
    InvalidDeclarationError,
    PublicationConflictError,
    PublicationNotFoundError,
    StoreCorruptionError,
    UnknownMemberError,
    UnsupportedCapabilityError,
)
from asterstore.metadata.capabilities import HistoryAccess, Management
from asterstore.metadata.membership import FileSet, Locator, Member, Object
from asterstore.metadata.protocol import (
    DeclarationRecord,
    ManagedRequest,
    StoreRecord,
    decode_declaration_record,
    encode_declaration_record,
    encode_managed_request,
)
from asterstore.publishing.transactions import commit_record
from asterstore.storage import (
    atomic_write,
    ensure_directory,
    file_lock,
    immutable_write,
    sync_control_files,
    sync_directory,
)
from asterstore.storage.registry import (
    find_record,
    managed_data_root,
    managed_directory,
    read_head,
    read_managed_request,
    read_object_record,
    read_operation,
    read_store,
    require_available,
    require_not_abandoned,
    token,
)

from ._membership import MembershipBuilder


class Candidate:
    """Single-use writer context. All file handles must be closed before sealing.

    Exiting neither commits nor deletes. Recovery accepts sealed candidates only;
    paths returned for staging cease to be writable once seal() starts.
    """

    def __init__(
        self,
        root: Path,
        operation_id: str,
        *,
        request: ManagedRequest | None = None,
        durable: bool = True,
    ) -> None:
        token(operation_id)
        if type(durable) is not bool:
            raise TypeError("durable must be a boolean")
        self._root, self.operation_id = root, operation_id
        self._request, self._durable = request, durable
        self._resume = request is None
        self._used = False
        self._stack: ExitStack | None = None
        self._sealed: DeclarationRecord | None = None
        self._pending: DeclarationRecord | None = None
        self._files = FileSet((), ())
        self._members = MembershipBuilder()
        self._store: StoreRecord | None = None

    @property
    def _directory(self) -> Path:
        return managed_directory(self._root, self.operation_id)

    def __enter__(self) -> "Candidate":
        if self._used:
            raise CandidateStateError("candidate contexts are single-use")
        self._used = True
        store = read_store(self._root)
        if store.managed_resource_id is None:
            raise UnsupportedCapabilityError("initialize with a managed resource before publishing")
        stack = ExitStack()
        try:
            # Candidate first: never hold the root lock while waiting on an active writer.
            stack.enter_context(file_lock(self._directory / "writer.lock", exclusive=True))
            with file_lock(self._root / ".asterstore/gc.lock", exclusive=True):
                store = read_store(self._root)
                require_not_abandoned(self._root, store, self.operation_id)
                previous = read_managed_request(self._root, store, self.operation_id)
                if self._resume:
                    if previous is None:
                        raise PublicationNotFoundError("managed candidate does not exist")
                    self._request = previous
                    try:
                        self._sealed = decode_declaration_record(
                            (self._directory / "sealed.json").read_bytes()
                        )
                    except FileNotFoundError as exc:
                        raise CandidateStateError("unsealed candidate cannot be resumed") from exc
                    if self._sealed != previous.declaration_record(self._sealed.declaration.files):
                        raise StoreCorruptionError("seal differs from candidate request")
                    try:
                        self._sealed.check_store(store)
                    except ValueError as exc:
                        raise StoreCorruptionError(str(exc)) from exc
                    self._files = self._sealed.declaration.files
                    self._members = MembershipBuilder(self._files)
                else:
                    assert self._request is not None
                    self._request.declaration_record(self._files).check_store(store)
                    if previous is not None or read_operation(self._root, store, self.operation_id):
                        raise PublicationConflictError(
                            "operation already reserved; resume sealed work"
                        )
                    if any(p.name != "writer.lock" for p in self._directory.iterdir()):
                        raise StoreCorruptionError("candidate directory has unknown residual files")
                    immutable_write(
                        self._directory / "request.json",
                        encode_managed_request(self._request),
                        durable=self._durable,
                    )
                    if store.lifecycle:
                        atomic_write(
                            self._directory / "protection.json",
                            encode_declaration_record(
                                self._request.declaration_record(self._files)
                            ),
                            durable=True,
                        )
                    ensure_directory(self._directory / "files", durable=self._durable)
            self._store = store
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

    def _active(self, *, writing: bool = False) -> ManagedRequest:
        if self._stack is None or self._request is None:
            raise CandidateStateError("candidate context is not active")
        if writing and (self._sealed is not None or self._pending is not None):
            raise CandidateStateError("sealed candidate cannot accept writes")
        return self._request

    def path(self, key: str, *, relative_path: str) -> Path:
        """Declare one new object and return a private path for a native writer."""
        self._active(writing=True)
        store = self._store
        assert store is not None and store.managed_resource_id is not None
        # Validate the unprefixed path too: it must not escape this candidate's directory.
        Locator(store.managed_resource_id, relative_path)
        obj = Object(
            uuid4().hex,
            Locator(store.managed_resource_id, token(self.operation_id) + "/" + relative_path),
        )
        member = Member(key, obj.object_id)
        # Validate before creating directories; filesystem failure must not reserve a member.
        self._members.check_addition(obj, member)
        path = self._directory / "files" / relative_path
        ensure_directory(path.parent, durable=self._durable)
        self._members.extend((obj,), (member,))
        return path

    def write_bytes(self, key: str, data: bytes, *, relative_path: str) -> Path:
        path = self.path(key, relative_path=relative_path)
        with path.open("xb") as stream:
            stream.write(data)
        return path

    def alias(self, key: str, *, member: str) -> None:
        self._active(writing=True)
        source = self._members.members.get(member)
        if source is None:
            raise UnknownMemberError(member)
        self._members.extend((), (Member(key, source.object_id),))

    def reuse(
        self, publication_id: str | None = None, *, keys: Sequence[str] | None = None
    ) -> tuple[Object, ...]:
        """Reuse committed members from this dataset, without data reads or copies."""
        request = self._active(writing=True)
        if isinstance(keys, str):
            raise InvalidDeclarationError("keys must be a sequence")
        with file_lock(self._root / ".asterstore/gc.lock", exclusive=False):
            store = read_store(self._root)
            source = (
                read_head(self._root, store, request.dataset_id)
                if publication_id is None
                else find_record(self._root, store, request.dataset_id, publication_id)
            )
            if source is None:
                raise PublicationNotFoundError("reuse source does not exist")
            if source.declaration.capabilities.management is not Management.MANAGED:
                raise UnsupportedCapabilityError("external objects cannot be adopted through reuse")
            require_available(self._root, store, source)
            current = read_head(self._root, store, request.dataset_id)
            if (
                source != current
                and source.declaration.capabilities.history is HistoryAccess.CURRENT_ONLY
            ):
                raise HistoryUnavailableError("current_only source is no longer current")
            by_key = {m.key: m for m in source.declaration.files.members}
            selected = tuple(by_key) if keys is None else tuple(keys)
            if any(key not in by_key for key in selected):
                raise UnknownMemberError("reuse member is absent")
            members = tuple(by_key[key] for key in selected)
            ids = {m.object_id for m in members}
            objects = tuple(obj for obj in source.declaration.files.objects if obj.object_id in ids)
            for obj in objects:
                proof = read_object_record(self._root, store, obj.object_id)
                if (
                    proof is None
                    or proof.creator_operation_id is None
                    or proof.locator != obj.locator
                ):
                    raise StoreCorruptionError("reuse source lacks managed object evidence")
            merged = dict(self._members.objects)
            for obj in objects:
                if obj.object_id in merged and merged[obj.object_id] != obj:
                    raise StoreCorruptionError("object identity conflict")
                merged[obj.object_id] = obj
            files = FileSet(tuple(merged.values()), (*self._members.members.values(), *members))
            if store.lifecycle:
                # Persist before returning the selected members. A crash may overprotect,
                # but cannot expose an unprotected reuse window before sealing.
                atomic_write(
                    self._directory / "protection.json",
                    encode_declaration_record(request.declaration_record(files)),
                    durable=True,
                )
            self._members = MembershipBuilder(files)
            return objects

    def _new_paths(self, base: Path) -> tuple[Path, ...]:
        prefix = token(self.operation_id) + "/"
        return tuple(
            base / obj.locator.relative_path.removeprefix(prefix)
            for obj in self._files.objects
            if obj.locator.relative_path.startswith(prefix)
        )

    def _verify_files(self, base: Path) -> None:
        if not base.is_dir() or base.is_symlink():
            raise StoreCorruptionError("managed candidate files are missing or not a directory")
        files = set(self._new_paths(base))
        found = set()
        for path in base.rglob("*"):
            mode = path.lstat().st_mode
            if stat.S_ISREG(mode):
                found.add(path)
            elif not stat.S_ISDIR(mode):
                raise StoreCorruptionError("managed files must be ordinary files and directories")
        if files != found:
            raise StoreCorruptionError("staged files differ from declared membership")
        if self._durable:
            sync_control_files(self._root, tuple(files) + (self._directory / "request.json",))
            sync_directory(base)

    def seal(self) -> DeclarationRecord:
        request = self._active()
        if self._sealed is not None:
            return self._sealed
        if self._pending is None:
            self._files = self._members.snapshot()
            self._pending = request.declaration_record(self._files)
        self._verify_files(self._directory / "files")
        with file_lock(self._root / ".asterstore/gc.lock", exclusive=False):
            immutable_write(
                self._directory / "sealed.json",
                encode_declaration_record(self._pending),
                durable=self._durable,
            )
        self._sealed = self._pending
        return self._sealed

    def _install(self) -> None:
        staged = self._directory / "files"
        destination = managed_data_root(self._root) / token(self.operation_id)
        ensure_directory(destination.parent, durable=self._durable)
        if staged.exists() and destination.exists():
            raise StoreCorruptionError("both staged and installed objects exist")
        self._verify_files(staged if staged.exists() else destination)
        if self._durable:
            sync_control_files(
                self._root, (self._directory / "request.json", self._directory / "sealed.json")
            )
        if staged.exists():
            os.rename(staged, destination)
        if self._durable:
            sync_directory(destination.parent)
            sync_directory(self._directory)

    def commit(self) -> DeclarationRecord:
        self._active()
        record = self.seal()
        return commit_record(self._root, record, durable=self._durable, install=self._install)
