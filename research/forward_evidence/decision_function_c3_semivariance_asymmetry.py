"""
Locked decision function for c3_semivariance_asymmetry.

This file is hashed at registration time. Any change to the computation below
is a protocol violation and must bump the version and create a new
pre-registration entry.

Implements, verbatim and without interpretation, the contract frozen at Gate A5
(docs/research/c3_semivariance_a5_specification.md §5) with the direction frozen
at Gate A4 (docs/research/c3_semivariance_a4_direction_decision.md §4). Neither
document may be re-read as guidance; they are the contract.

SOURCE vs PLATFORM -- the distinction this candidate exists to keep straight
---------------------------------------------------------------------------
SOURCE (Barndorff-Nielsen, Kinnebrock & Shephard 2008, "Measuring downside risk
-- realised semivariance"; sha256 460c77d59daaaaa80ccc2222174b709c9a6d7b9fb3be
7184f009275cd5f8f318) defines only the MEASURE:

    RS- = sum_j (Y_tj - Y_tj-1)^2 * 1{Y_tj - Y_tj-1 <= 0}
    RS+ = sum_j (Y_tj - Y_tj-1)^2 * 1{Y_tj - Y_tj-1 >= 0}

The `<=` / `>=` are the source's own, recovered at Gate A3 by decoding the PDF's
math glyph byte-codes and cross-verifying each against contexts where the
mathematics fixes the meaning -- never assumed from convention.

The source defines NO sampling frequency, NO log-price statement, NO trading
rule, NO threshold and NO direction. Everything below that turns the measure
into a position is this project's own choice, declared in advance.

Economic hypothesis (locked at A4, BEFORE any computation)
---------------------------------------------------------
    BTC perpetual positioning is structurally long-skewed -- a premise already
    registered on this platform, since funding is the price longs pay shorts.
    Given that, downside-dominated variance (SJ << 0) is disproportionately
    MECHANICAL: liquidation cascades fall on the crowded long side. Forced flow
    is price-insensitive and unrelated to value, so its displacement is a
    candidate for reversal, and the day has REMOVED leverage -> expect upward
    drift -> LONG. Upside-dominated variance (SJ >> 0) marks leverage
    ACCUMULATING and rising fragility -> expect weakness -> SHORT.

    Direction is therefore CONTRARIAN. It may not be revised by anything
    observed downstream; if the mechanism is wrong the signal fails, and that
    failure is the correct result.

Role (locked): FEATURE, not edge.
    No performance statistic of any kind has been computed for this candidate
    at any gate. Nothing here is an edge claim.

Parameters (locked at version 1.0.0)
    MIN_DAILY_BARS = 1440   bars in a complete UTC day  [platform convention, 6B.1]
    RW             = 252    rolling window in days      [platform convention, G2/IVRV]
    MINP           = 126    min_periods for the quantile[platform convention, G2/IVRV]
    PCTILE         = 33     tercile cut                 [platform convention, G2/IVRV]
    HOLD_DAYS      = 7      calendar days per hold      [platform convention]
    MIN_HISTORY    = 253    = RW + 1                    [platform convention, G2/IVRV]

Warmup note
    The rolling quantile uses min_periods=MINP=126, so a non-flat position can
    first appear at roughly 126 rows. MIN_HISTORY = RW + 1 = 253 is conservative
    by the same convention decision_function_g2_funding_level.py uses: it
    guarantees at least one full RW span is present before forward collection
    begins.

Tie-break note
    The two assignments are applied in the contract's stated order, so if
    lo == hi (a degenerate window in which every value is identical) the SHORT
    branch is applied last and wins. This is the contract's order, preserved
    deliberately rather than "fixed"; it is asserted by test.
"""
from __future__ import annotations

import pandas as pd

MIN_DAILY_BARS = 1440
RW             = 252
MINP           = 126
PCTILE         = 33
HOLD_DAYS      = 7
MIN_HISTORY    = RW + 1  # = 253


def _roll_q(s: pd.Series, win: int, p: float, minp: int) -> pd.Series:
    """Trailing rolling quantile. Same convention as decision_function_g2's _roll_q."""
    return s.rolling(win, min_periods=min(win, minp)).quantile(p)


def compute_signal(sj: pd.Series) -> pd.Series:
    """
    Contrarian on realized-semivariance asymmetry.

    Parameters
    ----------
    sj : pd.Series
        Daily SJ = (RS+ - RS-) / RV, chronological, one row per COMPLETE UTC
        day, index 'YYYY-MM-DD'. Built by
        c3_semivariance_input.load_daily_semivariance_asymmetry(), which owns
        the 1440-bar completeness rule, the log first differences, the exclusion
        of the cross-day return, and SJ := NaN when RV == 0.

    Returns
    -------
    pd.Series of {-1.0, 0.0, +1.0}, same index as `sj`.
    """
    f  = sj.astype(float)
    lo = _roll_q(f, RW, PCTILE / 100, MINP)
    hi = _roll_q(f, RW, (100 - PCTILE) / 100, MINP)

    pos = pd.Series(0.0, index=f.index)
    pos[f <= lo] = 1.0
    pos[f >= hi] = -1.0
    pos[f.isna() | lo.isna() | hi.isna()] = 0.0
    return pos
