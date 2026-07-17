"""
Data Completeness Framework — the evaluation engine (PR-7).

Covers: every readiness state producible in isolation, the normative check
ordering (and the masquerade it prevents), worst-wins aggregation, the full
policy matrix, fail-closed behaviour, determinism, and partial-day / schema-
mismatch / backfill detection.

data_completeness_gate.md's own gate for its PR-1: "100% branch coverage on
checks.py and policy.py. These two files are the entire safety argument;
nothing else in the framework matters if they are wrong."
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research" / "dq"))
sys.path.insert(0, str(ROOT / "research" / "evidence_library"))

import engine  # noqa: E402
import policy  # noqa: E402
import validation  # noqa: E402
from checks import CheckContext, UnknownCheckError, check_temporal, register_check  # noqa: E402
from contract import (  # noqa: E402
    Contract,
    ContractConfigurationError,
    parse_contract,
    parse_contract_text,
    period_bounds,
    validate_contract,
)
from observation import InputObservation, ObservationError  # noqa: E402
from states import (  # noqa: E402
    BACKFILL_REQUIRED,
    BLOCK,
    COMPUTE,
    CONFIGURATION_ERROR,
    DEFER,
    DEGRADE,
    HALT,
    INCOMPLETE_DATA,
    PARTIAL_DAY,
    READY,
    RETRY,
    SCHEMA_MISMATCH,
    STALE_DATA,
    UNKNOWN,
    UnknownStateError,
    severity,
    worst,
    worst_action,
)

PERIOD = "2026-06-14"
CLOSED = "2026-06-15T02:00:00+00:00"   # period closed, within every late budget
OPEN = "2026-06-14T00:05:00+00:00"     # H6's exact run time: today, five minutes old
LATE = "2026-06-30T00:00:00+00:00"     # every late-arrival budget long expired


def make_contract(**overrides) -> Contract:
    doc = {
        "contract_id": "corpus_daily",
        "grain": "utc_day",
        "produces": ["spot_close"],
        "inputs": [
            {
                "id": "funding",
                "source": {"type": "static"},
                "criticality": "required",
                "coverage": {"min_count": 3},
                "freshness": {"max_age_hours": 26},
                "late_arrival_hours": 12,
                "remediation": "python -m ingest.binance_funding --start 2026-06",
            }
        ],
    }
    doc.update(overrides)
    return parse_contract(doc)


def good_obs(**overrides) -> dict[str, InputObservation]:
    base = dict(
        input_id="funding", rows=3, coverage_count=3,
        newest_arrival_ts="2026-06-14T16:00:00+00:00",
    )
    base.update(overrides)
    return {"funding": InputObservation(**base)}


def ev(contract, obs, as_of=CLOSED, period=PERIOD):
    return engine.evaluate(contract, obs, period, as_of)


# ---------------------------------------------------------------------------
# States in isolation
# ---------------------------------------------------------------------------

class TestStates:
    def test_ready(self):
        v = ev(make_contract(), good_obs())
        assert v.state == READY
        assert v.action == COMPUTE
        assert v.may_derive
        assert v.is_ready

    def test_partial_day_period_not_closed(self):
        v = ev(make_contract(), good_obs(), as_of=OPEN)
        assert v.state == PARTIAL_DAY
        assert v.action == DEFER
        assert not v.may_derive

    def test_stale_data(self):
        v = ev(make_contract(), good_obs(newest_arrival_ts="2026-06-01T00:00:00+00:00"))
        assert v.state == STALE_DATA
        assert v.action == RETRY

    def test_incomplete_data_within_late_budget(self):
        v = ev(make_contract(), good_obs(rows=1, coverage_count=1))
        assert v.state == INCOMPLETE_DATA
        assert v.action == RETRY

    def test_backfill_required_late_budget_expired(self):
        v = ev(make_contract(), good_obs(rows=1, coverage_count=1), as_of=LATE)
        assert v.state == BACKFILL_REQUIRED
        assert v.action == BLOCK

    def test_backfill_required_carries_a_literal_runnable_command(self):
        v = ev(make_contract(), good_obs(rows=1, coverage_count=1), as_of=LATE)
        assert v.remediation == "python -m ingest.binance_funding --start 2026-06"

    def test_schema_mismatch_missing_column(self):
        c = make_contract(inputs=[{
            "id": "funding", "source": {"type": "static"}, "criticality": "required",
            "schema": {"columns": {"ts": "INTEGER", "rate": "REAL"}},
        }])
        v = ev(c, good_obs(schema={"ts": "INTEGER"}))
        assert v.state == SCHEMA_MISMATCH
        assert v.action == HALT

    def test_schema_mismatch_wrong_dtype(self):
        c = make_contract(inputs=[{
            "id": "funding", "source": {"type": "static"}, "criticality": "required",
            "schema": {"columns": {"ts": "INTEGER"}},
        }])
        v = ev(c, good_obs(schema={"ts": "TEXT"}))
        assert v.state == SCHEMA_MISMATCH

    def test_configuration_error_unknown_grain(self):
        doc = {
            "contract_id": "x", "grain": "lunar_month",
            "inputs": [{"id": "a", "source": {"type": "static"}}],
        }
        v = ev(parse_contract(doc), {})
        assert v.state == CONFIGURATION_ERROR
        assert v.action == HALT

    def test_unknown_on_missing_observation(self):
        v = ev(make_contract(), {})
        assert v.state == UNKNOWN
        assert v.action == BLOCK

    def test_unknown_on_probe_error(self):
        v = ev(make_contract(), {"funding": InputObservation("funding", probe_error="boom")})
        assert v.state == UNKNOWN
        assert v.action == BLOCK

    def test_unknown_when_coverage_not_observed(self):
        v = ev(make_contract(), good_obs(rows=None, coverage_count=None))
        assert v.state == UNKNOWN

    def test_unknown_when_arrival_not_observed(self):
        v = ev(make_contract(), good_obs(newest_arrival_ts=None))
        assert v.state == UNKNOWN

    def test_unknown_when_arrival_is_not_a_timestamp(self):
        v = ev(make_contract(), good_obs(newest_arrival_ts="not-a-date"))
        assert v.state == UNKNOWN

    def test_state_is_never_none(self):
        """Incompleteness is a VERDICT, not a value. There is no code path that
        yields None/NULL — that is F2, and it is the difference between H6's
        funding query (correct) and H6's derivation (not)."""
        for obs in ({}, good_obs(), good_obs(rows=0, coverage_count=0),
                    {"funding": InputObservation("funding", probe_error="x")}):
            for as_of in (OPEN, CLOSED, LATE):
                v = ev(make_contract(), obs, as_of=as_of)
                assert v.state is not None and v.state != ""
                assert v.action is not None and v.action != ""


