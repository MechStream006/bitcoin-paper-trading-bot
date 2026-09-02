"""Tests for the held-direction reduction and the `consumed_direction` feature.

Synthetic ledgers with explicit run dates, plus a real-corpus block gated on the
extracted production slices that checks the reconstruction against the eight
real execution decisions.
"""
from __future__ import annotations

import os
import pathlib
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_RESEARCH = _ROOT / "research"
if str(_RESEARCH) not in sys.path:
    sys.path.insert(0, str(_RESEARCH))

from alignment import align as AL  # noqa: E402
from alignment import features as FT  # noqa: E402
from alignment import forward_trades as FWT  # noqa: E402
from alignment import timebase as TB  # noqa: E402
from feature_store import decision_store as DS  # noqa: E402

UTC = timezone.utc
SIG = "g3_momentum_tsmom"

#: The contract hash `signal_position` has carried since the rows in
#: `decision_features.db` were written. Pinned so that adding a feature beside
#: it cannot silently redefine it.
SIGNAL_POSITION_HASH = (
    "6a2dcdb053c8f7eadcb1e9441513b034b4a90d8abd41e51c138dcee643d4d7dc")


def T(*a) -> datetime:
    return datetime(*a, tzinfo=UTC)


def avail(day: str) -> datetime:
    """When run_date `day`'s work becomes readable: D+1 at 01:00 UTC."""
    return datetime.fromisoformat(day).replace(tzinfo=UTC) + timedelta(hours=25)


def trade(entry_id, entry_date, direction, planned_exit_date, *,
          signal_id=SIG, open=0):
    return FWT.TradeRow(entry_id=entry_id, signal_id=signal_id,
                        entry_date=entry_date, direction=direction,
                        planned_exit_date=planned_exit_date, open=open)


def runs(days, *, signal_id=SIG, status=FWT.OK):
    return [FWT.RunRow(signal_id=signal_id, run_date=d, status=status)
            for d in days]


def daily(start: str, end: str, *, signal_id=SIG):
    d0, d1 = datetime.fromisoformat(start), datetime.fromisoformat(end)
    out, d = [], d0
    while d <= d1:
        out.append(FWT.RunRow(signal_id=signal_id, run_date=d.date().isoformat(),
                              status=FWT.OK))
        d += timedelta(days=1)
    return out


def load_of(obs):
    return {"held_direction": AL.SourceLoad("held_direction", tuple(obs),
                                            len(obs), 0)}


def spec_for(signal_id=SIG):
    return [s for s in FT.starter_features(signal_id)
            if s.name == "consumed_direction"]


