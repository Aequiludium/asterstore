"""Stable in-memory file membership; binding does not freeze or retain bytes."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Generic, TypeVar

from asterstore.errors import InvalidDeclarationError, ResourceNotBoundError
from asterstore.metadata import Capabilities, Declaration, Publication
from asterstore.storage import absolute_root, object_path

from ._selection import select_files
from .resources import ResourceMap

DeclarationT = TypeVar("DeclarationT", bound=Publication | Declaration, covariant=True)


@dataclass(frozen=True, slots=True)
class Binding(Generic[DeclarationT]):
    """Capture membership and resource roots without inspecting the filesystem.

    New Declarations require explicit resources; v3 Publications use the repository
    root. Repeated selections use cached paths and never acquire retention.
    """

    root: Path
    publication: DeclarationT
    resources: Mapping[str, str | Path] | None = field(default=None, kw_only=True)
    _paths: Mapping[str, Path] = field(init=False, repr=False, compare=False)
    _files: tuple[Path, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        root = absolute_root(self.root)
        if isinstance(self.publication, Declaration):
            if self.resources is None:
                raise ResourceNotBoundError("Declaration binding requires explicit resources")
            resources = ResourceMap(self.resources)
            objects = {
                obj.object_id: resources.locate(obj.locator)
                for obj in self.publication.files.objects
            }
            physical = set(objects.values())
            if len(physical) != len(objects):
                raise InvalidDeclarationError(
                    "resource mapping aliases distinct objects to one path"
                )
            if any(parent in physical for path in physical for parent in path.parents):
                raise InvalidDeclarationError("resource mapping makes a file another file's parent")
            paths = {
                member.key: objects[member.object_id] for member in self.publication.files.members
            }
            # Alias members share one object in the all-files view.
            files = tuple(dict.fromkeys(paths.values()))
            object.__setattr__(self, "resources", resources.roots)
        elif isinstance(self.publication, Publication):
            if self.resources is not None:
                raise InvalidDeclarationError("v3 Publications do not support resource mappings")
            paths = {obj.key: object_path(root, obj) for obj in self.publication.objects}
            files = tuple(paths.values())
        else:
            raise InvalidDeclarationError("expected a Publication or Declaration")
        object.__setattr__(self, "root", root)
        object.__setattr__(self, "_paths", MappingProxyType(paths))
        object.__setattr__(self, "_files", files)

    @property
    def capabilities(self) -> Capabilities | None:
        """Declared policy only; v3 capabilities are not silently reinterpreted."""
        return self.publication.capabilities if isinstance(self.publication, Declaration) else None

    def files(self, *, keys: Sequence[str] | None = None) -> tuple[Path, ...]:
        """All unique files, or selected keys in order (including explicit repeats).

        Declaration keys are logical member keys. v3 Publication keys remain
        physical object keys. Neither identity is inferred from a filename.
        """
        return (
            self._files
            if keys is None
            else select_files(self._paths, keys, logical=isinstance(self.publication, Declaration))
        )
