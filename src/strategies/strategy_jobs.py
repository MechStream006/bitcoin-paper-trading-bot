"""Strategy evaluation job -- the missing link between signal and execution.

``strategies/bridge.py`` has been complete and tested since the execution stack
landed, but nothing in ``src/`` ever imported it: no scheduled job called
``build_intent()``, so ``DEMO_STRATEGY_ENABLED`` gated a code path that was
unreachable either way. This module is that caller, and nothing more.

WHAT IT DOES
------------
One pass, on an interval::

    bridge.latest_direction()  ->  bridge.build_intent()
                               ->  RiskManager.validate()
                               ->  [dry-run: STOP]  |  executor.submit()
                                                    -> [ACKED only]
                                                       capture_signal_context

Every step delegates. No signal is recomputed, no price is invented, no size is
chosen here: sizing is ``limits.MAX_CONTRACTS_PER_ORDER`` inside `build_intent`,
the risk verdict is `RiskManager.validate`'s, and submission is the executor's.

TWO INDEPENDENT SAFETY GATES
----------------------------
1. ``DEMO_STRATEGY_ENABLED`` -- checked by `build_intent` itself, which raises
   `StrategyDisabled` rather than trusting a caller to remember. The job also
   checks it before doing any work, so a disabled strategy costs nothing.
2. ``dry_run`` -- when true the pass stops after the risk decision and
   **cannot reach the executor**: `submit()` is not called on any path. This is
   the gate that makes a first deployment observable without reaching Delta.

Both default to the refusing value.

IDEMPOTENCY
-----------
`bridge.client_order_id()` is deterministic in (signal, UTC day, side, seq), so
the same day's intent always has the same id. The job looks that id up in the
orders table before submitting: an id already present means this day's intent
was already acted on, and the pass returns without a second submission. The
interval can therefore be short without risking duplicates, and a restart
mid-day re-derives the same id rather than a new one.

WHAT IT DOES NOT TOUCH
----------------------
Reconciliation, the kill switch, risk limits, sizing, protective orders and
governance are all unchanged. The job consults `reconciler.trading_permitted`
but never clears a halt, and it never places a protective order -- protection
is established by the reconciliation pass, exactly as before.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from src.data import storage
from src.execution import fills as fills_mod
from src.execution.order_state import OrderState
from src.risk.manager import AccountSnapshot, Decision, OrderIntent
from src.strategies import bridge

log = logging.getLogger(__name__)

STRATEGY_JOB_ID = "strategy_evaluate"

#: Evaluation cadence. The signal changes at most once a UTC day (run_daily
#: writes it at 01:00 UTC), so this only has to be short enough to pick the day
#: up promptly. Idempotency, not the interval, is what prevents duplicates.
DEFAULT_STRATEGY_INTERVAL_SEC = 900


@dataclass(frozen=True)
class EvaluationResult:
    """What one pass did. Returned for tests and logged for operators."""
    outcome: str                     # see the constants below
    detail: str = ""
    intent: Optional[OrderIntent] = None
    decision: Optional[Decision] = None
    submitted: bool = False
    #: The state `executor.submit()` reported, verbatim, when it was reached.
    #: None on every path that never got that far.
    order_state: Optional[str] = None


# outcomes
DISABLED = "disabled"                 # DEMO_STRATEGY_ENABLED=0
NO_SIGNAL = "no_signal"               # signal flat, or no forward row
DUPLICATE = "duplicate"               # this day's intent already exists
NOT_PERMITTED = "not_permitted"       # reconciler halted / trading closed
QUOTE_UNAVAILABLE = "quote_unavailable"
REJECTED = "rejected"                 # risk said no
DRY_RUN = "dry_run"                   # approved, but not submitted by design
SUBMITTED = "submitted"               # the exchange ACKED it
NOT_ACKED = "not_acked"               # submit() returned without an ACK
ERROR = "error"

#: The one state that means the exchange accepted the order.
#:
#: `TestnetExecutor.submit` RETURNS rather than raises for every failure it
#: understands -- BLOCKED by its own risk re-validation, REJECTED on an HTTP
#: 4xx, and UNKNOWN on a timeout, a 5xx, a transport error or an
#: uninterpretable 200. Only `InstrumentMissing` escapes as an exception. So
#: "submit() did not raise" is not "the order was submitted", and treating it
#: as such reported five distinct failures as SUBMITTED.
ACCEPTED_STATE = OrderState.ACKED


def evaluate_once(
    *,
    forward_db_path: str,
    conn,
    client,
    risk_manager,
    executor,
    reconciler=None,
    strategy_enabled: bool,
    dry_run: bool = True,
    now: datetime | None = None,
) -> EvaluationResult:
    """One signal -> intent -> risk -> (dry-run stop | submit) pass.

    Never raises: a strategy job that throws would trip the scheduler's failure
    counter and confuse it with an execution fault. Every failure is returned
    as an ERROR result and logged.
    """
    now = now or datetime.now(timezone.utc)

    if not strategy_enabled:
        return EvaluationResult(DISABLED,
                                "DEMO_STRATEGY_ENABLED=0; no evaluation performed")

    # The execution gate the reconciler owns. Consulted, never modified: a
    # halted stack must not originate, and only clear_halt_if_clean() reopens it.
    if reconciler is not None and not reconciler.trading_permitted:
        return EvaluationResult(NOT_PERMITTED,
                                f"reconciler: {getattr(reconciler, 'halt_reason', '') or 'not permitted'}")

    try:
        direction = bridge.latest_direction(forward_db_path)
    except Exception as exc:  # noqa: BLE001 -- never raise into the scheduler
        log.error("[strategy] signal read failed: %s: %s", type(exc).__name__, exc)
        return EvaluationResult(ERROR, f"signal read: {type(exc).__name__}: {exc}")

    if direction is None or direction == 0:
        return EvaluationResult(NO_SIGNAL, f"latest direction is {direction!r}")

    day = now.strftime("%Y-%m-%d")
    side = "buy" if direction == 1 else "sell"
    coid = bridge.client_order_id(bridge.SIGNAL_ID, day, side, 1)

    # Idempotency, before any network call: the id is deterministic, so an
    # existing row means this day's intent was already acted on.
    try:
        if storage.get_order(conn, coid) is not None:
            return EvaluationResult(DUPLICATE, f"{coid} already exists")
    except Exception as exc:  # noqa: BLE001
        log.error("[strategy] duplicate check failed: %s: %s", type(exc).__name__, exc)
        return EvaluationResult(ERROR, f"duplicate check: {type(exc).__name__}: {exc}")

    try:
        quote = _quote(client)
    except Exception as exc:  # noqa: BLE001
        log.error("[strategy] quote unavailable: %s: %s", type(exc).__name__, exc)
        return EvaluationResult(QUOTE_UNAVAILABLE, f"{type(exc).__name__}: {exc}")
    if quote is None:
        return EvaluationResult(QUOTE_UNAVAILABLE, "no usable BTCUSD bid/ask/mark")
    mark, bid, ask = quote

    try:
        intent = bridge.build_intent(
            direction=direction, mark_price=mark, enabled=strategy_enabled,
            best_bid=bid, best_ask=ask, utc_day=day, seq=1)
    except bridge.StrategyDisabled as exc:
        return EvaluationResult(DISABLED, str(exc))
    except Exception as exc:  # noqa: BLE001
        log.error("[strategy] build_intent failed: %s: %s", type(exc).__name__, exc)
        return EvaluationResult(ERROR, f"build_intent: {type(exc).__name__}: {exc}")

    try:
        account = _account(conn, client, mark)
        decision = risk_manager.validate(intent, account, now=now)
    except Exception as exc:  # noqa: BLE001
        log.error("[strategy] risk evaluation failed: %s: %s", type(exc).__name__, exc)
        return EvaluationResult(ERROR, f"risk: {type(exc).__name__}: {exc}",
                                intent=intent)

    if not decision.approved:
        log.info("[strategy] %s %s x%d @ %.1f REJECTED: %s",
                 intent.side, intent.symbol, intent.size, intent.limit_price,
                 decision.reason)
        return EvaluationResult(REJECTED, decision.reason, intent, decision)

    if dry_run:
        # The gate that keeps a first deployment off the exchange. submit() is
        # not reachable from here.
        log.warning(
            "[strategy] DRY-RUN: would submit %s %s x%d limit=%.1f stop=%s "
            "coid=%s risk=$%.2f fees=$%.2f -- NOT submitted",
            intent.side, intent.symbol, intent.size, intent.limit_price,
            intent.stop_price, intent.client_order_id,
            decision.risk_usd or 0.0, decision.fees_usd or 0.0)
        return EvaluationResult(DRY_RUN, "approved; dry_run=True", intent, decision)

    try:
        outcome = executor.submit(intent, account, now=now)
    except Exception as exc:  # noqa: BLE001
        log.error("[strategy] submit failed: %s: %s", type(exc).__name__, exc)
        return EvaluationResult(ERROR, f"submit: {type(exc).__name__}: {exc}",
                                intent, decision)

    state = _submitted_state(outcome)
    if state != ACCEPTED_STATE:
        # Reported as what it was. The order row the executor already wrote is
        # the record of the attempt; nothing here retries, cancels or otherwise
        # acts on it -- reconciliation owns an ambiguous order, exactly as before.
        log.error("[strategy] NOT ACKED %s: executor returned %s",
                  intent.client_order_id, state)
        return EvaluationResult(NOT_ACKED, f"executor returned {state}",
                                intent, decision, order_state=state)

    log.warning("[strategy] SUBMITTED %s", intent.client_order_id)

    # Pin the signal state that motivated this entry, at submit time. The
    # forward database moves on, so a reader months later can recover what the
    # signal says now but not what it said when the order went out --
    # `capture_signal_context` exists precisely for that and, until this call,
    # had no caller anywhere (its own docstring said so), which is why
    # `trade_signal_context` was empty and research had to attribute strategy
    # from `positions.strategy`, a projection that was wrong for four trades.
    #
    # Strictly after a successful submit, and strictly best-effort: the helper
    # already swallows its own failures, and this guard covers the argument
    # handling outside its try block. A research record must never be able to
    # turn a filled order into a reported failure, so nothing here touches the
    # returned result.
    #
    # The direction is taken from the SUBMITTED INTENT, not re-read. This call
    # happens after the quote, the wallet read, risk validation and an HTTP
    # round-trip, and the 01:00 UTC collector can close or open a forward trade
    # inside that window -- so a re-read can pin a direction the order never
    # carried. `intent.side` is the artefact that actually went to Delta.
    try:
        bridge.capture_signal_context(
            conn, intent.client_order_id, forward_db_path, bridge.SIGNAL_ID,
            direction=_intent_direction(intent))
    except Exception as exc:  # noqa: BLE001
        log.warning("[strategy] signal context not captured for %s: %s: %s",
                    intent.client_order_id, type(exc).__name__, exc)

    return EvaluationResult(SUBMITTED, "submitted", intent, decision,
                            submitted=True, order_state=state)


def _submitted_state(outcome: Any) -> str:
    """The order state `submit()` reported, as its plain string value.

    `TestnetExecutor.submit` returns ``(OrderState, Decision)``. Anything this
    cannot read as a known state -- a differently shaped return from another
    executor, or None -- resolves to UNKNOWN rather than being assumed
    accepted. This predicate gates a durable research record and the
    operator-facing outcome, so it fails closed.
    """
    state = outcome[0] if isinstance(outcome, tuple) and outcome else outcome
    try:
        return OrderState(getattr(state, "value", state)).value
    except (ValueError, TypeError):
        return OrderState.UNKNOWN.value


def _intent_direction(intent: OrderIntent) -> int:
    """+1 / -1 for the side actually submitted.

    Read off the intent rather than the signal, so the recorded direction is
    the one the order carried even if the ledger moves underneath.
    """
    return 1 if intent.side == "buy" else -1


def _quote(client) -> tuple[float, float, float] | None:
    """(mark, best_bid, best_ask) for BTCUSD, or None if the book is unusable.

    Delta returns every numeric as a string (CLAUDE.md sec.11), hence the casts.
    """
    t = (client.get_ticker("BTCUSD") or {}).get("result") or {}
    quotes = t.get("quotes") or {}
    try:
        mark = float(t.get("mark_price"))
        bid = float(quotes.get("best_bid"))
        ask = float(quotes.get("best_ask"))
    except (TypeError, ValueError):
        return None
    if not (mark > 0 and bid > 0 and ask > 0 and ask >= bid):
        return None
    return mark, bid, ask


def _account(conn, client, mark: float) -> AccountSnapshot:
    """Account state for the risk manager, from the same sources the rest of
    the execution stack uses: exposure from the fill ledger, balances from the
    wallet endpoint."""
    position = fills_mod.position_size(conn)
    equity = available = 0.0
    try:
        wallet = (client.get_wallet_balances() or {})
        for row in wallet.get("result") or []:
            if row.get("asset_symbol") == "USD":
                equity = float(row.get("balance") or 0.0)
                available = float(row.get("available_balance") or 0.0)
                break
    except Exception as exc:  # noqa: BLE001 -- a missing balance must not raise
        log.warning("[strategy] wallet read failed (%s); treating as zero",
                    type(exc).__name__)
    return AccountSnapshot(
        equity_usd=equity, available_usd=available,
        open_positions=1 if position else 0,
        mark_price=mark, position_size=position)


def register_strategy_job(
    scheduler,
    *,
    forward_db_path: str,
    conn,
    client,
    risk_manager,
    executor,
    reconciler=None,
    strategy_enabled: bool,
    dry_run: bool = True,
    interval_seconds: int = DEFAULT_STRATEGY_INTERVAL_SEC,
    job_id: str = STRATEGY_JOB_ID,
) -> bool:
    """Register the periodic strategy evaluation. Idempotent.

    Returns True if this call registered it, False if it was already registered
    or the strategy is disabled -- a disabled strategy registers NO job at all,
    so the scheduler carries no work that can only decline to act.
    """
    if not strategy_enabled:
        log.info("[strategy] not registered (DEMO_STRATEGY_ENABLED=0)")
        return False
    if scheduler.has_job(job_id):
        log.info("[strategy] job %r already registered; not re-registering", job_id)
        return False

    def _job() -> None:
        try:
            result = evaluate_once(
                forward_db_path=forward_db_path, conn=conn, client=client,
                risk_manager=risk_manager, executor=executor,
                reconciler=reconciler, strategy_enabled=strategy_enabled,
                dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001 -- never raise into the scheduler
            log.error("[strategy] pass raised %s: %s", type(exc).__name__, exc)
            return
        if result.outcome not in (NO_SIGNAL, DUPLICATE):
            log.info("[strategy] pass: %s (%s)", result.outcome, result.detail)

    scheduler.register("strategy_evaluate", _job, trigger="interval",
                       job_id=job_id, seconds=interval_seconds)
    log.info("[strategy] registered evaluation every %ss (dry_run=%s)",
             interval_seconds, dry_run)
    return True