# ---------------------------------------------------------------------------
# The masquerade — the reason the check order is normative
# ---------------------------------------------------------------------------

class TestCheckOrdering:
    def test_unit_switch_zero_row_partition_is_schema_mismatch_not_incomplete_data(self):
        """
        THE test of the whole ordering rule (§2).

        The Binance ms->us switch silently produces a 0-row partition
        (CLAUDE.md §11). Coverage sees 0 rows and would happily call it
        INCOMPLETE_DATA -> an operator would "backfill" the same broken month
        forever, chasing a data problem that is actually a CODE problem.

        Schema runs first. It sees open_time at 1.7e15 instead of 1.7e12,
        outside the declared band, and calls it what it is.
        """
        c = make_contract(inputs=[{
            "id": "funding", "source": {"type": "static"}, "criticality": "required",
            "schema": {"columns": {"ts": "INTEGER"},
                       "bands": {"ts": [1.0e12, 1.5e15]}},
            "coverage": {"min_count": 1440},
            "late_arrival_hours": 6,
        }])
        # microsecond timestamps: 1000x too large, and coverage is 0.
        obs = {"funding": InputObservation(
            "funding", rows=0, coverage_count=0, schema={"ts": "INTEGER"},
            value_bounds={"ts": ["1750000000000000", "1750086400000000"]},
            newest_arrival_ts="2026-06-14T16:00:00+00:00",
        )}
        v = ev(c, obs, as_of=LATE)  # late enough that coverage WOULD say BACKFILL_REQUIRED

        assert v.state == SCHEMA_MISMATCH, "schema drift must never masquerade as missing data"
        assert v.action == HALT
        # and coverage never even ran for that input — it was short-circuited
        ran = [c_.check_id for c_ in v.checks if c_.input_id == "funding"]
        assert "coverage.min_count.v1" not in ran

    def test_configuration_error_short_circuits_everything(self):
        doc = {"contract_id": "x", "grain": "nope",
               "inputs": [{"id": "a", "source": {"type": "static"}}]}
        v = ev(parse_contract(doc), {})
        assert v.state == CONFIGURATION_ERROR
        assert [c.check_id for c in v.checks] == ["configuration.contract.v1"]

    def test_one_inputs_unknown_does_not_mask_anothers_schema_mismatch(self):
        """
        Short-circuiting GLOBALLY on the first severity>=5 state would be a
        defect, not an optimisation: input A's UNKNOWN (severity 7 -> BLOCK)
        would mask input B's SCHEMA_MISMATCH (severity 5 -> HALT), and the
        pipeline would BLOCK where it was required to HALT.

        Worst-wins on STATE does not imply worst-wins on ACTION, because the
        policy matrix is not monotonic in severity.
        """
        c = make_contract(inputs=[
            {"id": "a", "source": {"type": "static"}, "criticality": "required"},
            {"id": "b", "source": {"type": "static"}, "criticality": "required",
             "schema": {"columns": {"ts": "INTEGER"}}},
        ])
        obs = {
            "a": InputObservation("a", probe_error="unreachable"),          # UNKNOWN (7)
            "b": InputObservation("b", schema={"ts": "TEXT"}),              # SCHEMA_MISMATCH (5)
        }
        v = ev(c, obs)
        assert v.state == UNKNOWN                 # worst state
        assert v.action == HALT                   # but the SCHEMA_MISMATCH still halts
        assert v.input_states == {"a": UNKNOWN, "b": SCHEMA_MISMATCH}


