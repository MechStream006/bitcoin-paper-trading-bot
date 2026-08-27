"""
Faithful Python port of the Pine v5 core signal path of

    Mutanabby_AI | Ultimate Algo | Remastered+

Authoritative source : D:\\mutanabby_ai_ultimate_algo.pine.txt
                       sha256 75f1914a2f3e982cfc36eeb98edc142b494dcce2745e0707865389e77acc379d
Frozen specification : Gate M-2
Equivalence fixture  : Gate M-3, sha256 33825fbe7a10e5a84289fdd1da64c207d6d610c905d295dac7c2738d8cefe0cc

RESEARCH REFERENCE IMPLEMENTATION ONLY.
Not a registered signal, not wired to forward collection, not an execution path.
It exists to be proven bar-for-bar identical to TradingView before anyone asks
whether the strategy has edge.

PORTING DISCIPLINE
------------------
Pine executes bar by bar with recursive series state. The SuperTrend bands and
the direction state machine are genuinely recursive and are therefore written as
an explicit loop. They are NOT vectorised, because vectorising them would change
which value `upperBand[1]` refers to.

Nothing here is a "textbook equivalent". In particular:

  * ta.atr(n) is ta.rma(ta.tr(true), n) -- Wilder, SMA-seeded. It is NOT a
    rolling mean. (research/evidence/signals.py::_atr uses a rolling mean; that
    helper is deliberately not reused.)
  * ta.tr(true) returns high-low on the first bar rather than na.
  * nz(x) maps na -> 0.0, and that mapping is load-bearing: it is what makes the
    warm-up bands latch to 0.0 rather than propagate na.
  * Every comparison against na is false in Pine. numpy's NaN comparisons are
    also False, so the plain operators reproduce it -- but the arithmetic must
    still be guarded so NaN propagates where Pine propagates na.
  * The band persistence rule references close[t-1], not close[t].
  * `prevSuperTrend == prevUpperBand` is an exact float equality against the
    STORED previous values. Recomputing either side would break it.

FROZEN PARAMETERS (Pine source line references)
-----------------------------------------------
    SENSITIVITY  = 4      L23 input.int(defval=4), passed as `factor`  L67
    ST_ATR_LEN   = 11     L67 literal argument (NOT the L34 input, which is 14
                          and feeds only the risk-display path)
    SMA_FAST     = 8      L61  source variable `sma4`
    SMA_MED      = 9      L62  source variable `sma5`
    SMA_TREND    = 13     L63  source variable `sma9`
    SMA_RIBBON_1 = 21     L217 `candle`  (visual ribbon; carried for the fixture)
    SMA_RIBBON_2 = 34     L218 `reach`   (visual ribbon; carried for the fixture)
    ST_SOURCE    = close  L67 -- NOT hl2. TradingView's built-in SuperTrend uses
                          hl2; this script does not.

STRENGTH POLARITY (L228-229) -- deliberately counterintuitive, reproduced verbatim:
    bull and sma4 >= sma5  -> "Buy"          (+1)
    bull and sma4 <  sma5  -> "Strong Buy"   (+2)
    bear and sma4 <= sma5  -> "Sell"         (-1)
    bear and sma4 >  sma5  -> "Strong Sell"  (-2)
The published TradingView description states the opposite. The source, and the
source's own comment at L225-226, agree with the encoding above.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

SENSITIVITY = 4.0
ST_ATR_LEN = 11
SMA_FAST = 8
SMA_MED = 9
SMA_TREND = 13
SMA_RIBBON_1 = 21
SMA_RIBBON_2 = 34


def ta_tr_handle_na(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """Pine ``ta.tr(true)``.

    The ``true`` argument is what makes bar 0 return ``high - low`` instead of
    ``na``; that single value seeds the whole ATR recursion, so getting it wrong
    shifts every subsequent ATR.
    """
    n = len(close)
    tr = np.full(n, np.nan)
    if n == 0:
        return tr
    tr[0] = high[0] - low[0]
    prev_close = close[:-1]
    tr[1:] = np.maximum.reduce([
        high[1:] - low[1:],
        np.abs(high[1:] - prev_close),
        np.abs(low[1:] - prev_close),
    ])
    return tr


def ta_rma(x: np.ndarray, length: int) -> np.ndarray:
    """Pine ``ta.rma`` -- Wilder smoothing, seeded with the SMA of the first
    ``length`` values at index ``length-1``, then ``(prev*(n-1) + x)/n``.

    Returns na before the seed index. An ``ewm(alpha=1/n)`` without the SMA seed
    is a different series and would fail the ladder at the ATR stage.
    """
    n = len(x)
    out = np.full(n, np.nan)
    if n < length:
        return out
    seed = length - 1
    out[seed] = np.mean(x[:length])
    prev = out[seed]
    for i in range(seed + 1, n):
        prev = (prev * (length - 1) + x[i]) / length
        out[i] = prev
    return out


def ta_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, length: int) -> np.ndarray:
    """Pine ``ta.atr(length)`` == ``ta.rma(ta.tr(true), length)``."""
    return ta_rma(ta_tr_handle_na(high, low, close), length)


def ta_sma(x: np.ndarray, length: int) -> np.ndarray:
    """Pine ``ta.sma`` -- na for the first ``length-1`` bars."""
    return pd.Series(x).rolling(length, min_periods=length).mean().to_numpy()


def _nz(v: float) -> float:
    """Pine ``nz(x)`` for a float: na -> 0.0."""
    return 0.0 if (v is None or np.isnan(v)) else v


@dataclass(frozen=True)
class SupertrendResult:
    atr: np.ndarray
    upper_basic: np.ndarray
    lower_basic: np.ndarray
    upper_band: np.ndarray
    lower_band: np.ndarray
    direction: np.ndarray
    supertrend: np.ndarray


def supertrend(
    src: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    factor: float = SENSITIVITY,
    atr_len: int = ST_ATR_LEN,
) -> SupertrendResult:
    """Port of the Pine ``supertrend()`` function, source lines 39-57.

    Explicitly recursive, mirroring Pine's bar-by-bar execution. ``direction``
    follows the source convention: **-1 is uptrend** (SuperTrend rides the lower
    band), **+1 is downtrend**, initialised to +1 while ``atr[t-1]`` is na.
    """
    n = len(close)
    atr = ta_atr(high, low, close, atr_len)

    upper_basic = src + factor * atr
    lower_basic = src - factor * atr

    upper_band = np.full(n, np.nan)
    lower_band = np.full(n, np.nan)
    direction = np.zeros(n, dtype=np.int64)
    st = np.full(n, np.nan)

    for i in range(n):
        # nz(band[1]) -- na maps to 0.0, which is what latches the warm-up bands
        prev_lower = _nz(lower_band[i - 1]) if i > 0 else 0.0
        prev_upper = _nz(upper_band[i - 1]) if i > 0 else 0.0
        prev_close = close[i - 1] if i > 0 else np.nan

        # L45-46. Note close[t-1], not close[t]. NaN comparisons are False,
        # matching Pine's "any comparison with na is false".
        lower_band[i] = (
            lower_basic[i]
            if (lower_basic[i] > prev_lower) or (prev_close < prev_lower)
            else prev_lower
        )
        upper_band[i] = (
            upper_basic[i]
            if (upper_basic[i] < prev_upper) or (prev_close > prev_upper)
            else prev_upper
        )

        # L50-55. prevSuperTrend is the raw stored superTrend[1] (not nz'd);
        # prevUpperBand IS nz'd. Compared by exact float equality against the
        # stored values -- never against a recomputed band.
        prev_st = st[i - 1] if i > 0 else np.nan
        if i == 0 or np.isnan(atr[i - 1]):
            direction[i] = 1
        elif prev_st == prev_upper:
            direction[i] = -1 if close[i] > upper_band[i] else 1
        else:
            direction[i] = 1 if close[i] < lower_band[i] else -1

        # L56
        st[i] = lower_band[i] if direction[i] == -1 else upper_band[i]

    return SupertrendResult(
        atr=atr,
        upper_basic=upper_basic,
        lower_basic=lower_basic,
        upper_band=upper_band,
        lower_band=lower_band,
        direction=direction,
        supertrend=st,
    )


def ta_crossover(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Pine ``ta.crossover(a, b)`` == ``a > b and a[1] <= b[1]``.

    Asymmetric on purpose: strict on the current bar, non-strict on the previous.
    """
    out = np.zeros(len(a), dtype=bool)
    out[1:] = (a[1:] > b[1:]) & (a[:-1] <= b[:-1])
    return out


