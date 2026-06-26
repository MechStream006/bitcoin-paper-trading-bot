"""runner_atr_expansion.py — thin forward-evidence runner for g3_atr_expansion.

G3 ATR Expansion is an OHLCV-based signal (requires daily high/low/close from
1m bars). This runner uses run_ohlcv_signal() — not run_signal() — because True
Range = max(H-L, |H-prev_C|, |L-prev_C|) needs the genuine daily high and low,
not just the close price.

collect() signature differs from the close-only runners (H5/G1/G3) by accepting
an `ohlcv` DataFrame instead of a `closes` Series. It must be invoked on its own
isolated OHLCV branch in run_daily.py, never inside the uniform 3-runner loop.
"""
from __future__ import annotations

import pathlib

import pandas as pd

from forward_evidence import decision_function_g3_atr_expansion as _df
from forward_evidence.runner_base import run_ohlcv_signal

_HERE     = pathlib.Path(__file__).resolve().parent
_PRE_REG  = _HERE / "pre_registration_g3_atr_expansion.json"
_FN_FILE  = _HERE / "decision_function_g3_atr_expansion.py"
SIGNAL_ID = "g3_atr_expansion"


def _raw_value(ohlcv: pd.DataFrame) -> float | None:
    """
    ATR expansion ratio: current ATR / lagged ATR − 1.

    Positive value = ATR is rising (expansion); negative = contracting.
    Auxiliary value stored in signal_log.raw_value; NOT covered by fn_hash.
    Uses the same _atr() primitive as decision_function_g3_atr_expansion.
    """
    atr     = _df._atr(ohlcv)
    current = atr.iloc[-1]
    lagged  = atr.shift(_df.LAG).iloc[-1]
    if pd.isna(current) or pd.isna(lagged) or lagged == 0.0:
        return None
    return float(current / lagged - 1.0)


def collect(
    run_date: str,
    ohlcv: pd.DataFrame,
    db_path: pathlib.Path,
    cost_bps: float,
) -> str:
    """Run one day's forward collection for g3_atr_expansion. Returns 'ok'/'skipped'."""
    return run_ohlcv_signal(
        signal_id=SIGNAL_ID,
        run_date=run_date,
        ohlcv=ohlcv,
        compute_fn=_df.compute_signal,
        raw_value_fn=_raw_value,
        pre_reg_path=_PRE_REG,
        fn_file_path=_FN_FILE,
        db_path=db_path,
        hold_days=_df.HOLD_DAYS,
        min_history=_df.MIN_HISTORY,
        cost_bps=cost_bps,
    )
