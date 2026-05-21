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

from src.exchange.delta_ws import subscribe_payload, unsubscribe_payload
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
                    _consecutive_failures, _subscribed_symbols, _live_ws.
    MarketState._lock is owned by MarketState and is NEVER held while _lock
    is held — preventing any lock-inversion deadlock.
    No I/O, no sleep, no external calls happen while _lock is held.
    In particular, ws.send() in resubscribe() is always called AFTER _lock
    is released — the live_ws reference is captured under the lock, then used
    outside it.

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

        # --- Subscription state — authoritative source of truth for reconnect ---
        # Initialised from the constructor channels; updated by resubscribe().
        # Every reconnect reads this set to build the subscribe payload, so
        # correctness is guaranteed regardless of live-mutation outcomes.
        self._subscribed_symbols: set[str] = {
            s for ch in channels for s in ch.get("symbols", [])
        }

        # --- Live WS reference — set in _run_once, cleared in finally ---
        # resubscribe() captures this under _lock and sends outside the lock.
        # None when DISCONNECTED / between reconnects.
        self._live_ws: Optional[object] = None

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
    # Subscription state accessor  (safe from any thread)
    # -----------------------------------------------------------------------

    def subscribed_symbols(self) -> set[str]:
        """Return a copy of the current authoritative subscription symbol set."""
        with self._lock:
            return set(self._subscribed_symbols)

    # -----------------------------------------------------------------------
    # Health reporting  (safe to call from any thread, any time)
    # -----------------------------------------------------------------------

    def health(self) -> HealthStatus:
        """Return a point-in-time HealthStatus snapshot.

        healthy=True  when:
          - STREAMING (normal operational state), OR
          - CONNECTING / SUBSCRIBED (connection in progress — brief, self-resolving), OR
          - DISCONNECTED with consecutive_failures < 3 (early backoff — self-healing;
            allow WSManager's own exponential backoff to run before Supervisor interferes).
        healthy=False when:
          - DISCONNECTED with consecutive_failures >= 3 (stuck after repeated failures), OR
          - STALE (data stopped flowing; reconnect needed).
        fatal=False   always — WSManager never self-declares fatal.
                      The Supervisor decides escalation policy.

        The CONNECTING/SUBSCRIBED and early-DISCONNECTED grace prevents the Supervisor
        from exhausting its restart budget by restarting the WS on every 10s poll while
        the WS is already in the middle of its own backoff/reconnect sequence.
        """
        with self._lock:
            ws_state = self._ws_state
            last_msg = self._last_msg_at
            msg_count = self._msg_count
            reconnect_count = self._reconnect_count
            consec_failures = self._consecutive_failures
            sub_count = len(self._subscribed_symbols)

        age_str = f"{time.time() - last_msg:.1f}s" if last_msg is not None else "never"
        healthy = (
            ws_state in (WSState.STREAMING, WSState.CONNECTING, WSState.SUBSCRIBED)
            or (ws_state == WSState.DISCONNECTED and consec_failures < 3)
        )
        details = (
            f"state={ws_state.value} last_msg={age_str} "
            f"msgs={msg_count} reconnects={reconnect_count} subscribed={sub_count}"
        )
        return HealthStatus(
            name=self.COMPONENT_NAME,
            healthy=healthy,
            fatal=False,
            details=details,
            last_check_at=datetime.now(timezone.utc),
        )

    # -----------------------------------------------------------------------
    # Live subscription mutation  (safe to call from any thread)
    # -----------------------------------------------------------------------

    def resubscribe(self, prev_symbols: list[str], new_symbols: list[str]) -> None:
        """Diff-based live subscription mutation.

        Called by ChainRefresher's on_change callback when the option universe
        changes.  Computes the diff against the current authoritative
        _subscribed_symbols (not the caller's prev_symbols, which may be stale).
        This makes repeated calls with the same new_symbols idempotent.

        State updates happen under _lock.  WS sends happen outside _lock —
        the live_ws reference is captured under the lock, then used after release.

        Reconnect semantics
        -------------------
        _subscribed_symbols is always committed before any live send attempt.
        Every reconnect calls _current_channels() which reads _subscribed_symbols,
        so the correct symbol set is restored regardless of whether the live
        send succeeded, failed, or was skipped (wrong state / no socket).

        Called while DISCONNECTED:  state updated; reconnect picks it up.
        Called while CONNECTING:    state updated; subscribe payload for this
                                    connection was already sent with old symbols;
                                    next reconnect uses the new set.
        Called while SUBSCRIBED:    state updated; live send skipped (not yet
                                    STREAMING); next reconnect corrects.
        Called while STREAMING:     state updated; live sub/unsub sent best-effort.
        Send failure (any reason):  logged as WARNING; reconnect self-heals.

        Args:
            prev_symbols: symbol list before the universe change (logged only).
            new_symbols:  desired new symbol set.
        """
        new_set = set(new_symbols)

        # Single lock acquisition: diff against authoritative state, commit,
        # snapshot ws_state and live_ws for post-lock use.
        has_diff: bool
        added: set[str]
        removed: set[str]
        ws_state: WSState
        live_ws: object

        with self._lock:
            old_set = set(self._subscribed_symbols)
            added = new_set - old_set
            removed = old_set - new_set
            has_diff = bool(added or removed)
            if has_diff:
                self._subscribed_symbols = new_set
                ws_state = self._ws_state
                live_ws = self._live_ws  # type: ignore[assignment]

        if not has_diff:
            logger.debug(
                "[ws] resubscribe: no diff — no-op (subscribed=%d)", len(old_set)
            )
            return

        # All logging and I/O outside the lock.
        logger.info(
            "[ws] resubscribe: +%d -%d symbols (subscribed %d -> %d)",
            len(added), len(removed), len(old_set), len(new_set),
        )
        if added:
            logger.info("[ws] resubscribe adding:   %s", sorted(added))
        if removed:
            logger.info("[ws] resubscribe removing: %s", sorted(removed))

        if ws_state is not WSState.STREAMING or live_ws is None:
            logger.info(
                "[ws] resubscribe: state=%s — subscription updated, "
                "changes deferred to next reconnect",
                ws_state.value,
            )
            return

        # Live mutation — best-effort, no lock held, separate sends for add/remove.
        if added:
            try:
                live_ws.send(  # type: ignore[union-attr]
                    subscribe_payload([{"name": "v2/ticker", "symbols": sorted(added)}])
                )
                logger.info(
                    "[ws] resubscribe: live subscribe sent (%d symbols)", len(added)
                )
            except Exception as exc:
                logger.warning(
                    "[ws] resubscribe: live subscribe failed (%s: %s) "
                    "— reconnect will resubscribe correctly",
                    type(exc).__name__, exc,
                )

        if removed:
            try:
                live_ws.send(  # type: ignore[union-attr]
                    unsubscribe_payload([{"name": "v2/ticker", "symbols": sorted(removed)}])
                )
                logger.info(
                    "[ws] resubscribe: live unsubscribe sent (%d symbols)", len(removed)
                )
            except Exception as exc:
                logger.warning(
                    "[ws] resubscribe: live unsubscribe failed (%s: %s) "
                    "— reconnect will restore correct state",
                    type(exc).__name__, exc,
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

    def _current_channels(self) -> list[dict]:
        """Build channel payload from the authoritative subscription set.

        Called at the start of each _run_once() cycle so every reconnect
        subscribes exactly the current _subscribed_symbols, regardless of
        any mutations that occurred during a prior connection.
        """
        with self._lock:
            syms = sorted(self._subscribed_symbols)
        return [{"name": "v2/ticker", "symbols": syms}]

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
        this method exits.  _live_ws is always cleared in finally, so
        resubscribe() never holds a reference to a dead socket after reconnect.
        """
        ws = create_connection(self.url, timeout=10)
        ws.settimeout(self._recv_timeout)

        # Register live reference before subscribe so resubscribe() can send
        # mutations as soon as we enter STREAMING.  Cleared unconditionally in
        # finally — before ws.close() — so callers that captured live_ws under
        # lock will hit a closed socket (exception caught in resubscribe).
        with self._lock:
            self._live_ws = ws

        try:
            channels = self._current_channels()
            ws.send(subscribe_payload(channels))
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
            # Clear _live_ws first so resubscribe() stops seeing this socket,
            # then close it.  Any concurrent resubscribe() that already captured
            # the reference will hit a send() error on the closed socket —
            # caught and logged as a WARNING there.
            with self._lock:
                self._live_ws = None
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
    # DISCONNECTED + cf=0 is healthy per new policy: self-healing, no failures yet
    assert mgr.health().healthy, "must be healthy before start() (DISCONNECTED, cf=0)"

    mgr.start()
    # Poll until we catch it in DISCONNECTED state with cf >= 3.
    # Polling is necessary because the window between backoff-wait ending and the
    # next CONNECTING is short — a fixed sleep races between those two states.
    deadline = time.time() + 30
    caught_state = None
    caught_cf = 0
    caught_rc = 0
    while time.time() < deadline:
        with mgr._lock:
            ws_state = mgr._ws_state
            cf = mgr._consecutive_failures
            rc = mgr._reconnect_count
        if ws_state == WSState.DISCONNECTED and cf >= 3:
            h = mgr.health()
            assert not h.healthy, (
                f"health() must be False when DISCONNECTED with cf={cf}"
            )
            caught_state, caught_cf, caught_rc = ws_state, cf, rc
            break
        time.sleep(0.1)

    assert caught_state is not None, (
        f"never reached DISCONNECTED+cf>=3 within 30s "
        f"(last: state={ws_state.value}, cf={cf}, rc={rc})"
    )
    h = mgr.health()
    print(f"After failures: {h}")
    assert h.fatal is False, "WSManager must never self-declare fatal"
    assert caught_rc >= 3, f"expected at least 3 reconnect attempts, got {caught_rc}"
    print(f"Reconnect count: {caught_rc}, consecutive_failures: {caught_cf}")

    mgr.stop(timeout=5)
    if mgr._thread is not None:
        print("WARNING: thread handle not cleared after stop()", file=sys.stderr)
    print("WSManager offline smoke test passed.")
