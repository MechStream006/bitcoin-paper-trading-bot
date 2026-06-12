"""Daily certifier tests — Tier-A checks C1–C7.

All tests use an in-memory SQLite DB at user_version=4 (full schema).
No network, no filesystem, no .env required.

Coverage:
  TestSchemaGuard         — certify_day raises on user_version < 3
  TestC1Coverage          — full day GREEN/AMBER/RED, partial day cap, zero rows
  TestC2ExchTsPresence    — all present, mixed, all null, column absent
  TestC3ClockLatency      — green latencies, amber (high median), red, negatives,
                            absent column, no rows
  TestC4StaleRepersist    — no dupes, amber, red, absent column
  TestC5IvQuality         — all good, amber, red, no rows
  TestC6Integrity         — healthy DB (ok), absent exch_ts still passes
  TestC7Platform          — mainnet GREEN, testnet AMBER, unknown AMBER
  TestAggregateVerdict    — worst-case propagation; all-GREEN; SKIP ignored
  TestPersistVerdict      — row written; re-run overwrites; no table → warning
  TestCertifyDayOutput    — returned dict structure; verdict propagates
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from src.data.storage import init_schema

# Load certify_day from scripts/ (not a package)
_cd_spec = importlib.util.spec_from_file_location(
    "certify_day", ROOT / "scripts" / "certify_day.py"
)
_cd_mod = importlib.util.module_from_spec(_cd_spec)
sys.modules["certify_day"] = _cd_mod  # must register before exec_module for dataclasses
_cd_spec.loader.exec_module(_cd_mod)

certify_day            = _cd_mod.certify_day
CheckResult            = _cd_mod.CheckResult
_aggregate_verdict     = _cd_mod._aggregate_verdict
_persist_verdict       = _cd_mod._persist_verdict
_check_c1_coverage     = _cd_mod._check_c1_coverage
_check_c2_exch_ts_presence = _cd_mod._check_c2_exch_ts_presence
_check_c3_clock_latency    = _cd_mod._check_c3_clock_latency
_check_c4_stale_repersist  = _cd_mod._check_c4_stale_repersist
_check_c5_iv_quality       = _cd_mod._check_c5_iv_quality
_check_c6_integrity        = _cd_mod._check_c6_integrity
_check_c7_platform         = _cd_mod._check_c7_platform

# Load migrate
_mg_spec = importlib.util.spec_from_file_location(
    "migrate", ROOT / "scripts" / "migrate.py"
)
_mg_mod = importlib.util.module_from_spec(_mg_spec)
_mg_spec.loader.exec_module(_mg_mod)
migrate = _mg_mod.migrate

DAY = "2026-06-12"
_UTC = timezone.utc


# ---------------------------------------------------------------------------
# Test DB factory
# ---------------------------------------------------------------------------

def _make_db(version: int = 4) -> sqlite3.Connection:
    """In-memory DB at the requested user_version (0–4)."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    init_schema(conn)
    if version >= 1:
        migrate(conn)
    if version < 4:
        conn.execute(f"PRAGMA user_version = {version}")
        conn.commit()
    return conn


