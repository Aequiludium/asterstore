"""Persist external simulation declarations and reopen them in a separate process."""

import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from asterstore import Capabilities, Declaration, FileSet, Locator, Member, Object, Repository

READ = """
import sys
from asterstore import Repository
repo = Repository(sys.argv[1])
binding = repo.open('simulation:heat', resources={'simulator': sys.argv[2]})
assert binding.files(keys=['phase:initial'])[0].read_bytes() == b'temperature=20'
assert repo.registration_status('import:17').state == 'committed'
print('Separate process reopened the registered declaration.')
"""


def main() -> None:
    with TemporaryDirectory(prefix="asterstore-registration-") as temporary:
        root = Path(temporary)
        external = root / "external"
        external.mkdir()
        (external / "output.bin").write_bytes(b"temperature=20")
        repo = Repository(root / "control")
        repo.initialize(store_id="store:simulation", resource_ids=["simulator"])
        declaration = Declaration(
            "simulation:heat",
            "run:17",
            FileSet(
                [Object("object:17", Locator("simulator", "output.bin"))],
                [Member("phase:initial", "object:17")],
            ),
            Capabilities.registered(),
        )
        committed = repo.register(declaration, operation_id="import:17", expected_generation=0)
        subprocess.run(
            [sys.executable, "-I", "-c", READ, str(repo.root), str(external)], check=True
        )
        assert repo.resume_registration("import:17") == committed
        assert repo.describe("simulation:heat").generation == 1
        print("Fixed request retry preserved generation; external producer still owns its bytes.")


if __name__ == "__main__":
    main()
