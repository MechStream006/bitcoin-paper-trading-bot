"""
Locked decision function for g1_market_regime_sma200 pre-registration.

This file is hashed at registration time and the SHA-256 is stored in
g1_market_regime_sma200_prereg.json as decision_function_hash.  Any change
to the computation below is a protocol violation and must bump the version
and create a new pre-registration entry.

DO NOT IMPORT or call sig_market_regime from signals.py.  The function there
operates on the full engine DataFrame (m["close"]).  This file accepts a plain
pd.Series so the hash covers the exact computation that will run in forward
collection, independent of the engine's preprocessing pipeline.

Economic hypothesis (locked):
    When BTC price is above a rising SMA(200) (bull regime), forward
    risk-adjusted returns are positive.  When price is below a falling
    SMA(200) (bear regime), taking a short position earns positive returns.
    The slope filter (SMA today vs SMA LAG days ago) eliminates false signals
    during brief rallies inside downtrends and vice versa.  Flat (sideways)
    when price and slope contradict.

Parameters (locked at version 1.0.0):
    WIN        = 200   SMA period (calendar days of daily bars)
    LAG        = 20    Slope lookback: SMA(today) vs SMA(today - LAG)
    ANN        = 365.0 Annualisation factor
    HOLD_DAYS  = 7     Calendar days per forward-collection hold period
    MIN_HISTORY = 220  Rows needed before first signal (WIN + LAG)

IS battery note:
    The Phase B battery evaluated this signal with hold_days=1 (continuous
    daily re-evaluation) to match the regime-signal convention.  Forward
    collection uses hold_days=7 to reduce turnover and transaction costs.
    The IS DSR/BH-FDR statistics recorded in the pre-registration JSON were
    computed against the hold_days=1 battery evaluation, which produced
    IS_sharpe = 0.414 at 15 bps.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

WIN        = 200
LAG        = 20
ANN        = 365.0
HOLD_DAYS  = 7
MIN_HISTORY = WIN + LAG  # = 220


def compute_signal(close: pd.Series) -> pd.Series:
    """
    Given a daily close price series, return a position series in {-1, 0, +1}.

    +1  = bull regime  (close > SMA(WIN) AND SMA is rising over LAG days)
    -1  = bear regime  (close < SMA(WIN) AND SMA is falling over LAG days)
     0  = sideways (price and slope contradict) OR insufficient history

    Arguments:
        close: pd.Series of daily close prices, index is date-like, chronological.

    Returns:
        pd.Series of float, same index as close, values in {-1.0, 0.0, +1.0}.
    """
    sma  = close.rolling(WIN, min_periods=WIN).mean()
    prev = sma.shift(LAG)

    pos = pd.Series(0.0, index=close.index)
    pos[(close > sma) & (sma > prev)] =  1.0
    pos[(close < sma) & (sma < prev)] = -1.0
    pos[sma.isna() | prev.isna()]     =  0.0
    return pos


def sensitivity_variants() -> list[dict]:
    """
    Returns the 4 sensitivity variants evaluated in the Phase B IS battery.
    Each dict contains the overridden parameters; unspecified params remain
    at their canonical values above.

    All variants produced positive Sharpe (sens_pass_frac = 1.0).
    Sharpe values are at 15 bps cost, full-sample, annualised.
    """
    return [
        {
            "label": "v1_win150",
            "WIN": 150, "LAG": 20,
            "battery_sharpe_IS": 0.277,
            "description": "Shorter SMA window — faster regime detection, more signals",
        },
        {
            "label": "v2_win250",
            "WIN": 250, "LAG": 20,
            "battery_sharpe_IS": 0.707,
            "description": "Longer SMA window — slower, more stable regime identification",
        },
        {
            "label": "v3_lag10",
            "WIN": 200, "LAG": 10,
            "battery_sharpe_IS": 0.249,
            "description": "Shorter slope lookback — more responsive slope detection",
        },
        {
            "label": "v4_lag30",
            "WIN": 200, "LAG": 30,
            "battery_sharpe_IS": 0.323,
            "description": "Longer slope lookback — smoother, less sensitive to short slope reversals",
        },
    ]
