"""Explicit object checks; no registry traversal, coordination, or ownership changes."""

import hashlib
import os
import re
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from .errors import DataCheckError
from .metadata.protocol import DeclarationRecord

Check = Literal["none", "existence", "format", "checksum"]


class InvalidFileType(OSError):
    """A declared object or its managed parent is not an ordinary filesystem entry."""


def regular_file(path: Path, managed_root: Path | None) -> None:
    if managed_root is not None:
        relative = path.relative_to(managed_root)
        parent = managed_root
        for part in ("", *relative.parts[:-1]):
            parent = parent / part
            if not stat.S_ISDIR(parent.lstat().st_mode):
                raise InvalidFileType(f"managed parent is not an ordinary directory: {parent}")
    if not stat.S_ISREG(path.lstat().st_mode):
        raise InvalidFileType(f"object is not an ordinary file: {path}")


def checksum(path: Path) -> str:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise InvalidFileType("object is not an ordinary file")
        digest = hashlib.sha256()
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
        return digest.hexdigest()


@dataclass(frozen=True)
class CheckOptions:
    check: Check = "none"
    validator: Callable[[Path], None] | None = None
    checksums: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        if self.check not in ("none", "existence", "format", "checksum"):
            raise ValueError("unknown check mode")
        if (self.check == "format") != (self.validator is not None):
            raise ValueError("only format checking requires a validator")
        if self.validator is not None and not callable(self.validator):
            raise TypeError("validator must be callable")
        if (self.check == "checksum") != (self.checksums is not None):
            raise ValueError("only checksum checking requires trusted SHA-256 baselines")
        if self.checksums is not None:
            baselines = dict(self.checksums)
            if any(
                not isinstance(k, str)
                or not isinstance(v, str)
                or not re.fullmatch(r"[0-9a-fA-F]{64}", v)
                for k, v in baselines.items()
            ):
                raise ValueError("checksums must map object IDs to SHA-256 hex strings")
            object.__setattr__(self, "checksums", MappingProxyType(baselines))

    def run(
        self,
        record: DeclarationRecord,
        paths: Mapping[str, Path],
        *,
        managed_root: Path | None = None,
    ) -> None:
        if self.check == "none":
            return
        if self.checksums is not None and set(self.checksums) != set(paths):
            raise ValueError("checksum keys must exactly match the declaration's object IDs")
        for oid, path in sorted(paths.items()):
            code = "io_error"
            try:
                regular_file(path, managed_root)
                if self.check == "format":
                    assert self.validator is not None
                    code = "validation_failed"
                    self.validator(path)
                elif self.check == "checksum":
                    assert self.checksums is not None
                    if checksum(path) != self.checksums[oid].lower():
                        code = "checksum_mismatch"
                        raise ValueError("SHA-256 differs from trusted baseline")
            except Exception as exc:
                if code != "validation_failed":
                    if isinstance(exc, FileNotFoundError):
                        code = "missing"
                    elif isinstance(exc, InvalidFileType):
                        code = "invalid_type"
                raise DataCheckError(
                    check=self.check,
                    code=code,
                    object_id=oid,
                    path=path,
                    dataset_id=record.declaration.dataset_id,
                    publication_id=record.declaration.publication_id,
                    operation_id=record.operation_id,
                ) from exc
