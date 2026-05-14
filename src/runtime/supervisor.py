"""Supervisor — runtime health monitoring and restart-policy authority.

The Supervisor is the single entity that decides what to do when a
component reports unhealthy.  It never performs business logic itself.

Responsibilities
----------------
- Poll all registered components for HealthStatus on a configurable cadence.
- Log state transitions (healthy <-> unhealthy) without repeating for stable states.
- Restart unhealthy, restartable components via stop() -> start().
- Detect restart storms (too many restarts in a rolling window) and escalate.
- Escalate fatal components (health.fatal=True) to on_fatal callback.
- Expose runtime summary and aggregated health for external observability.

What the Supervisor does NOT do
---------------------------------
- Does not own any scheduler, WS, or Telegram logic.
- Does not manipulate MarketState, SQLite, or strategy state.
- Does not implement component-specific recovery hacks.
- Does not start or stop components at boot — that is the Runtime's job (Step 9).

Design: Components report reality.  Supervisor decides policy.

Restart policy
--------------
  - On unhealthy: stop() then start() (outside lock).
  - Timestamps tracked per component in a rolling deque.
  - After max_restarts_per_window restarts in restart_window_sec: storm escalation.
  - Fatal components (health.fatal=True) skip restart and escalate immediately.
  - Escalation is one-shot per component (fatal_escalated / storm_escalated flags).

Concurrency contract
--------------------
  run() blocks the calling thread; request_shutdown() and health() are safe
  from any thread.  _lock is NEVER held during component.health(),
  component.stop(), or component.start() calls.  All _ComponentRecord fields
  are written under _lock; summary() reads them under _lock.
"""
from __future__ import annotations

import collections
import enum
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, List, Optional

from src.runtime.health import HealthStatus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Supervisor state enum
# ---------------------------------------------------------------------------

class SupervisorState(enum.Enum):
    """Lifecycle states of the Supervisor.

    Transitions:
        IDLE -> RUNNING       (run() called)
        RUNNING -> SHUTTING_DOWN  (request_shutdown() called)
        RUNNING -> STOPPED        (loop exits normally or on exception)
        SHUTTING_DOWN -> STOPPED  (loop drains and exits)
    """
    IDLE          = "IDLE"
    RUNNING       = "RUNNING"
    SHUTTING_DOWN = "SHUTTING_DOWN"
    STOPPED       = "STOPPED"


# ---------------------------------------------------------------------------
# Per-component tracking record (internal)
# ---------------------------------------------------------------------------

class _ComponentRecord:
    """Mutable record tracking one supervised component.

    All fields are guarded by Supervisor._lock.  The supervision thread is
    the only writer; summary() reads under the same lock.
    """

    __slots__ = (
        "name", "component", "restartable",
        "restart_timestamps", "restart_count",
        "last_health", "fatal_escalated", "storm_escalated",
    )

    def __init__(self, name: str, component: Any, restartable: bool) -> None:
        self.name = name
        self.component = component
        self.restartable = restartable
        # Restart accounting
        self.restart_timestamps: collections.deque = collections.deque()
        self.restart_count: int = 0
        # Health snapshot from last poll
        self.last_health: Optional[HealthStatus] = None
        # One-shot escalation guards
        self.fatal_escalated: bool = False
        self.storm_escalated: bool = False


# ---------------------------------------------------------------------------
# Supervisor
# ---------------------------------------------------------------------------

