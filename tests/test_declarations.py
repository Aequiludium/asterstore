"""The new storage model is independent of application formats."""

from dataclasses import FrozenInstanceError
from itertools import product

import pytest

from asterstore import (
    ByteStability,
    Capabilities,
    Declaration,
    FileSet,
    HistoryAccess,
    InvalidDeclarationError,
    Locator,
    Management,
    Member,
    Object,
    RetentionScope,
    UnsupportedCapabilityError,
)


@pytest.mark.parametrize(
    "management,stability,history", list(product(Management, ByteStability, HistoryAccess))
)
def test_only_defined_capability_combinations_are_accepted(management, stability, history):
    valid = (management is Management.MANAGED) == (stability is ByteStability.COORDINATED_IMMUTABLE)
    if not valid:
        with pytest.raises(InvalidDeclarationError):
            Capabilities(management, stability, history)
        return
    policy = Capabilities(management, stability, history)
    policy.require_retention(RetentionScope.METADATA)
    assert policy.history is history
    if management is Management.REGISTERED:
        assert policy.retention_scopes == {RetentionScope.METADATA}
        with pytest.raises(UnsupportedCapabilityError):
            policy.require_retention(RetentionScope.OBJECTS)
        with pytest.raises(UnsupportedCapabilityError):
            policy.require_managed()
    else:
        policy.require_managed()
        policy.require_retention(RetentionScope.OBJECTS)


@pytest.mark.parametrize(
    "fields",
    [
        ("managed", ByteStability.COORDINATED_IMMUTABLE, HistoryAccess.CURRENT_ONLY),
        (Management.MANAGED, "coordinated_immutable", HistoryAccess.CURRENT_ONLY),
        (Management.MANAGED, ByteStability.COORDINATED_IMMUTABLE, True),
    ],
)
def test_capabilities_require_typed_values(fields):
    with pytest.raises(InvalidDeclarationError):
        Capabilities(*fields)


def test_current_only_and_versioned_are_independent_of_object_retention():
    for history in HistoryAccess:
        Capabilities.managed(history=history).require_retention(RetentionScope.OBJECTS)
        registered = Capabilities.registered(
            history=history, byte_stability=ByteStability.PRODUCER_IMMUTABLE
        )
        with pytest.raises(UnsupportedCapabilityError):
            registered.require_retention(RetentionScope.OBJECTS)
    with pytest.raises(InvalidDeclarationError):
        Capabilities.managed().require_retention("objects")


def test_members_can_explicitly_alias_one_object_and_inputs_are_copied():
    objects = [Object("obj:1", Locator("camera:front", "0001.bin"))]
    members = [Member("../original", "obj:1"), Member("display:thumbnail", "obj:1")]
    files = FileSet(objects, members)
    objects.clear()
    members.clear()
    assert len(files.objects) == 1 and len(files.members) == 2
    with pytest.raises(FrozenInstanceError):
        files.objects = ()


@pytest.mark.parametrize(
    "objects,members",
    [
        ([Object("x", Locator("r", "a")), Object("x", Locator("r", "b"))], [Member("m", "x")]),
        ([Object("x", Locator("r", "a"))], [Member("m", "x"), Member("m", "x")]),
        ([Object("x", Locator("r", "a"))], [Member("m", "missing")]),
        ([Object("x", Locator("r", "a"))], []),
        ([], [Member("m", "missing")]),
        (
            [Object("x", Locator("r", "a")), Object("y", Locator("r", "a"))],
            [Member("a", "x"), Member("b", "y")],
        ),
        (
            [Object("x", Locator("r", "a")), Object("y", Locator("r", "a/b"))],
            [Member("a", "x"), Member("b", "y")],
        ),
        (["not an object"], []),
        ([], ["not a member"]),
    ],
)
def test_membership_rejects_ambiguity_unclosed_sets_and_path_conflicts(objects, members):
    with pytest.raises(InvalidDeclarationError):
        FileSet(objects, members)


@pytest.mark.parametrize(
    "path", ["", "/absolute", "../outside", "a/../b", "a//b", "a/./b", "a\\b", "C:/a", "a\n"]
)
def test_locator_keeps_physical_path_constraints(path):
    with pytest.raises(InvalidDeclarationError):
        Locator("resource:../logical", path)


def test_objects_in_different_resources_are_not_conflated():
    files = FileSet(
        [Object("a", Locator("left", "part.bin")), Object("b", Locator("right", "part.bin"))],
        [Member("left:first", "a"), Member("right:first", "b")],
    )
    assert len(files.objects) == 2


def test_declaration_requires_explicit_profile_and_preserves_opaque_identity():
    declaration = Declaration(
        "sim:temperature", "run:17", FileSet([], []), Capabilities.registered()
    )
    assert declaration.capabilities.byte_stability is ByteStability.MUTABLE_OR_UNKNOWN
    with pytest.raises(InvalidDeclarationError):
        Declaration("sim", "run", FileSet([], []), None)
    with pytest.raises(InvalidDeclarationError):
        Declaration("sim", "run", [], Capabilities.registered())


@pytest.mark.parametrize(
    "constructor",
    [
        lambda value: Locator(value, "part.bin"),
        lambda value: Object(value, Locator("source", "part.bin")),
        lambda value: Member(value, "object"),
        lambda value: Member("member", value),
        lambda value: Declaration(value, "run", FileSet([], []), Capabilities.registered()),
    ],
)
def test_new_identity_domains_enforce_utf8_bytes_and_control_rules(constructor):
    assert constructor("界" * 1365 + "a")
    for value in ("", "\ud800", "x\x00", "界" * 1366):
        with pytest.raises(InvalidDeclarationError):
            constructor(value)
