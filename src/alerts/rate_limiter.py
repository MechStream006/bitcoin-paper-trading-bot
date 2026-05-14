"""RateLimiter — per-category alert suppression for the BTC bot runtime.

Provides thread-safe, non-blocking, per-category cooldown windows.
Callers follow an explicit allow() -> send -> record() pattern so that
suppression decisions are decoupled from recording the send.

Critical alerts may bypass the cooldown by passing bypass=True to allow().

Dependency direction
--------------------
  alerts.rate_limiter  (stdlib only — no runtime dependencies)

Concurrency contract
--------------------
  _lock guards _cooldowns, _last_sent, _suppressed_counts.
  No I/O, no sleep, no external calls while _lock is held.
  allow() and record() each hold _lock for the minimum time needed.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)


class RateLimiter:
    """Per-category alert suppression with configurable cooldown windows.

    Usage pattern
    -------------
      rl = RateLimiter(default_cooldown_sec=1800)
      rl.set_cooldown("ws_degraded", 300)

      if rl.allow("ws_degraded"):
          send_alert("WS is degraded")
          rl.record("ws_degraded")

    Critical bypass (always passes)
    --------------------------------
      if rl.allow("fatal", bypass=True):
          send_alert("FATAL: halting")
          rl.record("fatal")

    Args:
        default_cooldown_sec: Cooldown applied to any category without a
                              specific override.  Defaults to 1800s (30 min).
    """

    def __init__(self, default_cooldown_sec: float = 1800.0) -> None:
        self._default_cooldown = default_cooldown_sec
        self._cooldowns: dict[str, float] = {}
        self._last_sent: dict[str, float] = {}
        self._suppressed_counts: dict[str, int] = {}
        self._lock = threading.Lock()

    # -----------------------------------------------------------------------
    # Configuration
    # -----------------------------------------------------------------------

    def set_cooldown(self, category: str, cooldown_sec: float) -> None:
        """Override the cooldown for a specific category.

        Safe to call at any time, including after the limiter is in use.
        The new cooldown applies to the next allow() check.

        Raises:
            ValueError: if cooldown_sec is negative.
        """
        if cooldown_sec < 0:
            raise ValueError(f"cooldown_sec must be >= 0, got {cooldown_sec}")
        with self._lock:
            self._cooldowns[category] = cooldown_sec

    # -----------------------------------------------------------------------
    # Core API
    # -----------------------------------------------------------------------

    def allow(self, category: str, *, bypass: bool = False) -> bool:
        """Return True if an alert in this category may be sent right now.

        Does NOT update any state — call record() after the alert is sent.

        Args:
            category: Logical alert category, e.g. "heartbeat", "ws_degraded".
            bypass:   If True, always returns True (critical-alert path).
                      The cooldown timer is NOT reset; call record() for that.
        """
        if bypass:
            return True

        now = time.monotonic()
        with self._lock:
            cooldown = self._cooldowns.get(category, self._default_cooldown)
            last = self._last_sent.get(category)

        if last is None:
            return True

        elapsed = now - last
        if elapsed >= cooldown:
            return True

        # Suppressed — update counter and log outside lock.
        with self._lock:
            self._suppressed_counts[category] = (
                self._suppressed_counts.get(category, 0) + 1
            )
            suppressed = self._suppressed_counts[category]
            remaining = cooldown - elapsed

        logger.debug(
            "[rate_limiter] '%s' suppressed (cooldown=%.0fs remaining=%.0fs total_suppressed=%d)",
            category, cooldown, remaining, suppressed,
        )
        return False

    def record(self, category: str) -> None:
        """Record that an alert for this category was just sent.

        Resets the cooldown window and suppression counter.
        Call this immediately after the alert is confirmed sent.
        """
        now = time.monotonic()
        with self._lock:
            self._last_sent[category] = now
            self._suppressed_counts[category] = 0
        logger.debug("[rate_limiter] '%s' recorded at t=%.3f", category, now)

    # -----------------------------------------------------------------------
    # Observability
    # -----------------------------------------------------------------------

    def suppressed_count(self, category: str) -> int:
        """Suppression count since the last record() for this category."""
        with self._lock:
            return self._suppressed_counts.get(category, 0)

    def remaining_sec(self, category: str) -> Optional[float]:
        """Seconds until the cooldown for this category expires.

        Returns None if the category has never been recorded (no cooldown active).
        Returns a positive float if currently in cooldown.
        Returns None (not 0) if the cooldown has already expired.
        """
        now = time.monotonic()
        with self._lock:
            cooldown = self._cooldowns.get(category, self._default_cooldown)
            last = self._last_sent.get(category)
        if last is None:
            return None
        remaining = cooldown - (now - last)
        return remaining if remaining > 0 else None

    def __repr__(self) -> str:
        with self._lock:
            tracked = list(self._last_sent.keys())
        return (
            f"RateLimiter(default_cooldown={self._default_cooldown}s, "
            f"tracked={tracked})"
        )


# ---------------------------------------------------------------------------
# Self-test / smoke
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # -----------------------------------------------------------------------
    # Test 1: first call always allowed (no record yet)
    # -----------------------------------------------------------------------
    rl = RateLimiter(default_cooldown_sec=60)
    assert rl.allow("heartbeat"), "first allow() must return True"
    assert rl.suppressed_count("heartbeat") == 0
    print("[t1] first call allowed: PASS")

    # -----------------------------------------------------------------------
    # Test 2: suppressed within cooldown after record()
    # -----------------------------------------------------------------------
    rl.record("heartbeat")
    assert not rl.allow("heartbeat"), "must be suppressed immediately after record()"
    assert rl.suppressed_count("heartbeat") == 1
    assert not rl.allow("heartbeat"), "second suppressed call"
    assert rl.suppressed_count("heartbeat") == 2
    print("[t2] suppression after record: PASS")

    # -----------------------------------------------------------------------
    # Test 3: bypass=True always passes (even within cooldown)
    # -----------------------------------------------------------------------
    assert rl.allow("heartbeat", bypass=True), "bypass must override cooldown"
    print("[t3] bypass=True overrides cooldown: PASS")

    # -----------------------------------------------------------------------
    # Test 4: cooldown expiry restores allow()
    # -----------------------------------------------------------------------
    rl2 = RateLimiter(default_cooldown_sec=0.05)   # 50ms cooldown
    rl2.record("fast")
    assert not rl2.allow("fast"), "must be suppressed just after record()"
    time.sleep(0.1)
    assert rl2.allow("fast"), "must be allowed after cooldown expires"
    print("[t4] cooldown expiry allows again: PASS")

    # -----------------------------------------------------------------------
    # Test 5: record() resets suppression counter
    # -----------------------------------------------------------------------
    rl3 = RateLimiter(default_cooldown_sec=60)
    rl3.record("alert")
    rl3.allow("alert")   # suppressed
    rl3.allow("alert")   # suppressed again
    assert rl3.suppressed_count("alert") == 2
    rl3.record("alert")  # re-record (as if alert was sent via bypass)
    assert rl3.suppressed_count("alert") == 0, "record() must reset suppression counter"
    print("[t5] record() resets suppression counter: PASS")

    # -----------------------------------------------------------------------
    # Test 6: set_cooldown() affects subsequent allow() checks
    # -----------------------------------------------------------------------
    rl4 = RateLimiter(default_cooldown_sec=3600)
    rl4.record("cat")
    assert not rl4.allow("cat"), "suppressed under 3600s cooldown"
    rl4.set_cooldown("cat", 0.0)   # zero cooldown — always passes after elapsed > 0
    time.sleep(0.01)
    assert rl4.allow("cat"), "must be allowed with 0s cooldown"
    print("[t6] set_cooldown() takes effect immediately: PASS")

    # -----------------------------------------------------------------------
    # Test 7: remaining_sec() returns None when not recorded, positive in cooldown
    # -----------------------------------------------------------------------
    rl5 = RateLimiter(default_cooldown_sec=60)
    assert rl5.remaining_sec("x") is None, "None expected before any record()"
    rl5.record("x")
    rem = rl5.remaining_sec("x")
    assert rem is not None and 0 < rem <= 60, f"expected 0 < remaining <= 60, got {rem}"
    print(f"[t7] remaining_sec after record: {rem:.2f}s: PASS")

    # -----------------------------------------------------------------------
    # Test 8: negative cooldown raises ValueError
    # -----------------------------------------------------------------------
    try:
        rl5.set_cooldown("bad", -1)
        print("ERROR: expected ValueError", file=sys.stderr)
        sys.exit(1)
    except ValueError:
        pass
    print("[t8] negative cooldown raises ValueError: PASS")

    # -----------------------------------------------------------------------
    # Test 9: separate categories are independent
    # -----------------------------------------------------------------------
    rl6 = RateLimiter(default_cooldown_sec=3600)
    rl6.record("cat_a")
    assert not rl6.allow("cat_a"), "cat_a must be suppressed"
    assert rl6.allow("cat_b"), "cat_b (never recorded) must be allowed"
    print("[t9] categories are independent: PASS")

    print("\nRateLimiter smoke test passed.")
