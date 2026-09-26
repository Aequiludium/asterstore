"""Stable in-memory file membership; binding does not freeze or retain bytes."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from asterstore.errors import InvalidDeclarationError, ResourceNotBoundError
from asterstore.metadata import Capabilities, Declaration
from asterstore.storage import absolute_root

from ._selection import select_files
from .resources import ResourceMap


@dataclass(frozen=True, slots=True)
class Binding:
    """Capture membership and resource roots without inspecting the filesystem.

    Declarations require explicit resource roots. Repeated selections use cached
    paths and never acquire retention.
    """

    root: Path
    publication: Declaration
    resources: Mapping[str, str | Path] | None = field(default=None, kw_only=True)
    _paths: Mapping[str, Path] = field(init=False, repr=False, compare=False)
    _files: tuple[Path, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        root = absolute_root(self.root)
        if not isinstance(self.publication, Declaration):
            raise InvalidDeclarationError("expected a Declaration")
        if self.resources is None:
            raise ResourceNotBoundError("Declaration binding requires explicit resources")
        resources = ResourceMap(self.resources)
        objects = {
            obj.object_id: resources.locate(obj.locator) for obj in self.publication.files.objects
        }
        physical = set(objects.values())
        if len(physical) != len(objects):
            raise InvalidDeclarationError("resource mapping aliases distinct objects to one path")
        if any(parent in physical for path in physical for parent in path.parents):
            raise InvalidDeclarationError("resource mapping makes a file another file's parent")
        paths = {member.key: objects[member.object_id] for member in self.publication.files.members}
        # Alias members share one object in the all-files view.
        files = tuple(dict.fromkeys(paths.values()))
        object.__setattr__(self, "resources", resources.roots)
        object.__setattr__(self, "root", root)
        object.__setattr__(self, "_paths", MappingProxyType(paths))
        object.__setattr__(self, "_files", files)

    @property
    def capabilities(self) -> Capabilities:
        """Declared policy; binding does not acquire the corresponding permissions."""
        return self.publication.capabilities

    def files(self, *, keys: Sequence[str] | None = None) -> tuple[Path, ...]:
        """All unique files, or selected keys in order (including explicit repeats).

        Keys are logical member keys, never inferred from filenames.
        """
        return self._files if keys is None else select_files(self._paths, keys)
