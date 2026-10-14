"""Quiescent dataset cleanup based only on two retained manifests."""

from __future__ import annotations

from . import _storage as fs
from ._models import CleanResult
from .errors import StoreCorruptionError
from .repository import Repository


def clean(repo: Repository, dataset_id: str, *, quiescent: bool, dry_run: bool) -> CleanResult:
    if quiescent is not True:
        raise ValueError("cleanup requires a caller-coordinated quiescent window")
    if type(dry_run) is not bool:
        raise TypeError("dry_run must be bool")
    repo._marker()
    directory = repo._directory(dataset_id)
    if not directory.exists():
        return CleanResult(dataset_id, dry_run, skipped="no dataset")
    with fs.lock(repo.root, (directory / "writer.lock").relative_to(repo.root).as_posix()):
        head = repo._head(dataset_id)
        if head is None:
            return CleanResult(dataset_id, dry_run, skipped="no head; explicit recovery required")
        keep = set()
        for version_id in (head.current, head.rollback):
            if version_id is None:
                continue
            manifest = repo._manifest(dataset_id, version_id)
            keep.add(directory / "versions" / f"{version_id}.json")
            keep.update(directory / "objects" / m.path for m in manifest.members)
        # Validate *all* retained files and owned subtrees before the first unlink.
        for path in keep:
            fs.checked(repo.root, path.relative_to(repo.root).as_posix())
            if not path.is_file():
                raise StoreCorruptionError(f"missing retained regular file: {path}")
        files, directories = [], []
        for name in ("objects", "versions", "staging"):
            children, folders = fs.inventory(repo.root, directory / name)
            files.extend(children)
            directories.extend(folders)
        # Atomic-write leftovers have no authority. Reject other unknown control entries.
        for path in directory.iterdir():
            fs.checked(repo.root, path.relative_to(repo.root).as_posix())
            if path.name in {"objects", "versions", "staging", "head.json", "writer.lock"}:
                continue
            if path.name.startswith(".tmp-") and path.is_file():
                files.append(path)
            else:
                raise StoreCorruptionError(f"unexpected dataset entry: {path}")
        candidates = sorted(set(files) - keep)
        sizes = {path: path.stat().st_size for path in candidates}
        deleted, deleted_bytes = 0, 0
        if not dry_run:
            # Upgrade weak publication state BEFORE deleting any former rollback bytes.
            repo._sync_retained(head)
            fs.sync_paths(repo.root, {directory / "head.json"})
            for path in candidates:
                path.unlink(missing_ok=True)
                deleted += 1
                deleted_bytes += sizes[path]
            for path in sorted(directories, key=lambda p: len(p.parts), reverse=True):
                if not any(path.iterdir()):
                    path.rmdir()
        return CleanResult(
            dataset_id,
            dry_run,
            len(files),
            len(candidates),
            sum(sizes.values()),
            deleted,
            deleted_bytes,
        )
