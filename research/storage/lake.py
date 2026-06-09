"""
Parquet partition read/write conventions for the research data lake.

Disk layout (matches partition template in config/sources.yaml):
    <base_dir>/<dataset>/<symbol>/year=YYYY/month=MM/data.parquet

Schema decisions (column names, dtypes) belong to the ingest layer (WI-3+).
lake.py only handles paths and Parquet I/O — no domain knowledge here.
"""
from __future__ import annotations

import pathlib

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


def partition_path(
    base_dir: str | pathlib.Path,
    dataset: str,
    symbol: str,
    year: int,
    month: int,
) -> pathlib.Path:
    """Return the directory path for one monthly partition (does not create it)."""
    return (
        pathlib.Path(base_dir)
        / dataset
        / symbol
        / f"year={year}"
        / f"month={month:02d}"
    )


def write_partition(
    df: pd.DataFrame,
    base_dir: str | pathlib.Path,
    dataset: str,
    symbol: str,
    year: int,
    month: int,
    *,
    compression: str = "zstd",
) -> pathlib.Path:
    """
    Write *df* as a single Parquet file into the appropriate partition directory.
    Idempotent: overwrites any existing file so re-ingest is safe.
    Returns the path of the written file.
    """
    out_dir = partition_path(base_dir, dataset, symbol, year, month)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "data.parquet"
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), out_file,
                   compression=compression)
    return out_file


def list_partitions(
    base_dir: str | pathlib.Path,
    dataset: str,
    symbol: str,
) -> list[dict]:
    """
    Discover existing monthly partitions under base_dir/dataset/symbol/.
    Returns a list sorted by (year, month):
        [{"year": int, "month": int, "path": pathlib.Path}, ...]
    Only entries whose data.parquet file actually exists are included.
    """
    root = pathlib.Path(base_dir) / dataset / symbol
    if not root.exists():
        return []

    results = []
    for year_dir in sorted(root.iterdir()):
        if not year_dir.is_dir() or not year_dir.name.startswith("year="):
            continue
        try:
            year = int(year_dir.name.split("=", 1)[1])
        except ValueError:
            continue
        for month_dir in sorted(year_dir.iterdir()):
            if not month_dir.is_dir() or not month_dir.name.startswith("month="):
                continue
            try:
                month = int(month_dir.name.split("=", 1)[1])
            except ValueError:
                continue
            data_file = month_dir / "data.parquet"
            if data_file.exists():
                results.append({"year": year, "month": month, "path": data_file})

    return sorted(results, key=lambda r: (r["year"], r["month"]))


def read_partition(
    base_dir: str | pathlib.Path,
    dataset: str,
    symbol: str,
    year: int,
    month: int,
) -> pd.DataFrame:
    """Read one monthly partition back to a DataFrame."""
    data_file = partition_path(base_dir, dataset, symbol, year, month) / "data.parquet"
    if not data_file.exists():
        raise FileNotFoundError(f"Partition not found: {data_file}")
    return pq.read_table(data_file).to_pandas()
