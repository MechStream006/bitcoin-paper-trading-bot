"""Tests for the offline execution-evidence research layer.

Synthetic fixtures throughout, with one block that runs the real modules against
an extracted production slice when one is present. Every fixture is built with
explicit timestamps so nothing depends on the wall clock or on ordering luck.
"""
from __future__ import annotations

import ast
import os
import pathlib
import inspect
import sqlite3
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_RESEARCH = _ROOT / "research"
if str(_RESEARCH) not in sys.path:
    sys.path.insert(0, str(_RESEARCH))

from execution_evidence import completed_trades as CT  # noqa: E402
from execution_evidence import execution_quality as EQ  # noqa: E402


SCHEMA = """
CREATE TABLE instruments (
    id INTEGER PRIMARY KEY, symbol TEXT, contract_type TEXT, underlying TEXT,
    quoting TEXT, strike_price REAL, settlement_time TEXT, tick_size REAL,
    contract_value REAL, state TEXT, raw_json TEXT, fetched_at TEXT);
CREATE TABLE orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT, client_order_id TEXT,
    delta_order_id TEXT, instrument_id INTEGER NOT NULL, side TEXT NOT NULL,
    order_type TEXT NOT NULL, quantity REAL NOT NULL, price REAL,
    status TEXT NOT NULL, strategy TEXT, placed_ts TEXT NOT NULL,
    filled_ts TEXT);
CREATE TABLE order_fills (
    fill_id TEXT PRIMARY KEY, client_order_id TEXT, delta_order_id TEXT,
    instrument_id INTEGER NOT NULL, side TEXT NOT NULL, size REAL NOT NULL,
    price REAL NOT NULL, fee REAL NOT NULL DEFAULT 0.0, role TEXT NOT NULL,
    realised_pnl REAL, exch_ts TEXT, processed_ts TEXT NOT NULL);
CREATE TABLE order_rejections (
    client_order_id TEXT PRIMARY KEY, ts TEXT NOT NULL, http_status INTEGER,
    error_code TEXT, body TEXT);
CREATE TABLE positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, instrument_id INTEGER NOT NULL,
    side TEXT NOT NULL, quantity REAL NOT NULL, entry_price REAL NOT NULL,
    entry_ts TEXT NOT NULL, exit_price REAL, exit_ts TEXT, pnl REAL,
    status TEXT NOT NULL, strategy TEXT);
CREATE TABLE protective_stops (
    client_order_id TEXT PRIMARY KEY, delta_order_id TEXT, position_id INTEGER,
    side TEXT NOT NULL, size INTEGER NOT NULL, entry_price REAL NOT NULL,
    trigger_price REAL NOT NULL, limit_price REAL NOT NULL,
    reduce_only INTEGER NOT NULL DEFAULT 1, placed_ts TEXT NOT NULL);
CREATE TABLE spot_series (ts TEXT PRIMARY KEY, spot_price REAL NOT NULL);
CREATE TABLE trade_signal_context (
    client_order_id TEXT PRIMARY KEY, signal_id TEXT NOT NULL,
    direction INTEGER, raw_value REAL, signal_day TEXT, context_json TEXT,
    captured_ts TEXT NOT NULL);
"""

FWD_SCHEMA = """
CREATE TABLE signal_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, signal_id TEXT NOT NULL,
    day TEXT NOT NULL, position REAL, raw_value REAL, run_id TEXT);
"""


def _db(tmp_path, name="t.db"):
    p = tmp_path / name
    c = sqlite3.connect(p)
    c.executescript(SCHEMA)
    c.execute("INSERT INTO instruments (id,symbol,contract_value,tick_size) "
              "VALUES (84,'BTCUSD',0.001,0.1)")
    c.commit()
    return p, c


