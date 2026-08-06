"""
WI-6 validation: research/storage/manifest.py

Run from the research/ directory:
    python validate_wi6.py

No network calls.  Phases A-D use a synthetic parquet in data/raw/validate_wi6/
(cleaned up on success).  Phase E writes WI-6 manifests over existing WI-3/WI-4
partitions (upgrading them from the old sidecar format).

Phases:
  A. Manifest creation    — sidecar written, all expected fields present
  B. Hash validation      — independently recomputed SHA-256 matches manifest
  C. Metadata validation  — rows, min_ts, max_ts, source fields correct
  D. Tamper detection     — modified file → verify_hash() returns False
  E. Compatibility        — works for WI-3 OHLCV and WI-4 funding partitions
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from storage.manifest import (
    SIDECAR_NAME, SCHEMA_VERSION,
    write_manifest, read_manifest, verify_hash,
    _sha256,
)
from storage.lake import partition_path

BASE_DIR    = pathlib.Path(__file__).parent / "data" / "raw"
SPOT_PART   = partition_path(BASE_DIR, "spot",    "BTCUSDT", 2024, 1) / "data.parquet"
FUND_PART   = partition_path(BASE_DIR, "funding", "BTCUSDT", 2024, 1) / "data.parquet"

# Synthetic test partition
TEST_DIR    = BASE_DIR / "validate_wi6" / "BTCUSDT" / "year=2024" / "month=06"
TEST_PARQUET = TEST_DIR / "data.parquet"

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


def make_synthetic_parquet() -> tuple[int, int, int]:
    """Write a tiny synthetic parquet. Returns (rows, min_ts, max_ts)."""
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    ts_base = int(datetime(2024, 6, 1, tzinfo=timezone.utc).timestamp() * 1_000)
    data = {
        "open_time": [ts_base + i * 60_000 for i in range(10)],
        "open":  [60_000.0 + i for i in range(10)],
        "close": [60_001.0 + i for i in range(10)],
    }
    df = pd.DataFrame(data)
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), TEST_PARQUET,
                   compression="zstd")
    return len(df), ts_base, ts_base + 9 * 60_000


def sha256_independent(path: pathlib.Path) -> str:
    """Recompute SHA-256 directly with hashlib, independent of manifest._sha256."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65_536), b""):
            h.update(chunk)
    return h.hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# Synthetic parquet setup (used by phases A-D)
# ─────────────────────────────────────────────────────────────────────────────
rows, min_ts, max_ts = make_synthetic_parquet()
KNOWN_META = dict(
    dataset="validate_wi6",
    symbol="BTCUSDT",
    year=2024,
    month=6,
    source="synthetic",
    source_url=None,
    rows=rows,
    min_ts=min_ts,
    max_ts=max_ts,
    ts_column="open_time",
    ingest_ts=datetime.now(timezone.utc).isoformat(),
    config_version=1,
)


# ─────────────────────────────────────────────────────────────────────────────
# Phase A — Manifest creation
# ─────────────────────────────────────────────────────────────────────────────
section("Phase A — Manifest creation")

manifest_dict = write_manifest(TEST_PARQUET, **KNOWN_META)
sidecar_path  = TEST_PARQUET.parent / SIDECAR_NAME

check("_manifest.json sidecar created",    sidecar_path.exists())
check("write_manifest returns a dict",     isinstance(manifest_dict, dict))

# All required fields present
required_fields = [
    "schema_version", "dataset", "symbol", "year", "month",
    "parquet_file", "parquet_sha256",
    "rows", "min_ts", "max_ts", "ts_column",
    "source", "source_url", "ingest_ts", "config_version",
]
missing_fields = [f for f in required_fields if f not in manifest_dict]
check("all required fields present in dict",  len(missing_fields) == 0,
      f"missing: {missing_fields}")

check(f"schema_version == {SCHEMA_VERSION}",  manifest_dict.get("schema_version") == SCHEMA_VERSION)
check("parquet_file == 'data.parquet'",
      manifest_dict.get("parquet_file") == "data.parquet")

# Sidecar is valid JSON and round-trips correctly
on_disk = json.loads(sidecar_path.read_text(encoding="utf-8"))
check("sidecar JSON matches returned dict",  on_disk == manifest_dict)


# ─────────────────────────────────────────────────────────────────────────────
# Phase B — Hash validation
# ─────────────────────────────────────────────────────────────────────────────
section("Phase B — Hash validation")

stored_hash   = manifest_dict["parquet_sha256"]
expected_hash = sha256_independent(TEST_PARQUET)

check("stored SHA-256 is a 64-char hex string",  len(stored_hash) == 64)
check("independently recomputed hash matches manifest",
      stored_hash == expected_hash,
      f"stored={stored_hash[:16]}…, expected={expected_hash[:16]}…")

result_clean = verify_hash(TEST_PARQUET)
check("verify_hash() returns True for untampered file",  result_clean is True)


