"""Tests for the v2 option-panel reductions: spot index and ATM IV pair-mid.

Synthetic panels with explicit strikes and expiries, plus a real-corpus block
gated on the extracted production slices.
"""
from __future__ import annotations

import ast
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
from alignment import option_panel as OP  # noqa: E402
from alignment import timebase as TB  # noqa: E402

UTC = timezone.utc
CYCLE = "2026-09-01T10:00:00.000000+00:00"
EXP_NEAR = "2026-09-02T12:00:00Z"
EXP_FAR = "2026-09-03T12:00:00Z"
US = int(datetime(2026, 9, 1, 10, 0, tzinfo=UTC).timestamp() * 1_000_000)


def T(*a) -> datetime:
    return datetime(*a, tzinfo=UTC)


def row(iid, *, ts=CYCLE, spot=100000.0, iv=0.50, strike=100000.0,
        expiry=EXP_NEAR, ctype=OP.CALL, exch=US):
    return OP.PanelRow(instrument_id=iid, ts=ts, exch_ts=exch, spot_price=spot,
                       mark_iv=iv, strike_price=strike, settlement_time=expiry,
                       contract_type=ctype, symbol=f"X-{iid}")


def pair(strike, *, call_iv=0.50, put_iv=0.48, expiry=EXP_NEAR, ts=CYCLE,
         spot=100000.0, base=1000):
    return [row(base, ts=ts, spot=spot, iv=call_iv, strike=strike,
                expiry=expiry, ctype=OP.CALL),
            row(base + 1, ts=ts, spot=spot, iv=put_iv, strike=strike,
                expiry=expiry, ctype=OP.PUT)]


class TestSpotIndex:
    def test_median_across_the_cycle(self):
        rows = [row(1, spot=99000.0), row(2, spot=100000.0), row(3, spot=101000.0)]
        assert OP.cycle_spot(rows) == 100000.0

    def test_it_is_instrument_independent(self):
        """The whole point: defined for every cycle, keyed to no member."""
        obs = OP.spot_index_observations([row(1, spot=100.0), row(2, spot=102.0)])
        assert len(obs) == 1 and obs[0].payload["spot_price"] == 101.0
        assert "instrument_id" not in obs[0].provenance

    def test_missing_spot_yields_no_observation_not_a_guess(self):
        rows = [row(1, spot=None), row(2, spot=None)]
        assert OP.spot_index_observations(rows) == []

    def test_one_observation_per_cycle(self):
        rows = [row(1), row(2)] + [row(1, ts="2026-09-01T10:01:00+00:00"),
                                   row(2, ts="2026-09-01T10:01:00+00:00")]
        assert len(OP.spot_index_observations(rows)) == 2

    def test_availability_is_the_cycle_and_event_is_the_exchange(self):
        o = OP.spot_index_observations([row(1, exch=US)])[0]
        assert o.available_ts == T(2026, 9, 1, 10)
        assert o.event_ts == T(2026, 9, 1, 10)
        assert o.arrival_delay_s == 0.0

    def test_provenance_records_the_panel(self):
        o = OP.spot_index_observations([row(1), row(2), row(3, spot=None)])[0]
        assert o.provenance["panel_size"] == 3
        assert o.provenance["spot_observations"] == 2
        assert o.provenance["cycle_ts"] == CYCLE


class TestAtmSelection:
    def test_picks_the_strike_nearest_spot(self):
        rows = pair(99000.0, base=10) + pair(100100.0, base=20) + pair(101000.0, base=30)
        p = OP.select_atm_pair(rows, CYCLE)
        assert p.strike == 100100.0 and p.abs_moneyness == 100.0

    def test_tie_breaks_to_the_lower_strike(self):
        rows = pair(99900.0, base=10) + pair(100100.0, base=20)   # both 100 away
        assert OP.select_atm_pair(rows, CYCLE).strike == 99900.0

    def test_tie_break_is_independent_of_input_order(self):
        a = pair(99900.0, base=10) + pair(100100.0, base=20)
        b = pair(100100.0, base=20) + pair(99900.0, base=10)
        assert OP.select_atm_pair(a, CYCLE).strike == \
               OP.select_atm_pair(b, CYCLE).strike == 99900.0

    def test_picks_the_nearest_expiry(self):
        rows = pair(100000.0, expiry=EXP_FAR, base=10) + \
               pair(100500.0, expiry=EXP_NEAR, base=20)
        p = OP.select_atm_pair(rows, CYCLE)
        assert p.expiry == EXP_NEAR and p.strike == 100500.0, \
            "nearest expiry wins even when a farther expiry is closer to spot"

    def test_missing_put_yields_no_pair(self):
        rows = [row(1, strike=100000.0, ctype=OP.CALL)]
        assert OP.select_atm_pair(rows, CYCLE) is None

    def test_missing_call_yields_no_pair(self):
        rows = [row(1, strike=100000.0, ctype=OP.PUT)]
        assert OP.select_atm_pair(rows, CYCLE) is None

    def test_put_at_a_different_strike_does_not_substitute(self):
        rows = [row(1, strike=100000.0, ctype=OP.CALL),
                row(2, strike=99000.0, ctype=OP.PUT)]
        assert OP.select_atm_pair(rows, CYCLE) is None

    def test_tau_and_moneyness_are_computed(self):
        p = OP.select_atm_pair(pair(100500.0), CYCLE)
        assert p.tau_hours == pytest.approx(26.0)
        assert p.abs_moneyness == 500.0

    def test_selection_is_deterministic(self):
        rows = pair(99000.0, base=10) + pair(100100.0, base=20)
        assert OP.select_atm_pair(rows, CYCLE) == OP.select_atm_pair(rows, CYCLE)


