"""Tests for the testnet demo execution stack (B2/B3/B4/B5/B6).

No test may place a real order. Every exchange interaction is mocked, and a
`_NoNetworkClient` is used wherever the point is that NO call happened at all --
it raises on any method, so a rejection path that touched the network fails
loudly rather than silently passing.

Run:
    conda run -n btc_bot pytest tests/test_demo_execution.py -v
"""
from __future__ import annotations

import ast
import logging
import pathlib
import sqlite3
import textwrap
import threading
from datetime import datetime, timezone

import pytest
import requests

from src.data import storage
from src.execution.order_state import (
    ALLOWED_TRANSITIONS, OPEN_STATES, IllegalTransition, OrderState,
    can_transition, is_open, is_terminal, transition,
)
from src.execution import fills
from src.execution import protection
from src.execution.reconciler import Divergence, Reconciler
from src.execution.reconciliation_jobs import (
    RECONCILE_JOB_ID, _make_periodic_reconcile, register_reconciliation_jobs,
)
from src.execution.testnet_executor import (
    ExecutionBlocked, InstrumentMissing, TestnetExecutor,
)
from src.risk import limits
from src.risk.state import (
    MalformedRiskState, RiskStateStore, record_order_placed, utc_day_of,
)
from src.risk.manager import (
    AccountSnapshot, OrderIntent, RiskManager, affordable_stop_distance,
    compute_trade_risk_usd,
    required_margin_usd, round_trip_fees_usd,
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


class _FakeReconcileClient(_FakeClient):
    """_FakeClient in reconciliation-shaped terms.

    `_o` / `_p` are writable so a test can change what the exchange reports
    between two passes -- which is how "the halt persists even after the
    exchange agrees again" is expressed.
    """

    def __init__(self, orders=None, fills=None, positions=None):
        super().__init__(live=orders, fills=fills, positions=positions)

    @property
    def _o(self):
        return self._live

    @_o.setter
    def _o(self, value):
        self._live = value

    @property
    def _p(self):
        return self._positions

    @_p.setter
    def _p(self, value):
        self._positions = value

    @property
    def _f(self):
        return self._fills

    @_f.setter
    def _f(self, value):
        self._fills = value


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



def _fresh_db(path):
    """A real on-disk DB with the BTCUSD instrument, as production has."""
    c = storage.get_connection(path)
    storage.init_schema(c)
    storage.init_execution_schema(c)
    c.execute(
        "INSERT OR REPLACE INTO instruments "
        "(id,symbol,contract_type,underlying,quoting,tick_size,contract_value,"
        "state,fetched_at) VALUES "
        "(84,'BTCUSD','perpetual_futures','BTC','USD',0.1,0.001,'live','2026-08-19T00:00:00+00:00')"
    )
    c.commit()
    return c

def _account(**kw):
    base = dict(equity_usd=200.0, available_usd=200.0, open_positions=0, mark_price=MARK)
    base.update(kw)
    return AccountSnapshot(**base)


def _intent(**kw):
    base = dict(
        client_order_id="test-2026-08-18-buy-1", symbol="BTCUSD", product_id=84,
        side="buy", size=1, order_type="limit_order",
        limit_price=64187.5, stop_price=63287.5, strategy="test",   # $900 move + $0.064 fees = $0.964, inside the $1.00 budget
        intent_kind="entry",
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
        # 0.0642 raw, scaled by the measured FEE_RESERVE_MULTIPLIER.
        assert round_trip_fees_usd(MARK, 1) == pytest.approx(0.0758, abs=1e-3)

    def test_one_contract_fits_budget_at_reference_mark(self):
        assert compute_trade_risk_usd(_intent(stop_price=63287.5), MARK) <= limits.MAX_TRADE_RISK_USD


class TestMarginArithmetic:
    """Margin must reflect BOTH of Delta's inputs, not just the percentage one.

    `initial_margin: 0.5` (percent) and `default_leverage: 10` are BOTH published
    for BTCUSD, and the exchange reserves whichever is larger. Using only the
    0.5% floor understated the requirement by 20x.
    """

    # A mark high enough that margin, not MIN_FREE_EQUITY_USD, is the binding
    # constraint. At $64k the $10 floor dominates ($6.48 required), so the guard
    # cannot be exercised there.
    HIGH_MARK = 150_000.0
    HIGH_NOTIONAL = 150.0                       # 0.001 * 150_000 * 1

    @property
    def HIGH_FEES(self):
        # Derived, not hardcoded: FEE_RESERVE_MULTIPLIER must be free to change
        # without silently invalidating this boundary test.
        return round_trip_fees_usd(self.HIGH_MARK, 1)

    def _high_mark_case(self, available):
        return (
            _intent(limit_price=self.HIGH_MARK, stop_price=self.HIGH_MARK - 800.0),
            _account(available_usd=available, mark_price=self.HIGH_MARK),
        )

    def test_leverage_requirement_exceeds_initial_margin_floor(self):
        """Case 1: the live BTCUSD spec -- leverage is the binding input."""
        notional = limits.BTCUSD_CONTRACT_VALUE * MARK
        floor = notional * limits.BTCUSD_INITIAL_MARGIN_PCT / 100.0
        leveraged = notional / limits.BTCUSD_DEFAULT_LEVERAGE

        assert leveraged > floor
        assert required_margin_usd(notional) == pytest.approx(leveraged)
        assert required_margin_usd(notional) == pytest.approx(20.0 * floor)
        assert required_margin_usd(notional) == pytest.approx(6.4188, abs=1e-3)

    def test_initial_margin_floor_exceeds_leverage_requirement(self, monkeypatch):
        """Case 2: a spec where the percentage floor binds instead."""
        monkeypatch.setattr(limits, "BTCUSD_INITIAL_MARGIN_PCT", 20.0)
        monkeypatch.setattr(limits, "BTCUSD_DEFAULT_LEVERAGE", 10.0)

        notional = 150.0
        assert required_margin_usd(notional) == pytest.approx(30.0)   # 20% floor
        assert required_margin_usd(notional) > notional / 10.0        # not leverage

    def test_fees_are_added_on_top_of_margin(self):
        """Case 3: available covering margin but not margin+fees is rejected."""
        margin_only = self.HIGH_NOTIONAL / limits.BTCUSD_DEFAULT_LEVERAGE
        assert margin_only == pytest.approx(15.0)

        intent, account = self._high_mark_case(margin_only + 0.05)
        d = _risk().validate(intent, account, now=NOON)
        assert not d.approved
        assert "margin + fees" in d.reason

        intent, account = self._high_mark_case(margin_only + self.HIGH_FEES + 0.01)
        assert _risk().validate(intent, account, now=NOON).approved

    def test_insufficient_available_equity_is_rejected(self):
        """Case 4: above the free-equity floor, still short of true margin."""
        intent, account = self._high_mark_case(12.0)
        assert account.available_usd > limits.MIN_FREE_EQUITY_USD

        d = _risk().validate(intent, account, now=NOON)
        assert not d.approved
        assert "below required" in d.reason

    def test_existing_rejection_behaviour_unchanged(self):
        """Case 5: the fix tightens one guard and disturbs nothing else."""
        # The live-spec baseline still approves: $200 covers $6.48 comfortably.
        assert _risk().validate(_intent(), _account(), now=NOON).approved

        # The free-equity floor still fires first, with its own message.
        d = _risk().validate(_intent(), _account(available_usd=5.0), now=NOON)
        assert not d.approved
        assert "below floor" in d.reason

        # Earlier guards still short-circuit ahead of the margin check.
        assert not _risk(kill_switch_engaged=True).validate(
            _intent(), _account(available_usd=0.0), now=NOON).approved
        assert not _risk().validate(
            _intent(size=2), _account(), now=NOON).approved


class TestObservedExchangeBehaviour:
    """Pinned to the real supervised order of 2026-08-18.

    Exchange order 2169335031: 1 BTCUSD contract, buy limit @ 63051.2, never
    filled, cancelled ~33s later. Delta reported, while it rested:

        order_margin    = 6.30512
        blocked_margin  = 6.379520416

    These are measurements, not predictions. If a future Delta change breaks
    them, that is a real finding and this test is where it should surface.
    """

    ORDER_ID = 2169335031
    LIMIT_PRICE = 63051.2
    OBSERVED_ORDER_MARGIN = 6.30512
    OBSERVED_BLOCKED_TOTAL = 6.379520416
    OBSERVED_FEE_RESERVE = 6.379520416 - 6.30512

    def test_margin_is_computed_on_the_order_price_not_the_mark(self):
        """The exchange charged notional-at-LIMIT / leverage, to the cent."""
        notional = limits.BTCUSD_CONTRACT_VALUE * self.LIMIT_PRICE
        assert required_margin_usd(notional) == pytest.approx(
            self.OBSERVED_ORDER_MARGIN, abs=1e-9)

    def test_leverage_leg_bound_not_the_initial_margin_floor(self):
        """Confirms F2 against a real exchange reservation, not just the spec."""
        notional = limits.BTCUSD_CONTRACT_VALUE * self.LIMIT_PRICE
        floor = notional * limits.BTCUSD_INITIAL_MARGIN_PCT / 100.0
        assert self.OBSERVED_ORDER_MARGIN == pytest.approx(
            notional / limits.BTCUSD_DEFAULT_LEVERAGE, abs=1e-9)
        assert self.OBSERVED_ORDER_MARGIN > floor
        assert self.OBSERVED_ORDER_MARGIN / floor == pytest.approx(20.0)

    def test_fee_reserve_matches_our_model_at_the_order_price(self):
        """The whole point of FEE_RESERVE_MULTIPLIER."""
        assert round_trip_fees_usd(self.LIMIT_PRICE, 1) == pytest.approx(
            self.OBSERVED_FEE_RESERVE, abs=1e-9)

    def test_fee_reserve_is_exactly_1_18x_the_raw_commission(self):
        """Documents the measured ratio itself, independent of our constant."""
        raw = limits.BTCUSD_CONTRACT_VALUE * self.LIMIT_PRICE * limits.TAKER_FEE_RATE * 2
        assert self.OBSERVED_FEE_RESERVE / raw == pytest.approx(1.18, abs=1e-9)
        assert limits.FEE_RESERVE_MULTIPLIER == 1.18

    def test_total_blocked_is_margin_plus_fee_reserve(self):
        notional = limits.BTCUSD_CONTRACT_VALUE * self.LIMIT_PRICE
        modelled = required_margin_usd(notional) + round_trip_fees_usd(self.LIMIT_PRICE, 1)
        assert modelled == pytest.approx(self.OBSERVED_BLOCKED_TOTAL, abs=1e-9)

    def test_uncorrected_model_would_have_understated_the_reserve(self):
        """Guards against silently reverting the multiplier."""
        uncorrected = (limits.BTCUSD_CONTRACT_VALUE * self.LIMIT_PRICE
                       * limits.TAKER_FEE_RATE * 2)
        assert uncorrected < self.OBSERVED_FEE_RESERVE
        assert self.OBSERVED_FEE_RESERVE / uncorrected > 1.15

    def test_corrected_fees_still_leave_one_contract_inside_budget(self):
        """The correction must not make the intended trade size unviable."""
        it = _intent(stop_price=63287.5)
        assert compute_trade_risk_usd(it, MARK) <= limits.MAX_TRADE_RISK_USD

    def test_correction_is_strictly_conservative(self):
        """A multiplier > 1 can only ever tighten, never loosen, a guard."""
        assert limits.FEE_RESERVE_MULTIPLIER >= 1.0


class _FakeScheduler:
    """Mimics SchedulerService's duplicate-job contract exactly."""

    def __init__(self):
        self.jobs = {}

    def has_job(self, job_id):
        return job_id in self.jobs

    def register(self, name, fn, trigger, job_id=None, **kw):
        jid = job_id or name
        if jid in self.jobs:
            raise ValueError(f"job '{jid}' is already registered")
        self.jobs[jid] = (name, fn, trigger, kw)


class TestReconciliationLifecycle:
    """A-E: the reconciler as a real safety control, not an advisory."""

    def _rec(self, conn, client=None):
        return Reconciler(conn, client or _FakeReconcileClient(), product_id=84)

    def test_clean_boot_permits_execution(self, conn):
        r = self._rec(conn)
        r.start()
        assert r.running and r.booted
        assert not r.halted
        assert r.trading_permitted
        assert r.gate_state() == (False, "")

    def test_before_start_trading_is_not_permitted(self, conn):
        r = self._rec(conn)
        assert not r.trading_permitted
        blocked, reason = r.gate_state()
        assert blocked and "not running" in reason

    def test_failed_boot_reconciliation_prevents_trading(self, conn):
        client = _FakeReconcileClient(orders={"success": True, "result": [
            {"client_order_id": "ghost", "id": 42, "state": "open"}]})
        r = self._rec(conn, client)
        r.start()
        assert r.booted
        assert r.halted
        assert not r.trading_permitted
        blocked, reason = r.gate_state()
        assert blocked and "halted" in reason

    def test_boot_that_raises_halts_rather_than_propagates(self, conn):
        class Exploding:
            def get_live_orders(self, pid):
                raise ConnectionError("exchange unreachable")

            def get_fills(self, pid):
                raise ConnectionError("exchange unreachable")

            def get_positions(self):
                raise ConnectionError("exchange unreachable")

        r = self._rec(conn, Exploding())
        r.start()
        assert r.halted
        assert not r.trading_permitted
        assert "ConnectionError" in r.halt_reason

    def test_stop_does_not_clear_halt(self, conn):
        client = _FakeReconcileClient(positions={"success": True, "result": [{"size": 5}]})
        r = self._rec(conn, client)
        r.start()
        assert r.halted
        r.stop()
        assert r.halted, "stop() must not clear a halt"
        assert not r.trading_permitted

    def test_restart_cannot_clear_a_halt(self, conn):
        """The supervisor's restart policy must not become a halt bypass."""
        client = _FakeReconcileClient(positions={"success": True, "result": [{"size": 5}]})
        r = self._rec(conn, client)
        r.start()
        assert r.halted
        client._p = {"success": True, "result": []}
        r.stop()
        r.start()
        assert r.halted, "restart must not clear a halt"
        assert not r.trading_permitted

    def test_stopped_reconciler_blocks_even_when_not_halted(self, conn):
        r = self._rec(conn)
        r.start()
        assert r.trading_permitted
        r.stop()
        assert not r.trading_permitted
        blocked, reason = r.gate_state()
        assert blocked and "not running" in reason

    def test_halt_persists_across_repeated_periodic_passes(self, conn):
        client = _FakeReconcileClient(positions={"success": True, "result": [{"size": 5}]})
        r = self._rec(conn, client)
        r.start()
        assert r.halted
        client._p = {"success": True, "result": []}
        job = _make_periodic_reconcile(r)
        for _ in range(5):
            job()
        assert r.halted, "time passing must never clear a halt"

    def test_halt_cleared_only_by_explicit_clean_reconciliation(self, conn):
        client = _FakeReconcileClient(positions={"success": True, "result": [{"size": 5}]})
        r = self._rec(conn, client)
        r.start()
        assert r.halted

        assert r.clear_halt_if_clean() is False, "still divergent -- must not clear"
        assert r.halted

        client._p = {"success": True, "result": []}
        assert r.clear_halt_if_clean() is True
        assert not r.halted
        assert r.trading_permitted

    def test_reconciliation_never_places_or_cancels(self, conn):
        class Watching(_FakeReconcileClient):
            def place_order(self, **kw):
                raise AssertionError("reconciler placed an order")

            def cancel_order(self, *a, **k):
                raise AssertionError("reconciler cancelled an order")

        client = Watching(orders={"success": True, "result": [
            {"client_order_id": "ghost", "id": 7, "state": "open"}]})
        r = self._rec(conn, client)
        r.start()
        _make_periodic_reconcile(r)()
        assert r.halted
        assert client.calls, "it did query"


class TestScheduledReconciliation:
    """B: periodic execution, and the duplicate-job property that motivates it."""

    def test_registration_creates_the_job(self, conn):
        sched = _FakeScheduler()
        r = Reconciler(conn, _FakeReconcileClient(), product_id=84)
        assert register_reconciliation_jobs(sched, r) is True
        assert RECONCILE_JOB_ID in sched.jobs
        name, fn, trigger, kw = sched.jobs[RECONCILE_JOB_ID]
        assert trigger == "interval" and kw["seconds"] == 60

    def test_registration_is_idempotent(self, conn):
        sched = _FakeScheduler()
        r = Reconciler(conn, _FakeReconcileClient(), product_id=84)
        assert register_reconciliation_jobs(sched, r) is True
        assert register_reconciliation_jobs(sched, r) is False
        assert len(sched.jobs) == 1

    def test_component_restart_produces_no_duplicate_jobs(self, conn):
        """The reason job ownership sits outside the component."""
        sched = _FakeScheduler()
        r = Reconciler(conn, _FakeReconcileClient(), product_id=84)
        register_reconciliation_jobs(sched, r)
        for _ in range(3):
            r.stop()
            r.start()
        assert len(sched.jobs) == 1

    def test_scheduled_job_actually_reconciles(self, conn):
        sched = _FakeScheduler()
        client = _FakeReconcileClient()
        r = Reconciler(conn, client, product_id=84)
        r.start()
        register_reconciliation_jobs(sched, r)
        before = len(client.calls)
        sched.jobs[RECONCILE_JOB_ID][1]()
        assert len(client.calls) > before
        assert r._last_run is not None

    def test_scheduled_job_detects_divergence_and_halts(self, conn):
        sched = _FakeScheduler()
        client = _FakeReconcileClient()
        r = Reconciler(conn, client, product_id=84)
        r.start()
        register_reconciliation_jobs(sched, r)
        assert not r.halted
        client._p = {"success": True, "result": [{"size": 2}]}
        sched.jobs[RECONCILE_JOB_ID][1]()
        assert r.halted
        assert not r.trading_permitted

    def test_scheduled_job_swallows_exceptions(self, conn):
        class Exploding(_FakeReconcileClient):
            def get_live_orders(self, pid):
                raise RuntimeError("boom")

        r = Reconciler(conn, Exploding(), product_id=84)
        _make_periodic_reconcile(r)()


class TestHaltPropagation:
    """D: a reconciler HALT must block order submission."""

    def test_halt_provider_blocks_a_perfect_intent(self, conn):
        client = _FakeReconcileClient(positions={"success": True, "result": [{"size": 3}]})
        r = Reconciler(conn, client, product_id=84)
        r.start()
        assert r.halted

        rm = _risk(halt_provider=r.gate_state)
        d = rm.validate(_intent(), _account(), now=NOON)
        assert not d.approved
        assert "halted" in d.reason and "position mismatch" in d.reason

    def test_clean_reconciler_permits_the_same_intent(self, conn):
        r = Reconciler(conn, _FakeReconcileClient(), product_id=84)
        r.start()
        rm = _risk(halt_provider=r.gate_state)
        assert rm.validate(_intent(), _account(), now=NOON).approved

    def test_executor_blocks_and_never_reaches_the_exchange_when_halted(self, conn):
        client = _FakeReconcileClient(positions={"success": True, "result": [{"size": 3}]})
        r = Reconciler(conn, client, product_id=84)
        r.start()
        ex = TestnetExecutor(conn, _NoNetworkClient(), _risk(halt_provider=r.gate_state))
        state, decision = ex.submit(_intent(), _account(), now=NOON)
        assert state is OrderState.BLOCKED
        assert "halted" in decision.reason

    def test_halt_takes_effect_between_two_validate_calls(self, conn):
        """Live source, not a value frozen at construction."""
        client = _FakeReconcileClient()
        r = Reconciler(conn, client, product_id=84)
        r.start()
        rm = _risk(halt_provider=r.gate_state)
        assert rm.validate(_intent(), _account(), now=NOON).approved

        client._p = {"success": True, "result": [{"size": 1}]}
        r.reconcile()
        assert not rm.validate(_intent(), _account(), now=NOON).approved

    def test_unbooted_reconciler_blocks_trading(self, conn):
        r = Reconciler(conn, _FakeReconcileClient(), product_id=84)
        rm = _risk(halt_provider=r.gate_state)
        d = rm.validate(_intent(), _account(), now=NOON)
        assert not d.approved and "reconciler not running" in d.reason

    def test_kill_switch_still_outranks_the_halt_provider(self, conn):
        r = Reconciler(conn, _FakeReconcileClient(), product_id=84)
        r.start()
        rm = _risk(kill_switch_engaged=True, halt_provider=r.gate_state)
        d = rm.validate(_intent(), _account(), now=NOON)
        assert not d.approved and "kill switch" in d.reason

    def test_no_provider_preserves_existing_behaviour(self):
        assert _risk().validate(_intent(), _account(), now=NOON).approved


class TestDailyOrderCap:
    """1 - MAX_ORDERS_PER_DAY, counted from persisted state."""

    def _store(self, conn):
        return RiskStateStore(conn)

    def _rm(self, store, **kw):
        # Bound to NOON, matching the `now` every submit in this class uses.
        # Reading counters at wall-clock time while writing them at a fixed
        # test timestamp makes these tests silently date-dependent: they pass
        # on the day NOON falls on and fail every other day.
        return _risk(counters_provider=lambda: store.counters(NOON), **kw)

    def test_first_and_second_order_increment_the_counter(self, conn):
        store = self._store(conn)
        assert store.state().orders_placed == 0
        assert store.record_order_placed("coid-1").orders_placed == 1
        assert store.record_order_placed("coid-2").orders_placed == 2

    def test_third_order_is_rejected(self, conn):
        store = self._store(conn)
        rm = self._rm(store)
        assert rm.validate(_intent(), _account(), now=NOON).approved

        store.record_order_placed("coid-1", now=NOON)
        assert rm.validate(_intent(), _account(), now=NOON).approved

        store.record_order_placed("coid-2", now=NOON)
        d = rm.validate(_intent(), _account(), now=NOON)
        assert not d.approved
        assert "orders today" in d.reason and "max 2" in d.reason

    def test_rejected_order_does_not_consume_a_slot(self, conn):
        """The cap must count sent orders, never refused intents."""
        store = self._store(conn)
        rm = self._rm(store)
        ex = TestnetExecutor(conn, _NoNetworkClient(), rm, risk_state=store)

        for i in range(5):
            state, decision = ex.submit(
                _intent(client_order_id=f"bad-{i}", size=9), _account(), now=NOON)
            assert state is OrderState.BLOCKED
        assert store.state(NOON).orders_placed == 0, "blocked intents consumed slots"

    def test_submitted_order_consumes_exactly_one_slot(self, conn):
        store = self._store(conn)
        client = _FakeClient(place={"success": True, "result": {"id": 1}})
        ex = TestnetExecutor(conn, client, self._rm(store), risk_state=store)
        ex.submit(_intent(client_order_id="real-1"), _account(), now=NOON)
        assert store.state(NOON).orders_placed == 1

    def test_slot_is_consumed_even_when_the_submit_is_ambiguous(self, conn):
        """An UNKNOWN order may exist on the exchange; it must count."""
        store = self._store(conn)
        client = _FakeClient(place=requests.exceptions.Timeout("t"))
        ex = TestnetExecutor(conn, client, self._rm(store), risk_state=store)
        state, _ = ex.submit(_intent(client_order_id="ambiguous-1"), _account(), now=NOON)
        assert state is OrderState.UNKNOWN
        assert store.state(NOON).orders_placed == 1

    def test_cap_binds_through_the_executor(self, conn):
        store = self._store(conn)
        client = _FakeClient(place={"success": True, "result": {"id": 1}})
        ex = TestnetExecutor(conn, client, self._rm(store), risk_state=store)
        for i in (1, 2):
            state, _ = ex.submit(_intent(client_order_id=f"ok-{i}"), _account(), now=NOON)
            assert state is OrderState.ACKED
        calls_before = len(client.calls)
        state, decision = ex.submit(_intent(client_order_id="ok-3"), _account(), now=NOON)
        assert state is OrderState.BLOCKED
        assert "orders today" in decision.reason
        assert len(client.calls) == calls_before, "third order reached the exchange"

    def test_counter_persists_across_process_restart(self, tmp_path):
        """A new connection is what a restarted process actually has."""
        db = tmp_path / "persist.db"
        c1 = storage.get_connection(db)
        storage.init_schema(c1)
        storage.init_execution_schema(c1)
        RiskStateStore(c1).record_order_placed("coid-1")
        RiskStateStore(c1).record_order_placed("coid-2")
        c1.close()

        c2 = storage.get_connection(db)
        store = RiskStateStore(c2)
        assert store.state().orders_placed == 2
        d = _risk(counters_provider=store.counters).validate(
            _intent(), _account(), now=NOON)
        assert not d.approved and "orders today" in d.reason
        c2.close()


class TestDailyLossCap:
    """2 - MAX_DAILY_LOSS_USD, from deterministic realised-loss fixtures."""

    def test_below_the_limit_remains_permitted(self, conn):
        store = RiskStateStore(conn)
        store.record_realised_pnl("t1", -3.99)
        rm = _risk(counters_provider=store.counters)
        assert rm.validate(_intent(), _account(), now=NOON).approved

    def test_reaching_the_limit_blocks(self, conn):
        store = RiskStateStore(conn)
        store.record_realised_pnl("t1", -4.00)
        d = _risk(counters_provider=store.counters).validate(
            _intent(), _account(), now=NOON)
        assert not d.approved
        assert "daily loss" in d.reason and "cap" in d.reason

    def test_exceeding_the_limit_blocks(self, conn):
        store = RiskStateStore(conn)
        store.record_realised_pnl("t1", -4.01)
        assert not _risk(counters_provider=store.counters).validate(
            _intent(), _account(), now=NOON).approved

    def test_losses_accumulate_across_several_trades(self, conn):
        store = RiskStateStore(conn)
        rm = _risk(counters_provider=store.counters)
        for i, loss in enumerate([-1.50, -1.50]):
            store.record_realised_pnl(f"t{i}", loss)
            assert rm.validate(_intent(), _account(), now=NOON).approved
        store.record_realised_pnl("t2", -1.20)
        assert store.state().realised_pnl == pytest.approx(-4.20)
        assert not rm.validate(_intent(), _account(), now=NOON).approved

    def test_profit_offsets_loss(self, conn):
        store = RiskStateStore(conn)
        store.record_realised_pnl("loss", -4.50)
        assert not _risk(counters_provider=store.counters).validate(
            _intent(), _account(), now=NOON).approved
        store.record_realised_pnl("gain", +1.00)
        assert store.state().realised_pnl == pytest.approx(-3.50)
        assert _risk(counters_provider=store.counters).validate(
            _intent(), _account(), now=NOON).approved

    def test_loss_persists_across_restart(self, tmp_path):
        db = tmp_path / "loss.db"
        c1 = storage.get_connection(db)
        storage.init_schema(c1)
        storage.init_execution_schema(c1)
        RiskStateStore(c1).record_realised_pnl("t1", -4.25)
        c1.close()

        c2 = storage.get_connection(db)
        store = RiskStateStore(c2)
        assert store.state().realised_pnl == pytest.approx(-4.25)
        assert not _risk(counters_provider=store.counters).validate(
            _intent(), _account(), now=NOON).approved
        c2.close()

    def test_cap_checks_require_no_exchange_call(self, conn):
        store = RiskStateStore(conn)
        store.record_realised_pnl("t1", -5.00)
        ex = TestnetExecutor(conn, _NoNetworkClient(),
                             _risk(counters_provider=store.counters), risk_state=store)
        state, decision = ex.submit(_intent(), _account(), now=NOON)
        assert state is OrderState.BLOCKED
        assert "daily loss" in decision.reason


class TestUtcRollover:
    """3 - yesterday's counters must not leak into today."""

    DAY1 = datetime(2026, 8, 18, 23, 59, 0, tzinfo=timezone.utc)
    DAY2 = datetime(2026, 8, 19, 0, 0, 30, tzinfo=timezone.utc)

    def test_new_utc_day_starts_clean(self, conn):
        store = RiskStateStore(conn)
        store.record_order_placed("d1-a", now=self.DAY1)
        store.record_order_placed("d1-b", now=self.DAY1)
        store.record_realised_pnl("d1-loss", -4.50, now=self.DAY1)
        assert store.state(self.DAY1).orders_placed == 2
        assert store.state(self.DAY1).realised_pnl == pytest.approx(-4.50)

        day2 = store.state(self.DAY2)
        assert day2.utc_day == "2026-08-19"
        assert day2.orders_placed == 0
        assert day2.realised_pnl == 0.0

    def test_caps_release_at_the_boundary(self, conn):
        store = RiskStateStore(conn)
        store.record_order_placed("d1-a", now=self.DAY1)
        store.record_order_placed("d1-b", now=self.DAY1)

        blocked = _risk(counters_provider=lambda: store.counters(self.DAY1))
        assert not blocked.validate(_intent(), _account(), now=self.DAY1).approved

        fresh = _risk(counters_provider=lambda: store.counters(self.DAY2))
        assert fresh.validate(_intent(), _account(), now=self.DAY2).approved

    def test_yesterdays_state_is_still_readable(self, conn):
        """Rollover must not destroy the audit trail."""
        store = RiskStateStore(conn)
        store.record_order_placed("d1-a", now=self.DAY1)
        store.record_order_placed("d2-a", now=self.DAY2)
        assert store.state(self.DAY1).orders_placed == 1
        assert store.state(self.DAY2).orders_placed == 1
        days = [r[0] for r in conn.execute(
            "SELECT DISTINCT utc_day FROM risk_day_events ORDER BY utc_day")]
        assert days == ["2026-08-18", "2026-08-19"]

    def test_day_boundary_is_exact(self):
        assert utc_day_of(datetime(2026, 8, 18, 23, 59, 59, 999999,
                                   tzinfo=timezone.utc)) == "2026-08-18"
        assert utc_day_of(datetime(2026, 8, 19, 0, 0, 0,
                                   tzinfo=timezone.utc)) == "2026-08-19"

    def test_naive_datetime_is_treated_as_utc(self):
        assert utc_day_of(datetime(2026, 8, 19, 0, 0, 0)) == "2026-08-19"

    def test_non_utc_timezone_is_converted_not_truncated(self):
        from datetime import timedelta
        ist = timezone(timedelta(hours=5, minutes=30))
        # 2026-08-19 04:00 IST == 2026-08-18 22:30 UTC
        assert utc_day_of(datetime(2026, 8, 19, 4, 0, tzinfo=ist)) == "2026-08-18"


class TestRiskStatePersistenceSafety:
    """4 - idempotency, corruption, concurrency, transaction boundaries."""

    def test_duplicate_order_record_is_idempotent(self, conn):
        store = RiskStateStore(conn)
        for _ in range(10):
            store.record_order_placed("same-coid")
        assert store.state().orders_placed == 1

    def test_duplicate_pnl_record_is_idempotent(self, conn):
        store = RiskStateStore(conn)
        for _ in range(10):
            store.record_realised_pnl("same-ref", -1.25)
        assert store.state().realised_pnl == pytest.approx(-1.25)

    def test_mirror_always_agrees_with_the_event_ledger(self, conn):
        store = RiskStateStore(conn)
        store.record_order_placed("a")
        store.record_realised_pnl("p", -2.5)
        row = conn.execute("SELECT realised_pnl, orders_placed FROM risk_state "
                           "WHERE utc_day = ?", (utc_day_of(),)).fetchone()
        assert int(row[1]) == 1
        assert float(row[0]) == pytest.approx(-2.5)

    def test_tampered_mirror_fails_closed(self, conn):
        """A row that disagrees with the ledger must stop trading, not guess."""
        store = RiskStateStore(conn)
        store.record_order_placed("a")
        store.record_order_placed("b")
        conn.execute("UPDATE risk_state SET orders_placed = 0 WHERE utc_day = ?",
                     (utc_day_of(),))
        conn.commit()

        with pytest.raises(MalformedRiskState):
            store.state()

        d = _risk(counters_provider=store.counters).validate(
            _intent(), _account(), now=NOON)
        assert not d.approved
        assert "unreadable" in d.reason

    def test_negative_order_count_fails_closed(self, conn):
        store = RiskStateStore(conn)
        store.record_order_placed("a")
        conn.execute("UPDATE risk_state SET orders_placed = -5 WHERE utc_day = ?",
                     (utc_day_of(),))
        conn.commit()
        with pytest.raises(MalformedRiskState):
            store.state()

    def test_non_numeric_counter_fails_closed(self, conn):
        store = RiskStateStore(conn)
        store.record_order_placed("a")
        conn.execute("UPDATE risk_state SET realised_pnl = 'corrupt' WHERE utc_day = ?",
                     (utc_day_of(),))
        conn.commit()
        with pytest.raises(MalformedRiskState):
            store.state()

    def test_missing_row_is_a_new_day_not_a_corruption(self, conn):
        s = RiskStateStore(conn).state()
        assert s.orders_placed == 0 and s.realised_pnl == 0.0

    def test_unreadable_state_blocks_before_any_network_call(self, conn):
        def exploding():
            raise MalformedRiskState("simulated corruption")

        ex = TestnetExecutor(conn, _NoNetworkClient(),
                             _risk(counters_provider=exploding))
        state, decision = ex.submit(_intent(), _account(), now=NOON)
        assert state is OrderState.BLOCKED
        assert "unreadable" in decision.reason

    def test_concurrent_writers_do_not_lose_events(self, tmp_path):
        """Real threads, real connections, real SQLite locking."""
        import threading
        db = tmp_path / "concurrent.db"
        c0 = storage.get_connection(db)
        storage.init_schema(c0)
        storage.init_execution_schema(c0)
        c0.close()

        errors = []

        def writer(n):
            try:
                c = storage.get_connection(db)
                c.execute("PRAGMA busy_timeout = 10000")
                for i in range(10):
                    record_order_placed(c, f"w{n}-{i}")
                c.close()
            except Exception as exc:                       # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(n,)) for n in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, errors
        c = storage.get_connection(db)
        assert RiskStateStore(c).state().orders_placed == 50, "increments were lost"
        c.close()

    def test_failed_write_leaves_no_partial_state(self, conn):
        """Transaction boundary: mirror failure must roll back the event too."""
        store = RiskStateStore(conn)
        store.record_order_placed("before")
        assert store.state().orders_placed == 1

        conn.execute("DROP TABLE risk_state")
        conn.commit()
        with pytest.raises(Exception):
            store.record_order_placed("during-failure")

        rows = conn.execute(
            "SELECT COUNT(*) FROM risk_day_events WHERE ref = 'during-failure'"
        ).fetchone()[0]
        assert rows == 0, "event survived a failed transaction"


class TestCapsAndReconciliationInteraction:
    """5 - two independent controls that must not weaken each other."""

    def test_reconciler_halt_overrides_an_otherwise_permitted_intent(self, conn):
        store = RiskStateStore(conn)
        client = _FakeReconcileClient(positions={"success": True, "result": [{"size": 3}]})
        r = Reconciler(conn, client, product_id=84)
        r.start()
        assert r.halted

        rm = _risk(halt_provider=r.gate_state, counters_provider=store.counters)
        d = rm.validate(_intent(), _account(), now=NOON)
        assert not d.approved
        assert "halted" in d.reason, "halt must outrank an unused daily budget"

    def test_clearing_a_reconciler_halt_does_not_reset_counters(self, conn):
        store = RiskStateStore(conn)
        store.record_order_placed("a")
        store.record_order_placed("b")

        client = _FakeReconcileClient(positions={"success": True, "result": [{"size": 3}]})
        r = Reconciler(conn, client, product_id=84)
        r.start()
        assert r.halted

        client._p = {"success": True, "result": []}
        assert r.clear_halt_if_clean() is True
        assert not r.halted

        assert store.state().orders_placed == 2
        d = _risk(halt_provider=r.gate_state,
                  counters_provider=store.counters).validate(
            _intent(), _account(), now=NOON)
        assert not d.approved
        assert "orders today" in d.reason

    def test_daily_cap_cannot_be_cleared_by_clean_reconciliation(self, conn):
        store = RiskStateStore(conn)
        store.record_realised_pnl("big-loss", -5.00)

        r = Reconciler(conn, _FakeReconcileClient(), product_id=84)
        r.start()
        assert not r.halted and r.trading_permitted

        for _ in range(3):
            r.reconcile()
            r.clear_halt_if_clean()

        d = _risk(halt_provider=r.gate_state,
                  counters_provider=store.counters).validate(
            _intent(), _account(), now=NOON)
        assert not d.approved
        assert "daily loss" in d.reason

    def test_both_controls_clear_means_permitted(self, conn):
        store = RiskStateStore(conn)
        r = Reconciler(conn, _FakeReconcileClient(), product_id=84)
        r.start()
        rm = _risk(halt_provider=r.gate_state, counters_provider=store.counters)
        assert rm.validate(_intent(), _account(), now=NOON).approved


def _fill(fid, side, size=1, price=64000.0, fee=0.032, coid="entry-1", oid="900"):
    return {"id": fid, "client_order_id": coid, "order_id": oid, "product_id": 84,
            "side": side, "size": size, "price": price, "commission": fee,
            "created_at": "2026-08-19T04:00:00Z"}


class TestFillParsing:
    """1 - deterministic ingestion, and refusal of anything unreadable."""

    def test_parses_a_well_formed_fill(self):
        f = fills.parse_fill(_fill("f1", "buy"), default_instrument_id=84)
        assert (f.fill_id, f.side, f.size, f.price) == ("f1", "buy", 1.0, 64000.0)
        assert f.fee == 0.032 and f.instrument_id == 84

    def test_alternate_field_spellings_accepted(self):
        f = fills.parse_fill(
            {"fill_id": "f2", "side": "sell", "filled_size": 2,
             "fill_price": 100.0, "fee": 0.5, "product_id": 84},
            default_instrument_id=84)
        assert (f.fill_id, f.size, f.price, f.fee) == ("f2", 2.0, 100.0, 0.5)

    @pytest.mark.parametrize("payload,why", [
        ({"side": "buy", "size": 1, "price": 1.0}, "no id"),
        ({"id": "x", "side": "long", "size": 1, "price": 1.0}, "bad side"),
        ({"id": "x", "side": "buy", "size": 0, "price": 1.0}, "zero size"),
        ({"id": "x", "side": "buy", "size": -1, "price": 1.0}, "negative size"),
        ({"id": "x", "side": "buy", "size": 1, "price": 0}, "zero price"),
        ({"id": "x", "side": "buy", "size": "abc", "price": 1.0}, "non-numeric"),
        ({"id": "x", "side": "buy", "price": 1.0}, "missing size"),
    ])
    def test_malformed_fills_are_refused_not_defaulted(self, payload, why):
        with pytest.raises(fills.MalformedFill):
            fills.parse_fill(payload, default_instrument_id=84)

    def test_non_dict_payload_refused(self):
        with pytest.raises(fills.MalformedFill):
            fills.parse_fill("<html>", default_instrument_id=84)


class TestFillLifecycle:
    """1-4 - fill -> position -> trade -> realised P&L."""

    def test_entry_fill_creates_position_and_trade(self, conn):
        # delta_order_id matches _fill()'s order_id: an order that produced a
        # fill always carries the exchange id, and that is now the linkage key.
        storage.write_order(conn, client_order_id="entry-1", instrument_id=84,
                            side="buy", order_type="limit_order", quantity=1.0,
                            price=64000.0, status="acked", strategy="t",
                            placed_ts="2026-08-19T04:00:00+00:00",
                            delta_order_id="900")
        conn.commit()
        r = fills.ingest_fills(conn, [_fill("f1", "buy", price=64000.0)])
        assert r.ingested == 1 and r.position_after == 1
        pos = fills.open_position(conn)
        assert pos["side"] == "buy" and pos["quantity"] == 1 and pos["status"] == "open"
        assert pos["entry_price"] == pytest.approx(64000.0)
        assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 1
        assert conn.execute(
            "SELECT role FROM order_fills WHERE fill_id='f1'").fetchone()[0] == "entry"

    def test_an_ack_alone_never_creates_a_position(self, conn):
        """A resting order is not exposure."""
        client = _FakeClient(place={"success": True, "result": {"id": 1}})
        ex = TestnetExecutor(conn, client, _risk())
        ex.submit(_intent(), _account(), now=NOON)
        assert fills.position_size(conn) == 0
        assert fills.open_position(conn) is None

    def test_fill_for_an_unknown_order_records_no_trade_but_still_counts(self, conn):
        r = fills.ingest_fills(conn, [_fill("f1", "buy", coid="never-seen")])
        assert r.ingested == 1 and fills.position_size(conn) == 1
        assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0

    def test_duplicate_fill_is_ignored(self, conn):
        # delta_order_id matches _fill()'s order_id: an order that produced a
        # fill always carries the exchange id, and that is now the linkage key.
        storage.write_order(conn, client_order_id="entry-1", instrument_id=84,
                            side="buy", order_type="limit_order", quantity=1.0,
                            price=64000.0, status="acked", strategy="t",
                            placed_ts="2026-08-19T04:00:00+00:00",
                            delta_order_id="900")
        conn.commit()
        fills.ingest_fills(conn, [_fill("f1", "buy")])
        r = fills.ingest_fills(conn, [_fill("f1", "buy")])
        assert r.ingested == 0 and r.skipped_duplicate == 1
        assert fills.position_size(conn) == 1
        assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 1

    def test_reingesting_the_whole_history_is_a_noop(self, conn):
        batch = [_fill("f1", "buy"), _fill("f2", "sell", price=64500.0)]
        first = fills.ingest_fills(conn, batch)
        again = fills.ingest_fills(conn, batch)
        assert first.ingested == 2 and again.ingested == 0
        assert again.skipped_duplicate == 2
        assert fills.position_size(conn) == 0

    def test_partial_fills_accumulate(self, conn):
        fills.ingest_fills(conn, [_fill("p1", "buy", size=1, price=64000.0)])
        fills.ingest_fills(conn, [_fill("p2", "buy", size=1, price=64200.0)])
        assert fills.position_size(conn) == 2
        pos = fills.open_position(conn)
        assert pos["quantity"] == 2
        assert pos["entry_price"] == pytest.approx(64100.0)   # weighted average

    def test_partial_exit_reduces_without_closing(self, conn):
        fills.ingest_fills(conn, [_fill("e1", "buy", size=2, price=64000.0)])
        fills.ingest_fills(conn, [_fill("x1", "sell", size=1, price=64500.0)])
        assert fills.position_size(conn) == 1
        pos = fills.open_position(conn)
        assert pos is not None and pos["status"] == "open" and pos["quantity"] == 1

    def test_full_exit_closes_the_position(self, conn):
        fills.ingest_fills(conn, [_fill("e1", "buy", size=1, price=64000.0)])
        fills.ingest_fills(conn, [_fill("x1", "sell", size=1, price=64500.0)])
        assert fills.position_size(conn) == 0
        assert fills.open_position(conn) is None
        closed = conn.execute(
            "SELECT status, exit_price, pnl FROM positions ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert closed[0] == "closed"
        assert closed[1] == pytest.approx(64500.0)

    def test_exit_role_is_classified_from_exposure_not_side(self, conn):
        """A sell is an ENTRY when flat and an EXIT when long."""
        fills.ingest_fills(conn, [_fill("s1", "sell", size=1, price=64000.0)])
        assert conn.execute(
            "SELECT role FROM order_fills WHERE fill_id='s1'").fetchone()[0] == "entry"
        assert fills.position_size(conn) == -1
        fills.ingest_fills(conn, [_fill("b1", "buy", size=1, price=63500.0)])
        assert conn.execute(
            "SELECT role FROM order_fills WHERE fill_id='b1'").fetchone()[0] == "exit"


class TestRealisedPnl:
    """4 - P&L arithmetic and its ledger."""

    def test_long_profit(self):
        # +500 move, 1 contract, 0.001 BTC -> $0.50, minus fees
        assert fills.realised_pnl_usd(entry_price=64000.0, exit_price=64500.0,
                                      size=1, direction=1, fees=0.0) == pytest.approx(0.5)

    def test_long_loss(self):
        assert fills.realised_pnl_usd(entry_price=64000.0, exit_price=63500.0,
                                      size=1, direction=1, fees=0.0) == pytest.approx(-0.5)

    def test_short_profit_is_inverted(self):
        assert fills.realised_pnl_usd(entry_price=64000.0, exit_price=63500.0,
                                      size=1, direction=-1, fees=0.0) == pytest.approx(0.5)

    def test_fees_always_subtract(self):
        assert fills.realised_pnl_usd(entry_price=64000.0, exit_price=64500.0,
                                      size=1, direction=1, fees=0.1) == pytest.approx(0.4)
        assert fills.realised_pnl_usd(entry_price=64000.0, exit_price=63500.0,
                                      size=1, direction=-1, fees=0.1) == pytest.approx(0.4)

    def test_pnl_recorded_from_actual_fill_prices(self, conn):
        store = RiskStateStore(conn)
        fills.ingest_fills(conn, [_fill("e1", "buy", price=64000.0, fee=0.0)],
                           risk_state=store, now=NOON)
        fills.ingest_fills(conn, [_fill("x1", "sell", price=64500.0, fee=0.0)],
                           risk_state=store, now=NOON)
        assert store.state(NOON).realised_pnl == pytest.approx(0.5)

    def test_exactly_one_pnl_event_per_realising_fill(self, conn):
        store = RiskStateStore(conn)
        fills.ingest_fills(conn, [_fill("e1", "buy", price=64000.0, fee=0.0)],
                           risk_state=store, now=NOON)
        fills.ingest_fills(conn, [_fill("x1", "sell", price=64500.0, fee=0.0)],
                           risk_state=store, now=NOON)
        rows = conn.execute(
            "SELECT ref, amount FROM risk_day_events WHERE kind='pnl'").fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "fill:x1"
        assert rows[0][1] == pytest.approx(0.5)

    def test_pnl_event_is_idempotent_across_repeated_ingestion(self, conn):
        store = RiskStateStore(conn)
        batch_in = [_fill("e1", "buy", price=64000.0, fee=0.0)]
        batch_out = [_fill("x1", "sell", price=64500.0, fee=0.0)]
        for _ in range(4):
            fills.ingest_fills(conn, batch_in, risk_state=store, now=NOON)
            fills.ingest_fills(conn, batch_out, risk_state=store, now=NOON)
        assert conn.execute(
            "SELECT COUNT(*) FROM risk_day_events WHERE kind='pnl'").fetchone()[0] == 1
        assert store.state(NOON).realised_pnl == pytest.approx(0.5)

    def test_risk_state_mirror_derives_from_the_ledger(self, conn):
        store = RiskStateStore(conn)
        fills.ingest_fills(conn, [_fill("e1", "buy", price=64000.0, fee=0.0)],
                           risk_state=store, now=NOON)
        fills.ingest_fills(conn, [_fill("x1", "sell", price=63000.0, fee=0.0)],
                           risk_state=store, now=NOON)
        derived = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM risk_day_events WHERE kind='pnl'"
        ).fetchone()[0]
        mirror = conn.execute(
            "SELECT realised_pnl FROM risk_state WHERE utc_day=?",
            (utc_day_of(NOON),)).fetchone()[0]
        assert mirror == pytest.approx(derived)
        assert mirror == pytest.approx(-1.0)

    def test_a_loss_that_breaches_the_cap_blocks_further_entries(self, conn):
        store = RiskStateStore(conn)
        fills.ingest_fills(conn, [_fill("e1", "buy", size=1, price=64000.0, fee=0.0)],
                           risk_state=store, now=NOON)
        fills.ingest_fills(conn, [_fill("x1", "sell", size=1, price=59500.0, fee=0.0)],
                           risk_state=store, now=NOON)
        assert store.state(NOON).realised_pnl == pytest.approx(-4.5)
        rm = _risk(counters_provider=lambda: store.counters(NOON))
        d = rm.validate(_intent(), _account(), now=NOON)
        assert not d.approved and "daily loss" in d.reason


class TestEntryExitCapSemantics:
    """6 - the distinction that earns exits their exemption."""

    def _capped(self, conn):
        store = RiskStateStore(conn)
        store.record_order_placed("a", now=NOON)
        store.record_order_placed("b", now=NOON)
        return store, _risk(counters_provider=lambda: store.counters(NOON))

    def test_entry_blocked_once_the_cap_is_reached(self, conn):
        store, rm = self._capped(conn)
        d = rm.validate(_intent(), _account(), now=NOON)
        assert not d.approved and "orders today" in d.reason

    def test_exit_still_permitted_when_the_entry_cap_is_exhausted(self, conn):
        store, rm = self._capped(conn)
        exit_intent = _intent(client_order_id="x1", side="sell", intent_kind="exit")
        d = rm.validate(exit_intent, _account(position_size=1), now=NOON)
        assert d.approved, d.reason

    def test_exit_still_permitted_when_the_loss_cap_is_breached(self, conn):
        """A loss limit must not trap the account inside the losing position."""
        store = RiskStateStore(conn)
        store.record_realised_pnl("big", -10.0, now=NOON)
        rm = _risk(counters_provider=lambda: store.counters(NOON))
        assert not rm.validate(_intent(), _account(), now=NOON).approved
        d = rm.validate(_intent(client_order_id="x", side="sell", intent_kind="exit"),
                        _account(position_size=1), now=NOON)
        assert d.approved, d.reason

    def test_exit_with_no_position_is_rejected(self, conn):
        _, rm = self._capped(conn)
        d = rm.validate(_intent(side="sell", intent_kind="exit"),
                        _account(position_size=0), now=NOON)
        assert not d.approved and "no open position" in d.reason

    def test_exit_on_the_wrong_side_is_rejected(self, conn):
        """The exemption must not become a way to open a second position."""
        _, rm = self._capped(conn)
        d = rm.validate(_intent(side="buy", intent_kind="exit"),
                        _account(position_size=1), now=NOON)
        assert not d.approved and "would increase exposure" in d.reason

    def test_oversized_exit_is_rejected(self, conn):
        _, rm = self._capped(conn)
        d = rm.validate(_intent(side="sell", size=1, intent_kind="exit"),
                        _account(position_size=0), now=NOON)
        assert not d.approved

    def test_exit_cannot_flip_exposure(self, conn):
        rm = _risk()
        d = rm.validate(_intent(side="sell", size=3, intent_kind="exit"),
                        _account(position_size=1), now=NOON)
        assert not d.approved
        assert "exceeds open position" in d.reason or "exceeds max" in d.reason

    def test_exit_still_obeys_every_other_guard(self, conn):
        """Exempt from the CAPS only -- not from the kill switch or the rest."""
        rm = _risk(kill_switch_engaged=True)
        d = rm.validate(_intent(side="sell", intent_kind="exit"),
                        _account(position_size=1), now=NOON)
        assert not d.approved and "kill switch" in d.reason

        halted = _risk(halted=True, halt_reason="position mismatch")
        assert not halted.validate(_intent(side="sell", intent_kind="exit"),
                                   _account(position_size=1), now=NOON).approved

        off_tick = _risk().validate(
            _intent(side="sell", intent_kind="exit", limit_price=64187.53),
            _account(position_size=1), now=NOON)
        assert not off_tick.approved and "tick" in off_tick.reason

    def test_unknown_intent_kind_is_rejected(self, conn):
        d = _risk().validate(_intent(intent_kind="sneaky"), _account(), now=NOON)
        assert not d.approved and "unknown intent_kind" in d.reason

    def test_exit_does_not_consume_a_daily_slot(self, conn):
        store = RiskStateStore(conn)
        store.record_order_placed("a", now=NOON)
        store.record_order_placed("b", now=NOON)
        client = _FakeClient(place={"success": True, "result": {"id": 7}})
        rm = _risk(counters_provider=lambda: store.counters(NOON))
        ex = TestnetExecutor(conn, client, rm, risk_state=store)
        state, decision = ex.submit(
            _intent(client_order_id="exit-1", side="sell", intent_kind="exit"),
            _account(position_size=1), now=NOON)
        assert state is OrderState.ACKED, decision.reason
        assert store.state(NOON).orders_placed == 2, "exit consumed a slot"

    def test_entry_still_consumes_a_slot(self, conn):
        store = RiskStateStore(conn)
        client = _FakeClient(place={"success": True, "result": {"id": 8}})
        ex = TestnetExecutor(conn, client, _risk(
            counters_provider=lambda: store.counters(NOON)), risk_state=store)
        ex.submit(_intent(client_order_id="entry-1"), _account(), now=NOON)
        assert store.state(NOON).orders_placed == 1


class TestClosePath:
    """5 - the supervised close."""

    def test_close_builds_an_opposing_reducing_intent(self, conn):
        client = _FakeClient(place={"success": True, "result": {"id": 11}})
        ex = TestnetExecutor(conn, client, _risk())
        state, decision = ex.close_position(
            _account(position_size=1), limit_price=64187.5,
            client_order_id="close-1", now=NOON)
        assert state is OrderState.ACKED, decision.reason
        row = storage.get_order(conn, "close-1")
        assert row["side"] == "sell" and row["quantity"] == 1

    def test_close_of_a_short_buys(self, conn):
        client = _FakeClient(place={"success": True, "result": {"id": 12}})
        ex = TestnetExecutor(conn, client, _risk())
        ex.close_position(_account(position_size=-1), limit_price=64187.5,
                          client_order_id="close-2", now=NOON)
        assert storage.get_order(conn, "close-2")["side"] == "buy"

    def test_close_sends_reduce_only_to_the_exchange(self, conn):
        captured = {}

        class Capturing(_FakeClient):
            def place_order(self, **kw):
                captured.update(kw)
                return {"success": True, "result": {"id": 13}}

        ex = TestnetExecutor(conn, Capturing(), _risk())
        ex.close_position(_account(position_size=1), limit_price=64187.5,
                          client_order_id="close-3", now=NOON)
        assert captured.get("reduce_only") is True

    def test_entry_does_not_send_reduce_only(self, conn):
        captured = {}

        class Capturing(_FakeClient):
            def place_order(self, **kw):
                captured.update(kw)
                return {"success": True, "result": {"id": 14}}

        ex = TestnetExecutor(conn, Capturing(), _risk())
        ex.submit(_intent(), _account(), now=NOON)
        assert captured.get("reduce_only") is False

    def test_close_when_flat_refuses(self, conn):
        ex = TestnetExecutor(conn, _NoNetworkClient(), _risk())
        with pytest.raises(ValueError):
            ex.close_position(_account(position_size=0), limit_price=64187.5,
                              client_order_id="close-4", now=NOON)

    def test_close_size_matches_the_open_position(self, conn):
        client = _FakeClient(place={"success": True, "result": {"id": 15}})
        ex = TestnetExecutor(conn, client, _risk())
        ex.close_position(_account(position_size=-1), limit_price=64187.5,
                          client_order_id="close-5", now=NOON)
        assert storage.get_order(conn, "close-5")["quantity"] == 1


class TestReconciliationAfterFill:
    """8 - deterministic convergence, no invented state."""

    def test_fill_becomes_local_position_within_the_same_pass(self, conn):
        """The ordering property: no spurious halt on a genuine fill."""
        client = _FakeReconcileClient(
            fills={"success": True, "result": [_fill("f1", "buy")]},
            orders={"success": True, "result": [_stop_order()]},
            positions={"success": True, "result": [{"size": 1}]})
        _local_stop_row(conn)
        r = Reconciler(conn, client, product_id=84)
        result = r.reconcile()
        assert not r.halted, r.halt_reason
        assert result.clean
        assert fills.position_size(conn) == 1

    def test_position_without_a_matching_fill_still_halts(self, conn):
        """Exchange position we cannot explain must remain a halt."""
        client = _FakeReconcileClient(positions={"success": True, "result": [{"size": 1}]})
        r = Reconciler(conn, client, product_id=84)
        result = r.reconcile()
        assert r.halted and not result.clean
        assert Divergence.POSITION_MISMATCH in result.divergences

    def test_reconciliation_after_close_is_clean_and_flat(self, conn):
        client = _FakeReconcileClient(
            fills={"success": True, "result": [_fill("f1", "buy"),
                                               _fill("x1", "sell", price=64500.0)]},
            positions={"success": True, "result": []})
        r = Reconciler(conn, client, product_id=84)
        result = r.reconcile()
        assert result.clean and not r.halted
        assert fills.position_size(conn) == 0
        assert fills.open_position(conn) is None

    def test_repeated_passes_converge_and_do_not_double_count(self, conn):
        client = _FakeReconcileClient(
            fills={"success": True, "result": [_fill("f1", "buy")]},
            orders={"success": True, "result": [_stop_order()]},
            positions={"success": True, "result": [{"size": 1}]})
        _local_stop_row(conn)
        r = Reconciler(conn, client, product_id=84)
        for _ in range(5):
            r.reconcile()
        assert fills.position_size(conn) == 1
        assert not r.halted

    def test_malformed_fill_halts_rather_than_being_skipped(self, conn):
        client = _FakeReconcileClient(
            fills={"success": True, "result": [{"id": "bad", "side": "buy"}]})
        r = Reconciler(conn, client, product_id=84)
        result = r.reconcile()
        assert r.halted and not result.clean
        assert "fill ingestion failed" in r.halt_reason

    def test_reconciler_still_places_no_orders_while_ingesting(self, conn):
        class Watching(_FakeReconcileClient):
            def place_order(self, **kw):
                raise AssertionError("reconciler placed an order")

            def cancel_order(self, *a, **k):
                raise AssertionError("reconciler cancelled an order")

        client = Watching(fills={"success": True, "result": [_fill("f1", "buy")]},
                          positions={"success": True, "result": [{"size": 1}]})
        r = Reconciler(conn, client, product_id=84)
        r.reconcile()
        assert fills.position_size(conn) == 1

    def test_ingestion_can_be_disabled_for_a_read_only_reconciler(self, conn):
        client = _FakeReconcileClient(
            fills={"success": True, "result": [_fill("f1", "buy")]})
        r = Reconciler(conn, client, product_id=84, ingest_fills=False)
        r.reconcile()
        assert fills.position_size(conn) == 0


class TestFillPersistenceSafety:
    """Restart, concurrency, transaction boundaries, no-network."""

    def test_position_survives_restart(self, tmp_path):
        db = tmp_path / "fills.db"
        c1 = _fresh_db(db)
        fills.ingest_fills(c1, [_fill("f1", "buy", price=64000.0)])
        c1.close()

        c2 = storage.get_connection(db)
        assert fills.position_size(c2) == 1
        assert fills.open_position(c2)["entry_price"] == pytest.approx(64000.0)
        c2.close()

    def test_pnl_survives_restart(self, tmp_path):
        db = tmp_path / "pnl.db"
        c1 = _fresh_db(db)
        s1 = RiskStateStore(c1)
        fills.ingest_fills(c1, [_fill("e1", "buy", price=64000.0, fee=0.0)],
                           risk_state=s1, now=NOON)
        fills.ingest_fills(c1, [_fill("x1", "sell", price=64500.0, fee=0.0)],
                           risk_state=s1, now=NOON)
        c1.close()

        c2 = storage.get_connection(db)
        assert RiskStateStore(c2).state(NOON).realised_pnl == pytest.approx(0.5)
        c2.close()

    def test_failed_ingest_leaves_no_partial_state(self, conn):
        fills.ingest_fills(conn, [_fill("good", "buy")])
        before = fills.position_size(conn)
        # Force failure at the positions projection, which every ingest writes.
        conn.execute("DROP TABLE positions")
        conn.commit()
        with pytest.raises(Exception):
            fills.ingest_fills(conn, [_fill("bad-partner", "buy")])
        assert conn.execute(
            "SELECT COUNT(*) FROM order_fills WHERE fill_id='bad-partner'"
        ).fetchone()[0] == 0
        assert fills.position_size(conn) == before

    def test_malformed_fill_in_a_batch_rejects_the_whole_batch(self, conn):
        with pytest.raises(fills.MalformedFill):
            fills.ingest_fills(conn, [_fill("ok1", "buy"), {"id": "x", "side": "?"}])
        assert fills.position_size(conn) == 0, "partial batch was applied"

    def test_concurrent_ingestion_does_not_double_count(self, tmp_path):
        import threading
        db = tmp_path / "concurrent_fills.db"
        _fresh_db(db).close()

        errors = []
        batch = [_fill(f"f{i}", "buy") for i in range(5)]

        def worker():
            try:
                c = storage.get_connection(db)
                c.execute("PRAGMA busy_timeout = 10000")
                for _ in range(4):
                    fills.ingest_fills(c, batch)
                c.close()
            except Exception as exc:                      # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, errors

        c = storage.get_connection(db)
        assert c.execute("SELECT COUNT(*) FROM order_fills").fetchone()[0] == 5
        assert fills.position_size(c) == 5
        c.close()

    def test_ingestion_makes_no_network_call(self, conn):
        """Pure function of the payload it is handed."""
        fills.ingest_fills(conn, [_fill("f1", "buy")], risk_state=None)
        assert fills.position_size(conn) == 1


def _local_stop_row(conn, coid="stop-p1-q1", size=1, side="sell",
                    status="acked", delta_order_id="5001"):
    """Mirror of the local row the executor always writes when it places a stop.

    Without it the stop is, correctly, an ORPHAN exchange order -- the
    reconciler is right to halt on an exchange order it has no record of.
    """
    storage.write_order(conn, client_order_id=coid, instrument_id=84, side=side,
                        order_type="limit_order", quantity=float(size),
                        price=60997.0, status=status, strategy="protection",
                        placed_ts="2026-08-19T05:00:00+00:00",
                        delta_order_id=delta_order_id)
    conn.commit()


def _stop_order(size=1, side="sell", oid=5001, coid="stop-p1-q1",
                state="pending", reduce_only=True):
    """A live native protective stop as Delta returns it (probe 2169420335)."""
    return {"id": oid, "client_order_id": coid, "product_id": 84, "side": side,
            "size": size, "unfilled_size": size, "state": state,
            "limit_price": "60997", "stop_price": "61061.3",
            "stop_order_type": "stop_loss_order",
            "stop_trigger_method": "mark_price", "reduce_only": reduce_only}


class _StopCapturingClient(_FakeReconcileClient):
    """Records protective placements and lets a test refuse them."""

    def __init__(self, *a, place_result=None, place_exc=None, **kw):
        super().__init__(*a, **kw)
        self.placed = []
        self.cancelled = []
        self._place_result = place_result or {"success": True, "result": {"id": 7001}}
        self._place_exc = place_exc

    def place_order(self, **kw):
        self.placed.append(kw)
        if self._place_exc:
            raise self._place_exc
        if not self._place_result.get("success"):
            return self._place_result
        # A real exchange LISTS an order it acknowledged. Without this the
        # reconciler would correctly adopt the local row as cancelled (acked
        # but absent from the book) and re-place -- a thrash caused by the
        # double, not by the code under test.
        oid = (self._place_result.get("result") or {}).get("id", 7001)
        listed = dict(_stop_order(
            size=kw.get("size", 1), side=kw.get("side", "sell"), oid=oid,
            coid=kw.get("client_order_id"),
            reduce_only=bool(kw.get("reduce_only"))))
        if not kw.get("stop_order_type"):
            listed["stop_order_type"] = None
        self._o = {"success": True,
                   "result": list(self._o.get("result") or []) + [listed]}
        return self._place_result

    def cancel_order(self, order_id, product_id):
        self.cancelled.append(order_id)
        self._o = {"success": True,
                   "result": [o for o in (self._o.get("result") or [])
                              if str(o.get("id")) != str(order_id)]}
        return {"success": True, "result": {}}


class TestProtectionStateModel:
    """The invariant, derived rather than stored."""

    def test_flat_with_no_stop_is_flat(self, conn):
        st = protection.evaluate(conn, [])
        assert st.state is protection.ProtectionState.FLAT
        assert st.is_safe

    def test_exposed_with_no_stop_is_unprotected(self, conn):
        fills.ingest_fills(conn, [_fill("f1", "buy")])
        st = protection.evaluate(conn, [])
        assert st.state is protection.ProtectionState.UNPROTECTED
        assert not st.is_safe
        assert st.position_size == 1

    def test_exposed_with_correct_stop_is_protected(self, conn):
        fills.ingest_fills(conn, [_fill("f1", "buy")])
        st = protection.evaluate(conn, [_stop_order(size=1, side="sell")])
        assert st.state is protection.ProtectionState.PROTECTED
        assert st.is_safe

    def test_stop_of_the_wrong_size_is_stale(self, conn):
        fills.ingest_fills(conn, [_fill("f1", "buy", size=2)])
        st = protection.evaluate(conn, [_stop_order(size=1, side="sell")])
        assert st.state is protection.ProtectionState.STALE
        assert not st.is_safe

    def test_stop_on_the_wrong_side_is_stale_not_protected(self, conn):
        fills.ingest_fills(conn, [_fill("f1", "buy")])
        st = protection.evaluate(conn, [_stop_order(size=1, side="buy")])
        assert st.state is protection.ProtectionState.STALE

    def test_stop_without_reduce_only_does_not_count_as_protection(self, conn):
        fills.ingest_fills(conn, [_fill("f1", "buy")])
        st = protection.evaluate(conn, [_stop_order(reduce_only=False)])
        assert st.state is protection.ProtectionState.STALE

    def test_stop_while_flat_is_orphaned(self, conn):
        st = protection.evaluate(conn, [_stop_order()])
        assert st.state is protection.ProtectionState.ORPHANED
        assert not st.is_safe

    def test_non_stop_orders_are_ignored_by_protection(self, conn):
        fills.ingest_fills(conn, [_fill("f1", "buy")])
        plain = {"id": 1, "side": "sell", "size": 1, "state": "open",
                 "stop_order_type": None, "reduce_only": True}
        assert protection.evaluate(conn, [plain]).state is \
            protection.ProtectionState.UNPROTECTED

    def test_cancelled_stop_is_not_live(self, conn):
        fills.ingest_fills(conn, [_fill("f1", "buy")])
        st = protection.evaluate(conn, [_stop_order(state="cancelled")])
        assert st.state is protection.ProtectionState.UNPROTECTED

    def test_short_position_needs_a_buy_stop(self, conn):
        fills.ingest_fills(conn, [_fill("s1", "sell")])
        assert protection.evaluate(conn, [_stop_order(side="buy")]).state is \
            protection.ProtectionState.PROTECTED
        assert protection.evaluate(conn, [_stop_order(side="sell")]).state is \
            protection.ProtectionState.STALE

    def test_malformed_stop_payload_fails_closed(self, conn):
        fills.ingest_fills(conn, [_fill("f1", "buy")])
        for bad in ([{"stop_order_type": "stop_loss_order", "state": "pending",
                      "side": "sell", "size": "abc", "reduce_only": True}],
                    ["<html>"], [None]):
            st = protection.evaluate(conn, bad)
            assert not st.is_safe, f"{bad} was treated as protection"


class TestProtectivePricing:
    def test_long_stop_sits_below_entry(self):
        trigger, limit = protection.stop_prices(64000.0, +1)
        assert trigger < 64000.0 and limit < trigger

    def test_short_stop_sits_above_entry(self):
        trigger, limit = protection.stop_prices(64000.0, -1)
        assert trigger > 64000.0 and limit > trigger

    def test_distance_matches_the_configured_percentage(self):
        trigger, _ = protection.stop_prices(64000.0, +1)
        assert abs((1 - trigger / 64000.0) * 100
                   - limits.PROTECTIVE_STOP_DISTANCE_PCT) < 0.01

    def test_prices_are_tick_aligned(self):
        for size in (+1, -1):
            for px in (64000.0, 63187.37, 70123.45):
                for v in protection.stop_prices(px, size):
                    assert abs(round(v / 0.1) * 0.1 - v) < 1e-9


class TestProtectiveCoidIdempotency:
    def test_coid_is_deterministic(self):
        assert protection.protective_coid(7, 1) == protection.protective_coid(7, 1)

    def test_coid_encodes_position_and_quantity(self):
        assert protection.protective_coid(7, 1) == "stop-p7-q1"
        assert protection.protective_coid(7, 2) != protection.protective_coid(7, 1)
        assert protection.protective_coid(8, 1) != protection.protective_coid(7, 1)

    def test_sign_does_not_change_the_id(self):
        assert protection.protective_coid(7, -2) == protection.protective_coid(7, 2)

    def test_already_placed_detects_a_submitted_stop(self, conn):
        assert protection.already_placed(conn, "stop-p1-q1") is False
        storage.write_order(conn, client_order_id="stop-p1-q1", instrument_id=84,
                            side="sell", order_type="limit_order", quantity=1.0,
                            price=60997.0, status="acked", strategy="protection",
                            placed_ts="2026-08-19T05:00:00+00:00")
        conn.commit()
        assert protection.already_placed(conn, "stop-p1-q1") is True

    def test_blocked_stop_is_not_treated_as_placed(self, conn):
        storage.write_order(conn, client_order_id="stop-p1-q1", instrument_id=84,
                            side="sell", order_type="limit_order", quantity=1.0,
                            price=60997.0, status="blocked", strategy="protection",
                            placed_ts="2026-08-19T05:00:00+00:00")
        conn.commit()
        assert protection.already_placed(conn, "stop-p1-q1") is False


class TestProtectionThroughReconciliation:
    """End-to-end: the reconciler must establish or halt."""

    def _executor(self, conn, client, store=None):
        rm = _risk(counters_provider=(lambda: store.counters(NOON)) if store else None)
        return TestnetExecutor(conn, client, rm, strategy="protection",
                               risk_state=store)

    def test_entry_fill_creates_a_protective_stop(self, conn):
        client = _StopCapturingClient(
            fills={"success": True, "result": [_fill("f1", "buy", price=64000.0)]},
            positions={"success": True, "result": [{"size": 1}]})
        r = Reconciler(conn, client, product_id=84,
                       executor=self._executor(conn, client))
        r.reconcile()
        assert len(client.placed) == 1, client.placed
        sent = client.placed[0]
        assert sent["side"] == "sell" and sent["size"] == 1
        assert sent["reduce_only"] is True
        assert sent["stop_order_type"] == "stop_loss_order"
        assert sent["stop_trigger_method"] == "mark_price"
        assert float(sent["stop_price"]) < 64000.0

    def test_stop_uses_actual_filled_quantity_not_intended(self, conn):
        """A 1-lot fill of a 2-lot intent must be protected for 1, not 2."""
        client = _StopCapturingClient(
            fills={"success": True, "result": [_fill("f1", "buy", size=1)]},
            positions={"success": True, "result": [{"size": 1}]})
        r = Reconciler(conn, client, product_id=84,
                       executor=self._executor(conn, client))
        r.reconcile()
        assert client.placed[0]["size"] == 1

    def test_partial_fill_growth_resizes_protection(self, conn):
        client = _StopCapturingClient(
            fills={"success": True, "result": [_fill("f1", "buy", size=1)]},
            positions={"success": True, "result": [{"size": 1}]})
        ex = self._executor(conn, client)
        r = Reconciler(conn, client, product_id=84, executor=ex)
        r.reconcile()
        first = client.placed[0]["client_order_id"]

        client._f = {"success": True, "result": [_fill("f1", "buy", size=1),
                                                 _fill("f2", "buy", size=1)]}
        client._o = {"success": True, "result": [_stop_order(size=1, coid=first)]}
        client._p = {"success": True, "result": [{"size": 2}]}
        r.reconcile()
        assert len(client.placed) == 2
        assert client.placed[1]["size"] == 2
        assert client.placed[1]["client_order_id"] != first
        assert client.cancelled, "stale stop was not cancelled before replacement"

    def test_repeated_reconciliation_does_not_duplicate_the_stop(self, conn):
        client = _StopCapturingClient(
            fills={"success": True, "result": [_fill("f1", "buy")]},
            positions={"success": True, "result": [{"size": 1}]})
        ex = self._executor(conn, client)
        r = Reconciler(conn, client, product_id=84, executor=ex)
        r.reconcile()
        placed_after_first = len(client.placed)
        client._o = {"success": True, "result": [
            _stop_order(coid=client.placed[0]["client_order_id"])]}
        for _ in range(5):
            r.reconcile()
        assert len(client.placed) == placed_after_first == 1

    def test_no_duplicate_when_the_local_row_exists_but_exchange_lags(self, conn):
        """already_placed() is the guard for the submit->listed window."""
        client = _StopCapturingClient(
            fills={"success": True, "result": [_fill("f1", "buy")]},
            positions={"success": True, "result": [{"size": 1}]})
        r = Reconciler(conn, client, product_id=84,
                       executor=self._executor(conn, client))
        r.reconcile()
        coid = client.placed[0]["client_order_id"]
        assert protection.already_placed(conn, coid)
        # Simulate the exchange briefly not listing it: the local row must
        # still prevent a second placement within the same pass.
        st = protection.evaluate(conn, [])
        protection.ensure(conn, self._executor(conn, client), client, st,
                          mark_price=64000.0)
        assert len(client.placed) == 1

    def test_unprotected_position_halts(self, conn):
        client = _StopCapturingClient(
            fills={"success": True, "result": [_fill("f1", "buy")]},
            positions={"success": True, "result": [{"size": 1}]})
        r = Reconciler(conn, client, product_id=84, executor=None)
        result = r.reconcile()
        assert r.halted and not result.clean
        assert "unprotected" in r.halt_reason

    def test_stop_rejected_leaves_position_unprotected_and_halts(self, conn):
        client = _StopCapturingClient(
            fills={"success": True, "result": [_fill("f1", "buy")]},
            positions={"success": True, "result": [{"size": 1}]},
            place_result={"success": False, "error": {"code": "nope"}})
        r = Reconciler(conn, client, product_id=84,
                       executor=self._executor(conn, client))
        result = r.reconcile()
        assert r.halted and not result.clean

    def test_stop_timeout_is_not_protection(self, conn):
        client = _StopCapturingClient(
            fills={"success": True, "result": [_fill("f1", "buy")]},
            positions={"success": True, "result": [{"size": 1}]},
            place_exc=requests.exceptions.Timeout("t"))
        r = Reconciler(conn, client, product_id=84,
                       executor=self._executor(conn, client))
        result = r.reconcile()
        assert r.halted and not result.clean
        assert "unprotected" in r.halt_reason

    def test_stop_cancelled_while_exposed_halts(self, conn):
        client = _StopCapturingClient(
            fills={"success": True, "result": [_fill("f1", "buy")]},
            orders={"success": True, "result": [_stop_order(state="cancelled")]},
            positions={"success": True, "result": [{"size": 1}]})
        _local_stop_row(conn, status="cancelled")
        r = Reconciler(conn, client, product_id=84, executor=None)
        assert r.reconcile().clean is False
        assert r.halted

    def test_protected_position_does_not_halt(self, conn):
        client = _StopCapturingClient(
            fills={"success": True, "result": [_fill("f1", "buy")]},
            orders={"success": True, "result": [_stop_order()]},
            positions={"success": True, "result": [{"size": 1}]})
        _local_stop_row(conn)
        r = Reconciler(conn, client, product_id=84, executor=None)
        result = r.reconcile()
        assert not r.halted, r.halt_reason
        assert result.clean
        assert r.protection.state is protection.ProtectionState.PROTECTED

    def test_full_exit_cancels_the_orphaned_stop(self, conn):
        client = _StopCapturingClient(
            fills={"success": True, "result": [_fill("f1", "buy"),
                                               _fill("x1", "sell", price=64500.0)]},
            orders={"success": True, "result": [_stop_order(oid=9001)]},
            positions={"success": True, "result": []})
        _local_stop_row(conn, delta_order_id="9001")
        r = Reconciler(conn, client, product_id=84,
                       executor=self._executor(conn, client))
        r.reconcile()
        assert 9001 in client.cancelled, "orphaned stop left live while flat"
        assert fills.position_size(conn) == 0

    def test_flat_and_no_stop_is_clean(self, conn):
        client = _StopCapturingClient()
        r = Reconciler(conn, client, product_id=84, executor=None)
        result = r.reconcile()
        assert result.clean and not r.halted
        assert r.protection.state is protection.ProtectionState.FLAT

    def test_stop_fill_closes_the_position_and_clears_protection(self, conn):
        client = _StopCapturingClient(
            fills={"success": True, "result": [
                _fill("f1", "buy", price=64000.0),
                _fill("s1", "sell", price=63100.0, coid="stop-p1-q1")]},
            positions={"success": True, "result": []})
        r = Reconciler(conn, client, product_id=84,
                       executor=self._executor(conn, client))
        result = r.reconcile()
        assert fills.position_size(conn) == 0
        assert fills.open_position(conn) is None
        assert r.protection.state is protection.ProtectionState.FLAT
        assert result.clean and not r.halted

    def test_ensure_never_reports_protected_without_an_ack(self, conn):
        """ensure() called directly must not claim success on a non-ACK.

        The reconciler re-derives from the exchange afterwards, so this check
        is defence in depth -- but any other caller of ensure() would be
        trusting its return value, and it must never lie.
        """
        fills.ingest_fills(conn, [_fill("f1", "buy")])
        client = _StopCapturingClient(
            place_result={"success": False, "error": {"code": "refused"}})
        ex = self._executor(conn, client)
        st = protection.evaluate(conn, [])
        assert st.state is protection.ProtectionState.UNPROTECTED
        after = protection.ensure(conn, ex, client, st, mark_price=64000.0)
        assert after.state is not protection.ProtectionState.PROTECTED
        assert not after.is_safe

    def test_ensure_reports_protected_only_on_a_real_ack(self, conn):
        fills.ingest_fills(conn, [_fill("f1", "buy")])
        client = _StopCapturingClient()
        ex = self._executor(conn, client)
        st = protection.evaluate(conn, [])
        after = protection.ensure(conn, ex, client, st, mark_price=64000.0)
        assert after.state is protection.ProtectionState.PROTECTED

    def test_protection_can_be_disabled_for_a_read_only_reconciler(self, conn):
        client = _StopCapturingClient(
            fills={"success": True, "result": [_fill("f1", "buy")]},
            positions={"success": True, "result": [{"size": 1}]})
        r = Reconciler(conn, client, product_id=84, executor=None,
                       enforce_protection=False)
        assert r.reconcile().clean
        assert not r.halted


class TestProtectiveRiskSemantics:
    """Requirement 14: protection must never be blocked by the entry caps."""

    def _protective(self, **kw):
        base = dict(client_order_id="stop-p1-q1", side="sell", intent_kind="protective",
                    limit_price=63100.0, stop_price=63163.0)
        base.update(kw)
        return _intent(**base)

    def test_protective_permitted_when_the_order_cap_is_exhausted(self, conn):
        store = RiskStateStore(conn)
        store.record_order_placed("a", now=NOON)
        store.record_order_placed("b", now=NOON)
        rm = _risk(counters_provider=lambda: store.counters(NOON))
        d = rm.validate(self._protective(), _account(position_size=1), now=NOON)
        assert d.approved, d.reason

    def test_protective_permitted_when_the_loss_cap_is_breached(self, conn):
        store = RiskStateStore(conn)
        store.record_realised_pnl("big", -99.0, now=NOON)
        rm = _risk(counters_provider=lambda: store.counters(NOON))
        assert rm.validate(self._protective(), _account(position_size=1),
                           now=NOON).approved

    def test_protective_permitted_inside_the_settlement_dead_zone(self, conn):
        dead = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
        d = _risk().validate(self._protective(), _account(position_size=1), now=dead)
        assert d.approved, d.reason
        blocked = _risk().validate(_intent(), _account(), now=dead)
        assert not blocked.approved and "dead zone" in blocked.reason

    def test_protective_permitted_while_the_kill_switch_is_engaged(self, conn):
        # Corrected at Gate 5C.1: a kill switch that forbids protecting an
        # open position converts bounded exposure into unbounded exposure.
        # See TestKillSwitchProtectiveException for the full distinction.
        d = _risk(kill_switch_engaged=True).validate(
            self._protective(), _account(position_size=1), now=NOON)
        assert d.approved, d.reason

    def test_protective_cannot_increase_exposure(self, conn):
        d = _risk().validate(self._protective(side="buy"),
                             _account(position_size=1), now=NOON)
        assert not d.approved and "would increase exposure" in d.reason

    def test_protective_cannot_flip_the_position(self, conn):
        d = _risk().validate(self._protective(size=3),
                             _account(position_size=1), now=NOON)
        assert not d.approved

    def test_protective_with_no_position_is_rejected(self, conn):
        d = _risk().validate(self._protective(), _account(position_size=0), now=NOON)
        assert not d.approved and "no open position" in d.reason

    def test_protective_does_not_consume_a_daily_slot(self, conn):
        store = RiskStateStore(conn)
        client = _FakeClient(place={"success": True, "result": {"id": 3}})
        rm = _risk(counters_provider=lambda: store.counters(NOON))
        ex = TestnetExecutor(conn, client, rm, risk_state=store)
        state, decision = ex.submit(self._protective(), _account(position_size=1),
                                    now=NOON)
        assert state is OrderState.ACKED, decision.reason
        assert store.state(NOON).orders_placed == 0

    def test_entry_never_transmits_stop_parameters(self, conn):
        captured = {}

        class Capturing(_FakeClient):
            def place_order(self, **kw):
                captured.update(kw)
                return {"success": True, "result": {"id": 4}}

        ex = TestnetExecutor(conn, Capturing(), _risk())
        ex.submit(_intent(), _account(), now=NOON)
        assert "stop_order_type" not in captured
        assert captured.get("reduce_only") is False


class TestProtectionPersistenceAndConcurrency:
    def test_protection_survives_restart_without_duplicating(self, tmp_path):
        db = tmp_path / "prot.db"
        c1 = _fresh_db(db)
        client = _StopCapturingClient(
            fills={"success": True, "result": [_fill("f1", "buy")]},
            positions={"success": True, "result": [{"size": 1}]})
        ex1 = TestnetExecutor(c1, client, _risk(), strategy="protection")
        Reconciler(c1, client, product_id=84, executor=ex1).reconcile()
        assert len(client.placed) == 1
        coid = client.placed[0]["client_order_id"]
        c1.close()

        c2 = storage.get_connection(db)
        ex2 = TestnetExecutor(c2, client, _risk(), strategy="protection")
        Reconciler(c2, client, product_id=84, executor=ex2).reconcile()
        assert len(client.placed) == 1, "restart re-placed the stop"
        assert protection.already_placed(c2, coid)
        c2.close()

    def test_concurrent_reconciliation_places_at_most_one_stop(self, tmp_path):
        import threading
        db = tmp_path / "prot_concurrent.db"
        c0 = _fresh_db(db)
        fills.ingest_fills(c0, [_fill("f1", "buy")])
        c0.close()

        client = _StopCapturingClient(
            fills={"success": True, "result": [_fill("f1", "buy")]},
            positions={"success": True, "result": [{"size": 1}]})
        errors = []

        def worker():
            try:
                c = storage.get_connection(db)
                c.execute("PRAGMA busy_timeout = 10000")
                ex = TestnetExecutor(c, client, _risk(), strategy="protection")
                Reconciler(c, client, product_id=84, executor=ex).reconcile()
                c.close()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        c = storage.get_connection(db)
        rows = c.execute("SELECT COUNT(*) FROM orders WHERE client_order_id "
                         "LIKE 'stop-%'").fetchone()[0]
        c.close()
        assert rows <= 1, f"{rows} protective orders persisted"


class TestRuntimeSchemaOrdering:
    """Gate 5B regression: boot halted on `no such table: order_fills`.

    The defect was ordering, not logic -- boot_reconcile() ingests fills, which
    needs order_fills, and the schema-ensure ran afterwards. These tests pin
    both the behaviour and the source ordering that produces it.
    """

    def _bare_db(self, path):
        """Base schema only -- no execution schema. A genuinely fresh DB."""
        c = storage.get_connection(path)
        storage.init_schema(c)
        c.execute(
            "INSERT OR REPLACE INTO instruments "
            "(id,symbol,contract_type,underlying,quoting,tick_size,contract_value,"
            "state,fetched_at) VALUES "
            "(84,'BTCUSD','perpetual_futures','BTC','USD',0.1,0.001,'live','t')")
        c.commit()
        return c

    def test_schema_less_db_lacks_order_fills(self, tmp_path):
        c = self._bare_db(tmp_path / "bare.db")
        n = c.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='order_fills'"
                      ).fetchone()[0]
        c.close()
        assert n == 0, "fixture is not actually schema-less"

    def test_boot_without_schema_init_halts(self, tmp_path):
        """The exact production failure, reproduced."""
        c = self._bare_db(tmp_path / "nofix.db")
        r = Reconciler(c, _FakeReconcileClient(), product_id=84)
        r.start()
        assert r.halted
        assert "order_fills" in r.halt_reason
        assert not r.trading_permitted
        c.close()

    def test_schema_init_before_start_boots_clean(self, tmp_path):
        """The fix: initialise on the reconciler's own connection first."""
        c = self._bare_db(tmp_path / "fixed.db")
        storage.init_execution_schema(c)
        r = Reconciler(c, _FakeReconcileClient(), product_id=84)
        r.start()
        assert not r.halted, r.halt_reason
        assert r.trading_permitted
        assert r.protection.state is protection.ProtectionState.FLAT
        c.close()

    def test_schema_init_is_idempotent_and_preserves_rows(self, tmp_path):
        c = self._bare_db(tmp_path / "idem.db")
        storage.init_execution_schema(c)
        fills.ingest_fills(c, [_fill("f1", "buy")])
        before = fills.position_size(c)
        rows = c.execute("SELECT COUNT(*) FROM order_fills").fetchone()[0]
        for _ in range(3):
            storage.init_execution_schema(c)
        assert c.execute("SELECT COUNT(*) FROM order_fills").fetchone()[0] == rows
        assert fills.position_size(c) == before
        c.close()

    def test_lifecycle_initialises_schema_before_boot(self):
        """Source-order guard: the defect was invisible to behaviour tests."""
        src = pathlib.Path("src/runtime/lifecycle.py").read_text(encoding="utf-8")
        assert src.index("init_execution_schema(exec_conn)") < src.index("reconciler.start()")

    def test_lifecycle_initialises_schema_on_the_runtime_connection(self):
        src = pathlib.Path("src/runtime/lifecycle.py").read_text(encoding="utf-8")
        assert "init_execution_schema(exec_conn)" in src, \
            "execution schema must be ensured on the reconciler's connection"


class TestRuntimeExecutorWiring:
    """Gate 5B regression: protection could only detect, never establish."""

    def test_reconciler_accepts_an_executor_after_construction(self, conn):
        r = Reconciler(conn, _FakeReconcileClient(), product_id=84)
        assert r._executor is None
        ex = TestnetExecutor(conn, _FakeReconcileClient(), _risk())
        r.set_executor(ex)
        assert r._executor is ex

    def test_lifecycle_attaches_the_executor_before_boot(self):
        src = pathlib.Path("src/runtime/lifecycle.py").read_text(encoding="utf-8")
        assert src.index("set_executor(executor)") < src.index("reconciler.start()")

    def test_lifecycle_builds_exactly_one_executor_and_one_reconciler(self):
        """No parallel execution path may exist."""
        src = pathlib.Path("src/runtime/lifecycle.py").read_text(encoding="utf-8")
        assert src.count("TestnetExecutor(") == 1
        assert src.count("Reconciler(exec_conn, client") == 1

    def test_flat_state_creates_no_protective_stop(self, conn):
        """An executor being present must not mean an order gets placed."""
        client = _StopCapturingClient()
        ex = TestnetExecutor(conn, client, _risk(), strategy="protection")
        r = Reconciler(conn, client, product_id=84)
        r.set_executor(ex)
        r.start()
        assert r.protection.state is protection.ProtectionState.FLAT
        assert client.placed == [], "placed an order while flat"
        assert not r.halted

    def test_exposed_position_uses_the_supervised_executor(self, conn):
        client = _StopCapturingClient(
            fills={"success": True, "result": [_fill("f1", "buy", price=64000.0)]},
            positions={"success": True, "result": [{"size": 1}]})
        ex = TestnetExecutor(conn, client, _risk(), strategy="protection")
        r = Reconciler(conn, client, product_id=84)
        r.set_executor(ex)
        r.reconcile()
        assert len(client.placed) == 1
        assert client.placed[0]["stop_order_type"] == "stop_loss_order"
        # It went through the executor, so a local order row exists.
        assert storage.get_order(conn, client.placed[0]["client_order_id"]) is not None

    def test_without_an_executor_protection_still_fails_closed(self, conn):
        client = _StopCapturingClient(
            fills={"success": True, "result": [_fill("f1", "buy")]},
            positions={"success": True, "result": [{"size": 1}]})
        r = Reconciler(conn, client, product_id=84)      # no executor
        result = r.reconcile()
        assert r.halted and not result.clean
        assert client.placed == []

    def test_protection_uses_no_network_beyond_the_injected_client(self, conn):
        """All exchange access goes through the client double."""
        client = _StopCapturingClient(
            fills={"success": True, "result": [_fill("f1", "buy")]},
            positions={"success": True, "result": [{"size": 1}]})
        ex = TestnetExecutor(conn, client, _risk(), strategy="protection")
        r = Reconciler(conn, client, product_id=84)
        r.set_executor(ex)
        r.reconcile()
        assert set(client.calls) <= {"get_live_orders", "get_fills", "get_positions"}


class TestKillSwitchProtectiveException:
    """5C.1 fix 1: the kill switch must not make a held position unprotectable.

    The distinction is deliberately three-way and must stay that way:
    entries blocked, discretionary exits blocked, protective stops permitted.
    """

    def _protective(self, **kw):
        base = dict(client_order_id="stop-p1-q1", side="sell",
                    intent_kind="protective", limit_price=63100.0,
                    stop_price=63163.0)
        base.update(kw)
        return _intent(**base)

    def _rm(self):
        return _risk(kill_switch_engaged=True)

    def test_entry_remains_blocked_by_the_kill_switch(self):
        d = self._rm().validate(_intent(), _account(), now=NOON)
        assert not d.approved and "kill switch" in d.reason

    def test_discretionary_exit_remains_blocked_by_the_kill_switch(self):
        """An exit is still a decision to trade; the operator said no."""
        d = self._rm().validate(
            _intent(side="sell", intent_kind="exit"),
            _account(position_size=1), now=NOON)
        assert not d.approved and "kill switch" in d.reason

    def test_protective_stop_is_permitted_while_the_kill_switch_is_engaged(self):
        d = self._rm().validate(self._protective(), _account(position_size=1),
                                now=NOON)
        assert d.approved, d.reason

    def test_the_exemption_is_exactly_one_intent_kind(self):
        rm = self._rm()
        outcomes = {
            kind: rm.validate(
                _intent(side="sell", intent_kind=kind,
                        limit_price=63100.0, stop_price=63163.0),
                _account(position_size=1), now=NOON).approved
            for kind in ("entry", "exit", "protective")
        }
        assert outcomes == {"entry": False, "exit": False, "protective": True}

    def test_unknown_intent_kind_cannot_borrow_the_exemption(self):
        for kind in ("protective ", "PROTECTIVE", "prot", "", "sneaky"):
            d = self._rm().validate(
                _intent(side="sell", intent_kind=kind), _account(position_size=1),
                now=NOON)
            assert not d.approved, kind
            assert "unknown intent_kind" in d.reason, kind

    def test_protective_exemption_does_not_bypass_the_halt_latch(self):
        rm = _risk(kill_switch_engaged=True, halted=True,
                   halt_reason="position mismatch")
        d = rm.validate(self._protective(), _account(position_size=1), now=NOON)
        assert not d.approved and "halted" in d.reason

    def test_protective_exemption_does_not_bypass_the_exposure_proof(self):
        """The whole point: it still cannot open or increase exposure."""
        rm = self._rm()
        assert not rm.validate(self._protective(),
                               _account(position_size=0), now=NOON).approved
        assert not rm.validate(self._protective(side="buy"),
                               _account(position_size=1), now=NOON).approved
        assert not rm.validate(self._protective(size=3),
                               _account(position_size=1), now=NOON).approved

    def test_protective_exemption_does_not_bypass_symbol_or_product(self):
        rm = self._rm()
        assert not rm.validate(self._protective(symbol="ETHUSD"),
                               _account(position_size=1), now=NOON).approved
        assert not rm.validate(self._protective(product_id=99),
                               _account(position_size=1), now=NOON).approved

    def test_protective_exemption_does_not_bypass_tick_alignment(self):
        d = self._rm().validate(self._protective(limit_price=63100.03),
                                _account(position_size=1), now=NOON)
        assert not d.approved and "tick" in d.reason

    def test_protective_exemption_does_not_bypass_order_type(self):
        d = self._rm().validate(self._protective(order_type="market_order"),
                                _account(position_size=1), now=NOON)
        assert not d.approved and "order_type" in d.reason


class TestProtectiveDeviationAfterMarketMoves:
    """5C.1 fix 2: an adverse move must not make protection unplaceable.

    Gate 5C preflight measured the real failure: with mark at 64300 and a stop
    built from a 63163 entry, the stop-limit was 2.02% from mark and the
    entry-oriented guard rejected it -- exactly when it was needed.
    """

    ENTRY = 64000.0

    def _protective_at(self, position_size=1, **kw):
        trigger, limit = protection.stop_prices(self.ENTRY, position_size)
        base = dict(client_order_id="stop-p1-q1",
                    side="sell" if position_size > 0 else "buy",
                    intent_kind="protective", limit_price=limit,
                    stop_price=trigger)
        base.update(kw)
        return _intent(**base)

    def test_reproduces_the_gate_5c_rejection_shape_for_an_entry(self):
        """The same price is still rejected for an ENTRY -- guard intact."""
        trigger, limit = protection.stop_prices(self.ENTRY, 1)
        d = _risk().validate(
            _intent(limit_price=limit, stop_price=trigger),
            _account(mark_price=64300.0), now=NOON)
        assert not d.approved and "from mark" in d.reason

    def test_protective_survives_an_adverse_move(self):
        d = _risk().validate(self._protective_at(),
                             _account(position_size=1, mark_price=64300.0),
                             now=NOON)
        assert d.approved, d.reason

    @pytest.mark.parametrize("mark", [60000.0, 62000.0, 64000.0, 66000.0, 70000.0])
    def test_protective_is_placeable_at_any_mark(self, mark):
        """Protection must never become unplaceable because price moved."""
        d = _risk().validate(self._protective_at(),
                             _account(position_size=1, mark_price=mark), now=NOON)
        assert d.approved, f"mark={mark}: {d.reason}"

    def test_short_protection_survives_an_adverse_move(self):
        d = _risk().validate(self._protective_at(position_size=-1),
                             _account(position_size=-1, mark_price=63000.0),
                             now=NOON)
        assert d.approved, d.reason

    def test_limit_far_from_its_trigger_is_still_rejected(self):
        """The exemption is bounded: limit must stay adjacent to the trigger."""
        trigger, _ = protection.stop_prices(self.ENTRY, 1)
        far = round(round(trigger * 0.98 / 0.1) * 0.1, 1)     # 2% away
        d = _risk().validate(self._protective_at(limit_price=far),
                             _account(position_size=1, mark_price=64000.0),
                             now=NOON)
        assert not d.approved
        assert "from its trigger" in d.reason

    def test_protective_without_a_trigger_is_rejected(self):
        d = _risk().validate(self._protective_at(stop_price=None),
                             _account(position_size=1, mark_price=64000.0),
                             now=NOON)
        assert not d.approved

    def test_designed_offset_is_well_inside_the_bound(self):
        trigger, limit = protection.stop_prices(self.ENTRY, 1)
        offset = abs(limit - trigger) / trigger * 100
        assert offset < limits.MAX_PROTECTIVE_LIMIT_OFFSET_PCT
        assert offset == pytest.approx(limits.PROTECTIVE_STOP_LIMIT_SLIPPAGE_PCT,
                                       abs=0.02)

    def test_entry_deviation_guard_is_unchanged(self):
        """Fix 2 must not loosen anything for entries."""
        assert _risk().validate(_intent(limit_price=64187.5),
                                _account(mark_price=64187.5), now=NOON).approved
        d = _risk().validate(_intent(limit_price=70000.0),
                             _account(mark_price=64187.5), now=NOON)
        assert not d.approved and "from mark" in d.reason

    def test_exit_still_uses_the_mark_deviation_guard(self):
        """Only protective orders change basis; discretionary exits do not."""
        d = _risk().validate(
            _intent(side="sell", intent_kind="exit", limit_price=70000.0),
            _account(position_size=1, mark_price=64187.5), now=NOON)
        assert not d.approved and "from mark" in d.reason


class TestProtectiveExemptionCannotBecomeAnEntry:
    """Adversarial: the two exemptions together must not open a back door."""

    def _rm(self):
        return _risk(kill_switch_engaged=True)

    def test_cannot_open_a_position_while_flat(self):
        for side in ("buy", "sell"):
            d = self._rm().validate(
                _intent(side=side, intent_kind="protective",
                        limit_price=63100.0, stop_price=63163.0),
                _account(position_size=0), now=NOON)
            assert not d.approved and "no open position" in d.reason

    def test_cannot_flip_a_long_into_a_short(self):
        d = self._rm().validate(
            _intent(side="sell", size=2, intent_kind="protective",
                    limit_price=63100.0, stop_price=63163.0),
            _account(position_size=1), now=NOON)
        assert not d.approved and "exceeds open position" in d.reason

    def test_cannot_add_to_a_position(self):
        d = self._rm().validate(
            _intent(side="buy", intent_kind="protective",
                    limit_price=63100.0, stop_price=63163.0),
            _account(position_size=1), now=NOON)
        assert not d.approved and "would increase exposure" in d.reason

    def test_cannot_place_an_arbitrary_price_under_the_protective_label(self):
        d = self._rm().validate(
            _intent(side="sell", intent_kind="protective",
                    limit_price=1000.0, stop_price=63163.0),
            _account(position_size=1), now=NOON)
        assert not d.approved and "from its trigger" in d.reason

    def test_executor_sends_reduce_only_for_every_protective_order(self, conn):
        captured = {}

        class Capturing(_FakeClient):
            def place_order(self, **kw):
                captured.update(kw)
                return {"success": True, "result": {"id": 21}}

        ex = TestnetExecutor(conn, Capturing(), _risk(kill_switch_engaged=True))
        state, decision = ex.submit(
            _intent(client_order_id="stop-p1-q1", side="sell",
                    intent_kind="protective", limit_price=63100.0,
                    stop_price=63163.0),
            _account(position_size=1), now=NOON)
        assert state is OrderState.ACKED, decision.reason
        assert captured["reduce_only"] is True
        assert captured["stop_order_type"] == "stop_loss_order"
        assert captured["stop_trigger_method"] == "mark_price"

    def test_protective_order_still_consumes_no_daily_slot(self, conn):
        store = RiskStateStore(conn)
        client = _FakeClient(place={"success": True, "result": {"id": 22}})
        ex = TestnetExecutor(conn, client,
                             _risk(kill_switch_engaged=True,
                                   counters_provider=lambda: store.counters(NOON)),
                             risk_state=store)
        ex.submit(_intent(client_order_id="stop-p1-q1", side="sell",
                          intent_kind="protective", limit_price=63100.0,
                          stop_price=63163.0),
                  _account(position_size=1), now=NOON)
        assert store.state(NOON).orders_placed == 0


class TestNativeStopClientParamsUnchanged:
    """Requirement 4: the stop parameters remain additive."""

    def test_payload_is_identical_when_stop_params_are_absent(self):
        from unittest.mock import patch
        from src.exchange.delta_client import DeltaClient
        c = DeltaClient("https://x", "k", "s")

        plain, withstop = {}, {}
        with patch.object(DeltaClient, "_signed_post",
                          lambda self, path, payload: plain.update(payload) or {}):
            c.place_order(product_id=84, size=1, side="buy",
                          limit_price="100.0", client_order_id="t")
        with patch.object(DeltaClient, "_signed_post",
                          lambda self, path, payload: withstop.update(payload) or {}):
            c.place_order(product_id=84, size=1, side="buy", limit_price="100.0",
                          client_order_id="t", stop_order_type="stop_loss_order",
                          stop_price="90.0", stop_trigger_method="mark_price")

        assert set(plain) == {"product_id", "size", "side", "order_type",
                              "time_in_force", "limit_price", "client_order_id"}
        assert set(withstop) - set(plain) == {"stop_order_type", "stop_price",
                                              "stop_trigger_method"}
        for k in plain:
            assert plain[k] == withstop[k]


class TestRejectionObservability:
    """5C.2 fix 1: a 4xx body must be captured durably.

    Gate 5C was diagnosed from client_order_id filename patterns because the
    executor read only the status code and discarded the response.
    """

    def _http_error(self, status, body, json_payload=None):
        resp = requests.Response()
        resp.status_code = status
        resp._content = body.encode() if isinstance(body, str) else body
        return requests.exceptions.HTTPError(response=resp)

    def test_4xx_body_is_persisted(self, conn):
        err = self._http_error(400, '{"error":{"code":"invalid_client_order_id"},'
                                    '"success":false}')
        ex = TestnetExecutor(conn, _FakeClient(place=err), _risk())
        state, _ = ex.submit(_intent(client_order_id="rej-1"), _account(), now=NOON)
        assert state is OrderState.REJECTED

        rec = storage.get_order_rejection(conn, "rej-1")
        assert rec is not None
        assert rec["http_status"] == 400
        assert rec["error_code"] == "invalid_client_order_id"
        assert "invalid_client_order_id" in rec["body"]

    def test_definitive_4xx_remains_rejected_not_unknown(self, conn):
        for status in (400, 401, 403, 404, 422):
            err = self._http_error(status, '{"error":{"code":"nope"}}')
            ex = TestnetExecutor(conn, _FakeClient(place=err), _risk())
            state, _ = ex.submit(_intent(client_order_id=f"rej-{status}"),
                                 _account(), now=NOON)
            assert state is OrderState.REJECTED, status

    def test_5xx_remains_unknown_and_records_nothing(self, conn):
        err = self._http_error(503, "gateway down")
        ex = TestnetExecutor(conn, _FakeClient(place=err), _risk())
        state, _ = ex.submit(_intent(client_order_id="amb-1"), _account(), now=NOON)
        assert state is OrderState.UNKNOWN
        assert storage.get_order_rejection(conn, "amb-1") is None

    def test_unparseable_body_still_captured(self, conn):
        err = self._http_error(400, "<html>Bad Request</html>")
        ex = TestnetExecutor(conn, _FakeClient(place=err), _risk())
        state, _ = ex.submit(_intent(client_order_id="rej-html"), _account(), now=NOON)
        assert state is OrderState.REJECTED
        rec = storage.get_order_rejection(conn, "rej-html")
        assert rec["error_code"] is None
        assert "Bad Request" in rec["body"]

    def test_body_is_truncated(self, conn):
        from src.execution.testnet_executor import MAX_REJECTION_BODY
        err = self._http_error(400, "x" * (MAX_REJECTION_BODY * 3))
        ex = TestnetExecutor(conn, _FakeClient(place=err), _risk())
        ex.submit(_intent(client_order_id="rej-big"), _account(), now=NOON)
        rec = storage.get_order_rejection(conn, "rej-big")
        assert len(rec["body"]) <= MAX_REJECTION_BODY

    def test_recording_failure_never_masks_the_rejection(self, conn):
        """A broken audit table must not turn REJECTED into an exception."""
        conn.execute("DROP TABLE order_rejections")
        conn.commit()
        err = self._http_error(400, '{"error":{"code":"x"}}')
        ex = TestnetExecutor(conn, _FakeClient(place=err), _risk())
        state, _ = ex.submit(_intent(client_order_id="rej-nodb"), _account(), now=NOON)
        assert state is OrderState.REJECTED

    def test_no_credentials_can_reach_the_record(self, conn):
        """Only the exchange RESPONSE is read -- never our request or headers.

        Checked against EXECUTABLE code with docstrings and comments stripped:
        the prose legitimately names the things it promises not to touch, so a
        raw substring scan false-positives on its own documentation.
        """
        import ast, inspect
        from src.execution import testnet_executor as te

        tree = ast.parse(inspect.getsource(te._rejection_detail))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.Module))                     and ast.get_docstring(node):
                node.body = node.body[1:]
        code = ast.unparse(tree)
        for forbidden in ("api_key", "api_secret", "signature", "headers",
                          ".request", "session"):
            assert forbidden not in code, f"{forbidden} appears in executable code"
        # It must read the response and nothing else.
        assert "response" in code

    def test_rejection_record_is_idempotent(self, conn):
        err = self._http_error(400, '{"error":{"code":"dup"}}')
        for _ in range(3):
            TestnetExecutor(conn, _FakeClient(place=err), _risk()).submit(
                _intent(client_order_id="rej-idem"), _account(), now=NOON)
        n = conn.execute("SELECT COUNT(*) FROM order_rejections "
                         "WHERE client_order_id='rej-idem'").fetchone()[0]
        assert n == 1