class TestHoldingIntervals:
    def test_a_hold_starts_when_its_entry_becomes_readable(self):
        t = trade(1, "2026-08-13", -1, "2026-08-20")
        h = FWT.holding_intervals([t], daily("2026-08-13", "2026-08-25"))[0]
        assert h.open_from == avail("2026-08-13") == T(2026, 8, 14, 1)

    def test_closure_is_the_first_successful_run_reaching_the_exit_date(self):
        t = trade(1, "2026-08-13", -1, "2026-08-20")
        h = FWT.holding_intervals([t], daily("2026-08-13", "2026-08-25"))[0]
        assert h.closing_run_date == "2026-08-20"
        assert h.open_to == avail("2026-08-20") == T(2026, 8, 21, 1)

    def test_a_silenced_collector_holds_the_trade_past_its_nominal_exit(self):
        """INC-0005, exactly: entry 07-13, planned exit 07-20, no run until
        08-06. The position was genuinely held until 2026-08-07 01:00 UTC."""
        t = trade(18, "2026-07-13", -1, "2026-07-20")
        r = daily("2026-07-13", "2026-07-17") + daily("2026-08-06", "2026-08-10")
        h = FWT.holding_intervals([t], r)[0]
        assert h.closing_run_date == "2026-08-06"
        assert h.open_to == T(2026, 8, 7, 1)
        assert h.contains(T(2026, 8, 1, 12)), "held right through the outage"

    def test_planned_exit_date_alone_would_have_invented_a_flat_interval(self):
        """The guard on the rule above: the calendar answer differs."""
        t = trade(18, "2026-07-13", -1, "2026-07-20")
        r = daily("2026-07-13", "2026-07-17") + daily("2026-08-06", "2026-08-10")
        h = FWT.holding_intervals([t], r)[0]
        assert h.open_to != avail("2026-07-20")

    def test_an_unclosed_trade_has_no_end_yet(self):
        t = trade(34, "2026-08-27", 1, "2026-09-03", open=1)
        h = FWT.holding_intervals([t], daily("2026-08-27", "2026-09-01"))[0]
        assert h.closing_run_date is None and h.open_to is None
        assert h.contains(T(2030, 1, 1))

    def test_only_successful_runs_can_close_a_trade(self):
        t = trade(1, "2026-08-13", -1, "2026-08-20")
        r = (daily("2026-08-13", "2026-08-19")
             + runs(["2026-08-20"], status="error")
             + daily("2026-08-21", "2026-08-22"))
        h = FWT.holding_intervals([t], r)[0]
        assert h.closing_run_date == "2026-08-21"

    def test_runs_of_another_family_cannot_close_this_trade(self):
        t = trade(1, "2026-08-13", -1, "2026-08-20")
        r = daily("2026-08-13", "2026-08-19") + daily("2026-08-20", "2026-08-25",
                                                      signal_id="h5_vol_regime")
        assert FWT.holding_intervals([t], r)[0].open_to is None

    def test_an_unparseable_entry_date_drops_the_interval(self):
        t = trade(1, "not-a-date", -1, "2026-08-20")
        assert FWT.holding_intervals([t], daily("2026-08-13", "2026-08-25")) == []


class TestHeldAt:
    def _two(self):
        return [trade(26, "2026-08-13", -1, "2026-08-20"),
                trade(30, "2026-08-20", 1, "2026-08-27")]

    def test_the_handover_instant_belongs_to_the_new_trade(self):
        """The closing run and the opening run are the SAME run, so both
        become readable at one instant. Half-open intervals settle it with no
        tie-break: the closed trade ends exactly where the new one starts."""
        iv = FWT.holding_intervals(self._two(), daily("2026-08-13", "2026-08-30"))
        at = avail("2026-08-20")
        assert FWT.held_at(iv, SIG, at).trade.entry_id == 30
        assert FWT.held_at(iv, SIG, at - timedelta(microseconds=1)).trade.entry_id == 26

    def test_flat_when_nothing_is_held(self):
        iv = FWT.holding_intervals([trade(1, "2026-08-13", -1, "2026-08-20")],
                                   daily("2026-08-13", "2026-08-30"))
        assert FWT.held_at(iv, SIG, avail("2026-08-21")) is None

    def test_families_do_not_cross(self):
        iv = FWT.holding_intervals(
            [trade(1, "2026-08-13", -1, "2026-08-20"),
             trade(2, "2026-08-13", 1, "2026-08-20", signal_id="h5_vol_regime")],
            daily("2026-08-13", "2026-08-30") + daily("2026-08-13", "2026-08-30",
                                                      signal_id="h5_vol_regime"))
        t = avail("2026-08-15")
        assert FWT.held_at(iv, SIG, t).trade.direction == -1
        assert FWT.held_at(iv, "h5_vol_regime", t).trade.direction == 1

    def test_overlaps_are_reported_not_hidden(self):
        iv = FWT.holding_intervals(
            [trade(1, "2026-08-13", -1, "2026-08-25"),
             trade(2, "2026-08-15", 1, "2026-08-22")],
            daily("2026-08-13", "2026-08-30"))
        assert len(FWT.overlapping_holds(iv)) == 1
        # The live rule wins where the ledger is inconsistent: latest entry_date.
        assert FWT.held_at(iv, SIG, avail("2026-08-18")).trade.entry_id == 2

    def test_a_clean_ledger_has_no_overlaps(self):
        iv = FWT.holding_intervals(self._two(), daily("2026-08-13", "2026-08-30"))
        assert FWT.overlapping_holds(iv) == []


