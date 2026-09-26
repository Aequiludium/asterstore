import pytest

from asterstore import Dataset, ObjectRef, Publication


@pytest.fixture
def publication() -> Publication:
    return Publication(
        dataset=Dataset("market/prices"),
        publication_id="batch-001",
        objects=(ObjectRef("prices/day=2026-09-01/data.parquet"), ObjectRef("prices/extra.bin")),
    )


@pytest.fixture
def publish(tmp_path):
    from asterstore import PhysicalHistory, Repository

    def create(publication_id, dataset_id="data", history=PhysicalHistory.CURRENT_ONLY):
        with Repository(tmp_path).prepare(
            Dataset(dataset_id, history), publication_id=publication_id, durable=False
        ) as candidate:
            candidate.write_bytes("part.bin", publication_id.encode())
            return candidate.commit()

    return create
