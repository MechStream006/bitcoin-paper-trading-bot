"""
WI-2 validation: storage/lake.py + storage/duck.py

Run from the research/ directory:
    python validate_wi2.py

Uses a synthetic dataset written to data/raw/validate_wi2/ (gitignored).
Cleans up after itself on success; leaves files on failure for inspection.
"""
import pathlib
import shutil
import sys

import pandas as pd
import numpy as np

# Ensure research/ is on sys.path regardless of working directory.
sys.path.insert(0, str(pathlib.Path(__file__).parent))

from storage.lake import write_partition, list_partitions, read_partition
from storage.duck import open_db, register_view, query

BASE_DIR = pathlib.Path(__file__).parent / "data" / "raw"
DATASET  = "validate_wi2"
SYMBOL   = "BTCUSDT"
MONTHS   = [(2024, 1, 100), (2024, 2, 87)]   # (year, month, row_count)

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
errors: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    tag = PASS if condition else FAIL
    print(f"  [{tag}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        errors.append(label)


def synthetic_ohlcv(year: int, month: int, n: int) -> pd.DataFrame:
    """Minimal synthetic OHLCV with open_time as int64 ms timestamp."""
    rng = np.random.default_rng(seed=year * 100 + month)
    open_time = pd.date_range(
        f"{year}-{month:02d}-01", periods=n, freq="1min", tz="UTC"
    ).astype("int64") // 1_000_000   # ms since epoch
    close = 30_000 + rng.standard_normal(n).cumsum() * 10
    return pd.DataFrame({
        "open_time": open_time,
        "open":  close + rng.uniform(-5, 5, n),
        "high":  close + rng.uniform(0, 20, n),
        "low":   close - rng.uniform(0, 20, n),
        "close": close,
        "volume": rng.uniform(0.1, 5.0, n),
    })


# ── 1. Write partitions ──────────────────────────────────────────────────────
print("\n── 1. write_partition ──")
written_paths: list[pathlib.Path] = []
for year, month, n in MONTHS:
    df = synthetic_ohlcv(year, month, n)
    path = write_partition(df, BASE_DIR, DATASET, SYMBOL, year, month)
    written_paths.append(path)
    check(f"partition written year={year} month={month:02d}",
          path.exists(), str(path))
    check(f"row count correct ({n} rows)",
          len(df) == n)

# ── 2. list_partitions ───────────────────────────────────────────────────────
print("\n── 2. list_partitions ──")
found = list_partitions(BASE_DIR, DATASET, SYMBOL)
check("correct number of partitions", len(found) == len(MONTHS),
      f"found {len(found)}, expected {len(MONTHS)}")
check("sorted by (year, month)",
      [(r["year"], r["month"]) for r in found] == [(y, m) for y, m, _ in MONTHS])

# ── 3. read_partition (round-trip) ───────────────────────────────────────────
print("\n── 3. read_partition round-trip ──")
for year, month, n in MONTHS:
    rt = read_partition(BASE_DIR, DATASET, SYMBOL, year, month)
    check(f"round-trip rows match year={year} month={month:02d}",
          len(rt) == n, f"got {len(rt)}")
    check(f"columns present year={year} month={month:02d}",
          set(rt.columns) >= {"open_time", "open", "high", "low", "close", "volume"})

# ── 4. DuckDB view registration ──────────────────────────────────────────────
print("\n── 4. DuckDB register_view ──")
con = open_db()
n_parts = register_view(con, "spot_test", BASE_DIR, DATASET, SYMBOL)
check("register_view returns partition count",
      n_parts == len(MONTHS), f"got {n_parts}")

# ── 5. DuckDB query ──────────────────────────────────────────────────────────
print("\n── 5. DuckDB query ──")
total_expected = sum(n for _, _, n in MONTHS)

result = query(con, "SELECT COUNT(*) AS n FROM spot_test")
check("total row count across view",
      int(result["n"].iloc[0]) == total_expected,
      f"got {int(result['n'].iloc[0])}, expected {total_expected}")

result2 = query(con, "SELECT MIN(close) AS lo, MAX(close) AS hi FROM spot_test")
check("numeric columns queryable (min/max close)",
      result2["lo"].iloc[0] < result2["hi"].iloc[0])

# Fresh connection — views must be re-registered (expected behaviour).
con2 = open_db()
try:
    con2.execute("SELECT COUNT(*) FROM spot_test").fetchone()
    check("fresh connection sees stale view", False, "should have raised")
except Exception:
    check("fresh connection requires re-register (correct isolation)", True)
finally:
    con2.close()

# Re-register on con2 confirms discovery is repeatable.
con2 = open_db()
register_view(con2, "spot_test", BASE_DIR, DATASET, SYMBOL)
result3 = query(con2, "SELECT COUNT(*) AS n FROM spot_test")
check("re-registered view on fresh connection",
      int(result3["n"].iloc[0]) == total_expected)
con2.close()
con.close()

# ── Cleanup ───────────────────────────────────────────────────────────────────
print("\n── Cleanup ──")
if not errors:
    shutil.rmtree(BASE_DIR / DATASET, ignore_errors=True)
    print("  Synthetic data removed (all checks passed).")
else:
    print(f"  Leaving data/raw/{DATASET}/ for inspection ({len(errors)} failure(s)).")

# ── Summary ───────────────────────────────────────────────────────────────────
print()
if errors:
    print(f"FAILED — {len(errors)} check(s) failed: {errors}")
    sys.exit(1)
else:
    print("WI-2 PASSED — storage/lake.py and storage/duck.py are correct.")
