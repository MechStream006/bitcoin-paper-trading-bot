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


def _cycle_fills(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """The fills of the current cycle: since exposure was last flat.

    A cycle is one round trip. Exposure returning to zero ends it, and the
    fills before that point belong to a completed trade whose entry price and
    entry fees must not leak into the next one. Reading every `role='entry'`
    row ever written does exactly that leaking: after one short at 72797 is
    covered, a fresh short at 80000 averages to 76398.5, and a +1.00 winner is
    recorded as a -2.60 loss.

    Returns the still-open segment when exposure is non-zero, and the segment
    that just closed when it is -- so a position can be finalised with the P&L
    of its own round trip rather than of every round trip so far.
    """
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT fill_id, side, size, price, fee, role, realised_pnl "
        "FROM order_fills ORDER BY processed_ts, rowid"
    ).fetchall()

    flat_at: list[int] = []
    running = 0.0
    for i, r in enumerate(rows):
        running += float(r["size"]) if r["side"] == "buy" else -float(r["size"])
        if abs(running) < 1e-9:
            flat_at.append(i)

    if not flat_at:
        return list(rows)
    if flat_at[-1] == len(rows) - 1:
        previous = flat_at[-2] if len(flat_at) >= 2 else -1
        return list(rows[previous + 1:])
    return list(rows[flat_at[-1] + 1:])


def _weighted_entry_price(conn: sqlite3.Connection) -> float:
    """Average price of the fills that built the currently-open exposure."""
    qty = 0.0
    notional = 0.0
    for r in _cycle_fills(conn):
        if r["role"] != "entry":
            continue
        qty += float(r["size"])
        notional += float(r["size"]) * float(r["price"])
    return (notional / qty) if qty else 0.0


def _entry_fee_share(conn: sqlite3.Connection, closing_size: float) -> float:
    """The entry commission attributable to closing `closing_size` contracts.

    The exchange charges on both legs. The entry leg is charged when the
    position is opened and is already recorded on the entry fill, but it is
    only *realised* when the position is closed, so it belongs to the closing
    fill's P&L. Attributed pro rata by size so a partial exit carries its own
    share and a sequence of partial exits sums to the whole fee, never more.

    These are the fees Delta actually charged, read back from the fill ledger
    -- not `round_trip_fees_usd`, which is the pre-trade ESTIMATE the risk
    budget is sized against. Using the estimate here would put a prediction in
    the realised ledger, and would double-count the model.
    """
    entry_fee = 0.0
    entry_qty = 0.0
    for r in _cycle_fills(conn):
        if r["role"] != "entry":
            continue
        entry_fee += float(r["fee"] or 0.0)
        entry_qty += float(r["size"])
    if entry_qty <= 0:
        return 0.0
    return entry_fee * (float(closing_size) / entry_qty)


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
                # BOTH legs. Passing only f.fee recorded Gate 5C.8's round trip
                # as +0.05183619 when the wallet moved +0.00888596 -- overstated
                # by exactly the entry commission, which makes a loss look
                # smaller than it was and MAX_DAILY_LOSS_USD correspondingly
                # permissive. One subtraction, inside realised_pnl_usd, so the
                # fee model is not reimplemented here.
                fees = f.fee + _entry_fee_share(conn, f.size)
                realised = realised_pnl_usd(
                    entry_price=entry_price, exit_price=f.price,
                    size=f.size, direction=direction, fees=fees)
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
            order_id, why = _resolve_order(conn, f)
            if order_id is not None:
                conn.execute(
                    "INSERT INTO trades (order_id, instrument_id, side, quantity, "
                    "price, fee, ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (order_id, f.instrument_id, f.side, f.size, f.price,
                     f.fee, f.exch_ts or ts),
                )
            else:
                log.warning("[fills] fill %s not linked to an order (%s); "
                            "recorded in order_fills, no trade row",
                            f.fill_id, why)
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


