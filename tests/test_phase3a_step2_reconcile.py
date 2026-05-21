"""Phase 3a Step 2 — offline unit tests for MarketState.reconcile_universe()
and the lifecycle._on_universe_change() wiring.

Test plan
---------
  TestReconcileNoOp          -- idempotent; _reconcile_count stable
  TestReconcileAddSymbols    -- add semantics; coverage drops; stale appears
  TestReconcileRemoveSymbols -- retire semantics; purge; non-resurrection
  TestReconcileAddAndRemove  -- combined atomic operation
  TestReconcileEmptyGuard    -- empty new_symbols refused; universe unchanged
  TestUpdateTickerTOCTOU     -- membership check inside lock; no zombie entries
  TestReconcileConcurrency   -- concurrent update_ticker + reconcile is safe
  TestReconcileCountMonotonic-- count increments only on real diff
  TestUniverseAlignment      -- universe_symbols() ≡ _universe at all times
  TestLifecycleWiring        -- _on_universe_change calls both ws and ms

No live network required.
"""
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, call, patch

import pytest

from src.state.market_state import MarketState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SYMS_3 = ["C-BTC-95000-280526", "P-BTC-95000-280526", "C-BTC-96000-280526"]
SYMS_A = "C-BTC-95000-280526"
SYMS_B = "P-BTC-95000-280526"
SYMS_C = "C-BTC-96000-280526"
SYMS_D = "C-BTC-97000-280526"
SYMS_E = "P-BTC-97000-280526"

PAYLOAD_A = {"mark_price": "95100.5", "greeks": {"delta": "0.52"}}
PAYLOAD_B = {"mark_price": "400.0"}
PAYLOAD_C = {"mark_price": "500.0"}


def _ms(universe: list[str] | None = None) -> MarketState:
    return MarketState(universe=universe or list(SYMS_3))


# ---------------------------------------------------------------------------
# TestReconcileNoOp
# ---------------------------------------------------------------------------

class TestReconcileNoOp:
    def test_same_universe_no_count_increment(self):
        ms = _ms()
        before = ms._reconcile_count
        ms.reconcile_universe(SYMS_3, list(SYMS_3))
        assert ms._reconcile_count == before

    def test_same_universe_universe_unchanged(self):
        ms = _ms()
        ms.reconcile_universe(SYMS_3, list(SYMS_3))
        assert set(ms.universe_symbols()) == set(SYMS_3)

    def test_same_universe_tickers_unchanged(self):
        ms = _ms()
        ms.update_ticker(SYMS_A, PAYLOAD_A)
        ms.reconcile_universe(SYMS_3, list(SYMS_3))
        assert ms.latest_ticker(SYMS_A) == PAYLOAD_A

    def test_repeated_no_op_stable(self):
        ms = _ms()
        before = ms._reconcile_count
        for _ in range(5):
            ms.reconcile_universe(SYMS_3, list(SYMS_3))
        assert ms._reconcile_count == before

    def test_reorder_same_symbols_is_no_op(self):
        ms = _ms()
        before = ms._reconcile_count
        ms.reconcile_universe(SYMS_3, [SYMS_C, SYMS_A, SYMS_B])
        assert ms._reconcile_count == before


# ---------------------------------------------------------------------------
# TestReconcileAddSymbols
# ---------------------------------------------------------------------------

