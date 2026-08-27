"""
Gate A6 — C3 realized-semivariance implementation validation.

Validates the implementation against the contract frozen at A5
(docs/research/c3_semivariance_a5_specification.md §5) and the direction frozen
at A4. Nothing here evaluates the strategy: no Sharpe, DSR, BH-p, drawdown,
forward return or hit rate is computed anywhere in this file, and the sealed
lockbox (2025-06-01..2026-05-31) is never read.

Groups
    U*  unit
    E*  equivalence / invariant (independent reference, synthetic fixtures)
    R*  real-corpus sanity, TRAIN SLICE ONLY

Run from research/:
    pytest forward_evidence/tests/test_c3_semivariance.py -v
"""
from __future__ import annotations

import pathlib

import numpy as np
import pandas as pd
import pytest

from forward_evidence import decision_function_c3_semivariance_asymmetry as C3
from forward_evidence.c3_semivariance_input import (
    MIN_DAILY_BARS,
    RETURNS_PER_DAY,
    load_daily_semivariance_asymmetry,
)
from forward_evidence.runner_base import DataUnavailableError

_RESEARCH = pathlib.Path(__file__).resolve().parents[2]
TRAIN_END = "2025-05-31"
LOCKBOX = ("2025-06-01", "2026-05-31")


# ===========================================================================
# Fixture helpers
# ===========================================================================

def write_days(root: pathlib.Path, specs: dict[str, np.ndarray]) -> pathlib.Path:
    """
    Build a 1m parquet store from {day -> close array}.

    The array length is the bar count for that day, so an incomplete day is
    expressed simply by passing fewer than MIN_DAILY_BARS closes.
    """
    frames = []
    for day, closes in specs.items():
        ts = pd.date_range(day, periods=len(closes), freq="min", tz="UTC")
        frames.append(pd.DataFrame({
            "open_time": ts.astype("int64") // 10 ** 6,
            "close": np.asarray(closes, dtype=float),
        }))
    df = pd.concat(frames, ignore_index=True)
    dt = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for (y, m), g in df.groupby([dt.dt.year, dt.dt.month]):
        p = root / f"year={y}" / f"month={m:02d}"
        p.mkdir(parents=True, exist_ok=True)
        g.to_parquet(p / "data.parquet", index=False)
    return root


def flat_day(value: float = 100.0) -> np.ndarray:
    return np.full(MIN_DAILY_BARS, value, dtype=float)


def ramp_day(start: float, step: float) -> np.ndarray:
    """Strictly monotone day: every within-day return has the sign of `step`."""
    return start * np.exp(np.arange(MIN_DAILY_BARS) * step)


def n_days(n: int, start_day: str = "2026-01-01", seed: int = 0) -> dict:
    """n complete synthetic days with a deterministic pseudo-random path."""
    rng = np.random.default_rng(seed)
    out, px = {}, 100.0
    for d in pd.date_range(start_day, periods=n, freq="D"):
        steps = rng.normal(0.0, 1e-4, MIN_DAILY_BARS)
        path = px * np.exp(np.cumsum(steps))
        out[d.strftime("%Y-%m-%d")] = path
        px = float(path[-1])
    return out


def reference_sj(closes: np.ndarray) -> tuple[float, float, float, float]:
    """
    Independent reference for one day, written from the SOURCE formula directly
    and deliberately NOT sharing code with the implementation: a plain Python
    loop over within-day log returns.
    """
    rs_m = rs_p = 0.0
    for i in range(1, len(closes)):
        r = float(np.log(closes[i]) - np.log(closes[i - 1]))
        if r <= 0.0:
            rs_m += r * r
        if r >= 0.0:
            rs_p += r * r
    rv = rs_m + rs_p
    sj = (rs_p - rs_m) / rv if rv > 0 else float("nan")
    return rs_m, rs_p, rv, sj


# ===========================================================================
# U — unit
# ===========================================================================

