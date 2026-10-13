"""Explicit maintenance-window compaction with fixed, independently resumable replacements."""

from __future__ import annotations

import hashlib
import json
import zlib
from contextlib import ExitStack
from dataclasses import dataclass, replace
from pathlib import Path

from asterstore.errors import CandidateStateError, StoreCorruptionError
from asterstore.metadata.protocol import encode_store
from asterstore.metadata.protocol._archive import (
    ArchiveReference,
    ControlCheckpoint,
    decode_checkpoint,
    eligible,
    encode_checkpoint,
    encode_reference,
)
from asterstore.storage import atomic_write, immutable_write, sync_control_files
from asterstore.storage._archive import (
    ordinary,
    read_control,
    reference,
    resolve,
)
from asterstore.storage.registry import marker_path, token

from ._control import ControlReader
from ._inventory import entries
from .inspection._locking import existing_lock, inspection_lock
from .inspection._service import read_inventory


@dataclass(frozen=True, slots=True)
class CompactionResult:
    operation_id: str
    records: int
    original_bytes: int
    reference_bytes: int
    archive_bytes: int
    checkpoint_bytes: int
    complete: bool = True

    @property
    def saved_bytes(self) -> int:
        return (
            self.original_bytes - self.reference_bytes - self.archive_bytes - self.checkpoint_bytes
        )


def compact(root: Path, operation_id: str, *, quiescent: bool) -> CompactionResult:
    """Caller must stop all writers, governance mutations and historic readers first."""
    if quiescent is not True:
        raise ValueError("compaction requires an explicit quiescent maintenance window")
    identity = token(operation_id)
    control = root / ".asterstore"
    with inspection_lock(root) as coordinated, ExitStack() as candidate_locks:
        reader = ControlReader()
        view = read_inventory(root, coordinated, reader=reader)
        candidate_ids = {token(op) for op in view.requests}
        for directory in entries(control / "managed-candidates"):
            locked = candidate_locks.enter_context(existing_lock(directory / "writer.lock"))
            if not locked and directory.name in candidate_ids:
                raise StoreCorruptionError("candidate lacks its persistent writer lock")
        committed = {p.operation_id for p in view.publications.values()}
        pending = set(view.requests) - committed
        pending = {
            op
            for op in pending
            if op not in view.abandoned
            or op not in view.cleanup_progress
            or not view.cleanup_progress[op].complete
        }
        for path in reader.paths:
            if path.parent.name == "registrations":
                op = json.loads(read_control(path))["operation_id"]
                if op not in committed and op not in view.requests:
                    pending.add(op)
        if (
            pending
            or any(
                (p.declaration.dataset_id, p.declaration.publication_id) in view.current
                for plan in view.decommission_plans.values()
                for p in plan.targets
            )
            or any(not p.complete for p in view.collection_progress.values())
            or any(not p.complete for p in view.cleanup_progress.values())
        ):
            raise CandidateStateError("unfinished operations prevent control compaction")
        directory = control / "archives"
        if directory.exists():
            ordinary(directory, directory=True)
        checkpoint = directory / (identity + ".json")
        if checkpoint.exists():
            ordinary(checkpoint)
            data = checkpoint.read_bytes()
            plan = decode_checkpoint(data)
            if plan.operation_id != operation_id or plan.store_id != view.store.store_id:
                raise StoreCorruptionError("checkpoint belongs to another operation or store")
            records = plan.records
            archive = directory / (next(iter(records.values()))["archive"] + ".pack")
        else:
            records = {}
            blocks: dict[bytes, ArchiveReference] = {}
            packed = bytearray()
            for path in sorted(reader.paths):
                name = path.relative_to(control).as_posix()
                if not eligible(name):
                    continue
                raw = path.read_bytes()
                if reference(raw) is not None:
                    continue  # Immutable old segments are never reclaimed or repacked.
                value = blocks.get(raw)
                if value is None:
                    block = zlib.compress(raw)
                    value = ArchiveReference(
                        format_version=1,
                        kind="archive_reference",
                        archive=identity,
                        offset=len(packed),
                        length=len(block),
                        size=len(raw),
                        sha256=hashlib.sha256(raw).hexdigest(),
                    )
                    # Keep small records inline; total overhead may still exceed savings.
                    if len(encode_reference(value)) >= len(raw):
                        continue
                    blocks[raw] = value
                    packed.extend(block)
                records[name] = value
            archive_id = hashlib.sha256(packed).hexdigest()
            archive = directory / (archive_id + ".pack")
            for value in records.values():
                value["archive"] = archive_id
            data = encode_checkpoint(ControlCheckpoint(view.store.store_id, operation_id, records))
            expected_saving = (
                sum(v["size"] - len(encode_reference(v)) for v in records.values())
                - len(packed)
                - len(data)
            )
            marker_growth = max(
                0,
                len(encode_store(replace(view.store, archived=True)))
                - len(marker_path(root).read_bytes()),
            )
            if expected_saving <= marker_growth:
                raise ValueError("no positive control-byte savings; nothing was compacted")
            # Preserve inherited weak control evidence before replacing its representation.
            # This does not sync or upgrade any data payload.
            sync_control_files(root, tuple(sorted(view.control_paths)))
            # Enable the required feature BEFORE creating a new top-level authority layout.
            # Old clients must already be stopped; they cannot safely coexist during migration.
            if not view.store.archived:
                atomic_write(marker_path(root), encode_store(replace(view.store, archived=True)))
            immutable_write(archive, bytes(packed))
            immutable_write(checkpoint, data)
        ordinary(archive)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != archive.stem:
            raise StoreCorruptionError("archive content address differs")
        # Preflight the entire fixed plan before replacing any record. Missing is corruption.
        for name, value in records.items():
            path = control / name
            ordinary(path)
            expected = resolve(path, value)
            if read_control(path) != expected:
                raise StoreCorruptionError("compaction input changed since checkpoint")
        sync_control_files(root, (marker_path(root), archive, checkpoint))
        for name, value in records.items():
            path = control / name
            encoded = encode_reference(value)
            if path.read_bytes() != encoded:
                atomic_write(path, encoded)
        sync_control_files(root, tuple(control / name for name in records))
        return CompactionResult(
            operation_id,
            len(records),
            sum(v["size"] for v in records.values()),
            sum(len(encode_reference(v)) for v in records.values()),
            archive.stat().st_size,
            len(data),
        )
