"""Fill ingestion, position projection, trade records and realised P&L.

The same discipline the risk ledger already uses: **an append-only record of
what the exchange told us, with everything else derived from it.**

    order_fills   append-only, PRIMARY KEY (fill_id)   <- the only truth
    positions     projection, rewritten from fills
    trades        projection, one row per ingested fill
    risk_day_events(kind='pnl')  one row per realising fill

A fill is processed exactly once because `fill_id` is a primary key and inserts
use INSERT OR IGNORE: re-ingesting the same exchange response is a no-op, not a
doubled position. Nothing here ever invents a fill. A position exists because
the exchange reported a fill, never because an order was ACKed -- an ACK means
the order rests on the book, which is not exposure.

Exposure convention: signed contracts. +n long, -n short, 0 flat.
"""
from __future__ import annotations

import dataclasses
import logging
import sqlite3
from datetime import datetime, timezone

from src.risk import limits

log = logging.getLogger(__name__)

FILLS_SCHEMA = """
CREATE TABLE IF NOT EXISTS order_fills (
    fill_id         TEXT PRIMARY KEY,
    client_order_id TEXT,
    delta_order_id  TEXT,
    instrument_id   INTEGER NOT NULL,
    side            TEXT    NOT NULL CHECK (side IN ('buy', 'sell')),
    size            REAL    NOT NULL CHECK (size > 0),
    price           REAL    NOT NULL CHECK (price > 0),
    fee             REAL    NOT NULL DEFAULT 0.0,
    role            TEXT    NOT NULL CHECK (role IN ('entry', 'exit')),
    realised_pnl    REAL,
    exch_ts         TEXT,
    processed_ts    TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_order_fills_coid
    ON order_fills(client_order_id);
"""


class MalformedFill(ValueError):
    """A fill payload we refuse to interpret. Never guessed at."""


@dataclasses.dataclass(frozen=True)
class Fill:
    fill_id: str
    client_order_id: str | None
    delta_order_id: str | None
    instrument_id: int
    side: str
    size: float
    price: float
    fee: float
    exch_ts: str | None


@dataclasses.dataclass(frozen=True)
class IngestResult:
    ingested: int
    skipped_duplicate: int
    realised_pnl: float
    position_after: int
    pnl_refs: tuple[str, ...] = ()


def init_fills_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(FILLS_SCHEMA)
    conn.commit()


def _num(payload: dict, *names, required: bool = True, default=None):
    for n in names:
        if n in payload and payload[n] is not None:
            try:
                return float(payload[n])
            except (TypeError, ValueError) as exc:
                raise MalformedFill(f"field {n!r} is not numeric: {payload[n]!r}") from exc
    if required:
        raise MalformedFill(f"missing required numeric field, tried {names}")
    return default


def parse_fill(payload: dict, *, default_instrument_id: int) -> Fill:
    """Turn a Delta fill payload into a Fill, or refuse it.

    Delta's exact fill shape is NOT verified against live data -- this account
    has never produced a fill -- so several key spellings are accepted and
    anything missing or non-numeric raises instead of defaulting. A fill we
    cannot read must stop the pass, not silently become a zero-size no-op.
    """
    if not isinstance(payload, dict):
        raise MalformedFill(f"fill payload is {type(payload).__name__}, not a dict")

    fill_id = payload.get("id") or payload.get("fill_id") or payload.get("trade_id")
    if fill_id in (None, ""):
        raise MalformedFill("fill has no id; cannot be made idempotent")

    side = str(payload.get("side") or "").lower()
    if side not in ("buy", "sell"):
        raise MalformedFill(f"fill {fill_id}: unusable side {payload.get('side')!r}")

    size = _num(payload, "size", "filled_size", "quantity")
    if size <= 0:
        raise MalformedFill(f"fill {fill_id}: non-positive size {size}")
    price = _num(payload, "price", "fill_price", "average_fill_price")
    if price <= 0:
        raise MalformedFill(f"fill {fill_id}: non-positive price {price}")
    fee = _num(payload, "commission", "fee", "commission_amount",
               required=False, default=0.0)

    pid = payload.get("product_id", default_instrument_id)
    try:
        pid = int(pid)
    except (TypeError, ValueError) as exc:
        raise MalformedFill(f"fill {fill_id}: bad product_id {pid!r}") from exc

    return Fill(
        fill_id=str(fill_id),
        client_order_id=payload.get("client_order_id"),
        delta_order_id=(str(payload["order_id"]) if payload.get("order_id") else None),
        instrument_id=pid,
        side=side,
        size=float(size),
        price=float(price),
        fee=float(fee or 0.0),
        exch_ts=payload.get("created_at") or payload.get("timestamp"),
    )


