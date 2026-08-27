"""
Tests for forward_evidence schema_forward.sql and init_forward_db.py

Run from research/:
    conda run -n btc_bot pytest forward_evidence/tests/test_schema_forward.py -v

Covers all 22+ invariants from the schema review plan plus the three
design-review changes (D3 normalisation, created_at, UNIQUE crash/recovery).
"""
from __future__ import annotations

import pathlib
import sqlite3

import pytest

from forward_evidence.init_forward_db import init_forward_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_path(tmp_path: pathlib.Path) -> pathlib.Path:
    """Fresh initialised database for each test."""
    p = tmp_path / "evidence_forward.db"
    init_forward_db(p)
    return p


@pytest.fixture()
def conn(db_path: pathlib.Path) -> sqlite3.Connection:
    """Connection with PRAGMA foreign_keys=ON and autoclose."""
    c = sqlite3.connect(db_path)
    c.execute("PRAGMA foreign_keys = ON")
    yield c
    c.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _insert_run(
    conn: sqlite3.Connection,
    signal_id: str = "h5_vol_regime",
    run_date: str  = "2026-06-14",
    fn_hash: str   = "a" * 64,
    status: str    = "ok",
    note: str | None = None,
) -> int:
    """Insert a run_log row and return its id."""
    conn.execute(
        "INSERT INTO run_log (signal_id, run_date, fn_hash, status, note) "
        "VALUES (?,?,?,?,?)",
        (signal_id, run_date, fn_hash, status, note),
    )
    conn.commit()
    return conn.execute(
        "SELECT id FROM run_log WHERE signal_id=? AND run_date=?",
        (signal_id, run_date),
    ).fetchone()[0]


def _insert_entry(
    conn: sqlite3.Connection,
    signal_id: str    = "h5_vol_regime",
    entry_date: str   = "2026-06-14",
    direction: int    = 1,
    entry_close: float | None = 40000.0,
    planned_exit: str = "2026-06-21",
) -> int:
    """Insert a trade_entries row and return its id."""
    conn.execute(
        "INSERT INTO trade_entries "
        "(signal_id, entry_date, direction, entry_close_price, planned_exit_date) "
        "VALUES (?,?,?,?,?)",
        (signal_id, entry_date, direction, entry_close, planned_exit),
    )
    conn.commit()
    return conn.execute(
        "SELECT MAX(id) FROM trade_entries WHERE signal_id=? AND entry_date=?",
        (signal_id, entry_date),
    ).fetchone()[0]


def _insert_exit(
    conn: sqlite3.Connection,
    entry_id: int,
    exit_date: str    = "2026-06-21",
    exit_close: float = 42000.0,
    cost_bps: float   = 15.0,
) -> None:
    """Insert a trade_exits row and mark the parent entry as closed."""
    conn.execute(
        "INSERT INTO trade_exits (entry_id, exit_date, exit_close_price, cost_bps) "
        "VALUES (?,?,?,?)",
        (entry_id, exit_date, exit_close, cost_bps),
    )
    conn.execute("UPDATE trade_entries SET open=0 WHERE id=?", (entry_id,))
    conn.commit()


# ---------------------------------------------------------------------------
# Group 1 — Schema creation and metadata
# ---------------------------------------------------------------------------

class TestSchemaCreation:

    def test_T01_all_expected_objects_created(self, db_path: pathlib.Path) -> None:
        c = sqlite3.connect(db_path)
        tables = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )}
        views = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='view'"
        )}
        c.close()
        # collection_provenance added at Gate 6B.3 (defect B-03): records HOW
        # each run_date was collected, so an authorised historical backfill can
        # never be mistaken for scheduled forward evidence. Additive — no
        # existing table or view was modified.
        assert tables == {"run_log", "signal_log", "trade_entries", "trade_exits",
                          "collection_provenance"}
        assert views  == {"trade_returns"}

    def test_T02_wal_mode_persisted_on_new_connection(self, db_path: pathlib.Path) -> None:
        """WAL mode must be visible on a fresh connection without re-issuing the pragma."""
        c = sqlite3.connect(db_path)
        mode = c.execute("PRAGMA journal_mode").fetchone()[0]
        c.close()
        assert mode == "wal"

    def test_T03_idempotent_init(self, db_path: pathlib.Path) -> None:
        """Running init_forward_db multiple times on an existing DB must not raise."""
        init_forward_db(db_path)
        init_forward_db(db_path)


# ---------------------------------------------------------------------------
# Group 2 — run_log constraints
# ---------------------------------------------------------------------------

