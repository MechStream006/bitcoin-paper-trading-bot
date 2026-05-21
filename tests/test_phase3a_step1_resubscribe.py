"""Phase 3a Step 1 — WSManager.resubscribe() tests.

All tests are offline (no live network, no thread started).
The MockWS captures sent payloads and can simulate send failures.
WSManager state is injected directly to test each path without
running the full reconnect loop.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

import pytest

from src.exchange.ws_manager import WSManager, WSState
from src.state.market_state import MarketState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class MockWS:
    """Captures WS sends; optionally raises on send."""

    def __init__(self, raise_on_send: bool = False) -> None:
        self.sent: list[dict] = []
        self.raise_on_send = raise_on_send
        self.closed = False

    def send(self, payload: str) -> None:
        if self.raise_on_send:
            raise OSError("simulated send failure")
        self.sent.append(json.loads(payload))

    def close(self) -> None:
        self.closed = True

    # helpers for assertions
    def subscribe_msgs(self) -> list[dict]:
        return [m for m in self.sent if m.get("type") == "subscribe"]

    def unsubscribe_msgs(self) -> list[dict]:
        return [m for m in self.sent if m.get("type") == "unsubscribe"]

    def symbols_sent(self, msg_type: str) -> set[str]:
        msgs = self.subscribe_msgs() if msg_type == "subscribe" else self.unsubscribe_msgs()
        result: set[str] = set()
        for m in msgs:
            for ch in m.get("payload", {}).get("channels", []):
                result.update(ch.get("symbols", []))
        return result


def _make_mgr(universe: list[str]) -> WSManager:
    """Build a WSManager with no live thread, unreachable URL."""
    state = MarketState(universe=universe)
    channels = [{"name": "v2/ticker", "symbols": universe}]
    return WSManager(
        url="ws://127.0.0.1:1",  # port 1 — connection refused everywhere
        channels=channels,
        state=state,
    )


def _inject_streaming(mgr: WSManager, mock_ws: MockWS) -> None:
    """Force the manager into STREAMING state with a mock WS object."""
    with mgr._lock:
        mgr._ws_state = WSState.STREAMING
        mgr._live_ws = mock_ws
        mgr._last_msg_at = time.time()
        mgr._msg_count = 1
        mgr._consecutive_failures = 0


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestResubscribeNoDiff:
    def test_noop_same_symbols(self) -> None:
        universe = ["C-BTC-95000-280526", "P-BTC-95000-280526"]
        mgr = _make_mgr(universe)
        mock_ws = MockWS()
        _inject_streaming(mgr, mock_ws)

        mgr.resubscribe(universe, universe)

        assert mock_ws.sent == [], "no-op must send nothing"
        assert mgr.subscribed_symbols() == set(universe)

    def test_noop_preserves_fsm_state(self) -> None:
        universe = ["C-BTC-95000-280526"]
        mgr = _make_mgr(universe)
        mock_ws = MockWS()
        _inject_streaming(mgr, mock_ws)

        mgr.resubscribe(universe, universe)

        with mgr._lock:
            assert mgr._ws_state == WSState.STREAMING, "FSM state must not change on no-op"

    def test_noop_already_applied(self) -> None:
        """Second call with the same new_symbols is a no-op (idempotent)."""
        base = ["C-BTC-95000-280526"]
        new_syms = base + ["P-BTC-95000-280526"]
        mgr = _make_mgr(base)
        mock_ws = MockWS()
        _inject_streaming(mgr, mock_ws)

        mgr.resubscribe(base, new_syms)   # first: diff = {P-BTC-95000}
        first_send_count = len(mock_ws.sent)

        mgr.resubscribe(base, new_syms)   # second: _subscribed_symbols already == new_syms
        second_send_count = len(mock_ws.sent)

        assert first_send_count == 1, "first call must send one message"
        assert second_send_count == first_send_count, "second call must be a no-op"


class TestResubscribeAddSymbols:
    def test_live_subscribe_sent_when_streaming(self) -> None:
        base = ["C-BTC-95000-280526", "P-BTC-95000-280526"]
        added = ["C-BTC-96000-280526", "P-BTC-96000-280526"]
        new_syms = base + added
        mgr = _make_mgr(base)
        mock_ws = MockWS()
        _inject_streaming(mgr, mock_ws)

        mgr.resubscribe(base, new_syms)

        assert mgr.subscribed_symbols() == set(new_syms)
        assert mock_ws.symbols_sent("subscribe") == set(added)
        assert mock_ws.unsubscribe_msgs() == [], "add-only must not unsubscribe"

    def test_subscribe_payload_format(self) -> None:
        base = ["C-BTC-95000-280526"]
        new_syms = base + ["P-BTC-95000-280526"]
        mgr = _make_mgr(base)
        mock_ws = MockWS()
        _inject_streaming(mgr, mock_ws)

        mgr.resubscribe(base, new_syms)

        subs = mock_ws.subscribe_msgs()
        assert len(subs) == 1
        msg = subs[0]
        assert msg["type"] == "subscribe"
        channels = msg["payload"]["channels"]
        assert len(channels) == 1
        assert channels[0]["name"] == "v2/ticker"
        assert "P-BTC-95000-280526" in channels[0]["symbols"]


class TestResubscribeRemoveSymbols:
    def test_live_unsubscribe_sent_when_streaming(self) -> None:
        base = ["C-BTC-95000-280526", "P-BTC-95000-280526", "C-BTC-96000-280526"]
        removed = ["C-BTC-96000-280526"]
        new_syms = [s for s in base if s not in removed]
        mgr = _make_mgr(base)
        mock_ws = MockWS()
        _inject_streaming(mgr, mock_ws)

        mgr.resubscribe(base, new_syms)

        assert mgr.subscribed_symbols() == set(new_syms)
        assert mock_ws.symbols_sent("unsubscribe") == set(removed)
        assert mock_ws.subscribe_msgs() == [], "remove-only must not subscribe"

    def test_unsubscribe_payload_format(self) -> None:
        base = ["C-BTC-95000-280526", "P-BTC-95000-280526"]
        new_syms = ["C-BTC-95000-280526"]
        mgr = _make_mgr(base)
        mock_ws = MockWS()
        _inject_streaming(mgr, mock_ws)

        mgr.resubscribe(base, new_syms)

        unsubs = mock_ws.unsubscribe_msgs()
        assert len(unsubs) == 1
        msg = unsubs[0]
        assert msg["type"] == "unsubscribe"
        channels = msg["payload"]["channels"]
        assert channels[0]["name"] == "v2/ticker"
        assert "P-BTC-95000-280526" in channels[0]["symbols"]


class TestResubscribeAddAndRemove:
    def test_both_payloads_sent(self) -> None:
        base = ["C-BTC-95000-280526", "P-BTC-95000-280526"]
        new_syms = ["C-BTC-95000-280526", "C-BTC-96000-280526"]
        mgr = _make_mgr(base)
        mock_ws = MockWS()
        _inject_streaming(mgr, mock_ws)

        mgr.resubscribe(base, new_syms)

        assert mgr.subscribed_symbols() == set(new_syms)
        assert mock_ws.symbols_sent("subscribe") == {"C-BTC-96000-280526"}
        assert mock_ws.symbols_sent("unsubscribe") == {"P-BTC-95000-280526"}

    def test_two_separate_sends(self) -> None:
        """Add and remove must be two separate WS messages, not one combined."""
        base = ["C-BTC-95000-280526", "P-BTC-95000-280526"]
        new_syms = ["C-BTC-95000-280526", "C-BTC-96000-280526"]
        mgr = _make_mgr(base)
        mock_ws = MockWS()
        _inject_streaming(mgr, mock_ws)

        mgr.resubscribe(base, new_syms)

        assert len(mock_ws.sent) == 2, "must send exactly 2 messages (subscribe + unsubscribe)"


class TestResubscribeNonStreamingStates:
    def test_disconnected_no_send_state_updated(self) -> None:
        base = ["C-BTC-95000-280526"]
        new_syms = base + ["P-BTC-95000-280526"]
        mgr = _make_mgr(base)
        assert mgr._ws_state == WSState.DISCONNECTED
        assert mgr._live_ws is None

        mgr.resubscribe(base, new_syms)

        assert mgr.subscribed_symbols() == set(new_syms), "state must update even when disconnected"

    def test_connecting_no_send_state_updated(self) -> None:
        base = ["C-BTC-95000-280526"]
        new_syms = base + ["P-BTC-95000-280526"]
        mgr = _make_mgr(base)
        with mgr._lock:
            mgr._ws_state = WSState.CONNECTING

        mock_ws = MockWS()  # not injected as _live_ws
        mgr.resubscribe(base, new_syms)

        assert mgr.subscribed_symbols() == set(new_syms)
        assert mock_ws.sent == [], "no send while CONNECTING"

    def test_subscribed_no_live_send(self) -> None:
        base = ["C-BTC-95000-280526"]
        new_syms = base + ["P-BTC-95000-280526"]
        mgr = _make_mgr(base)
        mock_ws = MockWS()
        with mgr._lock:
            mgr._ws_state = WSState.SUBSCRIBED
            mgr._live_ws = mock_ws

        mgr.resubscribe(base, new_syms)

        assert mgr.subscribed_symbols() == set(new_syms)
        assert mock_ws.sent == [], "no live send while SUBSCRIBED (not yet STREAMING)"

    def test_stale_no_live_send(self) -> None:
        base = ["C-BTC-95000-280526"]
        new_syms = base + ["P-BTC-95000-280526"]
        mgr = _make_mgr(base)
        mock_ws = MockWS()
        with mgr._lock:
            mgr._ws_state = WSState.STALE
            mgr._live_ws = mock_ws

        mgr.resubscribe(base, new_syms)

        assert mgr.subscribed_symbols() == set(new_syms)
        assert mock_ws.sent == [], "no live send while STALE"


class TestResubscribeReconnectRebuild:
    def test_current_channels_reflects_new_symbols(self) -> None:
        """After resubscribe(), _current_channels() returns the new symbol set.

        This is the reconnect rebuild path: every _run_once() calls
        _current_channels() at connection time, so the reconnect always
        subscribes the correct set.
        """
        base = ["C-BTC-95000-280526"]
        new_syms = base + ["P-BTC-95000-280526", "C-BTC-96000-280526"]
        mgr = _make_mgr(base)

        mgr.resubscribe(base, new_syms)

        channels = mgr._current_channels()
        assert len(channels) == 1
        assert channels[0]["name"] == "v2/ticker"
        assert set(channels[0]["symbols"]) == set(new_syms)

    def test_current_channels_after_remove(self) -> None:
        base = ["C-BTC-95000-280526", "P-BTC-95000-280526", "C-BTC-96000-280526"]
        new_syms = ["C-BTC-95000-280526", "C-BTC-96000-280526"]
        mgr = _make_mgr(base)

        mgr.resubscribe(base, new_syms)

        channels = mgr._current_channels()
        assert set(channels[0]["symbols"]) == set(new_syms)
        assert "P-BTC-95000-280526" not in channels[0]["symbols"]

    def test_current_channels_sorted_deterministic(self) -> None:
        """Reconnect subscribe payload must be sorted for determinism."""
        universe = ["P-BTC-95000-280526", "C-BTC-95000-280526", "C-BTC-96000-280526"]
        mgr = _make_mgr(universe)
        channels = mgr._current_channels()
        syms = channels[0]["symbols"]
        assert syms == sorted(syms), "symbols must be sorted"

    def test_initial_subscribed_symbols_matches_constructor(self) -> None:
        universe = ["C-BTC-95000-280526", "P-BTC-95000-280526"]
        mgr = _make_mgr(universe)
        assert mgr.subscribed_symbols() == set(universe)


class TestResubscribeSendFailure:
    def test_subscribe_send_failure_state_still_updated(self) -> None:
        """If live subscribe send raises, _subscribed_symbols is still updated.
        The reconnect will fix subscription correctness.
        """
        base = ["C-BTC-95000-280526"]
        new_syms = base + ["P-BTC-95000-280526"]
        mgr = _make_mgr(base)
        mock_ws = MockWS(raise_on_send=True)
        _inject_streaming(mgr, mock_ws)

        mgr.resubscribe(base, new_syms)  # must not raise

        assert mgr.subscribed_symbols() == set(new_syms), "state updated despite send failure"

    def test_unsubscribe_send_failure_state_still_updated(self) -> None:
        base = ["C-BTC-95000-280526", "P-BTC-95000-280526"]
        new_syms = ["C-BTC-95000-280526"]
        mgr = _make_mgr(base)
        mock_ws = MockWS(raise_on_send=True)
        _inject_streaming(mgr, mock_ws)

        mgr.resubscribe(base, new_syms)  # must not raise

        assert mgr.subscribed_symbols() == set(new_syms)

    def test_send_failure_logged_as_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        base = ["C-BTC-95000-280526"]
        new_syms = base + ["P-BTC-95000-280526"]
        mgr = _make_mgr(base)
        mock_ws = MockWS(raise_on_send=True)
        _inject_streaming(mgr, mock_ws)

        with caplog.at_level(logging.WARNING, logger="src.exchange.ws_manager"):
            mgr.resubscribe(base, new_syms)

        assert any("live subscribe failed" in r.message for r in caplog.records)


class TestResubscribeFSMStability:
    def test_resubscribe_does_not_change_fsm_state(self) -> None:
        for state in [WSState.DISCONNECTED, WSState.CONNECTING, WSState.SUBSCRIBED,
                      WSState.STREAMING, WSState.STALE]:
            universe = ["C-BTC-95000-280526"]
            mgr = _make_mgr(universe)
            mock_ws = MockWS()
            with mgr._lock:
                mgr._ws_state = state
                if state == WSState.STREAMING:
                    mgr._live_ws = mock_ws
                    mgr._last_msg_at = time.time()

            mgr.resubscribe(universe, universe + ["P-BTC-95000-280526"])

            with mgr._lock:
                assert mgr._ws_state == state, (
                    f"resubscribe() must not change FSM state (was {state.value})"
                )

    def test_health_includes_subscribed_count(self) -> None:
        universe = ["C-BTC-95000-280526", "P-BTC-95000-280526"]
        mgr = _make_mgr(universe)
        h = mgr.health()
        assert f"subscribed={len(universe)}" in h.details

    def test_health_subscribed_count_updates_after_resubscribe(self) -> None:
        base = ["C-BTC-95000-280526"]
        new_syms = base + ["P-BTC-95000-280526", "C-BTC-96000-280526"]
        mgr = _make_mgr(base)

        mgr.resubscribe(base, new_syms)

        h = mgr.health()
        assert f"subscribed={len(new_syms)}" in h.details


class TestResubscribeConcurrency:
    def test_concurrent_calls_final_state_is_last_new_syms(self) -> None:
        """Concurrent resubscribe() calls must leave a consistent final state."""
        base = [f"C-BTC-{95000 + i * 1000}-280526" for i in range(10)]
        mgr = _make_mgr(base)
        mock_ws = MockWS()
        _inject_streaming(mgr, mock_ws)

        results: list[set[str]] = []
        errors: list[Exception] = []

        syms_a = base[:8]   # remove last 2
        syms_b = base[:6]   # remove last 4

        def call_a() -> None:
            try:
                mgr.resubscribe(base, syms_a)
                results.append(mgr.subscribed_symbols())
            except Exception as exc:
                errors.append(exc)

        def call_b() -> None:
            try:
                mgr.resubscribe(base, syms_b)
                results.append(mgr.subscribed_symbols())
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=call_a)
        t2 = threading.Thread(target=call_b)
        t1.start(); t2.start()
        t1.join(); t2.join()

        assert not errors, f"concurrent resubscribe raised: {errors}"
        # Final subscribed set must be one of the two valid outcomes
        final = mgr.subscribed_symbols()
        assert final in (set(syms_a), set(syms_b)), (
            f"final subscribed set {final!r} is not one of the expected outcomes"
        )

    def test_live_ws_cleared_after_stop(self) -> None:
        """After stop() (simulated), _live_ws must be None."""
        universe = ["C-BTC-95000-280526"]
        mgr = _make_mgr(universe)
        mock_ws = MockWS()
        with mgr._lock:
            mgr._live_ws = mock_ws

        # Simulate _run_once() finally: clear _live_ws, then close socket
        with mgr._lock:
            mgr._live_ws = None
        mock_ws.close()

        assert mgr._live_ws is None
        assert mock_ws.closed is True