class TestExecutableEntryPrice:
    """5C.2 fix 3: the entry price must actually cross the book.

    Measured in Gate 5C: mark 64400.35771707 -> limit 64400.4 against bid
    64400.0, i.e. ABOVE the bid, so the sell would have rested, not filled.
    """

    GATE5C_BID = 64400.0
    GATE5C_ASK = 64401.0
    GATE5C_MARK = 64400.35771707

    def test_reproduces_the_gate_5c_non_marketable_price(self):
        """The old rule, shown to be wrong on the real book."""
        tick = limits.BTCUSD_TICK_SIZE
        old = round(round(self.GATE5C_MARK / tick) * tick, 1)
        assert old == 64400.4
        assert old > self.GATE5C_BID, "old sell price did NOT cross the bid"

    def test_sell_crosses_the_bid_on_the_gate_5c_book(self):
        px = bridge.executable_limit_price(-1, self.GATE5C_BID, self.GATE5C_ASK)
        assert px <= self.GATE5C_BID
        assert px == 64400.0

    def test_buy_crosses_the_ask_on_the_gate_5c_book(self):
        px = bridge.executable_limit_price(1, self.GATE5C_BID, self.GATE5C_ASK)
        assert px >= self.GATE5C_ASK
        assert px == 64401.0

    @pytest.mark.parametrize("bid,ask", [
        (64400.0, 64401.0), (63000.03, 63000.07), (59999.95, 60000.05),
        (70000.0, 70000.1), (64187.44, 64187.56), (1.0, 1.1),
    ])
    def test_marketability_holds_for_every_book(self, bid, ask):
        buy = bridge.executable_limit_price(1, bid, ask)
        sell = bridge.executable_limit_price(-1, bid, ask)
        assert buy >= ask, f"buy {buy} does not reach ask {ask}"
        assert sell <= bid, f"sell {sell} does not reach bid {bid}"

    @pytest.mark.parametrize("bid,ask", [
        (64400.0, 64401.0), (63000.03, 63000.07), (59999.95, 60000.05),
        (64187.44, 64187.56),
    ])
    def test_prices_are_tick_aligned(self, bid, ask):
        tick = limits.BTCUSD_TICK_SIZE
        for d in (1, -1):
            px = bridge.executable_limit_price(d, bid, ask)
            assert abs(round(px / tick) * tick - px) < 1e-9, px

    def test_rounding_goes_away_from_the_touch(self):
        """Nearest-rounding is what produced the Gate 5C failure."""
        bid, ask = 64400.04, 64400.96          # nearest would give 64400.0/64401.0
        assert bridge.executable_limit_price(-1, bid, ask) <= bid   # floor -> 64400.0
        assert bridge.executable_limit_price(1, bid, ask) >= ask    # ceil  -> 64401.0

    def test_invalid_books_are_refused(self):
        for bid, ask in [(0.0, 100.0), (100.0, 0.0), (-1.0, 100.0), (101.0, 100.0)]:
            with pytest.raises(ValueError):
                bridge.executable_limit_price(1, bid, ask)

    def test_build_intent_requires_the_book(self):
        with pytest.raises(TypeError):
            bridge.build_intent(direction=1, mark_price=MARK, enabled=True)

    def test_stop_is_recomputed_from_the_executable_price(self):
        it = bridge.build_intent(direction=-1, mark_price=self.GATE5C_MARK,
                                 enabled=True, best_bid=self.GATE5C_BID,
                                 best_ask=self.GATE5C_ASK, utc_day="2026-08-19")
        assert it.limit_price == 64400.0
        assert it.stop_price > it.limit_price
        expected = it.limit_price * (1 + bridge.STOP_DISTANCE_PCT / 100)
        assert abs(it.stop_price - expected) < 0.11

    def test_long_stop_sits_below_the_executable_price(self):
        it = bridge.build_intent(direction=1, mark_price=self.GATE5C_MARK,
                                 enabled=True, best_bid=self.GATE5C_BID,
                                 best_ask=self.GATE5C_ASK, utc_day="2026-08-19")
        assert it.limit_price == 64401.0
        assert it.stop_price < it.limit_price

    def test_corrected_intent_still_passes_every_risk_guard(self):
        for d in (1, -1):
            it = bridge.build_intent(direction=d, mark_price=self.GATE5C_MARK,
                                     enabled=True, best_bid=self.GATE5C_BID,
                                     best_ask=self.GATE5C_ASK, utc_day="2026-08-19")
            dec = _risk().validate(it, _account(mark_price=self.GATE5C_MARK), now=NOON)
            assert dec.approved, f"direction={d}: {dec.reason}"

    def test_risk_budget_after_correction(self):
        it = bridge.build_intent(direction=-1, mark_price=self.GATE5C_MARK,
                                 enabled=True, best_bid=self.GATE5C_BID,
                                 best_ask=self.GATE5C_ASK, utc_day="2026-08-19")
        risk = compute_trade_risk_usd(it, self.GATE5C_MARK)
        assert risk <= limits.MAX_TRADE_RISK_USD, risk
        assert risk > 0.9, "stop distance collapsed"

    def test_deviation_guard_still_applies_to_the_entry(self):
        """Fix 3 must not bypass any guard."""
        it = bridge.build_intent(direction=1, mark_price=self.GATE5C_MARK,
                                 enabled=True, best_bid=self.GATE5C_BID,
                                 best_ask=self.GATE5C_ASK, utc_day="2026-08-19")
        far = _risk().validate(it, _account(mark_price=self.GATE5C_MARK * 1.5),
                               now=NOON)
        assert not far.approved and "from mark" in far.reason

    # A deliberately WIDE spread: with the Gate 5C book the mark and the
    # executable price differ by 0.36, too little to distinguish "derived from
    # the book" from "derived from the mark". These separate them.
    WIDE_BID, WIDE_ASK, WIDE_MARK = 64000.0, 64500.0, 64250.0

    def test_build_intent_price_is_marketable_against_the_book(self):
        """The Gate 5C defect, asserted at the level that produced it."""
        sell = bridge.build_intent(direction=-1, mark_price=self.WIDE_MARK,
                                   enabled=True, best_bid=self.WIDE_BID,
                                   best_ask=self.WIDE_ASK, utc_day="2026-08-19")
        assert sell.limit_price <= self.WIDE_BID, "sell would rest, not fill"
        buy = bridge.build_intent(direction=1, mark_price=self.WIDE_MARK,
                                  enabled=True, best_bid=self.WIDE_BID,
                                  best_ask=self.WIDE_ASK, utc_day="2026-08-19")
        assert buy.limit_price >= self.WIDE_ASK, "buy would rest, not fill"

    def test_stop_derives_from_the_executable_price_not_the_mark(self):
        it = bridge.build_intent(direction=-1, mark_price=self.WIDE_MARK,
                                 enabled=True, best_bid=self.WIDE_BID,
                                 best_ask=self.WIDE_ASK, utc_day="2026-08-19")
        from_limit = it.limit_price * (1 + bridge.STOP_DISTANCE_PCT / 100)
        from_mark = self.WIDE_MARK * (1 + bridge.STOP_DISTANCE_PCT / 100)
        assert abs(it.stop_price - from_limit) < 0.2, it.stop_price
        assert abs(it.stop_price - from_mark) > 100,             "stop was derived from the mark, not the executed price"

    def test_intent_kind_remains_entry(self):
        it = bridge.build_intent(direction=1, mark_price=self.GATE5C_MARK,
                                 enabled=True, best_bid=self.GATE5C_BID,
                                 best_ask=self.GATE5C_ASK)
        assert it.intent_kind == "entry"
        assert it.size == limits.MAX_CONTRACTS_PER_ORDER


