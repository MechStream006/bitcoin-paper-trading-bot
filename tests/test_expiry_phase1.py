"""Phase 1 expiry infrastructure tests.

All tests are pure unit tests: no network, no DB, no filesystem.
Clocks are injected via now_fn for full determinism and replay safety.

Coverage:
  - classify_expiry_class: DAILY / WEEKLY / MONTHLY / UNKNOWN, precedence
  - classify_expiry_phase: all 7 phases across the 0-to-∞ seconds range
  - No-drift invariant: same settlement_time always produces same ExpiryClass
  - DISCOVERY vs CONFIRMED classification mode
  - ExpiryCalendar.derive_posture: all 5 posture values
  - Posture transitions across a mocked 24-hour timeline
  - build_calendar: multi-instrument universe, sort order, divergence events
  - parse_settlement_time: Z suffix, +00:00, naive, malformed
  - cycle_elapsed_sec / cycle_fraction: correct relative to 12:00 UTC anchor
  - ExpiryContext.observe: normal flow, restart-safe recomputation
  - ExpiryContext.health: pre-snapshot, healthy, stale
  - ExpiryContext drift detection via prev_class_map
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest

from src.expiry.expiry_calendar import (
    ExpiryCalendar,
    build_calendar,
    classify_expiry_class,
    classify_expiry_phase,
)
from src.expiry.expiry_clock import (
    cycle_elapsed_sec,
    cycle_fraction,
    last_cycle_anchor,
    next_cycle_anchor,
    parse_settlement_time,
)
from src.expiry.expiry_context import ExpiryContext
from src.expiry.models import (
    ClassificationMode,
    ExpiryClass,
    ExpiryConfig,
    ExpiryPhase,
    ExpiryPosture,
)

_UTC = timezone.utc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _at(dt: datetime):
    """Return a zero-arg callable that always returns dt (injectable clock)."""
    return lambda: dt


def _inst(symbol, settle_dt, strike=95000.0, atm_dist=0.0, ctype="call_options"):
    """Build a minimal instrument dict matching select_universe() output."""
    return {
        "symbol": symbol,
        "contract_type": ctype,
        "strike_price": strike,
        "settlement_time": settle_dt.isoformat() if isinstance(settle_dt, datetime) else settle_dt,
        "atm_distance": atm_dist,
    }


class _MockRefresher:
    """Minimal duck-type of ChainRefresher for ExpiryContext tests."""

    def __init__(self, universe, mark_price):
        self._universe = universe
        self._mark = mark_price

    def current_snapshot(self):
        class _S:
            pass
        s = _S()
        s.universe = self._universe
        s.mark_price = self._mark
        return s


class _EmptyRefresher:
    def current_snapshot(self):
        return None


# ---------------------------------------------------------------------------
# Key reference dates (UTC, all at 12:00 settlement time)
#
# May 2026 Fridays: 1, 8, 15, 22, 29
#   May 29 = last Friday of May → MONTHLY
#   May 22 = Friday (not last)  → WEEKLY
#   May 18 = Monday             → DAILY
# ---------------------------------------------------------------------------

SETTLE_MONDAY  = datetime(2026, 5, 18, 12, 0, 0, tzinfo=_UTC)   # DAILY
SETTLE_FRIDAY  = datetime(2026, 5, 22, 12, 0, 0, tzinfo=_UTC)   # WEEKLY
SETTLE_MONTHLY = datetime(2026, 5, 29, 12, 0, 0, tzinfo=_UTC)   # MONTHLY
SETTLE_FAR     = datetime(2026, 7, 17, 12, 0, 0, tzinfo=_UTC)   # > 30d → DISCOVERY


# ===========================================================================
# Part 1: classify_expiry_class
# ===========================================================================

class TestClassifyExpiryClass:
    cfg = ExpiryConfig()

    def test_monday_is_daily(self):
        assert classify_expiry_class(SETTLE_MONDAY, self.cfg) == ExpiryClass.DAILY

    def test_friday_non_last_is_weekly(self):
        assert classify_expiry_class(SETTLE_FRIDAY, self.cfg) == ExpiryClass.WEEKLY

    def test_last_friday_of_month_is_monthly(self):
        assert classify_expiry_class(SETTLE_MONTHLY, self.cfg) == ExpiryClass.MONTHLY

    def test_monthly_takes_precedence_over_weekly(self):
        # Both WEEKLY and MONTHLY conditions apply to the last Friday;
        # MONTHLY must win.
        result = classify_expiry_class(SETTLE_MONTHLY, self.cfg)
        assert result == ExpiryClass.MONTHLY
        assert result != ExpiryClass.WEEKLY

    def test_none_settlement_is_unknown(self):
        assert classify_expiry_class(None, self.cfg) == ExpiryClass.UNKNOWN

    def test_naive_datetime_is_unknown(self):
        naive = datetime(2026, 5, 22, 12, 0, 0)   # no tzinfo
        assert classify_expiry_class(naive, self.cfg) == ExpiryClass.UNKNOWN

    def test_sunday_is_daily(self):
        sunday = datetime(2026, 5, 17, 12, 0, 0, tzinfo=_UTC)  # Sunday
        assert classify_expiry_class(sunday, self.cfg) == ExpiryClass.DAILY

    def test_all_fridays_in_may_2026(self):
        """Verify MONTHLY only fires on the last Friday (29th)."""
        fridays = [
            (datetime(2026, 5, 1,  12, 0, 0, tzinfo=_UTC), ExpiryClass.WEEKLY),
            (datetime(2026, 5, 8,  12, 0, 0, tzinfo=_UTC), ExpiryClass.WEEKLY),
            (datetime(2026, 5, 15, 12, 0, 0, tzinfo=_UTC), ExpiryClass.WEEKLY),
            (datetime(2026, 5, 22, 12, 0, 0, tzinfo=_UTC), ExpiryClass.WEEKLY),
            (datetime(2026, 5, 29, 12, 0, 0, tzinfo=_UTC), ExpiryClass.MONTHLY),
        ]
        for dt, expected in fridays:
            assert classify_expiry_class(dt, self.cfg) == expected, (
                f"{dt.date()} → expected {expected.value}"
            )

    def test_no_drift_same_inputs_same_output(self):
        """Anti-drift guarantee: same settlement_time always yields the same class."""
        targets = [SETTLE_MONDAY, SETTLE_FRIDAY, SETTLE_MONTHLY]
        for dt in targets:
            results = {classify_expiry_class(dt, self.cfg) for _ in range(5)}
            assert len(results) == 1, (
                f"drift detected for {dt.date()}: got {results}"
            )

    def test_config_injection_changes_weekly_day(self):
        """Custom weekly_weekday=2 (Wednesday) changes classification."""
        cfg_wed = ExpiryConfig(weekly_weekday=2)
        wednesday = datetime(2026, 5, 20, 12, 0, 0, tzinfo=_UTC)   # Wednesday
        friday    = datetime(2026, 5, 22, 12, 0, 0, tzinfo=_UTC)   # Friday
        assert classify_expiry_class(wednesday, cfg_wed) == ExpiryClass.WEEKLY
        assert classify_expiry_class(friday,    cfg_wed) == ExpiryClass.DAILY


# ===========================================================================
# Part 2: classify_expiry_phase
# ===========================================================================

class TestClassifyExpiryPhase:
    cfg = ExpiryConfig()

    def test_already_settled(self):
        assert classify_expiry_phase(-1.0, self.cfg) == ExpiryPhase.SETTLED
        assert classify_expiry_phase(-86400.0, self.cfg) == ExpiryPhase.SETTLED

    def test_settlement_window(self):
        # 30 min before settlement: 1800 < 3600 → SETTLEMENT
        assert classify_expiry_phase(1800.0, self.cfg) == ExpiryPhase.SETTLEMENT

    def test_expiry_intensive(self):
        # 2 h before: 7200, < 14400 (4h) but > 3600 (1h) → EXPIRY_INTENSIVE
        assert classify_expiry_phase(7200.0, self.cfg) == ExpiryPhase.EXPIRY_INTENSIVE

    def test_expiry_day(self):
        # 6 h before: 21600, < 43200 (12h) but > 14400 (4h) → EXPIRY_DAY
        assert classify_expiry_phase(21600.0, self.cfg) == ExpiryPhase.EXPIRY_DAY

    def test_pre_expiry(self):
        # 18 h before: 64800, < 86400 (24h) but > 43200 (12h) → PRE_EXPIRY
        assert classify_expiry_phase(64800.0, self.cfg) == ExpiryPhase.PRE_EXPIRY

    def test_active(self):
        # 3 days: > 1 day but < 7 days → ACTIVE
        assert classify_expiry_phase(3 * 86400.0, self.cfg) == ExpiryPhase.ACTIVE

    def test_distant(self):
        # 10 days: > 7 days → DISTANT
        assert classify_expiry_phase(10 * 86400.0, self.cfg) == ExpiryPhase.DISTANT

    def test_boundary_conditions(self):
        # Boundaries use strict > so the exact threshold value falls INTO the LOWER phase.
        # e.g. hours=12 is NOT > 12 → falls to EXPIRY_DAY (not PRE_EXPIRY).
        assert classify_expiry_phase(3600.0 * 12, self.cfg) == ExpiryPhase.EXPIRY_DAY
        assert classify_expiry_phase(3600.0 * 4,  self.cfg) == ExpiryPhase.EXPIRY_INTENSIVE
        assert classify_expiry_phase(3600.0 * 1,  self.cfg) == ExpiryPhase.SETTLEMENT
        assert classify_expiry_phase(0.0,          self.cfg) == ExpiryPhase.SETTLEMENT
        # Epsilon above threshold → higher phase
        assert classify_expiry_phase(3600.0 * 12 + 1, self.cfg) == ExpiryPhase.PRE_EXPIRY
        assert classify_expiry_phase(3600.0 * 4  + 1, self.cfg) == ExpiryPhase.EXPIRY_DAY
        assert classify_expiry_phase(3600.0 * 1  + 1, self.cfg) == ExpiryPhase.EXPIRY_INTENSIVE


# ===========================================================================
# Part 3: parse_settlement_time
# ===========================================================================

class TestParseSettlementTime:

    def test_z_suffix(self):
        dt = parse_settlement_time("2026-05-20T12:00:00Z")
        assert dt is not None
        assert dt.tzinfo is not None
        assert dt == datetime(2026, 5, 20, 12, 0, 0, tzinfo=_UTC)

    def test_plus00_suffix(self):
        dt = parse_settlement_time("2026-05-20T12:00:00+00:00")
        assert dt is not None
        assert dt == datetime(2026, 5, 20, 12, 0, 0, tzinfo=_UTC)

    def test_naive_assumed_utc(self):
        dt = parse_settlement_time("2026-05-20T12:00:00")
        assert dt is not None
        assert dt.tzinfo is not None
        assert dt == datetime(2026, 5, 20, 12, 0, 0, tzinfo=_UTC)

    def test_malformed_returns_none(self):
        assert parse_settlement_time("not-a-date") is None
        assert parse_settlement_time("") is None
        assert parse_settlement_time("2026-99-99T00:00:00Z") is None

    def test_whitespace_stripped(self):
        dt = parse_settlement_time("  2026-05-20T12:00:00Z  ")
        assert dt is not None
        assert dt == datetime(2026, 5, 20, 12, 0, 0, tzinfo=_UTC)


# ===========================================================================
# Part 4: cycle_elapsed_sec / cycle_fraction
# ===========================================================================

class TestCycleMetrics:

    def test_just_after_anchor_elapsed_near_zero(self):
        # 12:00:01 UTC → 1 second elapsed
        t = datetime(2026, 5, 20, 12, 0, 1, tzinfo=_UTC)
        elapsed = cycle_elapsed_sec(_at(t))
        assert 0 < elapsed < 5

    def test_before_anchor_uses_previous_day(self):
        # 10:00 UTC on May 20 → last anchor was May 19 12:00 UTC → 22 hours
        t = datetime(2026, 5, 20, 10, 0, 0, tzinfo=_UTC)
        elapsed = cycle_elapsed_sec(_at(t))
        expected = 22 * 3600.0
        assert abs(elapsed - expected) < 2

    def test_cycle_fraction_just_after_anchor(self):
        t = datetime(2026, 5, 20, 12, 0, 1, tzinfo=_UTC)
        f = cycle_fraction(_at(t))
        assert 0 <= f < 0.001

    def test_cycle_fraction_approaches_1_before_anchor(self):
        # 11:59:59 = 1 second before next 12:00 → fraction ≈ 1.0
        t = datetime(2026, 5, 20, 11, 59, 59, tzinfo=_UTC)
        f = cycle_fraction(_at(t))
        assert f > 0.999

    def test_last_cycle_anchor_same_day(self):
        t = datetime(2026, 5, 20, 15, 0, 0, tzinfo=_UTC)
        anchor = last_cycle_anchor(t)
        assert anchor == datetime(2026, 5, 20, 12, 0, 0, tzinfo=_UTC)

    def test_last_cycle_anchor_previous_day(self):
        t = datetime(2026, 5, 20, 10, 0, 0, tzinfo=_UTC)
        anchor = last_cycle_anchor(t)
        assert anchor == datetime(2026, 5, 19, 12, 0, 0, tzinfo=_UTC)

    def test_next_cycle_anchor(self):
        t = datetime(2026, 5, 20, 15, 0, 0, tzinfo=_UTC)
        anchor = next_cycle_anchor(t)
        assert anchor == datetime(2026, 5, 21, 12, 0, 0, tzinfo=_UTC)

    def test_next_cycle_anchor_before_todays_settlement(self):
        t = datetime(2026, 5, 20, 10, 0, 0, tzinfo=_UTC)
        anchor = next_cycle_anchor(t)
        assert anchor == datetime(2026, 5, 20, 12, 0, 0, tzinfo=_UTC)


# ===========================================================================
# Part 5: ExpiryCalendar + build_calendar
# ===========================================================================

class TestBuildCalendar:
    cfg = ExpiryConfig()

    def _universe(self, *settle_dts):
        """Build a minimal universe list with one call option per settlement."""
        return [
            _inst(f"C-BTC-95000-{dt.strftime('%d%m%y')}", dt)
            for dt in settle_dts
        ]

    def test_single_instrument_daily(self):
        # 20 hours before SETTLE_MONDAY → EXPIRY_DAY phase
        now = SETTLE_MONDAY - timedelta(hours=20)
        universe = self._universe(SETTLE_MONDAY)
        cal = build_calendar(universe, 95000.0, self.cfg, _at(now))
        assert len(cal.records) == 1
        r = cal.records[0]
        assert r.expiry_class == ExpiryClass.DAILY
        assert r.expiry_phase == ExpiryPhase.PRE_EXPIRY  # 20h > 12h threshold
        assert r.classification_mode == ClassificationMode.CONFIRMED

    def test_sort_order_settlement_then_strike(self):
        settle_a = datetime(2026, 5, 20, 12, 0, 0, tzinfo=_UTC)   # earlier
        settle_b = datetime(2026, 5, 21, 12, 0, 0, tzinfo=_UTC)   # later
        universe = [
            _inst("C-BTC-96000-200526", settle_a, strike=96000.0),
            _inst("C-BTC-95000-210526", settle_b, strike=95000.0),
            _inst("C-BTC-95000-200526", settle_a, strike=95000.0),
        ]
        now = settle_a - timedelta(hours=5)
        cal = build_calendar(universe, 95500.0, self.cfg, _at(now))
        assert cal.records[0].strike_price == 95000.0   # settle_a, lower strike first
        assert cal.records[1].strike_price == 96000.0   # settle_a, higher strike
        assert cal.records[2].settlement_time == settle_b

    def test_parse_failure_becomes_unknown_divergence(self):
        universe = [{"symbol": "BAD", "contract_type": "call_options",
                     "strike_price": 0, "settlement_time": "INVALID", "atm_distance": 0}]
        now = datetime(2026, 5, 20, 10, 0, 0, tzinfo=_UTC)
        cal = build_calendar(universe, 95000.0, self.cfg, _at(now))
        assert len(cal.divergence_events) == 1
        assert "PARSE_FAIL:BAD" in cal.divergence_events[0]
        assert cal.records[0].expiry_class == ExpiryClass.UNKNOWN

    def test_coverage_by_class(self):
        universe = [
            _inst("A", SETTLE_MONDAY),   # DAILY
            _inst("B", SETTLE_FRIDAY),   # WEEKLY
            _inst("C", SETTLE_MONTHLY),  # MONTHLY
        ]
        now = SETTLE_MONDAY - timedelta(hours=5)
        cal = build_calendar(universe, 95000.0, self.cfg, _at(now))
        cov = cal.coverage_by_class()
        assert cov.get(ExpiryClass.DAILY) == 1
        assert cov.get(ExpiryClass.WEEKLY) == 1
        assert cov.get(ExpiryClass.MONTHLY) == 1

    def test_no_drift_via_prev_class_map(self):
        # Build calendar, extract class map, re-classify — should produce no drift events.
        now = SETTLE_FRIDAY - timedelta(hours=10)
        universe = [_inst("A", SETTLE_FRIDAY)]
        cal1 = build_calendar(universe, 95000.0, self.cfg, _at(now))
        prev_map = {r.settlement_time.isoformat(): r.expiry_class for r in cal1.records}
        cal2 = build_calendar(universe, 95000.0, self.cfg, _at(now), prev_class_map=prev_map)
        drift_events = [e for e in cal2.divergence_events if "DRIFT" in e]
        assert drift_events == [], f"unexpected drift: {drift_events}"

    def test_discovery_mode_for_far_future(self):
        now = datetime(2026, 5, 20, 12, 0, 0, tzinfo=_UTC)
        universe = [_inst("FAR", SETTLE_FAR)]  # > 30 days from now
        cal = build_calendar(universe, 95000.0, self.cfg, _at(now))
        assert cal.records[0].classification_mode == ClassificationMode.DISCOVERY

    def test_confirmed_mode_for_near_future(self):
        now = SETTLE_FRIDAY - timedelta(hours=2)
        universe = [_inst("NEAR", SETTLE_FRIDAY)]
        cal = build_calendar(universe, 95000.0, self.cfg, _at(now))
        assert cal.records[0].classification_mode == ClassificationMode.CONFIRMED

    def test_nearest_record(self):
        universe = [
            _inst("A", SETTLE_MONDAY),
            _inst("B", SETTLE_FRIDAY),
        ]
        now = SETTLE_MONDAY - timedelta(hours=5)
        cal = build_calendar(universe, 95000.0, self.cfg, _at(now))
        nearest = cal.nearest_record()
        assert nearest is not None
        assert nearest.symbol == "A"   # SETTLE_MONDAY is earlier

    def test_no_nearest_when_all_settled(self):
        settle_past = datetime(2020, 1, 1, 12, 0, 0, tzinfo=_UTC)
        universe = [_inst("OLD", settle_past)]
        now = datetime(2026, 5, 20, 12, 0, 0, tzinfo=_UTC)
        cal = build_calendar(universe, 95000.0, self.cfg, _at(now))
        assert cal.nearest_record() is None
        assert cal.seconds_to_nearest() is None


# ===========================================================================
# Part 6: ExpiryCalendar.derive_posture — posture timeline
# ===========================================================================

class TestDerivePosture:
    """Walk through a mocked 24-hour timeline relative to SETTLE_MONDAY.

    SETTLE_MONDAY = 2026-05-18T12:00:00Z

    Timeline:
      normal     = 2026-05-17T18:00Z   →  18 h before settlement → NORMAL
      approaching = 2026-05-18T02:00Z  →  10 h before settlement → APPROACHING
      intensive   = 2026-05-18T09:00Z  →   3 h before settlement → INTENSIVE
      settlement  = 2026-05-18T11:00Z  →   1 h before settlement → SETTLEMENT
      rollover    = 2026-05-18T12:02Z  →   2 min AFTER settlement → ROLLOVER
      post_roll   = 2026-05-18T12:10Z  →  10 min AFTER → NORMAL (new cycle)
    """

    cfg = ExpiryConfig()

    def _cal(self, now: datetime):
        universe = [_inst("C-BTC-95000-180526", SETTLE_MONDAY)]
        return build_calendar(universe, 95000.0, self.cfg, _at(now))

    def test_normal(self):
        now = datetime(2026, 5, 17, 18, 0, 0, tzinfo=_UTC)  # 18 h before
        cal = self._cal(now)
        assert cal.derive_posture(self.cfg) == ExpiryPosture.NORMAL

    def test_approaching(self):
        now = datetime(2026, 5, 18, 2, 0, 0, tzinfo=_UTC)   # 10 h before
        cal = self._cal(now)
        assert cal.derive_posture(self.cfg) == ExpiryPosture.APPROACHING

    def test_intensive(self):
        now = datetime(2026, 5, 18, 9, 0, 0, tzinfo=_UTC)   # 3 h before
        cal = self._cal(now)
        assert cal.derive_posture(self.cfg) == ExpiryPosture.INTENSIVE

    def test_settlement(self):
        # 30 min before settlement: 1800s < settlement_window_sec (3600s) → SETTLEMENT
        # Note: exactly 3600s uses strict < so it falls into INTENSIVE.
        now = datetime(2026, 5, 18, 11, 30, 0, tzinfo=_UTC)
        cal = self._cal(now)
        assert cal.derive_posture(self.cfg) == ExpiryPosture.SETTLEMENT

    def test_rollover_just_after_settlement(self):
        # 2 min after SETTLE_MONDAY (seconds_to_settlement = -120)
        # -300 < -120 < 0 → ROLLOVER
        now = datetime(2026, 5, 18, 12, 2, 0, tzinfo=_UTC)
        cal = self._cal(now)
        assert cal.derive_posture(self.cfg) == ExpiryPosture.ROLLOVER

    def test_normal_after_rollover_window(self):
        # 10 min after settlement → outside rollover window (300 s) → NORMAL
        # (no unsettled contract in this single-instrument universe)
        now = datetime(2026, 5, 18, 12, 10, 0, tzinfo=_UTC)
        cal = self._cal(now)
        posture = cal.derive_posture(self.cfg)
        assert posture == ExpiryPosture.NORMAL   # empty unsettled → NORMAL

    def test_posture_sequence_monotone(self):
        """Posture should progress through exactly the expected states."""
        timeline = [
            (datetime(2026, 5, 17, 18, 0, 0, tzinfo=_UTC), ExpiryPosture.NORMAL),
            (datetime(2026, 5, 18,  2, 0, 0, tzinfo=_UTC), ExpiryPosture.APPROACHING),
            (datetime(2026, 5, 18,  9, 0, 0, tzinfo=_UTC), ExpiryPosture.INTENSIVE),
            (datetime(2026, 5, 18, 11, 30, 0, tzinfo=_UTC), ExpiryPosture.SETTLEMENT),  # 30 min before
            (datetime(2026, 5, 18, 12, 2, 0, tzinfo=_UTC), ExpiryPosture.ROLLOVER),
        ]
        for now, expected in timeline:
            cal = self._cal(now)
            got = cal.derive_posture(self.cfg)
            assert got == expected, (
                f"at {now.strftime('%H:%MZ')}: expected {expected.value}, got {got.value}"
            )


# ===========================================================================
# Part 7: ExpiryContext
# ===========================================================================

class TestExpiryContext:
    cfg = ExpiryConfig()

    def _universe_around(self, settle: datetime):
        return [_inst("C-BTC-95000", settle, atm_dist=0.0)]

    def test_observe_produces_snapshot(self):
        now = SETTLE_MONDAY - timedelta(hours=5)
        universe = self._universe_around(SETTLE_MONDAY)
        ctx = ExpiryContext(
            _MockRefresher(universe, 95000.0),
            config=self.cfg,
            now_fn=_at(now),
        )
        snap = ctx.observe()
        assert snap is not None
        assert snap.posture == ExpiryPosture.APPROACHING
        assert snap.observe_count == 1
        assert snap.nearest_expiry_class == ExpiryClass.DAILY

    def test_observe_increments_count(self):
        now = SETTLE_MONDAY - timedelta(hours=5)
        universe = self._universe_around(SETTLE_MONDAY)
        ctx = ExpiryContext(
            _MockRefresher(universe, 95000.0),
            config=self.cfg,
            now_fn=_at(now),
        )
        ctx.observe()
        ctx.observe()
        snap = ctx.observe()
        assert snap.observe_count == 3

    def test_current_posture_defaults_to_normal_before_first_observe(self):
        ctx = ExpiryContext(_EmptyRefresher(), config=self.cfg)
        assert ctx.current_posture() == ExpiryPosture.NORMAL

    def test_observe_returns_none_when_no_chain_snapshot(self):
        ctx = ExpiryContext(_EmptyRefresher(), config=self.cfg)
        result = ctx.observe()
        assert result is None

    def test_health_no_snapshot(self):
        ctx = ExpiryContext(_EmptyRefresher(), config=self.cfg)
        h = ctx.health()
        assert h.healthy   # no snapshot yet but zero failures → healthy
        assert "no_snapshot" in h.details

    def test_health_after_successful_observe(self):
        now = SETTLE_MONDAY - timedelta(hours=3)
        universe = self._universe_around(SETTLE_MONDAY)
        # Use a large stale_threshold so the snapshot (captured at a past fixture
        # time, checked against real UTC now) is not considered stale in tests.
        ctx = ExpiryContext(
            _MockRefresher(universe, 95000.0),
            config=self.cfg,
            now_fn=_at(now),
            stale_threshold_sec=999_999.0,
        )
        ctx.observe()
        h = ctx.health()
        assert h.healthy
        assert "posture=" in h.details

    def test_health_stale_after_threshold(self):
        # Build context with a stale_threshold_sec of 0 (instantly stale)
        now = SETTLE_MONDAY - timedelta(hours=3)
        universe = self._universe_around(SETTLE_MONDAY)
        ctx = ExpiryContext(
            _MockRefresher(universe, 95000.0),
            config=self.cfg,
            now_fn=_at(now),
            stale_threshold_sec=0.0,   # any age > 0 is stale
        )
        ctx.observe()
        h = ctx.health()
        assert not h.healthy   # snapshot age > 0 > threshold

    def test_restart_safe_recomputation(self):
        """Recreating ExpiryContext and re-observing must yield the same result."""
        now = SETTLE_MONDAY - timedelta(hours=5)
        universe = self._universe_around(SETTLE_MONDAY)

        def _build_ctx():
            return ExpiryContext(
                _MockRefresher(universe, 95000.0),
                config=self.cfg,
                now_fn=_at(now),
            )

        snap1 = _build_ctx().observe()
        snap2 = _build_ctx().observe()

        assert snap1.posture == snap2.posture
        assert snap1.nearest_expiry_class == snap2.nearest_expiry_class
        assert abs(snap1.seconds_to_nearest - snap2.seconds_to_nearest) < 1.0

    def test_no_drift_across_consecutive_observes(self):
        """Same universe, two consecutive observes → no drift events."""
        now = SETTLE_FRIDAY - timedelta(hours=10)
        universe = [
            _inst("C-BTC-95000-220526", SETTLE_FRIDAY),
            _inst("C-BTC-95000-180526", SETTLE_MONDAY),
        ]
        ctx = ExpiryContext(
            _MockRefresher(universe, 95000.0),
            config=self.cfg,
            now_fn=_at(now),
        )
        snap1 = ctx.observe()
        snap2 = ctx.observe()
        drift = [e for e in snap2.divergence_events if "DRIFT" in e]
        assert drift == [], f"unexpected drift events: {drift}"

    def test_start_stop_lifecycle(self):
        ctx = ExpiryContext(_EmptyRefresher(), config=self.cfg)
        ctx.start()
        ctx.start()   # idempotent — second call must not raise
        ctx.stop()
        assert not ctx._started

    def test_summary_dict_before_observe(self):
        ctx = ExpiryContext(_EmptyRefresher(), config=self.cfg)
        d = ctx.summary_dict()
        assert d["posture"] == "UNKNOWN"

    def test_summary_dict_after_observe(self):
        now = SETTLE_MONDAY - timedelta(hours=2)
        universe = self._universe_around(SETTLE_MONDAY)
        ctx = ExpiryContext(
            _MockRefresher(universe, 95000.0),
            config=self.cfg,
            now_fn=_at(now),
        )
        ctx.observe()
        d = ctx.summary_dict()
        assert d["posture"] == ExpiryPosture.INTENSIVE.value
        assert d["observe_count"] == 1
        assert d["nearest_expiry_class"] == ExpiryClass.DAILY.value


# ===========================================================================
# Part 8: ExpiryContext posture timeline (inject now_fn)
# ===========================================================================

class TestExpiryContextPostureTimeline:
    """Walk through the same SETTLE_MONDAY timeline via ExpiryContext."""

    cfg = ExpiryConfig()

    def _ctx(self, now: datetime):
        universe = [_inst("C-BTC-95000-180526", SETTLE_MONDAY)]
        return ExpiryContext(
            _MockRefresher(universe, 95000.0),
            config=self.cfg,
            now_fn=_at(now),
        )

    def test_full_timeline(self):
        timeline = [
            (datetime(2026, 5, 17, 18,  0, 0, tzinfo=_UTC), ExpiryPosture.NORMAL),
            (datetime(2026, 5, 18,  2,  0, 0, tzinfo=_UTC), ExpiryPosture.APPROACHING),
            (datetime(2026, 5, 18,  9,  0, 0, tzinfo=_UTC), ExpiryPosture.INTENSIVE),
            (datetime(2026, 5, 18, 11, 30, 0, tzinfo=_UTC), ExpiryPosture.SETTLEMENT),  # 30 min before
            (datetime(2026, 5, 18, 12,  2, 0, tzinfo=_UTC), ExpiryPosture.ROLLOVER),
        ]
        for now, expected in timeline:
            ctx = self._ctx(now)
            snap = ctx.observe()
            assert snap is not None
            assert snap.posture == expected, (
                f"at {now.strftime('%H:%MZ')}: "
                f"expected {expected.value}, got {snap.posture.value}"
            )


# ===========================================================================
# Part 9: ExpiryRecord + ExpirySnapshot immutability (frozen dataclass check)
# ===========================================================================

class TestExpiryRecord:

    def test_frozen(self):
        from src.expiry.models import ExpiryRecord
        r = ExpiryRecord(
            symbol="C-BTC-95000-200526",
            contract_type="call_options",
            strike_price=95000.0,
            settlement_time=SETTLE_MONDAY,
            expiry_class=ExpiryClass.DAILY,
            expiry_phase=ExpiryPhase.EXPIRY_INTENSIVE,
            seconds_to_settlement=7200.0,
            atm_distance=0.0,
            classification_mode=ClassificationMode.CONFIRMED,
        )
        with pytest.raises((AttributeError, TypeError)):
            r.symbol = "CHANGED"   # must be immutable

    def test_hashable(self):
        from src.expiry.models import ExpiryRecord
        r = ExpiryRecord(
            symbol="C-BTC-95000-200526",
            contract_type="call_options",
            strike_price=95000.0,
            settlement_time=SETTLE_MONDAY,
            expiry_class=ExpiryClass.DAILY,
            expiry_phase=ExpiryPhase.DISTANT,
            seconds_to_settlement=999999.0,
            atm_distance=0.0,
            classification_mode=ClassificationMode.CONFIRMED,
        )
        assert hash(r) == hash(r)   # frozen → hashable
        assert r in {r}


# ===========================================================================
# Part 10: Smoke test (import + basic end-to-end)
# ===========================================================================

class TestSmoke:

    def test_public_api_importable(self):
        from src.expiry import (   # noqa: F401
            ExpiryClass, ExpiryPhase, ExpiryPosture, ClassificationMode,
            ExpiryConfig, ExpiryRecord, ExpirySnapshot, ExpiryClassDrift,
            ExpiryCalendar, build_calendar, classify_expiry_class,
            classify_expiry_phase, ExpiryContext,
            utc_now, parse_settlement_time, seconds_to_settlement,
            cycle_elapsed_sec, cycle_fraction, last_cycle_anchor, next_cycle_anchor,
        )

    def test_end_to_end_two_expiries(self):
        """Realistic mini universe: 2 expiries, 3 strikes each."""
        settle1 = datetime(2026, 5, 20, 12, 0, 0, tzinfo=_UTC)  # DAILY (Wed)
        settle2 = datetime(2026, 5, 22, 12, 0, 0, tzinfo=_UTC)  # WEEKLY (Fri)
        universe = [
            _inst(f"C-BTC-{s}-200526", settle1, strike=s)
            for s in [94000.0, 95000.0, 96000.0]
        ] + [
            _inst(f"C-BTC-{s}-220526", settle2, strike=s)
            for s in [94000.0, 95000.0, 96000.0]
        ]
        now = settle1 - timedelta(hours=3)   # 3h before first settlement → INTENSIVE
        ctx = ExpiryContext(
            _MockRefresher(universe, 95000.0),
            config=ExpiryConfig(),
            now_fn=_at(now),
        )
        snap = ctx.observe()

        assert snap is not None
        assert snap.posture == ExpiryPosture.INTENSIVE
        assert snap.observe_count == 1
        assert len(snap.records) == 6
        assert snap.nearest_expiry_class == ExpiryClass.DAILY
        # Class coverage
        class_dict = dict(snap.n_by_class)
        assert class_dict.get(ExpiryClass.DAILY) == 3
        assert class_dict.get(ExpiryClass.WEEKLY) == 3
