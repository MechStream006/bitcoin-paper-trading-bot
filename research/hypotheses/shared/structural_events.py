"""
BTC structural event list for Phase B research.

Events serve three roles:
  1. Visual annotations on time series charts
  2. Exclusion windows in clean-sample sensitivity tests
     (±EXCLUSION_DAYS around each event)
  3. Sub-period breakpoints for regime stability checks

Anti-lookahead: event dates are fixed historical facts known before
any analysis begins. No price data is embedded here. [OK]
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# (date_str, key, type, short_label)
STRUCTURAL_EVENTS: list[tuple[str, str, str, str]] = [
    ("2021-05-19", "china_mining_ban",       "regulation",     "China ban"),
    ("2022-05-09", "luna_ust_collapse",       "contagion",      "LUNA"),
    ("2022-06-13", "celsius_halt",            "contagion",      "Celsius"),
    ("2022-07-05", "3ac_liquidation",         "contagion",      "3AC"),
    ("2022-11-11", "ftx_bankruptcy",          "contagion",      "FTX"),
    ("2023-03-10", "svb_usdc_depeg",          "macro_contagion","SVB"),
    ("2023-11-21", "binance_doj_settlement",  "regulation",     "BNB DOJ"),
    ("2024-01-10", "btc_spot_etf_approval",   "structural",     "ETF"),
    ("2024-04-20", "btc_halving_4",           "supply",         "Halving"),
    ("2024-07-05", "mtgox_distributions",     "supply",         "MtGox"),
]

EXCLUSION_DAYS = 7   # default ± window for clean-sample tests

# Colour per event type for chart annotations
TYPE_COLORS = {
    "regulation":     "#e67e22",
    "contagion":      "#c0392b",
    "macro_contagion":"#8e44ad",
    "structural":     "#2980b9",
    "supply":         "#27ae60",
}


def events_df() -> pd.DataFrame:
    df = pd.DataFrame(
        STRUCTURAL_EVENTS,
        columns=["date", "key", "type", "label"],
    )
    df["date"] = pd.to_datetime(df["date"], utc=True)
    return df.sort_values("date").reset_index(drop=True)


def build_exclusion_mask_ms(
    timestamps_ms: np.ndarray,
    window_days: int = EXCLUSION_DAYS,
) -> np.ndarray:
    """
    Boolean array (True = exclude).  Input timestamps_ms is an int64 array of
    UTC milliseconds.  Excludes any timestamp within window_days of an event.
    """
    window_ms = int(window_days) * 86_400 * 1_000
    mask = np.zeros(len(timestamps_ms), dtype=bool)
    for date_str, *_ in STRUCTURAL_EVENTS:
        ev_ms = int(pd.Timestamp(date_str, tz="UTC").timestamp() * 1_000)
        mask |= (timestamps_ms >= ev_ms - window_ms) & (
            timestamps_ms <= ev_ms + window_ms
        )
    return mask


def build_exclusion_mask_dates(
    date_index: pd.DatetimeIndex,
    window_days: int = EXCLUSION_DAYS,
) -> pd.Series:
    """Boolean Series aligned to date_index (True = exclude)."""
    mask = pd.Series(False, index=date_index)
    ev_df = events_df()
    for ev_date in ev_df["date"]:
        lo = ev_date - pd.Timedelta(days=window_days)
        hi = ev_date + pd.Timedelta(days=window_days)
        mask |= (date_index >= lo) & (date_index <= hi)
    return mask