class TestStopDistanceCalibration:
    """5C.4: a fixed-% stop against a fixed-$ budget breaks as price rises.

    Gate 5C.3 measured it live: at BTC 69548 the 1.4% stop cost $0.9737 which,
    with $0.0821 of fees, was $1.0558 against a $1.00 budget. Every intent was
    correctly vetoed, so the strategy could not trade at all above ~$65,876.
    """

    # The exact book and mark from the Gate 5C.3 failure.
    G5C3_BID, G5C3_ASK, G5C3_MARK = 69548.0, 69549.0, 69551.80188522

    def _intent_at(self, direction, bid, ask, mark):
        return bridge.build_intent(direction=direction, mark_price=mark,
                                   enabled=True, best_bid=bid, best_ask=ask,
                                   utc_day="2026-08-20")

    # --- the regression that reproduces the failure ----------------------
    def test_gate_5c3_old_rule_would_exceed_the_budget(self):
        """The pre-fix behaviour, recomputed: proves the defect was real."""
        limit = 69548.0
        old_distance = limit * bridge.STOP_DISTANCE_PCT / 100.0
        old_risk = (old_distance * limits.BTCUSD_CONTRACT_VALUE
                    + round_trip_fees_usd(self.G5C3_MARK, 1))
        assert old_risk == pytest.approx(1.0558, abs=1e-3)
        assert old_risk > limits.MAX_TRADE_RISK_USD, "the defect no longer reproduces"

    def test_gate_5c3_book_now_produces_an_approved_intent(self):
        it = self._intent_at(-1, self.G5C3_BID, self.G5C3_ASK, self.G5C3_MARK)
        assert it.limit_price == self.G5C3_BID
        d = _risk().validate(it, _account(mark_price=self.G5C3_MARK), now=NOON)
        assert d.approved, d.reason
        assert compute_trade_risk_usd(it, self.G5C3_MARK) <= limits.MAX_TRADE_RISK_USD

    # --- invariant 1: RiskManager approves at every price ----------------
    @pytest.mark.parametrize("mark", [
        30000.0, 64187.5, 65000.0, 65876.0, 69551.80188522, 90000.0,
        120000.0, 250000.0,
    ])
    @pytest.mark.parametrize("direction", [1, -1])
    def test_risk_manager_approves_at_every_price_and_side(self, mark, direction):
        bid = round(round((mark - 1.0) / 0.1) * 0.1, 1)
        ask = round(round((mark + 1.0) / 0.1) * 0.1, 1)
        it = self._intent_at(direction, bid, ask, mark)
        d = _risk().validate(it, _account(mark_price=mark), now=NOON)
        assert d.approved, f"mark={mark} dir={direction}: {d.reason}"

    @pytest.mark.parametrize("mark", [64187.5, 69551.80188522, 120000.0, 250000.0])
    @pytest.mark.parametrize("direction", [1, -1])
    def test_risk_never_exceeds_the_budget(self, mark, direction):
        bid = round(round((mark - 1.0) / 0.1) * 0.1, 1)
        ask = round(round((mark + 1.0) / 0.1) * 0.1, 1)
        it = self._intent_at(direction, bid, ask, mark)
        risk = compute_trade_risk_usd(it, mark)
        assert risk <= limits.MAX_TRADE_RISK_USD, f"{risk} at mark {mark}"

    # --- invariant 2: stop on the correct side ---------------------------
    @pytest.mark.parametrize("mark", [30000.0, 64187.5, 69551.8, 120000.0])
    def test_stop_sits_on_the_correct_side(self, mark):
        bid = round(round((mark - 1.0) / 0.1) * 0.1, 1)
        ask = round(round((mark + 1.0) / 0.1) * 0.1, 1)
        long_it = self._intent_at(1, bid, ask, mark)
        short_it = self._intent_at(-1, bid, ask, mark)
        assert long_it.stop_price < long_it.limit_price
        assert short_it.stop_price > short_it.limit_price

    # --- invariant 3: never exceeds the strategy maximum -----------------
    @pytest.mark.parametrize("mark", [30000.0, 50000.0, 64187.5, 69551.8, 120000.0])
    def test_distance_never_exceeds_the_strategy_maximum(self, mark):
        bid = round(round((mark - 1.0) / 0.1) * 0.1, 1)
        ask = round(round((mark + 1.0) / 0.1) * 0.1, 1)
        for d in (1, -1):
            it = self._intent_at(d, bid, ask, mark)
            distance = abs(it.limit_price - it.stop_price)
            cap = it.limit_price * bridge.STOP_DISTANCE_PCT / 100.0
            assert distance <= cap + 1e-9, f"{distance} > cap {cap} at {mark}"

    def test_strategy_maximum_binds_at_low_prices(self):
        """Below the crossover the strategy's intent is preserved untouched."""
        mark = 64187.5
        it = self._intent_at(1, 64186.0, 64187.0, mark)
        distance = abs(it.limit_price - it.stop_price)
        cap = it.limit_price * bridge.STOP_DISTANCE_PCT / 100.0
        assert distance == pytest.approx(cap, abs=limits.BTCUSD_TICK_SIZE)

    def test_budget_binds_at_high_prices(self):
        """Above the crossover the budget tightens the stop."""
        mark = 120000.0
        it = self._intent_at(1, 119999.0, 120001.0, mark)
        distance = abs(it.limit_price - it.stop_price)
        cap = it.limit_price * bridge.STOP_DISTANCE_PCT / 100.0
        assert distance < cap * 0.75, "budget did not tighten the stop"

    # --- invariant 4: fees counted exactly once --------------------------
    def test_affordable_distance_matches_the_risk_identity(self):
        """Solved from compute_trade_risk_usd, so fees appear exactly once."""
        for mark in (64187.5, 69551.8, 120000.0):
            aff = bridge.affordable_stop_distance(mark, 1)
            reconstructed = (aff * limits.BTCUSD_CONTRACT_VALUE
                             + round_trip_fees_usd(mark, 1))
            assert reconstructed == pytest.approx(limits.MAX_TRADE_RISK_USD, abs=1e-9)

    def test_affordable_distance_uses_the_shared_fee_function(self):
        """Not a parallel formula: a fee-model change must move both sides."""
        import inspect
        src = inspect.getsource(bridge.affordable_stop_distance)
        assert "round_trip_fees_usd" in src
        assert "TAKER_FEE_RATE" not in src, "fee arithmetic was reimplemented"

    def test_fee_model_change_moves_the_distance(self, monkeypatch):
        before = bridge.affordable_stop_distance(64187.5, 1)
        monkeypatch.setattr(limits, "FEE_RESERVE_MULTIPLIER", 3.0)
        after = bridge.affordable_stop_distance(64187.5, 1)
        assert after < before, "distance ignored a fee-model change"

    # --- invariant 5: price still from the book --------------------------
    def test_executable_price_still_from_the_book(self):
        it_s = self._intent_at(-1, self.G5C3_BID, self.G5C3_ASK, self.G5C3_MARK)
        it_l = self._intent_at(1, self.G5C3_BID, self.G5C3_ASK, self.G5C3_MARK)
        assert it_s.limit_price == self.G5C3_BID
        assert it_l.limit_price == self.G5C3_ASK
        mark_derived = round(round(self.G5C3_MARK / 0.1) * 0.1, 1)
        assert it_s.limit_price != mark_derived

    def test_wide_spread_still_marketable_and_in_budget(self):
        bid, ask, mark = 69000.0, 70000.0, 69500.0
        s = self._intent_at(-1, bid, ask, mark)
        l = self._intent_at(1, bid, ask, mark)
        assert s.limit_price <= bid and l.limit_price >= ask
        for it in (s, l):
            assert _risk().validate(it, _account(mark_price=mark), now=NOON).approved

    # --- invariant 6: RiskManager remains the authority ------------------
    def test_a_deliberately_over_budget_intent_is_still_rejected(self):
        """The fix must not blunt the guard."""
        it = _intent(limit_price=64187.5, stop_price=60000.0)
        d = _risk().validate(it, _account(mark_price=64187.5), now=NOON)
        assert not d.approved
        assert "exceeds" in d.reason

    def test_hard_ceiling_still_enforced(self):
        it = _intent(limit_price=64187.5, stop_price=61000.0)
        d = _risk().validate(it, _account(mark_price=64187.5), now=NOON)
        assert not d.approved

    def test_raising_the_price_cannot_silently_exceed_the_budget(self):
        """The specific regression: a price rise must tighten, never overflow."""
        prev = None
        for mark in range(60000, 200001, 10000):
            mark = float(mark)
            bid = round(round((mark - 1.0) / 0.1) * 0.1, 1)
            ask = round(round((mark + 1.0) / 0.1) * 0.1, 1)
            it = self._intent_at(-1, bid, ask, mark)
            risk = compute_trade_risk_usd(it, mark)
            assert risk <= limits.MAX_TRADE_RISK_USD, f"mark {mark}: {risk}"
            assert _risk().validate(it, _account(mark_price=mark),
                                    now=NOON).approved, mark
            prev = risk

    # --- tick discipline --------------------------------------------------
    @pytest.mark.parametrize("mark", [64187.5, 69551.80188522, 87654.321, 120000.0])
    def test_stop_is_tick_aligned(self, mark):
        tick = limits.BTCUSD_TICK_SIZE
        bid = round(round((mark - 1.0) / 0.1) * 0.1, 1)
        ask = round(round((mark + 1.0) / 0.1) * 0.1, 1)
        for d in (1, -1):
            it = self._intent_at(d, bid, ask, mark)
            assert abs(round(it.stop_price / tick) * tick - it.stop_price) < 1e-6

    def test_distance_is_floored_never_rounded_up(self):
        """Rounding a distance UP is the one direction that breaks the budget."""
        tick = limits.BTCUSD_TICK_SIZE
        for mark in (69551.80188522, 87654.321, 111111.11):
            d = bridge.stop_distance(mark, mark, 1)
            assert d <= bridge.affordable_stop_distance(mark, 1) + 1e-9
            assert abs(round(d / tick) * tick - d) < 1e-6

    def test_unaffordable_budget_raises_rather_than_building_a_bad_intent(self,
                                                                         monkeypatch):
        """If fees ever consume the budget, refuse -- never emit a null stop."""
        monkeypatch.setattr(limits, "MAX_TRADE_RISK_USD", 0.01)
        with pytest.raises(ValueError):
            bridge.stop_distance(64187.5, 64187.5, 1)

    def test_intent_still_single_contract_entry(self):
        it = self._intent_at(-1, self.G5C3_BID, self.G5C3_ASK, self.G5C3_MARK)
        assert it.size == limits.MAX_CONTRACTS_PER_ORDER
        assert it.intent_kind == "entry"
        assert it.order_type == "limit_order"


