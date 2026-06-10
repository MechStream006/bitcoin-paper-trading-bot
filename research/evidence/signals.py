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
        hypothesis_verdict="Validated B (return-differential across vol terciles; "
                           "strong as a conditioning variable, weak as a long-only rule)",
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
        hypothesis_verdict="Validated Strong (S1 walk-forward + regime validation: "
                           "genuine non-bear edge, Sharpe ~1.8 @15bps on 7d holds)",
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

# =============================================================================
# Group 2 — Funding & Derivatives
# =============================================================================

def _roll_q(s: pd.Series, win: int, p: float, minp: int = 126) -> pd.Series:
    return s.rolling(win, min_periods=min(win, minp)).quantile(p)


def sig_funding_level(m: pd.DataFrame, pctile: int = 33, rw: int = 252) -> pd.Series:
    """Contrarian funding: long when funding is low (no crowded longs), short when high."""
    f  = m["daily_mean_funding_bps"]
    lo = _roll_q(f, rw, pctile / 100)
    hi = _roll_q(f, rw, (100 - pctile) / 100)
    pos = pd.Series(0.0, index=m.index)
    pos[f < lo] = 1.0
    pos[f > hi] = -1.0
    pos[f.isna() | lo.isna() | hi.isna()] = 0.0
    return pos


def sig_funding_acceleration(m: pd.DataFrame, k: int = 3, pctile: int = 33,
                             rw: int = 252) -> pd.Series:
    """Contrarian on funding momentum: heating funding -> short, cooling -> long."""
    df = m["daily_mean_funding_bps"] - m["daily_mean_funding_bps"].shift(k)
    lo = _roll_q(df, rw, pctile / 100)
    hi = _roll_q(df, rw, (100 - pctile) / 100)
    pos = pd.Series(0.0, index=m.index)
    pos[df < lo] = 1.0       # funding cooling fast -> bullish
    pos[df > hi] = -1.0      # funding heating fast -> bearish
    pos[df.isna() | lo.isna() | hi.isna()] = 0.0
    return pos


def sig_funding_divergence(m: pd.DataFrame, k: int = 7, rw: int = 252) -> pd.Series:
    """Price/funding divergence: rally on low funding -> continue; rally on high funding -> fade."""
    pmom = m["close"] / m["close"].shift(k) - 1.0
    f    = m["daily_mean_funding_bps"]
    med  = _roll_q(f, rw, 0.50)
    hi   = _roll_q(f, rw, 0.67)
    pos = pd.Series(0.0, index=m.index)
    pos[(pmom > 0) & (f < med)] = 1.0    # healthy uptrend (not crowded)
    pos[(pmom > 0) & (f > hi)]  = -1.0   # crowded rally -> exhaustion
    pos[pmom.isna() | f.isna() | med.isna()] = 0.0
    return pos


def sig_basis_level(m: pd.DataFrame, pctile: int = 33, rw: int = 252) -> pd.Series:
    """Contrarian basis: long in backwardation (fear), short in rich contango (greed)."""
    b  = m["basis_bps"]
    lo = _roll_q(b, rw, pctile / 100)
    hi = _roll_q(b, rw, (100 - pctile) / 100)
    pos = pd.Series(0.0, index=m.index)
    pos[b < lo] = 1.0
    pos[b > hi] = -1.0
    pos[b.isna() | lo.isna() | hi.isna()] = 0.0
    return pos


def sig_basis_compression(m: pd.DataFrame, k: int = 3) -> pd.Series:
    """Contango compressing (leverage unwinding) -> mean-reversion long; deepening backwardation -> short."""
    b  = m["basis_bps"]
    db = b - b.shift(k)
    pos = pd.Series(0.0, index=m.index)
    pos[(b > 0) & (db < 0)] = 1.0     # rich premium unwinding -> reversion up
    pos[(b < 0) & (db < 0)] = -1.0    # backwardation deepening -> down
    pos[b.isna() | db.isna()] = 0.0
    return pos


def sig_basis_expansion(m: pd.DataFrame, k: int = 3) -> pd.Series:
    """Basis expanding (rising leverage demand) -> momentum long; backwardation easing -> long."""
    b  = m["basis_bps"]
    db = b - b.shift(k)
    pos = pd.Series(0.0, index=m.index)
    pos[(b > 0) & (db > 0)] = 1.0     # contango building -> momentum up
    pos[(b < 0) & (db > 0)] = 1.0     # backwardation easing -> recovery up
    pos[(b > 0) & (db < 0)] = -1.0    # premium fading -> down
    pos[b.isna() | db.isna()] = 0.0
    return pos


