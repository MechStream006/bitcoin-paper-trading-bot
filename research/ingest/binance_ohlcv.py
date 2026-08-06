"""
Binance OHLCV ingestion for BTC research lake (WI-3).

Primary path : data.binance.vision monthly ZIP archives — no auth, fast, complete.
Fallback path: CCXT (binance / binanceusdm) — rate-limited, for months not yet
               in the bulk archive (typically the current calendar month).

Public entry points:
    ingest_month(dataset, symbol, year, month, base_dir, *, force=False) -> dict
    ingest_range(dataset, symbol, start_ym, end_ym, base_dir, *, force=False) -> list[dict]

CLI:
    python ingest/binance_ohlcv.py --dataset spot --start 2024-01 --end 2024-03
"""
from __future__ import annotations

import pathlib as _pathlib
import sys as _sys

# When invoked as a script (`python ingest/binance_ohlcv.py`), Python inserts
# the script directory (research/ingest/) at sys.path[0] before any code runs.
# This causes `import calendar` below to find research/ingest/calendar.py instead
# of the stdlib calendar module. Pop the script dir first to restore correct lookup.
_this = str(_pathlib.Path(__file__).resolve().parent)
if _sys.path and _sys.path[0] == _this:
    _sys.path.pop(0)
del _this, _pathlib, _sys

import argparse
import calendar
import io
import json
import logging
import pathlib
import sys
import time
import zipfile
from datetime import datetime, timezone
from typing import Literal

import pandas as pd
import requests

# Ensure research/ root is importable regardless of invocation directory.
_RESEARCH_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_ROOT))

from storage.lake import partition_path, write_partition  # noqa: E402
from storage.raw_lake import land_raw                       # noqa: E402
from quality.gauntlet import run_gauntlet                    # noqa: E402
from quality.admissibility import partition_ceiling          # noqa: E402

log = logging.getLogger(__name__)

# ── Column specification ──────────────────────────────────────────────────────
# Binance klines: columns are positional. We keep only OHLCV + open_time.
_COL_IDX  = {0: "open_time", 1: "open", 2: "high", 3: "low", 4: "close", 5: "volume"}
_COL_KEEP = list(_COL_IDX.values())

# ── Bulk archive roots (no auth, public CDN) ──────────────────────────────────
_BULK_ROOT = {
    "spot": "https://data.binance.vision/data/spot/monthly/klines",
    "perp": "https://data.binance.vision/data/futures/um/monthly/klines",
}

# ── Minute-candle rows expected per day (1 row / minute) ─────────────────────
_ROWS_PER_DAY = 1440

# ADR-I009 §13.3 normalisation contract version for THIS source's
# _normalize(). Bump only when _normalize()'s own logic changes (dtype
# coercion, ordering, dedup rule, boundary handling -- e.g. the
# microsecond-vs-millisecond timestamp detection below) -- not for changes
# elsewhere in this file.
NORMALIZATION_CONTRACT_VERSION = 1


def _conditioning(dataset: str) -> str:
    """ADR-I009 §13.2 -- the population statement every observation exists
    under (WBS 1.6, reusing the WBS 1.5 pattern verified on
    research/ingest/dvol.py and research/ingest/funding.py). A function, not
    a module constant, because `dataset` ('spot'/'perp') is a per-call
    parameter here, unlike DATASET in the other two ingest writers.
    Mandatory on every raw-lake landing (storage.raw_lake.land_raw() refuses
    a blank value); always passed on every canonical write below.
    """
    label = "Binance spot BTCUSDT" if dataset == "spot" else "Binance USDT-M perpetual BTCUSDT"
    return (
        f"{label} 1-minute OHLCV candles, primarily from Binance Vision's "
        f"public monthly bulk archive (no auth), falling back to CCXT's "
        f"public REST API for months not yet archived (typically the "
        f"current calendar month). Complete for the queried range subject "
        f"to (a) Binance Vision's archive publication lag and (b) bulk-"
        f"archive/CCXT availability at ingest time. Every 1-minute candle "
        f"in the queried window is included; this is not an ATM/near-"
        f"expiry-conditioned sliding-window sample."
    )


def bulk_url(dataset: str, symbol: str, year: int, month: int, interval: str = "1m") -> str:
    """Build the public URL for a Binance Vision monthly klines ZIP."""
    root  = _BULK_ROOT[dataset]
    fname = f"{symbol}-{interval}-{year}-{month:02d}.zip"
    return f"{root}/{symbol}/{interval}/{fname}"


def _fetch_bulk(url: str) -> pd.DataFrame | None:
    """
    Download a Binance Vision monthly ZIP and parse the klines CSV.
    Returns None when the archive does not exist yet (HTTP 404).
    Raises on any other HTTP error.
    """
    log.debug("Bulk archive: %s", url)
    r = requests.get(url, timeout=90, stream=True)
    if r.status_code == 404:
        log.debug("Bulk archive not found (404): %s", url)
        return None
    r.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        csv_name  = next(n for n in zf.namelist() if n.endswith(".csv"))
        csv_bytes = zf.read(csv_name)

    # Binance Vision CSVs are headerless for most of history, but newer months
    # include a header row.  Peek at row 0, col 0: if it is not all-digits it
    # is a header.
    probe = pd.read_csv(io.BytesIO(csv_bytes), header=None, nrows=1, usecols=[0])
    has_header = not str(probe.iloc[0, 0]).lstrip("-").isdigit()

    df = pd.read_csv(
        io.BytesIO(csv_bytes),
        header=0 if has_header else None,
        usecols=list(_COL_IDX.keys()),
    )
    df.columns = _COL_KEEP
    return df


