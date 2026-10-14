import pytest

from asterstore import Repository
from asterstore.integrations.polars import scan_parquet


def test_exact_files_and_hive(tmp_path):
    pl = pytest.importorskip("polars")
    repo = Repository(tmp_path)
    repo.initialize()
    with repo.prepare("prices") as draft:
        path = draft.allocate("day", relative_path="date=2026-10-14/data.parquet")
        pl.DataFrame({"value": [1, 2]}).write_parquet(path)
        draft.allocate("metadata").write_text("{}")
        draft.seal()
        draft.commit()
    result = scan_parquet(repo.open("prices"), keys=["day"], hive_partitioning=True).collect()
    assert result["value"].to_list() == [1, 2]
    assert "date" in result.columns
