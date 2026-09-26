"""In-memory declarations, independent of engines and persistence formats."""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from asterstore.errors import InvalidDeclarationError

from .identity import validate_identifier, validate_object_key


class PhysicalHistory(StrEnum):
    """Producer-declared capability, not a retention guarantee."""

    CURRENT_ONLY = "current_only"
    VERSIONED = "versioned"


@dataclass(frozen=True, slots=True)
class Dataset:
    """Dataset identity and its declared physical-history capability."""

    dataset_id: str
    physical_history: PhysicalHistory = PhysicalHistory.CURRENT_ONLY

    def __post_init__(self) -> None:
        validate_identifier(self.dataset_id, "dataset_id")
        if not isinstance(self.physical_history, PhysicalHistory):
            raise InvalidDeclarationError("physical_history must be a PhysicalHistory value")


@dataclass(frozen=True, slots=True)
class ObjectRef:
    """A file key relative to a repository root; no file is inspected."""

    key: str

    def __post_init__(self) -> None:
        validate_object_key(self.key)


@dataclass(frozen=True, slots=True)
class Publication:
    """A declaration with explicit file membership, copied at construction.

    Constructing this value does not commit or verify a publication. Empty file
    sets are allowed. The publication ID is independent of content hashes.
    """

    dataset: Dataset
    publication_id: str
    objects: Sequence[ObjectRef]

    def __post_init__(self) -> None:
        if not isinstance(self.dataset, Dataset):
            raise InvalidDeclarationError("dataset must be a Dataset")
        validate_identifier(self.publication_id, "publication_id")
        objects = tuple(self.objects)
        if any(not isinstance(item, ObjectRef) for item in objects):
            raise InvalidDeclarationError("objects must contain only ObjectRef values")
        if len({item.key for item in objects}) != len(objects):
            raise InvalidDeclarationError("object keys must be unique within a publication")
        object.__setattr__(self, "objects", objects)


@dataclass(frozen=True, slots=True)
class Reference:
    """A named reference declaration; construction does not persist a pin."""

    name: str
    dataset_id: str
    publication_id: str

    def __post_init__(self) -> None:
        validate_identifier(self.name, "reference name")
        validate_identifier(self.dataset_id, "dataset_id")
        validate_identifier(self.publication_id, "publication_id")