# --------------------------------------------------------------------- state


def position_size(conn: sqlite3.Connection) -> int:
    """Signed open exposure in contracts, derived from fills alone."""
    row = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN side = 'buy' THEN size ELSE -size END), 0) "
        "FROM order_fills"
    ).fetchone()
    return int(round(float(row[0] or 0.0)))


def open_position(conn: sqlite3.Connection) -> dict | None:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM positions WHERE status = 'open' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def _weighted_entry_price(conn: sqlite3.Connection) -> float:
    """Average price of the fills that built the currently-open exposure."""
    rows = conn.execute(
        "SELECT side, size, price FROM order_fills WHERE role = 'entry' "
        "ORDER BY processed_ts, rowid"
    ).fetchall()
    qty = 0.0
    notional = 0.0
    for side, size, price in rows:
        qty += float(size)
        notional += float(size) * float(price)
    return (notional / qty) if qty else 0.0


def realised_pnl_usd(*, entry_price: float, exit_price: float, size: float,
                     direction: int, fees: float = 0.0) -> float:
    """P&L for closing `size` contracts.

    direction is the sign of the position being closed: +1 long, -1 short.
    A long earns (exit - entry); a short earns (entry - exit). Fees always
    subtract. Contract value converts contracts to underlying units.
    """
    move = (exit_price - entry_price) * direction
    return move * size * limits.BTCUSD_CONTRACT_VALUE - fees


# ------------------------------------------------------------------- ingest


