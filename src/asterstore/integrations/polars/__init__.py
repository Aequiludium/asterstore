"""Exact-member Polars scanning; no version refresh or implicit protection."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from asterstore import Binding

if TYPE_CHECKING:
    import polars as pl


def scan_parquet(
    binding: Binding,
    *,
    keys: Sequence[str] | None = None,
    hive_partitioning: bool = False,
) -> pl.LazyFrame:
    """Consume before the next coordinated cleanup; a LazyFrame does not pin files."""
    files = binding.files(keys=keys)
    if not files:
        raise ValueError("cannot infer schema from an empty file selection")
    import polars as pl

    return pl.scan_parquet(list(files), glob=False, hive_partitioning=hive_partitioning)


__all__ = ["scan_parquet"]