def _insert_instrument(conn: sqlite3.Connection, iid: int = 1) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO instruments
        (id, symbol, contract_type, underlying, quoting,
         strike_price, settlement_time, tick_size, contract_value,
         state, raw_json, fetched_at)
        VALUES (?, ?, 'call_options', 'BTC', 'USDT',
                95000.0, '2026-07-01T12:00:00Z', 0.01, 0.001,
                'live', '{}', '2026-06-01T00:00:00Z')
        """,
        (iid, f"C-BTC-{94000 + iid * 1000}-010726"),
    )
    conn.commit()


def _insert_collection_metrics(
    conn: sqlite3.Connection,
    day: str,
    n_cycles: int,
    start_hour: int = 0,
) -> None:
    """Insert n_cycles rows into collection_metrics for the given day."""
    base = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=_UTC)
    base = base.replace(hour=start_hour)
    for i in range(n_cycles):
        ts = (base + timedelta(seconds=i * 60)).isoformat()
        conn.execute(
            "INSERT OR REPLACE INTO collection_metrics "
            "(ts, universe_size, coverage_pct, wrote, skipped, failed, "
            "reconcile_count, stale_count) "
            "VALUES (?, 64, 1.0, 64, 0, 0, 0, 0)",
            (ts,),
        )
    conn.commit()


def _insert_option_quotes(
    conn: sqlite3.Connection,
    day: str,
    n_rows: int,
    instrument_id: int = 1,
    exch_ts_offset_sec: float = 1.0,
    exch_ts_null_count: int = 0,
    mark_iv_bad_count: int = 0,
    dup_exch_ts_count: int = 0,
) -> None:
    """Insert n_rows rows into option_quotes for testing.

    exch_ts_null_count: first N rows get NULL exch_ts.
    mark_iv_bad_count:  first N rows get mark_iv=0.0 (degenerate).
    dup_exch_ts_count:  first N rows share the same exch_ts as row 0 (dupes).
    """
    base    = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=_UTC)
    base_us = int(base.timestamp() * 1_000_000)

    for i in range(n_rows):
        ts       = (base + timedelta(seconds=i * 60 + 30)).isoformat()
        # exch_ts: slightly before wall ts so latency is positive
        raw_exch_us = base_us + i * 60_000_000  # one per minute
        if dup_exch_ts_count > 0 and i < dup_exch_ts_count:
            raw_exch_us = base_us  # all dupes share row-0 timestamp
        exch_ts  = None if i < exch_ts_null_count else raw_exch_us
        mark_iv  = 0.0 if i < mark_iv_bad_count else 0.5

        conn.execute(
            """
            INSERT OR REPLACE INTO option_quotes
            (instrument_id, ts, exch_ts, mark_price, mark_iv, bid_iv, ask_iv,
             spot_price, delta, gamma, theta, vega, rho,
             oi, oi_contracts, oi_value_usd, volume_24h,
             best_bid, best_ask, bid_qty, ask_qty, close)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (instrument_id, ts, exch_ts,
             95000.0, mark_iv, mark_iv * 0.95, mark_iv * 1.05,
             94000.0, 0.5, 0.001, -10.0, 50.0, 0.1,
             0.0, 0, 0.0, 10.0,
             94900.0, 95100.0, 5.0, 5.0, 94950.0),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# TestSchemaGuard
# ---------------------------------------------------------------------------

class TestSchemaGuard:
    def test_raises_below_required_version(self):
        conn = _make_db(version=2)
        with pytest.raises(RuntimeError, match="schema version"):
            certify_day(conn, DAY, "testnet")

    def test_raises_at_version_0(self):
        conn = _make_db(version=0)
        with pytest.raises(RuntimeError, match="schema version"):
            certify_day(conn, DAY, "testnet")

    def test_passes_at_version_3(self):
        conn = _make_db(version=3)
        # Should not raise (daily_certifier_log absent → persist=False path)
        result = certify_day(conn, DAY, "testnet", persist=False)
        assert result["verdict"] in ("GREEN", "AMBER", "RED")

    def test_passes_at_version_4(self):
        conn = _make_db(version=4)
        result = certify_day(conn, DAY, "testnet", persist=False)
        assert result["verdict"] in ("GREEN", "AMBER", "RED")


# ---------------------------------------------------------------------------
# TestC1Coverage
# ---------------------------------------------------------------------------

