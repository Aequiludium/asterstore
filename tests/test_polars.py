"""Optional real-engine coverage and the adapter's governance I/O boundary."""

import io
import os

import pytest

from asterstore import (
    Capabilities,
    Declaration,
    FileSet,
    Locator,
    Member,
    Object,
    Repository,
    RetentionScope,
)
from asterstore.integrations.polars import scan_parquet

pl = pytest.importorskip("polars")


def declaration(dataset, paths):
    return Declaration(
        dataset,
        "p1",
        FileSet(
            [Object(str(i), Locator("data", path)) for i, path in enumerate(paths)],
            [Member(path, str(i)) for i, path in enumerate(paths)],
        ),
        Capabilities.registered(),
    )


def initialize(repo):
    repo.initialize(resource_ids=["data"], managed_resource_id="data", lifecycle=True)


def test_exact_paths_and_hive_opt_in(tmp_path):
    repository = Repository(tmp_path)
    initialize(repository)
    with repository.prepare(
        "data", publication_id="p1", operation_id="p1", expected_generation=0, durable=False
    ) as candidate:
        pl.DataFrame({"value": [1]}).write_parquet(
            candidate.path("first", relative_path="day=01/part[1].parquet")
        )
        pl.DataFrame({"value": [2]}).write_parquet(
            candidate.path("second", relative_path="day=02/part[1].parquet")
        )
        publication = candidate.commit()
    binding = repository.open("data")
    # A glob interpretation would select this extra, undeclared file instead.
    pl.DataFrame({"value": [999]}).write_parquet(binding.files()[0].with_name("part1.parquet"))
    result = scan_parquet(binding).sort("value").collect()
    assert result.columns == ["value"]
    assert result["value"].to_list() == [1, 2]
    key = publication.declaration.files.members[1].key
    assert scan_parquet(binding, keys=[key]).collect()["value"].to_list() == [2]
    assert scan_parquet(binding, keys=[key, key]).collect()["value"].to_list() == [2, 2]
    assert scan_parquet(binding, hive_partitioning=True).sort("day").collect()["day"].to_list() == [
        1,
        2,
    ]


def test_retained_lazy_query_and_incremental_collection(tmp_path):
    producer = Repository(tmp_path)
    initialize(producer)
    dataset = "data"
    with producer.prepare(
        dataset, publication_id="p1", operation_id="p1", expected_generation=0, durable=False
    ) as candidate:
        for day, price in [(1, 10), (2, 20)]:
            pl.DataFrame({"day": [day], "price": [price]}).write_parquet(
                candidate.path(str(day), relative_path=f"day={day}/data.parquet")
            )
        first = candidate.commit()
    consumer = Repository(tmp_path)
    held = consumer.governance.retain("run", "data", "p1", scope=RetentionScope.OBJECTS)
    old_query = scan_parquet(
        consumer.governance.open("run", expected_revision=held.revision)
    ).select(pl.col("price").sum())
    with producer.prepare(
        dataset, publication_id="p2", operation_id="p2", expected_generation=1, durable=False
    ) as candidate:
        candidate.reuse(keys=[first.declaration.files.members[0].key])
        pl.DataFrame({"day": [2], "price": [21]}).write_parquet(
            candidate.path("2", relative_path="day=2/data.parquet")
        )
        candidate.commit()
    current = consumer.open("data")
    assert producer.governance.collect("protected").deleted_objects == ()
    assert old_query.collect().item() == 30
    assert scan_parquet(current).select(pl.col("price").sum()).collect().item() == 31
    consumer.governance.release("run", expected_revision=held.revision)
    assert len(producer.governance.collect("released").deleted_objects) == 1
    assert scan_parquet(current).select(pl.col("price").sum()).collect().item() == 31
    # Neither a Binding nor a prebuilt LazyFrame silently pins or refreshes files.
    with pytest.raises(FileNotFoundError):
        old_query.collect()


def test_adapter_adds_no_io_or_retention(tmp_path, monkeypatch):
    binding = Repository(tmp_path / "absent").bind(
        declaration("data", ["missing[1].parquet"]), resources={"data": tmp_path / "absent"}
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
    binding = Repository(tmp_path).bind(declaration("empty", []), resources={})
    with pytest.raises(ValueError, match="empty selection"):
        scan_parquet(binding, keys=keys)


def test_missing_file_is_engine_error(tmp_path):
    binding = Repository(tmp_path).bind(
        declaration("missing", ["absent.parquet"]), resources={"data": tmp_path}
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
