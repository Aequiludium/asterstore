"""Closed, immutable membership. No paths are probed or contents hashed."""

from collections.abc import Sequence
from dataclasses import dataclass

from asterstore.errors import InvalidDeclarationError
from asterstore.metadata.identity import (
    MAX_IDENTIFIER_BYTES,
    validate_identifier,
    validate_object_key,
)


def _identity(value: str, name: str) -> None:
    validate_identifier(value, name, max_bytes=MAX_IDENTIFIER_BYTES)


@dataclass(frozen=True, slots=True)
class Locator:
    resource_id: str
    relative_path: str

    def __post_init__(self) -> None:
        _identity(self.resource_id, "resource_id")
        validate_object_key(self.relative_path)


@dataclass(frozen=True, slots=True)
class Object:
    """An explicit object identity and locator; identity is not a content hash."""

    object_id: str
    locator: Locator

    def __post_init__(self) -> None:
        _identity(self.object_id, "object_id")
        if not isinstance(self.locator, Locator):
            raise InvalidDeclarationError("locator must be a Locator")


@dataclass(frozen=True, slots=True)
class Member:
    """One logical key referring to one object within this file set."""

    key: str
    object_id: str

    def __post_init__(self) -> None:
        _identity(self.key, "member key")
        _identity(self.object_id, "object_id")


@dataclass(frozen=True, slots=True)
class FileSet:
    """An explicit object table and ordered member map, copied at construction.

    Multiple members may explicitly alias the same object. An object ID has one
    locator; a locator has one object ID within this declaration. Every object
    must be referenced. Cross-publication ownership is a governance concern.
    """

    objects: Sequence[Object]
    members: Sequence[Member]

    def __post_init__(self) -> None:
        objects, members = tuple(self.objects), tuple(self.members)
        if any(not isinstance(obj, Object) for obj in objects):
            raise InvalidDeclarationError("objects must contain only Object values")
        if any(not isinstance(member, Member) for member in members):
            raise InvalidDeclarationError("members must contain only Member values")
        by_id = {obj.object_id: obj for obj in objects}
        if len(by_id) != len(objects):
            raise InvalidDeclarationError("object IDs must be unique")
        if len({member.key for member in members}) != len(members):
            raise InvalidDeclarationError("member keys must be unique")
        if len({obj.locator for obj in objects}) != len(objects):
            raise InvalidDeclarationError("one locator cannot have multiple object IDs")
        referenced = {member.object_id for member in members}
        if referenced != by_id.keys():
            raise InvalidDeclarationError("members must reference all and only declared objects")
        locators = {obj.locator for obj in objects}
        for locator in locators:
            parts = locator.relative_path.split("/")
            if any(
                Locator(locator.resource_id, "/".join(parts[:index])) in locators
                for index in range(1, len(parts))
            ):
                raise InvalidDeclarationError("a declared file cannot be another file's parent")
        object.__setattr__(self, "objects", objects)
        object.__setattr__(self, "members", members)
