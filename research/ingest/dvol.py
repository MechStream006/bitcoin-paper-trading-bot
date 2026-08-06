"""
Deribit DVOL (BTC volatility index) ingestion for the research lake.

Source   : Deribit public REST `public/get_volatility_index_data`
           (free, no auth). DVOL is the 30-day forward implied-vol index,
           the BTC analogue of the VIX.

Cadence  : daily (resolution=1D). One row per UTC day at 00:00 boundary.

Schema   : open_time (int64, UTC ms, day boundary), open, high, low, close
           (float64, DVOL in annualized vol *points*, e.g. 48.1 = 48.1%).

Quirks (CLAUDE.md §11, verified 2026-06-10):
  - BTC DVOL inception: 2021-03-24.
  - 1,000-row cap per request; the `continuation` token does NOT page
    backward when passed as a param. Paginate by explicit shrinking
    end_timestamp windows.

Entry points:
    ingest_range(symbol, start_ym, end_ym, base_dir, *, force=False) -> list[dict]

CLI:
    python ingest/dvol.py --start 2021-03 --end 2026-05
"""
from __future__ import annotations

import argparse
import logging
import pathlib
import sys
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

_RESEARCH_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_ROOT))

from storage.lake import partition_path, write_partition          # noqa: E402
from storage.manifest import write_manifest                       # noqa: E402
from storage.raw_lake import land_raw                              # noqa: E402
from quality.gauntlet import run_gauntlet                          # noqa: E402
from quality.admissibility import partition_ceiling                # noqa: E402

log = logging.getLogger(__name__)

DATASET      = "dvol"
SYMBOL_LAKE  = "BTC"          # DVOL is a BTC index, not a USDT pair
CURRENCY     = "BTC"
BASE_URL     = "https://www.deribit.com/api/v2/public/get_volatility_index_data"
RESOLUTION   = "1D"
DAY_MS       = 86_400_000
WINDOW_DAYS  = 900           # < 1000-row cap, safe per-request window
INCEPTION_MS = int(datetime(2021, 3, 24, tzinfo=timezone.utc).timestamp() * 1000)

# ADR-I009 §13.3 normalisation contract version for THIS source's
# _normalize(). Bump only when _normalize()'s own logic changes (dtype
# coercion, ordering, dedup rule, boundary handling) -- not for changes
# elsewhere in this file.
NORMALIZATION_CONTRACT_VERSION = 1

# ADR-I009 §13.2 -- the population statement every observation in this
# dataset exists under (WBS 1.5). Mandatory on every raw-lake landing
# (storage.raw_lake.land_raw() refuses a blank value); always passed on
# every canonical write below, making it mandatory IN PRACTICE for this
# path even though storage.manifest.write_manifest()'s own parameter stays
# optional for backward compatibility with other callers.
CONDITIONING = (
    "Deribit DVOL: the exchange's own published 30-day forward-looking "
    "BTC implied-volatility index, one observation per UTC calendar day at "
    "the 00:00 boundary. Complete for the queried range subject to (a) "
    "Deribit's DVOL inception at 2021-03-24 -- no observations exist "
    "before it by construction, not by omission -- and (b) the public "
    "endpoint's availability at ingest time. Unlike the audited option "
    "corpus, this is not an ATM/near-expiry sliding-window sample: DVOL is "
    "a single published index value per day, not a derived aggregate over "
    "a filtered instrument universe."
)


def _month_bounds_ms(year: int, month: int) -> tuple[int, int]:
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    nxt   = (datetime(year + 1, 1, 1, tzinfo=timezone.utc) if month == 12
             else datetime(year, month + 1, 1, tzinfo=timezone.utc))
    end   = nxt - timedelta(milliseconds=1)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def _fetch_window(start_ms: int, end_ms: int) -> list[list]:
    """One Deribit request for daily DVOL candles in [start_ms, end_ms]."""
    r = requests.get(BASE_URL, params=dict(
        currency=CURRENCY, start_timestamp=start_ms,
        end_timestamp=end_ms, resolution=RESOLUTION), timeout=60)
    r.raise_for_status()
    return r.json().get("result", {}).get("data", [])