# ---------------------------------------------------------------------------
# The three-way split
# ---------------------------------------------------------------------------

class TestThreeWaySplit:
    """PARTIAL_DAY / INCOMPLETE_DATA / BACKFILL_REQUIRED present as the
    IDENTICAL physical symptom — "fewer rows than expected" — and are separated
    by two clocks. Collapsing them is how H6 happened."""

    @pytest.mark.parametrize("as_of,expected_state,expected_action", [
        (OPEN,   PARTIAL_DAY,       DEFER),   # not finished. time fixes it.
        (CLOSED, INCOMPLETE_DATA,   RETRY),   # closed, within late budget. source may deliver.
        (LATE,   BACKFILL_REQUIRED, BLOCK),   # budget expired. only an operator fixes it.
    ])
    def test_same_shortfall_three_verdicts(self, as_of, expected_state, expected_action):
        v = ev(make_contract(), good_obs(rows=1, coverage_count=1), as_of=as_of)
        assert (v.state, v.action) == (expected_state, expected_action)

    def test_coverage_shortfall_on_an_open_period_is_never_incomplete_data(self):
        """At 00:05 UTC, 1 of 3 funding events having fired is not a data
        problem at all. Two more are coming."""
        v = ev(make_contract(), good_obs(rows=1, coverage_count=1), as_of=OPEN)
        cov = next(c for c in v.checks if c.check_id == "coverage.min_count.v1")
        assert cov.state == PARTIAL_DAY
        assert "still OPEN" in cov.detail

    def test_min_fraction_shortfall(self):
        c = make_contract(inputs=[{
            "id": "funding", "source": {"type": "static"}, "criticality": "required",
            "coverage": {"min_fraction": 1.0}, "late_arrival_hours": 12,
        }])
        v = ev(c, good_obs(coverage_count=900, coverage_denominator=1440))
        assert v.state == INCOMPLETE_DATA

    def test_min_fraction_without_denominator_is_unknown(self):
        c = make_contract(inputs=[{
            "id": "funding", "source": {"type": "static"}, "criticality": "required",
            "coverage": {"min_fraction": 1.0}, "late_arrival_hours": 12,
        }])
        v = ev(c, good_obs(coverage_count=900, coverage_denominator=None))
        assert v.state == UNKNOWN

    def test_zero_denominator_is_unknown_not_a_zero_division(self):
        c = make_contract(inputs=[{
            "id": "funding", "source": {"type": "static"}, "criticality": "required",
            "coverage": {"min_fraction": 1.0}, "late_arrival_hours": 12,
        }])
        v = ev(c, good_obs(coverage_count=0, coverage_denominator=0))
        assert v.state == UNKNOWN