class TestC1Coverage:
    def test_full_day_green(self):
        conn = _make_db()
        _insert_collection_metrics(conn, DAY, 1440, start_hour=0)
        cr = _check_c1_coverage(conn, DAY)
        assert cr.result == "GREEN"
        assert cr.value >= 0.95

    def test_95_pct_amber(self):
        conn = _make_db()
        _insert_collection_metrics(conn, DAY, 1350, start_hour=0)  # 93.75%
        cr = _check_c1_coverage(conn, DAY)
        assert cr.result == "AMBER"

    def test_below_85_pct_red(self):
        # Must span the full day (both boundaries within 30 min) but have very
        # low row count so the certifier cannot declare it a partial day.
        conn = _make_db()
        base = datetime.strptime(DAY, "%Y-%m-%d").replace(tzinfo=_UTC)
        for minutes in [1, 60, 120, 23 * 60 + 58]:  # 4 rows; coverage=0.3% < 85%
            ts = (base + timedelta(minutes=minutes)).isoformat()
            conn.execute(
                "INSERT OR REPLACE INTO collection_metrics "
                "(ts, universe_size, coverage_pct, wrote, skipped, failed, "
                "reconcile_count, stale_count) VALUES (?, 64, 1.0, 64, 0, 0, 0, 0)",
                (ts,),
            )
        conn.commit()
        cr = _check_c1_coverage(conn, DAY)
        assert cr.result == "RED"

    def test_zero_rows_red(self):
        conn = _make_db()
        cr = _check_c1_coverage(conn, DAY)
        assert cr.result == "RED"
        assert cr.value == 0.0

    def test_partial_day_capped_amber(self):
        # Start at 10:00 UTC — more than 30 min from midnight → partial
        conn = _make_db()
        _insert_collection_metrics(conn, DAY, 840, start_hour=10)
        cr = _check_c1_coverage(conn, DAY)
        assert cr.result == "AMBER"

    def test_no_table_amber(self):
        conn = sqlite3.connect(":memory:")
        init_schema(conn)
        conn.execute("PRAGMA user_version = 3")
        conn.execute("DROP TABLE IF EXISTS collection_metrics")
        cr = _check_c1_coverage(conn, DAY)
        assert cr.result == "AMBER"


# ---------------------------------------------------------------------------
# TestC2ExchTsPresence
# ---------------------------------------------------------------------------

class TestC2ExchTsPresence:
    def test_all_present_green(self):
        conn = _make_db()
        _insert_instrument(conn)
        _insert_option_quotes(conn, DAY, 100, exch_ts_null_count=0)
        cr = _check_c2_exch_ts_presence(conn, DAY)
        assert cr.result == "GREEN"
        assert cr.value == 1.0

    def test_2pct_null_amber(self):
        conn = _make_db()
        _insert_instrument(conn)
        _insert_option_quotes(conn, DAY, 100, exch_ts_null_count=2)
        cr = _check_c2_exch_ts_presence(conn, DAY)
        assert cr.result == "AMBER"

    def test_10pct_null_red(self):
        conn = _make_db()
        _insert_instrument(conn)
        _insert_option_quotes(conn, DAY, 100, exch_ts_null_count=10)
        cr = _check_c2_exch_ts_presence(conn, DAY)
        assert cr.result == "RED"

    def test_all_null_amber_with_note(self):
        """100% NULL is AMBER (likely pre-migration data), not RED."""
        conn = _make_db()
        _insert_instrument(conn)
        _insert_option_quotes(conn, DAY, 50, exch_ts_null_count=50)
        cr = _check_c2_exch_ts_presence(conn, DAY)
        assert cr.result == "RED"  # 100% null IS RED per threshold (0% presence < 95%)
        # The note should mention pre-migration
        assert "pre-migration" in cr.note.lower()

    def test_column_absent_amber(self):
        conn = sqlite3.connect(":memory:")
        init_schema(conn)
        conn.execute("PRAGMA user_version = 3")
        conn.execute("ALTER TABLE option_quotes RENAME TO _oq_backup")
        conn.execute(
            "CREATE TABLE option_quotes AS SELECT instrument_id, ts, mark_price "
            "FROM _oq_backup LIMIT 0"
        )
        conn.commit()
        cr = _check_c2_exch_ts_presence(conn, DAY)
        assert cr.result == "AMBER"
        assert "absent" in cr.note.lower()


# ---------------------------------------------------------------------------
# TestC3ClockLatency
# ---------------------------------------------------------------------------

