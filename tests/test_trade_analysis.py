"""Tests for the read-only forward-trade research surface."""
from __future__ import annotations

import ast
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_FWD = ROOT / "research" / "forward_evidence"
for _p in (ROOT / "research", _FWD):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import trade_analysis as TA  # noqa: E402


def _db(tmp_path, rows):
    """rows: (signal, entry, exit, direction, entry_px, exit_px)"""
    db = tmp_path / "evidence_forward.db"
    con = sqlite3.connect(db)
    con.executescript((_FWD / "schema_forward.sql").read_text(encoding="utf-8"))
    for sig, ent, ex, d, ep, xp in rows:
        con.execute("INSERT INTO trade_entries (signal_id, entry_date, direction,"
                    " entry_close_price, planned_exit_date, open) VALUES (?,?,?,?,?,0)",
                    (sig, ent, d, ep, ex))
        eid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        con.execute("INSERT INTO trade_exits (entry_id, exit_date, exit_close_price,"
                    " cost_bps) VALUES (?,?,?,?)", (eid, ex, xp, 15.0))
        con.execute("INSERT INTO run_log (signal_id, run_date, fn_hash, status)"
                    " VALUES (?,?,?, 'ok')", (sig, ent, "h"))
    con.commit(); con.close()
    return db


def _ohlcv(days, high, low, close):
    import pandas as pd
    return pd.DataFrame({"high": high, "low": low, "close": close}, index=days)


class TestLoading:
    def test_sequence_and_fields(self, tmp_path):
        db = _db(tmp_path, [("f", "2026-06-01", "2026-06-08", 1, 100.0, 110.0),
                            ("f", "2026-06-08", "2026-06-15", -1, 110.0, 100.0)])
        ts = TA.load_trades(db)
        assert [t.seq for t in ts] == [1, 2]
        assert ts[0].direction == 1 and ts[1].direction == -1
        assert ts[0].hold_days == 7
        # net = (110/100-1)*1 - 0.0015
        assert ts[0].net_return == pytest.approx(0.10 - 0.0015)
        assert ts[1].net_return == pytest.approx((100/110 - 1) * -1 - 0.0015)

    def test_weekday_properties(self, tmp_path):
        db = _db(tmp_path, [("f", "2026-06-15", "2026-06-22", 1, 100.0, 101.0)])
        t = TA.load_trades(db)[0]
        assert t.entry_dow == "Mon" and t.exit_dow == "Mon"

    def test_open_trades_are_structurally_absent(self, tmp_path):
        db = _db(tmp_path, [("f", "2026-06-01", "2026-06-08", 1, 100.0, 110.0)])
        con = sqlite3.connect(db)
        con.execute("INSERT INTO trade_entries (signal_id, entry_date, direction,"
                    " entry_close_price, planned_exit_date, open) VALUES"
                    " ('f','2026-07-01',1,100.0,'2026-07-08',1)")
        con.commit(); con.close()
        assert len(TA.load_trades(db)) == 1

    def test_family_filter(self, tmp_path):
        db = _db(tmp_path, [("a", "2026-06-01", "2026-06-08", 1, 100.0, 110.0),
                            ("b", "2026-06-01", "2026-06-08", 1, 100.0, 110.0)])
        assert {t.signal_id for t in TA.load_trades(db, "a")} == {"a"}


