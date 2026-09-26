import pytest

from asterstore import Repository


def explicit_declaration():
    from asterstore import Capabilities, Declaration, FileSet, Locator, Member, Object

    return Declaration(
        "simulation:temperature",
        "run:17",
        FileSet(
            [
                Object("obj:one", Locator("results", "part-001.bin")),
                Object("obj:two", Locator("results", "nested/part-002.bin")),
            ],
            [
                Member("../logical:first", "obj:two"),
                Member("display:alias", "obj:two"),
                Member("logical:second", "obj:one"),
            ],
        ),
        Capabilities.registered(),
    )


def test_explicit_member_selection_and_resource_capture(tmp_path):
    from asterstore import UnknownMemberError

    resources = {"results": tmp_path / "external"}
    root = tmp_path / "control-not-created"
    declaration = explicit_declaration()
    old = Repository(root).bind(declaration, resources=resources)
    resources["results"] = tmp_path / "moved"
    new = Repository(root).bind(declaration, resources=resources)
    assert old.files() == (
        tmp_path / "external/nested/part-002.bin",
        tmp_path / "external/part-001.bin",
    )
    assert old.files(keys=["display:alias", "../logical:first"]) == (old.files()[0],) * 2
    assert old.files(keys=[]) == ()
    assert new.files()[0] == tmp_path / "moved/nested/part-002.bin"
    assert old.capabilities == declaration.capabilities
    with pytest.raises(UnknownMemberError):
        old.files(keys=["nested/part-002.bin"])
    with pytest.raises(TypeError):
        old.resources["results"] = tmp_path / "changed"
    assert not root.exists()


def test_explicit_binding_requires_roots_and_rejects_resolved_aliases(tmp_path):
    from asterstore import (
        Capabilities,
        Declaration,
        FileSet,
        InvalidDeclarationError,
        Locator,
        Member,
        Object,
        ResourceNotBoundError,
    )

    repo = Repository(tmp_path)
    with pytest.raises(ResourceNotBoundError):
        repo.bind(explicit_declaration())
    with pytest.raises(ResourceNotBoundError):
        repo.bind(explicit_declaration(), resources={})
    ambiguous = Declaration(
        "d",
        "p",
        FileSet(
            [Object("a", Locator("left", "data")), Object("b", Locator("right", "data"))],
            [Member("a", "a"), Member("b", "b")],
        ),
        Capabilities.registered(),
    )
    with pytest.raises(InvalidDeclarationError, match="aliases"):
        repo.bind(ambiguous, resources={"left": tmp_path, "right": tmp_path})


def test_registered_binding_exposes_external_mutation_and_native_missing_error(tmp_path):
    declaration = explicit_declaration()
    external = tmp_path / "data"
    external.mkdir()
    file = external / "part-001.bin"
    file.write_bytes(b"first")
    binding = Repository(tmp_path / "control").bind(declaration, resources={"results": external})
    selected = binding.files(keys=["logical:second"])[0]
    file.write_bytes(b"changed outside")
    assert selected.read_bytes() == b"changed outside"
    file.unlink()
    with pytest.raises(FileNotFoundError):
        selected.read_bytes()
    assert not (tmp_path / "control").exists()


def test_resource_capture_is_lexical_and_relative_roots_are_fixed(tmp_path, monkeypatch):
    from asterstore.reading.resources import ResourceMap

    monkeypatch.chdir(tmp_path)
    mapping = {"results": "relative"}
    resources = ResourceMap(mapping)
    monkeypatch.chdir(tmp_path.parent)
    mapping["results"] = "different"
    from asterstore import Locator

    assert resources.locate(Locator("results", "part.bin")) == tmp_path / "relative/part.bin"


def test_resource_mapping_rejects_file_parent_collision_across_resources(tmp_path):
    from asterstore import (
        Capabilities,
        Declaration,
        FileSet,
        InvalidDeclarationError,
        Locator,
        Member,
        Object,
    )

    declaration = Declaration(
        "d",
        "p",
        FileSet(
            [Object("a", Locator("left", "part")), Object("b", Locator("right", "child"))],
            [Member("a", "a"), Member("b", "b")],
        ),
        Capabilities.registered(),
    )
    with pytest.raises(InvalidDeclarationError, match="parent"):
        Repository(tmp_path).bind(
            declaration, resources={"left": tmp_path, "right": tmp_path / "part"}
        )


@pytest.mark.parametrize("resources", [{"results": ""}, {"results": 12}, {"bad\n": "/data"}, []])
def test_resource_mapping_rejects_invalid_roots_and_names(tmp_path, resources):
    from asterstore import InvalidDeclarationError

    with pytest.raises(InvalidDeclarationError):
        Repository(tmp_path).bind(explicit_declaration(), resources=resources)


def test_empty_explicit_declaration_binds_without_resources_on_disk(tmp_path):
    from asterstore import Capabilities, Declaration, FileSet

    binding = Repository(tmp_path / "absent").bind(
        Declaration("empty", "p1", FileSet([], []), Capabilities.registered()),
        resources={},
    )
    assert binding.files() == ()
    assert not (tmp_path / "absent").exists()
