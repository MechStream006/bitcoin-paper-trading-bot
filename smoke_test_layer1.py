"""Layer 1 smoke test.

What it does:
  1. Pulls live BTC option products from Delta testnet (REST, public endpoint).
  2. Writes them into data/btc_trading.db (SQLite).
  3. Sends a one-line confirmation to the Telegram channel.

Run from the project root:
    python smoke_test_layer1.py

Done condition (per CLAUDE.md Layer 1):
  - Option chain refresh writes rows to SQLite.
  - Telegram "hello" verifies the alert pipe.
"""
from datetime import datetime, timezone

from config.settings import (
    DB_PATH,
    DELTA_API_KEY,
    DELTA_API_SECRET,
    DELTA_BASE_URL,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHANNEL_ID,
)
from src.alerts.telegram import send_message
from src.data.storage import get_connection, init_schema, upsert_instrument
from src.exchange.delta_client import DeltaClient


def main() -> None:
    print(f"[1/3] Fetching BTC option products from {DELTA_BASE_URL} ...")
    client = DeltaClient(DELTA_BASE_URL, DELTA_API_KEY, DELTA_API_SECRET)
    resp = client.get_products(contract_types="call_options,put_options", states="live")
    products = resp.get("result", []) if isinstance(resp, dict) else []
    btc_options = [
        p for p in products
        if (p.get("underlying_asset") or {}).get("symbol") == "BTC"
    ]
    print(f"      Found {len(btc_options)} live BTC option products "
          f"(out of {len(products)} total options returned).")

    print(f"[2/3] Writing to SQLite at {DB_PATH} ...")
    fetched_at = datetime.now(timezone.utc).isoformat()
    conn = get_connection(DB_PATH)
    try:
        init_schema(conn)
        for p in btc_options:
            upsert_instrument(conn, p, fetched_at)
        conn.commit()
        row_count = conn.execute("SELECT COUNT(*) FROM instruments").fetchone()[0]
    finally:
        conn.close()
    print(f"      instruments table now has {row_count} rows.")

    print("[3/3] Sending Telegram confirmation ...")
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHANNEL_ID):
        print("      SKIPPED: TELEGRAM_BOT_TOKEN / TELEGRAM_CHANNEL_ID not set in .env")
        return
    msg = (
        "BTC bot — Layer 1 smoke test OK\n"
        f"Stored {len(btc_options)} BTC option instruments in SQLite.\n"
        f"Time: {fetched_at}"
    )
    send_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, msg)
    print("      Telegram alert sent.")


if __name__ == "__main__":
    main()
