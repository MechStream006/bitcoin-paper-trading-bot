"""runner_g3.py — thin forward-evidence runner for g3_momentum_tsmom."""
from __future__ import annotations

import pathlib

import numpy as np
import pandas as pd

from forward_evidence import decision_function_g3_tsmom as _df
from forward_evidence.runner_base import run_signal

_HERE     = pathlib.Path(__file__).resolve().parent
_PRE_REG  = _HERE / "pre_registration_g3_tsmom.json"
_FN_FILE  = _HERE / "decision_function_g3_tsmom.py"
SIGNAL_ID = "g3_momentum_tsmom"


def _raw_value(closes: pd.Series) -> float | None:
    """K-day return: close[-1]/close[-K-1] − 1. Auxiliary; not fn_hash-covered."""
    if len(closes) < _df.K + 1:
        return None
    val = closes.iloc[-1] / closes.iloc[-(_df.K + 1)] - 1.0
    return float(val) if pd.notna(val) else None


def collect(
    run_date: str,
    closes: pd.Series,
    db_path: pathlib.Path,
    cost_bps: float,
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
    )
