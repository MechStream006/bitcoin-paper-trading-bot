"""
Locked decision function for h5_vol_regime pre-registration.

This file is hashed at registration time and the SHA-256 is stored in
h5_vol_regime_prereg.json as decision_function_hash.  Any change to the
computation below is a protocol violation and must bump the version and
create a new pre-registration entry.

DO NOT IMPORT or call sig_h5_vol_regimes from signals.py.  That function
reads the pre-computed m["vol_regime"] column injected by engine.py.
The column lookup is not the thing being hashed — the RV21 computation is.
This file must be self-contained so the hash covers the full algorithm.

Economic hypothesis (locked):
    Periods in which 21-day realized volatility is NOT in the top tercile
    of its trailing 252-day distribution earn positive risk-adjusted forward
    returns.  Signal is +1 (long) when active, 0 (flat) when in high-vol.

Parameters (locked at version 1.0.0):
    RV_WINDOW      = 21    days for annualized realized-vol estimate
    REGIME_WINDOW  = 252   days for rolling tercile boundaries
    HIGH_PCTILE    = 67    percentile above which vol is "high" (top tercile)
    ANN            = 365.0 annualisation factor (calendar days)
    HOLD_DAYS      = 7     calendar days per hold period
    MIN_HISTORY    = 273   rows needed before first signal (RV_WINDOW + REGIME_WINDOW)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

RV_WINDOW     = 21
REGIME_WINDOW = 252
HIGH_PCTILE   = 67      # top-tercile threshold (%)
ANN           = 365.0
HOLD_DAYS     = 7
MIN_HISTORY   = RV_WINDOW + REGIME_WINDOW   # = 273


def compute_signal(close: pd.Series) -> pd.Series:
    """
    Given a daily close price series, return a position series in {0, +1}.

    +1  = vol_regime is 'low' or 'medium' (RV21 <= 67th-pctile of trailing 252d)
     0  = vol_regime is 'high' OR insufficient history

    Arguments:
        close: pd.Series of daily close prices, index is date-like, chronological.

    Returns:
        pd.Series of float, same index as close, values in {0.0, 1.0}.
    """
    log_ret = np.log(close / close.shift(1))

    rv = log_ret.rolling(RV_WINDOW, min_periods=RV_WINDOW).std() * np.sqrt(ANN)

    hi_q = rv.rolling(REGIME_WINDOW, min_periods=REGIME_WINDOW).quantile(
        HIGH_PCTILE / 100.0
    )

    pos = pd.Series(0.0, index=close.index)
    active = rv <= hi_q
    pos[active] = 1.0
    pos[rv.isna() | hi_q.isna()] = 0.0
    return pos


def sensitivity_variants() -> list[dict]:
    """
    Returns the 4 sensitivity variants used in the Phase B IS battery.
    Each dict describes overridden parameters; unspecified params remain
    at their canonical values above.

    Battery attribution:
        These variants were evaluated in the Phase B battery under the
        signal name "Volatility Regime" (GROUP1 in signals.py), which calls
        sig_volatility_regime(m, rv_win, pctile) — mathematically identical
        to compute_signal() with rv_win → RV_WINDOW and pctile expressed as
        the LOWER bound (pctile=33 ⟺ HIGH_PCTILE=67).  The sens_pass_frac=1.0
        and individual Sharpe values are sourced from that battery entry.
        The hash of THIS file covers the canonical computation; sensitivity
        variants are re-evaluated against this file's compute_signal().
    """
    return [
        {
            "label": "v1_rv14",
            "RV_WINDOW": 14,
            "HIGH_PCTILE": 67,
            "battery_sharpe_IS": 0.431,
            "description": "Shorter RV window — more responsive to recent vol shifts",
        },
        {
            "label": "v2_rv30",
            "RV_WINDOW": 30,
            "HIGH_PCTILE": 67,
            "battery_sharpe_IS": 0.338,
            "description": "Longer RV window — smoother, slower regime switches",
        },
        {
            "label": "v3_pctile75",
            "RV_WINDOW": 21,
            "HIGH_PCTILE": 75,
            "battery_sharpe_IS": 0.328,
            "description": "Top-quartile exclusion (narrower active zone than top-tercile)",
        },
        {
            "label": "v4_pctile60",
            "RV_WINDOW": 21,
            "HIGH_PCTILE": 60,
            "battery_sharpe_IS": 0.245,
            "description": "Top 40% excluded — wider exclusion, less time active",
        },
    ]
