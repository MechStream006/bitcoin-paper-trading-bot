"""BTC Paper Trading Bot — daily instrument id->symbol map snapshot.

Appends one row per (seen_date, id) capturing today's id->symbol mapping from
the instruments table into instrument_map_log. Enables detection of Delta
product_id reuse, which would silently conflate two contracts under one join
key in option_quotes.

This is a pure observer: it reads the instruments table read-only (WAL allows
concurrent reads while the collector runs) and is the sole writer of
instrument_map_log. It never touches the live collection path. No downtime.

Reuse-detection query (run by the future certifier):

    SELECT id, COUNT(DISTINCT symbol) AS n
    FROM instrument_map_log GROUP BY id HAVING n > 1;   -- any row = reuse

Run from the repo root:

    python scripts/snapshot_instrument_map.py [--db-path PATH] [--dry-run]

Exit codes:
    0  success
    1  failure (DB missing, instruments empty, or write error)
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def snapshot(conn: sqlite3.Connection, *, dry_run: bool = False) -> int:
    """Append today's id->symbol map. Returns rows inserted (0 if already done)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = conn.execute("SELECT id, symbol FROM instruments").fetchall()
    if not rows:
        raise RuntimeError("instruments table is empty — nothing to snapshot")

    print(f"seen_date={today}  instruments={len(rows)}")
    if dry_run:
        print("DRY RUN — no rows written")
        return 0

    before = conn.execute(
        "SELECT COUNT(*) FROM instrument_map_log WHERE seen_date = ?", (today,)
    ).fetchone()[0]

    # INSERT OR IGNORE keeps the daily snapshot idempotent (PK seen_date,id).
    conn.executemany(
        "INSERT OR IGNORE INTO instrument_map_log (seen_date, id, symbol) VALUES (?, ?, ?)",
        [(today, r[0], r[1]) for r in rows],
    )
    conn.commit()

    after = conn.execute(
        "SELECT COUNT(*) FROM instrument_map_log WHERE seen_date = ?", (today,)
    ).fetchone()[0]
    inserted = after - before
    print(f"rows inserted={inserted}  (already present today={before})")

    # Inline reuse check — cheap, and surfaces the signal in the journal daily.
    reuse = conn.execute(
        "SELECT id, COUNT(DISTINCT symbol) AS n FROM instrument_map_log "
        "GROUP BY id HAVING n > 1"
    ).fetchall()
    if reuse:
        print(f"WARNING: product_id REUSE DETECTED for {len(reuse)} id(s): "
              f"{[r[0] for r in reuse][:10]}")
    else:
        print("id stability OK: no product_id maps to multiple symbols")
    return inserted


def main() -> int:
    ap = argparse.ArgumentParser(description="Daily instrument id->symbol snapshot")
    ap.add_argument("--db-path", type=Path, default=ROOT / "data" / "btc_trading.db")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.db_path.exists():
        print(f"ERROR: DB not found at {args.db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(args.db_path))
    try:
        snapshot(conn, dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: snapshot failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
