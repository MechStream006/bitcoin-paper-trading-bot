"""
Checkpoint 2 tests for the H-IVRV MVP.

Scope (the first shared-infrastructure change, isolated to H-IVRV):
  - run_signal aux_series extension (runner_base.py)
  - runner_ivrv.collect (runner_ivrv.py)

Run from research/:
    conda run -n btc_bot pytest forward_evidence/tests/test_ivrv_checkpoint2.py -v

These tests exercise ONLY the new aux path and the ivrv runner. The unchanged
None path (H5/G1/G3) is covered by the existing 59-test test_runner_base.py
suite, which must remain green (the regression gate).
"""
from __future__ import annotations

import json
import pathlib
import sqlite3

import numpy as np
import pandas as pd
import pytest

import forward_evidence.decision_function_ivrv as ivrv_df
from forward_evidence import runner_ivrv
from forward_evidence.init_forward_db import init_forward_db
from forward_evidence.pre_reg_guard import FnHashViolation, _sha256_lf
from forward_evidence.runner_base import DataUnavailableError, run_signal


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

def _dates(n: int, start: str = "2021-01-01") -> pd.Index:
    return pd.date_range(start, periods=n, freq="D").strftime("%Y-%m-%d")


def make_closes(n: int, seed: int = 7, vol: float = 0.04, start: float = 40_000.0) -> pd.Series:
    rng   = np.random.default_rng(seed)
    price = start * np.exp(np.cumsum(rng.normal(0, vol, n)))
    return pd.Series(price, index=_dates(n), dtype=float)


def make_dvol(n: int, seed: int = 11, level: float = 60.0, vol: float = 0.05) -> pd.Series:
    rng = np.random.default_rng(seed)
    s   = level * np.exp(np.cumsum(rng.normal(0, vol, n)))
    return pd.Series(s, index=_dates(n), dtype=float)


def _write_prereg(tmp_path: pathlib.Path, fn_file: pathlib.Path, signal_id: str) -> pathlib.Path:
    data = {
        "schema_version": 1,
        "version": "1.0.0",
        "signal_id": signal_id,
        "pre_registration_date": "2026-06-19T00:00:00Z",
        "function_spec": {"decision_function_hash": _sha256_lf(fn_file)},
    }
    p = tmp_path / f"prereg_{signal_id}.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _counts(db: pathlib.Path) -> dict:
    c = sqlite3.connect(db)
    try:
        return {
            "run_log":       c.execute("SELECT COUNT(*) FROM run_log").fetchone()[0],
            "signal_log":    c.execute("SELECT COUNT(*) FROM signal_log").fetchone()[0],
            "trade_entries": c.execute("SELECT COUNT(*) FROM trade_entries").fetchone()[0],
            "trade_exits":   c.execute("SELECT COUNT(*) FROM trade_exits").fetchone()[0],
        }
    finally:
        c.close()


def _signal_row(db: pathlib.Path) -> tuple:
    c = sqlite3.connect(db)
    try:
        return c.execute("SELECT position, raw_value FROM signal_log").fetchone()
    finally:
        c.close()


@pytest.fixture()
def db_path(tmp_path: pathlib.Path) -> pathlib.Path:
    p = tmp_path / "evidence_forward.db"
    init_forward_db(p)
    return p


@pytest.fixture()
def aux_fn_file(tmp_path: pathlib.Path) -> pathlib.Path:
    """A throwaway 'decision function' file; content only needs to hash stably."""
    f = tmp_path / "decision_function_auxtest.py"
    f.write_bytes(b"def compute_signal(closes, aux):\n    return closes * 0.0\n")
    return f


# ===========================================================================
# Group AUX — run_signal aux_series extension (direct)
# ===========================================================================

def test_AUX01_none_path_single_arg_unchanged(db_path, aux_fn_file, tmp_path):
    """aux omitted -> single-arg compute/raw_value path works exactly as before."""
    pre = _write_prereg(tmp_path, aux_fn_file, "aux_none")
    closes = make_closes(30)

    def compute(c):
        s = pd.Series(0.0, index=c.index); s.iloc[-1] = 1.0; return s

    def raw(c):
        return 1.23

    out = run_signal(
        signal_id="aux_none", run_date=closes.index[-1], closes=closes,
        compute_fn=compute, raw_value_fn=raw, pre_reg_path=pre,
        fn_file_path=aux_fn_file, db_path=db_path,
        hold_days=7, min_history=5, cost_bps=15.0,
    )
    assert out == "ok"
    assert _counts(db_path) == {"run_log": 1, "signal_log": 1, "trade_entries": 1, "trade_exits": 0}
    pos, raw_v = _signal_row(db_path)
    assert pos == 1.0 and raw_v == 1.23


