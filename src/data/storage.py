"""SQLite schema + write helpers for the BTC paper bot.

Tables: instruments, candles, oi_snapshots, positions, orders, trades.
Each row stores ISO-8601 UTC timestamps as TEXT to keep things portable.
"""
import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS instruments (
    id              INTEGER PRIMARY KEY,
    symbol          TEXT UNIQUE NOT NULL,
    contract_type   TEXT NOT NULL,
    underlying      TEXT,
    quoting         TEXT,
    strike_price    REAL,
    settlement_time TEXT,
    tick_size       REAL,
    contract_value  REAL,
    state           TEXT,
    raw_json        TEXT,
    fetched_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_instruments_underlying_type
    ON instruments(underlying, contract_type);

CREATE TABLE IF NOT EXISTS candles (
    instrument_id INTEGER NOT NULL,
    timeframe     TEXT    NOT NULL,
    ts            TEXT    NOT NULL,
    open          REAL    NOT NULL,
    high          REAL    NOT NULL,
    low           REAL    NOT NULL,
    close         REAL    NOT NULL,
    volume        REAL,
    PRIMARY KEY (instrument_id, timeframe, ts),
    FOREIGN KEY (instrument_id) REFERENCES instruments(id)
);

CREATE TABLE IF NOT EXISTS oi_snapshots (
    instrument_id INTEGER NOT NULL,
    ts            TEXT    NOT NULL,
    open_interest REAL,
    PRIMARY KEY (instrument_id, ts),
    FOREIGN KEY (instrument_id) REFERENCES instruments(id)
);

CREATE TABLE IF NOT EXISTS positions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument_id INTEGER NOT NULL,
    side          TEXT    NOT NULL,
    quantity      REAL    NOT NULL,
    entry_price   REAL    NOT NULL,
    entry_ts      TEXT    NOT NULL,
    exit_price    REAL,
    exit_ts       TEXT,
    pnl           REAL,
    status        TEXT    NOT NULL,
    strategy      TEXT,
    FOREIGN KEY (instrument_id) REFERENCES instruments(id)
);

CREATE TABLE IF NOT EXISTS orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    client_order_id TEXT,
    delta_order_id  TEXT,
    instrument_id   INTEGER NOT NULL,
    side            TEXT    NOT NULL,
    order_type      TEXT    NOT NULL,
    quantity        REAL    NOT NULL,
    price           REAL,
    status          TEXT    NOT NULL,
    strategy        TEXT,
    placed_ts       TEXT    NOT NULL,
    filled_ts       TEXT,
    FOREIGN KEY (instrument_id) REFERENCES instruments(id)
);

CREATE TABLE IF NOT EXISTS trades (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id      INTEGER NOT NULL,
    instrument_id INTEGER NOT NULL,
    side          TEXT    NOT NULL,
    quantity      REAL    NOT NULL,
    price         REAL    NOT NULL,
    fee           REAL,
    ts            TEXT    NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(id),
    FOREIGN KEY (instrument_id) REFERENCES instruments(id)
);

CREATE TABLE IF NOT EXISTS option_quotes (
    instrument_id INTEGER NOT NULL,
    ts            TEXT    NOT NULL,
    exch_ts       INTEGER,
    spot_price    REAL,
    mark_price    REAL,
    close         REAL,
    best_bid      REAL,
    best_ask      REAL,
    bid_qty       REAL,
    ask_qty       REAL,
    mark_iv       REAL,
    bid_iv        REAL,
    ask_iv        REAL,
    delta         REAL,
    gamma         REAL,
    theta         REAL,
    vega          REAL,
    rho           REAL,
    oi            REAL,
    oi_contracts  REAL,
    oi_value_usd  REAL,
    volume_24h    REAL,
    PRIMARY KEY (instrument_id, ts),
    FOREIGN KEY (instrument_id) REFERENCES instruments(id)
);

CREATE TABLE IF NOT EXISTS funding_snapshots (
    symbol                    TEXT    NOT NULL,
    ts                        TEXT    NOT NULL,
    funding_rate              REAL,
    funding_rate_8h           REAL,
    predicted_funding_rate    REAL,
    predicted_funding_rate_8h REAL,
    next_funding_realization  INTEGER,
    funding_interval          INTEGER,
    PRIMARY KEY (symbol, ts)
);

