"""Strategy -> execution boundary.

Read-only adapter over ``evidence_forward.db``. It reads the latest
``g3_momentum_tsmom`` position and turns it into at most one ``OrderIntent`` per
UTC day. It has no authority to execute: it emits intents, and the risk manager
decides.

**Disabled by default** (``DEMO_STRATEGY_ENABLED=0``). The execution, risk and
reconciliation layers must be proven before any signal is allowed to originate
an order. Enabling this is a separate, explicit operator decision.

Deliberately does NOT: recompute signals, touch the research pipeline, write to
``evidence_forward.db``, or size positions. Sizing is fixed at one contract by
``limits.MAX_CONTRACTS_PER_ORDER``.
"""
from __future__ import annotations

import logging
import math
import pathlib
import sqlite3
from datetime import datetime, timezone

from src.risk import limits
from src.risk.manager import OrderIntent

log = logging.getLogger(__name__)

SIGNAL_ID = "g3_momentum_tsmom"

#: Stop distance as a percentage of price.
#:
#: Sized so NET risk -- adverse move PLUS round-trip fees -- fits the $1.00
#: per-trade budget. At a $64,187.50 mark, 1 contract (0.001 BTC):
#:
#:     budget                       $1.0000
#:     round-trip taker fees        $0.0642
#:     available for price movement $0.9358
#:     max stop distance            $935.80  = 1.458% of mark
#:
#: 1.5% was tried first and is WRONG: it yields $1.027 net and every intent is
#: correctly vetoed by the risk manager. 1.4% leaves headroom for fee/mark
#: drift. The risk manager recomputes this per intent regardless and remains
#: the authority -- this constant only has to keep the bridge from emitting
#: orders that are certain to be rejected.
STOP_DISTANCE_PCT = 1.4


class StrategyDisabled(RuntimeError):
    """The bridge is off. This is the default and expected state."""


def _utc_today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def read_latest_signal(forward_db_path: str, signal_id: str = SIGNAL_ID) -> dict | None:
    """Latest run_log row for `signal_id`, READ-ONLY. Never raises on absence."""
    p = pathlib.Path(forward_db_path)
    if not p.exists():
        return None
    uri = p.resolve().as_uri() + "?mode=ro"
    conn = None
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM run_log WHERE signal_id = ? "
            "ORDER BY run_date DESC LIMIT 1",
            (signal_id,),
        ).fetchone()
        return dict(row) if row else None
    except Exception as exc:
        log.warning("[bridge] could not read %s: %s", forward_db_path, exc)
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def latest_direction(forward_db_path: str, signal_id: str = SIGNAL_ID) -> int | None:
    """Most recent open trade direction: +1 long, -1 short, None if flat/absent."""
    p = pathlib.Path(forward_db_path)
    if not p.exists():
        return None
    uri = p.resolve().as_uri() + "?mode=ro"
    conn = None
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
        row = conn.execute(
            "SELECT direction FROM trade_entries WHERE signal_id = ? AND open = 1 "
            "ORDER BY entry_date DESC LIMIT 1",
            (signal_id,),
        ).fetchone()
        return int(row[0]) if row else None
    except Exception as exc:
        log.warning("[bridge] could not read direction: %s", exc)
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def executable_limit_price(direction: int, best_bid: float, best_ask: float) -> float:
    """A limit price that actually crosses, tick-aligned in the safe direction.

    `round(mark / tick) * tick` -- the previous rule -- is NOT marketable. Gate
    5C measured it: a sell built from mark 64400.36 produced 64400.4 against a
    bid of 64400.0, so it sat ABOVE the bid and would have rested rather than
    filled. The intent that gate existed to test could never have produced a
    fill.

    To cross, a BUY must reach the ask and a SELL must reach the bid. Rounding
    must therefore go AWAY from the touch (ceil for a buy, floor for a sell):
    rounding to nearest can land a buy below the ask or a sell above the bid,
    which is exactly the failure being fixed.
    """
    tick = limits.BTCUSD_TICK_SIZE
    if best_bid <= 0 or best_ask <= 0:
        raise ValueError(f"invalid book: bid={best_bid} ask={best_ask}")
    if best_ask < best_bid:
        raise ValueError(f"crossed book: bid={best_bid} ask={best_ask}")
    if direction == 1:
        return round(math.ceil(best_ask / tick) * tick, 1)
    return round(math.floor(best_bid / tick) * tick, 1)


def build_intent(
    *,
    direction: int,
    mark_price: float,
    enabled: bool,
    best_bid: float,
    best_ask: float,
    utc_day: str | None = None,
    seq: int = 1,
) -> OrderIntent:
    """Turn a direction into a single-contract, genuinely marketable limit intent.

    Raises StrategyDisabled unless explicitly enabled -- the guard lives here so
    a caller cannot forget it.

    best_bid/best_ask are REQUIRED: the price must be built from the book, not
    from the mark, or the order does not cross (see executable_limit_price).
    """
    if not enabled:
        raise StrategyDisabled(
            "DEMO_STRATEGY_ENABLED=0: the strategy bridge is disabled until the "
            "execution stack is proven"
        )
    if direction not in (1, -1):
        raise ValueError(f"direction must be +1 or -1, got {direction!r}")

    day = utc_day or _utc_today()
    side = "buy" if direction == 1 else "sell"

    tick = limits.BTCUSD_TICK_SIZE
    limit_price = executable_limit_price(direction, best_bid, best_ask)
    # The stop is recomputed from the EXECUTED price, not the mark, so the risk
    # budget describes the trade that will actually exist.
    offset = limit_price * STOP_DISTANCE_PCT / 100
    stop_price = round(
        round((limit_price - offset if direction == 1 else limit_price + offset) / tick) * tick,
        1,
    )

    return OrderIntent(
        client_order_id=f"{SIGNAL_ID}-{day}-{side}-{seq}",
        symbol="BTCUSD",
        product_id=limits.BTCUSD_PRODUCT_ID,
        side=side,
        size=limits.MAX_CONTRACTS_PER_ORDER,
        order_type="limit_order",
        limit_price=limit_price,
        stop_price=stop_price,
        strategy=SIGNAL_ID,
    )
