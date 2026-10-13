"""Explicit checks, stable bindings and candidate inventory; no automatic retention."""

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory

from asterstore import Repository
from asterstore.errors import DataCheckError

with TemporaryDirectory() as temporary:
    repo = Repository(Path(temporary))
    repo.initialize(resource_ids=["owned"], managed_resource_id="owned", lifecycle=True)
    payload = b"producer output"
    trusted = hashlib.sha256(payload).hexdigest()  # Known producer bytes, before writing.
    with repo.prepare("data", publication_id="p1", operation_id="op1", expected_generation=0) as w:
        path = w.path("key", relative_path="part.bin")
        path.write_bytes(payload)
        path.with_name("extra.bin").write_bytes(b"untracked output")
        record = w.seal()
        oid = record.declaration.files.objects[0].object_id
        w.commit(check="checksum", checksums={oid: trusted})

    binding = repo.open("data", check="existence")
    assert binding.files()[0].read_bytes() == payload
    view = repo.governance.inspect_candidate("op1")
    assert view.undeclared_entries == ("extra.bin",)
    assert view.status.state == "current"
    try:
        repo.open("data", check="checksum", checksums={oid: "0" * 64})
    except DataCheckError as error:
        assert error.code == "checksum_mismatch"
        assert error.commit_state == "not_applicable"
    else:
        raise AssertionError("explicit check must detect a mismatch")
    print("Optional checks and candidate inventory: PASS")
