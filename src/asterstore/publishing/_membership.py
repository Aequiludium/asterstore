"""Incremental candidate membership; immutable FileSet validation remains at sealing."""

from collections.abc import Iterable

from asterstore.errors import InvalidDeclarationError
from asterstore.metadata.membership import FileSet, Locator, Member, Object


class MembershipBuilder:
    """Index identities and path ancestors, with atomic validation of additions.

    Checking one addition visits its path depth, not every previous member.
    No filesystem or store-marker reads occur here. A batch is accepted only
    after every object/member has passed validation against current and new indexes.
    """

    def __init__(self, files: FileSet | None = None) -> None:
        self.objects: dict[str, Object] = {}
        self.members: dict[str, Member] = {}
        self._locators: dict[tuple[str, str], str] = {}
        self._parents: set[tuple[str, str]] = set()
        if files is not None:
            self.extend(files.objects, files.members)

    @staticmethod
    def _ancestors(locator: Locator) -> tuple[tuple[str, str], ...]:
        parts = locator.relative_path.split("/")
        return tuple((locator.resource_id, "/".join(parts[:i])) for i in range(1, len(parts)))

    def check_addition(self, obj: Object, member: Member) -> None:
        self.extend((obj,), (member,), validate_only=True)

    def extend(
        self, objects: Iterable[Object], members: Iterable[Member], *, validate_only: bool = False
    ) -> None:
        new_objects: dict[str, Object] = {}
        new_members: dict[str, Member] = {}
        locators: dict[tuple[str, str], str] = {}
        parents: set[tuple[str, str]] = set()
        for obj in objects:
            existing = new_objects.get(obj.object_id) or self.objects.get(obj.object_id)
            if existing is not None:
                if existing != obj:
                    raise InvalidDeclarationError("object identity cannot be rebound")
                continue
            location = obj.locator.resource_id, obj.locator.relative_path
            ancestors = self._ancestors(obj.locator)
            if location in self._locators or location in locators:
                raise InvalidDeclarationError("one locator cannot have multiple object IDs")
            if (
                location in self._parents
                or location in parents
                or any(p in self._locators or p in locators for p in ancestors)
            ):
                raise InvalidDeclarationError("a declared file cannot be another file's parent")
            locators[location] = obj.object_id
            parents.update(ancestors)
            new_objects[obj.object_id] = obj
        referenced = set()
        for member in members:
            if member.key in self.members or member.key in new_members:
                raise InvalidDeclarationError("member keys must be unique")
            if member.object_id not in self.objects and member.object_id not in new_objects:
                raise InvalidDeclarationError("member references an undeclared object")
            referenced.add(member.object_id)
            new_members[member.key] = member
        if not new_objects.keys() <= referenced:
            raise InvalidDeclarationError("every new object must be referenced")
        if validate_only:
            return
        self.objects.update(new_objects)
        self.members.update(new_members)
        self._locators.update(locators)
        self._parents.update(parents)

    def snapshot(self) -> FileSet:
        return FileSet(tuple(self.objects.values()), tuple(self.members.values()))
