"""A retained lazy query survives incremental publication and explicit collection."""

from pathlib import Path
from tempfile import TemporaryDirectory

import polars as pl

from asterstore import Repository, RetentionScope
from asterstore.integrations.polars import scan_parquet


def main() -> None:
    with TemporaryDirectory(prefix="asterstore-parquet-") as temporary:
        repo = Repository(Path(temporary))
        repo.initialize(resource_ids=["data"], managed_resource_id="data", lifecycle=True)
        with repo.prepare(
            "prices", publication_id="p1", operation_id="write:1", expected_generation=0
        ) as writer:
            for day, price in [(1, 10), (2, 20)]:
                pl.DataFrame({"day": [day], "price": [price]}).write_parquet(
                    writer.path(str(day), relative_path=f"day={day}/data.parquet")
                )
            writer.commit()
        held = repo.governance.retain("reader", "prices", "p1", scope=RetentionScope.OBJECTS)
        old_query = scan_parquet(repo.governance.open("reader", expected_revision=held.revision))
        with repo.prepare(
            "prices", publication_id="p2", operation_id="write:2", expected_generation=1
        ) as writer:
            writer.reuse("p1", keys=["1"])
            pl.DataFrame({"day": [2], "price": [21]}).write_parquet(
                writer.path("2", relative_path="day=2/data.parquet")
            )
            writer.commit()
        assert repo.governance.collect("protected").deleted_objects == ()
        assert old_query.select(pl.col("price").sum()).collect().item() == 30
        assert (
            scan_parquet(repo.open("prices")).select(pl.col("price").sum()).collect().item() == 31
        )
        repo.governance.release("reader", expected_revision=held.revision)
        assert len(repo.governance.collect("released").deleted_objects) == 1
        assert (
            scan_parquet(repo.open("prices")).select(pl.col("price").sum()).collect().item() == 31
        )
        print("Parquet: retained reader=30, current reader=31; only replaced partition reclaimed.")


if __name__ == "__main__":
    main()
