"""Natural Hive scanning excludes staging and rollback material."""

from datetime import date
from pathlib import Path

import polars as pl

from asterstore import Repository


def test_hive_revision_and_rollback(tmp_path: Path) -> None:
    repo = Repository(tmp_path / "registry")
    repo.initialize()
    ds = repo.create_dataset("market", tmp_path / "market")
    with ds.write() as batch:
        for day in (1, 2):
            pl.DataFrame({"price": [day]}).write_parquet(
                batch.stage(f"date=2026-10-0{day}/data.parquet")
            )
        batch.commit(metadata={"through": "2026-10-02"}, quiescent=True)
    with ds.write() as batch:
        pl.DataFrame({"price": [99]}).write_parquet(batch.stage("date=2026-10-01/data.parquet"))
        batch.commit(metadata={"through": "2026-10-02"}, quiescent=True)
    ds.current()
    result = (
        pl.scan_parquet(ds.root / "date=*/data.parquet", hive_partitioning=True)
        .sort("date")
        .collect()
    )
    assert result["price"].to_list() == [99, 2]
    assert result["date"].to_list() == [date(2026, 10, 1), date(2026, 10, 2)]
    ds.clean()
    ds.rollback(quiescent=True)
    result = (
        pl.scan_parquet(ds.root / "date=*/data.parquet", hive_partitioning=True)
        .sort("date")
        .collect()
    )
    assert result["price"].to_list() == [1, 2]