# ─────────────────────────────────────────────────────────────────────────────
# Phase C — Metadata validation
# ─────────────────────────────────────────────────────────────────────────────
section("Phase C — Metadata validation")

m = read_manifest(TEST_PARQUET)
check("read_manifest() round-trips sidecar",  m == manifest_dict)
check("rows matches known value",   m["rows"]   == rows,   f"got {m['rows']}")
check("min_ts matches known value", m["min_ts"] == min_ts, f"got {m['min_ts']}")
check("max_ts matches known value", m["max_ts"] == max_ts, f"got {m['max_ts']}")
check("source recorded",            m["source"] == "synthetic")
check("source_url is null (None → JSON null)",
      m["source_url"] is None)
check("ts_column recorded",         m["ts_column"] == "open_time")
check("config_version == 1",        m["config_version"] == 1)
check("ingest_ts is a non-empty string", isinstance(m["ingest_ts"], str) and len(m["ingest_ts"]) > 10)


# ─────────────────────────────────────────────────────────────────────────────
# Phase D — Tamper detection
# ─────────────────────────────────────────────────────────────────────────────
section("Phase D — Tamper detection")

original_bytes = TEST_PARQUET.read_bytes()

# Append a null byte to corrupt the file
TEST_PARQUET.write_bytes(original_bytes + b"\x00")
result_tampered = verify_hash(TEST_PARQUET)
check("verify_hash() returns False after appending a byte",
      result_tampered is False)

# Restore original bytes
TEST_PARQUET.write_bytes(original_bytes)
result_restored = verify_hash(TEST_PARQUET)
check("verify_hash() returns True after restoration",
      result_restored is True)

# Corrupt the stored hash itself (change 1 hex char)
bad_manifest = dict(on_disk)
bad_manifest["parquet_sha256"] = "0" * 64
sidecar_path.write_text(json.dumps(bad_manifest, indent=2), encoding="utf-8")
result_bad_hash = verify_hash(TEST_PARQUET)
check("verify_hash() returns False when stored hash is wrong",
      result_bad_hash is False)

# Restore the correct manifest
sidecar_path.write_text(json.dumps(manifest_dict, indent=2), encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# Cleanup synthetic data
# ─────────────────────────────────────────────────────────────────────────────
section("Cleanup synthetic partition")
shutil.rmtree(BASE_DIR / "validate_wi6", ignore_errors=True)
print("  Synthetic data/raw/validate_wi6/ removed.")


# ─────────────────────────────────────────────────────────────────────────────
# Phase E — Compatibility with real WI-3/WI-4 partitions
# ─────────────────────────────────────────────────────────────────────────────
section("Phase E — Compatibility: OHLCV (spot 2024-01) and funding (2024-01)")

for label, parquet_path, ts_col, src, src_url in [
    (
        "OHLCV spot 2024-01",
        SPOT_PART,
        "open_time",
        "bulk",
        "https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2024-01.zip",
    ),
    (
        "funding 2024-01",
        FUND_PART,
        "timestamp_ms",
        "ccxt",
        None,
    ),
]:
    check(f"{label}: parquet exists on disk", parquet_path.exists())
    if not parquet_path.exists():
        continue

    # Read parquet to get stats
    df = pq.read_table(parquet_path).to_pandas()
    p_rows   = len(df)
    p_min_ts = int(df[ts_col].min())
    p_max_ts = int(df[ts_col].max())

    # Write WI-6 manifest (overwrites old WI-3/WI-4 sidecar)
    m = write_manifest(
        parquet_path,
        dataset="spot" if ts_col == "open_time" else "funding",
        symbol="BTCUSDT",
        year=2024,
        month=1,
        source=src,
        source_url=src_url,
        rows=p_rows,
        min_ts=p_min_ts,
        max_ts=p_max_ts,
        ts_column=ts_col,
        ingest_ts=datetime.now(timezone.utc).isoformat(),
        config_version=1,
    )

    check(f"{label}: manifest written",          (parquet_path.parent / SIDECAR_NAME).exists())
    check(f"{label}: verify_hash() == True",     verify_hash(parquet_path))
    check(f"{label}: rows in manifest correct",  m["rows"] == p_rows, f"got {m['rows']}")
    check(f"{label}: min_ts correct",            m["min_ts"] == p_min_ts)
    check(f"{label}: max_ts correct",            m["max_ts"] == p_max_ts)

    # Independently recompute hash to confirm manifest._sha256 is correct
    indep = sha256_independent(parquet_path)
    check(f"{label}: independently computed hash matches",
          m["parquet_sha256"] == indep,
          f"len={len(m['parquet_sha256'])}")

print()
if errors:
    print(f"FAILED — {len(errors)} check(s) failed:")
    for e in errors:
        print(f"  ✗ {e}")
    sys.exit(1)
else:
    print("WI-6 PASSED — storage/manifest.py is correct.")
    print("WI-3/WI-4 partition sidecars upgraded to WI-6 format (SHA-256 added).")
