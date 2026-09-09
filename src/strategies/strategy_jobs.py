"""Strategy evaluation job -- the missing link between signal and execution.

``strategies/bridge.py`` has been complete and tested since the execution stack
landed, but nothing in ``src/`` ever imported it: no scheduled job called
``build_intent()``, so ``DEMO_STRATEGY_ENABLED`` gated a code path that was
unreachable either way. This module is that caller, and nothing more.

WHAT IT DOES
------------
One pass, on an interval::

    bridge.mirrorable_entry()  ->  claim_forward_entry()   [atomic]
                               ->  bridge.build_intent()
                               ->  RiskManager.validate()
                               ->  [dry-run: STOP]  |  executor.submit()
                                                    -> [ACKED only]
                                                       capture_signal_context

and, on the same pass, the governed exit::

    bridge.forward_exit()      ->  [trade_exits row exists]
                               ->  reduce-only exit  |  CLOSED_BY_RISK

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

WHAT THE TRIGGER IS (ADR-0003, P-MIRROR)
----------------------------------------
A forward ENTRY EVENT -- a new `trade_entries` row -- and nothing else. The
live path mirrors the governed 7-day strategy; it does not originate entries.
`bridge.latest_direction()` is retained as a diagnostic but is NOT the trigger:
it reports whether a forward trade is *held*, which is nearly always true and
is the exact complement of the collector's own entry condition.

An entry is mirrorable only inside `bridge.MIRROR_WINDOW` (1 hour) after it
becomes readable. Past that it is permanently NOT_MIRRORED, because a position
opened mid-hold has a different entry price and a truncated horizon and is not
comparable at the entry level.

IDEMPOTENCY AND CONCURRENCY
---------------------------
Two mechanisms, both keyed on the forward entry rather than on wall-clock time:

1. `storage.claim_forward_entry()` is an atomic INSERT against a PRIMARY KEY
   inside BEGIN IMMEDIATE. Of two passes racing one entry exactly one proceeds,
   before any network call. Check-then-act on `orders` cannot guarantee that.
2. `bridge.client_order_id(signal, ENTRY_DATE, side, 1)` is deterministic in
   the mirrored event, so a restart, a retry and UTC midnight all re-derive the
   same id. The UTC day no longer appears anywhere in the entry path.

RISK OVERLAY (ADR-LE-5)
-----------------------
The protective stop is unchanged and remains an independent live risk overlay.
A stop exit is a RISK exit, not a strategy exit: it closes the live position,
never the forward trade, and the mirror row keeps accruing the governed
`forward_exit_*` outcome afterwards so both outcomes exist for one entry.
No re-entry is ever made after a risk exit while the forward entry is open.

WHAT IT DOES NOT TOUCH
----------------------
Reconciliation, the kill switch, risk limits, sizing, protective orders and
governance are all unchanged. The job consults `reconciler.trading_permitted`
but never clears a halt, and it never places a protective order -- protection
is established by the reconciliation pass, exactly as before.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, replace
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
    service_started_at: datetime | None = None,
) -> EvaluationResult:
    """One forward-entry -> intent -> risk -> (dry-run stop | submit) pass.

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

    # ADR-0003 (P-MIRROR). The trigger is a forward ENTRY EVENT, not an open
    # forward trade. `latest_direction()` is retained as a diagnostic but is no
    # longer wired here: it reports whether something is held, which is nearly
    # always true and is the complement of the collector's own entry condition.
    try:
        entries = bridge.read_forward_entries(forward_db_path, bridge.SIGNAL_ID)
        considered = storage.mirrored_entry_ids(conn)
    except Exception as exc:  # noqa: BLE001 -- never raise into the scheduler
        log.error("[strategy] forward read failed: %s: %s", type(exc).__name__, exc)
        return EvaluationResult(ERROR, f"forward read: {type(exc).__name__}: {exc}")

    # A claim older than the mirror window has no owner: the process that made
    # it died before releasing or promoting it. Sweep first, then re-read what
    # counts as considered, so a swept entry can still be entered if its window
    # is open and can be classified NOT_MIRRORED if it is not.
    freed = storage.release_stale_claims(
        conn, (now - bridge.MIRROR_WINDOW).isoformat())
    if freed:
        log.warning("[strategy] released %d stale mirror claim(s): %s",
                    len(freed), freed)
        considered = storage.mirrored_entry_ids(conn)

    # Close out anything whose 1h mirror window has passed, so a forward trade
    # with no live counterpart is visibly absent rather than silently missing.
    _record_expired(conn, entries, considered, now, service_started_at)

    entry = bridge.mirrorable_entry(entries, now)
    if entry is None:
        return EvaluationResult(NO_SIGNAL, "no forward entry inside its mirror window")
    if entry.entry_id in considered:
        return EvaluationResult(DUPLICATE,
                                f"forward entry {entry.entry_id} already mirrored")

    direction = entry.direction
    side = "buy" if direction == 1 else "sell"
    # The id is deterministic in the forward ENTRY, not in the submission day,
    # so it survives a restart, a retry and UTC midnight unchanged (ADR-LE-2).
    coid = bridge.client_order_id(bridge.SIGNAL_ID, entry.entry_date, side, 1)
    day = entry.entry_date

    # Atomic claim. Two passes racing this entry cannot both reach the exchange:
    # the loser fails the PRIMARY KEY insert and stops here, before any network
    # call. A check-then-act read of `orders` could not guarantee that.
    try:
        if not storage.claim_forward_entry(
                conn, forward_entry_id=entry.entry_id, signal_id=entry.signal_id,
                entry_date=entry.entry_date, direction=direction,
                planned_exit_date=entry.planned_exit_date,
                readable_ts=entry.readable_ts.isoformat(),
                now_ts=now.isoformat()):
            return EvaluationResult(DUPLICATE,
                                    f"forward entry {entry.entry_id} already claimed")
        if storage.get_order(conn, coid) is not None:
            # The claim is already ours at this point. Returning without
            # releasing it would strand the entry in PENDING, which no other
            # path can recover from -- see release_stale_claims().
            _release(conn, entry.entry_id)
            return EvaluationResult(DUPLICATE, f"{coid} already exists")
    except Exception as exc:  # noqa: BLE001
        log.error("[strategy] claim failed: %s: %s", type(exc).__name__, exc)
        _release(conn, entry.entry_id)
        return EvaluationResult(ERROR, f"claim: {type(exc).__name__}: {exc}")

    try:
        quote = _quote(client, now)
    except Exception as exc:  # noqa: BLE001
        log.error("[strategy] quote unavailable: %s: %s", type(exc).__name__, exc)
        _release(conn, entry.entry_id)
        return EvaluationResult(QUOTE_UNAVAILABLE, f"{type(exc).__name__}: {exc}")
    if quote is None:
        _release(conn, entry.entry_id)
        return EvaluationResult(QUOTE_UNAVAILABLE, "no usable BTCUSD bid/ask/mark")
    mark, bid, ask = quote

    try:
        intent = bridge.build_intent(
            direction=direction, mark_price=mark, enabled=strategy_enabled,
            best_bid=bid, best_ask=ask, utc_day=day, seq=1)
    except bridge.StrategyDisabled as exc:
        _release(conn, entry.entry_id)
        return EvaluationResult(DISABLED, str(exc))
    except Exception as exc:  # noqa: BLE001
        log.error("[strategy] build_intent failed: %s: %s", type(exc).__name__, exc)
        _release(conn, entry.entry_id)
        return EvaluationResult(ERROR, f"build_intent: {type(exc).__name__}: {exc}")

    try:
        account = _account(conn, client, mark)
        decision = risk_manager.validate(intent, account, now=now)
    except Exception as exc:  # noqa: BLE001
        log.error("[strategy] risk evaluation failed: %s: %s", type(exc).__name__, exc)
        _release(conn, entry.entry_id)
        return EvaluationResult(ERROR, f"risk: {type(exc).__name__}: {exc}",
                                intent=intent)

    if not decision.approved:
        log.info("[strategy] %s %s x%d @ %.1f REJECTED: %s",
                 intent.side, intent.symbol, intent.size, intent.limit_price,
                 decision.reason)
        _release(conn, entry.entry_id)
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
        _release(conn, entry.entry_id)
        return EvaluationResult(DRY_RUN, "approved; dry_run=True", intent, decision)

    try:
        outcome = executor.submit(intent, account, now=now)
    except Exception as exc:  # noqa: BLE001
        log.error("[strategy] submit failed: %s: %s", type(exc).__name__, exc)
        _release(conn, entry.entry_id)
        return EvaluationResult(ERROR, f"submit: {type(exc).__name__}: {exc}",
                                intent, decision)

    state = _submitted_state(outcome)
    if state != ACCEPTED_STATE:
        # Reported as what it was. The order row the executor already wrote is
        # the record of the attempt; nothing here retries, cancels or otherwise
        # acts on it -- reconciliation owns an ambiguous order, exactly as before.
        log.error("[strategy] NOT ACKED %s: executor returned %s",
                  intent.client_order_id, state)
        _release(conn, entry.entry_id)
        return EvaluationResult(NOT_ACKED, f"executor returned {state}",
                                intent, decision, order_state=state)

    log.warning("[strategy] SUBMITTED %s", intent.client_order_id)
    try:
        storage.update_mirror(conn, entry.entry_id, now_ts=now.isoformat(),
                              state="ENTERED",
                              entry_client_order_id=intent.client_order_id)
    except Exception as exc:  # noqa: BLE001 -- the order is already live
        log.error("[strategy] could not mark mirror ENTERED for %s: %s: %s",
                  entry.entry_id, type(exc).__name__, exc)

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
    #
    # `now` is the decision instant, already held here, and bounds the signal
    # fields to what had become available by then. It is passed rather than
    # re-derived, and no signal_log read is added to this path: the bound is
    # applied inside the capture, against the same source it already reads.
    try:
        bridge.capture_signal_context(
            conn, intent.client_order_id, forward_db_path, bridge.SIGNAL_ID,
            direction=_intent_direction(intent), decision_ts=now)
    except Exception as exc:  # noqa: BLE001
        log.warning("[strategy] signal context not captured for %s: %s: %s",
                    intent.client_order_id, type(exc).__name__, exc)

    # The decision-time feature row, keyed on the same client_order_id.
    #
    # ACKED only, and this is the enforcement point: DRY_RUN, a pre-submit risk
    # rejection and every earlier return leave the function above, so none of
    # them reaches here and none produces a research row. That is the recorded
    # policy. Admitting them would require LABEL_CONTRACT's linkage clause --
    # "decision_id == orders.client_order_id" -- to change, which is an
    # operator act; an ACKED decision satisfies it because the executor writes
    # the order row write-ahead at SUBMITTED, before the request leaves.
    #
    # `now` again, not a fresh clock: capture necessarily runs after an HTTP
    # round-trip, but the row must describe what was knowable when the decision
    # was taken, and `build_row` takes the instant from its caller.
    #
    # Best-effort on the same terms as the capture above, and for the same
    # reason: the order is already live.
    try:
        bridge.capture_decision_features(
            conn, intent.client_order_id, forward_db_path, decision_ts=now)
    except Exception as exc:  # noqa: BLE001
        log.warning("[strategy] decision features not captured for %s: %s: %s",
                    intent.client_order_id, type(exc).__name__, exc)

    return EvaluationResult(SUBMITTED, "submitted", intent, decision,
                            submitted=True, order_state=state)