def ta_crossunder(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Pine ``ta.crossunder(a, b)`` == ``a < b and a[1] >= b[1]``."""
    out = np.zeros(len(a), dtype=bool)
    out[1:] = (a[1:] < b[1:]) & (a[:-1] >= b[:-1])
    return out


def compute(ohlc: pd.DataFrame) -> pd.DataFrame:
    """Reproduce the full Pine core signal path for a frame of OHLC bars.

    Parameters
    ----------
    ohlc : columns ``open``/``high``/``low``/``close``, chronological, one row
        per closed bar. ``open`` is unused by the signal path (the source's
        ``ocAvg`` is dead code) and is accepted only so callers can pass a full
        frame.

    Returns
    -------
    DataFrame with one row per input bar and columns matching the M-3 fixture:
    ``tr, atr, upperBasic, lowerBasic, upperBand, lowerBand, direction,
    supertrend, sma4, sma5, sma8, sma9, sma13, sma21, sma34, bull, bear,
    strength``. ``sma4``/``sma8`` and ``sma5``/``sma9`` are the same series --
    the fixture carries both spellings because the Pine variable names and the
    actual periods disagree (M-2 D1).
    """
    high = ohlc["high"].to_numpy(dtype=float)
    low = ohlc["low"].to_numpy(dtype=float)
    close = ohlc["close"].to_numpy(dtype=float)

    st = supertrend(close, high, low, close)

    sma_fast = ta_sma(close, SMA_FAST)      # Pine `sma4`
    sma_med = ta_sma(close, SMA_MED)        # Pine `sma5`
    sma_trend = ta_sma(close, SMA_TREND)    # Pine `sma9`
    ribbon_1 = ta_sma(close, SMA_RIBBON_1)  # Pine `candle`
    ribbon_2 = ta_sma(close, SMA_RIBBON_2)  # Pine `reach`

    # L213-214. Filter comparisons are inclusive (>= / <=).
    bull = ta_crossover(close, st.supertrend) & (close >= sma_trend)
    bear = ta_crossunder(close, st.supertrend) & (close <= sma_trend)

    # L228-229, polarity verbatim.
    strength = np.zeros(len(close), dtype=np.int64)
    strength[bull] = np.where(sma_fast[bull] >= sma_med[bull], 1, 2)
    strength[bear] = np.where(sma_fast[bear] <= sma_med[bear], -1, -2)

    return pd.DataFrame(
        {
            "tr": ta_tr_handle_na(high, low, close),
            "atr": st.atr,
            "upperBasic": st.upper_basic,
            "lowerBasic": st.lower_basic,
            "upperBand": st.upper_band,
            "lowerBand": st.lower_band,
            "direction": st.direction,
            "supertrend": st.supertrend,
            "sma4": sma_fast,
            "sma5": sma_med,
            "sma8": sma_fast,
            "sma9": sma_med,
            "sma13": sma_trend,
            "sma21": ribbon_1,
            "sma34": ribbon_2,
            "bull": bull.astype(np.int64),
            "bear": bear.astype(np.int64),
            "strength": strength,
        },
        index=ohlc.index,
    )
