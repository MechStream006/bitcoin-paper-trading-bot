"""SchedulerService — APScheduler wrapper for the BTC bot runtime.

Wraps BackgroundScheduler with:
  - duplicate-safe job registration
  - per-job execution accounting (runs, failures, duration, last success)
  - structured failure logging; exceptions never escape into APScheduler
  - health() reporting surfacing worst-job failure count
  - lifecycle-safe start / stop

Design rules
------------
- Jobs are thin orchestration triggers only.  Business logic lives in the
  called component (SnapshotWriter.snapshot, HeartbeatService.send, etc.).
- SchedulerService never owns persistence, WebSocket, or Telegram logic.
- SchedulerService never restarts components or contains strategy logic.

Dependency direction
--------------------
  scheduler.runner
    -> runtime.health   (produces HealthStatus)
    -> apscheduler      (BackgroundScheduler)

Concurrency contract
--------------------
  _lock guards _stats dict and JobStats fields.
  _lock is NEVER held while fn() executes — fn may block for seconds.
  APScheduler calls each job's wrapper in its own thread-pool thread.
  health() reads a shallow copy of stats so it never blocks job execution.
"""
from __future__ import annotations

import copy
import dataclasses
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from apscheduler.schedulers.background import BackgroundScheduler

from src.runtime.health import HealthStatus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-job statistics
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class JobStats:
    """Execution accounting for a single registered job.

    Instances are created by SchedulerService.register() and mutated by
    the job wrapper under _lock.  health() takes a shallow copy for reading.
    """
    name: str
    failure_threshold: int = 3
    total_runs: int = 0
    total_failures: int = 0
    consecutive_failures: int = 0
    last_attempt: Optional[float] = None   # monotonic seconds
    last_success: Optional[float] = None   # monotonic seconds
    last_duration_sec: Optional[float] = None
    interval_sec: Optional[float] = None   # interval-trigger period; None for cron


@dataclasses.dataclass(frozen=True)
class JobAge:
    """Read-only per-job liveness snapshot returned by job_ages().

    last_success_age: seconds since the job last succeeded, or None if it has
                      never succeeded.
    interval_sec:     the job's interval period, or None for cron jobs (which
                      have no fixed cadence and are not stall-escalated).
    """
    name: str
    last_success_age: Optional[float]
    interval_sec: Optional[float]


# ---------------------------------------------------------------------------
# SchedulerService
# ---------------------------------------------------------------------------