class Supervisor:
    """Runtime health monitor and restart-policy authority.

    Lifecycle
    ---------
      sup = Supervisor(
          poll_interval_sec=10,
          restart_window_sec=300,
          max_restarts_per_window=3,
          on_fatal=lambda name, health: ...,
      )
      sup.register("ws_manager", ws_mgr)
      sup.register("snapshot_writer", writer, restartable=True)
      sup.register("scheduler", sched, restartable=False)

      sup.run()   # blocks; call request_shutdown() from another thread to exit

    Args:
        poll_interval_sec:       Seconds between health-check cycles.
        restart_window_sec:      Rolling window for restart-storm counting.
        max_restarts_per_window: Restarts within the window before storm escalation.
        on_fatal:                Called once when a component goes fatal or storms.
                                 Signature: (component_name: str, last_health: HealthStatus).
                                 If None, request_shutdown() is called instead.
    """

    COMPONENT_NAME = "supervisor"

    def __init__(
        self,
        poll_interval_sec: float = 10.0,
        restart_window_sec: float = 300.0,
        max_restarts_per_window: int = 3,
        on_fatal: Optional[Callable[[str, HealthStatus], None]] = None,
    ) -> None:
        self._poll_interval    = poll_interval_sec
        self._restart_window   = restart_window_sec
        self._max_restarts     = max_restarts_per_window
        self._on_fatal         = on_fatal

        self._lock             = threading.Lock()
        self._state            = SupervisorState.IDLE
        self._shutdown_event   = threading.Event()
        self._records: List[_ComponentRecord] = []
        self._started_at: Optional[float] = None

    # -----------------------------------------------------------------------
    # Registration
    # -----------------------------------------------------------------------

    def register(
        self,
        name: str,
        component: Any,
        *,
        restartable: bool = True,
    ) -> None:
        """Register a component for supervision.

        Args:
            name:        Unique identifier (used in logs and summary output).
            component:   Any object with start(), stop(), health() -> HealthStatus.
            restartable: If False, the Supervisor logs unhealthy state but never
                         calls stop()/start().  Useful for the Scheduler or any
                         component whose lifecycle the Runtime manages.

        Raises:
            ValueError: if a component with the same name is already registered.
        """
        with self._lock:
            for rec in self._records:
                if rec.name == name:
                    raise ValueError(
                        f"[supervisor] component '{name}' already registered"
                    )
            self._records.append(_ComponentRecord(name, component, restartable))
        logger.info(
            "[supervisor] registered '%s' (restartable=%s)", name, restartable
        )

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    def run(self) -> None:
        """Start the blocking supervision loop.

        Blocks until request_shutdown() is called or an unhandled exception
        occurs in the loop itself.  Returns normally in both cases.

        Raises:
            RuntimeError: if called in a non-IDLE state (e.g. double-start).
        """
        with self._lock:
            if self._state != SupervisorState.IDLE:
                raise RuntimeError(
                    f"[supervisor] run() called in state {self._state.value}"
                )
            self._state      = SupervisorState.RUNNING
            self._started_at = time.monotonic()
            n_components     = len(self._records)

        logger.info(
            "[supervisor] started (poll=%.1fs window=%.0fs "
            "max_restarts=%d n_components=%d)",
            self._poll_interval, self._restart_window,
            self._max_restarts, n_components,
        )

        try:
            while not self._shutdown_event.is_set():
                self._poll_cycle()
                # Interruptible: wakes immediately when shutdown is signalled
                self._shutdown_event.wait(timeout=self._poll_interval)
        except Exception as exc:
            # Protect the loop from any bug in our own code
            logger.critical(
                "[supervisor] unhandled exception in run() - loop exiting: "
                "%s: %s", type(exc).__name__, exc,
            )
        finally:
            with self._lock:
                self._state = SupervisorState.STOPPED
            logger.info("[supervisor] supervision loop exited")

    def request_shutdown(self, reason: str = "shutdown requested") -> None:
        """Signal the supervision loop to stop.  Safe to call from any thread.

        Idempotent — repeated calls have no additional effect beyond the first.
        """
        with self._lock:
            if self._state == SupervisorState.RUNNING:
                self._state = SupervisorState.SHUTTING_DOWN
        self._shutdown_event.set()
        logger.info("[supervisor] shutdown requested: %s", reason)

    # -----------------------------------------------------------------------
    # Health (Component protocol — safe from any thread)
    # -----------------------------------------------------------------------

    def health(self) -> HealthStatus:
        """Return an aggregated HealthStatus for the Supervisor itself.

        healthy=True  when RUNNING and no component is currently unhealthy.
        fatal=False   always — the Supervisor never self-declares fatal.
        """
        with self._lock:
            state = self._state
            degraded = [
                r.name for r in self._records
                if r.last_health is not None and not r.last_health.healthy
            ]

        running  = state == SupervisorState.RUNNING
        healthy  = running and not degraded
        details  = f"state={state.value} degraded={degraded if degraded else 'none'}"

        return HealthStatus(
            name=self.COMPONENT_NAME,
            healthy=healthy,
            fatal=False,
            details=details,
            last_check_at=datetime.now(timezone.utc),
        )

    # -----------------------------------------------------------------------
    # Summary (safe from any thread)
    # -----------------------------------------------------------------------

    def summary(self) -> dict:
        """Return a runtime snapshot as a plain dict.

        Takes a single _lock snapshot so the returned values are internally
        consistent.

        Fields
        ------
          state:              SupervisorState value string.
          uptime_sec:         Seconds since run() was called (0 if never started).
          shutdown_requested: True once request_shutdown() has been called.
          components:         List of per-component dicts (see below).
          degraded:           Names of components currently reporting unhealthy.

        Component dict fields: name, healthy, fatal, details, restart_count,
          restarts_in_window, restartable, storm_escalated.
        """
        now_mono = time.monotonic()
        with self._lock:
            state      = self._state
            started_at = self._started_at
            rec_snaps  = [
                {
                    "name":               r.name,
                    "healthy":            r.last_health.healthy if r.last_health else None,
                    "fatal":              r.last_health.fatal   if r.last_health else None,
                    "details":            r.last_health.details if r.last_health else "not yet polled",
                    "restart_count":      r.restart_count,
                    "restarts_in_window": self._count_in_window(r, now_mono),
                    "restartable":        r.restartable,
                    "storm_escalated":    r.storm_escalated,
                }
                for r in self._records
            ]

        uptime   = now_mono - started_at if started_at is not None else 0.0
        degraded = [r["name"] for r in rec_snaps if r["healthy"] is False]

        return {
            "state":              state.value,
            "uptime_sec":         uptime,
            "shutdown_requested": self._shutdown_event.is_set(),
            "components":         rec_snaps,
            "degraded":           degraded,
        }

    # -----------------------------------------------------------------------
    # Poll loop
    # -----------------------------------------------------------------------

    def _poll_cycle(self) -> None:
        """Run one health-check pass over all registered components.

        Component failures in one iteration do not block others.
        """
        with self._lock:
            records_snapshot = list(self._records)   # copy list, not records

        for rec in records_snapshot:
            try:
                self._poll_one(rec)
            except Exception as exc:
                logger.error(
                    "[supervisor] unexpected error polling '%s': %s: %s",
                    rec.name, type(exc).__name__, exc,
                )

    def _poll_one(self, rec: _ComponentRecord) -> None:
        """Poll a single component and apply restart / escalation policy.

        Lock discipline:
          - Read record fields under _lock (brief).
          - Call component.health() OUTSIDE lock.
          - Write record fields under _lock (brief).
          - Call component.stop() / component.start() OUTSIDE lock.
        """
        # 1. Capture current record state under lock
        with self._lock:
            prev_health    = rec.last_health
            storm_esc      = rec.storm_escalated
            fatal_esc      = rec.fatal_escalated
            restartable    = rec.restartable

        # 2. Fetch health OUTSIDE lock (may block or raise)
        try:
            health = rec.component.health()
        except Exception as exc:
            health = HealthStatus.degraded(
                rec.name,
                f"health() raised {type(exc).__name__}: {exc}",
            )
            logger.warning(
                "[supervisor] '%s' health() raised %s: %s",
                rec.name, type(exc).__name__, exc,
            )

        # 3. Store result under lock
        with self._lock:
            rec.last_health = health

        # 4. Log state transitions only (no spam for stable states)
        prev_healthy = prev_health.healthy if prev_health is not None else None
        if prev_healthy is None or prev_healthy != health.healthy:
            if health.healthy:
                logger.info("[supervisor] '%s' -> HEALTHY", rec.name)
            else:
                logger.warning(
                    "[supervisor] '%s' -> UNHEALTHY (fatal=%s): %s",
                    rec.name, health.fatal, health.details,
                )

        # 5. Nothing to do for healthy components
        if health.healthy:
            return

        # 6. Fatal component — escalate once, no restart
        if health.fatal:
            if not fatal_esc:
                with self._lock:
                    rec.fatal_escalated = True
                self._escalate_fatal(rec, health, reason="component reported fatal=True")
            else:
                logger.debug(
                    "[supervisor] '%s' remains fatal, already escalated", rec.name
                )
            return

        # 7. Non-restartable components: log once on transition, then quiet
        if not restartable:
            if prev_healthy is None or prev_healthy != health.healthy:
                logger.info(
                    "[supervisor] '%s' unhealthy but not restartable, no action",
                    rec.name,
                )
            return

        # 8. Storm already escalated — suppress further action
        if storm_esc:
            logger.debug(
                "[supervisor] '%s' storm already escalated, skipping restart",
                rec.name,
            )
            return

        # 9. Storm threshold check (under lock for consistent count)
        now_mono = time.monotonic()
        with self._lock:
            n_in_window = self._count_in_window(rec, now_mono)

        if n_in_window >= self._max_restarts:
            with self._lock:
                rec.storm_escalated = True
            self._escalate_storm(rec, health, n_in_window)
            return

        # 10. Attempt restart (all component calls outside lock)
        self._do_restart(rec, health)

    # -----------------------------------------------------------------------
    # Restart
    # -----------------------------------------------------------------------

    def _do_restart(
        self,
        rec: _ComponentRecord,
        health: HealthStatus,
    ) -> None:
        """Execute stop() -> start() for a component.

        Updates restart_timestamps and restart_count under _lock after the
        calls complete.  Exceptions from stop()/start() are caught and logged;
        the restart timestamp is recorded regardless so the storm counter is
        not fooled by failed restart attempts.
        """
        now_mono = time.monotonic()
        with self._lock:
            n_in_window = self._count_in_window(rec, now_mono)
            total_so_far = rec.restart_count

        logger.warning(
            "[supervisor] restarting '%s' "
            "(attempt %d in window, total restart #%d): %s",
            rec.name, n_in_window + 1, total_so_far + 1, health.details,
        )

        try:
            rec.component.stop()
        except Exception as exc:
            logger.error(
                "[supervisor] '%s' stop() raised during restart: %s: %s",
                rec.name, type(exc).__name__, exc,
            )

        try:
            rec.component.start()
        except Exception as exc:
            logger.error(
                "[supervisor] '%s' start() raised during restart: %s: %s",
                rec.name, type(exc).__name__, exc,
            )

        now_after = time.monotonic()
        with self._lock:
            rec.restart_timestamps.append(now_after)
            rec.restart_count += 1
            total    = rec.restart_count
            in_win   = self._count_in_window(rec, now_after)

        logger.info(
            "[supervisor] '%s' restarted (total=%d in_window=%d/%d)",
            rec.name, total, in_win, self._max_restarts,
        )

    # -----------------------------------------------------------------------
    # Escalation
    # -----------------------------------------------------------------------

    def _escalate_storm(
        self,
        rec: _ComponentRecord,
        health: HealthStatus,
        n_in_window: int,
    ) -> None:
        """Restart storm detected — log and escalate to fatal path."""
        logger.critical(
            "[supervisor] RESTART STORM: '%s' restarted %d/%d times "
            "in %.0fs window - escalating",
            rec.name, n_in_window, self._max_restarts, self._restart_window,
        )
        self._escalate_fatal(
            rec, health,
            reason=(
                f"restart storm: {n_in_window} restarts "
                f"in {self._restart_window:.0f}s window"
            ),
        )

    def _escalate_fatal(
        self,
        rec: _ComponentRecord,
        health: HealthStatus,
        *,
        reason: str,
    ) -> None:
        """Invoke on_fatal callback, or request shutdown if none provided.

        Exceptions raised by on_fatal are caught and logged — the escalation
        path must not itself crash.
        """
        logger.critical(
            "[supervisor] FATAL: '%s': %s", rec.name, reason
        )
        if self._on_fatal is not None:
            try:
                self._on_fatal(rec.name, health)
            except Exception as exc:
                logger.error(
                    "[supervisor] on_fatal callback raised: %s: %s",
                    type(exc).__name__, exc,
                )
        else:
            self.request_shutdown(
                reason=f"fatal component '{rec.name}': {reason}",
            )

    # -----------------------------------------------------------------------
    # Internal helper
    # -----------------------------------------------------------------------

    def _count_in_window(self, rec: _ComponentRecord, now_mono: float) -> int:
        """Count restarts inside the rolling window.  Caller holds _lock."""
        cutoff = now_mono - self._restart_window
        return sum(1 for t in rec.restart_timestamps if t > cutoff)


