"""Migration v4 tests — daily_certifier_log.

Verifies:
  - migrate() advances from user_version 3 to 4 and creates the table
  - migrate() is idempotent on an already-v4 DB
  - migrate() self-heals if daily_certifier_log was created outside user_version
    (guard-based self-healing property)
  - dry_run leaves the DB unchanged
  - Full chain v0 → v4 works on a schema created by init_schema
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from src.data.storage import init_schema

# Load migrate module from scripts/ (not a package; use importlib)
_spec   = importlib.util.spec_from_file_location("migrate", ROOT / "scripts" / "migrate.py")
_mod    = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
migrate          = _mod.migrate
TARGET_VERSION   = _mod.TARGET_VERSION


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _v3_db() -> sqlite3.Connection:
    """In-memory DB at user_version=3 (pre-v4); daily_certifier_log absent."""
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    # Advance to exactly v3 by running migration but then the schema already
    # has all tables (init_schema is current).  We just need user_version=3.
    conn.execute("PRAGMA user_version = 3")
    conn.commit()
    # Confirm daily_certifier_log does NOT exist (so migration has something to do)
    conn.execute("DROP TABLE IF EXISTS daily_certifier_log")
    conn.commit()
    return conn


def _v4_db() -> sqlite3.Connection:
    """In-memory DB already at user_version=4."""
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    migrate(conn)
    return conn


def _fresh_db() -> sqlite3.Connection:
    """In-memory DB at user_version=0 (bare init_schema, no migrations run)."""
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    # init_schema does not set user_version; it stays 0.
    return conn


def _user_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _column_names(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


# ---------------------------------------------------------------------------
# TestMigrationV3toV4
# ---------------------------------------------------------------------------

class TestMigrationV3toV4:
    def test_advances_user_version(self):
        conn = _v3_db()
        migrate(conn)
        # migrate() always runs the chain to its head. This file's subject is
        # the v3->v4 STEP (daily_certifier_log, asserted below), not the head
        # version, so track TARGET_VERSION rather than pinning a literal that
        # every future migration would have to come back and edit.
        assert _user_version(conn) == TARGET_VERSION

    def test_creates_daily_certifier_log(self):
        conn = _v3_db()
        assert not _table_exists(conn, "daily_certifier_log")
        migrate(conn)
        assert _table_exists(conn, "daily_certifier_log")

    def test_daily_certifier_log_columns(self):
        conn = _v3_db()
        migrate(conn)
        cols = _column_names(conn, "daily_certifier_log")
        expected = {"day", "run_ts", "verdict", "platform", "certifier_version", "checks_json"}
        assert expected.issubset(set(cols))

    def test_v4_step_is_still_in_the_chain(self):
        # Was `TARGET_VERSION == 4` until ENG-017 added v5. The invariant this
        # file actually cares about is that the v4 step has not been dropped or
        # reordered out of the chain -- pinning the head version here would just
        # make every future migration fail an unrelated test.
        # The head itself is pinned in tests/test_migrate_v5.py.
        assert TARGET_VERSION >= 4


# ---------------------------------------------------------------------------
# TestMigrationIdempotent
# ---------------------------------------------------------------------------

class TestMigrationIdempotent:
    def test_version_unchanged_on_second_run(self):
        conn = _v4_db()
        migrate(conn)
        assert _user_version(conn) == TARGET_VERSION

    def test_no_duplicate_table(self):
        conn = _v4_db()
        migrate(conn)
        count = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='daily_certifier_log'"
        ).fetchone()[0]
        assert count == 1

    def test_existing_rows_preserved(self):
        conn = _v4_db()
        conn.execute(
            "INSERT INTO daily_certifier_log "
            "(day, run_ts, verdict, platform, certifier_version, checks_json) "
            "VALUES ('2026-06-12', '2026-06-12T00:00:00Z', 'GREEN', 'testnet', 'abc', '{}')"
        )
        conn.commit()
        migrate(conn)
        row = conn.execute(
            "SELECT verdict FROM daily_certifier_log WHERE day = '2026-06-12'"
        ).fetchone()
        assert row is not None and row[0] == "GREEN"


# ---------------------------------------------------------------------------
# TestMigrationSelfHeal
# ---------------------------------------------------------------------------

class TestMigrationSelfHeal:
    def test_table_exists_but_version_stuck_at_3(self):
        """Guard-based self-heal: table already created, user_version still 3."""
        conn = sqlite3.connect(":memory:")
        init_schema(conn)
        conn.execute("PRAGMA user_version = 3")
        # daily_certifier_log already exists (e.g. partial prior run)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_certifier_log (
                day TEXT NOT NULL PRIMARY KEY, run_ts TEXT NOT NULL,
                verdict TEXT NOT NULL, platform TEXT,
                certifier_version TEXT, checks_json TEXT NOT NULL
            )
            """
        )
        conn.commit()

        migrate(conn)

        assert _user_version(conn) == TARGET_VERSION
        assert _table_exists(conn, "daily_certifier_log")


# ---------------------------------------------------------------------------
# TestDryRun
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_dry_run_does_not_create_table(self):
        conn = _v3_db()
        migrate(conn, dry_run=True)
        assert not _table_exists(conn, "daily_certifier_log")

    def test_dry_run_does_not_advance_version(self):
        conn = _v3_db()
        migrate(conn, dry_run=True)
        assert _user_version(conn) == 3

    def test_dry_run_idempotent_on_v4(self):
        conn = _v4_db()
        migrate(conn, dry_run=True)
        assert _user_version(conn) == TARGET_VERSION


# ---------------------------------------------------------------------------
# TestFullChain
# ---------------------------------------------------------------------------

class TestFullChain:
    def test_v0_to_v4(self):
        """Full migration chain from a fresh init_schema DB."""
        conn = _fresh_db()
        assert _user_version(conn) == 0
        migrate(conn)
        assert _user_version(conn) == TARGET_VERSION
        assert _table_exists(conn, "instrument_map_log")
        assert _table_exists(conn, "collection_metrics")
        assert _column_names(conn, "option_quotes").__contains__("exch_ts")
        assert "stale_count" in _column_names(conn, "collection_metrics")
        assert _table_exists(conn, "daily_certifier_log")

    def test_v0_to_v4_idempotent(self):
        """Running migration twice from v0 reaches v4 both times without error."""
        conn = _fresh_db()
        migrate(conn)
        migrate(conn)
        assert _user_version(conn) == TARGET_VERSION
