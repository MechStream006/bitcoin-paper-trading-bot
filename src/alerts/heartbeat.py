"""HeartbeatService — operational summary emitter for the BTC bot runtime.

Responsibilities
----------------
- Compose a human-readable runtime summary from component health snapshots
  and market-state readings.
- Emit the summary via a caller-provided sender (transport-agnostic).
- Suppress duplicate heartbeats via RateLimiter.
- Expose its own health() for Supervisor aggregation.
- Provide a daily_summary() placeholder for Layer 5.

What HeartbeatService does NOT do
----------------------------------
- Does not own a scheduler or timer.  SchedulerService calls send_heartbeat().
- Does not restart components.
- Does not write to SQLite.
- Does not mutate MarketState or any WS connection state.
- Does not contain strategy or risk logic.

Dependency direction
--------------------
  alerts.heartbeat
    -> alerts.rate_limiter         (alert suppression)
    -> runtime.health              (produces HealthStatus)
    -> state.market_state          (read coverage / freshness)
    -> exchange.ws_manager         (read health() snapshot)       [TYPE_CHECKING only]
    -> persistence.snapshot_writer (read health() snapshot)       [TYPE_CHECKING only]
    -> ingestion.chain_refresher   (read universe / snapshot)     [TYPE_CHECKING only]

Concurrency contract
--------------------
  send_heartbeat() is called from a scheduler thread.
  health() may be called from any thread at any time.
  _lock guards _total_sent, _last_send_at, _last_failure_reason.
  No lock is held across sender() calls or component health() reads.

Failure isolation
-----------------
  send_heartbeat() catches ALL exceptions from _compose_heartbeat() and
  sender().  A heartbeat failure never propagates to the scheduler.
  Failures are recorded in health() and logged at ERROR level.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable, List, Optional

from src.alerts.rate_limiter import RateLimiter
from src.runtime.health import HealthStatus
from src.state.market_state import MarketState

if TYPE_CHECKING:
    from src.exchange.ws_manager import WSManager
    from src.ingestion.chain_refresher import ChainRefresher
    from src.persistence.snapshot_writer import SnapshotWriter

logger = logging.getLogger(__name__)


class HeartbeatService:
    """Compose and emit runtime heartbeats via a caller-provided sender.

    Lifecycle
    ---------
      hb = HeartbeatService(
          sender=partial(send_message, token, chat_id),
          ws_manager=mgr, market_state=ms,
          snapshot_writer=sw, chain_refresher=cr,
          rate_limiter=rl, started_at=time.monotonic(),
      )
      scheduler.register("heartbeat", hb.send_heartbeat,
                         trigger="interval", seconds=1800)
      hb.start()

    Args:
        sender:             Callable(text: str) -> None.  Raises on error.
                            Exceptions are caught inside send_heartbeat().
        ws_manager:         WSManager — read health() for WS stats.
        market_state:       MarketState — read coverage_pct / stale_symbols.
        snapshot_writer:    SnapshotWriter — read health().
        chain_refresher:    ChainRefresher — read current_snapshot().
        rate_limiter:       RateLimiter — heartbeat suppression.
        started_at:         time.monotonic() captured at bot boot; used for uptime.
        heartbeat_category: Rate-limiter category for heartbeat sends.
    """

    COMPONENT_NAME = "heartbeat"

    def __init__(
        self,
        sender: Callable[[str], None],
        ws_manager: "WSManager",
        market_state: MarketState,
        snapshot_writer: "SnapshotWriter",
        chain_refresher: "ChainRefresher",
        rate_limiter: RateLimiter,
        started_at: float,
        heartbeat_category: str = "heartbeat",
    ) -> None:
        self._sender = sender
        self._ws_manager = ws_manager
        self._market_state = market_state
        self._snapshot_writer = snapshot_writer
        self._chain_refresher = chain_refresher
        self._rate_limiter = rate_limiter
        self._started_at = started_at
        self._heartbeat_category = heartbeat_category

        self._lock = threading.Lock()
        self._total_sent: int = 0
        self._last_send_at: Optional[float] = None       # monotonic
        self._last_failure_reason: Optional[str] = None

    # -----------------------------------------------------------------------
    # Lifecycle (Component contract — lightweight, no background thread)
    # -----------------------------------------------------------------------

    def start(self) -> None:
        """Mark the component as active."""
        logger.info("[heartbeat] ready (scheduler-driven)")

    def stop(self) -> None:
        """Mark the component as inactive."""
        logger.info("[heartbeat] stopped")

    # -----------------------------------------------------------------------
    # Health reporting (safe to call from any thread)
    # -----------------------------------------------------------------------

    def health(self) -> HealthStatus:
        """Return a HealthStatus reflecting the most recent heartbeat send.

        healthy=True  when last_failure_reason is None.
        fatal=False   always.
        """
        with self._lock:
            total = self._total_sent
            last = self._last_send_at
            failure = self._last_failure_reason

        age_str = f"{time.monotonic() - last:.0f}s" if last is not None else "never"
        healthy = failure is None
        details = (
            f"total_sent={total} last_send={age_str}"
            + (f" last_failure={failure}" if failure else "")
        )
        return HealthStatus(
            name=self.COMPONENT_NAME,
            healthy=healthy,
            fatal=False,
            details=details,
            last_check_at=datetime.now(timezone.utc),
        )

    # -----------------------------------------------------------------------
    # Heartbeat emission (called by SchedulerService)
    # -----------------------------------------------------------------------

    def send_heartbeat(self) -> None:
        """Compose and emit a heartbeat message.

        Rate-limiter check: if a heartbeat was sent too recently, log the
        suppression and return.

        All exceptions from composition and sender are caught and recorded.
        A heartbeat failure must never crash the scheduler or the runtime.
        """
        category = self._heartbeat_category
        if not self._rate_limiter.allow(category):
            logger.debug("[heartbeat] suppressed by rate limiter")
            return

        # --- composition (no lock, no I/O, all reads are snapshot-style) ---
        try:
            text = self._compose_heartbeat()
        except Exception as exc:
            reason = f"compose:{type(exc).__name__}:{exc}"
            logger.error("[heartbeat] composition failed: %s: %s", type(exc).__name__, exc)
            with self._lock:
                self._last_failure_reason = reason
            return

        # --- send (no lock held during I/O) ---
        try:
            self._sender(text)
        except Exception as exc:
            reason = f"send:{type(exc).__name__}:{exc}"
            logger.error("[heartbeat] sender failed: %s: %s", type(exc).__name__, exc)
            with self._lock:
                self._last_failure_reason = reason
            return

        # --- record success ---
        self._rate_limiter.record(category)
        now = time.monotonic()
        with self._lock:
            self._total_sent += 1
            self._last_send_at = now
            self._last_failure_reason = None
        logger.info("[heartbeat] sent (total=%d)", self._total_sent)

    # -----------------------------------------------------------------------
    # Daily summary (placeholder for Layer 5)
    # -----------------------------------------------------------------------

    def daily_summary(self) -> None:
        """Emit an end-of-day summary.  Placeholder — implemented in Layer 5."""
        logger.info("[heartbeat] daily_summary() called (not yet implemented)")

    # -----------------------------------------------------------------------
    # Composition helpers (never raise — resilient to missing/None data)
    # -----------------------------------------------------------------------

    def _compose_heartbeat(self) -> str:
        """Assemble the heartbeat text from live component state.

        Reads each section independently; a failure in one section falls back
        to a safe error string so the rest of the message is still emitted.
        """
        lines: List[str] = [
            "--- BTC Bot Heartbeat ---",
            f"uptime       : {self._format_uptime()}",
            self._format_ws_line(),
            self._format_market_line(),
            self._format_snapshot_line(),
            self._format_universe_line(),
        ]

        degraded = self._collect_degraded()
        if degraded:
            lines.append("")
            for entry in degraded:
                lines.append(f"[DEGRADED] {entry}")

        lines.append("-------------------------")
        return "\n".join(lines)

    def _format_uptime(self) -> str:
        total_sec = int(time.monotonic() - self._started_at)
        h, rem = divmod(total_sec, 3600)
        m, s = divmod(rem, 60)
        if h > 0:
            return f"{h}h {m:02d}m {s:02d}s"
        return f"{m}m {s:02d}s"

    def _format_ws_line(self) -> str:
        try:
            h = self._ws_manager.health()
            # health().details: "state=STREAMING last_msg=2.1s msgs=1240 reconnects=0"
            state_val = _extract_kv(h.details, "state") or "UNKNOWN"
            msgs_val = _extract_kv(h.details, "msgs") or "?"
            rc_val = _extract_kv(h.details, "reconnects") or "?"
            return (
                f"ws           : {state_val} (msgs={msgs_val}, reconnects={rc_val})"
            )
        except Exception:
            return "ws           : [error reading health]"

    def _format_market_line(self) -> str:
        try:
            cov = self._market_state.coverage_pct()
            total = len(self._market_state.universe_symbols())
            covered = int(round(cov * total))
            stale = self._market_state.stale_symbols(threshold_sec=30.0)
            stale_str = f"{len(stale)} stale" if stale else "all fresh"
            return f"coverage     : {covered}/{total} ({cov:.0%}), {stale_str}"
        except Exception:
            return "coverage     : [error reading market state]"

    def _format_snapshot_line(self) -> str:
        try:
            h = self._snapshot_writer.health()
            # health().details: "last_snap=58s total_rows=900 last_rows=30 consec_failures=0"
            last_snap = _extract_kv(h.details, "last_snap") or "unknown"
            last_rows = _extract_kv(h.details, "last_rows") or "?"
            cf = _extract_kv(h.details, "consec_failures") or "0"
            status = "ok" if h.healthy else f"DEGRADED (cf={cf})"
            return f"snapshot     : last={last_snap}, {last_rows} rows, {status}"
        except Exception:
            return "snapshot     : [error reading health]"

    def _format_universe_line(self) -> str:
        try:
            snap = self._chain_refresher.current_snapshot()
            if snap is None:
                return "universe     : not yet loaded"
            n = len(snap.symbols)
            age_str = _format_age(time.monotonic() - snap.refreshed_at)
            return f"universe     : {n} options, refresh {age_str} ago"
        except Exception:
            return "universe     : [error reading snapshot]"

    def _collect_degraded(self) -> List[str]:
        """Return one-line summaries for all unhealthy tracked components."""
        result: List[str] = []
        for comp in (self._ws_manager, self._snapshot_writer, self._chain_refresher):
            try:
                h = comp.health()
                if not h.healthy:
                    result.append(f"{h.name}: {h.details}")
            except Exception:
                pass
        return result


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _extract_kv(text: str, key: str) -> Optional[str]:
    """Extract the value from the first 'key=value' token in a space-separated string.

    Returns None if the key is not present.
    """
    prefix = f"{key}="
    for token in text.split():
        if token.startswith(prefix):
            return token[len(prefix):]
    return None


def _format_age(sec: float) -> str:
    """Human-readable age: '3s', '5m', '2h 15m'."""
    total = int(sec)
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m"
    h = total // 3600
    m = (total % 3600) // 60
    return f"{h}h {m}m"


# ---------------------------------------------------------------------------
# Self-test / smoke  (no live network; uses lightweight stubs)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import dataclasses
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # ------------------------------------------------------------------
    # Minimal stubs so we can exercise HeartbeatService without live deps
    # ------------------------------------------------------------------

    @dataclasses.dataclass
    class _FakeSnap:
        symbols: list
        refreshed_at: float

    class _FakeWSManager:
        def health(self):
            return HealthStatus.ok(
                "ws_manager",
                "state=STREAMING last_msg=2.1s msgs=500 reconnects=1",
            )

    class _FakeSnapshotWriter:
        def health(self):
            return HealthStatus.ok(
                "snapshot_writer",
                "last_snap=45s total_rows=300 last_rows=20 consec_failures=0",
            )

    class _FakeChainRefresher:
        def current_snapshot(self):
            return _FakeSnap(
                symbols=["C-BTC-95000-280526"] * 20,
                refreshed_at=time.monotonic() - 300,
            )
        def health(self):
            return HealthStatus.ok("chain_refresher", "ok")

    class _DegradedWSManager:
        def health(self):
            return HealthStatus.degraded(
                "ws_manager",
                "state=STALE last_msg=90.0s msgs=200 reconnects=5",
            )

    universe = ["C-BTC-95000-280526", "P-BTC-95000-280526"]
    ms = MarketState(universe=universe)
    ms.update_ticker("C-BTC-95000-280526", {"mark_price": "95100.0"})

    # -----------------------------------------------------------------------
    # Test 1: _compose_heartbeat() produces valid ASCII text
    # -----------------------------------------------------------------------
    rl = RateLimiter(default_cooldown_sec=0)   # no cooldown for tests
    sent: list[str] = []

    hb = HeartbeatService(
        sender=sent.append,
        ws_manager=_FakeWSManager(),
        market_state=ms,
        snapshot_writer=_FakeSnapshotWriter(),
        chain_refresher=_FakeChainRefresher(),
        rate_limiter=rl,
        started_at=time.monotonic() - 3723,   # ~1h 2m 3s uptime
    )

    text = hb._compose_heartbeat()
    print("[t1] composed heartbeat:")
    print(text)
    assert "BTC Bot Heartbeat" in text
    assert "uptime" in text
    assert "ws" in text
    assert "coverage" in text
    assert "snapshot" in text
    assert "universe" in text
    assert "[DEGRADED]" not in text, "should be no degraded in healthy state"
    text.encode("ascii")   # must be pure ASCII (no Unicode escapes)
    print("[t1] compose + ASCII check: PASS")

    # -----------------------------------------------------------------------
    # Test 2: send_heartbeat() calls sender
    # -----------------------------------------------------------------------
    hb.start()
    hb.send_heartbeat()
    assert len(sent) == 1, f"expected 1 message, got {len(sent)}"
    assert "BTC Bot Heartbeat" in sent[0]
    h = hb.health()
    assert h.healthy, f"expected healthy after send: {h}"
    assert h.details.startswith("total_sent=1")
    print("[t2] send_heartbeat() calls sender: PASS")

    # -----------------------------------------------------------------------
    # Test 3: rate limiter suppresses second call within cooldown
    # -----------------------------------------------------------------------
    rl2 = RateLimiter(default_cooldown_sec=3600)
    sent2: list[str] = []
    hb2 = HeartbeatService(
        sender=sent2.append,
        ws_manager=_FakeWSManager(),
        market_state=ms,
        snapshot_writer=_FakeSnapshotWriter(),
        chain_refresher=_FakeChainRefresher(),
        rate_limiter=rl2,
        started_at=time.monotonic(),
    )
    hb2.send_heartbeat()   # first: sends
    hb2.send_heartbeat()   # second: suppressed
    hb2.send_heartbeat()   # third:  suppressed
    assert len(sent2) == 1, f"expected 1 send (2 suppressed), got {len(sent2)}"
    print("[t3] rate limiter suppresses duplicate heartbeats: PASS")

    # -----------------------------------------------------------------------
    # Test 4: sender failure does not propagate; health() degrades
    # -----------------------------------------------------------------------
    def _failing_sender(text: str) -> None:
        raise RuntimeError("Telegram API unreachable")

    rl3 = RateLimiter(default_cooldown_sec=0)
    hb3 = HeartbeatService(
        sender=_failing_sender,
        ws_manager=_FakeWSManager(),
        market_state=ms,
        snapshot_writer=_FakeSnapshotWriter(),
        chain_refresher=_FakeChainRefresher(),
        rate_limiter=rl3,
        started_at=time.monotonic(),
    )
    hb3.send_heartbeat()   # must not raise
    h3 = hb3.health()
    assert not h3.healthy, f"expected unhealthy after send failure: {h3}"
    assert "send:" in h3.details
    print("[t4] sender failure does not propagate; health() degrades: PASS")

    # -----------------------------------------------------------------------
    # Test 5: [DEGRADED] block appears when a component is unhealthy
    # -----------------------------------------------------------------------
    rl4 = RateLimiter(default_cooldown_sec=0)
    sent4: list[str] = []
    hb4 = HeartbeatService(
        sender=sent4.append,
        ws_manager=_DegradedWSManager(),
        market_state=ms,
        snapshot_writer=_FakeSnapshotWriter(),
        chain_refresher=_FakeChainRefresher(),
        rate_limiter=rl4,
        started_at=time.monotonic(),
    )
    hb4.send_heartbeat()
    assert sent4, "should have sent"
    assert "[DEGRADED]" in sent4[0], "DEGRADED block missing from heartbeat"
    print("[t5] degraded component appears in heartbeat:")
    print(sent4[0])
    print("[t5] PASS")

    # -----------------------------------------------------------------------
    # Test 6: universe line falls back gracefully when snapshot is None
    # -----------------------------------------------------------------------
    class _NoSnapshotRefresher:
        def current_snapshot(self):
            return None
        def health(self):
            return HealthStatus.ok("chain_refresher", "ok")

    rl5 = RateLimiter(default_cooldown_sec=0)
    sent5: list[str] = []
    hb5 = HeartbeatService(
        sender=sent5.append,
        ws_manager=_FakeWSManager(),
        market_state=ms,
        snapshot_writer=_FakeSnapshotWriter(),
        chain_refresher=_NoSnapshotRefresher(),
        rate_limiter=rl5,
        started_at=time.monotonic(),
    )
    hb5.send_heartbeat()
    assert "not yet loaded" in sent5[0], "expected 'not yet loaded' fallback"
    print("[t6] None snapshot handled gracefully: PASS")

    # -----------------------------------------------------------------------
    # Test 7: _format_age helper
    # -----------------------------------------------------------------------
    assert _format_age(5) == "5s"
    assert _format_age(65) == "1m"
    assert _format_age(3661) == "1h 1m"
    print("[t7] _format_age helper: PASS")

    # -----------------------------------------------------------------------
    # Test 8: _extract_kv helper
    # -----------------------------------------------------------------------
    s = "state=STREAMING last_msg=2.1s msgs=500 reconnects=1"
    assert _extract_kv(s, "state") == "STREAMING"
    assert _extract_kv(s, "msgs") == "500"
    assert _extract_kv(s, "missing") is None
    print("[t8] _extract_kv helper: PASS")

    # -----------------------------------------------------------------------
    # Test 9: daily_summary() is a no-op (placeholder)
    # -----------------------------------------------------------------------
    hb.daily_summary()   # must not raise
    print("[t9] daily_summary() is a no-op: PASS")

    print("\nHeartbeatService smoke test passed.")
