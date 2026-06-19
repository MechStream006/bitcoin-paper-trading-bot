"""
Locked decision function for ivrv_vrp_spread pre-registration.

This file is hashed at registration time and the SHA-256 is stored in
pre_registration_ivrv.json as function_spec.decision_function_hash. Any change
to the computation below is a protocol violation and must bump the version and
create a new pre-registration entry.

DO NOT IMPORT or call sig_vrp_spread from evidence/signals.py. That function
operates on the full engine master DataFrame (m["vrp_spread"], precomputed in
engine.load_master). This file is self-contained and accepts plain pd.Series
so the hash covers the exact computation that will run in forward collection,
independent of the engine's preprocessing pipeline. The numeric logic
(rv30, dvol_frac, vrp spread, rolling-tercile gating) is replicated here to be
identical to the engine's HV-2 VRP Spread construction.

Economic hypothesis (locked):
    The variance risk premium (VRP) is the gap between implied volatility
    (Deribit DVOL, the 30-day forward-looking BTC vol index) and trailing
    realized volatility (RV30). A rich premium prices fear/insurance demand and
    tends to precede positive forward spot returns; a cheap premium reflects
    complacency. The signal is contrarian on the premium: long spot when VRP is
    in its top trailing tercile, short when in the bottom tercile, flat in the
    middle.

    P&L SEMANTICS (read before interpreting results): this is a DIRECTIONAL
    SPOT-timing signal conditioned on VRP, NOT a short-volatility / short-
    straddle position. Forward return is measured on spot price, consistent with
    the in-sample HV-2 battery (ret_fwd1). The literal "short-vol edge" framing
    in the roadmap is approximated here as VRP-as-a-spot-direction predictor.

Parameters (locked at version 1.0.0):
    PCTILE      = 33     Tercile cut for the rolling VRP quantiles
    RW          = 252    Rolling window (days) for the VRP quantiles
    RV_WIN      = 30     Realized-vol window (days), horizon-matched to DVOL's 30d
    ANN         = 365.0  Annualisation factor (matches engine.ANN)
    MINP        = 126    min_periods for the rolling quantile (matches _roll_q)
    HOLD_DAYS   = 7      Calendar days per forward-collection hold period
    MIN_HISTORY = 253    Rows required before the first signal (= RW + 1)

Warmup note:
    rv30 needs RV_WIN+1 closes; the rolling quantile uses min_periods=MINP=126,
    so a non-flat position can first appear at roughly RV_WIN + MINP (~156 rows).
    MIN_HISTORY=253 (= RW + 1) is conservative: it guarantees at least one full
    RW span is present. In steady-state forward collection (years of history) the
    trailing RW window is fully saturated, matching the engine's tail behaviour.

IS battery note:
    The Phase B / DVOL-family battery evaluated HV-2 VRP Spread with hold_days=1
    (continuous daily re-evaluation) on the engine master frame. Forward
    collection uses hold_days=7 to reduce turnover. IS and forward Sharpe/DSR
    distributions are NOT directly comparable due to the different hold
    conventions; the Validated gate evaluates forward returns only.

Governance note (R5, reconciled):
    IS DSR/BH for the pre-registration must be recomputed via
    compute_dsr_for_registration.py at the FROZEN n_trials=34 and the Validated
    bar dsr_prob>=0.50 / bh_p<=0.10 (thresholds.yaml validated.edge). Do NOT
    reuse dvol_family_report.json's dsr_prob=0.161 — that used n_trials=38 and a
    DSR_CONF=0.95 discovery screen, not the governance gate.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

PCTILE      = 33
RW          = 252
RV_WIN      = 30
ANN         = 365.0
MINP        = 126
HOLD_DAYS   = 7
MIN_HISTORY = RW + 1  # = 253


def _roll_q(s: pd.Series, win: int, p: float, minp: int = MINP) -> pd.Series:
    """Trailing rolling quantile. Identical convention to evidence/signals.py _roll_q."""
    return s.rolling(win, min_periods=min(win, minp)).quantile(p)


def compute_signal(closes: pd.Series, dvol: pd.Series) -> pd.Series:
    """
    Given daily spot closes and daily DVOL, return a position series in {-1,0,+1}.

    +1  = VRP spread in top trailing tercile  (rich premium / fear -> long spot)
    -1  = VRP spread in bottom trailing tercile (cheap premium / complacency -> short)
     0  = middle tercile, or insufficient history (NaN inputs)

    Arguments:
        closes: pd.Series of daily spot close prices, index date-like 'YYYY-MM-DD',
                chronological.
        dvol:   pd.Series of daily DVOL close (vol points, e.g. 48.1), index aligned
                to (or a superset/subset of) closes. Reindexed to closes internally.

    Returns:
        pd.Series of float, same index as closes, values in {-1.0, 0.0, +1.0}.

    Construction (identical to engine HV-2 VRP Spread):
        log_ret   = ln(close_t / close_{t-1})
        rv30      = rolling(RV_WIN).std(log_ret) * sqrt(ANN)        # annualised fraction
        dvol_frac = dvol / 100.0                                    # vol points -> fraction
        vrp       = dvol_frac - rv30
        lo/hi     = rolling(RW, min_periods=MINP) terciles of vrp
    """
    dvol_aligned = dvol.reindex(closes.index)

    log_ret   = np.log(closes / closes.shift(1))
    rv30      = log_ret.rolling(RV_WIN, min_periods=RV_WIN).std() * np.sqrt(ANN)
    dvol_frac = dvol_aligned / 100.0
    vrp       = dvol_frac - rv30

    lo = _roll_q(vrp, RW, PCTILE / 100.0)
    hi = _roll_q(vrp, RW, (100 - PCTILE) / 100.0)

    pos = pd.Series(0.0, index=closes.index)
    pos[vrp > hi] = 1.0
    pos[vrp < lo] = -1.0
    pos[vrp.isna() | lo.isna() | hi.isna()] = 0.0
    return pos


def sensitivity_variants() -> list[dict]:
    """
    Pre-registered sensitivity variants for HV-2 VRP Spread (parameter overrides
    on top of the canonical PCTILE=33, RW=252). Mirrors the variants declared in
    evidence/signals.py HV2_SPREAD. These are documentation for the forward
    sensitivity check; the canonical signal uses PCTILE=33, RW=252 only.
    """
    return [
        {"label": "v1_pctile25", "PCTILE": 25, "RW": 252,
         "description": "Tighter terciles — more selective, fewer signals"},
        {"label": "v2_pctile40", "PCTILE": 40, "RW": 252,
         "description": "Wider terciles — more time in-position, noisier"},
        {"label": "v3_rw365",    "PCTILE": 33, "RW": 365,
         "description": "1-year quantile window — slower premium-regime adaptation"},
    ]
