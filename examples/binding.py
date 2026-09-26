"""Resolve supplied declarations; no files or repository metadata are written."""

from asterstore import Dataset, ObjectRef, Publication, Repository


def main() -> None:
    publication = Publication(
        dataset=Dataset("market/prices"),
        publication_id="example-001",
        objects=[ObjectRef("prices/day=2026-09-01/data.parquet")],
    )
    binding = Repository("./example-data").bind(publication)
    for path in binding.files():
        print(path)


if __name__ == "__main__":
    main()