# --------------------------------------------------------------------- mirror

#: Outcomes of the exit pass, kept distinct from the entry outcomes above.
NO_EXIT = "no_exit"                   # nothing held, or the hold is not over
EXIT_SUBMITTED = "exit_submitted"     # the governed horizon exit went out
RISK_EXITED = "risk_exited"           # the overlay closed it first (ADR-LE-5)


def _release(conn, forward_entry_id: int) -> None:
    """Undo an unconsummated claim so the entry can be retried in-window."""
    try:
        storage.release_forward_entry(conn, forward_entry_id)
    except Exception as exc:  # noqa: BLE001 -- never raise into the scheduler
        log.warning("[strategy] could not release claim %s: %s: %s",
                    forward_entry_id, type(exc).__name__, exc)


def _record_expired(conn, entries, considered, now, service_started_at) -> None:
    """Permanently mark entries whose 1h mirror window has closed.

    `cold_start` when the entry was already readable before this process
    started -- it was never mirrorable by this process. `missed` when it became
    readable while this process was running and was not acted on. The first
    classification written stands; the row is never rewritten, which is what
    keeps the distinction stable across a restart.
    """
    started = service_started_at or now
    for e in bridge.expired_entries(entries, now, considered):
        reason = "cold_start" if e.readable_ts < started else "missed"
        try:
            storage.record_not_mirrored(
                conn, forward_entry_id=e.entry_id, signal_id=e.signal_id,
                entry_date=e.entry_date, direction=e.direction,
                planned_exit_date=e.planned_exit_date,
                readable_ts=e.readable_ts.isoformat(), reason=reason,
                now_ts=now.isoformat())
            log.info("[strategy] forward entry %s (%s) NOT_MIRRORED: %s",
                     e.entry_id, e.entry_date, reason)
        except Exception as exc:  # noqa: BLE001
            log.warning("[strategy] could not record NOT_MIRRORED for %s: %s: %s",
                        e.entry_id, type(exc).__name__, exc)


