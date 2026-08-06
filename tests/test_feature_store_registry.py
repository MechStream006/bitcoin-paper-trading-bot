"""
Tests for research/feature_store/registry.py (engineering roadmap Phase 2,
WBS 2.1 + 2.3 -- the first Feature Store milestone).

Per the roadmap's own Sprint exit criterion: "Feature registry accepts a
hand-written test Specification; Tier A/B validation refuses a malformed
one." Every Specification in this file is hand-written -- no production
feature code is imported or wrapped (that is WBS 2.4, a later milestone).

Pure stdlib module (sqlite3/json/hashlib) -- no research dependency, runs
in any environment.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research" / "feature_store"))

import registry as reg  # noqa: E402


NOW = "2026-08-05T00:00:00+00:00"


@pytest.fixture
def db(tmp_path) -> Path:
    p = tmp_path / "feature_store.db"
    reg.init_db(p)
    return p


def _valid_spec_kwargs(db_path, **overrides) -> dict:
    d = reg.register_definition(
        db_path, overrides.pop("definition_name", "test_feature"),
        "test rationale", "test role", "rohith", NOW,
    )
    defaults = dict(
        definition_name=d.name,
        definition_version=d.version,
        parameters={"window": 21},
        input_requirements=[{"source": "spot_close", "fields": ["close"]}],
        warmup_periods=21,
        conditioning_rule="preserving",
        binding_id="test.binding",
        engine_version="v1",
        validity_rule="valid once warmup satisfied",
        sensitivity_variants=[],
        actor="rohith",
        registered_at=NOW,
    )
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# Feature Definitions
# ---------------------------------------------------------------------------

class TestFeatureDefinitions:
    def test_first_registration_is_version_1(self, db):
        handle = reg.register_definition(db, "rv21", "rationale", "role", "rohith", NOW)
        assert handle == reg.FeatureDefinitionHandle(name="rv21", version=1)

    def test_second_registration_of_same_name_is_version_2(self, db):
        reg.register_definition(db, "rv21", "rationale v1", "role", "rohith", NOW)
        second = reg.register_definition(db, "rv21", "rationale v2", "role", "rohith", NOW)
        assert second.version == 2

    def test_definitions_for_different_names_are_independent(self, db):
        a = reg.register_definition(db, "feature_a", "r", "role", "rohith", NOW)
        b = reg.register_definition(db, "feature_b", "r", "role", "rohith", NOW)
        assert a.version == 1
        assert b.version == 1

    @pytest.mark.parametrize("field", ["name", "rationale", "intended_role", "actor"])
    def test_blank_required_field_is_refused(self, db, field):
        kwargs = dict(name="x", rationale="r", intended_role="role", actor="rohith")
        kwargs[field] = "   "
        with pytest.raises(reg.FeatureRegistryError):
            reg.register_definition(db, kwargs["name"], kwargs["rationale"],
                                     kwargs["intended_role"], kwargs["actor"], NOW)

    def test_definitions_are_never_updated_in_place(self, db):
        reg.register_definition(db, "rv21", "original", "role", "rohith", NOW)
        con = sqlite3.connect(db)
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            con.execute("UPDATE feature_definitions SET rationale = 'changed' WHERE name = 'rv21'")

    def test_definitions_are_never_deleted(self, db):
        reg.register_definition(db, "rv21", "r", "role", "rohith", NOW)
        con = sqlite3.connect(db)
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            con.execute("DELETE FROM feature_definitions WHERE name = 'rv21'")

    def test_get_definition_round_trips(self, db):
        reg.register_definition(db, "rv21", "the rationale", "the role", "rohith", NOW)
        d = reg.get_definition(db, "rv21", 1)
        assert d["rationale"] == "the rationale"
        assert d["intended_role"] == "the role"

    def test_get_definition_missing_returns_none(self, db):
        assert reg.get_definition(db, "does_not_exist", 1) is None


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
# register_specification() -- Tier A
# ---------------------------------------------------------------------------

class TestTierACompleteness:
    def test_valid_hand_written_specification_is_registered(self, db):
        kwargs = _valid_spec_kwargs(db)
        spec_hash = reg.register_specification(db, **kwargs)
        spec = reg.get_specification(db, spec_hash)
        assert spec["phase"] == "Registered"
        assert spec["rejection_reason"] is None

    def test_missing_input_requirements_is_rejected_and_persisted(self, db):
        kwargs = _valid_spec_kwargs(db, input_requirements=[])
        with pytest.raises(reg.SpecificationRejected) as exc_info:
            reg.register_specification(db, **kwargs)
        assert any("input_requirements" in e for e in exc_info.value.errors)
        # FG3/IQROS-P15: the rejection is a permanent record, not merely an exception.
        spec = reg.get_specification(db, exc_info.value.spec_hash)
        assert spec is not None
        assert spec["phase"] == "Rejected"
        assert "input_requirements" in spec["rejection_reason"]

    def test_malformed_input_requirement_entry_is_rejected(self, db):
        kwargs = _valid_spec_kwargs(db, input_requirements=[{"source": "x"}])  # missing 'fields'
        with pytest.raises(reg.SpecificationRejected) as exc_info:
            reg.register_specification(db, **kwargs)
        assert any("input_requirements[0]" in e for e in exc_info.value.errors)

    def test_blank_binding_id_is_rejected(self, db):
        kwargs = _valid_spec_kwargs(db, binding_id="")
        with pytest.raises(reg.SpecificationRejected) as exc_info:
            reg.register_specification(db, **kwargs)
        assert any("binding_id" in e for e in exc_info.value.errors)

    def test_negative_warmup_is_rejected(self, db):
        kwargs = _valid_spec_kwargs(db, warmup_periods=-1)
        with pytest.raises(reg.SpecificationRejected) as exc_info:
            reg.register_specification(db, **kwargs)
        assert any("warmup_periods" in e for e in exc_info.value.errors)

    def test_invalid_conditioning_rule_is_rejected(self, db):
        kwargs = _valid_spec_kwargs(db, conditioning_rule="not-a-real-rule")
        with pytest.raises(reg.SpecificationRejected) as exc_info:
            reg.register_specification(db, **kwargs)
        assert any("conditioning_rule" in e for e in exc_info.value.errors)

    def test_omitted_sensitivity_variants_is_rejected_but_empty_list_is_valid(self, db):
        # FG7: sensitivity variants must be DECLARED; an empty list is a
        # valid declaration ("none apply"), None is an omission.
        kwargs = _valid_spec_kwargs(db, sensitivity_variants=None)
        with pytest.raises(reg.SpecificationRejected) as exc_info:
            reg.register_specification(db, **kwargs)
        assert any("sensitivity_variants" in e for e in exc_info.value.errors)

        kwargs2 = _valid_spec_kwargs(db, sensitivity_variants=[])
        spec_hash = reg.register_specification(db, **kwargs2)
        assert reg.get_specification(db, spec_hash)["phase"] == "Registered"

    def test_unknown_definition_version_is_rejected(self, db):
        kwargs = _valid_spec_kwargs(db, definition_version=999)
        with pytest.raises(reg.SpecificationRejected) as exc_info:
            reg.register_specification(db, **kwargs)
        assert any("does not exist" in e for e in exc_info.value.errors)

    def test_multiple_simultaneous_failures_are_all_named(self, db):
        # AD4-style discipline: every unmet condition is named, not just the first.
        kwargs = _valid_spec_kwargs(db, input_requirements=[], binding_id="")
        with pytest.raises(reg.SpecificationRejected) as exc_info:
            reg.register_specification(db, **kwargs)
        assert len(exc_info.value.errors) >= 2

    def test_registering_identical_content_twice_is_idempotent(self, db):
        kwargs = _valid_spec_kwargs(db)
        first = reg.register_specification(db, **kwargs)
        second = reg.register_specification(db, **kwargs)
        assert first == second
        con = sqlite3.connect(db)
        count = con.execute(
            "SELECT COUNT(*) FROM feature_specifications WHERE spec_hash = ?", (first,)
        ).fetchone()[0]
        assert count == 1

    def test_different_parameters_produce_different_hashes(self, db):
        base = _valid_spec_kwargs(db)
        a = reg.register_specification(db, **{**base, "parameters": {"window": 21}})
        b = reg.register_specification(db, **{**base, "parameters": {"window": 42}})
        assert a != b


# ---------------------------------------------------------------------------
# activate_specification() -- Tier B
# ---------------------------------------------------------------------------

class TestTierBEquivalence:
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
        # This module does not itself enforce dataset citation (that is a
        # future Dataset Builder concern) -- this test documents the phase
        # value a future consumer would check, per ADR-I010 §11.
        spec_hash = reg.register_specification(db, **_valid_spec_kwargs(db))
        assert reg.get_specification(db, spec_hash)["phase"] != "Active"
        reg.activate_specification(db, spec_hash, lambda: True)
        assert reg.get_specification(db, spec_hash)["phase"] == "Active"


# ---------------------------------------------------------------------------
# Phase transitions -- allowed arcs only
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