def test_AUX02_aux_drives_signal_and_raw_value(db_path, aux_fn_file, tmp_path):
    """aux provided -> compute_fn/raw_value_fn receive it as 2nd arg."""
    pre = _write_prereg(tmp_path, aux_fn_file, "aux_drive")
    closes = make_closes(30)
    dvol   = make_dvol(30)

    def compute(c, a):
        v = a.reindex(c.index).iloc[-1]
        s = pd.Series(0.0, index=c.index)
        s.iloc[-1] = 1.0 if v > 0 else -1.0
        return s

    def raw(c, a):
        return float(a.iloc[-1])

    out = run_signal(
        signal_id="aux_drive", run_date=closes.index[-1], closes=closes,
        compute_fn=compute, raw_value_fn=raw, pre_reg_path=pre,
        fn_file_path=aux_fn_file, db_path=db_path,
        hold_days=7, min_history=5, cost_bps=15.0, aux_series=dvol,
    )
    assert out == "ok"
    pos, raw_v = _signal_row(db_path)
    assert pos == 1.0                              # DVOL level > 0
    assert raw_v == pytest.approx(float(dvol.iloc[-1]))
    assert _counts(db_path)["trade_entries"] == 1


def test_AUX03_aux_lookahead_filtered_to_run_date(db_path, aux_fn_file, tmp_path):
    """aux is sliced to <= run_date before reaching compute_fn (no look-ahead)."""
    pre = _write_prereg(tmp_path, aux_fn_file, "aux_la")
    closes = make_closes(40)
    dvol   = make_dvol(40)
    run_date = closes.index[20]                    # mid-series; aux extends beyond
    captured = {}

    def compute(c, a):
        captured["aux_max"]    = a.index.max()
        captured["closes_max"] = c.index.max()
        return pd.Series(0.0, index=c.index)

    def raw(c, a):
        return None

    out = run_signal(
        signal_id="aux_la", run_date=run_date, closes=closes,
        compute_fn=compute, raw_value_fn=raw, pre_reg_path=pre,
        fn_file_path=aux_fn_file, db_path=db_path,
        hold_days=7, min_history=5, cost_bps=15.0, aux_series=dvol,
    )
    assert out == "ok"
    assert captured["aux_max"] == run_date          # aux did NOT leak future rows
    assert captured["closes_max"] == run_date


def test_AUX04_aux_missing_run_date_raises_and_writes_nothing(db_path, aux_fn_file, tmp_path):
    pre = _write_prereg(tmp_path, aux_fn_file, "aux_missing")
    closes = make_closes(30)
    dvol   = make_dvol(30).iloc[:-5]                # aux missing the last 5 dates

    def compute(c, a):  # pragma: no cover - must never be reached
        raise AssertionError("compute_fn must not run when aux is unavailable")

    def raw(c, a):      # pragma: no cover
        return None

    with pytest.raises(DataUnavailableError):
        run_signal(
            signal_id="aux_missing", run_date=closes.index[-1], closes=closes,
            compute_fn=compute, raw_value_fn=raw, pre_reg_path=pre,
            fn_file_path=aux_fn_file, db_path=db_path,
            hold_days=7, min_history=5, cost_bps=15.0, aux_series=dvol,
        )
    # Transient: nothing persisted (no run_log row -> auto-retry next run)
    assert _counts(db_path) == {"run_log": 0, "signal_log": 0, "trade_entries": 0, "trade_exits": 0}


def test_AUX05_aux_nan_at_run_date_raises(db_path, aux_fn_file, tmp_path):
    pre = _write_prereg(tmp_path, aux_fn_file, "aux_nan")
    closes = make_closes(30)
    dvol   = make_dvol(30)
    dvol.iloc[-1] = np.nan                          # present index, NaN value

    with pytest.raises(DataUnavailableError):
        run_signal(
            signal_id="aux_nan", run_date=closes.index[-1], closes=closes,
            compute_fn=lambda c, a: pd.Series(0.0, index=c.index),
            raw_value_fn=lambda c, a: None, pre_reg_path=pre,
            fn_file_path=aux_fn_file, db_path=db_path,
            hold_days=7, min_history=5, cost_bps=15.0, aux_series=dvol,
        )
    assert _counts(db_path)["run_log"] == 0


# ===========================================================================
# Group IVRV — runner_ivrv.collect
# ===========================================================================

@pytest.fixture()
def ivrv_prereg(tmp_path: pathlib.Path, monkeypatch) -> pathlib.Path:
    """Temp pre-reg whose hash matches the REAL decision_function_ivrv.py."""
    pre = _write_prereg(tmp_path, runner_ivrv._FN_FILE, runner_ivrv.SIGNAL_ID)
    monkeypatch.setattr(runner_ivrv, "_PRE_REG", pre)
    return pre