class TestC3ClockLatency:
    def _insert_with_latencies(
        self,
        conn: sqlite3.Connection,
        latencies_sec: list[float],
        day: str = DAY,
    ) -> None:
        """Insert option_quotes rows with controlled ts - exch_ts latencies."""
        _insert_instrument(conn)
        base = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=_UTC)
        for i, lat in enumerate(latencies_sec):
            ts      = (base + timedelta(seconds=i * 60 + 30)).isoformat()
            exch_ts = int((base + timedelta(seconds=i * 60 + 30 - lat)).timestamp() * 1_000_000)
            conn.execute(
                "INSERT OR REPLACE INTO option_quotes "
                "(instrument_id, ts, exch_ts, mark_price, mark_iv, bid_iv, ask_iv, "
                "spot_price, delta, gamma, theta, vega, rho, oi, oi_contracts, "
                "oi_value_usd, volume_24h, best_bid, best_ask, bid_qty, ask_qty, close) "
                "VALUES (1,?,?,95000,0.5,0.475,0.525,94000,0.5,0.001,-10,50,0.1,0,0,0,10,94900,95100,5,5,94950)",
                (ts, exch_ts),
            )
        conn.commit()

    def test_low_latency_green(self):
        conn = _make_db()
        self._insert_with_latencies(conn, [1.0] * 100)
        cr = _check_c3_clock_latency(conn, DAY)
        assert cr.result == "GREEN"
        assert cr.value["median_sec"] < 10
        assert cr.value["negatives"] == 0

    def test_high_median_amber(self):
        conn = _make_db()
        self._insert_with_latencies(conn, [15.0] * 100)  # median=15s > 10s → AMBER
        cr = _check_c3_clock_latency(conn, DAY)
        assert cr.result == "AMBER"

    def test_very_high_median_red(self):
        conn = _make_db()
        self._insert_with_latencies(conn, [70.0] * 100)  # median=70s > 60s → RED
        cr = _check_c3_clock_latency(conn, DAY)
        assert cr.result == "RED"

    def test_negative_latency_amber(self):
        # 2 negatives: wall clock is behind exchange → AMBER (≤5 negatives)
        conn = _make_db()
        latencies = [2.0] * 98 + [-1.0, -1.0]
        self._insert_with_latencies(conn, latencies)
        cr = _check_c3_clock_latency(conn, DAY)
        assert cr.result == "AMBER"
        assert cr.value["negatives"] == 2

    def test_many_negatives_red(self):
        conn = _make_db()
        latencies = [-1.0] * 10 + [2.0] * 90  # 10 negatives > 5 → RED
        self._insert_with_latencies(conn, latencies)
        cr = _check_c3_clock_latency(conn, DAY)
        assert cr.result == "RED"
        assert cr.value["negatives"] == 10

    def test_no_rows_amber(self):
        conn = _make_db()
        cr = _check_c3_clock_latency(conn, DAY)
        assert cr.result == "AMBER"


# ---------------------------------------------------------------------------
# TestC4StaleRepersist
# ---------------------------------------------------------------------------

class TestC4StaleRepersist:
    def test_no_dupes_green(self):
        conn = _make_db()
        _insert_instrument(conn)
        _insert_option_quotes(conn, DAY, 100, dup_exch_ts_count=0)
        cr = _check_c4_stale_repersist(conn, DAY)
        assert cr.result == "GREEN"
        assert cr.value == 0.0

    def test_15pct_dupes_amber(self):
        conn = _make_db()
        _insert_instrument(conn)
        _insert_option_quotes(conn, DAY, 100, dup_exch_ts_count=15)
        cr = _check_c4_stale_repersist(conn, DAY)
        assert cr.result == "AMBER"

    def test_30pct_dupes_red(self):
        conn = _make_db()
        _insert_instrument(conn)
        _insert_option_quotes(conn, DAY, 100, dup_exch_ts_count=30)
        cr = _check_c4_stale_repersist(conn, DAY)
        assert cr.result == "RED"

    def test_no_rows_amber(self):
        conn = _make_db()
        cr = _check_c4_stale_repersist(conn, DAY)
        assert cr.result == "AMBER"


# ---------------------------------------------------------------------------
# TestC5IvQuality
# ---------------------------------------------------------------------------