def _long_trade(c, *, entry=100000.0, exit_px=99000.0, trigger=99100.0,
                limit=99000.0, entry_limit=100001.0, strategy="g3_x",
                entry_fee=0.05, exit_fee=0.05, pnl=None):
    """A single closed long that exits on its protective stop."""
    if pnl is None:
        pnl = (exit_px - entry) * 0.001 - entry_fee - exit_fee
    c.execute("INSERT INTO orders (client_order_id,delta_order_id,instrument_id,"
              "side,order_type,quantity,price,status,strategy,placed_ts) VALUES "
              "('coid-e','1001',84,'buy','limit_order',1,?,'filled',?, "
              "'2026-09-01T10:00:00.000000+00:00')", (entry_limit, strategy))
    c.execute("INSERT INTO orders (client_order_id,delta_order_id,instrument_id,"
              "side,order_type,quantity,price,status,strategy,placed_ts) VALUES "
              "('stop-p1-q1','1002',84,'sell','limit_order',1,?,'filled','protection',"
              "'2026-09-01T10:01:00.000000+00:00')", (limit,))
    c.execute("INSERT INTO order_fills VALUES "
              "('f1',NULL,'1001',84,'buy',1,?,?,'entry',NULL,"
              "'2026-09-01T10:00:01.000000Z','2026-09-01T10:01:00.000000+00:00')",
              (entry, entry_fee))
    c.execute("INSERT INTO order_fills VALUES "
              "('f2',NULL,'1002',84,'sell',1,?,?,'exit',?,"
              "'2026-09-01T11:00:01.000000Z','2026-09-01T11:01:00.000000+00:00')",
              (exit_px, exit_fee, pnl))
    c.execute("INSERT INTO positions (instrument_id,side,quantity,entry_price,"
              "entry_ts,exit_price,exit_ts,pnl,status,strategy) VALUES "
              "(84,'buy',1,?, '2026-09-01T10:01:00.000000+00:00', ?, "
              "'2026-09-01T11:01:00.000000+00:00', ?, 'closed', ?)",
              (entry, exit_px, pnl, strategy))
    c.execute("INSERT INTO protective_stops VALUES "
              "('stop-p1-q1','1002',1,'sell',1,?,?,?,1,"
              "'2026-09-01T10:01:00.500000+00:00')", (entry, trigger, limit))
    c.commit()


def _spot(c, rows):
    c.executemany("INSERT INTO spot_series VALUES (?,?)", rows)
    c.commit()


class TestTimestamps:
    def test_parses_both_iso_spellings_to_utc(self):
        a = CT.parse_ts("2026-09-01T10:00:00.000000Z")
        b = CT.parse_ts("2026-09-01T10:00:00.000000+00:00")
        assert a == b and a.tzinfo is not None

    def test_naive_is_assumed_utc(self):
        assert CT.parse_ts("2026-09-01T10:00:00") == CT.parse_ts(
            "2026-09-01T10:00:00Z")

    def test_bad_and_empty_values_are_none(self):
        assert CT.parse_ts(None) is None
        assert CT.parse_ts("") is None
        assert CT.parse_ts("not-a-timestamp") is None

    def test_norm_ts_emits_the_stored_spelling(self):
        assert CT._norm_ts("2026-09-01T10:00:00Z").endswith("+00:00")

    def test_seconds_between_is_signed(self):
        assert CT.seconds_between("2026-09-01T10:00:00Z",
                                  "2026-09-01T10:00:30Z") == 30.0
        assert CT.seconds_between("2026-09-01T10:00:30Z",
                                  "2026-09-01T10:00:00Z") == -30.0


class TestSlippageConvention:
    def test_buy_improved_by_filling_lower(self):
        assert CT.favourable_slippage("buy", 100.0, 99.0) == pytest.approx(1.0)

    def test_buy_penalised_by_filling_higher(self):
        assert CT.favourable_slippage("buy", 100.0, 101.0) == pytest.approx(-1.0)

    def test_sell_improved_by_filling_higher(self):
        assert CT.favourable_slippage("sell", 100.0, 101.0) == pytest.approx(1.0)

    def test_sell_penalised_by_filling_lower(self):
        assert CT.favourable_slippage("sell", 100.0, 99.0) == pytest.approx(-1.0)

    def test_missing_side_of_the_comparison_is_none(self):
        assert CT.favourable_slippage("buy", None, 99.0) is None
        assert CT.favourable_slippage("buy", 100.0, None) is None