def test_IVRV01_collect_ok_writes_rows(db_path, ivrv_prereg):
    n = 320
    closes, dvol = make_closes(n), make_dvol(n)
    out = runner_ivrv.collect(closes.index[-1], closes, dvol, db_path, 15.0)
    assert out == "ok"
    counts = _counts(db_path)
    assert counts["run_log"] == 1
    assert counts["signal_log"] == 1
    pos, _ = _signal_row(db_path)
    assert pos in (-1.0, 0.0, 1.0)
    # A trade opens iff the signal fired
    assert counts["trade_entries"] == (1 if pos != 0.0 else 0)


def test_IVRV02_idempotent_rerun_skips(db_path, ivrv_prereg):
    n = 320
    closes, dvol = make_closes(n), make_dvol(n)
    rd = closes.index[-1]
    assert runner_ivrv.collect(rd, closes, dvol, db_path, 15.0) == "ok"
    before = _counts(db_path)
    assert runner_ivrv.collect(rd, closes, dvol, db_path, 15.0) == "skipped"
    assert _counts(db_path) == before              # no duplicate rows


def test_IVRV03_fn_hash_violation_raises(db_path, tmp_path, monkeypatch):
    bad = tmp_path / "prereg_bad.json"
    bad.write_text(json.dumps({
        "schema_version": 1, "version": "1.0.0", "signal_id": runner_ivrv.SIGNAL_ID,
        "pre_registration_date": "2026-06-19T00:00:00Z",
        "function_spec": {"decision_function_hash": "00" * 32},
    }), encoding="utf-8")
    monkeypatch.setattr(runner_ivrv, "_PRE_REG", bad)
    n = 320
    closes, dvol = make_closes(n), make_dvol(n)
    with pytest.raises(FnHashViolation):
        runner_ivrv.collect(closes.index[-1], closes, dvol, db_path, 15.0)


def test_IVRV04_dvol_unavailable_propagates(db_path, ivrv_prereg):
    n = 320
    closes = make_closes(n)
    dvol   = make_dvol(n).iloc[:-3]                 # missing run_date
    with pytest.raises(DataUnavailableError):
        runner_ivrv.collect(closes.index[-1], closes, dvol, db_path, 15.0)
    assert _counts(db_path)["run_log"] == 0         # transient skip, nothing written


def test_IVRV05_insufficient_history_propagates(db_path, ivrv_prereg):
    n = 100                                          # < MIN_HISTORY (253)
    closes, dvol = make_closes(n), make_dvol(n)
    with pytest.raises(DataUnavailableError):
        runner_ivrv.collect(closes.index[-1], closes, dvol, db_path, 15.0)


def test_IVRV05b_short_dvol_with_sufficient_closes_is_a_skip(db_path, ivrv_prereg):
    """
    Gate 6A.11 regression on the IVRV path. IVRV shares run_signal's aux
    handling with G2, so the same pre-fix gap applied here: the Step 3 length
    check covered `closes` only, and long closes + a short DVOL series reached
    compute_fn, persisting a warmup 0.0 as a decision.

    IVRV05 above shortens BOTH series, so the closes check trips first and the
    aux path is never reached — this case is what actually exercises it.
    """
    n = 320
    closes = make_closes(n)
    short_dvol = make_dvol(n).iloc[-40:]              # 40 rows << MIN_HISTORY

    with pytest.raises(DataUnavailableError, match="Insufficient auxiliary history"):
        runner_ivrv.collect(closes.index[-1], closes, short_dvol, db_path, 15.0)

    assert _counts(db_path)["run_log"] == 0           # transient, nothing written


def test_IVRV05c_sufficient_dvol_still_collects(db_path, ivrv_prereg):
    """The fix must not change behaviour when DVOL history IS sufficient."""
    n = 320
    closes, dvol = make_closes(n), make_dvol(n)
    assert runner_ivrv.collect(closes.index[-1], closes, dvol, db_path, 15.0) == "ok"
    assert _counts(db_path)["run_log"] == 1


def test_IVRV06_raw_value_is_current_vrp(db_path, ivrv_prereg):
    """signal_log.raw_value should equal DVOL/100 - RV30 at run_date."""
    n = 320
    closes, dvol = make_closes(n), make_dvol(n)
    runner_ivrv.collect(closes.index[-1], closes, dvol, db_path, 15.0)
    _, raw_v = _signal_row(db_path)
    # recompute expected VRP at the tail
    log_ret   = np.log(closes / closes.shift(1))
    rv30      = log_ret.rolling(ivrv_df.RV_WIN, min_periods=ivrv_df.RV_WIN).std() * np.sqrt(ivrv_df.ANN)
    expected  = float((dvol.reindex(closes.index) / 100.0 - rv30).iloc[-1])
    assert raw_v == pytest.approx(expected)
