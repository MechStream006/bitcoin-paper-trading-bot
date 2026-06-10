"""
M1 — Forward Evidence Database Initializer.

Creates the SQLite database from schema.sql and verifies all tables.
Safe to re-run: existing tables and data are left untouched.

Run from research/:
    python forward_evidence/init_db.py
"""
from __future__ import annotations

import pathlib
import sqlite3
import sys
from datetime import datetime, timezone

_HERE     = pathlib.Path(__file__).resolve().parent
_RESEARCH = _HERE.parent

DB_PATH   = _RESEARCH / "data" / "forward_evidence.db"
SCHEMA    = _HERE / "schema.sql"
REG_PATH  = _HERE / "pre_registration.json"

EXPECTED_TABLES = [
    "corpus_daily",
    "regime_states",
    "signal_log",
    "trade_entries",
    "trade_exits",
    "trade_labels",
    "mae_mfe_path",
    "attribution",
    "run_log",
]


def init_db(db_path: pathlib.Path = DB_PATH) -> sqlite3.Connection:
    """
    Create (or open) the forward evidence DB and apply schema.sql.
    Returns an open connection.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row

    sql = SCHEMA.read_text(encoding="utf-8")
    con.executescript(sql)
    con.commit()
    return con


def verify_db(con: sqlite3.Connection) -> dict[str, bool]:
    """Returns {table_name: exists}."""
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    found = {r["name"] for r in rows}
    return {t: t in found for t in EXPECTED_TABLES}


def main() -> None:
    print("=" * 60)
    print(f"  M1 DB INIT  —  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)

    if not SCHEMA.exists():
        print(f"  [ERROR] schema.sql not found: {SCHEMA}")
        sys.exit(1)

    if not REG_PATH.exists():
        print("  [WARN] pre_registration.json not found — run m0_pre_register.py first.")

    existing = DB_PATH.exists()
    con = init_db()

    status = verify_db(con)
    all_ok = all(status.values())

    for table, ok in status.items():
        tag = "[OK]" if ok else "[MISSING]"
        print(f"  {tag}  {table}")

    print()
    if not existing:
        print(f"  Created: {DB_PATH}")
    else:
        print(f"  Verified: {DB_PATH}")

    if all_ok:
        print("  All 9 tables present. M1 complete.")
    else:
        missing = [t for t, ok in status.items() if not ok]
        print(f"  [ERROR] Missing tables: {missing}")
        sys.exit(1)

    con.close()
    print("=" * 60)


if __name__ == "__main__":
    main()
