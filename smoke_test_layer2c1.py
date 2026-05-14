"""Layer 2c.1 smoke test - WebSocket schema discovery.

What it does:
  1. Refreshes BTC option chain.
  2. Picks the universe (ATM +/- 5 strikes x 3 expiries = ~64 instruments).
  3. Subscribes via WS to:
       - v2/ticker for the universe (Greeks + IV + OI + mark)
       - mark_price for BTCUSDT perp
       - funding_rate for BTCUSDT perp
       - l1_orderbook for the universe
  4. Logs every raw message to data/logs/ws_raw.jsonl for DURATION_SEC.
  5. Prints a summary by message type: count, observed fields, one sample.

Goal: see the actual field names (especially the IV field) so we can design
the option_quotes table correctly in 2c.2 instead of guessing.

Run from project root:
    python smoke_test_layer2c1.py
"""
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from config.settings import (
    DB_PATH,
    DELTA_API_KEY,
    DELTA_API_SECRET,
    DELTA_BASE_URL,
    DELTA_WS_URL,
)
from src.data.storage import get_connection, init_schema
from src.exchange.delta_client import DeltaClient
from src.exchange.delta_ws import stream
from src.exchange.instruments import (
    get_btc_mark_price,
    refresh_chain,
    select_universe,
)

DURATION_SEC = 120.0
LOG_PATH = Path("data/logs/ws_raw.jsonl")


def main() -> None:
    client = DeltaClient(DELTA_BASE_URL, DELTA_API_KEY, DELTA_API_SECRET)
    conn = get_connection(DB_PATH)
    init_schema(conn)
    try:
        print("[1/4] Refreshing BTC option chain ...")
        n = refresh_chain(client, conn)
        print(f"      {n} live BTC options upserted.")

        print("[2/4] Selecting universe ...")
        mark = get_btc_mark_price(client)
        universe = select_universe(conn, mark, n_strikes=5, n_expiries=3)
        symbols = [u["symbol"] for u in universe]
        print(f"      Mark = {mark:,.2f}, universe = {len(symbols)} symbols")
    finally:
        conn.close()

    channels = [
        {"name": "v2/ticker", "symbols": symbols},
        {"name": "mark_price", "symbols": ["BTCUSDT"]},
        {"name": "funding_rate", "symbols": ["BTCUSDT"]},
        {"name": "l1_orderbook", "symbols": symbols},
    ]

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log_file = LOG_PATH.open("w", encoding="utf-8")

    type_counts: Counter = Counter()
    type_fields: dict[str, set[str]] = defaultdict(set)
    type_samples: dict[str, dict] = {}

    def on_message(msg: object) -> None:
        if isinstance(msg, dict):
            t = str(msg.get("type", "?"))
            type_counts[t] += 1
            type_fields[t].update(msg.keys())
            if t not in type_samples:
                type_samples[t] = msg
            log_file.write(json.dumps({
                "recv_at": datetime.now(timezone.utc).isoformat(),
                "msg": msg,
            }))
            log_file.write("\n")
        else:
            type_counts["__non_json__"] += 1
            log_file.write(json.dumps({
                "recv_at": datetime.now(timezone.utc).isoformat(),
                "raw": str(msg)[:500],
            }))
            log_file.write("\n")

    print(f"[3/4] Subscribing and streaming for {DURATION_SEC:.0f}s ...")
    print(f"      Channels: v2/ticker ({len(symbols)} syms), mark_price, "
          f"funding_rate, l1_orderbook ({len(symbols)} syms)")
    total = stream(DELTA_WS_URL, channels, on_message, duration_sec=DURATION_SEC)
    log_file.close()
    print(f"      Logged {total} messages to {LOG_PATH}")

    print("\n[4/4] Summary by message type")
    print("=" * 70)
    if not type_counts:
        print("  (no messages received - check subscription)")
        return
    for t, c in sorted(type_counts.items(), key=lambda x: -x[1]):
        print(f"\n  type={t!r}  count={c}")
        fields = sorted(type_fields.get(t, set()))
        print(f"    fields: {fields}")
        sample = type_samples.get(t)
        if sample is not None:
            sample_str = json.dumps(sample, default=str)
            if len(sample_str) > 350:
                sample_str = sample_str[:350] + "..."
            print(f"    sample: {sample_str}")


if __name__ == "__main__":
    main()