def _entry_filled(conn, entry_client_order_id) -> bool:
    """Did the mirrored entry order actually produce a fill?

    Joined through `orders.delta_order_id` because `order_fills.client_order_id`
    is NULL on every row the runtime has written.
    """
    if not entry_client_order_id:
        return False
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM order_fills f JOIN orders o "
            " ON o.delta_order_id = f.delta_order_id "
            "WHERE o.client_order_id = ?", (entry_client_order_id,)).fetchone()
        return bool(row and row[0])
    except Exception:  # noqa: BLE001
        return False


def _risk_exit_cause(conn, mirror) -> str:
    """Why a mirrored position closed without a horizon exit from us.

    `risk_stop` when a protective order filled after the entry; `external`
    otherwise (a reconciler action, a manual close, or an exchange event).
    Both are risk-overlay exits under ADR-LE-5; neither is a strategy exit.
    """
    coid = mirror.get("entry_client_order_id")
    if not coid:
        return "external"
    try:
        # Joined, not inferred: the mirrored entry order -> its fill -> the
        # position that fill opened (positions.entry_ts is written from the
        # entry fill's processed_ts) -> the protective stop registered against
        # that position id -> whether that stop's order actually filled.
        #
        # The previous form counted ANY filled protection order placed after
        # the claim. That is only unambiguous because MAX_OPEN_POSITIONS is 1,
        # which is a risk bound and not a join key: raise it, or run two
        # strategies, and a stop belonging to another position would be
        # attributed here. exit_cause is what keeps the ADR-LE-5 horizon and
        # overlay outcomes apart, so it must be keyed, not coincidental.
        row = conn.execute(
            "SELECT COUNT(*) FROM protective_stops ps "
            "JOIN orders so ON so.client_order_id = ps.client_order_id "
            "JOIN positions pos ON pos.id = ps.position_id "
            "JOIN order_fills ef ON ef.processed_ts = pos.entry_ts "
            "JOIN orders eo ON eo.delta_order_id = ef.delta_order_id "
            "WHERE eo.client_order_id = ? AND so.status = 'filled'",
            (coid,)).fetchone()
        return "risk_stop" if row and row[0] else "external"
    except Exception:  # noqa: BLE001
        return "external"


