"""
Locked decision function for g3_momentum_tsmom pre-registration.

This file is hashed at registration time and the SHA-256 is stored in
g3_momentum_tsmom_prereg.json as decision_function_hash.  Any change to the
computation below is a protocol violation and must bump the version and create
a new pre-registration entry.

DO NOT IMPORT or call sig_momentum from signals.py.  The function there
operates on the full engine DataFrame (m["close"]).  This file accepts a plain
pd.Series so the hash covers the exact computation that will run in forward
collection, independent of the engine's preprocessing pipeline.

Economic hypothesis (locked):
    BTC exhibits time-series momentum: the sign of the past K-day return
    predicts the sign of the forward return.  A long position when the K-day
    return is positive (uptrend) and a short position when negative (downtrend)
    harvests this persistence.  K=30 captures the 1-month horizon where
    cross-sectional and time-series momentum are widely documented across assets.

Parameters (locked at version 1.0.0):
    K          = 30    Lookback days for return sign (calendar days of daily bars)
    ANN        = 365.0 Annualisation factor
    HOLD_DAYS  = 7     Calendar days per forward-collection hold period
    MIN_HISTORY = 31   Rows needed before first signal (= K+1; K+1 rows for first non-NaN K-day return)

IS battery note:
    The Phase B battery evaluated this signal with hold_days=1 (continuous
    daily re-evaluation).  Forward collection uses hold_days=7 to reduce
    turnover and transaction costs.  The IS DSR/BH-FDR statistics recorded in
    the pre-registration JSON were computed against the hold_days=1 battery
    evaluation, which produced IS_sharpe = 0.441 at 15 bps.

Known IS risk:
    The 2026 walk-forward fold produced Sharpe -1.330 (2025: +0.006), suggesting
    2025-2026 is a low-momentum / sideways environment.  The working hypothesis
    is a regime artifact consistent with regime_sharpe["sideways"] = -0.948 in
    IS data.  This is recorded in hypothesis.known_caveats_at_registration and
    monitored in forward collection.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

K          = 30
ANN        = 365.0
HOLD_DAYS  = 7
MIN_HISTORY = K + 1  # = 31; K+1 rows required for first non-NaN K-day return


def compute_signal(close: pd.Series) -> pd.Series:
    """
    Given a daily close price series, return a position series in {-1, 0, +1}.

    +1  = k-day return is positive  (long: uptrend momentum)
    -1  = k-day return is negative  (short: downtrend momentum)
     0  = insufficient history (first K bars)

    Arguments:
        close: pd.Series of daily close prices, index is date-like, chronological.

    Returns:
        pd.Series of float, same index as close, values in {-1.0, 0.0, +1.0}.
    """
    r = close / close.shift(K) - 1.0

    pos = pd.Series(0.0, index=close.index)
    pos[r > 0] =  1.0
    pos[r < 0] = -1.0
    pos[r.isna()] = 0.0
    return pos


def sensitivity_variants() -> list[dict]:
    """
    Returns the 4 sensitivity variants evaluated in the Phase B IS battery.
    Each dict contains the overridden K parameter; all other params remain
    at their canonical values above.

    All 4 variants produced positive Sharpe (sens_pass_frac = 1.0).
    Sharpe values are at 15 bps cost, full-sample, annualised.

    Notable: k=20 has very low Sharpe (0.051) — weak edge at short horizon.
    k=60 is the strongest variant (0.459).
    """
    return [
        {
            "label": "v1_k20",
            "K": 20,
            "battery_sharpe_IS": 0.051,
            "description": "Shorter lookback — more responsive, noisier momentum signal",
        },
        {
            "label": "v2_k60",
            "K": 60,
            "battery_sharpe_IS": 0.459,
            "description": "2-month lookback — stronger IS edge, captures medium-term trends",
        },
        {
            "label": "v3_k90",
            "K": 90,
            "battery_sharpe_IS": 0.200,
            "description": "3-month lookback — slower cycle, fewer signals",
        },
        {
            "label": "v4_k120",
            "K": 120,
            "battery_sharpe_IS": 0.258,
            "description": "4-month lookback — quarterly momentum horizon",
        },
    ]
