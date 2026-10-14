"""Optional Polars adapter with a generic partition path."""

from tempfile import TemporaryDirectory

import polars as pl

from asterstore import Repository
from asterstore.integrations.polars import scan_parquet

with TemporaryDirectory() as temporary:
    repo = Repository(temporary)
    repo.initialize()
    with repo.prepare("table") as draft:
        path = draft.allocate("partition", relative_path="day=2026-10-14/data.parquet")
        pl.DataFrame({"value": [1, 2]}).write_parquet(path)
        draft.seal()
        draft.commit()
    assert scan_parquet(repo.open("table"), hive_partitioning=True).collect().height == 2
