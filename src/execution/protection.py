"""Protective-stop layer.

One invariant governs this module:

    open_position != 0  =>  a valid native reduce-only protective stop exists

Anything else is a halt. Not a warning, not a retry loop -- a halt, because an
exposed position with no protection is the one state this system must never
occupy silently. The per-trade risk budget that authorised the entry is
computed from a stop; if that stop does not exist, the budget is fiction.

Protection state is DERIVED, never stored as a flag. It is computed from the
open position (itself derived from the fill ledger) and the exchange's live
order list. A stored "protected" boolean could be true while the exchange has
no such order; a derived one cannot.

The protective stop's client_order_id encodes the position it protects AND the
quantity it protects, so:

  * re-running reconciliation cannot create a second stop (same id, and the
    UNIQUE index on orders.client_order_id is the DB-level backstop),
  * a change in exposure produces a DIFFERENT id, which is what makes
    re-sizing a placement rather than a mutation,
  * a restart recomputes the same id from persisted state.

Verified exchange semantics (probe order 2169420335, 2026-08-19): a native
stop_loss_order with mark_price triggering and reduce_only=true is accepted on
product 84, rests as state="pending", and reserves zero margin.
"""
from __future__ import annotations

import dataclasses
import enum
import logging

from src.data import storage
from src.execution import fills as fills_mod
from src.execution.order_state import OPEN_STATES, OrderState
from src.risk import limits
from src.risk.manager import AccountSnapshot, OrderIntent

log = logging.getLogger(__name__)

#: Exchange order states in which a stop is genuinely standing guard.
#: "pending" is the resting state of an UNTRIGGERED stop -- verified on the
#: probe order -- and is therefore the normal healthy value, not a warning.
LIVE_STOP_STATES = frozenset({"pending", "open"})


class ProtectionState(str, enum.Enum):
    FLAT = "flat"                  # no exposure, no stop needed
    PROTECTED = "protected"        # exposure, correct stop live
    UNPROTECTED = "unprotected"    # exposure, no live stop -> HALT
    STALE = "stale"                # exposure, stop for the wrong quantity -> replace
    ORPHANED = "orphaned"          # no exposure, stop still live -> cancel


@dataclasses.dataclass(frozen=True)
class ProtectionStatus:
    state: ProtectionState
    position_size: int
    stop_coid: str | None = None
    stop_order_id: str | None = None
    stop_size: int | None = None
    detail: str = ""

    @property
    def is_safe(self) -> bool:
        """Only FLAT and PROTECTED are states the system may keep running in."""
        return self.state in (ProtectionState.FLAT, ProtectionState.PROTECTED)


def protective_coid(position_id: int, size: int) -> str:
    """Deterministic id for the stop protecting `size` of position `position_id`.

    Quantity is part of the identity on purpose: a partial fill changes the
    exposure, which must produce a new order rather than silently leave a stop
    guarding the wrong amount.
    """
    return f"stop-p{int(position_id)}-q{int(abs(size))}"


def stop_prices(entry_price: float, position_size: int) -> tuple[float, float]:
    """(trigger, limit) for a protective stop on `position_size` at `entry_price`.

    A long is protected below entry and a short above it. The limit sits
    further along the same direction so a triggered stop can actually fill
    rather than resting through the move it exists to escape.
    """
    tick = limits.BTCUSD_TICK_SIZE
    direction = 1 if position_size > 0 else -1
    trigger = entry_price * (1 - direction * limits.PROTECTIVE_STOP_DISTANCE_PCT / 100.0)
    limit = trigger * (1 - direction * limits.PROTECTIVE_STOP_LIMIT_SLIPPAGE_PCT / 100.0)
    return (round(round(trigger / tick) * tick, 1),
            round(round(limit / tick) * tick, 1))


def _live_stops(exchange_orders) -> list[dict]:
    out = []
    for o in exchange_orders or []:
        if not isinstance(o, dict):
            continue
        if o.get("stop_order_type") and str(o.get("state", "")).lower() in LIVE_STOP_STATES:
            out.append(o)
    return out


def evaluate(conn, exchange_orders) -> ProtectionStatus:
    """Derive protection state from exposure + the exchange's live orders.

    Pure and read-only: it decides nothing and places nothing. Callers act on
    the verdict, which keeps "what is true" separable from "what to do".
    """
    size = fills_mod.position_size(conn)
    stops = _live_stops(exchange_orders)

    if size == 0:
        if stops:
            s = stops[0]
            return ProtectionStatus(
                ProtectionState.ORPHANED, 0, s.get("client_order_id"),
                str(s.get("id")) if s.get("id") is not None else None,
                _stop_size(s), f"{len(stops)} stop(s) live while flat")
        return ProtectionStatus(ProtectionState.FLAT, 0)

    if not stops:
        return ProtectionStatus(ProtectionState.UNPROTECTED, size,
                                detail=f"position {size:+d} with no live stop")

    wanted_side = "sell" if size > 0 else "buy"
    matching = [s for s in stops
                if str(s.get("side", "")).lower() == wanted_side
                and _stop_size(s) == abs(size)
                and bool(s.get("reduce_only"))]
    if matching:
        s = matching[0]
        return ProtectionStatus(
            ProtectionState.PROTECTED, size, s.get("client_order_id"),
            str(s.get("id")) if s.get("id") is not None else None,
            _stop_size(s), "stop live and correctly sized")

    s = stops[0]
    return ProtectionStatus(
        ProtectionState.STALE, size, s.get("client_order_id"),
        str(s.get("id")) if s.get("id") is not None else None, _stop_size(s),
        f"stop size={_stop_size(s)} side={s.get('side')} "
        f"reduce_only={s.get('reduce_only')} does not guard position {size:+d}")


