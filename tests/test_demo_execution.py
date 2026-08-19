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
from src.execution import fills
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
    AccountSnapshot, OrderIntent, RiskManager, compute_trade_risk_usd,
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
        storage.write_order(conn, client_order_id="entry-1", instrument_id=84,
                            side="buy", order_type="limit_order", quantity=1.0,
                            price=64000.0, status="acked", strategy="t",
                            placed_ts="2026-08-19T04:00:00+00:00")
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
        storage.write_order(conn, client_order_id="entry-1", instrument_id=84,
                            side="buy", order_type="limit_order", quantity=1.0,
                            price=64000.0, status="acked", strategy="t",
                            placed_ts="2026-08-19T04:00:00+00:00")
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
            positions={"success": True, "result": [{"size": 1}]})
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
            positions={"success": True, "result": [{"size": 1}]})
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
