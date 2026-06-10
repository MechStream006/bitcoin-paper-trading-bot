"""
build_dvol_corpus.py  --  Ingest Deribit BTC DVOL and extend the corpus.

Stages
------
  1. Ingest DVOL daily      : 2021-03 (inception) -> last complete month
  2. Quality gauntlet       : per-partition battery (daily cadence)
  3. Summary + corpus_summary.json update

Run from the research/ directory (so `ingest` is a package and stdlib
`calendar` is not shadowed by ingest/calendar.py):
    python build_dvol_corpus.py
"""
from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from ingest.dvol import ingest_range, INCEPTION_MS                 # noqa: E402
from quality.gauntlet import run_gauntlet                          # noqa: E402
from storage.lake import list_partitions, read_partition          # noqa: E402
from storage.manifest import read_manifest                        # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")

BASE_DIR     = pathlib.Path(__file__).parent / "data" / "raw"
REPORT_DIR   = pathlib.Path(__file__).parent / "reports"
SUMMARY_PATH = REPORT_DIR / "corpus_summary.json"
SYMBOL       = "BTC"
DATASET      = "dvol"
TS_COL       = "open_time"
DVOL_START   = (2021, 3)

_now = datetime.now(timezone.utc)
LAST_COMPLETE = (_now.year - 1, 12) if _now.month == 1 else (_now.year, _now.month - 1)


def _ts(label: str = "") -> str:
    return f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}]{f' {label}' if label else ''}"


def _sep(c="-", w=70) -> str:
    return c * w


def _disk_bytes(path: pathlib.Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _ms_to_iso(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC") if ms else None


def stage_ingest(force: bool) -> dict:
    print(f"\n{_sep()}\n{_ts('STAGE 1')}  DVOL ingestion ({DVOL_START[0]}-{DVOL_START[1]:02d} -> "
          f"{LAST_COMPLETE[0]}-{LAST_COMPLETE[1]:02d})\n{_sep()}")
    t0 = time.time()
    res = ingest_range(SYMBOL, DVOL_START, LAST_COMPLETE, BASE_DIR, force=force)
    ing = sum(1 for r in res if not r.get("skipped"))
    skp = sum(1 for r in res if r.get("skipped"))
    rows = sum(r.get("rows", 0) for r in res if not r.get("skipped"))
    print(f"  ingested={ing}  skipped={skp}  rows_added={rows:,}  ({time.time()-t0:.0f}s)")
    return {"ingested": ing, "skipped": skp}


def stage_gauntlet() -> dict:
    print(f"\n{_sep()}\n{_ts('STAGE 2')}  Quality gauntlet\n{_sep()}")
    results, crit, warn = [], 0, 0
    for p in list_partitions(BASE_DIR, DATASET, SYMBOL):
        y, m = p["year"], p["month"]
        df = read_partition(BASE_DIR, DATASET, SYMBOL, y, m)
        r = run_gauntlet(df, dataset=DATASET, symbol=SYMBOL, year=y, month=m, ts_column=TS_COL)
        crit += r["critical_count"]; warn += r["warning_count"]; results.append(r)
        status = "PASS" if r["passed"] else "FAIL"
        cs = f"  {r['critical_count']}C" if r["critical_count"] else ""
        ws = f"  {r['warning_count']}W" if r["warning_count"] else ""
        print(f"  {_ts()} DVOL {y}-{m:02d}  {r['rows_checked']:>4} rows  [{status}]{cs}{ws}")
        for f in r.get("findings", []):
            if f["severity"] in ("CRITICAL", "WARNING"):
                print(f"    -> {f['severity'][0]} {f['check']}: {f['detail'][:90]}")
    passed = sum(1 for r in results if r["passed"])
    print(f"\n  partitions={len(results)}  PASS={passed}  FAIL={len(results)-passed}  "
          f"critical={crit}  warning={warn}")
    return {"partition_results": results, "passed": passed,
            "failed": len(results) - passed, "total_critical": crit, "total_warning": warn}


def stage_summary(g: dict) -> dict:
    print(f"\n{_sep()}\n{_ts('STAGE 3')}  Summary + corpus_summary.json\n{_sep()}")
    parts = list_partitions(BASE_DIR, DATASET, SYMBOL)
    rows = mn = mx = 0; mn = None; mx = None
    for p in parts:
        try:
            man = read_manifest(p["path"])
            rows += man.get("rows", 0)
            if man.get("rows", 0):
                mn = man["min_ts"] if mn is None else min(mn, man["min_ts"])
                mx = man["max_ts"] if mx is None else max(mx, man["max_ts"])
        except FileNotFoundError:
            pass
    disk = _disk_bytes(BASE_DIR / DATASET / SYMBOL)
    section = {
        "partition_count": len(parts),
        "first_partition": f"{parts[0]['year']}-{parts[0]['month']:02d}" if parts else None,
        "last_partition":  f"{parts[-1]['year']}-{parts[-1]['month']:02d}" if parts else None,
        "total_rows": rows,
        "first_timestamp": _ms_to_iso(mn),
        "last_timestamp":  _ms_to_iso(mx),
        "disk_mb": round(disk / 1_048_576, 3),
        "source": "deribit get_volatility_index_data",
        "inception": _ms_to_iso(INCEPTION_MS),
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8")) if SUMMARY_PATH.exists() else {"datasets": {}, "quality": {"by_dataset": {}}}
    summary.setdefault("datasets", {})["dvol"] = section
    q = summary.setdefault("quality", {}).setdefault("by_dataset", {})
    q["dvol"] = {"passed": g["passed"], "failed": g["failed"],
                 "critical": g["total_critical"], "warning": g["total_warning"]}
    summary["generated_at"] = datetime.now(timezone.utc).isoformat()
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

    print(f"  DVOL: {section['partition_count']} partitions  "
          f"({section['first_partition']} -> {section['last_partition']})")
    print(f"  rows={rows:,}  range={section['first_timestamp']} .. {section['last_timestamp']}")
    print(f"  disk={section['disk_mb']} MB")
    print(f"  corpus_summary.json updated -> {SUMMARY_PATH}")
    return section


def main() -> None:
    ap = argparse.ArgumentParser(description="Build DVOL corpus extension.")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--skip-ingest", action="store_true")
    args = ap.parse_args()

    t = time.time()
    print(f"\n{_sep('=')}\n  DVOL CORPUS BUILD  --  "
          f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n{_sep('=')}")
    if not args.skip_ingest:
        stage_ingest(force=args.force)
    g = stage_gauntlet()
    stage_summary(g)
    if g["total_critical"] == 0:
        print(f"\n  [PASS] DVOL CORPUS BUILD COMPLETE -- 0 CRITICAL findings")
    else:
        print(f"\n  [FAIL] DVOL CORPUS BUILD -- {g['total_critical']} CRITICAL finding(s)")
    print(f"  Total elapsed: {(time.time()-t)/60:.1f} min\n")


if __name__ == "__main__":
    main()