def _stop_size(order: dict) -> int | None:
    for key in ("size", "unfilled_size"):
        if order.get(key) is not None:
            try:
                return int(abs(float(order[key])))
            except (TypeError, ValueError):
                return None
    return None


def already_placed(conn, coid: str) -> bool:
    """Whether this exact protective order was already submitted.

    Guards the window between "we sent it" and "the exchange lists it": within
    that gap the exchange query shows no stop, and without this check the next
    reconciliation pass would submit a second one.
    """
    row = storage.get_order(conn, coid)
    if row is None:
        return False
    try:
        return OrderState(row["status"]) in OPEN_STATES or \
            OrderState(row["status"]) is OrderState.FILLED
    except ValueError:
        return False


def build_intent(conn, position_size: int, position_id: int,
                 entry_price: float, strategy: str = "protection") -> OrderIntent:
    trigger, limit = stop_prices(entry_price, position_size)
    return OrderIntent(
        client_order_id=protective_coid(position_id, position_size),
        symbol="BTCUSD",
        product_id=limits.BTCUSD_PRODUCT_ID,
        side="sell" if position_size > 0 else "buy",
        size=abs(position_size),
        order_type="limit_order",
        limit_price=limit,
        stop_price=trigger,
        strategy=strategy,
        intent_kind="protective",
    )


def ensure(conn, executor, client, status: ProtectionStatus, *,
           mark_price: float, now=None) -> ProtectionStatus:
    """Act on a protection verdict. Returns the status after acting.

    Never treats "we tried" as "protected": the caller re-derives state from
    the exchange, and any outcome other than an acknowledged stop leaves the
    position UNPROTECTED, which halts. Failure is not retried here -- one
    attempt per reconciliation pass, so a persistent failure produces a
    persistent halt rather than an order storm.
    """
    if status.state in (ProtectionState.FLAT, ProtectionState.PROTECTED):
        return status

    if status.state is ProtectionState.ORPHANED:
        # A stop with nothing to protect must go: it is a resting order that
        # could act on a position that no longer exists.
        if status.stop_order_id:
            try:
                client.cancel_order(int(status.stop_order_id), limits.BTCUSD_PRODUCT_ID)
                log.warning("[protection] cancelled orphaned stop %s", status.stop_order_id)
            except Exception as exc:
                log.error("[protection] failed to cancel orphaned stop %s: %s",
                          status.stop_order_id, exc)
        return status

    if status.state is ProtectionState.STALE and status.stop_order_id:
        # Re-sizing is cancel-then-place. The window between the two is exactly
        # why the caller re-derives and halts if it ends unprotected.
        try:
            client.cancel_order(int(status.stop_order_id), limits.BTCUSD_PRODUCT_ID)
            log.warning("[protection] cancelled mis-sized stop %s (%s)",
                        status.stop_order_id, status.detail)
        except Exception as exc:
            log.error("[protection] could not cancel stale stop %s: %s",
                      status.stop_order_id, exc)
            return status

    position = fills_mod.open_position(conn)
    if position is None:
        return status

    intent = build_intent(conn, status.position_size, int(position["id"]),
                          float(position["entry_price"]))
    if already_placed(conn, intent.client_order_id):
        log.info("[protection] stop %s already submitted; not duplicating",
                 intent.client_order_id)
        return status
    if executor is None:
        log.error("[protection] no executor wired; cannot establish protection")
        return status

    # Equity is deliberately zero here: the reconciler does not know the wallet
    # balance, and a reducing order does not consult it -- a reduce-only stop
    # reserves no margin (measured, probe 2169420335). If that guard ever
    # applied to protection again, this would fail closed rather than lie.
    account = AccountSnapshot(equity_usd=0.0, available_usd=0.0,
                              open_positions=1, mark_price=mark_price,
                              position_size=status.position_size)
    try:
        state, decision = executor.submit(intent, account, now=now)
    except Exception as exc:
        log.error("[protection] stop submission raised %s: %s", type(exc).__name__, exc)
        return status

    if state is not OrderState.ACKED:
        log.critical("[protection] stop %s did not ACK (state=%s, reason=%s); "
                     "position remains UNPROTECTED",
                     intent.client_order_id, state.value, decision.reason)
        return status

    log.info("[protection] protective stop %s established for position %+d",
             intent.client_order_id, status.position_size)
    return dataclasses.replace(status, state=ProtectionState.PROTECTED,
                               stop_coid=intent.client_order_id,
                               detail="stop established this pass")