class TestDataset:
    def test_one_row_per_closed_position(self, tmp_path):
        p, c = _db(tmp_path)
        _long_trade(c)
        c.execute("INSERT INTO positions (instrument_id,side,quantity,entry_price,"
                  "entry_ts,status,strategy) VALUES "
                  "(84,'buy',1,100.0,'2026-09-02T10:00:00+00:00','open','g3_x')")
        c.commit()
        rows = CT.build_dataset(p)
        assert len(rows) == 1
        assert rows[0].trade_id == 1

    def test_open_positions_are_excluded_but_counted(self, tmp_path):
        p, c = _db(tmp_path)
        _long_trade(c)
        c.execute("INSERT INTO positions (instrument_id,side,quantity,entry_price,"
                  "entry_ts,status,strategy) VALUES "
                  "(84,'buy',1,100.0,'2026-09-02T10:00:00+00:00','open','g3_x')")
        c.commit()
        assert CT.coverage(p)["positions_open"] == 1
        assert CT.coverage(p)["completed_trades"] == 1

    def test_contract_value_comes_from_the_instrument_row(self, tmp_path):
        p, c = _db(tmp_path)
        c.execute("UPDATE instruments SET contract_value=0.002 WHERE id=84")
        _long_trade(c)
        assert CT.build_dataset(p)[0].contract_value == pytest.approx(0.002)

    def test_no_live_limit_is_imported_or_hardcoded(self):
        src = (_RESEARCH / "execution_evidence" / "completed_trades.py").read_text(
            encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names]
                mod = getattr(node, "module", "") or ""
                assert not mod.startswith("src"), f"research imported {mod}"
                assert not any(n.startswith("src") for n in names)
        assert "MAX_TRADE_RISK_USD" not in _strip_docstrings(src)

    def test_quantity_falls_back_to_the_fill_when_positions_says_zero(self, tmp_path):
        p, c = _db(tmp_path)
        _long_trade(c)
        c.execute("UPDATE positions SET quantity=0.0 WHERE id=1")
        c.commit()
        assert CT.build_dataset(p)[0].quantity == pytest.approx(1.0)

    def test_direction_and_gross_pnl(self, tmp_path):
        p, c = _db(tmp_path)
        _long_trade(c, entry=100000.0, exit_px=99000.0)
        t = CT.build_dataset(p)[0]
        assert t.direction == 1
        assert t.gross_pnl == pytest.approx((99000.0 - 100000.0) * 0.001)

    def test_configured_risk_uses_the_recorded_stop(self, tmp_path):
        p, c = _db(tmp_path)
        _long_trade(c, entry=100000.0, trigger=99100.0,
                    entry_fee=0.04, exit_fee=0.06)
        t = CT.build_dataset(p)[0]
        assert t.configured_risk_usd == pytest.approx(900 * 0.001 + 0.10)

    def test_realised_risk_is_none_for_a_winner(self, tmp_path):
        p, c = _db(tmp_path)
        _long_trade(c, entry=99000.0, exit_px=100000.0, trigger=98000.0,
                    limit=97900.0)
        t = CT.build_dataset(p)[0]
        assert t.realised_pnl > 0
        assert t.realised_risk_usd is None
        assert t.risk_overrun_usd is None

    def test_overrun_only_when_the_stop_closed_the_trade(self, tmp_path):
        p, c = _db(tmp_path)
        _long_trade(c)
        c.execute("UPDATE order_fills SET delta_order_id='9999' WHERE fill_id='f2'")
        c.commit()
        t = CT.build_dataset(p)[0]
        assert t.exited_via != "protective_stop"
        assert t.risk_overrun_usd is None

    def test_stop_slippage_is_measured_against_the_trigger(self, tmp_path):
        p, c = _db(tmp_path)
        _long_trade(c, exit_px=99000.0, trigger=99100.0)
        t = CT.build_dataset(p)[0]
        assert t.exit_slippage_pts == pytest.approx(-100.0)

    def test_protection_latency_is_measured_from_observation(self, tmp_path):
        p, c = _db(tmp_path)
        _long_trade(c)
        assert CT.build_dataset(p)[0].protection_latency_seconds == pytest.approx(0.5)

    def test_link_exact_false_when_no_fill_matches(self, tmp_path):
        p, c = _db(tmp_path)
        _long_trade(c)
        c.execute("UPDATE order_fills SET processed_ts='2026-09-01T23:59:59+00:00' "
                  "WHERE fill_id='f1'")
        c.commit()
        t = CT.build_dataset(p)[0]
        assert t.link_exact is False

    def test_dataset_is_deterministic(self, tmp_path):
        p, c = _db(tmp_path)
        _long_trade(c)
        _spot(c, [("2026-09-01T10:30:00.000000+00:00", 99500.0)])
        assert CT.to_records(CT.build_dataset(p)) == CT.to_records(
            CT.build_dataset(p))


