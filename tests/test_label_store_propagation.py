"""
Tests for research/label_store/propagation.py (ADR-I011 §10.2, DG6; Phase 3
Label Store roadmap v1.1.0, WBS 3.7 -- the seventh Label Store milestone).

THREE TIERS, mirroring tests/test_conditioning.py's and
tests/test_label_store_superseded.py's own established split:

  - Pure, base-env-portable (no gating): TestSourceCeiling,
    TestLabelValueOwnCeiling, TestLabelValueCeiling,
    TestFeatureSpecificationCeiling, TestAllActiveFeatureSpecification
    Ceilings, TestUnknownBindingSource, TestConditioningPyUnchanged,
    TestNoDatetimeNow. propagation.py's own ceiling-computation functions
    (source_ceiling/label_value_own_ceiling/label_value_ceiling/
    feature_specification_ceiling) never call conditioning.py at all --
    they only need lattice.py/composition.py, both pure -- so these run
    in the base env with no gating needed, exactly like
    test_conditioning.py's own pure-logic tier.
  - Pandas-gated (class-level skipif): TestConditioningForLabel
    Specification, TestConditioningForFeatureSpecification -- these call
    conditioning_for_specification() with no injected source_map, which
    transitively imports the real, pandas-dependent ingest writers via
    conditioning.py's own _source_conditioning_map().
  - Real-database integration: TestRealFeatureStoreIntegration, run
    against the real local feature_store.db (17 Active Feature
    Specifications) -- skipped if that file is absent (gitignored,
    absent in CI), mirroring test_migration_harness.py's own
    real-local-artifact skip discipline.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_LABEL_STORE_DIR = _REPO_ROOT / "research" / "label_store"
_FEATURE_STORE_DIR = _REPO_ROOT / "research" / "feature_store"

_vs_spec = importlib.util.spec_from_file_location("label_store_value_store", _LABEL_STORE_DIR / "value_store.py")
vs = importlib.util.module_from_spec(_vs_spec)
sys.modules["label_store_value_store"] = vs
_vs_spec.loader.exec_module(vs)

_reg_spec = importlib.util.spec_from_file_location("label_store_registry", _LABEL_STORE_DIR / "registry.py")
reg = importlib.util.module_from_spec(_reg_spec)
sys.modules["label_store_registry"] = reg
_reg_spec.loader.exec_module(reg)

_freg_spec = importlib.util.spec_from_file_location("feature_store_registry", _FEATURE_STORE_DIR / "registry.py")
freg = importlib.util.module_from_spec(_freg_spec)
sys.modules["feature_store_registry"] = freg
_freg_spec.loader.exec_module(freg)

_prop_spec = importlib.util.spec_from_file_location("label_store_propagation", _LABEL_STORE_DIR / "propagation.py")
prop = importlib.util.module_from_spec(_prop_spec)
sys.modules["label_store_propagation"] = prop
_prop_spec.loader.exec_module(prop)

sys.path.insert(0, str(_REPO_ROOT / "research" / "admissibility"))
import lattice  # noqa: E402
import composition  # noqa: E402

_PANDAS_AVAILABLE = importlib.util.find_spec("pandas") is not None

SPEC_HASH = "cafebabe" * 8
NOW = "2026-08-06T00:00:00+00:00"
BOOTSTRAP_DECLARED_BY = "migrate_h6_forward_labels.py (bootstrap, migrated 2026-08-06)"
LIVE_DECLARED_BY = "live-test-declaration"
REAL_BINDING_ID = "forward_evidence.daily_job.close_trade"  # WBS 3.3's real C1 binding


@pytest.fixture
def db(tmp_path) -> Path:
    p = tmp_path / "label_store.db"
    reg.init_db(p)
    vs.init_db(p)
    return p


def _register_real_spec(db, *, conditioning_propagation="preserving", binding_id=REAL_BINDING_ID):
    d = reg.register_definition(db, "trade_forward_return", "test rationale", "test interpretation", "rohith", NOW)
    spec_hash = reg.register_specification(
        db,
        definition_name=d.name, definition_version=d.version,
        horizon_days=1, horizon_governance="calendar_span",
        fill_convention="same-day spot close, both sides",
        cost_convention="flat 15bps, charged once at exit",
        direction_convention="long-only",
        maturity_rule="matured once horizon_days have elapsed on the venue calendar",
        validity_condition="valid once the subject's entry is recorded",
        conditioning_propagation=conditioning_propagation,
        binding_id=binding_id, engine_version="v1",
        actor="rohith", registered_at=NOW,
    )
    return {"spec_hash": spec_hash}


def _declare(db, spec_hash, subject_id, *, maturity_state="Declared", declared_by=LIVE_DECLARED_BY):
    vs.declare_forward_label(db, spec_hash, subject_id, NOW, declared_by=declared_by)
    if maturity_state == "Declared":
        return
    vs.observe_forward_label(db, spec_hash, subject_id, NOW, observed_at=NOW)
    if maturity_state == "Pending":
        return
    if maturity_state == "Void":
        vs.void_forward_label(db, spec_hash, subject_id, NOW, void_at=NOW, void_reason="test void")
        return
    vs.mature_forward_label(
        db, spec_hash, subject_id, NOW,
        outcome={"forward_return": 0.05}, input_revision_set=["placeholder"], matured_at=NOW,
    )
    if maturity_state == "Matured":
        return
    vs.flag_forward_label(db, spec_hash, subject_id, NOW, flagged_at=NOW, flag_reason="test flag")
    if maturity_state == "Flagged":
        return
    vs.adjudicate_forward_label(
        db, spec_hash, subject_id, NOW,
        adjudicated_at=NOW, adjudication_decision="test adjudication: admissible",
    )


# ---------------------------------------------------------------------------
# source_ceiling -- honest, uncertified ceilings
# ---------------------------------------------------------------------------

class TestSourceCeiling:
    def test_certification_and_readiness_are_unknown(self):
        c = prop.source_ceiling("spot_1m")
        assert c.certification == lattice.Certification.UNKNOWN
        assert c.readiness == lattice.Readiness.UNKNOWN

    def test_never_fabricates_a_passing_verdict(self):
        """The exact failure mode this milestone's design explicitly
        avoided: certification_from_gauntlet({}) would silently return
        GREEN. source_ceiling() must never reach that."""
        c = prop.source_ceiling("spot_1m")
        assert c.certification != lattice.Certification.GREEN
        assert c.readiness != lattice.Readiness.READY

    def test_maturity_is_not_applicable_at_top_rung(self):
        c = prop.source_ceiling("spot_1m")
        assert c.is_not_applicable(lattice.Dimension.MATURITY)
        assert c.maturity == lattice.Maturity.FULL

    def test_integrity_is_clean(self):
        c = prop.source_ceiling("spot_1m")
        assert c.integrity == lattice.Integrity.CLEAN

    def test_provenance_is_offline_corpus(self):
        c = prop.source_ceiling("spot_1m")
        assert c.provenance == lattice.Provenance.OFFLINE_CORPUS

    def test_source_name_does_not_affect_shape(self):
        """source_ceiling() makes no real per-source distinction today --
        no gauntlet report is read for any source (module docstring) --
        so any source name produces the same honest ceiling shape."""
        assert prop.source_ceiling("spot_1m") == prop.source_ceiling("perp_1m")


# ---------------------------------------------------------------------------
# label_value_own_ceiling -- maturity_state -> (Maturity, Integrity)
# ---------------------------------------------------------------------------

class TestLabelValueOwnCeiling:
    @pytest.mark.parametrize("state,maturity,integrity", [
        ("Declared", lattice.Maturity.DECLARED, lattice.Integrity.CLEAN),
        ("Pending", lattice.Maturity.DECLARED, lattice.Integrity.CLEAN),
        ("Matured", lattice.Maturity.FULL, lattice.Integrity.CLEAN),
        ("Flagged", lattice.Maturity.FULL, lattice.Integrity.FLAGGED),
        ("Adjudicated", lattice.Maturity.FULL, lattice.Integrity.ADJUDICATED),
        ("Void", lattice.Maturity.UNKNOWN, lattice.Integrity.UNKNOWN),
    ])
    def test_maturity_state_mapping(self, state, maturity, integrity):
        c = prop.label_value_own_ceiling(state, LIVE_DECLARED_BY)
        assert c.maturity == maturity
        assert c.integrity == integrity

    def test_adjudicated_is_never_restored_to_clean(self):
        """lattice.py's own documented Choice 2: a flag is neither
        silently rewritten nor silently trusted."""
        c = prop.label_value_own_ceiling("Adjudicated", LIVE_DECLARED_BY)
        assert c.integrity != lattice.Integrity.CLEAN

    def test_void_is_the_most_conservative_reading(self):
        c = prop.label_value_own_ceiling("Void", LIVE_DECLARED_BY)
        assert c.maturity == lattice.Maturity.UNKNOWN
        assert not c.is_not_applicable(lattice.Dimension.MATURITY)

    def test_bootstrap_declared_by_yields_offline_corpus_provenance(self):
        c = prop.label_value_own_ceiling("Matured", BOOTSTRAP_DECLARED_BY)
        assert c.provenance == lattice.Provenance.OFFLINE_CORPUS

    def test_non_bootstrap_declared_by_yields_live_forward_provenance(self):
        c = prop.label_value_own_ceiling("Matured", LIVE_DECLARED_BY)
        assert c.provenance == lattice.Provenance.LIVE_FORWARD

    def test_unrecognised_state_raises(self):
        with pytest.raises(ValueError):
            prop.label_value_own_ceiling("NotAState", LIVE_DECLARED_BY)

    def test_own_ceiling_never_constrains_certification_or_readiness(self):
        """Own ceiling has no opinion on data quality -- both dimensions
        are set to their top rung so meet() lets the real input-source
        ceiling determine them (module docstring)."""
        c = prop.label_value_own_ceiling("Matured", LIVE_DECLARED_BY)
        assert c.certification == lattice.Certification.GREEN
        assert c.readiness == lattice.Readiness.READY


# ---------------------------------------------------------------------------
# label_value_ceiling -- full meet, real Label Store lifecycle functions
# ---------------------------------------------------------------------------

class TestLabelValueCeiling:
    def test_full_flow(self, db):
        spec = _register_real_spec(db)
        _declare(db, spec["spec_hash"], "sig-1", maturity_state="Matured")
        c = prop.label_value_ceiling(db, spec["spec_hash"], "sig-1", NOW)
        # own ceiling contributes Maturity.FULL/Integrity.CLEAN; source
        # ceiling contributes Certification.UNKNOWN/Readiness.UNKNOWN --
        # meet() takes the componentwise minimum of the two.
        assert c.maturity == lattice.Maturity.FULL
        assert c.integrity == lattice.Integrity.CLEAN
        assert c.certification == lattice.Certification.UNKNOWN
        assert c.readiness == lattice.Readiness.UNKNOWN

    def test_declared_only_label_has_declared_maturity(self, db):
        spec = _register_real_spec(db)
        _declare(db, spec["spec_hash"], "sig-declared", maturity_state="Declared")
        c = prop.label_value_ceiling(db, spec["spec_hash"], "sig-declared", NOW)
        assert c.maturity == lattice.Maturity.DECLARED

    def test_flagged_label_ceiling_shows_flagged_integrity(self, db):
        spec = _register_real_spec(db)
        _declare(db, spec["spec_hash"], "sig-flagged", maturity_state="Flagged")
        c = prop.label_value_ceiling(db, spec["spec_hash"], "sig-flagged", NOW)
        assert c.integrity == lattice.Integrity.FLAGGED

    def test_adjudicated_label_ceiling_never_shows_clean(self, db):
        spec = _register_real_spec(db)
        _declare(db, spec["spec_hash"], "sig-adj", maturity_state="Adjudicated")
        c = prop.label_value_ceiling(db, spec["spec_hash"], "sig-adj", NOW)
        assert c.integrity == lattice.Integrity.ADJUDICATED
        assert c.integrity != lattice.Integrity.CLEAN

    def test_unknown_binding_id_raises(self, db):
        spec = _register_real_spec(db, binding_id="nonexistent.binding.id")
        _declare(db, spec["spec_hash"], "sig-x", maturity_state="Matured")
        with pytest.raises(prop.UnknownBindingSourceError):
            prop.label_value_ceiling(db, spec["spec_hash"], "sig-x", NOW)

    def test_missing_label_raises_lookup_error(self, db):
        spec = _register_real_spec(db)
        with pytest.raises(LookupError):
            prop.label_value_ceiling(db, spec["spec_hash"], "no-such-subject", NOW)

    def test_missing_specification_raises_lookup_error(self, db):
        vs.declare_forward_label(db, SPEC_HASH, "sig-orphan", NOW, declared_by=LIVE_DECLARED_BY)
        with pytest.raises(LookupError):
            prop.label_value_ceiling(db, SPEC_HASH, "sig-orphan", NOW)

    def test_result_is_meet_not_own_ceiling_alone(self, db):
        """Confirms meet() is actually being applied -- a naive
        implementation that just returned the own ceiling would wrongly
        report GREEN/READY certification."""
        spec = _register_real_spec(db)
        _declare(db, spec["spec_hash"], "sig-meet", maturity_state="Matured")
        own = prop.label_value_own_ceiling("Matured", LIVE_DECLARED_BY)
        result = prop.label_value_ceiling(db, spec["spec_hash"], "sig-meet", NOW)
        assert result != own
        assert result.certification < own.certification


# ---------------------------------------------------------------------------
# feature_specification_ceiling
# ---------------------------------------------------------------------------

class TestFeatureSpecificationCeiling:
    def _spec(self, **overrides):
        base = {
            "input_requirements": [{"source": "spot_1m", "fields": ["close"]}],
        }
        base.update(overrides)
        return base

    def test_maturity_is_not_applicable(self):
        c = prop.feature_specification_ceiling(self._spec())
        assert c.is_not_applicable(lattice.Dimension.MATURITY)
        assert c.maturity == lattice.Maturity.FULL

    def test_inherits_uncertified_cap_from_input_source(self):
        c = prop.feature_specification_ceiling(self._spec())
        assert c.certification == lattice.Certification.UNKNOWN
        assert c.readiness == lattice.Readiness.UNKNOWN

    def test_integrity_is_clean(self):
        c = prop.feature_specification_ceiling(self._spec())
        assert c.integrity == lattice.Integrity.CLEAN

    def test_multi_source_specification_still_meets_correctly(self):
        c = prop.feature_specification_ceiling(self._spec(
            input_requirements=[
                {"source": "spot_1m", "fields": ["close"]},
                {"source": "perp_1m", "fields": ["close"]},
            ],
        ))
        assert c.certification == lattice.Certification.UNKNOWN


# ---------------------------------------------------------------------------
# UnknownBindingSourceError -- both adapter functions refuse, never guess
# ---------------------------------------------------------------------------

class TestUnknownBindingSource:
    def test_conditioning_adapter_raises_for_unregistered_binding(self):
        spec = {"binding_id": "nonexistent.binding.id", "conditioning_propagation": "preserving"}
        with pytest.raises(prop.UnknownBindingSourceError):
            prop.conditioning_for_label_specification(spec)

    def test_error_names_known_bindings(self):
        spec = {"binding_id": "nonexistent.binding.id", "conditioning_propagation": "preserving"}
        with pytest.raises(prop.UnknownBindingSourceError, match="forward_evidence.daily_job.close_trade"):
            prop.conditioning_for_label_specification(spec)


# ---------------------------------------------------------------------------
# conditioning.py imported unchanged -- the roadmap's own exit criterion
# ---------------------------------------------------------------------------

class TestConditioningPyUnchanged:
    def test_propagation_never_defines_its_own_conditioning_logic(self):
        """Structural guard: propagation.py must call into conditioning.py
        rather than reimplementing preserving/narrowing/cross-sectional
        logic itself."""
        source = (_LABEL_STORE_DIR / "propagation.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        calls_conditioning_for_specification = any(
            isinstance(node, ast.Attribute) and node.attr == "conditioning_for_specification"
            for node in ast.walk(tree)
        )
        assert calls_conditioning_for_specification
        assert "RULES = frozenset" not in source
        assert '"preserving"' not in source or "conditioning_propagation" in source

    def test_never_calls_conditioning_private_source_map(self):
        """propagation.py's own docstring names _source_conditioning_map()
        in prose (explaining what it must NOT reach into) -- this checks
        the AST for an actual call/reference, not a substring match, so
        the docstring itself does not trip the guard."""
        source = (_LABEL_STORE_DIR / "propagation.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "_source_conditioning_map":
                pytest.fail(f"propagation.py must never call _source_conditioning_map (line {node.lineno})")
            if isinstance(node, ast.Name) and node.id == "_source_conditioning_map":
                pytest.fail(f"propagation.py must never reference _source_conditioning_map (line {node.lineno})")


# ---------------------------------------------------------------------------
# conditioning_for_label_specification / conditioning_for_feature_specification
# -- pandas-gated (real ingest writers)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _PANDAS_AVAILABLE, reason="conditioning.py's real source map needs pandas transitively")
class TestConditioningForLabelSpecification:
    def test_resolves_real_binding_to_spot_conditioning(self):
        spec = {"binding_id": REAL_BINDING_ID, "conditioning_propagation": "preserving"}
        result = prop.conditioning_for_label_specification(spec)
        assert "Binance spot" in result.statement

    def test_all_five_real_bindings_resolve(self):
        for binding_id in prop._LABEL_BINDING_SOURCES:
            spec = {"binding_id": binding_id, "conditioning_propagation": "preserving"}
            result = prop.conditioning_for_label_specification(spec)
            assert result.statement


@pytest.mark.skipif(not _PANDAS_AVAILABLE, reason="conditioning.py's real source map needs pandas transitively")
class TestConditioningForFeatureSpecification:
    def test_resolves_real_feature_specification(self):
        spec = {
            "conditioning_rule": "preserving",
            "input_requirements": [{"source": "spot_1m", "fields": ["close"]}],
        }
        result = prop.conditioning_for_feature_specification(spec)
        assert "Binance spot" in result.statement


# ---------------------------------------------------------------------------
# LR5 injected-clock discipline
# ---------------------------------------------------------------------------

class TestNoDatetimeNow:
    def test_no_datetime_now_anywhere_in_propagation_py(self):
        source = (_LABEL_STORE_DIR / "propagation.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "now":
                pytest.fail(f"found a '.now' attribute access at line {node.lineno}")


# ---------------------------------------------------------------------------
# Real database integration -- the roadmap's own exit criterion:
# "the same module computes the ceiling for all 17 Feature Specifications"
# ---------------------------------------------------------------------------

_REAL_FEATURE_STORE_DB = _FEATURE_STORE_DIR / "feature_store.db"


@pytest.mark.skipif(not _REAL_FEATURE_STORE_DB.exists(), reason="real local feature_store.db not present (gitignored)")
@pytest.mark.skipif(not _PANDAS_AVAILABLE, reason="feature_store.db integration goes through pandas-dependent paths indirectly")
class TestRealFeatureStoreIntegration:
    def test_computes_a_ceiling_for_every_active_feature_specification(self):
        ceilings = prop.all_active_feature_specification_ceilings(_REAL_FEATURE_STORE_DB)
        assert len(ceilings) == 17

    def test_every_real_ceiling_is_honestly_uncertified(self):
        """No real gauntlet report exists for spot_1m/perp_1m/funding_raw
        (module docstring) -- every real Feature Specification's ceiling
        must show this honestly, not a fabricated pass."""
        ceilings = prop.all_active_feature_specification_ceilings(_REAL_FEATURE_STORE_DB)
        for spec_hash, c in ceilings.items():
            assert c.certification == lattice.Certification.UNKNOWN, spec_hash
            assert c.readiness == lattice.Readiness.UNKNOWN, spec_hash

    def test_every_real_ceiling_has_maturity_not_applicable(self):
        ceilings = prop.all_active_feature_specification_ceilings(_REAL_FEATURE_STORE_DB)
        for spec_hash, c in ceilings.items():
            assert c.is_not_applicable(lattice.Dimension.MATURITY), spec_hash