class TestClientOrderIdLength:
    """5C.6: Delta refuses client_order_id longer than 32 characters.

    Authoritative evidence, Gate 5C.5:
        HTTP 400 error.code=bad_schema
        schema_errors: message="String length must be at most 32",
                       param="client_order_id"
    No restriction on underscores was established, and none is assumed.
    """

    # The id Delta actually refused, and the one it actually accepted.
    REJECTED = "g3_momentum_tsmom-2026-08-20-sell-1"   # 35
    ACCEPTED = "supervised-gate-20260818T084125Z"      # 32

    def test_the_rejected_id_was_over_the_limit(self):
        assert len(self.REJECTED) == 35
        assert len(self.REJECTED) > bridge.CLIENT_ORDER_ID_MAX_LEN

    def test_the_accepted_id_sat_exactly_on_the_boundary(self):
        """32 was ACCEPTED, so the bound is inclusive, not exclusive."""
        assert len(self.ACCEPTED) == 32
        assert len(self.ACCEPTED) == bridge.CLIENT_ORDER_ID_MAX_LEN

    def test_the_previously_failing_case_now_fits(self):
        coid = bridge.client_order_id(bridge.SIGNAL_ID, "2026-08-20", "sell", 1)
        assert len(coid) <= bridge.CLIENT_ORDER_ID_MAX_LEN
        assert len(coid) == 32

    def test_underscores_are_not_forbidden(self):
        """The constraint is length only; do not encode a phantom rule."""
        import inspect
        src = inspect.getsource(bridge.client_order_id) + \
            inspect.getsource(bridge._signal_token)
        assert "underscore" not in src.lower().replace("underscores", "")  # doc only

    # --- requirement 9: never exceed 32, across the whole domain --------
    @pytest.mark.parametrize("signal_id", [
        "g3_momentum_tsmom", "h5_vol_regime", "g1_market_regime_sma200",
        "ivrv_vrp_spread", "g3_atr_expansion", "x", "",
        "a" * 64, "a" * 200, "signal.with.dots-and-dashes_and_underscores",
    ])
    @pytest.mark.parametrize("side", ["buy", "sell"])
    @pytest.mark.parametrize("seq", [1, 9, 10, 99, 1000, 999999])
    def test_never_exceeds_the_limit(self, signal_id, side, seq):
        coid = bridge.client_order_id(signal_id, "2026-08-20", side, seq)
        assert len(coid) <= bridge.CLIENT_ORDER_ID_MAX_LEN, f"{coid} = {len(coid)}"

    def test_built_intent_id_is_within_the_limit(self):
        for direction in (1, -1):
            it = bridge.build_intent(direction=direction, mark_price=64187.5,
                                     enabled=True, best_bid=64186.0,
                                     best_ask=64187.0, utc_day="2026-08-20")
            assert len(it.client_order_id) <= bridge.CLIENT_ORDER_ID_MAX_LEN

    # --- requirements 4-7: every component stays distinguishing ---------
    def test_different_signals_do_not_collapse(self):
        a = bridge.client_order_id("g3_momentum_tsmom", "2026-08-20", "sell", 1)
        b = bridge.client_order_id("g3_momentum_tsmom_v2", "2026-08-20", "sell", 1)
        assert a != b, "two distinct signals produced the same id"

    def test_signals_sharing_every_truncatable_character_differ(self):
        """The precise case naive truncation gets wrong."""
        base = "a" * 40
        ids = {bridge.client_order_id(base + suffix, "2026-08-20", "sell", 1)
               for suffix in ("", "1", "2", "_v2", "_variant_b")}
        assert len(ids) == 5, ids

    def test_different_dates_remain_distinct(self):
        ids = {bridge.client_order_id(bridge.SIGNAL_ID, d, "sell", 1)
               for d in ("2026-08-19", "2026-08-20", "2026-08-21", "2027-08-20")}
        assert len(ids) == 4

    def test_different_sides_remain_distinct(self):
        a = bridge.client_order_id(bridge.SIGNAL_ID, "2026-08-20", "buy", 1)
        b = bridge.client_order_id(bridge.SIGNAL_ID, "2026-08-20", "sell", 1)
        assert a != b

    def test_different_sequences_remain_distinct(self):
        ids = {bridge.client_order_id(bridge.SIGNAL_ID, "2026-08-20", "sell", n)
               for n in (1, 2, 3, 10, 99)}
        assert len(ids) == 5

    # --- requirement 8: determinism / idempotency ------------------------
    def test_regeneration_is_identical(self):
        args = (bridge.SIGNAL_ID, "2026-08-20", "sell", 1)
        first = bridge.client_order_id(*args)
        for _ in range(50):
            assert bridge.client_order_id(*args) == first

    def test_determinism_across_a_fresh_interpreter(self):
        """No PYTHONHASHSEED dependence -- sha256, not hash()."""
        import subprocess, sys, pathlib
        code = ("import sys; sys.path.insert(0, r'%s');"
                "from src.strategies import bridge;"
                "print(bridge.client_order_id(bridge.SIGNAL_ID,'2026-08-20','sell',1))"
                % pathlib.Path.cwd())
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True, env={"PYTHONHASHSEED": "12345", **__import__("os").environ})
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == bridge.client_order_id(
            bridge.SIGNAL_ID, "2026-08-20", "sell", 1)

    def test_build_intent_id_is_idempotent(self):
        ids = {bridge.build_intent(direction=-1, mark_price=64187.5, enabled=True,
                                   best_bid=64186.0, best_ask=64187.0,
                                   utc_day="2026-08-20").client_order_id
               for _ in range(10)}
        assert len(ids) == 1

    # --- collision safety over the real generation domain ---------------
    def test_no_collisions_across_the_real_domain(self):
        """Every signal the platform actually collects x dates x sides x seqs."""
        signals = ["g3_momentum_tsmom", "h5_vol_regime", "g1_market_regime_sma200",
                   "ivrv_vrp_spread", "g3_atr_expansion"]
        seen = {}
        import datetime
        day = datetime.date(2026, 1, 1)
        for _ in range(400):                      # >13 months of dates
            for sig in signals:
                for side in ("buy", "sell"):
                    for seq in (1, 2, 3):
                        key = (sig, day.isoformat(), side, seq)
                        coid = bridge.client_order_id(*key)
                        assert len(coid) <= 32
                        if coid in seen:
                            assert seen[coid] == key, f"COLLISION {coid}: {seen[coid]} vs {key}"
                        seen[coid] = key
            day += datetime.timedelta(days=1)
        assert len(seen) == 400 * len(signals) * 2 * 3

    # --- traceability retained -------------------------------------------
    def test_id_retains_day_side_and_sequence_verbatim(self):
        coid = bridge.client_order_id(bridge.SIGNAL_ID, "2026-08-20", "sell", 7)
        assert "-20260820-" in coid
        assert coid.endswith("-s7")

    def test_buy_side_initial(self):
        assert bridge.client_order_id(bridge.SIGNAL_ID, "2026-08-20", "buy", 1).endswith("-b1")

    def test_signal_prefix_is_recognisable(self):
        coid = bridge.client_order_id("g3_momentum_tsmom", "2026-08-20", "sell", 1)
        assert coid.startswith("g3momentum")

    # --- requirement 10: nothing else changed ----------------------------
    def test_protective_coid_scheme_is_untouched(self):
        assert protection.protective_coid(1, 1) == "stop-p1-q1"
        assert len(protection.protective_coid(999999, 999)) <= 32

    def test_protective_ids_also_fit_the_limit(self):
        for pid in (1, 42, 999999):
            for size in (1, 10, 999):
                assert len(protection.protective_coid(pid, size)) <= \
                    bridge.CLIENT_ORDER_ID_MAX_LEN

    def test_executor_transmits_the_intent_id_unchanged(self, conn):
        captured = {}

        class Capturing(_FakeClient):
            def place_order(self, **kw):
                captured.update(kw)
                return {"success": True, "result": {"id": 31}}

        it = bridge.build_intent(direction=-1, mark_price=64187.5, enabled=True,
                                 best_bid=64186.0, best_ask=64187.0,
                                 utc_day="2026-08-20")
        ex = TestnetExecutor(conn, Capturing(), _risk())
        ex.submit(it, _account(mark_price=64187.5), now=NOON)
        assert captured["client_order_id"] == it.client_order_id
        assert len(captured["client_order_id"]) <= 32


