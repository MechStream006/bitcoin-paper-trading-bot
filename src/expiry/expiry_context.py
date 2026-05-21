"""ExpiryContext — supervised, scheduler-driven expiry posture engine.

ExpiryContext is the single source of truth for the runtime's expiry posture.
It OBSERVES the option universe (from ChainRefresher) and wall-clock time,
derives posture, and stores the result as an immutable ExpirySnapshot.

Ownership rules
---------------
- Scheduler calls observe() on a configured cadence (default 300 s).
- Only ExpiryContext calls build_calendar() and derive_posture().
- No other component recomputes posture independently.
- ExpiryContext never writes to the DB, never modifies MarketState, never
  changes WS subscriptions.
- ExpiryContext fires on_settlement_trigger() when settlement crossings are
  detected at the end of a cycle.  It does NOT name symbols to retire — it
  only signals that a settlement event occurred.  The trigger callback is
  owned by Runtime; ChainRefresher remains the sole authoritative universe
  source.

Concurrency contract
--------------------
observe() is called from the scheduler thread.
emit_cycle_summary() is called from a separate scheduler job at 12:05 UTC.
health() / current_snapshot() / current_posture() may be called from any thread.
_lock guards all mutable state fields.
No lock is held during build_calendar() (pure computation) or analytics I/O.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable, Dict, List, Optional

from src.expiry.expiry_calendar import ExpiryCalendar, build_calendar
from src.expiry.expiry_clock import last_cycle_anchor
from src.expiry.models import ExpiryConfig, ExpiryPosture, ExpirySnapshot
from src.runtime.health import HealthStatus

if TYPE_CHECKING:
    from src.reports.eod import ExpiryAnalyticsWriter

logger = logging.getLogger(__name__)

_UTC = timezone.utc
_NOW_FN = Callable[[], datetime]


def _fresh_cycle_stats() -> dict:
    """Return a zeroed cycle-stat accumulator."""
    return {"posture_ticks": {}, "class_ticks": {}, "total_ticks": 0, "divergence_count": 0}


class ExpiryContext:
    """Observe-and-tag component.  No background threads.  Scheduler-driven.

    Args:
        chain_refresher:    ChainRefresher instance (provides current_snapshot()).
                            Duck-typed: must have current_snapshot() -> obj with
                            .universe: list[dict] and .mark_price: float.
        config:             ExpiryConfig; uses default if None.
        now_fn:             Injectable UTC clock for testing.
        stale_threshold_sec: health() reports DEGRADED if last observe is older.
        analytics_writer:   ExpiryAnalyticsWriter for JSONL telemetry (optional).
                            When None, telemetry events are silently skipped.
    """

    COMPONENT_NAME = "expiry_context"

    def __init__(
        self,
        chain_refresher: object,
        config: Optional[ExpiryConfig] = None,
        now_fn: Optional[_NOW_FN] = None,
        stale_threshold_sec: float = 600.0,
        analytics_writer: Optional["ExpiryAnalyticsWriter"] = None,
        on_settlement_trigger: Optional[Callable[[], None]] = None,
    ) -> None:
        self._refresher = chain_refresher
        self._config = config or ExpiryConfig()
        self._now_fn = now_fn
        self._stale_sec = stale_threshold_sec
        self._analytics = analytics_writer
        self._on_settlement_trigger = on_settlement_trigger

        self._lock = threading.Lock()

        # Core observe state
        self._snapshot: Optional[ExpirySnapshot] = None
        self._observe_count: int = 0
        self._consecutive_failures: int = 0
        self._last_class_map: dict = {}   # {settlement_time_iso: ExpiryClass}
        self._started: bool = False

        # Phase 2 telemetry state (all guarded by _lock)
        self._prev_posture: Optional[ExpiryPosture] = None
        self._cycle_stats: dict = _fresh_cycle_stats()
        self._settled_symbols: set = set()                      # symbols already crossed
        self._cycle_settlement_crossings: List[dict] = []       # crossings this cycle

    # -----------------------------------------------------------------------
    # Lifecycle (Component contract: start / stop / health)
    # -----------------------------------------------------------------------

    def start(self) -> None:
        if self._started:
            logger.warning("[expiry_context] start() called twice — ignoring")
            return
        self._started = True
        logger.info(
            "[expiry_context] ready (stale_threshold=%.0fs settlement=%02d:%02dZ analytics=%s)",
            self._stale_sec,
            self._config.settlement_hour_utc,
            self._config.settlement_minute_utc,
            "on" if self._analytics is not None else "off",
        )

    def stop(self) -> None:
        self._started = False
        logger.info("[expiry_context] stopped")

    def health(self) -> HealthStatus:
        """Healthy when observe() has succeeded at least once and is not stale."""
        with self._lock:
            snap = self._snapshot
            cf = self._consecutive_failures
            count = self._observe_count

        now = datetime.now(_UTC)

        if snap is None:
            return HealthStatus(
                name=self.COMPONENT_NAME,
                healthy=(cf == 0),
                fatal=False,
                details=f"no_snapshot observe_count={count} consec_failures={cf}",
                last_check_at=now,
            )

        age_sec = (now - snap.captured_at).total_seconds()
        stale = age_sec > self._stale_sec
        return HealthStatus(
            name=self.COMPONENT_NAME,
            healthy=(not stale and cf == 0),
            fatal=False,
            details=(
                f"posture={snap.posture.value} "
                f"nearest={_fmt_nearest(snap)} "
                f"age={age_sec:.0f}s "
                f"observe_count={count} "
                f"consec_failures={cf}"
            ),
            last_check_at=now,
        )

    # -----------------------------------------------------------------------
    # Observe (called by scheduler — must not raise)
    # -----------------------------------------------------------------------

    def observe(self) -> Optional[ExpirySnapshot]:
        """Build a fresh ExpirySnapshot from the current universe + wall-clock.

        Returns the new snapshot on success, or None if the chain snapshot is
        not yet available (bootstrap timing race).
        Never raises — exceptions are caught, counted, and logged.
        """
        try:
            return self._do_observe()
        except Exception as exc:
            with self._lock:
                self._consecutive_failures += 1
                cf = self._consecutive_failures
            logger.error(
                "[expiry_context] observe() failed (consecutive=%d): %s: %s",
                cf, type(exc).__name__, exc,
            )
            return None

    def _do_observe(self) -> Optional[ExpirySnapshot]:
        cr_snap = self._refresher.current_snapshot()
        if cr_snap is None:
            logger.debug("[expiry_context] no chain snapshot yet — skipping")
            return None

        universe = cr_snap.universe
        mark_price = cr_snap.mark_price

        with self._lock:
            prev_class_map = dict(self._last_class_map)
            count = self._observe_count + 1
            prev_posture = self._prev_posture
            settled_syms = set(self._settled_symbols)

        # Pure computation — no lock held
        calendar: ExpiryCalendar = build_calendar(
            universe=universe,
            mark_price=mark_price,
            config=self._config,
            now_fn=self._now_fn,
            prev_class_map=prev_class_map,
        )

        posture = calendar.derive_posture(self._config)
        snapshot = calendar.to_snapshot(posture=posture, observe_count=count)

        # Build updated drift map
        new_class_map = {
            r.settlement_time.isoformat(): r.expiry_class
            for r in snapshot.records
        }

        # Detect newly-settled contracts (first time seconds_to_settlement < 0)
        newly_settled = [
            {
                "symbol": r.symbol,
                "expiry_class": r.expiry_class.value,
                "settled_at": r.settlement_time.isoformat(),
                "observe_at": snapshot.captured_at.isoformat(),
            }
            for r in snapshot.records
            if r.seconds_to_settlement < 0 and r.symbol not in settled_syms
        ]

        # Atomic state update
        with self._lock:
            self._snapshot = snapshot
            self._observe_count = count
            self._last_class_map = new_class_map
            self._consecutive_failures = 0
            self._prev_posture = snapshot.posture

            # Cycle-stat accumulation
            p = snapshot.posture.value
            self._cycle_stats["posture_ticks"][p] = (
                self._cycle_stats["posture_ticks"].get(p, 0) + 1
            )
            if snapshot.nearest_expiry_class is not None:
                k = snapshot.nearest_expiry_class.value
                self._cycle_stats["class_ticks"][k] = (
                    self._cycle_stats["class_ticks"].get(k, 0) + 1
                )
            self._cycle_stats["total_ticks"] += 1
            self._cycle_stats["divergence_count"] += len(snapshot.divergence_events)

            # Settlement-crossing bookkeeping
            for c in newly_settled:
                self._settled_symbols.add(c["symbol"])
            self._cycle_settlement_crossings.extend(newly_settled)

        # Post-lock: log crossings (no lock needed — newly_settled is local)
        for c in newly_settled:
            logger.info(
                "[expiry_context] settlement crossing: %s (%s)",
                c["symbol"], c["expiry_class"],
            )

        # Posture transition (compare prev captured before lock)
        if prev_posture is not None and prev_posture != snapshot.posture:
            self._on_posture_transition(prev_posture, snapshot)

        self._emit_log(snapshot)
        return snapshot

    # -----------------------------------------------------------------------
    # Cycle summary (called by scheduler at 12:05 UTC)
    # -----------------------------------------------------------------------

    def emit_cycle_summary(self) -> None:
        """Emit a JSONL cycle_summary event, fire settlement trigger, and reset.

        Called by the expiry_cycle_eod scheduler job at 12:05 UTC, five
        minutes after the 12:00 UTC settlement boundary so all crossing
        events from the closing cycle have been captured.

        Settlement trigger
        ------------------
        If on_settlement_trigger was provided and settlement crossings occurred
        this cycle, the callback is fired BEFORE analytics writes.  The trigger
        is always checked regardless of analytics_writer configuration.

        State reset
        -----------
        Cycle accumulators (_cycle_stats, _cycle_settlement_crossings,
        _settled_symbols) are always reset at the end, regardless of whether
        analytics writes succeeded.
        """
        with self._lock:
            stats = {
                "posture_ticks": dict(self._cycle_stats["posture_ticks"]),
                "class_ticks": dict(self._cycle_stats["class_ticks"]),
                "total_ticks": self._cycle_stats["total_ticks"],
                "divergence_count": self._cycle_stats["divergence_count"],
            }
            crossings = list(self._cycle_settlement_crossings)
            snap = self._snapshot

        # Settlement trigger — fires regardless of analytics_writer configuration.
        # ExpiryContext only signals that a settlement event occurred; it does not
        # name symbols to retire.  ChainRefresher remains the authoritative source.
        if crossings and self._on_settlement_trigger is not None:
            logger.info(
                "[expiry_context] settlement trigger: %d crossing(s) — signalling runtime",
                len(crossings),
            )
            try:
                self._on_settlement_trigger()
            except Exception as exc:
                logger.error(
                    "[expiry_context] on_settlement_trigger raised: %s: %s",
                    type(exc).__name__, exc,
                )

        if self._analytics is not None:
            # Telemetry: record that the trigger fired (only when crossings present)
            if crossings:
                self._analytics.settlement_trigger_fired(
                    crossing_count=len(crossings),
                    crossings=crossings,
                )

            if self._now_fn is not None:
                now = self._now_fn()
            else:
                now = datetime.now(_UTC)

            anchor = last_cycle_anchor(
                now,
                self._config.settlement_hour_utc,
                self._config.settlement_minute_utc,
            )
            final_summary = _snapshot_to_summary(snap)

            self._analytics.cycle_summary(
                cycle_anchor_iso=anchor.isoformat(),
                posture_ticks=stats["posture_ticks"],
                class_ticks=stats["class_ticks"],
                total_ticks=stats["total_ticks"],
                divergence_count=stats["divergence_count"],
                settlement_crossings=crossings,
                final_snapshot_summary=final_summary,
            )

            logger.info(
                "[expiry_context] cycle_summary emitted (anchor=%s total_ticks=%d crossings=%d)",
                anchor.isoformat(), stats["total_ticks"], len(crossings),
            )

        # Always reset for the next cycle so crossings do not persist across days
        with self._lock:
            self._cycle_stats = _fresh_cycle_stats()
            self._cycle_settlement_crossings = []
            self._settled_symbols = set()

    # -----------------------------------------------------------------------
    # Read accessors (any thread)
    # -----------------------------------------------------------------------

    def current_snapshot(self) -> Optional[ExpirySnapshot]:
        """Latest ExpirySnapshot, or None if observe() has not fired yet."""
        with self._lock:
            return self._snapshot

    def current_posture(self) -> ExpiryPosture:
        """Current runtime posture; defaults to NORMAL before first observe."""
        with self._lock:
            snap = self._snapshot
        return snap.posture if snap is not None else ExpiryPosture.NORMAL

    def summary_dict(self) -> dict:
        """JSON-serializable summary for heartbeat and monitoring."""
        with self._lock:
            snap = self._snapshot
        if snap is None:
            return {"posture": "UNKNOWN", "observe_count": 0}
        return {
            "posture": snap.posture.value,
            "observe_count": snap.observe_count,
            "nearest_settlement": (
                snap.nearest_settlement.isoformat()
                if snap.nearest_settlement else None
            ),
            "seconds_to_nearest": snap.seconds_to_nearest,
            "nearest_expiry_class": (
                snap.nearest_expiry_class.value
                if snap.nearest_expiry_class else None
            ),
            "cycle_fraction": round(snap.cycle_fraction, 4),
            "n_records": len(snap.records),
            "n_by_class": {
                cls.value: count for cls, count in snap.n_by_class
            },
            "divergences": snap.divergence_events,
        }

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _on_posture_transition(
        self, prev: ExpiryPosture, snap: ExpirySnapshot
    ) -> None:
        """Log and optionally emit JSONL for a posture change."""
        logger.info(
            "[expiry_context] posture transition: %s -> %s (nearest=%s cycle=%.1f%%)",
            prev.value, snap.posture.value,
            _fmt_nearest(snap), snap.cycle_fraction * 100,
        )
        if self._analytics is not None:
            self._analytics.posture_transition(
                prev_posture=prev.value,
                new_posture=snap.posture.value,
                seconds_to_nearest=snap.seconds_to_nearest,
                nearest_class=(
                    snap.nearest_expiry_class.value
                    if snap.nearest_expiry_class else None
                ),
                nearest_settlement_iso=(
                    snap.nearest_settlement.isoformat()
                    if snap.nearest_settlement else None
                ),
                cycle_fraction=snap.cycle_fraction,
                observe_count=snap.observe_count,
            )

    def _emit_log(self, snap: ExpirySnapshot) -> None:
        n_settled = sum(1 for r in snap.records if r.seconds_to_settlement < 0)
        n_active = len(snap.records) - n_settled
        div_str = f" DIVERGENCE:{snap.divergence_events}" if snap.divergence_events else ""

        logger.info(
            "[expiry_context] #%d posture=%s nearest=%s "
            "active=%d settled=%d cycle=%.1f%% mark=%.0f%s",
            snap.observe_count,
            snap.posture.value,
            _fmt_nearest(snap),
            n_active,
            n_settled,
            snap.cycle_fraction * 100.0,
            snap.mark_price,
            div_str,
        )

        if logger.isEnabledFor(logging.DEBUG):
            for cls, count in snap.n_by_class:
                logger.debug(
                    "[expiry_context]   class=%s count=%d", cls.value, count
                )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _fmt_nearest(snap: ExpirySnapshot) -> str:
    if snap.nearest_settlement is None or snap.seconds_to_nearest is None:
        return "none"
    secs = max(0.0, snap.seconds_to_nearest)
    h = int(secs // 3600)
    m = int((secs % 3600) // 60)
    cls_str = snap.nearest_expiry_class.value if snap.nearest_expiry_class else "?"
    return f"{cls_str}@{snap.nearest_settlement.strftime('%H:%MZ')}({h:02d}h{m:02d}m)"


def _snapshot_to_summary(snap: Optional[ExpirySnapshot]) -> dict:
    """Compact JSON-serializable summary for cycle_summary final_snapshot field."""
    if snap is None:
        return {"posture": "UNKNOWN"}
    return {
        "posture": snap.posture.value,
        "nearest_settlement": (
            snap.nearest_settlement.isoformat() if snap.nearest_settlement else None
        ),
        "cycle_fraction": round(snap.cycle_fraction, 4),
    }
