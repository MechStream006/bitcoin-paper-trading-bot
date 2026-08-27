"""
Input builder for c3_semivariance_asymmetry: 1m spot bars -> daily SJ.

Deliberately a SEPARATE module rather than a loader added to runner_base.py.
Gate A6 is implement-and-validate, not wire-into-collection; runner_base is the
shared path all six collecting signals run through, and it must not be touched
until a wiring gate authorises it (the precedent is G2, whose load_daily_funding
entered runner_base at the dedicated wiring gate 6A.6, not at implementation).
Nothing here is imported by any collecting runner.

Everything below is the Gate A5 frozen specification, section by section. The
tags mark what comes from where, because C3's whole discipline is not confusing
the two:

  [SOURCE]        Barndorff-Nielsen, Kinnebrock & Shephard (2008)
  [PLATFORM-CONV] an existing, already-registered project convention
  [PLATFORM-NEW]  a choice this project makes and owns; the source does not
                  support it and it must never be presented as source-derived

The four PLATFORM-NEW choices, all of them, are: the 1-minute grid; log first
differences; excluding the cross-day return; and the SJ transformation.
"""
from __future__ import annotations

import pathlib

import numpy as np
import pandas as pd

from forward_evidence.runner_base import DataUnavailableError, _select_partitions

#: Bars in a complete UTC day.                      [PLATFORM-CONV, gate 6B.1]
#: A day is admitted only if it holds EXACTLY this many 1m bars. A truncated day
#: would understate both semivariance components with no marker in the record,
#: which is precisely the B-01 failure mode.
MIN_DAILY_BARS = 1440

#: Within-day returns produced by a complete day.   [PLATFORM-NEW, consequence]
#: 1440 bars yield 1439 within-day first differences: the return spanning the
#: boundary from the previous day's last bar to this day's first bar is
#: EXCLUDED. BNKS normalise each day to [0,1] and sum over t_j <= 1, which is
#: consistent with within-day returns -- but BTC trades 24/7 with no session
#: break, so this is a CHOICE, not a necessity. It is made so that a day's value
#: cannot be dominated by a single cross-boundary move.
RETURNS_PER_DAY = MIN_DAILY_BARS - 1  # = 1439


def load_daily_semivariance_asymmetry(
    parquet_dir: pathlib.Path,
    through_date: str,
    min_rows: int,
) -> pd.DataFrame:
    """
    Build daily RS-, RS+, RV and SJ from 1-minute spot bars.

    Parameters
    ----------
    parquet_dir : e.g. research/data/raw/spot/BTCUSDT
    through_date : 'YYYY-MM-DD' inclusive upper bound. Nothing dated after this
        is read, let alone used -- the no-lookahead guarantee is enforced here,
        at the point of ingestion, not downstream.
    min_rows : minimum COMPLETE daily rows required (= signal MIN_HISTORY).

    Returns
    -------
    pd.DataFrame indexed by 'YYYY-MM-DD' strings, chronological and unique,
    columns ['RSm', 'RSp', 'RV', 'SJ', 'n_returns'], ending at through_date.

    Raises
    ------
    DataUnavailableError
        if no partitions exist, if through_date is absent or incomplete, or if
        fewer than min_rows complete days are available. Missing data is always
        a transient skip and never a flat decision (gate 6B.1, defect B-02).
    """
    from datetime import date, timedelta

    through_dt = date.fromisoformat(through_date)
    start_dt   = through_dt - timedelta(days=min_rows + 32)

    partitions = _select_partitions(parquet_dir, start_dt, through_dt)
    if not partitions:
        raise DataUnavailableError(
            f"No OHLCV partitions found in {parquet_dir} for "
            f"{start_dt}..{through_dt}. Run ingest/binance_ohlcv.py to populate."
        )

    raw = pd.concat(
        [pd.read_parquet(p, columns=['open_time', 'close']) for p in partitions],
        ignore_index=True,
    )

    # Timestamp normalisation, identical to runner_base's loaders: 2024 and
    # earlier are 13-digit ms, 2025+ could be 16-digit us.   [PLATFORM-CONV]
    ts = raw['open_time'].astype('int64')
    if ts.max() > 1_500_000_000_000_000:
        ts = ts // 1000
    raw['dt'] = pd.to_datetime(ts, unit='ms', utc=True)

    raw = raw.sort_values('dt', kind='mergesort').reset_index(drop=True)
    raw['day'] = raw['dt'].dt.strftime('%Y-%m-%d')

    # No-lookahead: drop everything after through_date BEFORE any computation,
    # so a later day cannot influence an earlier one through any path.
    raw = raw[raw['day'] <= through_date]
    if raw.empty:
        raise DataUnavailableError(
            f"No 1m bars at or before {through_date} in {parquet_dir}."
        )

    # Completeness: exactly MIN_DAILY_BARS.                  [PLATFORM-CONV]
    bar_counts = raw.groupby('day')['close'].size()
    complete = bar_counts.index[bar_counts == MIN_DAILY_BARS]
    raw = raw[raw['day'].isin(set(complete))]

    if through_date not in set(complete):
        latest = complete[-1] if len(complete) else 'none'
        n = int(bar_counts.get(through_date, 0))
        raise DataUnavailableError(
            f"Incomplete or absent day {through_date}: {n} 1m bars, need "
            f"{MIN_DAILY_BARS}. Latest complete day: {latest}."
        )

    # Log first differences.                                 [PLATFORM-NEW]
    raw['r'] = np.log(raw['close'].astype(float)).diff()
    # Exclude the cross-day return: the first bar of each day has no within-day
    # predecessor.                                           [PLATFORM-NEW]
    raw.loc[raw['day'] != raw['day'].shift(), 'r'] = np.nan
    raw = raw.dropna(subset=['r'])

    sq = raw['r'].to_numpy(float) ** 2
    r  = raw['r'].to_numpy(float)
    # [SOURCE] RS- takes r <= 0 and RS+ takes r >= 0; a zero return is counted in
    # BOTH and contributes exactly zero to each, so RS- + RS+ = RV identically.
    frame = pd.DataFrame({
        'day': raw['day'].to_numpy(),
        'sq_neg': np.where(r <= 0.0, sq, 0.0),
        'sq_pos': np.where(r >= 0.0, sq, 0.0),
        'one': 1,
    })
    agg = frame.groupby('day', sort=True).agg(
        RSm=('sq_neg', 'sum'),
        RSp=('sq_pos', 'sum'),
        n_returns=('one', 'sum'),
    )

    agg['RV'] = agg['RSm'] + agg['RSp']
    # SJ := NaN when RV == 0 -> the decision function maps it to FLAT. A day with
    # no price movement carries no asymmetry information; it must not be coerced
    # into a decision.                                       [PLATFORM-CONV]
    agg['SJ'] = np.where(agg['RV'] > 0.0, (agg['RSp'] - agg['RSm']) / agg['RV'], np.nan)

    out = agg[['RSm', 'RSp', 'RV', 'SJ', 'n_returns']]

    if len(out) < min_rows:
        raise DataUnavailableError(
            f"Insufficient complete-day history: need {min_rows} rows, "
            f"have {len(out)}. Run ingest/binance_ohlcv.py to extend the store."
        )
    return out
