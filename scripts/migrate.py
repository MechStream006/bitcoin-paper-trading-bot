"""BTC Paper Trading Bot — idempotent schema migration runner.

Applies additive, backward-compatible schema changes to the live trading DB.
Safe to run repeatedly: each step is guarded so re-running is a no-op.

Schema versions
---------------
  0 -> 1  Tier-0 observability:
            - option_quotes.exch_ts INTEGER  (exchange tick time, microseconds)
            - instrument_map_log table        (daily id->symbol snapshots)
  1 -> 2  Collection-quality observability:
            - collection_metrics table        (per-cycle coverage/skip/fail)
  2 -> 3  Freshness observability:
            - collection_metrics.stale_count INTEGER  (stalls hidden by coverage)

Run from the repo root (bot stopped is recommended but not required — the
ALTER is a metadata-only operation under WAL):

    python scripts/migrate.py [--db-path PATH] [--dry-run]

Exit codes:
    0  success (migrated or already up to date)
    1  failure
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TARGET_VERSION = 3


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def migrate(conn: sqlite3.Connection, *, dry_run: bool = False) -> int:
    """Apply pending migrations. Returns the resulting user_version."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    print(f"current user_version = {version}; target = {TARGET_VERSION}")

    # --- 0 -> 1 -----------------------------------------------------------
    # Guard on actual schema state (not just user_version) so a partially
    # applied migration self-heals.
    cols = _columns(conn, "option_quotes")
    need_exch_ts = "exch_ts" not in cols
    need_map_log = not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='instrument_map_log'"
    ).fetchone()

    if need_exch_ts:
        print("  + ALTER option_quotes ADD COLUMN exch_ts INTEGER")
        if not dry_run:
            conn.execute("ALTER TABLE option_quotes ADD COLUMN exch_ts INTEGER")
    else:
        print("  = option_quotes.exch_ts already present")

    if need_map_log:
        print("  + CREATE TABLE instrument_map_log")
        if not dry_run:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS instrument_map_log (
                    seen_date TEXT    NOT NULL,
                    id        INTEGER NOT NULL,
                    symbol    TEXT    NOT NULL,
                    PRIMARY KEY (seen_date, id)
                )
                """
            )
    else:
        print("  = instrument_map_log already present")

    if version < 1:
        print("  + PRAGMA user_version = 1")
        if not dry_run:
            conn.execute("PRAGMA user_version = 1")

    # --- 1 -> 2 -----------------------------------------------------------
    need_metrics = not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='collection_metrics'"
    ).fetchone()

    if need_metrics:
        print("  + CREATE TABLE collection_metrics")
        if not dry_run:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS collection_metrics (
                    ts              TEXT    NOT NULL PRIMARY KEY,
                    universe_size   INTEGER,
                    coverage_pct    REAL,
                    wrote           INTEGER,
                    skipped         INTEGER,
                    failed          INTEGER,
                    reconcile_count INTEGER
                )
                """
            )
    else:
        print("  = collection_metrics already present")

    if version < 2:
        print("  + PRAGMA user_version = 2")
        if not dry_run:
            conn.execute("PRAGMA user_version = 2")

    # --- 2 -> 3 -----------------------------------------------------------
    metrics_cols = _columns(conn, "collection_metrics")
    # Guard: only meaningful if collection_metrics exists (it does after 1->2).
    need_stale = bool(metrics_cols) and "stale_count" not in metrics_cols

    if need_stale:
        print("  + ALTER collection_metrics ADD COLUMN stale_count INTEGER")
        if not dry_run:
            conn.execute(
                "ALTER TABLE collection_metrics ADD COLUMN stale_count INTEGER"
            )
    else:
        print("  = collection_metrics.stale_count already present")

    if version < 3:
        print("  + PRAGMA user_version = 3")
        if not dry_run:
            conn.execute("PRAGMA user_version = 3")

    if not dry_run:
        conn.commit()

    final = conn.execute("PRAGMA user_version").fetchone()[0]
    print(f"done; user_version = {final}{' (DRY RUN — no changes written)' if dry_run else ''}")
    return final


def main() -> int:
    ap = argparse.ArgumentParser(description="BTC bot schema migration")
    ap.add_argument("--db-path", type=Path, default=ROOT / "data" / "btc_trading.db")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print planned changes without writing.")
    args = ap.parse_args()

    if not args.db_path.exists():
        print(f"ERROR: DB not found at {args.db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(args.db_path))
    try:
        migrate(conn, dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: migration failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
