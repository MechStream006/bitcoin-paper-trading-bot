"""Testnet order executor.

Write-ahead discipline: every state change is persisted BEFORE the network call
that might cause it, and again after the outcome is known. A crash between the
two leaves a durable row the reconciler can resolve, rather than an order that
exists on the exchange and nowhere else.

Ambiguity is never guessed. If a submit times out, or the connection drops after
the bytes went out, or the response cannot be parsed, the order becomes UNKNOWN
and stays there until reconciliation matches it by ``client_order_id``. There is
no retry in this module, deliberately: a blind retry is how one intent becomes
two positions.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import requests

from src.data import storage
from src.execution.order_state import IllegalTransition, OrderState, transition
from src.risk import limits
from src.risk.manager import (
    AccountSnapshot, Decision, OrderIntent, RiskManager,
)

log = logging.getLogger(__name__)


class ExecutionBlocked(RuntimeError):
    """Risk vetoed the intent. No network call was made."""


class InstrumentMissing(RuntimeError):
    """The instrument row is absent, so an order row cannot be written."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TestnetExecutor:
    # Not a pytest test class -- the name begins with "Test" only because the
    # venue is Delta *testnet*. Without this, pytest tries to collect it.
    __test__ = False

    def __init__(self, conn, client, risk: RiskManager, *, strategy: str = "manual",
                 risk_state=None) -> None:
        self._conn = conn
        self._client = client
        self._risk = risk
        self._strategy = strategy
        #: Optional RiskStateStore. When present, a submitted order consumes a
        #: daily slot before the request leaves.
        self._risk_state = risk_state

    # -- internal ----------------------------------------------------------

    def _persist(self, intent: OrderIntent, state: OrderState,
                 delta_order_id: str | None = None,
                 filled_ts: str | None = None) -> None:
        storage.write_order(
            self._conn,
            client_order_id=intent.client_order_id,
            instrument_id=intent.product_id,
            side=intent.side,
            order_type=intent.order_type,
            quantity=float(intent.size),
            price=intent.limit_price,
            status=state.value,
            strategy=intent.strategy or self._strategy,
            placed_ts=_now_iso(),
            delta_order_id=delta_order_id,
            filled_ts=filled_ts,
        )

    # -- public ------------------------------------------------------------

    def submit(self, intent: OrderIntent, account: AccountSnapshot,
               now: datetime | None = None) -> tuple[OrderState, Decision]:
        """Validate then submit. Returns the resulting state and the decision.

        Ordering is the safety property: risk runs to completion BEFORE the
        instrument check and before any HTTP call, so a rejected intent is
        provably network-free.
        """
        state = OrderState.INTENT

        decision = self._risk.validate(intent, account, now=now)
        if not decision.approved:
            state = transition(state, OrderState.BLOCKED)
            self._persist(intent, state)
            log.warning("[exec] BLOCKED %s: %s", intent.client_order_id, decision.reason)
            return state, decision

        state = transition(state, OrderState.VALIDATED)

        # Explicit instrument existence check. orders.instrument_id carries a
        # FOREIGN KEY, but foreign_keys is a per-connection pragma and is
        # observed OFF on the live database -- so the constraint cannot be
        # relied upon. Verify rather than trust.
        if not storage.instrument_exists(self._conn, intent.product_id):
            raise InstrumentMissing(
                f"instrument id={intent.product_id} ({intent.symbol}) not in "
                "instruments; run scripts/resolve_perp_instrument.py first"
            )

        # Write-ahead: SUBMITTED is durable before the request leaves.
        state = transition(state, OrderState.SUBMITTED)
        self._persist(intent, state)

        # Consume the daily slot BEFORE sending. An order that times out may
        # exist on the exchange, so it must count; the alternative -- counting
        # on success -- would let a string of ambiguous submits bypass the cap
        # entirely. Idempotent per client_order_id, so a retry of this method
        # cannot consume two slots.
        # ENTRIES consume the daily slot; exits do not. An exit is bounded by
        # the position it closes, not by a daily budget, and counting it would
        # make the cap able to strand an open position.
        if self._risk_state is not None and intent.intent_kind not in ("exit", "protective"):
            self._risk_state.record_order_placed(intent.client_order_id, now)

        try:
            resp = self._client.place_order(
                product_id=intent.product_id,
                size=intent.size,
                side=intent.side,
                order_type=intent.order_type,
                limit_price=f"{intent.limit_price:.1f}",
                client_order_id=intent.client_order_id,
                # Belt and braces: risk already proved this reduces exposure,
                # but reduce_only makes the EXCHANGE refuse it if the position
                # moved between validation and arrival.
                reduce_only=(intent.intent_kind in ("exit", "protective")),
                # Native protective stop. intent.stop_price is the TRIGGER for
                # a protective order (for an entry it is only a risk-budget
                # input and is never transmitted).
                **({"stop_order_type": limits.STOP_ORDER_TYPE,
                    "stop_price": f"{intent.stop_price:.1f}",
                    "stop_trigger_method": limits.STOP_TRIGGER_METHOD}
                   if intent.intent_kind == "protective" and intent.stop_price
                   else {}),
            )
        except requests.exceptions.Timeout as exc:
            # The request may or may not have reached Delta. Ambiguous.
            state = transition(state, OrderState.UNKNOWN)
            self._persist(intent, state)
            log.error("[exec] UNKNOWN (timeout) %s: %s", intent.client_order_id, exc)
            return state, decision
        except requests.exceptions.HTTPError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status is not None and 400 <= status < 500:
                # A definitive refusal: the exchange saw it and said no.
                state = transition(state, OrderState.REJECTED)
                self._persist(intent, state)
                log.error("[exec] REJECTED %s: HTTP %s", intent.client_order_id, status)
                return state, decision
            # 5xx or unknown status: the order may exist. Ambiguous.
            state = transition(state, OrderState.UNKNOWN)
            self._persist(intent, state)
            log.error("[exec] UNKNOWN (HTTP %s) %s", status, intent.client_order_id)
            return state, decision
        except Exception as exc:                       # connection reset, DNS, etc.
            state = transition(state, OrderState.UNKNOWN)
            self._persist(intent, state)
            log.error("[exec] UNKNOWN (%s) %s: %s",
                      type(exc).__name__, intent.client_order_id, exc)
            return state, decision

        # A 200 that we cannot interpret is still ambiguous -- the order may
        # well exist on the exchange.
        if not isinstance(resp, dict) or resp.get("success") is not True:
            state = transition(state, OrderState.UNKNOWN)
            self._persist(intent, state)
            log.error("[exec] UNKNOWN (malformed response) %s", intent.client_order_id)
            return state, decision

        result = resp.get("result") or {}
        delta_id = result.get("id")
        state = transition(state, OrderState.ACKED)
        self._persist(intent, state, delta_order_id=str(delta_id) if delta_id else None)
        log.info("[exec] ACKED %s -> delta_order_id=%s", intent.client_order_id, delta_id)
        return state, decision

    def cancel(self, client_order_id: str) -> OrderState:
        """Cancel a resting order. Ambiguity again resolves to UNKNOWN."""
        row = storage.get_order(self._conn, client_order_id)
        if row is None:
            raise KeyError(f"no local order {client_order_id!r}")

        current = OrderState(row["status"])
        delta_id = row.get("delta_order_id")
        if delta_id is None:
            raise ValueError(
                f"{client_order_id} has no delta_order_id; resolve by "
                "reconciliation before attempting cancel"
            )

        try:
            self._client.cancel_order(int(delta_id), int(row["instrument_id"]))
        except Exception as exc:
            state = transition(current, OrderState.UNKNOWN)
            storage.write_order(
                self._conn, client_order_id=client_order_id,
                instrument_id=row["instrument_id"], side=row["side"],
                order_type=row["order_type"], quantity=row["quantity"],
                price=row["price"], status=state.value, strategy=row["strategy"],
                placed_ts=row["placed_ts"], delta_order_id=delta_id,
            )
            log.error("[exec] cancel UNKNOWN %s: %s", client_order_id, exc)
            return state

        state = transition(current, OrderState.CANCELLED)
        storage.write_order(
            self._conn, client_order_id=client_order_id,
            instrument_id=row["instrument_id"], side=row["side"],
            order_type=row["order_type"], quantity=row["quantity"],
            price=row["price"], status=state.value, strategy=row["strategy"],
            placed_ts=row["placed_ts"], delta_order_id=delta_id,
        )
        log.info("[exec] CANCELLED %s", client_order_id)
        return state

    def close_position(self, account: AccountSnapshot, *, limit_price: float,
                       client_order_id: str, now: datetime | None = None,
                       strategy: str | None = None) -> tuple[OrderState, Decision]:
        """Reduce an open position to flat. Supervised, same path as entry.

        Deliberately built on submit() rather than beside it: the close gets
        the same risk validation, the same write-ahead persistence, the same
        UNKNOWN handling and the same reconciliation. The only differences are
        carried on the intent itself -- intent_kind="exit", which exempts it
        from the daily caps and requires it to reduce exposure.

        Raises ValueError when flat: "close" with nothing to close would be an
        entry wearing the wrong label, and that is exactly the confusion the
        exit exemption must never permit.
        """
        if account.position_size == 0:
            raise ValueError("no open position to close")

        side = "sell" if account.position_size > 0 else "buy"
        intent = OrderIntent(
            client_order_id=client_order_id,
            symbol="BTCUSD",
            product_id=limits.BTCUSD_PRODUCT_ID,
            side=side,
            size=abs(account.position_size),
            order_type="limit_order",
            limit_price=limit_price,
            # An exit's risk is the position it removes, not a new stop.
            stop_price=limit_price,
            strategy=strategy or self._strategy,
            intent_kind="exit",
        )
        return self.submit(intent, account, now=now)
