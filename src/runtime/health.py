"""HealthStatus dataclass for component health reporting.

Each component in the runtime implements a check_health() -> HealthStatus
method.  The Supervisor aggregates these to decide whether to escalate or halt.

Design notes
------------
- Immutable (frozen dataclass): a HealthStatus is a point-in-time snapshot.
  Components produce a new one on each check; they do not mutate old ones.
- Timezone-aware: last_check_at is always UTC.
- No logging, no Telegram, no scheduler, no restart logic.
- Convenience constructors (ok / degraded) stamp the current UTC time so
  callers never need to remember to set last_check_at themselves.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from typing import Optional


@dataclasses.dataclass(frozen=True)
class HealthStatus:
    """Immutable snapshot of a single component's health.

    Attributes:
        name:          Component identifier, e.g. "ws_manager".
        healthy:       True iff the component is functioning normally.
        fatal:         True iff the failure is unrecoverable and the Supervisor
                       should halt the runtime.  Ignored when healthy=True.
        details:       Human-readable description — reason for failure or "ok".
        last_check_at: UTC datetime when this status was produced (required
                       to be timezone-aware; __post_init__ enforces this).
    """

    name: str
    healthy: bool
    fatal: bool
    details: str
    last_check_at: datetime

    def __post_init__(self) -> None:
        if self.last_check_at.tzinfo is None:
            raise ValueError(
                f"HealthStatus.last_check_at must be timezone-aware (got naive datetime "
                f"for component '{self.name}')"
            )

    # ------------------------------------------------------------------
    # Convenience constructors
    # ------------------------------------------------------------------

    @staticmethod
    def ok(name: str, details: str = "ok") -> HealthStatus:
        """Return a healthy status stamped with the current UTC time."""
        return HealthStatus(
            name=name,
            healthy=True,
            fatal=False,
            details=details,
            last_check_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def degraded(name: str, details: str, *, fatal: bool = False) -> HealthStatus:
        """Return an unhealthy status stamped with the current UTC time.

        Args:
            name:    Component identifier.
            details: Reason for the failure.
            fatal:   Set True if the Supervisor should halt (not just restart).
        """
        return HealthStatus(
            name=name,
            healthy=False,
            fatal=fatal,
            details=details,
            last_check_at=datetime.now(timezone.utc),
        )

    # ------------------------------------------------------------------
    # Observability helpers
    # ------------------------------------------------------------------

    def age_sec(self, now: Optional[datetime] = None) -> float:
        """Seconds elapsed since this status was produced.

        Args:
            now: Reference time; defaults to datetime.now(UTC).
        """
        ref = now if now is not None else datetime.now(timezone.utc)
        return (ref - self.last_check_at).total_seconds()

    def is_stale(self, threshold_sec: float, now: Optional[datetime] = None) -> bool:
        """True if this status is older than threshold_sec seconds."""
        return self.age_sec(now) > threshold_sec

    def __str__(self) -> str:
        if self.healthy:
            state = "OK"
        elif self.fatal:
            state = "FATAL"
        else:
            state = "DEGRADED"
        return f"[{state}] {self.name}: {self.details}"


if __name__ == "__main__":
    import time

    # --- ok / degraded constructors ---
    h_ok = HealthStatus.ok("ws_manager")
    assert h_ok.healthy
    assert not h_ok.fatal
    assert h_ok.last_check_at.tzinfo is not None
    print(h_ok)

    h_deg = HealthStatus.degraded("snapshot_writer", "DB locked", fatal=False)
    assert not h_deg.healthy
    assert not h_deg.fatal
    print(h_deg)

    h_fat = HealthStatus.degraded("ws_manager", "no data for 5 min", fatal=True)
    assert h_fat.fatal
    print(h_fat)

    # --- age / staleness ---
    time.sleep(0.05)
    assert h_ok.age_sec() >= 0.05
    assert h_ok.is_stale(threshold_sec=0.01)
    assert not h_ok.is_stale(threshold_sec=9999)

    # --- naive datetime rejected ---
    try:
        HealthStatus(
            name="bad",
            healthy=True,
            fatal=False,
            details="test",
            last_check_at=datetime(2026, 1, 1),  # naive — no tzinfo
        )
        assert False, "should have raised"
    except ValueError as e:
        print(f"  naive datetime correctly rejected: {e}")

    # --- Supervisor aggregation pattern ---
    statuses = [h_ok, h_deg, h_fat]
    any_fatal = any(not s.healthy and s.fatal for s in statuses)
    any_degraded = any(not s.healthy for s in statuses)
    print(f"  any_fatal={any_fatal}  any_degraded={any_degraded}")
    assert any_fatal
    assert any_degraded

    print("health.py smoke test passed.")
