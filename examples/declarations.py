"""A framework-independent simulation producer with external, mutable files."""

from pathlib import Path
from tempfile import TemporaryDirectory

from asterstore import (
    Capabilities,
    Declaration,
    FileSet,
    Locator,
    Member,
    Object,
    Repository,
    RetentionScope,
    UnsupportedCapabilityError,
)


def main() -> None:
    with TemporaryDirectory(prefix="asterstore-declarations-") as directory:
        root = Path(directory)
        external = root / "external-simulator"
        external.mkdir()
        (external / "output-0001.bin").write_bytes(b"temperature=20")
        declaration = Declaration(
            dataset_id="simulation:temperature",
            publication_id="run:17",
            files=FileSet(
                objects=[Object("object:17", Locator("simulator", "output-0001.bin"))],
                members=[Member("phase:initial", "object:17")],
            ),
            capabilities=Capabilities.registered(),
        )
        repository = Repository(root / "control-not-created")
        resources = {"simulator": external}
        binding = repository.bind(declaration, resources=resources)
        selected = binding.files(keys=["phase:initial"])[0]
        assert selected.read_bytes() == b"temperature=20"
        # The external producer can update bytes behind the same declaration.
        selected.write_bytes(b"temperature=21")
        assert binding.files()[0].read_bytes() == b"temperature=21"
        try:
            declaration.capabilities.require_retention(RetentionScope.OBJECTS)
        except UnsupportedCapabilityError:
            pass
        else:
            raise AssertionError("registered files must not claim object retention")
        resources["simulator"] = root / "new-location"
        rebound = repository.bind(declaration, resources=resources)
        assert rebound.files()[0] != binding.files()[0]
        assert binding.files()[0] == selected
        assert not repository.root.exists()
        print("Logical members bound; external bytes remain mutable; no governance state written.")


if __name__ == "__main__":
    main()
