"""
Macro event calendar — load, validate, and register macro_events.csv (WI-5).

The CSV is hand-curated and tracked in git (unlike the Parquet lake which is
gitignored).  This module validates the schema, normalises timestamps to UTC ms,
detects duplicates, and exposes the calendar as a DuckDB view for research queries.

Dates are best-effort from public schedules (Fed, BLS).  Verify against official
sources before using in event-study analysis.

Public API:
    load_and_validate(csv_path)  -> pd.DataFrame
    register_macro_events(con, csv_path) -> int
"""
from __future__ import annotations

import pathlib
import sys
from datetime import datetime, timezone

import pandas as pd

_RESEARCH_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_ROOT))

# ── Schema constants ──────────────────────────────────────────────────────────
REQUIRED_COLUMNS  = ["event_date", "event_time_utc", "event_type", "description"]
ALLOWED_TYPES     = {"FOMC", "CPI", "HALVING"}
DATE_FMT          = "%Y-%m-%d"
TIME_FMT          = "%H:%M"
DUPLICATE_KEY     = ["event_date", "event_type"]   # two events same date+type = error


def _parse_timestamp_ms(date_str: str, time_str: str) -> int:
    """Combine YYYY-MM-DD + HH:MM into a UTC millisecond timestamp."""
    dt = datetime.strptime(f"{date_str} {time_str}", f"{DATE_FMT} {TIME_FMT}")
    return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1_000)


def load_and_validate(csv_path: str | pathlib.Path) -> pd.DataFrame:
    """
    Load and validate macro_events.csv.

    Checks performed (in order):
      1. Required columns present.
      2. event_type values in {FOMC, CPI, HALVING}.
      3. event_date parseable as YYYY-MM-DD.
      4. event_time_utc parseable as HH:MM.
      5. No duplicate (event_date, event_type) pairs.

    Returns a DataFrame with the original columns plus a computed column:
        timestamp_ms  int64   UTC milliseconds since epoch

    Raises ValueError for any violation, listing all offending rows.
    """
    df = pd.read_csv(csv_path, dtype=str)
    df.columns = [c.strip() for c in df.columns]

    # ── 1. Required columns ───────────────────────────────────────────────────
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required column(s): {missing}")

    df = df[REQUIRED_COLUMNS].copy()

    # ── 2. event_type values ──────────────────────────────────────────────────
    bad_types = df[~df["event_type"].isin(ALLOWED_TYPES)]["event_type"].unique().tolist()
    if bad_types:
        raise ValueError(
            f"Invalid event_type value(s): {bad_types}. Allowed: {sorted(ALLOWED_TYPES)}"
        )

    # ── 3 & 4. Date and time parsing ──────────────────────────────────────────
    date_errors: list[str] = []
    time_errors: list[str] = []
    ts_list: list[int] = []

    for idx, row in df.iterrows():
        try:
            datetime.strptime(row["event_date"], DATE_FMT)
        except ValueError:
            date_errors.append(f"row {idx}: {row['event_date']!r}")

        try:
            datetime.strptime(row["event_time_utc"], TIME_FMT)
        except ValueError:
            time_errors.append(f"row {idx}: {row['event_time_utc']!r}")

        # Compute timestamp only when both fields are parseable
        if not date_errors and not time_errors:
            ts_list.append(_parse_timestamp_ms(row["event_date"], row["event_time_utc"]))

    if date_errors:
        raise ValueError(f"Unparseable event_date (expected YYYY-MM-DD): {date_errors}")
    if time_errors:
        raise ValueError(f"Unparseable event_time_utc (expected HH:MM): {time_errors}")

    df["timestamp_ms"] = pd.array(ts_list, dtype="int64")

    # ── 5. Duplicate detection ────────────────────────────────────────────────
    dupes = df[df.duplicated(subset=DUPLICATE_KEY, keep=False)]
    if not dupes.empty:
        pairs = dupes[DUPLICATE_KEY].drop_duplicates().to_dict("records")
        raise ValueError(
            f"Duplicate (event_date, event_type) pairs found — each date+type must be unique:\n"
            + "\n".join(f"  {p}" for p in pairs)
        )

    df = df.sort_values("timestamp_ms").reset_index(drop=True)
    return df


def register_macro_events(
    con,  # duckdb.DuckDBPyConnection — typed loosely to avoid import at module level
    csv_path: str | pathlib.Path,
) -> int:
    """
    Load, validate, and register a 'macro_events' DuckDB view on *con*.
    Returns the number of events registered.
    Caller owns the connection lifetime; the view is tied to that connection.

    Use query() from storage.duck to run SQL against the registered view:
        from storage.duck import open_db, query
        con = open_db()
        register_macro_events(con, csv_path)
        df = query(con, "SELECT event_type, COUNT(*) FROM macro_events GROUP BY 1")
    """
    df = load_and_validate(csv_path)
    con.register("macro_events", df)
    return len(df)


# ── CLI ───────────────────────────────────────────────────────────────────────
def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Validate and summarise macro_events.csv")
    ap.add_argument(
        "csv_path",
        nargs="?",
        default=str(_RESEARCH_ROOT / "data" / "calendar" / "macro_events.csv"),
    )
    args = ap.parse_args()

    df = load_and_validate(args.csv_path)
    print(f"OK — {len(df)} events loaded from {args.csv_path}")
    print(df.groupby("event_type").size().to_string())


if __name__ == "__main__":
    main()