class TestUnit:

    def test_U01_rs_algebra_identity(self, tmp_path):
        """[SOURCE] RS- + RS+ == RV exactly, for every day."""
        store = write_days(tmp_path / "s", n_days(5))
        d = load_daily_semivariance_asymmetry(store, "2026-01-05", min_rows=1)
        assert float((d.RSm + d.RSp - d.RV).abs().max()) == 0.0

    def test_U02_zero_returns_counted_in_both_and_contribute_zero(self, tmp_path):
        """
        A flat day has 1439 zero returns. The source's <= / >= overlap at r=0,
        so a zero return enters BOTH sums -- and contributes exactly 0 to each.
        RV is therefore 0 and SJ is NaN, not 0.0.
        """
        store = write_days(tmp_path / "s", {"2026-01-01": flat_day()})
        d = load_daily_semivariance_asymmetry(store, "2026-01-01", min_rows=1)
        row = d.loc["2026-01-01"]
        assert row.RSm == 0.0 and row.RSp == 0.0 and row.RV == 0.0
        assert np.isnan(row.SJ)
        assert row.n_returns == RETURNS_PER_DAY

    def test_U03_all_negative_day_gives_sj_minus_one(self, tmp_path):
        store = write_days(tmp_path / "s", {"2026-01-01": ramp_day(100.0, -1e-5)})
        d = load_daily_semivariance_asymmetry(store, "2026-01-01", min_rows=1)
        assert d.loc["2026-01-01", "RSp"] == 0.0
        assert d.loc["2026-01-01", "SJ"] == pytest.approx(-1.0)

    def test_U04_all_positive_day_gives_sj_plus_one(self, tmp_path):
        store = write_days(tmp_path / "s", {"2026-01-01": ramp_day(100.0, +1e-5)})
        d = load_daily_semivariance_asymmetry(store, "2026-01-01", min_rows=1)
        assert d.loc["2026-01-01", "RSm"] == 0.0
        assert d.loc["2026-01-01", "SJ"] == pytest.approx(+1.0)

    def test_U05_incomplete_day_rejected(self, tmp_path):
        """1439 bars is not a complete day; the guard is exact equality."""
        short = ramp_day(100.0, 1e-5)[: MIN_DAILY_BARS - 1]
        store = write_days(tmp_path / "s", {"2026-01-01": short})
        with pytest.raises(DataUnavailableError, match="Incomplete or absent day"):
            load_daily_semivariance_asymmetry(store, "2026-01-01", min_rows=1)

    def test_U06_incomplete_interior_day_is_dropped_not_partially_used(self, tmp_path):
        specs = n_days(3)
        days = sorted(specs)
        specs[days[1]] = specs[days[1]][:1000]          # truncate the middle day
        store = write_days(tmp_path / "s", specs)
        d = load_daily_semivariance_asymmetry(store, days[2], min_rows=1)
        assert days[1] not in d.index
        assert set(d.index) == {days[0], days[2]}

    def test_U07_cross_day_return_excluded(self, tmp_path):
        """
        Two days with a large gap between them. The boundary move must appear in
        NEITHER day, so each day has exactly RETURNS_PER_DAY returns and the
        gap contributes nothing to either RV.
        """
        a = ramp_day(100.0, 1e-6)
        b = ramp_day(500.0, 1e-6)                        # 5x gap at the boundary
        store = write_days(tmp_path / "s", {"2026-01-01": a, "2026-01-02": b})
        d = load_daily_semivariance_asymmetry(store, "2026-01-02", min_rows=1)
        assert list(d["n_returns"]) == [RETURNS_PER_DAY, RETURNS_PER_DAY]
        gap = float(np.log(b[0]) - np.log(a[-1]))
        assert abs(gap) > 1.0, "fixture must actually contain a large boundary move"
        for day in ("2026-01-01", "2026-01-02"):
            assert d.loc[day, "RV"] < gap ** 2 / 100, "boundary move leaked into RV"

    def test_U08_min_history_enforced(self, tmp_path):
        store = write_days(tmp_path / "s", n_days(5))
        with pytest.raises(DataUnavailableError, match="Insufficient complete-day history"):
            load_daily_semivariance_asymmetry(store, "2026-01-05", min_rows=6)

    def test_U09_percentile_boundaries_are_inclusive(self):
        """
        Contract uses `<= lo` and `>= hi`, so a value sitting exactly ON a
        tercile is ACTIVE, not flat. Constructed so the quantiles land on values
        actually present in the series: 84 zeros then 42 ones gives lo = 0.0
        exactly, and a final value of 0.0 must therefore be LONG.
        """
        # 50 zeros + 76 ones over 126 points puts the 33rd percentile at index
        # 0.33*125 = 41.25 (inside the zeros -> lo = 0.0) and the 67th at
        # 0.67*125 = 83.75 (inside the ones -> hi = 1.0), so the terciles are
        # distinct AND lo sits exactly on a value present in the series.
        vals = [0.0] * 49 + [1.0] * 76 + [0.0]          # 126 points, last == 0.0
        idx = pd.date_range("2024-01-01", periods=len(vals), freq="D").strftime("%Y-%m-%d")
        sj = pd.Series(vals, index=idx)
        lo = sj.rolling(C3.RW, min_periods=C3.MINP).quantile(C3.PCTILE / 100)
        hi = sj.rolling(C3.RW, min_periods=C3.MINP).quantile((100 - C3.PCTILE) / 100)
        assert lo.iloc[-1] == 0.0, "fixture must put the lower tercile exactly on a value"
        assert sj.iloc[-1] == lo.iloc[-1], "final value must sit exactly ON the tercile"
        assert lo.iloc[-1] < hi.iloc[-1], "terciles must be distinct for this test"
        assert C3.compute_signal(sj).iloc[-1] == 1.0, "on-boundary must be ACTIVE (<=)"

    def test_U09b_degenerate_window_tie_break_follows_contract_order(self):
        """
        If every value in the window is identical then lo == hi and BOTH branches
        match. The contract applies `<= lo -> +1` first and `>= hi -> -1` second,
        so SHORT wins. Pinned deliberately: this is the contract's order, not a
        bug to be 'fixed'.
        """
        idx = pd.date_range("2024-01-01", periods=C3.MINP, freq="D").strftime("%Y-%m-%d")
        sj = pd.Series([0.25] * C3.MINP, index=idx)
        assert C3.compute_signal(sj).iloc[-1] == -1.0

    def test_U10_exact_position_mapping(self):
        """
        Uses a STATIONARY series, not a monotone one. Under a monotone series
        every point is the top of its own trailing window, so the SHORT branch
        fires everywhere and the window LENGTH becomes unobservable -- which let
        adversarial mutation M11 (upper tercile computed over RW//2) survive the
        first battery.
        """
        rng = np.random.default_rng(42)
        idx = pd.date_range("2024-01-01", periods=400, freq="D").strftime("%Y-%m-%d")
        sj = pd.Series(rng.normal(0.0, 0.2, 400), index=idx)
        pos = C3.compute_signal(sj)
        lo = sj.rolling(C3.RW, min_periods=C3.MINP).quantile(C3.PCTILE / 100)
        hi = sj.rolling(C3.RW, min_periods=C3.MINP).quantile((100 - C3.PCTILE) / 100)
        expect = pd.Series(0.0, index=idx)
        expect[sj <= lo] = 1.0
        expect[sj >= hi] = -1.0
        expect[sj.isna() | lo.isna() | hi.isna()] = 0.0
        pd.testing.assert_series_equal(pos, expect)
        assert set(pos.unique()) <= {-1.0, 0.0, 1.0}

    def test_U09c_over_long_day_is_rejected(self):
        """
        Completeness is EXACT equality, not a lower bound. A day carrying MORE
        than MIN_DAILY_BARS bars (a duplicated bar, a re-ingest artefact) is not
        a valid day and must be dropped. Adversarial mutation M20 relaxed `==`
        to `>=` and survived the first battery because no fixture produced an
        over-long day.
        """
        import tempfile
        tmp = pathlib.Path(tempfile.mkdtemp())
        # A 1441st MINUTE would spill into the next UTC day and leave day one
        # exactly complete. A genuine over-long day needs a DUPLICATED bar, which
        # is also the realistic cause (a re-ingest writing a row twice).
        closes = ramp_day(100.0, 1e-5)
        ts = pd.date_range("2026-01-01", periods=MIN_DAILY_BARS, freq="min", tz="UTC")
        df = pd.DataFrame({"open_time": ts.astype("int64") // 10 ** 6, "close": closes})
        df = pd.concat([df, df.iloc[[500]]], ignore_index=True)        # 1441 rows, one day
        p = tmp / "s" / "year=2026" / "month=01"
        p.mkdir(parents=True, exist_ok=True)
        df.to_parquet(p / "data.parquet", index=False)
        with pytest.raises(DataUnavailableError, match="Incomplete or absent day"):
            load_daily_semivariance_asymmetry(tmp / "s", "2026-01-01", min_rows=1)

    def test_U09d_source_operators_are_verbatim(self):
        """
        STRUCTURAL, and structural by necessity -- not a weaker choice made for
        convenience.

        A3 proved the source's `<=` / `>=` are numerically inert: the summand is
        r^2, so the only overlapping point r=0 contributes exactly zero to either
        sum. That is precisely why RS- + RS+ = RV holds exactly. It also means no
        behavioural test can ever distinguish `<=` from `<`, and adversarial
        mutations M16/M17 correctly survived the behavioural battery.

        Source FIDELITY still matters even where behaviour is invariant, so it is
        pinned here at the source level instead.
        """
        src = (_RESEARCH / "forward_evidence" / "c3_semivariance_input.py").read_text(
            encoding="utf-8")
        body = src.split("def load_daily_semivariance_asymmetry", 1)[1]
        assert "np.where(r <= 0.0, sq, 0.0)" in body, "RS- must use the source's <="
        assert "np.where(r >= 0.0, sq, 0.0)" in body, "RS+ must use the source's >="

    def test_U11_contrarian_polarity_is_the_frozen_direction(self):
        """
        Low SJ -> LONG, high SJ -> SHORT (A4's frozen mapping).

        Tested against a STATIONARY history with the final value pushed to one
        extreme. A monotone series would not test this: with a trailing window
        every point is the top of its own window, so everything is correctly
        SHORT and the polarity is unfalsifiable.
        """
        rng = np.random.default_rng(0)
        base = list(rng.normal(0.0, 0.1, 299))
        idx = pd.date_range("2024-01-01", periods=300, freq="D").strftime("%Y-%m-%d")

        low = pd.Series(base + [-5.0], index=idx)
        assert C3.compute_signal(low).iloc[-1] == 1.0, "very low SJ must be LONG"

        high = pd.Series(base + [+5.0], index=idx)
        assert C3.compute_signal(high).iloc[-1] == -1.0, "very high SJ must be SHORT"

        mid = pd.Series(base + [float(np.median(base))], index=idx)
        assert C3.compute_signal(mid).iloc[-1] == 0.0, "mid SJ must be FLAT"

    def test_U12_nan_sj_is_flat_never_a_decision(self):
        idx = pd.date_range("2024-01-01", periods=300, freq="D").strftime("%Y-%m-%d")
        sj = pd.Series(np.linspace(-0.5, 0.5, 300), index=idx)
        sj.iloc[-1] = np.nan
        assert C3.compute_signal(sj).iloc[-1] == 0.0

    def test_U13_warmup_before_min_periods_is_flat(self):
        idx = pd.date_range("2024-01-01", periods=C3.MINP - 1, freq="D").strftime("%Y-%m-%d")
        sj = pd.Series(np.linspace(-1, 1, C3.MINP - 1), index=idx)
        assert (C3.compute_signal(sj) == 0.0).all()

    def test_U14_missing_partitions_fail_closed(self, tmp_path):
        empty = tmp_path / "none"
        empty.mkdir()
        with pytest.raises(DataUnavailableError, match="No OHLCV partitions"):
            load_daily_semivariance_asymmetry(empty, "2026-01-01", min_rows=1)

    def test_U15_frozen_constants(self):
        assert (C3.MIN_DAILY_BARS, C3.RW, C3.MINP, C3.PCTILE,
                C3.HOLD_DAYS, C3.MIN_HISTORY) == (1440, 252, 126, 33, 7, 253)
        assert C3.MIN_HISTORY == C3.RW + 1
        assert MIN_DAILY_BARS == 1440 and RETURNS_PER_DAY == 1439


# ===========================================================================
# E — equivalence / invariant
# ===========================================================================

class TestEquivalence:

    def test_E01_matches_independent_reference(self, tmp_path):
        """Plain-Python loop written from the source formula, no shared code."""
        specs = n_days(4, seed=7)
        store = write_days(tmp_path / "s", specs)
        d = load_daily_semivariance_asymmetry(store, sorted(specs)[-1], min_rows=1)
        for day, closes in specs.items():
            rs_m, rs_p, rv, sj = reference_sj(closes)
            assert d.loc[day, "RSm"] == pytest.approx(rs_m, rel=1e-12, abs=1e-18)
            assert d.loc[day, "RSp"] == pytest.approx(rs_p, rel=1e-12, abs=1e-18)
            assert d.loc[day, "RV"] == pytest.approx(rv, rel=1e-12, abs=1e-18)
            assert d.loc[day, "SJ"] == pytest.approx(sj, rel=1e-12, abs=1e-15)

    def test_E02_known_hand_computed_fixture(self, tmp_path):
        """
        A day of 1440 bars: one bar down by factor exp(-a), the rest flat.
        Then RS- = a², RS+ = 0, SJ = -1, computed by hand not by code.
        """
        a = 0.01
        closes = np.full(MIN_DAILY_BARS, 100.0)
        closes[5:] = 100.0 * np.exp(-a)
        store = write_days(tmp_path / "s", {"2026-01-01": closes})
        d = load_daily_semivariance_asymmetry(store, "2026-01-01", min_rows=1)
        assert d.loc["2026-01-01", "RSm"] == pytest.approx(a ** 2, rel=1e-12)
        assert d.loc["2026-01-01", "RSp"] == pytest.approx(0.0, abs=1e-24)
        assert d.loc["2026-01-01", "SJ"] == pytest.approx(-1.0)

    def test_E03_log_not_arithmetic_returns(self, tmp_path):
        """
        A large single move separates log from arithmetic differences. RS- must
        equal ln(2)^2, not (100-200)^2 nor 0.5^2.
        """
        closes = np.full(MIN_DAILY_BARS, 200.0)
        closes[100:] = 100.0
        store = write_days(tmp_path / "s", {"2026-01-01": closes})
        d = load_daily_semivariance_asymmetry(store, "2026-01-01", min_rows=1)
        assert d.loc["2026-01-01", "RSm"] == pytest.approx(np.log(2.0) ** 2, rel=1e-12)

    def test_E04_prefix_invariance_no_future_leakage(self, tmp_path):
        """
        Truncating the input at day k must not change any value at or before k.
        This is the no-lookahead guarantee, tested rather than asserted.
        """
        specs = n_days(8, seed=3)
        days = sorted(specs)
        store = write_days(tmp_path / "s", specs)
        full = load_daily_semivariance_asymmetry(store, days[-1], min_rows=1)
        for cut in (days[3], days[5]):
            part = load_daily_semivariance_asymmetry(store, cut, min_rows=1)
            pd.testing.assert_frame_equal(part, full.loc[:cut])

    def test_E05_future_day_cannot_alter_an_earlier_one(self, tmp_path):
        specs = n_days(5, seed=11)
        days = sorted(specs)
        store_a = write_days(tmp_path / "a", specs)
        before = load_daily_semivariance_asymmetry(store_a, days[2], min_rows=1)
        specs2 = dict(specs)
        specs2[days[4]] = specs2[days[4]] * 3.0          # mutate a LATER day
        store_b = write_days(tmp_path / "b", specs2)
        after = load_daily_semivariance_asymmetry(store_b, days[2], min_rows=1)
        pd.testing.assert_frame_equal(before, after)

    def test_E06_returns_per_day_is_exactly_1439(self, tmp_path):
        store = write_days(tmp_path / "s", n_days(6))
        d = load_daily_semivariance_asymmetry(store, "2026-01-06", min_rows=1)
        assert set(d["n_returns"].unique()) == {RETURNS_PER_DAY}

    def test_E07_sj_bounded(self, tmp_path):
        store = write_days(tmp_path / "s", n_days(10, seed=5))
        d = load_daily_semivariance_asymmetry(store, "2026-01-10", min_rows=1)
        assert d["SJ"].dropna().between(-1.0, 1.0).all()


# ===========================================================================
# R — real corpus, TRAIN SLICE ONLY (no performance, no lockbox)
# ===========================================================================

class TestRealCorpus:

    SPOT = _RESEARCH / "data" / "raw" / "spot" / "BTCUSDT"

    def _load(self):
        if not list(self.SPOT.glob("year=*/month=*/data.parquet")):
            pytest.skip("real spot corpus not present (gitignored)")
        return load_daily_semivariance_asymmetry(self.SPOT, TRAIN_END, C3.MIN_HISTORY)

    def test_R01_builds_on_real_corpus_and_stops_at_train_end(self):
        d = self._load()
        assert d.index[-1] == TRAIN_END
        assert len(d) >= C3.MIN_HISTORY

    def test_R02_never_reads_the_sealed_lockbox(self):
        d = self._load()
        assert (d.index < LOCKBOX[0]).all(), "no row may fall inside the sealed lockbox"

    def test_R03_identity_holds_on_real_data(self):
        d = self._load()
        assert float((d.RSm + d.RSp - d.RV).abs().max()) == 0.0

    def test_R04_every_real_day_has_exactly_1439_returns(self):
        d = self._load()
        assert set(d["n_returns"].unique()) == {RETURNS_PER_DAY}

    def test_R05_index_is_unique_and_sorted(self):
        d = self._load()
        assert d.index.is_unique and list(d.index) == sorted(d.index)

    def test_R06_positions_are_only_the_three_legal_values(self):
        d = self._load()
        pos = C3.compute_signal(d["SJ"])
        assert set(pos.unique()) <= {-1.0, 0.0, 1.0}
        assert (pos != 0.0).any(), "signal must actually activate on real data"
