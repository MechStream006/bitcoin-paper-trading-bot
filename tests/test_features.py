"""Tests for the deterministic feature-construction layer.

Synthetic fixtures with explicit instants, covering each of the nine cases the
interface has to get right, plus a real-corpus block gated on the extracted
production slices.
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
from alignment import timebase as TB  # noqa: E402

UTC = timezone.utc
S = FT.states
P = FT.policy


def T(*a) -> datetime:
    return datetime(*a, tzinfo=UTC)


def quote_rows(*specs):
    """(ts, exch_ts, instrument_id, field values...) -> raw option_quotes rows."""
    out = []
    for ts, exch_us, iid, extra in specs:
        row = {"ts": ts, "exch_ts": exch_us, "instrument_id": iid}
        row.update(extra)
        out.append(row)
    return out


def quote_load(rows):
    return AL.load_observations(
        rows, TB.get_spec("option_quotes"),
        payload_fields=("spot_price", "mark_iv", "oi_contracts",
                        "best_bid", "best_ask"))


def one_input_feature(name="f", source="option_quotes", field_name="spot_price",
                      tolerance_s=300.0, criticality=P.REQUIRED,
                      key_field="instrument_id", key=84, degradation=False):
    return FT.FeatureSpec(
        name=name,
        inputs=(FT.InputRequirement(
            name="x", source=source, field=field_name, tolerance_s=tolerance_s,
            criticality=criticality, key_field=key_field, key=key,
            degradation_declared=degradation),),
        compute=lambda v: v["x"],
    )


BASE_TS = "2026-09-01T10:00:00.000000+00:00"
# Derived, not hand-written: an off-by-a-year literal here silently changes
# every event_ts assertion below without changing any availability assertion.
BASE_US = int(datetime(2026, 9, 1, 10, 0, tzinfo=UTC).timestamp() * 1_000_000)


class TestContractValidation:
    def test_feature_without_inputs_is_refused(self):
        with pytest.raises(FT.FeatureContractError, match="declares no inputs"):
            FT.FeatureSpec(name="f", inputs=(), compute=lambda v: 1)

    def test_duplicate_input_names_are_refused(self):
        r = FT.InputRequirement(name="x", source="option_quotes",
                                field="spot_price", tolerance_s=1.0)
        with pytest.raises(FT.FeatureContractError, match="duplicate"):
            FT.FeatureSpec(name="f", inputs=(r, r), compute=lambda v: 1)

    def test_panel_key_without_a_value_is_refused(self):
        with pytest.raises(FT.FeatureContractError, match="without a key"):
            FT.InputRequirement(name="x", source="option_quotes",
                                field="spot_price", key_field="instrument_id")

    def test_negative_tolerance_is_refused(self):
        with pytest.raises(FT.FeatureContractError, match="negative tolerance"):
            FT.InputRequirement(name="x", source="option_quotes",
                                field="spot_price", tolerance_s=-1.0)

    def test_bad_criticality_is_refused(self):
        with pytest.raises(FT.FeatureContractError, match="required|optional"):
            FT.InputRequirement(name="x", source="option_quotes",
                                field="spot_price", criticality="mandatory")


class TestValidAlignedInput:
    def test_fresh_input_is_ready_and_computes(self):
        load = quote_load(quote_rows((BASE_TS, BASE_US, 84, {"spot_price": 100.0})))
        row = FT.build_row(T(2026, 9, 1, 10, 0, 30), [one_input_feature()],
                           {"option_quotes": load})
        f = row.features["f"]
        assert f.state == S.READY and f.action == P.COMPUTE
        assert f.value == 100.0 and f.available is True
        assert row.state == S.READY and row.action == P.COMPUTE

    def test_all_four_provenance_facts_are_preserved(self):
        load = quote_load(quote_rows((BASE_TS, BASE_US, 84, {"spot_price": 100.0})))
        rec = FT.build_row(T(2026, 9, 1, 10, 0, 30), [one_input_feature()],
                           {"option_quotes": load}).features["f"].inputs[0]
        assert rec.event_ts == T(2026, 9, 1, 10)
        assert rec.available_ts == T(2026, 9, 1, 10)
        assert rec.age_s == 30.0
        assert rec.provenance == {"instrument_id": 84}
        assert rec.tolerance_s == 300.0 and rec.criticality == P.REQUIRED

    def test_values_returns_only_computed_features(self):
        load = quote_load(quote_rows((BASE_TS, BASE_US, 84, {"spot_price": 100.0})))
        row = FT.build_row(T(2026, 9, 1, 10, 0, 30),
                           [one_input_feature("a"), one_input_feature("b", field_name="mark_iv")],
                           {"option_quotes": load})
        assert row.values() == {"a": 100.0}
        assert row.unavailable() == {"b": S.INCOMPLETE_DATA}

    def test_multi_input_feature_computes_from_one_observation(self):
        load = quote_load(quote_rows(
            (BASE_TS, BASE_US, 84, {"best_bid": 99.0, "best_ask": 101.0})))
        spec = FT.FeatureSpec(
            name="spread",
            inputs=(FT.InputRequirement(name="bid", source="option_quotes",
                                        field="best_bid", tolerance_s=300.0,
                                        key_field="instrument_id", key=84),
                    FT.InputRequirement(name="ask", source="option_quotes",
                                        field="best_ask", tolerance_s=300.0,
                                        key_field="instrument_id", key=84)),
            compute=FT._spread_bps)
        f = FT.build_row(T(2026, 9, 1, 10, 0, 5), [spec],
                         {"option_quotes": load}).features["spread"]
        assert f.state == S.READY
        assert f.value == pytest.approx(200.0)


class TestStaleInput:
    def test_beyond_tolerance_is_stale_and_not_forward_filled(self):
        load = quote_load(quote_rows((BASE_TS, BASE_US, 84, {"spot_price": 100.0})))
        f = FT.build_row(T(2026, 9, 1, 11), [one_input_feature(tolerance_s=300.0)],
                         {"option_quotes": load}).features["f"]
        assert f.state == S.STALE_DATA
        assert f.value is None, "a stale input must not deliver its value"
        assert f.available is False

    def test_stale_required_input_retries_rather_than_computing(self):
        load = quote_load(quote_rows((BASE_TS, BASE_US, 84, {"spot_price": 100.0})))
        f = FT.build_row(T(2026, 9, 1, 11), [one_input_feature()],
                         {"option_quotes": load}).features["f"]
        assert f.action == P.RETRY

    def test_stale_optional_without_declared_degradation_blocks(self):
        """An undeclared degradation path is never taken silently."""
        load = quote_load(quote_rows((BASE_TS, BASE_US, 84, {"spot_price": 100.0})))
        f = FT.build_row(T(2026, 9, 1, 11),
                         [one_input_feature(criticality=P.OPTIONAL)],
                         {"option_quotes": load}).features["f"]
        assert f.action == P.BLOCK

    def test_stale_optional_with_declared_degradation_degrades(self):
        load = quote_load(quote_rows((BASE_TS, BASE_US, 84, {"spot_price": 100.0})))
        f = FT.build_row(T(2026, 9, 1, 11),
                         [one_input_feature(criticality=P.OPTIONAL,
                                            degradation=True)],
                         {"option_quotes": load}).features["f"]
        assert f.action == P.DEGRADE

    def test_age_is_recorded_even_when_stale(self):
        load = quote_load(quote_rows((BASE_TS, BASE_US, 84, {"spot_price": 100.0})))
        rec = FT.build_row(T(2026, 9, 1, 11), [one_input_feature()],
                           {"option_quotes": load}).features["f"].inputs[0]
        assert rec.age_s == 3600.0 and "exceeds tolerance" in rec.detail

    def test_unbounded_tolerance_never_goes_stale(self):
        load = quote_load(quote_rows((BASE_TS, BASE_US, 84, {"spot_price": 100.0})))
        f = FT.build_row(T(2027, 1, 1), [one_input_feature(tolerance_s=None)],
                         {"option_quotes": load}).features["f"]
        assert f.state == S.READY and f.value == 100.0


class TestMissingInput:
    def test_no_observation_before_the_decision_is_incomplete(self):
        load = quote_load(quote_rows((BASE_TS, BASE_US, 84, {"spot_price": 100.0})))
        f = FT.build_row(T(2026, 9, 1, 9), [one_input_feature()],
                         {"option_quotes": load}).features["f"]
        assert f.state == S.INCOMPLETE_DATA and f.value is None

    def test_source_not_supplied_at_all_is_incomplete(self):
        f = FT.build_row(T(2026, 9, 1, 10), [one_input_feature()],
                         {}).features["f"]
        assert f.state == S.INCOMPLETE_DATA and "not supplied" in f.inputs[0].detail

    def test_null_field_on_a_matched_row_is_incomplete_not_zero(self):
        load = quote_load(quote_rows((BASE_TS, BASE_US, 84, {"spot_price": None})))
        f = FT.build_row(T(2026, 9, 1, 10, 0, 5), [one_input_feature()],
                         {"option_quotes": load}).features["f"]
        assert f.state == S.INCOMPLETE_DATA
        assert f.value is None and "is NULL" in f.inputs[0].detail

    def test_wrong_panel_key_matches_nothing(self):
        load = quote_load(quote_rows((BASE_TS, BASE_US, 84, {"spot_price": 100.0})))
        f = FT.build_row(T(2026, 9, 1, 10, 0, 5),
                         [one_input_feature(key=999)],
                         {"option_quotes": load}).features["f"]
        assert f.state == S.INCOMPLETE_DATA

    def test_unknown_source_is_a_configuration_error(self):
        f = FT.build_row(T(2026, 9, 1, 10),
                         [one_input_feature(source="not_a_source", key_field=None,
                                            key=None)],
                         {}).features["f"]
        assert f.state == S.CONFIGURATION_ERROR and f.action == P.HALT

    def test_dormant_source_is_a_configuration_error_not_empty_data(self):
        """Asking for oi_snapshots is a misconfiguration, not an absence."""
        f = FT.build_row(T(2026, 9, 1, 10),
                         [one_input_feature(source="oi_snapshots",
                                            field_name="open_interest",
                                            key_field=None, key=None)],
                         {}).features["f"]
        assert f.state == S.CONFIGURATION_ERROR
        assert "dormant" in f.inputs[0].detail


class TestNullArrivalTime:
    def test_rows_with_null_arrival_are_not_dated_from_the_event_clock(self):
        load = quote_load(quote_rows((None, BASE_US, 84, {"spot_price": 100.0})))
        assert load.rows_seen == 1 and load.observations == ()
        assert load.unusable_clock is True

    def test_unusable_clock_is_unknown_and_fails_closed(self):
        load = quote_load(quote_rows((None, BASE_US, 84, {"spot_price": 100.0})))
        f = FT.build_row(T(2026, 9, 1, 12), [one_input_feature()],
                         {"option_quotes": load}).features["f"]
        assert f.state == S.UNKNOWN
        assert f.action == P.BLOCK, "UNKNOWN must block, not degrade"
        assert f.value is None

    def test_unknown_blocks_even_for_an_optional_input(self):
        load = quote_load(quote_rows((None, BASE_US, 84, {"spot_price": 100.0})))
        f = FT.build_row(T(2026, 9, 1, 12),
                         [one_input_feature(criticality=P.OPTIONAL,
                                            degradation=True)],
                         {"option_quotes": load}).features["f"]
        assert f.action == P.BLOCK

    def test_a_usable_row_alongside_a_null_one_is_still_used(self):
        load = quote_load(quote_rows(
            (None, BASE_US, 84, {"spot_price": 1.0}),
            (BASE_TS, BASE_US, 84, {"spot_price": 100.0})))
        assert load.unusable_clock is False
        f = FT.build_row(T(2026, 9, 1, 10, 0, 5), [one_input_feature()],
                         {"option_quotes": load}).features["f"]
        assert f.state == S.READY and f.value == 100.0


class TestBoundaryTimestamp:
    def test_observation_exactly_at_the_decision_instant_is_available(self):
        load = quote_load(quote_rows((BASE_TS, BASE_US, 84, {"spot_price": 100.0})))
        f = FT.build_row(T(2026, 9, 1, 10), [one_input_feature()],
                         {"option_quotes": load}).features["f"]
        assert f.state == S.READY and f.inputs[0].age_s == 0.0

    def test_observation_one_microsecond_later_is_not_available(self):
        load = quote_load(quote_rows((BASE_TS, BASE_US, 84, {"spot_price": 100.0})))
        f = FT.build_row(T(2026, 9, 1, 9, 59, 59, 999999), [one_input_feature()],
                         {"option_quotes": load}).features["f"]
        assert f.state == S.INCOMPLETE_DATA

    def test_age_exactly_equal_to_tolerance_is_not_stale(self):
        load = quote_load(quote_rows((BASE_TS, BASE_US, 84, {"spot_price": 100.0})))
        f = FT.build_row(T(2026, 9, 1, 10, 5), [one_input_feature(tolerance_s=300.0)],
                         {"option_quotes": load}).features["f"]
        assert f.state == S.READY and f.inputs[0].age_s == 300.0

    def test_one_microsecond_past_tolerance_is_stale(self):
        load = quote_load(quote_rows((BASE_TS, BASE_US, 84, {"spot_price": 100.0})))
        f = FT.build_row(T(2026, 9, 1, 10, 5, 0, 1),
                         [one_input_feature(tolerance_s=300.0)],
                         {"option_quotes": load}).features["f"]
        assert f.state == S.STALE_DATA


class TestMultipleSourceCadences:
    def _loads(self):
        quotes = quote_load(quote_rows(
            (BASE_TS, BASE_US, 84, {"spot_price": 100.0})))
        sig_rows = [{"day": "2026-08-31", "position": 1.0, "signal_id": "g3",
                     "run_id": 7}]
        signals = AL.load_observations(sig_rows, TB.get_spec("signal_log"),
                                       payload_fields=("position",))
        return {"option_quotes": quotes, "signal_log": signals}

    def test_sources_with_different_grains_align_to_one_row(self):
        specs = [one_input_feature("spot"),
                 FT.FeatureSpec(name="sig", inputs=(FT.InputRequirement(
                     name="p", source="signal_log", field="position",
                     key_field="signal_id", key="g3",
                     tolerance_s=172_800.0),), compute=lambda v: v["p"])]
        row = FT.build_row(T(2026, 9, 1, 10, 0, 30), specs, self._loads())
        assert row.features["spot"].state == S.READY
        assert row.features["sig"].state == S.READY
        assert row.features["sig"].value == 1.0

    def test_each_source_keeps_its_own_age(self):
        specs = [one_input_feature("spot"),
                 FT.FeatureSpec(name="sig", inputs=(FT.InputRequirement(
                     name="p", source="signal_log", field="position",
                     key_field="signal_id", key="g3",
                     tolerance_s=172_800.0),), compute=lambda v: v["p"])]
        row = FT.build_row(T(2026, 9, 1, 10, 0, 30), specs, self._loads())
        assert row.features["spot"].inputs[0].age_s == 30.0
        assert row.features["sig"].inputs[0].age_s == pytest.approx(
            (T(2026, 9, 1, 10, 0, 30) - T(2026, 9, 1, 1)).total_seconds())

    def test_row_state_is_worst_wins_across_sources(self):
        specs = [one_input_feature("spot"),
                 one_input_feature("missing", field_name="mark_iv")]
        row = FT.build_row(T(2026, 9, 1, 10, 0, 30), specs, self._loads())
        assert row.features["spot"].state == S.READY
        assert row.state == S.INCOMPLETE_DATA

    def test_signal_is_not_available_on_its_own_day(self):
        loads = self._loads()
        spec = FT.FeatureSpec(name="sig", inputs=(FT.InputRequirement(
            name="p", source="signal_log", field="position",
            key_field="signal_id", key="g3", tolerance_s=None),),
            compute=lambda v: v["p"])
        f = FT.build_row(T(2026, 8, 31, 23), [spec], loads).features["sig"]
        assert f.state == S.INCOMPLETE_DATA


class TestIrregularOiCadence:
    def _irregular(self):
        """Gaps of 60s, 60s, 116s -- the shape production actually shows."""
        stamps = [("2026-09-01T10:00:00+00:00", 0),
                  ("2026-09-01T10:01:00+00:00", 60),
                  ("2026-09-01T10:02:00+00:00", 120),
                  ("2026-09-01T10:03:56+00:00", 236)]
        return quote_load(quote_rows(*[
            (ts, BASE_US + off * 1_000_000, 84, {"oi_contracts": float(i)})
            for i, (ts, off) in enumerate(stamps)]))

    def test_irregular_cadence_still_resolves_the_newest_prior_observation(self):
        load = self._irregular()
        f = FT.build_row(T(2026, 9, 1, 10, 3, 30),
                         [one_input_feature(field_name="oi_contracts")],
                         {"option_quotes": load}).features["f"]
        assert f.state == S.READY and f.value == 2.0

    def test_a_long_gap_makes_the_input_stale_rather_than_interpolated(self):
        load = self._irregular()
        f = FT.build_row(T(2026, 9, 1, 10, 3, 30),
                         [one_input_feature(field_name="oi_contracts",
                                            tolerance_s=60.0)],
                         {"option_quotes": load}).features["f"]
        assert f.state == S.STALE_DATA
        assert f.value is None

    def test_no_synthetic_grid_point_is_ever_created(self):
        load = self._irregular()
        decisions = [T(2026, 9, 1, 10, 0, 30), T(2026, 9, 1, 10, 3, 55)]
        rows = FT.build_rows(decisions,
                             [one_input_feature(field_name="oi_contracts",
                                                tolerance_s=None)],
                             {"option_quotes": load})
        observed = {r.features["f"].inputs[0].available_ts for r in rows}
        real = {o.available_ts for o in load.observations}
        assert observed <= real, "an aligned instant must be one that was observed"

    def test_cadence_is_measurably_irregular(self):
        load = self._irregular()
        c = TB.cadence_profile([o.available_ts for o in load.observations],
                               nominal_seconds=60.0)
        assert c["is_regular_grid"] is False and c["max_s"] == 116.0


class TestLookAheadRejection:
    def test_a_future_observation_is_never_selected(self):
        load = quote_load(quote_rows(
            ("2026-09-01T12:00:00+00:00", BASE_US, 84, {"spot_price": 999.0})))
        f = FT.build_row(T(2026, 9, 1, 10), [one_input_feature()],
                         {"option_quotes": load}).features["f"]
        assert f.state == S.INCOMPLETE_DATA and f.value != 999.0

    def test_the_independent_check_runs_over_every_resolved_input(self):
        load = quote_load(quote_rows((BASE_TS, BASE_US, 84, {"spot_price": 100.0})))
        row = FT.build_row(T(2026, 9, 1, 10, 0, 30), [one_input_feature()],
                           {"option_quotes": load}, verify_no_lookahead=True)
        assert row.features["f"].state == S.READY

    def test_a_forged_future_input_is_caught_by_the_verifier(self, monkeypatch):
        """The verifier must not trust the resolver that produced the record."""
        load = quote_load(quote_rows((BASE_TS, BASE_US, 84, {"spot_price": 100.0})))
        real = FT._resolve_input

        def forged(decision_ts, req, loads):
            rec = real(decision_ts, req, loads)
            return FT.InputRecord(
                **{**rec.__dict__,
                   "available_ts": decision_ts + timedelta(hours=1),
                   "age_s": -3600.0})

        monkeypatch.setattr(FT, "_resolve_input", forged)
        with pytest.raises(AssertionError, match="look-ahead"):
            FT.build_row(T(2026, 9, 1, 10, 0, 30), [one_input_feature()],
                         {"option_quotes": load})

    def test_event_time_is_never_substituted_for_a_missing_arrival(self):
        """exch_ts is earlier than ts; using it would create foresight."""
        load = quote_load(quote_rows((None, BASE_US, 84, {"spot_price": 100.0})))
        f = FT.build_row(T(2026, 9, 1, 10, 0, 1), [one_input_feature()],
                         {"option_quotes": load}).features["f"]
        assert f.state == S.UNKNOWN and f.value is None


class TestDeterminismAndPurity:
    def test_same_inputs_give_identical_rows(self):
        load = quote_load(quote_rows((BASE_TS, BASE_US, 84, {"spot_price": 100.0})))
        a = FT.build_row(T(2026, 9, 1, 10, 0, 30), [one_input_feature()],
                         {"option_quotes": load})
        b = FT.build_row(T(2026, 9, 1, 10, 0, 30), [one_input_feature()],
                         {"option_quotes": load})
        assert a == b

    def test_build_row_reads_no_clock_and_does_no_io(self):
        src = _strip_docstrings(
            (_RESEARCH / "alignment" / "features.py").read_text(encoding="utf-8"))
        tree = ast.parse(src)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "build_row")
        for node in ast.walk(fn):
            if isinstance(node, ast.Call):
                label = getattr(node.func, "attr", None) or getattr(
                    node.func, "id", None)
                assert label not in ("now", "utcnow", "today", "time", "connect",
                                     "open", "execute")

    def test_module_does_not_import_from_src(self):
        src = (_RESEARCH / "alignment" / "features.py").read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                assert not (getattr(node, "module", "") or "").startswith("src")

    def test_loading_features_leaks_no_global_module_names(self):
        """The scoped dq load must not widen the known `engine` collision."""
        import subprocess
        code = (
            "import sys, pathlib;"
            f"sys.path.insert(0, r'{_RESEARCH}');"
            "before=set(sys.modules);"
            "import alignment.features;"
            "leaked={m for m in set(sys.modules)-before if m in ('states','policy','engine')};"
            "assert not leaked, leaked;"
            "assert not any(p.endswith('dq') for p in sys.path);"
            "print('clean')")
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True)
        assert out.returncode == 0, out.stderr
        assert "clean" in out.stdout


class TestStarterFeatureSet:
    def test_it_is_small_and_every_feature_is_declared(self):
        specs = FT.starter_features(instrument_id=84, signal_id="g3")
        assert len(specs) == 6
        for s in specs:
            assert s.description and s.inputs
            for i in s.inputs:
                assert i.tolerance_s is not None or i.source == "spot_1m"

    def test_market_bar_feature_exists_and_is_expected_to_be_unavailable(self):
        specs = {s.name: s for s in FT.starter_features(84, "g3")}
        assert "market_close_1m" in specs
        assert specs["market_close_1m"].inputs[0].source == "spot_1m"

    def test_panel_features_all_name_their_member(self):
        for s in FT.starter_features(instrument_id=84, signal_id="g3"):
            for i in s.inputs:
                if i.source in ("option_quotes", "signal_log"):
                    assert i.key is not None

    def test_spread_is_none_on_a_crossed_or_zero_book(self):
        assert FT._spread_bps({"bid": 101.0, "ask": 99.0}) is None
        assert FT._spread_bps({"bid": 0.0, "ask": 0.0}) is None


class TestCoverageReport:
    def test_counts_states_per_feature(self):
        load = quote_load(quote_rows((BASE_TS, BASE_US, 84, {"spot_price": 100.0})))
        rows = FT.build_rows([T(2026, 9, 1, 9), T(2026, 9, 1, 10, 0, 30)],
                             [one_input_feature()], {"option_quotes": load})
        cov = FT.coverage(rows)
        assert cov["rows"] == 2
        assert cov["features"]["f"]["available"] == 1
        assert cov["features"]["f"]["states"] == {S.INCOMPLETE_DATA: 1, S.READY: 1}

    def test_empty_input_is_not_an_error(self):
        assert FT.coverage([])["rows"] == 0


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


_EXEC = _env_db("BTCBOT_EXEC_SLICE")
_OQ = _env_db("BTCBOT_OQ_SLICE")
_FWD = _env_db("BTCBOT_FWD_SLICE")


def _sqlite_load(db, source, payload_fields):
    spec = TB.get_spec(source)
    conn = sqlite3.connect(pathlib.Path(db).resolve().as_uri() + "?mode=ro",
                           uri=True)
    conn.row_factory = sqlite3.Row
    cols = set(payload_fields) | {spec.event_field} | set(spec.provenance_fields)
    if spec.availability_field:
        cols.add(spec.availability_field)
    rows = conn.execute(
        f"SELECT {', '.join(sorted(cols))} FROM {spec.table}").fetchall()
    conn.close()
    return AL.load_observations(rows, spec, payload_fields)


@pytest.mark.skipif(_OQ is None or _FWD is None or _EXEC is None,
                    reason="production slices not available")
class TestAgainstRealCorpus:
    _cache = None

    def _setup(self):
        if TestAgainstRealCorpus._cache is None:
            TestAgainstRealCorpus._cache = self._build()
        return TestAgainstRealCorpus._cache

    def _build(self):
        orders = AL.load_sqlite_source(
            _EXEC, "orders", payload_fields=("client_order_id", "strategy"))
        decisions = [o.available_ts for o in orders
                     if str(o.provenance.get("strategy")) == "g3_momentum_tsmom"
                     and str(o.provenance.get("client_order_id", "")).startswith("g3")]
        loads = {
            "option_quotes": _sqlite_load(
                _OQ, "option_quotes",
                ("spot_price", "mark_iv", "oi_contracts", "best_bid", "best_ask")),
            "signal_log": _sqlite_load(_FWD, "signal_log", ("position",)),
        }
        lake = AL.load_lake_source("spot_1m", payload_fields=("close",))
        loads["spot_1m"] = AL.SourceLoad("spot_1m", tuple(lake), len(lake), 0)
        return decisions, loads

    def test_real_decisions_produce_rows_with_no_lookahead(self):
        decisions, loads = self._setup()
        specs = FT.starter_features(197889, "g3_momentum_tsmom")
        rows = FT.build_rows(decisions, specs, loads)
        assert len(rows) == len(decisions)
        for r in rows:
            for f in r.features.values():
                for i in f.inputs:
                    if i.available_ts is not None:
                        assert i.available_ts <= r.decision_ts

    def test_market_bars_resolve_after_the_lake_extension(self):
        """Superseded assertion.

        This previously asserted market bars stay UNAVAILABLE, which was true
        only while the lake ended 2026-07-31. Extending it through the live
        period is what the assertion was waiting on, so it now checks the
        thing that actually matters: the bar is real, fresh enough, and
        traceable to the partition it came from. Unavailability itself stays
        covered by the synthetic stale/missing cases, which do not depend on
        the state of the corpus.
        """
        decisions, loads = self._setup()
        specs = FT.starter_features(197889, "g3_momentum_tsmom")
        rows = FT.build_rows(decisions, specs, loads)
        for r in rows:
            f = r.features["market_close_1m"]
            assert f.state == S.READY, f.inputs[0].detail
            rec = f.inputs[0]
            assert rec.age_s <= rec.tolerance_s
            assert rec.provenance["parquet_sha256"]
            assert rec.available_ts <= r.decision_ts

    def test_the_lake_holds_no_incomplete_final_day(self):
        """The corpus invariant B01 depends on: no short day after 2026-06-15.

        An in-progress month fetched via the CCXT fallback ends mid-day, which
        would put a <1440-bar day into the corpus and trip the collector's
        guard on an already-collected observation.
        """
        import pandas as pd
        from storage.lake import list_partitions, read_partition
        base = _RESEARCH / "data" / "raw"
        for ds in ("spot", "perp"):
            months = [(p["year"], p["month"]) for p in
                      list_partitions(base, ds, "BTCUSDT")][-2:]
            df = pd.concat([read_partition(base, ds, "BTCUSDT", y, m)
                            for y, m in months])
            counts = pd.to_datetime(df["open_time"], unit="ms", utc=True)                 .dt.strftime("%Y-%m-%d").value_counts()
            short = sorted(d for d, c in counts.items() if c < 1440)
            assert [d for d in short if d >= "2026-06-15"] == [], short

    def test_lake_inputs_carry_a_partition_sha(self):
        obs = AL.load_lake_source("dvol_1d", payload_fields=("close",))
        assert obs and obs[-1].provenance["parquet_sha256"]
        assert obs[-1].provenance["dataset"] == "dvol"

    def test_quote_features_resolve_with_sub_second_age(self):
        decisions, loads = self._setup()
        specs = FT.starter_features(197889, "g3_momentum_tsmom")
        rows = FT.build_rows(decisions, specs, loads)
        ready = [r for r in rows if r.features["spot_price"].state == S.READY]
        assert ready, "expected at least one decision inside the quote window"
        assert all(r.features["spot_price"].inputs[0].age_s < 5.0 for r in ready)

    def test_rows_are_deterministic_on_real_data(self):
        decisions, loads = self._setup()
        specs = FT.starter_features(197889, "g3_momentum_tsmom")
        assert FT.build_rows(decisions, specs, loads) == \
               FT.build_rows(decisions, specs, loads)