def evaluate_exit_once(*, forward_db_path: str, conn, client, risk_manager,
                       executor, reconciler=None, strategy_enabled: bool,
                       dry_run: bool = True,
                       now: datetime | None = None) -> EvaluationResult:
    """One governed-horizon exit pass for the mirrored position, if any.

    ADR-0003: the exit trigger is the appearance of the forward `trade_exits`
    row, which the collector writes in the same transaction that sets
    `trade_entries.open = 0`. There is deliberately NO live horizon timer -- a
    timer would drift from the governed convention during a collector outage
    and would amount to inventing a second exit policy.

    A position closed earlier by the risk overlay is recorded as
    CLOSED_BY_RISK. Its forward exit is still recorded when the collector
    produces it, with no order submitted, so the strategy-horizon outcome and
    the live-overlay outcome both exist for the same entry (ADR-LE-5).
    """
    now = now or datetime.now(timezone.utc)
    if not strategy_enabled:
        return EvaluationResult(DISABLED, "DEMO_STRATEGY_ENABLED=0")

    try:
        outstanding = [m for m in storage.list_mirrors(conn)
                       if m["state"] in ("ENTERED", "EXIT_SUBMITTED",
                                         "CLOSED_BY_RISK")]
    except Exception as exc:  # noqa: BLE001
        log.error("[strategy] mirror read failed: %s: %s", type(exc).__name__, exc)
        return EvaluationResult(ERROR, f"mirror read: {type(exc).__name__}: {exc}")
    if not outstanding:
        return EvaluationResult(NO_EXIT, "no mirrored position outstanding")

    result = EvaluationResult(NO_EXIT, "nothing to do")
    for m in outstanding:
        fid = int(m["forward_entry_id"])
        fx = bridge.forward_exit(forward_db_path, fid)

        # Record the governed outcome whenever it appears, in EVERY state --
        # a stopped-out trade still earns its strategy_horizon_outcome.
        if fx is not None and m.get("forward_exit_date") is None:
            try:
                storage.update_mirror(
                    conn, fid, now_ts=now.isoformat(),
                    forward_exit_date=fx["exit_date"],
                    forward_exit_price=fx["exit_close_price"])
            except Exception as exc:  # noqa: BLE001
                log.warning("[strategy] could not record forward exit %s: %s",
                            fid, exc)

        if m["state"] == "CLOSED_BY_RISK":
            continue                   # overlay closed it; never re-enter

        try:
            flat = fills_mod.position_size(conn) == 0
        except Exception:  # noqa: BLE001
            flat = False

        # A position that never opened cannot have been closed. Between the ACK
        # and the fill being reconciled the book is legitimately flat, and
        # reading that as a risk exit would retire every mirror seconds after
        # entry. Require evidence the entry actually filled first.
        if not _entry_filled(conn, m.get("entry_client_order_id")):
            continue

        if m["state"] == "ENTERED" and flat:
            cause = _risk_exit_cause(conn, m)
            storage.update_mirror(conn, fid, now_ts=now.isoformat(),
                                  state="CLOSED_BY_RISK", exit_cause=cause,
                                  live_exit_ts=now.isoformat())
            log.warning("[strategy] mirror %s closed by risk overlay (%s)",
                        fid, cause)
            result = EvaluationResult(RISK_EXITED, f"{fid}: {cause}")
            continue

        if fx is None:
            continue                   # still inside the governed hold

        if m["state"] == "EXIT_SUBMITTED":
            if flat:
                # Submitting the horizon exit does not make it the closer. The
                # stop is a live overlay and stays armed while that exit rests,
                # so it can fill first -- and attributing that to the horizon
                # would merge the two outcomes ADR-LE-5 requires kept apart.
                # Asked of the same keyed evidence the ENTERED branch uses.
                cause = _risk_exit_cause(conn, m)
                if cause == "risk_stop":
                    storage.update_mirror(conn, fid, now_ts=now.isoformat(),
                                          state="CLOSED_BY_RISK",
                                          exit_cause=cause,
                                          live_exit_ts=now.isoformat())
                    log.warning("[strategy] mirror %s closed by risk overlay "
                                "while the horizon exit was resting (%s)",
                                fid, cause)
                    result = EvaluationResult(RISK_EXITED, f"{fid}: {cause}")
                else:
                    storage.update_mirror(conn, fid, now_ts=now.isoformat(),
                                          state="CLOSED_AT_HORIZON",
                                          exit_cause="horizon",
                                          live_exit_ts=now.isoformat())
                    result = EvaluationResult(EXIT_SUBMITTED,
                                              f"{fid}: closed at horizon")
            continue

        if reconciler is not None and not reconciler.trading_permitted:
            result = EvaluationResult(NOT_PERMITTED, "reconciler: exit deferred")
            continue
        if dry_run:
            result = EvaluationResult(DRY_RUN, f"{fid}: horizon exit withheld")
            continue

        quote = _quote(client, now)
        if quote is None:
            result = EvaluationResult(QUOTE_UNAVAILABLE, "no usable book for exit")
            continue
        mark, bid, ask = quote
        try:
            intent = bridge.build_intent(
                direction=-int(m["direction"]), mark_price=mark,
                enabled=strategy_enabled, best_bid=bid, best_ask=ask,
                utc_day=str(m["entry_date"]), seq=2)
            intent = replace(intent,
                             client_order_id="x" + intent.client_order_id[:31],
                             intent_kind="exit", stop_price=None)
            account = _account(conn, client, mark)
            outcome = executor.submit(intent, account, now=now)
        except Exception as exc:  # noqa: BLE001
            log.error("[strategy] horizon exit failed for %s: %s: %s",
                      fid, type(exc).__name__, exc)
            result = EvaluationResult(ERROR, f"exit: {type(exc).__name__}: {exc}")
            continue
        state = _submitted_state(outcome)
        if state != ACCEPTED_STATE:
            result = EvaluationResult(NOT_ACKED, f"exit returned {state}",
                                      order_state=state)
            continue
        storage.update_mirror(conn, fid, now_ts=now.isoformat(),
                              state="EXIT_SUBMITTED",
                              exit_client_order_id=intent.client_order_id)
        result = EvaluationResult(EXIT_SUBMITTED,
                                  f"{fid}: horizon exit submitted",
                                  intent=intent, order_state=state)
    return result


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