# ---------------------------------------------------------------------------
# Continuity / history window
# ---------------------------------------------------------------------------

class TestContinuity:
    def base(self, **cont):
        return make_contract(inputs=[{
            "id": "funding", "source": {"type": "static"}, "criticality": "required",
            "continuity": {"min_rows": 31, **cont},
        }])

    def test_short_history_window_is_backfill_required(self):
        v = ev(self.base(), good_obs(history_rows=20, has_interior_gaps=False))
        assert v.state == BACKFILL_REQUIRED

    def test_interior_gaps_are_backfill_required(self):
        v = ev(self.base(), good_obs(history_rows=31, has_interior_gaps=True))
        assert v.state == BACKFILL_REQUIRED

    def test_unobserved_history_is_unknown(self):
        v = ev(self.base(), good_obs(history_rows=None))
        assert v.state == UNKNOWN

    def test_unobserved_gaps_are_unknown(self):
        v = ev(self.base(), good_obs(history_rows=31, has_interior_gaps=None))
        assert v.state == UNKNOWN

    def test_clean_window_is_ready(self):
        v = ev(self.base(), good_obs(history_rows=31, has_interior_gaps=False))
        assert v.state == READY

    def test_contract_level_history_window_applies_to_required_inputs(self):
        c = parse_contract({
            "contract_id": "x", "grain": "utc_day",
            "inputs": [{"id": "funding", "source": {"type": "static"}, "criticality": "required"}],
            "history_window": {"min_rows": 31, "continuity": "no_interior_gaps"},
        })
        assert c.input("funding").continuity.min_rows == 31
        v = ev(c, good_obs(history_rows=10, has_interior_gaps=False))
        assert v.state == BACKFILL_REQUIRED


# ---------------------------------------------------------------------------
# Degradation — only along a DECLARED path
# ---------------------------------------------------------------------------

class TestDegradation:
    def contract_with_optional(self, declared_on):
        return parse_contract({
            "contract_id": "corpus_daily", "grain": "utc_day",
            "inputs": [
                {"id": "spot", "source": {"type": "static"}, "criticality": "required"},
                {"id": "dvol", "source": {"type": "static"}, "criticality": "optional",
                 "freshness": {"max_age_hours": 30}, "late_arrival_hours": 24,
                 "degradation": {"on": declared_on,
                                 "outputs_unavailable": ["ivrv_vrp_spread", "dvol_close"]}},
            ],
        })

    def obs(self, dvol_arrival):
        return {
            "spot": InputObservation("spot", rows=1440, coverage_count=1440),
            "dvol": InputObservation("dvol", rows=1, newest_arrival_ts=dvol_arrival),
        }

    def test_declared_degradation_degrades_and_never_blocks(self):
        """The IVRV precedent, generalised (CLAUDE.md §16): a stale DVOL day
        yields ivrv=unavailable and NEVER blocks H5/G1/G3."""
        c = self.contract_with_optional(["STALE_DATA"])
        v = ev(c, self.obs("2026-06-01T00:00:00+00:00"))
        assert v.state == STALE_DATA
        assert v.action == DEGRADE
        assert v.may_derive                                  # H5/G1/G3 are NOT blocked
        assert v.outputs_unavailable == ("dvol_close", "ivrv_vrp_spread")

    def test_undeclared_degradation_blocks_it_is_never_taken_silently(self):
        """Degradation is only ever available along a path the contract
        DECLARED IN ADVANCE. Ad-hoc "compute with what we have" is exactly what
        wrote a 00:05 tick into spot_close."""
        c = self.contract_with_optional(["INCOMPLETE_DATA"])  # STALE_DATA NOT declared
        v = ev(c, self.obs("2026-06-01T00:00:00+00:00"))
        assert v.state == STALE_DATA
        assert v.action == BLOCK
        assert not v.may_derive
        assert v.outputs_unavailable == ()

    def test_outputs_are_marked_unavailable_never_imputed(self):
        c = self.contract_with_optional(["STALE_DATA"])
        v = ev(c, self.obs("2026-06-01T00:00:00+00:00"))
        assert "ivrv_vrp_spread" in v.outputs_unavailable

    def test_required_partial_day_outranks_optional_degrade(self):
        """DEFER outranks DEGRADE. Writing ANYTHING for a period that has not
        finished is the H6 bug — no optional degradation can license it."""
        c = self.contract_with_optional(["STALE_DATA"])
        v = ev(c, self.obs("2026-06-01T00:00:00+00:00"), as_of=OPEN)
        assert v.action == DEFER
        assert not v.may_derive