class TestReconcileAddSymbols:
    def test_added_symbol_in_universe(self):
        ms = _ms([SYMS_A, SYMS_B])
        ms.reconcile_universe([SYMS_A, SYMS_B], [SYMS_A, SYMS_B, SYMS_C])
        assert SYMS_C in ms.universe_symbols()

    def test_added_symbol_no_phantom_ticker(self):
        ms = _ms([SYMS_A, SYMS_B])
        ms.reconcile_universe([SYMS_A, SYMS_B], [SYMS_A, SYMS_B, SYMS_C])
        assert ms.latest_ticker(SYMS_C) is None

    def test_added_symbol_no_freshness(self):
        ms = _ms([SYMS_A, SYMS_B])
        ms.reconcile_universe([SYMS_A, SYMS_B], [SYMS_A, SYMS_B, SYMS_C])
        assert ms.freshness_sec(SYMS_C) is None

    def test_added_symbol_appears_stale(self):
        ms = _ms([SYMS_A, SYMS_B])
        ms.reconcile_universe([SYMS_A, SYMS_B], [SYMS_A, SYMS_B, SYMS_C])
        # threshold=0 → every symbol with no tick is stale
        stale = ms.stale_symbols(0)
        assert SYMS_C in stale

    def test_coverage_drops_after_add(self):
        ms = _ms([SYMS_A, SYMS_B])
        ms.update_ticker(SYMS_A, PAYLOAD_A)
        ms.update_ticker(SYMS_B, PAYLOAD_B)
        assert ms.coverage_pct() == 1.0
        ms.reconcile_universe([SYMS_A, SYMS_B], [SYMS_A, SYMS_B, SYMS_C])
        assert ms.coverage_pct() < 1.0

    def test_added_symbol_accepts_tick_after_reconcile(self):
        ms = _ms([SYMS_A, SYMS_B])
        ms.reconcile_universe([SYMS_A, SYMS_B], [SYMS_A, SYMS_B, SYMS_C])
        ms.update_ticker(SYMS_C, PAYLOAD_C)
        assert ms.latest_ticker(SYMS_C) == PAYLOAD_C

    def test_count_incremented_on_add(self):
        ms = _ms([SYMS_A, SYMS_B])
        before = ms._reconcile_count
        ms.reconcile_universe([SYMS_A, SYMS_B], [SYMS_A, SYMS_B, SYMS_C])
        assert ms._reconcile_count == before + 1


# ---------------------------------------------------------------------------
# TestReconcileRemoveSymbols
# ---------------------------------------------------------------------------

class TestReconcileRemoveSymbols:
    def test_retired_symbol_not_in_universe(self):
        ms = _ms()
        ms.reconcile_universe(SYMS_3, [SYMS_A, SYMS_C])
        assert SYMS_B not in ms.universe_symbols()

    def test_retired_symbol_not_in_tickers(self):
        ms = _ms()
        ms.update_ticker(SYMS_B, PAYLOAD_B)
        ms.reconcile_universe(SYMS_3, [SYMS_A, SYMS_C])
        assert SYMS_B not in ms.all_tickers()

    def test_retired_symbol_freshness_none(self):
        ms = _ms()
        ms.update_ticker(SYMS_B, PAYLOAD_B)
        ms.reconcile_universe(SYMS_3, [SYMS_A, SYMS_C])
        assert ms.freshness_sec(SYMS_B) is None

    def test_retired_symbol_not_in_stale(self):
        ms = _ms()
        ms.reconcile_universe(SYMS_3, [SYMS_A, SYMS_C])
        stale = ms.stale_symbols(0)
        assert SYMS_B not in stale

    def test_non_resurrection_after_retire(self):
        """update_ticker for a retired symbol must be silently rejected."""
        ms = _ms()
        ms.update_ticker(SYMS_B, PAYLOAD_B)
        ms.reconcile_universe(SYMS_3, [SYMS_A, SYMS_C])
        ms.update_ticker(SYMS_B, {"mark_price": "999"})
        assert SYMS_B not in ms.all_tickers()

    def test_surviving_symbols_tickers_intact(self):
        ms = _ms()
        ms.update_ticker(SYMS_A, PAYLOAD_A)
        ms.update_ticker(SYMS_C, PAYLOAD_C)
        ms.reconcile_universe(SYMS_3, [SYMS_A, SYMS_C])
        assert ms.latest_ticker(SYMS_A) == PAYLOAD_A
        assert ms.latest_ticker(SYMS_C) == PAYLOAD_C

    def test_count_incremented_on_remove(self):
        ms = _ms()
        before = ms._reconcile_count
        ms.reconcile_universe(SYMS_3, [SYMS_A, SYMS_C])
        assert ms._reconcile_count == before + 1

    def test_universe_size_reduced(self):
        ms = _ms()
        ms.reconcile_universe(SYMS_3, [SYMS_A, SYMS_C])
        assert ms.universe_size() == 2

    def test_all_tickers_excludes_retired(self):
        ms = _ms()
        ms.update_ticker(SYMS_A, PAYLOAD_A)
        ms.update_ticker(SYMS_B, PAYLOAD_B)
        ms.reconcile_universe(SYMS_3, [SYMS_A, SYMS_C])
        snap = ms.all_tickers()
        assert SYMS_B not in snap
        assert SYMS_A in snap


# ---------------------------------------------------------------------------
# TestReconcileAddAndRemove
# ---------------------------------------------------------------------------

