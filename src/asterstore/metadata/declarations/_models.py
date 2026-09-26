"""Input declarations do not acquire management rights or persist a publication."""

from dataclasses import dataclass

from asterstore.errors import InvalidDeclarationError
from asterstore.metadata.capabilities import Capabilities
from asterstore.metadata.identity import MAX_IDENTIFIER_BYTES, validate_identifier
from asterstore.metadata.membership import FileSet


@dataclass(frozen=True, slots=True)
class Declaration:
    """A publication input with explicit membership and declared capabilities.

    Binding it fixes identities, membership and paths, not external file bytes.
    v4 registration persists this input; v3 publication/GC operations do not accept it.
    """

    dataset_id: str
    publication_id: str
    files: FileSet
    capabilities: Capabilities

    def __post_init__(self) -> None:
        for name, value in (
            ("dataset_id", self.dataset_id),
            ("publication_id", self.publication_id),
        ):
            validate_identifier(value, name, max_bytes=MAX_IDENTIFIER_BYTES)
        if not isinstance(self.files, FileSet):
            raise InvalidDeclarationError("files must be a FileSet")
        if not isinstance(self.capabilities, Capabilities):
            raise InvalidDeclarationError("capabilities must be explicit Capabilities")