#: The mark-price cadence budget from CLAUDE.md sec.13 ("v2/ticker mark_price
#: cadence ~3s, design budget 3-5s"). That budget describes how often a NEW
#: value is published, not how old the newest one is when sampled.
MARK_CADENCE_BUDGET_S = 5.0

#: Reject a quote older than two cadence budgets. One is not enough: sampling a
#: 3s-cadence value at an arbitrary instant costs up to a full cadence before
#: the REST round-trip is added. Measured on production 2026-09-08 over 8
#: consecutive GET /v2/tickers/BTCUSD calls, `timestamp` age was min 1.08s /
#: median 3.81s / max 5.99s -- so a literal 5s bound would have rejected live,
#: healthy data. Ten seconds still catches a genuine publication stall, which
#: is what this guard is for.
QUOTE_MAX_AGE_S = 2 * MARK_CADENCE_BUDGET_S


def _quote(client, now: datetime | None = None
           ) -> tuple[float, float, float] | None:
    """(mark, best_bid, best_ask) for BTCUSD, or None if the book is unusable.

    Delta returns every numeric as a string (CLAUDE.md sec.11), hence the casts.

    Rejects a stale book. Freshness is taken from `timestamp` (microseconds
    since epoch) and NEVER from the sibling `time` field: on production
    2026-09-08 `time` was frozen at 2026-09-07T16:45:24Z across 8 consecutive
    calls while `timestamp` advanced normally -- roughly 24h stale, and reading
    it would reject every quote forever. A ticker carrying no usable timestamp
    is refused rather than trusted, because an unverifiable age is exactly the
    case this guard exists for.
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

    now = now or datetime.now(timezone.utc)
    try:
        published = datetime.fromtimestamp(
            float(t.get("timestamp")) / 1_000_000.0, tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        log.warning("[strategy] ticker carries no usable timestamp; refusing quote")
        return None
    age = (now - published).total_seconds()
    if age > QUOTE_MAX_AGE_S:
        log.warning("[strategy] stale BTCUSD quote refused: %.1fs old "
                    "(max %.1fs)", age, QUOTE_MAX_AGE_S)
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

    # The instant this process began mirroring. A forward entry readable before
    # it was never mirrorable by this process, which is what separates
    # `cold_start` from `missed`.
    service_started_at = datetime.now(timezone.utc)

    def _job() -> None:
        common = dict(forward_db_path=forward_db_path, conn=conn, client=client,
                      risk_manager=risk_manager, executor=executor,
                      reconciler=reconciler, strategy_enabled=strategy_enabled,
                      dry_run=dry_run)
        # Exit first: a horizon exit frees the single position slot that an
        # entry in the same pass would otherwise be refused for.
        try:
            ex = evaluate_exit_once(**common)
            if ex.outcome not in (NO_EXIT, DISABLED):
                log.info("[strategy] exit pass: %s (%s)", ex.outcome, ex.detail)
        except Exception as exc:  # noqa: BLE001 -- never raise into the scheduler
            log.error("[strategy] exit pass raised %s: %s", type(exc).__name__, exc)
        try:
            result = evaluate_once(service_started_at=service_started_at, **common)
        except Exception as exc:  # noqa: BLE001
            log.error("[strategy] pass raised %s: %s", type(exc).__name__, exc)
            return
        if result.outcome not in (NO_SIGNAL, DUPLICATE):
            log.info("[strategy] pass: %s (%s)", result.outcome, result.detail)

    scheduler.register("strategy_evaluate", _job, trigger="interval",
                       job_id=job_id, seconds=interval_seconds)
    log.info("[strategy] registered evaluation every %ss (dry_run=%s)",
             interval_seconds, dry_run)
    return True