CREATE TABLE IF NOT EXISTS instrument_map_log (
    seen_date TEXT    NOT NULL,
    id        INTEGER NOT NULL,
    symbol    TEXT    NOT NULL,
    PRIMARY KEY (seen_date, id)
);
"""


def _to_float(x) -> float | None:
    if x is None:
        return None
    try:
        return float(x)
    except (ValueError, TypeError):
        return None


def _to_int(x) -> int | None:
    """Coerce to int, tolerating native ints and numeric strings.

    Delta sends the option-ticker `timestamp` as a native int (microseconds
    since epoch), but other numeric fields arrive as strings; handle both so
    a future payload-shape change does not silently drop exch_ts.
    """
    if x is None:
        return None
    try:
        return int(float(x))
    except (ValueError, TypeError):
        return None


def write_option_quote(
    conn: sqlite3.Connection,
    instrument_id: int,
    ts: str,
    ticker_msg: dict,
) -> None:
    """Insert one option_quotes row from a v2/ticker WS payload.

    All numeric fields arrive as strings from Delta — coerce here.
    """
    quotes = ticker_msg.get("quotes") or {}
    greeks = ticker_msg.get("greeks") or {}
    conn.execute(
        """
        INSERT OR REPLACE INTO option_quotes
        (instrument_id, ts, exch_ts, spot_price, mark_price, close,
         best_bid, best_ask, bid_qty, ask_qty,
         mark_iv, bid_iv, ask_iv,
         delta, gamma, theta, vega, rho,
         oi, oi_contracts, oi_value_usd,
         volume_24h)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            instrument_id, ts,
            _to_int(ticker_msg.get("timestamp")),
            _to_float(ticker_msg.get("spot_price")),
            _to_float(ticker_msg.get("mark_price")),
            _to_float(ticker_msg.get("close")),
            _to_float(quotes.get("best_bid")),
            _to_float(quotes.get("best_ask")),
            _to_float(quotes.get("bid_size")),
            _to_float(quotes.get("ask_size")),
            _to_float(quotes.get("mark_iv")),
            _to_float(quotes.get("bid_iv")),
            _to_float(quotes.get("ask_iv")),
            _to_float(greeks.get("delta")),
            _to_float(greeks.get("gamma")),
            _to_float(greeks.get("theta")),
            _to_float(greeks.get("vega")),
            _to_float(greeks.get("rho")),
            _to_float(ticker_msg.get("oi")),
            _to_float(ticker_msg.get("oi_contracts")),
            _to_float(ticker_msg.get("oi_value_usd")),
            _to_float(ticker_msg.get("volume")),
        ),
    )


def write_funding_snapshot(
    conn: sqlite3.Connection,
    symbol: str,
    ts: str,
    funding_msg: dict,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO funding_snapshots
        (symbol, ts, funding_rate, funding_rate_8h,
         predicted_funding_rate, predicted_funding_rate_8h,
         next_funding_realization, funding_interval)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            symbol, ts,
            _to_float(funding_msg.get("funding_rate")),
            _to_float(funding_msg.get("funding_rate_8h")),
            _to_float(funding_msg.get("predicted_funding_rate")),
            _to_float(funding_msg.get("predicted_funding_rate_8h")),
            funding_msg.get("next_funding_realization"),
            funding_msg.get("funding_interval"),
        ),
    )


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def upsert_instrument(conn: sqlite3.Connection, product: dict, fetched_at: str) -> None:
    """Insert or replace an instrument row from a Delta /v2/products payload."""
    underlying = (product.get("underlying_asset") or {}).get("symbol")
    quoting = (product.get("quoting_asset") or {}).get("symbol")
    strike = product.get("strike_price")
    conn.execute(
        """
        INSERT OR REPLACE INTO instruments
        (id, symbol, contract_type, underlying, quoting, strike_price,
         settlement_time, tick_size, contract_value, state, raw_json, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            product.get("id"),
            product.get("symbol"),
            product.get("contract_type"),
            underlying,
            quoting,
            float(strike) if strike is not None else None,
            product.get("settlement_time"),
            product.get("tick_size"),
            product.get("contract_value"),
            product.get("state"),
            json.dumps(product),
            fetched_at,
        ),
    )
