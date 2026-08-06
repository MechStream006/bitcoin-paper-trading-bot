"""
Tests for research/label_store/registry.py (Phase 3 Label Store roadmap
v1.1.0, WBS 3.1 -- the first Label Store milestone).

Per WBS 3.1's own exit criterion: "Registry accepts a hand-written test
Specification; Tier A refuses a malformed one on each of the ten
mandatory fields, one negative test per field; an invariant test proves
maturation does not change a Specification hash (§11.2)." Every
Specification in this file is hand-written -- no H6 data, no real
convention, no production binding is imported or wrapped (that is WBS
3.3/3.4, later milestones).

Pure stdlib module (sqlite3/json/hashlib) -- no research dependency, runs
in any environment.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

# Loaded via importlib under a distinct module name, NOT the house
# sys.path-insert + bare `import registry` pattern -- that pattern
# collides in sys.modules with research/feature_store/registry.py, which
# is also bare-imported as `registry` by test_feature_store_registry.py in
# the same suite (confirmed reproducible: both test files pass in
# isolation, the second one to import fails full-suite with
# "unexpected keyword argument" once bitten by the wrong cached module).
# This is the same class of same-named-sibling-module hazard already
# logged for research/dq/engine.py vs research/evidence/engine.py
# (CLAUDE.md §18, Increment 6) -- there it was pre-existing and left
# unfixed as out-of-scope; here it is newly introduced by this milestone,
# so it is fixed at the point of introduction rather than deferred.
_spec = importlib.util.spec_from_file_location(
    "label_store_registry",
    Path(__file__).resolve().parents[1] / "research" / "label_store" / "registry.py",
)
reg = importlib.util.module_from_spec(_spec)
sys.modules["label_store_registry"] = reg  # dataclass() needs this at class-definition time
_spec.loader.exec_module(reg)


NOW = "2026-08-05T00:00:00+00:00"
LATER = "2026-09-01T00:00:00+00:00"


@pytest.fixture
def db(tmp_path) -> Path:
    p = tmp_path / "label_store.db"
    reg.init_db(p)
    return p


def _valid_spec_kwargs(db_path, **overrides) -> dict:
    d = reg.register_definition(
        db_path, overrides.pop("definition_name", "one_day_forward_return"),
        "test rationale", "test interpretation", "rohith", NOW,
    )
    defaults = dict(
        definition_name=d.name,
        definition_version=d.version,
        horizon_days=5,
        horizon_governance="calendar_span",
        fill_convention="same-day spot close, both sides",
        cost_convention="flat 15bps, charged once at exit",
        direction_convention="long-only",
        maturity_rule="matured once horizon_days have elapsed on the venue calendar",
        validity_condition="valid once the subject's entry is recorded",
        conditioning_propagation="preserving",
        binding_id="test.binding",
        engine_version="v1",
        actor="rohith",
        registered_at=NOW,
    )
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# Label Definitions
# ---------------------------------------------------------------------------

class TestLabelDefinitions:
    def test_first_registration_is_version_1(self, db):
        handle = reg.register_definition(db, "fwd_5d", "rationale", "interpretation", "rohith", NOW)
        assert handle == reg.LabelDefinitionHandle(name="fwd_5d", version=1)

    def test_second_registration_of_same_name_is_version_2(self, db):
        reg.register_definition(db, "fwd_5d", "rationale v1", "interpretation", "rohith", NOW)
        second = reg.register_definition(db, "fwd_5d", "rationale v2", "interpretation", "rohith", NOW)
        assert second.version == 2

    def test_definitions_for_different_names_are_independent(self, db):
        a = reg.register_definition(db, "label_a", "r", "i", "rohith", NOW)
        b = reg.register_definition(db, "label_b", "r", "i", "rohith", NOW)
        assert a.version == 1
        assert b.version == 1

    @pytest.mark.parametrize("field", ["name", "rationale", "interpretation", "actor"])
    def test_blank_required_field_is_refused(self, db, field):
        kwargs = dict(name="x", rationale="r", interpretation="i", actor="rohith")
        kwargs[field] = "   "
        with pytest.raises(reg.LabelRegistryError):
            reg.register_definition(db, kwargs["name"], kwargs["rationale"],
                                     kwargs["interpretation"], kwargs["actor"], NOW)

    def test_definitions_are_never_updated_in_place(self, db):
        reg.register_definition(db, "fwd_5d", "original", "interpretation", "rohith", NOW)
        con = sqlite3.connect(db)
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            con.execute("UPDATE label_definitions SET rationale = 'changed' WHERE name = 'fwd_5d'")

    def test_definitions_are_never_deleted(self, db):
        reg.register_definition(db, "fwd_5d", "r", "i", "rohith", NOW)
        con = sqlite3.connect(db)
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            con.execute("DELETE FROM label_definitions WHERE name = 'fwd_5d'")

    def test_get_definition_round_trips(self, db):
        reg.register_definition(db, "fwd_5d", "the rationale", "the interpretation", "rohith", NOW)
        d = reg.get_definition(db, "fwd_5d", 1)
        assert d["rationale"] == "the rationale"
        assert d["interpretation"] == "the interpretation"

    def test_get_definition_missing_returns_none(self, db):
        assert reg.get_definition(db, "does_not_exist", 1) is None

    def test_get_latest_definition_returns_highest_version(self, db):
        reg.register_definition(db, "fwd_5d", "v1", "i", "rohith", NOW)
        reg.register_definition(db, "fwd_5d", "v2", "i", "rohith", NOW)
        latest = reg.get_latest_definition(db, "fwd_5d")
        assert latest["version"] == 2
        assert latest["rationale"] == "v2"


# ---------------------------------------------------------------------------
# Binding resolution
# ---------------------------------------------------------------------------

class TestBindingResolution:
    def test_round_trip(self):
        reg.register_binding("t.roundtrip", "v1", lambda: 42)
        fn = reg.get_binding("t.roundtrip", "v1")
        assert fn() == 42

    def test_unresolved_pair_raises(self):
        with pytest.raises(reg.UnknownBindingError):
            reg.get_binding("does.not.exist", "v99")

    def test_different_engine_versions_are_independent(self):
        reg.register_binding("t.versioned", "v1", lambda: "old")
        reg.register_binding("t.versioned", "v2", lambda: "new")
        assert reg.get_binding("t.versioned", "v1")() == "old"
        assert reg.get_binding("t.versioned", "v2")() == "new"

    def test_registering_a_new_binding_does_not_disturb_an_existing_one(self):
        reg.register_binding("t.stable", "v1", lambda: "first")
        first = reg.get_binding("t.stable", "v1")
        reg.register_binding("t.other", "v1", lambda: "second")
        assert reg.get_binding("t.stable", "v1") is first


# ---------------------------------------------------------------------------
# register_specification() -- Tier A, ten fields, one negative test each
# ---------------------------------------------------------------------------

class TestTierACompleteness:
    def test_valid_hand_written_specification_is_registered(self, db):
        kwargs = _valid_spec_kwargs(db)
        spec_hash = reg.register_specification(db, **kwargs)
        spec = reg.get_specification(db, spec_hash)
        assert spec["phase"] == "Registered"
        assert spec["rejection_reason"] is None

    # Field 1/10 -- Identity: Definition version reference (§8.2).
    def test_field_01_definition_reference_is_rejected_when_blank_or_unresolvable(self, db):
        valid = _valid_spec_kwargs(db)
        kwargs = {**valid, "definition_name": ""}
        with pytest.raises(reg.SpecificationRejected) as exc_info:
            reg.register_specification(db, **kwargs)
        assert any("definition_name" in e for e in exc_info.value.errors)

        kwargs2 = _valid_spec_kwargs(db, definition_version=999)
        with pytest.raises(reg.SpecificationRejected) as exc_info2:
            reg.register_specification(db, **kwargs2)
        assert any("does not exist" in e for e in exc_info2.value.errors)

    # Field 2/10 -- Horizon (§8.2; identity per §16.1).
    def test_field_02_horizon_days_is_rejected_when_missing_or_zero(self, db):
        kwargs = _valid_spec_kwargs(db, horizon_days=0)
        with pytest.raises(reg.SpecificationRejected) as exc_info:
            reg.register_specification(db, **kwargs)
        assert any("horizon_days" in e for e in exc_info.value.errors)

    # Field 3/10 -- Horizon governance (§17 calendar trap; LR7).
    def test_field_03_horizon_governance_is_rejected_when_invalid(self, db):
        kwargs = _valid_spec_kwargs(db, horizon_governance="wall_clock")
        with pytest.raises(reg.SpecificationRejected) as exc_info:
            reg.register_specification(db, **kwargs)
        assert any("horizon_governance" in e for e in exc_info.value.errors)

    # Field 4/10 -- Fill convention (§8.2; PS1).
    def test_field_04_fill_convention_is_rejected_when_blank(self, db):
        kwargs = _valid_spec_kwargs(db, fill_convention="  ")
        with pytest.raises(reg.SpecificationRejected) as exc_info:
            reg.register_specification(db, **kwargs)
        assert any("fill_convention" in e for e in exc_info.value.errors)

    # Field 5/10 -- Cost convention (§8.2).
    def test_field_05_cost_convention_is_rejected_when_blank(self, db):
        kwargs = _valid_spec_kwargs(db, cost_convention="")
        with pytest.raises(reg.SpecificationRejected) as exc_info:
            reg.register_specification(db, **kwargs)
        assert any("cost_convention" in e for e in exc_info.value.errors)

    # Field 6/10 -- Direction convention (§8.2).
    def test_field_06_direction_convention_is_rejected_when_blank(self, db):
        kwargs = _valid_spec_kwargs(db, direction_convention="")
        with pytest.raises(reg.SpecificationRejected) as exc_info:
            reg.register_specification(db, **kwargs)
        assert any("direction_convention" in e for e in exc_info.value.errors)

    # Field 7/10 -- Maturity rule (§8.2; §12.2's Pending -> Matured condition).
    def test_field_07_maturity_rule_is_rejected_when_blank(self, db):
        kwargs = _valid_spec_kwargs(db, maturity_rule="")
        with pytest.raises(reg.SpecificationRejected) as exc_info:
            reg.register_specification(db, **kwargs)
        assert any("maturity_rule" in e for e in exc_info.value.errors)

    # Field 8/10 -- Validity condition (§8.2).
    def test_field_08_validity_condition_is_rejected_when_blank(self, db):
        kwargs = _valid_spec_kwargs(db, validity_condition="")
        with pytest.raises(reg.SpecificationRejected) as exc_info:
            reg.register_specification(db, **kwargs)
        assert any("validity_condition" in e for e in exc_info.value.errors)

    # Field 9/10 -- Conditioning propagation (§8.2; PS7, IQROS-P12).
    def test_field_09_conditioning_propagation_is_rejected_when_invalid(self, db):
        kwargs = _valid_spec_kwargs(db, conditioning_propagation="not-a-real-rule")
        with pytest.raises(reg.SpecificationRejected) as exc_info:
            reg.register_specification(db, **kwargs)
        assert any("conditioning_propagation" in e for e in exc_info.value.errors)

    # Field 10/10 -- Binding: exactly one implementation identity + engine
    # version (§8.2, §8.3).
    def test_field_10_binding_is_rejected_when_blank(self, db):
        kwargs = _valid_spec_kwargs(db, binding_id="")
        with pytest.raises(reg.SpecificationRejected) as exc_info:
            reg.register_specification(db, **kwargs)
        assert any("binding_id" in e for e in exc_info.value.errors)

    def test_multiple_simultaneous_failures_are_all_named(self, db):
        kwargs = _valid_spec_kwargs(db, fill_convention="", binding_id="")
        with pytest.raises(reg.SpecificationRejected) as exc_info:
            reg.register_specification(db, **kwargs)
        assert len(exc_info.value.errors) >= 2

    def test_rejection_is_a_permanent_record_not_merely_an_exception(self, db):
        kwargs = _valid_spec_kwargs(db, maturity_rule="")
        with pytest.raises(reg.SpecificationRejected) as exc_info:
            reg.register_specification(db, **kwargs)
        spec = reg.get_specification(db, exc_info.value.spec_hash)
        assert spec is not None
        assert spec["phase"] == "Rejected"
        assert "maturity_rule" in spec["rejection_reason"]

    def test_registering_identical_content_twice_is_idempotent(self, db):
        kwargs = _valid_spec_kwargs(db)
        first = reg.register_specification(db, **kwargs)
        second = reg.register_specification(db, **kwargs)
        assert first == second
        con = sqlite3.connect(db)
        count = con.execute(
            "SELECT COUNT(*) FROM label_specifications WHERE spec_hash = ?", (first,)
        ).fetchone()[0]
        assert count == 1

    def test_different_horizons_produce_different_hashes(self, db):
        base = _valid_spec_kwargs(db)
        a = reg.register_specification(db, **{**base, "horizon_days": 5})
        b = reg.register_specification(db, **{**base, "horizon_days": 30})
        assert a != b

    def test_different_conventions_produce_different_hashes(self, db):
        base = _valid_spec_kwargs(db)
        a = reg.register_specification(db, **{**base, "cost_convention": "flat 15bps"})
        b = reg.register_specification(db, **{**base, "cost_convention": "turnover-based cost"})
        assert a != b


# ---------------------------------------------------------------------------
# §11.2 invariant -- maturation is not a version change
# ---------------------------------------------------------------------------

class TestMaturationIsNotAVersionChange:
    def test_spec_hash_is_invariant_to_registration_time(self, db):
        """§11.2: 'Labels maturing further do not alter [the Specification].
        Requiring higher maturity is a new declaration, not a new
        version.' At this milestone's scope there is no maturity-state
        field on a Specification at all -- this test proves the hash is a
        pure function of the declared contract by showing two
        registrations of identical content at two different points in
        time (simulating that calendar time, and therefore maturity
        elsewhere in the system, has elapsed between them) still produce
        the same spec_hash and are treated as the same specification."""
        kwargs_now = _valid_spec_kwargs(db, registered_at=NOW)
        first = reg.register_specification(db, **kwargs_now)

        kwargs_later = dict(kwargs_now)
        kwargs_later["registered_at"] = LATER
        second = reg.register_specification(db, **kwargs_later)

        assert first == second

    def test_specification_content_carries_no_maturity_field(self, db):
        spec_hash = reg.register_specification(db, **_valid_spec_kwargs(db))
        spec = reg.get_specification(db, spec_hash)
        maturity_like_keys = {k for k in spec if "matur" in k.lower() and k != "maturity_rule"}
        assert maturity_like_keys == set()


# ---------------------------------------------------------------------------
# activate_specification() -- binding equivalence (ADR-I010 §13 Tier B
# mechanism, reused; distinct from ADR-I011's own §12 Tier B -- see module
# docstring)
# ---------------------------------------------------------------------------

class TestSpecificationActivation:
    def test_passing_check_activates_the_specification(self, db):
        spec_hash = reg.register_specification(db, **_valid_spec_kwargs(db))
        reg.activate_specification(db, spec_hash, lambda: True)
        assert reg.get_specification(db, spec_hash)["phase"] == "Active"

    def test_failing_check_rejects_and_persists_permanently(self, db):
        spec_hash = reg.register_specification(db, **_valid_spec_kwargs(db))
        with pytest.raises(reg.SpecificationRejected):
            reg.activate_specification(db, spec_hash, lambda: False)
        spec = reg.get_specification(db, spec_hash)
        assert spec["phase"] == "Rejected"
        assert spec["rejection_reason"] is not None

    def test_an_exception_in_the_check_is_treated_as_failure_not_propagated(self, db):
        spec_hash = reg.register_specification(db, **_valid_spec_kwargs(db))

        def broken_check():
            raise RuntimeError("binding crashed")

        with pytest.raises(reg.SpecificationRejected) as exc_info:
            reg.activate_specification(db, spec_hash, broken_check)
        assert "binding crashed" in exc_info.value.errors[0]
        assert reg.get_specification(db, spec_hash)["phase"] == "Rejected"

    def test_activating_a_non_registered_specification_raises(self, db):
        spec_hash = reg.register_specification(db, **_valid_spec_kwargs(db))
        reg.activate_specification(db, spec_hash, lambda: True)  # now Active
        with pytest.raises(reg.TransitionError):
            reg.activate_specification(db, spec_hash, lambda: True)  # already Active

    def test_only_active_specifications_may_be_referenced_by_a_new_dataset(self, db):
        # This module does not itself enforce dataset citation (a future
        # Dataset Builder concern, ADR-I012) -- this test documents the
        # phase value a future consumer would check, per ADR-I011 §12.1.
        spec_hash = reg.register_specification(db, **_valid_spec_kwargs(db))
        assert reg.get_specification(db, spec_hash)["phase"] != "Active"
        reg.activate_specification(db, spec_hash, lambda: True)
        assert reg.get_specification(db, spec_hash)["phase"] == "Active"


# ---------------------------------------------------------------------------
# Phase transitions -- allowed arcs only (§12.1)
# ---------------------------------------------------------------------------

class TestPhaseTransitions:
    def test_retire_from_deprecated(self, db):
        spec_hash = reg.register_specification(db, **_valid_spec_kwargs(db))
        reg.activate_specification(db, spec_hash, lambda: True)
        reg._transition_phase(db, spec_hash, "Active", "Deprecated")
        reg.retire_specification(db, spec_hash, "Deprecated")
        assert reg.get_specification(db, spec_hash)["phase"] == "Retired"

    def test_illegal_arc_is_refused(self, db):
        spec_hash = reg.register_specification(db, **_valid_spec_kwargs(db))
        with pytest.raises(reg.TransitionError):
            # Registered -> Retired is not an allowed arc.
            reg.retire_specification(db, spec_hash, "Registered")
        assert reg.get_specification(db, spec_hash)["phase"] == "Registered"

    def test_transition_with_wrong_current_phase_is_refused(self, db):
        spec_hash = reg.register_specification(db, **_valid_spec_kwargs(db))
        reg.activate_specification(db, spec_hash, lambda: True)
        # spec is Active; claiming it is Registered must be refused.
        with pytest.raises(reg.TransitionError):
            reg._transition_phase(db, spec_hash, "Registered", "Active")
