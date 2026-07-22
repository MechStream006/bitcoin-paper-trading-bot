"""Migration v5 tests — ENG-017 `idx_option_quotes_ts`.

Verifies:
  - migrate() advances v4 -> v5 and creates the index
  - the index is actually USED by the query it exists for (healthcheck.py's
    `SELECT max(ts)` dead-man probe) — a created-but-unused index is a pure
    write-cost regression, so presence alone is not the assertion
  - init_schema() creates it too, so a FRESH database is never slower than a
    migrated one
  - idempotency, self-heal, and dry-run, matching the v4 file's contract
  - existing option_quotes rows survive the index build
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from src.data.storage import init_schema

_spec = importlib.util.spec_from_file_location("migrate", ROOT / "scripts" / "migrate.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
migrate = _mod.migrate
TARGET_VERSION = _mod.TARGET_VERSION

INDEX = "idx_option_quotes_ts"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    return conn


def _v4_db() -> sqlite3.Connection:
    """A DB at v4: fully migrated, then the v5 index removed and version reset."""
    conn = _fresh_db()
    migrate(conn)
    conn.execute(f"DROP INDEX IF EXISTS {INDEX}")
    conn.execute("PRAGMA user_version = 4")
    conn.commit()
    return conn


def _user_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def _index_exists(conn: sqlite3.Connection, name: str = INDEX) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (name,)
    ).fetchone() is not None


def _plan(conn: sqlite3.Connection, sql: str) -> str:
    return " | ".join(r[-1] for r in conn.execute("EXPLAIN QUERY PLAN " + sql))


def _seed(conn: sqlite3.Connection, n: int = 200) -> None:
    # No `instruments` parent row: these are raw `sqlite3.connect(":memory:")`
    # handles, so `PRAGMA foreign_keys` is OFF (storage.get_connection() turns it
    # on, init_schema() does not) and the FK is not enforced. Seeding only what
    # the query plans need keeps the fixture honest about its own scope.
    conn.executemany(
        "INSERT OR REPLACE INTO option_quotes(instrument_id, ts, mark_price) VALUES (?,?,?)",
        [(1, f"2026-01-01T{i // 60:02d}:{i % 60:02d}:00+00:00", 1.0 + i) for i in range(n)],
    )
    conn.commit()


# ---------------------------------------------------------------------------
# TestMigrationV4toV5
# ---------------------------------------------------------------------------

class TestMigrationV4toV5:
    def test_target_version_is_5(self):
        """Pins the CURRENT head of the migration chain (moved here from
        tests/test_migrate_v4.py, which now only asserts the v4 step survives)."""
        assert TARGET_VERSION == 5

    def test_advances_user_version(self):
        conn = _v4_db()
        assert _user_version(conn) == 4
        migrate(conn)
        assert _user_version(conn) == 5

    def test_creates_index(self):
        conn = _v4_db()
        assert not _index_exists(conn)
        migrate(conn)
        assert _index_exists(conn)

    def test_index_is_on_ts_column(self):
        conn = _v4_db()
        migrate(conn)
        cols = [r[2] for r in conn.execute(f"PRAGMA index_info('{INDEX}')")]
        assert cols == ["ts"]


# ---------------------------------------------------------------------------
# The index must actually be USED — presence alone is a write-cost regression
# ---------------------------------------------------------------------------

class TestIndexIsUsed:
    def test_liveness_probe_uses_index(self):
        """healthcheck.py:190 `SELECT max(ts) FROM option_quotes` — the query
        this index exists for. Without it the plan is a full scan of the
        (instrument_id, ts) PK, O(rows)."""
        conn = _v4_db()
        _seed(conn)
        before = _plan(conn, "SELECT max(ts) FROM option_quotes")
        migrate(conn)
        after = _plan(conn, "SELECT max(ts) FROM option_quotes")
        assert INDEX not in before
        assert INDEX in after

    def test_sargable_range_uses_index(self):
        """The `ts >= ? AND ts < ?` shape used by research/dq/probes.py."""
        conn = _fresh_db()
        _seed(conn)
        plan = _plan(
            conn,
            "SELECT COUNT(*) FROM option_quotes WHERE ts >= '2026-01-01' AND ts < '2026-01-02'",
        )
        assert INDEX in plan

    def test_date_function_wrapper_does_not_use_index(self):
        """Documents a real, measured limitation rather than implying a fix.

        `WHERE DATE(ts) = ?` (scripts/certify_day.py) wraps the column in a
        function, so no index on `ts` can restrict the range for it.

        Note the plan does NAME the index — SQLite scans it as a narrower
        covering structure than the table. That is not the same as using it:
        the operation is SCAN (every entry) rather than SEARCH (a seek to a
        range), so the cost stays O(rows). Asserting SEARCH-vs-SCAN is
        therefore the real invariant; asserting the index is merely absent from
        the plan would fail for the wrong reason.

        This test exists so that if someone later 'fixes' the certifier by
        adding another index, they discover here that the fix has to be a
        sargable rewrite (`ts >= day AND ts < next_day`) instead.
        See docs/ops/eng-017-sqlite-performance.md.
        """
        conn = _fresh_db()
        _seed(conn)
        plan = _plan(conn, "SELECT COUNT(*) FROM option_quotes WHERE DATE(ts) = '2026-01-01'")
        assert "SCAN" in plan, plan
        assert "SEARCH" not in plan, plan

        sargable = _plan(
            conn,
            "SELECT COUNT(*) FROM option_quotes WHERE ts >= '2026-01-01' AND ts < '2026-01-02'",
        )
        assert "SEARCH" in sargable, sargable


# ---------------------------------------------------------------------------
# Fresh DBs must not be slower than migrated ones
# ---------------------------------------------------------------------------

class TestFreshSchemaParity:
    def test_init_schema_creates_index(self):
        assert _index_exists(_fresh_db())

    def test_fresh_and_migrated_agree(self):
        fresh = _fresh_db()
        migrated = _v4_db()
        migrate(migrated)
        assert _index_exists(fresh) == _index_exists(migrated) is True


# ---------------------------------------------------------------------------
# Idempotency / self-heal / dry-run
# ---------------------------------------------------------------------------

class TestIdempotent:
    def test_second_run_is_noop(self):
        conn = _v4_db()
        migrate(conn)
        migrate(conn)
        assert _user_version(conn) == 5
        assert _index_exists(conn)

    def test_no_duplicate_index(self):
        conn = _v4_db()
        migrate(conn)
        migrate(conn)
        n = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name=?", (INDEX,)
        ).fetchone()[0]
        assert n == 1

    def test_rows_preserved_across_index_build(self):
        conn = _v4_db()
        _seed(conn, 200)
        before = conn.execute("SELECT COUNT(*), MAX(ts) FROM option_quotes").fetchone()
        migrate(conn)
        assert conn.execute("SELECT COUNT(*), MAX(ts) FROM option_quotes").fetchone() == before


class TestSelfHeal:
    def test_index_exists_but_version_stuck_at_4(self):
        """Guard is on sqlite_master, not user_version: an index created out of
        band (e.g. from a fresh init_schema) must not cause a duplicate-create
        error, and the version must still advance."""
        conn = _v4_db()
        conn.execute(f"CREATE INDEX {INDEX} ON option_quotes(ts)")
        conn.commit()
        migrate(conn)
        assert _user_version(conn) == 5
        assert _index_exists(conn)


class TestDryRun:
    def test_dry_run_does_not_create_index(self):
        conn = _v4_db()
        migrate(conn, dry_run=True)
        assert not _index_exists(conn)

    def test_dry_run_does_not_advance_version(self):
        conn = _v4_db()
        migrate(conn, dry_run=True)
        assert _user_version(conn) == 4
