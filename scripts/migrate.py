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
  3 -> 4  Daily certifier infrastructure:
            - daily_certifier_log table       (per-day evidence admissibility verdicts)
  4 -> 5  ENG-017 time-series index:
            - idx_option_quotes_ts on option_quotes(ts)

Note on 4 -> 5: CREATE INDEX is the one migration step here whose cost scales
with table size (it sorts every existing row). Measured ~4.3 s at 2.85M rows;
extrapolate ~55 s at the 36M rows that correspond to a 10 GB database. It takes
a write lock for that duration, so prefer running it with the bot stopped on a
large database. Every earlier step is metadata-only and effectively instant.

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

TARGET_VERSION = 5


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

    # --- 3 -> 4 -----------------------------------------------------------
    need_certifier_log = not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='daily_certifier_log'"
    ).fetchone()

    if need_certifier_log:
        print("  + CREATE TABLE daily_certifier_log")
        if not dry_run:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_certifier_log (
                    day               TEXT NOT NULL PRIMARY KEY,
                    run_ts            TEXT NOT NULL,
                    verdict           TEXT NOT NULL,
                    platform          TEXT,
                    certifier_version TEXT,
                    checks_json       TEXT NOT NULL
                )
                """
            )
    else:
        print("  = daily_certifier_log already present")

    if version < 4:
        print("  + PRAGMA user_version = 4")
        if not dry_run:
            conn.execute("PRAGMA user_version = 4")

    # --- 4 -> 5 (ENG-017) -------------------------------------------------
    # option_quotes' PK is (instrument_id, ts): `ts` is non-leading, so every
    # time-ranged query is a full scan, O(rows). Chief victim is healthcheck.py's
    # `SELECT max(ts)` dead-man probe (every 10 min, plus every API health call),
    # measured at 512 ms on 2.85M rows and growing linearly.
    #
    # Guarded on sqlite_master rather than user_version so a database that
    # already has the index (created fresh from storage.py's SCHEMA) is a no-op.
    need_ts_index = not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_option_quotes_ts'"
    ).fetchone()

    if need_ts_index:
        print("  + CREATE INDEX idx_option_quotes_ts ON option_quotes(ts)")
        print("    (sorts every existing row; ~4s per 3M rows, holds a write lock)")
        if not dry_run:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_option_quotes_ts ON option_quotes(ts)"
            )
    else:
        print("  = idx_option_quotes_ts already present")

    if version < 5:
        print("  + PRAGMA user_version = 5")
        if not dry_run:
            conn.execute("PRAGMA user_version = 5")

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
