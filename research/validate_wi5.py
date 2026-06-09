"""
WI-5 validation: research/ingest/calendar.py + research/data/calendar/macro_events.csv

Run from the research/ directory:
    python validate_wi5.py

No network calls.  Uses only the tracked CSV and in-memory DuckDB.

Phases:
  A. Schema validation     — required columns, type checks, date/time format checks
  B. Duplicate handling    — duplicate rows are rejected with a clear ValueError
  C. DuckDB validation     — macro_events view queryable, aggregation queries work
  D. Coverage validation   — FOMC == 40, CPI == 60, HALVING == 1, total == 101
"""
from __future__ import annotations

import io
import pathlib
import sys
import textwrap

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from ingest.calendar import (
    load_and_validate,
    register_macro_events,
    REQUIRED_COLUMNS,
    ALLOWED_TYPES,
)
from storage.duck import open_db, query

CALENDAR_PATH = pathlib.Path(__file__).parent / "data" / "calendar" / "macro_events.csv"

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
errors: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    tag = PASS if ok else FAIL
    print(f"  [{tag}] {label}" + (f"  ({detail})" if detail else ""))
    if not ok:
        errors.append(label)


def section(title: str) -> None:
    print(f"\n── {title} ──")


def make_csv(rows: list[str]) -> io.StringIO:
    """Build a tiny in-memory CSV for error-path tests."""
    header = ",".join(REQUIRED_COLUMNS)
    content = header + "\n" + "\n".join(rows) + "\n"
    buf = io.StringIO(content)
    buf.name = "<test-csv>"  # make pathlib-free usage work
    return buf


def raises_value_error(fn, *args, **kwargs) -> tuple[bool, str]:
    """Return (True, message) if fn raises ValueError, (False, repr) otherwise."""
    try:
        fn(*args, **kwargs)
        return False, "no exception raised"
    except ValueError as exc:
        return True, str(exc)
    except Exception as exc:
        return False, repr(exc)


# ─────────────────────────────────────────────────────────────────────────────
# Phase A — Schema validation
# ─────────────────────────────────────────────────────────────────────────────
section("Phase A — Schema validation")

# A1. Production CSV loads cleanly
df_prod = load_and_validate(CALENDAR_PATH)
check("production CSV loads without error", df_prod is not None)
check("all required columns present",
      set(REQUIRED_COLUMNS) <= set(df_prod.columns))
check("timestamp_ms column computed",    "timestamp_ms" in df_prod.columns)

# A2. Dtypes
check("event_date dtype is object (str)", df_prod["event_date"].dtype == object)
check("timestamp_ms dtype is int64",      df_prod["timestamp_ms"].dtype == "int64")

# A3. event_type values restricted to allowed set
bad_types = set(df_prod["event_type"]) - ALLOWED_TYPES
check("all event_type values are in allowed set", len(bad_types) == 0,
      f"unexpected: {bad_types}")

# A4. Missing required column → ValueError
good_row = "2024-01-01,12:30,FOMC,test"
csv_no_type = io.StringIO("event_date,event_time_utc,description\n2024-01-01,12:30,test\n")
csv_no_type.name = "<test>"
ok, msg = raises_value_error(load_and_validate, csv_no_type)
check("missing 'event_type' column raises ValueError", ok, msg[:80] if not ok else "")

# A5. Invalid event_type value → ValueError
csv_bad_type = make_csv(["2024-01-01,12:30,UNKNOWN_TYPE,test"])
ok, msg = raises_value_error(load_and_validate, csv_bad_type)
check("invalid event_type raises ValueError", ok, msg[:80] if not ok else "")

# A6. Bad date format → ValueError
csv_bad_date = make_csv(["01/01/2024,12:30,FOMC,test"])
ok, msg = raises_value_error(load_and_validate, csv_bad_date)
check("invalid date format raises ValueError", ok, msg[:80] if not ok else "")

# A7. Bad time format → ValueError
csv_bad_time = make_csv(["2024-01-01,6:00PM,FOMC,test"])  # not HH:MM 24h
ok, msg = raises_value_error(load_and_validate, csv_bad_time)
check("invalid time format raises ValueError", ok, msg[:80] if not ok else "")