class TestExcursionsAreLeakageSafe:
    def test_prices_outside_the_window_are_ignored(self, tmp_path):
        p, c = _db(tmp_path)
        _long_trade(c, entry=100000.0, exit_px=99000.0)
        _spot(c, [
            ("2026-09-01T09:00:00.000000+00:00", 200000.0),   # before entry fill
            ("2026-09-01T10:30:00.000000+00:00", 100500.0),   # inside
            ("2026-09-01T12:00:00.000000+00:00", 300000.0),   # after exit fill
        ])
        t = CT.build_dataset(p)[0]
        assert t.mfe_pts == pytest.approx(500.0)
        assert t.excursion_samples == 1

    def test_fill_prices_are_folded_in(self, tmp_path):
        p, c = _db(tmp_path)
        _long_trade(c, entry=100000.0, exit_px=99000.0)
        t = CT.build_dataset(p)[0]
        assert t.mae_pts == pytest.approx(-1000.0)
        assert t.excursion_samples == 0

    def test_short_direction_flips_the_sign(self, tmp_path):
        p, c = _db(tmp_path)
        c.execute("INSERT INTO order_fills VALUES "
                  "('f1',NULL,'1001',84,'sell',1,100000.0,0.05,'entry',NULL,"
                  "'2026-09-01T10:00:01.000000Z','2026-09-01T10:01:00.000000+00:00')")
        c.execute("INSERT INTO order_fills VALUES "
                  "('f2',NULL,'1002',84,'buy',1,99000.0,0.05,'exit',0.9,"
                  "'2026-09-01T11:00:01.000000Z','2026-09-01T11:01:00.000000+00:00')")
        c.execute("INSERT INTO positions (instrument_id,side,quantity,entry_price,"
                  "entry_ts,exit_price,exit_ts,pnl,status,strategy) VALUES "
                  "(84,'sell',1,100000.0,'2026-09-01T10:01:00.000000+00:00',99000.0,"
                  "'2026-09-01T11:01:00.000000+00:00',0.9,'closed','g3_x')")
        c.commit()
        t = CT.build_dataset(p)[0]
        assert t.direction == -1
        assert t.mfe_pts == pytest.approx(1000.0)
        assert t.mae_pts == pytest.approx(0.0)

    def test_mae_never_positive_and_mfe_never_negative(self, tmp_path):
        p, c = _db(tmp_path)
        _long_trade(c, entry=100000.0, exit_px=100500.0, trigger=99000.0,
                    limit=98900.0)
        t = CT.build_dataset(p)[0]
        assert t.mae_pts <= 0.0 <= t.mfe_pts


class TestSignalLinkageIsLeakageSafe:
    def _fwd(self, tmp_path, rows):
        p = tmp_path / "fwd.db"
        c = sqlite3.connect(p)
        c.executescript(FWD_SCHEMA)
        c.executemany(
            "INSERT INTO signal_log (signal_id,day,position,raw_value) "
            "VALUES (?,?,?,?)", rows)
        c.commit()
        c.close()
        return p

    def test_same_day_observation_is_refused(self, tmp_path):
        """The day-D row is produced at 01:00 UTC on D+1, after the entry."""
        p, c = _db(tmp_path)
        _long_trade(c, strategy="g3_x")
        fwd = self._fwd(tmp_path, [
            ("g3_x", "2026-08-31", 1.0, 0.25),
            ("g3_x", "2026-09-01", -1.0, -0.99),
        ])
        t = CT.build_dataset(p, fwd)[0]
        assert t.signal_day == "2026-08-31"
        assert t.signal_position == pytest.approx(1.0)

    def test_exit_day_observation_is_never_read(self, tmp_path):
        p, c = _db(tmp_path)
        _long_trade(c, strategy="g3_x")
        fwd = self._fwd(tmp_path, [
            ("g3_x", "2026-08-31", 1.0, 0.25),
            ("g3_x", "2026-09-02", -1.0, -0.99),
        ])
        assert CT.build_dataset(p, fwd)[0].signal_day == "2026-08-31"

    def test_stored_context_takes_precedence_when_present(self, tmp_path):
        p, c = _db(tmp_path)
        _long_trade(c, strategy="g3_x")
        c.execute("INSERT INTO trade_signal_context VALUES "
                  "('coid-e','h5_vol_regime',1,0.42,'2026-08-31',NULL,"
                  "'2026-09-01T10:00:00+00:00')")
        c.commit()
        t = CT.build_dataset(p)[0]
        assert t.linkage_source == "trade_signal_context"
        assert t.signal_id == "h5_vol_regime"

    def test_manual_trades_get_no_signal_linkage(self, tmp_path):
        p, c = _db(tmp_path)
        _long_trade(c, strategy="manual")
        t = CT.build_dataset(p)[0]
        assert t.is_automated is False
        assert t.linkage_source is None and t.signal_id is None

    def test_missing_forward_db_degrades_without_raising(self, tmp_path):
        p, c = _db(tmp_path)
        _long_trade(c, strategy="g3_x")
        t = CT.build_dataset(p, tmp_path / "absent.db")[0]
        assert t.linkage_source == "strategy_name"
        assert t.signal_day is None


