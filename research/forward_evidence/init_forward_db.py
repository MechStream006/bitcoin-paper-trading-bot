"""
init_forward_db.py
==================
Initialise evidence_forward.db from schema_forward.sql.

Idempotent: safe to run on an existing database (CREATE TABLE IF NOT EXISTS).
Sets WAL journal mode (persisted in DB header after first call).
Does NOT set PRAGMA foreign_keys — that is per-connection and must be set
by every collection runner immediately after sqlite3.connect().

Usage (CLI):
    conda run -n btc_bot python forward_evidence/init_forward_db.py
    conda run -n btc_bot python forward_evidence/init_forward_db.py --db-path /tmp/test.db

Usage (code):
    from forward_evidence.init_forward_db import init_forward_db
    init_forward_db(pathlib.Path("research/data/evidence_forward.db"))
"""
from __future__ import annotations

import argparse
import pathlib
import sqlite3

_HERE    = pathlib.Path(__file__).resolve().parent
_SCHEMA  = _HERE / "schema_forward.sql"

_DEFAULT_DB = _HERE.parent / "data" / "evidence_forward.db"

_EXPECTED_TABLES = frozenset({"run_log", "signal_log", "trade_entries", "trade_exits"})
_EXPECTED_VIEWS  = frozenset({"trade_returns"})


def init_forward_db(db_path: pathlib.Path) -> None:
    """
    Create or verify evidence_forward.db.

    Sets WAL journal mode (persisted) then executes schema_forward.sql.
    Raises RuntimeError if any expected table or view is missing after init.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        # WAL mode is persisted in the DB header after first set.
        conn.execute("PRAGMA journal_mode = WAL")
        # Run schema DDL (CREATE TABLE IF NOT EXISTS — idempotent).
        # executescript commits any open transaction before running.
        conn.executescript(_SCHEMA.read_text(encoding="utf-8"))

        # Verify all expected objects exist.
        actual_tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        actual_views = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='view'"
            )
        }
        missing_tables = _EXPECTED_TABLES - actual_tables
        missing_views  = _EXPECTED_VIEWS  - actual_views
        if missing_tables or missing_views:
            raise RuntimeError(
                f"Schema verification failed after init.\n"
                f"  Missing tables: {sorted(missing_tables)}\n"
                f"  Missing views:  {sorted(missing_views)}"
            )

        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Initialise evidence_forward.db")
    parser.add_argument(
        "--db-path",
        type=pathlib.Path,
        default=_DEFAULT_DB,
        help=f"Path to SQLite database (default: {_DEFAULT_DB})",
    )
    args = parser.parse_args()
    print(f"Initialising: {args.db_path}")
    init_forward_db(args.db_path)
    print("OK — evidence_forward.db ready.")
