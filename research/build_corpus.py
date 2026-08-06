"""
build_corpus.py -- Full Phase B research corpus builder.

Stages
------
  1. Ingest spot OHLCV  : 2021-01 -> last complete month
  2. Ingest funding     : 2020-01 -> last complete month  (maximum history;
                          pre-launch months return 0 rows and are discarded)
  3. Manifest upgrade   : write/overwrite WI-6 _manifest.json for every
                          partition in the lake (SHA-256 + structured metadata)
  4. Quality gauntlet   : run 8-check battery on every partition
  5. Corpus summary     : JSON + human-readable report

Output
------
  research/reports/corpus_summary.json

Run from the research/ directory:
    python build_corpus.py

A re-run with no arguments is safe (stages 1-2 skip already-ingested months).
Use --force to re-download everything.

Runtime estimate: 20-40 min depending on network speed.
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

import pyarrow.parquet as pq

from ingest.binance_ohlcv import bulk_url
from ingest.binance_ohlcv import ingest_range as spot_ingest_range
from ingest.funding import ingest_range as fund_ingest_range
from quality.gauntlet import run_gauntlet
from storage.lake import list_partitions, partition_path, read_partition
from storage.manifest import (
    SCHEMA_VERSION, SIDECAR_NAME, read_manifest, verify_hash, write_manifest,
)

logging.basicConfig(
    level=logging.WARNING,           # suppress per-record CCXT noise
    format="%(levelname)s %(name)s %(message)s",
)

BASE_DIR    = pathlib.Path(__file__).parent / "data" / "raw"
REPORT_DIR  = pathlib.Path(__file__).parent / "reports"
REPORT_PATH = REPORT_DIR / "corpus_summary.json"
SYMBOL      = "BTCUSDT"

# Dynamic end month: last COMPLETE calendar month
_now = datetime.now(timezone.utc)
if _now.month == 1:
    LAST_COMPLETE = (_now.year - 1, 12)
else:
    LAST_COMPLETE = (_now.year, _now.month - 1)

SPOT_START = (2021, 1)
FUND_START = (2020, 1)   # pre-launch months return 0 rows and are discarded


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


# ---------------------------------------------------------------------------
# Stage 1 -- Spot OHLCV ingestion
# ---------------------------------------------------------------------------

def stage_ingest_spot(force: bool) -> dict:
    months = _ym_seq(SPOT_START, LAST_COMPLETE)
    print(f"\n{_sep()}")
    print(f"{_ts('STAGE 1')}  Spot OHLCV ingestion")
    print(f"  Range : {SPOT_START[0]}-{SPOT_START[1]:02d} -> {LAST_COMPLETE[0]}-{LAST_COMPLETE[1]:02d}  ({len(months)} months)")
    print(_sep())

    ingested = skipped = errors = 0
    t_stage = time.time()

    for year, month in months:
        t0 = time.time()
        try:
            meta = spot_ingest_range(
                "spot", SYMBOL, (year, month), (year, month), BASE_DIR, force=force
            )[0]
            elapsed = time.time() - t0
            if meta.get("skipped"):
                skipped += 1
                status = "skip"
            else:
                ingested += 1
                status = f"{meta['rows']:,} rows  src={meta.get('source', '?')}"
            print(f"  {_ts()} SPOT {year}-{month:02d}  {status}  ({elapsed:.1f}s)", flush=True)
        except Exception as exc:
            errors += 1
            print(f"  {_ts()} SPOT {year}-{month:02d}  ERROR: {repr(exc)[:80]}", flush=True)

    elapsed_total = time.time() - t_stage
    print(f"\n  Done -- ingested={ingested}  skipped={skipped}  errors={errors}  "
          f"elapsed={elapsed_total:.0f}s")
    return {"ingested": ingested, "skipped": skipped, "errors": errors}


# ---------------------------------------------------------------------------
# Stage 2 -- Funding ingestion
# ---------------------------------------------------------------------------

def stage_ingest_funding(force: bool) -> dict:
    months = _ym_seq(FUND_START, LAST_COMPLETE)
    print(f"\n{_sep()}")
    print(f"{_ts('STAGE 2')}  Funding rate ingestion (maximum history)")
    print(f"  Range : {FUND_START[0]}-{FUND_START[1]:02d} -> {LAST_COMPLETE[0]}-{LAST_COMPLETE[1]:02d}  ({len(months)} months)")
    print(f"  Note  : months before Binance USDT-M launch return 0 rows and are discarded")
    print(_sep())

    ingested = skipped = empty_discarded = errors = 0
    t_stage = time.time()

    for year, month in months:
        t0 = time.time()
        try:
            meta = fund_ingest_range(
                SYMBOL, (year, month), (year, month), BASE_DIR, force=force
            )[0]
            elapsed = time.time() - t0

            if meta.get("skipped"):
                skipped += 1
                print(f"  {_ts()} FUND {year}-{month:02d}  skip  ({elapsed:.1f}s)", flush=True)
                continue

            # Discard empty partitions (pre-launch months)
            if meta.get("rows", 0) == 0:
                part_dir = partition_path(BASE_DIR, "funding", SYMBOL, year, month)
                if part_dir.exists():
                    shutil.rmtree(part_dir)
                empty_discarded += 1
                print(f"  {_ts()} FUND {year}-{month:02d}  0 rows -> discarded  ({elapsed:.1f}s)", flush=True)
            else:
                ingested += 1
                print(f"  {_ts()} FUND {year}-{month:02d}  {meta['rows']} rows  ({elapsed:.1f}s)", flush=True)

        except Exception as exc:
            errors += 1
            print(f"  {_ts()} FUND {year}-{month:02d}  ERROR: {repr(exc)[:80]}", flush=True)

    elapsed_total = time.time() - t_stage
    print(f"\n  Done -- ingested={ingested}  skipped={skipped}  "
          f"discarded(empty)={empty_discarded}  errors={errors}  elapsed={elapsed_total:.0f}s")
    return {"ingested": ingested, "skipped": skipped,
            "empty_discarded": empty_discarded, "errors": errors}


# ---------------------------------------------------------------------------
# Stage 3 -- WI-6 manifest upgrade
# ---------------------------------------------------------------------------

def stage_manifest_upgrade() -> dict:
    print(f"\n{_sep()}")
    print(f"{_ts('STAGE 3')}  WI-6 manifest upgrade (SHA-256 + structured metadata)")
    print(_sep())

    upgraded = already_wi6 = errors = 0
    t_stage = time.time()

    for dataset in ["spot", "funding"]:
        ts_col = "open_time" if dataset == "spot" else "timestamp_ms"
        partitions = list_partitions(BASE_DIR, dataset, SYMBOL)
        for p in partitions:
            year, month = p["year"], p["month"]
            parquet_path = p["path"]
            sidecar = parquet_path.parent / SIDECAR_NAME

            # Read old manifest for provenance metadata
            old_m: dict = {}
            if sidecar.exists():
                try:
                    old_m = json.loads(sidecar.read_text(encoding="utf-8"))
                except Exception:
                    pass

            # Skip if already at the CURRENT manifest schema with a valid hash.
            # Compared against the imported constant, never a literal: this
            # gate previously hardcoded `== 1`, which silently inverted its
            # polarity when Phase 1 bumped SCHEMA_VERSION 1 -> 3 (WBS 4.0.1,
            # INC-0005). Reading the constant makes it self-maintaining across
            # any future bump -- validate_wi6.py's already-correct pattern.
            if old_m.get("schema_version") == SCHEMA_VERSION and "parquet_sha256" in old_m:
                try:
                    if verify_hash(parquet_path):
                        already_wi6 += 1
                        print(f"  {_ts()} {dataset.upper()} {year}-{month:02d}  already WI-6 [OK]", flush=True)
                        continue
                except Exception:
                    pass  # hash mismatch -- fall through to re-upgrade

            # Upgrade: read parquet for stats, write WI-6 manifest
            try:
                df = pq.read_table(parquet_path).to_pandas()
                if ts_col not in df.columns:
                    raise ValueError(f"ts_column '{ts_col}' not in {list(df.columns)}")
                ts = df[ts_col].astype("int64")
                rows   = len(df)
                min_ts = int(ts.min()) if rows > 0 else 0
                max_ts = int(ts.max()) if rows > 0 else 0

                # Recover provenance from old manifest
                source     = old_m.get("source", "bulk" if dataset == "spot" else "ccxt")
                source_url = old_m.get("source_url") or (
                    bulk_url(dataset, SYMBOL, year, month) if source == "bulk" else None
                )

                # Carry conformance metadata forward. write_manifest()
                # OVERWRITES the sidecar and defaults these four to None, so
                # omitting them destroys whatever the ingest writer (stage 1)
                # or backfill_conformance.py recorded. Same recovery pattern
                # as source/source_url above, extended to the fields Phase 1
                # added (WBS 4.0.1).
                write_manifest(
                    parquet_path,
                    dataset=dataset,
                    symbol=SYMBOL,
                    year=year,
                    month=month,
                    source=source,
                    source_url=source_url,
                    rows=rows,
                    min_ts=min_ts,
                    max_ts=max_ts,
                    ts_column=ts_col,
                    ingest_ts=old_m.get("ingest_ts", datetime.now(timezone.utc).isoformat()),
                    config_version=1,
                    normalization_contract_version=old_m.get("normalization_contract_version"),
                    conditioning=old_m.get("conditioning"),
                    admissibility=old_m.get("admissibility"),
                    raw_content_hash=old_m.get("raw_content_hash"),
                )
                upgraded += 1
                print(f"  {_ts()} {dataset.upper()} {year}-{month:02d}  upgraded to WI-6  rows={rows:,}", flush=True)
            except Exception as exc:
                errors += 1
                print(f"  {_ts()} {dataset.upper()} {year}-{month:02d}  MANIFEST ERROR: {repr(exc)[:80]}", flush=True)

    elapsed_total = time.time() - t_stage
    print(f"\n  Done -- upgraded={upgraded}  already_wi6={already_wi6}  errors={errors}  "
          f"elapsed={elapsed_total:.0f}s")
    return {"upgraded": upgraded, "already_wi6": already_wi6, "errors": errors}


# ---------------------------------------------------------------------------
# Stage 4 -- Quality gauntlet
# ---------------------------------------------------------------------------

def stage_gauntlet() -> dict:
    print(f"\n{_sep()}")
    print(f"{_ts('STAGE 4')}  Quality gauntlet (8 checks per partition)")
    print(_sep())

    results: list[dict] = []
    total_critical = total_warning = errors = 0
    t_stage = time.time()

    for dataset in ["spot", "funding"]:
        ts_col = "open_time" if dataset == "spot" else "timestamp_ms"
        partitions = list_partitions(BASE_DIR, dataset, SYMBOL)
        for p in partitions:
            year, month = p["year"], p["month"]
            t0 = time.time()
            try:
                df = read_partition(BASE_DIR, dataset, SYMBOL, year, month)
                r  = run_gauntlet(
                    df,
                    dataset=dataset,
                    symbol=SYMBOL,
                    year=year,
                    month=month,
                    ts_column=ts_col,
                )
                elapsed = time.time() - t0
                total_critical += r["critical_count"]
                total_warning  += r["warning_count"]
                results.append(r)

                status = "PASS" if r["passed"] else "FAIL"
                c_str  = f"  {r['critical_count']}C" if r["critical_count"] else ""
                w_str  = f"  {r['warning_count']}W"  if r["warning_count"]  else ""
                print(f"  {_ts()} {dataset.upper()} {year}-{month:02d}  "
                      f"{r['rows_checked']:>7,} rows  [{status}]{c_str}{w_str}  ({elapsed:.1f}s)",
                      flush=True)
                if r["warning_count"]:
                    for f in r["findings"]:
                        if f["severity"] == "WARNING":
                            print(f"    -> W {f['check']}: {f['detail'][:100]}", flush=True)
                if r["critical_count"]:
                    for f in r["findings"]:
                        if f["severity"] == "CRITICAL":
                            print(f"    -> C {f['check']}: {f['detail'][:100]}", flush=True)

            except Exception as exc:
                errors += 1
                print(f"  {_ts()} {dataset.upper()} {year}-{month:02d}  GAUNTLET ERROR: {repr(exc)[:80]}", flush=True)

    elapsed_total = time.time() - t_stage
    passed = sum(1 for r in results if r["passed"])
    failed = len(results) - passed
    print(f"\n  Done -- partitions={len(results)}  PASS={passed}  FAIL={failed}  "
          f"total_critical={total_critical}  total_warning={total_warning}  "
          f"errors={errors}  elapsed={elapsed_total:.0f}s")
    return {
        "partition_results": results,
        "total_critical":   total_critical,
        "total_warning":    total_warning,
        "passed":           passed,
        "failed":           failed,
        "errors":           errors,
    }


# ---------------------------------------------------------------------------
# Stage 5 -- Corpus summary
# ---------------------------------------------------------------------------

def stage_summary(gauntlet_data: dict) -> dict:
    print(f"\n{_sep()}")
    print(f"{_ts('STAGE 5')}  Corpus summary")
    print(_sep())

    summary: dict = {
        "generated_at":     datetime.now(timezone.utc).isoformat(),
        "corpus_end_month": f"{LAST_COMPLETE[0]}-{LAST_COMPLETE[1]:02d}",
        "datasets":         {},
        "quality":          {},
        "storage":          {},
    }

    total_rows  = 0
    total_bytes = 0

    for dataset in ["spot", "funding"]:
        ts_col     = "open_time" if dataset == "spot" else "timestamp_ms"
        partitions = list_partitions(BASE_DIR, dataset, SYMBOL)
        if not partitions:
            summary["datasets"][dataset] = {"status": "no partitions found"}
            continue

        ds_rows  = 0
        ds_bytes = 0
        min_ts   = None
        max_ts   = None
        wi6_count = 0

        for p in partitions:
            parquet = p["path"]
            try:
                m      = read_manifest(parquet)
                r      = m.get("rows", 0)
                lo     = m.get("min_ts")
                hi     = m.get("max_ts")
                is_wi6 = m.get("schema_version") == SCHEMA_VERSION
            except FileNotFoundError:
                df     = pq.read_table(parquet).to_pandas()
                r      = len(df)
                ts     = df[ts_col].astype("int64")
                lo     = int(ts.min()) if r > 0 else None
                hi     = int(ts.max()) if r > 0 else None
                is_wi6 = False

            ds_rows += r
            if lo and (min_ts is None or lo < min_ts):
                min_ts = lo
            if hi and (max_ts is None or hi > max_ts):
                max_ts = hi
            if is_wi6:
                wi6_count += 1
            ds_bytes += _disk_bytes(parquet.parent)

        total_rows  += ds_rows
        total_bytes += ds_bytes

        def _ms_to_iso(ms: int | None) -> str | None:
            if ms is None:
                return None
            return datetime.fromtimestamp(ms / 1_000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        summary["datasets"][dataset] = {
            "partition_count":  len(partitions),
            "first_partition":  f"{partitions[0]['year']}-{partitions[0]['month']:02d}",
            "last_partition":   f"{partitions[-1]['year']}-{partitions[-1]['month']:02d}",
            "total_rows":       ds_rows,
            "first_timestamp":  _ms_to_iso(min_ts),
            "last_timestamp":   _ms_to_iso(max_ts),
            "wi6_manifests":    wi6_count,
            "legacy_manifests": len(partitions) - wi6_count,
            "disk_bytes":       ds_bytes,
            "disk_mb":          round(ds_bytes / 1_048_576, 1),
        }

    # Quality rollup
    results     = gauntlet_data.get("partition_results", [])
    by_dataset: dict[str, dict] = {}
    for r in results:
        ds = r["dataset"]
        if ds not in by_dataset:
            by_dataset[ds] = {"passed": 0, "failed": 0, "critical": 0, "warning": 0, "warnings": []}
        by_dataset[ds]["passed" if r["passed"] else "failed"] += 1
        by_dataset[ds]["critical"] += r["critical_count"]
        by_dataset[ds]["warning"]  += r["warning_count"]
        for f in r.get("findings", []):
            if f["severity"] == "WARNING":
                by_dataset[ds]["warnings"].append(
                    f"{r['year']}-{r['month']:02d}: {f['check']}: {f['detail'][:80]}"
                )

    summary["quality"] = {
        "total_partitions_checked": len(results),
        "total_passed":   gauntlet_data["passed"],
        "total_failed":   gauntlet_data["failed"],
        "total_critical": gauntlet_data["total_critical"],
        "total_warning":  gauntlet_data["total_warning"],
        "by_dataset":     by_dataset,
    }

    summary["storage"] = {
        "total_rows":       total_rows,
        "total_disk_bytes": total_bytes,
        "total_disk_mb":    round(total_bytes / 1_048_576, 1),
        "total_disk_gb":    round(total_bytes / 1_073_741_824, 3),
    }

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

    # Human-readable summary
    print(f"\n{_sep('=')}")
    print("  CORPUS SUMMARY")
    print(_sep("="))
    print(f"  Generated   : {summary['generated_at']}")
    print(f"  Corpus end  : {summary['corpus_end_month']} (last complete month)")
    print()

    for ds in ["spot", "funding"]:
        ds_info = summary["datasets"].get(ds, {})
        if "status" in ds_info:
            print(f"  {ds.upper():10s}: {ds_info['status']}")
            continue
        print(f"  {ds.upper():10s}:")
        print(f"    Partitions  : {ds_info['partition_count']}  "
              f"({ds_info['first_partition']} -> {ds_info['last_partition']})")
        print(f"    Rows        : {ds_info['total_rows']:,}")
        print(f"    First ts    : {ds_info['first_timestamp']}")
        print(f"    Last ts     : {ds_info['last_timestamp']}")
        print(f"    Manifests   : {ds_info['wi6_manifests']} WI-6  {ds_info['legacy_manifests']} legacy")
        print(f"    Disk        : {ds_info['disk_mb']} MB")
        q    = by_dataset.get(ds, {})
        crit = q.get("critical", 0)
        warn = q.get("warning", 0)
        print(f"    Quality     : {q.get('passed', 0)}/{ds_info['partition_count']} PASS  "
              f"{crit} CRITICAL  {warn} WARNING")
        if q.get("warnings"):
            for w in q["warnings"][:5]:
                print(f"      -> W {w}")
            if len(q["warnings"]) > 5:
                print(f"      ... {len(q['warnings']) - 5} more warnings (see JSON report)")
        print()

    print(f"  TOTAL rows  : {total_rows:,}")
    print(f"  TOTAL disk  : {summary['storage']['total_disk_mb']} MB  "
          f"({summary['storage']['total_disk_gb']} GB)")
    print(f"\n  Report      : {REPORT_PATH}")
    print(_sep("="))

    overall_ok = gauntlet_data["total_critical"] == 0
    if overall_ok:
        print("\n  [PASS] CORPUS BUILD COMPLETE -- 0 CRITICAL findings")
        print("  Phase B evidence discovery can begin.")
    else:
        c = gauntlet_data["total_critical"]
        print(f"\n  [FAIL] CORPUS BUILD -- {c} CRITICAL finding(s) require investigation")

    return summary


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Build Phase B research corpus.")
    ap.add_argument("--force", action="store_true",
                    help="Re-download all partitions even if they already exist")
    ap.add_argument("--skip-ingest", action="store_true",
                    help="Skip stages 1+2 (manifests + gauntlet only)")
    args = ap.parse_args()

    t_total = time.time()
    print(f"\n{_sep('=')}")
    print(f"  CORPUS BUILD  --  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Spot    : {SPOT_START[0]}-{SPOT_START[1]:02d} -> {LAST_COMPLETE[0]}-{LAST_COMPLETE[1]:02d}")
    print(f"  Funding : {FUND_START[0]}-{FUND_START[1]:02d} -> {LAST_COMPLETE[0]}-{LAST_COMPLETE[1]:02d}  "
          f"(pre-launch months discarded)")
    print(_sep("="))

    if not args.skip_ingest:
        stage_ingest_spot(force=args.force)
        stage_ingest_funding(force=args.force)

    stage_manifest_upgrade()
    gauntlet_data = stage_gauntlet()
    stage_summary(gauntlet_data)

    elapsed = time.time() - t_total
    print(f"\n  Total elapsed: {elapsed / 60:.1f} min\n")


if __name__ == "__main__":
    main()
