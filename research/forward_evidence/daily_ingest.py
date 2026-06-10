"""
daily_ingest.py — Incremental corpus refresh for forward evidence collection.

Fetches yesterday's BTCUSDT data from Binance/Deribit public REST APIs using
the existing ingest infrastructure, refreshing the current month's partitions
in the research data lake so daily_job.py has a new row to process.

Run at 00:05 UTC daily, before daily_job.py.

Exit codes: 0 = success, 1 = critical failure (spot or perp missing).
DVOL failures are non-critical warnings (handled as NaN in daily_job.py).

Run from research/:
    python forward_evidence/daily_ingest.py
    python forward_evidence/daily_ingest.py --dry-run
"""
from __future__ import annotations

import argparse
import pathlib
import sys
from datetime import datetime, timedelta, timezone

_HERE = pathlib.Path(__file__).resolve().parent
_RESEARCH = _HERE.parent
if str(_RESEARCH) not in sys.path:
    sys.path.insert(0, str(_RESEARCH))

from ingest import binance_ohlcv                   # noqa: E402
from ingest import funding as funding_ingest        # noqa: E402
from ingest import dvol as dvol_ingest             # noqa: E402
from storage.lake import list_partitions            # noqa: E402

RAW_BASE    = _RESEARCH / "data" / "raw"
SPOT_SYM    = "BTCUSDT"
FUND_SYM    = "BTCUSDT"
DVOL_SYM    = "BTC"


def _yesterday_utc() -> datetime:
    return (datetime.now(timezone.utc) - timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def _day_ts_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _spot_has_date(target_date: datetime) -> bool:
    """Return True if the spot partition contains any 1m candle for target_date."""
    day_start = _day_ts_ms(target_date)
    day_end   = day_start + 86_400_000 - 1

    parts = list_partitions(RAW_BASE, "spot", SPOT_SYM)
    target_ym = (target_date.year, target_date.month)
    part = next((p for p in parts if (p["year"], p["month"]) == target_ym), None)
    if part is None:
        return False

    import pyarrow.parquet as pq                   # noqa: PLC0415
    df = pq.read_table(str(part["path"]), columns=["open_time"]).to_pandas()
    return bool(df["open_time"].between(day_start, day_end).any())


def main() -> int:
    ap = argparse.ArgumentParser(description="Refresh today's corpus slice.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print what would be fetched; no network calls.")
    args = ap.parse_args()

    yesterday = _yesterday_utc()
    year, month = yesterday.year, yesterday.month

    print("=" * 70)
    print(f"  DAILY INGEST  —  target: {yesterday.date()}"
          f"  (partition: {year}-{month:02d})")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 70)

    if args.dry_run:
        print(f"  [DRY RUN] spot/{SPOT_SYM}  {year}-{month:02d}  force=True")
        print(f"  [DRY RUN] perp/{SPOT_SYM}  {year}-{month:02d}  force=True")
        print(f"  [DRY RUN] funding/{FUND_SYM}  {year}-{month:02d}  force=True")
        print(f"  [DRY RUN] dvol/{DVOL_SYM}  {year}-{month:02d}  force=True")
        return 0

    errors: list[str] = []

    # 1. Spot 1m
    print(f"  [1/4] spot/{SPOT_SYM}  {year}-{month:02d} ...", flush=True)
    try:
        r = binance_ohlcv.ingest_month(
            "spot", SPOT_SYM, year, month, RAW_BASE, force=True
        )
        print(f"        rows={r.get('rows', '?')}  source={r.get('source', '?')}")
    except Exception as exc:
        errors.append(f"spot: {exc}")
        print(f"  [ERROR] spot: {exc}")

    # 2. Perp 1m (same symbol, different dataset)
    print(f"  [2/4] perp/{SPOT_SYM}  {year}-{month:02d} ...", flush=True)
    try:
        r = binance_ohlcv.ingest_month(
            "perp", SPOT_SYM, year, month, RAW_BASE, force=True
        )
        print(f"        rows={r.get('rows', '?')}  source={r.get('source', '?')}")
    except Exception as exc:
        errors.append(f"perp: {exc}")
        print(f"  [ERROR] perp: {exc}")

    # 3. Funding (CCXT — always current through yesterday)
    print(f"  [3/4] funding/{FUND_SYM}  {year}-{month:02d} ...", flush=True)
    try:
        r = funding_ingest.ingest_month(
            FUND_SYM, year, month, RAW_BASE, force=True
        )
        print(f"        rows={r.get('rows', '?')}  source=ccxt")
    except Exception as exc:
        errors.append(f"funding: {exc}")
        print(f"  [ERROR] funding: {exc}")

    # 4. DVOL (Deribit public — non-critical if fails)
    print(f"  [4/4] dvol/{DVOL_SYM}  {year}-{month:02d} ...", flush=True)
    try:
        results = dvol_ingest.ingest_range(
            DVOL_SYM, (year, month), (year, month), RAW_BASE, force=True
        )
        total = sum(r.get("rows", 0) for r in results if not r.get("skipped"))
        print(f"        rows={total}")
    except Exception as exc:
        print(f"  [WARN] dvol: {exc}  (non-critical)")

    if errors:
        print(f"\n  [FAIL] {len(errors)} critical error(s):")
        for e in errors:
            print(f"    {e}")
        return 1

    # Verify spot partition now contains yesterday
    print(f"\n  Verifying spot partition has {yesterday.date()} ...", end=" ", flush=True)
    if _spot_has_date(yesterday):
        print("OK")
    else:
        print("MISSING")
        print(f"  [FAIL] {yesterday.date()} not found in spot lake after refresh")
        return 1

    print("  Daily ingest complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
