"""runner_h5.py — thin forward-evidence runner for h5_vol_regime."""
from __future__ import annotations

import pathlib

import numpy as np
import pandas as pd

from forward_evidence import decision_function_h5 as _df
from forward_evidence.runner_base import run_signal

_HERE        = pathlib.Path(__file__).resolve().parent
_PRE_REG     = _HERE / "pre_registration_h5_vol_regime.json"
_FN_FILE     = _HERE / "decision_function_h5.py"
SIGNAL_ID    = "h5_vol_regime"


def _raw_value(closes: pd.Series) -> float | None:
    """Annualised 21-day realized vol at the last bar. Auxiliary; not fn_hash-covered."""
    log_ret = np.log(closes / closes.shift(1))
    rv = log_ret.rolling(_df.RV_WINDOW).std() * np.sqrt(_df.ANN)
    val = rv.iloc[-1]
    return float(val) if pd.notna(val) else None


def collect(
    run_date: str,
    closes: pd.Series,
    db_path: pathlib.Path,
    cost_bps: float,
    inputs: list[dict] | None = None,
) -> str:
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
        inputs=inputs,
    )