def _resolve_order(conn: sqlite3.Connection, f: Fill) -> tuple[int | None, str]:
    """Local order row for a fill, by the exchange's own identifier.

    Delta's `/v2/fills` does not echo `client_order_id` -- it is null on every
    fill, even for orders submitted with one (`/v2/orders` does return it).
    Keying the join on it therefore matched nothing, and Gate 5C.8's complete
    round trip produced no trade rows at all. `order_id` IS present on the
    fill, and the executor already stores it as `orders.delta_order_id`, so the
    authoritative link was there the whole time.

    When the native id is present it is the ONLY key consulted: an unmatched
    one means the exchange filled an order this database has no record of,
    which is a real divergence and must fail closed rather than fall through to
    a weaker key and guess. The client_order_id fallback exists for the one
    case where the native id is genuinely unavailable -- a submission that
    ended UNKNOWN, whose order row was written before any exchange id came
    back. Returns (order_id, reason) with reason set only on failure.
    """
    if f.delta_order_id:
        rows = conn.execute(
            "SELECT id FROM orders WHERE delta_order_id = ?",
            (f.delta_order_id,)).fetchall()
        if len(rows) == 1:
            return int(rows[0][0]), ""
        if not rows:
            return None, "no local order with delta_order_id=%r" % (f.delta_order_id,)
        return None, ("delta_order_id=%r matches %d local orders; ambiguous"
                      % (f.delta_order_id, len(rows)))

    if f.client_order_id:
        rows = conn.execute(
            "SELECT id FROM orders WHERE client_order_id = ?",
            (f.client_order_id,)).fetchall()
        if len(rows) == 1:
            return int(rows[0][0]), ""
        return None, ("fallback client_order_id=%r matched %d local orders"
                      % (f.client_order_id, len(rows)))

    return None, "fill carries neither delta_order_id nor client_order_id"


def _cycle_strategy(conn: sqlite3.Connection, *, default: str) -> str:
    """The strategy that actually opened this cycle, from its entry order.

    ingest_fills() defaults to "manual" and the reconciler -- the only caller in
    the runtime -- does not override it, so every reconciled position was
    projected as manual regardless of what placed it. The fill knows its
    delta_order_id and the order knows its strategy, so the attribution is a
    lookup, not a guess.

    Falls back to `default` when there is no entry fill, no linked order, or the
    order carries no strategy: a genuinely operator-placed trade stays manual
    rather than being relabelled.
    """
    entry_ids = [r["fill_id"] for r in _cycle_fills(conn) if r["role"] == "entry"]
    if not entry_ids:
        return default
    # Scoped to THIS cycle's entry fills. _cycle_fills does not select
    # delta_order_id (it exists for P&L), so the join is done here rather than
    # by widening a query the realised-P&L path depends on.
    marks = ",".join("?" for _ in entry_ids)
    row = conn.execute(
        f"SELECT o.strategy FROM order_fills f "
        f"JOIN orders o ON o.delta_order_id = f.delta_order_id "
        f"WHERE f.fill_id IN ({marks}) AND o.strategy IS NOT NULL "
        f"ORDER BY f.processed_ts LIMIT 1",
        entry_ids,
    ).fetchone()
    return str(row[0]) if row and row[0] else default


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
            # This position's own round trip, not every round trip ever
            # closed -- summing across cycles made each closed position inherit
            # its predecessors' P&L.
            cycle = _cycle_fills(conn)
            exits = [r for r in cycle if r["role"] == "exit"]
            realised = sum(float(r["realised_pnl"] or 0.0) for r in exits)
            last_exit = exits[-1]["price"] if exits else None
            # quantity is NOT zeroed. Openness is decided by status alone --
            # nothing filters on quantity -- so zeroing it only destroyed the
            # one number a closed row exists to record: how much was traded.
            # order_fills remains authoritative; this keeps the projection
            # from contradicting it.
            conn.execute(
                "UPDATE positions SET status = 'closed', exit_price = ?, "
                "exit_ts = ?, pnl = ? WHERE id = ?",
                (float(last_exit) if last_exit is not None else None, ts,
                 float(realised), row[0]),
            )
        return

    entry_price = _weighted_entry_price(conn)
    side = "buy" if size > 0 else "sell"
    strategy = _cycle_strategy(conn, default=strategy)
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
