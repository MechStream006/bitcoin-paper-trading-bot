"""Scheduler registration for periodic reconciliation.

Kept in its own module, separate from `Reconciler`, for one structural reason:
**job ownership must not follow component lifetime.** `SchedulerService.register`
raises `ValueError` on a duplicate `job_id`, so if `Reconciler.start()` registered
its own job, a single supervisor-initiated restart would either crash the restart
or silently leave two reconciliation jobs running against one connection.

Registration happens once, at wiring time. Restarting the reconciler re-runs boot
reconciliation and touches no scheduler state.

Nothing here is invoked automatically — `register_reconciliation_jobs()` has no
caller in the collector runtime. Wiring it into `lifecycle.py` is a separate,
gated decision.
"""
from __future__ import annotations

import json
import logging
import pathlib
from datetime import datetime, timezone

log = logging.getLogger(__name__)

RECONCILE_JOB_ID = "execution_reconcile"

#: Bounds how long an UNKNOWN order can sit unresolved without anyone looking.
#: 60s is one snapshot cadence — frequent enough that an ambiguous submit is
#: reconciled within a minute, infrequent enough to be three authenticated GETs
#: per minute against Delta.
DEFAULT_RECONCILE_INTERVAL_SEC = 60

#: Consecutive reconciliation EXCEPTIONS before trading fails closed.
#:
#: 3 is not chosen here -- it is the threshold every other scheduled component
#: in this runtime already uses (chain_refresher, snapshot_writer,
#: scheduler.runner, and ws_manager's stuck-state test), so the runtime has one
#: number to reason about rather than five. At the 60s cadence above it bounds
#: blind operation at ~3 minutes.
#:
#: Only exceptions are counted. A pass that COMPLETES and finds something wrong
#: has already halted itself inside reconcile() -- an unreadable exchange, a
#: position mismatch, an unprotected position all halt on the first pass. What
#: this counts is the residual class: a pass that never reached those checks.
MAX_CONSECUTIVE_RECONCILE_FAILURES = 3



def write_execution_status(reconciler, status_path) -> None:
    """Persist reconciler.health() so an out-of-process checker can see a halt.

    A halt lives only in the Reconciler instance and in journald. The Telegram
    heartbeat sees it (the reconciler is bound into its degraded-component
    scan), but `scripts/healthcheck.py` runs in a separate process and had no
    way to observe it at all -- so its verdict read OK while the execution
    stack had been halted for days. This sidecar is that missing surface, and
    follows the `.offhost-status.json` precedent already used for backups.

    Best-effort by construction: a status-write failure must never affect
    reconciliation, so every exception is swallowed and logged.
    """
    if status_path is None:
        return
    try:
        # Read HealthStatus by its real field names. Defensive getattr was
        # actively harmful here: `ok` resolved to the HealthStatus.ok
        # CONSTRUCTOR -- a truthy function object -- so the payload reported
        # "ok": true beside "fatal": true on a halted stack, and `component`
        # and `detail` silently fell back to their defaults because the fields
        # are named `name` and `details`.
        h = reconciler.health()
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "component": h.name,
            "healthy": bool(h.healthy),
            "fatal": bool(h.fatal),
            "detail": str(h.details or ""),
            "trading_permitted": bool(reconciler.trading_permitted),
            "halt_reason": str(getattr(reconciler, "halt_reason", "") or ""),
        }
        path = pathlib.Path(status_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(path)
    except Exception as exc:  # noqa: BLE001 -- never disturb reconciliation
        log.warning("[reconcile] could not write execution status: %s: %s",
                    type(exc).__name__, exc)


def register_reconciliation_jobs(
    scheduler,
    reconciler,
    *,
    interval_seconds: int = DEFAULT_RECONCILE_INTERVAL_SEC,
    job_id: str = RECONCILE_JOB_ID,
    status_path=None,
) -> bool:
    """Register the periodic reconciliation job. Idempotent.

    Returns True if this call registered the job, False if it was already
    registered. Never raises on a duplicate — that is the whole point.
    """
    if scheduler.has_job(job_id):
        log.info("[reconcile] job %r already registered; not re-registering", job_id)
        return False

    scheduler.register(
        "execution_reconcile",
        _make_periodic_reconcile(reconciler, status_path=status_path),
        trigger="interval",
        job_id=job_id,
        seconds=interval_seconds,
    )
    log.info("[reconcile] registered periodic reconciliation every %ss", interval_seconds)
    return True


def _make_periodic_reconcile(reconciler, *,
                             failure_threshold: int = MAX_CONSECUTIVE_RECONCILE_FAILURES,
                             status_path=None):
    """Wrap `reconcile()` for scheduled execution.

    Three deliberate properties:

    1. **It never clears a halt.** Only `clear_halt_if_clean()` does, and that is
       operator-initiated. A halted stack does not recover because a timer fired.
    2. **It never raises into the scheduler.** A reconciliation that cannot query
       the exchange has already halted itself; re-raising would additionally trip
       the scheduler's failure counter and confuse two independent signals.
    3. **It fails closed on PERSISTENT failure.** Property 2 was load-bearing for
       a claim that turned out to be incomplete: it holds for failures reconcile()
       DETECTS, but not for exceptions that escape it. Those reach no _halt() call
       site, so the pass aborts -- before fill ingestion, the position comparison
       and the protection invariant -- while trading_permitted stays True. Seen in
       production at Gate 5C.17: 4 consecutive failed passes, halted=0 throughout,
       reconciliation blind and trading still permitted.

       One failure is tolerated as transient. `failure_threshold` consecutive
       failures are not: the counter latches a halt, which closes the trading
       gate through the path RiskManager already consults.

    The counter resets on any completed pass. Once the halt latches, recovery is
    operator-initiated via clear_halt_if_clean() -- this does NOT self-heal, for
    the reason that function's own docstring gives.
    """
    consecutive_failures = 0

    def _job() -> None:
        nonlocal consecutive_failures
        if reconciler.halted:
            log.warning("[reconcile] periodic pass skipped: halted (%s)",
                        reconciler.halt_reason)
            write_execution_status(reconciler, status_path)
            return
        try:
            result = reconciler.reconcile()
        except Exception as exc:
            consecutive_failures += 1
            log.error("[reconcile] periodic pass failed (consecutive=%d/%d): %s: %s",
                      consecutive_failures, failure_threshold,
                      type(exc).__name__, exc)
            if consecutive_failures >= failure_threshold:
                reconciler.halt(
                    f"reconciliation failed {consecutive_failures} consecutive "
                    f"times; last: {type(exc).__name__}: {exc}")
            write_execution_status(reconciler, status_path)
            return
        consecutive_failures = 0
        if not result.clean:
            log.critical("[reconcile] periodic pass NOT clean: %s",
                         result.halt_reason or "see findings")
        write_execution_status(reconciler, status_path)

    return _job
