"""Order lifecycle state machine for the testnet demo executor.

Every transition is explicit. There are no implicit or inferred moves: an order
changes state only because code asked it to, and only along an arc this module
permits. Illegal arcs raise rather than being silently tolerated, because a
mis-tracked order is indistinguishable from a lost one.

    INTENT ──► VALIDATED ──► SUBMITTED ──► ACKED ──┬──► FILLED     (terminal)
       │           │             │                 ├──► PARTIAL ──► FILLED
       │           │             │                 │            └─► CANCELLED
       │           │             │                 └──► CANCELLED (terminal)
       │           │             └──► UNKNOWN ──(reconcile only)──► ACKED
       │           │                                              │ FILLED
       │           │                                              │ PARTIAL
       │           │                                              │ CANCELLED
       │           │                                              └─ REJECTED
       │           └──► REJECTED (terminal)     exchange refused
       └──► BLOCKED  (terminal)                 risk veto / kill switch

UNKNOWN is the state that matters. It is entered when a submit may or may not
have reached the exchange -- a timeout, a connection reset after the bytes went
out, a malformed response. The only legal way out is RECONCILIATION against the
deterministic client_order_id. It is never resolved by retrying, because a blind
retry is exactly how one intent becomes two positions.
"""
from __future__ import annotations

import enum


class OrderState(str, enum.Enum):
    INTENT = "intent"
    VALIDATED = "validated"
    SUBMITTED = "submitted"
    ACKED = "acked"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


TERMINAL_STATES = frozenset({
    OrderState.FILLED,
    OrderState.CANCELLED,
    OrderState.REJECTED,
    OrderState.BLOCKED,
})

#: States that still need attention from the reconciler.
OPEN_STATES = frozenset({
    OrderState.SUBMITTED,
    OrderState.ACKED,
    OrderState.PARTIAL,
    OrderState.UNKNOWN,
})

ALLOWED_TRANSITIONS: dict[OrderState, frozenset[OrderState]] = {
    OrderState.INTENT:    frozenset({OrderState.VALIDATED, OrderState.BLOCKED}),
    OrderState.VALIDATED: frozenset({OrderState.SUBMITTED, OrderState.REJECTED,
                                     OrderState.BLOCKED}),
    OrderState.SUBMITTED: frozenset({OrderState.ACKED, OrderState.REJECTED,
                                     OrderState.UNKNOWN}),
    OrderState.ACKED:     frozenset({OrderState.PARTIAL, OrderState.FILLED,
                                     OrderState.CANCELLED, OrderState.UNKNOWN}),
    OrderState.PARTIAL:   frozenset({OrderState.PARTIAL, OrderState.FILLED,
                                     OrderState.CANCELLED, OrderState.UNKNOWN}),
    # UNKNOWN is resolvable ONLY by the reconciler (enforced by
    # `reconcile_only=True` on transition()), never by the submit path.
    OrderState.UNKNOWN:   frozenset({OrderState.ACKED, OrderState.PARTIAL,
                                     OrderState.FILLED, OrderState.CANCELLED,
                                     OrderState.REJECTED}),
    OrderState.FILLED:    frozenset(),
    OrderState.CANCELLED: frozenset(),
    OrderState.REJECTED:  frozenset(),
    OrderState.BLOCKED:   frozenset(),
}


class IllegalTransition(RuntimeError):
    """Raised when code attempts an arc the state machine does not permit."""


def is_terminal(state: OrderState) -> bool:
    return state in TERMINAL_STATES


def is_open(state: OrderState) -> bool:
    return state in OPEN_STATES


def can_transition(src: OrderState, dst: OrderState) -> bool:
    return dst in ALLOWED_TRANSITIONS.get(src, frozenset())


def transition(src: OrderState, dst: OrderState, *, reconcile_only: bool = False) -> OrderState:
    """Validate and return `dst`. Raises IllegalTransition otherwise.

    `reconcile_only` must be True to leave UNKNOWN. That flag is passed only by
    the reconciler, so the submit path cannot resolve an ambiguous order by
    guessing -- which is the single most dangerous thing an executor can do.
    """
    if is_terminal(src):
        raise IllegalTransition(f"{src.value} is terminal; cannot move to {dst.value}")
    if not can_transition(src, dst):
        raise IllegalTransition(f"illegal transition {src.value} -> {dst.value}")
    if src is OrderState.UNKNOWN and not reconcile_only:
        raise IllegalTransition(
            f"UNKNOWN -> {dst.value} is reconciliation-only; a blind retry or "
            "optimistic resolve is never permitted"
        )
    return dst
