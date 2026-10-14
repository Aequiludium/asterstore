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
    with (
        fs.lock(repo.root, (directory / "writer.lock").relative_to(repo.root).as_posix()),
        fs.lock(repo.root, ".asterstore/ownership.lock"),
    ):
        head = repo._head(dataset_id)
        if head is None:
            return CleanResult(dataset_id, dry_run, skipped="no head; explicit recovery required")
        claims = repo._claims()
        owned = {path for path, owner in claims.items() if owner == dataset_id}
        keep = set()
        for version_id in (head.current, head.rollback):
            if version_id is None:
                continue
            manifest = repo._manifest(dataset_id, version_id)
            keep.add(directory / "versions" / f"{version_id}.json")
            keep.update(repo._member_path(dataset_id, m.path) for m in manifest.members)
        # Validate *all* retained files and owned subtrees before the first unlink.
        for path in keep:
            fs.check_file_path(path)
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
        # External directories are never inventoried: manifests name the exact owned files.
        for path in owned:
            fs.check_file_path(path, missing=True)
            if path.exists() and not path.is_file():
                raise StoreCorruptionError(f"owned path is not a regular file: {path}")
        all_files = set(files) | {path for path in owned if path.exists()}
        candidates = sorted(all_files - keep)
        sizes = {path: path.stat().st_size for path in candidates}
        deleted, deleted_bytes = 0, 0
        if not dry_run:
            # Upgrade weak publication state BEFORE deleting any former rollback bytes.
            repo._sync_retained(head)
            fs.sync_paths(repo.root, {directory / "head.json"})
            manifests = {p for p in candidates if p.parent == directory / "versions"}
            for path in candidates:
                if path not in manifests:
                    path.unlink(missing_ok=True)
                    deleted += 1
                    deleted_bytes += sizes[path]
            # Keep ownership evidence until external unlinks are durable, including retry
            # paths already missing after an earlier interrupted deletion.
            parents = set()
            for path in owned - keep:
                if not path.is_relative_to(directory / "objects"):
                    parent = path.parent
                    while not parent.exists():
                        parent = parent.parent
                    parents.add(parent)
            for parent in parents:
                fs.sync_directory(parent)
            for path in sorted(manifests):
                path.unlink(missing_ok=True)
                deleted += 1
                deleted_bytes += sizes[path]
            # A completed cleanup must durably release ownership, not just data bytes.
            # Also settle an interrupted prior cleanup whose manifests are already absent.
            fs.sync_directory(directory / "versions")
            for path in sorted(directories, key=lambda p: len(p.parts), reverse=True):
                if not any(path.iterdir()):
                    path.rmdir()
        return CleanResult(
            dataset_id,
            dry_run,
            len(all_files),
            len(candidates),
            sum(sizes.values()),
            deleted,
            deleted_bytes,
        )