class TestAtmIv:
    def test_pair_mid(self):
        o = OP.atm_iv_observations(pair(100000.0, call_iv=0.50, put_iv=0.40))[0]
        assert o.payload["atm_iv"] == pytest.approx(0.45)
        assert o.payload["call_iv"] == 0.50 and o.payload["put_iv"] == 0.40

    def test_a_null_leg_yields_no_observation(self):
        rows = pair(100000.0)
        rows[1] = OP.PanelRow(**{**rows[1].__dict__, "mark_iv": None})
        assert OP.atm_iv_observations(rows) == []

    def test_event_time_is_the_later_leg(self):
        """The pair is not complete until both legs exist."""
        rows = pair(100000.0)
        rows[1] = OP.PanelRow(**{**rows[1].__dict__, "exch_ts": US + 5_000_000})
        o = OP.atm_iv_observations(rows)[0]
        assert o.event_ts == T(2026, 9, 1, 10, 0, 5)

    def test_full_provenance(self):
        o = OP.atm_iv_observations(pair(100500.0, base=77))[0]
        for k in ("call_id", "put_id", "strike", "spot", "expiry", "tau_hours",
                  "abs_moneyness", "cycle_ts"):
            assert k in o.provenance, k
        assert o.provenance["call_id"] == 77 and o.provenance["put_id"] == 78
        assert o.provenance["strike"] == 100500.0

    def test_no_forward_fill_across_a_broken_cycle(self):
        good = pair(100000.0, ts=CYCLE)
        broken = [row(9, ts="2026-09-01T10:01:00+00:00", strike=100000.0,
                      ctype=OP.CALL)]                      # no put
        obs = OP.atm_iv_observations(good + broken)
        assert len(obs) == 1, "a broken cycle must not inherit the previous one"


