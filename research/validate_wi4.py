"""
WI-4 validation: research/ingest/funding.py

Run from the research/ directory:
    python validate_wi4.py

Ingests Q1 2024 funding data (3 months, ~273 records total, a few seconds).
Data is written to data/raw/funding/BTCUSDT/ (gitignored).
Data is left in place on success — it joins the OHLCV partitions in the lake.

Phases:
  A. Coverage     — first/last timestamp, expected vs actual records, coverage %
  B. Timestamp    — cadence consistency (8h = 28,800,000 ms between consecutive rows)
  C. DuckDB       — view registration, aggregation queries, date filtering
  D. Idempotency  — re-run force=False → skip, no duplicate rows, mtime unchanged
"""
from __future__ import annotations

import calendar
import json
import pathlib
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from ingest.funding import ingest_month, ingest_range, CADENCE_MS, DATASET, SYMBOL_LAKE
from storage.lake import list_partitions, read_partition, partition_path
from storage.duck import open_db, register_view, query

import pandas as pd

BASE_DIR = pathlib.Path(__file__).parent / "data" / "raw"
SYMBOL   = SYMBOL_LAKE            # "BTCUSDT"

# Validation window: Q1 2024
START_YM = (2024, 1)
END_YM   = (2024, 3)

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


def month_bounds_ms(year: int, month: int) -> tuple[int, int]:
    start = int(datetime(year, month, 1, tzinfo=timezone.utc).timestamp() * 1_000)
    last  = calendar.monthrange(year, month)[1]
    end   = int(datetime(year, month, last, 23, 59, 59, 999_000, tzinfo=timezone.utc).timestamp() * 1_000)
    return start, end


def expected_records(year: int, month: int) -> int:
    """3 funding payments per day (00:00, 08:00, 16:00 UTC)."""
    return calendar.monthrange(year, month)[1] * 3


# ─────────────────────────────────────────────────────────────────────────────
# Ingest Q1 2024 (force=True so Phase A always exercises the download path)
# ─────────────────────────────────────────────────────────────────────────────
section("Ingesting Q1 2024 funding data (force=True for fresh download)")
results = ingest_range(SYMBOL, START_YM, END_YM, BASE_DIR, force=True)
print(f"  ingested {len(results)} months")


# ─────────────────────────────────────────────────────────────────────────────
# Phase A — Coverage validation
# ─────────────────────────────────────────────────────────────────────────────
section("Phase A — Coverage: first/last timestamp, records, coverage %")

# Load all three months into one DataFrame
frames = [read_partition(BASE_DIR, DATASET, SYMBOL, y, m) for y, m in
          [(2024, 1), (2024, 2), (2024, 3)]]
df_all = pd.concat(frames, ignore_index=True).sort_values("timestamp_ms")

total_expected = sum(expected_records(y, m) for y, m in [(2024, 1), (2024, 2), (2024, 3)])
total_actual   = len(df_all)
coverage_pct   = total_actual / total_expected * 100

print(f"  expected records: {total_expected}")
print(f"  actual records  : {total_actual}")
print(f"  coverage        : {coverage_pct:.2f}%")

# First timestamp should be 2024-01-01 00:00:00 UTC
q1_start_ms, _ = month_bounds_ms(2024, 1)
first_ts = int(df_all["timestamp_ms"].iloc[0])
check("first timestamp is 2024-01-01 00:00:00 UTC",
      first_ts == q1_start_ms,
      f"got {first_ts}, expected {q1_start_ms}")

# Last timestamp should be 2024-03-31 16:00:00 UTC.
# Binance funding timestamps have ±1 ms noise from the exact 8h grid
# (documented in CLAUDE.md §11 "Binance funding timestamp precision").
# Allow ±1 second tolerance.
mar_start_ms, _ = month_bounds_ms(2024, 3)
expected_last_ts = mar_start_ms + 30 * 86_400_000 + 16 * 3_600_000  # Mar31 16:00 UTC
last_ts = int(df_all["timestamp_ms"].iloc[-1])
check("last timestamp ≈ 2024-03-31 16:00:00 UTC (±1 s)",
      abs(last_ts - expected_last_ts) <= 1_000,
      f"got {last_ts}, expected {expected_last_ts}, delta={last_ts - expected_last_ts} ms")

check("coverage >= 99%", coverage_pct >= 99.0, f"{coverage_pct:.2f}%")
check("actual records within 1% of expected",
      abs(total_actual - total_expected) <= max(1, int(total_expected * 0.01)),
      f"actual={total_actual}, expected={total_expected}")

# Manifest sanity for each month
for y, m in [(2024, 1), (2024, 2), (2024, 3)]:
    part_dir = partition_path(BASE_DIR, DATASET, SYMBOL, y, m)
    mf_path  = part_dir / "_manifest.json"
    check(f"manifest exists for {y}-{m:02d}", mf_path.exists())
    if mf_path.exists():
        mf = json.loads(mf_path.read_text())
        check(f"manifest rows match partition for {y}-{m:02d}",
              mf["rows"] == expected_records(y, m),
              f"manifest={mf['rows']}, expected={expected_records(y, m)}")


# ─────────────────────────────────────────────────────────────────────────────
# Phase B — Timestamp / cadence validation
# ─────────────────────────────────────────────────────────────────────────────
section("Phase B — Timestamp: cadence consistency (8h = 28,800,000 ms)")

