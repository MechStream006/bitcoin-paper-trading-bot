"""runner_ivrv.py — thin forward-evidence runner for ivrv_vrp_spread (H-IVRV).

H-IVRV (HV-2 VRP Spread) is a TWO-input signal: it needs daily spot closes
(for RV30 and for trade pricing) AND daily Deribit DVOL (implied vol). The DVOL
series is passed to run_signal via aux_series; spot closes remain the pricing
series. The locked computation lives in decision_function_ivrv.py.

This runner is NOT wired into run_daily.py yet (Checkpoint 3). It is exercised
directly by tests at Checkpoint 2. Its collect() signature intentionally differs
from the single-input runners (H5/G1/G3) by taking an extra `dvol` argument, so
it must be invoked on its own branch — never inside the uniform 3-runner loop.
"""
from __future__ import annotations

import pathlib

import numpy as np
import pandas as pd

from forward_evidence import decision_function_ivrv as _df
from forward_evidence.runner_base import run_signal

_HERE     = pathlib.Path(__file__).resolve().parent
_PRE_REG  = _HERE / "pre_registration_ivrv.json"
_FN_FILE  = _HERE / "decision_function_ivrv.py"
SIGNAL_ID = "ivrv_vrp_spread"


def _raw_value(closes: pd.Series, dvol: pd.Series) -> float | None:
    """
    Current VRP spread = DVOL/100 - RV30 at the latest date. Auxiliary value
    stored in signal_log.raw_value; NOT covered by the fn_hash. Uses the same
    primitives as decision_function_ivrv.compute_signal.
    """
    if len(closes) < _df.RV_WIN + 1:
        return None
    log_ret   = np.log(closes / closes.shift(1))
    rv30      = log_ret.rolling(_df.RV_WIN, min_periods=_df.RV_WIN).std() * np.sqrt(_df.ANN)
    dvol_frac = dvol.reindex(closes.index) / 100.0
    vrp       = dvol_frac - rv30
    val       = vrp.iloc[-1]
    return float(val) if pd.notna(val) else None


def collect(
    run_date: str,
    closes: pd.Series,
    dvol: pd.Series,
    db_path: pathlib.Path,
    cost_bps: float,
) -> str:
    """Run one day's forward collection for ivrv_vrp_spread. Returns 'ok'/'skipped'."""
    return run_signal(
        signal_id=SIGNAL_ID,
        run_date=run_date,
        closes=closes,
        compute_fn=_df.compute_signal,
        raw_value_fn=_raw_value,
        pre_reg_path=_PRE_REG,
        fn_file_path=_FN_FILE,
        db_path=db_path,
        hold_days=_df.HOLD_DAYS,
        min_history=_df.MIN_HISTORY,
        cost_bps=cost_bps,
        aux_series=dvol,
    )
