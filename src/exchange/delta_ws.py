"""Delta Exchange WebSocket client (sync, via websocket-client).

Two entrypoints:
  - `stream(...)` — bounded one-shot for smoke tests (Layer 2c.1).
  - `DeltaWSListener` — long-running threaded client with reconnect, maintains
    in-memory state of latest tickers / funding rates (Layer 2c.2 onward).
"""
from __future__ import annotations

import json
import threading
import time
from typing import TYPE_CHECKING, Callable, Optional

from websocket import WebSocketTimeoutException, create_connection

if TYPE_CHECKING:
    from src.state.market_state import MarketState


def subscribe_payload(channels: list[dict]) -> str:
    """Build a Delta WS subscribe message.

    `channels` is a list of {"name": <channel>, "symbols": [<sym>, ...]} dicts.
    """
    return json.dumps({
        "type": "subscribe",
        "payload": {"channels": channels},
    })


def stream(
    url: str,
    channels: list[dict],
    on_message: Callable[[object], None],
    duration_sec: float = 120.0,
    recv_timeout: float = 30.0,
) -> int:
    """Connect, subscribe, dispatch each received message to `on_message` until duration_sec elapses.

    `on_message` is called with the parsed dict (or the raw string if JSON parse fails).
    Returns the number of messages received.
    """
    ws = create_connection(url, timeout=10)
    ws.settimeout(recv_timeout)
    count = 0
    try:
        ws.send(subscribe_payload(channels))
        deadline = time.monotonic() + duration_sec
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            ws.settimeout(min(recv_timeout, max(1.0, remaining)))
            try:
                raw = ws.recv()
            except Exception as e:
                print(f"  recv stopped: {type(e).__name__}: {e}")
                break
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                msg = raw
            on_message(msg)
            count += 1
        return count
    finally:
        try:
            ws.close()
        except Exception:
            pass


class DeltaWSListener:
    """Threaded WS listener that keeps the latest payload per (channel, symbol).

    Usage:
        listener = DeltaWSListener(url, channels)
        listener.start()
        ...                              # do other work; tickers/funding stay fresh
        snap = listener.snapshot_tickers()
        listener.stop()

    Reconnects on socket errors with a 5s backoff. Daemon thread, so a clean
    main-thread exit will tear it down even if .stop() isn't called.
    """

    def __init__(
        self,
        url: str,
        channels: list[dict],
        reconnect_backoff_sec: float = 5.0,
        state: Optional[MarketState] = None,
    ) -> None:
        self.url = url
        self.channels = channels
        self.reconnect_backoff_sec = reconnect_backoff_sec
        self._state = state                  # optional shared MarketState (Layer 2d+)
        self._tickers: dict[str, dict] = {}  # kept for backward compat (Layer 2c.2)
        self._funding: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_msg_at: float | None = None
        self.msg_count: int = 0
        self.reconnect_count: int = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="delta-ws", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def snapshot_tickers(self) -> dict[str, dict]:
        with self._lock:
            return dict(self._tickers)

    def snapshot_funding(self) -> dict[str, dict]:
        with self._lock:
            return dict(self._funding)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._run_once()
            except Exception as e:
                print(f"  [ws] connection error: {type(e).__name__}: {e}")
                if self._stop.is_set():
                    break
                self.reconnect_count += 1
                time.sleep(self.reconnect_backoff_sec)

    def _run_once(self) -> None:
        ws = create_connection(self.url, timeout=10)
        ws.settimeout(10)
        try:
            ws.send(subscribe_payload(self.channels))
            while not self._stop.is_set():
                try:
                    raw = ws.recv()
                except WebSocketTimeoutException:
                    continue
                if not raw:
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                self._dispatch(msg)
        finally:
            try:
                ws.close()
            except Exception:
                pass

    def _dispatch(self, msg: dict) -> None:
        self.msg_count += 1
        self.last_msg_at = time.time()
        msg_type = msg.get("type")
        symbol = msg.get("symbol")
        if not symbol:
            return
        # Write to internal dicts (backward-compat with 2c.2 snapshot_* methods).
        with self._lock:
            if msg_type == "v2/ticker":
                self._tickers[symbol] = msg
            elif msg_type == "funding_rate":
                self._funding[symbol] = msg
        # Also propagate to shared MarketState when injected (Layer 2d+).
        if self._state is not None:
            if msg_type == "v2/ticker":
                self._state.update_ticker(symbol, msg)
            elif msg_type == "funding_rate":
                self._state.update_funding(symbol, msg)