# A8. UTC timestamp correctness: FOMC 2024-06-12 18:00 UTC
# Expected: 2024-06-12T18:00:00Z = 1718215200000 ms
from datetime import datetime, timezone
expected_ts = int(datetime(2024, 6, 12, 18, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
fomc_jun24 = df_prod[
    (df_prod["event_date"] == "2024-06-12") & (df_prod["event_type"] == "FOMC")
]
check("FOMC 2024-06-12 18:00 UTC timestamp correct",
      len(fomc_jun24) == 1 and int(fomc_jun24["timestamp_ms"].iloc[0]) == expected_ts,
      f"got {fomc_jun24['timestamp_ms'].iloc[0] if len(fomc_jun24) else 'no row'}, "
      f"expected {expected_ts}")

# A9. HALVING timestamp: 2024-04-19 20:09 UTC
expected_halving_ts = int(datetime(2024, 4, 19, 20, 9, 0, tzinfo=timezone.utc).timestamp() * 1000)
halving_row = df_prod[df_prod["event_type"] == "HALVING"]
check("HALVING 2024-04-19 20:09 UTC timestamp correct",
      len(halving_row) == 1 and int(halving_row["timestamp_ms"].iloc[0]) == expected_halving_ts,
      f"got {halving_row['timestamp_ms'].iloc[0] if len(halving_row) else 'no row'}, "
      f"expected {expected_halving_ts}")

# A10. Rows are sorted by timestamp_ms after load
check("rows sorted by timestamp_ms",
      bool((df_prod["timestamp_ms"].diff().dropna() >= 0).all()))


# ─────────────────────────────────────────────────────────────────────────────
# Phase B — Duplicate handling
# ─────────────────────────────────────────────────────────────────────────────
section("Phase B — Duplicate handling")

# B1. Exact duplicate row → rejected
csv_dup_exact = make_csv([
    "2024-01-11,12:30,CPI,US CPI release",
    "2024-01-11,12:30,CPI,US CPI release",   # exact duplicate
])
ok, msg = raises_value_error(load_and_validate, csv_dup_exact)
check("exact duplicate row raises ValueError", ok, msg[:100] if not ok else "")

# B2. Same date + type but different description → also rejected (key is date+type)
csv_dup_desc = make_csv([
    "2024-01-11,12:30,CPI,First description",
    "2024-01-11,13:00,CPI,Second description",   # same date+type, different time
])
ok, msg = raises_value_error(load_and_validate, csv_dup_desc)
check("same date+type with different time/desc raises ValueError",
      ok, msg[:100] if not ok else "")

# B3. Same date but different type → allowed (FOMC + CPI on same day is real)
csv_same_date_diff_type = make_csv([
    "2024-06-12,12:30,CPI,US CPI release",
    "2024-06-12,18:00,FOMC,FOMC rate decision",
])
try:
    df_same = load_and_validate(csv_same_date_diff_type)
    check("same date but different event_type accepted (not a duplicate)",
          len(df_same) == 2)
except ValueError as exc:
    check("same date but different event_type accepted (not a duplicate)",
          False, str(exc)[:80])


# ─────────────────────────────────────────────────────────────────────────────
# Phase C — DuckDB validation
# ─────────────────────────────────────────────────────────────────────────────
section("Phase C — DuckDB: view registration and queries")

con    = open_db()
n_rows = register_macro_events(con, CALENDAR_PATH)
check("register_macro_events returns total row count", n_rows == 101, f"got {n_rows}")

# Basic count
ct = query(con, "SELECT COUNT(*) AS n FROM macro_events")
check("macro_events view queryable (COUNT works)", int(ct["n"].iloc[0]) == n_rows,
      f"duckdb={ct['n'].iloc[0]}, expected={n_rows}")

# Count by event_type
by_type = query(con,
    "SELECT event_type, COUNT(*) AS n FROM macro_events "
    "GROUP BY event_type ORDER BY event_type")
type_counts = dict(zip(by_type["event_type"], by_type["n"].astype(int)))
check("DuckDB returns correct types", set(type_counts.keys()) == {"CPI", "FOMC", "HALVING"},
      str(type_counts))
check("FOMC count from DuckDB == 40", type_counts.get("FOMC") == 40,
      f"got {type_counts.get('FOMC')}")
check("CPI count from DuckDB == 60",  type_counts.get("CPI") == 60,
      f"got {type_counts.get('CPI')}")
check("HALVING count from DuckDB == 1", type_counts.get("HALVING") == 1,
      f"got {type_counts.get('HALVING')}")

# Range query: events in 2024
events_2024 = query(con,
    "SELECT COUNT(*) AS n FROM macro_events WHERE event_date LIKE '2024-%'")
check("date-filter query works (events in 2024)", int(events_2024["n"].iloc[0]) > 0,
      f"got {int(events_2024['n'].iloc[0])}")

# timestamp_ms filter: halving is in April 2024
from datetime import datetime, timezone as tz_utc
apr_start = int(datetime(2024, 4, 1, tzinfo=tz_utc.utc).timestamp() * 1000)
apr_end   = int(datetime(2024, 4, 30, 23, 59, 59, tzinfo=tz_utc.utc).timestamp() * 1000)
# April 2024 has 2 events: CPI (Apr 10) + HALVING (Apr 19).
# The test verifies that timestamp_ms range filtering works and that the
# HALVING event is present in the results.
halving_q = query(con,
    f"SELECT event_type, event_date FROM macro_events "
    f"WHERE timestamp_ms BETWEEN {apr_start} AND {apr_end}")
check("timestamp_ms range query finds HALVING in April 2024",
      "HALVING" in halving_q["event_type"].values,
      f"rows={len(halving_q)}, types={halving_q['event_type'].tolist()}")

# Numeric aggregation: earliest and latest timestamps
bounds = query(con, "SELECT MIN(timestamp_ms) AS lo, MAX(timestamp_ms) AS hi FROM macro_events")
lo_ts = int(bounds["lo"].iloc[0])
hi_ts = int(bounds["hi"].iloc[0])
expected_lo = int(datetime(2021, 6, 10, 12, 30, tzinfo=tz_utc.utc).timestamp() * 1000)
check("earliest timestamp is 2021-06-10 12:30 UTC (CPI)", lo_ts == expected_lo,
      f"got {lo_ts}, expected {expected_lo}")
check("latest timestamp > 2026-01-01 UTC", hi_ts > int(datetime(2026, 1, 1, tzinfo=tz_utc.utc).timestamp() * 1000),
      f"got {hi_ts}")

con.close()


# ─────────────────────────────────────────────────────────────────────────────
# Phase D — Coverage validation
# ─────────────────────────────────────────────────────────────────────────────
section("Phase D — Coverage: expected event counts")

counts = df_prod["event_type"].value_counts()

check("FOMC count == 40",    counts.get("FOMC", 0) == 40,    f"got {counts.get('FOMC', 0)}")
check("CPI count == 60",     counts.get("CPI", 0) == 60,     f"got {counts.get('CPI', 0)}")
check("HALVING count == 1",  counts.get("HALVING", 0) == 1,  f"got {counts.get('HALVING', 0)}")
check("total == 101",        len(df_prod) == 101,              f"got {len(df_prod)}")

# Verify no events after today's date (2026-06-09) — seed data is complete through today
from datetime import date
today_str = "2026-06-09"
future = df_prod[df_prod["event_date"] > today_str]
check("no events dated after 2026-06-09 (today)",
      len(future) == 0, f"found {len(future)} future rows: {future['event_date'].tolist()}")

# Verify all years 2021-2025 have FOMC entries
for yr in range(2021, 2026):
    yr_fomc = df_prod[(df_prod["event_type"] == "FOMC") &
                      (df_prod["event_date"].str.startswith(str(yr)))]
    check(f"FOMC entries present for {yr}", len(yr_fomc) >= 5,
          f"found {len(yr_fomc)}")

# CPI: every year 2021-2025 has at least 7 entries
for yr in range(2021, 2026):
    yr_cpi = df_prod[(df_prod["event_type"] == "CPI") &
                     (df_prod["event_date"].str.startswith(str(yr)))]
    check(f"CPI entries present for {yr}", len(yr_cpi) >= 7,
          f"found {len(yr_cpi)}")


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
print()
if errors:
    print(f"FAILED — {len(errors)} check(s) failed:")
    for e in errors:
        print(f"  ✗ {e}")
    sys.exit(1)
else:
    print("WI-5 PASSED — calendar.py and macro_events.csv are correct.")
    print(f"Seed calendar: {len(df_prod)} events "
          f"(FOMC={counts.get('FOMC')}, CPI={counts.get('CPI')}, HALVING={counts.get('HALVING')})")
