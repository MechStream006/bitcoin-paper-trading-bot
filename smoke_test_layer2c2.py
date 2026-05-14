"""Layer 2c.2 smoke test - persistent ticker + funding snapshots.

What it does:
  1. Refreshes BTC option chain, picks universe (~64 instruments).
  2. Starts DeltaWSListener (background thread) subscribing to:
       - v2/ticker for the universe
       - funding_rate for BTCUSDT
  3. Every SNAPSHOT_INTERVAL_SEC, writes the in-memory state to SQLite:
       - One option_quotes row per instrument (whatever's in latest ticker)
       - One funding_snapshots row for BTCUSDT
  4. Runs for DURATION_SEC, then prints a summary:
       - Total rows in option_quotes
       - Per-instrument row count (should be ~DURATION/INTERVAL for each)
       - Sample row for the ATM call
       - Funding row count

Run from project root:
    python smoke_test_layer2c2.py

Done condition (Layer 2c.2):
  - option_quotes has ~5 rows per instrument after a 5-min run.
  - All 64 instruments have rows (no missing).
  - Critical fields (mark_price, mark_iv, delta) are non-null in spot checks.
"""
import time
from collections import defaultdict
from datetime import datetime, timezone

from config.settings import (
    DB_PATH,
    DELTA_API_KEY,
    DELTA_API_SECRET,
    DELTA_BASE_URL,
    DELTA_WS_URL,
)
from src.data.storage import (
    get_connection,
    init_schema,
    write_funding_snapshot,
    write_option_quote,
)
from src.exchange.delta_client import DeltaClient
from src.exchange.delta_ws import DeltaWSListener
from src.exchange.instruments import (
    get_btc_mark_price,
    refresh_chain,
    select_universe,
)

SNAPSHOT_INTERVAL_SEC = 60
DURATION_SEC = 300  # 5 minutes
WARMUP_SEC = 10     # let the WS fill the in-memory dict before first snap


def main() -> None:
    client = DeltaClient(DELTA_BASE_URL, DELTA_API_KEY, DELTA_API_SECRET)
    conn = get_connection(DB_PATH)
    init_schema(conn)

    print("[1/5] Refreshing BTC option chain ...")
    n = refresh_chain(client, conn)
    print(f"      {n} live BTC options upserted.")

    print("[2/5] Selecting universe ...")
    mark = get_btc_mark_price(client)
    universe = select_universe(conn, mark, n_strikes=5, n_expiries=3)
    instrument_map = {u["symbol"]: u["id"] for u in universe}
    symbols = list(instrument_map.keys())
    print(f"      Mark = {mark:,.2f}, universe = {len(symbols)} symbols")

    channels = [
        {"name": "v2/ticker", "symbols": symbols},
        {"name": "funding_rate", "symbols": ["BTCUSDT"]},
    ]
    print(f"[3/5] Starting WS listener ...")
    listener = DeltaWSListener(DELTA_WS_URL, channels)
    listener.start()

    print(f"      Warming up for {WARMUP_SEC}s ...")
    time.sleep(WARMUP_SEC)
    initial = listener.snapshot_tickers()
    print(f"      In-memory tickers after warmup: {len(initial)} / {len(symbols)}")

    print(f"[4/5] Running for {DURATION_SEC}s, snapshot every {SNAPSHOT_INTERVAL_SEC}s ...")
    deadline = time.time() + DURATION_SEC
    next_snap = time.time() + SNAPSHOT_INTERVAL_SEC
    snap_no = 0
    try:
        while time.time() < deadline:
            if time.time() >= next_snap:
                ts = datetime.now(timezone.utc).isoformat()
                tickers = listener.snapshot_tickers()
                fundings = listener.snapshot_funding()
                wrote_q = 0
                for sym, msg in tickers.items():
                    iid = instrument_map.get(sym)
                    if iid is None:
                        continue
                    write_option_quote(conn, iid, ts, msg)
                    wrote_q += 1
                wrote_f = 0
                for sym, msg in fundings.items():
                    write_funding_snapshot(conn, sym, ts, msg)
                    wrote_f += 1
                conn.commit()
                snap_no += 1
                print(
                    f"  snap #{snap_no} @ {ts}: "
                    f"{wrote_q} option_quotes, {wrote_f} funding rows "
                    f"(ws msgs total: {listener.msg_count}, reconnects: {listener.reconnect_count})"
                )
                next_snap = time.time() + SNAPSHOT_INTERVAL_SEC
            time.sleep(1)
    finally:
        print("[5/5] Stopping listener ...")
        listener.stop()

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    total_q = conn.execute("SELECT COUNT(*) FROM option_quotes").fetchone()[0]
    total_f = conn.execute("SELECT COUNT(*) FROM funding_snapshots").fetchone()[0]
    print(f"option_quotes rows total: {total_q}")
    print(f"funding_snapshots rows total: {total_f}")

    print("\nPer-instrument row counts (this run):")
    rows = conn.execute(
        """
        SELECT i.symbol, COUNT(q.ts) AS n
        FROM instruments i
        LEFT JOIN option_quotes q ON q.instrument_id = i.id
        WHERE i.symbol IN ({})
        GROUP BY i.symbol
        ORDER BY n ASC, i.symbol
        """.format(",".join("?" * len(symbols))),
        symbols,
    ).fetchall()
    counts = [r[1] for r in rows]
    if counts:
        print(f"  min={min(counts)} max={max(counts)} avg={sum(counts)/len(counts):.1f}")
        missing = [r[0] for r in rows if r[1] == 0]
        if missing:
            print(f"  WARNING: {len(missing)} instruments have 0 rows: {missing[:5]}...")
        else:
            print(f"  All {len(rows)} instruments have at least 1 row.")

    atm_strike = min(
        {u["strike_price"] for u in universe},
        key=lambda s: abs(s - mark),
    )
    atm_sym = next(
        (u["symbol"] for u in universe
         if u["strike_price"] == atm_strike and u["contract_type"] == "call_options"),
        None,
    )
    if atm_sym:
        print(f"\nLatest sample row for ATM call ({atm_sym}):")
        cols = ["ts", "spot_price", "mark_price", "best_bid", "best_ask",
                "mark_iv", "delta", "gamma", "theta", "vega", "rho",
                "oi_contracts", "oi_value_usd"]
        row = conn.execute(
            f"""SELECT {','.join(cols)}
                FROM option_quotes q
                JOIN instruments i ON i.id = q.instrument_id
                WHERE i.symbol = ?
                ORDER BY q.ts DESC LIMIT 1""",
            (atm_sym,),
        ).fetchone()
        if row:
            for c, v in zip(cols, row):
                print(f"  {c:<14} = {v}")

    conn.close()


if __name__ == "__main__":
    main()
