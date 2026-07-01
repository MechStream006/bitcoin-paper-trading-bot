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
from typing import TYPE_CHECKING, Callable, List, Optional, Tuple

from src.alerts import forward_status as _forward_status
from src.alerts import storage_status as _storage_status
from src.alerts.delta_tracker import DeltaTracker
from src.alerts.rate_limiter import RateLimiter
from src.runtime.health import HealthStatus
from src.state.market_state import MarketState

if TYPE_CHECKING:
    from src.exchange.ws_manager import WSManager
    from src.expiry.expiry_context import ExpiryContext
    from src.ingestion.chain_refresher import ChainRefresher
    from src.persistence.snapshot_writer import SnapshotWriter

logger = logging.getLogger(__name__)

# D1: universe snapshot older than this multiple of the chain-refresh interval
# is flagged STALE (DEGRADED).  2x tolerates jitter + one missed refresh.
_UNIVERSE_STALE_FACTOR = 2.0
# D2: an interval job whose last success is older than this multiple of its
# interval is flagged stalled (DEGRADED).  3x absorbs jitter / coalesced misfires.
_JOB_STALL_FACTOR = 3.0
# D2: short display labels for the jobs line (falls back to the raw job name).
_JOB_ALIASES = {
    "snapshot_quotes": "snap",
    "chain_refresh":   "chain",
    "expiry_observe":  "expiry",
    "heartbeat":       "hb",
    "daily_summary":   "daily",
    "expiry_cycle_eod": "eod",
}


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
        forward_status_path: Optional path to evidence_forward.db.  When set, the
                            heartbeat appends a read-only forward-evidence summary
                            line and folds it into the overall status.  When None
                            (default), the forward section is omitted entirely.
        data_db_path:       Optional path to the live collector DB (btc_trading.db).
                            When set, the heartbeat renders a storage line (free
                            disk + db/wal sizes) and folds free-disk into status.
                            When None (default), no storage line is emitted.
        emit_deltas:        When True (default), render per-interval deltas
                            (msgs/reconnects/rows since the last successful
                            heartbeat) and worst staleness age.  Display-only —
                            never escalates status.  False reverts the coverage
                            and deltas lines to their prior format.
        chain_refresh_interval_sec: Chain-refresh cadence, used for the D1
                            universe-age warning (age > 2x interval -> DEGRADED).
                            None disables the warning (kill-switch).
        emit_job_ages:      When True (default), render a D2 per-job last-run-age
                            line and escalate a stalled interval job to DEGRADED.
                            Requires set_scheduler(); None scheduler -> no line.
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
        expiry_context: Optional["ExpiryContext"] = None,
        forward_status_path: Optional[str] = None,
        data_db_path: Optional[str] = None,
        emit_deltas: bool = True,
        chain_refresh_interval_sec: Optional[int] = None,
        emit_job_ages: bool = True,
    ) -> None:
        self._sender = sender
        self._ws_manager = ws_manager
        self._market_state = market_state
        self._snapshot_writer = snapshot_writer
        self._chain_refresher = chain_refresher
        self._rate_limiter = rate_limiter
        self._started_at = started_at
        self._heartbeat_category = heartbeat_category
        self._expiry_context = expiry_context
        # Optional: path to evidence_forward.db.  When set, the heartbeat reads
        # the daily forward-evidence cycle READ-ONLY (see alerts.forward_status).
        # When None, the forward section is omitted entirely (backward compatible).
        self._forward_status_path = forward_status_path
        # Optional: path to the live collector DB (btc_trading.db).  When set, the
        # heartbeat renders a storage line (free disk + db/wal sizes) and folds
        # free-disk into the overall status.  When None, no storage line.
        self._data_db_path = data_db_path
        # Per-interval deltas (msgs/reconnects/rows) + worst staleness age.
        # Display-only — never escalates status.  emit_deltas=False reverts the
        # coverage and deltas lines to their prior format (kill-switch).
        self._emit_deltas = emit_deltas
        self._delta_tracker = DeltaTracker()
        # Samples read during the most recent _compose_heartbeat(), committed to
        # the tracker baseline only after a SUCCESSFUL send (see send_heartbeat).
        self._pending_delta: Optional[Tuple[dict, float]] = None
        # D1: chain-refresh interval used for the universe-age warning.  None
        # disables the warning (kill-switch).  D2: per-job last-run ages read
        # from a late-bound SchedulerService (set via set_scheduler()); None
        # scheduler or emit_job_ages=False -> no jobs line.
        self._chain_refresh_interval_sec = chain_refresh_interval_sec
        self._emit_job_ages = emit_job_ages
        self._scheduler: Optional[object] = None

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

    def set_scheduler(self, scheduler: object) -> None:
        """Late-bind the SchedulerService for the D2 per-job-age line.

        The scheduler is constructed after the heartbeat in the runtime
        bootstrap, so it is injected here rather than via the constructor.  Only
        ``job_ages()`` is called (read-only); the heartbeat never drives it.
        """
        self._scheduler = scheduler

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
        # Advance the delta baseline ONLY on a successful send, using exactly the
        # samples read during the composition above.  A failed send leaves the
        # baseline untouched so the next successful delta spans the true gap.
        if self._pending_delta is not None:
            samples, sampled_at = self._pending_delta
            self._delta_tracker.commit(samples, sampled_at)
            self._pending_delta = None
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
        # Forward, disk, universe-age and job-age signals are each read once and
        # reused for both their line and the overall-status roll-up (single read
        # per heartbeat).
        fwd = self._forward_summary()
        disk = self._disk_info()
        universe_stale = self._universe_stale()
        job_ages = self._get_job_ages()
        jobs_stalled = self._jobs_stalled(job_ages)
        level = self._overall_level(
            fwd, disk, universe_stale=universe_stale, jobs_stalled=jobs_stalled,
        )
        as_of = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        lines: List[str] = [
            f"--- BTC Bot Heartbeat [{level}] ---",
            f"as_of        : {as_of}",
            f"uptime       : {self._format_uptime()}",
            self._format_ws_line(),
            self._format_market_line(),
            self._format_snapshot_line(),
            self._format_universe_line(universe_stale),
        ]

        storage_line = self._format_storage_line(disk)
        if storage_line is not None:
            lines.append(storage_line)

        deltas_line = self._format_deltas_line()
        if deltas_line is not None:
            lines.append(deltas_line)

        jobs_line = self._format_jobs_line(job_ages)
        if jobs_line is not None:
            lines.append(jobs_line)

        if fwd is not None:
            lines.append(_forward_status.render_line(fwd))

        for line in self._format_expiry_block():
            lines.append(line)

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
            if stale:
                stale_str = f"{len(stale)} stale"
                if self._emit_deltas:
                    worst = self._worst_staleness()
                    if worst is not None:
                        stale_str += f" (worst {_format_age(worst)})"
            else:
                stale_str = "all fresh"
            return f"coverage     : {covered}/{total} ({cov:.0%}), {stale_str}"
        except Exception:
            return "coverage     : [error reading market state]"

    def _worst_staleness(self) -> Optional[float]:
        """Max age (seconds) among universe symbols that have ticked at least once.

        Uses freshness_sec, which returns None for never-ticked symbols (those are
        coverage gaps, not staleness — see CLAUDE.md sec.11).  Returns None if no
        symbol has ticked yet.  Never raises.
        """
        try:
            ages = [
                self._market_state.freshness_sec(s)
                for s in self._market_state.universe_symbols()
            ]
            seen = [a for a in ages if a is not None]
            return max(seen) if seen else None
        except Exception:
            return None

    def _read_delta_counters(self) -> dict:
        """Snapshot the cumulative counters used for deltas (None where unread)."""
        counters: dict = {"msgs": None, "reconnects": None, "rows": None}
        try:
            d = self._ws_manager.health().details
            counters["msgs"] = _safe_int(_extract_kv(d, "msgs"))
            counters["reconnects"] = _safe_int(_extract_kv(d, "reconnects"))
        except Exception:
            pass
        try:
            d = self._snapshot_writer.health().details
            counters["rows"] = _safe_int(_extract_kv(d, "total_rows"))
        except Exception:
            pass
        return counters

    def _format_deltas_line(self) -> Optional[str]:
        """Render per-interval deltas, or None when disabled.  Never raises.

        Computes deltas against the committed baseline and stashes the freshly
        read samples in ``_pending_delta``; the baseline is advanced only after a
        successful send (see send_heartbeat).  A metric that cannot be computed
        confidently renders 'n/a' rather than a misleading number.
        """
        if not self._emit_deltas:
            return None
        try:
            now = time.monotonic()
            current = self._read_delta_counters()
            deltas, elapsed = self._delta_tracker.compute(current, now)
            self._pending_delta = (current, now)

            def fmt(v: Optional[int]) -> str:
                return f"+{v}" if v is not None else "n/a"

            window = f"(in {_format_age(elapsed)})" if elapsed is not None else "(first sample)"
            return (
                f"deltas       : msgs {fmt(deltas.get('msgs'))}  "
                f"reconn {fmt(deltas.get('reconnects'))}  "
                f"rows {fmt(deltas.get('rows'))}  {window}"
            )
        except Exception:
            self._pending_delta = None
            return "deltas       : [error computing deltas]"

    def _format_snapshot_line(self) -> str:
        try:
            h = self._snapshot_writer.health()
            # health().details: "last_snap=58s total_rows=900 last_rows=30 consec_failures=0"
            last_snap = _extract_kv(h.details, "last_snap") or "unknown"
            last_rows = _extract_kv(h.details, "last_rows") or "?"
            cf = _extract_kv(h.details, "consec_failures") or "0"
            if h.healthy:
                status = "ok"
            else:
                status = f"DEGRADED (cf={cf})"
                reason = self._snapshot_failure_reason()
                if reason:
                    status += f" reason={reason}"
            return f"snapshot     : last={last_snap}, {last_rows} rows, {status}"
        except Exception:
            return "snapshot     : [error reading health]"

    def _snapshot_failure_reason(self) -> str:
        """Read the SnapshotWriter's last write-failure reason, read-only.

        Returns '' if the writer does not expose a reason or the call fails — the
        reason is decorative; it must never break the snapshot line.
        """
        try:
            getter = getattr(self._snapshot_writer, "last_failure_reason", None)
            if getter is None:
                return ""
            return getter() or ""
        except Exception:
            return ""

    def _format_universe_line(self, universe_stale: bool = False) -> str:
        try:
            snap = self._chain_refresher.current_snapshot()
            if snap is None:
                return "universe     : not yet loaded"
            n = len(snap.symbols)
            age_str = _format_age(time.monotonic() - snap.refreshed_at)
            suffix = "  (STALE)" if universe_stale else ""
            return f"universe     : {n} options, refresh {age_str} ago{suffix}"
        except Exception:
            return "universe     : [error reading snapshot]"

    def _universe_stale(self) -> bool:
        """True if the universe snapshot is older than 2x the refresh interval.

        Detects a frozen/failed chain-refresh that coverage masks (frozen symbols
        keep ticking -> coverage stays 100%).  Returns False when the warning is
        disabled (interval None), no snapshot exists yet, or on any error.
        """
        if self._chain_refresh_interval_sec is None:
            return False
        try:
            snap = self._chain_refresher.current_snapshot()
            if snap is None:
                return False
            age = time.monotonic() - snap.refreshed_at
            return age > _UNIVERSE_STALE_FACTOR * self._chain_refresh_interval_sec
        except Exception:
            return False

    def _get_job_ages(self):
        """Read per-job liveness from the late-bound scheduler, or None if off."""
        if not self._emit_job_ages or self._scheduler is None:
            return None
        try:
            return self._scheduler.job_ages()
        except Exception:
            return None

    def _format_jobs_line(self, job_ages) -> Optional[str]:
        """Render the D2 per-job last-run-age line, or None when disabled."""
        if not job_ages:
            return None
        try:
            parts = []
            for name, ja in job_ages.items():
                label = _JOB_ALIASES.get(name, name)
                age_str = (
                    _format_age(ja.last_success_age)
                    if ja.last_success_age is not None else "never"
                )
                parts.append(f"{label} {age_str}")
            return "jobs         : " + "  ".join(parts)
        except Exception:
            return "jobs         : [error reading scheduler]"

    def _jobs_stalled(self, job_ages) -> bool:
        """True if any interval job ran once and is now past 3x its interval.

        Cron jobs (interval_sec None) are excluded.  A never-succeeded job is not
        escalated here (startup transient / covered by scheduler component
        health) — only the ran-then-stalled case escalates.
        """
        if not job_ages:
            return False
        try:
            for ja in job_ages.values():
                if ja.interval_sec is None or ja.last_success_age is None:
                    continue
                if ja.last_success_age > _JOB_STALL_FACTOR * ja.interval_sec:
                    return True
            return False
        except Exception:
            return False

    def _format_expiry_block(self) -> List[str]:
        """Return 0, 1, or 2 lines for the expiry posture section."""
        if self._expiry_context is None:
            return []
        try:
            snap = self._expiry_context.current_snapshot()
            if snap is None:
                return ["expiry       : not yet observed"]

            posture = snap.posture.value

            if snap.nearest_settlement is not None and snap.seconds_to_nearest is not None:
                secs = max(0.0, snap.seconds_to_nearest)
                h = int(secs // 3600)
                m = int((secs % 3600) // 60)
                cls_str = snap.nearest_expiry_class.value if snap.nearest_expiry_class else "?"
                nearest = (
                    f"{cls_str}@{snap.nearest_settlement.strftime('%H:%MZ')}"
                    f" ({h:02d}h{m:02d}m)"
                )
            else:
                nearest = "none"

            cycle_pct = f"cycle={snap.cycle_fraction:.0%}"

            class_map = {cls.value: cnt for cls, cnt in snap.n_by_class}
            d = class_map.get("DAILY", 0)
            w = class_map.get("WEEKLY", 0)
            m_cnt = class_map.get("MONTHLY", 0)
            conf = sum(
                1 for r in snap.records
                if r.classification_mode.value == "CONFIRMED"
            )
            divs = len(snap.divergence_events)

            return [
                f"expiry       : {posture} | {nearest} | {cycle_pct}",
                f"  classes    : D:{d} W:{w} M:{m_cnt} | CONF:{conf} | div:{divs}",
            ]
        except Exception:
            return ["expiry       : [error reading context]"]

    def _collect_degraded(self) -> List[str]:
        """Return one-line summaries for all unhealthy tracked components."""
        result: List[str] = []
        components: list = [
            self._ws_manager, self._snapshot_writer, self._chain_refresher,
        ]
        if self._expiry_context is not None:
            components.append(self._expiry_context)
        for comp in components:
            try:
                h = comp.health()
                if not h.healthy:
                    result.append(f"{h.name}: {h.details}")
            except Exception:
                pass
        return result

    def _forward_summary(self) -> Optional["_forward_status.ForwardStatus"]:
        """Read the forward-evidence cycle READ-ONLY, or None if not configured.

        Never raises: forward_status.summarize() is total, and any unexpected
        error is folded into an 'error' ForwardStatus so the heartbeat still
        renders.
        """
        if not self._forward_status_path:
            return None
        try:
            return _forward_status.summarize(self._forward_status_path)
        except Exception as exc:  # defensive; summarize is designed not to raise
            return _forward_status.ForwardStatus.read_error(
                _forward_status.expected_run_date(),
                f"{type(exc).__name__}: {exc}",
            )

    def _disk_info(self) -> Optional["_storage_status.DiskInfo"]:
        """Free-disk info for the collector data volume, or None if not configured."""
        if not self._data_db_path:
            return None
        try:
            return _storage_status.disk_free(self._data_db_path)
        except Exception as exc:  # defensive; disk_free is designed not to raise
            return _storage_status.DiskInfo(
                False, None, None, None, f"{type(exc).__name__}: {exc}")

    def _format_storage_line(
        self, disk: Optional["_storage_status.DiskInfo"]
    ) -> Optional[str]:
        """Render the storage line (disk free + db/wal sizes), or None if off.

        Uses the pre-computed ``disk`` (so disk is read once per heartbeat) and
        adds db/wal sizes for the collector DB and, when configured, the forward
        DB.  Never raises.
        """
        if not self._data_db_path:
            return None
        try:
            if disk is not None and disk.ok and disk.free_pct is not None:
                hb = _storage_status.human_bytes
                disk_str = (
                    f"disk {disk.free_pct:.0f}% free "
                    f"({hb(disk.free_bytes)}/{hb(disk.total_bytes)})"
                )
            else:
                disk_str = "disk n/a"

            parts = [disk_str]
            live = _storage_status.db_sizes(self._data_db_path)
            if live.ok:
                parts.append(f"db {_storage_status.human_bytes(live.db_bytes)}")
                parts.append(f"wal {_storage_status.human_bytes(live.wal_bytes)}")
            if self._forward_status_path:
                fwd_sz = _storage_status.db_sizes(self._forward_status_path)
                if fwd_sz.ok:
                    total = fwd_sz.db_bytes + fwd_sz.wal_bytes
                    parts.append(f"fwd {_storage_status.human_bytes(total)}")
            return "storage      : " + "  ".join(parts)
        except Exception:
            return "storage      : [error reading storage]"

    def _overall_level(
        self,
        fwd: Optional["_forward_status.ForwardStatus"],
        disk: Optional["_storage_status.DiskInfo"] = None,
        universe_stale: bool = False,
        jobs_stalled: bool = False,
    ) -> str:
        """Roll component health + forward + disk + D1/D2 into OK/DEGRADED/CRITICAL.

        CRITICAL: any component fatal, forward fn_hash_violation, or disk
                  below DISK_CRITICAL_PCT.
        DEGRADED: any component unhealthy (non-fatal), forward
                  stale / signal error / DB unreadable, disk below
                  DISK_DEGRADED_PCT, a stale universe (D1), or a stalled
                  scheduler job (D2).
        OK:       everything healthy (or not configured).
        A 'missing' forward DB and an unknown disk are status-neutral.
        """
        any_fatal = False
        any_unhealthy = False
        components: list = [
            self._ws_manager, self._snapshot_writer, self._chain_refresher,
        ]
        if self._expiry_context is not None:
            components.append(self._expiry_context)
        for comp in components:
            try:
                h = comp.health()
                if not h.healthy:
                    any_unhealthy = True
                    if h.fatal:
                        any_fatal = True
            except Exception:
                any_unhealthy = True

        fwd_sev = fwd.severity() if fwd is not None else "ok"
        disk_sev = disk.severity() if disk is not None else "ok"
        if any_fatal or fwd_sev == "critical" or disk_sev == "critical":
            return "CRITICAL"
        if (any_unhealthy or fwd_sev == "degraded" or disk_sev == "degraded"
                or universe_stale or jobs_stalled):
            return "DEGRADED"
        return "OK"


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _safe_int(s: Optional[str]) -> Optional[int]:
    """Parse an int, or None on any failure (used for delta counters)."""
    if s is None:
        return None
    try:
        return int(s)
    except (ValueError, TypeError):
        return None


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