class TestFillCycles:
    def test_flat_to_flat_segmentation(self, tmp_path):
        p, c = _db(tmp_path)
        _long_trade(c)
        conn = CT.connect_ro(p)
        try:
            cycles = CT.fill_cycles(CT.load_fills(conn))
        finally:
            conn.close()
        assert len(cycles) == 1
        assert cycles[0]["exit_processed_ts"] is not None

    def test_trailing_open_cycle_is_reported_unclosed(self, tmp_path):
        p, c = _db(tmp_path)
        _long_trade(c)
        c.execute("INSERT INTO order_fills VALUES "
                  "('f3',NULL,'1003',84,'buy',1,98000.0,0.05,'entry',NULL,"
                  "'2026-09-02T10:00:01.000000Z','2026-09-02T10:01:00.000000+00:00')")
        c.commit()
        conn = CT.connect_ro(p)
        try:
            cycles = CT.fill_cycles(CT.load_fills(conn))
        finally:
            conn.close()
        assert len(cycles) == 2
        assert cycles[1]["exit_processed_ts"] is None


class TestExecutionQuality:
    def test_latency_splits_resting_stops_from_marketable_orders(self, tmp_path):
        p, c = _db(tmp_path)
        _long_trade(c)
        lat = EQ.latency(p)
        assert lat["submit_to_fill_seconds"]["n"] == 1
        assert lat["resting_stop_time_to_fill_seconds"]["n"] == 1
        assert (lat["resting_stop_time_to_fill_seconds"]["max"]
                > lat["submit_to_fill_seconds"]["max"])

    def test_observation_lag_is_measured_separately(self, tmp_path):
        p, c = _db(tmp_path)
        _long_trade(c)
        assert EQ.latency(p)["observation_lag_seconds"]["n"] == 2

    def test_idempotency_clean_on_distinct_ids(self, tmp_path):
        p, c = _db(tmp_path)
        _long_trade(c)
        assert EQ.idempotency(p)["clean"] is True

    def test_idempotency_flags_a_duplicate_client_order_id(self, tmp_path):
        p, c = _db(tmp_path)
        _long_trade(c)
        c.execute("INSERT INTO orders (client_order_id,delta_order_id,"
                  "instrument_id,side,order_type,quantity,price,status,strategy,"
                  "placed_ts) VALUES ('coid-e','1009',84,'buy','limit_order',1,"
                  "100.0,'filled','g3_x','2026-09-01T10:00:00+00:00')")
        c.commit()
        idem = EQ.idempotency(p)
        assert idem["clean"] is False
        assert idem["duplicate_client_order_ids"][0]["client_order_id"] == "coid-e"

    def test_rejections_are_grouped_by_error_code(self, tmp_path):
        p, c = _db(tmp_path)
        _long_trade(c)
        c.execute("INSERT INTO order_rejections VALUES "
                  "('x','2026-08-20T07:34:04+00:00',400,'bad_schema','{}')")
        c.commit()
        rej = EQ.rejections(p)
        assert rej["total"] == 1 and rej["by_error_code"] == {"bad_schema": 1}

    def test_reconciliation_clean_on_consistent_data(self, tmp_path):
        p, c = _db(tmp_path)
        _long_trade(c)
        rec = EQ.reconciliation_integrity(p, CT.build_dataset(p))
        assert rec["clean"] is True and rec["findings"] == []

    def test_reconciliation_reports_a_pnl_mismatch_without_repairing_it(self, tmp_path):
        p, c = _db(tmp_path)
        _long_trade(c)
        c.execute("UPDATE positions SET pnl=-99.0 WHERE id=1")
        c.commit()
        trades = CT.build_dataset(p)
        rec = EQ.reconciliation_integrity(p, trades)
        assert rec["clean"] is False
        assert rec["pnl_mismatches"][0]["positions_pnl"] == -99.0
        assert trades[0].realised_pnl == -99.0

    def test_reconciliation_flags_an_unprotected_automated_trade(self, tmp_path):
        p, c = _db(tmp_path)
        _long_trade(c, strategy="g3_x")
        c.execute("DELETE FROM protective_stops")
        c.commit()
        rec = EQ.reconciliation_integrity(p, CT.build_dataset(p))
        assert any("no protective stop" in f for f in rec["findings"])

    def test_risk_comparison_only_covers_stop_closed_trades(self, tmp_path):
        p, c = _db(tmp_path)
        _long_trade(c, entry=100000.0, exit_px=99000.0, trigger=99100.0)
        inf = EQ.infrastructure_evidence(p, CT.build_dataset(p))
        assert inf["realised_vs_configured_risk"]["comparable_trades"] == 1

    def test_supplied_budget_is_optional_and_only_used_for_comparison(self, tmp_path):
        p, c = _db(tmp_path)
        _long_trade(c, entry=100000.0, exit_px=99000.0, trigger=99100.0)
        without = EQ.infrastructure_evidence(p, CT.build_dataset(p))
        assert "supplied_budget_usd" not in without["realised_vs_configured_risk"]
        with_budget = EQ.infrastructure_evidence(
            p, CT.build_dataset(p), risk_budget_usd=0.5)
        assert len(with_budget["realised_vs_configured_risk"]
                   ["over_supplied_budget"]) == 1


