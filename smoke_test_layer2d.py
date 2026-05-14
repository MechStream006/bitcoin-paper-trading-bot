"""Layer 2d diagnostic / smoke-monitoring script.

PURPOSE
-------
This script queries the live SQLite database while the bot is running
(or after it stops) to verify that Layer 2d is operating correctly.
It is read-only -- it never writes to the database.

USAGE
-----
Run alongside the bot in a second terminal:

    # Terminal 1 -- start the bot
    python src/main.py

    # Terminal 2 -- monitor DB state (repeat as needed)
    python smoke_test_layer2d.py

Or pass --once to print a single report and exit:

    python smoke_test_layer2d.py --once

Or pass --watch N to repeat every N seconds (default 30):

    python smoke_test_layer2d.py --watch 30

1-HOUR SMOKE TEST PROCEDURE
----------------------------
1. Ensure .env is populated with testnet credentials.
2. Delete or clear data/btc_trading.db if doing a fresh run:
       del data\btc_trading.db
3. Start the bot:
       python src/main.py
4. Verify startup sequence in logs:
   - "[runtime] state: INIT -> BOOTSTRAPPING"
   - "[runtime] initial chain refresh (REST)..."
   - "[runtime] universe: N symbols"
   - "[runtime] state: BOOTSTRAPPING -> WARMING"
   - "[runtime] warmup: X/N symbols ..."
   - "[runtime] state: WARMING -> RUNNING"
   - "[runtime] started 'scheduler'"
   - "[heartbeat-stub] ..." (or Telegram send if configured)
5. After 60-90s: first snapshot_quotes job fires (every 60s by default).
   Verify in this script: option_quotes rows > 0.
6. Every 30 min: run this script to check row growth.
7. At the 1-hour mark:
   - Press Ctrl+C in Terminal 1.
   - Verify in logs:
     - "[runtime] SIGINT received - requesting shutdown"
     - "[supervisor] shutdown requested: ..."
     - "[supervisor] supervision loop exited"
     - "[runtime] state: RUNNING -> DRAINING"
     - "[runtime] stopped 'scheduler'"
     - "[runtime] stopped 'heartbeat'"
     - "[runtime] stopped 'snapshot_writer'"
     - "[runtime] stopped 'chain_refresher'"
     - "[runtime] stopped 'ws_manager'"
     - "[runtime] DB closed"
     - "[runtime] state: DRAINING -> SHUTDOWN"
   - Verify process exits with code 0.
8. Run this script one final time to capture end-state metrics.

EXPECTED METRICS AFTER 1 HOUR
-------------------------------
  option_quotes rows:
    N_symbols * floor(3600 / snapshot_interval) approx 60 * 60 = 3600 rows
    (60 symbols * 60 snapshot cycles)

  snapshot cycles (distinct timestamps):
    ~60 (one per minute)

  funding_snapshots rows:
    ~60 (one per snapshot cycle for BTCUSDT)

  WS reconnects:
    0 on a stable connection; < 3 on testnet (acceptable)

  Scheduler job failures:
    0 for snapshot_quotes in normal operation
    chain_refresh: 0 unless testnet API is flaky

WHAT CAN GO WRONG (and what to check)
---------------------------------------
  - option_quotes rows not growing:
    Check WS is STREAMING (look for "state=STREAMING" in logs).
    Check instruments table is populated (run: SELECT COUNT(*) FROM instruments).
    Check snapshot_writer health in scheduler logs.

  - Coverage stuck at 0% during warmup:
    WS not receiving data. Check DELTA_WS_URL in .env.
    Check for "state=SUBSCRIBED" transition in logs.

  - Restart storm:
    CRITICAL log: "[supervisor] RESTART STORM" -- check which component.
    Check network stability; check Delta testnet status.

  - Empty universe after chain refresh:
    Testnet may have no live BTC options. Check /v2/products directly.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _conn() -> sqlite3.Connection:
    db_path = ROOT / "data" / "btc_trading.db"
    if not db_path.exists():
        print(f"[smoke] DB not found: {db_path}")
        print("[smoke] Has the bot been started? Run: python src/main.py")
        sys.exit(1)
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    return c


def _report(conn: sqlite3.Connection) -> None:
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"\n{'='*60}")
    print(f"  BTC Bot Layer 2d Diagnostic  [{now_utc}]")
    print(f"{'='*60}")

    # --- instruments ---
    row = conn.execute("SELECT COUNT(*) AS n FROM instruments WHERE underlying='BTC'").fetchone()
    n_instruments = row["n"]
    print(f"\ninstruments (BTC):  {n_instruments}")

    # --- option_quotes ---
    row = conn.execute("SELECT COUNT(*) AS n FROM option_quotes").fetchone()
    n_quotes = row["n"]

    row = conn.execute(
        "SELECT COUNT(DISTINCT ts) AS n FROM option_quotes"
    ).fetchone()
    n_cycles = row["n"]

    row = conn.execute(
        "SELECT ts FROM option_quotes ORDER BY ts DESC LIMIT 1"
    ).fetchone()
    latest_quote_ts = row["ts"] if row else "never"

    row = conn.execute(
        "SELECT ts FROM option_quotes ORDER BY ts ASC LIMIT 1"
    ).fetchone()
    first_quote_ts = row["ts"] if row else "never"

    print(f"\noption_quotes:")
    print(f"  total rows        : {n_quotes}")
    print(f"  snapshot cycles   : {n_cycles}")
    print(f"  first snapshot    : {first_quote_ts}")
    print(f"  latest snapshot   : {latest_quote_ts}")
    if n_instruments > 0 and n_cycles > 0:
        rows_per_cycle = n_quotes / n_cycles
        print(f"  rows / cycle      : {rows_per_cycle:.1f}  (expected ~{n_instruments})")

    # --- per-instrument row counts (top 5 and bottom 5) ---
    if n_quotes > 0:
        rows = conn.execute(
            """
            SELECT i.symbol, COUNT(*) AS n
            FROM option_quotes q
            JOIN instruments i ON i.id = q.instrument_id
            GROUP BY q.instrument_id
            ORDER BY n DESC
            """
        ).fetchall()
        if rows:
            print(f"\n  per-instrument row counts ({len(rows)} instruments):")
            show = rows[:5]
            if len(rows) > 10:
                show = rows[:3] + [None] + rows[-3:]
            for r in show:
                if r is None:
                    print(f"    ...")
                else:
                    print(f"    {r['symbol'][:35]:<35} {r['n']:>4}")

    # --- sample latest quote ---
    row = conn.execute(
        """
        SELECT i.symbol, q.ts, q.mark_price, q.mark_iv, q.delta, q.spot_price
        FROM option_quotes q
        JOIN instruments i ON i.id = q.instrument_id
        ORDER BY q.ts DESC LIMIT 1
        """
    ).fetchone()
    if row:
        print(f"\n  latest quote sample:")
        print(f"    symbol     : {row['symbol']}")
        print(f"    ts         : {row['ts']}")
        print(f"    mark_price : {row['mark_price']}")
        print(f"    mark_iv    : {row['mark_iv']}")
        print(f"    delta      : {row['delta']}")
        print(f"    spot_price : {row['spot_price']}")

    # --- funding_snapshots ---
    row = conn.execute("SELECT COUNT(*) AS n FROM funding_snapshots").fetchone()
    n_funding = row["n"]
    row = conn.execute(
        "SELECT ts FROM funding_snapshots ORDER BY ts DESC LIMIT 1"
    ).fetchone()
    latest_funding_ts = row["ts"] if row else "never"

    print(f"\nfunding_snapshots:")
    print(f"  total rows        : {n_funding}")
    print(f"  latest            : {latest_funding_ts}")

    # --- instruments table freshness ---
    row = conn.execute(
        "SELECT MAX(fetched_at) AS latest FROM instruments WHERE underlying='BTC'"
    ).fetchone()
    print(f"\nchain last refreshed: {row['latest'] if row else 'never'}")

    # --- validation checks ---
    print(f"\nvalidation:")
    checks = []

    if n_instruments == 0:
        checks.append("WARN: no BTC instruments in DB (chain_refresh not yet run?)")
    else:
        checks.append(f"OK: {n_instruments} BTC instruments loaded")

    if n_quotes == 0:
        checks.append("WARN: no option_quotes yet (snapshot not yet run? WS not streaming?)")
    else:
        checks.append(f"OK: {n_quotes} option_quote rows written")

    if n_quotes > 0 and n_instruments > 0 and n_cycles > 0:
        expected_per_cycle = n_instruments
        actual_per_cycle   = n_quotes / n_cycles
        ratio = actual_per_cycle / expected_per_cycle
        if ratio < 0.5:
            checks.append(
                f"WARN: only {ratio:.0%} coverage per snapshot cycle "
                f"({actual_per_cycle:.0f}/{expected_per_cycle} symbols)"
            )
        else:
            checks.append(f"OK: per-cycle coverage {ratio:.0%}")

    if n_funding == 0:
        checks.append("INFO: no funding_snapshots yet (normal if BTCUSDT not in WS channels)")
    else:
        checks.append(f"OK: {n_funding} funding_snapshot rows")

    for c in checks:
        print(f"  {c}")

    print(f"\n{'='*60}\n")


def main() -> None:
    ap = argparse.ArgumentParser(description="Layer 2d DB diagnostic")
    ap.add_argument("--once", action="store_true", help="Print once and exit")
    ap.add_argument("--watch", type=int, metavar="SEC",
                    help="Repeat every SEC seconds (default 30)", default=0)
    args = ap.parse_args()

    interval = args.watch if args.watch > 0 else 30

    if args.once or args.watch == 0 and not args.once:
        # Default: print once
        with _conn() as conn:
            _report(conn)
        if not args.watch:
            return

    print(f"[smoke] watching DB every {interval}s -- Ctrl+C to stop")
    try:
        while True:
            with _conn() as conn:
                _report(conn)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n[smoke] stopped")


if __name__ == "__main__":
    main()
