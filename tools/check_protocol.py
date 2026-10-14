"""Validate emitted format-2 records against schema and strict codecs."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import jsonschema  # type: ignore[import-untyped]

from asterstore import Repository
from asterstore._models import decode_head, decode_manifest, encode


def main() -> None:
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "src/asterstore/schemas/store.json").read_text()
    )
    jsonschema.Draft202012Validator.check_schema(schema)
    with TemporaryDirectory() as temporary:
        repo = Repository(temporary)
        repo.initialize()
        for _ in range(2):
            with repo.prepare("dataset", durable=False) as draft:
                draft.allocate("member").write_bytes(b"value")
                draft.seal()
                draft.commit()
        for path in Path(temporary).rglob("*.json"):
            data = path.read_bytes()
            value = json.loads(data)
            jsonschema.validate(value, schema)
            if path.name == "head.json":
                assert encode(decode_head(data)) == data
            elif path.name != "format.json":
                assert encode(decode_manifest(data)) == data
    print("Current protocol: PASS (format 2, 3 record shapes)")


if __name__ == "__main__":
    main()