GROUP2 = [
    SignalSpec(
        name="Funding Level",
        group="G2 Funding & Derivatives",
        rationale="Perp funding proxies leverage positioning; persistently high "
                  "funding marks crowded longs (mean-reversion down), low/negative "
                  "funding marks washed-out positioning (mean-reversion up).",
        data_source="funding (Binance/CCXT) -> daily mean bps",
        feature_def="long when funding < lower tercile of trailing rw days, short "
                    "when > upper tercile; 7-day hold.",
        fn=sig_funding_level, params={"pctile": 33, "rw": 252},
        variants=[{"pctile": 25, "rw": 252}, {"pctile": 40, "rw": 252},
                  {"pctile": 33, "rw": 180}, {"pctile": 33, "rw": 365}],
        hold_days=7,
    ),
    SignalSpec(
        name="Funding Acceleration",
        group="G2 Funding & Derivatives",
        rationale="The rate of change of funding captures positioning building or "
                  "unwinding; rapidly heating funding precedes long squeezes, "
                  "rapidly cooling funding precedes relief rallies.",
        data_source="funding -> daily mean bps, k-day change",
        feature_def="d(funding,k); long when change < lower tercile, short when > "
                    "upper tercile; 7-day hold.",
        fn=sig_funding_acceleration, params={"k": 3, "pctile": 33, "rw": 252},
        variants=[{"k": 2, "pctile": 33, "rw": 252}, {"k": 5, "pctile": 33, "rw": 252},
                  {"k": 3, "pctile": 25, "rw": 252}, {"k": 7, "pctile": 33, "rw": 252}],
        hold_days=7,
    ),
    SignalSpec(
        name="Funding Divergence",
        group="G2 Funding & Derivatives",
        rationale="A rally on low funding is organic and continues; a rally on high "
                  "funding is leverage-driven and exhausts. Combines price momentum "
                  "with funding state.",
        data_source="spot close + funding",
        feature_def="price k-day momentum gated by funding regime; long if up & "
                    "funding<median, short if up & funding>upper-third; 7-day hold.",
        fn=sig_funding_divergence, params={"k": 7, "rw": 252},
        variants=[{"k": 5, "rw": 252}, {"k": 10, "rw": 252},
                  {"k": 14, "rw": 252}, {"k": 7, "rw": 180}],
        hold_days=7,
    ),
    SignalSpec(
        name="Basis Level",
        group="G2 Funding & Derivatives",
        rationale="Perp-spot basis measures leverage demand; rich contango signals "
                  "crowded longs (fade), backwardation signals fear (contrarian long). "
                  "Expected to be collinear with funding.",
        data_source="perp 1m + spot 1m -> daily basis bps",
        feature_def="long when basis < lower tercile, short when > upper tercile; "
                    "7-day hold.",
        fn=sig_basis_level, params={"pctile": 33, "rw": 252},
        variants=[{"pctile": 25, "rw": 252}, {"pctile": 40, "rw": 252},
                  {"pctile": 33, "rw": 180}, {"pctile": 33, "rw": 365}],
        hold_days=7,
    ),
    SignalSpec(
        name="Basis Compression",
        group="G2 Funding & Derivatives",
        rationale="A compressing contango marks leverage unwinding that often "
                  "precedes price stabilization; deepening backwardation marks "
                  "accelerating fear.",
        data_source="perp + spot -> daily basis bps, k-day change",
        feature_def="long when basis>0 and falling; short when basis<0 and falling; "
                    "7-day hold.",
        fn=sig_basis_compression, params={"k": 3},
        variants=[{"k": 2}, {"k": 5}, {"k": 7}, {"k": 10}],
        hold_days=7,
    ),
    SignalSpec(
        name="Basis Expansion",
        group="G2 Funding & Derivatives",
        rationale="An expanding basis reflects strengthening leverage demand and "
                  "trend conviction; easing backwardation marks recovery.",
        data_source="perp + spot -> daily basis bps, k-day change",
        feature_def="long when basis rising (contango building or backwardation "
                    "easing); short when contango fading; 7-day hold.",
        fn=sig_basis_expansion, params={"k": 3},
        variants=[{"k": 2}, {"k": 5}, {"k": 7}, {"k": 10}],
        hold_days=7,
    ),
]

# =============================================================================
# Group 3 — Price Action
# =============================================================================