# ---------------------------------------------------------------------------
# The policy matrix — every cell
# ---------------------------------------------------------------------------

class TestPolicyMatrix:
    @pytest.mark.parametrize("state,expected", [
        (READY, COMPUTE), (PARTIAL_DAY, DEFER), (STALE_DATA, RETRY),
        (INCOMPLETE_DATA, RETRY), (BACKFILL_REQUIRED, BLOCK),
        (SCHEMA_MISMATCH, HALT), (CONFIGURATION_ERROR, HALT), (UNKNOWN, BLOCK),
    ])
    def test_required_column(self, state, expected):
        assert policy.resolve(state, policy.REQUIRED) == expected

    @pytest.mark.parametrize("state,expected", [
        (READY, COMPUTE), (PARTIAL_DAY, DEFER), (STALE_DATA, DEGRADE),
        (INCOMPLETE_DATA, DEGRADE), (BACKFILL_REQUIRED, DEGRADE),
        (SCHEMA_MISMATCH, HALT), (CONFIGURATION_ERROR, HALT), (UNKNOWN, BLOCK),
    ])
    def test_optional_column_with_declared_degradation(self, state, expected):
        assert policy.resolve(state, policy.OPTIONAL, degradation_declared=True) == expected

    def test_schema_mismatch_halts_even_for_an_optional_input(self):
        """The one state where an OPTIONAL input still halts. A unit change or a
        silently-unwritten column corrupts everything downstream, retroactively,
        without any symptom. There is no safe degraded computation against data
        whose MEANING has changed."""
        assert policy.resolve(SCHEMA_MISMATCH, policy.OPTIONAL, degradation_declared=True) == HALT

    def test_unknown_blocks_fail_closed(self):
        for crit in (policy.REQUIRED, policy.OPTIONAL):
            assert policy.resolve(UNKNOWN, crit, degradation_declared=True) == BLOCK

    def test_per_contract_override(self):
        overrides = {STALE_DATA: {policy.REQUIRED: BLOCK}}
        assert policy.resolve(STALE_DATA, policy.REQUIRED, overrides=overrides) == BLOCK
        assert policy.resolve(STALE_DATA, policy.OPTIONAL, overrides=overrides) == BLOCK  # undeclared

    def test_override_flows_through_the_engine(self):
        c = make_contract(policy={PARTIAL_DAY: {policy.REQUIRED: BLOCK}})
        v = ev(c, good_obs(), as_of=OPEN)
        assert v.action == BLOCK

    def test_bad_state_rejected(self):
        with pytest.raises(policy.PolicyError):
            policy.resolve("MOSTLY_FINE", policy.REQUIRED)

    def test_bad_criticality_rejected(self):
        with pytest.raises(policy.PolicyError):
            policy.resolve(READY, "sort_of_important")

    def test_invalid_override_is_rejected_at_parse_time(self):
        with pytest.raises(ContractConfigurationError):
            make_contract(policy={"NOT_A_STATE": {policy.REQUIRED: BLOCK}})
        with pytest.raises(ContractConfigurationError):
            make_contract(policy={READY: {policy.REQUIRED: "VIBES"}})


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

class TestAggregation:
    def test_worst_wins(self):
        assert worst([READY, PARTIAL_DAY, SCHEMA_MISMATCH]) == SCHEMA_MISMATCH
        assert worst([READY, READY]) == READY

    def test_worst_of_nothing_is_unknown_not_ready(self):
        """L2: absence of evidence of completeness is not evidence of
        completeness. A gate asked to aggregate nothing has established
        nothing."""
        assert worst([]) == UNKNOWN

    def test_worst_action_of_nothing_blocks(self):
        assert worst_action([]) == BLOCK

    def test_defer_outranks_degrade_and_retry(self):
        assert worst_action([DEGRADE, DEFER]) == DEFER
        assert worst_action([RETRY, DEFER]) == DEFER

    def test_halt_outranks_everything(self):
        assert worst_action([COMPUTE, DEGRADE, RETRY, DEFER, BLOCK, HALT]) == HALT

    def test_severity_of_unknown_state_raises(self):
        with pytest.raises(UnknownStateError):
            severity("PROBABLY_OK")

    def test_multi_input_worst_wins_through_the_engine(self):
        c = make_contract(inputs=[
            {"id": "a", "source": {"type": "static"}, "criticality": "required",
             "coverage": {"min_count": 3}, "late_arrival_hours": 12},
            {"id": "b", "source": {"type": "static"}, "criticality": "required",
             "coverage": {"min_count": 3}, "late_arrival_hours": 12},
        ])
        obs = {
            "a": InputObservation("a", coverage_count=3),
            "b": InputObservation("b", coverage_count=1),
        }
        v = ev(c, obs)
        assert v.input_states == {"a": READY, "b": INCOMPLETE_DATA}
        assert v.state == INCOMPLETE_DATA