# ---------------------------------------------------------------------------
# Self-test / smoke  (no live network; exercises all policy branches)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # ------------------------------------------------------------------
    # Stub component base
    # ------------------------------------------------------------------
    class _Comp:
        def __init__(self, name: str) -> None:
            self.name = name
            self.starts = 0
            self.stops  = 0
        def start(self) -> None: self.starts += 1
        def stop(self)  -> None: self.stops  += 1
        def health(self) -> HealthStatus:
            return HealthStatus.ok(self.name)

    # ------------------------------------------------------------------
    # Test 1: healthy component — no restarts, clean shutdown
    # ------------------------------------------------------------------
    comp1 = _Comp("healthy")
    sup1 = Supervisor(poll_interval_sec=0.05)
    sup1.register("healthy", comp1)

    t1 = threading.Thread(target=sup1.run, daemon=True)
    t1.start()
    time.sleep(0.3)
    sup1.request_shutdown("test done")
    t1.join(timeout=2)

    assert not t1.is_alive(), "thread should have exited"
    s1 = sup1.summary()
    assert s1["state"] == "STOPPED"
    assert s1["components"][0]["restart_count"] == 0
    assert not s1["degraded"]
    print("[t1] healthy component, clean shutdown: PASS")

    # ------------------------------------------------------------------
    # Test 2: unhealthy -> restart -> recovers
    # ------------------------------------------------------------------
    class _RecoverComp(_Comp):
        def health(self) -> HealthStatus:
            if self.starts < 2:
                return HealthStatus.degraded(self.name, "not ready yet")
            return HealthStatus.ok(self.name)

    comp2 = _RecoverComp("recoverable")
    sup2 = Supervisor(poll_interval_sec=0.05, max_restarts_per_window=5)
    sup2.register("recoverable", comp2)

    t2 = threading.Thread(target=sup2.run, daemon=True)
    t2.start()
    time.sleep(0.5)
    sup2.request_shutdown()
    t2.join(timeout=2)

    assert comp2.starts >= 2, f"expected >= 2 starts (initial + restart), got {comp2.starts}"
    assert comp2.stops  >= 1, f"expected >= 1 stop before restart, got {comp2.stops}"
    s2 = sup2.summary()
    assert s2["components"][0]["restart_count"] >= 1
    print(f"[t2] restart triggered (starts={comp2.starts}): PASS")

    # ------------------------------------------------------------------
    # Test 3: fatal component — on_fatal called, no restart attempted
    # ------------------------------------------------------------------
    fatal_calls: list[tuple] = []

    def _on_fatal(name: str, h: HealthStatus) -> None:
        fatal_calls.append((name, h))

    class _FatalComp(_Comp):
        def health(self) -> HealthStatus:
            return HealthStatus.degraded(self.name, "disk failed", fatal=True)

    comp3 = _FatalComp("fatal_comp")
    sup3 = Supervisor(poll_interval_sec=0.05, on_fatal=_on_fatal)
    sup3.register("fatal_comp", comp3)

    t3 = threading.Thread(target=sup3.run, daemon=True)
    t3.start()
    time.sleep(0.4)
    sup3.request_shutdown()
    t3.join(timeout=2)

    assert len(fatal_calls) >= 1, f"on_fatal not called (calls={len(fatal_calls)})"
    assert fatal_calls[0][0] == "fatal_comp"
    # Fatal component must NOT have been restarted
    assert comp3.starts == 0, f"fatal component should never be restarted (starts={comp3.starts})"
    # on_fatal called only once (fatal_escalated flag)
    assert len(fatal_calls) == 1, f"on_fatal should fire once only, got {len(fatal_calls)}"
    print(f"[t3] fatal escalation, no restart, one-shot: PASS")

    # ------------------------------------------------------------------
    # Test 4: restart storm → on_fatal after max_restarts
    # ------------------------------------------------------------------
    storm_calls: list[str] = []

    def _on_storm(name: str, h: HealthStatus) -> None:
        storm_calls.append(name)

    class _AlwaysBroken(_Comp):
        def health(self) -> HealthStatus:
            return HealthStatus.degraded(self.name, "always broken")

    comp4 = _AlwaysBroken("storm_comp")
    sup4 = Supervisor(
        poll_interval_sec=0.05,
        restart_window_sec=10.0,
        max_restarts_per_window=2,
        on_fatal=_on_storm,
    )
    sup4.register("storm_comp", comp4)

    t4 = threading.Thread(target=sup4.run, daemon=True)
    t4.start()
    time.sleep(1.0)
    sup4.request_shutdown()
    t4.join(timeout=2)

    s4 = sup4.summary()
    rc4 = s4["components"][0]["restart_count"]
    assert rc4 >= 2, f"expected >= 2 restarts, got {rc4}"
    assert s4["components"][0]["storm_escalated"], "storm_escalated flag must be set"
    assert len(storm_calls) >= 1, "on_fatal must be called for storm"
    assert len(storm_calls) == 1, f"on_fatal must fire exactly once for storm, got {len(storm_calls)}"
    print(f"[t4] restart storm (restarts={rc4}, on_fatal={len(storm_calls)}): PASS")

    # ------------------------------------------------------------------
    # Test 5: no on_fatal → self-shutdown on fatal component
    # ------------------------------------------------------------------
    class _FatalSelfShutdown(_Comp):
        def health(self) -> HealthStatus:
            return HealthStatus.degraded(self.name, "fatal, no callback", fatal=True)

    sup5 = Supervisor(poll_interval_sec=0.05)   # on_fatal=None
    sup5.register("no_cb", _FatalSelfShutdown("no_cb"))

    t5 = threading.Thread(target=sup5.run, daemon=True)
    t5.start()
    t5.join(timeout=3)   # supervisor must self-terminate via request_shutdown()

    assert not t5.is_alive(), "supervisor should self-shutdown after fatal (no callback)"
    s5 = sup5.summary()
    assert s5["shutdown_requested"], "shutdown_requested must be True"
    print("[t5] fatal without callback triggers self-shutdown: PASS")

    # ------------------------------------------------------------------
    # Test 6: health() exception treated as degraded → triggers restart
    # ------------------------------------------------------------------
    class _HealthRaises(_Comp):
        def health(self) -> HealthStatus:
            raise RuntimeError("health check exploded")

    comp6 = _HealthRaises("broken_health")
    sup6 = Supervisor(poll_interval_sec=0.05, max_restarts_per_window=5)
    sup6.register("broken_health", comp6)

    t6 = threading.Thread(target=sup6.run, daemon=True)
    t6.start()
    time.sleep(0.4)
    sup6.request_shutdown()
    t6.join(timeout=2)

    assert comp6.starts >= 1, (
        f"expected restart after health() exception (starts={comp6.starts})"
    )
    print(f"[t6] health() exception treated as degraded (starts={comp6.starts}): PASS")

    # ------------------------------------------------------------------
    # Test 7: non-restartable component — never restarted
    # ------------------------------------------------------------------
    class _NonRestart(_Comp):
        def start(self):
            raise AssertionError("non-restartable component must not be restarted")
        def stop(self):
            raise AssertionError("non-restartable component must not be stopped")
        def health(self) -> HealthStatus:
            return HealthStatus.degraded(self.name, "always broken but not restartable")

    sup7 = Supervisor(poll_interval_sec=0.05)
    sup7.register("non_restart", _NonRestart("non_restart"), restartable=False)

    t7 = threading.Thread(target=sup7.run, daemon=True)
    t7.start()
    time.sleep(0.4)
    sup7.request_shutdown()
    t7.join(timeout=2)

    s7 = sup7.summary()
    assert s7["components"][0]["restart_count"] == 0
    print("[t7] non-restartable component: never restarted: PASS")

    # ------------------------------------------------------------------
    # Test 8: duplicate registration raises ValueError
    # ------------------------------------------------------------------
    sup8 = Supervisor()
    sup8.register("comp_a", _Comp("comp_a"))
    try:
        sup8.register("comp_a", _Comp("comp_a"))
        print("ERROR: should have raised ValueError", file=sys.stderr)
        sys.exit(1)
    except ValueError:
        pass
    print("[t8] duplicate registration raises ValueError: PASS")

    # ------------------------------------------------------------------
    # Test 9: summary() is consistent under concurrent calls
    # ------------------------------------------------------------------
    sup9 = Supervisor(poll_interval_sec=0.05)
    sup9.register("c1", _Comp("c1"))
    sup9.register("c2", _Comp("c2"))

    errors9: list = []

    def _summary_caller():
        for _ in range(50):
            try:
                s = sup9.summary()
                assert "state" in s
                assert "components" in s
            except Exception as exc:
                errors9.append(exc)
            time.sleep(0.01)

    t9a = threading.Thread(target=sup9.run, daemon=True)
    t9b = threading.Thread(target=_summary_caller, daemon=True)
    t9a.start()
    t9b.start()
    t9b.join(timeout=3)
    sup9.request_shutdown()
    t9a.join(timeout=2)

    assert not errors9, f"concurrent summary() errors: {errors9}"
    print("[t9] concurrent summary() consistent: PASS")

    # ------------------------------------------------------------------
    # Test 10: run() raises RuntimeError on double-start
    # ------------------------------------------------------------------
    sup10 = Supervisor(poll_interval_sec=0.05)
    t10 = threading.Thread(target=sup10.run, daemon=True)
    t10.start()
    time.sleep(0.1)

    try:
        sup10.run()   # called from main thread while t10 has it
        print("ERROR: should have raised RuntimeError", file=sys.stderr)
        sys.exit(1)
    except RuntimeError:
        pass
    finally:
        sup10.request_shutdown()
        t10.join(timeout=2)

    print("[t10] double-start raises RuntimeError: PASS")

    # ------------------------------------------------------------------
    # Test 11: health() reflects aggregated state
    # ------------------------------------------------------------------
    class _SometimesUnhealthy(_Comp):
        def __init__(self, name):
            super().__init__(name)
            self._ok = False
        def health(self):
            return HealthStatus.ok(self.name) if self._ok else HealthStatus.degraded(self.name, "not ok")

    sup11 = Supervisor(poll_interval_sec=0.05, max_restarts_per_window=10)
    comp11 = _SometimesUnhealthy("comp11")
    sup11.register("comp11", comp11)

    t11 = threading.Thread(target=sup11.run, daemon=True)
    t11.start()
    time.sleep(0.2)

    h_deg = sup11.health()
    assert not h_deg.healthy, f"expected unhealthy while comp is unhealthy: {h_deg}"
    comp11._ok = True
    time.sleep(0.2)

    h_ok = sup11.health()
    assert h_ok.healthy, f"expected healthy after comp recovers: {h_ok}"

    sup11.request_shutdown()
    t11.join(timeout=2)
    print("[t11] aggregated health() reflects component state: PASS")

    print("\nSupervisor smoke test passed.")
