"""
WI-3 validation: research/ingest/binance_ohlcv.py

Run from the research/ directory:
    python validate_wi3.py

Downloads real data from data.binance.vision (~3 ZIP files, ~20 MB total).
Data is written to data/raw/spot/BTCUSDT/ (gitignored).
Leaves data in place on success — the lake now holds real data ready for WI-4+.

Phases:
  A. Single-month  — ingest spot 2024-01; verify rows, ordering, partition layout
  B. Multi-month   — extend to 2024-02 + 2024-03; verify discovery + DuckDB query
  C. Idempotency   — re-run 2024-01 with force=False; verify skip (no re-download)
  D. Fallback path — call _fetch_bulk with a non-existent URL; verify None return
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from ingest.binance_ohlcv import ingest_month, ingest_range, _fetch_bulk, bulk_url
from storage.lake import list_partitions, read_partition, partition_path
from storage.duck import open_db, register_view, query

BASE_DIR = pathlib.Path(__file__).parent / "data" / "raw"
DATASET  = "spot"
SYMBOL   = "BTCUSDT"

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


# ─────────────────────────────────────────────────────────────────────────────
# Phase A — single month (spot 2024-01)
# ─────────────────────────────────────────────────────────────────────────────
section("Phase A — single month: spot 2024-01")

# force=True so Phase A always exercises the download path even on re-runs.
# Phase C separately verifies the force=False skip.
t0  = time.time()
meta = ingest_month(DATASET, SYMBOL, 2024, 1, BASE_DIR, force=True)
elapsed = time.time() - t0

check("ingest_month returned without skipped flag", not meta.get("skipped"))
check("source is bulk",            meta.get("source") == "bulk")
check("source_url recorded",       meta.get("source_url", "").startswith("https://"))
check("ingest_ts recorded",        bool(meta.get("ingest_ts")))
print(f"       download time: {elapsed:.1f}s, rows: {meta.get('rows')}")

# Partition layout on disk
part_dir = partition_path(BASE_DIR, DATASET, SYMBOL, 2024, 1)
check("data.parquet exists",       (part_dir / "data.parquet").exists())
check("_manifest.json exists",     (part_dir / "_manifest.json").exists())

# Read back the manifest
with open(part_dir / "_manifest.json", encoding="utf-8") as f:
    m = json.load(f)
check("manifest rows matches meta", m["rows"] == meta["rows"])
check("manifest dataset matches",   m["dataset"] == DATASET)

# Read the partition
df = read_partition(BASE_DIR, DATASET, SYMBOL, 2024, 1)

# Row-count sanity: January has 31 days → expect 31 × 1440 = 44,640 rows
# Allow a small tolerance (exchange maintenance gaps, partial archive edge)
expected_min = 44_000
expected_max = 44_640
check("row count in valid range",
      expected_min <= len(df) <= expected_max,
      f"got {len(df)}, expected {expected_min}–{expected_max}")
check("manifest rows matches on-disk rows", m["rows"] == len(df))

# Timestamps
ts = df["open_time"]
check("timestamps strictly ascending",  bool((ts.diff().dropna() > 0).all()))

# All timestamps inside Jan 2024 (UTC)
import pandas as pd
jan_start_ms = int(pd.Timestamp("2024-01-01", tz="UTC").value // 1_000_000)
jan_end_ms   = int(pd.Timestamp("2024-01-31 23:59:59.999", tz="UTC").value // 1_000_000)
check("all timestamps within 2024-01",
      bool(ts.between(jan_start_ms, jan_end_ms).all()),
      f"min={ts.min()}, max={ts.max()}")

# Expected columns
expected_cols = {"open_time", "open", "high", "low", "close", "volume"}
check("all expected columns present", expected_cols <= set(df.columns))

# Dtype checks
check("open_time dtype is int64",  df["open_time"].dtype == "int64")
check("close dtype is float64",    df["close"].dtype    == "float64")

# No NaNs in OHLCV
nan_count = df[list(expected_cols)].isna().sum().sum()
check("zero NaN values in OHLCV",  nan_count == 0, f"found {nan_count}")


# ─────────────────────────────────────────────────────────────────────────────
# Phase B — multi-month (extend to 2024-02, 2024-03; query via DuckDB)
# ─────────────────────────────────────────────────────────────────────────────
section("Phase B — multi-month + DuckDB: 2024-01..2024-03")

results = ingest_range(DATASET, SYMBOL, (2024, 1), (2024, 3), BASE_DIR)
check("ingest_range returned 3 results", len(results) == 3)
# Each result must be either a fresh ingest (has rows key) or a skip (skipped=True).
# Skip/ingest counts depend on prior runs leaving data in place — don't assert them.
check("all 3 results are valid (ingested or skipped)",
      all("rows" in r or r.get("skipped") for r in results))

parts = list_partitions(BASE_DIR, DATASET, SYMBOL)
check("3 partitions discovered", len(parts) == 3, f"found {len(parts)}")
check("partitions sorted correctly",
      [(p["year"], p["month"]) for p in parts] == [(2024, 1), (2024, 2), (2024, 3)])

# DuckDB
con = open_db()
n_parts = register_view(con, "spot", BASE_DIR, DATASET, SYMBOL)
check("register_view covers 3 partitions", n_parts == 3)

total_rows_result = query(con, "SELECT COUNT(*) AS n FROM spot")
total_rows = int(total_rows_result["n"].iloc[0])
# Feb 2024 = 29 days (leap year) → 41,760; Mar = 31 → 44,640; Jan = 44,640
# Combined minimum (with small tolerance) ≥ 128,000
check("total rows across 3 months >= 128,000", total_rows >= 128_000, f"got {total_rows}")

# Verify DuckDB sees correct month distribution.
# Use epoch_ms() (returns naive TIMESTAMP from ms) so strftime formats in UTC,
# not the machine's local timezone.  to_timestamp() returns TIMESTAMPTZ and
# strftime would then use local time, producing wrong month labels for candles
# near midnight UTC on month boundaries when the machine is UTC+N.
monthly = query(con,
    "SELECT strftime(epoch_ms(open_time), '%Y-%m') AS ym, "
    "COUNT(*) AS n FROM spot GROUP BY ym ORDER BY ym")
check("DuckDB groups into 3 distinct months",
      len(monthly) == 3, f"got {len(monthly)}: {monthly['ym'].tolist()}")
check("months are 2024-01, 2024-02, 2024-03",
      monthly["ym"].tolist() == ["2024-01", "2024-02", "2024-03"])

# Spot-check a numeric query
price_result = query(con, "SELECT MIN(low) AS lo, MAX(high) AS hi FROM spot")
lo = float(price_result["lo"].iloc[0])
hi = float(price_result["hi"].iloc[0])
check("price range plausible (BTC 2024 Q1 $30k–$75k)", 30_000 < lo < hi < 75_000,
      f"lo={lo:.0f}, hi={hi:.0f}")
con.close()

# _manifest.json present for all 3 partitions
for p in parts:
    mf = p["path"].parent / "_manifest.json"
    check(f"manifest present for {p['year']}-{p['month']:02d}", mf.exists())


# ─────────────────────────────────────────────────────────────────────────────
# Phase C — idempotency: re-running 2024-01 must skip (no download)
# ─────────────────────────────────────────────────────────────────────────────
section("Phase C — idempotency: re-run 2024-01 with force=False")

parquet_path = partition_path(BASE_DIR, DATASET, SYMBOL, 2024, 1) / "data.parquet"
mtime_before = parquet_path.stat().st_mtime

result_skip = ingest_month(DATASET, SYMBOL, 2024, 1, BASE_DIR, force=False)

mtime_after = parquet_path.stat().st_mtime
check("ingest_month returned skipped=True", result_skip.get("skipped") is True)
check("data.parquet mtime unchanged (no re-write)", mtime_before == mtime_after)


# ─────────────────────────────────────────────────────────────────────────────
# Phase D — fallback-path guard: _fetch_bulk returns None on 404
# ─────────────────────────────────────────────────────────────────────────────
section("Phase D — fallback-path: _fetch_bulk returns None on 404")

bad_url = bulk_url(DATASET, SYMBOL, 2099, 12)   # future month — guaranteed 404
result_none = _fetch_bulk(bad_url)
check("_fetch_bulk returns None for a 404 URL", result_none is None)


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
    print("WI-3 PASSED — binance_ohlcv.py ingestion is correct.")
    print(f"Lake now holds {len(parts)} spot partition(s) under data/raw/ (gitignored).")