def _fetch_ccxt(dataset: str, ccxt_symbol: str, year: int, month: int) -> pd.DataFrame:
    """
    CCXT fallback: fetch 1-minute candles for the entire month via the
    Binance REST API.  Used only when the bulk archive is not yet available.
    """
    import ccxt  # local import; not needed unless fallback is triggered

    exchange_id = "binance" if dataset == "spot" else "binanceusdm"
    exchange: ccxt.Exchange = getattr(ccxt, exchange_id)({"enableRateLimit": True})

    month_start_ms = int(datetime(year, month, 1, tzinfo=timezone.utc).timestamp() * 1000)
    last_day       = calendar.monthrange(year, month)[1]
    month_end_ms   = int(
        datetime(year, month, last_day, 23, 59, 0, tzinfo=timezone.utc).timestamp() * 1000
    )

    rows: list[list] = []
    cursor = month_start_ms
    while cursor <= month_end_ms:
        batch = exchange.fetch_ohlcv(ccxt_symbol, "1m", since=cursor, limit=1000)
        if not batch:
            break
        rows.extend(batch)
        cursor = batch[-1][0] + 60_000
        if len(batch) < 1000:
            break
        time.sleep(exchange.rateLimit / 1000)

    if not rows:
        raise RuntimeError(
            f"CCXT returned no candles for {dataset} {ccxt_symbol} {year}-{month:02d}"
        )

    df = pd.DataFrame(rows, columns=_COL_KEEP)
    df = df[df["open_time"] <= month_end_ms].copy()
    return df


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """
    Enforce dtypes, sort by open_time, and drop duplicate timestamps.
    open_time is stored as int64 milliseconds UTC (Binance native format).
    NOTE: Binance Vision CSV files from 2025 onward use microsecond timestamps
    (16-digit values) instead of milliseconds (13-digit). Detect by threshold
    and divide by 1000 to normalize to milliseconds.
    """
    df = df[_COL_KEEP].copy()
    df["open_time"] = df["open_time"].astype("int64")
    # Microsecond timestamps are > 1e15; millisecond timestamps for any date
    # in our range (2017-2030) are < 2e12. Divide microseconds by 1000.
    if len(df) > 0 and df["open_time"].max() > 1_500_000_000_000_000:
        df["open_time"] = df["open_time"] // 1_000
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
    df.sort_values("open_time", inplace=True)
    df.drop_duplicates(subset="open_time", keep="first", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


def _month_bounds_ms(year: int, month: int) -> tuple[int, int]:
    """Return (start_ms, end_ms) inclusive for the calendar month, UTC."""
    start = int(datetime(year, month, 1, tzinfo=timezone.utc).timestamp() * 1000)
    last  = calendar.monthrange(year, month)[1]
    end   = int(datetime(year, month, last, 23, 59, 59, 999_000, tzinfo=timezone.utc).timestamp() * 1000)
    return start, end


def _write_manifest(part_dir: pathlib.Path, meta: dict) -> pathlib.Path:
    """
    Write a JSON provenance sidecar alongside data.parquet.
    WI-6 (storage/manifest.py) will add SHA-256 and formalise the schema.
    """
    path = part_dir / "_manifest.json"
    path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return path


def _already_ingested(part_dir: pathlib.Path) -> bool:
    """True only when both data.parquet and _manifest.json are present."""
    return (part_dir / "data.parquet").exists() and (part_dir / "_manifest.json").exists()


# ── Public API ────────────────────────────────────────────────────────────────

def ingest_month(
    dataset: Literal["spot", "perp"],
    symbol: str,
    year: int,
    month: int,
    base_dir: str | pathlib.Path,
    *,
    force: bool = False,
    interval: str = "1m",
    ccxt_symbol: str | None = None,
) -> dict:
    """
    Ingest one calendar month of 1-minute OHLCV data.

    If the partition already exists (data.parquet + _manifest.json) and
    force=False, returns {"skipped": True, ...} without any network I/O.

    Otherwise downloads from the bulk archive (primary) or CCXT (fallback),
    normalises to int64 ms timestamps, writes via storage/lake.py, and records
    a _manifest.json sidecar.  Returns the provenance dict.
    """
    base_dir = pathlib.Path(base_dir)
    part_dir = partition_path(base_dir, dataset, symbol, year, month)

    if not force and _already_ingested(part_dir):
        log.info("Skip (exists): %s/%s %d-%02d", dataset, symbol, year, month)
        return {
            "skipped": True, "dataset": dataset, "symbol": symbol,
            "year": year, "month": month,
        }

    url    = bulk_url(dataset, symbol, year, month, interval)
    source = "bulk"
    df     = _fetch_bulk(url)

    if df is None:
        log.info("Bulk unavailable, falling back to CCXT: %d-%02d", year, month)
        if ccxt_symbol is None:
            ccxt_symbol = "BTC/USDT" if dataset == "spot" else "BTC/USDT:USDT"
        df     = _fetch_ccxt(dataset, ccxt_symbol, year, month)
        source = "ccxt"

    fetched_at = datetime.now(timezone.utc).isoformat()
    conditioning = _conditioning(dataset)

    # Z2 -- land as-received, content-hash-addressed, revisioned
    # (WBS 1.6, reusing WBS 1.2/1.3; ADR-I009 §9, §13.1, §16.1).
    raw_receipt = land_raw(
        df, base_dir, dataset=dataset, symbol=symbol, year=year, month=month,
        source=source, source_url=url if source == "bulk" else None,
        conditioning=conditioning, fetched_at=fetched_at,
    )

    df = _normalize(df)   # canonical (Z3)

    # Trim any rows that leaked outside the requested month boundary.
    start_ms, end_ms = _month_bounds_ms(year, month)
    outside = (~df["open_time"].between(start_ms, end_ms)).sum()
    if outside:
        log.warning("%d rows outside %d-%02d range — trimmed", outside, year, month)
        df = df[df["open_time"].between(start_ms, end_ms)].copy()

    write_partition(df, base_dir, dataset, symbol, year, month)

    # Assessed -> Certified (WBS 1.6, reusing WBS 1.4; ADR-I009 §10, §18;
    # ADR-I014). Never blocks the write above -- a low ceiling is a label,
    # not a failure (ADR-I012 DA3).
    gauntlet_report = run_gauntlet(
        df, dataset=dataset, symbol=symbol, year=year, month=month, ts_column="open_time",
    )
    ceiling = partition_ceiling(gauntlet_report)

    meta = {
        "dataset":    dataset,
        "symbol":     symbol,
        "year":       year,
        "month":      month,
        "interval":   interval,
        "source":     source,
        "source_url": url if source == "bulk" else None,
        "rows":       len(df),
        "ingest_ts":  fetched_at,
        "skipped":    False,
        "normalization_contract_version": NORMALIZATION_CONTRACT_VERSION,
        "conditioning": conditioning,
        "raw_content_hash": raw_receipt["hash"],
        "admissibility": {
            "certification": ceiling.certification.name,
            "readiness": ceiling.readiness.name,
            "lattice_version": ceiling.lattice_version,
        },
    }
    _write_manifest(part_dir, meta)

    log.info(
        "Ingested %s/%s %d-%02d via %s → %d rows (certification=%s, raw_revision=%d)",
        dataset, symbol, year, month, source, len(df),
        ceiling.certification.name, raw_receipt["revision_ordinal"],
    )
    return {
        **meta,
        "raw_hash": raw_receipt["hash"],
        "raw_is_new_revision": raw_receipt["is_new_revision"],
        "certification": ceiling.certification.name,
        "readiness": ceiling.readiness.name,
    }


def ingest_range(
    dataset: Literal["spot", "perp"],
    symbol: str,
    start_ym: tuple[int, int],
    end_ym: tuple[int, int],
    base_dir: str | pathlib.Path,
    *,
    force: bool = False,
    interval: str = "1m",
) -> list[dict]:
    """
    Ingest every calendar month from start_ym to end_ym inclusive.
    Returns a list of provenance dicts (one per month).
    """
    results: list[dict] = []
    year, month = start_ym
    ey, em      = end_ym
    while (year, month) <= (ey, em):
        results.append(
            ingest_month(dataset, symbol, year, month, base_dir,
                         force=force, interval=interval)
        )
        month += 1
        if month > 12:
            month, year = 1, year + 1
    return results


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_ym(s: str) -> tuple[int, int]:
    try:
        dt = datetime.strptime(s, "%Y-%m")
        return dt.year, dt.month
    except ValueError:
        raise argparse.ArgumentTypeError(f"Expected YYYY-MM, got {s!r}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(
        description="Ingest BTC spot or perp 1m OHLCV from Binance Vision bulk archives."
    )
    ap.add_argument("--dataset", choices=["spot", "perp"], required=True)
    ap.add_argument("--symbol",  default="BTCUSDT")
    ap.add_argument("--start",   type=_parse_ym, required=True, metavar="YYYY-MM")
    ap.add_argument("--end",     type=_parse_ym, required=True, metavar="YYYY-MM")
    ap.add_argument(
        "--base-dir",
        default=str(_RESEARCH_ROOT / "data" / "raw"),
        help="Lake base directory (default: research/data/raw)",
    )
    ap.add_argument("--force", action="store_true", help="Re-ingest even if partition exists")
    args = ap.parse_args()

    results  = ingest_range(args.dataset, args.symbol, args.start, args.end,
                            args.base_dir, force=args.force)
    ingested = sum(1 for r in results if not r.get("skipped"))
    skipped  = sum(1 for r in results if r.get("skipped"))
    print(f"Done — {ingested} ingested, {skipped} skipped, {len(results)} total.")


if __name__ == "__main__":
    main()
