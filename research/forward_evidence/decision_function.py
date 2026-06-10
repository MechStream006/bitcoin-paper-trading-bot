"""
Frozen H6 Decision Function — Phase E Forward Evidence Collection.

Pre-registered 2026-06-10. DO NOT MODIFY after pre_registration.json is written.
Any parameter change requires a new version + new pre-registration.

Signal (frozen):
    long (1) when vol_regime==medium AND funding_regime==low
              AND market_regime not in {bear, unknown}
    flat (0) otherwise

Data source (frozen):
    spot_close  : Binance BTCUSDT daily close (from 1-min OHLCV, last bar of day)
    funding_bps : Binance USDT-perp daily mean funding rate × 10 000

Hold horizon: 7 calendar days. No intra-hold exits in Phase E demo.
"""
from __future__ import annotations

import hashlib
from typing import Optional

import numpy as np
import pandas as pd

# ============================================================================
# FROZEN PARAMETERS
# ============================================================================
RV_WINDOW        = 21      # realized-vol window (days)
REGIME_WINDOW    = 252     # trailing quantile window (days)
REGIME_PCTILE    = 33      # tercile boundary: 33rd / 67th percentile
SMA_WINDOW       = 200     # market-regime SMA period
SLOPE_LAG        = 20      # market-regime slope lookback
HOLD_DAYS        = 7       # hold horizon
MIN_HISTORY      = 273     # minimum rows required for regime warmup (252 + 21)
ANN              = 365.0   # annualization factor

VERSION          = "1.0.0"


# ============================================================================
# Regime helpers  (match engine.py logic exactly; pandas-rolling for speed)
# ============================================================================

def _vol_regime(close: np.ndarray) -> tuple[str, float]:
    """Returns (regime_label, rv_21d)."""
    s = pd.Series(close, dtype=float)
    lr = np.log(s / s.shift(1))
    rv = lr.rolling(RV_WINDOW, min_periods=RV_WINDOW).std() * np.sqrt(ANN)
    lo = rv.rolling(REGIME_WINDOW, min_periods=REGIME_WINDOW).quantile(REGIME_PCTILE / 100)
    hi = rv.rolling(REGIME_WINDOW, min_periods=REGIME_WINDOW).quantile(
        (100 - REGIME_PCTILE) / 100
    )
    cur, lo_v, hi_v = float(rv.iloc[-1]), float(lo.iloc[-1]), float(hi.iloc[-1])
    rv21 = cur
    if any(np.isnan(v) for v in (cur, lo_v, hi_v)):
        return "unknown", float("nan")
    if cur < lo_v:
        return "low", rv21
    if cur > hi_v:
        return "high", rv21
    return "medium", rv21


def _funding_regime(funding_bps: np.ndarray) -> str:
    s = pd.Series(funding_bps, dtype=float)
    valid = s.dropna()
    if len(valid) < REGIME_WINDOW:
        return "unknown"
    win = valid.iloc[-REGIME_WINDOW:]
    lo = float(win.quantile(REGIME_PCTILE / 100))
    hi = float(win.quantile((100 - REGIME_PCTILE) / 100))
    cur = float(valid.iloc[-1])
    if cur < lo:
        return "low"
    if cur > hi:
        return "high"
    return "medium"


def _market_regime(close: np.ndarray) -> tuple[str, float]:
    """Returns (regime_label, sma200_value)."""
    s = pd.Series(close, dtype=float)
    sma = s.rolling(SMA_WINDOW, min_periods=SMA_WINDOW).mean()
    sma_prev = sma.shift(SLOPE_LAG)
    cur_c, cur_sma, cur_prev = (
        float(s.iloc[-1]), float(sma.iloc[-1]), float(sma_prev.iloc[-1])
    )
    if any(np.isnan(v) for v in (cur_sma, cur_prev)):
        return "unknown", float("nan")
    if cur_c > cur_sma and cur_sma > cur_prev:
        return "bull", cur_sma
    if cur_c < cur_sma and cur_sma < cur_prev:
        return "bear", cur_sma
    return "sideways", cur_sma


# ============================================================================
# Core decision rule (immutable)
# ============================================================================

def h6_decide(vol_regime: str, funding_regime: str, market_regime: str) -> int:
    """Returns 1 (enter long) or 0 (stay flat). Never returns -1."""
    if (vol_regime == "medium"
            and funding_regime == "low"
            and market_regime not in ("bear", "unknown")):
        return 1
    return 0


# ============================================================================
# Public API
# ============================================================================

