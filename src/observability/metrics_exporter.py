"""MetricsExporter -- in-process Prometheus exporter for the BTC bot runtime.

Implements ADR-0001 §11.2/§15.2 (Phase 2). This is observability plumbing
only: it reads existing component state and renders it as Prometheus text.
It contains no business, trading, or strategy logic, and it changes nothing
about what any other component computes or does.

Hard constraints (ADR-0001 §11.2, and this task's explicit requirements)
-------------------------------------------------------------------------
- Localhost only: the HTTP server binds 127.0.0.1 unconditionally. This is
  not an operator-configurable default -- there is no parameter that can
  widen it to 0.0.0.0.
- M4 -- never opens SQLite on the scrape path. collect() reads only
  in-memory state: MarketState counters, WSManager/SchedulerService/
  HeartbeatService health() snapshots, and HeartbeatService's already-cached
  last_forward_status() (a value HeartbeatService computed on its own
  existing schedule -- see src/alerts/heartbeat.py -- never re-read here).
- M5 -- never raises into the scrape thread or the runtime. Every metric
  group is collected in its own try/except; one group failing does not
  affect the others, and collect() itself cannot propagate an exception.
- M6 -- exporter failure degrades to no-metrics, never a runtime fault.
  start() catches its own setup failures and logs rather than raising, so a
  bind failure (e.g. port already in use) cannot prevent the bot itself
  from starting.

Not registered with Supervisor. Supervisor's job is trading-runtime health;
wiring this component's health() into that escalation path would let an
observability failure influence restart/halt decisions for the trading
runtime, which is exactly what M6 rules out. HeartbeatService's own
[DEGRADED] block similarly never lists this component (see
_collect_degraded() in heartbeat.py).

Dependency direction
---------------------
  observability.metrics_exporter
    -> state.market_state          (read-only: coverage / universe / reconcile)
    -> alerts.heartbeat            (read-only: health() + last_forward_status())
    -> exchange.ws_manager         (read-only: health())            [TYPE_CHECKING]
    -> scheduler.runner            (read-only: health() / job_ages()) [TYPE_CHECKING]
    -> persistence.snapshot_writer (read-only: health())            [TYPE_CHECKING]
    -> ingestion.chain_refresher   (read-only: health())            [TYPE_CHECKING]
    -> expiry.expiry_context       (read-only: health(), optional)  [TYPE_CHECKING]
"""
from __future__ import annotations

import logging
import threading
from datetime import date
from typing import TYPE_CHECKING, Optional

from prometheus_client import REGISTRY, start_http_server
from prometheus_client.core import GaugeMetricFamily

from src.alerts.heartbeat import _extract_kv, _safe_int
from src.runtime.health import HealthStatus

if TYPE_CHECKING:
    from src.alerts.heartbeat import HeartbeatService
    from src.exchange.ws_manager import WSManager
    from src.expiry.expiry_context import ExpiryContext
    from src.ingestion.chain_refresher import ChainRefresher
    from src.persistence.snapshot_writer import SnapshotWriter
    from src.scheduler.runner import SchedulerService
    from src.state.market_state import MarketState

logger = logging.getLogger(__name__)

# Matches SnapshotWriter.STALE_THRESHOLD_SEC / HeartbeatService's own 30s
# "N stale" threshold, so this metric agrees with the Telegram heartbeat line.
STALE_THRESHOLD_SEC = 30.0

# fn_hash_violation is the tamper-detection status from
# src/alerts/forward_status.py's run_log status vocabulary.
_FN_HASH_VIOLATION_STATUS = "fn_hash_violation"