class TestObservations:
    def test_one_observation_per_successful_run(self):
        obs = FWT.held_direction_observations(
            [trade(1, "2026-08-13", -1, "2026-08-20")],
            daily("2026-08-13", "2026-08-17") + runs(["2026-08-18"], status="error"))
        assert [o.provenance["run_date"] for o in obs] == [
            "2026-08-13", "2026-08-14", "2026-08-15", "2026-08-16", "2026-08-17"]

    def test_availability_is_the_run_completion_instant(self):
        obs = FWT.held_direction_observations(
            [trade(1, "2026-08-13", -1, "2026-08-20")], daily("2026-08-13", "2026-08-13"))
        assert obs[0].available_ts == T(2026, 8, 14, 1)
        assert obs[0].event_ts == T(2026, 8, 13)
        assert obs[0].arrival_delay_s == 25 * 3600

    def test_flat_is_zero_and_not_null(self):
        obs = FWT.held_direction_observations(
            [trade(1, "2026-08-13", -1, "2026-08-20")], daily("2026-08-13", "2026-08-25"))
        after = [o for o in obs if o.provenance["run_date"] == "2026-08-22"][0]
        assert after.payload["direction"] == 0.0
        assert after.provenance["entry_id"] is None

    def test_the_held_direction_tracks_the_trade_not_the_calendar(self):
        obs = FWT.held_direction_observations(
            [trade(26, "2026-08-13", -1, "2026-08-20"),
             trade(30, "2026-08-20", 1, "2026-08-27")],
            daily("2026-08-13", "2026-08-26"))
        got = {o.provenance["run_date"]: o.payload["direction"] for o in obs}
        assert got["2026-08-19"] == -1.0
        assert got["2026-08-20"] == 1.0

    def test_provenance_carries_the_trade_it_came_from(self):
        obs = FWT.held_direction_observations(
            [trade(26, "2026-08-13", -1, "2026-08-20")], daily("2026-08-15", "2026-08-15"))
        p = obs[0].provenance
        assert p["entry_id"] == 26 and p["entry_date"] == "2026-08-13"
        assert p["planned_exit_date"] == "2026-08-20"
        assert p["closing_run_date"] is None, "no run reaches the exit date here"

    def test_observations_never_precede_their_event(self):
        obs = FWT.held_direction_observations(
            [trade(1, "2026-08-13", -1, "2026-08-20")], daily("2026-08-13", "2026-08-25"))
        assert all(o.available_ts > o.event_ts for o in obs)


class TestSourceDeclaration:
    def test_the_derived_source_is_declared_and_populated(self):
        s = TB.get_spec("held_direction")
        assert s.populated and s.availability_rule and s.provenance_fields
        assert "signal_id" in s.provenance_fields

    def test_it_declares_the_same_collector_delay_as_the_signal_log(self):
        assert (TB.get_spec("held_direction").availability_offset_s
                == TB.get_spec("signal_log").availability_offset_s)

    def test_the_notes_record_why_run_log_resolves_closure(self):
        assert "run_log" in TB.get_spec("held_direction").notes