class SchedulerService:
    """APScheduler wrapper with execution accounting and health reporting.

    Lifecycle
    ---------
      svc = SchedulerService()
      svc.register("snapshot", writer.snapshot, trigger="interval", seconds=60)
      svc.register("heartbeat", hb.send,        trigger="interval", seconds=1800)
      svc.start()
      ...
      svc.stop()        # drains running jobs (wait=True by default)

    Note: BackgroundScheduler cannot be restarted once stopped.  Create a
    fresh SchedulerService if restart is needed (rare in a 24/7 bot).

    Args:
        failure_threshold:  Consecutive job failures before health() reports
                            unhealthy.  Does NOT stop the job — logging only.
        misfire_grace_time: Seconds past the scheduled time to still run a job
                            that was delayed (e.g. by a slow previous execution).
    """

    COMPONENT_NAME = "scheduler"

    def __init__(
        self,
        failure_threshold: int = 3,
        misfire_grace_time: int = 30,
    ) -> None:
        self._scheduler = BackgroundScheduler()
        self._failure_threshold = failure_threshold
        self._misfire_grace_time = misfire_grace_time
        self._stats: dict[str, JobStats] = {}
        self._lock = threading.Lock()

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    def start(self) -> None:
        """Start the APScheduler background thread.  Idempotent guard included."""
        if self._scheduler.running:
            logger.warning(
                "[scheduler] start() called while already running — ignoring"
            )
            return
        self._scheduler.start()
        logger.info(
            "[scheduler] started (APScheduler %s, %d job(s) registered)",
            _aps_version(), len(self._stats),
        )

    def stop(self, wait: bool = True) -> None:
        """Shut down APScheduler, optionally waiting for running jobs to finish.

        Args:
            wait: True (default) blocks until all executing jobs complete.
                  Pass False for an immediate shutdown (jobs may be interrupted).
        """
        if not self._scheduler.running:
            logger.debug("[scheduler] stop() called but not running — ignoring")
            return
        self._scheduler.shutdown(wait=wait)
        logger.info("[scheduler] stopped (wait=%s)", wait)

    # -----------------------------------------------------------------------
    # Job registration
    # -----------------------------------------------------------------------

    def has_job(self, job_id: str) -> bool:
        """Whether `job_id` is already registered.

        Lets a caller make registration idempotent without catching the
        ValueError that register() raises, which would be indistinguishable
        from a genuine trigger-argument error.
        """
        with self._lock:
            return job_id in self._stats

    def register(
        self,
        name: str,
        fn: Callable[[], None],
        trigger: str,
        job_id: Optional[str] = None,
        **trigger_kwargs,
    ) -> None:
        """Register a job.

        Args:
            name:             Human-readable label (logs, health output).
            fn:               Zero-argument callable; the actual work.
            trigger:          APScheduler trigger type: "interval" or "cron".
            job_id:           Unique key; defaults to `name`.  Used to detect
                              duplicate registrations.
            **trigger_kwargs: Forwarded to APScheduler add_job verbatim.
                              Interval: seconds=60 | hours=1 | ...
                              Cron:     hour=0 | minute="*/15" | ...

        Raises:
            ValueError: if job_id is already registered.
            Any APScheduler error if the trigger arguments are invalid.
        """
        jid = job_id or name

        # Capture the interval period (observability only) so job_ages() can
        # stall-escalate against each job's own cadence.  Cron jobs -> None.
        interval_sec: Optional[float] = None
        if trigger == "interval":
            secs = (
                float(trigger_kwargs.get("seconds", 0) or 0)
                + float(trigger_kwargs.get("minutes", 0) or 0) * 60.0
                + float(trigger_kwargs.get("hours", 0) or 0) * 3600.0
            )
            interval_sec = secs if secs > 0 else None

        # --- guard duplicate registration BEFORE touching APScheduler ---
        with self._lock:
            if jid in self._stats:
                raise ValueError(
                    f"[scheduler] job '{jid}' is already registered; "
                    f"pass a unique job_id= to register a second variant"
                )
            stats = JobStats(
                name=name, failure_threshold=self._failure_threshold,
                interval_sec=interval_sec,
            )
            self._stats[jid] = stats

        # --- add to APScheduler; rollback stats on failure ---
        wrapped = self._wrap(jid, fn, stats)
        try:
            self._scheduler.add_job(
                wrapped,
                trigger=trigger,
                id=jid,
                name=name,
                coalesce=True,          # skip missed runs instead of piling up
                max_instances=1,        # no overlapping executions
                misfire_grace_time=self._misfire_grace_time,
                **trigger_kwargs,
            )
        except Exception:
            with self._lock:
                self._stats.pop(jid, None)
            raise

        logger.info(
            "[scheduler] registered '%s' (trigger=%s %s)",
            jid, trigger, trigger_kwargs,
        )

    # -----------------------------------------------------------------------
    # Health reporting  (safe to call from any thread)
    # -----------------------------------------------------------------------

    def health(self) -> HealthStatus:
        """Return a HealthStatus snapshot.

        healthy=True  when scheduler is running AND no job has
                      consecutive_failures >= failure_threshold.
        fatal=False   always — the Supervisor decides escalation policy.
        """
        running = self._scheduler.running
        with self._lock:
            # Shallow copy is safe: all JobStats fields are primitives/None.
            stats_copy = {jid: copy.copy(s) for jid, s in self._stats.items()}

        worst_cf = max(
            (s.consecutive_failures for s in stats_copy.values()), default=0
        )
        healthy = running and worst_cf < self._failure_threshold

        parts = []
        now = time.monotonic()
        for jid, s in stats_copy.items():
            ok_age = (
                f"{now - s.last_success:.0f}s"
                if s.last_success is not None else "never"
            )
            parts.append(
                f"{jid}(runs={s.total_runs} fail={s.total_failures} "
                f"cf={s.consecutive_failures} last_ok={ok_age})"
            )

        details = (
            f"running={running} worst_cf={worst_cf} | "
            + (", ".join(parts) if parts else "no jobs")
        )
        return HealthStatus(
            name=self.COMPONENT_NAME,
            healthy=healthy,
            fatal=False,
            details=details,
            last_check_at=datetime.now(timezone.utc),
        )

    def job_ages(self) -> "dict[str, JobAge]":
        """Return per-job liveness: {job name -> JobAge}.  Read-only; any thread.

        Observability accessor only — takes a shallow copy of stats under _lock
        (never blocks job execution) and computes last-success ages against a
        single ``now``.  Does not mutate scheduler state.
        """
        now = time.monotonic()
        with self._lock:
            stats_copy = {jid: copy.copy(s) for jid, s in self._stats.items()}
        result: "dict[str, JobAge]" = {}
        for s in stats_copy.values():
            age = (now - s.last_success) if s.last_success is not None else None
            result[s.name] = JobAge(
                name=s.name, last_success_age=age, interval_sec=s.interval_sec
            )
        return result

    # -----------------------------------------------------------------------
    # Job wrapper
    # -----------------------------------------------------------------------

    def _wrap(self, job_id: str, fn: Callable, stats: JobStats) -> Callable:
        """Return a zero-argument callable that wraps fn with execution accounting.

        Execution flow
        --------------
        1. Record attempt timestamp.
        2. Call fn() — _lock is NOT held during execution.
        3a. SUCCESS: update last_success, reset consecutive_failures, record
            total_runs and duration under _lock.  Log at DEBUG.
        3b. FAILURE: update consecutive_failures, total_failures, duration
            under _lock.  Log at ERROR.  DO NOT re-raise — exceptions must
            never escape into APScheduler internals.

        The closure captures `lock` (not `self`) to avoid an implicit strong
        reference cycle between the wrapper and SchedulerService.
        """
        lock = self._lock  # direct ref to Lock object, not to self

        def _job() -> None:
            attempt_at = time.monotonic()
            try:
                fn()
                duration = time.monotonic() - attempt_at
                with lock:
                    stats.last_attempt = attempt_at
                    stats.last_success = time.monotonic()
                    stats.consecutive_failures = 0
                    stats.total_runs += 1
                    stats.last_duration_sec = duration
                    _runs = stats.total_runs
                logger.debug(
                    "[scheduler] '%s' ok in %.3fs (total_runs=%d)",
                    job_id, duration, _runs,
                )
            except Exception as exc:
                duration = time.monotonic() - attempt_at
                with lock:
                    stats.last_attempt = attempt_at
                    stats.consecutive_failures += 1
                    stats.total_failures += 1
                    stats.last_duration_sec = duration
                    _cf = stats.consecutive_failures
                    _tf = stats.total_failures
                logger.error(
                    "[scheduler] '%s' FAILED in %.3fs "
                    "(consecutive=%d total=%d): %s: %s",
                    job_id, duration, _cf, _tf,
                    type(exc).__name__, exc,
                )
                # Never re-raise: exceptions must not propagate into APScheduler.

        _job.__name__ = f"job_{job_id}"
        _job.__qualname__ = f"job_{job_id}"
        return _job


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _aps_version() -> str:
    try:
        import apscheduler
        return apscheduler.__version__
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Self-test / smoke  (no live network; exercises APScheduler integration)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # -----------------------------------------------------------------------
    # Test 1: normal job execution and accounting
    # -----------------------------------------------------------------------
    svc = SchedulerService(failure_threshold=3, misfire_grace_time=30)
    counter = {"n": 0}

    def bump() -> None:
        counter["n"] += 1

    svc.register("counter", bump, trigger="interval", seconds=1)
    svc.start()

    h0 = svc.health()
    print(f"[t1] immediately after start: {h0}")
    assert h0.healthy, f"expected healthy at start, got: {h0}"

    time.sleep(3.5)
    h1 = svc.health()
    print(f"[t1] after 3.5s:  {h1}  counter={counter['n']}")
    assert counter["n"] >= 3, f"expected >= 3 runs, got {counter['n']}"
    assert h1.healthy, f"expected healthy after clean runs: {h1}"

    svc.stop()
    print("[t1] normal execution test passed.")

    # -----------------------------------------------------------------------
    # Test 2: failure tracking degrades health
    # -----------------------------------------------------------------------
    svc2 = SchedulerService(failure_threshold=3, misfire_grace_time=30)

    def always_fails() -> None:
        raise ValueError("intentional test failure")

    svc2.register("failer", always_fails, trigger="interval", seconds=1)
    svc2.start()
    time.sleep(4.5)   # fires >= 4 times; failure_threshold=3 so health degrades

    h2 = svc2.health()
    print(f"[t2] after failures: {h2}")
    with svc2._lock:
        cf = svc2._stats["failer"].consecutive_failures
    assert cf >= 3, f"expected cf >= 3, got {cf}"
    assert not h2.healthy, f"expected unhealthy after {cf} failures: {h2}"

    svc2.stop()
    print("[t2] failure tracking test passed.")

    # -----------------------------------------------------------------------
    # Test 3: duplicate job registration raises ValueError
    # -----------------------------------------------------------------------
    svc3 = SchedulerService()
    svc3.register("job_a", lambda: None, trigger="interval", seconds=60)
    try:
        svc3.register("job_a", lambda: None, trigger="interval", seconds=120)
        print("ERROR: should have raised ValueError", file=sys.stderr)
        sys.exit(1)
    except ValueError as exc:
        print(f"[t3] duplicate correctly rejected: {exc}")
    with svc3._lock:
        assert len(svc3._stats) == 1, "stats should have exactly 1 entry"
    print("[t3] duplicate registration test passed.")

    # -----------------------------------------------------------------------
    # Test 4: _wrap isolation — exception never propagates; resets on success
    # -----------------------------------------------------------------------
    svc4 = SchedulerService()
    calls = {"n": 0}

    def intermittent() -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("fail on call 2")

    stats_obj = JobStats(name="intermittent", failure_threshold=3)
    with svc4._lock:
        svc4._stats["intermittent"] = stats_obj
    wrapped = svc4._wrap("intermittent", intermittent, stats_obj)

    wrapped()   # call 1 — success
    wrapped()   # call 2 — raises internally; must NOT propagate
    wrapped()   # call 3 — success; resets consecutive_failures

    assert stats_obj.total_runs == 2,        f"runs={stats_obj.total_runs}"
    assert stats_obj.total_failures == 1,    f"failures={stats_obj.total_failures}"
    assert stats_obj.consecutive_failures == 0, "cf should reset after success"
    assert stats_obj.last_duration_sec is not None
    print(
        f"[t4] wrap isolation: runs={stats_obj.total_runs} "
        f"failures={stats_obj.total_failures} cf={stats_obj.consecutive_failures}"
    )
    print("[t4] wrap isolation test passed.")

    # -----------------------------------------------------------------------
    # Test 5: job_id different from name; explicit id provided
    # -----------------------------------------------------------------------
    svc5 = SchedulerService()
    svc5.register(
        name="snapshot_v2",
        fn=lambda: None,
        trigger="interval",
        seconds=60,
        job_id="snapshot",
    )
    with svc5._lock:
        assert "snapshot" in svc5._stats
        assert svc5._stats["snapshot"].name == "snapshot_v2"
    print("[t5] explicit job_id test passed.")

    print("\nSchedulerService smoke test passed.")