class MetricsExporter:
    """Read-only Prometheus exporter over existing runtime component state.

    Lifecycle
    ---------
      exp = MetricsExporter(
          market_state=ms, ws_manager=wsm, snapshot_writer=sw,
          chain_refresher=cr, scheduler=sched, heartbeat=hb,
          expiry_context=ec, port=9108,
      )
      exp.start()   # binds 127.0.0.1:port, registers collector; never raises
      ...
      exp.stop()

    Args:
        market_state:     MarketState — coverage / universe / reconcile counters.
        ws_manager:       WSManager — health() for WS state/msgs/reconnects.
        snapshot_writer:  SnapshotWriter — included in the component-health rollup.
        chain_refresher:  ChainRefresher — included in the component-health rollup.
        scheduler:        SchedulerService — health() + job_ages().
        heartbeat:        HeartbeatService — health() + last_forward_status().
        expiry_context:   Optional ExpiryContext — included in the rollup if set.
        host:             Bind address. Not exposed to settings/env — hardcoded
                          127.0.0.1 by the caller; kept as a parameter only so
                          tests can bind an ephemeral port on the same address.
        port:             Bind port (default 9108, per ADR-0001 §8).
    """

    COMPONENT_NAME = "metrics_exporter"

    def __init__(
        self,
        market_state: "MarketState",
        ws_manager: "WSManager",
        snapshot_writer: "SnapshotWriter",
        chain_refresher: "ChainRefresher",
        scheduler: "SchedulerService",
        heartbeat: "HeartbeatService",
        expiry_context: Optional["ExpiryContext"] = None,
        host: str = "127.0.0.1",
        port: int = 9108,
    ) -> None:
        self._market_state = market_state
        self._ws_manager = ws_manager
        self._scheduler = scheduler
        self._heartbeat = heartbeat
        self._host = host
        self._port = port

        # Iterated by _collect_component_health() for the generic health
        # rollup. Order is display order only (labels, not positional).
        self._components: dict = {
            "ws_manager": ws_manager,
            "snapshot_writer": snapshot_writer,
            "chain_refresher": chain_refresher,
            "scheduler": scheduler,
            "heartbeat": heartbeat,
        }
        if expiry_context is not None:
            self._components["expiry_context"] = expiry_context

        self._lock = threading.Lock()
        self._server = None
        self._thread: Optional[threading.Thread] = None
        self._registered = False

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    def start(self) -> None:
        """Bind the HTTP server and register this collector. Idempotent.

        Never raises (M6): any failure (e.g. port already in use) is caught
        and logged at ERROR level. The bot must be able to start with metrics
        unavailable -- a bind failure here must never abort bootstrap.
        """
        with self._lock:
            if self._server is not None:
                logger.warning(
                    "[metrics] start() called while already running -- ignoring"
                )
                return
            try:
                REGISTRY.register(self)
                self._registered = True
                server, thread = start_http_server(
                    self._port, addr=self._host, registry=REGISTRY
                )
                self._server = server
                self._thread = thread
                logger.info(
                    "[metrics] exporter listening on %s:%d", self._host, self._port
                )
            except Exception as exc:
                logger.error(
                    "[metrics] failed to start exporter on %s:%d: %s: %s -- "
                    "continuing without metrics",
                    self._host, self._port, type(exc).__name__, exc,
                )
                if self._registered:
                    try:
                        REGISTRY.unregister(self)
                    except Exception:
                        pass
                    self._registered = False
                self._server = None
                self._thread = None

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the HTTP server and unregister this collector. Never raises."""
        with self._lock:
            if self._server is not None:
                try:
                    self._server.shutdown()
                except Exception as exc:
                    logger.warning("[metrics] server shutdown error: %s", exc)
                if self._thread is not None:
                    self._thread.join(timeout=timeout)
                self._server = None
                self._thread = None
            if self._registered:
                try:
                    REGISTRY.unregister(self)
                except Exception:
                    pass
                self._registered = False
        logger.info("[metrics] stopped")

    def health(self) -> HealthStatus:
        """Return whether the HTTP server is currently bound.

        Not registered with Supervisor (see module docstring) -- this exists
        for symmetry with other components and for manual/future diagnostic
        use, not to feed any restart/halt decision.
        """
        with self._lock:
            bound = self._server is not None
        details = (
            f"listening={self._host}:{self._port}" if bound
            else "not listening"
        )
        return HealthStatus(
            name=self.COMPONENT_NAME,
            healthy=bound,
            fatal=False,
            details=details,
            last_check_at=_utcnow(),
        )

    # -----------------------------------------------------------------------
    # prometheus_client Collector protocol
    # -----------------------------------------------------------------------

    def collect(self):
        """Called synchronously by the scrape HTTP handler thread.

        Each metric group is collected independently. A failure in one group
        is logged and skipped; it never prevents the others from being
        rendered, and it never raises out of this method (M5).
        """
        for builder in (
            self._collect_market_state,
            self._collect_ws,
            self._collect_scheduler,
            self._collect_heartbeat,
            self._collect_forward_evidence,
            self._collect_component_health,
        ):
            try:
                yield from builder()
            except Exception as exc:
                logger.error(
                    "[metrics] collector %s raised: %s: %s",
                    builder.__name__, type(exc).__name__, exc,
                )

    # -----------------------------------------------------------------------
    # Metric group builders (each independently exception-safe)
    # -----------------------------------------------------------------------

    def _collect_market_state(self):
        try:
            ms = self._market_state
            universe_size = ms.universe_size()
            coverage = ms.coverage_pct()
            stale = len(ms.stale_symbols(threshold_sec=STALE_THRESHOLD_SEC))
            reconciles = ms.reconcile_count()
        except Exception as exc:
            logger.warning(
                "[metrics] market_state collection failed: %s: %s",
                type(exc).__name__, exc,
            )
            return
        yield GaugeMetricFamily(
            "btcbot_universe_size",
            "Current option universe size (symbol count).",
            value=float(universe_size),
        )
        yield GaugeMetricFamily(
            "btcbot_coverage_ratio",
            "Fraction of universe symbols with at least one received tick (0-1).",
            value=coverage,
        )
        yield GaugeMetricFamily(
            "btcbot_stale_symbols",
            f"Universe symbols with no tick within {STALE_THRESHOLD_SEC:.0f}s.",
            value=float(stale),
        )
        yield GaugeMetricFamily(
            "btcbot_reconcile_count_total",
            "Cumulative non-no-op universe reconciliations since process start.",
            value=float(reconciles),
        )

    def _collect_ws(self):
        try:
            h = self._ws_manager.health()
        except Exception as exc:
            logger.warning(
                "[metrics] ws_manager health() failed: %s: %s",
                type(exc).__name__, exc,
            )
            return
        yield GaugeMetricFamily(
            "btcbot_ws_connected",
            "1 if WSManager.health() reports healthy, 0 otherwise.",
            value=1.0 if h.healthy else 0.0,
        )
        msgs = _safe_int(_extract_kv(h.details, "msgs"))
        if msgs is not None:
            yield GaugeMetricFamily(
                "btcbot_ws_messages_total",
                "Cumulative WS ticker messages received this process.",
                value=float(msgs),
            )
        reconnects = _safe_int(_extract_kv(h.details, "reconnects"))
        if reconnects is not None:
            yield GaugeMetricFamily(
                "btcbot_ws_reconnects_total",
                "Cumulative WS reconnect attempts this process.",
                value=float(reconnects),
            )
        age = _parse_age_seconds(_extract_kv(h.details, "last_msg"))
        if age is not None:
            yield GaugeMetricFamily(
                "btcbot_ws_last_message_age_seconds",
                "Seconds since the last WS message was received.",
                value=age,
            )

    def _collect_scheduler(self):
        try:
            h = self._scheduler.health()
        except Exception as exc:
            logger.warning(
                "[metrics] scheduler health() failed: %s: %s",
                type(exc).__name__, exc,
            )
        else:
            yield GaugeMetricFamily(
                "btcbot_scheduler_healthy",
                "1 if SchedulerService.health() reports healthy, 0 otherwise.",
                value=1.0 if h.healthy else 0.0,
            )

        try:
            ages = self._scheduler.job_ages()
        except Exception as exc:
            logger.warning(
                "[metrics] scheduler job_ages() failed: %s: %s",
                type(exc).__name__, exc,
            )
            return
        fam = GaugeMetricFamily(
            "btcbot_scheduler_job_last_success_age_seconds",
            "Seconds since each scheduled job last succeeded.",
            labels=["job"],
        )
        any_age = False
        for name, ja in ages.items():
            if ja.last_success_age is not None:
                fam.add_metric([name], ja.last_success_age)
                any_age = True
        if any_age:
            yield fam

    def _collect_heartbeat(self):
        try:
            h = self._heartbeat.health()
        except Exception as exc:
            logger.warning(
                "[metrics] heartbeat health() failed: %s: %s",
                type(exc).__name__, exc,
            )
            return
        yield GaugeMetricFamily(
            "btcbot_heartbeat_healthy",
            "1 if the last heartbeat send succeeded, 0 otherwise.",
            value=1.0 if h.healthy else 0.0,
        )
        total = _safe_int(_extract_kv(h.details, "total_sent"))
        if total is not None:
            yield GaugeMetricFamily(
                "btcbot_heartbeat_sent_total",
                "Cumulative heartbeats successfully sent this process.",
                value=float(total),
            )
        age = _parse_age_seconds(_extract_kv(h.details, "last_send"))
        if age is not None:
            yield GaugeMetricFamily(
                "btcbot_heartbeat_last_send_age_seconds",
                "Seconds since the last successful heartbeat send.",
                value=age,
            )

    def _collect_forward_evidence(self):
        """Forward-evidence metrics -- from HeartbeatService's cache only.

        This NEVER reads evidence_forward.db itself (M4): last_forward_status()
        returns whatever HeartbeatService last computed on its own existing
        schedule (see src/alerts/heartbeat.py). None means either
        forward_status_path was never configured or no heartbeat has composed
        yet (a brief startup window) -- both cases simply emit nothing.
        """
        try:
            fs = self._heartbeat.last_forward_status()
        except Exception as exc:
            logger.warning(
                "[metrics] last_forward_status() failed: %s: %s",
                type(exc).__name__, exc,
            )
            return
        if fs is None:
            return

        days_behind = _days_behind(fs.latest_run_date, fs.expected_run_date)
        if days_behind is not None:
            yield GaugeMetricFamily(
                "btcbot_forward_days_behind",
                "Days the forward-evidence cycle's latest run is behind the "
                "expected run date.",
                value=float(days_behind),
            )
        yield GaugeMetricFamily(
            "btcbot_forward_signals_ok",
            "Signals with status=ok in the latest forward-evidence run.",
            value=float(fs.n_ok),
        )
        yield GaugeMetricFamily(
            "btcbot_forward_signals_logged",
            "Signals logged (any status) in the latest forward-evidence run.",
            value=float(fs.n_logged),
        )
        violations = sum(
            1 for st in fs.signals.values() if st == _FN_HASH_VIOLATION_STATUS
        )
        yield GaugeMetricFamily(
            "btcbot_forward_fn_hash_violations",
            "Signals with fn_hash_violation status in the latest "
            "forward-evidence run (tamper detection).",
            value=float(violations),
        )

    def _collect_component_health(self):
        """Generic health rollup across all tracked components, one family."""
        fam = GaugeMetricFamily(
            "btcbot_component_healthy",
            "1 if component.health() reports healthy, 0 otherwise.",
            labels=["component"],
        )
        any_ok = False
        for name, comp in self._components.items():
            try:
                h = comp.health()
                fam.add_metric([name], 1.0 if h.healthy else 0.0)
                any_ok = True
            except Exception as exc:
                logger.warning(
                    "[metrics] health() failed for component=%s: %s: %s",
                    name, type(exc).__name__, exc,
                )
        if any_ok:
            yield fam


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _utcnow():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


def _parse_age_seconds(raw: Optional[str]) -> Optional[float]:
    """Parse a health-details age token like '2.1s' or 'never' -> float seconds.

    Returns None for 'never', a missing token, or any unparseable value.
    Mirrors the age-string convention already used by WSManager.health() and
    HeartbeatService.health() (`f"{age:.1f}s"` / `"never"`).
    """
    if not raw or raw == "never" or not raw.endswith("s"):
        return None
    try:
        return float(raw[:-1])
    except ValueError:
        return None


def _days_behind(latest: Optional[str], expected: str) -> Optional[int]:
    """Days `latest` (an ISO date string) is behind `expected`. None-safe.

    Mirrors scripts/healthcheck.py's `_days_behind()`: None if latest is
    unknown; clamped to 0 (never negative) if latest is at or after expected.
    """
    if latest is None:
        return None
    try:
        diff = (date.fromisoformat(expected) - date.fromisoformat(latest)).days
    except ValueError:
        return None
    return max(0, diff)