# ---------------------------------------------------------------------------
# 1g - Connection ownership (Gate 5C.7)
# ---------------------------------------------------------------------------

_INSTRUMENT_ROW = (
    "INSERT OR REPLACE INTO instruments "
    "(id, symbol, contract_type, underlying, quoting, tick_size, "
    " contract_value, state, fetched_at) "
    "VALUES (?,?,'call_options','BTC','USD',0.1,0.001,'live','t')"
)


def _exec_db(path):
    """A database with both schemas and BTCUSD present, like production."""
    c = storage.get_connection(path)
    storage.init_schema(c)
    storage.init_execution_schema(c)
    c.execute(
        "INSERT OR REPLACE INTO instruments "
        "(id, symbol, contract_type, underlying, quoting, tick_size, "
        " contract_value, state, fetched_at) "
        "VALUES (84,'BTCUSD','perpetual_futures','BTC','USD',0.1,0.001,'live','t')"
    )
    c.commit()
    return c


class _ExplodingConn:
    def close(self):
        raise sqlite3.OperationalError("close failed")


class TestSharedConnectionReproduction:
    """Gate 5C.7: the exact production halt, reproduced and then prevented.

        2026-08-20T11:16:06Z CRITICAL [reconcile] HALT: fill ingestion failed:
            OperationalError: cannot start a transaction within a transaction

    The hourly chain refresh was mid-write on the connection the reconciler was
    also using when `ingest_fills` issued its BEGIN IMMEDIATE. A SQLite
    transaction belongs to the connection, so "the other thread's write" and
    "my transaction" were the same transaction.
    """

    def test_one_connection_reproduces_the_halt_verbatim(self, tmp_path):
        c = _exec_db(tmp_path / "shared.db")
        c.execute(_INSTRUMENT_ROW, (85, "C-BTC-71000-200826"))   # refresher, uncommitted
        with pytest.raises(sqlite3.OperationalError) as exc:
            fills.ingest_fills(c, [_fill("f1", "buy")])          # reconciler
        assert "cannot start a transaction within a transaction" in str(exc.value)
        c.close()

    def test_two_connections_do_not_collide(self, tmp_path):
        """The production shape: refresh_chain() upserts and commits its batch."""
        path = tmp_path / "split.db"
        chain = _exec_db(path)
        execution = storage.get_connection(path)
        chain.execute(_INSTRUMENT_ROW, (85, "C-BTC-71000-200826"))
        chain.commit()
        fills.ingest_fills(execution, [_fill("f1", "buy")])
        assert fills.position_size(execution) == 1
        assert chain.execute(
            "SELECT COUNT(*) FROM instruments WHERE id = 85").fetchone()[0] == 1
        chain.close()
        execution.close()

    def test_an_uncommitted_writer_is_serialised_never_contaminated(self, tmp_path):
        """The boundary separate connections replace the old one with.

        Holding a write transaction open past the busy timeout now yields
        `database is locked` -- SQLite refusing to interleave two writers -- and
        NOT `cannot start a transaction within a transaction`. The distinction
        matters: the first is the database enforcing isolation and leaves both
        components' rows intact; the second was two components sharing one
        transaction. Production writes commit in well under a second, so this
        path needs a writer that stalls, which is why it is provoked here rather
        than waited for.
        """
        path = tmp_path / "locked.db"
        chain = _exec_db(path)
        execution = storage.get_connection(path)
        execution.execute("PRAGMA busy_timeout = 200")

        chain.execute(_INSTRUMENT_ROW, (85, "C-BTC-71000-200826"))   # not committed
        with pytest.raises(sqlite3.OperationalError) as exc:
            fills.ingest_fills(execution, [_fill("f1", "buy")])
        assert "database is locked" in str(exc.value)
        assert "within a transaction" not in str(exc.value)

        chain.commit()
        fills.ingest_fills(execution, [_fill("f1", "buy")])
        assert fills.position_size(execution) == 1
        assert chain.execute(
            "SELECT COUNT(*) FROM instruments WHERE id = 85").fetchone()[0] == 1
        chain.close()
        execution.close()

    def test_collision_happens_across_real_threads(self, tmp_path):
        """The production shape: the refresher's write is on another thread."""
        c = _exec_db(tmp_path / "threads.db")
        opened, release = threading.Event(), threading.Event()
        errors = []

        def refresher():
            try:
                c.execute(_INSTRUMENT_ROW, (85, "C-BTC-71000-200826"))
                opened.set()
                release.wait(5)
                c.commit()
            except Exception as exc:                    # pragma: no cover
                errors.append(exc)

        t = threading.Thread(target=refresher)
        t.start()
        assert opened.wait(5)
        with pytest.raises(sqlite3.OperationalError) as exc:
            fills.ingest_fills(c, [_fill("f1", "buy")])
        assert "within a transaction" in str(exc.value)
        release.set()
        t.join(5)
        assert errors == []
        c.close()

    def test_separate_connections_survive_the_same_race(self, tmp_path):
        path = tmp_path / "threads_ok.db"
        chain = _exec_db(path)
        execution = storage.get_connection(path)
        opened, release = threading.Event(), threading.Event()
        errors = []

        def refresher():
            try:
                release.wait(5)
                chain.execute(_INSTRUMENT_ROW, (85, "C-BTC-71000-200826"))
                chain.commit()
                opened.set()
            except Exception as exc:                    # pragma: no cover
                errors.append(exc)

        t = threading.Thread(target=refresher)
        t.start()
        release.set()
        fills.ingest_fills(execution, [_fill("f1", "buy")])
        assert opened.wait(5)
        t.join(5)
        assert errors == []
        assert fills.position_size(execution) == 1
        assert chain.execute(
            "SELECT COUNT(*) FROM instruments WHERE id = 85").fetchone()[0] == 1
        chain.close()
        execution.close()

    def test_sustained_concurrency_on_separate_connections_is_clean(self, tmp_path):
        """20 interleaved refresh/ingest rounds -- no errors, everything durable."""
        path = tmp_path / "soak.db"
        chain = _exec_db(path)
        execution = storage.get_connection(path)
        errors = []
        rounds = 20

        def refresher():
            try:
                for i in range(rounds):
                    chain.execute(_INSTRUMENT_ROW, (1000 + i, "C-BTC-%d-200826" % (1000 + i)))
                    chain.commit()
            except Exception as exc:
                errors.append(("chain", exc))

        def reconciler():
            try:
                for i in range(rounds):
                    fills.ingest_fills(execution, [_fill("f%d" % i, "buy")])
            except Exception as exc:
                errors.append(("execution", exc))

        threads = [threading.Thread(target=refresher), threading.Thread(target=reconciler)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        assert errors == [], errors
        assert chain.execute(
            "SELECT COUNT(*) FROM instruments WHERE id >= 1000").fetchone()[0] == rounds
        assert execution.execute(
            "SELECT COUNT(*) FROM order_fills").fetchone()[0] == rounds
        chain.close()
        execution.close()


class TestCrossThreadTransactionContamination:
    """A shared handle does not merely raise -- it silently corrupts.

    These failure modes are worse than the halt, because nothing logs them.
    """

    def test_shared_connection_lets_a_rollback_discard_another_components_rows(
            self, tmp_path):
        c = _exec_db(tmp_path / "rollback.db")
        c.execute(_INSTRUMENT_ROW, (85, "C-BTC-71000-200826"))   # component A
        c.rollback()                                             # component B fails
        assert c.execute(
            "SELECT COUNT(*) FROM instruments WHERE id = 85").fetchone()[0] == 0
        c.close()

    def test_separate_connections_confine_a_rollback_to_its_owner(self, tmp_path):
        path = tmp_path / "rollback_ok.db"
        chain = _exec_db(path)
        other = storage.get_connection(path)
        chain.execute(_INSTRUMENT_ROW, (85, "C-BTC-71000-200826"))
        chain.commit()
        other.execute(_INSTRUMENT_ROW, (86, "C-BTC-71200-200826"))
        other.rollback()
        assert chain.execute(
            "SELECT COUNT(*) FROM instruments WHERE id = 85").fetchone()[0] == 1
        assert chain.execute(
            "SELECT COUNT(*) FROM instruments WHERE id = 86").fetchone()[0] == 0
        chain.close()
        other.close()

    def test_shared_connection_lets_a_commit_publish_a_half_written_batch(
            self, tmp_path):
        """Component A is mid-batch; component B commits; A's partial batch lands."""
        c = _exec_db(tmp_path / "commit.db")
        c.execute(_INSTRUMENT_ROW, (85, "C-BTC-71000-200826"))   # A, batch incomplete
        c.commit()                                               # B, unrelated commit
        assert c.execute(
            "SELECT COUNT(*) FROM instruments WHERE id = 85").fetchone()[0] == 1
        c.close()

    def test_ingest_fills_still_rolls_back_atomically_on_its_own_connection(
            self, tmp_path):
        """Transaction semantics preserved, not merely isolated."""
        c = _exec_db(tmp_path / "atomic.db")
        with pytest.raises(Exception):
            fills.ingest_fills(c, [_fill("f1", "buy"), {"garbage": True}])
        assert c.execute("SELECT COUNT(*) FROM order_fills").fetchone()[0] == 0
        assert fills.position_size(c) == 0
        fills.ingest_fills(c, [_fill("f1", "buy")])
        assert fills.position_size(c) == 1
        c.close()

    def test_begin_immediate_is_retained_by_fill_ingestion(self):
        """The authoritative write lock must not be softened by this fix."""
        src = pathlib.Path("src/execution/fills.py").read_text(encoding="utf-8")
        assert 'conn.execute("BEGIN IMMEDIATE")' in src

    def test_risk_state_begin_immediate_is_retained(self):
        src = pathlib.Path("src/risk/state.py").read_text(encoding="utf-8")
        assert 'conn.execute("BEGIN IMMEDIATE")' in src


class TestAssertDistinctConnections:
    """The boot-time guard. Wiring mistakes must cost a refused start."""

    def test_distinct_connections_pass(self, tmp_path):
        a = storage.get_connection(tmp_path / "d.db")
        b = storage.get_connection(tmp_path / "d.db")
        storage.assert_distinct_connections(chain_refresher=a, snapshot_writer=b)
        a.close()
        b.close()

    def test_two_handles_to_one_file_are_the_intended_arrangement(self, tmp_path):
        """Identity, not the database path, is what must differ."""
        path = tmp_path / "same.db"
        a = storage.get_connection(path)
        b = storage.get_connection(path)
        assert a is not b
        storage.assert_distinct_connections(a=a, b=b)
        a.close()
        b.close()

    def test_a_shared_connection_is_refused(self, tmp_path):
        c = storage.get_connection(tmp_path / "s.db")
        with pytest.raises(storage.SharedConnectionError) as exc:
            storage.assert_distinct_connections(chain_refresher=c, execution=c)
        assert "chain_refresher" in str(exc.value)
        assert "execution" in str(exc.value)
        c.close()

    def test_the_error_names_every_sharing_component(self, tmp_path):
        c = storage.get_connection(tmp_path / "s3.db")
        other = storage.get_connection(tmp_path / "s3.db")
        with pytest.raises(storage.SharedConnectionError) as exc:
            storage.assert_distinct_connections(
                chain_refresher=c, snapshot_writer=other, execution=c, risk_state=c)
        msg = str(exc.value)
        for name in ("chain_refresher", "execution", "risk_state"):
            assert name in msg
        c.close()
        other.close()

    def test_no_components_is_not_an_error(self):
        storage.assert_distinct_connections()

    def test_one_component_is_not_an_error(self, tmp_path):
        c = storage.get_connection(tmp_path / "one.db")
        storage.assert_distinct_connections(only=c)
        c.close()


class TestRuntimeConnectionOwnership:
    """Lifecycle owns every handle: opens it, names it, and closes it."""

    def _runtime(self, tmp_path):
        from src.runtime.lifecycle import Runtime
        rt = Runtime.__new__(Runtime)
        rt._db_path = tmp_path / "own.db"
        rt._owned_conns = []
        rt._conn = None
        rt._risk_conn = None
        return rt

    def test_each_opened_connection_is_distinct(self, tmp_path):
        rt = self._runtime(tmp_path)
        a = rt._open_owned_connection("chain_refresher")
        b = rt._open_owned_connection("snapshot_writer")
        c = rt._open_owned_connection("execution")
        d = rt._open_owned_connection("risk_state")
        assert len({id(a), id(b), id(c), id(d)}) == 4
        storage.assert_distinct_connections(
            chain_refresher=a, snapshot_writer=b, execution=c, risk_state=d)
        rt._close_db()

    def test_labels_are_recorded_in_open_order(self, tmp_path):
        rt = self._runtime(tmp_path)
        for label in ("chain_refresher", "snapshot_writer", "execution"):
            rt._open_owned_connection(label)
        assert [n for n, _ in rt._owned_conns] == [
            "chain_refresher", "snapshot_writer", "execution"]
        rt._close_db()

    def test_close_db_closes_every_owned_connection(self, tmp_path):
        rt = self._runtime(tmp_path)
        conns = [rt._open_owned_connection(n) for n in ("a", "b", "c")]
        rt._close_db()
        for c in conns:
            with pytest.raises(sqlite3.ProgrammingError):
                c.execute("SELECT 1")
        assert rt._owned_conns == []

    def test_close_db_is_idempotent(self, tmp_path):
        rt = self._runtime(tmp_path)
        rt._open_owned_connection("a")
        rt._close_db()
        rt._close_db()
        assert rt._owned_conns == [] and rt._risk_conn is None

    def test_close_db_continues_past_a_failing_close(self, tmp_path):
        """One bad handle must not strand the others open."""
        rt = self._runtime(tmp_path)
        good = rt._open_owned_connection("good")
        rt._owned_conns.insert(0, ("exploding", _ExplodingConn()))
        rt._close_db()
        with pytest.raises(sqlite3.ProgrammingError):
            good.execute("SELECT 1")
        assert rt._owned_conns == []

    def test_close_db_clears_the_risk_connection_reference(self, tmp_path):
        rt = self._runtime(tmp_path)
        rt._risk_conn = rt._open_owned_connection("risk_state")
        rt._close_db()
        assert rt._risk_conn is None


class TestLifecycleConnectionWiring:
    """Source-level guards: the defect was invisible to behaviour tests.

    Nothing in the collector's observable behaviour changed when the reconciler
    was handed the collector's connection -- it took an hourly job overlapping a
    minute job to surface it. These pin the wiring itself.
    """

    def _src(self):
        return pathlib.Path("src/runtime/lifecycle.py").read_text(encoding="utf-8")

    def test_chain_refresher_receives_its_own_connection(self):
        src = self._src()
        assert "conn=chain_conn" in src
        assert "            conn=conn," not in src

    def test_snapshot_writer_receives_its_own_connection(self):
        src = self._src()
        assert "SnapshotWriter(conn=snapshot_conn" in src
        assert "SnapshotWriter(conn=conn" not in src

    def test_reconciler_receives_its_own_connection(self):
        src = self._src()
        assert "Reconciler(exec_conn, client" in src
        assert "Reconciler(conn, client" not in src

    def test_executor_receives_the_execution_connection(self):
        src = self._src()
        assert "TestnetExecutor(exec_conn, client" in src
        assert "TestnetExecutor(conn, client" not in src

    def test_execution_schema_is_initialised_on_the_execution_connection(self):
        assert "init_execution_schema(exec_conn)" in self._src()

    def test_risk_state_keeps_its_own_connection(self):
        src = self._src()
        assert 'risk_state_conn = self._open_owned_connection("risk_state")' in src
        assert "RiskStateStore(risk_state_conn)" in src
        assert "RiskStateStore(exec_conn)" not in src

    def test_the_guard_runs_before_the_first_begin_immediate(self):
        src = self._src()
        assert src.index("assert_distinct_connections(") < src.index("reconciler.start()")

    def test_the_guard_covers_all_four_writers(self):
        src = self._src()
        block = src[src.index("            assert_distinct_connections("):]
        block = block[:block.index(")")]
        for name in ("chain_refresher", "snapshot_writer", "execution", "risk_state"):
            assert name in block

    def test_the_guard_asserts_on_components_not_on_local_variables(self):
        """Handing a component the wrong (still distinct) handle must be caught."""
        src = self._src()
        block = src[src.index("            assert_distinct_connections("):]
        block = block[:block.index(")")]
        for expr in ("refresher.connection", "snapshot_writer.connection",
                     "reconciler.connection"):
            assert expr in block

    def test_the_executor_sharing_the_reconcilers_handle_is_asserted(self):
        src = self._src()
        assert "if executor.connection is not reconciler.connection:" in src
        assert src.index("executor.connection is not reconciler.connection") <             src.index("reconciler.start()")

    def test_components_expose_the_connection_they_hold(self, conn):
        """The guard needs a fact, not a promise."""
        r = Reconciler(conn, _FakeReconcileClient(), product_id=84)
        ex = TestnetExecutor(conn, _FakeReconcileClient(), _risk())
        assert r.connection is conn
        assert ex.connection is conn

    def test_snapshot_writer_and_chain_refresher_expose_their_connection(self, conn):
        from src.ingestion.chain_refresher import ChainRefresher
        from src.persistence.snapshot_writer import SnapshotWriter
        from src.state.market_state import MarketState
        cr = ChainRefresher(client=_NoNetworkClient(), conn=conn)
        sw = SnapshotWriter(conn=conn, state=MarketState(universe=["BTCUSD"]))
        assert cr.connection is conn
        assert sw.connection is conn

    def test_the_collector_pair_is_guarded_even_with_trading_disarmed(self):
        """The chain/snapshot collision needs no execution stack to happen."""
        src = self._src()
        first = src.index("assert_distinct_connections(")
        gate = src.index("if self._demo_trading_enabled:")
        assert first < gate

    def test_exactly_one_reconciler_and_one_executor_are_built(self):
        src = self._src()
        assert src.count("TestnetExecutor(") == 1
        assert src.count("Reconciler(exec_conn, client") == 1

    def test_every_concurrent_component_uses_the_ownership_helper(self):
        src = self._src()
        for label in ("chain_refresher", "snapshot_writer", "execution", "risk_state"):
            assert 'self._open_owned_connection("%s")' % label in src

    def test_only_the_schema_connection_is_opened_outside_the_helper(self):
        src = self._src()
        assert src.count("get_connection(self._db_path)") == 2


# ---------------------------------------------------------------------------
# 1h - Realised P&L accounting and fill->order linkage (Gate 5C.10)
# ---------------------------------------------------------------------------

#: The real Gate 5C.8 round trip, from Delta's own fill payloads.
G5C8_ENTRY_PRICE = 72797.0
G5C8_ENTRY_FEE = 0.04295023
G5C8_EXIT_PRICE = 72728.0
G5C8_EXIT_FEE = 0.01716381
G5C8_GROSS = 0.069                       # (72797-72728) * 0.001 * 1, short
G5C8_WALLET_DELTA = 0.00888596           # 200.00000000 -> 200.00888596
G5C8_OLD_STORED = 0.05183619             # what the defect recorded


def _order(conn, coid, delta_order_id, side="sell", price=72797.0, status="acked"):
    storage.write_order(
        conn, client_order_id=coid, instrument_id=84, side=side,
        order_type="limit_order", quantity=1.0, price=price, status=status,
        strategy="g3_momentum_tsmom", placed_ts="2026-08-20T20:43:11+00:00",
        delta_order_id=delta_order_id)
    conn.commit()


def _dfill(fid, side, price, fee, *, oid=None, coid=None, size=1):
    """A fill shaped like Delta's: client_order_id is null, order_id is not."""
    return {"id": fid, "client_order_id": coid, "order_id": oid, "product_id": 84,
            "side": side, "size": size, "price": price, "commission": fee,
            "created_at": "2026-08-20T20:43:12.235519Z"}


class TestRealisedPnlIncludesBothFees:
    """Gate 5C.8 recorded +0.05183619 while the wallet moved +0.00888596.

    The difference was exactly the entry commission, which the ingestor never
    subtracted. A loss understated by its entry fee makes MAX_DAILY_LOSS_USD
    permissive, so this is a risk defect, not only a bookkeeping one.
    """

    def _round_trip(self, conn, entry_fee=G5C8_ENTRY_FEE, exit_fee=G5C8_EXIT_FEE,
                    entry=G5C8_ENTRY_PRICE, exit_=G5C8_EXIT_PRICE):
        fills.ingest_fills(conn, [_dfill("e1", "sell", entry, entry_fee)])
        fills.ingest_fills(conn, [_dfill("x1", "buy", exit_, exit_fee)])
        return conn.execute(
            "SELECT realised_pnl FROM order_fills WHERE fill_id = 'x1'").fetchone()[0]

    def test_both_fees_are_subtracted(self, conn):
        realised = self._round_trip(conn)
        assert realised == pytest.approx(
            G5C8_GROSS - G5C8_ENTRY_FEE - G5C8_EXIT_FEE, abs=1e-9)

    def test_matches_the_real_wallet_delta(self, conn):
        """The exchange is the authority: 200.00000000 -> 200.00888596."""
        assert self._round_trip(conn) == pytest.approx(G5C8_WALLET_DELTA, abs=1e-8)

    def test_is_no_longer_the_defective_value(self, conn):
        realised = self._round_trip(conn)
        assert realised != pytest.approx(G5C8_OLD_STORED, abs=1e-8)
        assert G5C8_OLD_STORED - realised == pytest.approx(G5C8_ENTRY_FEE, abs=1e-9)

    def test_a_loss_is_more_negative_by_the_entry_fee(self, conn):
        """The direction that matters: losses must not shrink."""
        realised = self._round_trip(conn, entry=72728.0, exit_=72797.0)
        gross = -G5C8_GROSS
        assert realised == pytest.approx(gross - G5C8_ENTRY_FEE - G5C8_EXIT_FEE, abs=1e-9)
        assert realised < gross - G5C8_EXIT_FEE          # strictly worse than before
        assert realised < 0

    def test_zero_fees_leave_the_price_arithmetic_untouched(self, conn):
        assert self._round_trip(conn, entry_fee=0.0, exit_fee=0.0) == \
            pytest.approx(G5C8_GROSS, abs=1e-9)

    def test_long_side_is_symmetric(self, conn):
        fills.ingest_fills(conn, [_dfill("e1", "buy", 70000.0, 0.03)])
        fills.ingest_fills(conn, [_dfill("x1", "sell", 71000.0, 0.02)])
        realised = conn.execute(
            "SELECT realised_pnl FROM order_fills WHERE fill_id='x1'").fetchone()[0]
        assert realised == pytest.approx(1000 * 0.001 - 0.03 - 0.02, abs=1e-9)

    def test_the_corrected_value_reaches_position_risk_ledger_and_state(self, tmp_path):
        """One number, four stores -- they must not disagree again."""
        c = _exec_db(tmp_path / "flow.db")
        risk_state = RiskStateStore(c)
        now = datetime(2026, 8, 20, 20, 45, tzinfo=timezone.utc)
        fills.ingest_fills(c, [_dfill("e1", "sell", G5C8_ENTRY_PRICE, G5C8_ENTRY_FEE)],
                           risk_state=risk_state, now=now)
        res = fills.ingest_fills(c, [_dfill("x1", "buy", G5C8_EXIT_PRICE, G5C8_EXIT_FEE)],
                                 risk_state=risk_state, now=now)
        expected = G5C8_WALLET_DELTA
        assert res.realised_pnl == pytest.approx(expected, abs=1e-8)
        assert c.execute("SELECT realised_pnl FROM order_fills WHERE fill_id='x1'"
                         ).fetchone()[0] == pytest.approx(expected, abs=1e-8)
        assert c.execute("SELECT pnl FROM positions ORDER BY id DESC LIMIT 1"
                         ).fetchone()[0] == pytest.approx(expected, abs=1e-8)
        assert c.execute("SELECT amount FROM risk_day_events WHERE kind='pnl'"
                         ).fetchone()[0] == pytest.approx(expected, abs=1e-8)
        assert risk_state.state(now).realised_pnl == pytest.approx(expected, abs=1e-8)
        c.close()

    def test_the_fee_model_is_not_reimplemented(self):
        """Fees are subtracted in exactly one place: realised_pnl_usd.

        Scans executable code only -- a docstring may legitimately explain why
        round_trip_fees_usd is the wrong tool here without calling it.
        """
        src = pathlib.Path("src/execution/fills.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        called = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        called |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        assert "round_trip_fees_usd" not in called, \
            "the pre-trade fee ESTIMATE must not enter the realised ledger"
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "realised_pnl_usd")
        subs = [n for n in ast.walk(fn) if isinstance(n, ast.BinOp)
                and isinstance(n.op, ast.Sub)]
        assert subs, "realised_pnl_usd no longer subtracts fees"

    def test_entry_fee_is_attributed_once_across_partial_exits(self, conn):
        """Two half-exits must consume the whole entry fee, never twice."""
        fills.ingest_fills(conn, [_dfill("e1", "sell", 72797.0, 0.04, size=2)])
        fills.ingest_fills(conn, [_dfill("x1", "buy", 72728.0, 0.01, size=1)])
        fills.ingest_fills(conn, [_dfill("x2", "buy", 72728.0, 0.01, size=1)])
        rows = conn.execute(
            "SELECT fill_id, realised_pnl FROM order_fills WHERE role='exit' "
            "ORDER BY rowid").fetchall()
        total = sum(r[1] for r in rows)
        gross = (72797.0 - 72728.0) * 0.001 * 2
        assert total == pytest.approx(gross - 0.04 - 0.02, abs=1e-9)
        assert rows[0][1] == pytest.approx(rows[1][1], abs=1e-9)


class TestCycleScoping:
    """A second round trip must not inherit the first one's entry.

    Found while implementing the fee fix: `_weighted_entry_price` read every
    `role='entry'` row ever written, so after one short at 72797 was covered, a
    fresh short at 80000 averaged to 76398.5 and a +1.00 winner was recorded as
    a -2.60 loss. The fee fix is worthless without this -- the entry fee would
    have been drawn from the same contaminated set.
    """

    def _two_cycles(self, conn):
        fills.ingest_fills(conn, [_dfill("a1", "sell", 72797.0, G5C8_ENTRY_FEE)])
        fills.ingest_fills(conn, [_dfill("a2", "buy", 72728.0, G5C8_EXIT_FEE)])
        fills.ingest_fills(conn, [_dfill("b1", "sell", 80000.0, 0.0)])
        fills.ingest_fills(conn, [_dfill("b2", "buy", 79000.0, 0.0)])

    def test_second_cycle_entry_price_excludes_the_first(self, conn):
        fills.ingest_fills(conn, [_dfill("a1", "sell", 72797.0, 0.0)])
        fills.ingest_fills(conn, [_dfill("a2", "buy", 72728.0, 0.0)])
        fills.ingest_fills(conn, [_dfill("b1", "sell", 80000.0, 0.0)])
        assert fills._weighted_entry_price(conn) == pytest.approx(80000.0)

    def test_second_cycle_pnl_is_its_own(self, conn):
        self._two_cycles(conn)
        realised = conn.execute(
            "SELECT realised_pnl FROM order_fills WHERE fill_id='b2'").fetchone()[0]
        assert realised == pytest.approx(1000 * 0.001, abs=1e-9)

    def test_the_defect_would_have_inverted_the_sign(self, conn):
        """Guards the magnitude, not just the value: -2.60 vs +1.00."""
        self._two_cycles(conn)
        realised = conn.execute(
            "SELECT realised_pnl FROM order_fills WHERE fill_id='b2'").fetchone()[0]
        assert realised > 0, "a winning cycle must not be recorded as a loss"
        assert realised == pytest.approx(1.0, abs=1e-9)

    def test_entry_fee_does_not_leak_across_cycles(self, conn):
        self._two_cycles(conn)
        realised = conn.execute(
            "SELECT realised_pnl FROM order_fills WHERE fill_id='b2'").fetchone()[0]
        assert realised == pytest.approx(1.0, abs=1e-9), \
            "cycle 1's entry fee was charged to cycle 2"

    def test_closed_position_pnl_is_per_cycle(self, conn):
        self._two_cycles(conn)
        rows = conn.execute(
            "SELECT pnl FROM positions ORDER BY id").fetchall()
        assert rows[-1][0] == pytest.approx(1.0, abs=1e-9)

    def test_open_cycle_is_used_while_exposure_is_live(self, conn):
        fills.ingest_fills(conn, [_dfill("e1", "sell", 72000.0, 0.0, size=1)])
        fills.ingest_fills(conn, [_dfill("e2", "sell", 74000.0, 0.0, size=1)])
        assert fills._weighted_entry_price(conn) == pytest.approx(73000.0)
        assert fills.position_size(conn) == -2


class TestFillOrderLinkage:
    """Delta's /v2/fills returns client_order_id=null; order_id is authoritative."""

    def test_delta_order_id_is_the_primary_key(self, conn):
        _order(conn, "coid-A", "2169700358")
        fills.ingest_fills(conn, [_dfill("f1", "sell", 72797.0, 0.04,
                                         oid="2169700358")])
        row = conn.execute("SELECT order_id FROM trades").fetchone()
        assert row is not None, "no trade row created from the native order id"
        assert row[0] == conn.execute(
            "SELECT id FROM orders WHERE client_order_id='coid-A'").fetchone()[0]

    def test_null_client_order_id_does_not_block_a_trade(self, conn):
        """The exact Gate 5C.8 payload shape."""
        _order(conn, "g3momentumtsmo3ad48e-20260820-s1", "2169700358")
        f = _dfill("693dee267ab4445d9c9ad27ac0b96e9e", "sell",
                   G5C8_ENTRY_PRICE, G5C8_ENTRY_FEE, oid="2169700358")
        assert f["client_order_id"] is None
        fills.ingest_fills(conn, [f])
        assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 1

    def test_both_gate_5c8_fills_link(self, conn):
        _order(conn, "g3momentumtsmo3ad48e-20260820-s1", "2169700358", side="sell")
        _order(conn, "exit-p1-20260820-b1", "2169700561", side="buy",
               price=G5C8_EXIT_PRICE)
        fills.ingest_fills(conn, [_dfill("693dee267ab4445d9c9ad27ac0b96e9e", "sell",
                                         G5C8_ENTRY_PRICE, G5C8_ENTRY_FEE,
                                         oid="2169700358")])
        fills.ingest_fills(conn, [_dfill("a0c586107e764feda525b9a35200756f", "buy",
                                         G5C8_EXIT_PRICE, G5C8_EXIT_FEE,
                                         oid="2169700561")])
        rows = [tuple(r) for r in conn.execute(
            "SELECT t.side, o.client_order_id FROM trades t "
            "JOIN orders o ON o.id = t.order_id ORDER BY t.id").fetchall()]
        assert rows == [("sell", "g3momentumtsmo3ad48e-20260820-s1"),
                        ("buy", "exit-p1-20260820-b1")]

    def test_client_order_id_fallback_when_no_native_id(self, conn):
        """The UNKNOWN-submission case: an order row exists, no exchange id yet."""
        _order(conn, "coid-B", None)
        fills.ingest_fills(conn, [_dfill("f1", "sell", 72797.0, 0.04,
                                         oid=None, coid="coid-B")])
        assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 1

    def test_unmatched_native_id_fails_closed_and_does_not_fall_back(self, conn):
        """A coid that WOULD match must not rescue an unknown native id."""
        _order(conn, "coid-C", "111")
        fills.ingest_fills(conn, [_dfill("f1", "sell", 72797.0, 0.04,
                                         oid="999", coid="coid-C")])
        assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM order_fills").fetchone()[0] == 1

    def test_ambiguous_native_id_fails_closed(self, conn):
        _order(conn, "coid-D", "777")
        _order(conn, "coid-E", "777", side="buy")
        fills.ingest_fills(conn, [_dfill("f1", "sell", 72797.0, 0.04, oid="777")])
        assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0

    def test_fill_with_no_identifier_at_all_fails_closed(self, conn):
        fills.ingest_fills(conn, [_dfill("f1", "sell", 72797.0, 0.04)])
        assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0
        assert fills.position_size(conn) == -1        # the fill is still recorded

    def test_rejected_orders_are_unaffected(self, conn):
        """delta_order_id IS NULL must never be matched by a native lookup."""
        _order(conn, "rejected-1", None, status="rejected")
        _order(conn, "rejected-2", None, status="rejected", side="buy")
        fills.ingest_fills(conn, [_dfill("f1", "sell", 72797.0, 0.04,
                                         oid="2169700358")])
        assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM orders WHERE status='rejected'").fetchone()[0] == 2

    def test_partial_fills_each_produce_a_trade_row(self, conn):
        _order(conn, "coid-F", "555")
        fills.ingest_fills(conn, [
            _dfill("p1", "sell", 72797.0, 0.02, oid="555"),
            _dfill("p2", "sell", 72800.0, 0.02, oid="555"),
        ])
        rows = conn.execute("SELECT order_id, quantity FROM trades ORDER BY id").fetchall()
        assert len(rows) == 2
        assert rows[0][0] == rows[1][0]
        assert fills.position_size(conn) == -2

    def test_duplicate_ingestion_stays_idempotent(self, conn):
        _order(conn, "coid-G", "666")
        payload = [_dfill("dup1", "sell", 72797.0, 0.04, oid="666")]
        fills.ingest_fills(conn, payload)
        r = fills.ingest_fills(conn, payload)
        assert r.ingested == 0 and r.skipped_duplicate == 1
        assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM order_fills").fetchone()[0] == 1
        assert fills.position_size(conn) == -1

    def test_linkage_is_deterministic_across_repeat_runs(self, tmp_path):
        results = []
        for i in range(3):
            c = _exec_db(tmp_path / ("det%d.db" % i))
            _order(c, "coid-H", "888")
            fills.ingest_fills(c, [_dfill("f1", "sell", 72797.0, 0.04, oid="888")])
            results.append(c.execute(
                "SELECT order_id, side, quantity, price, fee FROM trades").fetchall())
            c.close()
        assert results[0] == results[1] == results[2]

    def test_no_trade_is_fabricated_without_an_order(self, conn):
        fills.ingest_fills(conn, [_dfill("f1", "sell", 72797.0, 0.04, oid="does-not-exist")])
        assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0

    def test_source_uses_delta_order_id_before_client_order_id(self):
        """Executable order, not docstring order."""
        src = pathlib.Path("src/execution/fills.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "_resolve_order")
        body = list(fn.body)
        if body and isinstance(body[0], ast.Expr) and isinstance(
                getattr(body[0], "value", None), ast.Constant):
            body = body[1:]                       # drop the docstring
        seen = []
        for node in body:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Attribute) and sub.attr in (
                        "delta_order_id", "client_order_id"):
                    if sub.attr not in seen:
                        seen.append(sub.attr)
        assert seen[0] == "delta_order_id", seen


class TestDailyLossSemanticsUnchanged:
    """Requirement 12: only the number changes, never the rule."""

    def test_daily_loss_cap_still_blocks_at_the_same_threshold(self, conn):
        risk = RiskManager(kill_switch_engaged=False, platform="testnet",
                           base_url=TESTNET_URL,
                           counters_provider=lambda now=None:
                               (-limits.MAX_DAILY_LOSS_USD, 0))
        decision = risk.validate(_intent(), _account())
        assert not decision.approved
        assert "loss" in decision.reason.lower()

    def test_a_correct_loss_reaches_the_cap_sooner_than_the_defective_one(self, tmp_path):
        """The safety consequence, made concrete."""
        c = _exec_db(tmp_path / "cap.db")
        rs = RiskStateStore(c)
        now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
        fills.ingest_fills(c, [_dfill("e1", "sell", 72728.0, G5C8_ENTRY_FEE)],
                           risk_state=rs, now=now)
        fills.ingest_fills(c, [_dfill("x1", "buy", 72797.0, G5C8_EXIT_FEE)],
                           risk_state=rs, now=now)
        corrected = rs.state(now).realised_pnl
        defective = -G5C8_GROSS - G5C8_EXIT_FEE
        assert corrected < defective
        assert defective - corrected == pytest.approx(G5C8_ENTRY_FEE, abs=1e-9)
        c.close()

    def test_realised_pnl_usd_signature_and_arithmetic_are_unchanged(self):
        assert fills.realised_pnl_usd(entry_price=100.0, exit_price=110.0, size=1,
                                      direction=1, fees=0.0) == \
            pytest.approx(10 * limits.BTCUSD_CONTRACT_VALUE)
        assert fills.realised_pnl_usd(entry_price=100.0, exit_price=110.0, size=1,
                                      direction=-1, fees=0.0) == \
            pytest.approx(-10 * limits.BTCUSD_CONTRACT_VALUE)


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
        # Fixture corrected at Gate 5C.13: the fill is linked by order_id, the
        # only key Delta actually returns on /v2/fills (client_order_id is
        # absent from the record -- verified live, 0 of 6 production fills had
        # one). The assertion is unchanged: an UNKNOWN order that did fill is
        # resolved to FILLED by the fills feed. An UNKNOWN order with no
        # delta_order_id has no linkable key at all and still becomes REJECTED
        # -- see test_unknown_with_no_trace_becomes_rejected just below.
        _seed_order(conn, "c3", "unknown", "900")
        c = _FakeClient(fills={"success": True, "result": [
            {"id": "f1", "order_id": "900", "product_id": 84, "side": "buy",
             "size": 1, "price": 64187.5, "commission": 0.032,
             "created_at": "2026-08-18T09:05:00Z"}]})
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
            bridge.build_intent(direction=1, mark_price=MARK, enabled=False,
                                best_bid=MARK - 0.5, best_ask=MARK + 0.5)

    def test_enabled_builds_tick_aligned_single_contract_intent(self):
        it = bridge.build_intent(direction=1, mark_price=MARK, enabled=True,
                                 best_bid=MARK - 0.5, best_ask=MARK + 0.5,
                                 utc_day="2026-08-18")
        assert it.size == 1 and it.symbol == "BTCUSD" and it.product_id == 84
        assert it.order_type == "limit_order" and it.side == "buy"
        # 5C.6: compressed to satisfy Delta's 32-char limit; day, side and
        # sequence are still carried verbatim.
        assert it.client_order_id == bridge.client_order_id(
            bridge.SIGNAL_ID, "2026-08-18", "buy", 1)
        assert len(it.client_order_id) <= bridge.CLIENT_ORDER_ID_MAX_LEN
        assert it.client_order_id.endswith("-20260818-b1")
        assert _risk().validate(it, _account(), now=NOON).approved

    def test_short_direction(self):
        it = bridge.build_intent(direction=-1, mark_price=MARK, enabled=True,
                                 best_bid=MARK - 0.5, best_ask=MARK + 0.5,
                                 utc_day="2026-08-18")
        assert it.side == "sell" and it.stop_price > it.limit_price

    def test_invalid_direction_rejected(self):
        with pytest.raises(ValueError):
            bridge.build_intent(direction=0, mark_price=MARK, enabled=True,
                                best_bid=MARK - 0.5, best_ask=MARK + 0.5)

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

        # Linked by order_id (Gate 5C.13): _ok_place acks with this id, so the
        # local row carries it and the fill resolves through the same key the
        # exchange really provides.
        acked = storage.get_order(conn, "test-2026-08-18-buy-1")["delta_order_id"]
        c._fills = {"success": True, "result": [
            {"id": "f1", "order_id": acked, "product_id": 84, "side": "buy",
             "size": 1, "price": 64187.5, "commission": 0.032,
             "created_at": "2026-08-18T09:05:00Z"}]}
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


class TestProtectiveRiskBudget:
    """Gate 5C.12: the protective stop must obey the budget that authorised
    the trade.

    The invariant, for every open position:

        |entry - trigger| * BTCUSD_CONTRACT_VALUE * size
            + round_trip_fees_usd(entry, size)   <=   MAX_TRADE_RISK_USD

    Gate 5C.4 made ENTRY sizing budget-aware and left PROTECTION on a flat
    PROTECTIVE_STOP_DISTANCE_PCT. Gate 5C.11 measured the resulting gap on two
    live lifecycles (10.5% and 20.2% over budget) and established that Guard 11
    structurally cannot catch it: for a protective intent |limit - stop| is the
    trigger-to-limit slippage band, not the loss. Nothing computed
    |entry - trigger| anywhere, so nothing tested it. This class does.
    """

    # The audited sweep: below, at, and far above the crossover where a flat
    # 1.4% stop stops fitting the budget.
    PRICES = (60000.0, 65876.0, 71429.0, 79210.0, 100000.0, 131752.0, 150000.0)

    # The two real Gate 5C lifecycles, as regression fixtures.
    G5C8_SHORT_ENTRY = 72797.0
    G5C11_LONG_ENTRY = 79210.0

    @staticmethod
    def _exposure(entry, position_size):
        trigger, _ = protection.stop_prices(entry, position_size)
        size = abs(position_size)
        return (abs(entry - trigger) * limits.BTCUSD_CONTRACT_VALUE * size
                + round_trip_fees_usd(entry, size))

    # --- requirements 1-3: the invariant, both directions, fees included ---
    @pytest.mark.parametrize("entry", PRICES)
    @pytest.mark.parametrize("position_size", (1, -1))
    def test_invariant_holds_across_the_price_sweep(self, entry, position_size):
        exposure = self._exposure(entry, position_size)
        assert exposure <= limits.MAX_TRADE_RISK_USD + 1e-9, (
            f"protective stop at {entry} risks {exposure:.4f} "
            f"against a {limits.MAX_TRADE_RISK_USD:.2f} budget")

    @pytest.mark.parametrize("entry", PRICES)
    def test_fees_are_part_of_the_budget_not_extra(self, entry):
        """Price risk alone clearing the cap is not the invariant."""
        trigger, _ = protection.stop_prices(entry, 1)
        price_risk = abs(entry - trigger) * limits.BTCUSD_CONTRACT_VALUE
        fees = round_trip_fees_usd(entry, 1)
        assert fees > 0
        assert price_risk + fees <= limits.MAX_TRADE_RISK_USD + 1e-9

    @pytest.mark.parametrize("entry", PRICES)
    def test_long_and_short_are_exactly_symmetric(self, entry):
        long_d = abs(entry - protection.stop_prices(entry, 1)[0])
        short_d = abs(entry - protection.stop_prices(entry, -1)[0])
        assert long_d == pytest.approx(short_d, abs=1e-9)
        assert self._exposure(entry, 1) == pytest.approx(
            self._exposure(entry, -1), abs=1e-9)

    def test_off_grid_entry_stays_symmetric_to_within_one_tick(self):
        """An entry between ticks cannot be exactly symmetric AND stay in budget.

        The grid sits at a different offset above and below such a price, so
        snapping toward entry differs by up to one tick either side. Where the
        two properties conflict the budget wins; the residual asymmetry is
        bounded at one tick (USD 0.0001) and is never a real Delta fill anyway.
        """
        tick = limits.BTCUSD_TICK_SIZE
        for base in self.PRICES:
            for off in (0.03, 0.049, 0.07):
                entry = base + off
                long_d = abs(entry - protection.stop_prices(entry, 1)[0])
                short_d = abs(entry - protection.stop_prices(entry, -1)[0])
                assert abs(long_d - short_d) <= tick + 1e-9
                assert self._exposure(entry, 1) <= limits.MAX_TRADE_RISK_USD + 1e-9
                assert self._exposure(entry, -1) <= limits.MAX_TRADE_RISK_USD + 1e-9

    def test_direction_still_places_the_stop_on_the_losing_side(self):
        """Symmetry must not be bought by losing the sign."""
        for entry in self.PRICES:
            assert protection.stop_prices(entry, 1)[0] < entry
            assert protection.stop_prices(entry, -1)[0] > entry

    # --- requirement 4: tick rounding can only tighten ---------------------
    def test_tick_rounding_never_widens_past_the_budget(self):
        """Rounding a distance UP is the one direction that breaks the budget."""
        entry = 60000.0
        while entry <= 150000.0:
            for px in (entry, entry + 0.03, entry + 0.07, entry + 0.049):
                for size in (1, -1):
                    assert self._exposure(px, size) <= limits.MAX_TRADE_RISK_USD + 1e-9
                    trigger, limit = protection.stop_prices(px, size)
                    for v in (trigger, limit):
                        assert abs(round(v / limits.BTCUSD_TICK_SIZE)
                                   * limits.BTCUSD_TICK_SIZE - v) < 1e-6
            entry += 733.7

    def test_distance_is_floored_to_a_tick_never_ceilinged(self):
        for entry in self.PRICES:
            d = protection.protective_stop_distance(entry, 1)
            allowed = min(entry * limits.PROTECTIVE_STOP_DISTANCE_PCT / 100.0,
                          affordable_stop_distance(entry, 1))
            assert d <= allowed + 1e-9
            assert allowed - d < limits.BTCUSD_TICK_SIZE + 1e-9

    # --- requirement 5: protection is still PLACED when the budget binds ---
    def test_budget_binding_protection_is_still_created(self, conn):
        """Never refuse protection. An unprotected position is strictly worse."""
        for entry in (100000.0, 131752.0, 150000.0):
            for size in (1, -1):
                it = protection.build_intent(conn, size, 1, entry)
                assert it is not None
                assert it.intent_kind == "protective"
                assert it.stop_price > 0 and it.limit_price > 0
                assert it.size == abs(size)
                assert it.side == ("sell" if size > 0 else "buy")

    def test_distance_is_never_zero_even_if_fees_eat_the_budget(self, monkeypatch):
        """A stop one tick wide still beats no stop at all."""
        monkeypatch.setattr(limits, "FEE_RESERVE_MULTIPLIER", 10_000.0)
        assert affordable_stop_distance(150000.0, 1) == 0.0
        d = protection.protective_stop_distance(150000.0, 1)
        assert d >= limits.BTCUSD_TICK_SIZE
        trigger, limit = protection.stop_prices(150000.0, 1)
        assert trigger < 150000.0 and limit < trigger

    def test_the_budget_actually_binds_above_the_crossover(self):
        """Otherwise every assertion above passes vacuously."""
        assert (protection.protective_stop_distance(150000.0, 1)
                < 150000.0 * limits.PROTECTIVE_STOP_DISTANCE_PCT / 100.0)
        # ...and below it, the strategy maximum still governs.
        assert (protection.protective_stop_distance(60000.0, 1)
                == pytest.approx(60000.0 * limits.PROTECTIVE_STOP_DISTANCE_PCT
                                 / 100.0, abs=limits.BTCUSD_TICK_SIZE))

    # --- requirement 7: the two live lifecycles as regression fixtures -----
    def test_gate_5c8_short_lifecycle_would_now_be_within_budget(self):
        """Live 2026-08-20: entry 72797 short, trigger 73816.2, risked 1.1051."""
        before = (1019.2 * limits.BTCUSD_CONTRACT_VALUE
                  + round_trip_fees_usd(self.G5C8_SHORT_ENTRY, 1))
        assert before > limits.MAX_TRADE_RISK_USD
        after = self._exposure(self.G5C8_SHORT_ENTRY, -1)
        assert after <= limits.MAX_TRADE_RISK_USD + 1e-9
        assert after < before

    def test_gate_5c11_long_lifecycle_would_now_be_within_budget(self):
        """Live 2026-08-24: entry 79210 long, trigger 78101.1, risked 1.2024."""
        before = (1108.9 * limits.BTCUSD_CONTRACT_VALUE
                  + round_trip_fees_usd(self.G5C11_LONG_ENTRY, 1))
        assert before > limits.MAX_TRADE_RISK_USD
        after = self._exposure(self.G5C11_LONG_ENTRY, 1)
        assert after <= limits.MAX_TRADE_RISK_USD + 1e-9
        assert after < before

    def test_protection_now_agrees_with_the_entry_sizing_that_authorised_it(self):
        """The Gate 5C.11 root cause was these two disagreeing by 12-22%."""
        for entry in (self.G5C8_SHORT_ENTRY, self.G5C11_LONG_ENTRY) + self.PRICES:
            assert (protection.protective_stop_distance(entry, 1)
                    == pytest.approx(bridge.stop_distance(entry, entry, 1), abs=1e-9))

    # --- requirement 8: a naive reversion is caught ------------------------
    def test_flat_percentage_protection_is_rejected(self):
        """Reverting to a raw entry-times-percentage stop must not pass."""
        for entry in (79210.0, 100000.0, 150000.0):
            flat = entry * limits.PROTECTIVE_STOP_DISTANCE_PCT / 100.0
            flat_exposure = (flat * limits.BTCUSD_CONTRACT_VALUE
                             + round_trip_fees_usd(entry, 1))
            assert flat_exposure > limits.MAX_TRADE_RISK_USD, (
                "fixture no longer exercises the defect")
            assert protection.protective_stop_distance(entry, 1) < flat

    def test_fee_model_change_moves_the_protective_distance(self, monkeypatch):
        """Proves the fee model is shared, not reimplemented alongside."""
        before = protection.protective_stop_distance(150000.0, 1)
        monkeypatch.setattr(limits, "FEE_RESERVE_MULTIPLIER", 3.0)
        after = protection.protective_stop_distance(150000.0, 1)
        assert after < before

    def test_protection_uses_the_shared_helper_not_a_parallel_formula(self):
        import ast
        import inspect
        # Scan CODE, not prose. A docstring naming the constants it explains is
        # not a reimplementation -- matching raw source text here would report
        # the opposite of the truth in both directions.
        tree = ast.parse(inspect.getsource(protection.protective_stop_distance))
        fn = tree.body[0]
        if (fn.body and isinstance(fn.body[0], ast.Expr)
                and isinstance(fn.body[0].value, ast.Constant)
                and isinstance(fn.body[0].value.value, str)):
            fn.body = fn.body[1:]
        names = {n.id for n in ast.walk(ast.Module(body=fn.body, type_ignores=[]))
                 if isinstance(n, ast.Name)}
        names |= {n.attr for n in ast.walk(ast.Module(body=fn.body, type_ignores=[]))
                  if isinstance(n, ast.Attribute)}
        assert "affordable_stop_distance" in names
        assert "TAKER_FEE_RATE" not in names, "fee arithmetic was reimplemented"
        assert "MAX_TRADE_RISK_USD" not in names, "budget arithmetic was reimplemented"

    def test_execution_does_not_depend_on_a_strategy_module(self):
        """Protection is safety infrastructure; a strategy edit must not reach it."""
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(protection))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported |= {a.name for a in node.names}
        assert not any(m.startswith("src.strategies") for m in imported), imported

    # --- requirement 6: the 5C.1 exemptions survive ------------------------
    def test_budget_bound_protection_keeps_every_cap_exemption(self, conn):
        """Tightening the stop must not make protection blockable."""
        it = protection.build_intent(conn, 1, 1, 150000.0)
        acct = _account(position_size=1, mark_price=150000.0)
        store = RiskStateStore(conn)
        store.record_order_placed("a", now=NOON)
        store.record_order_placed("b", now=NOON)
        store.record_realised_pnl("big", -99.0, now=NOON)
        exhausted = _risk(counters_provider=lambda: store.counters(NOON))
        assert exhausted.validate(it, acct, now=NOON).approved
        assert _risk(kill_switch_engaged=True).validate(it, acct, now=NOON).approved
        dead = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
        assert _risk().validate(it, acct, now=dead).approved

    def test_protective_intent_still_passes_guard_11_unchanged(self, conn):
        """Guard 11 is untouched by this gate: it still sees |limit - stop|."""
        it = protection.build_intent(conn, 1, 1, 150000.0)
        seen = compute_trade_risk_usd(it, 150000.0)
        expected = (abs(it.limit_price - it.stop_price)
                    * limits.BTCUSD_CONTRACT_VALUE * it.size
                    + round_trip_fees_usd(150000.0, 1))
        assert seen == pytest.approx(expected, abs=1e-9)
        assert _risk().validate(it, _account(position_size=1,
                                             mark_price=150000.0), now=NOON).approved

    # --- requirement 10: nothing else moved --------------------------------
    def test_entry_sizing_semantics_are_unchanged(self):
        """bridge.stop_distance is still min(strategy_max, affordable), floored."""
        for mark in (64187.5, 69551.8, 79210.0, 120000.0):
            expected = min(mark * bridge.STOP_DISTANCE_PCT / 100.0,
                           affordable_stop_distance(mark, 1))
            got = bridge.stop_distance(mark, mark, 1)
            assert got <= expected + 1e-9
            assert expected - got < limits.BTCUSD_TICK_SIZE + 1e-9

    def test_order_size_and_side_are_untouched_by_the_budget(self, conn):
        for entry in (60000.0, 150000.0):
            assert protection.build_intent(conn, 1, 1, entry).size == 1
            assert protection.build_intent(conn, 2, 1, entry).size == 2
            assert protection.build_intent(conn, 1, 1, entry).side == "sell"
            assert protection.build_intent(conn, -1, 1, entry).side == "buy"

    def test_protective_coid_is_unaffected_by_the_budget(self, conn):
        a = protection.build_intent(conn, 1, 7, 60000.0).client_order_id
        b = protection.build_intent(conn, 1, 7, 150000.0).client_order_id
        assert a == b == protection.protective_coid(7, 1)

    def test_larger_size_tightens_rather_than_scaling_the_distance(self):
        """The budget is per trade, not per contract."""
        one = protection.protective_stop_distance(150000.0, 1)
        two = protection.protective_stop_distance(150000.0, 2)
        assert two < one
        exposure = (two * limits.BTCUSD_CONTRACT_VALUE * 2
                    + round_trip_fees_usd(150000.0, 2))
        assert exposure <= limits.MAX_TRADE_RISK_USD + 1e-9


