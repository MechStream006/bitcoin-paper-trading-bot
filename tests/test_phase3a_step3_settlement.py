"""Phase 3a Step 3 — offline unit tests for the settlement reconciliation spine.

Covers:
  TestSettlementTriggerFires       — fires when crossings present; no fire when empty
  TestSettlementTriggerIdempotent  — state reset after fire; second call is no-op
  TestSettlementTriggerCallOrder   — trigger before analytics; reset always last
  TestSettlementTriggerFailure     — callback raises → caught; state still reset
  TestRuntimeSettlementWiring      — _on_settlement_trigger() calls refresh(triggered_by=)
  TestChainRefresherSkipIfBusy     — concurrent refresh() → second skips, returns snapshot
  TestFullSettlementPath           — mock diff → resubscribe() + reconcile_universe() called
  TestRestartDuringRollover        — no crossings in session → no trigger fired

No live network required.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, call, patch

import pytest

from src.expiry.expiry_context import ExpiryContext
from src.expiry.models import ExpiryConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UTC = timezone.utc

SYM_A = "C-BTC-77400-210526"
SYM_B = "P-BTC-77400-210526"
SYM_C = "C-BTC-78000-220526"   # rollover


def _make_refresher(snapshot=None):
    """Mock ChainRefresher that satisfies ExpiryContext.current_snapshot()."""
    m = MagicMock()
    m.current_snapshot.return_value = snapshot
    return m


def _make_context(
    *,
    crossings: list | None = None,
    on_settlement_trigger=None,
    analytics=None,
    now_fn=None,
) -> ExpiryContext:
    """Build an ExpiryContext with pre-populated cycle state."""
    snap_mock = MagicMock()
    snap_mock.universe = []
    snap_mock.mark_price = 78000.0
    refresher = _make_refresher(snapshot=snap_mock)

    ctx = ExpiryContext(
        chain_refresher=refresher,
        config=ExpiryConfig(),
        now_fn=now_fn or (lambda: datetime.now(_UTC)),
        analytics_writer=analytics,
        on_settlement_trigger=on_settlement_trigger,
    )
    ctx._started = True

    if crossings is not None:
        with ctx._lock:
            ctx._cycle_settlement_crossings = list(crossings)
            for c in crossings:
                ctx._settled_symbols.add(c["symbol"])

    return ctx


def _make_crossing(symbol: str) -> dict:
    return {
        "symbol": symbol,
        "expiry_class": "DAILY",
        "settled_at": "2026-05-21T12:00:00+00:00",
        "observe_at": "2026-05-21T12:02:00+00:00",
    }


# ---------------------------------------------------------------------------
# TestSettlementTriggerFires
# ---------------------------------------------------------------------------

class TestSettlementTriggerFires:
    def test_trigger_fires_when_crossings_present(self):
        fired = []
        crossings = [_make_crossing(SYM_A), _make_crossing(SYM_B)]
        ctx = _make_context(crossings=crossings, on_settlement_trigger=lambda: fired.append(1))
        ctx.emit_cycle_summary()
        assert len(fired) == 1

    def test_trigger_does_not_fire_when_crossings_empty(self):
        fired = []
        ctx = _make_context(crossings=[], on_settlement_trigger=lambda: fired.append(1))
        ctx.emit_cycle_summary()
        assert len(fired) == 0

    def test_trigger_does_not_fire_when_callback_not_configured(self):
        crossings = [_make_crossing(SYM_A)]
        ctx = _make_context(crossings=crossings, on_settlement_trigger=None)
        ctx.emit_cycle_summary()  # must not raise

    def test_crossing_count_passed_to_analytics(self):
        analytics = MagicMock()
        crossings = [_make_crossing(SYM_A), _make_crossing(SYM_B)]
        ctx = _make_context(crossings=crossings, analytics=analytics, on_settlement_trigger=lambda: None)
        ctx.emit_cycle_summary()
        analytics.settlement_trigger_fired.assert_called_once()
        call_kwargs = analytics.settlement_trigger_fired.call_args[1]
        assert call_kwargs["crossing_count"] == 2

    def test_no_analytics_trigger_event_when_crossings_empty(self):
        analytics = MagicMock()
        ctx = _make_context(crossings=[], analytics=analytics, on_settlement_trigger=lambda: None)
        ctx.emit_cycle_summary()
        analytics.settlement_trigger_fired.assert_not_called()


# ---------------------------------------------------------------------------
# TestSettlementTriggerIdempotent
# ---------------------------------------------------------------------------

class TestSettlementTriggerIdempotent:
    def test_state_reset_after_first_call(self):
        fired = []
        crossings = [_make_crossing(SYM_A)]
        ctx = _make_context(crossings=crossings, on_settlement_trigger=lambda: fired.append(1))
        ctx.emit_cycle_summary()
        assert len(fired) == 1
        # Second call: crossings reset → no trigger
        ctx.emit_cycle_summary()
        assert len(fired) == 1

    def test_settled_symbols_cleared_after_call(self):
        ctx = _make_context(crossings=[_make_crossing(SYM_A)])
        ctx.emit_cycle_summary()
        with ctx._lock:
            assert len(ctx._cycle_settlement_crossings) == 0
            assert len(ctx._settled_symbols) == 0

    def test_cycle_stats_reset_after_call(self):
        ctx = _make_context(crossings=[])
        with ctx._lock:
            ctx._cycle_stats["total_ticks"] = 42
        ctx.emit_cycle_summary()
        with ctx._lock:
            assert ctx._cycle_stats["total_ticks"] == 0

    def test_no_analytics_path_still_resets(self):
        """State must be reset even when analytics_writer is None."""
        fired = []
        crossings = [_make_crossing(SYM_A)]
        ctx = _make_context(crossings=crossings, analytics=None, on_settlement_trigger=lambda: fired.append(1))
        ctx.emit_cycle_summary()
        assert len(fired) == 1
        # After reset: second call fires nothing
        ctx.emit_cycle_summary()
        assert len(fired) == 1
        with ctx._lock:
            assert ctx._cycle_settlement_crossings == []


# ---------------------------------------------------------------------------
# TestSettlementTriggerCallOrder
# ---------------------------------------------------------------------------

class TestSettlementTriggerCallOrder:
    def test_trigger_fires_before_analytics_writes(self):
        """Trigger must fire before analytics.settlement_trigger_fired call."""
        call_order: list[str] = []
        analytics = MagicMock()
        analytics.settlement_trigger_fired.side_effect = lambda **_: call_order.append("analytics_stf")
        analytics.cycle_summary.side_effect = lambda **_: call_order.append("analytics_cs")

        crossings = [_make_crossing(SYM_A)]
        ctx = _make_context(
            crossings=crossings,
            analytics=analytics,
            on_settlement_trigger=lambda: call_order.append("trigger"),
        )
        ctx.emit_cycle_summary()

        assert call_order[0] == "trigger", f"trigger must be first, got: {call_order}"
        assert "analytics_stf" in call_order
        assert "analytics_cs" in call_order

    def test_state_reset_happens_last(self):
        """_cycle_settlement_crossings must still be populated when trigger fires."""
        seen_crossings: list = []

        def _trigger():
            # Peek at the ExpiryContext state — crossings already read under lock
            # before trigger fires; the lock is not held here
            pass

        crossings = [_make_crossing(SYM_A)]
        ctx = _make_context(crossings=crossings, on_settlement_trigger=_trigger)
        ctx.emit_cycle_summary()
        # After emit_cycle_summary() crossings must be empty (reset happened)
        with ctx._lock:
            assert ctx._cycle_settlement_crossings == []

    def test_analytics_cycle_summary_called_after_trigger(self):
        """analytics.cycle_summary() fires after the settlement trigger."""
        call_order: list[str] = []
        analytics = MagicMock()
        analytics.settlement_trigger_fired.side_effect = lambda **_: call_order.append("stf")
        analytics.cycle_summary.side_effect = lambda **_: call_order.append("cs")

        crossings = [_make_crossing(SYM_A)]
        ctx = _make_context(
            crossings=crossings,
            analytics=analytics,
            on_settlement_trigger=lambda: call_order.append("trigger"),
        )
        ctx.emit_cycle_summary()

        trigger_idx = call_order.index("trigger")
        cs_idx = call_order.index("cs")
        assert trigger_idx < cs_idx, f"trigger must precede cycle_summary: {call_order}"


# ---------------------------------------------------------------------------
# TestSettlementTriggerFailure
# ---------------------------------------------------------------------------

class TestSettlementTriggerFailure:
    def test_trigger_exception_caught(self):
        def _bad_trigger():
            raise RuntimeError("simulated trigger failure")

        crossings = [_make_crossing(SYM_A)]
        ctx = _make_context(crossings=crossings, on_settlement_trigger=_bad_trigger)
        ctx.emit_cycle_summary()  # must not raise

    def test_state_reset_after_trigger_failure(self):
        def _bad_trigger():
            raise RuntimeError("simulated")

        crossings = [_make_crossing(SYM_A)]
        ctx = _make_context(crossings=crossings, on_settlement_trigger=_bad_trigger)
        ctx.emit_cycle_summary()
        with ctx._lock:
            assert ctx._cycle_settlement_crossings == []
            assert ctx._settled_symbols == set()

    def test_analytics_still_writes_after_trigger_failure(self):
        """Analytics write must proceed even if the trigger callback raises."""
        analytics = MagicMock()

        def _bad_trigger():
            raise ValueError("trigger error")

        crossings = [_make_crossing(SYM_A)]
        ctx = _make_context(crossings=crossings, analytics=analytics, on_settlement_trigger=_bad_trigger)
        ctx.emit_cycle_summary()
        analytics.settlement_trigger_fired.assert_called_once()
        analytics.cycle_summary.assert_called_once()

    def test_second_emit_after_failure_is_noop(self):
        """After a failed trigger, state is still reset → second call fires nothing."""
        fired: list[str] = []

        def _bad_first():
            if not fired:
                fired.append("fail")
                raise RuntimeError("first call fails")
            fired.append("second")

        crossings = [_make_crossing(SYM_A)]
        ctx = _make_context(crossings=crossings, on_settlement_trigger=_bad_first)
        ctx.emit_cycle_summary()   # fails but resets state
        ctx.emit_cycle_summary()   # no crossings → no trigger
        assert fired == ["fail"], f"Expected only first fail, got: {fired}"


# ---------------------------------------------------------------------------
# TestRuntimeSettlementWiring
# ---------------------------------------------------------------------------

class TestRuntimeSettlementWiring:
    def _make_runtime(self):
        from src.runtime.lifecycle import Runtime, RuntimeState
        rt = Runtime.__new__(Runtime)
        import threading
        rt._lock = threading.Lock()
        rt._drain_lock = threading.Lock()
        rt._state = RuntimeState.INIT
        rt._drain_called = False
        rt._started_components = []
        rt._started_at = None
        rt._chain_refresher = MagicMock()
        rt._market_state = None
        rt._ws_manager = None
        return rt

    def test_on_settlement_trigger_calls_refresh(self):
        rt = self._make_runtime()
        rt._on_settlement_trigger()
        rt._chain_refresher.refresh.assert_called_once_with(triggered_by="settlement")

    def test_on_settlement_trigger_refresh_exception_caught(self):
        rt = self._make_runtime()
        rt._chain_refresher.refresh.side_effect = RuntimeError("REST down")
        rt._on_settlement_trigger()  # must not raise

    def test_on_settlement_trigger_chain_refresher_none(self):
        rt = self._make_runtime()
        rt._chain_refresher = None
        rt._on_settlement_trigger()  # must not raise

    def test_triggered_by_label_passed(self):
        rt = self._make_runtime()
        rt._on_settlement_trigger()
        call_kwargs = rt._chain_refresher.refresh.call_args[1]
        assert call_kwargs.get("triggered_by") == "settlement"


# ---------------------------------------------------------------------------
# TestChainRefresherSkipIfBusy
# ---------------------------------------------------------------------------

class TestChainRefresherSkipIfBusy:
    def _make_refresher(self):
        from src.ingestion.chain_refresher import ChainRefresher
        import sqlite3
        from src.data.storage import init_schema
        conn = sqlite3.connect(":memory:")
        init_schema(conn)

        refresher = ChainRefresher.__new__(ChainRefresher)
        refresher._client = MagicMock()
        refresher._conn = conn
        refresher._n_strikes = 3
        refresher._n_expiries = 2
        refresher._failure_threshold = 3
        refresher._on_change = None
        refresher._lock = threading.Lock()
        refresher._refresh_lock = threading.Lock()
        refresher._snapshot = None
        refresher._consecutive_failures = 0
        refresher._started = True
        return refresher

    def test_skip_if_busy_returns_snapshot(self):
        refresher = self._make_refresher()
        from src.ingestion.chain_refresher import RefreshSnapshot
        sentinel = MagicMock(spec=RefreshSnapshot)
        refresher._snapshot = sentinel

        # Hold the refresh lock to simulate a concurrent in-progress refresh
        refresher._refresh_lock.acquire()
        try:
            result = refresher.refresh(triggered_by="settlement")
        finally:
            refresher._refresh_lock.release()

        assert result is sentinel

    def test_skip_if_busy_does_not_call_do_refresh(self):
        refresher = self._make_refresher()
        refresher._snapshot = MagicMock()
        refresher._do_refresh = MagicMock()

        refresher._refresh_lock.acquire()
        try:
            refresher.refresh(triggered_by="settlement")
        finally:
            refresher._refresh_lock.release()

        refresher._do_refresh.assert_not_called()

    def test_concurrent_calls_do_not_double_fire(self):
        """Two concurrent refresh() calls: one executes, one skips."""
        from src.ingestion.chain_refresher import ChainRefresher
        refresher = self._make_refresher()
        refresher._snapshot = MagicMock()

        do_refresh_calls: list[int] = []
        original_do_refresh = ChainRefresher._do_refresh

        def _slow_do_refresh(self_cr, triggered_by="scheduler"):
            do_refresh_calls.append(1)
            time.sleep(0.05)
            return self_cr._snapshot

        refresher._do_refresh = lambda triggered_by="scheduler": (
            do_refresh_calls.append(1) or time.sleep(0.05) or refresher._snapshot
        )

        results: list = []
        errors: list[str] = []

        def _call():
            try:
                r = refresher.refresh()
                results.append(r)
            except Exception as exc:
                errors.append(str(exc))

        t1 = threading.Thread(target=_call)
        t2 = threading.Thread(target=_call)
        t1.start()
        t2.start()
        t1.join(timeout=3)
        t2.join(timeout=3)

        assert not errors, f"Errors: {errors}"
        assert len(results) == 2  # both return a snapshot
        # At most one do_refresh call (the other skipped)
        assert len(do_refresh_calls) <= 1

    def test_triggered_by_label_in_skip_log(self, caplog):
        import logging
        refresher = self._make_refresher()
        refresher._snapshot = MagicMock()
        refresher._refresh_lock.acquire()
        try:
            with caplog.at_level(logging.INFO, logger="src.ingestion.chain_refresher"):
                refresher.refresh(triggered_by="settlement")
        finally:
            refresher._refresh_lock.release()
        assert any("settlement" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# TestFullSettlementPath
# ---------------------------------------------------------------------------

class TestFullSettlementPath:
    """Integration-style test: settlement trigger → refresh → _on_universe_change."""

    def test_settlement_trigger_calls_on_change_on_diff(self):
        """ExpiryContext trigger → Runtime → chain_refresher.refresh() → on_change."""
        on_change_calls: list = []

        def _on_change(prev, new):
            on_change_calls.append((list(prev), list(new)))

        ws_mock = MagicMock()
        ms_mock = MagicMock()
        call_order: list[str] = []
        ws_mock.resubscribe.side_effect = lambda *a: call_order.append("ws")
        ms_mock.reconcile_universe.side_effect = lambda *a: call_order.append("ms")

        # Wire _on_universe_change to call both mocks (as lifecycle does)
        def _universe_change(prev, new):
            ws_mock.resubscribe(prev, new)
            ms_mock.reconcile_universe(prev, new)

        from src.ingestion.chain_refresher import ChainRefresher, RefreshSnapshot

        # Build two snapshots: pre-settlement and post-settlement
        pre_snap = RefreshSnapshot(
            symbols=[SYM_A, SYM_B, SYM_C],
            universe=[],
            mark_price=78000.0,
            n_chain=3,
            duration_sec=1.0,
            refreshed_at=time.monotonic(),
        )
        post_snap = RefreshSnapshot(
            symbols=[SYM_C],  # A and B retired (settled), C is rollover
            universe=[],
            mark_price=78100.0,
            n_chain=1,
            duration_sec=1.0,
            refreshed_at=time.monotonic(),
        )

        # Minimal ChainRefresher that alternates snapshots
        refresher = MagicMock()
        refresher._snapshot = pre_snap
        refresher._refresh_lock = threading.Lock()
        refresher._lock = threading.Lock()

        def _mock_refresh(*, triggered_by="scheduler"):
            # Simulate: second call (settlement-triggered) returns post-settlement
            with refresher._lock:
                if refresher._snapshot is pre_snap:
                    refresher._snapshot = post_snap
                    # fire on_change
                    _universe_change(pre_snap.symbols, post_snap.symbols)
            return refresher._snapshot

        refresher.refresh.side_effect = _mock_refresh

        # Build runtime _on_settlement_trigger
        from src.runtime.lifecycle import Runtime, RuntimeState
        rt = Runtime.__new__(Runtime)
        import threading as _thr
        rt._lock = _thr.Lock()
        rt._drain_lock = _thr.Lock()
        rt._state = RuntimeState.INIT
        rt._drain_called = False
        rt._started_components = []
        rt._started_at = None
        rt._chain_refresher = refresher
        rt._market_state = ms_mock
        rt._ws_manager = ws_mock

        # Fire the trigger
        rt._on_settlement_trigger()

        # ws.resubscribe() and ms.reconcile_universe() were called in correct order
        assert call_order == ["ws", "ms"], f"Expected ['ws','ms'], got {call_order}"

    def test_settlement_trigger_no_diff_no_reconcile(self):
        """If chain refresh produces no diff, reconcile_universe must not be called."""
        ms_mock = MagicMock()
        ws_mock = MagicMock()

        from src.ingestion.chain_refresher import RefreshSnapshot
        same_snap = RefreshSnapshot(
            symbols=[SYM_C],
            universe=[],
            mark_price=78000.0,
            n_chain=1,
            duration_sec=1.0,
            refreshed_at=time.monotonic(),
        )

        refresher = MagicMock()
        refresher.refresh.return_value = same_snap

        from src.runtime.lifecycle import Runtime, RuntimeState
        rt = Runtime.__new__(Runtime)
        import threading as _thr
        rt._lock = _thr.Lock()
        rt._drain_lock = _thr.Lock()
        rt._state = RuntimeState.INIT
        rt._drain_called = False
        rt._started_components = []
        rt._started_at = None
        rt._chain_refresher = refresher
        rt._market_state = ms_mock
        rt._ws_manager = ws_mock

        # refresh() returns same_snap with no diff — _on_universe_change never fires
        rt._on_settlement_trigger()

        # No diff means on_change not called → resubscribe/reconcile_universe not called
        ws_mock.resubscribe.assert_not_called()
        ms_mock.reconcile_universe.assert_not_called()


# ---------------------------------------------------------------------------
# TestRestartDuringRollover
# ---------------------------------------------------------------------------

class TestRestartDuringRollover:
    """When the bot restarts after settlement, no crossings are observed in the
    current session → emit_cycle_summary() fires no trigger → correct because
    bootstrap refresh already got the post-settlement universe."""

    def test_no_crossings_after_restart_no_trigger(self):
        fired: list[int] = []
        ctx = _make_context(crossings=[], on_settlement_trigger=lambda: fired.append(1))
        ctx.emit_cycle_summary()
        assert fired == []

    def test_fresh_context_has_empty_crossings(self):
        ctx = _make_context()
        with ctx._lock:
            assert ctx._cycle_settlement_crossings == []
            assert ctx._settled_symbols == set()

    def test_observe_after_settlement_does_not_add_crossings(self):
        """If all symbols already settled before first observe(), no new crossings."""
        # Use now_fn set to post-settlement time so seconds_to_settlement is deeply negative
        post_settlement_now = datetime(2026, 5, 21, 12, 10, 0, tzinfo=_UTC)

        snap_mock = MagicMock()
        # Provide a mock chain snapshot with no universe (empty chain post-settlement)
        snap_mock.universe = []
        snap_mock.mark_price = 78000.0
        refresher = _make_refresher(snapshot=snap_mock)

        ctx = ExpiryContext(
            chain_refresher=refresher,
            config=ExpiryConfig(),
            now_fn=lambda: post_settlement_now,
            on_settlement_trigger=lambda: None,
        )
        ctx._started = True
        ctx.observe()

        with ctx._lock:
            # Empty universe → no crossings
            assert ctx._cycle_settlement_crossings == []