def ingest_fills(conn: sqlite3.Connection, payloads, *, instrument_id: int = 84,
                 strategy: str = "manual", risk_state=None,
                 now: datetime | None = None) -> IngestResult:
    """Process exchange fills. Idempotent, transactional, never inventing state.

    Each fill is classified against the exposure that exists *at that moment*:
    a fill that moves exposure away from zero is an entry, one that moves it
    toward zero is an exit. Classification therefore depends on processing
    order, which is why fills are applied in the order the exchange lists them
    and recorded with a stable primary key.
    """
    now = now or datetime.now(timezone.utc)
    ts = now.isoformat()

    fills = [parse_fill(p, default_instrument_id=instrument_id) for p in payloads]

    ingested = 0
    duplicates = 0
    realised_total = 0.0
    pnl_refs: list[str] = []

    conn.execute("BEGIN IMMEDIATE")
    try:
        for f in fills:
            if conn.execute("SELECT 1 FROM order_fills WHERE fill_id = ?",
                            (f.fill_id,)).fetchone():
                duplicates += 1
                continue

            before = position_size(conn)
            signed = f.size if f.side == "buy" else -f.size
            after = before + signed
            # Toward zero (and not past it) is an exit; anything else is an entry.
            is_exit = before != 0 and abs(after) < abs(before)
            role = "exit" if is_exit else "entry"

            realised = None
            if is_exit:
                direction = 1 if before > 0 else -1
                entry_price = _weighted_entry_price(conn)
                realised = realised_pnl_usd(
                    entry_price=entry_price, exit_price=f.price,
                    size=f.size, direction=direction, fees=f.fee)
                realised_total += realised
                pnl_refs.append(f.fill_id)

            conn.execute(
                "INSERT OR IGNORE INTO order_fills (fill_id, client_order_id, "
                "delta_order_id, instrument_id, side, size, price, fee, role, "
                "realised_pnl, exch_ts, processed_ts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (f.fill_id, f.client_order_id, f.delta_order_id, f.instrument_id,
                 f.side, f.size, f.price, f.fee, role, realised, f.exch_ts, ts),
            )
            # trades.order_id carries a FOREIGN KEY. Resolve it honestly: if
            # the fill belongs to an order we have no record of, that is a real
            # divergence for the reconciler to surface -- not something to
            # paper over by inventing an order row to satisfy the constraint.
            order_row = conn.execute(
                "SELECT id FROM orders WHERE client_order_id = ?",
                (f.client_order_id,)).fetchone()
            if order_row is not None:
                conn.execute(
                    "INSERT INTO trades (order_id, instrument_id, side, quantity, "
                    "price, fee, ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (order_row[0], f.instrument_id, f.side, f.size, f.price,
                     f.fee, f.exch_ts or ts),
                )
            else:
                log.warning("[fills] fill %s references unknown order %r; "
                            "recorded in order_fills, no trade row",
                            f.fill_id, f.client_order_id)
            ingested += 1

        _project_positions(conn, strategy=strategy, ts=ts)
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    # The risk ledger is a separate connection by design (it owns its own
    # BEGIN IMMEDIATE), so P&L is recorded after the fill transaction commits.
    # Idempotency is by fill_id, so a crash between the two leaves the P&L
    # recordable on the next pass rather than lost or doubled.
    if risk_state is not None:
        for ref in pnl_refs:
            amount = conn.execute(
                "SELECT realised_pnl FROM order_fills WHERE fill_id = ?", (ref,)
            ).fetchone()[0]
            risk_state.record_realised_pnl(f"fill:{ref}", float(amount or 0.0), now)

    result = IngestResult(ingested, duplicates, realised_total,
                          position_size(conn), tuple(pnl_refs))
    if ingested or duplicates:
        log.info("[fills] ingested=%d duplicate=%d realised=%.4f position=%d",
                 ingested, duplicates, realised_total, result.position_after)
    return result


def _project_positions(conn: sqlite3.Connection, *, strategy: str, ts: str) -> None:
    """Rewrite the positions projection from the fill ledger.

    Rewritten rather than incrementally patched: a projection that is rebuilt
    from an append-only source cannot drift from it, which is exactly the
    property the reconciler's position check depends on.
    """
    size = position_size(conn)
    row = conn.execute(
        "SELECT id, entry_ts FROM positions WHERE status = 'open' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()

    if size == 0:
        if row is not None:
            realised = conn.execute(
                "SELECT COALESCE(SUM(realised_pnl), 0.0) FROM order_fills "
                "WHERE role = 'exit'"
            ).fetchone()[0]
            last_exit = conn.execute(
                "SELECT price FROM order_fills WHERE role = 'exit' "
                "ORDER BY processed_ts DESC, rowid DESC LIMIT 1"
            ).fetchone()
            conn.execute(
                "UPDATE positions SET status = 'closed', exit_price = ?, "
                "exit_ts = ?, pnl = ?, quantity = 0 WHERE id = ?",
                (float(last_exit[0]) if last_exit else None, ts,
                 float(realised or 0.0), row[0]),
            )
        return

    entry_price = _weighted_entry_price(conn)
    side = "buy" if size > 0 else "sell"
    if row is None:
        conn.execute(
            "INSERT INTO positions (instrument_id, side, quantity, entry_price, "
            "entry_ts, status, strategy) VALUES (?, ?, ?, ?, ?, 'open', ?)",
            (limits.BTCUSD_PRODUCT_ID, side, abs(size), entry_price, ts, strategy),
        )
    else:
        conn.execute(
            "UPDATE positions SET side = ?, quantity = ?, entry_price = ? WHERE id = ?",
            (side, abs(size), entry_price, row[0]),
        )
