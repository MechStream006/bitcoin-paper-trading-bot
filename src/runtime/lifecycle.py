"""Runtime -- top-level lifecycle orchestrator for the BTC paper trading bot.

Responsibilities
----------------
- Construct and wire all components (single composition root).
- Own the bootstrap -> warmup -> run -> drain lifecycle.
- Install signal handlers for clean shutdown.
- Coordinate warmup: wait until MarketState has sufficient data coverage.
- Drain: stop components in reverse-start order; close DB cleanly.

What the Runtime does NOT do
------------------------------
- No business logic, strategy, or execution logic.
- No direct scheduler manipulation (uses SchedulerService exclusively).
- No component-specific recovery hacks.
- No hidden globals; every dependency is constructor-injected.

State machine
-------------
  INIT -> BOOTSTRAPPING  (bootstrap() called)
  BOOTSTRAPPING -> WARMING  (run() starts components)
  WARMING -> RUNNING    (warmup coverage threshold met)
  RUNNING -> DEGRADED   (_on_fatal callback triggered)
  RUNNING/DEGRADED -> DRAINING  (drain() called)
  DRAINING -> SHUTDOWN  (all components stopped, DB closed)

Dependency construction order (bootstrap)
------------------------------------------
  1. DB (get_connection + init_schema)
  2. DeltaClient (REST auth)
  3. ChainRefresher.refresh() -> initial universe
  4. MarketState (universe from step 3)
  5. WSManager (channels from step 3)
  6. SnapshotWriter (conn + market_state)
  7. Sender adapter (Telegram or log stub)
  8. RateLimiter + HeartbeatService
  9. SchedulerService + job registrations
  10. Supervisor (registers all components)
  11. Signal handlers

Component start order (run)
----------------------------
  1. WSManager       (opens WS thread; data starts flowing)
  2. ChainRefresher  (stub start)
  3. SnapshotWriter  (stub start)
  4. HeartbeatService (stub start)
  [warmup: wait for MarketState coverage]
  5. SchedulerService (starts APScheduler; jobs begin firing)

Component stop order (drain -- reverse of start)
-------------------------------------------------
  1. SchedulerService.stop(wait=True)  [jobs drain first]
  [shutdown notice sent]
  2. HeartbeatService.stop()
  3. SnapshotWriter.stop()
  4. ChainRefresher.stop()
  5. WSManager.stop()
  6. DB close
"""
from __future__ import annotations

import enum
import logging
import signal
import sqlite3
import threading
import time
from functools import partial
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple

from src.alerts.heartbeat import HeartbeatService
from src.alerts.rate_limiter import RateLimiter
from src.alerts.telegram import send_message
from src.data.storage import get_connection, init_schema
from src.exchange.delta_client import DeltaClient
from src.exchange.ws_manager import WSManager
from src.expiry.expiry_context import ExpiryContext
from src.expiry.models import ExpiryConfig
from src.reports.eod import ExpiryAnalyticsWriter
from src.ingestion.chain_refresher import ChainRefresher
from src.persistence.snapshot_writer import SnapshotWriter
from src.runtime.health import HealthStatus
from src.runtime.supervisor import Supervisor
from src.scheduler.runner import SchedulerService
from src.state.market_state import MarketState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Runtime state enum
# ---------------------------------------------------------------------------

class RuntimeState(enum.Enum):
    """Explicit lifecycle states.  All transitions logged via _set_state()."""
    INIT          = "INIT"
    BOOTSTRAPPING = "BOOTSTRAPPING"
    WARMING       = "WARMING"
    RUNNING       = "RUNNING"
    DEGRADED      = "DEGRADED"
    DRAINING      = "DRAINING"
    SHUTDOWN      = "SHUTDOWN"


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------

