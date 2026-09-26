"""Root mapping without stat, resolve, directory creation or discovery."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from asterstore.errors import InvalidDeclarationError, ResourceNotBoundError
from asterstore.metadata.identity import MAX_IDENTIFIER_BYTES, validate_identifier
from asterstore.metadata.membership import Locator
from asterstore.storage import absolute_root


@dataclass(frozen=True, slots=True)
class ResourceMap:
    roots: Mapping[str, str | Path]

    def __post_init__(self) -> None:
        if not isinstance(self.roots, Mapping):
            raise InvalidDeclarationError("resources must be a mapping of IDs to paths")
        roots: dict[str, str | Path] = {}
        for resource_id, root in self.roots.items():
            validate_identifier(resource_id, "resource_id", max_bytes=MAX_IDENTIFIER_BYTES)
            if not isinstance(root, (str, Path)) or root == "":
                raise InvalidDeclarationError("resource roots must be nonempty strings or Paths")
            roots[resource_id] = absolute_root(root)
        object.__setattr__(self, "roots", MappingProxyType(roots))

    def locate(self, locator: Locator) -> Path:
        if not isinstance(locator, Locator):
            raise InvalidDeclarationError("expected a Locator")
        try:
            root = self.roots[locator.resource_id]
        except KeyError as exc:
            raise ResourceNotBoundError(f"unbound resource: {locator.resource_id}") from exc
        return Path(root).joinpath(*locator.relative_path.split("/"))