class TestRunLog:

    def test_T04_unique_signal_run_date(self, conn: sqlite3.Connection) -> None:
        _insert_run(conn, run_date="2026-06-14")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO run_log (signal_id, run_date, fn_hash) VALUES (?,?,?)",
                ("h5_vol_regime", "2026-06-14", "b" * 64),
            )
            conn.commit()

    def test_T05_status_check_rejects_invalid(self, conn: sqlite3.Connection) -> None:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO run_log (signal_id, run_date, fn_hash, status) "
                "VALUES (?,?,?,?)",
                ("h5_vol_regime", "2026-06-14", "a" * 64, "tampered"),
            )
            conn.commit()

    def test_T06_status_check_accepts_all_valid_values(self, conn: sqlite3.Connection) -> None:
        valid_statuses = ("ok", "skipped", "error", "fn_hash_violation")
        for i, status in enumerate(valid_statuses):
            conn.execute(
                "INSERT INTO run_log (signal_id, run_date, fn_hash, status) "
                "VALUES (?,?,?,?)",
                ("h5_vol_regime", f"2026-06-{14 + i:02d}", "a" * 64, status),
            )
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM run_log").fetchone()[0]
        assert count == len(valid_statuses)

    def test_T07_fn_hash_not_null(self, conn: sqlite3.Connection) -> None:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO run_log (signal_id, run_date, fn_hash) VALUES (?,?,?)",
                ("h5_vol_regime", "2026-06-14", None),
            )
            conn.commit()

    def test_T23_created_at_auto_populated(self, conn: sqlite3.Connection) -> None:
        _insert_run(conn)
        row = conn.execute("SELECT created_at FROM run_log").fetchone()
        assert row[0] is not None
        assert "T" in row[0]       # ISO-8601 separator
        assert row[0].endswith("Z")

    def test_T24_created_at_populated_on_all_rows(self, conn: sqlite3.Connection) -> None:
        _insert_run(conn, run_date="2026-06-14")
        _insert_run(conn, run_date="2026-06-15")
        rows = conn.execute("SELECT created_at FROM run_log ORDER BY id").fetchall()
        assert all(r[0] is not None for r in rows)
        assert len(rows) == 2


# ---------------------------------------------------------------------------
# Group 3 — signal_log constraints
# ---------------------------------------------------------------------------

class TestSignalLog:

    def test_T08_unique_signal_day(self, conn: sqlite3.Connection) -> None:
        run_id = _insert_run(conn)
        conn.execute(
            "INSERT INTO signal_log (signal_id, day, position, run_id) VALUES (?,?,?,?)",
            ("h5_vol_regime", "2026-06-14", 1.0, run_id),
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO signal_log (signal_id, day, position, run_id) VALUES (?,?,?,?)",
                ("h5_vol_regime", "2026-06-14", 0.0, run_id),
            )
            conn.commit()

    def test_T09_fk_run_id_enforced_with_pragma_on(self, conn: sqlite3.Connection) -> None:
        """FK violation must raise when foreign_keys=ON."""
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO signal_log (signal_id, day, position, run_id) VALUES (?,?,?,?)",
                ("h5_vol_regime", "2026-06-14", 1.0, 99999),
            )
            conn.commit()

    def test_T09b_fk_not_enforced_without_pragma(self, db_path: pathlib.Path) -> None:
        """Without PRAGMA foreign_keys=ON, the FK is silently ignored.
        Documents why every runner connection MUST set the pragma explicitly."""
        c = sqlite3.connect(db_path)
        # Deliberately omit: c.execute("PRAGMA foreign_keys = ON")
        c.execute(
            "INSERT INTO signal_log (signal_id, day, position, run_id) VALUES (?,?,?,?)",
            ("h5_vol_regime", "2026-06-14", 1.0, 99999),
        )
        c.commit()  # succeeds without the pragma
        c.close()

    def test_T10_raw_value_nullable(self, conn: sqlite3.Connection) -> None:
        run_id = _insert_run(conn)
        conn.execute(
            "INSERT INTO signal_log (signal_id, day, position, raw_value, run_id) "
            "VALUES (?,?,?,?,?)",
            ("g1_market_regime_sma200", "2026-06-14", -1.0, None, run_id),
        )
        conn.commit()
        row = conn.execute("SELECT raw_value FROM signal_log").fetchone()
        assert row[0] is None


# ---------------------------------------------------------------------------
# Group 4 — trade_entries constraints
# ---------------------------------------------------------------------------

