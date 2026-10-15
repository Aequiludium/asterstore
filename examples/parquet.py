"""Publish and read factor data using natural Hive paths."""

from pathlib import Path
from tempfile import TemporaryDirectory

import polars as pl

from asterstore import Repository

with TemporaryDirectory() as temporary:
    root = Path(temporary)
    repo = Repository(root / "registry")
    repo.initialize()
    dataset = repo.create_dataset("factor", root / "factor")
    with dataset.write() as batch:
        for day in (1, 2):
            pl.DataFrame({"asset": ["A"], "value": [day]}).write_parquet(
                batch.stage(f"version=v1/date=2026-10-{day:02d}/data.parquet")
            )
        batch.commit(metadata={"definition": "v1"}, quiescent=True)
    state = dataset.current()
    frame = pl.scan_parquet(
        dataset.root / f"version={state.metadata['definition']}/date=*/data.parquet",
        hive_partitioning=True,
    )
    assert frame.select(pl.len()).collect().item() == 2
    print("Natural Hive read: PASS")
