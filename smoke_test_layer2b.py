"""Layer 2b smoke test.

What it does:
  1. Refreshes the BTC option chain from Delta REST (in case daily-expiry rows
     are stale from yesterday's Layer 1 run).
  2. Pulls current BTC perpetual mark price.
  3. Selects the universe: ATM +/- 5 strikes x next 3 expiries x {call, put}.
  4. Bulk-fetches Greeks/IV snapshot for the BTC option chain.
  5. Prints the universe grouped by expiry, with the ATM call's mark/IV/delta.

Run from the project root:
    python smoke_test_layer2b.py

Done condition (Layer 2b):
  Console shows ~3 expiries, each with ~11 strikes x 2 sides, ATM = strike
  closest to mark, and Greeks visible for the ATM call.
"""
from collections import defaultdict

from config.settings import (
    DB_PATH,
    DELTA_API_KEY,
    DELTA_API_SECRET,
    DELTA_BASE_URL,
)
from src.data.storage import get_connection, init_schema
from src.exchange.delta_client import DeltaClient
from src.exchange.instruments import (
    fetch_chain_quotes,
    get_btc_mark_price,
    refresh_chain,
    select_universe,
)


def main() -> None:
    client = DeltaClient(DELTA_BASE_URL, DELTA_API_KEY, DELTA_API_SECRET)
    conn = get_connection(DB_PATH)
    init_schema(conn)
    try:
        print("[1/4] Refreshing BTC option chain from REST ...")
        n = refresh_chain(client, conn)
        print(f"      {n} live BTC options upserted to instruments table.")

        print("[2/4] Fetching BTC perpetual mark price ...")
        mark = get_btc_mark_price(client)
        print(f"      BTC mark = {mark:,.2f}")

        print("[3/4] Selecting universe (ATM +/- 5 strikes x next 3 expiries) ...")
        universe = select_universe(conn, mark, n_strikes=5, n_expiries=3)
        print(f"      {len(universe)} instruments selected.")

        print("[4/4] Snapshotting Greeks/IV for the BTC option chain ...")
        quotes = fetch_chain_quotes(client)
        print(f"      Got tickers for {len(quotes)} BTC options.\n")
    finally:
        conn.close()

    by_expiry: dict[str, list[dict]] = defaultdict(list)
    for inst in universe:
        by_expiry[inst["settlement_time"]].append(inst)

    print("=" * 70)
    for exp in sorted(by_expiry.keys()):
        insts = by_expiry[exp]
        strikes = sorted({i["strike_price"] for i in insts})
        atm_strike = min(strikes, key=lambda s: abs(s - mark))
        n_calls = sum(1 for i in insts if i["contract_type"] == "call_options")
        n_puts = sum(1 for i in insts if i["contract_type"] == "put_options")

        print(f"\nExpiry {exp}")
        print(
            f"  Strikes: {strikes[0]:,.0f} -> {strikes[-1]:,.0f}  "
            f"({len(strikes)} total, ATM = {atm_strike:,.0f})"
        )
        print(f"  {n_calls} calls + {n_puts} puts = {len(insts)} instruments")

        atm_call = next(
            (i for i in insts
             if i["strike_price"] == atm_strike and i["contract_type"] == "call_options"),
            None,
        )
        if atm_call:
            q = quotes.get(atm_call["symbol"], {})
            greeks = q.get("greeks") or {}
            print(
                f"  ATM call ({atm_call['symbol']}):  "
                f"mark={q.get('mark_price','-')}  "
                f"iv={greeks.get('iv', q.get('iv', '-'))}  "
                f"delta={greeks.get('delta','-')}  "
                f"oi={q.get('oi', q.get('open_interest', '-'))}"
            )

    print("\n" + "=" * 70)
    print(f"Universe size: {len(universe)} instruments. Ready for Layer 2c subscription.")


if __name__ == "__main__":
    main()
