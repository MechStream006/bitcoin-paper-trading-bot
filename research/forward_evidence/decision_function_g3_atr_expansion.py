"""
decision_function_g3_atr_expansion.py
======================================
Locked decision function for the G3 ATR Expansion forward-evidence signal.

Logic mirrors sig_atr_expansion() in research/evidence/signals.py exactly:
    1. Compute ATR(N) via True Range rolling mean.
    2. Position is non-zero only when ATR is expanding (current > LAG bars ago).
    3. Direction follows price trend: sign(close - close.shift(N)).

This file is the fn_hash target. Any modification invalidates all existing
pre-registration hashes and requires a new pre-registration before collection
can resume. Do NOT edit without a formal governance decision.

Cross-validation: compute_signal(ohlcv) must produce identical position VALUES
(ignoring index) to sig_atr_expansion(m, n=N, lag=LAG) for the same price data.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

N          = 14   # ATR period (matches spec.params)
LAG        = 10   # lookback for expansion comparison (matches spec.params)
HOLD_DAYS  = 7    # hold-period length (matches SignalSpec.hold_days)
ANN        = 365.0
MIN_HISTORY = N + LAG + 1  # = 25: first row where position can be non-zero


def _atr(ohlcv: pd.DataFrame) -> pd.Series:
    h, l, c = ohlcv['high'], ohlcv['low'], ohlcv['close']
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(N, min_periods=N).mean()


def compute_signal(ohlcv: pd.DataFrame) -> pd.Series:
    """
    ATR Expansion: follow trend direction only when ATR is expanding.

    Parameters
    ----------
    ohlcv : pd.DataFrame with columns ['high', 'low', 'close'], daily, any index.

    Returns
    -------
    pd.Series of positions {-1.0, 0.0, +1.0}, same index as ohlcv.
    """
    atr       = _atr(ohlcv)
    expanding = atr > atr.shift(LAG)
    close     = ohlcv['close']
    direction = np.sign(close - close.shift(N))
    pos = pd.Series(0.0, index=ohlcv.index)
    pos[expanding] = direction[expanding]
    pos[atr.isna() | atr.shift(LAG).isna()] = 0.0
    return pos


def sensitivity_variants() -> list[dict]:
    """5-variant sensitivity battery (matches signals.py SignalSpec.variants)."""
    return [
        {"n": 10, "lag": 10},
        {"n": 21, "lag": 10},
        {"n": 14, "lag": 5},
        {"n": 14, "lag": 20},
        {"n": 21, "lag": 20},
    ]