class TestFeatureResolution:
    def _loads(self, first="2026-08-13", last="2026-08-26"):
        return load_of(FWT.held_direction_observations(
            [trade(26, "2026-08-13", -1, "2026-08-20"),
             trade(30, "2026-08-20", 1, "2026-08-27")], daily(first, last)))

    def _value(self, at, loads=None):
        row = FT.build_row(at, spec_for(), loads or self._loads())
        return row.features["consumed_direction"]

    def test_it_resolves_the_held_direction(self):
        f = self._value(T(2026, 8, 19, 9, 59, 55))
        assert f.state == FT.states.READY and f.value == -1.0

    def test_an_observation_available_at_the_exact_instant_is_admitted(self):
        f = self._value(avail("2026-08-19"))
        assert f.state == FT.states.READY
        assert f.inputs[0].age_s == 0.0

    def test_one_microsecond_earlier_reads_the_previous_run(self):
        at = avail("2026-08-19") - timedelta(microseconds=1)
        assert self._value(at).inputs[0].available_ts == avail("2026-08-18")

    def test_nothing_before_the_first_run_is_incomplete_never_zero(self):
        f = self._value(T(2026, 8, 13, 23, 59))
        assert f.state == FT.states.INCOMPLETE_DATA and f.value is None

    def test_a_flat_book_is_ready_at_zero(self):
        loads = load_of(FWT.held_direction_observations(
            [trade(26, "2026-08-13", -1, "2026-08-20")], daily("2026-08-13", "2026-08-26")))
        f = self._value(T(2026, 8, 24, 12), loads)
        assert f.state == FT.states.READY and f.value == 0.0

    def test_a_silent_collector_goes_stale_rather_than_forward_filling(self):
        loads = self._loads(last="2026-08-20")
        f = self._value(T(2026, 8, 24, 12), loads)
        assert f.state == FT.states.STALE_DATA and f.value is None

    def test_the_tolerance_matches_the_signal_features(self):
        by = {s.name: s for s in FT.starter_features(SIG)}
        assert (by["consumed_direction"].inputs[0].tolerance_s
                == by["signal_position"].inputs[0].tolerance_s
                == FT.SIGNAL_TOLERANCE_S)

    def test_it_is_keyed_to_the_requested_family(self):
        obs = FWT.held_direction_observations(
            [trade(26, "2026-08-13", -1, "2026-08-20"),
             trade(99, "2026-08-13", 1, "2026-08-20", signal_id="h5_vol_regime")],
            daily("2026-08-13", "2026-08-19")
            + daily("2026-08-13", "2026-08-19", signal_id="h5_vol_regime"))
        at = T(2026, 8, 18, 12)
        assert FT.build_row(at, spec_for(SIG), load_of(obs)
                            ).features["consumed_direction"].value == -1.0
        assert FT.build_row(at, spec_for("h5_vol_regime"), load_of(obs)
                            ).features["consumed_direction"].value == 1.0

    def test_an_unrelated_family_finds_nothing_rather_than_borrowing(self):
        f = FT.build_row(T(2026, 8, 18, 12), spec_for("g2_funding_level"),
                         self._loads()).features["consumed_direction"]
        assert f.state == FT.states.INCOMPLETE_DATA

    def test_no_look_ahead_reaches_the_row(self):
        at = T(2026, 8, 19, 9, 59, 55)
        row = FT.build_row(at, spec_for(), self._loads(), verify_no_lookahead=True)
        assert row.features["consumed_direction"].inputs[0].available_ts <= at


class TestContracts:
    def test_the_new_feature_is_in_the_joined_view_spec_set(self):
        assert "consumed_direction" in {s.name for s in FT.starter_features(SIG)}

    def test_it_carries_its_own_contract_hash(self):
        by = {s.name: s for s in FT.starter_features(SIG)}
        assert (DS.contract_hash(by["consumed_direction"])
                != DS.contract_hash(by["signal_position"]))

    def test_signal_position_is_byte_for_byte_what_it_always_was(self):
        """The rows already persisted under this hash must stay resolvable."""
        by = {s.name: s for s in FT.starter_features(SIG)}
        assert DS.contract_hash(by["signal_position"]) == SIGNAL_POSITION_HASH

    def test_the_two_read_different_sources(self):
        by = {s.name: s for s in FT.starter_features(SIG)}
        assert by["signal_position"].inputs[0].source == "signal_log"
        assert by["consumed_direction"].inputs[0].source == "held_direction"

    def test_the_hash_moves_if_the_family_key_changes(self):
        a = [s for s in FT.starter_features(SIG) if s.name == "consumed_direction"][0]
        b = [s for s in FT.starter_features("h5_vol_regime")
             if s.name == "consumed_direction"][0]
        assert DS.contract_hash(a) != DS.contract_hash(b)