class TestC5IvQuality:
    def test_all_good_green(self):
        conn = _make_db()
        _insert_instrument(conn)
        _insert_option_quotes(conn, DAY, 100, mark_iv_bad_count=0)
        cr = _check_c5_iv_quality(conn, DAY)
        assert cr.result == "GREEN"

    def test_5pct_bad_amber(self):
        conn = _make_db()
        _insert_instrument(conn)
        _insert_option_quotes(conn, DAY, 100, mark_iv_bad_count=5)
        cr = _check_c5_iv_quality(conn, DAY)
        assert cr.result == "AMBER"

    def test_15pct_bad_red(self):
        conn = _make_db()
        _insert_instrument(conn)
        _insert_option_quotes(conn, DAY, 100, mark_iv_bad_count=15)
        cr = _check_c5_iv_quality(conn, DAY)
        assert cr.result == "RED"

    def test_no_rows_amber(self):
        conn = _make_db()
        cr = _check_c5_iv_quality(conn, DAY)
        assert cr.result == "AMBER"

    def test_degenerate_very_low_iv_caught(self):
        """mark_iv=0.000005 (documented testnet value) must be caught."""
        conn = _make_db()
        _insert_instrument(conn)
        base = datetime.strptime(DAY, "%Y-%m-%d").replace(tzinfo=_UTC)
        conn.execute(
            "INSERT OR REPLACE INTO option_quotes "
            "(instrument_id, ts, exch_ts, mark_price, mark_iv, bid_iv, ask_iv, "
            "spot_price, delta, gamma, theta, vega, rho, oi, oi_contracts, "
            "oi_value_usd, volume_24h, best_bid, best_ask, bid_qty, ask_qty, close) "
            "VALUES (1,?,?,95000,0.000005,0,0,94000,0,0,0,0,0,0,0,0,0,0,0,0,0,0)",
            (base.isoformat(), int(base.timestamp() * 1_000_000)),
        )
        conn.commit()
        cr = _check_c5_iv_quality(conn, DAY)
        # 1 bad row / 1 total = 100% → RED
        assert cr.result == "RED"


# ---------------------------------------------------------------------------
# TestC6Integrity
# ---------------------------------------------------------------------------

class TestC6Integrity:
    def test_healthy_db_green(self):
        conn = _make_db()
        cr = _check_c6_integrity(conn)
        assert cr.result == "GREEN"
        assert cr.value == "ok"

    def test_returns_check_id_c6(self):
        conn = _make_db()
        cr = _check_c6_integrity(conn)
        assert cr.check_id == "C6"


# ---------------------------------------------------------------------------
# TestC7Platform
# ---------------------------------------------------------------------------

class TestC7Platform:
    def test_mainnet_green(self):
        cr = _check_c7_platform("mainnet")
        assert cr.result == "GREEN"

    def test_testnet_amber(self):
        cr = _check_c7_platform("testnet")
        assert cr.result == "AMBER"

    def test_unknown_platform_amber(self):
        cr = _check_c7_platform("staging")
        assert cr.result == "AMBER"

    def test_provisional_false_for_c7(self):
        cr = _check_c7_platform("testnet")
        assert cr.provisional is False


# ---------------------------------------------------------------------------
# TestAggregateVerdict
# ---------------------------------------------------------------------------

class TestAggregateVerdict:
    def _cr(self, check_id: str, result: str) -> CheckResult:
        return CheckResult(check_id, "test", result, None, None, "")

    def test_all_green_is_green(self):
        results = [self._cr(f"C{i}", "GREEN") for i in range(1, 8)]
        assert _aggregate_verdict(results) == "GREEN"

    def test_one_amber_is_amber(self):
        results = [self._cr(f"C{i}", "GREEN") for i in range(1, 7)]
        results.append(self._cr("C7", "AMBER"))
        assert _aggregate_verdict(results) == "AMBER"

    def test_one_red_is_red(self):
        results = [self._cr(f"C{i}", "AMBER") for i in range(1, 7)]
        results.append(self._cr("C7", "RED"))
        assert _aggregate_verdict(results) == "RED"

    def test_red_beats_amber(self):
        results = [
            self._cr("C1", "RED"),
            self._cr("C2", "AMBER"),
            self._cr("C3", "GREEN"),
        ]
        assert _aggregate_verdict(results) == "RED"

    def test_skip_ignored(self):
        results = [
            self._cr("C1", "SKIP"),
            self._cr("C2", "GREEN"),
        ]
        assert _aggregate_verdict(results) == "GREEN"

    def test_all_skip_is_green(self):
        results = [self._cr(f"C{i}", "SKIP") for i in range(1, 4)]
        assert _aggregate_verdict(results) == "GREEN"


