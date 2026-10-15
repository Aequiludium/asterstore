"""Validate emitted format-4 directory transaction records and state codecs."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import jsonschema  # type: ignore[import-untyped]

from asterstore import Repository
from asterstore import _storage as fs
from asterstore._models import State


def main() -> None:
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "src/asterstore/schemas/store.json").read_text()
    )
    jsonschema.Draft202012Validator.check_schema(schema)
    shapes: set[str] = set()
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        repo = Repository(root / "registry")
        repo.initialize()
        ds = repo.create_dataset("data", root / "data")

        def validate() -> None:
            for path in root.rglob("*.json"):
                value = json.loads(path.read_bytes())
                jsonschema.validate(value, schema)
                shapes.add(path.name)
                if path.name == "head.json":
                    assert State.parse(value).payload() == value

        validate()
        for index in range(2):
            with ds.write() as batch:
                batch.stage("value").write_bytes(str(index).encode())
                batch.commit(metadata={"index": index}, quiescent=True)
        with patch.object(fs, "install", side_effect=OSError("installation interrupted")):
            try:
                with ds.write() as batch:
                    batch.stage("value").write_bytes(b"candidate")
                    batch.commit(metadata={}, quiescent=True)
            except OSError:
                pass
        validate()
        ds.recover(quiescent=True)
        with patch.object(ds, "_restore", side_effect=OSError("rollback interrupted")):
            try:
                ds.rollback(quiescent=True)
            except OSError:
                pass
        validate()
        ds.recover(quiescent=True)
        ds.clean()
        validate()
    assert shapes == {"registry.json", "dataset.json", "head.json", "record.json", "active.json"}
    print("Current protocol: PASS (format 4, five record shapes)")


if __name__ == "__main__":
    main()