class TestTradeEntries:

    def test_T11_direction_not_null_no_default(self, conn: sqlite3.Connection) -> None:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO trade_entries "
                "(signal_id, entry_date, entry_close_price, planned_exit_date) "
                "VALUES (?,?,?,?)",
                ("h5_vol_regime", "2026-06-14", 40000.0, "2026-06-21"),
            )
            conn.commit()

    def test_T12_direction_check_rejects_zero(self, conn: sqlite3.Connection) -> None:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO trade_entries "
                "(signal_id, entry_date, direction, entry_close_price, planned_exit_date) "
                "VALUES (?,?,?,?,?)",
                ("h5_vol_regime", "2026-06-14", 0, 40000.0, "2026-06-21"),
            )
            conn.commit()

    def test_T12b_direction_check_rejects_two(self, conn: sqlite3.Connection) -> None:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO trade_entries "
                "(signal_id, entry_date, direction, entry_close_price, planned_exit_date) "
                "VALUES (?,?,?,?,?)",
                ("h5_vol_regime", "2026-06-14", 2, 40000.0, "2026-06-21"),
            )
            conn.commit()

    def test_T12c_direction_check_accepts_minus1_and_1(self, conn: sqlite3.Connection) -> None:
        for direction, date in ((-1, "2026-06-14"), (1, "2026-06-15")):
            conn.execute(
                "INSERT INTO trade_entries "
                "(signal_id, entry_date, direction, planned_exit_date) VALUES (?,?,?,?)",
                ("g3_momentum_tsmom", date, direction, "2026-06-21"),
            )
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM trade_entries").fetchone()[0]
        assert count == 2

    def test_T13_open_check_rejects_two(self, conn: sqlite3.Connection) -> None:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO trade_entries "
                "(signal_id, entry_date, direction, planned_exit_date, open) "
                "VALUES (?,?,?,?,?)",
                ("h5_vol_regime", "2026-06-14", 1, "2026-06-21", 2),
            )
            conn.commit()

    def test_T14_same_day_exit_and_reentry_no_constraint_fires(
        self, conn: sqlite3.Connection
    ) -> None:
        """G1 same-day direction flip: two rows with the same entry_date, opposite
        directions. Neither INSERT must raise — verifies absence of
        UNIQUE(signal_id, entry_date)."""
        for direction in (1, -1):
            conn.execute(
                "INSERT INTO trade_entries "
                "(signal_id, entry_date, direction, planned_exit_date) VALUES (?,?,?,?)",
                ("g1_market_regime_sma200", "2026-06-21", direction, "2026-06-28"),
            )
        conn.commit()
        count = conn.execute(
            "SELECT COUNT(*) FROM trade_entries WHERE entry_date='2026-06-21'"
        ).fetchone()[0]
        assert count == 2

    def test_T14b_entry_close_price_nullable(self, conn: sqlite3.Connection) -> None:
        """entry_close_price must accept NULL for recovery/backfill scenarios."""
        conn.execute(
            "INSERT INTO trade_entries "
            "(signal_id, entry_date, direction, entry_close_price, planned_exit_date) "
            "VALUES (?,?,?,?,?)",
            ("h5_vol_regime", "2026-06-14", 1, None, "2026-06-21"),
        )
        conn.commit()
        row = conn.execute("SELECT entry_close_price FROM trade_entries").fetchone()
        assert row[0] is None


# ---------------------------------------------------------------------------
# Group 5 — trade_exits constraints
# ---------------------------------------------------------------------------

class TestTradeExits:

    def test_T15_cost_bps_not_null_no_default(self, conn: sqlite3.Connection) -> None:
        entry_id = _insert_entry(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO trade_exits (entry_id, exit_date, exit_close_price) "
                "VALUES (?,?,?)",
                (entry_id, "2026-06-21", 42000.0),
            )
            conn.commit()

    def test_T16_unique_entry_id_no_duplicate_exits(self, conn: sqlite3.Connection) -> None:
        entry_id = _insert_entry(conn)
        _insert_exit(conn, entry_id)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO trade_exits (entry_id, exit_date, exit_close_price, cost_bps) "
                "VALUES (?,?,?,?)",
                (entry_id, "2026-06-21", 43000.0, 15.0),
            )
            conn.commit()

    def test_T17_fk_entry_id_enforced(self, conn: sqlite3.Connection) -> None:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO trade_exits (entry_id, exit_date, exit_close_price, cost_bps) "
                "VALUES (?,?,?,?)",
                (99999, "2026-06-21", 42000.0, 15.0),
            )
            conn.commit()

    def test_no_signal_id_column_in_trade_exits(self, db_path: pathlib.Path) -> None:
        """D3 normalisation: signal_id must NOT be a column in trade_exits."""
        c = sqlite3.connect(db_path)
        cols = {row[1] for row in c.execute("PRAGMA table_info(trade_exits)")}
        c.close()
        assert "signal_id" not in cols


# ---------------------------------------------------------------------------
# Group 6 — trade_returns view
# ---------------------------------------------------------------------------