class TestExcursions:
    def test_long_uses_high_for_mfe_and_low_for_mae(self, tmp_path):
        db = _db(tmp_path, [("f", "2026-06-01", "2026-06-03", 1, 100.0, 102.0)])
        ts = TA.load_trades(db)
        o = _ohlcv(["2026-06-01", "2026-06-02", "2026-06-03"],
                   high=[999.0, 120.0, 105.0], low=[0.1, 95.0, 99.0],
                   close=[100.0, 110.0, 102.0])
        assert TA.enrich_excursions(ts, o) == 1
        t = ts[0]
        # entry bar excluded: 999/0.1 must NOT leak in
        assert t.mfe == pytest.approx(0.20)     # high 120 -> +20%
        assert t.mae == pytest.approx(-0.05)    # low 95  -> -5%
        assert t.mfe_close == pytest.approx(0.10)
        assert t.mae_close == pytest.approx(0.02)

    def test_short_inverts_favourable_and_adverse(self, tmp_path):
        db = _db(tmp_path, [("f", "2026-06-01", "2026-06-03", -1, 100.0, 98.0)])
        ts = TA.load_trades(db)
        o = _ohlcv(["2026-06-01", "2026-06-02", "2026-06-03"],
                   high=[100.0, 110.0, 99.0], low=[100.0, 90.0, 97.0],
                   close=[100.0, 95.0, 98.0])
        TA.enrich_excursions(ts, o)
        t = ts[0]
        assert t.mfe == pytest.approx(0.10)     # low 90 is the short's best
        assert t.mae == pytest.approx(-0.10)    # high 110 is its worst

    def test_entry_bar_is_excluded(self, tmp_path):
        db = _db(tmp_path, [("f", "2026-06-01", "2026-06-02", 1, 100.0, 100.0)])
        ts = TA.load_trades(db)
        o = _ohlcv(["2026-06-01", "2026-06-02"], high=[500.0, 100.0],
                   low=[1.0, 100.0], close=[100.0, 100.0])
        TA.enrich_excursions(ts, o)
        assert ts[0].mfe == pytest.approx(0.0) and ts[0].mae == pytest.approx(0.0)

    def test_incomplete_window_leaves_none_not_a_partial_figure(self, tmp_path):
        db = _db(tmp_path, [("f", "2026-06-01", "2026-06-05", 1, 100.0, 102.0)])
        ts = TA.load_trades(db)
        o = _ohlcv(["2026-06-02", "2026-06-03"], [110.0, 110.0], [90.0, 90.0], [100.0, 100.0])
        assert TA.enrich_excursions(ts, o) == 0
        assert ts[0].mfe is None and ts[0].mae is None

    def test_edge_captured_is_none_when_no_favourable_excursion(self, tmp_path):
        db = _db(tmp_path, [("f", "2026-06-01", "2026-06-02", 1, 100.0, 95.0)])
        ts = TA.load_trades(db)
        o = _ohlcv(["2026-06-01", "2026-06-02"], [100.0, 96.0], [100.0, 94.0], [100.0, 95.0])
        TA.enrich_excursions(ts, o)
        assert ts[0].mfe < 0 and ts[0].edge_captured is None

    def test_excursions_are_gross_not_net(self, tmp_path):
        """net_return carries the 15bps cost; the excursion must not."""
        db = _db(tmp_path, [("f", "2026-06-01", "2026-06-02", 1, 100.0, 110.0)])
        ts = TA.load_trades(db)
        o = _ohlcv(["2026-06-01", "2026-06-02"], [100.0, 110.0], [100.0, 110.0],
                   [100.0, 110.0])
        TA.enrich_excursions(ts, o)
        assert ts[0].mfe == pytest.approx(0.10)
        assert ts[0].net_return == pytest.approx(0.10 - 0.0015)


