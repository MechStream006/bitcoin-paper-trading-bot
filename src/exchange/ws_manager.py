"""WSManager — FSM-driven WebSocket component for the BTC bot runtime.

Supersedes DeltaWSListener (delta_ws.py / Layer 2c) with:
  - explicit 5-state FSM
  - exponential backoff with jitter, reset on successful stream
  - staleness detection with configurable threshold
  - thread-safe health() reporting (HealthStatus)
  - lifecycle-safe start / stop with no zombie threads

Dependency direction
--------------------
  ws_manager  ->  market_state  (writes live data)
  ws_manager  ->  runtime.health  (produces HealthStatus)
  ws_manager  ->  exchange.delta_ws  (reuses subscribe_payload)

DeltaWSListener (delta_ws.py) is kept unchanged for Layer 2c smoke compat.
"""
from __future__ import annotations

import enum
import json
import logging
import random
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from websocket import WebSocketTimeoutException, create_connection

from src.exchange.delta_ws import subscribe_payload
from src.runtime.health import HealthStatus
from src.state.market_state import MarketState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# FSM state enum
# ---------------------------------------------------------------------------

class WSState(enum.Enum):
    """States of the WebSocket connection FSM.

    Valid transitions:
        DISCONNECTED -> CONNECTING   (start() or backoff wait elapsed)
        CONNECTING   -> SUBSCRIBED   (connection made, subscribe payload sent)
        SUBSCRIBED   -> STREAMING    (first data message received)
        STREAMING    -> STALE        (no message for stale_sec seconds)
        STALE        -> CONNECTING   (reconnect triggered by outer loop)
        any          -> DISCONNECTED (stop() called or unrecoverable error)
    """
    DISCONNECTED = "DISCONNECTED"
    CONNECTING   = "CONNECTING"
    SUBSCRIBED   = "SUBSCRIBED"
    STREAMING    = "STREAMING"
    STALE        = "STALE"


# ---------------------------------------------------------------------------
# WSManager
# ---------------------------------------------------------------------------