class TestEvidenceClassesStaySeparate:
    def test_report_has_three_distinct_blocks(self, tmp_path):
        p, c = _db(tmp_path)
        _long_trade(c)
        rep = EQ.build_report(p)
        assert set(rep) == {"coverage", "infrastructure_evidence",
                            "descriptive_observations", "strategy_evidence"}

    def test_strategy_block_declares_insufficiency_and_carries_no_statistic(
            self, tmp_path):
        p, c = _db(tmp_path)
        _long_trade(c)
        s = EQ.build_report(p)["strategy_evidence"]
        assert s["sufficient_for_inference"] is False
        assert set(s) == {"automated_completed_trades_by_strategy",
                          "sufficient_for_inference", "notice"}

    def test_no_inferential_statistic_is_computed_anywhere(self):
        """No DSR/BH/Sharpe machinery in either module -- calls, not prose."""
        banned = {"deflated_sr", "psr", "bh_fdr", "bh_adjusted_p", "sharpe",
                  "t_stat", "pvalue", "p_value"}
        for name in ("completed_trades.py", "execution_quality.py"):
            src = _strip_docstrings(
                (_RESEARCH / "execution_evidence" / name).read_text(encoding="utf-8"))
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    fn = node.func
                    label = getattr(fn, "id", None) or getattr(fn, "attr", None)
                    assert (label or "").lower() not in banned, f"{name}: {label}"
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    mod = (getattr(node, "module", "") or "").lower()
                    assert "governance" not in mod and "dsr" not in mod

    def test_modules_never_open_a_database_for_writing(self):
        """Every connection is read-only, and no statement can mutate a row."""
        for name in ("completed_trades.py", "execution_quality.py"):
            src = _strip_docstrings(
                (_RESEARCH / "execution_evidence" / name).read_text(encoding="utf-8"))
            for verb in ("INSERT ", "UPDATE ", "DELETE ", "CREATE ", "DROP ",
                         "ALTER ", "REPLACE "):
                assert verb not in src.upper(), f"{name} contains {verb.strip()}"
            for node in ast.walk(ast.parse(src)):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                if getattr(fn, "attr", None) == "connect" and getattr(
                        getattr(fn, "value", None), "id", None) == "sqlite3":
                    literals = [a.value for a in node.args
                                if isinstance(a, ast.Constant)]
                    joined = " ".join(str(v) for v in literals)
                    assert "mode=ro" in joined or any(
                        isinstance(a, ast.JoinedStr) for a in node.args), (
                        f"{name}: sqlite3.connect without mode=ro")

    def test_only_completed_trades_opens_connections(self):
        """execution_quality routes every read through connect_ro."""
        src = _strip_docstrings(
            (_RESEARCH / "execution_evidence" / "execution_quality.py").read_text(
                encoding="utf-8"))
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Call):
                fn = node.func
                assert not (getattr(fn, "attr", None) == "connect"
                            and getattr(getattr(fn, "value", None), "id", None)
                            == "sqlite3"), "execution_quality opened its own handle"


