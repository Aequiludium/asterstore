"""Lexical paths: no resolve(), stat(), listing, or implicit directory creation."""

from hashlib import sha256
from pathlib import Path

from asterstore.metadata import Dataset, ObjectRef, Reference, validate_candidate_id


def absolute_root(root: str | Path) -> Path:
    """Capture the current directory without resolving filesystem symlinks."""
    return Path(root).expanduser().absolute()


def object_path(root: Path, obj: ObjectRef) -> Path:
    """Join an already-validated object key to a root without probing the file."""
    return root.joinpath(*obj.key.split("/"))


def control_directory(root: Path) -> Path:
    return root / ".asterstore"


def dataset_directory(root: Path, dataset_id: str) -> Path:
    Dataset(dataset_id)
    token = sha256(dataset_id.encode("utf-8")).hexdigest()
    return control_directory(root) / "datasets" / token


def history_path(root: Path, dataset_id: str, publication_id: str) -> Path:
    Reference("lookup", dataset_id, publication_id)
    token = sha256(publication_id.encode("utf-8")).hexdigest()
    return dataset_directory(root, dataset_id) / "history" / f"{token}.json"


def candidate_directory(root: Path, candidate_id: str) -> Path:
    validate_candidate_id(candidate_id)
    return control_directory(root) / "candidates" / candidate_id


def candidate_lock_path(root: Path, candidate_id: str) -> Path:
    validate_candidate_id(candidate_id)
    return control_directory(root) / "locks" / "candidates" / f"{candidate_id}.lock"


def objects_directory(root: Path, candidate_id: str) -> Path:
    validate_candidate_id(candidate_id)
    return control_directory(root) / "objects" / candidate_id


def reference_path(root: Path, name: str) -> Path:
    Reference(name, "lookup", "lookup")
    token = sha256(name.encode("utf-8")).hexdigest()
    return control_directory(root) / "references" / f"{token}.json"


def reference_lock_path(root: Path, name: str) -> Path:
    return (
        control_directory(root)
        / "locks"
        / "references"
        / reference_path(root, name).with_suffix(".lock").name
    )