class TestPositiveIvValidity:
    """Both ATM legs must quote mark_iv > 0, tested per leg not on the mid."""

    def test_normal_positive_ivs_still_resolve(self):
        o = OP.atm_iv_observations(pair(100000.0, call_iv=0.50, put_iv=0.40))[0]
        assert o.payload["atm_iv"] == pytest.approx(0.45)

    def test_both_legs_zero_yields_no_observation(self):
        assert OP.atm_iv_observations(
            pair(100000.0, call_iv=0.0, put_iv=0.0)) == []

    def test_a_single_zero_leg_yields_no_observation(self):
        assert OP.atm_iv_observations(
            pair(100000.0, call_iv=0.50, put_iv=0.0)) == []
        assert OP.atm_iv_observations(
            pair(100000.0, call_iv=0.0, put_iv=0.50)) == []

    def test_a_positive_mid_cannot_rescue_a_zero_leg(self):
        """The real case: call 0.0184, put 0.0 -> mid 0.0092 > 0.

        One cycle in the extracted window is exactly this. A mid-only test
        would admit it, which is why the rule is per leg.
        """
        assert OP.atm_iv_observations(
            pair(100000.0, call_iv=0.0184, put_iv=0.0)) == []

    def test_negative_iv_is_refused(self):
        assert OP.atm_iv_observations(
            pair(100000.0, call_iv=-0.10, put_iv=0.50)) == []

    def test_a_tiny_but_positive_iv_is_kept(self):
        """The rule is validity, not a plausibility filter."""
        o = OP.atm_iv_observations(
            pair(100000.0, call_iv=0.002, put_iv=0.004))[0]
        assert o.payload["atm_iv"] == pytest.approx(0.003)

    def test_an_invalid_cycle_does_not_inherit_a_neighbour(self):
        good = pair(100000.0, call_iv=0.50, put_iv=0.40, ts=CYCLE)
        bad = pair(100000.0, call_iv=0.0, put_iv=0.0,
                   ts="2026-09-01T10:01:00+00:00", base=50)
        obs = OP.atm_iv_observations(good + bad)
        assert len(obs) == 1 and obs[0].provenance["cycle_ts"] == CYCLE

    def test_the_feature_reports_incomplete_not_ready_zero(self):
        # rows_without_availability=0: the cycle was timestamped fine, the
        # value was invalid. Counting it as a clock failure would raise the
        # state to UNKNOWN, which means "we cannot tell when" -- not the case.
        loads = {"atm_iv": AL.SourceLoad(
            "atm_iv",
            tuple(OP.atm_iv_observations(pair(100000.0, call_iv=0.0, put_iv=0.0))),
            1, 0)}
        row_ = FT.build_row(T(2026, 9, 1, 10, 0, 30),
                            [s for s in FT.starter_features("g3")
                             if s.name == "option_atm_iv"], loads)
        f = row_.features["option_atm_iv"]
        assert f.state == FT.states.INCOMPLETE_DATA
        assert f.value is None and f.available is False

    def test_a_validity_drop_is_not_reported_as_a_clock_failure(self, tmp_path):
        """UNKNOWN means "we cannot tell when"; that is not what happened."""
        import sqlite3
        p = tmp_path / "q.db"
        con = sqlite3.connect(p)
        con.executescript(
            "CREATE TABLE option_quotes (instrument_id INTEGER, ts TEXT, "
            "exch_ts INTEGER, spot_price REAL, mark_iv REAL);"
            "CREATE TABLE instruments (id INTEGER PRIMARY KEY, symbol TEXT, "
            "strike_price REAL, settlement_time TEXT, contract_type TEXT);")
        con.executemany("INSERT INTO option_quotes VALUES (?,?,?,?,?)", [
            (1, CYCLE, US, 100000.0, 0.0), (2, CYCLE, US, 100000.0, 0.0)])
        con.executemany("INSERT INTO instruments VALUES (?,?,?,?,?)", [
            (1, "C", 100000.0, EXP_NEAR, OP.CALL),
            (2, "P", 100000.0, EXP_NEAR, OP.PUT)])
        con.commit()
        con.close()
        loads = OP.load_derived_sources(p)
        assert loads["atm_iv"].observations == ()
        assert loads["atm_iv"].rows_without_availability == 0
        assert loads["atm_iv"].unusable_clock is False

    def test_spot_index_is_unaffected_by_the_iv_rule(self):
        """The fix is scoped to option_atm_iv only."""
        rows = pair(100000.0, call_iv=0.0, put_iv=0.0)
        assert OP.atm_iv_observations(rows) == []
        assert len(OP.spot_index_observations(rows)) == 1

    def test_validity_is_declared_on_the_contract(self):
        spec = [s for s in FT.starter_features("g3")
                if s.name == "option_atm_iv"][0]
        assert spec.inputs[0].validity == "both ATM legs quote mark_iv > 0"

    def test_only_the_iv_contract_hash_moves(self):
        """Adding the field must not bump features nobody touched."""
        from feature_store import decision_store as DS
        hashes = {s.name: DS.contract_hash(s)
                  for s in FT.starter_features("g3_momentum_tsmom")}
        assert hashes["spot_price"].startswith("d0c3da2b9c73")
        assert hashes["signal_position"].startswith("6a2dcdb053c8")
        assert hashes["market_close_1m"].startswith("5bee276a9aad")
        assert not hashes["option_atm_iv"].startswith("35dbdc82fd85")


class TestNoLookAhead:
    def test_state_is_never_read(self):
        """instruments.state is a current-snapshot field, not history."""
        src = _strip_docstrings(
            (_RESEARCH / "alignment" / "option_panel.py").read_text(encoding="utf-8"))
        assert ".state" not in src and "'state'" not in src and '"state"' not in src

    def test_selection_uses_only_rows_at_the_cycle(self):
        """A later cycle's instrument cannot be chosen for an earlier cycle."""
        early = pair(100000.0, ts=CYCLE, base=10)
        later = pair(100000.0, ts="2026-09-01T10:05:00+00:00", base=20)
        obs = OP.atm_iv_observations(early + later)
        first = [o for o in obs if o.provenance["cycle_ts"] == CYCLE][0]
        assert first.provenance["call_id"] == 10

    def test_derived_observations_pass_the_shared_checker(self):
        obs = OP.atm_iv_observations(pair(100000.0)) + \
              OP.spot_index_observations(pair(100000.0))
        joined = AL.as_of_join([T(2026, 9, 1, 10, 0, 30)], obs)
        assert AL.check_no_lookahead(joined) == []

    def test_availability_never_precedes_the_event(self):
        for o in OP.atm_iv_observations(pair(100000.0)):
            assert o.available_ts >= o.event_ts