def _atr(m: pd.DataFrame, n: int) -> pd.Series:
    h, l, c = m["high"], m["low"], m["close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=n).mean()


def _rsi(close: pd.Series, n: int) -> pd.Series:
    d  = close.diff()
    up = d.clip(lower=0).rolling(n, min_periods=n).mean()
    dn = (-d.clip(upper=0)).rolling(n, min_periods=n).mean()
    rs = up / dn
    return 100.0 - 100.0 / (1.0 + rs)


def sig_momentum(m: pd.DataFrame, k: int = 30) -> pd.Series:
    """Time-series momentum: long if k-day return>0, short if <0."""
    r = m["close"] / m["close"].shift(k) - 1.0
    pos = pd.Series(0.0, index=m.index)
    pos[r > 0] = 1.0
    pos[r < 0] = -1.0
    pos[r.isna()] = 0.0
    return pos


def sig_trend_continuation(m: pd.DataFrame, n: int = 20, thr: float = 0.30) -> pd.Series:
    """Follow direction only when the trend is efficient (Kaufman efficiency ratio high)."""
    change = (m["close"] - m["close"].shift(n)).abs()
    path   = m["close"].diff().abs().rolling(n, min_periods=n).sum()
    er     = change / path
    direction = np.sign(m["close"] - m["close"].shift(n))
    pos = pd.Series(0.0, index=m.index)
    strong = er > thr
    pos[strong] = direction[strong]
    pos[er.isna()] = 0.0
    return pos


def sig_mean_reversion(m: pd.DataFrame, n: int = 14, lo: int = 30, hi: int = 70) -> pd.Series:
    """RSI reversal: long when oversold (RSI<lo), short when overbought (RSI>hi)."""
    rsi = _rsi(m["close"], n)
    pos = pd.Series(0.0, index=m.index)
    pos[rsi < lo] = 1.0
    pos[rsi > hi] = -1.0
    pos[rsi.isna()] = 0.0
    return pos


def sig_atr_expansion(m: pd.DataFrame, n: int = 14, lag: int = 10) -> pd.Series:
    """Volatility-confirmed momentum: when ATR is expanding, follow price direction."""
    atr = _atr(m, n)
    expanding = atr > atr.shift(lag)
    direction = np.sign(m["close"] - m["close"].shift(n))
    pos = pd.Series(0.0, index=m.index)
    pos[expanding] = direction[expanding]
    pos[atr.isna() | atr.shift(lag).isna()] = 0.0
    return pos


def sig_atr_compression(m: pd.DataFrame, n: int = 14, lag: int = 10, sma_n: int = 50) -> pd.Series:
    """Consolidation-continuation: when ATR contracts, hold the prevailing SMA-trend direction."""
    atr = _atr(m, n)
    compressing = atr < atr.shift(lag)
    sma = m["close"].rolling(sma_n, min_periods=sma_n).mean()
    pos = pd.Series(0.0, index=m.index)
    pos[compressing & (m["close"] > sma)] = 1.0
    pos[compressing & (m["close"] < sma)] = -1.0
    pos[atr.isna() | sma.isna()] = 0.0
    return pos


GROUP3 = [
    SignalSpec(
        name="Momentum (TSMOM)",
        group="G3 Price Action",
        rationale="Time-series momentum: assets that have risen tend to keep "
                  "rising over medium horizons; a robust, widely-documented effect "
                  "across markets including crypto.",
        data_source="spot 1m -> daily close",
        feature_def="sign of k-day return; long if positive, short if negative.",
        fn=sig_momentum, params={"k": 30},
        variants=[{"k": 20}, {"k": 60}, {"k": 90}, {"k": 120}],
        hold_days=1,
    ),
    SignalSpec(
        name="Trend Continuation (ER)",
        group="G3 Price Action",
        rationale="Trends persist only when price moves efficiently; the Kaufman "
                  "efficiency ratio filters choppy moves so the signal follows only "
                  "clean, directional trends.",
        data_source="spot 1m -> daily close",
        feature_def="efficiency ratio = |net change| / sum|daily changes| over n; "
                    "follow trend direction when ER>thr, else flat.",
        fn=sig_trend_continuation, params={"n": 20, "thr": 0.30},
        variants=[{"n": 14, "thr": 0.30}, {"n": 30, "thr": 0.30},
                  {"n": 20, "thr": 0.20}, {"n": 20, "thr": 0.40}],
        hold_days=1,
    ),
    SignalSpec(
        name="Mean Reversion (RSI)",
        group="G3 Price Action",
        rationale="Short-horizon overextensions revert; oversold RSI marks "
                  "capitulation (bounce), overbought RSI marks exhaustion (pullback).",
        data_source="spot 1m -> daily close",
        feature_def="RSI(n); long when RSI<lo, short when RSI>hi; 5-day hold.",
        fn=sig_mean_reversion, params={"n": 14, "lo": 30, "hi": 70},
        variants=[{"n": 7, "lo": 30, "hi": 70}, {"n": 21, "lo": 30, "hi": 70},
                  {"n": 14, "lo": 25, "hi": 75}, {"n": 14, "lo": 35, "hi": 65}],
        hold_days=5,
    ),
    SignalSpec(
        name="ATR Expansion",
        group="G3 Price Action",
        rationale="Rising true-range accompanies decisive, conviction-driven moves; "
                  "trading in the direction of price while volatility expands rides "
                  "breakouts and trend accelerations.",
        data_source="spot 1m -> daily OHLC",
        feature_def="ATR(n) > ATR(n) lag bars ago -> follow sign of n-day return; "
                    "else flat.",
        fn=sig_atr_expansion, params={"n": 14, "lag": 10},
        variants=[{"n": 10, "lag": 10}, {"n": 21, "lag": 10},
                  {"n": 14, "lag": 5}, {"n": 14, "lag": 20}],
        hold_days=1,
    ),
    SignalSpec(
        name="ATR Compression",
        group="G3 Price Action",
        rationale="Contracting true-range marks consolidation that typically "
                  "resolves in the direction of the prevailing trend (the volatility "
                  "squeeze).",
        data_source="spot 1m -> daily OHLC",
        feature_def="ATR(n) < ATR(n) lag bars ago -> hold SMA(sma_n) trend "
                    "direction; else flat.",
        fn=sig_atr_compression, params={"n": 14, "lag": 10, "sma_n": 50},
        variants=[{"n": 10, "lag": 10, "sma_n": 50}, {"n": 21, "lag": 10, "sma_n": 50},
                  {"n": 14, "lag": 10, "sma_n": 30}, {"n": 14, "lag": 10, "sma_n": 100}],
        hold_days=1,
    ),
]

# =============================================================================
# Group 4 — Candlestick Evidence (textbook definitions, no tuning)
# =============================================================================
# Patterns fire on the completion day and are held 5 days. Candlestick signals
# are event-sparse; several will fall below the 15-trade trust floor and be
# capped at Weak by design. Failures here are recorded as valid negative
# evidence, not optimized.

def _candle_parts(m: pd.DataFrame):
    o, h, l, c = m["open"], m["high"], m["low"], m["close"]
    body  = c - o
    abody = body.abs()
    upper = h - np.maximum(o, c)
    lower = np.minimum(o, c) - l
    return o, h, l, c, body, abody, upper, lower


def sig_engulfing(m: pd.DataFrame, body_atr: float = 0.0) -> pd.Series:
    """Bullish/Bearish engulfing: current body engulfs prior opposite-color body."""
    o, h, l, c, body, abody, upper, lower = _candle_parts(m)
    atr = _atr(m, 14)
    big = abody >= body_atr * atr
    prev_bear = c.shift(1) < o.shift(1)
    prev_bull = c.shift(1) > o.shift(1)
    up = (c > o) & prev_bear & (c >= o.shift(1)) & (o <= c.shift(1)) & big
    dn = (c < o) & prev_bull & (o >= c.shift(1)) & (c <= o.shift(1)) & big
    pos = pd.Series(0.0, index=m.index)
    pos[up] =  1.0
    pos[dn] = -1.0
    return pos


def sig_pin_bar(m: pd.DataFrame, wick_ratio: float = 2.0) -> pd.Series:
    """Pin bars: long lower wick (bullish hammer) / long upper wick (bearish star)."""
    o, h, l, c, body, abody, upper, lower = _candle_parts(m)
    bull = (lower >= wick_ratio * abody) & (lower >= wick_ratio * upper) & (abody > 0)
    bear = (upper >= wick_ratio * abody) & (upper >= wick_ratio * lower) & (abody > 0)
    pos = pd.Series(0.0, index=m.index)
    pos[bull] =  1.0
    pos[bear] = -1.0
    return pos


def sig_inside_bar(m: pd.DataFrame, sma_n: int = 50) -> pd.Series:
    """Inside bar (consolidation): take the prevailing SMA-trend direction."""
    o, h, l, c, *_ = _candle_parts(m)
    inside = (h < h.shift(1)) & (l > l.shift(1))
    sma = c.rolling(sma_n, min_periods=sma_n).mean()
    pos = pd.Series(0.0, index=m.index)
    pos[inside & (c > sma)] =  1.0
    pos[inside & (c < sma)] = -1.0
    pos[sma.isna()] = 0.0
    return pos


def sig_outside_bar(m: pd.DataFrame, body_atr: float = 0.0) -> pd.Series:
    """Outside bar (range expansion): direction = candle color."""
    o, h, l, c, body, abody, upper, lower = _candle_parts(m)
    atr = _atr(m, 14)
    big = abody >= body_atr * atr
    outside = (h > h.shift(1)) & (l < l.shift(1))
    pos = pd.Series(0.0, index=m.index)
    pos[outside & (c > o) & big] =  1.0
    pos[outside & (c < o) & big] = -1.0
    return pos


def sig_morning_star(m: pd.DataFrame, large: float = 0.5, star: float = 0.5) -> pd.Series:
    """Morning star: large bear, small star, large bull recovering past day-1 midpoint."""
    o, h, l, c, *_ = _candle_parts(m)
    atr = _atr(m, 14)
    o1, c1 = o.shift(2), c.shift(2)
    o2, c2 = o.shift(1), c.shift(1)
    day1 = (c1 < o1) & ((o1 - c1) >= large * atr)
    star_sm = (c2 - o2).abs() <= star * atr
    day3 = (c > o) & (c > (o1 + c1) / 2.0)
    ms = day1 & star_sm & day3
    pos = pd.Series(0.0, index=m.index)
    pos[ms] = 1.0
    return pos


def sig_evening_star(m: pd.DataFrame, large: float = 0.5, star: float = 0.5) -> pd.Series:
    """Evening star: large bull, small star, large bear breaking below day-1 midpoint."""
    o, h, l, c, *_ = _candle_parts(m)
    atr = _atr(m, 14)
    o1, c1 = o.shift(2), c.shift(2)
    o2, c2 = o.shift(1), c.shift(1)
    day1 = (c1 > o1) & ((c1 - o1) >= large * atr)
    star_sm = (c2 - o2).abs() <= star * atr
    day3 = (c < o) & (c < (o1 + c1) / 2.0)
    es = day1 & star_sm & day3
    pos = pd.Series(0.0, index=m.index)
    pos[es] = -1.0
    return pos


GROUP4 = [
    SignalSpec(
        name="Engulfing", group="G4 Candlestick",
        rationale="An engulfing candle marks a decisive one-day shift in control; "
                  "classically read as a short-term reversal signal.",
        data_source="spot 1m -> daily OHLC",
        feature_def="current body engulfs prior opposite-color body; long bullish, "
                    "short bearish; 5-day hold.",
        fn=sig_engulfing, params={"body_atr": 0.0},
        variants=[{"body_atr": 0.5}, {"body_atr": 1.0}],
        hold_days=5,
    ),
    SignalSpec(
        name="Pin Bar", group="G4 Candlestick",
        rationale="A long rejection wick signals failed continuation; hammers mark "
                  "demand at lows, shooting stars mark supply at highs.",
        data_source="spot 1m -> daily OHLC",
        feature_def="wick >= ratio*body and >= ratio*opposite wick; long hammer, "
                    "short shooting-star; 5-day hold.",
        fn=sig_pin_bar, params={"wick_ratio": 2.0},
        variants=[{"wick_ratio": 1.5}, {"wick_ratio": 2.5}, {"wick_ratio": 3.0}],
        hold_days=5,
    ),
    SignalSpec(
        name="Inside Bar", group="G4 Candlestick",
        rationale="An inside bar marks volatility contraction; the common read is "
                  "continuation in the prevailing trend after consolidation.",
        data_source="spot 1m -> daily OHLC",
        feature_def="high<prior high and low>prior low; take sign(close-SMA(n)); "
                    "5-day hold.",
        fn=sig_inside_bar, params={"sma_n": 50},
        variants=[{"sma_n": 20}, {"sma_n": 100}, {"sma_n": 30}],
        hold_days=5,
    ),
    SignalSpec(
        name="Outside Bar", group="G4 Candlestick",
        rationale="An outside bar engulfs the prior range on expanding volatility; "
                  "the close color signals the side that won the expansion.",
        data_source="spot 1m -> daily OHLC",
        feature_def="high>prior high and low<prior low; long if close>open, short "
                    "if close<open; 5-day hold.",
        fn=sig_outside_bar, params={"body_atr": 0.0},
        variants=[{"body_atr": 0.5}, {"body_atr": 1.0}],
        hold_days=5,
    ),
    SignalSpec(
        name="Morning Star", group="G4 Candlestick",
        rationale="A three-candle bottoming pattern: capitulation, indecision, then "
                  "decisive recovery; a classic bullish reversal.",
        data_source="spot 1m -> daily OHLC",
        feature_def="large bear day1, small star day2, large bull day3 recovering "
                    "past day1 midpoint; long; 5-day hold.",
        fn=sig_morning_star, params={"large": 0.5, "star": 0.5},
        variants=[{"large": 0.75, "star": 0.5}, {"large": 0.5, "star": 0.3}],
        hold_days=5,
    ),
    SignalSpec(
        name="Evening Star", group="G4 Candlestick",
        rationale="A three-candle topping pattern: euphoria, indecision, then "
                  "decisive breakdown; a classic bearish reversal.",
        data_source="spot 1m -> daily OHLC",
        feature_def="large bull day1, small star day2, large bear day3 breaking "
                    "below day1 midpoint; short; 5-day hold.",
        fn=sig_evening_star, params={"large": 0.5, "star": 0.5},
        variants=[{"large": 0.75, "star": 0.5}, {"large": 0.5, "star": 0.3}],
        hold_days=5,
    ),
]

# =============================================================================
# Group 5 — Multi-Timeframe Structure
# =============================================================================
# Trend states (50/200 MA cross) precomputed per timeframe in the engine loader
# (trend_1h, trend_4h, trend_1d), each sampled to end-of-day. The objective for
# this group is INDEPENDENCE vs existing trend/regime signals, not raw Sharpe.

def sig_trend_1h(m: pd.DataFrame) -> pd.Series:
    return m["trend_1h"].astype(float)


def sig_trend_4h(m: pd.DataFrame) -> pd.Series:
    return m["trend_4h"].astype(float)


def sig_trend_1d(m: pd.DataFrame) -> pd.Series:
    return m["trend_1d"].astype(float)


def sig_trend_alignment(m: pd.DataFrame) -> pd.Series:
    """Confluence: long only when 1H, 4H and 1D trends all agree."""
    a, b, c = m["trend_1h"], m["trend_4h"], m["trend_1d"]
    pos = pd.Series(0.0, index=m.index)
    pos[(a == 1) & (b == 1) & (c == 1)] = 1.0
    pos[(a == -1) & (b == -1) & (c == -1)] = -1.0
    return pos


def sig_trend_misalignment(m: pd.DataFrame) -> pd.Series:
    """Counter-move within the daily trend: buy dips in 1D uptrend, sell bounces in 1D downtrend."""
    a, b, c = m["trend_1h"], m["trend_4h"], m["trend_1d"]
    pos = pd.Series(0.0, index=m.index)
    pos[(c == 1) & ((a == -1) | (b == -1))] = 1.0
    pos[(c == -1) & ((a == 1) | (b == 1))] = -1.0
    return pos


GROUP5 = [
    SignalSpec(
        name="1H Trend", group="G5 Multi-Timeframe",
        rationale="Intraday (hourly) trend state; tests whether short-timeframe "
                  "structure carries information at the daily decision point.",
        data_source="spot 1m -> 1H bars -> 50/200 MA cross, end-of-day sample",
        feature_def="sign(SMA50_1h - SMA200_1h) sampled at last hour of each day.",
        fn=sig_trend_1h, params={}, variants=[], hold_days=1,
    ),
    SignalSpec(
        name="4H Trend", group="G5 Multi-Timeframe",
        rationale="Swing (4-hour) trend state; intermediate horizon between "
                  "intraday and daily structure.",
        data_source="spot 1m -> 4H bars -> 50/200 MA cross, end-of-day sample",
        feature_def="sign(SMA50_4h - SMA200_4h) sampled at last 4H bar of each day.",
        fn=sig_trend_4h, params={}, variants=[], hold_days=1,
    ),
    SignalSpec(
        name="1D Trend", group="G5 Multi-Timeframe",
        rationale="Daily trend state (50/200 MA cross); included as the redundancy "
                  "reference point for the multi-timeframe family.",
        data_source="spot 1m -> daily close -> 50/200 MA cross",
        feature_def="sign(SMA50_1d - SMA200_1d).",
        fn=sig_trend_1d, params={}, variants=[], hold_days=1,
    ),
    SignalSpec(
        name="Trend Alignment", group="G5 Multi-Timeframe",
        rationale="Multi-timeframe confluence: trades only when intraday, swing and "
                  "daily trends agree, on the thesis that aligned structure is higher "
                  "conviction.",
        data_source="trend_1h + trend_4h + trend_1d",
        feature_def="long when all three trends = +1, short when all three = -1.",
        fn=sig_trend_alignment, params={}, variants=[], hold_days=1,
    ),
    SignalSpec(
        name="Trend Misalignment", group="G5 Multi-Timeframe",
        rationale="Counter-trend pullback entry: when shorter timeframes diverge "
                  "from the daily trend, fade the divergence in the daily direction "
                  "(buy dips / sell bounces).",
        data_source="trend_1h + trend_4h + trend_1d",
        feature_def="long when 1D=+1 and a shorter TF=-1; short when 1D=-1 and a "
                    "shorter TF=+1.",
        fn=sig_trend_misalignment, params={}, variants=[], hold_days=1,
    ),
]

# =============================================================================
# Group 6 — Positioning / Order-Flow Proxies (available data only)
# =============================================================================
# Tests whether order-flow / positioning proxies add a NEW information axis or
# merely re-represent funding / basis / volatility / trend. Volume is the only
# previously-unused data axis, so volume-based proxies are the real test.

def _clv(m: pd.DataFrame) -> pd.Series:
    """Close Location Value: where the close sits in the day's range. +1 top, -1 bottom."""
    rng = (m["high"] - m["low"]).replace(0, np.nan)
    return ((m["close"] - m["low"]) - (m["high"] - m["close"])) / rng


def sig_clv_pressure(m: pd.DataFrame, n: int = 10) -> pd.Series:
    """Intrabar buying/selling pressure: smoothed CLV. Long when persistently closing high."""
    clv = _clv(m).rolling(n, min_periods=n).mean()
    pos = pd.Series(0.0, index=m.index)
    pos[clv > 0] = 1.0
    pos[clv < 0] = -1.0
    pos[clv.isna()] = 0.0
    return pos


def sig_chaikin_money_flow(m: pd.DataFrame, n: int = 20) -> pd.Series:
    """Chaikin Money Flow: volume-weighted CLV. Long when money flow positive."""
    clv = _clv(m).fillna(0.0)
    mfv = clv * m["volume"]
    cmf = mfv.rolling(n, min_periods=n).sum() / m["volume"].rolling(n, min_periods=n).sum()
    pos = pd.Series(0.0, index=m.index)
    pos[cmf > 0] = 1.0
    pos[cmf < 0] = -1.0
    pos[cmf.isna()] = 0.0
    return pos


def sig_obv_trend(m: pd.DataFrame, n: int = 20) -> pd.Series:
    """On-Balance Volume momentum: long when OBV is rising over n days."""
    direction = np.sign(m["close"].diff()).fillna(0.0)
    obv = (direction * m["volume"]).cumsum()
    chg = obv - obv.shift(n)
    pos = pd.Series(0.0, index=m.index)
    pos[chg > 0] = 1.0
    pos[chg < 0] = -1.0
    pos[chg.isna()] = 0.0
    return pos


def sig_volume_surge(m: pd.DataFrame, n: int = 20, mult: float = 1.5) -> pd.Series:
    """Volume-confirmed breakout: on a volume spike, follow the day's direction."""
    avg = m["volume"].rolling(n, min_periods=n).mean()
    surge = m["volume"] > mult * avg
    pos = pd.Series(0.0, index=m.index)
    pos[surge & (m["close"] > m["open"])] = 1.0
    pos[surge & (m["close"] < m["open"])] = -1.0
    pos[avg.isna()] = 0.0
    return pos


def sig_funding_basis_composite(m: pd.DataFrame, rw: int = 252) -> pd.Series:
    """Composite positioning index: z(funding)+z(basis), contrarian. Tests the
    funding/basis collinearity directly."""
    f = m["daily_mean_funding_bps"]
    b = m["basis_bps"]
    zf = (f - f.rolling(rw, min_periods=126).mean()) / f.rolling(rw, min_periods=126).std()
    zb = (b - b.rolling(rw, min_periods=126).mean()) / b.rolling(rw, min_periods=126).std()
    comp = (zf + zb) / 2.0
    pos = pd.Series(0.0, index=m.index)
    pos[comp < -0.5] = 1.0    # washed-out positioning -> contrarian long
    pos[comp >  0.5] = -1.0   # crowded positioning -> contrarian short
    pos[comp.isna()] = 0.0
    return pos


GROUP6 = [
    SignalSpec(
        name="CLV Pressure", group="G6 Positioning/Order-Flow",
        rationale="Close location within the daily range proxies intraday buying vs "
                  "selling pressure; persistent high closes signal demand. Uses price "
                  "structure (a potentially new axis vs trend/level signals).",
        data_source="spot 1m -> daily OHLC",
        feature_def="smoothed Close Location Value over n days; long if >0, short if <0.",
        fn=sig_clv_pressure, params={"n": 10},
        variants=[{"n": 5}, {"n": 20}, {"n": 14}],
        hold_days=1,
    ),
    SignalSpec(
        name="Chaikin Money Flow", group="G6 Positioning/Order-Flow",
        rationale="Volume-weighted close-location flow; the canonical OHLCV order-flow "
                  "proxy. Volume is the only data axis untested elsewhere in the library.",
        data_source="spot 1m -> daily OHLC + volume",
        feature_def="CMF = sum(CLV*vol)/sum(vol) over n; long if >0, short if <0.",
        fn=sig_chaikin_money_flow, params={"n": 20},
        variants=[{"n": 10}, {"n": 30}, {"n": 14}],
        hold_days=1,
    ),
    SignalSpec(
        name="OBV Trend", group="G6 Positioning/Order-Flow",
        rationale="On-balance volume accumulates signed volume; rising OBV signals "
                  "accumulation. Tests whether volume direction is independent of price "
                  "trend.",
        data_source="spot 1m -> daily close + volume",
        feature_def="OBV = cumsum(sign(dclose)*vol); long if OBV rising over n days.",
        fn=sig_obv_trend, params={"n": 20},
        variants=[{"n": 10}, {"n": 30}, {"n": 50}],
        hold_days=1,
    ),
    SignalSpec(
        name="Volume Surge", group="G6 Positioning/Order-Flow",
        rationale="A volume spike marks conviction; trading the spike-day direction "
                  "tests volume-confirmed initiation.",
        data_source="spot 1m -> daily OHLC + volume",
        feature_def="volume > mult * SMA(vol,n) -> follow candle color; 5-day hold.",
        fn=sig_volume_surge, params={"n": 20, "mult": 1.5},
        variants=[{"n": 20, "mult": 2.0}, {"n": 30, "mult": 1.5}, {"n": 10, "mult": 1.5}],
        hold_days=5,
    ),
    SignalSpec(
        name="Funding-Basis Composite", group="G6 Positioning/Order-Flow",
        rationale="Combined funding+basis positioning index; explicitly tests whether "
                  "merging the two collinear positioning measures adds anything beyond "
                  "either alone.",
        data_source="funding + perp/spot basis",
        feature_def="z(funding)+z(basis) over rw; contrarian long when < -0.5, short "
                    "when > +0.5; 7-day hold.",
        fn=sig_funding_basis_composite, params={"rw": 252},
        variants=[{"rw": 180}, {"rw": 365}],
        hold_days=7,
    ),
]

# =============================================================================
# DVOL family — Reduced Options-Volatility Evidence Program (HV-1, HV-2, HV-3)
# =============================================================================

def sig_implied_vol_regime(m: pd.DataFrame, pctile: int = 33,
                           method: str = "percentile") -> pd.Series:
    """HV-1: implied-vol-managed long. Long when DVOL is NOT in the top tercile."""
    col = m["dvol_pctile"] if method == "percentile" else m["dvol_ivrank"]
    thresh = (100 - pctile) / 100.0          # top-tercile boundary
    pos = pd.Series(0.0, index=m.index)
    pos[col <= thresh] = 1.0
    pos[col.isna()] = 0.0
    return pos


def sig_vrp_spread(m: pd.DataFrame, pctile: int = 33, rw: int = 252) -> pd.Series:
    """HV-2 primary: VRP spread (DVOL-RV30). Long when premium rich (contrarian)."""
    v  = m["vrp_spread"]
    lo = _roll_q(v, rw, pctile / 100)
    hi = _roll_q(v, rw, (100 - pctile) / 100)
    pos = pd.Series(0.0, index=m.index)
    pos[v > hi] = 1.0      # rich premium / fear -> contrarian long
    pos[v < lo] = -1.0     # cheap premium / complacency -> short
    pos[v.isna() | lo.isna() | hi.isna()] = 0.0
    return pos


def sig_vrp_logratio(m: pd.DataFrame, pctile: int = 33, rw: int = 252) -> pd.Series:
    """HV-2 variant: VRP log-ratio ln(DVOL/RV30). Level-neutral premium."""
    v  = m["vrp_logratio"]
    lo = _roll_q(v, rw, pctile / 100)
    hi = _roll_q(v, rw, (100 - pctile) / 100)
    pos = pd.Series(0.0, index=m.index)
    pos[v > hi] = 1.0
    pos[v < lo] = -1.0
    pos[v.isna() | lo.isna() | hi.isna()] = 0.0
    return pos


def sig_vol_of_vol(m: pd.DataFrame, pctile: int = 33, rw: int = 252) -> pd.Series:
    """HV-3: regime stability. Long when vol-of-vol low (stable), short when high."""
    v  = m["vol_of_vol"]
    lo = _roll_q(v, rw, pctile / 100)
    hi = _roll_q(v, rw, (100 - pctile) / 100)
    pos = pd.Series(0.0, index=m.index)
    pos[v < lo] = 1.0
    pos[v > hi] = -1.0
    pos[v.isna() | lo.isna() | hi.isna()] = 0.0
    return pos


HV1 = SignalSpec(
    name="HV-1 Implied Vol Regime",
    group="DVOL Options-Vol",
    rationale="DVOL (30d implied vol) is the forward-looking risk gauge; staying "
              "long only outside the top implied-vol tercile is the implied analogue "
              "of the H5 realized-vol-managed premium.",
    data_source="Deribit DVOL daily index",
    feature_def="long when trailing-252 DVOL percentile <= (1 - pctile); flat else.",
    fn=sig_implied_vol_regime, params={"pctile": 33, "method": "percentile"},
    variants=[{"pctile": 25, "method": "percentile"},
              {"pctile": 40, "method": "percentile"},
              {"pctile": 33, "method": "ivrank"}],
    hold_days=1,
)

HV2_SPREAD = SignalSpec(
    name="HV-2 VRP Spread",
    group="DVOL Options-Vol",
    rationale="Variance risk premium (implied minus trailing realized) is the "
              "canonical options edge; a rich premium prices fear and tends to "
              "precede positive forward returns. Spread form (absolute, vol points).",
    data_source="Deribit DVOL + spot RV30",
    feature_def="VRP = DVOL_frac - RV30 (ex-ante, trailing RV); long when VRP in "
                "top tercile, short in bottom.",
    fn=sig_vrp_spread, params={"pctile": 33, "rw": 252},
    variants=[{"pctile": 25, "rw": 252}, {"pctile": 40, "rw": 252},
              {"pctile": 33, "rw": 365}],
    hold_days=1,
)

HV2_LOGRATIO = SignalSpec(
    name="HV-2 VRP LogRatio",
    group="DVOL Options-Vol",
    rationale="Level-neutral VRP: ln(DVOL/RV30) cancels the common vol level "
              "multiplicatively, isolating proportional richness. Pre-registered "
              "robustness variant of HV-2 (one discovery, not two).",
    data_source="Deribit DVOL + spot RV30",
    feature_def="ln(DVOL_frac / RV30) (ex-ante, trailing RV); long top tercile, "
                "short bottom.",
    fn=sig_vrp_logratio, params={"pctile": 33, "rw": 252},
    variants=[{"pctile": 25, "rw": 252}, {"pctile": 40, "rw": 252},
              {"pctile": 33, "rw": 365}],
    hold_days=1,
)

HV3 = SignalSpec(
    name="HV-3 Vol-of-Vol",
    group="DVOL Options-Vol",
    rationale="Volatility of DVOL marks instability of the risk regime; rising "
              "vol-of-vol signals uncertainty/convexity demand. Evaluated primarily "
              "as a conditioning variable.",
    data_source="Deribit DVOL daily index",
    feature_def="21d std of DVOL daily log-changes; long when low tercile (stable), "
                "short when high.",
    fn=sig_vol_of_vol, params={"pctile": 33, "rw": 252},
    variants=[{"pctile": 25, "rw": 252}, {"pctile": 40, "rw": 252}],
    hold_days=1,
)

DVOL_FAMILY = [HV1, HV2_SPREAD, HV2_LOGRATIO, HV3]

REGISTRY = {
    "seeds":  SEEDS,
    "group1": GROUP1,
    "group2": GROUP2,
    "group3": GROUP3,
    "group4": GROUP4,
    "group5": GROUP5,
    "group6": GROUP6,
    "dvol":   DVOL_FAMILY,
}
