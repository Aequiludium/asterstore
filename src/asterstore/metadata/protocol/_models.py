"""records for the unified declaration model; no filesystem access."""

from dataclasses import dataclass
from hashlib import sha256

from asterstore.errors import InvalidDeclarationError
from asterstore.metadata.capabilities import ByteStability, Capabilities, HistoryAccess, Management
from asterstore.metadata.declarations import Declaration
from asterstore.metadata.identity import MAX_IDENTIFIER_BYTES, validate_identifier
from asterstore.metadata.membership import FileSet, Locator, Object

FORMAT_VERSION = 1
MAX_GENERATION = 2**63 - 1


def identity(value: str, field: str) -> None:
    validate_identifier(value, field, max_bytes=MAX_IDENTIFIER_BYTES)


def generation(value: int, *, minimum: int = 0) -> None:
    if type(value) is not int or not minimum <= value <= MAX_GENERATION:
        raise InvalidDeclarationError(
            f"generation must be an integer in [{minimum}, {MAX_GENERATION}]"
        )


@dataclass(frozen=True, slots=True)
class StoreRecord:
    """Immutable coordination identity and resource namespace, not deployment paths."""

    store_id: str
    resource_ids: tuple[str, ...]
    managed_resource_id: str | None = None
    lifecycle: bool = False

    def __post_init__(self) -> None:
        identity(self.store_id, "store_id")
        if type(self.lifecycle) is not bool:
            raise InvalidDeclarationError("lifecycle must be a boolean")
        if isinstance(self.resource_ids, str):
            raise InvalidDeclarationError("resource_ids must be a sequence")
        resources = tuple(self.resource_ids)
        for resource in resources:
            identity(resource, "resource_id")
        if len(set(resources)) != len(resources):
            raise InvalidDeclarationError("duplicate resource identity")
        object.__setattr__(self, "resource_ids", tuple(sorted(resources)))
        if self.managed_resource_id is not None:
            identity(self.managed_resource_id, "managed_resource_id")
            if self.managed_resource_id not in resources:
                raise InvalidDeclarationError("managed resource must be declared")


@dataclass(frozen=True, slots=True)
class DeclarationRecord:
    """A fixed request/result: current replacement, not record construction, commits it."""

    store_id: str
    operation_id: str
    expected_generation: int
    declaration: Declaration

    def __post_init__(self) -> None:
        identity(self.store_id, "store_id")
        identity(self.operation_id, "operation_id")
        generation(self.expected_generation)
        if self.expected_generation == MAX_GENERATION:
            raise InvalidDeclarationError("generation exhausted")
        if not isinstance(self.declaration, Declaration):
            raise InvalidDeclarationError("expected a Declaration")

    @property
    def generation(self) -> int:
        return self.expected_generation + 1

    def check_store(self, store: StoreRecord) -> None:
        if self.store_id != store.store_id:
            raise InvalidDeclarationError("record belongs to another store")
        if any(
            obj.locator.resource_id not in store.resource_ids
            for obj in self.declaration.files.objects
        ):
            raise InvalidDeclarationError("publication uses an undeclared resource")
        managed = self.declaration.capabilities.management is Management.MANAGED
        if managed and store.managed_resource_id is None:
            raise InvalidDeclarationError("store has no managed publishing feature")
        for obj in self.declaration.files.objects:
            if (obj.locator.resource_id == store.managed_resource_id) != managed:
                raise InvalidDeclarationError("resource management does not match declaration")


@dataclass(frozen=True, slots=True)
class ObjectRecord:
    """Immutable location and creator identity; deletion also needs governance evidence."""

    store_id: str
    object_id: str
    locator: Locator
    byte_stability: ByteStability
    creator_operation_id: str | None = None

    def __post_init__(self) -> None:
        identity(self.store_id, "store_id")
        Object(self.object_id, self.locator)
        if not isinstance(self.byte_stability, ByteStability):
            raise InvalidDeclarationError("expected byte stability")
        managed = self.byte_stability is ByteStability.COORDINATED_IMMUTABLE
        if managed != (self.creator_operation_id is not None):
            raise InvalidDeclarationError("managed object needs creator operation evidence")
        if self.creator_operation_id is not None:
            identity(self.creator_operation_id, "creator_operation_id")
            prefix = sha256(self.creator_operation_id.encode("utf-8")).hexdigest() + "/"
            if not self.locator.relative_path.startswith(prefix):
                raise InvalidDeclarationError("managed locator is outside creator namespace")

    @property
    def management(self) -> Management:
        return (
            Management.MANAGED if self.creator_operation_id is not None else Management.REGISTERED
        )


@dataclass(frozen=True, slots=True)
class ManagedRequest:
    """Immutable request reserved before a writer receives any data paths."""

    store_id: str
    operation_id: str
    dataset_id: str
    publication_id: str
    expected_generation: int
    history: HistoryAccess = HistoryAccess.CURRENT_ONLY

    def __post_init__(self) -> None:
        for name in ("store_id", "operation_id", "dataset_id", "publication_id"):
            identity(getattr(self, name), name)
        generation(self.expected_generation)
        if self.expected_generation == MAX_GENERATION:
            raise InvalidDeclarationError("generation exhausted")
        if not isinstance(self.history, HistoryAccess):
            raise InvalidDeclarationError("expected HistoryAccess")

    def declaration_record(self, files: FileSet) -> DeclarationRecord:
        return DeclarationRecord(
            self.store_id,
            self.operation_id,
            self.expected_generation,
            Declaration(
                self.dataset_id,
                self.publication_id,
                files,
                Capabilities.managed(history=self.history),
            ),
        )
