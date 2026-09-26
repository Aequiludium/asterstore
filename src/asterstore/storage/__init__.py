"""Internal cross-package local storage interface; no I/O on import."""

from ._catalog import (
    check_repository,
    find_committed,
    find_publication_record,
    initialize_repository,
    read_candidate,
    read_candidate_state,
    read_current,
)
from ._deletion import delete_candidate_file, delete_object, delete_owned_file
from ._files import (
    atomic_write,
    ensure_directory,
    immutable_write,
    sync_control_files,
    sync_directory,
    sync_file,
)
from ._inventory import ControlInventory, scan_controls
from ._layout import (
    absolute_root,
    candidate_directory,
    candidate_lock_path,
    control_directory,
    dataset_directory,
    history_path,
    object_path,
    objects_directory,
    reference_lock_path,
    reference_path,
)
from ._locks import file_lock
from ._references import read_reference
from ._residuals import PrivateFiles, scan_private_files

__all__ = [
    "delete_owned_file",
    "delete_candidate_file",
    "PrivateFiles",
    "scan_private_files",
    "read_candidate_state",
    "delete_object",
    "ControlInventory",
    "scan_controls",
    "read_reference",
    "reference_path",
    "reference_lock_path",
    "sync_control_files",
    "absolute_root",
    "object_path",
    "candidate_directory",
    "candidate_lock_path",
    "control_directory",
    "dataset_directory",
    "history_path",
    "objects_directory",
    "check_repository",
    "find_committed",
    "find_publication_record",
    "initialize_repository",
    "read_candidate",
    "read_current",
    "atomic_write",
    "ensure_directory",
    "immutable_write",
    "sync_directory",
    "sync_file",
    "file_lock",
]