ts = df_all["timestamp_ms"].reset_index(drop=True)

# Binance funding timestamps have ±1 ms noise from the exact 8h UTC grid.
# "Aligned" = within 1 second of a grid point in either direction.
remainder = ts % CADENCE_MS
near_grid  = (remainder <= 1_000) | (remainder >= CADENCE_MS - 1_000)
not_aligned = (~near_grid).sum()
check("all timestamps within 1 s of 8h UTC grid (Binance ±1 ms noise)",
      not_aligned == 0, f"{not_aligned} outside tolerance")

# Consecutive diffs should be ≈ CADENCE_MS (±1 second tolerance).
# Cross-month gaps should still be exactly CADENCE_MS since funding is continuous.
diffs = ts.diff().dropna().astype("int64")
near_cadence = ((diffs - CADENCE_MS).abs() <= 1_000).sum()
off_cadence  = ((diffs - CADENCE_MS).abs() >  1_000).sum()
print(f"  diffs within ±1 s of 8h: {near_cadence}  real gaps: {off_cadence}")
check("no real cadence gaps (all diffs ≈ 8h ± 1 s)",
      off_cadence == 0, f"off_cadence={off_cadence}")

# dtype
check("timestamp_ms dtype is int64", ts.dtype == "int64")
check("funding_rate dtype is float64",
      df_all["funding_rate"].dtype == "float64")

# Funding rates in a plausible range: typically ±0.05%
fr = df_all["funding_rate"]
check("all funding_rates in plausible range [-0.05, 0.05]",
      bool((fr.abs() <= 0.05).all()),
      f"min={fr.min():.6f}, max={fr.max():.6f}")
print(f"  funding_rate: mean={fr.mean():.6f}, std={fr.std():.6f}, "
      f"min={fr.min():.6f}, max={fr.max():.6f}")


# ─────────────────────────────────────────────────────────────────────────────
# Phase C — DuckDB validation
# ─────────────────────────────────────────────────────────────────────────────
section("Phase C — DuckDB: view registration and aggregation queries")

con     = open_db()
n_parts = register_view(con, "funding", BASE_DIR, DATASET, SYMBOL)
check("register_view returns 3 partitions", n_parts == 3)

# Total row count
ct = query(con, "SELECT COUNT(*) AS n FROM funding")
check("total funding rows matches in-memory count",
      int(ct["n"].iloc[0]) == total_actual,
      f"duckdb={ct['n'].iloc[0]}, pandas={total_actual}")

# Aggregation
agg = query(con,
    "SELECT AVG(funding_rate) AS avg_fr, MIN(funding_rate) AS min_fr, "
    "MAX(funding_rate) AS max_fr FROM funding")
avg_fr = float(agg["avg_fr"].iloc[0])
check("AVG funding_rate is finite and plausible",
      -0.01 < avg_fr < 0.01, f"avg={avg_fr:.6f}")

# Monthly grouping using epoch_ms() for UTC-safe formatting (lesson from WI-3)
monthly = query(con,
    "SELECT strftime(epoch_ms(timestamp_ms), '%Y-%m') AS ym, COUNT(*) AS n "
    "FROM funding GROUP BY ym ORDER BY ym")
check("DuckDB groups into exactly 3 months",
      len(monthly) == 3, f"got {len(monthly)}: {monthly['ym'].tolist()}")
check("months are 2024-01, 2024-02, 2024-03",
      monthly["ym"].tolist() == ["2024-01", "2024-02", "2024-03"])

# Date-range filter: only January records
jan_start_ms, jan_end_ms = month_bounds_ms(2024, 1)
jan_ct = query(con,
    f"SELECT COUNT(*) AS n FROM funding "
    f"WHERE timestamp_ms >= {jan_start_ms} AND timestamp_ms <= {jan_end_ms}")
check("filtered January row count correct",
      int(jan_ct["n"].iloc[0]) == expected_records(2024, 1),
      f"got {int(jan_ct['n'].iloc[0])}, expected {expected_records(2024, 1)}")

con.close()


# ─────────────────────────────────────────────────────────────────────────────
# Phase D — Idempotency validation
# ─────────────────────────────────────────────────────────────────────────────
section("Phase D — Idempotency: re-run 2024-01 with force=False")

parquet_path = partition_path(BASE_DIR, DATASET, SYMBOL, 2024, 1) / "data.parquet"
mtime_before = parquet_path.stat().st_mtime

result_skip = ingest_month(SYMBOL, 2024, 1, BASE_DIR, force=False)

mtime_after = parquet_path.stat().st_mtime
check("ingest_month returned skipped=True",   result_skip.get("skipped") is True)
check("data.parquet mtime unchanged",         mtime_before == mtime_after)

# Read back and confirm row count is unchanged (no duplicates)
df_reread = read_partition(BASE_DIR, DATASET, SYMBOL, 2024, 1)
check("no duplicate rows after re-run",
      len(df_reread) == expected_records(2024, 1),
      f"rows={len(df_reread)}, expected={expected_records(2024, 1)}")
check("no duplicate timestamp_ms values",
      df_reread["timestamp_ms"].nunique() == len(df_reread))


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
    print("WI-4 PASSED — funding.py ingestion is correct.")
    parts = list_partitions(BASE_DIR, DATASET, SYMBOL)
    print(f"Lake now holds {len(parts)} funding partition(s) under data/raw/ (gitignored).")
