"""Optional real-engine coverage and the adapter's governance I/O boundary."""

import io
import os

import pytest

from asterstore import CollectionPolicy, Dataset, ObjectRef, Publication, Repository
from asterstore.integrations.polars import scan_parquet

pl = pytest.importorskip("polars")


def test_exact_paths_and_hive_opt_in(tmp_path):
    repository = Repository(tmp_path)
    with repository.prepare(Dataset("data"), durable=False) as candidate:
        pl.DataFrame({"value": [1]}).write_parquet(candidate.path("day=01/part[1].parquet"))
        pl.DataFrame({"value": [2]}).write_parquet(candidate.path("day=02/part[1].parquet"))
        publication = candidate.commit()
    binding = repository.bind(publication)
    # A glob interpretation would select this extra, undeclared file instead.
    pl.DataFrame({"value": [999]}).write_parquet(binding.files()[0].with_name("part1.parquet"))
    result = scan_parquet(binding).sort("value").collect()
    assert result.columns == ["value"]
    assert result["value"].to_list() == [1, 2]
    key = publication.objects[1].key
    assert scan_parquet(binding, keys=[key]).collect()["value"].to_list() == [2]
    assert scan_parquet(binding, keys=[key, key]).collect()["value"].to_list() == [2, 2]
    assert scan_parquet(binding, hive_partitioning=True).sort("day").collect()["day"].to_list() == [
        1,
        2,
    ]


def test_retained_lazy_query_and_incremental_collection(tmp_path):
    producer = Repository(tmp_path)
    dataset = Dataset("data")
    with producer.prepare(dataset, publication_id="p1", durable=False) as candidate:
        for day, price in [(1, 10), (2, 20)]:
            pl.DataFrame({"day": [day], "price": [price]}).write_parquet(
                candidate.path(f"day={day}/data.parquet")
            )
        first = candidate.commit()
    consumer = Repository(tmp_path)
    held = consumer.retention.retain("run", "data", durable=False)
    old_query = scan_parquet(held.binding).select(pl.col("price").sum())
    with producer.prepare(dataset, publication_id="p2", durable=False) as candidate:
        candidate.reuse(keys=[first.objects[0].key])
        pl.DataFrame({"day": [2], "price": [21]}).write_parquet(
            candidate.path("day=2/data.parquet")
        )
        candidate.commit()
    current = consumer.open("data")
    policy = CollectionPolicy(("data",))
    assert producer.retention.collect(policy).deleted_objects == ()
    assert old_query.collect().item() == 30
    assert scan_parquet(current).select(pl.col("price").sum()).collect().item() == 31
    consumer.retention.release("run", expected_revision=held.revision)
    assert producer.retention.collect(policy).deleted_objects == first.objects[1:]
    assert scan_parquet(current).select(pl.col("price").sum()).collect().item() == 31
    # Neither a Binding nor a prebuilt LazyFrame silently pins or refreshes files.
    with pytest.raises(FileNotFoundError):
        old_query.collect()


def test_adapter_adds_no_io_or_retention(tmp_path, monkeypatch):
    binding = Repository(tmp_path / "absent").bind(
        Publication(Dataset("data"), "p1", [ObjectRef("missing[1].parquet")])
    )
    sentinel = object()
    calls = []

    def engine(source, **options):
        calls.append((source, options))
        return sentinel

    def forbidden(*args, **kwargs):
        raise AssertionError("adapter performed I/O")

    monkeypatch.setattr(pl, "scan_parquet", engine)
    with monkeypatch.context() as patch:
        for target, names in [(os, ("stat", "lstat", "scandir", "open")), (io, ("open",))]:
            for name in names:
                patch.setattr(target, name, forbidden)
        patch.setattr("builtins.open", forbidden)
        assert scan_parquet(binding) is sentinel
        assert scan_parquet(binding, hive_partitioning=True) is sentinel
    assert calls == [
        ([tmp_path / "absent/missing[1].parquet"], {"glob": False, "hive_partitioning": hive})
        for hive in (False, True)
    ]
    assert not (tmp_path / "absent").exists()


@pytest.mark.parametrize("keys", [None, []])
def test_empty_selection_does_not_invent_schema(tmp_path, keys):
    binding = Repository(tmp_path).bind(Publication(Dataset("empty"), "p1", []))
    with pytest.raises(ValueError, match="empty selection"):
        scan_parquet(binding, keys=keys)


def test_missing_file_is_engine_error(tmp_path):
    binding = Repository(tmp_path).bind(
        Publication(Dataset("missing"), "p1", [ObjectRef("absent.parquet")])
    )
    with pytest.raises(FileNotFoundError):
        scan_parquet(binding).collect()


def test_engine_reads_logical_members_without_implicit_external_snapshot(tmp_path):
    from asterstore import Capabilities, Declaration, FileSet, Locator, Member, Object

    external = tmp_path / "simulation"
    external.mkdir()
    first, second = external / "part[1].parquet", external / "part[2].parquet"
    pl.DataFrame({"temperature": [10]}).write_parquet(first)
    pl.DataFrame({"temperature": [20]}).write_parquet(second)
    pl.DataFrame({"temperature": [999]}).write_parquet(external / "part1.parquet")
    declaration = Declaration(
        "simulation:temperature",
        "run:1",
        FileSet(
            [
                Object("initial", Locator("results", first.name)),
                Object("final", Locator("results", second.name)),
            ],
            [
                Member("phase:initial", "initial"),
                Member("phase:final", "final"),
                Member("alias:final", "final"),
            ],
        ),
        Capabilities.registered(),
    )
    root = tmp_path / "control"
    binding = Repository(root).bind(declaration, resources={"results": external})
    assert scan_parquet(binding).collect()["temperature"].to_list() == [10, 20]
    assert scan_parquet(binding, keys=["alias:final"]).collect().item() == 20
    pl.DataFrame({"temperature": [21]}).write_parquet(second)
    assert scan_parquet(binding, keys=["phase:final"]).collect().item() == 21
    assert not root.exists()