class TestContractV2:
    def test_feature_set_is_pinned_and_carries_no_instrument_id(self):
        specs = FT.starter_features("g3")
        assert [s.name for s in specs] == [
            "spot_price", "option_atm_iv", "signal_position",
            "consumed_direction", "market_close_1m"]

    def test_removed_features_cannot_silently_reappear(self):
        names = {s.name for s in FT.starter_features("g3")}
        assert "option_oi_contracts" not in names
        assert "quote_spread_bps" not in names
        assert set(FT.REMOVED_IN_V2) == {"option_oi_contracts", "quote_spread_bps"}
        assert not (names & set(FT.REMOVED_IN_V2))

    def test_option_features_read_the_derived_sources(self):
        by = {s.name: s for s in FT.starter_features("g3")}
        assert by["spot_price"].inputs[0].source == "spot_index"
        assert by["option_atm_iv"].inputs[0].source == "atm_iv"
        for n in ("spot_price", "option_atm_iv"):
            assert by[n].inputs[0].key_field is None

    def test_derived_sources_are_declared_in_the_timebase(self):
        for n in ("spot_index", "atm_iv"):
            s = TB.get_spec(n)
            assert s.populated and s.availability_rule
            assert s.provenance_fields

    def test_atm_iv_is_not_declared_as_the_vol_state_feature(self):
        assert "DVOL" in TB.get_spec("atm_iv").notes

    def test_v2_features_build_end_to_end(self):
        loads = {
            "spot_index": AL.SourceLoad(
                "spot_index", tuple(OP.spot_index_observations(pair(100000.0))), 1, 0),
            "atm_iv": AL.SourceLoad(
                "atm_iv", tuple(OP.atm_iv_observations(pair(100000.0))), 1, 0),
        }
        row_ = FT.build_row(T(2026, 9, 1, 10, 0, 30), FT.starter_features("g3"), loads)
        assert row_.features["spot_price"].state == FT.states.READY
        assert row_.features["option_atm_iv"].value == pytest.approx(0.49)
        assert row_.features["signal_position"].state == FT.states.INCOMPLETE_DATA


def _strip_docstrings(src: str) -> str:
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and ast.get_docstring(node):
            node.body = node.body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def _env_db(var):
    v = os.environ.get(var)
    p = pathlib.Path(v) if v else None
    return p if p and p.exists() else None


_OQ = _env_db("BTCBOT_OQ_SLICE")


@pytest.mark.skipif(_OQ is None, reason="no quote slice")
class TestAgainstRealCorpus:
    _cache = None

    def _loads(self):
        if TestAgainstRealCorpus._cache is None:
            TestAgainstRealCorpus._cache = OP.load_derived_sources(_OQ)
        return TestAgainstRealCorpus._cache

    def test_both_series_resolve_on_real_data(self):
        loads = self._loads()
        assert loads["spot_index"].observations
        assert loads["atm_iv"].observations

    def test_atm_iv_is_positive_everywhere_after_the_validity_rule(self):
        """1,245 of 20,870 cycles (5.97%) had at least one ATM leg at 0.0 --
        the synthetic-quote degeneracy CLAUDE.md sec.11 documents, reaching the
        money and not only the wings. Those cycles now yield no observation."""
        vals = [o.payload["atm_iv"] for o in self._loads()["atm_iv"].observations]
        assert all(v > 0.0 for v in vals), "the validity rule removed the zeros"
        assert min(vals) > 0.005 and max(vals) < 5.0
        for o in self._loads()["atm_iv"].observations[:2000]:
            assert o.payload["call_iv"] > 0 and o.payload["put_iv"] > 0

    def test_call_and_put_legs_agree_closely(self):
        obs = self._loads()["atm_iv"].observations
        diffs = [abs(o.payload["call_iv"] - o.payload["put_iv"]) for o in obs]
        diffs.sort()
        assert diffs[len(diffs) // 2] < 0.05, "ATM legs should estimate one IV"

    def test_every_atm_observation_carries_full_provenance(self):
        for o in self._loads()["atm_iv"].observations[:200]:
            assert o.provenance["call_id"] != o.provenance["put_id"]
            assert o.provenance["tau_hours"] > 0
            assert o.provenance["abs_moneyness"] >= 0

    def test_real_series_are_free_of_look_ahead(self):
        for name in ("spot_index", "atm_iv"):
            for o in self._loads()[name].observations[:500]:
                if o.event_ts is not None:
                    assert o.available_ts >= o.event_ts

    def test_selection_is_reproducible_on_real_data(self):
        a = OP.load_derived_sources(_OQ)["atm_iv"].observations
        b = self._loads()["atm_iv"].observations
        assert [o.provenance for o in a[:100]] == [o.provenance for o in b[:100]]