_NO_COID = object()


def _prod_fill(fid, side, *, oid, price=64000.0, size=1, fee=0.032,
               created_at="2026-08-19T04:00:00Z", coid=_NO_COID):
    """A fill shaped as Delta ACTUALLY returns it: no client_order_id field.

    Verified live on production 2026-08-24 -- 0 of 6 fills carried the key, and
    it is absent from the record entirely. The module-level _fill() helper
    includes one, which is why the reconciler's client_order_id keying looked
    correct under test while being dead on the exchange.
    """
    f = {"id": fid, "order_id": oid, "product_id": 84, "side": side,
         "size": size, "price": price, "commission": fee,
         "created_at": created_at}
    if coid is not _NO_COID:
        f["client_order_id"] = coid
    return f


def _local_order(conn, coid, *, delta_order_id, status="acked", side="buy",
                 price=64000.0, size=1):
    storage.write_order(conn, client_order_id=coid, instrument_id=84, side=side,
                        order_type="limit_order", quantity=float(size),
                        price=price, status=status, strategy="g3_momentum_tsmom",
                        placed_ts="2026-08-19T03:59:00+00:00",
                        delta_order_id=delta_order_id)
    conn.commit()


class TestReconcilerFillLinkage:
    """Gate 5C.13: filled orders are detected via delta_order_id.

    Delta's /v2/fills does not return client_order_id -- the field is absent
    from the record, not merely null (verified live: 0 of 6 production fills
    carried it). The reconciler keyed its filled-order set on exactly that
    field, so the set was always empty, the FILLED branch was unreachable, and
    every genuine fill was adopted as CANCELLED with an ORPHAN_LOCAL finding.

    Accounting was never affected -- order_fills is keyed on delta_order_id and
    was already correct -- but the orders table asserted that no order had ever
    filled, and filled_ts was NULL by construction.
    """

    @staticmethod
    def _status(conn, coid):
        return storage.get_order(conn, coid)["status"]

    @staticmethod
    def _filled_ts(conn, coid):
        return storage.get_order(conn, coid)["filled_ts"]

    # --- 1: a production-shaped fill (no coid) still reaches FILLED --------
    def test_fill_without_client_order_id_still_reaches_filled(self, conn):
        _local_order(conn, "entry-A", delta_order_id="900")
        _local_order(conn, "exit-A", delta_order_id="901", side="sell")
        client = _FakeReconcileClient(
            fills={"success": True, "result": [
                _prod_fill("f1", "buy", oid="900"),
                _prod_fill("f2", "sell", oid="901", price=64100.0)]},
            positions={"success": True, "result": []})
        r = Reconciler(conn, client, product_id=84)
        result = r.reconcile()
        assert not r.halted, r.halt_reason
        assert self._status(conn, "entry-A") == "filled"
        assert self._status(conn, "exit-A") == "filled"
        assert result.clean

    # --- 2: delta_order_id resolves the CORRECT local order ---------------
    def test_delta_order_id_resolves_the_correct_order(self, conn):
        _local_order(conn, "entry-A", delta_order_id="900")
        _local_order(conn, "entry-B", delta_order_id="901")
        _local_order(conn, "exit-A", delta_order_id="902", side="sell")
        client = _FakeReconcileClient(
            fills={"success": True, "result": [
                _prod_fill("f1", "buy", oid="900"),
                _prod_fill("f2", "sell", oid="902", price=64100.0)]},
            positions={"success": True, "result": []})
        Reconciler(conn, client, product_id=84).reconcile()
        assert self._status(conn, "entry-A") == "filled"
        assert self._status(conn, "exit-A") == "filled"
        # 901 never filled: it must NOT be dragged along.
        assert self._status(conn, "entry-B") == "cancelled"

    # --- 3: filled_ts comes from the exchange's own fill timestamp --------
    def test_filled_ts_is_populated_from_the_fill(self, conn):
        _local_order(conn, "entry-A", delta_order_id="900")
        _local_order(conn, "exit-A", delta_order_id="901", side="sell")
        client = _FakeReconcileClient(
            fills={"success": True, "result": [
                _prod_fill("f1", "buy", oid="900",
                           created_at="2026-08-19T04:00:00Z"),
                _prod_fill("f2", "sell", oid="901", price=64100.0,
                           created_at="2026-08-19T04:05:00Z")]},
            positions={"success": True, "result": []})
        Reconciler(conn, client, product_id=84).reconcile()
        assert self._filled_ts(conn, "entry-A") == "2026-08-19T04:00:00Z"
        assert self._filled_ts(conn, "exit-A") == "2026-08-19T04:05:00Z"

    def test_partial_fills_take_the_completing_timestamp(self, conn):
        """Several fills for one order: the last one is when it became FILLED."""
        _local_order(conn, "entry-A", delta_order_id="900", size=2)
        _local_order(conn, "exit-A", delta_order_id="901", side="sell", size=2)
        client = _FakeReconcileClient(
            fills={"success": True, "result": [
                _prod_fill("f1", "buy", oid="900", created_at="2026-08-19T04:00:00Z"),
                _prod_fill("f2", "buy", oid="900", created_at="2026-08-19T04:02:00Z"),
                _prod_fill("f3", "sell", oid="901", size=2, price=64100.0,
                           created_at="2026-08-19T04:05:00Z")]},
            positions={"success": True, "result": []})
        Reconciler(conn, client, product_id=84).reconcile()
        assert self._filled_ts(conn, "entry-A") == "2026-08-19T04:02:00Z"

    def test_filled_ts_is_write_once_across_repeated_passes(self, conn):
        _local_order(conn, "entry-A", delta_order_id="900")
        _local_order(conn, "exit-A", delta_order_id="901", side="sell")
        client = _FakeReconcileClient(
            fills={"success": True, "result": [
                _prod_fill("f1", "buy", oid="900"),
                _prod_fill("f2", "sell", oid="901", price=64100.0)]},
            positions={"success": True, "result": []})
        r = Reconciler(conn, client, product_id=84)
        for _ in range(4):
            r.reconcile()
        assert self._filled_ts(conn, "entry-A") == "2026-08-19T04:00:00Z"
        assert self._status(conn, "entry-A") == "filled"
        assert not r.halted

    # --- 4: a normal fill is not an orphan -------------------------------
    def test_normal_fill_is_not_classified_as_orphan(self, conn):
        _local_order(conn, "entry-A", delta_order_id="900")
        _local_order(conn, "exit-A", delta_order_id="901", side="sell")
        client = _FakeReconcileClient(
            fills={"success": True, "result": [
                _prod_fill("f1", "buy", oid="900"),
                _prod_fill("f2", "sell", oid="901", price=64100.0)]},
            positions={"success": True, "result": []})
        result = Reconciler(conn, client, product_id=84).reconcile()
        assert Divergence.ORPHAN_LOCAL not in result.divergences
        reasons = [why for _, d, why in result.findings
                   if d is Divergence.LOCAL_STALE]
        assert any("filled (via fills)" in w for w in reasons), result.findings

    # --- 5: unresolvable linkage fails closed ----------------------------
    def test_order_without_delta_order_id_is_never_guessed_filled(self, conn):
        """No exchange id means it never reached matching: never infer FILLED."""
        _local_order(conn, "entry-A", delta_order_id=None)
        client = _FakeReconcileClient(
            fills={"success": True, "result": [_prod_fill("f1", "buy", oid="900")]},
            positions={"success": True, "result": [{"size": 1}]})
        r = Reconciler(conn, client, product_id=84)
        r.reconcile()
        assert self._status(conn, "entry-A") == "cancelled"
        assert self._filled_ts(conn, "entry-A") is None

    def test_unknown_delta_order_id_does_not_mark_any_order_filled(self, conn):
        """A fill for an order id we do not hold must not resolve a bystander."""
        _local_order(conn, "entry-A", delta_order_id="900")
        client = _FakeReconcileClient(
            fills={"success": True, "result": [
                _prod_fill("f1", "buy", oid="999999")]},
            positions={"success": True, "result": [{"size": 1}]})
        r = Reconciler(conn, client, product_id=84)
        r.reconcile()
        assert self._status(conn, "entry-A") != "filled"
        # The unexplained exchange position is still the thing that halts.
        assert r.halted

    def test_unknown_order_with_no_exchange_id_stays_unlinkable(self, conn):
        """Residual, disclosed limitation -- pinned so it cannot drift silently.

        An ambiguous submit that never returned an id has no key on either
        side: Delta puts no client_order_id on a fill, and we hold no
        delta_order_id. Such an order is adopted REJECTED even if a fill for it
        exists. The old client_order_id branch appeared to cover this case but
        never could in production, so nothing real is lost -- the fill itself
        is still ingested, so position and P&L remain correct.
        """
        _local_order(conn, "entry-A", delta_order_id=None, status="unknown")
        client = _FakeReconcileClient(
            fills={"success": True, "result": [_prod_fill("f1", "buy", oid="900")]},
            positions={"success": True, "result": [{"size": 1}]})
        Reconciler(conn, client, product_id=84).reconcile()
        assert self._status(conn, "entry-A") == "rejected"
        # The money is still right: the fill was ingested regardless.
        assert fills.position_size(conn) == 1

    def test_fill_with_no_order_id_is_ignored_for_linkage(self, conn):
        _local_order(conn, "entry-A", delta_order_id="900")
        client = _FakeReconcileClient(
            fills={"success": True, "result": [
                dict(_prod_fill("f1", "buy", oid="900"), order_id=None)]},
            positions={"success": True, "result": [{"size": 1}]})
        r = Reconciler(conn, client, product_id=84)
        r.reconcile()
        assert self._status(conn, "entry-A") != "filled"

    # --- 6: the client_order_id assumption cannot come back --------------
    def test_a_misleading_client_order_id_cannot_resolve_the_wrong_order(self, conn):
        """The adversarial case: if the coid key returned, B would be filled.

        The fill genuinely belongs to order 900 (entry-A) but carries a
        client_order_id naming entry-B. Only delta_order_id is authoritative.
        """
        _local_order(conn, "entry-A", delta_order_id="900")
        _local_order(conn, "entry-B", delta_order_id="901")
        _local_order(conn, "exit-A", delta_order_id="902", side="sell")
        client = _FakeReconcileClient(
            fills={"success": True, "result": [
                _prod_fill("f1", "buy", oid="900", coid="entry-B"),
                _prod_fill("f2", "sell", oid="902", price=64100.0, coid="entry-B")]},
            positions={"success": True, "result": []})
        Reconciler(conn, client, product_id=84).reconcile()
        assert self._status(conn, "entry-A") == "filled"
        assert self._status(conn, "entry-B") == "cancelled", \
            "client_order_id keying returned: the wrong order was resolved"

    def test_filled_set_is_not_built_from_client_order_id(self):
        """Structural guard, on code rather than prose."""
        import ast
        import inspect
        src = inspect.getsource(Reconciler.reconcile)
        tree = ast.parse(textwrap.dedent(src))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if isinstance(fn, ast.Attribute) and fn.attr == "get" and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and arg.value == "client_order_id":
                    # Permitted only for LIVE orders, which do carry it.
                    src_seg = ast.get_source_segment(textwrap.dedent(src), node) or ""
                    assert "f." not in src_seg, \
                        "a fill is being keyed by client_order_id again"

    # --- 7: cancellation / rejection semantics unchanged -----------------
    def test_absent_acked_order_still_becomes_cancelled(self, conn):
        _local_order(conn, "entry-A", delta_order_id="900")
        client = _FakeReconcileClient(positions={"success": True, "result": []})
        result = Reconciler(conn, client, product_id=84).reconcile()
        assert self._status(conn, "entry-A") == "cancelled"
        assert self._filled_ts(conn, "entry-A") is None
        assert Divergence.ORPHAN_LOCAL in result.divergences

    def test_unknown_with_no_trace_still_becomes_rejected(self, conn):
        _local_order(conn, "entry-A", delta_order_id=None, status="unknown")
        client = _FakeReconcileClient(positions={"success": True, "result": []})
        result = Reconciler(conn, client, product_id=84).reconcile()
        assert self._status(conn, "entry-A") == "rejected"
        assert Divergence.ORPHAN_LOCAL in result.divergences

    def test_live_order_path_is_untouched(self, conn):
        """Orders still live on the exchange are matched by coid, as before."""
        _local_stop_row(conn)
        client = _FakeReconcileClient(
            orders={"success": True, "result": [_stop_order()]},
            fills={"success": True, "result": [_prod_fill("f1", "buy", oid="900")]},
            positions={"success": True, "result": [{"size": 1}]})
        _local_order(conn, "entry-A", delta_order_id="900")
        r = Reconciler(conn, client, product_id=84)
        result = r.reconcile()
        assert not r.halted, r.halt_reason
        assert self._status(conn, "stop-p1-q1") == "acked"
        assert Divergence.IN_SYNC in result.divergences

    # --- accounting must not move ----------------------------------------
    def test_position_and_pnl_are_unaffected_by_the_status_change(self, conn):
        _local_order(conn, "entry-A", delta_order_id="900")
        _local_order(conn, "exit-A", delta_order_id="901", side="sell")
        client = _FakeReconcileClient(
            fills={"success": True, "result": [
                _prod_fill("f1", "buy", oid="900", price=64000.0),
                _prod_fill("f2", "sell", oid="901", price=64100.0)]},
            positions={"success": True, "result": []})
        Reconciler(conn, client, product_id=84).reconcile()
        assert fills.position_size(conn) == 0
        assert fills.open_position(conn) is None
        realised = conn.execute(
            "SELECT SUM(COALESCE(realised_pnl, 0)) FROM order_fills").fetchone()[0]
        # (64100 - 64000) * 0.001 - both fees; unchanged by order status.
        assert realised == pytest.approx(0.1 - 0.064, abs=1e-9)


