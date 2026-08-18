"""Tests for the testnet demo execution stack (B2/B3/B4/B5/B6).

No test may place a real order. Every exchange interaction is mocked, and a
`_NoNetworkClient` is used wherever the point is that NO call happened at all --
it raises on any method, so a rejection path that touched the network fails
loudly rather than silently passing.

Run:
    conda run -n btc_bot pytest tests/test_demo_execution.py -v
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest
import requests

from src.data import storage
from src.execution.order_state import (
    ALLOWED_TRANSITIONS, IllegalTransition, OrderState, can_transition,
    is_open, is_terminal, transition,
)
from src.execution.reconciler import Divergence, Reconciler
from src.execution.testnet_executor import (
    ExecutionBlocked, InstrumentMissing, TestnetExecutor,
)
from src.risk import limits
from src.risk.manager import (
    AccountSnapshot, OrderIntent, RiskManager, compute_trade_risk_usd,
    round_trip_fees_usd,
)
from src.strategies import bridge

MARK = 64187.5
TESTNET_URL = "https://cdn-ind.testnet.deltaex.org"


# ---------------------------------------------------------------------------
# Fixtures / doubles
# ---------------------------------------------------------------------------

class _NoNetworkClient:
    """Any attribute access is a test failure: proves zero network calls."""

    def __getattr__(self, name):
        def _boom(*a, **kw):
            raise AssertionError(f"network call {name}() made on a rejected path")
        return _boom


class _FakeClient:
    def __init__(self, place=None, live=None, fills=None, positions=None, cancel=None):
        self._place = place
        self._live = live if live is not None else {"success": True, "result": []}
        self._fills = fills if fills is not None else {"success": True, "result": []}
        self._positions = positions if positions is not None else {"success": True, "result": []}
        self._cancel = cancel if cancel is not None else {"success": True, "result": {}}
        self.calls: list[str] = []

    def place_order(self, **kw):
        self.calls.append("place_order")
        if isinstance(self._place, Exception):
            raise self._place
        if callable(self._place):
            return self._place(**kw)
        return self._place

    def cancel_order(self, order_id, product_id):
        self.calls.append("cancel_order")
        if isinstance(self._cancel, Exception):
            raise self._cancel
        return self._cancel

    def get_live_orders(self, product_id=None):
        self.calls.append("get_live_orders")
        return self._live

    def get_fills(self, product_id=None):
        self.calls.append("get_fills")
        return self._fills

    def get_positions(self):
        self.calls.append("get_positions")
        return self._positions


@pytest.fixture
def conn(tmp_path):
    c = storage.get_connection(tmp_path / "t.db")
    storage.init_schema(c)
    storage.init_execution_schema(c)
    c.execute(
        "INSERT OR REPLACE INTO instruments "
        "(id, symbol, contract_type, underlying, quoting, tick_size, contract_value, state, fetched_at) "
        "VALUES (84,'BTCUSD','perpetual_futures','BTC','USD',0.1,0.001,'live','2026-08-18T00:00:00+00:00')"
    )
    c.commit()
    yield c
    c.close()


def _account(**kw):
    base = dict(equity_usd=200.0, available_usd=200.0, open_positions=0, mark_price=MARK)
    base.update(kw)
    return AccountSnapshot(**base)


def _intent(**kw):
    base = dict(
        client_order_id="test-2026-08-18-buy-1", symbol="BTCUSD", product_id=84,
        side="buy", size=1, order_type="limit_order",
        limit_price=64187.5, stop_price=63287.5, strategy="test",   # $900 move + $0.064 fees = $0.964, inside the $1.00 budget
    )
    base.update(kw)
    return OrderIntent(**base)


def _risk(**kw):
    base = dict(kill_switch_engaged=False, platform="testnet", base_url=TESTNET_URL)
    base.update(kw)
    return RiskManager(**base)


NOON = datetime(2026, 8, 18, 9, 0, 0, tzinfo=timezone.utc)   # outside dead zone


# ---------------------------------------------------------------------------
# 1 — Risk: every rejection, and each proves zero network calls
# ---------------------------------------------------------------------------

class TestRiskRejections:
    def test_baseline_intent_is_approved(self):
        d = _risk().validate(_intent(), _account(), now=NOON)
        assert d.approved, d.reason
        assert d.risk_usd is not None and d.risk_usd <= limits.MAX_TRADE_RISK_USD

    def test_kill_switch_blocks(self):
        d = _risk(kill_switch_engaged=True).validate(_intent(), _account(), now=NOON)
        assert not d.approved and "kill switch" in d.reason

    def test_halt_latch_blocks(self):
        d = _risk(halted=True, halt_reason="position mismatch").validate(
            _intent(), _account(), now=NOON)
        assert not d.approved and "halted" in d.reason

    def test_non_testnet_platform_blocks(self):
        d = _risk(platform="production").validate(_intent(), _account(), now=NOON)
        assert not d.approved and "not testnet" in d.reason

    def test_non_testnet_url_blocks(self):
        d = _risk(base_url="https://api.india.delta.exchange").validate(
            _intent(), _account(), now=NOON)
        assert not d.approved and "not a testnet endpoint" in d.reason

    def test_non_allowlisted_symbol_blocks(self):
        d = _risk().validate(_intent(symbol="ETHUSD"), _account(), now=NOON)
        assert not d.approved and "not allowlisted" in d.reason

    def test_non_allowlisted_product_id_blocks(self):
        d = _risk().validate(_intent(product_id=999), _account(), now=NOON)
        assert not d.approved and "product_id" in d.reason

    def test_market_order_blocks(self):
        d = _risk().validate(_intent(order_type="market_order"), _account(), now=NOON)
        assert not d.approved and "limit only" in d.reason

    def test_invalid_side_blocks(self):
        d = _risk().validate(_intent(side="hodl"), _account(), now=NOON)
        assert not d.approved and "invalid side" in d.reason

    def test_size_two_blocks(self):
        d = _risk().validate(_intent(size=2), _account(), now=NOON)
        assert not d.approved and "exceeds max" in d.reason

    def test_size_zero_blocks(self):
        d = _risk().validate(_intent(size=0), _account(), now=NOON)
        assert not d.approved and "below minimum" in d.reason

    def test_second_position_blocks(self):
        d = _risk().validate(_intent(), _account(open_positions=1), now=NOON)
        assert not d.approved and "open position" in d.reason

    def test_third_order_of_day_blocks(self):
        d = _risk(orders_placed_today=2).validate(_intent(), _account(), now=NOON)
        assert not d.approved and "orders today" in d.reason

    def test_daily_loss_cap_blocks(self):
        d = _risk(daily_realised_pnl=-limits.MAX_DAILY_LOSS_USD).validate(
            _intent(), _account(), now=NOON)
        assert not d.approved and "daily loss" in d.reason

    def test_settlement_window_blocks(self):
        for t in (datetime(2026, 8, 18, 11, 58, tzinfo=timezone.utc),
                  datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc),
                  datetime(2026, 8, 18, 12, 6, tzinfo=timezone.utc)):
            d = _risk().validate(_intent(), _account(), now=t)
            assert not d.approved and "settlement dead zone" in d.reason, t

    def test_just_outside_settlement_window_allowed(self):
        for t in (datetime(2026, 8, 18, 11, 57, tzinfo=timezone.utc),
                  datetime(2026, 8, 18, 12, 7, tzinfo=timezone.utc)):
            assert _risk().validate(_intent(), _account(), now=t).approved, t

    def test_price_too_far_from_mark_blocks(self):
        d = _risk().validate(_intent(limit_price=MARK * 1.05, stop_price=MARK),
                             _account(), now=NOON)
        assert not d.approved and "from mark" in d.reason

    def test_non_tick_aligned_price_blocks(self):
        d = _risk().validate(_intent(limit_price=64187.53), _account(), now=NOON)
        assert not d.approved and "not aligned to tick" in d.reason

    def test_missing_stop_blocks(self):
        d = _risk().validate(_intent(stop_price=None), _account(), now=NOON)
        assert not d.approved and "unbounded" in d.reason

    def test_risk_over_budget_blocks(self):
        # 2% stop distance -> above the $1 budget, below the $2 ceiling
        d = _risk().validate(_intent(stop_price=64187.5 - 1300.0), _account(), now=NOON)
        assert not d.approved and "per-trade budget" in d.reason

    def test_risk_over_hard_ceiling_blocks(self):
        d = _risk().validate(_intent(stop_price=64187.5 - 2500.0), _account(), now=NOON)
        assert not d.approved and "HARD ceiling" in d.reason

    def test_equity_floor_blocks(self):
        d = _risk().validate(_intent(), _account(available_usd=5.0), now=NOON)
        assert not d.approved and "below floor" in d.reason

    def test_zero_mark_price_blocks(self):
        d = _risk().validate(_intent(), _account(mark_price=0.0), now=NOON)
        assert not d.approved and "mark price unavailable" in d.reason

    def test_every_rejection_makes_zero_network_calls(self, conn):
        """The load-bearing safety property."""
        cases = [
            (_risk(kill_switch_engaged=True), _intent(), _account()),
            (_risk(platform="production"), _intent(), _account()),
            (_risk(), _intent(symbol="ETHUSD"), _account()),
            (_risk(), _intent(size=2), _account()),
            (_risk(), _intent(order_type="market_order"), _account()),
            (_risk(), _intent(stop_price=None), _account()),
            (_risk(), _intent(), _account(open_positions=1)),
            (_risk(orders_placed_today=2), _intent(), _account()),
        ]
        for rm, it, acct in cases:
            ex = TestnetExecutor(conn, _NoNetworkClient(), rm)
            state, decision = ex.submit(it, acct, now=NOON)   # must not raise
            assert not decision.approved
            assert state is OrderState.BLOCKED


class TestRiskArithmetic:
    def test_fees_included_in_risk(self):
        it = _intent(stop_price=64187.5 - 1000.0)
        price_only = 1000.0 * limits.BTCUSD_CONTRACT_VALUE
        total = compute_trade_risk_usd(it, MARK)
        assert total > price_only
        assert total == pytest.approx(price_only + round_trip_fees_usd(MARK, 1))

    def test_round_trip_fee_magnitude(self):
        assert round_trip_fees_usd(MARK, 1) == pytest.approx(0.0642, abs=1e-3)

    def test_one_contract_fits_budget_at_reference_mark(self):
        assert compute_trade_risk_usd(_intent(stop_price=63287.5), MARK) <= limits.MAX_TRADE_RISK_USD


# ---------------------------------------------------------------------------
# 2 — State machine
# ---------------------------------------------------------------------------

class TestStateMachine:
    def test_happy_path(self):
        s = OrderState.INTENT
        for nxt in (OrderState.VALIDATED, OrderState.SUBMITTED, OrderState.ACKED,
                    OrderState.FILLED):
            s = transition(s, nxt)
        assert is_terminal(s)

    def test_partial_then_filled(self):
        s = transition(transition(transition(OrderState.INTENT, OrderState.VALIDATED),
                                  OrderState.SUBMITTED), OrderState.ACKED)
        s = transition(s, OrderState.PARTIAL)
        s = transition(s, OrderState.PARTIAL)      # repeated partials legal
        assert transition(s, OrderState.FILLED) is OrderState.FILLED

    @pytest.mark.parametrize("terminal", sorted(
        [OrderState.FILLED, OrderState.CANCELLED, OrderState.REJECTED, OrderState.BLOCKED],
        key=lambda s: s.value))
    def test_terminal_states_are_immutable(self, terminal):
        assert is_terminal(terminal)
        with pytest.raises(IllegalTransition, match="terminal"):
            transition(terminal, OrderState.ACKED)

    def test_illegal_arcs_raise(self):
        for src, dst in ((OrderState.INTENT, OrderState.ACKED),
                         (OrderState.INTENT, OrderState.FILLED),
                         (OrderState.VALIDATED, OrderState.FILLED),
                         (OrderState.SUBMITTED, OrderState.PARTIAL),
                         (OrderState.ACKED, OrderState.SUBMITTED)):
            assert not can_transition(src, dst)
            with pytest.raises(IllegalTransition):
                transition(src, dst)

    def test_unknown_requires_reconcile_flag(self):
        with pytest.raises(IllegalTransition, match="reconciliation-only"):
            transition(OrderState.UNKNOWN, OrderState.FILLED)
        assert transition(OrderState.UNKNOWN, OrderState.FILLED,
                          reconcile_only=True) is OrderState.FILLED

    def test_open_states_are_exactly_the_reconcilable_ones(self):
        assert {s for s in OrderState if is_open(s)} == {
            OrderState.SUBMITTED, OrderState.ACKED, OrderState.PARTIAL, OrderState.UNKNOWN}

    def test_every_state_has_a_transition_entry(self):
        assert set(ALLOWED_TRANSITIONS) == set(OrderState)


# ---------------------------------------------------------------------------
# 3 — Executor: happy path, idempotency, failure injection
# ---------------------------------------------------------------------------

def _ok_place(**kw):
    return {"success": True, "result": {"id": 555001, "state": "open"}}


class TestExecutorHappyPath:
    def test_submit_acks_and_journals(self, conn):
        c = _FakeClient(place=_ok_place)
        state, d = TestnetExecutor(conn, c, _risk()).submit(_intent(), _account(), now=NOON)
        assert state is OrderState.ACKED and d.approved
        row = storage.get_order(conn, "test-2026-08-18-buy-1")
        assert row["status"] == "acked" and row["delta_order_id"] == "555001"
        assert row["instrument_id"] == 84 and row["quantity"] == 1.0

    def test_write_ahead_row_exists_before_ack(self, conn):
        """A crash mid-submit must still leave a durable row."""
        seen = {}

        def _capture(**kw):
            seen["row"] = storage.get_order(conn, "test-2026-08-18-buy-1")
            return _ok_place(**kw)

        TestnetExecutor(conn, _FakeClient(place=_capture), _risk()).submit(
            _intent(), _account(), now=NOON)
        assert seen["row"] is not None
        assert seen["row"]["status"] == "submitted"


class TestIdempotency:
    def test_same_client_order_id_yields_one_row(self, conn):
        c = _FakeClient(place=_ok_place)
        ex = TestnetExecutor(conn, c, _risk())
        ex.submit(_intent(), _account(), now=NOON)
        ex.submit(_intent(), _account(), now=NOON)
        n = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE client_order_id = ?",
            ("test-2026-08-18-buy-1",)).fetchone()[0]
        assert n == 1

    def test_unique_index_rejects_duplicate_at_db_level(self, conn):
        conn.execute(
            "INSERT INTO orders (client_order_id, instrument_id, side, order_type,"
            " quantity, price, status, placed_ts) VALUES ('dup',84,'buy','limit_order',1,1,'acked','t')")
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO orders (client_order_id, instrument_id, side, order_type,"
                " quantity, price, status, placed_ts) VALUES ('dup',84,'buy','limit_order',1,1,'acked','t')")
            conn.commit()


class TestFailureInjection:
    def test_timeout_becomes_unknown_not_retried(self, conn):
        c = _FakeClient(place=requests.exceptions.Timeout("timed out"))
        state, _ = TestnetExecutor(conn, c, _risk()).submit(_intent(), _account(), now=NOON)
        assert state is OrderState.UNKNOWN
        assert c.calls == ["place_order"], "must not retry an ambiguous submit"
        assert storage.get_order(conn, "test-2026-08-18-buy-1")["status"] == "unknown"

    def test_4xx_is_definitive_rejection(self, conn):
        resp = requests.Response()
        resp.status_code = 400
        c = _FakeClient(place=requests.exceptions.HTTPError(response=resp))
        state, _ = TestnetExecutor(conn, c, _risk()).submit(_intent(), _account(), now=NOON)
        assert state is OrderState.REJECTED

    def test_5xx_is_ambiguous(self, conn):
        resp = requests.Response()
        resp.status_code = 503
        c = _FakeClient(place=requests.exceptions.HTTPError(response=resp))
        state, _ = TestnetExecutor(conn, c, _risk()).submit(_intent(), _account(), now=NOON)
        assert state is OrderState.UNKNOWN

    def test_connection_reset_after_send_is_ambiguous(self, conn):
        c = _FakeClient(place=requests.exceptions.ConnectionError("reset by peer"))
        state, _ = TestnetExecutor(conn, c, _risk()).submit(_intent(), _account(), now=NOON)
        assert state is OrderState.UNKNOWN

    def test_malformed_response_is_ambiguous(self, conn):
        for bad in ({"success": False}, {"nope": 1}, "not-a-dict", None):
            conn.execute("DELETE FROM orders")
            conn.commit()
            c = _FakeClient(place=bad)
            state, _ = TestnetExecutor(conn, c, _risk()).submit(_intent(), _account(), now=NOON)
            assert state is OrderState.UNKNOWN, bad

    def test_missing_instrument_raises_before_network(self, conn):
        conn.execute("DELETE FROM instruments WHERE id = 84")
        conn.commit()
        with pytest.raises(InstrumentMissing):
            TestnetExecutor(conn, _NoNetworkClient(), _risk()).submit(
                _intent(), _account(), now=NOON)


# ---------------------------------------------------------------------------
# 4 — Reconciliation
# ---------------------------------------------------------------------------

def _seed_order(conn, coid, status, delta_id=None):
    storage.write_order(
        conn, client_order_id=coid, instrument_id=84, side="buy",
        order_type="limit_order", quantity=1.0, price=64187.5, status=status,
        strategy="test", placed_ts="2026-08-18T09:00:00+00:00",
        delta_order_id=delta_id)


class TestReconciliation:
    def test_in_sync(self, conn):
        _seed_order(conn, "c1", "acked", "1")
        c = _FakeClient(live={"success": True,
                              "result": [{"id": 1, "client_order_id": "c1", "state": "open"}]})
        r = Reconciler(conn, c, product_id=84).reconcile()
        assert r.clean and Divergence.IN_SYNC in r.divergences

    def test_local_stale_adopts_exchange(self, conn):
        _seed_order(conn, "c2", "acked", "2")
        c = _FakeClient(live={"success": True,
                              "result": [{"id": 2, "client_order_id": "c2",
                                          "state": "partially_filled"}]})
        r = Reconciler(conn, c, product_id=84).reconcile()
        assert Divergence.LOCAL_STALE in r.divergences
        assert storage.get_order(conn, "c2")["status"] == "partial"

    def test_unknown_resolved_by_fills(self, conn):
        _seed_order(conn, "c3", "unknown")
        c = _FakeClient(fills={"success": True, "result": [{"client_order_id": "c3"}]})
        Reconciler(conn, c, product_id=84).reconcile()
        assert storage.get_order(conn, "c3")["status"] == "filled"

    def test_unknown_with_no_trace_becomes_rejected(self, conn):
        _seed_order(conn, "c4", "unknown")
        r = Reconciler(conn, _FakeClient(), product_id=84).reconcile()
        assert Divergence.ORPHAN_LOCAL in r.divergences
        assert storage.get_order(conn, "c4")["status"] == "rejected"

    def test_orphan_exchange_order_halts(self, conn):
        c = _FakeClient(live={"success": True,
                              "result": [{"id": 99, "client_order_id": "ghost",
                                          "state": "open"}]})
        rec = Reconciler(conn, c, product_id=84)
        r = rec.reconcile()
        assert not r.clean and rec.halted
        assert "orphan exchange order" in rec.halt_reason
        assert "cancel_order" not in c.calls, "must never auto-cancel"

    def test_position_mismatch_halts(self, conn):
        c = _FakeClient(positions={"success": True,
                                   "result": [{"size": 1, "product_id": 84}]})
        rec = Reconciler(conn, c, product_id=84)
        r = rec.reconcile()
        assert not r.clean and rec.halted and "position mismatch" in rec.halt_reason

    def test_failed_exchange_query_halts(self, conn):
        rec = Reconciler(conn, _FakeClient(live={"success": False}), product_id=84)
        r = rec.reconcile()
        assert not r.clean and rec.halted

    def test_unmapped_exchange_state_halts(self, conn):
        _seed_order(conn, "c5", "acked", "5")
        c = _FakeClient(live={"success": True,
                              "result": [{"id": 5, "client_order_id": "c5",
                                          "state": "quantum_superposition"}]})
        rec = Reconciler(conn, c, product_id=84)
        rec.reconcile()
        assert rec.halted

    def test_reconciler_never_places_or_cancels(self, conn):
        _seed_order(conn, "c6", "unknown")
        c = _FakeClient()
        Reconciler(conn, c, product_id=84).reconcile()
        assert "place_order" not in c.calls and "cancel_order" not in c.calls

    def test_health_protocol(self, conn):
        rec = Reconciler(conn, _FakeClient(), product_id=84)
        assert rec.health().name == "reconciler"
        rec.reconcile()
        assert rec.health().name == "reconciler"


# ---------------------------------------------------------------------------
# 5 — Restart / recovery
# ---------------------------------------------------------------------------

class TestRecovery:
    def test_boot_reconcile_recovers_unknown_from_prior_process(self, conn):
        _seed_order(conn, "boot1", "unknown")          # survivor of a crash
        c = _FakeClient(live={"success": True,
                              "result": [{"id": 7, "client_order_id": "boot1",
                                          "state": "open"}]})
        rec = Reconciler(conn, c, product_id=84)
        assert rec.boot_reconcile().clean
        assert storage.get_order(conn, "boot1")["status"] == "acked"

    def test_boot_reconcile_reports_unclean_on_orphan(self, conn):
        c = _FakeClient(live={"success": True,
                              "result": [{"id": 8, "client_order_id": "ghost2",
                                          "state": "open"}]})
        assert not Reconciler(conn, c, product_id=84).boot_reconcile().clean

    def test_open_orders_survive_reconnect(self, conn, tmp_path):
        _seed_order(conn, "persist1", "submitted")
        conn.close()
        c2 = storage.get_connection(tmp_path / "t.db")
        rows = storage.get_open_orders(c2, ("submitted", "acked", "partial", "unknown"))
        assert [r["client_order_id"] for r in rows] == ["persist1"]
        c2.close()

    def test_daily_risk_state_survives_restart(self, conn, tmp_path):
        storage.write_risk_state(conn, utc_day="2026-08-18", realised_pnl=-3.5,
                                 orders_placed=2, halted=0, halt_reason=None,
                                 updated_ts="2026-08-18T09:00:00+00:00")
        conn.close()
        c2 = storage.get_connection(tmp_path / "t.db")
        st = storage.get_risk_state(c2, "2026-08-18")
        assert st["realised_pnl"] == -3.5 and st["orders_placed"] == 2
        # and the restored counters still veto
        d = _risk(daily_realised_pnl=st["realised_pnl"],
                  orders_placed_today=st["orders_placed"]).validate(
            _intent(), _account(), now=NOON)
        assert not d.approved
        c2.close()

    def test_risk_state_defaults_for_new_day(self, conn):
        st = storage.get_risk_state(conn, "2099-01-01")
        assert st["realised_pnl"] == 0.0 and st["orders_placed"] == 0


# ---------------------------------------------------------------------------
# 6 — Strategy bridge: disabled by default
# ---------------------------------------------------------------------------

class TestStrategyBridge:
    def test_disabled_by_default_raises(self):
        with pytest.raises(bridge.StrategyDisabled):
            bridge.build_intent(direction=1, mark_price=MARK, enabled=False)

    def test_enabled_builds_tick_aligned_single_contract_intent(self):
        it = bridge.build_intent(direction=1, mark_price=MARK, enabled=True,
                                 utc_day="2026-08-18")
        assert it.size == 1 and it.symbol == "BTCUSD" and it.product_id == 84
        assert it.order_type == "limit_order" and it.side == "buy"
        assert it.client_order_id == "g3_momentum_tsmom-2026-08-18-buy-1"
        assert _risk().validate(it, _account(), now=NOON).approved

    def test_short_direction(self):
        it = bridge.build_intent(direction=-1, mark_price=MARK, enabled=True,
                                 utc_day="2026-08-18")
        assert it.side == "sell" and it.stop_price > it.limit_price

    def test_invalid_direction_rejected(self):
        with pytest.raises(ValueError):
            bridge.build_intent(direction=0, mark_price=MARK, enabled=True)

    def test_missing_db_returns_none_not_raise(self, tmp_path):
        assert bridge.read_latest_signal(str(tmp_path / "nope.db")) is None
        assert bridge.latest_direction(str(tmp_path / "nope.db")) is None

    def test_config_defaults_are_off(self):
        import config.settings as cfg
        assert cfg.DEMO_TRADING_ENABLED is False
        assert cfg.DEMO_STRATEGY_ENABLED is False
        assert cfg.DEMO_KILL_SWITCH_ENGAGED is True


# ---------------------------------------------------------------------------
# 7 — End-to-end (mocked)
# ---------------------------------------------------------------------------

class TestEndToEnd:
    def test_intent_to_fill_to_reconcile(self, conn):
        c = _FakeClient(place=_ok_place)
        state, d = TestnetExecutor(conn, c, _risk()).submit(_intent(), _account(), now=NOON)
        assert state is OrderState.ACKED and d.approved

        c._fills = {"success": True, "result": [{"client_order_id": "test-2026-08-18-buy-1"}]}
        c._live = {"success": True, "result": []}
        Reconciler(conn, c, product_id=84).reconcile()
        assert storage.get_order(conn, "test-2026-08-18-buy-1")["status"] == "filled"

    def test_blocked_intent_never_reaches_exchange_and_is_journalled(self, conn):
        ex = TestnetExecutor(conn, _NoNetworkClient(), _risk(kill_switch_engaged=True))
        state, d = ex.submit(_intent(), _account(), now=NOON)
        assert state is OrderState.BLOCKED and not d.approved
        assert storage.get_order(conn, "test-2026-08-18-buy-1")["status"] == "blocked"

    def test_cancel_path(self, conn):
        c = _FakeClient(place=_ok_place)
        ex = TestnetExecutor(conn, c, _risk())
        ex.submit(_intent(), _account(), now=NOON)
        assert ex.cancel("test-2026-08-18-buy-1") is OrderState.CANCELLED
        assert storage.get_order(conn, "test-2026-08-18-buy-1")["status"] == "cancelled"

    def test_cancel_failure_is_ambiguous(self, conn):
        c = _FakeClient(place=_ok_place, cancel=requests.exceptions.Timeout("t"))
        ex = TestnetExecutor(conn, c, _risk())
        ex.submit(_intent(), _account(), now=NOON)
        assert ex.cancel("test-2026-08-18-buy-1") is OrderState.UNKNOWN

    def test_cancel_without_delta_id_refuses(self, conn):
        _seed_order(conn, "nodelta", "submitted")
        with pytest.raises(ValueError, match="reconciliation"):
            TestnetExecutor(conn, _FakeClient(), _risk()).cancel("nodelta")
