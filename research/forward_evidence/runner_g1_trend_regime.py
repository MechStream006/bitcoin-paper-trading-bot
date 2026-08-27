"""runner_g1_trend_regime.py — thin forward-evidence runner for g1_trend_regime_ma_cross."""
from __future__ import annotations

import pathlib

import pandas as pd

from forward_evidence import decision_function_g1_trend_regime as _df
from forward_evidence.runner_base import run_signal

_HERE     = pathlib.Path(__file__).resolve().parent
_PRE_REG  = _HERE / "pre_registration_g1_trend_regime.json"
_FN_FILE  = _HERE / "decision_function_g1_trend_regime.py"
SIGNAL_ID = "g1_trend_regime_ma_cross"


def _raw_value(closes: pd.Series) -> float | None:
    """SMA spread (SMA[FAST] − SMA[SLOW]) at run_date. Auxiliary; not fn_hash-covered."""
    sf = closes.rolling(_df.FAST, min_periods=_df.FAST).mean().iloc[-1]
    ss = closes.rolling(_df.SLOW, min_periods=_df.SLOW).mean().iloc[-1]
    spread = sf - ss
    return float(spread) if pd.notna(spread) else None


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
