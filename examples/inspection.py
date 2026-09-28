"""Inspect protection provenance and explicitly check a trusted content baseline."""

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory

from asterstore import Repository, RetentionScope


def main() -> None:
    with TemporaryDirectory(prefix="asterstore-inspection-") as temporary:
        repo = Repository(Path(temporary))
        repo.initialize(resource_ids=["owned"], managed_resource_id="owned", lifecycle=True)
        payload = b"example bytes"
        with repo.prepare(
            "example", publication_id="p1", operation_id="write:1", expected_generation=0
        ) as writer:
            writer.write_bytes("part", payload, relative_path="part.bin")
            record = writer.commit()
        repo.governance.retain("reader", "example", "p1", scope=RetentionScope.OBJECTS)
        snapshot = repo.governance.inspect()
        oid = record.declaration.files.objects[0].object_id
        assert {r.kind for r in snapshot.explain_object(oid).reasons} == {"current", "retention"}
        report = repo.governance.check(
            level="checksum",
            checksums={oid: hashlib.sha256(payload).hexdigest()},
        )
        assert report.ok and report.checked_objects == (oid,)
        print("Inspection: current + reader protection; trusted SHA-256 verified explicitly.")


if __name__ == "__main__":
    main()