class TestTradeReturnsView:

    def _make_trade(
        self,
        conn: sqlite3.Connection,
        signal_id: str    = "h5_vol_regime",
        direction: int    = 1,
        entry: float      = 40000.0,
        exit_: float      = 42000.0,
        cost_bps: float   = 15.0,
        entry_date: str   = "2026-06-14",
        exit_date: str    = "2026-06-21",
    ) -> None:
        entry_id = _insert_entry(
            conn, signal_id=signal_id, direction=direction,
            entry_close=entry, entry_date=entry_date, planned_exit=exit_date,
        )
        _insert_exit(conn, entry_id, exit_date=exit_date, exit_close=exit_, cost_bps=cost_bps)

    def test_T18_long_trade_returns_correct(self, conn: sqlite3.Connection) -> None:
        self._make_trade(conn, direction=1, entry=40000.0, exit_=42000.0, cost_bps=15.0)
        row = conn.execute(
            "SELECT gross_return, net_return, hold_days_actual FROM trade_returns"
        ).fetchone()
        assert abs(row[0] - 0.05)   < 1e-9   # (42000/40000 - 1) * 1
        assert abs(row[1] - 0.0485) < 1e-9   # 0.05 - 15/10000
        assert row[2] == 7

    def test_T19_profitable_short_trade_correct(self, conn: sqlite3.Connection) -> None:
        # Short: price fell 40000 -> 38000 — short profits
        self._make_trade(conn, direction=-1, entry=40000.0, exit_=38000.0, cost_bps=15.0)
        row = conn.execute("SELECT gross_return, net_return FROM trade_returns").fetchone()
        assert abs(row[0] - 0.05)   < 1e-9   # (38000/40000 - 1) * -1
        assert abs(row[1] - 0.0485) < 1e-9

    def test_T20_losing_long_trade_negative_net_return(self, conn: sqlite3.Connection) -> None:
        self._make_trade(conn, direction=1, entry=40000.0, exit_=39000.0, cost_bps=15.0)
        row = conn.execute("SELECT gross_return, net_return FROM trade_returns").fetchone()
        assert abs(row[0] - (-0.025))  < 1e-9
        assert abs(row[1] - (-0.0265)) < 1e-9

    def test_T21_view_excludes_open_trades(self, conn: sqlite3.Connection) -> None:
        _insert_entry(conn)   # open trade, no exit row
        count = conn.execute("SELECT COUNT(*) FROM trade_returns").fetchone()[0]
        assert count == 0

    def test_T21b_view_includes_closed_trades(self, conn: sqlite3.Connection) -> None:
        self._make_trade(conn)
        count = conn.execute("SELECT COUNT(*) FROM trade_returns").fetchone()[0]
        assert count == 1


# ---------------------------------------------------------------------------
# Group 7 — Multi-signal isolation
# ---------------------------------------------------------------------------

class TestMultiSignalIsolation:

    def test_T22_signal_filtering_via_te_signal_id(self, conn: sqlite3.Connection) -> None:
        """trade_returns.signal_id comes from trade_entries (normalised).
        Filtering by signal_id works without any column on trade_exits."""
        h5 = _insert_entry(conn, signal_id="h5_vol_regime",
                            direction=1, entry_date="2026-06-14", planned_exit="2026-06-21")
        _insert_exit(conn, h5, exit_date="2026-06-21", exit_close=42000.0, cost_bps=15.0)

        g3 = _insert_entry(conn, signal_id="g3_momentum_tsmom", direction=-1,
                            entry_date="2026-06-14", entry_close=42000.0, planned_exit="2026-06-21")
        _insert_exit(conn, g3, exit_date="2026-06-21", exit_close=41000.0, cost_bps=15.0)

        h5_rows = conn.execute(
            "SELECT signal_id, direction FROM trade_returns WHERE signal_id='h5_vol_regime'"
        ).fetchall()
        g3_rows = conn.execute(
            "SELECT signal_id, direction FROM trade_returns WHERE signal_id='g3_momentum_tsmom'"
        ).fetchall()

        assert len(h5_rows) == 1 and h5_rows[0] == ("h5_vol_regime", 1)
        assert len(g3_rows) == 1 and g3_rows[0] == ("g3_momentum_tsmom", -1)

    def test_T22b_total_count_spans_all_signals(self, conn: sqlite3.Connection) -> None:
        for sig, d, edate, xdate in [
            ("h5_vol_regime",            1,  "2026-06-14", "2026-06-21"),
            ("g1_market_regime_sma200",  1,  "2026-06-14", "2026-06-21"),
            ("g1_market_regime_sma200", -1,  "2026-06-21", "2026-06-28"),
            ("g3_momentum_tsmom",       -1,  "2026-06-14", "2026-06-21"),
        ]:
            eid = _insert_entry(conn, signal_id=sig, direction=d,
                                entry_date=edate, planned_exit=xdate)
            _insert_exit(conn, eid, exit_date=xdate)
        count = conn.execute("SELECT COUNT(*) FROM trade_returns").fetchone()[0]
        assert count == 4
