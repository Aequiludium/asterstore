"""Archive references preserve control paths; current records never use this layer."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import zlib
from pathlib import Path

from asterstore.errors import StoreCorruptionError
from asterstore.metadata.protocol._archive import (
    ArchiveReference,
    decode_checkpoint,
    decode_reference,
    eligible,
)
from asterstore.metadata.protocol._json import load


def reference(data: bytes) -> ArchiveReference | None:
    # Ordinary control records perform no archive filesystem work.
    if b'"archive_reference"' not in data:
        return None
    try:
        value = load(data)
    except ValueError as exc:
        raise StoreCorruptionError(str(exc)) from exc
    if value.get("kind") != "archive_reference":
        return None
    return decode_reference(data)


def control_root(path: Path) -> Path:
    for parent in path.parents:
        if parent.name == ".asterstore":
            return parent
    raise StoreCorruptionError("archive reference outside control directory")


def ordinary(path: Path, *, directory: bool = False) -> None:
    check = stat.S_ISDIR if directory else stat.S_ISREG
    if not check(path.lstat().st_mode):
        raise StoreCorruptionError(f"archive path is not ordinary: {path}")


def archive_path(path: Path, value: ArchiveReference) -> Path:
    control = control_root(path)
    ordinary(control, directory=True)
    ordinary(control / "archives", directory=True)
    return control / "archives" / (value["archive"] + ".pack")


def resolve(path: Path, value: ArchiveReference) -> bytes:
    try:
        target = archive_path(path, value)
        fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise StoreCorruptionError("archive is not a regular file")
            if value["offset"] + value["length"] > os.fstat(stream.fileno()).st_size:
                raise StoreCorruptionError("archive extent is truncated")
            stream.seek(value["offset"])
            packed = stream.read(value["length"])
        decoder = zlib.decompressobj()
        data = decoder.decompress(packed, value["size"] + 1)
        if (
            len(data) != value["size"]
            or not decoder.eof
            or decoder.unused_data
            or decoder.unconsumed_tail
            or hashlib.sha256(data).hexdigest() != value["sha256"]
        ):
            raise StoreCorruptionError("archive block integrity failure")
        if reference(data) is not None:
            raise StoreCorruptionError("nested archive reference")
        return data
    except (OSError, zlib.error, OverflowError) as exc:
        raise StoreCorruptionError(f"archive unavailable: {exc}") from exc


ArchiveCache = dict[tuple[Path, str, int, int, int, str], bytes]


def read_control(path: Path, *, cache: ArchiveCache | None = None) -> bytes:
    data = path.read_bytes()
    value = reference(data)
    if value is None:
        return data
    if not eligible(path.relative_to(control_root(path)).as_posix()):
        raise StoreCorruptionError("archive reference at forbidden control path")
    if cache is None:
        return resolve(path, value)
    key = (
        control_root(path),
        value["archive"],
        value["offset"],
        value["length"],
        value["size"],
        value["sha256"],
    )
    if key not in cache:
        cache[key] = resolve(path, value)
    return cache[key]


def validate_layout(control: Path, store_id: str) -> None:
    """Full governance audit only; ordinary current and task retry do not scan archives."""
    directory = control / "archives"
    try:
        ordinary(directory, directory=True)
    except FileNotFoundError:
        return  # Feature enabled before creating the first archive is recoverable.
    for path in sorted(directory.iterdir()):
        ordinary(path)
        if re.fullmatch(r"\.[0-9a-f]{64}\.(?:pack|json)\.[a-z0-9_]+", path.name):
            continue  # Interrupted atomic output, never authority or a deletion target.
        if not re.fullmatch(r"[0-9a-f]{64}\.(?:pack|json)", path.name):
            raise StoreCorruptionError("unknown archive entry")
        if path.suffix == ".json":
            plan = decode_checkpoint(path.read_bytes())
            if (
                plan.store_id != store_id
                or hashlib.sha256(plan.operation_id.encode()).hexdigest() != path.stem
            ):
                raise StoreCorruptionError("checkpoint path or store differs")
            try:
                ordinary(directory / (next(iter(plan.records.values()))["archive"] + ".pack"))
            except OSError as exc:
                raise StoreCorruptionError("checkpoint archive missing") from exc
