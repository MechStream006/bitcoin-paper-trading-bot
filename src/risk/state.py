"""Daily portfolio risk state: order count and realised P&L, per UTC day.

The counters that bound total damage — MAX_ORDERS_PER_DAY and
MAX_DAILY_LOSS_USD — are worthless if they can be lost, double-counted, or
raced. Three decisions follow from that:

**Derived, not incremented.** Every contributing event is appended to
``risk_day_events`` keyed ``(utc_day, kind, ref)``; the counters are a COUNT
and a SUM over it. There is no `x = x + 1` anywhere, so recording the same
order twice is a no-op rather than a lost slot, and a caller that retries after
a crash cannot inflate the count.

**Atomic.** Every write runs inside ``BEGIN IMMEDIATE``, so concurrent writers
serialise at the database rather than interleaving a read-modify-write. This is
the same discipline the governance ledger adopted after its TOCTOU defect
(CLAUDE.md section 17, PR-8).

**Fail closed on corruption, open on absence.** A missing row for today means a
new UTC day and yields honest zeros. A row that *exists* but disagrees with the
event ledger, or carries impossible values, raises ``MalformedRiskState`` — the
caller must refuse to trade rather than guess which number is real.

``risk_state`` is kept as a mirror of the derived values, written in the same
transaction, so the pre-existing schema and any reader of it stay correct.
"""
from __future__ import annotations

import dataclasses
import logging
import sqlite3
from datetime import datetime, timezone

log = logging.getLogger(__name__)

RISK_EVENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS risk_day_events (
    utc_day  TEXT NOT NULL,
    kind     TEXT NOT NULL CHECK (kind IN ('order', 'pnl')),
    ref      TEXT NOT NULL,
    amount   REAL NOT NULL DEFAULT 0.0,
    ts       TEXT NOT NULL,
    PRIMARY KEY (utc_day, kind, ref)
);
"""

KIND_ORDER = "order"
KIND_PNL = "pnl"


class MalformedRiskState(RuntimeError):
    """Persisted state is self-inconsistent. Trading must stop, not guess."""


@dataclasses.dataclass(frozen=True)
class DailyRiskState:
    utc_day: str
    realised_pnl: float
    orders_placed: int


def init_risk_state_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(RISK_EVENTS_SCHEMA)
    conn.commit()


def utc_day_of(now: datetime | None = None) -> str:
    """The UTC calendar day that owns a timestamp.

    A naive datetime is treated as UTC: every caller in this codebase works in
    UTC, and silently reinterpreting one as local time would move an event
    across the day boundary.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc).date().isoformat()


def _derive(conn: sqlite3.Connection, utc_day: str) -> tuple[float, int]:
    row = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN kind = ? THEN amount END), 0.0), "
        "       COUNT(CASE WHEN kind = ? THEN 1 END) "
        "FROM risk_day_events WHERE utc_day = ?",
        (KIND_PNL, KIND_ORDER, utc_day),
    ).fetchone()
    return float(row[0] or 0.0), int(row[1] or 0)


def _mirror(conn: sqlite3.Connection, utc_day: str, pnl: float,
            orders: int, ts: str) -> None:
    conn.execute(
        """
        INSERT INTO risk_state
            (utc_day, realised_pnl, orders_placed, halted, halt_reason, updated_ts)
        VALUES (?, ?, ?, 0, NULL, ?)
        ON CONFLICT(utc_day) DO UPDATE SET
            realised_pnl  = excluded.realised_pnl,
            orders_placed = excluded.orders_placed,
            updated_ts    = excluded.updated_ts
        """,
        (utc_day, float(pnl), int(orders), ts),
    )


def load_state(conn: sqlite3.Connection, now: datetime | None = None) -> DailyRiskState:
    """Today's counters, validated against the event ledger.

    Raises MalformedRiskState if the mirror disagrees with the events or holds
    an impossible value.
    """
    day = utc_day_of(now)
    pnl, orders = _derive(conn, day)

    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM risk_state WHERE utc_day = ?", (day,)).fetchone()

    if row is not None:
        try:
            stored_pnl = float(row["realised_pnl"])
            stored_orders = int(row["orders_placed"])
        except (TypeError, ValueError) as exc:
            raise MalformedRiskState(
                f"risk_state row for {day} has non-numeric counters: {exc}") from exc
        if stored_orders < 0:
            raise MalformedRiskState(
                f"risk_state row for {day} has negative orders_placed={stored_orders}")
        if stored_orders != orders or abs(stored_pnl - pnl) > 1e-9:
            raise MalformedRiskState(
                f"risk_state row for {day} disagrees with the event ledger: "
                f"stored (pnl={stored_pnl}, orders={stored_orders}) vs "
                f"derived (pnl={pnl}, orders={orders})")

    return DailyRiskState(utc_day=day, realised_pnl=pnl, orders_placed=orders)


def record_order_placed(conn: sqlite3.Connection, client_order_id: str,
                        now: datetime | None = None) -> DailyRiskState:
    """Consume one order slot. Idempotent per client_order_id.

    Called when an order is about to be SENT, not when it succeeds: an order
    that times out may well exist on the exchange, and must consume its slot.
    """
    day = utc_day_of(now)
    ts = (now or datetime.now(timezone.utc)).isoformat()
    return _append(conn, day, KIND_ORDER, client_order_id, 0.0, ts)


def record_realised_pnl(conn: sqlite3.Connection, ref: str, amount_usd: float,
                        now: datetime | None = None) -> DailyRiskState:
    """Record realised P&L (negative for a loss). Idempotent per `ref`."""
    day = utc_day_of(now)
    ts = (now or datetime.now(timezone.utc)).isoformat()
    return _append(conn, day, KIND_PNL, ref, float(amount_usd), ts)


def _append(conn: sqlite3.Connection, day: str, kind: str, ref: str,
            amount: float, ts: str) -> DailyRiskState:
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "INSERT OR IGNORE INTO risk_day_events (utc_day, kind, ref, amount, ts) "
            "VALUES (?, ?, ?, ?, ?)",
            (day, kind, ref, float(amount), ts),
        )
        pnl, orders = _derive(conn, day)
        _mirror(conn, day, pnl, orders, ts)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return DailyRiskState(utc_day=day, realised_pnl=pnl, orders_placed=orders)


class RiskStateStore:
    """Binds persisted daily state to the risk layer.

    ``counters()`` is the provider ``RiskManager`` consults on every validate(),
    so a cap tripped between two calls binds on the next one instead of at the
    next process start.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def state(self, now: datetime | None = None) -> DailyRiskState:
        return load_state(self._conn, now)

    def counters(self, now: datetime | None = None) -> tuple[float, int]:
        s = load_state(self._conn, now)
        return s.realised_pnl, s.orders_placed

    def record_order_placed(self, client_order_id: str,
                            now: datetime | None = None) -> DailyRiskState:
        return record_order_placed(self._conn, client_order_id, now)

    def record_realised_pnl(self, ref: str, amount_usd: float,
                            now: datetime | None = None) -> DailyRiskState:
        return record_realised_pnl(self._conn, ref, amount_usd, now)
