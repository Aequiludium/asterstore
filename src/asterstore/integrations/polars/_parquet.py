"""Pass exact membership to Polars without governance work on the read path."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from asterstore.metadata import Declaration, Publication
from asterstore.reading import Binding

if TYPE_CHECKING:
    import polars as pl


def scan_parquet(
    binding: Binding[Publication | Declaration],
    *,
    keys: Sequence[str] | None = None,
    hive_partitioning: bool = False,
) -> pl.LazyFrame:
    """Build a lazy scan of explicitly selected files, with glob expansion disabled.

    No schema/existence check, refresh, or retention is added by this adapter.
    Polars owns decoding and its own I/O. A lazy plan does not retain its files:
    hold an explicit reference through collection when GC can run concurrently.
    Empty selections raise ValueError because the binding declares no schema.
    Use binding.files() directly for other engine-specific scan options.
    """
    files = binding.files(keys=keys)
    if not files:
        raise ValueError("cannot scan an empty selection without a declared schema")

    import polars as pl

    return pl.scan_parquet(list(files), glob=False, hive_partitioning=hive_partitioning)