def compute_h6_decision(
    history_df: pd.DataFrame,
    strata_funding_bps: Optional[float] = None,
) -> dict:
    """
    Compute the frozen H6 signal for the MOST RECENT row of history_df.

    Parameters
    ----------
    history_df : pd.DataFrame
        Must contain columns: 'spot_close', 'funding_bps'.
        Optional column: 'day_ts' (epoch ms).
        Must have >= MIN_HISTORY rows.
        Rows are sorted ascending; the last row is today.

    strata_funding_bps : float, optional
        If supplied: also computes a shadow H6 signal using StrataTrade's
        funding rate in place of Binance's. Stored for divergence tracking
        only; NEVER used for canonical decisions.

    Returns
    -------
    dict with keys:
        h6_canonical (int)          : 0 or 1
        vol_regime (str)
        funding_regime (str)
        market_regime (str)
        rv_21d (float)
        sma200 (float)
        decision_hash (str)         : SHA-256 of canonical inputs + VERSION
        h6_shadow (int|None)        : shadow signal from StrataTrade
        strata_funding_regime (str|None)
        regime_diverges (int|None)  : 1 if funding regimes differ
        error (str|None)            : set if insufficient history
    """
    close   = history_df["spot_close"].to_numpy(float)
    funding = history_df["funding_bps"].to_numpy(float)
    day_ts  = int(history_df["day_ts"].iloc[-1]) if "day_ts" in history_df.columns else 0

    if len(close) < MIN_HISTORY:
        return {
            "h6_canonical": 0, "vol_regime": "unknown",
            "funding_regime": "unknown", "market_regime": "unknown",
            "rv_21d": float("nan"), "sma200": float("nan"),
            "decision_hash": "", "h6_shadow": None,
            "strata_funding_regime": None, "regime_diverges": None,
            "error": f"insufficient history: {len(close)} rows < {MIN_HISTORY}",
        }

    vol_r,  rv21  = _vol_regime(close)
    fund_r        = _funding_regime(funding)
    mkt_r,  sma   = _market_regime(close)
    signal        = h6_decide(vol_r, fund_r, mkt_r)

    payload   = f"{day_ts}|{vol_r}|{fund_r}|{mkt_r}|{VERSION}"
    dec_hash  = hashlib.sha256(payload.encode()).hexdigest()

    strata_fund_r: Optional[str] = None
    h6_shadow_v:   Optional[int] = None
    diverges:      Optional[int] = None

    if strata_funding_bps is not None:
        # Shadow: replace today's Binance funding with StrataTrade's value
        strata_arr           = funding.copy()
        strata_arr[-1]       = strata_funding_bps
        strata_fund_r        = _funding_regime(strata_arr)
        h6_shadow_v          = h6_decide(vol_r, strata_fund_r, mkt_r)
        diverges             = int(strata_fund_r != fund_r)

    return {
        "h6_canonical":         signal,
        "vol_regime":           vol_r,
        "funding_regime":       fund_r,
        "market_regime":        mkt_r,
        "rv_21d":               float(rv21),
        "sma200":               float(sma),
        "decision_hash":        dec_hash,
        "h6_shadow":            h6_shadow_v,
        "strata_funding_regime": strata_fund_r,
        "regime_diverges":      diverges,
        "error":                None,
    }


# ============================================================================
# Specification record (embedded; duplicated in pre_registration.json)
# ============================================================================

FUNCTION_SPEC: dict = {
    "name":            "H6 Funding × Vol × Market-Regime",
    "version":         VERSION,
    "signal_class":    "regime-conditional long-only",
    "data_source":     "Binance BTCUSDT spot close + USDT-perp daily mean funding bps",
    "entry_condition": (
        "vol_regime == 'medium' "
        "AND funding_regime == 'low' "
        "AND market_regime not in {'bear', 'unknown'}"
    ),
    "direction":       "long_only",
    "hold_days":       HOLD_DAYS,
    "exit_rule":       "scheduled at entry + 7 calendar days; no intra-hold exits in Phase E demo",
    "parameters": {
        "RV_WINDOW":     RV_WINDOW,
        "REGIME_WINDOW": REGIME_WINDOW,
        "REGIME_PCTILE": REGIME_PCTILE,
        "SMA_WINDOW":    SMA_WINDOW,
        "SLOPE_LAG":     SLOPE_LAG,
        "HOLD_DAYS":     HOLD_DAYS,
        "ANN":           ANN,
        "MIN_HISTORY":   MIN_HISTORY,
    },
    "regime_definitions": {
        "vol_regime": (
            f"RV{RV_WINDOW} annualized (std of log-returns × √{ANN:.0f}); "
            f"trailing {REGIME_WINDOW}d tercile. "
            f"low < {REGIME_PCTILE}th pctile; high > {100-REGIME_PCTILE}th pctile."
        ),
        "funding_regime": (
            f"Binance USDT-perp daily mean funding rate × 10000 (bps); "
            f"trailing {REGIME_WINDOW}d tercile, same boundaries."
        ),
        "market_regime": (
            f"SMA{SMA_WINDOW} + {SLOPE_LAG}d slope. "
            "bull = close > rising SMA200; "
            "bear = close < falling SMA200; "
            "else sideways. unknown until 220d history."
        ),
    },
    "cost_assumption_bps":   15,
    "governance_phase":      "Phase E forward evidence collection",
    "pre_registration_date": "2026-06-10",
    "lockbox_status":        "spent in D.5 (2025-06-01 to 2026-05-31); never reused",
    "evidence_basis": {
        "IS_sharpe_7d":     2.22,
        "DSR":              0.961,
        "BH_p":             0.0065,
        "phase_c_duration_peak_sharpe_1d": 3.49,
        "lockbox_verdict":  "REJECT BOTH (regime absent in -30.3% bear year)",
        "integration_rec":  "B: demo-only research strategy",
    },
}
