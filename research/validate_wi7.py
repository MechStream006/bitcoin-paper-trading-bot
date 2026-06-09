"""
WI-7 validation: research/quality/gauntlet.py

Run from the research/ directory:
    python validate_wi7.py

No network calls.  Phase A uses real spot 2024-01 data already ingested.
Phases B-D use synthetic DataFrames derived from that partition.

Phases:
  A. Clean dataset pass          — 0 CRITICAL on real spot 2024-01 data
  B. Synthetic corruption tests  — each corruption produces the expected findings
       B1. Duplicate timestamps
       B2. Missing timestamps / gaps
       B3. Timestamp disorder
       B4. Wrong-partition records
  C. Severity verification       — CRITICAL vs WARNING correctly assigned
  D. JSON report generation      — report_path written, valid JSON, correct schema
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import pandas as pd

from quality.gauntlet import run_gauntlet
from storage.lake import read_partition

BASE_DIR   = pathlib.Path(__file__).parent / "data" / "raw"
REPORT_OUT = pathlib.Path(__file__).parent / "data" / "raw" / "validate_wi7_report.json"

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


def findings_with(report: dict, *, severity: str | None = None, check_name: str | None = None) -> list[dict]:
    """Filter findings by optional severity and/or check name."""
    result = report["findings"]
    if severity:
        result = [f for f in result if f["severity"] == severity]
    if check_name:
        result = [f for f in result if f["check"] == check_name]
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Load real spot 2024-01 (used by Phase A and as the base for Phase B)
# ─────────────────────────────────────────────────────────────────────────────
section("Setup — loading spot 2024-01")

spot_exists = (BASE_DIR / "spot" / "BTCUSDT" / "year=2024" / "month=01" / "data.parquet").exists()
check("spot 2024-01 partition present on disk", spot_exists)
if not spot_exists:
    print("\nABORTED — run research/ingest/binance_ohlcv.py first to ingest spot 2024-01.")
    sys.exit(1)

df_clean = read_partition(BASE_DIR, "spot", "BTCUSDT", 2024, 1)
print(f"  Loaded {len(df_clean):,} rows, columns: {list(df_clean.columns)}")
check("spot 2024-01 has >=44,000 rows", len(df_clean) >= 44_000, f"got {len(df_clean)}")


# ─────────────────────────────────────────────────────────────────────────────
# Phase A — Clean dataset pass
# ─────────────────────────────────────────────────────────────────────────────
section("Phase A — Clean dataset pass (spot 2024-01)")

r_clean = run_gauntlet(
    df_clean,
    dataset="spot",
    symbol="BTCUSDT",
    year=2024,
    month=1,
    ts_column="open_time",
)

check("checks_run == 8", r_clean["checks_run"] == 8, f"got {r_clean['checks_run']}")
check("rows_checked matches df length", r_clean["rows_checked"] == len(df_clean))
check("critical_count == 0", r_clean["critical_count"] == 0,
      f"CRITICAL findings: {findings_with(r_clean, severity='CRITICAL')}")
check("passed == True", r_clean["passed"] is True)
check("report has required top-level keys", all(k in r_clean for k in [
    "dataset", "symbol", "year", "month", "rows_checked",
    "checks_run", "critical_count", "warning_count", "passed", "findings", "run_ts"
]))
check("dataset recorded correctly", r_clean["dataset"] == "spot")
check("year/month recorded correctly", r_clean["year"] == 2024 and r_clean["month"] == 1)
check("run_ts is a non-empty string", isinstance(r_clean["run_ts"], str) and len(r_clean["run_ts"]) > 10)

if r_clean["warning_count"] > 0:
    warn_names = [f["check"] for f in findings_with(r_clean, severity="WARNING")]
    print(f"    (NOTE: {r_clean['warning_count']} WARNING(s) on clean data: {warn_names})")


# ─────────────────────────────────────────────────────────────────────────────
# Phase B + C — Synthetic corruption tests (severity verified inline)
# ─────────────────────────────────────────────────────────────────────────────

# Use only the first-7-days slice (10,080 rows) to keep corruptions tidy
# and avoid the category columns from PyArrow polluting concat operations
_BASE_COLS = ["open_time", "open", "high", "low", "close", "volume"]
df_base = df_clean[_BASE_COLS].copy().reset_index(drop=True)


# ── B1: Duplicate timestamps ──────────────────────────────────────────────────
section("Phase B1 + C1 — Duplicate timestamps")

# Append 5 rows from position 500 back into the DataFrame, then sort by open_time
df_dup = pd.concat([df_base, df_base.iloc[500:505][_BASE_COLS]], ignore_index=True)
df_dup = df_dup.sort_values("open_time").reset_index(drop=True)

r_dup = run_gauntlet(
    df_dup,
    dataset="spot",
    symbol="BTCUSDT",
    year=2024,
    month=1,
    ts_column="open_time",
)

critical_checks_b1 = {f["check"] for f in findings_with(r_dup, severity="CRITICAL")}
check("B1: at least one CRITICAL finding", r_dup["critical_count"] > 0,
      f"critical_count={r_dup['critical_count']}")
check("B1: duplicate_timestamps is CRITICAL",
      "duplicate_timestamps" in critical_checks_b1,
      f"CRITICAL checks: {critical_checks_b1}")
check("B1: passed == False", r_dup["passed"] is False)


# ── B2: Missing timestamps / gaps ─────────────────────────────────────────────
section("Phase B2 + C2 — Gaps (missing timestamps)")

# Drop 200 consecutive rows from the middle → 200-minute gap (3h 20m)
gap_start = len(df_base) // 2
df_gap = pd.concat([df_base.iloc[:gap_start], df_base.iloc[gap_start + 200:]], ignore_index=True)

r_gap = run_gauntlet(
    df_gap,
    dataset="spot",
    symbol="BTCUSDT",
    year=2024,
    month=1,
    ts_column="open_time",
)

warning_checks_b2 = {f["check"] for f in findings_with(r_gap, severity="WARNING")}
check("B2: gap_detection is WARNING",
      "gap_detection" in warning_checks_b2,
      f"WARNING checks: {warning_checks_b2}")

gap_findings = findings_with(r_gap, check_name="gap_detection")
check("B2: gap finding mentions missing record(s)",
      any("missing" in f["detail"].lower() for f in gap_findings),
      str([f["detail"] for f in gap_findings]))


# ── B3: Timestamp disorder ─────────────────────────────────────────────────────
section("Phase B3 + C3 — Timestamp disorder")

# Swap first row with row 200 to introduce a large out-of-order jump
df_disorder = df_base.copy()
tmp = df_disorder.iloc[0].copy()
df_disorder.iloc[0] = df_disorder.iloc[200]
df_disorder.iloc[200] = tmp

r_dis = run_gauntlet(
    df_disorder,
    dataset="spot",
    symbol="BTCUSDT",
    year=2024,
    month=1,
    ts_column="open_time",
)

critical_checks_b3 = {f["check"] for f in findings_with(r_dis, severity="CRITICAL")}
check("B3: at least one CRITICAL finding", r_dis["critical_count"] > 0,
      f"critical_count={r_dis['critical_count']}")
check("B3: monotonic_timestamps is CRITICAL",
      "monotonic_timestamps" in critical_checks_b3,
      f"CRITICAL checks: {critical_checks_b3}")
check("B3: passed == False", r_dis["passed"] is False)

mono_findings = findings_with(r_dis, check_name="monotonic_timestamps")
check("B3: monotonic finding mentions non-positive diff",
      any("non-positive" in f["detail"].lower() for f in mono_findings),
      str([f["detail"] for f in mono_findings]))


# ── B4: Wrong-partition records ───────────────────────────────────────────────
section("Phase B4 + C4 — Wrong-partition timestamps")

from datetime import datetime, timezone

# Build 5 rows with timestamps from December 2023 (before January 2024 start)
dec_start_ms = int(datetime(2023, 12, 1, tzinfo=timezone.utc).timestamp() * 1_000)
alien_rows = pd.DataFrame({
    "open_time": [dec_start_ms + i * 60_000 for i in range(5)],
    "open":      [42_000.0] * 5,
    "high":      [42_100.0] * 5,
    "low":       [41_900.0] * 5,
    "close":     [42_050.0] * 5,
    "volume":    [10.0] * 5,
})
# Prepend the alien rows; sort so timestamps are in ascending order (won't confuse monotonic check)
df_wrong_part = pd.concat([alien_rows, df_base], ignore_index=True).sort_values("open_time").reset_index(drop=True)

r_wp = run_gauntlet(
    df_wrong_part,
    dataset="spot",
    symbol="BTCUSDT",
    year=2024,
    month=1,
    ts_column="open_time",
)

critical_checks_b4 = {f["check"] for f in findings_with(r_wp, severity="CRITICAL")}
check("B4: at least one CRITICAL finding", r_wp["critical_count"] > 0,
      f"critical_count={r_wp['critical_count']}")
check("B4: partition_consistency is CRITICAL",
      "partition_consistency" in critical_checks_b4,
      f"CRITICAL checks: {critical_checks_b4}")
check("B4: passed == False", r_wp["passed"] is False)

pc_findings = findings_with(r_wp, check_name="partition_consistency")
check("B4: partition_consistency finding mentions out-of-partition rows",
      any("outside" in f["detail"].lower() for f in pc_findings),
      str([f["detail"] for f in pc_findings]))


# ─────────────────────────────────────────────────────────────────────────────
# Phase C — Cross-check severity correctness (summary)
# ─────────────────────────────────────────────────────────────────────────────
section("Phase C — Severity verification summary")

check("C1: duplicate_timestamps → CRITICAL (not WARNING)",
      all(f["severity"] == "CRITICAL"
          for f in findings_with(r_dup, check_name="duplicate_timestamps")))
check("C2: gap_detection → WARNING (not CRITICAL)",
      all(f["severity"] == "WARNING"
          for f in findings_with(r_gap, check_name="gap_detection")))
check("C3: monotonic_timestamps → CRITICAL (not WARNING)",
      all(f["severity"] == "CRITICAL"
          for f in findings_with(r_dis, check_name="monotonic_timestamps")))
check("C4: partition_consistency → CRITICAL (not WARNING)",
      all(f["severity"] == "CRITICAL"
          for f in findings_with(r_wp, check_name="partition_consistency")))


# ─────────────────────────────────────────────────────────────────────────────
# Phase D — JSON report generation
# ─────────────────────────────────────────────────────────────────────────────
section("Phase D — JSON report generation")

REPORT_OUT.parent.mkdir(parents=True, exist_ok=True)
r_json = run_gauntlet(
    df_clean,
    dataset="spot",
    symbol="BTCUSDT",
    year=2024,
    month=1,
    ts_column="open_time",
    report_path=REPORT_OUT,
)

check("report_path file written to disk", REPORT_OUT.exists())

raw = REPORT_OUT.read_text(encoding="utf-8")
try:
    parsed = json.loads(raw)
    valid_json = True
except json.JSONDecodeError as exc:
    parsed = {}
    valid_json = False
    print(f"    JSON parse error: {exc}")

check("written file is valid JSON", valid_json)

required_keys = [
    "dataset", "symbol", "year", "month", "rows_checked",
    "checks_run", "critical_count", "warning_count", "passed", "findings", "run_ts"
]
missing_keys = [k for k in required_keys if k not in parsed]
check("all required keys present in JSON report", len(missing_keys) == 0,
      f"missing: {missing_keys}")
check("findings is a JSON array", isinstance(parsed.get("findings"), list))
check("run_ts present in JSON report", isinstance(parsed.get("run_ts"), str) and len(parsed.get("run_ts", "")) > 10)
check("in-memory return dict matches written JSON",
      parsed.get("dataset") == r_json["dataset"] and
      parsed.get("rows_checked") == r_json["rows_checked"] and
      parsed.get("critical_count") == r_json["critical_count"])

print(f"\n  Report written: {REPORT_OUT}")
print(f"  critical_count={parsed.get('critical_count')}, "
      f"warning_count={parsed.get('warning_count')}, "
      f"passed={parsed.get('passed')}")

# Cleanup report file
REPORT_OUT.unlink(missing_ok=True)
print("  Report file cleaned up.")


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
    print("WI-7 PASSED — quality/gauntlet.py is correct.")
    print(f"  8 checks run, clean spot 2024-01 passed ({r_clean['rows_checked']:,} rows).")
    print(f"  All 4 corruption types correctly detected with right severities.")
    print(f"  JSON report generation verified.")