class TestReconcileAddAndRemove:
    def test_combined_add_remove_universe(self):
        ms = _ms([SYMS_A, SYMS_B, SYMS_C])
        # Remove B+C, add D+E
        ms.reconcile_universe(
            [SYMS_A, SYMS_B, SYMS_C],
            [SYMS_A, SYMS_D, SYMS_E],
        )
        syms = set(ms.universe_symbols())
        assert SYMS_A in syms
        assert SYMS_D in syms
        assert SYMS_E in syms
        assert SYMS_B not in syms
        assert SYMS_C not in syms

    def test_combined_add_remove_tickers(self):
        ms = _ms([SYMS_A, SYMS_B, SYMS_C])
        ms.update_ticker(SYMS_A, PAYLOAD_A)
        ms.update_ticker(SYMS_B, PAYLOAD_B)
        ms.update_ticker(SYMS_C, PAYLOAD_C)
        ms.reconcile_universe(
            [SYMS_A, SYMS_B, SYMS_C],
            [SYMS_A, SYMS_D, SYMS_E],
        )
        snap = ms.all_tickers()
        assert SYMS_A in snap
        assert SYMS_B not in snap
        assert SYMS_C not in snap

    def test_count_incremented_once_for_combined(self):
        ms = _ms([SYMS_A, SYMS_B, SYMS_C])
        before = ms._reconcile_count
        ms.reconcile_universe(
            [SYMS_A, SYMS_B, SYMS_C],
            [SYMS_A, SYMS_D, SYMS_E],
        )
        assert ms._reconcile_count == before + 1

    def test_added_symbols_accept_ticks_after(self):
        ms = _ms([SYMS_A, SYMS_B])
        ms.reconcile_universe([SYMS_A, SYMS_B], [SYMS_A, SYMS_C])
        ms.update_ticker(SYMS_C, PAYLOAD_C)
        assert ms.latest_ticker(SYMS_C) == PAYLOAD_C

    def test_removed_symbols_rejected_after(self):
        ms = _ms([SYMS_A, SYMS_B])
        ms.reconcile_universe([SYMS_A, SYMS_B], [SYMS_A, SYMS_C])
        ms.update_ticker(SYMS_B, PAYLOAD_B)
        assert SYMS_B not in ms.all_tickers()


# ---------------------------------------------------------------------------
# TestReconcileEmptyGuard
# ---------------------------------------------------------------------------

class TestReconcileEmptyGuard:
    def test_empty_new_symbols_refused(self):
        ms = _ms()
        before_size = ms.universe_size()
        before_count = ms._reconcile_count
        ms.reconcile_universe(SYMS_3, [])
        assert ms.universe_size() == before_size
        assert ms._reconcile_count == before_count

    def test_empty_new_symbols_universe_preserved(self):
        ms = _ms([SYMS_A, SYMS_B])
        ms.reconcile_universe([SYMS_A, SYMS_B], [])
        assert set(ms.universe_symbols()) == {SYMS_A, SYMS_B}

    def test_empty_new_symbols_tickers_preserved(self):
        ms = _ms([SYMS_A])
        ms.update_ticker(SYMS_A, PAYLOAD_A)
        ms.reconcile_universe([SYMS_A], [])
        assert ms.latest_ticker(SYMS_A) == PAYLOAD_A


# ---------------------------------------------------------------------------
# TestUpdateTickerTOCTOU
# ---------------------------------------------------------------------------

class TestUpdateTickerTOCTOU:
    """Verify the membership check in update_ticker is inside _lock.

    We cannot directly race the lock in a unit test, so we verify the
    semantics: after retirement, no tick can resurrect the symbol
    regardless of call ordering.
    """

    def test_tick_after_retire_rejected(self):
        ms = _ms()
        ms.update_ticker(SYMS_B, PAYLOAD_B)
        ms.reconcile_universe(SYMS_3, [SYMS_A, SYMS_C])
        ms.update_ticker(SYMS_B, {"mark_price": "999"})
        assert SYMS_B not in ms.all_tickers()

    def test_tick_between_retires_rejected(self):
        ms = _ms([SYMS_A, SYMS_B, SYMS_C])
        ms.update_ticker(SYMS_B, PAYLOAD_B)
        # First retire B
        ms.reconcile_universe([SYMS_A, SYMS_B, SYMS_C], [SYMS_A, SYMS_C])
        # Try to inject a tick — must be rejected
        ms.update_ticker(SYMS_B, {"mark_price": "777"})
        # Now re-add B
        ms.reconcile_universe([SYMS_A, SYMS_C], [SYMS_A, SYMS_B, SYMS_C])
        # B is back in universe but should have no phantom tick from step above
        assert ms.latest_ticker(SYMS_B) is None

    def test_fresh_tick_accepted_after_readd(self):
        ms = _ms([SYMS_A, SYMS_B])
        ms.update_ticker(SYMS_B, PAYLOAD_B)
        ms.reconcile_universe([SYMS_A, SYMS_B], [SYMS_A])
        ms.reconcile_universe([SYMS_A], [SYMS_A, SYMS_B])
        # Now a fresh tick for B must be accepted
        new_payload = {"mark_price": "111"}
        ms.update_ticker(SYMS_B, new_payload)
        assert ms.latest_ticker(SYMS_B) == new_payload


