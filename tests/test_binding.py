from pathlib import Path

import pytest

from asterstore import Dataset, ObjectRef, Publication, Repository, UnknownObjectError


def test_binding_does_not_create_or_require_files(tmp_path: Path, publication: Publication) -> None:
    root = tmp_path / "not-created"
    binding = Repository(root).bind(publication)
    assert binding.files() == tuple(root / obj.key for obj in publication.objects)
    assert not root.exists()


def test_selection_is_limited_to_declared_members(tmp_path: Path, publication: Publication) -> None:
    binding = Repository(tmp_path).bind(publication)
    (tmp_path / "unpublished.bin").write_bytes(b"later")
    keys = [obj.key for obj in reversed(publication.objects)]
    assert binding.files(keys=keys) == tuple(tmp_path / key for key in keys)
    assert binding.files(keys=[]) == ()
    with pytest.raises(UnknownObjectError, match="bound publication"):
        binding.files(keys=["unpublished.bin"])
    with pytest.raises(TypeError, match="single string"):
        binding.files(keys=keys[0])


def test_binding_a_new_declaration_preserves_old_membership(tmp_path: Path) -> None:
    repository = Repository(tmp_path)
    dataset = Dataset("prices")
    old = repository.bind(Publication(dataset, "p1", [ObjectRef("a.bin")]))
    new = repository.bind(Publication(dataset, "p2", [ObjectRef("b.bin")]))
    assert old.files() == (tmp_path / "a.bin",)
    assert new.files() == (tmp_path / "b.bin",)


def test_binding_does_not_freeze_or_hide_missing_bytes(tmp_path: Path) -> None:
    path = tmp_path / "mutable.bin"
    path.write_bytes(b"old")
    binding = Repository(tmp_path).bind(
        Publication(Dataset("current"), "p1", [ObjectRef(path.name)])
    )
    path.write_bytes(b"new")
    assert binding.files()[0].read_bytes() == b"new"
    path.unlink()
    with pytest.raises(FileNotFoundError):
        binding.files()[0].read_bytes()


def test_empty_publication_and_relative_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    repository = Repository("data")
    monkeypatch.chdir(tmp_path.parent)
    binding = repository.bind(Publication(Dataset("empty"), "p0", []))
    assert repository.root == tmp_path / "data"
    assert binding.files() == ()


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


def test_new_declaration_cannot_be_smuggled_into_v3_publishing(tmp_path):
    from asterstore import InvalidDeclarationError
    from asterstore.metadata import PublishedRecord

    declaration = explicit_declaration()
    with pytest.raises(InvalidDeclarationError):
        Repository(tmp_path / "not-created").prepare(declaration)
    with pytest.raises(InvalidDeclarationError):
        PublishedRecord(1, "a" * 32, declaration)
    assert not (tmp_path / "not-created").exists()


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
