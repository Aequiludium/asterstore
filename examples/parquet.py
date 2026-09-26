"""A standalone Polars producer and two consumers with explicit retention."""

from pathlib import Path
from tempfile import TemporaryDirectory

import polars as pl

from asterstore import CollectionPolicy, Dataset, Repository
from asterstore.integrations.polars import scan_parquet


def main() -> None:
    with TemporaryDirectory(prefix="asterstore-parquet-") as directory:
        producer = Repository(Path(directory))
        dataset = Dataset("prices")
        with producer.prepare(dataset, publication_id="p1") as candidate:
            pl.DataFrame({"day": [1], "price": [10]}).write_parquet(
                candidate.path("day=01/data.parquet")
            )
            pl.DataFrame({"day": [2], "price": [20]}).write_parquet(
                candidate.path("day=02/data.parquet")
            )
            first = candidate.commit()

        consumer = Repository(Path(directory))
        held = consumer.retention.retain("research/run", "prices")
        old_query = scan_parquet(held.binding).select(pl.col("price").sum())
        with producer.prepare(dataset, publication_id="p2") as candidate:
            candidate.reuse(keys=[first.objects[0].key])
            pl.DataFrame({"day": [2], "price": [21]}).write_parquet(
                candidate.path("day=02/data.parquet")
            )
            candidate.commit()

        policy = CollectionPolicy(("prices",))
        assert not producer.retention.collect(policy).deleted_objects
        assert old_query.collect().item() == 30
        current = consumer.open("prices")
        assert scan_parquet(current).select(pl.col("price").sum()).collect().item() == 31
        # Release only after the lazy query has actually finished executing.
        consumer.retention.release("research/run", expected_revision=held.revision)
        collected = producer.retention.collect(policy)
        assert collected.status == "complete"
        assert collected.deleted_objects == first.objects[1:]
        assert scan_parquet(current).select(pl.col("price").sum()).collect().item() == 31
        print("Parquet: retained reader=30, current reader=31; only replaced partition reclaimed.")


if __name__ == "__main__":
    main()