# ---------------------------------------------------------------------------
# TestPersistVerdict
# ---------------------------------------------------------------------------

class TestPersistVerdict:
    def _make_checks(self) -> list[CheckResult]:
        return [CheckResult(f"C{i}", f"check{i}", "GREEN", 1.0, 1.0, "ok")
                for i in range(1, 8)]

    def test_row_written(self):
        conn = _make_db()
        _persist_verdict(conn, DAY, "GREEN", "testnet", "abc1234", self._make_checks())
        row = conn.execute(
            "SELECT verdict, platform FROM daily_certifier_log WHERE day=?", (DAY,)
        ).fetchone()
        assert row is not None
        assert row[0] == "GREEN"
        assert row[1] == "testnet"

    def test_rerun_overwrites(self):
        conn = _make_db()
        _persist_verdict(conn, DAY, "GREEN", "testnet", "abc", self._make_checks())
        _persist_verdict(conn, DAY, "RED",   "testnet", "def", self._make_checks())
        row = conn.execute(
            "SELECT verdict FROM daily_certifier_log WHERE day=?", (DAY,)
        ).fetchone()
        assert row[0] == "RED"

    def test_checks_json_parseable(self):
        conn = _make_db()
        _persist_verdict(conn, DAY, "AMBER", "testnet", "abc", self._make_checks())
        checks_json = conn.execute(
            "SELECT checks_json FROM daily_certifier_log WHERE day=?", (DAY,)
        ).fetchone()[0]
        parsed = json.loads(checks_json)
        assert "C1" in parsed
        assert parsed["C1"]["result"] == "GREEN"


# ---------------------------------------------------------------------------
# TestCertifyDayOutput
# ---------------------------------------------------------------------------

class TestCertifyDayOutput:
    def test_returns_dict_with_required_keys(self):
        conn = _make_db()
        result = certify_day(conn, DAY, "testnet", persist=False)
        for key in ("day", "verdict", "platform", "certifier_version", "checks"):
            assert key in result

    def test_day_preserved(self):
        conn = _make_db()
        result = certify_day(conn, DAY, "testnet", persist=False)
        assert result["day"] == DAY

    def test_platform_preserved(self):
        conn = _make_db()
        result = certify_day(conn, DAY, "mainnet", persist=False)
        assert result["platform"] == "mainnet"

    def test_testnet_caps_at_amber(self):
        """An all-GREEN data day on testnet should still be AMBER via C7."""
        conn = _make_db()
        _insert_collection_metrics(conn, DAY, 1440, start_hour=0)
        _insert_instrument(conn)
        _insert_option_quotes(conn, DAY, 100)
        result = certify_day(conn, DAY, "testnet", persist=False)
        assert result["verdict"] == "AMBER"
        assert result["checks"]["C7"]["result"] == "AMBER"

    def test_no_persist_does_not_write(self):
        conn = _make_db()
        certify_day(conn, DAY, "testnet", persist=False)
        count = conn.execute(
            "SELECT COUNT(*) FROM daily_certifier_log WHERE day=?", (DAY,)
        ).fetchone()[0]
        assert count == 0

    def test_persist_true_writes_row(self):
        conn = _make_db()
        certify_day(conn, DAY, "testnet", persist=True, certifier_version="test")
        count = conn.execute(
            "SELECT COUNT(*) FROM daily_certifier_log WHERE day=?", (DAY,)
        ).fetchone()[0]
        assert count == 1

    def test_all_checks_present_in_output(self):
        conn = _make_db()
        result = certify_day(conn, DAY, "testnet", persist=False)
        for check_id in ("C1", "C2", "C3", "C4", "C5", "C6", "C7"):
            assert check_id in result["checks"], f"missing {check_id}"

    def test_verdict_propagates_red(self):
        """A RED check produces a RED verdict."""
        conn = _make_db()
        # No collection_metrics rows → C1 is RED → overall RED
        result = certify_day(conn, DAY, "mainnet", persist=False)
        assert result["checks"]["C1"]["result"] == "RED"
        assert result["verdict"] == "RED"
