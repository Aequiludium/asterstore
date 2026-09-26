"""Explicit Parquet scans; install the ``polars`` extra to execute them."""

from ._parquet import scan_parquet

__all__ = ["scan_parquet"]
