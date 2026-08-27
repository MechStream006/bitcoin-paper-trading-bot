"""
Locked decision function for g1_trend_regime_ma_cross.

This file is hashed at pinning time and the SHA-256 is stored in the
pre-registration draft as decision_function_hash. Any change to the computation
below is a protocol violation and must bump the version and create a new
pre-registration entry.

Implements, verbatim and without interpretation, the contract frozen at Gate
6A.15 (docs/research/g1_trend_regime_ma_cross_6a15_specification.md). That
document is the contract, not guidance.

DO NOT IMPORT or call sig_trend_regime from research/evidence/signals.py. The
function there operates on the full engine DataFrame (m["close"]) and is part
of the offline battery's preprocessing pipeline. This file accepts a plain
pd.Series so the hash covers the exact computation that will run in forward
collection, independent of that pipeline. The two are held identical by an
independent equivalence test, never by a shared import.

ORIGIN -- there is no external source
------------------------------------
This candidate did not come from a paper. It is a platform-native construct
that entered the registry through the Phase B battery, and its origin of record
is the GROUP1 SignalSpec "Trend Regime (MA cross)" in signals.py. Nothing here
may be presented as source-derived. The 6A.15 specification tags every choice
as PLATFORM-ORIGIN, PLATFORM-CONV or DERIVED; no PLATFORM-NEW choice exists.

Economic hypothesis (locked in the registry 2026-06-13, restated unchanged)
--------------------------------------------------------------------------
    When a fast moving average leads a slow moving average, price momentum
    tends to persist. Fast above slow is a trend-persistence state and is
    taken LONG; fast below slow is taken SHORT.

    Direction is frozen. It may not be revised by anything observed
    downstream; if the mechanism is wrong the signal fails, and that failure
    is the correct result.

Role (locked): FEATURE, not edge.
    Edge-ineligible on the record: the pinned battery run's sharpe (0.2337) is
    below candidate.edge.min_sharpe (0.30). No performance statistic was
    computed at Gate 6A.15-PRE, 6A.15 or 6A.16.

Parameters (locked at version 1.0.0)
    FAST        = 20    fast SMA period      [PLATFORM-ORIGIN, SignalSpec.params]
    SLOW        = 100   slow SMA period      [PLATFORM-ORIGIN, SignalSpec.params]
    HOLD_DAYS   = 7     calendar days/hold   [PLATFORM-CONV]
    MIN_HISTORY = 100   = SLOW               [DERIVED]
    ANN         = 365.0 annualisation        [PLATFORM-CONV]

HOLD_DAYS and ANN are metadata: they are consumed by the runner and the
evidence layer, never by compute_signal. They are declared here so the hashed
file carries the full frozen contract.

Warm-up note -- why MIN_HISTORY is exactly SLOW, not padded
-----------------------------------------------------------
    rolling(SLOW, min_periods=SLOW) first yields a non-NaN value on row SLOW,
    and SMA(FAST) is satisfied far earlier, so at exactly SLOW rows the final
    position is already decidable. MIN_HISTORY = SLOW = 100 is therefore the
    exact minimum at which the `sf.isna() | ss.isna()` branch below is
    unreachable -- a derived quantity, not a chosen one.

Manufactured-FLAT note -- the reason the isna branch must never be reached
-------------------------------------------------------------------------
    The last assignment maps an undefined moving average to 0.0. That 0.0 is
    indistinguishable from a genuine flat view, so if it were ever reached in
    forward collection a *missing input* would be persisted as a *decision*
    (gate 6B.1 defect B-02). It is retained verbatim because the frozen
    contract contains it, and it is rendered unreachable from outside:
    load_daily_closes and run_signal both refuse a run whose history is shorter
    than MIN_HISTORY, and run_signal separately refuses a present-but-NaN close
    at run_date. Fail-closed is an upstream invariant; this function must not
    and does not attempt to enforce it.

Tie-break note
    `sf > ss` and `sf < ss` are mutually exclusive, so their order is
    immaterial. Exact equality (measure-zero on real prices) is matched by
    neither and remains 0.0. The contract's statement order is preserved
    deliberately rather than "tidied"; it is asserted by test.
"""
from __future__ import annotations

import pandas as pd

FAST        = 20
SLOW        = 100
HOLD_DAYS   = 7
ANN         = 365.0
MIN_HISTORY = SLOW  # = 100


def compute_signal(close: pd.Series) -> pd.Series:
    """
    Given a daily close price series, return a position series in {-1, 0, +1}.

    +1  = SMA(FAST) > SMA(SLOW)   (trend-persistence state, taken long)
    -1  = SMA(FAST) < SMA(SLOW)   (taken short)
     0  = the two are exactly equal, OR either moving average is undefined
          (warm-up; unreachable in forward collection -- see module docstring)

    Parameters
    ----------
    close : pd.Series
        Daily close prices, chronological, one row per COMPLETE UTC day, index
        'YYYY-MM-DD'. Supplied by runner_base.load_daily_closes, which owns the
        1440-bar completeness rule, the UTC day boundary and the MIN_HISTORY
        floor. This function performs no loading, no validation and no
        completeness check of its own.

    Returns
    -------
    pd.Series of {-1.0, 0.0, +1.0}, same index as `close`.
    """
    sf = close.rolling(FAST, min_periods=FAST).mean()
    ss = close.rolling(SLOW, min_periods=SLOW).mean()

    pos = pd.Series(0.0, index=close.index)
    pos[sf > ss] = 1.0
    pos[sf < ss] = -1.0
    pos[sf.isna() | ss.isna()] = 0.0
    return pos
