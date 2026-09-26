"""Reference file access. Coordination is owned by the calling service."""

from pathlib import Path

from asterstore.errors import StoreCorruptionError
from asterstore.metadata import CURRENT_FORMAT_VERSION, ReferenceRecord, decode_reference

from ._layout import reference_path


def read_reference(
    root: Path, name: str, *, format_version: int = CURRENT_FORMAT_VERSION
) -> ReferenceRecord | None:
    path = reference_path(root, name)
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    record = decode_reference(data, expected_version=format_version)
    if record.reference.name != name:
        raise StoreCorruptionError(f"reference identity differs from its path: {path}")
    return record
