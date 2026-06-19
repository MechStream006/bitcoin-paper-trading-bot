"""
Checkpoint 1 tests for the H-IVRV MVP.

Scope (additive only — no shared infrastructure changed):
  Step 1 — forward_evidence/decision_function_ivrv.py  (compute_signal, variants)
  Step 2 — forward_evidence/runner_base.load_daily_dvol

Run from research/:
    conda run -n btc_bot pytest forward_evidence/tests/test_ivrv_checkpoint1.py -v

These tests do NOT touch run_signal, the existing runners, or orchestration.
They validate the standalone decision-function logic (correctness, NaN/warmup
handling, anti-lookahead) and the new DVOL loader (alignment, errors).
"""
from __future__ import annotations

import pathlib

import numpy as np
import pandas as pd
import pytest

import forward_evidence.decision_function_ivrv as df
from forward_evidence.runner_base import DataUnavailableError, load_daily_dvol


# ===========================================================================
# Helpers
# ===========================================================================

def _dates(n: int, start: str = "2021-01-01") -> pd.Index:
    return pd.date_range(start, periods=n, freq="D").strftime("%Y-%m-%d")


def make_closes(n: int, start: float = 40_000.0, vol: float = 0.04,
                seed: int = 7) -> pd.Series:
    """n daily closes, geometric random walk, 'YYYY-MM-DD' index."""
    rng   = np.random.default_rng(seed)
    rets  = rng.normal(0, vol, n)
    price = start * np.exp(np.cumsum(rets))
    return pd.Series(price, index=_dates(n), dtype=float)


def make_dvol(n: int, level: float = 60.0, vol: float = 0.05,
              seed: int = 11) -> pd.Series:
    """n daily DVOL points (vol points), 'YYYY-MM-DD' index."""
    rng    = np.random.default_rng(seed)
    series = level * np.exp(np.cumsum(rng.normal(0, vol, n)))
    return pd.Series(series, index=_dates(n), dtype=float)


