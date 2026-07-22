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

-- ENG-017. The PK is (instrument_id, ts), so `ts` is a NON-LEADING index
-- column: every time-ranged query degrades to a full covering-index scan,
-- O(rows). That includes healthcheck.py's `SELECT max(ts)` dead-man probe,
-- which runs every 10 minutes and on every /api/v1/runtime/health call.
-- Measured at 2.85M rows (30 days at production cadence): 512 ms -> 0.01 ms.
-- Write cost of maintaining it: +0.26 ms on a 60 s cycle (+8%), i.e. 0.006%
-- duty cycle. Storage cost: ~34 bytes/row (~12%).
CREATE INDEX IF NOT EXISTS idx_option_quotes_ts
    ON option_quotes(ts);

-- DORMANT / NOT-YET-WIRED (see CLAUDE.md "Dormant: funding_snapshots not wired").
-- The live runtime subscribes only v2/ticker over options; no funding_rate
-- channel is subscribed, so this table is intentionally empty at Layer 2d.
-- The write path (write_funding_snapshot) is exercised by tests only.
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

-- One row per certified day. Written by scripts/certify_day.py.
-- Append-only: INSERT OR REPLACE on re-run (run_ts records the latest run).
-- Research store reads verdict + platform to gate evidence admissibility.
-- GREEN = all Tier-A checks pass.  AMBER = requires adjudication.  RED = non-admissible.
CREATE TABLE IF NOT EXISTS daily_certifier_log (
    day               TEXT NOT NULL PRIMARY KEY,  -- YYYY-MM-DD UTC
    run_ts            TEXT NOT NULL,              -- ISO-8601 UTC, certifier run time
    verdict           TEXT NOT NULL,              -- GREEN / AMBER / RED
    platform          TEXT,                       -- testnet / mainnet
    certifier_version TEXT,                       -- git SHA
    checks_json       TEXT NOT NULL               -- JSON: {check_id: {result, value, threshold, note, provisional}}
);

-- One row per SnapshotWriter cycle. Persists per-cycle collection quality so
-- coverage is auditable retrospectively from the DB alone (journald rotates).
-- coverage_pct is a fraction in [0.0, 1.0]. wrote/skipped/failed are option_quote
-- counts (funding is a single symbol, excluded from coverage accounting).
--
-- coverage_pct measures "ever-ticked-and-still-in-universe", NOT freshness, so it
-- stays 1.0 during a ticker stall. stale_count is the freshness counterpart:
-- universe symbols with no tick within STALE_THRESHOLD_SEC at sample time. The
-- pair disambiguates: coverage_pct<1 => some symbol never ticked (new/dead);
-- coverage_pct=1 AND stale_count>0 => symbols ticked once but are now stale.
CREATE TABLE IF NOT EXISTS collection_metrics (
    ts              TEXT    NOT NULL PRIMARY KEY,
    universe_size   INTEGER,
    coverage_pct    REAL,
    wrote           INTEGER,
    skipped         INTEGER,
    failed          INTEGER,
    reconcile_count INTEGER,
    stale_count     INTEGER
);
"""

# exch_ts sanity band (microseconds since epoch).
#   1e15 µs  = 2001-09-09   — anything below is almost certainly milliseconds
#              (the documented Binance ms/µs failure mode) or otherwise corrupt.
#   1e16 µs  = 2286-11-20   — anything at/above is likely nanoseconds or corrupt.
# Live BTC option timestamps sit around 1.78e15 (year 2026), well inside the band.
EXCH_TS_MIN = 1_000_000_000_000_000
EXCH_TS_MAX = 10_000_000_000_000_000


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


def coerce_exch_ts(raw) -> int | None:
    """Coerce a raw `timestamp` field to a band-validated exch_ts.

    Returns the integer microsecond timestamp when it parses AND falls inside
    the sane band [EXCH_TS_MIN, EXCH_TS_MAX). Returns None when the value is
    absent, unparseable, or out of band — an out-of-band value (e.g. Delta
    silently switching to milliseconds, as Binance once did) is treated as
    "no valid exchange timestamp" rather than stored as a wrong-magnitude
    number that would silently poison the wall_ts - exch_ts staleness proxy.

    Pure function — no logging. Callers that want to alert on rejections
    (e.g. SnapshotWriter) re-check with this helper and emit an aggregated
    warning, so a systematic format shift does not flood the log per-row.
    """
    v = _to_int(raw)
    if v is None:
        return None
    if v < EXCH_TS_MIN or v >= EXCH_TS_MAX:
        return None
    return v


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
            coerce_exch_ts(ticker_msg.get("timestamp")),
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


def write_collection_metrics(
    conn: sqlite3.Connection,
    ts: str,
    universe_size: int,
    coverage_pct: float,
    wrote: int,
    skipped: int,
    failed: int,
    reconcile_count: int,
    stale_count: int,
) -> None:
    """Insert one collection_metrics row for a SnapshotWriter cycle.

    Keyed on ts (one cycle = one batch timestamp). INSERT OR REPLACE so a
    retried cycle with the same ts overwrites rather than errors.
    """
    conn.execute(
        """
        INSERT OR REPLACE INTO collection_metrics
        (ts, universe_size, coverage_pct, wrote, skipped, failed,
         reconcile_count, stale_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (ts, universe_size, coverage_pct, wrote, skipped, failed,
         reconcile_count, stale_count),
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