def _strip_docstrings(src: str) -> str:
    """Source with docstrings removed, so prose cannot satisfy a code assertion."""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and ast.get_docstring(node):
            node.body = node.body[1:] or [ast.Pass()]
    return ast.unparse(tree)


_SLICE = pathlib.Path(
    os.environ.get("BTCBOT_EXEC_SLICE", "")) if os.environ.get(
        "BTCBOT_EXEC_SLICE") else None


@pytest.mark.skipif(_SLICE is None or not _SLICE.exists(),
                    reason="no production slice available (set BTCBOT_EXEC_SLICE)")
class TestAgainstProductionSlice:
    def test_builds_and_is_deterministic(self):
        a = CT.to_records(CT.build_dataset(_SLICE))
        b = CT.to_records(CT.build_dataset(_SLICE))
        assert a == b and len(a) > 0

    def test_reconciliation_is_clean_on_real_data(self):
        rec = EQ.reconciliation_integrity(_SLICE, CT.build_dataset(_SLICE))
        assert rec["clean"] is True, rec["findings"]

    def test_idempotency_is_clean_on_real_data(self):
        assert EQ.idempotency(_SLICE)["clean"] is True

    def test_known_automated_trade_reproduces(self):
        auto = [t for t in CT.build_dataset(_SLICE) if t.is_automated]
        assert len(auto) >= 1
        t = auto[0]
        assert t.strategy == "g3_momentum_tsmom"
        assert t.realised_pnl == pytest.approx(-1.01338508)
        assert t.exited_via == "protective_stop"
        assert t.exit_slippage_pts == pytest.approx(-14.0)
        assert t.mae_pts == pytest.approx(-922.5)


