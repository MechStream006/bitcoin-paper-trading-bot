"""runner_g2.py — thin forward-evidence runner for g2_funding_level.

g2 is a SINGLE-input signal, but its one input is funding, not spot closes.
run_signal() prices trades on `closes` and passes `aux_series` as the second
argument to compute_fn, so funding is supplied as aux_series and the decision
function receives it as its only meaningful argument. Spot closes remain the
pricing series — funding is never used to price a trade.

That is why collect() takes both series and why this runner must be invoked on
its own branch rather than inside the uniform single-input loop, exactly as
runner_ivrv is. The locked computation lives in
decision_function_g2_funding_level.py.
"""
from __future__ import annotations

import pathlib

import pandas as pd

from forward_evidence import decision_function_g2_funding_level as _df
from forward_evidence.runner_base import run_signal

_HERE     = pathlib.Path(__file__).resolve().parent
_PRE_REG  = _HERE / "pre_registration_g2_funding_level.json"
_FN_FILE  = _HERE / "decision_function_g2_funding_level.py"
SIGNAL_ID = "g2_funding_level"


def _compute(closes: pd.Series, funding: pd.Series) -> pd.Series:
    """Adapt run_signal's (closes, aux) call shape to the locked single-input
    decision function. Contains no decision logic: closes are ignored here
    because g2 is a pure funding signal, and the returned series is exactly
    what compute_signal produced."""
    return _df.compute_signal(funding)


def _raw_value(closes: pd.Series, funding: pd.Series) -> float | None:
    """Current daily mean funding in bps. Auxiliary value stored in
    signal_log.raw_value; NOT covered by the fn_hash."""
    if len(funding) == 0:
        return None
    val = funding.iloc[-1]
    return float(val) if pd.notna(val) else None


def collect(
    run_date: str,
    closes: pd.Series,
    funding: pd.Series,
    db_path: pathlib.Path,
    cost_bps: float,
) -> str:
    """Run one day's forward collection for g2_funding_level. Returns 'ok'/'skipped'."""
    return run_signal(
        signal_id=SIGNAL_ID,
        run_date=run_date,
        closes=closes,
        compute_fn=_compute,
        raw_value_fn=_raw_value,
        pre_reg_path=_PRE_REG,
        fn_file_path=_FN_FILE,
        db_path=db_path,
        hold_days=_df.HOLD_DAYS,
        min_history=_df.MIN_HISTORY,
        cost_bps=cost_bps,
        aux_series=funding,
    )
