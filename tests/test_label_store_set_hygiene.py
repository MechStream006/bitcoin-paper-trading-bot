"""
Tests for research/label_store/set_hygiene.py (ADR-I011 §12 Tier D, §16.3,
DG2, PS3; Phase 3 Label Store roadmap v1.1.0, WBS 3.9 -- the tenth,
Phase-3-completing Label Store milestone).

Per WBS 3.9's own exit criterion: "A full-maturity request against a
partially-matured set is refused by name, observable via a query; all
four Tier D statistics computed and recorded; each caps admissibility
rather than blocking, feeding 3.7's meet."

FOUR TIERS:
  - Pure, synthetic-data unit tests for each of the four Tier D
    statistics and the partial-maturity refusal gate.
  - TestSetAdmissibilityCeiling: the PARTIAL-rung activation this
    milestone's own module docstring centres on.
  - Real-database integration, read-only, against the real local
    label_store.db (8 real Specifications, 148 real H6 values -- see
    WBS 3.4-3.8a's own records for provenance).
  - TestEndToEndLabelStoreIntegration: a full, real-shaped exercise of
    every completed Label Store milestone together (3.1-3.9), the
    "validate end-to-end dataset integration across the completed Label
    Store" activity this milestone's own instructions name explicitly.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_LABEL_STORE_DIR = _REPO_ROOT / "research" / "label_store"
_ADMISSIBILITY_DIR = _REPO_ROOT / "research" / "admissibility"


def _load(module_file: Path, registered_name: str):
    if registered_name in sys.modules:
        return sys.modules[registered_name]
    spec = importlib.util.spec_from_file_location(registered_name, module_file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[registered_name] = mod
    spec.loader.exec_module(mod)
    return mod


reg = _load(_LABEL_STORE_DIR / "registry.py", "label_store_registry")
vs = _load(_LABEL_STORE_DIR / "value_store.py", "label_store_value_store")
prop = _load(_LABEL_STORE_DIR / "propagation.py", "label_store_propagation")
lr = _load(_LABEL_STORE_DIR / "label_receipts.py", "label_store_label_receipts")
sh = _load(_LABEL_STORE_DIR / "set_hygiene.py", "label_store_set_hygiene")

sys.path.insert(0, str(_ADMISSIBILITY_DIR))
import lattice  # noqa: E402

NOW = "2026-08-06T00:00:00+00:00"
LATER = "2026-08-13T00:00:00+00:00"
REAL_LABEL_STORE_DB = _LABEL_STORE_DIR / "label_store.db"
REAL_SPOT_1D_HORIZON5 = "ff680d93f01d56297105235b1a4cfa11f2a94ecc989c902cfe2f1a88aef84612"
REAL_SPOT_1D_HORIZON1_UNPOPULATED = "e785df371f901da1e85c2130735d1e8508c38991a01bb2b758561e16ac9be1d0"
REAL_TRADE_FWD_DEFINITION = "trade_forward_return"
REAL_SPOT_DEFINITION = "spot_one_day_forward_return"
REAL_BINDING_ID = "forward_evidence.daily_job.close_trade"


@pytest.fixture
def db(tmp_path) -> Path:
    p = tmp_path / "label_store.db"
    lr.init_db(p)
    return p


def _register_spec(db, *, horizon_days=5, binding_id=REAL_BINDING_ID, definition_name="trade_forward_return"):
    d = reg.register_definition(db, definition_name, "test rationale", "test interpretation", "rohith", NOW)
    return reg.register_specification(
        db,
        definition_name=d.name, definition_version=d.version,
        horizon_days=horizon_days, horizon_governance="calendar_span",
        fill_convention="same-day spot close, both sides",
        cost_convention="flat 15bps, charged once at exit",
        direction_convention="long-only",
        maturity_rule="matured once horizon_days have elapsed on the venue calendar",
        validity_condition="valid once the subject's entry is recorded",
        conditioning_propagation="preserving",
        binding_id=binding_id, engine_version="v1",
        actor="rohith", registered_at=NOW,
    )


def _declare(db, spec_hash, subject_id, *, state="Declared", standing=None, outcome=None):
    vs.declare_forward_label(db, spec_hash, subject_id, NOW, declared_by="live-test-declaration", standing=standing)
    if state == "Declared":
        return
    vs.observe_forward_label(db, spec_hash, subject_id, NOW, observed_at=NOW)
    if state == "Pending":
        return
    if state == "Void":
        vs.void_forward_label(db, spec_hash, subject_id, NOW, void_at=NOW, void_reason="test void")
        return
    vs.mature_forward_label(
        db, spec_hash, subject_id, NOW,
        outcome=outcome if outcome is not None else {"forward_return": 0.05},
        input_revision_set=["placeholder"], matured_at=NOW,
    )
    if state == "Matured":
        return
    vs.flag_forward_label(db, spec_hash, subject_id, NOW, flagged_at=NOW, flag_reason="test flag")
    if state == "Flagged":
        return
    vs.adjudicate_forward_label(
        db, spec_hash, subject_id, NOW, adjudicated_at=LATER, adjudication_decision="test: admissible",
    )


# ---------------------------------------------------------------------------
# Tier D statistic 1: maturity_fraction
# ---------------------------------------------------------------------------

class TestMaturityFraction:
    def test_empty_set_has_none_fraction(self, db):
        spec_hash = _register_spec(db)
        stats = sh.maturity_fraction(db, spec_hash)
        assert stats["total"] == 0
        assert stats["fraction"] is None

    def test_all_declared_only_is_zero_fraction(self, db):
        spec_hash = _register_spec(db)
        _declare(db, spec_hash, "sig-1", state="Declared")
        _declare(db, spec_hash, "sig-2", state="Declared")
        stats = sh.maturity_fraction(db, spec_hash)
        assert stats["total"] == 2
        assert stats["matured_or_later"] == 0
        assert stats["fraction"] == 0.0

    def test_all_matured_is_full_fraction(self, db):
        spec_hash = _register_spec(db)
        _declare(db, spec_hash, "sig-1", state="Matured")
        _declare(db, spec_hash, "sig-2", state="Matured")
        stats = sh.maturity_fraction(db, spec_hash)
        assert stats["fraction"] == 1.0

    def test_mixed_states_partial_fraction(self, db):
        spec_hash = _register_spec(db)
        _declare(db, spec_hash, "sig-1", state="Matured")
        _declare(db, spec_hash, "sig-2", state="Pending")
        _declare(db, spec_hash, "sig-3", state="Declared")
        _declare(db, spec_hash, "sig-4", state="Matured")
        stats = sh.maturity_fraction(db, spec_hash)
        assert stats["total"] == 4
        assert stats["matured_or_later"] == 2
        assert stats["fraction"] == 0.5

    def test_flagged_and_adjudicated_count_as_matured_or_later(self, db):
        spec_hash = _register_spec(db)
        _declare(db, spec_hash, "sig-flagged", state="Flagged")
        _declare(db, spec_hash, "sig-adjudicated", state="Adjudicated")
        stats = sh.maturity_fraction(db, spec_hash)
        assert stats["matured_or_later"] == 2
        assert stats["fraction"] == 1.0

    def test_void_does_not_count_as_matured(self, db):
        spec_hash = _register_spec(db)
        _declare(db, spec_hash, "sig-void", state="Void")
        _declare(db, spec_hash, "sig-matured", state="Matured")
        stats = sh.maturity_fraction(db, spec_hash)
        assert stats["total"] == 2
        assert stats["matured_or_later"] == 1
        assert stats["fraction"] == 0.5
        assert stats["by_state"]["Void"] == 1

    def test_subject_ids_filter_narrows_the_set(self, db):
        spec_hash = _register_spec(db)
        _declare(db, spec_hash, "sig-1", state="Matured")
        _declare(db, spec_hash, "sig-2", state="Declared")
        stats = sh.maturity_fraction(db, spec_hash, subject_ids=["sig-1"])
        assert stats["total"] == 1
        assert stats["fraction"] == 1.0


# ---------------------------------------------------------------------------
# Tier D statistic 2: horizon_coverage
# ---------------------------------------------------------------------------

class TestHorizonCoverage:
    def test_no_specifications_for_definition(self, db):
        result = sh.horizon_coverage(db, "nonexistent_definition")
        assert result["total_horizons"] == 0
        assert result["coverage_fraction"] is None

    def test_single_unpopulated_specification(self, db):
        _register_spec(db, horizon_days=5)
        result = sh.horizon_coverage(db, "trade_forward_return")
        assert result["total_horizons"] == 1
        assert result["populated_horizons"] == 0
        assert result["coverage_fraction"] == 0.0

    def test_mix_of_populated_and_unpopulated(self, db):
        h5 = _register_spec(db, horizon_days=5, binding_id="b.h5")
        _register_spec(db, horizon_days=30, binding_id="b.h30")
        _declare(db, h5, "sig-1", state="Declared")
        result = sh.horizon_coverage(db, "trade_forward_return")
        assert result["total_horizons"] == 2
        assert result["populated_horizons"] == 1
        assert result["coverage_fraction"] == 0.5

    def test_per_horizon_sorted_by_horizon_days(self, db):
        _register_spec(db, horizon_days=30, binding_id="b.h30")
        _register_spec(db, horizon_days=5, binding_id="b.h5")
        _register_spec(db, horizon_days=14, binding_id="b.h14")
        result = sh.horizon_coverage(db, "trade_forward_return")
        assert [h["horizon_days"] for h in result["per_horizon"]] == [5, 14, 30]

    def test_real_spot_one_day_forward_return_coverage(self):
        if not REAL_LABEL_STORE_DB.exists():
            pytest.skip("real local label_store.db not present")
        result = sh.horizon_coverage(REAL_LABEL_STORE_DB, REAL_SPOT_DEFINITION)
        assert result["total_horizons"] == 5
        assert result["populated_horizons"] == 4
        assert result["coverage_fraction"] == 0.8

    def test_real_trade_forward_return_coverage_is_entirely_unpopulated(self):
        if not REAL_LABEL_STORE_DB.exists():
            pytest.skip("real local label_store.db not present")
        result = sh.horizon_coverage(REAL_LABEL_STORE_DB, REAL_TRADE_FWD_DEFINITION)
        assert result["total_horizons"] == 3
        assert result["populated_horizons"] == 0
        assert result["coverage_fraction"] == 0.0


# ---------------------------------------------------------------------------
# Tier D statistic 3: outcome_distribution
# ---------------------------------------------------------------------------

class TestOutcomeDistribution:
    def test_no_matured_rows_is_not_disclosed(self, db):
        spec_hash = _register_spec(db)
        _declare(db, spec_hash, "sig-1", state="Declared")
        result = sh.outcome_distribution(db, spec_hash)
        assert result["disclosed"] is False

    def test_auto_detects_common_numeric_field(self, db):
        spec_hash = _register_spec(db)
        _declare(db, spec_hash, "sig-1", state="Matured", outcome={"forward_return": 0.1})
        _declare(db, spec_hash, "sig-2", state="Matured", outcome={"forward_return": -0.2})
        result = sh.outcome_distribution(db, spec_hash)
        assert result["disclosed"] is True
        assert result["outcome_field"] == "forward_return"
        assert result["n"] == 2

    def test_explicit_outcome_field_override(self, db):
        spec_hash = _register_spec(db)
        _declare(db, spec_hash, "sig-1", state="Matured", outcome={"a": 1.0, "b": 2.0})
        result = sh.outcome_distribution(db, spec_hash, outcome_field="b")
        assert result["outcome_field"] == "b"
        assert result["mean"] == 2.0

    def test_class_imbalance_counts(self, db):
        spec_hash = _register_spec(db)
        _declare(db, spec_hash, "sig-pos-1", state="Matured", outcome={"forward_return": 0.1})
        _declare(db, spec_hash, "sig-pos-2", state="Matured", outcome={"forward_return": 0.2})
        _declare(db, spec_hash, "sig-pos-3", state="Matured", outcome={"forward_return": 0.3})
        _declare(db, spec_hash, "sig-neg", state="Matured", outcome={"forward_return": -0.5})
        _declare(db, spec_hash, "sig-zero", state="Matured", outcome={"forward_return": 0.0})
        result = sh.outcome_distribution(db, spec_hash)
        assert result["class_imbalance"]["positive"] == 3
        assert result["class_imbalance"]["negative"] == 1
        assert result["class_imbalance"]["zero"] == 1
        assert result["class_imbalance"]["positive_fraction"] == 0.6

    def test_mean_and_bounds_are_correct(self, db):
        spec_hash = _register_spec(db)
        _declare(db, spec_hash, "sig-1", state="Matured", outcome={"forward_return": 0.1})
        _declare(db, spec_hash, "sig-2", state="Matured", outcome={"forward_return": 0.3})
        result = sh.outcome_distribution(db, spec_hash)
        assert result["mean"] == pytest.approx(0.2)
        assert result["min"] == pytest.approx(0.1)
        assert result["max"] == pytest.approx(0.3)

    def test_no_common_numeric_field_across_differing_shapes(self, db):
        spec_hash = _register_spec(db)
        _declare(db, spec_hash, "sig-1", state="Matured", outcome={"a": 1.0})
        _declare(db, spec_hash, "sig-2", state="Matured", outcome={"b": 2.0})
        result = sh.outcome_distribution(db, spec_hash)
        assert result["disclosed"] is False

    def test_real_outcome_distribution_auto_detects_forward_return(self):
        if not REAL_LABEL_STORE_DB.exists():
            pytest.skip("real local label_store.db not present")
        result = sh.outcome_distribution(REAL_LABEL_STORE_DB, REAL_SPOT_1D_HORIZON5)
        assert result["disclosed"] is True
        assert result["outcome_field"] == "forward_return"
        assert result["n"] == 37


# ---------------------------------------------------------------------------
# Tier D statistic 4: standing_composition
# ---------------------------------------------------------------------------

class TestStandingComposition:
    def test_all_context(self, db):
        spec_hash = _register_spec(db)
        _declare(db, spec_hash, "sig-1", state="Declared", standing="Context")
        _declare(db, spec_hash, "sig-2", state="Declared", standing="Context")
        result = sh.standing_composition(db, spec_hash)
        assert result["by_standing"] == {"Context": 2}

    def test_unassigned_bucket_for_null_standing(self, db):
        spec_hash = _register_spec(db)
        _declare(db, spec_hash, "sig-1", state="Declared", standing=None)
        result = sh.standing_composition(db, spec_hash)
        assert result["by_standing"] == {sh.UNASSIGNED_STANDING: 1}

    def test_mixed_standings_with_fractions(self, db):
        spec_hash = _register_spec(db)
        _declare(db, spec_hash, "sig-1", standing="Context")
        _declare(db, spec_hash, "sig-2", standing="Forward evidence")
        _declare(db, spec_hash, "sig-3", standing="Forward evidence")
        _declare(db, spec_hash, "sig-4", standing=None)
        result = sh.standing_composition(db, spec_hash)
        assert result["by_standing"] == {"Context": 1, "Forward evidence": 2, sh.UNASSIGNED_STANDING: 1}
        assert result["fractions"]["Forward evidence"] == 0.5

    def test_real_standing_composition_is_entirely_context(self):
        if not REAL_LABEL_STORE_DB.exists():
            pytest.skip("real local label_store.db not present")
        result = sh.standing_composition(REAL_LABEL_STORE_DB, REAL_SPOT_1D_HORIZON5)
        assert result["by_standing"] == {"Context": 37}


# ---------------------------------------------------------------------------
# tier_d_report -- aggregate
# ---------------------------------------------------------------------------

class TestTierDReport:
    def test_aggregates_all_four_statistics(self, db):
        spec_hash = _register_spec(db)
        _declare(db, spec_hash, "sig-1", state="Matured", standing="Context")
        report = sh.tier_d_report(db, spec_hash)
        assert set(report) == {"spec_hash", "maturity_fraction", "horizon_coverage", "outcome_distribution", "standing_composition"}
        assert report["maturity_fraction"]["fraction"] == 1.0

    def test_unregistered_spec_hash_raises_lookup_error(self, db):
        with pytest.raises(LookupError):
            sh.tier_d_report(db, "deadbeef" * 8)


# ---------------------------------------------------------------------------
# §16.3 partial-maturity refusal
# ---------------------------------------------------------------------------

class TestRequestFullyMatured:
    def test_full_admission_when_100_percent_matured(self, db):
        spec_hash = _register_spec(db)
        _declare(db, spec_hash, "sig-1", state="Matured")
        _declare(db, spec_hash, "sig-2", state="Matured")
        result = sh.request_fully_matured(db, spec_hash)
        assert len(result["rows"]) == 2
        assert result["admitted_partial"] is False
        assert result["maturity_fraction"] == 1.0

    def test_refuses_by_name_when_partial_and_not_admitted(self, db):
        spec_hash = _register_spec(db)
        _declare(db, spec_hash, "sig-matured", state="Matured")
        _declare(db, spec_hash, "sig-pending", state="Pending")
        with pytest.raises(sh.PartialMaturityRefusalError) as exc_info:
            sh.request_fully_matured(db, spec_hash)
        message = str(exc_info.value)
        assert spec_hash[:12] in message
        assert "sig-pending" in message
        assert "1/2" in message

    def test_admits_partial_when_requested(self, db):
        spec_hash = _register_spec(db)
        _declare(db, spec_hash, "sig-matured", state="Matured")
        _declare(db, spec_hash, "sig-pending", state="Pending")
        result = sh.request_fully_matured(db, spec_hash, admit_partial=True)
        assert len(result["rows"]) == 1
        assert result["rows"][0]["subject_id"] == "sig-matured"
        assert result["admitted_partial"] is True
        assert result["maturity_fraction"] == 0.5

    def test_empty_set_always_refuses_even_with_admit_partial(self, db):
        spec_hash = _register_spec(db)
        with pytest.raises(sh.PartialMaturityRefusalError):
            sh.request_fully_matured(db, spec_hash, admit_partial=True)

    def test_real_fully_matured_horizon_succeeds(self):
        if not REAL_LABEL_STORE_DB.exists():
            pytest.skip("real local label_store.db not present")
        result = sh.request_fully_matured(REAL_LABEL_STORE_DB, REAL_SPOT_1D_HORIZON5)
        assert len(result["rows"]) == 37
        assert result["admitted_partial"] is False

    def test_real_unpopulated_horizon_refuses(self):
        if not REAL_LABEL_STORE_DB.exists():
            pytest.skip("real local label_store.db not present")
        with pytest.raises(sh.PartialMaturityRefusalError):
            sh.request_fully_matured(REAL_LABEL_STORE_DB, REAL_SPOT_1D_HORIZON1_UNPOPULATED)


# ---------------------------------------------------------------------------
# Set-level admissibility ceiling -- activates lattice.Maturity.PARTIAL
# ---------------------------------------------------------------------------

class TestSetAdmissibilityCeiling:
    def test_empty_set_yields_bottom(self, db):
        spec_hash = _register_spec(db)
        result = sh.set_admissibility_ceiling(db, spec_hash)
        assert result == lattice.bottom()

    def test_fully_matured_set_yields_full_maturity(self, db):
        spec_hash = _register_spec(db)
        _declare(db, spec_hash, "sig-1", state="Matured")
        _declare(db, spec_hash, "sig-2", state="Matured")
        result = sh.set_admissibility_ceiling(db, spec_hash)
        assert result.maturity == lattice.Maturity.FULL

    def test_declared_only_set_yields_declared_maturity(self, db):
        spec_hash = _register_spec(db)
        _declare(db, spec_hash, "sig-1", state="Declared")
        result = sh.set_admissibility_ceiling(db, spec_hash)
        assert result.maturity == lattice.Maturity.DECLARED

    def test_partially_matured_set_yields_partial_maturity_not_declared(self, db):
        """The central claim of this module: a naive per-value meet()
        would collapse this set to DECLARED (the minimum of one Declared
        row and one Matured row); set_admissibility_ceiling() must
        instead activate the previously-unused PARTIAL rung."""
        spec_hash = _register_spec(db)
        _declare(db, spec_hash, "sig-matured", state="Matured")
        _declare(db, spec_hash, "sig-declared", state="Declared")
        result = sh.set_admissibility_ceiling(db, spec_hash)
        assert result.maturity == lattice.Maturity.PARTIAL
        assert result.maturity != lattice.Maturity.DECLARED

    def test_high_fraction_partial_set_is_still_partial_not_full(self, db):
        spec_hash = _register_spec(db)
        for i in range(9):
            _declare(db, spec_hash, f"sig-matured-{i}", state="Matured")
        _declare(db, spec_hash, "sig-declared", state="Declared")
        result = sh.set_admissibility_ceiling(db, spec_hash)
        assert result.maturity == lattice.Maturity.PARTIAL

    def test_flagged_row_drags_integrity_down_even_at_full_maturity(self, db):
        spec_hash = _register_spec(db)
        _declare(db, spec_hash, "sig-clean", state="Matured")
        _declare(db, spec_hash, "sig-flagged", state="Flagged")
        result = sh.set_admissibility_ceiling(db, spec_hash)
        assert result.maturity == lattice.Maturity.FULL  # both are matured-or-later
        assert result.integrity == lattice.Integrity.FLAGGED  # weakest-link meet still applies

    def test_real_fully_matured_horizon_ceiling(self):
        if not REAL_LABEL_STORE_DB.exists():
            pytest.skip("real local label_store.db not present")
        result = sh.set_admissibility_ceiling(REAL_LABEL_STORE_DB, REAL_SPOT_1D_HORIZON5)
        assert result.maturity == lattice.Maturity.FULL
        assert result.certification == lattice.Certification.UNKNOWN  # honest, uncertified (module 3.7's own claim)
        assert result.integrity == lattice.Integrity.CLEAN  # 0 real flags, confirmed at WBS 3.6/3.7


# ---------------------------------------------------------------------------
# Structural guards
# ---------------------------------------------------------------------------

class TestNoRegistryOrValueStoreMutation:
    def test_module_issues_no_write_statement(self):
        source = (_LABEL_STORE_DIR / "set_hygiene.py").read_text(encoding="utf-8")
        upper = source.upper()
        assert "INSERT INTO" not in upper
        assert "UPDATE " not in upper
        assert "DELETE FROM" not in upper


class TestNoDatetimeNow:
    def test_no_datetime_now_anywhere_in_set_hygiene_py(self):
        source = (_LABEL_STORE_DIR / "set_hygiene.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "now":
                pytest.fail(f"found a '.now' attribute access at line {node.lineno}")


# ---------------------------------------------------------------------------
# End-to-end integration -- the completed Label Store, exercised together
# ---------------------------------------------------------------------------

class TestEndToEndLabelStoreIntegration:
    """
    Exercises WBS 3.1 through 3.9 together against one realistic,
    real-shaped scenario: registration (with a receipt), multi-subject
    declaration with standing, maturation, flagging, adjudication (with a
    receipt), conditioning propagation, per-value and set-level
    admissibility, all four Tier D statistics, partial-maturity refusal,
    and governance-receipt chain integrity -- the "validate end-to-end
    dataset integration across the completed Label Store" activity this
    milestone's own instructions name explicitly.
    """

    def test_full_pipeline(self, db):
        # 3.8a -- registration, receipted.
        registration_receipt = lr.register_specification_with_receipt(
            db,
            definition_name=reg.register_definition(
                db, "e2e_forward_return", "test rationale", "test interpretation", "rohith", NOW,
            ).name,
            definition_version=1,
            horizon_days=5, horizon_governance="calendar_span",
            fill_convention="same-day spot close, both sides",
            cost_convention="flat 15bps, charged once at exit",
            direction_convention="long-only",
            maturity_rule="matured once horizon_days have elapsed",
            validity_condition="valid once entry recorded",
            conditioning_propagation="preserving",
            binding_id=REAL_BINDING_ID, engine_version="v1",
            actor="rohith", registered_at=NOW,
        )
        assert registration_receipt["decision_type"] == "label.register"
        spec_hash = registration_receipt["subject_id"]
        spec = reg.get_specification(db, spec_hash)
        assert spec["phase"] == "Registered"

        # 3.2/3.5 -- declare five subjects with standing, mixed maturity.
        for i in range(3):
            _declare(db, spec_hash, f"e2e-matured-{i}", state="Matured", standing="Context",
                      outcome={"forward_return": 0.01 * (i + 1)})
        _declare(db, spec_hash, "e2e-pending", state="Pending", standing="Context")
        _declare(db, spec_hash, "e2e-to-flag", state="Matured", standing="Context", outcome={"forward_return": -0.02})

        # 3.6 -- flag one Matured label (simulating a superseded input, without
        # needing raw_lake.py for real -- this milestone reuses value_store's
        # own already-tested flag_forward_label() directly, exactly as
        # superseded.py itself does).
        vs.flag_forward_label(db, spec_hash, "e2e-to-flag", NOW, flagged_at=NOW, flag_reason="e2e simulated correction")

        # 3.8a -- adjudicate it, receipted.
        adjudication_receipt = lr.adjudicate_forward_label_with_receipt(
            db, spec_hash, "e2e-to-flag", NOW,
            adjudicated_at=LATER, adjudication_decision="e2e: reviewed, admissible",
            actor="rohith",
        )
        assert adjudication_receipt["decision_type"] == "label.adjudicate"

        # 3.7 -- conditioning + per-value admissibility for one matured subject.
        ceiling = prop.label_value_ceiling(db, spec_hash, "e2e-matured-0", NOW)
        assert ceiling.maturity == lattice.Maturity.FULL
        assert ceiling.certification == lattice.Certification.UNKNOWN  # honest, no real gauntlet

        # 3.9 -- all four Tier D statistics.
        report = sh.tier_d_report(db, spec_hash)
        assert report["maturity_fraction"]["total"] == 5
        assert report["maturity_fraction"]["matured_or_later"] == 4  # 3 matured + 1 flagged-then-adjudicated
        assert report["horizon_coverage"]["populated_horizons"] == 1
        assert report["outcome_distribution"]["disclosed"] is True
        assert report["standing_composition"]["by_standing"] == {"Context": 5}

        # 3.9 -- set-level ceiling activates PARTIAL (4/5 matured-or-later).
        set_ceiling = sh.set_admissibility_ceiling(db, spec_hash)
        assert set_ceiling.maturity == lattice.Maturity.PARTIAL
        assert set_ceiling.integrity == lattice.Integrity.ADJUDICATED  # weakest-link: the adjudicated row

        # 3.9 -- §16.3 partial-maturity refusal, then explicit admission.
        with pytest.raises(sh.PartialMaturityRefusalError):
            sh.request_fully_matured(db, spec_hash)
        partial = sh.request_fully_matured(db, spec_hash, admit_partial=True)
        assert len(partial["rows"]) == 4
        assert partial["admitted_partial"] is True

        # 3.8a -- governance receipts: chained, replayable, self-validating.
        chain = lr._import_receipt_infra()[1].verify_chain(db)
        assert chain.ok is True
        assert chain.n_receipts == 2
        assert lr.validate_label_governance(db) == []

        replay = lr.replay_registration_receipt(db, spec_hash)
        assert replay.replay.mode == "verified"
        replay_adj = lr.replay_adjudication_receipt(db, adjudication_receipt["receipt_id"])
        assert replay_adj.replay.mode == "envelope-verified"

    def test_real_data_end_to_end_snapshot(self, tmp_path):
        """The same pipeline against the real local label_store.db's own
        data -- every completed milestone's real, already-committed
        output, composed together one more time. Tier D statistics and
        the set ceiling are read directly against the real file (pure
        SELECT queries, no table ever created there by set_hygiene.py --
        see TestNoRegistryOrValueStoreMutation). The governance-receipt
        check runs against a disposable COPY instead: the real
        label_store.db has never had governance_receipts created on it
        (unlike feature_store.db post-WBS 3.8b) -- WBS 3.8a's own real-
        data tests only ever exercised a copy (label_receipts.py's own
        init_db() would otherwise create that table on the real file, an
        unintended write of exactly the kind found and corrected at
        WBS 3.8b)."""
        if not REAL_LABEL_STORE_DB.exists():
            pytest.skip("real local label_store.db not present")

        tier_d = sh.tier_d_report(REAL_LABEL_STORE_DB, REAL_SPOT_1D_HORIZON5)
        assert tier_d["maturity_fraction"]["fraction"] == 1.0
        assert tier_d["horizon_coverage"]["definition_name"] == REAL_SPOT_DEFINITION
        assert tier_d["outcome_distribution"]["n"] == 37
        assert tier_d["standing_composition"]["by_standing"] == {"Context": 37}

        set_ceiling = sh.set_admissibility_ceiling(REAL_LABEL_STORE_DB, REAL_SPOT_1D_HORIZON5)
        assert set_ceiling.maturity == lattice.Maturity.FULL

        copy_path = tmp_path / "label_store_copy.db"
        shutil.copy2(REAL_LABEL_STORE_DB, copy_path)
        lr.init_db(copy_path)  # additive; only ever touches the disposable copy
        governance_errors = lr.validate_label_governance(copy_path)
        lrec1_errors = [e for e in governance_errors if e.startswith("LREC1")]
        assert lrec1_errors == []  # WBS 3.8a's disclosed gap, closed by WBS 3.8c (`LS-GAP-001`)

        import hashlib
        real_hash_before = hashlib.sha256(REAL_LABEL_STORE_DB.read_bytes()).hexdigest()
        sh.tier_d_report(REAL_LABEL_STORE_DB, REAL_SPOT_1D_HORIZON5)
        real_hash_after = hashlib.sha256(REAL_LABEL_STORE_DB.read_bytes()).hexdigest()
        assert real_hash_before == real_hash_after
