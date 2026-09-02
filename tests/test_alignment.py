"""Tests for the canonical timestamp-alignment layer.

Synthetic fixtures with explicit instants throughout, plus a block that runs
against extracted production slices when they are present.
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
from alignment import timebase as TB  # noqa: E402

UTC = timezone.utc


def T(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


def obs(source="s", event=None, avail=None, payload=None, prov=None) -> AL.Observation:
    return AL.Observation(source=source, event_ts=event, available_ts=avail,
                          payload=payload or {}, provenance=prov or {})


class TestUnitNormalisation:
    def test_iso_both_spellings_agree(self):
        assert TB.to_utc("2026-09-01T10:00:00Z", TB.ISO) == \
               TB.to_utc("2026-09-01T10:00:00+00:00", TB.ISO)

    def test_naive_iso_is_utc(self):
        assert TB.to_utc("2026-09-01T10:00:00", TB.ISO) == T(2026, 9, 1, 10)

    def test_microseconds(self):
        assert TB.to_utc(1_777_316_036_302_005, TB.MICROS).year == 2026

    def test_milliseconds(self):
        assert TB.to_utc(1_735_689_600_000, TB.MILLIS) == T(2025, 1, 1)

    def test_ms_and_us_of_the_same_instant_agree(self):
        assert TB.to_utc(1_735_689_600_000, TB.MILLIS) == \
               TB.to_utc(1_735_689_600_000_000, TB.MICROS)

    def test_date_becomes_midnight_utc(self):
        assert TB.to_utc("2026-09-01", TB.DATE) == T(2026, 9, 1)

    def test_unparseable_and_empty_are_none(self):
        for v in (None, "", "nonsense", "2026-13-45"):
            assert TB.to_utc(v, TB.ISO) is None

    def test_every_returned_datetime_is_aware_utc(self):
        for v, u in (("2026-09-01T10:00:00Z", TB.ISO), (1_735_689_600_000, TB.MILLIS),
                     ("2026-09-01", TB.DATE), (1_735_689_600_000_000, TB.MICROS)):
            assert TB.to_utc(v, u).tzinfo is not None
            assert TB.to_utc(v, u).utcoffset() == timedelta(0)


class TestSourceRegistry:
    def test_all_six_families_are_declared(self):
        names = set(TB.SOURCES)
        assert {"spot_1m", "perp_1m", "dvol_1d"} <= names          # market/index
        assert "option_quotes" in names                            # option/premium
        assert "oi_snapshots" in names                             # OI
        assert "signal_log" in names                               # signal evaluations
        assert {"positions", "orders", "trades", "order_fills"} <= names
        assert "forward_trade_entries" in names                    # forward evidence

    def test_every_spec_declares_both_times(self):
        for s in TB.SOURCES.values():
            assert s.event_field and s.event_unit in ("iso", "us", "ms", "date")
            assert s.availability_field is not None or s.availability_offset_s >= 0
            assert s.availability_rule, f"{s.name} has no stated rule"

    def test_every_spec_carries_provenance(self):
        for s in TB.SOURCES.values():
            assert isinstance(s.provenance_fields, tuple)

    def test_dormant_sources_are_flagged_not_omitted(self):
        assert TB.SOURCES["oi_snapshots"].populated is False
        assert TB.SOURCES["funding_snapshots"].populated is False

    def test_oi_source_redirects_to_where_data_actually_is(self):
        assert TB.oi_source().name == "option_quotes"
        assert TB.SOURCES["oi_snapshots"].populated is False

    def test_unknown_source_raises_with_the_known_list(self):
        with pytest.raises(KeyError, match="unknown source"):
            TB.get_spec("nope")

    def test_negative_availability_offset_is_refused(self):
        with pytest.raises(ValueError, match="before it existed"):
            TB.SourceSpec(name="x", store="s", table="t", grain="g",
                          event_field="e", event_unit=TB.ISO,
                          availability_field=None, availability_offset_s=-1.0,
                          availability_rule="r")

    def test_bad_unit_is_refused(self):
        with pytest.raises(ValueError, match="unknown unit"):
            TB.SourceSpec(name="x", store="s", table="t", grain="g",
                          event_field="e", event_unit="fortnights",
                          availability_field=None, availability_offset_s=0.0,
                          availability_rule="r")


class TestAvailabilityIsNeverBeforeEvent:
    def test_bar_is_available_only_after_it_closes(self):
        spec = TB.get_spec("spot_1m")
        row = {"open_time": 1_735_689_600_000}
        assert TB.availability_time(row, spec) - TB.event_time(row, spec) == \
               timedelta(seconds=60)

    def test_daily_bar_offset_is_a_full_day(self):
        spec = TB.get_spec("dvol_1d")
        row = {"open_time": 1_735_689_600_000}
        assert TB.availability_time(row, spec) - TB.event_time(row, spec) == \
               timedelta(days=1)

    def test_signal_is_not_available_until_the_next_collector_run(self):
        spec = TB.get_spec("signal_log")
        row = {"day": "2026-09-01"}
        assert TB.availability_time(row, spec) == T(2026, 9, 2, 1)
        assert TB.event_time(row, spec) == T(2026, 9, 1)

    def test_quote_availability_is_the_recorded_arrival_not_the_exchange_time(self):
        spec = TB.get_spec("option_quotes")
        row = {"exch_ts": 1_756_745_780_377_008,
               "ts": "2026-09-01T17:17:20.409050+00:00"}
        assert TB.availability_time(row, spec) > TB.event_time(row, spec)

    def test_availability_never_precedes_event_across_every_spec(self):
        samples = {
            TB.ISO: "2026-09-01T10:00:00+00:00",
            TB.MICROS: 1_756_720_800_000_000,
            TB.MILLIS: 1_756_720_800_000,
            TB.DATE: "2026-09-01",
        }
        for s in TB.SOURCES.values():
            row = {s.event_field: samples[s.event_unit]}
            if s.availability_field and s.availability_field != s.event_field:
                row[s.availability_field] = "2026-09-01T10:00:05+00:00"
            ev, av = TB.event_time(row, s), TB.availability_time(row, s)
            assert av >= ev, f"{s.name}: availability {av} precedes event {ev}"

    def test_missing_event_time_yields_no_availability_when_derived(self):
        spec = TB.get_spec("spot_1m")
        assert TB.availability_time({"open_time": None}, spec) is None


class TestCadenceNeverAssumesAGrid:
    def test_irregular_series_is_reported_irregular(self):
        times = ["2026-09-01T10:00:00Z", "2026-09-01T10:01:00Z",
                 "2026-09-01T10:02:37Z", "2026-09-01T10:03:00Z"]
        c = TB.cadence_profile(times, nominal_seconds=60.0)
        assert c["is_regular_grid"] is False
        assert c["max_s"] == 97.0
        assert c["exact_fraction"] < 1.0

    def test_perfect_grid_is_recognised_but_not_required(self):
        times = [f"2026-09-01T10:{m:02d}:00Z" for m in range(5)]
        c = TB.cadence_profile(times, nominal_seconds=60.0)
        assert c["is_regular_grid"] is True and c["exact_fraction"] == 1.0

    def test_panel_timestamps_are_deduped_so_gaps_are_schedule_not_width(self):
        """One cycle writes many rows at the same ts; that is not a zero gap."""
        times = ["2026-09-01T10:00:00Z"] * 50 + ["2026-09-01T10:01:00Z"] * 50
        c = TB.cadence_profile(times, nominal_seconds=60.0)
        assert c["n"] == 2 and c["gaps"] == 1 and c["median_s"] == 60.0

    def test_dedupe_can_be_disabled_for_a_true_single_series(self):
        times = ["2026-09-01T10:00:00Z", "2026-09-01T10:00:00Z"]
        assert TB.cadence_profile(times, dedupe=False)["min_s"] == 0.0

    def test_no_expected_cadence_is_baked_in(self):
        c = TB.cadence_profile(["2026-09-01T10:00:00Z", "2026-09-01T10:03:00Z"])
        assert "nominal_s" not in c and "is_regular_grid" not in c

    def test_insufficient_series_is_flagged_not_crashed(self):
        assert TB.cadence_profile([])["insufficient"] is True
        assert TB.cadence_profile(["2026-09-01T10:00:00Z"])["insufficient"] is True


class TestAsOfIsBackwardOnly:
    def _series(self):
        return [obs(avail=T(2026, 9, 1, 10, m), payload={"v": m}) for m in (0, 5, 10)]

    def test_picks_the_newest_available_not_the_nearest(self):
        r = AL.as_of_join([T(2026, 9, 1, 10, 9)], self._series())[0]
        assert r["matched"] and r["payload"]["v"] == 5

    def test_future_observations_are_never_selected(self):
        r = AL.as_of_join([T(2026, 9, 1, 9, 59)], self._series())[0]
        assert r["matched"] is False

    def test_exact_match_is_admitted_by_default(self):
        r = AL.as_of_join([T(2026, 9, 1, 10, 5)], self._series())[0]
        assert r["matched"] and r["age_s"] == 0.0

    def test_exact_match_can_be_excluded(self):
        r = AL.as_of_join([T(2026, 9, 1, 10, 5)], self._series(),
                          allow_exact=False)[0]
        assert r["payload"]["v"] == 0

    def test_age_is_returned_so_staleness_is_visible(self):
        r = AL.as_of_join([T(2026, 9, 1, 10, 30)], self._series())[0]
        assert r["age_s"] == 1200.0

    def test_tolerance_drops_a_stale_match_rather_than_returning_it(self):
        r = AL.as_of_join([T(2026, 9, 1, 12, 0)], self._series(),
                          tolerance_s=300.0)[0]
        assert r["matched"] is False and r["dropped_stale"] is True
        assert r["age_s"] > 300.0

    def test_empty_observations_match_nothing(self):
        r = AL.as_of_join([T(2026, 9, 1, 10, 0)], [])[0]
        assert r["matched"] is False

    def test_result_length_always_equals_decision_count(self):
        d = [T(2026, 9, 1, 9), T(2026, 9, 1, 10, 7), T(2026, 9, 1, 11)]
        assert len(AL.as_of_join(d, self._series())) == 3

    def test_unsorted_observations_are_handled(self):
        s = list(reversed(self._series()))
        assert AL.as_of_join([T(2026, 9, 1, 10, 9)], s)[0]["payload"]["v"] == 5


class TestPanelJoin:
    def _panel(self):
        out = []
        for m in (0, 5):
            for k in ("A", "B"):
                out.append(obs(avail=T(2026, 9, 1, 10, m),
                               payload={"v": f"{k}{m}"}, prov={"key": k}))
        return out

    def test_each_member_gets_its_own_asof(self):
        res = AL.as_of_panel([T(2026, 9, 1, 10, 7)], self._panel(), "key")
        assert set(res) == {"A", "B"}
        assert res["A"][0]["payload"]["v"] == "A5"
        assert res["B"][0]["payload"]["v"] == "B5"

    def test_key_may_come_from_payload(self):
        rows = [obs(avail=T(2026, 9, 1, 10), payload={"sig": "x", "v": 1})]
        assert set(AL.as_of_panel([T(2026, 9, 1, 11)], rows, "sig")) == {"x"}

    def test_align_many_refuses_a_panel_without_a_key(self):
        with pytest.raises(ValueError, match="pass keys"):
            AL.align_many([T(2026, 9, 1, 10, 7)], {"p": self._panel()},
                          key_fields={"p": "key"})

    def test_align_many_selects_the_named_member(self):
        r = AL.align_many([T(2026, 9, 1, 10, 7)], {"p": self._panel()},
                          key_fields={"p": "key"}, keys={"p": "B"})[0]
        assert r["sources"]["p"]["payload"]["v"] == "B5"


class TestLookAheadDetection:
    def test_clean_join_has_no_violations(self):
        j = AL.as_of_join([T(2026, 9, 1, 10, 9)],
                          [obs(avail=T(2026, 9, 1, 10, 5))])
        assert AL.check_no_lookahead(j) == []
        AL.assert_no_lookahead(j)

    def test_a_forged_future_match_is_caught(self):
        """The checker must not trust the join that produced the rows."""
        bad = [{"decision_ts": T(2026, 9, 1, 10), "matched": True,
                "available_ts": T(2026, 9, 1, 11), "age_s": -3600.0}]
        v = AL.check_no_lookahead(bad)
        assert len(v) == 2  # future availability, and negative age
        with pytest.raises(AL.LookAheadError):
            AL.assert_no_lookahead(bad)

    def test_matched_row_without_timestamps_is_a_violation(self):
        bad = [{"decision_ts": T(2026, 9, 1, 10), "matched": True,
                "available_ts": None, "age_s": 0.0}]
        assert AL.check_no_lookahead(bad)[0]["reason"].startswith("matched row")

    def test_unmatched_rows_are_ignored(self):
        assert AL.check_no_lookahead([{"matched": False}]) == []

    def test_exact_equality_is_a_violation_only_in_strict_mode(self):
        rows = [{"decision_ts": T(2026, 9, 1, 10), "matched": True,
                 "available_ts": T(2026, 9, 1, 10), "age_s": 0.0}]
        assert AL.check_no_lookahead(rows, allow_exact=True) == []
        assert AL.check_no_lookahead(rows, allow_exact=False)


class TestObservationsAndProvenance:
    def test_source_timestamps_and_provenance_are_preserved(self):
        spec = TB.get_spec("option_quotes")
        rows = [{"exch_ts": 1_756_745_780_377_008,
                 "ts": "2026-09-01T17:17:20.409050+00:00",
                 "instrument_id": 84, "mark_price": 1.5}]
        o = AL.to_observations(rows, spec, payload_fields=("mark_price",))[0]
        assert o.event_ts is not None and o.available_ts is not None
        assert o.provenance == {"instrument_id": 84}
        assert o.payload == {"mark_price": 1.5}
        assert o.arrival_delay_s > 0

    def test_row_without_availability_is_dropped(self):
        spec = TB.get_spec("option_quotes")
        assert AL.to_observations([{"exch_ts": 1, "ts": None}], spec) == []

    def test_row_with_null_event_time_is_kept(self):
        """0.234% of production quotes have NULL exch_ts but a known arrival."""
        spec = TB.get_spec("option_quotes")
        o = AL.to_observations(
            [{"exch_ts": None, "ts": "2026-09-01T10:00:00+00:00",
              "instrument_id": 84}], spec)[0]
        assert o.event_ts is None and o.available_ts == T(2026, 9, 1, 10)
        assert o.arrival_delay_s is None

    def test_observations_are_sorted_deterministically(self):
        spec = TB.get_spec("option_quotes")
        rows = [{"exch_ts": None, "ts": "2026-09-01T10:00:00+00:00",
                 "instrument_id": i} for i in (5, 1, 3)]
        a = [o.provenance["instrument_id"] for o in AL.to_observations(rows, spec)]
        b = [o.provenance["instrument_id"]
             for o in AL.to_observations(list(reversed(rows)), spec)]
        assert a == b


class TestReadOnlyAndNoStatistics:
    def test_modules_contain_no_write_statements(self):
        """Scan SQL actually handed to execute(), not any prose containing a verb."""
        verbs = ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "REPLACE",
                 "CREATE")
        for name in ("timebase.py", "align.py"):
            src = _strip_docstrings(
                (_RESEARCH / "alignment" / name).read_text(encoding="utf-8"))
            for node in ast.walk(ast.parse(src)):
                if not (isinstance(node, ast.Call)
                        and getattr(node.func, "attr", None) == "execute"):
                    continue
                for arg in ast.walk(node):
                    text = arg.value if isinstance(arg, ast.Constant) and isinstance(
                        arg.value, str) else ""
                    first = text.strip().split(" ")[0].upper() if text.strip() else ""
                    assert first not in verbs, f"{name}: execute({text[:40]!r})"

    def test_every_sqlite_connection_is_read_only(self):
        src = _strip_docstrings(
            (_RESEARCH / "alignment" / "align.py").read_text(encoding="utf-8"))
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Call):
                fn = node.func
                if getattr(fn, "attr", None) == "connect" and getattr(
                        getattr(fn, "value", None), "id", None) == "sqlite3":
                    assert any("mode=ro" in a.value for a in node.args
                               if isinstance(a, ast.Constant)) or any(
                        isinstance(a, ast.JoinedStr) for a in node.args)

    def test_alignment_does_not_import_from_src(self):
        for name in ("timebase.py", "align.py"):
            src = (_RESEARCH / "alignment" / name).read_text(encoding="utf-8")
            for node in ast.walk(ast.parse(src)):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    mod = (getattr(node, "module", "") or "")
                    assert not mod.startswith("src"), f"{name} imports {mod}"

    def test_no_inferential_statistic_is_computed(self):
        banned = {"sharpe", "deflated_sr", "psr", "bh_fdr", "bh_adjusted_p"}
        for name in ("timebase.py", "align.py"):
            src = _strip_docstrings(
                (_RESEARCH / "alignment" / name).read_text(encoding="utf-8"))
            for node in ast.walk(ast.parse(src)):
                if isinstance(node, ast.Call):
                    label = getattr(node.func, "id", None) or getattr(
                        node.func, "attr", None)
                    assert (label or "").lower() not in banned


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


@pytest.mark.skipif(_EXEC is None, reason="no execution slice (BTCBOT_EXEC_SLICE)")
class TestAgainstProductionExecution:
    def test_loads_every_execution_source(self):
        for src in ("orders", "order_fills", "positions", "trades"):
            assert AL.load_sqlite_source(_EXEC, src)

    def test_fill_availability_lags_its_exchange_time(self):
        o = AL.load_sqlite_source(_EXEC, "order_fills", payload_fields=("role",))
        delays = [x.arrival_delay_s for x in o if x.arrival_delay_s is not None]
        assert delays and min(delays) > 0, "processed_ts must follow exch_ts"

    def test_alignment_over_real_decisions_has_no_lookahead(self):
        orders = AL.load_sqlite_source(_EXEC, "orders",
                                       payload_fields=("price", "status"))
        decisions = [o.available_ts for o in orders]
        joined = AL.as_of_join(decisions,
                               AL.load_sqlite_source(_EXEC, "order_fills",
                                                     payload_fields=("price",)))
        assert AL.check_no_lookahead(joined) == []


@pytest.mark.skipif(_OQ is None, reason="no quote slice (BTCBOT_OQ_SLICE)")
class TestAgainstProductionQuotes:
    def test_oi_comes_from_option_quotes_not_the_empty_table(self):
        _, cad = AL.load_oi(_OQ)
        assert cad["actual_source"] == "option_quotes"
        assert cad["declared_source"] == "oi_snapshots"

    def test_oi_cadence_is_not_a_grid(self):
        _, cad = AL.load_oi(_OQ)
        assert cad["is_regular_grid"] is False
        assert cad["max_s"] > cad["median_s"], "real series drifts"

    def test_quote_arrival_never_precedes_the_exchange(self):
        q = AL.load_sqlite_source(_OQ, "option_quotes",
                                  payload_fields=("mark_price",))
        delays = [x.arrival_delay_s for x in q if x.arrival_delay_s is not None]
        assert delays and min(delays) > 0


@pytest.mark.skipif(_FWD is None, reason="no forward slice (BTCBOT_FWD_SLICE)")
class TestAgainstProductionForward:
    def test_signal_is_not_available_on_its_own_day(self):
        sig = AL.load_sqlite_source(_FWD, "signal_log",
                                    payload_fields=("position",))
        for o in sig[:200]:
            assert o.available_ts > o.event_ts
            assert (o.available_ts - o.event_ts) == timedelta(hours=25)

    def test_panel_resolves_every_signal_family_separately(self):
        sig = AL.load_sqlite_source(_FWD, "signal_log",
                                    payload_fields=("position",))
        res = AL.as_of_panel([T(2026, 9, 1, 17)], sig, "signal_id")
        assert len(res) >= 5
        assert all(v[0]["matched"] for v in res.values())