class WSManager:
    """FSM-driven WebSocket component.

    Owns the full WebSocket lifecycle: connect, subscribe, recv loop,
    staleness detection, exponential-backoff reconnect, and health reporting.
    MarketState is the sole destination for parsed data — WSManager never
    writes to a DB, scheduler, or alert channel.

    Thread safety
    -------------
    One daemon thread (ws-manager) owns all socket I/O and all writes to
    `_lock`-protected fields.  The main thread (and any future Supervisor
    thread) may call health() and stop() safely at any time.

    Lock discipline
    ---------------
    `_lock` guards: _ws_state, _msg_count, _last_msg_at, _reconnect_count,
                    _consecutive_failures.
    MarketState._lock is owned by MarketState and is NEVER held while _lock
    is held — preventing any lock-inversion deadlock.
    No I/O, no sleep, no external calls happen while _lock is held.

    Args:
        url:                 Delta WS endpoint (testnet or live).
        channels:            Subscribe payload channels list.
        state:               Shared MarketState instance; sole write target.
        stale_sec:           Seconds of silence before STREAMING -> STALE.
        base_backoff_sec:    First reconnect delay (doubles each failure).
        max_backoff_sec:     Backoff cap.
        recv_timeout_sec:    Per-recv() socket timeout; also loop cadence.
    """

    COMPONENT_NAME = "ws_manager"

    def __init__(
        self,
        url: str,
        channels: list[dict],
        state: MarketState,
        stale_sec: float = 60.0,
        base_backoff_sec: float = 5.0,
        max_backoff_sec: float = 60.0,
        recv_timeout_sec: float = 10.0,
    ) -> None:
        self.url = url
        self.channels = channels
        self._state = state
        self._stale_sec = stale_sec
        self._base_backoff = base_backoff_sec
        self._max_backoff = max_backoff_sec
        self._recv_timeout = recv_timeout_sec

        # --- FSM stats — all writes from WS thread, reads from any thread ---
        self._lock = threading.Lock()
        self._ws_state: WSState = WSState.DISCONNECTED
        self._msg_count: int = 0
        self._last_msg_at: Optional[float] = None
        self._reconnect_count: int = 0         # total, monotonically increasing
        self._consecutive_failures: int = 0    # resets on first STREAMING message

        # --- Thread management ---
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # -----------------------------------------------------------------------
    # Public lifecycle API
    # -----------------------------------------------------------------------

    def start(self) -> None:
        """Start the background WS thread.  Idempotent: repeated calls are no-ops."""
        if self._thread and self._thread.is_alive():
            logger.warning("[ws] start() called while thread already running — ignoring")
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="ws-manager", daemon=True
        )
        self._thread.start()
        logger.info("[ws] started thread '%s'", self._thread.name)

    def stop(self, timeout: float = 10.0) -> None:
        """Signal the WS thread to exit and wait up to `timeout` seconds."""
        logger.info("[ws] stop() requested")
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                logger.warning(
                    "[ws] thread did not terminate within %.1fs — possible leak", timeout
                )
            else:
                logger.info("[ws] thread terminated cleanly")
            self._thread = None

    # -----------------------------------------------------------------------
    # Health reporting  (safe to call from any thread, any time)
    # -----------------------------------------------------------------------

    def health(self) -> HealthStatus:
        """Return a point-in-time HealthStatus snapshot.

        healthy=True  only when FSM is in STREAMING state.
        fatal=False   always — WSManager never self-declares fatal.
                      The Supervisor decides escalation policy.
        """
        with self._lock:
            ws_state = self._ws_state
            last_msg = self._last_msg_at
            msg_count = self._msg_count
            reconnect_count = self._reconnect_count

        age_str = f"{time.time() - last_msg:.1f}s" if last_msg is not None else "never"
        healthy = ws_state == WSState.STREAMING
        details = (
            f"state={ws_state.value} last_msg={age_str} "
            f"msgs={msg_count} reconnects={reconnect_count}"
        )
        return HealthStatus(
            name=self.COMPONENT_NAME,
            healthy=healthy,
            fatal=False,
            details=details,
            last_check_at=datetime.now(timezone.utc),
        )

    # -----------------------------------------------------------------------
    # FSM helpers  (called from WS thread only, except health())
    # -----------------------------------------------------------------------

    def _set_state(self, new_state: WSState, *, reason: str) -> None:
        """Transition to new_state.  Log is emitted OUTSIDE the lock.

        No-ops silently if already in new_state (avoids spurious self-transition
        logs when stop() fires during a backoff wait that already set DISCONNECTED).
        """
        with self._lock:
            prev = self._ws_state
            if prev == new_state:
                return          # not a real transition — skip
            self._ws_state = new_state
            rc = self._reconnect_count          # snapshot for log
        # I/O outside the lock — no blocking while _lock is held
        logger.info(
            "[ws] FSM: %s -> %s  reason=%r  reconnects=%d",
            prev.value, new_state.value, reason, rc,
        )

    def _calc_backoff(self, consecutive_failures: int) -> float:
        """Exponential delay, capped at max_backoff, with ±20% jitter."""
        raw = min(self._base_backoff * (2 ** consecutive_failures), self._max_backoff)
        jitter = raw * 0.2 * (2 * random.random() - 1)
        return max(1.0, raw + jitter)

    # -----------------------------------------------------------------------
    # Thread entry point — outer reconnect loop
    # -----------------------------------------------------------------------

    def _run(self) -> None:
        """Outer reconnect loop.  Runs entirely on the ws-manager daemon thread.

        Structure:
            while not stopped:
                set CONNECTING
                call _run_once()           # returns on stop, stale, or exception
                if stopped: break
                increment counters, compute backoff, sleep (interruptible)
            set DISCONNECTED
        """
        while not self._stop.is_set():
            self._set_state(WSState.CONNECTING, reason="establishing connection")
            error_occurred = False
            try:
                self._run_once()
            except Exception as exc:
                error_occurred = True
                if not self._stop.is_set():
                    logger.warning(
                        "[ws] _run_once raised %s: %s", type(exc).__name__, exc
                    )

            if self._stop.is_set():
                break

            # Increment counters; only grow consecutive_failures on real errors.
            # Stale exits are clean returns — backoff is already near-zero because
            # _consecutive_failures was reset when STREAMING was entered.
            with self._lock:
                self._reconnect_count += 1
                if error_occurred:
                    self._consecutive_failures += 1
                cf = self._consecutive_failures
                rc = self._reconnect_count

            delay = self._calc_backoff(cf)
            logger.info(
                "[ws] reconnect #%d in %.1fs (consecutive_failures=%d)",
                rc, delay, cf,
            )
            self._set_state(
                WSState.DISCONNECTED,
                reason=f"waiting {delay:.1f}s before reconnect #{rc}",
            )
            # Interruptible sleep: wakes immediately if stop() is called
            self._stop.wait(timeout=delay)

        self._set_state(WSState.DISCONNECTED, reason="stop() called")

    # -----------------------------------------------------------------------
    # Single connection lifecycle
    # -----------------------------------------------------------------------

    def _run_once(self) -> None:
        """One connect → subscribe → stream cycle.

        Returns normally when:
          - stop() was called (self._stop.is_set())
          - staleness threshold exceeded (state set to STALE before return)

        Raises on any socket or protocol error; caller (_run) handles reconnect.

        Guarantee: websocket is always closed in finally, regardless of how
        this method exits.  No state is leaked.
        """
        ws = create_connection(self.url, timeout=10)
        ws.settimeout(self._recv_timeout)
        try:
            ws.send(subscribe_payload(self.channels))
            self._set_state(WSState.SUBSCRIBED, reason="subscribe payload sent")

            while not self._stop.is_set():
                # ----------------------------------------------------------
                # Staleness check — only meaningful once streaming
                # Read under lock so main-thread health() sees consistent data.
                # ----------------------------------------------------------
                with self._lock:
                    current_ws_state = self._ws_state
                    last_msg = self._last_msg_at

                if current_ws_state == WSState.STREAMING and last_msg is not None:
                    age = time.time() - last_msg
                    if age > self._stale_sec:
                        self._set_state(
                            WSState.STALE,
                            reason=f"no message for {age:.0f}s (threshold={self._stale_sec}s)",
                        )
                        return  # triggers reconnect in outer loop

                # ----------------------------------------------------------
                # Blocking recv — raises WebSocketTimeoutException on timeout
                # (controlled by recv_timeout_sec, default 10s).
                # Other exceptions propagate to _run() for reconnect.
                # ----------------------------------------------------------
                try:
                    raw = ws.recv()
                except WebSocketTimeoutException:
                    continue    # loop back: stop-check + staleness-check
                if not raw:
                    continue

                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                self._dispatch(msg)

        finally:
            # Socket must always be closed, even if an exception is in flight.
            try:
                ws.close()
            except Exception:
                pass

    # -----------------------------------------------------------------------
    # Message dispatch
    # -----------------------------------------------------------------------

    def _dispatch(self, msg: dict) -> None:
        """Update stats and forward payload to MarketState.

        Lock discipline:
          1. Acquire _lock → update msg_count, last_msg_at, maybe FSM state.
          2. Release _lock.
          3. Call MarketState.update_* (acquires MarketState._lock internally).
          Never hold _lock and call MarketState simultaneously.
        """
        msg_type = msg.get("type")
        symbol = msg.get("symbol")
        if not symbol:
            return

        now = time.time()
        transitioned_to_streaming = False
        rc_snapshot = 0

        with self._lock:
            self._msg_count += 1
            self._last_msg_at = now
            if self._ws_state == WSState.SUBSCRIBED:
                # First data message — enter STREAMING and reset backoff counter.
                self._ws_state = WSState.STREAMING
                self._consecutive_failures = 0
                transitioned_to_streaming = True
                rc_snapshot = self._reconnect_count   # for log, captured atomically

        # Emit FSM transition log outside the lock (no I/O while locked).
        if transitioned_to_streaming:
            logger.info(
                "[ws] FSM: %s -> %s  reason=%r  reconnects=%d",
                WSState.SUBSCRIBED.value, WSState.STREAMING.value,
                "first message received", rc_snapshot,
            )

        # Forward to MarketState — its own lock, never nested with _lock.
        if msg_type == "v2/ticker":
            self._state.update_ticker(symbol, msg)
        elif msg_type == "funding_rate":
            self._state.update_funding(symbol, msg)