# ---------------------------------------------------------------------------
# Determinism  (§6)
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_repeated_evaluation_is_byte_stable(self):
        assert validation.assert_deterministic(
            make_contract(), good_obs(rows=1, coverage_count=1), PERIOD, CLOSED, iterations=5
        ) == []

    def test_as_of_is_an_argument_never_a_syscall(self):
        """L6. The verdict for a fixed as_of must not drift as the wall clock
        moves — that is what makes replay possible at all."""
        v1 = ev(make_contract(), good_obs(), as_of=OPEN)
        v2 = ev(make_contract(), good_obs(), as_of=OPEN)
        assert v1.to_dict() == v2.to_dict()
        assert v1.as_of == "2026-06-14T00:05:00+00:00"

    def test_engine_module_never_calls_datetime_now(self):
        import ast
        for module in ("engine.py", "checks.py", "policy.py", "states.py"):
            src = (ROOT / "research" / "dq" / module).read_text(encoding="utf-8")
            for node in ast.walk(ast.parse(src)):
                if isinstance(node, ast.Attribute) and node.attr in ("now", "utcnow", "today"):
                    pytest.fail(f"{module} reads a clock; the gate must never do that (L6)")

    def test_naive_as_of_is_treated_as_utc(self):
        v = ev(make_contract(), good_obs(), as_of="2026-06-15T02:00:00")
        assert v.state == READY

    def test_datetime_as_of_accepted(self):
        v = ev(make_contract(), good_obs(),
               as_of=datetime(2026, 6, 15, 2, tzinfo=timezone.utc))
        assert v.state == READY

    def test_contract_hash_is_stable_across_float_spellings(self):
        a = parse_contract({"contract_id": "x", "grain": "utc_day", "inputs": [
            {"id": "i", "source": {"type": "static"},
             "coverage": {"min_fraction": 1.0}, "late_arrival_hours": 6}]})
        b = parse_contract({"contract_id": "x", "grain": "utc_day", "inputs": [
            {"id": "i", "source": {"type": "static"},
             "coverage": {"min_fraction": Decimal("1.000")}, "late_arrival_hours": 6}]})
        assert a.contract_hash() == b.contract_hash()

    def test_contract_hash_changes_when_a_floor_changes(self):
        a = make_contract()
        b = make_contract(inputs=[{
            "id": "funding", "source": {"type": "static"}, "criticality": "required",
            "coverage": {"min_count": 2}, "late_arrival_hours": 12,
        }])
        assert a.contract_hash() != b.contract_hash()


# ---------------------------------------------------------------------------
# Check registry — semantics are frozen by id
# ---------------------------------------------------------------------------

class TestCheckRegistry:
    def test_redefining_a_check_under_an_existing_id_is_refused(self):
        """contract_versioning.md §3: freeze the contract perfectly, and a
        maintainer can STILL change what `min_count: 3` MEANS. Every historical
        receipt would replay to a different verdict while every hash still
        matched. A new meaning needs a new id."""
        def impostor(ctx):
            raise AssertionError("should never run")

        with pytest.raises(UnknownCheckError, match="already registered"):
            register_check("temporal.period_closed.v1", impostor)

    def test_a_new_check_id_may_be_registered_additively(self):
        def temporal_v2(ctx):
            return check_temporal(ctx)

        register_check("temporal.period_closed.v2", temporal_v2)
        c = make_contract(temporal_check="temporal.period_closed.v2")
        assert ev(c, good_obs()).state == READY

    def test_contract_naming_an_unknown_check_is_a_configuration_error(self):
        c = make_contract(temporal_check="temporal.vibes.v9")
        v = ev(c, good_obs())
        assert v.state == CONFIGURATION_ERROR
        assert v.action == HALT


