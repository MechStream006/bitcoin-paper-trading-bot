"""runner_g1.py — thin forward-evidence runner for g1_market_regime_sma200."""
from __future__ import annotations

import pathlib

import numpy as np
import pandas as pd

from forward_evidence import decision_function_g1_market_regime as _df
from forward_evidence.runner_base import run_signal

_HERE     = pathlib.Path(__file__).resolve().parent
_PRE_REG  = _HERE / "pre_registration_g1_market_regime_sma200.json"
_FN_FILE  = _HERE / "decision_function_g1_market_regime.py"
SIGNAL_ID = "g1_market_regime_sma200"


def _raw_value(closes: pd.Series) -> float | None:
    """SMA200 slope delta (SMA[today] − SMA[today−LAG]). Auxiliary; not fn_hash-covered."""
    sma   = closes.rolling(_df.WIN).mean()
    slope = sma.iloc[-1] - sma.shift(_df.LAG).iloc[-1]
    return float(slope) if pd.notna(slope) else None


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
