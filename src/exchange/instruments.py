"""BTC option universe selection.

Picks ATM ± n_strikes around current BTC mark for the next n_expiries.
Reads the option chain from SQLite (kept fresh by `refresh_chain()`).

Used by Layer 2c to know which symbols to subscribe to over the WS feed.
"""
import sqlite3
from datetime import datetime, timezone

from src.data.storage import upsert_instrument
from src.exchange.delta_client import DeltaClient


def refresh_chain(client: DeltaClient, conn: sqlite3.Connection) -> int:
    """Re-fetch live BTC options from REST and upsert to instruments table.

    Returns the number of BTC option products written.
    """
    resp = client.get_products(contract_types="call_options,put_options", states="live")
    products = resp.get("result") if isinstance(resp, dict) else None
    if not isinstance(products, list):
        raise ValueError(
            f"refresh_chain: unexpected response from {client.base_url} "
            f"(result={products!r})"
        )
    btc_options = [
        p for p in products
        if (p.get("underlying_asset") or {}).get("symbol") == "BTC"
    ]
    fetched_at = datetime.now(timezone.utc).isoformat()
    for p in btc_options:
        upsert_instrument(conn, p, fetched_at)
    conn.commit()
    return len(btc_options)


def get_btc_mark_price(client: DeltaClient) -> float:
    """Current BTC perpetual mark price (testnet symbol BTCUSDT — see CLAUDE.md §11)."""
    resp = client.get_ticker("BTCUSDT")
    result = resp.get("result") if isinstance(resp, dict) else None
    if not isinstance(result, dict):
        raise ValueError(
            f"get_btc_mark_price: unexpected response from {client.base_url} "
            f"(result={result!r})"
        )
    return float(result["mark_price"])


def select_universe(
    conn: sqlite3.Connection,
    mark_price: float,
    n_strikes: int = 5,
    n_expiries: int = 3,
) -> list[dict]:
    """Pick ATM ± n_strikes for each of the next n_expiries.

    `n_strikes=5` means ATM plus 5 above plus 5 below = 11 strikes per expiry.
    Returns list of dicts: id, symbol, contract_type, strike_price, settlement_time, atm_distance.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """
        SELECT id, symbol, contract_type, strike_price, settlement_time
        FROM instruments
        WHERE underlying = 'BTC'
          AND contract_type IN ('call_options', 'put_options')
          AND state = 'live'
          AND settlement_time > ?
        ORDER BY settlement_time ASC, strike_price ASC
        """,
        (now_iso,),
    )
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, row)) for row in cur.fetchall()]

    by_expiry: dict[str, list[dict]] = {}
    for r in rows:
        by_expiry.setdefault(r["settlement_time"], []).append(r)

    selected_expiries = sorted(by_expiry.keys())[:n_expiries]

    universe: list[dict] = []
    for exp in selected_expiries:
        by_strike: dict[float, list[dict]] = {}
        for r in by_expiry[exp]:
            by_strike.setdefault(r["strike_price"], []).append(r)

        strikes = sorted(by_strike.keys())
        atm_idx = min(range(len(strikes)), key=lambda i: abs(strikes[i] - mark_price))
        lo = max(0, atm_idx - n_strikes)
        hi = min(len(strikes), atm_idx + n_strikes + 1)

        for strike in strikes[lo:hi]:
            for inst in by_strike[strike]:
                inst["atm_distance"] = strike - mark_price
                universe.append(inst)

    return universe


def fetch_chain_quotes(client: DeltaClient) -> dict[str, dict]:
    """Bulk ticker fetch for all live BTC options (Greeks + IV + OI).
    Returns dict keyed by symbol.
    """
    resp = client.get_tickers_bulk(
        contract_types="call_options,put_options",
        underlying_asset_symbols="BTC",
    )
    result = resp.get("result") if isinstance(resp, dict) else None
    if not isinstance(result, list):
        raise ValueError(
            f"fetch_chain_quotes: unexpected response from {client.base_url} "
            f"(result={result!r})"
        )
    return {t.get("symbol"): t for t in result if t.get("symbol")}
