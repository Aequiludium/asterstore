"""Reference records and explicit collection scope; no filesystem access."""

from dataclasses import dataclass
from typing import Literal

from asterstore.errors import InvalidDeclarationError

from ._models import Dataset, Reference
from ._records import validate_generation
from ._versions import CURRENT_FORMAT_VERSION, validate_governance_version
from .identity import validate_record_identifiers


@dataclass(frozen=True, slots=True)
class ReferenceRecord:
    reference: Reference
    revision: int
    state: Literal["active", "released"]
    selection: Literal["current", "publication"]
    format_version: int = CURRENT_FORMAT_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.reference, Reference):
            raise InvalidDeclarationError("expected a Reference")
        validate_governance_version(self.format_version)
        validate_record_identifiers(
            self.format_version,
            name=self.reference.name,
            dataset_id=self.reference.dataset_id,
            publication_id=self.reference.publication_id,
        )
        validate_generation(self.revision, minimum=1)
        if self.state not in ("active", "released"):
            raise InvalidDeclarationError("invalid reference state")
        if (self.revision % 2 == 1) != (self.state == "active"):
            raise InvalidDeclarationError("active revisions are odd; released revisions are even")
        if self.selection not in ("current", "publication"):
            raise InvalidDeclarationError("invalid reference selection")


@dataclass(frozen=True, slots=True)
class CollectionPolicy:
    datasets: tuple[str, ...]
    keep_last: int = 1

    def __post_init__(self) -> None:
        if isinstance(self.datasets, str):
            raise InvalidDeclarationError("datasets must be an explicit sequence of names")
        names = tuple(self.datasets)
        if not names:
            raise InvalidDeclarationError("collection requires at least one dataset")
        for name in names:
            Dataset(name)
        if len(set(names)) != len(names):
            raise InvalidDeclarationError("duplicate dataset in collection scope")
        validate_generation(self.keep_last, minimum=1)
        object.__setattr__(self, "datasets", names)


@dataclass(frozen=True, slots=True)
class PreviewIssue:
    path: str
    message: str
