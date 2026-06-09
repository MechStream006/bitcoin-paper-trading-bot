"""
build_perp_corpus.py  --  Ingest BTCUSDT perpetual OHLCV and extend corpus.

Stages
------
  1. Ingest perp OHLCV   : 2021-01 -> last complete month (matches spot range)
  2. Manifest upgrade    : WI-6 SHA-256 + structured metadata
  3. Quality gauntlet    : 8-check battery on every perp partition
  4. Basis verification  : perp-spot join at 1m / 1H / 4H / 1D (DuckDB)
  5. Delta report        : rows / partitions / storage added
  6. Update corpus_summary.json

Run from the research/ directory:
    python build_perp_corpus.py

Output
------
  research/reports/corpus_summary.json  -- updated to include perp
  research/reports/perp_delta.json      -- delta report for this build
"""
from __future__ import annotations

import argparse
import json
import logging
import pathlib
import shutil
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import duckdb
import pyarrow.parquet as pq

from ingest.binance_ohlcv import bulk_url
from ingest.binance_ohlcv import ingest_range as perp_ingest_range
from quality.gauntlet import run_gauntlet
from storage.lake import list_partitions, partition_path, read_partition
from storage.manifest import SIDECAR_NAME, read_manifest, verify_hash, write_manifest

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")

BASE_DIR     = pathlib.Path(__file__).parent / "data" / "raw"
REPORT_DIR   = pathlib.Path(__file__).parent / "reports"
SUMMARY_PATH = REPORT_DIR / "corpus_summary.json"
DELTA_PATH   = REPORT_DIR / "perp_delta.json"
SYMBOL       = "BTCUSDT"
DATASET      = "perp"
TS_COL       = "open_time"

_now = datetime.now(timezone.utc)
if _now.month == 1:
    LAST_COMPLETE = (_now.year - 1, 12)
else:
    LAST_COMPLETE = (_now.year, _now.month - 1)

PERP_START = (2021, 1)   # match spot corpus start


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts(label: str = "") -> str:
    t = datetime.now(timezone.utc).strftime("%H:%M:%S")
    return f"[{t}]{f' {label}' if label else ''}"


def _ym_seq(start_ym: tuple[int, int], end_ym: tuple[int, int]) -> list[tuple[int, int]]:
    result = []
    y, m = start_ym
    ey, em = end_ym
    while (y, m) <= (ey, em):
        result.append((y, m))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return result