# ---------------------------------------------------------------------------
# Self-test / smoke (offline — exercises reconnect loop, no live network)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    universe = ["C-BTC-95000-280526", "P-BTC-95000-280526"]
    ms = MarketState(universe=universe)
    channels = [{"name": "v2/ticker", "symbols": universe}]

    # Non-existent endpoint: connection refused immediately → exercises the
    # reconnect loop and backoff without live network.
    mgr = WSManager(
        url="ws://127.0.0.1:1",   # port 1 — connection refused on all OSes
        channels=channels,
        state=ms,
        stale_sec=10.0,
        base_backoff_sec=1.0,     # fast backoff for test
        max_backoff_sec=3.0,
    )

    print(f"Before start: {mgr.health()}")
    assert not mgr.health().healthy, "must not be healthy before start()"

    mgr.start()
    time.sleep(3)   # let reconnect loop spin a few times

    h = mgr.health()
    print(f"After 3s:     {h}")
    assert not h.healthy, "must not be healthy against refused endpoint"
    assert h.fatal is False, "WSManager must never self-declare fatal"

    with mgr._lock:
        rc = mgr._reconnect_count
    assert rc >= 1, f"expected at least 1 reconnect attempt, got {rc}"
    print(f"Reconnect count: {rc}")

    mgr.stop(timeout=5)
    if mgr._thread is not None:
        print("WARNING: thread handle not cleared after stop()", file=sys.stderr)
    print("WSManager offline smoke test passed.")