def _resting_exit(oid=6001, coid="exit-p1-20260819-s1", side="sell",
                  state="open", reduce_only=True, size=1):
    """A supervised exit as Delta returns it: reduce-only, no stop_order_type.

    Shape confirmed against /v2/orders/history at Gate 5C.15 -- every exit in
    production history carries reduce_only=True and stop_order_type=None.
    """
    return {"id": oid, "client_order_id": coid, "product_id": 84, "side": side,
            "size": size, "unfilled_size": size, "state": state,
            "limit_price": "64500", "stop_order_type": None,
            "reduce_only": reduce_only}


def _entry_order(oid=6002, coid="entry-1", side="buy", state="open"):
    """An entry: never reduce-only. Must never be selected for cleanup."""
    return {"id": oid, "client_order_id": coid, "product_id": 84, "side": side,
            "size": 1, "unfilled_size": 1, "state": state,
            "limit_price": "64000", "stop_order_type": None,
            "reduce_only": False}


def _local_exit_row(conn, coid="exit-p1-20260819-s1", side="sell",
                    delta_order_id="6001", status="acked"):
    """The local row the executor always writes when it places an exit.

    Without it the exit is, correctly, an ORPHAN exchange order and the
    reconciler halts before cleanup is even the question.
    """
    storage.write_order(conn, client_order_id=coid, instrument_id=84, side=side,
                        order_type="limit_order", quantity=1.0, price=64500.0,
                        status=status, strategy="g3_momentum_tsmom",
                        placed_ts="2026-08-19T05:00:00+00:00",
                        delta_order_id=delta_order_id)
    conn.commit()