# ---------------------------------------------------------------------------
# TestReconcileConcurrency
# ---------------------------------------------------------------------------

class TestReconcileConcurrency:
    """Stress test: concurrent update_ticker and reconcile_universe calls.

    Goal: no deadlock, no crash, final state consistent.
    """

    def test_concurrent_update_and_reconcile(self):
        ms = _ms([SYMS_A, SYMS_B, SYMS_C])
        errors: list[str] = []
        stop = threading.Event()

        def ticker_writer():
            i = 0
            while not stop.is_set():
                try:
                    ms.update_ticker(SYMS_A, {"mark_price": str(i)})
                    ms.update_ticker(SYMS_B, {"mark_price": str(i + 1)})
                    i += 1
                except Exception as exc:
                    errors.append(f"ticker_writer: {exc}")
                    break

        def reconciler():
            new_u1 = [SYMS_A, SYMS_C]
            new_u2 = [SYMS_A, SYMS_B, SYMS_C]
            for _ in range(20):
                try:
                    ms.reconcile_universe([SYMS_A, SYMS_B, SYMS_C], new_u1)
                    time.sleep(0.001)
                    ms.reconcile_universe(new_u1, new_u2)
                    time.sleep(0.001)
                except Exception as exc:
                    errors.append(f"reconciler: {exc}")
                    break

        t_write = threading.Thread(target=ticker_writer, daemon=True)
        t_rec   = threading.Thread(target=reconciler, daemon=True)
        t_write.start()
        t_rec.start()
        t_rec.join(timeout=5)
        stop.set()
        t_write.join(timeout=2)

        assert not errors, f"Concurrency errors: {errors}"
        # Universe must be valid (one of the two states)
        final = set(ms.universe_symbols())
        assert final in ({SYMS_A, SYMS_C}, {SYMS_A, SYMS_B, SYMS_C})

    def test_concurrent_reads_no_crash(self):
        ms = _ms()
        ms.update_ticker(SYMS_A, PAYLOAD_A)
        errors: list[str] = []
        stop = threading.Event()

        def reader():
            while not stop.is_set():
                try:
                    ms.all_tickers()
                    ms.universe_symbols()
                    ms.coverage_pct()
                    ms.stale_symbols(1)
                    repr(ms)
                except Exception as exc:
                    errors.append(f"reader: {exc}")
                    break

        def writer():
            for i in range(10):
                try:
                    ms.reconcile_universe(SYMS_3, [SYMS_A, SYMS_B])
                    time.sleep(0.002)
                    ms.reconcile_universe([SYMS_A, SYMS_B], SYMS_3)
                    time.sleep(0.002)
                except Exception as exc:
                    errors.append(f"writer: {exc}")
                    break

        threads = [threading.Thread(target=reader, daemon=True) for _ in range(3)]
        t_write = threading.Thread(target=writer, daemon=True)
        for t in threads:
            t.start()
        t_write.start()
        t_write.join(timeout=5)
        stop.set()
        for t in threads:
            t.join(timeout=2)

        assert not errors, f"Concurrent read/write errors: {errors}"


# ---------------------------------------------------------------------------
# TestReconcileCountMonotonic
# ---------------------------------------------------------------------------

class TestReconcileCountMonotonic:
    def test_starts_at_zero(self):
        ms = _ms()
        assert ms._reconcile_count == 0

    def test_add_increments(self):
        ms = _ms([SYMS_A])
        ms.reconcile_universe([SYMS_A], [SYMS_A, SYMS_B])
        assert ms._reconcile_count == 1

    def test_remove_increments(self):
        ms = _ms([SYMS_A, SYMS_B])
        ms.reconcile_universe([SYMS_A, SYMS_B], [SYMS_A])
        assert ms._reconcile_count == 1

    def test_no_op_no_increment(self):
        ms = _ms([SYMS_A, SYMS_B])
        ms.reconcile_universe([SYMS_A, SYMS_B], [SYMS_A, SYMS_B])
        assert ms._reconcile_count == 0

    def test_monotonic_across_multiple_changes(self):
        ms = _ms([SYMS_A])
        for i in range(5):
            ms.reconcile_universe([SYMS_A], [SYMS_A, SYMS_B])
            ms.reconcile_universe([SYMS_A, SYMS_B], [SYMS_A])
        # 5 real changes: add B, remove B (×5) = 10 non-no-op calls
        assert ms._reconcile_count == 10