# ---------------------------------------------------------------------------
# Contract validation
# ---------------------------------------------------------------------------

class TestContractValidation:
    def test_optional_input_without_a_degradation_path_is_invalid(self):
        c = parse_contract({"contract_id": "x", "grain": "utc_day", "inputs": [
            {"id": "i", "source": {"type": "static"}, "criticality": "optional"}]})
        assert any("declares no degradation path" in e for e in validate_contract(c))

    def test_coverage_without_a_late_budget_is_invalid(self):
        c = parse_contract({"contract_id": "x", "grain": "utc_day", "inputs": [
            {"id": "i", "source": {"type": "static"}, "coverage": {"min_count": 3}}]})
        errors = validate_contract(c)
        assert any("late_arrival_hours" in e for e in errors)

    def test_coverage_without_a_late_budget_yields_unknown_not_a_guess(self):
        """Defence in depth: even if the configuration check were bypassed, the
        coverage check refuses to invent a budget it was not given."""
        c = Contract(contract_id="x", grain="utc_day", inputs=parse_contract(
            {"contract_id": "x", "grain": "utc_day",
             "inputs": [{"id": "i", "source": {"type": "static"},
                         "coverage": {"min_count": 3}}]}).inputs)
        from checks import check_coverage
        ctx = CheckContext(
            contract=c, period=PERIOD,
            as_of=datetime(2026, 6, 15, 2, tzinfo=timezone.utc),
            spec=c.inputs[0], observation=InputObservation("i", coverage_count=1),
        )
        assert check_coverage(ctx).state == UNKNOWN

    def test_negative_min_count_invalid(self):
        c = parse_contract({"contract_id": "x", "grain": "utc_day", "inputs": [
            {"id": "i", "source": {"type": "static"},
             "coverage": {"min_count": -1}, "late_arrival_hours": 1}]})
        assert any("min_count must be >= 0" in e for e in validate_contract(c))

    def test_min_fraction_outside_0_1_invalid(self):
        c = parse_contract({"contract_id": "x", "grain": "utc_day", "inputs": [
            {"id": "i", "source": {"type": "static"},
             "coverage": {"min_fraction": 1.5}, "late_arrival_hours": 1}]})
        assert any("min_fraction must be in [0, 1]" in e for e in validate_contract(c))

    def test_missing_contract_id(self):
        with pytest.raises(ContractConfigurationError, match="contract_id"):
            parse_contract({"grain": "utc_day", "inputs": [{"id": "i", "source": {"type": "static"}}]})

    def test_missing_inputs(self):
        with pytest.raises(ContractConfigurationError, match="inputs"):
            parse_contract({"contract_id": "x", "grain": "utc_day", "inputs": []})

    def test_duplicate_input_id(self):
        with pytest.raises(ContractConfigurationError, match="duplicate input"):
            parse_contract({"contract_id": "x", "grain": "utc_day", "inputs": [
                {"id": "i", "source": {"type": "static"}},
                {"id": "i", "source": {"type": "static"}}]})

    def test_input_without_a_source_type(self):
        with pytest.raises(ContractConfigurationError, match="source"):
            parse_contract({"contract_id": "x", "grain": "utc_day",
                            "inputs": [{"id": "i", "source": {}}]})

    def test_bad_criticality(self):
        with pytest.raises(ContractConfigurationError, match="criticality"):
            parse_contract({"contract_id": "x", "grain": "utc_day", "inputs": [
                {"id": "i", "source": {"type": "static"}, "criticality": "kinda"}]})

    def test_degradation_naming_an_unknown_state(self):
        with pytest.raises(ContractConfigurationError, match="unknown state"):
            parse_contract({"contract_id": "x", "grain": "utc_day", "inputs": [
                {"id": "i", "source": {"type": "static"}, "criticality": "optional",
                 "degradation": {"on": ["MOSTLY_FINE"]}}]})

    def test_inverted_band(self):
        with pytest.raises(ContractConfigurationError, match="min > max"):
            parse_contract({"contract_id": "x", "grain": "utc_day", "inputs": [
                {"id": "i", "source": {"type": "static"},
                 "schema": {"bands": {"ts": [10, 1]}}}]})

    def test_yaml_working_copy_parses(self):
        text = """
contract_id: corpus_daily
grain: utc_day
owner: research
produces: [spot_close]
inputs:
  - id: funding
    source: {type: static}
    criticality: required
    coverage: {min_count: 3}
    late_arrival_hours: 12
"""
        c = parse_contract_text(text)
        assert c.contract_id == "corpus_daily"
        assert c.input("funding").coverage.min_count == 3

    def test_malformed_yaml(self):
        with pytest.raises(ContractConfigurationError, match="not valid YAML"):
            parse_contract_text("contract_id: [unclosed")

    def test_framework_is_internally_coherent(self):
        assert validation.validate_framework() == []

    def test_validate_contract_document_reports_not_raises(self):
        assert validation.validate_contract_document({"grain": "utc_day"})[0].startswith("DQ-C1")