class _CancelRecordingClient(_FakeReconcileClient):
    """Records cancels and can be told to fail them."""

    def __init__(self, *a, cancel_exc=None, **kw):
        super().__init__(*a, **kw)
        self.cancelled: list[int] = []
        self._cancel_exc = cancel_exc

    def cancel_order(self, order_id, product_id):
        if self._cancel_exc is not None:
            raise self._cancel_exc
        self.cancelled.append(int(order_id))
        return {"success": True, "result": {}}


class TestSignedPositionReconciliation:
    """Gate 5C.16 Fix 1: direction is part of a position, so compare it.

    The old check was `abs(exchange) != abs(local)`. An exchange long of 1 and
    a local short of 1 both collapsed to 1 and were declared in sync -- the one
    disagreement that matters most was the one it could not see.
    """

    @staticmethod
    def _seed_local(conn, side, qty=1):
        """Seed through the real fills path -- the source the check reads."""
        fills.ingest_fills(conn, [_fill("seed-%s" % side, side, size=qty)],
                           instrument_id=84)

    def test_exact_signed_match_is_clean(self, conn):
        self._seed_local(conn, "buy")
        _local_stop_row(conn)
        client = _FakeReconcileClient(
            positions={"success": True, "result": [{"size": 1}]},
            orders={"success": True, "result": [_stop_order()]})
        r = Reconciler(conn, client, product_id=84, ingest_fills=False)
        result = r.reconcile()
        assert not r.halted, r.halt_reason
        assert Divergence.POSITION_MISMATCH not in result.divergences

    def test_short_exact_signed_match_is_clean(self, conn):
        self._seed_local(conn, "sell")
        _local_stop_row(conn, side="buy")
        client = _FakeReconcileClient(
            positions={"success": True, "result": [{"size": -1}]},
            orders={"success": True, "result": [_stop_order(side="buy")]})
        r = Reconciler(conn, client, product_id=84, ingest_fills=False)
        r.reconcile()
        assert not r.halted, r.halt_reason

    def test_long_versus_short_halts(self, conn):
        """The case abs() hid: same magnitude, opposite direction."""
        self._seed_local(conn, "sell")                       # local short 1
        client = _FakeReconcileClient(
            positions={"success": True, "result": [{"size": 1}]})   # exchange long 1
        r = Reconciler(conn, client, product_id=84, ingest_fills=False)
        result = r.reconcile()
        assert r.halted, "opposite directions were treated as in sync"
        assert Divergence.POSITION_MISMATCH in result.divergences
        # halt_reason holds the LAST halt of the pass -- an unprotected short
        # also halts here -- so assert on the finding, which is per-check.
        detail = [why for who, d, why in result.findings
                  if who == "<position>" and d is Divergence.POSITION_MISMATCH]
        assert detail and "+1" in detail[0] and "-1" in detail[0], result.findings

    def test_short_versus_long_halts(self, conn):
        self._seed_local(conn, "buy")
        client = _FakeReconcileClient(
            positions={"success": True, "result": [{"size": -1}]})
        r = Reconciler(conn, client, product_id=84, ingest_fills=False)
        result = r.reconcile()
        assert r.halted
        assert Divergence.POSITION_MISMATCH in result.divergences

    def test_magnitude_mismatch_still_halts(self, conn):
        """Pre-existing behaviour must survive the change."""
        self._seed_local(conn, "buy", qty=1.0)
        client = _FakeReconcileClient(
            positions={"success": True, "result": [{"size": 2}]})
        r = Reconciler(conn, client, product_id=84, ingest_fills=False)
        assert Divergence.POSITION_MISMATCH in r.reconcile().divergences
        assert r.halted

    def test_flat_both_sides_is_clean(self, conn):
        client = _FakeReconcileClient(positions={"success": True, "result": []})
        r = Reconciler(conn, client, product_id=84, ingest_fills=False)
        assert not r.halted and r.reconcile().clean

    def test_helper_returns_a_signed_value(self, conn):
        self._seed_local(conn, "sell")
        r = Reconciler(conn, _FakeReconcileClient(), product_id=84)
        assert r._local_signed_position() == -1
        fills.ingest_fills(conn, [_fill("flip-a", "buy"), _fill("flip-b", "buy")],
                           instrument_id=84)
        assert r._local_signed_position() == 1

    def test_comparison_does_not_use_abs(self):
        """Structural guard: abs() on the position comparison must not return."""
        import ast
        import inspect
        src = textwrap.dedent(inspect.getsource(Reconciler.reconcile))
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "abs"):
                seg = ast.get_source_segment(src, node) or ""
                assert "size" not in seg, f"abs() reintroduced on a size: {seg}"


class TestStaleReducingOrderCleanup:
    """Gate 5C.16 Fix 2: a resting exit must not outlive its position.

    A supervised exit is reduce-only and gtc. If it does not cross it rests
    (Gate 5C.14 saw one rest 3.5 minutes). Protection's orphan sweep only looks
    at orders carrying a stop_order_type, and the reconciler never cancels, so
    nothing removed it. reduce_only is expected to stop the exchange filling it
    into a fresh position, but that guarantee is unverified (Gate 5C.15) and
    was the only barrier. This removes the order instead of trusting it.
    """

    def test_stale_reducing_order_cancelled_when_flat(self, conn):
        _local_exit_row(conn)
        client = _CancelRecordingClient(
            orders={"success": True, "result": [_resting_exit()]},
            positions={"success": True, "result": []})
        Reconciler(conn, client, product_id=84, ingest_fills=False).reconcile()
        assert client.cancelled == [6001]

    def test_short_side_resting_exit_also_cancelled(self, conn):
        """Long/short symmetry: a short's exit is a reduce-only BUY."""
        _local_exit_row(conn, coid="exit-p1-b1", side="buy", delta_order_id="6011")
        client = _CancelRecordingClient(
            orders={"success": True, "result": [
                _resting_exit(oid=6011, coid="exit-p1-b1", side="buy")]},
            positions={"success": True, "result": []})
        Reconciler(conn, client, product_id=84, ingest_fills=False).reconcile()
        assert client.cancelled == [6011]

    def test_not_cancelled_while_a_position_is_open(self, conn):
        """A live exit for a live position is exactly what should be resting."""
        fills.ingest_fills(conn, [_fill("open-1", "buy")], instrument_id=84)
        _local_stop_row(conn)
        _local_exit_row(conn)
        client = _CancelRecordingClient(
            orders={"success": True, "result": [_resting_exit(), _stop_order()]},
            positions={"success": True, "result": [{"size": 1}]})
        Reconciler(conn, client, product_id=84, ingest_fills=False).reconcile()
        assert client.cancelled == [], "cancelled an exit that still had a position"

    def test_entry_order_is_never_cancelled(self, conn):
        """An entry is not reduce-only, so it can never be selected."""
        _local_exit_row(conn, coid="entry-1", side="buy", delta_order_id="6002")
        client = _CancelRecordingClient(
            orders={"success": True, "result": [_entry_order()]},
            positions={"success": True, "result": []})
        Reconciler(conn, client, product_id=84, ingest_fills=False).reconcile()
        assert client.cancelled == []

    def test_protective_stop_is_never_cancelled_by_this_path(self, conn):
        """Stops are owned by evaluate()/ensure(), not by this cleanup."""
        cancelled = protection.cancel_stale_reducing_orders(
            conn, _CancelRecordingClient(), [_stop_order()], 0)
        assert cancelled == []

    def test_cancel_failure_is_contained(self, conn):
        """An unreachable exchange must not turn cleanup into a crash."""
        _local_exit_row(conn)
        client = _CancelRecordingClient(
            orders={"success": True, "result": [_resting_exit()]},
            positions={"success": True, "result": []},
            cancel_exc=requests.exceptions.Timeout("boom"))
        r = Reconciler(conn, client, product_id=84, ingest_fills=False)
        result = r.reconcile()          # must not raise
        assert not r.halted, r.halt_reason
        assert result.clean

    def test_direct_call_is_a_noop_when_not_flat(self, conn):
        client = _CancelRecordingClient()
        assert protection.cancel_stale_reducing_orders(
            conn, client, [_resting_exit()], 1) == []
        assert protection.cancel_stale_reducing_orders(
            conn, client, [_resting_exit()], -1) == []
        assert client.cancelled == []

    def test_only_live_states_are_cancelled(self, conn):
        client = _CancelRecordingClient()
        for dead in ("closed", "cancelled", "filled"):
            assert protection.cancel_stale_reducing_orders(
                conn, client, [_resting_exit(state=dead)], 0) == []
        assert client.cancelled == []

    def test_malformed_orders_are_skipped(self, conn):
        client = _CancelRecordingClient()
        junk = ["not-a-dict", {}, {"reduce_only": True, "state": "open"}]
        assert protection.cancel_stale_reducing_orders(conn, client, junk, 0) == []

    def test_cleanup_runs_before_protection_and_leaves_the_pass_clean(self, conn):
        _local_exit_row(conn)
        client = _CancelRecordingClient(
            orders={"success": True, "result": [_resting_exit()]},
            positions={"success": True, "result": []})
        r = Reconciler(conn, client, product_id=84, ingest_fills=False)
        result = r.reconcile()
        assert client.cancelled == [6001]
        assert result.clean and not r.halted


class TestReversalPathAfterBothFixes:
    """What actually happens on the Gate 5C.15 stop-first sequence."""

    def test_reversal_is_prevented_by_removing_the_resting_exit(self, conn):
        """Fix 2 is the barrier: the exit is gone before it can fill."""
        _local_exit_row(conn)
        client = _CancelRecordingClient(
            orders={"success": True, "result": [_resting_exit()]},
            positions={"success": True, "result": []})
        Reconciler(conn, client, product_id=84, ingest_fills=False).reconcile()
        assert 6001 in client.cancelled, "the reversal source was left resting"

    def test_a_reversal_that_did_occur_is_not_caught_by_the_position_check(self, conn):
        """Documented limit, pinned so it is not mistaken for coverage.

        If a reversal DID happen, exchange and local would both read -1 and
        AGREE -- signed comparison catches disagreement, not an unintended but
        mutually-consistent position. Fix 1 does not close this; Fix 2 does, by
        removing the order that could cause it. What still fires is the
        protection invariant: a naked short with no stop is not safe.
        """
        fills.ingest_fills(conn, [_fill("rev-1", "sell")], instrument_id=84)
        client = _FakeReconcileClient(
            positions={"success": True, "result": [{"size": -1}]},
            orders={"success": True, "result": []})
        r = Reconciler(conn, client, product_id=84, ingest_fills=False)
        result = r.reconcile()
        assert Divergence.POSITION_MISMATCH in result.divergences
        assert r.halted, "an unprotected reversed position must not pass"
        assert "unprotected" in r.halt_reason or "position" in r.halt_reason


class TestSubmittedCrashRecovery:
    """Gate 5C.18-F: a SUBMITTED row must not abort the reconciliation pass.

    SUBMITTED is the durable write-ahead state. Every API outcome moves the row
    off it (4xx -> REJECTED, timeout/5xx/reset/malformed -> UNKNOWN, ok ->
    ACKED), so a row still SUBMITTED at reconciliation means the process died
    between the write-ahead persist and the outcome persist.

    The fallback sent it to CANCELLED, for which SUBMITTED has no arc, so
    transition() raised IllegalTransition and the WHOLE pass aborted -- before
    fill ingestion, the position comparison and the protection invariant. The
    row never converged, so it failed again every 60s indefinitely, while
    _job() logs and returns without halting, leaving trading_permitted True.
    Observed in production at Gate 5C.17: 4 failed passes over ~3 minutes.
    """

    @staticmethod
    def _flat_client():
        return _FakeReconcileClient(
            orders={"success": True, "result": []},
            fills={"success": True, "result": []},
            positions={"success": True, "result": []})

    # --- 1: SUBMITTED with no exchange trace -> UNKNOWN, no exception -----
    def test_submitted_with_no_trace_becomes_unknown(self, conn):
        _seed_order(conn, "crashed", "submitted")
        r = Reconciler(conn, self._flat_client(), product_id=84)
        result = r.reconcile()          # must not raise
        assert storage.get_order(conn, "crashed")["status"] == "unknown"
        assert Divergence.ORPHAN_LOCAL in result.divergences
        assert any("submitted with no exchange trace -> unknown" in why
                   for _, _, why in result.findings), result.findings

    def test_submitted_pass_does_not_raise_illegal_transition(self, conn):
        _seed_order(conn, "crashed", "submitted")
        r = Reconciler(conn, self._flat_client(), product_id=84)
        try:
            r.reconcile()
        except IllegalTransition as exc:                    # the exact defect
            pytest.fail(f"reconciliation aborted on a write-ahead row: {exc}")

    def test_the_pass_still_completes_its_later_checks(self, conn):
        """The abort's real cost: fills/positions/protection never ran."""
        _seed_order(conn, "crashed", "submitted")
        client = _FakeReconcileClient(
            orders={"success": True, "result": []},
            fills={"success": True, "result": [_prod_fill("f1", "buy", oid="900"),
                                               _prod_fill("f2", "sell", oid="901",
                                                          price=64100.0)]},
            positions={"success": True, "result": []})
        result = Reconciler(conn, client, product_id=84).reconcile()
        assert fills.position_size(conn) == 0
        assert any(who == "<fills>" for who, _, _ in result.findings), \
            "fill ingestion did not run: the pass aborted early"

    # --- 2: ACKED / PARTIAL fallback unchanged ---------------------------
    @pytest.mark.parametrize("state", ["acked", "partial"])
    def test_acked_and_partial_still_become_cancelled(self, conn, state):
        _seed_order(conn, "gone", state, "900")
        r = Reconciler(conn, self._flat_client(), product_id=84)
        result = r.reconcile()
        assert storage.get_order(conn, "gone")["status"] == "cancelled"
        assert any("absent from exchange -> cancelled" in why
                   for _, _, why in result.findings), result.findings

    def test_unknown_fallback_still_becomes_rejected(self, conn):
        _seed_order(conn, "amb", "unknown")
        Reconciler(conn, self._flat_client(), product_id=84).reconcile()
        assert storage.get_order(conn, "amb")["status"] == "rejected"

    # --- 3: the periodic job stops failing -------------------------------
    def test_periodic_job_does_not_fail_repeatedly(self, conn, caplog):
        _seed_order(conn, "crashed", "submitted")
        r = Reconciler(conn, self._flat_client(), product_id=84)
        job = _make_periodic_reconcile(r)
        with caplog.at_level(logging.ERROR):
            for _ in range(3):
                job()
        failures = [rec for rec in caplog.records
                    if "periodic pass failed" in rec.getMessage()]
        assert failures == [], [f.getMessage() for f in failures]

    def test_repeated_passes_do_not_reopen_a_settled_row(self, conn):
        _seed_order(conn, "crashed", "submitted")
        r = Reconciler(conn, self._flat_client(), product_id=84)
        for _ in range(5):
            r.reconcile()
        assert storage.get_order(conn, "crashed")["status"] == "rejected"
        assert not r.halted

    # --- 4: convergence across two passes --------------------------------
    def test_converges_submitted_then_unknown_then_rejected(self, conn):
        _seed_order(conn, "crashed", "submitted")
        r = Reconciler(conn, self._flat_client(), product_id=84)
        assert storage.get_order(conn, "crashed")["status"] == "submitted"
        r.reconcile()
        assert storage.get_order(conn, "crashed")["status"] == "unknown"
        r.reconcile()
        assert storage.get_order(conn, "crashed")["status"] == "rejected"
        r.reconcile()
        assert storage.get_order(conn, "crashed")["status"] == "rejected", \
            "a terminal row must stay terminal"

    def test_convergence_reaches_a_terminal_state(self, conn):
        _seed_order(conn, "crashed", "submitted")
        r = Reconciler(conn, self._flat_client(), product_id=84)
        r.reconcile(); r.reconcile()
        assert is_terminal(OrderState(storage.get_order(conn, "crashed")["status"]))
        assert storage.get_open_orders(
            conn, tuple(s.value for s in OPEN_STATES)) == []

    # --- 5: crash-recovery fixture ---------------------------------------
    def test_boot_reconcile_recovers_a_write_ahead_row(self, conn):
        """Mirror of the existing UNKNOWN boot-recovery test, for SUBMITTED.

        Simulates process death between the write-ahead persist and the
        outcome persist: the row exists, nothing else does.
        """
        _seed_order(conn, "crashed", "submitted")
        r = Reconciler(conn, self._flat_client(), product_id=84)
        assert r.boot_reconcile().clean
        assert not r.halted
        assert storage.get_order(conn, "crashed")["status"] == "unknown"

    def test_write_ahead_row_never_links_a_fill(self, conn):
        """It has no delta_order_id, so a fill cannot be attributed to it.

        That is why UNKNOWN -- not CANCELLED -- is the honest state: we cannot
        assert the order reached the exchange, and an unattributable fill still
        surfaces as a position divergence rather than being silently absorbed.
        """
        _seed_order(conn, "crashed", "submitted")
        client = _FakeReconcileClient(
            orders={"success": True, "result": []},
            fills={"success": True, "result": [_prod_fill("f1", "buy", oid="900")]},
            positions={"success": True, "result": [{"size": 1}]})
        r = Reconciler(conn, client, product_id=84)
        r.reconcile()
        assert storage.get_order(conn, "crashed")["status"] == "unknown"
        assert storage.get_order(conn, "crashed")["filled_ts"] is None

    # --- 6: adversarial ---------------------------------------------------
    def test_submitted_is_never_sent_to_cancelled(self, conn):
        """Direct guard on the arc the defect used."""
        with pytest.raises(IllegalTransition):
            transition(OrderState.SUBMITTED, OrderState.CANCELLED,
                       reconcile_only=True)

    @pytest.mark.parametrize("state", [s.value for s in OPEN_STATES])
    def test_no_open_state_ever_raises_on_a_flat_exchange(self, conn, state):
        """Generalises past the one state that happened to bite."""
        _seed_order(conn, f"o-{state}", state, "900")
        r = Reconciler(conn, self._flat_client(), product_id=84)
        try:
            r.reconcile()
        except IllegalTransition as exc:
            pytest.fail(f"{state} raised: {exc}")
        assert is_terminal(OrderState(storage.get_order(conn, f"o-{state}")["status"])) \
            or storage.get_order(conn, f"o-{state}")["status"] == "unknown"


class TestExecutorOutcomeSemanticsUnchanged:
    """Gate 5C.18-F guard: the submission layer was correct and must stay so.

    The audit established that every API outcome already moves the row off
    SUBMITTED. The fix is confined to the reconciler; if any of these change,
    the crash window has been widened rather than the recovery path repaired.
    """

    @staticmethod
    def _http(status, body=b""):
        """Returns the HTTPError itself -- never wrap this again."""
        resp = requests.Response()
        resp.status_code = status
        resp._content = body
        return requests.exceptions.HTTPError(response=resp)

    def _ex(self, conn, place):
        return TestnetExecutor(conn, _FakeClient(place=place), _risk())

    def test_4xx_still_rejected(self, conn):
        err = self._http(400, b'{"error":{"code":"x"}}')
        state, _ = self._ex(conn, err).submit(_intent(), _account(), now=NOON)
        assert state is OrderState.REJECTED

    def test_5xx_still_unknown(self, conn):
        err = self._http(503)
        state, _ = self._ex(conn, err).submit(_intent(), _account(), now=NOON)
        assert state is OrderState.UNKNOWN

    def test_timeout_still_unknown(self, conn):
        state, _ = self._ex(conn, requests.exceptions.Timeout("t")).submit(
            _intent(), _account(), now=NOON)
        assert state is OrderState.UNKNOWN

    def test_connection_reset_still_unknown(self, conn):
        state, _ = self._ex(conn, requests.exceptions.ConnectionError("reset")).submit(
            _intent(), _account(), now=NOON)
        assert state is OrderState.UNKNOWN

    def test_success_still_acked(self, conn):
        state, _ = self._ex(conn, {"success": True, "result": {"id": 4242}}).submit(
            _intent(), _account(), now=NOON)
        assert state is OrderState.ACKED

    def test_no_outcome_leaves_the_row_submitted(self, conn):
        """The property the whole audit rests on."""
        cases = [
            requests.exceptions.Timeout("t"),
            requests.exceptions.ConnectionError("reset"),
            self._http(400, b'{"error":{"code":"x"}}'),
            self._http(503),
            {"success": False},
            {"success": True, "result": {"id": 1}},
        ]
        for i, place in enumerate(cases):
            coid = f"outcome-{i}"
            self._ex(conn, place).submit(
                _intent(client_order_id=coid), _account(), now=NOON)
            assert storage.get_order(conn, coid)["status"] != "submitted", place