def _fetch_range(start_ms: int, end_ms: int) -> pd.DataFrame:
    """
    Fetch all daily DVOL candles in [start_ms, end_ms] via explicit windowed
    pagination (the continuation token does not page backward). Deduplicated.
    """
    rows: dict[int, list] = {}
    cur = max(start_ms, INCEPTION_MS)
    while cur <= end_ms:
        win_end = min(cur + WINDOW_DAYS * DAY_MS, end_ms)
        batch = _fetch_window(cur, win_end)
        for row in batch:
            rows[int(row[0])] = row          # row = [ts, o, h, l, c]
        cur = win_end + DAY_MS
        time.sleep(0.2)                       # be polite to the public endpoint

    if not rows:
        return pd.DataFrame(columns=["open_time", "open", "high", "low", "close"])

    data = sorted(rows.values(), key=lambda x: x[0])
    return pd.DataFrame({
        "open_time": [int(x[0])    for x in data],
        "open":      [float(x[1])  for x in data],
        "high":      [float(x[2])  for x in data],
        "low":       [float(x[3])  for x in data],
        "close":     [float(x[4])  for x in data],
    })


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["open_time"] = df["open_time"].astype("int64")
    for c in ("open", "high", "low", "close"):
        df[c] = df[c].astype("float64")
    df.sort_values("open_time", inplace=True)
    df.drop_duplicates(subset="open_time", keep="first", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


def _already_ingested(part_dir: pathlib.Path) -> bool:
    return (part_dir / "data.parquet").exists() and (part_dir / "_manifest.json").exists()


def ingest_range(
    symbol: str,
    start_ym: tuple[int, int],
    end_ym: tuple[int, int],
    base_dir: str | pathlib.Path,
    *,
    force: bool = False,
) -> list[dict]:
    """
    Ingest DVOL for every calendar month in [start_ym, end_ym].
    Fetches the full span in one windowed pass, then writes monthly partitions
    with SHA-256 manifests. Months already present are skipped unless force.
    """
    base_dir = pathlib.Path(base_dir)

    # Determine which months need fetching
    months: list[tuple[int, int]] = []
    y, m = start_ym
    ey, em = end_ym
    while (y, m) <= (ey, em):
        months.append((y, m))
        m += 1
        if m > 12:
            m, y = 1, y + 1

    todo = [ym for ym in months
            if force or not _already_ingested(partition_path(base_dir, DATASET, symbol, *ym))]
    if not todo:
        return [{"skipped": True, "dataset": DATASET, "symbol": symbol,
                 "year": ym[0], "month": ym[1]} for ym in months]

    span_start = _month_bounds_ms(*todo[0])[0]
    span_end   = _month_bounds_ms(*todo[-1])[1]
    fetched = _fetch_range(span_start, span_end)   # as-received (Z2) -- pre-normalisation
    full    = _normalize(fetched)                   # canonical (Z3)

    results: list[dict] = []
    for (yy, mm) in months:
        part_dir = partition_path(base_dir, DATASET, symbol, yy, mm)
        if not force and _already_ingested(part_dir):
            results.append({"skipped": True, "dataset": DATASET, "symbol": symbol,
                            "year": yy, "month": mm})
            continue
        lo, hi = _month_bounds_ms(yy, mm)
        fetched_at = datetime.now(timezone.utc).isoformat()

        # Z2 -- land as-received, content-hash-addressed, revisioned
        # (WBS 1.2/1.3; ADR-I009 §9, §13.1, §16.1).
        raw_slice = fetched[fetched["open_time"].between(lo, hi)].copy().reset_index(drop=True)
        raw_receipt = land_raw(
            raw_slice, base_dir, dataset=DATASET, symbol=symbol, year=yy, month=mm,
            source="deribit", source_url=BASE_URL, conditioning=CONDITIONING,
            fetched_at=fetched_at,
        )

        # Z3 -- canonical, unchanged in shape/path from before this milestone.
        part = full[full["open_time"].between(lo, hi)].copy().reset_index(drop=True)
        out_file = write_partition(part, base_dir, DATASET, symbol, yy, mm)

        # Assessed -> Certified (WBS 1.4; ADR-I009 §10, §18; ADR-I014).
        # A low ceiling is a label, not a failure (ADR-I012 DA3) -- this
        # never blocks the write above, it only records what was found.
        gauntlet_report = run_gauntlet(
            part, dataset=DATASET, symbol=symbol, year=yy, month=mm, ts_column="open_time",
        )
        ceiling = partition_ceiling(gauntlet_report)

        write_manifest(
            out_file, dataset=DATASET, symbol=symbol, year=yy, month=mm,
            source="deribit", source_url=BASE_URL,
            rows=len(part),
            min_ts=int(part["open_time"].min()) if len(part) else 0,
            max_ts=int(part["open_time"].max()) if len(part) else 0,
            ts_column="open_time",
            ingest_ts=fetched_at,
            config_version=1,
            normalization_contract_version=NORMALIZATION_CONTRACT_VERSION,
            conditioning=CONDITIONING,
            raw_content_hash=raw_receipt["hash"],
            admissibility={
                "certification": ceiling.certification.name,
                "readiness": ceiling.readiness.name,
                "lattice_version": ceiling.lattice_version,
            },
        )
        results.append({
            "skipped": False, "dataset": DATASET, "symbol": symbol,
            "year": yy, "month": mm, "rows": len(part),
            "raw_hash": raw_receipt["hash"],
            "raw_is_new_revision": raw_receipt["is_new_revision"],
            "certification": ceiling.certification.name,
            "readiness": ceiling.readiness.name,
        })
        log.info(
            "Ingested dvol/%s %d-%02d -> %d rows (certification=%s, raw_revision=%d)",
            symbol, yy, mm, len(part), ceiling.certification.name, raw_receipt["revision_ordinal"],
        )
    return results


def _parse_ym(s: str) -> tuple[int, int]:
    try:
        y, m = s.split("-")
        yi, mi = int(y), int(m)
        if not (1 <= mi <= 12):
            raise ValueError
        return yi, mi
    except ValueError:
        raise argparse.ArgumentTypeError(f"Expected YYYY-MM, got {s!r}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Ingest Deribit BTC DVOL history.")
    ap.add_argument("--symbol", default=SYMBOL_LAKE)
    ap.add_argument("--start", type=_parse_ym, required=True, metavar="YYYY-MM")
    ap.add_argument("--end",   type=_parse_ym, required=True, metavar="YYYY-MM")
    ap.add_argument("--base-dir", default=str(_RESEARCH_ROOT / "data" / "raw"))
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    res = ingest_range(args.symbol, args.start, args.end, args.base_dir, force=args.force)
    ing = sum(1 for r in res if not r.get("skipped"))
    skp = sum(1 for r in res if r.get("skipped"))
    print(f"Done -- {ing} ingested, {skp} skipped, {len(res)} total.")


if __name__ == "__main__":
    main()
