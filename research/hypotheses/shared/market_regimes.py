"""
Market regime classification for BTC research hypotheses.

Assigns Bull / Bear / Sideways label to each calendar day using a
purely rolling rule — no future data is used at any point.

Anti-lookahead:
    SMA200(d) uses close prices from [d-199, d].          Available at d. [OK]
    SMA slope uses SMA200(d) vs SMA200(d-20).             Available at d. [OK]
    First 220 days return 'unknown' (insufficient history).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

SMA_WINDOW = 200
SLOPE_LAG  = 20   # days used to measure SMA direction


def build_market_regimes(daily_close: pd.Series) -> pd.Series:
    """
    Parameters
    ----------
    daily_close : pd.Series
        Daily closing prices, DatetimeIndex (UTC), sorted ascending.

    Returns
    -------
    pd.Series of str with same index: 'bull' | 'bear' | 'sideways' | 'unknown'
    """
    sma = daily_close.rolling(SMA_WINDOW, min_periods=SMA_WINDOW).mean()
    sma_prev = sma.shift(SLOPE_LAG)

    bull = (daily_close > sma) & (sma > sma_prev)
    bear = (daily_close < sma) & (sma < sma_prev)

    regime = pd.Series("sideways", index=daily_close.index, dtype=object)
    regime[bull] = "bull"
    regime[bear] = "bear"
    regime[sma.isna() | sma_prev.isna()] = "unknown"

    return regime


REGIME_COLORS = {
    "bull":     "#d4edda",   # light green
    "bear":     "#f8d7da",   # light red
    "sideways": "#e2e3e5",   # light gray
    "unknown":  "#ffffff",
}
