"""Opt-in checks with explicit coverage; never infer deletion authority or repair."""

import hashlib
import os
import re
import stat
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

from asterstore.errors import AsterStoreError
from asterstore.metadata import protocol as codec
from asterstore.reading.resources import ResourceMap
from asterstore.storage.registry import managed_data_root

from .._control import ControlReader
from ._locking import inspection_lock
from ._models import CheckIssue, CheckLevel, CheckReport
from ._service import read_inventory


def decoder(parts: tuple[str, ...]) -> Callable[[bytes], object] | None:
    if parts == ("format.json",):
        return codec.decode_store
    if len(parts) == 2 and parts[-1].endswith(".json"):
        return {
            "registrations": codec.decode_declaration_record,
            "object-records": codec.decode_object_record,
            "fixed-retentions": codec.decode_retention,
        }.get(parts[0])
    if parts[0] == "datasets" and (
        len(parts) == 3
        and parts[-1] == "current.json"
        or len(parts) == 4
        and parts[2] == "history"
        and parts[-1].endswith(".json")
    ):
        return codec.decode_declaration_record
    if len(parts) == 3:
        if parts[0] == "retired" and parts[-1].endswith(".json"):
            return codec.decode_retired
        if parts[0] == "collections":
            return {
                "plan.json": codec.decode_governance_plan,
                "progress.json": codec.decode_governance_progress,
            }.get(parts[-1])
        if parts[0] == "managed-candidates":
            return {
                "request.json": codec.decode_managed_request,
                "sealed.json": codec.decode_declaration_record,
                "protection.json": codec.decode_declaration_record,
                "abandoned.json": codec.decode_managed_abandonment,
                "cleanup-plan.json": codec.decode_managed_cleanup_plan,
                "cleanup-progress.json": codec.decode_managed_cleanup_progress,
            }.get(parts[-1])
    return None


def record_issues(root: Path, reader: ControlReader) -> list[CheckIssue]:
    """Collect independent malformed control records before checking their relationships."""
    issues: list[CheckIssue] = []
    control = root / ".asterstore"

    def failed(exc: OSError) -> None:
        issues.append(
            CheckIssue("control_io", str(exc), Path(exc.filename) if exc.filename else None)
        )

    for directory, dirs, files in os.walk(control, followlinks=False, onerror=failed):
        parent = Path(directory)
        relative = parent.relative_to(control).parts
        for name in sorted(dirs.copy()):
            path = parent / name
            if (not relative and name == "managed-data") or (
                len(relative) == 2 and relative[0] == "managed-candidates" and name == "files"
            ):
                dirs.remove(name)
            elif path.is_symlink():
                issues.append(CheckIssue("control_type", "symlink control directory", path))
                dirs.remove(name)
            elif len(relative) >= 3:
                issues.append(
                    CheckIssue("control_layout", "unexpected nested control directory", path)
                )
                dirs.remove(name)
        dirs.sort()
        for name in sorted(files):
            path = parent / name
            if name.endswith(".lock") or re.fullmatch(r"\.[^.]+\.json\.[a-z0-9_]+", name):
                continue
            decode = decoder((*relative, name))
            if decode is None:
                issues.append(CheckIssue("control_layout", "unknown control record", path))
                continue
            try:
                if not stat.S_ISREG(path.lstat().st_mode):
                    issues.append(
                        CheckIssue("control_type", "control record is not ordinary", path)
                    )
                    continue
                reader.read(path, decode)
            except (AsterStoreError, OSError) as exc:
                issues.append(CheckIssue("control_record", str(exc), path))
    return issues


def regular_file(path: Path, managed_root: Path | None) -> None:
    if managed_root is not None:
        relative = path.relative_to(managed_root)
        parent = managed_root
        for part in ("", *relative.parts[:-1]):
            parent = parent / part
            if not stat.S_ISDIR(parent.lstat().st_mode):
                raise OSError(f"managed parent is not an ordinary directory: {parent}")
    if not stat.S_ISREG(path.lstat().st_mode):
        raise OSError(f"object is not an ordinary file: {path}")


def checksum(path: Path) -> str:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise OSError("object is not an ordinary file")
        digest = hashlib.sha256()
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
        return digest.hexdigest()


def check(
    root: Path,
    *,
    level: CheckLevel = "metadata",
    resources: Mapping[str, str | Path] | None = None,
    validator: Callable[[Path], None] | None = None,
    checksums: Mapping[str, str] | None = None,
) -> CheckReport:
    if level not in ("metadata", "existence", "format", "checksum"):
        raise ValueError("unknown check level")
    if (level == "format") != (validator is not None):
        raise ValueError("format checking requires a validator; other levels do not use one")
    if (level == "checksum") != (checksums is not None):
        raise ValueError("checksum checking requires trusted SHA-256 baselines")
    baselines = {} if checksums is None else dict(checksums)
    if any(
        not isinstance(v, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", v)
        for v in baselines.values()
    ):
        raise ValueError("checksums must be 64-digit SHA-256 hex strings")
    resources_map = ResourceMap({} if resources is None else resources)
    started = datetime.now(UTC)
    issues: list[CheckIssue] = []
    targets: tuple[str, ...] = ()
    checked: list[str] = []
    coordinated = metadata_complete = False
    try:
        with inspection_lock(root) as coordinated:
            reader = ControlReader()
            issues.extend(record_issues(root, reader))
            if not issues:
                view = read_inventory(root, coordinated, reader=reader)
                metadata_complete = True
                if level != "metadata":
                    # Check all non-retired committed publications, deduplicating shared
                    # objects. Private candidates and intentionally retired data are out of scope.
                    targets = tuple(
                        sorted(
                            {
                                obj.object_id
                                for identity, p in view.publications.items()
                                if identity not in view.retired
                                for obj in p.declaration.files.objects
                            }
                        )
                    )
                    owned_root = managed_data_root(root)
                    for oid in targets:
                        obj = view.objects[oid]
                        path: Path | None = None
                        try:
                            owned = obj.creator_operation_id is not None
                            path = (
                                owned_root / obj.locator.relative_path
                                if owned
                                else resources_map.locate(obj.locator)
                            )
                            regular_file(path, owned_root if owned else None)
                            if level == "format":
                                assert validator is not None
                                try:
                                    validator(path)
                                except Exception as exc:
                                    issues.append(CheckIssue("format_error", str(exc), path, oid))
                                    continue
                            elif level == "checksum":
                                expected = baselines.get(oid)
                                if expected is None:
                                    issues.append(
                                        CheckIssue(
                                            "checksum_unavailable",
                                            "no trusted baseline for object",
                                            path,
                                            oid,
                                        )
                                    )
                                    continue
                                if checksum(path) != expected.lower():
                                    issues.append(
                                        CheckIssue(
                                            "checksum_mismatch",
                                            "SHA-256 differs from baseline",
                                            path,
                                            oid,
                                        )
                                    )
                                    continue
                            checked.append(oid)
                        except (AsterStoreError, OSError) as exc:
                            issues.append(CheckIssue("object_unavailable", str(exc), path, oid))
    except BlockingIOError as exc:
        issues.append(CheckIssue("busy", str(exc)))
    except (AsterStoreError, OSError) as exc:
        issues.append(CheckIssue("metadata_error", str(exc)))
    return CheckReport(
        level,
        started,
        datetime.now(UTC),
        coordinated,
        metadata_complete,
        targets,
        tuple(checked),
        tuple(issues),
    )
