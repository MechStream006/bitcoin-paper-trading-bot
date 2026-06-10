"""
Signal definitions for the Phase B Evidence Discovery Program.

Each signal is a function fn(m, **params) -> daily target position in {-1,0,+1},
point-in-time (decided at close of day t; the engine applies the t+1 shift).
Signals must NOT peek ahead. Rolling windows must end at t (use .shift(1) on any
"prior N-bar" extremes).

Registered as SignalSpec objects grouped by program group.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from engine import SignalSpec

# Maps the engine's pre-computed string regimes to position helpers.


# =============================================================================
# Group 1 — Market Structure
# =============================================================================

def sig_trend_regime(m: pd.DataFrame, fast: int = 20, slow: int = 100) -> pd.Series:
    """Fast/slow SMA crossover. Long when fast>slow, short when fast<slow."""
    sf = m["close"].rolling(fast, min_periods=fast).mean()
    ss = m["close"].rolling(slow, min_periods=slow).mean()
    pos = pd.Series(0.0, index=m.index)
    pos[sf > ss] = 1.0
    pos[sf < ss] = -1.0
    pos[sf.isna() | ss.isna()] = 0.0
    return pos


def sig_market_regime(m: pd.DataFrame, win: int = 200, lag: int = 20) -> pd.Series:
    """Price vs SMA(win) and SMA slope. Long bull, short bear, flat sideways."""
    sma  = m["close"].rolling(win, min_periods=win).mean()
    prev = sma.shift(lag)
    pos = pd.Series(0.0, index=m.index)
    pos[(m["close"] > sma) & (sma > prev)] = 1.0
    pos[(m["close"] < sma) & (sma < prev)] = -1.0
    pos[sma.isna() | prev.isna()] = 0.0
    return pos


def sig_volatility_regime(m: pd.DataFrame, rv_win: int = 21, pctile: int = 33,
                          regime_win: int = 252) -> pd.Series:
    """Vol-managed long: long when realized vol is NOT in the top tercile."""
    rv = np.log(m["close"] / m["close"].shift(1)).rolling(
        rv_win, min_periods=rv_win).std() * np.sqrt(365.0)
    hi = rv.rolling(regime_win, min_periods=regime_win).quantile((100 - pctile) / 100)
    pos = pd.Series(0.0, index=m.index)
    pos[(rv <= hi)] = 1.0
    pos[rv.isna() | hi.isna()] = 0.0
    return pos


def sig_breakout_regime(m: pd.DataFrame, n: int = 20) -> pd.Series:
    """Donchian channel breakout (stateful). Long above prior-N high, short below prior-N low."""
    ph = m["high"].rolling(n, min_periods=n).max().shift(1)
    pl = m["low"].rolling(n, min_periods=n).min().shift(1)
    close = m["close"].to_numpy()
    ph_a, pl_a = ph.to_numpy(), pl.to_numpy()
    out = np.zeros(len(m))
    state = 0.0
    for t in range(len(m)):
        if np.isnan(ph_a[t]) or np.isnan(pl_a[t]):
            out[t] = 0.0
            continue
        if close[t] > ph_a[t]:
            state = 1.0
        elif close[t] < pl_a[t]:
            state = -1.0
        out[t] = state
    return pd.Series(out, index=m.index)


def sig_range_regime(m: pd.DataFrame, win: int = 20, k: float = 1.5) -> pd.Series:
    """Bollinger mean-reversion (fade). Long when z<-k, short when z>+k, else flat."""
    mid = m["close"].rolling(win, min_periods=win).mean()
    sd  = m["close"].rolling(win, min_periods=win).std()
    z   = (m["close"] - mid) / sd
    pos = pd.Series(0.0, index=m.index)
    pos[z < -k] = 1.0
    pos[z >  k] = -1.0
    pos[z.isna()] = 0.0
    return pos


# =============================================================================
# Seed entries (already-validated evidence): H5, H6
# =============================================================================

def sig_h5_vol_regimes(m: pd.DataFrame) -> pd.Series:
    """H5: long when vol_regime is not 'high' (low/medium). Vol-managed exposure."""
    pos = pd.Series(0.0, index=m.index)
    pos[m["vol_regime"].isin(["low", "medium"])] = 1.0
    return pos


def sig_h6_funding_vol_regime(m: pd.DataFrame) -> pd.Series:
    """H6/S1: long when medium_vol AND low_funding AND market_regime != bear."""
    cond = (
        (m["vol_regime"] == "medium") &
        (m["funding_regime"] == "low") &
        (~m["market_regime"].isin(["bear", "unknown"]))
    )
    pos = pd.Series(0.0, index=m.index)
    pos[cond] = 1.0
    return pos


# =============================================================================
# Registry
# =============================================================================

SEEDS = [
    SignalSpec(
        name="H5 Volatility Regimes",
        group="Seed (validated)",
        rationale="High realized-vol regimes carry poor risk-adjusted forward "
                  "returns; staying long only outside the top vol tercile harvests "
                  "the vol-managed premium.",
        data_source="spot 1m -> daily close (Binance Vision)",
        feature_def="RV21d annualized; long when RV not in top tercile of "
                    "trailing 252d; flat otherwise.",
        fn=sig_h5_vol_regimes,
        params={},
        variants=[],
    ),
    SignalSpec(
        name="H6 Funding x Vol x Regime",
        group="Seed (validated)",
        rationale="In medium-vol, non-bear markets, low perp funding marks "
                  "absence of crowded longs and precedes positive drift; gated by "
                  "SMA200 regime to avoid bear capitulation.",
        data_source="spot 1m + funding (Binance/CCXT)",
        feature_def="long when vol_regime==medium AND funding_regime==low AND "
                    "market_regime != bear; 7-day hold.",
        fn=sig_h6_funding_vol_regime,
        params={},
        variants=[],
        hold_days=7,
    ),
]

GROUP1 = [
    SignalSpec(
        name="Trend Regime (MA cross)",
        group="G1 Market Structure",
        rationale="Trend persistence: when a fast MA leads a slow MA, momentum "
                  "tends to continue (time-series momentum).",
        data_source="spot 1m -> daily close",
        feature_def="SMA(fast) vs SMA(slow); long if fast>slow, short if fast<slow.",
        fn=sig_trend_regime,
        params={"fast": 20, "slow": 100},
        variants=[{"fast": 10, "slow": 50}, {"fast": 50, "slow": 200},
                  {"fast": 30, "slow": 150}, {"fast": 20, "slow": 80}],
    ),
    SignalSpec(
        name="Market Regime (SMA200 slope)",
        group="G1 Market Structure",
        rationale="Long-horizon trend filter: price above a rising SMA200 marks "
                  "structurally bullish conditions; below a falling SMA200, bearish.",
        data_source="spot 1m -> daily close",
        feature_def="long when close>SMA(win) AND SMA rising over lag; short when "
                    "close<SMA AND SMA falling; flat otherwise.",
        fn=sig_market_regime,
        params={"win": 200, "lag": 20},
        variants=[{"win": 150, "lag": 20}, {"win": 250, "lag": 20},
                  {"win": 200, "lag": 10}, {"win": 200, "lag": 30}],
    ),
    SignalSpec(
        name="Volatility Regime",
        group="G1 Market Structure",
        rationale="Realized-vol regime as a risk switch; top-tercile vol predicts "
                  "weak risk-adjusted returns (overlaps H5 by construction).",
        data_source="spot 1m -> daily close",
        feature_def="RV(rv_win) annualized; long when RV not in top tercile of "
                    "trailing window; flat otherwise.",
        fn=sig_volatility_regime,
        params={"rv_win": 21, "pctile": 33},
        variants=[{"rv_win": 14, "pctile": 33}, {"rv_win": 30, "pctile": 33},
                  {"rv_win": 21, "pctile": 25}, {"rv_win": 21, "pctile": 40}],
    ),
    SignalSpec(
        name="Breakout Regime (Donchian)",
        group="G1 Market Structure",
        rationale="Range breakouts signal regime shifts; new N-day highs/lows "
                  "tend to extend (classic trend-following).",
        data_source="spot 1m -> daily OHLC",
        feature_def="stateful: long when close>prior-N high, short when "
                    "close<prior-N low, hold until opposite break.",
        fn=sig_breakout_regime,
        params={"n": 20},
        variants=[{"n": 10}, {"n": 30}, {"n": 55}, {"n": 15}],
    ),
    SignalSpec(
        name="Range Regime (BB fade)",
        group="G1 Market Structure",
        rationale="In range-bound conditions price mean-reverts to its moving "
                  "average; fading Bollinger extremes harvests that reversion.",
        data_source="spot 1m -> daily close",
        feature_def="z=(close-SMA(win))/STD(win); long when z<-k, short when z>+k.",
        fn=sig_range_regime,
        params={"win": 20, "k": 1.5},
        variants=[{"win": 20, "k": 1.0}, {"win": 20, "k": 2.0},
                  {"win": 14, "k": 1.5}, {"win": 30, "k": 1.5}],
    ),
]

REGISTRY = {
    "seeds":  SEEDS,
    "group1": GROUP1,
}
