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

import logging

log = logging.getLogger(__name__)

RECONCILE_JOB_ID = "execution_reconcile"

#: Bounds how long an UNKNOWN order can sit unresolved without anyone looking.
#: 60s is one snapshot cadence — frequent enough that an ambiguous submit is
#: reconciled within a minute, infrequent enough to be three authenticated GETs
#: per minute against Delta.
DEFAULT_RECONCILE_INTERVAL_SEC = 60


def register_reconciliation_jobs(
    scheduler,
    reconciler,
    *,
    interval_seconds: int = DEFAULT_RECONCILE_INTERVAL_SEC,
    job_id: str = RECONCILE_JOB_ID,
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
        _make_periodic_reconcile(reconciler),
        trigger="interval",
        job_id=job_id,
        seconds=interval_seconds,
    )
    log.info("[reconcile] registered periodic reconciliation every %ss", interval_seconds)
    return True


def _make_periodic_reconcile(reconciler):
    """Wrap `reconcile()` for scheduled execution.

    Two deliberate properties:

    1. **It never clears a halt.** Only `clear_halt_if_clean()` does, and that is
       operator-initiated. A halted stack does not recover because a timer fired.
    2. **It never raises into the scheduler.** A reconciliation that cannot query
       the exchange has already halted itself; re-raising would additionally trip
       the scheduler's failure counter and confuse two independent signals.
    """

    def _job() -> None:
        if reconciler.halted:
            log.warning("[reconcile] periodic pass skipped: halted (%s)",
                        reconciler.halt_reason)
            return
        try:
            result = reconciler.reconcile()
        except Exception as exc:
            log.error("[reconcile] periodic pass failed: %s: %s",
                      type(exc).__name__, exc)
            return
        if not result.clean:
            log.critical("[reconcile] periodic pass NOT clean: %s",
                         result.halt_reason or "see findings")

    return _job