# ---------------------------------------------------------------------------
# Grain
# ---------------------------------------------------------------------------

class TestGrain:
    def test_utc_day_bounds(self):
        start, end = period_bounds("utc_day", "2026-06-14")
        assert start.isoformat() == "2026-06-14T00:00:00+00:00"
        assert end.isoformat() == "2026-06-15T00:00:00+00:00"

    def test_utc_hour_bounds(self):
        start, end = period_bounds("utc_hour", "2026-06-14T05")
        assert (end - start).total_seconds() == 3600

    def test_a_new_grain_is_a_registry_entry_not_an_engine_change(self):
        c = parse_contract({"contract_id": "x", "grain": "utc_hour", "inputs": [
            {"id": "i", "source": {"type": "static"},
             "coverage": {"min_count": 60}, "late_arrival_hours": 1}]})
        v = engine.evaluate(
            c, {"i": InputObservation("i", coverage_count=60)},
            "2026-06-14T05", "2026-06-14T07:00:00+00:00",
        )
        assert v.state == READY

    def test_unparseable_period_for_grain(self):
        v = engine.evaluate(make_contract(), good_obs(), "not-a-date", CLOSED)
        assert v.state == CONFIGURATION_ERROR


# ---------------------------------------------------------------------------
# Observations round-trip (the replay substrate)
# ---------------------------------------------------------------------------

class TestObservation:
    def test_round_trip(self):
        obs = InputObservation("i", rows=3, coverage_count=3, schema={"a": "INTEGER"},
                               value_bounds={"a": ["1", "2"]}, has_interior_gaps=False)
        assert InputObservation.from_dict(obs.to_dict()) == obs

    def test_unknown_field_is_refused_not_dropped(self):
        """A silently-dropped field would change what replay sees while every
        hash still verified."""
        with pytest.raises(ObservationError, match="unknown observation field"):
            InputObservation.from_dict({"input_id": "i", "vibes": "good"})

    def test_missing_input_id(self):
        with pytest.raises(ObservationError):
            InputObservation.from_dict({"rows": 1})

    def test_malformed_bound(self):
        with pytest.raises(ObservationError):
            InputObservation("i", value_bounds={"a": ["1"]}).bound("a")

    def test_non_numeric_bound(self):
        with pytest.raises(ObservationError):
            InputObservation("i", value_bounds={"a": ["x", "y"]}).bound("a")

    def test_unreadable_bound_is_unknown_not_a_crash(self):
        c = make_contract(inputs=[{
            "id": "funding", "source": {"type": "static"},
            "schema": {"bands": {"ts": [1, 10]}}}])
        v = ev(c, {"funding": InputObservation("funding", value_bounds={"ts": ["x", "y"]})})
        assert v.state == UNKNOWN

    def test_missing_band_observation_is_unknown(self):
        c = make_contract(inputs=[{
            "id": "funding", "source": {"type": "static"},
            "schema": {"bands": {"ts": [1, 10]}}}])
        v = ev(c, {"funding": InputObservation("funding", rows=1)})
        assert v.state == UNKNOWN

    def test_unobservable_schema_is_unknown_not_assumed_fine(self):
        c = make_contract(inputs=[{
            "id": "funding", "source": {"type": "static"},
            "schema": {"columns": {"ts": "INTEGER"}}}])
        v = ev(c, {"funding": InputObservation("funding", rows=1, schema=None)})
        assert v.state == UNKNOWN