def _env_db(var):
    v = os.environ.get(var)
    p = pathlib.Path(v) if v else None
    return p if p and p.exists() else None


_FWD = _env_db("BTCBOT_FWD_SLICE")
_EXEC = _env_db("BTCBOT_EXEC_SLICE")


@pytest.mark.skipif(_FWD is None or _EXEC is None,
                    reason="no forward/execution slice")
class TestAgainstTheRealDecisions:
    """The reconstruction must reproduce the side actually submitted.

    Eight real `g3_momentum_tsmom` orders exist, three of them sells taken while
    the daily signal read +1. If `consumed_direction` cannot reproduce those
    three it has not captured what the decision consumed.
    """

    _cache = None

    def _loads(self):
        if TestAgainstTheRealDecisions._cache is None:
            TestAgainstTheRealDecisions._cache = FWT.load_held_direction(_FWD)
        return TestAgainstTheRealDecisions._cache

    def _orders(self):
        con = sqlite3.connect(f"{_EXEC.resolve().as_uri()}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in con.execute(
                "SELECT client_order_id, placed_ts, side FROM orders "
                "WHERE strategy = ? AND client_order_id NOT LIKE 'exit-%' "
                "ORDER BY placed_ts", (SIG,))]
        finally:
            con.close()

    def _consumed(self, ts):
        at = datetime.fromisoformat(ts)
        return FT.build_row(at, spec_for(), self._loads()
                            ).features["consumed_direction"]

    def test_the_ledger_holds_no_overlapping_trades(self):
        iv = FWT.holding_intervals(FWT.load_trades(_FWD), FWT.load_runs(_FWD))
        assert FWT.overlapping_holds(iv) == []

    def test_every_decision_reproduces_the_submitted_side(self):
        orders = self._orders()
        assert len(orders) == 8, "the corpus is eight execution decisions"
        for o in orders:
            f = self._consumed(o["placed_ts"])
            assert f.state == FT.states.READY, o["client_order_id"]
            expected = 1.0 if o["side"] == "buy" else -1.0
            assert f.value == expected, o["client_order_id"]

    def test_the_three_sells_are_the_ones_the_daily_signal_disagreed_with(self):
        sells = [o for o in self._orders() if o["side"] == "sell"]
        assert len(sells) == 3
        sig = AL.load_sqlite_source(_FWD, "signal_log", payload_fields=("position",),
                                    where="signal_id = ?", params=(SIG,))
        sig_load = {"signal_log": AL.SourceLoad("signal_log", tuple(sig), len(sig), 0)}
        pos_spec = [s for s in FT.starter_features(SIG) if s.name == "signal_position"]
        for o in sells:
            at = datetime.fromisoformat(o["placed_ts"])
            daily_pos = FT.build_row(at, pos_spec, sig_load
                                     ).features["signal_position"]
            assert daily_pos.value == 1.0
            assert self._consumed(o["placed_ts"]).value == -1.0

    def test_the_five_buys_agree_with_the_daily_signal(self):
        buys = [o for o in self._orders() if o["side"] == "buy"]
        assert len(buys) == 5
        for o in buys:
            assert self._consumed(o["placed_ts"]).value == 1.0

    def test_the_outage_hold_is_visible_in_the_real_ledger(self):
        iv = FWT.holding_intervals(FWT.load_trades(_FWD, SIG),
                                   FWT.load_runs(_FWD, SIG))
        held = [h for h in iv if h.trade.entry_date == "2026-07-13"]
        if not held:
            pytest.skip("the 2026-07-13 entry is not in this slice")
        assert held[0].closing_run_date == "2026-08-06"

    def test_every_real_observation_is_available_after_its_event(self):
        for o in self._loads()["held_direction"].observations:
            assert o.available_ts > o.event_ts
