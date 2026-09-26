"""Explicit private-directory inventory; only inspection and cleanup call this."""

import stat
from dataclasses import dataclass
from pathlib import Path

from asterstore.metadata import PreviewIssue, validate_private_key

from ._layout import candidate_directory, objects_directory


@dataclass(frozen=True, slots=True)
class PrivateFiles:
    keys: tuple[str, ...]
    total_bytes: int
    locations: tuple[str, ...]
    issues: tuple[PreviewIssue, ...]


def scan_private_files(root: Path, candidate_id: str) -> PrivateFiles:
    keys = []
    size = 0
    locations = []
    issues = []
    bases = (
        candidate_directory(root, candidate_id) / "files",
        objects_directory(root, candidate_id),
    )
    for base in bases:
        label = "staged" if base == bases[0] else "installed"
        try:
            # Validate ancestors before traversing, including a possibly symlinked root.
            for path in (root, *reversed(base.relative_to(root).parents[:-1])):
                directory = path if path == root else root / path
                if not stat.S_ISDIR(directory.lstat().st_mode):
                    raise ValueError(f"private-file parent is not a real directory: {directory}")
            try:
                mode = base.lstat().st_mode
            except FileNotFoundError:
                continue
            if not stat.S_ISDIR(mode):
                raise ValueError(f"private location is not a real directory: {base}")
            locations.append(label)
            pending = [base]
            while pending:
                directory = pending.pop()
                for entry in sorted(directory.iterdir()):
                    key = str(entry.relative_to(root))
                    validate_private_key(candidate_id, key)
                    details = entry.lstat()
                    if stat.S_ISDIR(details.st_mode):
                        pending.append(entry)
                    elif stat.S_ISREG(details.st_mode):
                        keys.append(key)
                        size += details.st_size
                    else:
                        issues.append(PreviewIssue(key, "not a regular file or real directory"))
        except FileNotFoundError:
            # Missing ancestors imply no files at this private location.
            continue
        except (OSError, ValueError) as exc:
            issues.append(PreviewIssue(str(base.relative_to(root)), str(exc)))
    if len(locations) == 2:
        issues.append(
            PreviewIssue(
                str(candidate_directory(root, candidate_id).relative_to(root)),
                "both staged and installed locations exist",
            )
        )
    return PrivateFiles(tuple(sorted(keys)), size, tuple(locations), tuple(issues))
