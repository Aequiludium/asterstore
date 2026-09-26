"""Versioned control records; exported for internal cross-package cooperation."""

from dataclasses import dataclass
from string import hexdigits

from asterstore.errors import InvalidDeclarationError

from ._models import Dataset, ObjectRef, Publication, Reference
from ._versions import CURRENT_FORMAT_VERSION, validate_format_version
from .identity import validate_record_identifiers


def validate_candidate_id(value: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 32
        or any(char not in hexdigits or char.isupper() for char in value)
    ):
        raise InvalidDeclarationError(
            "candidate_id must contain 32 lowercase hexadecimal characters"
        )


def validate_generation(value: int, *, minimum: int = 0) -> None:
    if type(value) is not int or value < minimum:
        raise InvalidDeclarationError(f"generation must be an integer >= {minimum}")


def object_creator(key: str) -> str:
    """Validate a managed object key and return its original creator identity."""
    ObjectRef(key)
    parts = key.split("/")
    if len(parts) < 4 or parts[:2] != [".asterstore", "objects"]:
        raise InvalidDeclarationError("expected a managed object key")
    validate_candidate_id(parts[2])
    return parts[2]


@dataclass(frozen=True, slots=True)
class ReusedObject:
    key: str
    publication_id: str

    def __post_init__(self) -> None:
        object_creator(self.key)
        Reference("source", "source", self.publication_id)


@dataclass(frozen=True, slots=True)
class CandidateManifest:
    candidate_id: str
    dataset: Dataset
    publication_id: str
    expected_generation: int
    keys: tuple[str, ...] | None = None
    format_version: int = CURRENT_FORMAT_VERSION
    reused_objects: tuple[ReusedObject, ...] = ()

    def __post_init__(self) -> None:
        validate_format_version(self.format_version)
        validate_candidate_id(self.candidate_id)
        validate_generation(self.expected_generation)
        Publication(self.dataset, self.publication_id, ())
        validate_record_identifiers(
            self.format_version,
            dataset_id=self.dataset.dataset_id,
            publication_id=self.publication_id,
        )
        reused = tuple(self.reused_objects)
        if any(not isinstance(item, ReusedObject) for item in reused):
            raise InvalidDeclarationError("expected ReusedObject entries")
        for item in reused:
            validate_record_identifiers(self.format_version, publication_id=item.publication_id)
        if self.format_version == 1 and reused:
            raise InvalidDeclarationError("v1 candidates cannot reuse objects")
        if self.keys is None and reused:
            raise InvalidDeclarationError("unsealed records cannot persist reused membership")
        if any(object_creator(item.key) == self.candidate_id for item in reused):
            raise InvalidDeclarationError("reused objects cannot belong to this candidate")
        if any(item.publication_id == self.publication_id for item in reused):
            raise InvalidDeclarationError("a candidate cannot reuse its own publication")
        Publication(self.dataset, self.publication_id, [ObjectRef(item.key) for item in reused])
        object.__setattr__(self, "reused_objects", reused)
        if self.keys is not None:
            keys = tuple(self.keys)
            Publication(self.dataset, self.publication_id, [ObjectRef(key) for key in keys])
            # File keys must not be ancestors of other file keys.
            members = set(keys)
            for key in keys:
                parts = key.split("/")
                if any("/".join(parts[:index]) in members for index in range(1, len(parts))):
                    raise InvalidDeclarationError("a candidate file key is another file's parent")
            object.__setattr__(self, "keys", keys)

    def publication(self) -> Publication:
        if self.keys is None:
            raise InvalidDeclarationError("an unsealed candidate has no final file membership")
        prefix = f".asterstore/objects/{self.candidate_id}/"
        return Publication(
            self.dataset,
            self.publication_id,
            [ObjectRef(prefix + key) for key in self.keys]
            + [ObjectRef(item.key) for item in self.reused_objects],
        )


@dataclass(frozen=True, slots=True)
class PublishedRecord:
    generation: int
    candidate_id: str
    publication: Publication
    format_version: int = CURRENT_FORMAT_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.publication, Publication):
            raise InvalidDeclarationError("v3 publication records require a Publication")
        validate_generation(self.generation, minimum=1)
        validate_format_version(self.format_version)
        validate_candidate_id(self.candidate_id)
        validate_record_identifiers(
            self.format_version,
            dataset_id=self.publication.dataset.dataset_id,
            publication_id=self.publication.publication_id,
        )
        for obj in self.publication.objects:
            creator = object_creator(obj.key)
            if self.format_version == 1 and creator != self.candidate_id:
                raise InvalidDeclarationError(
                    "v1 published objects must belong to the candidate namespace"
                )