def _disk_bytes(path: pathlib.Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _sep(char: str = "-", width: int = 70) -> str:
    return char * width


def _ms_to_iso(ms: int | None) -> str | None:
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1_000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


# ---------------------------------------------------------------------------
# Stage 1 -- Perp OHLCV ingestion
# ---------------------------------------------------------------------------

def stage_ingest_perp(force: bool) -> dict:
    months = _ym_seq(PERP_START, LAST_COMPLETE)
    print(f"\n{_sep()}")
    print(f"{_ts('STAGE 1')}  Perp OHLCV ingestion")
    print(f"  Range : {PERP_START[0]}-{PERP_START[1]:02d} -> "
          f"{LAST_COMPLETE[0]}-{LAST_COMPLETE[1]:02d}  ({len(months)} months)")
    print(_sep())

    ingested = skipped = errors = 0
    t_stage = time.time()

    for year, month in months:
        t0 = time.time()
        try:
            meta = perp_ingest_range(
                DATASET, SYMBOL, (year, month), (year, month), BASE_DIR, force=force
            )[0]
            elapsed = time.time() - t0
            if meta.get("skipped"):
                skipped += 1
                print(f"  {_ts()} PERP {year}-{month:02d}  skip  ({elapsed:.1f}s)", flush=True)
            else:
                ingested += 1
                print(f"  {_ts()} PERP {year}-{month:02d}  {meta['rows']:,} rows  "
                      f"src={meta.get('source', '?')}  ({elapsed:.1f}s)", flush=True)
        except Exception as exc:
            errors += 1
            print(f"  {_ts()} PERP {year}-{month:02d}  ERROR: {repr(exc)[:80]}", flush=True)

    elapsed_total = time.time() - t_stage
    print(f"\n  Done -- ingested={ingested}  skipped={skipped}  errors={errors}  "
          f"elapsed={elapsed_total:.0f}s")
    return {"ingested": ingested, "skipped": skipped, "errors": errors}


# ---------------------------------------------------------------------------
# Stage 2 -- WI-6 manifest upgrade
# ---------------------------------------------------------------------------

def stage_manifest_upgrade() -> dict:
    print(f"\n{_sep()}")
    print(f"{_ts('STAGE 2')}  WI-6 manifest upgrade")
    print(_sep())

    upgraded = already_wi6 = errors = 0
    t_stage = time.time()

    partitions = list_partitions(BASE_DIR, DATASET, SYMBOL)
    for p in partitions:
        year, month = p["year"], p["month"]
        parquet_path = p["path"]
        sidecar = parquet_path.parent / SIDECAR_NAME

        old_m: dict = {}
        if sidecar.exists():
            try:
                old_m = json.loads(sidecar.read_text(encoding="utf-8"))
            except Exception:
                pass

        if old_m.get("schema_version") == 1 and "parquet_sha256" in old_m:
            try:
                if verify_hash(parquet_path):
                    already_wi6 += 1
                    print(f"  {_ts()} PERP {year}-{month:02d}  already WI-6 [OK]", flush=True)
                    continue
            except Exception:
                pass

        try:
            df  = pq.read_table(parquet_path).to_pandas()
            ts  = df[TS_COL].astype("int64")
            rows    = len(df)
            min_ts  = int(ts.min()) if rows > 0 else 0
            max_ts  = int(ts.max()) if rows > 0 else 0
            source     = old_m.get("source", "bulk")
            source_url = old_m.get("source_url") or bulk_url(DATASET, SYMBOL, year, month)

            write_manifest(
                parquet_path,
                dataset=DATASET,
                symbol=SYMBOL,
                year=year,
                month=month,
                source=source,
                source_url=source_url,
                rows=rows,
                min_ts=min_ts,
                max_ts=max_ts,
                ts_column=TS_COL,
                ingest_ts=old_m.get("ingest_ts", datetime.now(timezone.utc).isoformat()),
                config_version=1,
            )
            upgraded += 1
            print(f"  {_ts()} PERP {year}-{month:02d}  upgraded  rows={rows:,}", flush=True)
        except Exception as exc:
            errors += 1
            print(f"  {_ts()} PERP {year}-{month:02d}  ERROR: {repr(exc)[:80]}", flush=True)

    elapsed_total = time.time() - t_stage
    print(f"\n  Done -- upgraded={upgraded}  already_wi6={already_wi6}  errors={errors}  "
          f"elapsed={elapsed_total:.0f}s")
    return {"upgraded": upgraded, "already_wi6": already_wi6, "errors": errors}


# ---------------------------------------------------------------------------
# Stage 3 -- Quality gauntlet
# ---------------------------------------------------------------------------

def stage_gauntlet() -> dict:
    print(f"\n{_sep()}")
    print(f"{_ts('STAGE 3')}  Quality gauntlet (8 checks per partition)")
    print(_sep())

    results: list[dict] = []
    total_critical = total_warning = errors = 0
    t_stage = time.time()

    for p in list_partitions(BASE_DIR, DATASET, SYMBOL):
        year, month = p["year"], p["month"]
        t0 = time.time()
        try:
            df = read_partition(BASE_DIR, DATASET, SYMBOL, year, month)
            r  = run_gauntlet(
                df,
                dataset=DATASET,
                symbol=SYMBOL,
                year=year,
                month=month,
                ts_column=TS_COL,
            )
            elapsed = time.time() - t0
            total_critical += r["critical_count"]
            total_warning  += r["warning_count"]
            results.append(r)

            status = "PASS" if r["passed"] else "FAIL"
            c_str  = f"  {r['critical_count']}C" if r["critical_count"] else ""
            w_str  = f"  {r['warning_count']}W"  if r["warning_count"]  else ""
            print(f"  {_ts()} PERP {year}-{month:02d}  "
                  f"{r['rows_checked']:>7,} rows  [{status}]{c_str}{w_str}  ({elapsed:.1f}s)",
                  flush=True)
            for f in r.get("findings", []):
                sev = f["severity"]
                if sev in ("CRITICAL", "WARNING"):
                    tag = "C" if sev == "CRITICAL" else "W"
                    print(f"    -> {tag} {f['check']}: {f['detail'][:100]}", flush=True)
        except Exception as exc:
            errors += 1
            print(f"  {_ts()} PERP {year}-{month:02d}  ERROR: {repr(exc)[:80]}", flush=True)

    elapsed_total = time.time() - t_stage
    passed = sum(1 for r in results if r["passed"])
    failed = len(results) - passed
    print(f"\n  Done -- partitions={len(results)}  PASS={passed}  FAIL={failed}  "
          f"critical={total_critical}  warning={total_warning}  elapsed={elapsed_total:.0f}s")
    return {
        "partition_results": results,
        "total_critical":    total_critical,
        "total_warning":     total_warning,
        "passed":            passed,
        "failed":            failed,
        "errors":            errors,
    }


# ---------------------------------------------------------------------------
# Stage 4 -- Basis verification
# ---------------------------------------------------------------------------

_BASIS_RESOLUTIONS = [
    ("1m",   60_000),
    ("1H",   3_600_000),
    ("4H",   14_400_000),
    ("1D",   86_400_000),
]

# Sample window for verification (avoid loading full corpus into memory)
_VERIFY_YEAR = 2024
_VERIFY_MONTHS = [1, 2, 3]   # 3 months of 1m data ~= 130K rows per dataset


def stage_basis_verification() -> dict:
    print(f"\n{_sep()}")
    print(f"{_ts('STAGE 4')}  Basis verification (perp-spot join, DuckDB)")
    print(f"  Sample window : {_VERIFY_YEAR}-{_VERIFY_MONTHS[0]:02d} to "
          f"{_VERIFY_YEAR}-{_VERIFY_MONTHS[-1]:02d}  ({len(_VERIFY_MONTHS)} months)")
    print(_sep())

    # Collect parquet file paths for the sample window
    spot_files = []
    perp_files = []
    for m in _VERIFY_MONTHS:
        sf = partition_path(BASE_DIR, "spot", SYMBOL, _VERIFY_YEAR, m) / "data.parquet"
        pf = partition_path(BASE_DIR, DATASET,  SYMBOL, _VERIFY_YEAR, m) / "data.parquet"
        if sf.exists():
            spot_files.append(str(sf))
        if pf.exists():
            perp_files.append(str(pf))

    if not spot_files:
        return {"status": "ERROR", "detail": "Spot sample files not found"}
    if not perp_files:
        return {"status": "ERROR", "detail": "Perp sample files not found"}

    print(f"  Spot files    : {len(spot_files)}  Perp files: {len(perp_files)}", flush=True)

    conn = duckdb.connect()

    # Build file-list literals for DuckDB
    spot_list = "[" + ", ".join(f"'{f}'" for f in spot_files) + "]"
    perp_list = "[" + ", ".join(f"'{f}'" for f in perp_files) + "]"

    # Register 1m views
    conn.execute(f"""
        CREATE OR REPLACE VIEW spot_1m AS
        SELECT CAST(open_time AS BIGINT) AS open_time,
               CAST(close     AS DOUBLE) AS close
        FROM read_parquet({spot_list})
    """)
    conn.execute(f"""
        CREATE OR REPLACE VIEW perp_1m AS
        SELECT CAST(open_time AS BIGINT) AS open_time,
               CAST(close     AS DOUBLE) AS close
        FROM read_parquet({perp_list})
    """)

    # 1m joined basis view
    conn.execute("""
        CREATE OR REPLACE VIEW basis_1m AS
        SELECT
            s.open_time                                    AS ts,
            s.close                                        AS spot_close,
            p.close                                        AS perp_close,
            (p.close - s.close)                            AS basis_abs,
            (p.close / s.close - 1.0) * 100.0             AS basis_pct
        FROM spot_1m s
        JOIN perp_1m p ON s.open_time = p.open_time
    """)

    result: dict = {
        "status": "PASS",
        "sample_window": (f"{_VERIFY_YEAR}-{_VERIFY_MONTHS[0]:02d} to "
                          f"{_VERIFY_YEAR}-{_VERIFY_MONTHS[-1]:02d}"),
        "resolutions": {},
    }

    for res_name, interval_ms in _BASIS_RESOLUTIONS:
        t0 = time.time()
        try:
            if res_name == "1m":
                stats = conn.execute("""
                    SELECT
                        COUNT(*)            AS rows,
                        AVG(basis_abs)      AS mean_basis_abs,
                        AVG(basis_pct)      AS mean_basis_pct,
                        STDDEV(basis_abs)   AS std_basis_abs,
                        MIN(basis_abs)      AS min_basis_abs,
                        MAX(basis_abs)      AS max_basis_abs
                    FROM basis_1m
                """).fetchone()
                rows_expected = len(_VERIFY_MONTHS) * 43800   # ~44k per month
            else:
                stats = conn.execute(f"""
                    SELECT
                        COUNT(*)            AS rows,
                        AVG(basis_abs)      AS mean_basis_abs,
                        AVG(basis_pct)      AS mean_basis_pct,
                        STDDEV(basis_abs)   AS std_basis_abs,
                        MIN(basis_abs)      AS min_basis_abs,
                        MAX(basis_abs)      AS max_basis_abs
                    FROM (
                        SELECT
                            FLOOR(ts / {interval_ms}) * {interval_ms} AS bucket,
                            AVG(basis_abs)  AS basis_abs,
                            AVG(basis_pct)  AS basis_pct
                        FROM basis_1m
                        GROUP BY bucket
                    )
                """).fetchone()
                rows_expected = None   # variable

            rows, mean_abs, mean_pct, std_abs, min_abs, max_abs = stats
            elapsed = time.time() - t0

            join_ok = rows is not None and rows > 0
            status  = "PASS" if join_ok else "FAIL"
            result["resolutions"][res_name] = {
                "status":         status,
                "rows":           int(rows) if rows else 0,
                "mean_basis_abs": round(float(mean_abs),  4) if mean_abs else None,
                "mean_basis_pct": round(float(mean_pct),  6) if mean_pct else None,
                "std_basis_abs":  round(float(std_abs),   4) if std_abs  else None,
                "min_basis_abs":  round(float(min_abs),   2) if min_abs  else None,
                "max_basis_abs":  round(float(max_abs),   2) if max_abs  else None,
            }
            basis_sign = "+" if (mean_abs or 0) >= 0 else ""
            print(f"  {res_name:>3s}  [{status}]  {int(rows):>7,} bars  "
                  f"mean_basis={basis_sign}{mean_abs:.2f} ({basis_sign}{mean_pct:.4f}%)  "
                  f"std={std_abs:.2f}  ({elapsed:.1f}s)", flush=True)

        except Exception as exc:
            result["resolutions"][res_name] = {"status": "ERROR", "detail": repr(exc)[:120]}
            print(f"  {res_name:>3s}  [ERROR] {repr(exc)[:80]}", flush=True)
            result["status"] = "PARTIAL"

    conn.close()

    all_pass = all(v.get("status") == "PASS" for v in result["resolutions"].values())
    if not all_pass:
        result["status"] = "PARTIAL"

    return result


# ---------------------------------------------------------------------------
# Stage 5 -- Delta report + corpus_summary update
# ---------------------------------------------------------------------------

def stage_delta_and_summary(gauntlet_data: dict, basis_data: dict) -> dict:
    print(f"\n{_sep()}")
    print(f"{_ts('STAGE 5')}  Delta report + corpus_summary update")
    print(_sep())

    partitions = list_partitions(BASE_DIR, DATASET, SYMBOL)
    if not partitions:
        print("  No perp partitions found -- nothing to report.")
        return {}

    ds_rows  = 0
    ds_bytes = 0
    min_ts: int | None = None
    max_ts: int | None = None
    wi6_count = 0

    for p in partitions:
        parquet = p["path"]
        try:
            m      = read_manifest(parquet)
            r      = m.get("rows", 0)
            lo     = m.get("min_ts")
            hi     = m.get("max_ts")
            is_wi6 = m.get("schema_version") == 1
        except FileNotFoundError:
            df  = pq.read_table(parquet).to_pandas()
            r   = len(df)
            ts  = df[TS_COL].astype("int64")
            lo  = int(ts.min()) if r > 0 else None
            hi  = int(ts.max()) if r > 0 else None
            is_wi6 = False

        ds_rows += r
        if lo and (min_ts is None or lo < min_ts):
            min_ts = lo
        if hi and (max_ts is None or hi > max_ts):
            max_ts = hi
        if is_wi6:
            wi6_count += 1
        ds_bytes += _disk_bytes(parquet.parent)

    # Build delta report
    g = gauntlet_data
    delta: dict = {
        "generated_at":      datetime.now(timezone.utc).isoformat(),
        "corpus_end_month":  f"{LAST_COMPLETE[0]}-{LAST_COMPLETE[1]:02d}",
        "dataset":           DATASET,
        "symbol":            SYMBOL,
        "partitions_added":  len(partitions),
        "first_partition":   f"{partitions[0]['year']}-{partitions[0]['month']:02d}",
        "last_partition":    f"{partitions[-1]['year']}-{partitions[-1]['month']:02d}",
        "rows_added":        ds_rows,
        "first_timestamp":   _ms_to_iso(min_ts),
        "last_timestamp":    _ms_to_iso(max_ts),
        "wi6_manifests":     wi6_count,
        "disk_bytes":        ds_bytes,
        "disk_mb":           round(ds_bytes / 1_048_576, 1),
        "gauntlet": {
            "partitions_checked": len(g.get("partition_results", [])),
            "passed":            g.get("passed", 0),
            "failed":            g.get("failed", 0),
            "total_critical":    g.get("total_critical", 0),
            "total_warning":     g.get("total_warning", 0),
        },
        "basis_verification": basis_data,
    }

    # Compute readiness assessment
    readiness = _readiness_assessment()
    delta["readiness"] = readiness

    # Write perp_delta.json
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    DELTA_PATH.write_text(json.dumps(delta, indent=2, default=str), encoding="utf-8")

    # Update corpus_summary.json
    if SUMMARY_PATH.exists():
        summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
    else:
        summary = {
            "generated_at":     datetime.now(timezone.utc).isoformat(),
            "corpus_end_month": f"{LAST_COMPLETE[0]}-{LAST_COMPLETE[1]:02d}",
            "datasets":         {},
            "quality":          {"total_partitions_checked": 0, "total_passed": 0,
                                 "total_failed": 0, "total_critical": 0,
                                 "total_warning": 0, "by_dataset": {}},
            "storage":          {"total_rows": 0, "total_disk_bytes": 0,
                                 "total_disk_mb": 0, "total_disk_gb": 0},
        }

    # Add / overwrite perp section in datasets
    summary["generated_at"] = datetime.now(timezone.utc).isoformat()
    summary["datasets"]["perp"] = {
        "partition_count": len(partitions),
        "first_partition": delta["first_partition"],
        "last_partition":  delta["last_partition"],
        "total_rows":      ds_rows,
        "first_timestamp": delta["first_timestamp"],
        "last_timestamp":  delta["last_timestamp"],
        "wi6_manifests":   wi6_count,
        "legacy_manifests": 0,
        "disk_bytes":      ds_bytes,
        "disk_mb":         delta["disk_mb"],
    }

    # Update quality totals: remove old perp contribution (if any), add fresh
    q = summary["quality"]
    old_perp_q = q["by_dataset"].get("perp", {"passed": 0, "failed": 0,
                                               "critical": 0, "warning": 0})
    q["total_partitions_checked"] += (g["passed"] + g["failed"]) - \
        (old_perp_q["passed"] + old_perp_q["failed"])
    q["total_passed"]   += g["passed"]    - old_perp_q["passed"]
    q["total_failed"]   += g["failed"]    - old_perp_q["failed"]
    q["total_critical"] += g["total_critical"] - old_perp_q["critical"]
    q["total_warning"]  += g["total_warning"]  - old_perp_q["warning"]

    warnings_list = []
    for r in g.get("partition_results", []):
        for f in r.get("findings", []):
            if f["severity"] == "WARNING":
                warnings_list.append(
                    f"{r['year']}-{r['month']:02d}: {f['check']}: {f['detail'][:80]}"
                )
    q["by_dataset"]["perp"] = {
        "passed":   g["passed"],
        "failed":   g["failed"],
        "critical": g["total_critical"],
        "warning":  g["total_warning"],
        "warnings": warnings_list,
    }

    # Update storage totals
    st = summary["storage"]
    old_perp_bytes = 0  # fresh addition, not subtracting previous
    # Recalculate from scratch across all datasets to stay consistent
    total_rows  = sum(v.get("total_rows", 0)  for v in summary["datasets"].values())
    total_bytes = sum(v.get("disk_bytes", 0) for v in summary["datasets"].values())
    st["total_rows"]       = total_rows
    st["total_disk_bytes"] = total_bytes
    st["total_disk_mb"]    = round(total_bytes / 1_048_576,    1)
    st["total_disk_gb"]    = round(total_bytes / 1_073_741_824, 3)

    SUMMARY_PATH.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

    # Human-readable report
    print(f"\n{_sep('=')}")
    print("  PERP CORPUS DELTA REPORT")
    print(_sep("="))
    print(f"  Generated   : {delta['generated_at']}")
    print(f"  Corpus end  : {delta['corpus_end_month']}")
    print()
    print(f"  PERP OHLCV  ({SYMBOL})")
    print(f"    Partitions  : {len(partitions)}  "
          f"({delta['first_partition']} -> {delta['last_partition']})")
    print(f"    Rows        : {ds_rows:,}")
    print(f"    First ts    : {delta['first_timestamp']}")
    print(f"    Last ts     : {delta['last_timestamp']}")
    print(f"    Manifests   : {wi6_count} WI-6")
    print(f"    Disk        : {delta['disk_mb']} MB")
    print(f"    Quality     : {g['passed']}/{len(partitions)} PASS  "
          f"{g['total_critical']} CRITICAL  {g['total_warning']} WARNING")
    print()
    print(f"  Corpus total after perp addition:")
    print(f"    Total rows  : {total_rows:,}")
    print(f"    Total disk  : {round(total_bytes / 1_048_576, 1)} MB  "
          f"({round(total_bytes / 1_073_741_824, 3)} GB)")
    print()
    print(f"  BASIS VERIFICATION")
    if basis_data.get("status") == "PASS":
        print(f"    Sample window : {basis_data.get('sample_window', '?')}")
        for res, stats in basis_data.get("resolutions", {}).items():
            s = stats.get("status", "?")
            rows = stats.get("rows", 0)
            m_abs = stats.get("mean_basis_abs")
            m_pct = stats.get("mean_basis_pct")
            sign  = "+" if (m_abs or 0) >= 0 else ""
            print(f"    {res:>3s}  [{s}]  {rows:>7,} bars  "
                  f"mean_basis={sign}{m_abs:.2f} ({sign}{m_pct:.4f}%)" if m_abs else
                  f"    {res:>3s}  [{s}]  {rows:>7,} bars")
    else:
        print(f"    Status: {basis_data.get('status', 'UNKNOWN')}")
    print()
    _print_readiness(readiness)
    print(_sep("="))
    print(f"\n  Reports:")
    print(f"    {DELTA_PATH}")
    print(f"    {SUMMARY_PATH}")

    return delta


def _readiness_assessment() -> dict:
    """
    Derive corpus readiness for each study type from what is actually on disk.
    Returns a structured dict with status, requirements_met, and missing.
    """

    def _ds_ok(dataset: str) -> bool:
        return len(list_partitions(BASE_DIR, dataset, SYMBOL)) > 0

    spot_ok    = _ds_ok("spot")
    perp_ok    = _ds_ok("perp")
    funding_ok = _ds_ok("funding")

    calendar_path = (pathlib.Path(__file__).parent / "data" / "calendar"
                     / "macro_events.csv")
    calendar_ok   = calendar_path.exists()

    return {
        "funding_studies": {
            "status": "READY" if (spot_ok and funding_ok and perp_ok) else "PARTIAL",
            "requirements_met": (
                (["spot_ohlcv"] if spot_ok else []) +
                (["funding_rates"] if funding_ok else []) +
                (["perp_ohlcv", "continuous_basis"] if perp_ok else [])
            ),
            "missing": (
                (["spot_ohlcv"] if not spot_ok else []) +
                (["funding_rates"] if not funding_ok else []) +
                (["perp_ohlcv"] if not perp_ok else [])
            ),
            "can_test": [
                "H1: funding regime vs return distribution (Kruskal-Wallis)",
                "H6: spot-funding correlation (Spearman + Newey-West OLS)",
                "basis carry regime (perp-spot continuous basis)",
                "funding rate persistence / autocorrelation",
            ],
        },
        "basis_studies": {
            "status": "READY" if (spot_ok and perp_ok) else "BLOCKED",
            "requirements_met": (
                (["spot_ohlcv"] if spot_ok else []) +
                (["perp_ohlcv"] if perp_ok else [])
            ),
            "missing": (
                (["spot_ohlcv"] if not spot_ok else []) +
                (["perp_ohlcv"] if not perp_ok else [])
            ),
            "can_test": [
                "perp-spot basis level and sign distribution",
                "basis as funding rate predictor (leads by ~8h)",
                "basis regime classification",
                "basis mean-reversion speed",
                "basis divergence as signal (basis > threshold -> funding will spike)",
            ],
            "supported_resolutions": ["1m", "1H", "4H", "1D"] if (spot_ok and perp_ok) else [],
        },
        "event_studies": {
            "status": "READY" if (spot_ok and calendar_ok) else "PARTIAL",
            "requirements_met": (
                (["spot_ohlcv"] if spot_ok else []) +
                (["macro_calendar_101_events"] if calendar_ok else [])
            ),
            "missing": (
                (["spot_ohlcv"] if not spot_ok else []) +
                (["macro_calendar"] if not calendar_ok else [])
            ),
            "can_test": [
                "H2: FOMC/CPI pre/post drift (Binomial + Mann-Whitney)",
                "H3: autocorrelation structure around events (Ljung-Box + AR1)",
                "event-day return distribution vs. non-event",
                "FOMC vs. CPI differential response",
                "BTC halving event window (single event, indicative only)",
            ],
            "note": (
                "Perp OHLCV also enables event-study on perp basis and "
                "funding rate behavior around macro events."
            ),
        },
        "volatility_studies": {
            "status": "PARTIAL",
            "requirements_met": (
                (["spot_ohlcv_realized_vol_derivable"] if spot_ok else []) +
                (["perp_ohlcv_basis_vol_derivable"] if perp_ok else [])
            ),
            "missing": [
                "implied_vol_surface (Deribit ATM IV history)",
                "dvol_index (Deribit DVOL / BitVol BVIV)",
                "put_call_skew (25-delta risk reversal history)",
                "options_oi_by_strike",
            ],
            "can_test": [
                "H3: realized vol autocorrelation (Ljung-Box + AR1)",
                "vol regime classification (low / mid / high realized vol)",
                "GARCH vol forecast vs. realized",
                "vol clustering and regime persistence",
                "basis vol and funding vol joint dynamics",
            ],
            "cannot_test": [
                "H4: IV crush around daily expiry (requires implied vol)",
                "variance risk premium (IV minus realized)",
                "skew as tail-event predictor",
                "term structure inversion signal",
            ],
            "recommendation": (
                "Source Deribit daily ATM IV snapshots (free public API) "
                "and DVOL index to complete volatility study capability. "
                "Priority: H4 (IV crush) is blocked until this is added."
            ),
        },
    }


def _print_readiness(r: dict) -> None:
    print("  READINESS ASSESSMENT")
    print(f"  {'-'*40}")
    for study, info in r.items():
        status = info.get("status", "?")
        tag    = "[READY]" if status == "READY" else "[PARTIAL]" if status == "PARTIAL" else "[BLOCKED]"
        print(f"  {study.replace('_', ' ').title():30s} {tag}")
        for item in info.get("requirements_met", []):
            print(f"      [+] {item}")
        for item in info.get("missing", []):
            print(f"      [-] {item}  (missing)")
        for test in info.get("can_test", []):
            print(f"      --> {test}")
        if info.get("cannot_test"):
            for test in info["cannot_test"]:
                print(f"      [x] {test}  (blocked)")
        if info.get("recommendation"):
            print(f"      NOTE: {info['recommendation'][:100]}")
        if info.get("supported_resolutions"):
            print(f"      Resolutions: {', '.join(info['supported_resolutions'])}")
        print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Build perp OHLCV corpus extension.")
    ap.add_argument("--force", action="store_true",
                    help="Re-download all perp partitions even if they already exist")
    ap.add_argument("--skip-ingest", action="store_true",
                    help="Skip stage 1 (manifests + gauntlet + basis only)")
    args = ap.parse_args()

    t_total = time.time()
    print(f"\n{_sep('=')}")
    print(f"  PERP CORPUS BUILD  --  "
          f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Perp    : {PERP_START[0]}-{PERP_START[1]:02d} -> "
          f"{LAST_COMPLETE[0]}-{LAST_COMPLETE[1]:02d}")
    print(_sep("="))

    if not args.skip_ingest:
        stage_ingest_perp(force=args.force)

    stage_manifest_upgrade()
    gauntlet_data = stage_gauntlet()
    basis_data    = stage_basis_verification()
    stage_delta_and_summary(gauntlet_data, basis_data)

    elapsed = time.time() - t_total
    crit = gauntlet_data.get("total_critical", 0)
    if crit == 0:
        print(f"\n  [PASS] PERP CORPUS BUILD COMPLETE -- 0 CRITICAL findings")
    else:
        print(f"\n  [FAIL] PERP CORPUS BUILD -- {crit} CRITICAL finding(s) require investigation")
    print(f"  Total elapsed: {elapsed / 60:.1f} min\n")


if __name__ == "__main__":
    main()