def write_dvol_partitions(base_dir: pathlib.Path, dvol: pd.Series) -> pathlib.Path:
    """
    Write a DVOL series into the lake layout dvol/BTC/year=Y/month=MM/data.parquet.
    Returns the dvol/BTC directory (what load_daily_dvol expects as parquet_dir).
    """
    out_root = base_dir / "dvol" / "BTC"
    ts = pd.to_datetime(dvol.index, utc=True)
    frame = pd.DataFrame({
        "open_time": (ts.view("int64") // 1_000_000).astype("int64"),  # ns -> ms
        "open":  dvol.to_numpy(float),
        "high":  dvol.to_numpy(float),
        "low":   dvol.to_numpy(float),
        "close": dvol.to_numpy(float),
    })
    frame["year"]  = ts.year
    frame["month"] = ts.month
    for (y, m), grp in frame.groupby(["year", "month"]):
        part = out_root / f"year={y}" / f"month={m:02d}"
        part.mkdir(parents=True, exist_ok=True)
        grp.drop(columns=["year", "month"]).to_parquet(part / "data.parquet", index=False)
    return out_root


# ===========================================================================
# Step 1 — decision_function_ivrv.compute_signal
# ===========================================================================

def test_constants_locked():
    assert df.PCTILE == 33
    assert df.RW == 252
    assert df.RV_WIN == 30
    assert df.ANN == 365.0
    assert df.MINP == 126
    assert df.HOLD_DAYS == 7
    assert df.MIN_HISTORY == df.RW + 1 == 253


def test_output_shape_and_domain():
    n = 400
    closes = make_closes(n)
    dvol   = make_dvol(n)
    pos = df.compute_signal(closes, dvol)
    assert isinstance(pos, pd.Series)
    assert len(pos) == n
    assert list(pos.index) == list(closes.index)
    assert set(pd.unique(pos.to_numpy())).issubset({-1.0, 0.0, 1.0})


def test_warmup_is_flat():
    """Before enough history, rv30/quantiles are NaN -> position must be 0.0."""
    n = 400
    closes = make_closes(n)
    dvol   = make_dvol(n)
    pos = df.compute_signal(closes, dvol)
    # rv30 needs RV_WIN rows; quantile needs MINP non-NaN vrp. First ~RV_WIN rows
    # are guaranteed flat.
    assert (pos.iloc[: df.RV_WIN] == 0.0).all()
    # And at least one non-flat signal should eventually appear on a 400-row series.
    assert (pos != 0.0).any()


def test_nan_dvol_rows_are_flat():
    n = 400
    closes = make_closes(n)
    dvol   = make_dvol(n)
    dvol.iloc[300:310] = np.nan          # inject a DVOL gap
    pos = df.compute_signal(closes, dvol)
    assert (pos.iloc[300:310] == 0.0).all()


def test_dvol_reindexed_to_closes():
    """DVOL provided on a superset index must align to closes (subset) cleanly."""
    n = 400
    closes = make_closes(n)
    dvol_big = make_dvol(n + 50)          # longer index, same start
    pos = df.compute_signal(closes, dvol_big)
    assert list(pos.index) == list(closes.index)
    assert set(pd.unique(pos.to_numpy())).issubset({-1.0, 0.0, 1.0})


def test_anti_lookahead_prefix_stability():
    """
    The position at date t must depend only on data up to t. Truncating the
    series at t must not change the position at any date <= t.
    """
    n = 400
    closes = make_closes(n)
    dvol   = make_dvol(n)
    full = df.compute_signal(closes, dvol)

    cut = 360  # a date well past warmup
    truncated = df.compute_signal(closes.iloc[: cut + 1], dvol.iloc[: cut + 1])
    # Every position up to and including `cut` must match the full-series result.
    pd.testing.assert_series_equal(
        truncated, full.iloc[: cut + 1], check_names=False
    )


def test_matches_engine_vrp_spread_logic():
    """
    Numerical parity with the engine's HV-2 construction, recomputed inline from
    the same primitives (rv30, dvol_frac, vrp, rolling terciles). This guards
    against drift between the locked fn and engine.load_master / sig_vrp_spread.
    """
    n = 400
    closes = make_closes(n)
    dvol   = make_dvol(n)

    log_ret   = np.log(closes / closes.shift(1))
    rv30      = log_ret.rolling(df.RV_WIN, min_periods=df.RV_WIN).std() * np.sqrt(df.ANN)
    dvol_frac = dvol.reindex(closes.index) / 100.0
    vrp       = dvol_frac - rv30
    lo = vrp.rolling(df.RW, min_periods=min(df.RW, df.MINP)).quantile(df.PCTILE / 100.0)
    hi = vrp.rolling(df.RW, min_periods=min(df.RW, df.MINP)).quantile((100 - df.PCTILE) / 100.0)
    expected = pd.Series(0.0, index=closes.index)
    expected[vrp > hi] = 1.0
    expected[vrp < lo] = -1.0
    expected[vrp.isna() | lo.isna() | hi.isna()] = 0.0

    pd.testing.assert_series_equal(
        df.compute_signal(closes, dvol), expected, check_names=False
    )


def test_sensitivity_variants_shape():
    variants = df.sensitivity_variants()
    assert isinstance(variants, list) and len(variants) == 3
    for v in variants:
        assert "label" in v and "PCTILE" in v and "RW" in v


# ===========================================================================
# Step 2 — runner_base.load_daily_dvol
# ===========================================================================

def test_load_daily_dvol_roundtrip(tmp_path):
    dvol = make_dvol(300)
    dvol_dir = write_dvol_partitions(tmp_path, dvol)
    through = dvol.index[-1]
    out = load_daily_dvol(dvol_dir, through_date=through, min_rows=200)
    assert isinstance(out, pd.Series)
    assert out.index[-1] == through
    assert len(out) >= 200
    # Values preserved (compare on the overlap)
    np.testing.assert_allclose(out.loc[through], dvol.loc[through], rtol=1e-9)


def test_load_daily_dvol_index_is_date_strings(tmp_path):
    dvol = make_dvol(200)
    dvol_dir = write_dvol_partitions(tmp_path, dvol)
    out = load_daily_dvol(dvol_dir, through_date=dvol.index[-1], min_rows=150)
    # 'YYYY-MM-DD' strings, sorted ascending
    assert all(isinstance(i, str) and len(i) == 10 for i in out.index)
    assert list(out.index) == sorted(out.index)


def test_load_daily_dvol_missing_through_date_raises(tmp_path):
    dvol = make_dvol(200)
    dvol_dir = write_dvol_partitions(tmp_path, dvol)
    missing = "2099-01-01"
    with pytest.raises(DataUnavailableError):
        load_daily_dvol(dvol_dir, through_date=missing, min_rows=100)


def test_load_daily_dvol_insufficient_history_raises(tmp_path):
    dvol = make_dvol(50)
    dvol_dir = write_dvol_partitions(tmp_path, dvol)
    with pytest.raises(DataUnavailableError):
        load_daily_dvol(dvol_dir, through_date=dvol.index[-1], min_rows=253)


def test_load_daily_dvol_no_partitions_raises(tmp_path):
    empty_dir = tmp_path / "dvol" / "BTC"
    empty_dir.mkdir(parents=True, exist_ok=True)
    with pytest.raises(DataUnavailableError):
        load_daily_dvol(empty_dir, through_date="2021-06-01", min_rows=10)


def test_load_daily_dvol_feeds_compute_signal(tmp_path):
    """End-to-end (still additive): loaded DVOL + closes -> valid position series."""
    n = 320
    closes = make_closes(n)
    dvol   = make_dvol(n)
    dvol_dir = write_dvol_partitions(tmp_path, dvol)
    loaded = load_daily_dvol(dvol_dir, through_date=dvol.index[-1], min_rows=253)
    pos = df.compute_signal(closes, loaded)
    assert set(pd.unique(pos.to_numpy())).issubset({-1.0, 0.0, 1.0})
    assert pos.index[-1] == closes.index[-1]