class TestSignalContextIsBoundedByAvailability:
    @pytest.fixture(autouse=True)
    def _tmp(self, tmp_path):
        self.tmp = tmp_path

    """`signal_log` day D is written at 01:00 UTC on D+1, not at D's close.

    The bound used to be the calendar (`day < entry date`), which leaks for an
    entry in the 00:00-01:00 UTC hour: day D-1 does not exist yet, so such an
    entry could only ever have seen D-2. `alignment.timebase` already declares
    the real delay; this is that rule applied here.
    """

    SIG = "g3_momentum_tsmom"

    _seq = 0

    def _fwd(self, tmp_path, days):
        TestSignalContextIsBoundedByAvailability._seq += 1
        db = tmp_path / f"fwd_sigctx_{self._seq}.db"
        c = sqlite3.connect(db)
        c.execute("CREATE TABLE signal_log (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                  " signal_id TEXT, day TEXT, position REAL, raw_value REAL,"
                  " run_id INTEGER)")
        for i, day in enumerate(days):
            c.execute("INSERT INTO signal_log (signal_id, day, position, raw_value,"
                      " run_id) VALUES (?,?,?,?,?)",
                      (self.SIG, day, 1.0, float(i), i))
        c.commit()
        con = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        return con

    def _day(self, tmp_path, entry_ts, days=("2026-08-30", "2026-08-31",
                                             "2026-09-01", "2026-09-02")):
        fwd = self._fwd(tmp_path, days)
        try:
            return CT._signal_context(fwd, self.SIG, entry_ts)[0]
        finally:
            fwd.close()

    def test_the_delay_comes_from_the_alignment_layer(self):
        from alignment.timebase import get_spec
        assert (CT.SIGNAL_AVAILABILITY_DELAY.total_seconds()
                == get_spec("signal_log").availability_offset_s == 25 * 3600)

    def test_an_entry_in_the_first_hour_selects_two_days_back(self):
        """00:30 on 09-02: the 09-01 row is written at 01:00 on 09-02."""
        assert self._day(tmp_path=self.tmp, entry_ts="2026-09-02T00:30:00+00:00") \
            == "2026-08-31"

    def test_an_entry_after_the_collector_selects_the_previous_day(self):
        assert self._day(tmp_path=self.tmp, entry_ts="2026-09-02T01:30:00+00:00") \
            == "2026-09-01"

    def test_the_availability_boundary_is_inclusive(self):
        """Available at exactly the entry instant was available."""
        assert self._day(tmp_path=self.tmp, entry_ts="2026-09-02T01:00:00+00:00") \
            == "2026-09-01"

    def test_one_microsecond_before_the_boundary_falls_back(self):
        assert self._day(tmp_path=self.tmp, entry_ts="2026-09-02T00:59:59.999999+00:00") \
            == "2026-08-31"

    def test_no_future_signal_row_can_be_selected(self):
        """Every row the corpus holds is later than this entry."""
        assert self._day(tmp_path=self.tmp, entry_ts="2026-08-30T12:00:00+00:00",
                         days=("2026-09-01", "2026-09-02")) is None

    def test_the_exit_day_signal_is_never_reached(self):
        day = self._day(tmp_path=self.tmp, entry_ts="2026-09-01T12:00:00+00:00")
        assert day == "2026-08-31", "an entry on 09-01 must not see 09-01 or later"

    def test_the_z_spelling_is_handled_like_the_offset_spelling(self):
        assert (self._day(tmp_path=self.tmp, entry_ts="2026-09-02T00:30:00Z")
                == self._day(tmp_path=self.tmp,
                             entry_ts="2026-09-02T00:30:00+00:00"))

    def test_an_unparseable_entry_ts_yields_nothing(self):
        fwd = self._fwd(self.tmp, ("2026-08-31",))
        try:
            assert CT._signal_context(fwd, self.SIG, "not-a-timestamp") == (
                None, None, None)
            assert CT._signal_context(fwd, None, "2026-09-02T12:00:00+00:00") == (
                None, None, None)
            assert CT._signal_context(None, self.SIG, "2026-09-02T12:00:00+00:00") == (
                None, None, None)
        finally:
            fwd.close()

    def test_afternoon_entries_are_unaffected_by_the_change(self):
        """The five trades already in the dataset must not move."""
        for entry_ts, expected in (
                ("2026-08-20T20:43:24.847514+00:00", "2026-08-19"),
                ("2026-08-24T14:27:53.740638+00:00", "2026-08-23"),
                ("2026-08-24T15:59:06.399434+00:00", "2026-08-23"),
                ("2026-08-25T06:44:52.593135+00:00", "2026-08-24"),
                ("2026-09-01T17:17:20.409050+00:00", "2026-08-31"),
        ):
            days = tuple(f"2026-08-{d:02d}" for d in range(15, 32)) + (
                "2026-09-01", "2026-09-02")
            assert self._day(tmp_path=self.tmp, entry_ts=entry_ts,
                             days=days) == expected, entry_ts

    def test_real_position_6_regression(self):
        """The order actually placed 2026-09-02T00:01:20Z.

        Its position entry_ts is 00:02:20.539657. Under the old calendar bound
        this resolved to 2026-09-01, a row written at 01:00 on 09-02 -- 58
        minutes after the order was submitted.
        """
        assert self._day(tmp_path=self.tmp,
                         entry_ts="2026-09-02T00:02:20.539657+00:00") == "2026-08-31"

    def test_stored_context_still_takes_precedence(self):
        """The fix touches the fallback only; a stored row still wins."""
        src = inspect.getsource(CT._build)
        stored = src.index("_stored_linkage")
        fallback = src.index("_signal_context")
        assert stored < fallback, "the stored row must be consulted first"
        assert "if stored is not None:" in src