class Runtime:
    """Composition root and lifecycle orchestrator.

    Public API
    ----------
      rt = Runtime(db_path=..., delta_base_url=..., ...)
      rt.bootstrap()   # wire components; fatal on error
      rt.run()         # start, warmup, supervise (blocking)
      rt.drain()       # graceful shutdown (idempotent)

    Args (all keyword-only):
        db_path:                    SQLite database file path.
        delta_base_url:             Delta REST base URL.
        delta_api_key:              Delta API key.
        delta_api_secret:           Delta API secret.
        delta_ws_url:               Delta WebSocket URL.
        telegram_bot_token:         Telegram bot token (empty -> log stub).
        telegram_channel_id:        Telegram channel ID (empty -> log stub).
        n_strikes:                  Option strikes above/below ATM per expiry.
        n_expiries:                 Number of upcoming expiries in universe.
        snapshot_interval_sec:      Cadence for snapshot_quotes scheduler job.
        chain_refresh_interval_sec: Cadence for chain_refresh scheduler job.
        heartbeat_interval_sec:     Cadence for Telegram heartbeat job.
        warmup_coverage_pct:        MarketState coverage needed before RUNNING.
        warmup_timeout_sec:         Seconds before warmup is declared fatal.
        supervisor_poll_interval_sec:  Supervisor health-check cadence.
        supervisor_restart_window_sec: Rolling window for restart-storm count.
        supervisor_max_restarts:    Storm threshold (restarts per window).
    """

    def __init__(
        self,
        *,
        db_path: str | Path,
        delta_base_url: str,
        delta_api_key: str,
        delta_api_secret: str,
        delta_ws_url: str,
        telegram_bot_token: str = "",
        telegram_channel_id: str = "",
        n_strikes: int = 5,
        n_expiries: int = 3,
        snapshot_interval_sec: int = 60,
        chain_refresh_interval_sec: int = 3600,
        expiry_observe_interval_sec: int = 300,
        heartbeat_interval_sec: int = 1800,
        warmup_coverage_pct: float = 0.8,
        warmup_timeout_sec: float = 120.0,
        supervisor_poll_interval_sec: float = 10.0,
        supervisor_restart_window_sec: float = 300.0,
        supervisor_max_restarts: int = 3,
    ) -> None:
        # Immutable config
        self._db_path               = Path(db_path)
        self._delta_base_url        = delta_base_url
        self._delta_api_key         = delta_api_key
        self._delta_api_secret      = delta_api_secret
        self._delta_ws_url          = delta_ws_url
        self._telegram_token        = telegram_bot_token
        self._telegram_channel_id   = telegram_channel_id
        self._n_strikes             = n_strikes
        self._n_expiries            = n_expiries
        self._snapshot_interval      = snapshot_interval_sec
        self._chain_refresh_interval = chain_refresh_interval_sec
        self._expiry_observe_interval = expiry_observe_interval_sec
        self._heartbeat_interval     = heartbeat_interval_sec
        self._warmup_coverage_pct   = warmup_coverage_pct
        self._warmup_timeout_sec    = warmup_timeout_sec
        self._sup_poll              = supervisor_poll_interval_sec
        self._sup_window            = supervisor_restart_window_sec
        self._sup_max_restarts      = supervisor_max_restarts

        # Lifecycle state (guarded by _lock)
        self._lock        = threading.Lock()
        self._state       = RuntimeState.INIT
        self._drain_lock  = threading.Lock()
        self._drain_called = False

        # Component references (all None until bootstrap)
        self._conn:             Optional[sqlite3.Connection]  = None
        self._client:           Optional[DeltaClient]         = None
        self._market_state:     Optional[MarketState]         = None
        self._ws_manager:       Optional[WSManager]           = None
        self._chain_refresher:  Optional[ChainRefresher]      = None
        self._expiry_context:   Optional[ExpiryContext]       = None
        self._analytics_writer: Optional[ExpiryAnalyticsWriter] = None
        self._snapshot_writer:  Optional[SnapshotWriter]      = None
        self._scheduler:        Optional[SchedulerService]    = None
        self._rate_limiter:     Optional[RateLimiter]         = None
        self._heartbeat:        Optional[HeartbeatService]    = None
        self._supervisor:       Optional[Supervisor]          = None
        self._sender:           Optional[Callable[[str], None]] = None

        # Start order tracking (name, component) for reverse-stop in drain()
        self._started_components: List[Tuple[str, Any]] = []
        self._started_at: Optional[float] = None

    # -----------------------------------------------------------------------
    # State
    # -----------------------------------------------------------------------

    @property
    def state(self) -> RuntimeState:
        with self._lock:
            return self._state

    def _set_state(self, new: RuntimeState) -> None:
        """Transition to `new` with a structured INFO log. Thread-safe."""
        with self._lock:
            old = self._state
            if old == new:
                return
            self._state = new
        logger.info("[runtime] state: %s -> %s", old.value, new.value)

    # -----------------------------------------------------------------------
    # Bootstrap
    # -----------------------------------------------------------------------

    def bootstrap(self) -> None:
        """Construct and wire all components.

        On success: state remains BOOTSTRAPPING (run() advances further).
        On failure: DB connection closed, exception re-raised.

        Raises:
            RuntimeError: if not in INIT state.
        """
        with self._lock:
            if self._state != RuntimeState.INIT:
                raise RuntimeError(
                    f"[runtime] bootstrap() called in state {self._state.value}"
                )
        self._set_state(RuntimeState.BOOTSTRAPPING)
        try:
            self._do_bootstrap()
        except Exception as exc:
            logger.critical(
                "[runtime] bootstrap failed: %s: %s", type(exc).__name__, exc
            )
            self._close_db()
            raise

    def _do_bootstrap(self) -> None:
        self._started_at = time.monotonic()

        # 1. DB
        logger.info("[runtime] opening DB: %s", self._db_path)
        conn = get_connection(self._db_path)
        init_schema(conn)
        self._conn = conn

        # 2. REST client
        client = DeltaClient(
            base_url=self._delta_base_url,
            api_key=self._delta_api_key,
            api_secret=self._delta_api_secret,
        )
        self._client = client

        # 3. ChainRefresher -- initial REST call to get universe
        logger.info("[runtime] initial chain refresh (REST)...")
        refresher = ChainRefresher(
            client=client,
            conn=conn,
            n_strikes=self._n_strikes,
            n_expiries=self._n_expiries,
            on_change=self._on_universe_change,
        )
        snap = refresher.refresh()
        universe = snap.symbols
        if not universe:
            raise RuntimeError(
                f"initial chain refresh returned empty universe "
                f"(BTC mark={snap.mark_price:.0f}, n_chain={snap.n_chain})"
            )
        self._chain_refresher = refresher
        logger.info(
            "[runtime] universe: %d symbols (BTC mark=%.0f, n_chain=%d)",
            len(universe), snap.mark_price, snap.n_chain,
        )

        # 3b. ExpiryContext — observe-and-tag, no threads, no runtime control
        expiry_log_path = self._db_path.parent / "logs" / "expiry_telemetry.jsonl"
        analytics_writer = ExpiryAnalyticsWriter(expiry_log_path)
        self._analytics_writer = analytics_writer

        expiry_context = ExpiryContext(
            chain_refresher=refresher,
            config=ExpiryConfig(),
            analytics_writer=analytics_writer,
            on_settlement_trigger=self._on_settlement_trigger,
        )
        expiry_context.observe()   # initial classification while chain is fresh
        self._expiry_context = expiry_context

        # 4. MarketState
        market_state = MarketState(universe=universe)
        self._market_state = market_state

        # 5. WSManager
        channels = [{"name": "v2/ticker", "symbols": universe}]
        ws_manager = WSManager(
            url=self._delta_ws_url,
            channels=channels,
            state=market_state,
        )
        self._ws_manager = ws_manager

        # 6. SnapshotWriter
        snapshot_writer = SnapshotWriter(conn=conn, state=market_state)
        self._snapshot_writer = snapshot_writer

        # 7. Sender adapter
        sender = self._build_sender()
        self._sender = sender

        # 8. RateLimiter + HeartbeatService
        rate_limiter = RateLimiter(default_cooldown_sec=float(self._heartbeat_interval))
        heartbeat = HeartbeatService(
            sender=sender,
            ws_manager=ws_manager,
            market_state=market_state,
            snapshot_writer=snapshot_writer,
            chain_refresher=refresher,
            rate_limiter=rate_limiter,
            started_at=self._started_at,
            expiry_context=expiry_context,
        )
        self._rate_limiter = rate_limiter
        self._heartbeat = heartbeat

        # 9. SchedulerService + job registrations (not started yet)
        scheduler = SchedulerService()
        scheduler.register(
            "snapshot_quotes",
            snapshot_writer.snapshot,
            trigger="interval",
            seconds=self._snapshot_interval,
        )
        scheduler.register(
            "chain_refresh",
            refresher.refresh,
            trigger="interval",
            seconds=self._chain_refresh_interval,
        )
        scheduler.register(
            "expiry_observe",
            expiry_context.observe,
            trigger="interval",
            seconds=self._expiry_observe_interval,
        )
        scheduler.register(
            "heartbeat",
            heartbeat.send_heartbeat,
            trigger="interval",
            seconds=self._heartbeat_interval,
        )
        scheduler.register(
            "daily_summary",
            heartbeat.daily_summary,
            trigger="cron",
            hour=0,
            minute=0,
        )
        scheduler.register(
            "expiry_cycle_eod",
            expiry_context.emit_cycle_summary,
            trigger="cron",
            hour=12,
            minute=5,
        )
        self._scheduler = scheduler

        # 10. Supervisor
        supervisor = Supervisor(
            poll_interval_sec=self._sup_poll,
            restart_window_sec=self._sup_window,
            max_restarts_per_window=self._sup_max_restarts,
            on_fatal=self._on_fatal,
        )
        supervisor.register("ws_manager",      ws_manager,      restartable=True)
        supervisor.register("chain_refresher", refresher,       restartable=True)
        supervisor.register("snapshot_writer", snapshot_writer, restartable=True)
        supervisor.register("expiry_context",  expiry_context,  restartable=False)
        supervisor.register("scheduler",       scheduler,       restartable=False)
        supervisor.register("heartbeat",       heartbeat,       restartable=False)
        self._supervisor = supervisor

        # 11. Signal handlers
        self._install_signal_handlers()

        logger.info(
            "[runtime] bootstrap complete: 6 components wired, universe=%d",
            len(universe),
        )

    # -----------------------------------------------------------------------
    # Run (blocking)
    # -----------------------------------------------------------------------

    def run(self) -> None:
        """Start components, warmup, enter supervisor loop.

        Blocks until shutdown is signalled.  Always calls drain() on exit.

        Raises:
            RuntimeError: if bootstrap() has not been called first.
        """
        with self._lock:
            if self._state != RuntimeState.BOOTSTRAPPING:
                raise RuntimeError(
                    f"[runtime] run() requires state BOOTSTRAPPING, "
                    f"got {self._state.value}"
                )

        # Start data-ingestion components before warmup
        self._start_component("ws_manager",      self._ws_manager)
        self._start_component("chain_refresher", self._chain_refresher)
        self._start_component("expiry_context",  self._expiry_context)
        self._start_component("snapshot_writer", self._snapshot_writer)
        self._start_component("heartbeat",       self._heartbeat)

        self._set_state(RuntimeState.WARMING)

        try:
            self._warmup()
        except Exception as exc:
            logger.critical("[runtime] warmup fatal: %s", exc)
            self.drain()
            raise

        self._set_state(RuntimeState.RUNNING)

        # Scheduler starts AFTER warmup so jobs have data to work with
        self._start_component("scheduler", self._scheduler)

        # Startup heartbeat (rate limiter is fresh -- always sends)
        self._emit_startup_notice()

        # Enter supervisor loop (blocking until shutdown event fires)
        try:
            self._supervisor.run()
        finally:
            self.drain()

    # -----------------------------------------------------------------------
    # Drain (idempotent)
    # -----------------------------------------------------------------------

    def drain(self) -> None:
        """Gracefully stop all components and close the DB.

        Idempotent: subsequent calls after the first are silent no-ops.
        Individual stop() failures are caught, logged, and skipped.
        """
        with self._drain_lock:
            if self._drain_called:
                logger.debug("[runtime] drain() already called, ignoring")
                return
            self._drain_called = True

        self._set_state(RuntimeState.DRAINING)
        logger.info("[runtime] draining...")

        # Ask supervisor to exit (safe if already stopped or None)
        if self._supervisor is not None:
            self._supervisor.request_shutdown("drain() called")

        # Scheduler first: drains in-flight jobs before we tear down state
        if self._scheduler is not None:
            self._safe_stop("scheduler", self._scheduler, wait=True)
        # Remove scheduler from the reverse-stop list to avoid double-stop
        self._started_components = [
            (n, c) for n, c in self._started_components if n != "scheduler"
        ]

        # Shutdown notice while sender is still live
        self._emit_shutdown_notice()

        # Stop remaining components in reverse-start order
        for name, comp in reversed(self._started_components):
            self._safe_stop(name, comp)

        self._close_db()
        self._set_state(RuntimeState.SHUTDOWN)
        logger.info("[runtime] shutdown complete")

    # -----------------------------------------------------------------------
    # Signal handling
    # -----------------------------------------------------------------------

    def _install_signal_handlers(self) -> None:
        """Install SIGINT/SIGTERM handlers that request clean supervisor exit."""

        def _handler(signum: int, frame: Any) -> None:
            try:
                name = signal.Signals(signum).name
            except Exception:
                name = str(signum)
            logger.info("[runtime] %s received - requesting shutdown", name)
            if self._supervisor is not None:
                self._supervisor.request_shutdown(f"signal {name}")

        signal.signal(signal.SIGINT, _handler)
        try:
            signal.signal(signal.SIGTERM, _handler)
            logger.debug("[runtime] signal handlers installed (SIGINT + SIGTERM)")
        except (OSError, AttributeError):
            logger.debug("[runtime] SIGTERM not available on this platform (SIGINT only)")

    # -----------------------------------------------------------------------
    # Supervisor fatal callback
    # -----------------------------------------------------------------------

    def _on_fatal(self, name: str, health: HealthStatus) -> None:
        """Called by Supervisor on fatal component or restart storm.

        Transitions RUNNING -> DEGRADED, then signals supervisor to exit.
        The finally block in run() calls drain() after supervisor.run() returns.
        """
        with self._lock:
            if self._state == RuntimeState.RUNNING:
                # Transition outside lock to avoid _set_state deadlock
                should_transition = True
            else:
                should_transition = False

        if should_transition:
            self._set_state(RuntimeState.DEGRADED)

        logger.critical("[runtime] fatal: '%s': %s", name, health.details)

        if self._supervisor is not None:
            self._supervisor.request_shutdown(f"fatal component: {name}")

    # -----------------------------------------------------------------------
    # Warmup
    # -----------------------------------------------------------------------

    def _warmup(self) -> None:
        """Block until MarketState coverage meets threshold or timeout fires.

        Logs progress every poll.  Coverage threshold of 0 skips warmup.

        Raises:
            RuntimeError: on timeout.
        """
        target = self._warmup_coverage_pct
        if target <= 0.0:
            logger.info("[runtime] warmup skipped (coverage_pct=0)")
            return

        deadline = time.monotonic() + self._warmup_timeout_sec
        total    = len(self._market_state.universe_symbols())

        while True:
            cov     = self._market_state.coverage_pct()
            covered = int(round(cov * total))
            logger.info(
                "[runtime] warmup: %d/%d symbols (%.0f%% / target %.0f%%)",
                covered, total, cov * 100, target * 100,
            )
            if cov >= target:
                logger.info("[runtime] warmup complete (coverage=%.0f%%)", cov * 100)
                return

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(
                    f"warmup timed out after {self._warmup_timeout_sec:.0f}s: "
                    f"coverage {cov:.0%} < target {target:.0%}"
                )
            time.sleep(min(2.0, remaining))

    # -----------------------------------------------------------------------
    # Component lifecycle helpers
    # -----------------------------------------------------------------------

    def _start_component(self, name: str, comp: Any) -> None:
        """Call comp.start() and append to the started-components list."""
        comp.start()
        self._started_components.append((name, comp))
        logger.info("[runtime] started '%s'", name)

    def _safe_stop(self, name: str, comp: Any, **kwargs: Any) -> None:
        """Call comp.stop(**kwargs), logging any exception without re-raising."""
        try:
            comp.stop(**kwargs)
            logger.info("[runtime] stopped '%s'", name)
        except Exception as exc:
            logger.error(
                "[runtime] '%s' stop() raised: %s: %s", name, type(exc).__name__, exc
            )

    def _close_db(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
                logger.info("[runtime] DB closed")
            except Exception as exc:
                logger.error("[runtime] DB close error: %s", exc)
            self._conn = None

    # -----------------------------------------------------------------------
    # Settlement trigger callback
    # -----------------------------------------------------------------------

    def _on_settlement_trigger(self) -> None:
        """Called by ExpiryContext.emit_cycle_summary() when settlement crossings
        were detected this cycle.

        Triggers an immediate chain refresh so settled symbols are retired and
        rollover symbols added through the existing _on_universe_change path.
        ExpiryContext never names symbols to retire — ChainRefresher is the
        authoritative universe source.  All reconciliation flows through the
        established resubscribe() + reconcile_universe() machinery.
        """
        logger.info("[runtime] settlement trigger received — immediate chain refresh")
        if self._chain_refresher is not None:
            try:
                self._chain_refresher.refresh(triggered_by="settlement")
            except Exception as exc:
                logger.error(
                    "[runtime] settlement-triggered refresh failed: %s: %s",
                    type(exc).__name__, exc,
                )

    # -----------------------------------------------------------------------
    # Universe change callback
    # -----------------------------------------------------------------------

    def _on_universe_change(
        self, prev_symbols: List[str], new_symbols: List[str]
    ) -> None:
        """Called by ChainRefresher when the option universe changes.

        Call order: resubscribe() first, reconcile_universe() second.
        This minimises spurious 'not in universe' warnings: new symbols are
        subscribed before MarketState accepts ticks; removed symbols stop
        receiving ticks (eventually) before MarketState stops accepting them.
        """
        added   = len(set(new_symbols) - set(prev_symbols))
        removed = len(set(prev_symbols) - set(new_symbols))
        logger.info(
            "[runtime] universe changed: +%d -%d symbols — "
            "triggering WS resubscribe + MS reconcile",
            added, removed,
        )
        if self._ws_manager is not None:
            self._ws_manager.resubscribe(prev_symbols, new_symbols)
        if self._market_state is not None:
            self._market_state.reconcile_universe(prev_symbols, new_symbols)

    # -----------------------------------------------------------------------
    # Sender adapter
    # -----------------------------------------------------------------------

    def _build_sender(self) -> Callable[[str], None]:
        """Build a (text: str) -> None callable for HeartbeatService.

        If Telegram credentials are present: wraps send_message().
        Otherwise: logs at INFO level (silent stub, no network calls).
        """
        token   = self._telegram_token.strip()
        channel = self._telegram_channel_id.strip()
        if token and channel:
            logger.info(
                "[runtime] Telegram sender active (channel=%s)", channel
            )
            return partial(send_message, token, channel)

        logger.info(
            "[runtime] Telegram not configured -- heartbeats will be logged only"
        )

        def _log_sender(text: str) -> None:
            preview = text[:120].replace("\n", " | ")
            logger.info("[heartbeat-stub] %s", preview)

        return _log_sender

    # -----------------------------------------------------------------------
    # Startup / shutdown notices
    # -----------------------------------------------------------------------

    def _emit_startup_notice(self) -> None:
        """Send a startup heartbeat.  Rate limiter is fresh so always fires."""
        try:
            self._heartbeat.send_heartbeat()
        except Exception as exc:
            logger.warning("[runtime] startup heartbeat failed: %s", exc)

    def _emit_shutdown_notice(self) -> None:
        """Send a brief shutdown notice via the configured sender (best-effort)."""
        if self._sender is None:
            return
        uptime_sec = int(time.monotonic() - self._started_at) if self._started_at else 0
        h, rem = divmod(uptime_sec, 3600)
        m, s   = divmod(rem, 60)
        uptime_str = f"{h}h {m:02d}m {s:02d}s" if h else f"{m}m {s:02d}s"
        text = (
            "--- BTC Bot Shutdown ---\n"
            f"uptime: {uptime_str}\n"
            "------------------------"
        )
        try:
            self._sender(text)
        except Exception as exc:
            logger.warning("[runtime] shutdown notice failed: %s", exc)


# ---------------------------------------------------------------------------
# Self-test / smoke  (no live network required for most tests)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    import sys
    import tempfile

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Temp DB for tests that need one
    _tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    _tmp.close()
    DB = _tmp.name

    def _rt(**kwargs) -> Runtime:
        defaults = dict(
            db_path=DB,
            delta_base_url="http://127.0.0.1:1",   # unreachable
            delta_api_key="k",
            delta_api_secret="s",
            delta_ws_url="ws://127.0.0.1:1",
        )
        defaults.update(kwargs)
        return Runtime(**defaults)

    # ------------------------------------------------------------------
    # Test 1: RuntimeState enum has all required values
    # ------------------------------------------------------------------
    required = {"INIT", "BOOTSTRAPPING", "WARMING", "RUNNING",
                "DEGRADED", "DRAINING", "SHUTDOWN"}
    assert {s.value for s in RuntimeState} == required
    print("[t1] RuntimeState enum values: PASS")

    # ------------------------------------------------------------------
    # Test 2: initial state is INIT
    # ------------------------------------------------------------------
    rt2 = _rt()
    assert rt2.state == RuntimeState.INIT
    print("[t2] initial state INIT: PASS")

    # ------------------------------------------------------------------
    # Test 3: _set_state() transitions and logs
    # ------------------------------------------------------------------
    rt3 = _rt()
    rt3._set_state(RuntimeState.BOOTSTRAPPING)
    assert rt3.state == RuntimeState.BOOTSTRAPPING
    rt3._set_state(RuntimeState.RUNNING)
    assert rt3.state == RuntimeState.RUNNING
    rt3._set_state(RuntimeState.RUNNING)     # self-transition: no-op
    assert rt3.state == RuntimeState.RUNNING
    print("[t3] state transitions: PASS")

    # ------------------------------------------------------------------
    # Test 4: drain() before bootstrap -- no crash, reaches SHUTDOWN
    # ------------------------------------------------------------------
    rt4 = _rt()
    rt4.drain()
    assert rt4.state == RuntimeState.SHUTDOWN
    rt4.drain()   # idempotent
    rt4.drain()   # idempotent again
    assert rt4.state == RuntimeState.SHUTDOWN
    print("[t4] drain() before bootstrap, idempotent: PASS")

    # ------------------------------------------------------------------
    # Test 5: bootstrap() fails on unreachable REST; drain() still works
    # ------------------------------------------------------------------
    rt5 = _rt()
    try:
        rt5.bootstrap()
        print("ERROR: expected bootstrap to fail (no network)", file=sys.stderr)
        sys.exit(1)
    except Exception:
        pass   # expected -- REST call fails
    # State should be BOOTSTRAPPING (bootstrap did not succeed but state
    # was set before the error; _close_db() was called in except block)
    assert rt5._conn is None, "DB must be closed after bootstrap failure"
    rt5.drain()   # must not crash after partial bootstrap
    assert rt5.state == RuntimeState.SHUTDOWN
    print("[t5] bootstrap failure + clean drain: PASS")

    # ------------------------------------------------------------------
    # Test 6: bootstrap() raises if called in non-INIT state
    # ------------------------------------------------------------------
    rt6 = _rt()
    rt6._set_state(RuntimeState.BOOTSTRAPPING)
    try:
        rt6.bootstrap()
        print("ERROR: expected RuntimeError", file=sys.stderr)
        sys.exit(1)
    except RuntimeError:
        pass
    print("[t6] bootstrap() raises in non-INIT state: PASS")

    # ------------------------------------------------------------------
    # Test 7: run() raises if bootstrap() was not called
    # ------------------------------------------------------------------
    rt7 = _rt()
    try:
        rt7.run()
        print("ERROR: expected RuntimeError", file=sys.stderr)
        sys.exit(1)
    except RuntimeError:
        pass
    print("[t7] run() raises if bootstrap() not called: PASS")

    # ------------------------------------------------------------------
    # Test 8: _build_sender() returns log stub when Telegram not configured
    # ------------------------------------------------------------------
    rt8 = _rt()
    sender8 = rt8._build_sender()
    sent8: list = []
    # Verify it's callable and doesn't raise
    sender8("hello from test 8")
    print("[t8] log-stub sender does not raise: PASS")

    # ------------------------------------------------------------------
    # Test 9: _on_universe_change logs without crashing
    # ------------------------------------------------------------------
    rt9 = _rt()
    rt9._on_universe_change(
        ["C-BTC-95000-280526"], ["C-BTC-95000-280526", "C-BTC-96000-280526"]
    )
    print("[t9] _on_universe_change: PASS")

    # ------------------------------------------------------------------
    # Test 10: _on_fatal transitions RUNNING -> DEGRADED and requests shutdown
    # ------------------------------------------------------------------
    rt10 = _rt()
    rt10._set_state(RuntimeState.RUNNING)
    # Supervisor is None; _on_fatal must not crash
    dummy_health = HealthStatus.degraded("test_comp", "test fatal")
    rt10._on_fatal("test_comp", dummy_health)
    assert rt10.state == RuntimeState.DEGRADED
    print("[t10] _on_fatal: RUNNING -> DEGRADED, no crash: PASS")

    # ------------------------------------------------------------------
    # Test 11: _emit_shutdown_notice with log stub doesn't crash
    # ------------------------------------------------------------------
    rt11 = _rt()
    rt11._sender = rt8._build_sender()   # reuse log stub
    rt11._started_at = time.monotonic() - 3723
    rt11._emit_shutdown_notice()
    print("[t11] _emit_shutdown_notice: PASS")

    # ------------------------------------------------------------------
    # Test 12: signal handler installation doesn't crash
    # ------------------------------------------------------------------
    rt12 = _rt()
    rt12._install_signal_handlers()   # must not raise
    # Restore default SIGINT so subsequent Ctrl-C works normally
    signal.signal(signal.SIGINT, signal.default_int_handler)
    print("[t12] signal handler installation: PASS")

    # ------------------------------------------------------------------
    # Test 13: import chain -- all 9 steps importable
    # ------------------------------------------------------------------
    from src.runtime.supervisor import Supervisor as _S
    from src.alerts.heartbeat import HeartbeatService as _H
    from src.alerts.rate_limiter import RateLimiter as _RL
    from src.ingestion.chain_refresher import ChainRefresher as _CR
    from src.scheduler.runner import SchedulerService as _SS
    from src.persistence.snapshot_writer import SnapshotWriter as _SW
    from src.exchange.ws_manager import WSManager as _WS
    from src.runtime.health import HealthStatus as _HS
    from src.state.market_state import MarketState as _MS
    print("[t13] full 9-step import chain: PASS")

    # Cleanup
    try:
        os.unlink(DB)
    except Exception:
        pass

    print("\nRuntime lifecycle smoke test passed.")
    print("NOTE: bootstrap() + run() require live Delta testnet -- tested in Step 10.")
