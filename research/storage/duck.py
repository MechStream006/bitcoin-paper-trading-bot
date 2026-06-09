"""
DuckDB harness for zero-copy SQL queries over the Parquet research lake.

The lake is authoritative (Parquet files on disk).  DuckDB is a query engine only
— it holds no state of its own.  Open a fresh connection per analysis session;
re-register views each time.

Typical usage:
    con = open_db()
    n = register_view(con, "spot", base_dir, "spot", "BTCUSDT")
    df = query(con, "SELECT COUNT(*) AS n FROM spot")
    con.close()
"""
from __future__ import annotations

import pathlib

import duckdb
import pandas as pd

from storage.lake import list_partitions


def open_db() -> duckdb.DuckDBPyConnection:
    """Open a fresh in-memory DuckDB connection. The lake lives in Parquet, not here."""
    return duckdb.connect(database=":memory:")


def register_view(
    con: duckdb.DuckDBPyConnection,
    view_name: str,
    base_dir: str | pathlib.Path,
    dataset: str,
    symbol: str,
) -> int:
    """
    Register a DuckDB VIEW named *view_name* spanning all monthly partitions
    for *dataset*/*symbol* under *base_dir*.

    Uses an explicit file list (not a shell glob) so Windows path separators
    are not a problem.  Returns the number of partitions registered.
    Raises ValueError if no partitions exist yet.
    """
    partitions = list_partitions(base_dir, dataset, symbol)
    if not partitions:
        raise ValueError(
            f"No partitions found for dataset={dataset!r} symbol={symbol!r} "
            f"under {pathlib.Path(base_dir)}"
        )
    # Build a DuckDB list literal of POSIX paths — forward slashes on all platforms.
    paths_sql = ", ".join(f"'{p['path'].as_posix()}'" for p in partitions)
    con.execute(
        f"CREATE OR REPLACE VIEW {view_name} AS "
        f"SELECT * FROM read_parquet([{paths_sql}])"
    )
    return len(partitions)


def query(con: duckdb.DuckDBPyConnection, sql: str) -> pd.DataFrame:
    """Execute *sql* against the DuckDB connection and return a pandas DataFrame."""
    return con.execute(sql).df()
