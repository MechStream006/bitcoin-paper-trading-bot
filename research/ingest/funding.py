"""
Binance USDT-M perpetual funding-rate ingestion for BTC research lake (WI-4).

Source   : Binance USDT-M futures via CCXT (binanceusdm, public endpoint,
           no API key required).  The full funding history is ~5,500 records
           over 5 years — small enough that CCXT is faster than a bulk archive.

Cadence  : 3 payments per day at 00:00 / 08:00 / 16:00 UTC → 28,800,000 ms.

Schema   : timestamp_ms (int64, UTC ms), funding_rate (float64).

Entry points:
    ingest_month(symbol, year, month, base_dir, *, force=False) -> dict
    ingest_range(symbol, start_ym, end_ym, base_dir, *, force=False) -> list[dict]

CLI:
    python ingest/funding.py --start 2021-06 --end 2024-03
"""
from __future__ import annotations

import argparse
import calendar
import json
import logging
import pathlib
import sys
import time
from datetime import datetime, timezone

import pandas as pd

# Ensure research/ root is importable regardless of invocation directory.
_RESEARCH_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_ROOT))

from storage.lake import partition_path, write_partition  # noqa: E402

log = logging.getLogger(__name__)

DATASET       = "funding"
SYMBOL_LAKE   = "BTCUSDT"       # lake path component
CCXT_SYMBOL   = "BTC/USDT:USDT" # binanceusdm market identifier
CCXT_EXCHANGE = "binanceusdm"
CADENCE_MS    = 8 * 3_600 * 1_000   # 28,800,000 ms — funding period
CCXT_LIMIT    = 1_000               # Binance max records per request


def _month_bounds_ms(year: int, month: int) -> tuple[int, int]:
    """UTC millisecond boundaries (inclusive) for the calendar month."""
    start = int(datetime(year, month, 1, tzinfo=timezone.utc).timestamp() * 1_000)
    last  = calendar.monthrange(year, month)[1]
    end   = int(
        datetime(year, month, last, 23, 59, 59, 999_000, tzinfo=timezone.utc).timestamp() * 1_000
    )
    return start, end


def _fetch_ccxt(since_ms: int, until_ms: int) -> pd.DataFrame:
    """
    Paginated CCXT fetch for funding records in [since_ms, until_ms].
    Advances the cursor past the last returned timestamp until no more
    records arrive or the cursor exceeds until_ms.
    """
    import ccxt

    exchange: ccxt.Exchange = getattr(ccxt, CCXT_EXCHANGE)({"enableRateLimit": True})

    rows: list[dict] = []
    cursor = since_ms
    while cursor <= until_ms:
        batch = exchange.fetch_funding_rate_history(
            CCXT_SYMBOL, since=cursor, limit=CCXT_LIMIT
        )
        if not batch:
            break
        in_range = [r for r in batch if r["timestamp"] <= until_ms]
        rows.extend(in_range)
        if len(batch) < CCXT_LIMIT or not in_range:
            break
        cursor = batch[-1]["timestamp"] + 1
        time.sleep(exchange.rateLimit / 1_000)

    if not rows:
        return pd.DataFrame(columns=["timestamp_ms", "funding_rate"])

    return pd.DataFrame({
        "timestamp_ms": [r["timestamp"] for r in rows],
        "funding_rate": [float(r["fundingRate"]) for r in rows],
    })


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Sort by timestamp_ms, deduplicate, enforce dtypes."""
    df = df.copy()
    df["timestamp_ms"] = df["timestamp_ms"].astype("int64")
    df["funding_rate"] = df["funding_rate"].astype("float64")
    df.sort_values("timestamp_ms", inplace=True)
    df.drop_duplicates(subset="timestamp_ms", keep="first", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


def _write_manifest(part_dir: pathlib.Path, meta: dict) -> pathlib.Path:
    """JSON provenance sidecar alongside data.parquet. WI-6 adds SHA-256."""
    path = part_dir / "_manifest.json"
    path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return path


def _already_ingested(part_dir: pathlib.Path) -> bool:
    return (part_dir / "data.parquet").exists() and (part_dir / "_manifest.json").exists()


# ── Public API ────────────────────────────────────────────────────────────────

def ingest_month(
    symbol: str,
    year: int,
    month: int,
    base_dir: str | pathlib.Path,
    *,
    force: bool = False,
) -> dict:
    """
    Ingest one calendar month of funding-rate data.

    Returns {"skipped": True, ...} without any network I/O if the partition
    already exists and force=False.  Otherwise fetches from CCXT, normalises,
    writes via storage/lake.py, and records a _manifest.json sidecar.
    """
    base_dir = pathlib.Path(base_dir)
    part_dir = partition_path(base_dir, DATASET, symbol, year, month)

    if not force and _already_ingested(part_dir):
        log.info("Skip (exists): funding/%s %d-%02d", symbol, year, month)
        return {"skipped": True, "dataset": DATASET, "symbol": symbol,
                "year": year, "month": month}

    since_ms, until_ms = _month_bounds_ms(year, month)
    df = _fetch_ccxt(since_ms, until_ms)
    df = _normalize(df)

    # Trim any records that leaked outside the requested month.
    outside = (~df["timestamp_ms"].between(since_ms, until_ms)).sum()
    if outside:
        log.warning("%d funding records outside %d-%02d — trimmed", outside, year, month)
        df = df[df["timestamp_ms"].between(since_ms, until_ms)].copy()

    write_partition(df, base_dir, DATASET, symbol, year, month)

    meta = {
        "dataset":      DATASET,
        "symbol":       symbol,
        "year":         year,
        "month":        month,
        "cadence_ms":   CADENCE_MS,
        "source":       "ccxt",
        "ccxt_exchange": CCXT_EXCHANGE,
        "ccxt_symbol":  CCXT_SYMBOL,
        "rows":         len(df),
        "ingest_ts":    datetime.now(timezone.utc).isoformat(),
        "skipped":      False,
    }
    _write_manifest(part_dir, meta)

    log.info("Ingested funding/%s %d-%02d → %d rows", symbol, year, month, len(df))
    return meta


def ingest_range(
    symbol: str,
    start_ym: tuple[int, int],
    end_ym: tuple[int, int],
    base_dir: str | pathlib.Path,
    *,
    force: bool = False,
) -> list[dict]:
    """Ingest every calendar month from start_ym to end_ym inclusive."""
    results: list[dict] = []
    year, month = start_ym
    ey, em      = end_ym
    while (year, month) <= (ey, em):
        results.append(ingest_month(symbol, year, month, base_dir, force=force))
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
        description="Ingest BTCUSDT perpetual funding-rate history from Binance."
    )
    ap.add_argument("--symbol",   default=SYMBOL_LAKE)
    ap.add_argument("--start",    type=_parse_ym, required=True, metavar="YYYY-MM")
    ap.add_argument("--end",      type=_parse_ym, required=True, metavar="YYYY-MM")
    ap.add_argument(
        "--base-dir",
        default=str(_RESEARCH_ROOT / "data" / "raw"),
        help="Lake base directory (default: research/data/raw)",
    )
    ap.add_argument("--force", action="store_true", help="Re-ingest even if partition exists")
    args = ap.parse_args()

    results  = ingest_range(args.symbol, args.start, args.end, args.base_dir, force=args.force)
    ingested = sum(1 for r in results if not r.get("skipped"))
    skipped  = sum(1 for r in results if r.get("skipped"))
    print(f"Done — {ingested} ingested, {skipped} skipped, {len(results)} total.")


if __name__ == "__main__":
    main()