# ---------------------------------------------------------------------------
# TestUniverseAlignment
# ---------------------------------------------------------------------------

class TestUniverseAlignment:
    """universe_symbols() must always reflect _universe exactly."""

    def test_alignment_initial(self):
        ms = _ms()
        assert set(ms.universe_symbols()) == ms._universe

    def test_alignment_after_add(self):
        ms = _ms([SYMS_A, SYMS_B])
        ms.reconcile_universe([SYMS_A, SYMS_B], [SYMS_A, SYMS_B, SYMS_C])
        assert set(ms.universe_symbols()) == ms._universe

    def test_alignment_after_remove(self):
        ms = _ms()
        ms.reconcile_universe(SYMS_3, [SYMS_A, SYMS_C])
        assert set(ms.universe_symbols()) == ms._universe

    def test_alignment_after_combined(self):
        ms = _ms([SYMS_A, SYMS_B, SYMS_C])
        ms.reconcile_universe(
            [SYMS_A, SYMS_B, SYMS_C],
            [SYMS_A, SYMS_D, SYMS_E],
        )
        assert set(ms.universe_symbols()) == ms._universe

    def test_universe_symbols_sorted(self):
        ms = _ms([SYMS_C, SYMS_A, SYMS_B])
        syms = ms.universe_symbols()
        assert syms == sorted(syms)


# ---------------------------------------------------------------------------
# TestLifecycleWiring
# ---------------------------------------------------------------------------

class TestLifecycleWiring:
    """_on_universe_change must call both resubscribe() and reconcile_universe()
    in the correct order (WS first, MS second).
    """

    def _make_runtime(self):
        """Build a Runtime with all components mocked (no live network)."""
        from src.runtime.lifecycle import Runtime

        rt = Runtime.__new__(Runtime)
        # Minimal required attributes from __init__
        import threading
        rt._lock         = threading.Lock()
        rt._drain_lock   = threading.Lock()
        rt._state        = __import__("src.runtime.lifecycle", fromlist=["RuntimeState"]).RuntimeState.INIT
        rt._drain_called = False
        rt._started_components = []
        rt._started_at   = None

        rt._ws_manager    = MagicMock()
        rt._market_state  = MagicMock()
        return rt

    def test_resubscribe_called(self):
        rt = self._make_runtime()
        rt._on_universe_change([SYMS_A], [SYMS_A, SYMS_B])
        rt._ws_manager.resubscribe.assert_called_once_with([SYMS_A], [SYMS_A, SYMS_B])

    def test_reconcile_universe_called(self):
        rt = self._make_runtime()
        rt._on_universe_change([SYMS_A], [SYMS_A, SYMS_B])
        rt._market_state.reconcile_universe.assert_called_once_with(
            [SYMS_A], [SYMS_A, SYMS_B]
        )

    def test_resubscribe_before_reconcile(self):
        """Verify call order: resubscribe() is always called before reconcile_universe()."""
        rt = self._make_runtime()
        call_order: list[str] = []
        rt._ws_manager.resubscribe.side_effect    = lambda *a: call_order.append("ws")
        rt._market_state.reconcile_universe.side_effect = lambda *a: call_order.append("ms")

        rt._on_universe_change([SYMS_A], [SYMS_A, SYMS_B])
        assert call_order == ["ws", "ms"], f"Expected ['ws','ms'], got {call_order}"

    def test_ws_manager_none_no_crash(self):
        rt = self._make_runtime()
        rt._ws_manager = None
        rt._on_universe_change([SYMS_A], [SYMS_A, SYMS_B])
        rt._market_state.reconcile_universe.assert_called_once()

    def test_market_state_none_no_crash(self):
        rt = self._make_runtime()
        rt._market_state = None
        rt._on_universe_change([SYMS_A], [SYMS_A, SYMS_B])
        rt._ws_manager.resubscribe.assert_called_once()

    def test_both_none_no_crash(self):
        rt = self._make_runtime()
        rt._ws_manager   = None
        rt._market_state = None
        rt._on_universe_change([SYMS_A], [SYMS_A, SYMS_B])  # must not raise