class TestSegmentationAndAnchoring:
    GAPS = [("2026-07-18", "2026-08-05")]

    def _seven_day_chain(self, tmp_path):
        # Mondays before the gap, Thursdays after -- the real shape
        rows = [("f", d, (date.fromisoformat(d) + timedelta(days=7)).isoformat(),
                 -1, 100.0, 101.0)
                for d in ("2026-06-15", "2026-06-22", "2026-06-29", "2026-07-06",
                          "2026-07-13", "2026-08-06", "2026-08-13")]
        return _db(tmp_path, rows)

    def test_segments_label_but_never_drop(self, tmp_path):
        ts = TA.load_trades(self._seven_day_chain(tmp_path))
        TA.assign_segments(ts, self.GAPS)
        assert len(ts) == 7, "no trade is removed"
        assert [t.segment for t in ts] == ["pre"] * 5 + ["post"] * 2

    def test_reanchor_is_quantified(self, tmp_path):
        ts = TA.load_trades(self._seven_day_chain(tmp_path))
        TA.assign_segments(ts, self.GAPS)
        a = TA.weekday_anchoring(ts)
        assert a["segments"]["pre"]["anchor"] == "Mon"
        assert a["segments"]["post"]["anchor"] == "Thu"
        assert a["segments"]["pre"]["anchor_purity"] == 1.0
        assert a["reanchor"]["shift_days"] == 3
        assert a["reanchor"]["confounded"] is True

    def test_no_reanchor_key_without_both_segments(self, tmp_path):
        db = _db(tmp_path, [("f", "2026-06-15", "2026-06-22", 1, 100.0, 101.0)])
        ts = TA.load_trades(db)
        TA.assign_segments(ts, self.GAPS)
        assert "reanchor" not in TA.weekday_anchoring(ts)

    def test_gaps_come_from_frozen_policy_not_this_module(self):
        gaps = TA.declared_gaps()
        assert ("2026-07-18", "2026-08-05") in gaps
        src = (_FWD / "trade_analysis.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        for n in ast.walk(tree):
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and \
                    ast.get_docstring(n):
                n.body = n.body[1:] or [ast.Pass()]
        code = ast.unparse(tree)
        assert "2026-07-18" not in code and "2026-08-05" not in code


class TestQualityFindings:
    def test_clean_corpus_yields_no_findings(self, tmp_path):
        db = _db(tmp_path, [("f", "2026-06-01", "2026-06-08", 1, 100.0, 110.0),
                            ("f", "2026-06-08", "2026-06-15", 1, 110.0, 111.0)])
        ts = TA.load_trades(db)
        assert TA.quality_findings(db, ts, []) == []

    def test_overlap_is_reported(self, tmp_path):
        db = _db(tmp_path, [("f", "2026-06-01", "2026-06-08", 1, 100.0, 110.0),
                            ("f", "2026-06-05", "2026-06-12", 1, 105.0, 111.0)])
        ts = TA.load_trades(db)
        assert any("overlapping" in f for f in TA.quality_findings(db, ts, []))

    def test_entry_inside_an_immutable_gap_is_reported(self, tmp_path):
        db = _db(tmp_path, [("f", "2026-07-20", "2026-07-27", 1, 100.0, 110.0)])
        ts = TA.load_trades(db)
        found = TA.quality_findings(db, ts, [("2026-07-18", "2026-08-05")])
        assert any("ENTERED inside the immutable gap" in f for f in found)

    def test_mixed_hold_days_is_reported(self, tmp_path):
        db = _db(tmp_path, [("f", "2026-06-01", "2026-06-08", 1, 100.0, 110.0),
                            ("f", "2026-06-10", "2026-06-15", 1, 110.0, 111.0)])
        ts = TA.load_trades(db)
        assert any("mixed hold_days" in f for f in TA.quality_findings(db, ts, []))


class TestReportAndBoundaries:
    def test_report_shape_without_minima(self, tmp_path):
        db = _db(tmp_path, [("f", "2026-06-15", "2026-06-22", 1, 100.0, 110.0)])
        rep = TA.build_report(db, parquet_dir=None)
        assert rep["n_trades"] == 1
        fam = rep["families"]["f"]
        assert fam["minimum"] is None and fam["short_by"] is None
        assert fam["entry_exit_dow"] == {"Mon->Mon": 1}

    def test_minimum_is_display_only_and_never_defaulted(self, tmp_path):
        db = _db(tmp_path, [("f", "2026-06-15", "2026-06-22", 1, 100.0, 110.0)])
        rep = TA.build_report(db, parquet_dir=None, minima={"f": 10})
        assert rep["families"]["f"]["short_by"] == 9

    def test_no_minimum_table_in_the_module(self):
        """FDB-8/OD-3: a minimum is operator-supplied, never defaulted here."""
        src = (_FWD / "trade_analysis.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        for n in ast.walk(tree):
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and \
                    ast.get_docstring(n):
                n.body = n.body[1:] or [ast.Pass()]
        code = ast.unparse(tree)
        for fam in ("h5_vol_regime", "g1_market_regime_sma200", "g3_atr_expansion",
                    "ivrv_vrp_spread", "g2_funding_level"):
            assert fam not in code, f"{fam} must not be named in executable code"

    def test_computes_no_gating_statistic(self):
        src = (_FWD / "trade_analysis.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        for n in ast.walk(tree):
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and \
                    ast.get_docstring(n):
                n.body = n.body[1:] or [ast.Pass()]
        # Check CALLS and IMPORTS, not prose: the CLI description legitimately
        # says "no DSR/BH", and naming a method to disclaim it is the opposite
        # of using it.
        banned = {"deflated_sr", "psr", "bh_fdr", "bh_adjusted_p",
                  "continuous_correlation", "high_pairs"}
        called = {n.func.id for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        called |= {n.func.attr for n in ast.walk(tree)
                   if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        assert not (called & banned), f"calls a gating statistic: {called & banned}"

        imported = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                imported |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module:
                imported.add(n.module.split(".")[0])
            if isinstance(n, ast.ImportFrom):
                imported |= {a.name for a in n.names}
        for mod in ("governance", "forward_dsr_bh", "compute_dsr_for_registration",
                    "run_harmonization"):
            assert mod not in imported, f"must not import {mod}"

    def test_writes_nothing(self, tmp_path):
        db = _db(tmp_path, [("f", "2026-06-15", "2026-06-22", 1, 100.0, 110.0)])
        before = db.read_bytes()
        TA.build_report(db, parquet_dir=None)
        assert db.read_bytes() == before
        src = (_FWD / "trade_analysis.py").read_text(encoding="utf-8")
        for banned in ("INSERT", "UPDATE", "DELETE", "append_run", "write_text("):
            assert banned not in src, banned

    def test_cli_runs_read_only(self, tmp_path, capsys):
        db = _db(tmp_path, [("f", "2026-06-15", "2026-06-22", 1, 100.0, 110.0)])
        assert TA.main(["--forward-db", str(db), "--no-excursions"]) == 0
        assert "FORWARD TRADE EVIDENCE" in capsys.readouterr().out

    def test_output_is_ascii_safe(self):
        """Windows consoles are cp1252; the report must render on both hosts."""
        src = (_FWD / "trade_analysis.py").read_text(encoding="utf-8")
        assert all(ord(c) < 128 for c in src)


class TestExcursionCoverage:
    def test_every_trade_enriches_when_the_frame_spans_the_history(self, tmp_path):
        """Regression: min_rows is a LOOKBACK in load_daily_ohlcv, so a report
        must size it from the trade span. Under-loading reads as 'no excursion'
        rather than 'not loaded' -- silent, and wrong."""
        rows, days, hi, lo, cl = [], [], [], [], []
        d0 = date(2026, 6, 15)
        for i in range(6):
            e = (d0 + timedelta(days=7 * i)).isoformat()
            x = (d0 + timedelta(days=7 * i + 7)).isoformat()
            rows.append(("f", e, x, 1, 100.0, 101.0))
        for i in range((date(2026, 7, 27) - d0).days + 1):
            days.append((d0 + timedelta(days=i)).isoformat())
            hi.append(110.0); lo.append(90.0); cl.append(100.0)
        db = _db(tmp_path, rows)
        ts = TA.load_trades(db)
        assert TA.enrich_excursions(ts, _ohlcv(days, hi, lo, cl)) == len(ts)
        assert all(t.mfe is not None for t in ts)

    def test_partially_covering_frame_enriches_only_what_it_covers(self, tmp_path):
        db = _db(tmp_path, [("f", "2026-06-15", "2026-06-22", 1, 100.0, 101.0),
                            ("f", "2026-07-06", "2026-07-13", 1, 100.0, 101.0)])
        ts = TA.load_trades(db)
        days = [(date(2026, 7, 1) + timedelta(days=i)).isoformat() for i in range(20)]
        n = TA.enrich_excursions(ts, _ohlcv(days, [110.0]*20, [90.0]*20, [100.0]*20))
        assert n == 1
        assert ts[0].mfe is None and ts[1].mfe is not None
