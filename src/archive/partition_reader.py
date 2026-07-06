"""Read-only hot-DB day-partition reader.

Reads a single UTC-day partition of option_quotes from the hot SQLite DB
for handoff to content_hash.py / parquet_writer.py. Opens the DB via a
read-only ("mode=ro") URI, the same discipline already used by
src/alerts/forward_status.py -- this module is a consumer of the hot
path, never a writer, and never creates a missing DB file.

Symbol resolution
------------------
option_quotes is keyed by instrument_id only (see src/data/storage.py) --
it has no symbol column. The canonical_sort key in content_hash.py needs
`symbol`, so this module resolves instrument_id -> symbol using a
two-tier lookup:

  1. instrument_map_log, filtered to seen_date = the partition's own UTC
     day. This is the day-accurate mapping (see scripts/snapshot_instrument_map.py)
     and is preferred because the live `instruments` table only reflects
     the *current* universe -- an instrument retired since the exported
     day would be missing from it entirely.
  2. The live `instruments` table, as a fallback for any instrument_id the
     daily snapshot job has not (yet) recorded for that day (e.g.
     exporting "today" before the snapshot cron has run).

If neither source resolves a symbol for some instrument_id, this module
raises rather than fabricating or omitting the row -- a partition with a
missing or invented symbol would silently corrupt every content_hash
computed over it (no imputation).

Read-only guarantee
--------------------
A missing db_path raises FileNotFoundError before any sqlite3.connect
call -- the file is never created as a side effect of reading. A day
with zero matching option_quotes rows (in an existing DB) is a valid,
non-error result: an empty list.
"""
from __future__ import annotations

import pathlib
import sqlite3
from dataclasses import dataclass

from src.archive.content_hash import Row


@dataclass(frozen=True)
class PartitionKey:
    """Identifies one day-partition to export.

    utc_date is stored verbatim as a "YYYY-MM-DD" string (see M1.1 v1.0
    decision D-1: ts/date values are never reparsed or reformatted).
    """
    utc_date: str


# LEFT JOINs so a day/instrument_id absent from either mapping source
# still returns a row (with symbol=NULL), so the fail-closed check in
# read_partition can raise a precise error rather than the row silently
# disappearing from a plain INNER JOIN.
_PARTITION_QUERY = """
    SELECT
        q.instrument_id, q.ts, q.exch_ts, q.spot_price, q.mark_price, q.close,
        q.best_bid, q.best_ask, q.bid_qty, q.ask_qty,
        q.mark_iv, q.bid_iv, q.ask_iv, q.delta, q.gamma, q.theta, q.vega, q.rho,
        q.oi, q.oi_contracts, q.oi_value_usd, q.volume_24h,
        COALESCE(m.symbol, i.symbol) AS symbol
    FROM option_quotes q
    LEFT JOIN instrument_map_log m
        ON m.id = q.instrument_id AND m.seen_date = ?
    LEFT JOIN instruments i
        ON i.id = q.instrument_id
    WHERE q.ts LIKE ? || '%'
"""


def read_partition(db_path: str, key: PartitionKey) -> list[Row]:
    """Read one UTC-day partition of option_quotes, read-only.

    Raises:
        FileNotFoundError: db_path does not exist. Never creates it.
        ValueError: an instrument_id in the partition has no resolvable
            symbol in either instrument_map_log (for key.utc_date) or the
            live instruments table.
    """
    path = pathlib.Path(db_path)
    if not path.exists():
        raise FileNotFoundError(f"hot DB not found: {db_path}")

    uri = path.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=2.0)
    try:
        cur = conn.cursor()
        cur.execute(_PARTITION_QUERY, (key.utc_date, key.utc_date))
        columns = [d[0] for d in cur.description]
        rows: list[Row] = []
        for record in cur.fetchall():
            row: Row = dict(zip(columns, record))
            if row["symbol"] is None:
                raise ValueError(
                    f"instrument_id={row['instrument_id']} has no resolvable "
                    f"symbol for partition {key.utc_date} (absent from "
                    f"instrument_map_log for that seen_date and from the "
                    f"live instruments table) -- refusing to export a "
                    f"partition with a fabricated or missing symbol"
                )
            rows.append(row)
        return rows
    finally:
        conn.close()
