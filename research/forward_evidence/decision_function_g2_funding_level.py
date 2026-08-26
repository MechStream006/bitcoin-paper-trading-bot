"""
Locked decision function for g2_funding_level pre-registration.

This file is hashed at registration time and the SHA-256 is stored in
pre_registration_g2_funding_level.json as function_spec.decision_function_hash.
Any change to the computation below is a protocol violation and must bump the
version and create a new pre-registration entry.

DO NOT IMPORT or call sig_funding_level from evidence/signals.py. That function
operates on the full engine master DataFrame (m["daily_mean_funding_bps"],
precomputed in engine.load_master). This file is self-contained and accepts a
plain pd.Series so the hash covers the exact computation that will run in
forward collection, independent of the engine's preprocessing pipeline. The
numeric logic (rolling-tercile gating on daily mean funding) is replicated here
to be identical to the engine's construction, and that identity is asserted
against the real corpus by test, not assumed.

Economic hypothesis (locked):
    Perpetual funding is the price longs pay shorts to hold the position.
    Persistently low or negative funding marks washed-out positioning — the
    crowded longs have been flushed — and tends to precede upward drift.
    Persistently high funding marks crowded longs and precedes weakness. The
    signal is therefore CONTRARIAN on the funding level: long when funding sits
    in its bottom trailing tercile, short when in the top tercile, flat between.

    Input is daily mean funding in basis points, i.e. the mean of that UTC day's
    8-hourly prints times 10 000, as produced by runner_base.load_daily_funding()
    and by engine.load_master(). It is NOT a single funding print.

Role (locked): FEATURE, not edge.
    Standalone train Sharpe is +0.22 at 15 bps, below the 0.30 candidate.edge
    floor (thresholds.yaml). This signal is registered as a conditioning /
    explanatory input, and nothing here should be read as a standalone edge
    claim. Gate 6A.8-PRE measured its value as a filter: gating an existing
    signal by agreement with it raised risk-adjusted return on 3 of 5 collecting
    signals and reduced max drawdown on 5 of 5, at the cost of a large exposure
    reduction.

Known independence risk (declared at registration, NOT resolved):
    |r| = 0.8187 against g6_funding_basis_composite on the train slice
    (2021-01-01..2025-05-31), measured with the analyze_mtf_independence
    convention. That exceeds max_pairwise_r = 0.70. g6_funding_basis_composite
    is currently `discovery` and is not collecting, so the conflict is latent,
    not active — but if it is ever promoted, the Validated independence gate
    will fire on this pair. This is recorded so that outcome is foreseeable from
    the registration rather than a surprise at promotion.
    Against the five signals actually collecting, max |r| is 0.3774 (all
    correlations negative — a contrarian positioning signal against a
    trend-heavy book), and VIF R^2-on-others is 0.225.

Parameters (locked at version 1.0.0):
    PCTILE      = 33     Tercile cut for the rolling funding quantiles
    RW          = 252    Rolling window (days) for those quantiles
    MINP        = 126    min_periods for the rolling quantile (matches _roll_q)
    HOLD_DAYS   = 7      Calendar days per forward-collection hold period
    MIN_HISTORY = 253    Rows required before the first signal (= RW + 1)

Warmup note:
    The rolling quantile uses min_periods=MINP=126, so a non-flat position can
    first appear at roughly 126 rows. MIN_HISTORY=253 (= RW + 1) is conservative
    by the same convention decision_function_ivrv.py uses: it guarantees at
    least one full RW span is present, so forward collection begins only once
    the trailing window is saturated and matches the engine's tail behaviour.

IS battery note:
    The battery evaluated Funding Level with hold_days=1 (continuous daily
    re-evaluation) on the engine master frame. Forward collection uses
    hold_days=7 to reduce turnover. IS and forward Sharpe distributions are NOT
    directly comparable across the two hold conventions; the Validated gate
    evaluates forward returns only.
"""
from __future__ import annotations

import pandas as pd

PCTILE      = 33
RW          = 252
MINP        = 126
HOLD_DAYS   = 7
MIN_HISTORY = RW + 1  # = 253


def _roll_q(s: pd.Series, win: int, p: float, minp: int = MINP) -> pd.Series:
    """Trailing rolling quantile. Identical convention to evidence/signals.py _roll_q."""
    return s.rolling(win, min_periods=min(win, minp)).quantile(p)


def compute_signal(funding: pd.Series) -> pd.Series:
    """
    Contrarian funding level: long when funding is low, short when high.

    Parameters
    ----------
    funding : pd.Series
        Daily mean funding in basis points, chronological, one row per UTC day.

    Returns
    -------
    pd.Series of positions {-1.0, 0.0, +1.0}, same index as `funding`.
    """
    f  = funding.astype(float)
    lo = _roll_q(f, RW, PCTILE / 100)
    hi = _roll_q(f, RW, (100 - PCTILE) / 100)
    pos = pd.Series(0.0, index=f.index)
    pos[f < lo] = 1.0
    pos[f > hi] = -1.0
    pos[f.isna() | lo.isna() | hi.isna()] = 0.0
    return pos
