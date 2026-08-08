"""WBS 4.9 — Experiment Datasets and use-counting
(ADR-I012 §7.1, §9, §15.3, DG-6, DG-8; ADR-I011 LG7/DI8; resolves PS6).

DG-8 is *"the multiple-comparisons defence at the substrate layer"* — it makes
reuse **checkable** rather than declared. `TestUseCountIntegrity` is where that
property is proven.
"""

from __future__ import annotations

import ast
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "research", ROOT / "research" / "admissibility",
          ROOT / "research" / "evidence_library"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import receipt_ledger as rl  # noqa: E402

from dataset_builder import citation_index as ci  # noqa: E402
from dataset_builder import experiments as ex  # noqa: E402
from dataset_builder import materialisation as mat  # noqa: E402
from dataset_builder import registry as reg  # noqa: E402
from dataset_builder import resolution as res  # noqa: E402
from dataset_builder import sealing as sl  # noqa: E402
from dataset_builder import validation as val  # noqa: E402
from tests.test_dataset_registry import valid_declaration  # noqa: E402
from tests.test_dataset_resolution import good_resolver  # noqa: E402
from tests.test_dataset_validation import good_inputs  # noqa: E402

TRIAL = "phase-b-battery-2026"


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    p = tmp_path / "dataset_builder.db"
    reg.init_db(p); ci.init_db(p); sl.init_db(p); mat.init_db(p); ex.init_db(p)
    return p


@pytest.fixture()
def ledger(tmp_path: Path) -> Path:
    p = tmp_path / "ledger.db"
    rl.init_db(p)
    return p


def _sealed(db, ledger, decl=None, lineage="line-a") -> str:
    d = decl if decl is not None else valid_declaration()
    h = res.declare(db, d, lineage=lineage, declared_by="rohith").spec_hash
    res.resolve(db, h, good_resolver())
    report = val.validate(db, h, good_inputs())
    sl.seal_dataset(db, h, report, sealed_by="rohith", ledger_db=ledger)
    return h


def _second_sealed(db, ledger, tag="second", lineage="line-b") -> str:
    d = valid_declaration()
    d["intent"]["rationale"] = tag
    return _sealed(db, ledger, d, lineage=lineage)


def _register(db, h, position=1, size=34, trial=TRIAL):
    return ex.register_experiment(
        db, h, trial_registration=trial, denominator_position=position,
        denominator_size=size, created_by="rohith")


# ===========================================================================
# Build / scope
# ===========================================================================

class TestBuildAndScope:
    def test_no_operational_import(self):
        src = (ROOT / "research/dataset_builder/experiments.py").read_text(encoding="utf-8")
        for n in ast.walk(ast.parse(src)):
            if isinstance(n, ast.ImportFrom) and n.module:
                assert n.module.split(".")[0] != "src"
            elif isinstance(n, ast.Import):
                assert all(a.name.split(".")[0] != "src" for a in n.names)

    def test_no_set_use_count_exists(self):
        """DG-8 requires reuse to be CHECKABLE. A setter would make the count
        an assertion again; its absence is the design, so assert the absence."""
        assert not hasattr(ex, "set_use_count")
        src = (ROOT / "research/dataset_builder/experiments.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        assert not any("set_use" in n or "reset" in n or "decrement" in n for n in names)

    def test_denominator_is_not_hardcoded(self):
        """n_trials=34 belongs to the Phase B battery, not to this module.
        Hard-coding it would invent governance and break the first registration
        with a different denominator."""
        src = (ROOT / "research/dataset_builder/experiments.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                val_node = node.value
                if isinstance(val_node, ast.Constant) and val_node.value == 34:
                    pytest.fail("denominator size 34 is hard-coded")

    def test_bindable_phases_are_the_citable_ones(self):
        assert ex.BINDABLE_PHASES == reg.CITABLE_PHASES

    def test_purposes_include_promotion(self):
        """ADR-I011 DI8 attaches a specific obligation to promotion uses."""
        assert "promotion" in ex.USE_PURPOSES


# ===========================================================================
# Registration
# ===========================================================================

class TestRegistration:
    def test_binds_a_sealed_dataset(self, db, ledger):
        h = _sealed(db, ledger)
        e = _register(db, h)
        assert e.dataset_hash == h and e.denominator_position == 1
        assert e.denominator_size == 34

    def test_experiment_id_is_content_addressed(self, db, ledger):
        h = _sealed(db, ledger)
        e = _register(db, h)
        assert e.experiment_id == ex.experiment_id(h, TRIAL, 1)

    def test_idempotent_on_identical_context(self, db, ledger):
        """Re-registering must not create a second entry — that would inflate
        the very reuse count DG-8 exists to make trustworthy."""
        h = _sealed(db, ledger)
        a, b = _register(db, h), _register(db, h)
        assert a.experiment_id == b.experiment_id
        assert len(ex.experiments_for_dataset(db, h)) == 1

    def test_unsealed_dataset_refused(self, db):
        d = valid_declaration()
        h = res.declare(db, d, lineage="l", declared_by="r").spec_hash
        with pytest.raises(ex.NotBindableError, match="citable"):
            _register(db, h)

    def test_resolved_but_unsealed_refused(self, db):
        d = valid_declaration()
        h = res.declare(db, d, lineage="l", declared_by="r").spec_hash
        res.resolve(db, h, good_resolver())
        with pytest.raises(ex.NotBindableError):
            _register(db, h)

    def test_superseded_may_still_bind(self, db, ledger):
        a = _sealed(db, ledger)
        d2 = valid_declaration(); d2["temporal_scope"]["window_end"] = "2025-06-30"
        b = _sealed(db, ledger, d2)
        reg.supersede(db, a, successor_hash=b, change_class="extension")
        assert _register(db, a, position=5).denominator_position == 5

    def test_unknown_dataset(self, db):
        with pytest.raises(ex.ExperimentError, match="no such specification"):
            _register(db, "0" * 64)

    def test_trial_registration_required(self, db, ledger):
        h = _sealed(db, ledger)
        with pytest.raises(ex.ExperimentError, match="trial_registration"):
            _register(db, h, trial="")

    def test_created_by_required(self, db, ledger):
        h = _sealed(db, ledger)
        with pytest.raises(ex.ExperimentError, match="created_by"):
            ex.register_experiment(db, h, trial_registration=TRIAL,
                                   denominator_position=1, denominator_size=34,
                                   created_by="")

    def test_requires_experiment_schema(self, tmp_path, ledger):
        p = tmp_path / "d.db"
        reg.init_db(p); ci.init_db(p); sl.init_db(p)   # no ex.init_db
        h = _sealed(p, ledger)
        with pytest.raises(reg.NotADatasetBuilderStore, match="experiment"):
            _register(p, h)

    def test_lr4_on_foreign_db(self, tmp_path):
        foreign = tmp_path / "f.db"
        sqlite3.connect(foreign).execute("CREATE TABLE x (a TEXT)")
        with pytest.raises(reg.NotADatasetBuilderStore):
            _register(foreign, "0" * 64)


class TestDenominatorIntegrity:
    def test_position_zero_refused(self, db, ledger):
        h = _sealed(db, ledger)
        with pytest.raises(ex.DenominatorError, match="outside 1"):
            _register(db, h, position=0)

    def test_position_beyond_size_refused(self, db, ledger):
        h = _sealed(db, ledger)
        with pytest.raises(ex.DenominatorError, match="outside 1"):
            _register(db, h, position=35, size=34)

    def test_boundary_positions_accepted(self, db, ledger):
        a = _sealed(db, ledger)
        b = _second_sealed(db, ledger)
        assert _register(db, a, position=1, size=34).denominator_position == 1
        assert _register(db, b, position=34, size=34).denominator_position == 34

    def test_size_zero_refused(self, db, ledger):
        h = _sealed(db, ledger)
        with pytest.raises(ex.DenominatorError, match="denominator_size"):
            _register(db, h, position=1, size=0)

    def test_duplicate_position_refused(self, db, ledger):
        a = _sealed(db, ledger)
        b = _second_sealed(db, ledger)
        _register(db, a, position=3)
        with pytest.raises(ex.DenominatorError, match="already held"):
            _register(db, b, position=3)

    def test_size_disagreement_within_a_registration_refused(self, db, ledger):
        """A denominator that changes mid-registration makes the
        multiple-comparisons count unverifiable."""
        a = _sealed(db, ledger)
        b = _second_sealed(db, ledger)
        _register(db, a, position=1, size=34)
        with pytest.raises(ex.DenominatorError, match="already declares"):
            _register(db, b, position=2, size=40)

    def test_different_registrations_may_differ_in_size(self, db, ledger):
        a = _sealed(db, ledger)
        b = _second_sealed(db, ledger)
        _register(db, a, position=1, size=34, trial="battery-a")
        assert _register(db, b, position=1, size=12,
                         trial="battery-b").denominator_size == 12

    def test_non_integer_refused(self, db, ledger):
        h = _sealed(db, ledger)
        with pytest.raises(ex.DenominatorError, match="integers"):
            _register(db, h, position="one")

    def test_verify_denominator_clean(self, db, ledger):
        a = _sealed(db, ledger); b = _second_sealed(db, ledger)
        _register(db, a, position=1); _register(db, b, position=2)
        ok, errors = ex.verify_denominator(db, TRIAL)
        assert ok is True and errors == []

    def test_verify_denominator_empty_registration(self, db):
        assert ex.verify_denominator(db, "nothing-here") == (True, [])

    def test_db_check_constraints_hold_directly(self, db, ledger):
        """The bounds are enforced at the DATABASE layer too, not only in
        Python — a direct insert cannot bypass them."""
        h = _sealed(db, ledger)
        con = sqlite3.connect(db)
        try:
            with pytest.raises(sqlite3.IntegrityError):
                con.execute(
                    "INSERT INTO experiment_datasets (experiment_id, dataset_hash,"
                    " trial_registration, denominator_position, denominator_size,"
                    " created_at, created_by) VALUES ('x', ?, 't', 99, 34, 'now', 'r')",
                    (h,))
        finally:
            con.close()


# ===========================================================================
# Use-counting — DG-8's core property
# ===========================================================================

class TestUseCountIntegrity:
    def test_starts_at_zero(self, db, ledger):
        e = _register(db, _sealed(db, ledger))
        assert ex.use_count(db, e.experiment_id) == 0

    def test_increments_on_each_use(self, db, ledger):
        e = _register(db, _sealed(db, ledger))
        for i in range(3):
            ex.record_use(db, e.experiment_id, consuming_ref=f"run-{i}")
        assert ex.use_count(db, e.experiment_id) == 3

    def test_count_is_derived_not_stored(self, db, ledger):
        """DG-8: checkable, not declared. There is no counter column to trust
        — the count is a COUNT over append-only evidence."""
        con = sqlite3.connect(db)
        try:
            cols = {r[1] for r in con.execute(
                "PRAGMA table_info(experiment_datasets)")}
        finally:
            con.close()
        assert "use_count" not in cols
        assert not any("count" in c for c in cols)

    def test_uses_cannot_be_deleted(self, db, ledger):
        e = _register(db, _sealed(db, ledger))
        ex.record_use(db, e.experiment_id, consuming_ref="run-1")
        con = sqlite3.connect(db)
        try:
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                con.execute("DELETE FROM dataset_uses")
        finally:
            con.close()

    def test_uses_cannot_be_updated(self, db, ledger):
        e = _register(db, _sealed(db, ledger))
        ex.record_use(db, e.experiment_id, consuming_ref="run-1")
        con = sqlite3.connect(db)
        try:
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                con.execute("UPDATE dataset_uses SET consuming_ref = 'other'")
        finally:
            con.close()

    def test_consuming_ref_required(self, db, ledger):
        e = _register(db, _sealed(db, ledger))
        with pytest.raises(ex.ExperimentError, match="consuming_ref"):
            ex.record_use(db, e.experiment_id, consuming_ref="")

    def test_blank_consuming_ref_blocked_at_db_layer_too(self, db, ledger):
        e = _register(db, _sealed(db, ledger))
        con = sqlite3.connect(db)
        try:
            with pytest.raises(sqlite3.IntegrityError):
                con.execute(
                    "INSERT INTO dataset_uses (experiment_id, consuming_ref, "
                    "purpose, used_at) VALUES (?, '', 'evaluation', 'now')",
                    (e.experiment_id,))
        finally:
            con.close()

    def test_unknown_purpose_refused(self, db, ledger):
        e = _register(db, _sealed(db, ledger))
        with pytest.raises(ex.ExperimentError, match="purpose"):
            ex.record_use(db, e.experiment_id, consuming_ref="r", purpose="vibes")

    def test_unknown_experiment_refused(self, db):
        with pytest.raises(ex.UnknownExperimentError):
            ex.record_use(db, "0" * 64, consuming_ref="r")

    def test_count_by_purpose(self, db, ledger):
        e = _register(db, _sealed(db, ledger))
        ex.record_use(db, e.experiment_id, consuming_ref="r1", purpose="evaluation")
        ex.record_use(db, e.experiment_id, consuming_ref="r2", purpose="promotion")
        assert ex.use_count(db, e.experiment_id) == 2
        assert ex.use_count(db, e.experiment_id, purpose="promotion") == 1

    def test_uses_ordered_oldest_first(self, db, ledger):
        e = _register(db, _sealed(db, ledger))
        for i in range(3):
            ex.record_use(db, e.experiment_id, consuming_ref=f"run-{i}")
        assert [u.consuming_ref for u in ex.uses(db, e.experiment_id)] == [
            "run-0", "run-1", "run-2"]

    def test_repeated_use_by_the_same_consumer_is_recorded(self, db, ledger):
        """Not deduplicated: the same run consuming twice IS two uses, and
        hiding that would defeat the count's purpose."""
        e = _register(db, _sealed(db, ledger))
        ex.record_use(db, e.experiment_id, consuming_ref="run-1")
        ex.record_use(db, e.experiment_id, consuming_ref="run-1")
        assert ex.use_count(db, e.experiment_id) == 2


# ===========================================================================
# DG-8 — substrate reuse made visible
# ===========================================================================

class TestSubstrateReuse:
    def test_single_use_is_not_reuse(self, db, ledger):
        h = _sealed(db, ledger)
        _register(db, h)
        r = ex.substrate_reuse(db, h)
        assert r.experiment_count == 1 and r.is_reused is False

    def test_same_substrate_across_two_trials_is_visible(self, db, ledger):
        """The signal that matters: one substrate serving two registrations
        means the two are not independent."""
        h = _sealed(db, ledger)
        _register(db, h, position=1, trial="battery-a")
        _register(db, h, position=1, trial="battery-b")
        r = ex.substrate_reuse(db, h)
        assert r.experiment_count == 2
        assert r.is_reused is True
        assert r.spans_multiple_trials is True
        assert r.trial_registrations == ("battery-a", "battery-b")

    def test_two_positions_in_one_trial_is_reuse_but_not_cross_trial(self, db, ledger):
        h = _sealed(db, ledger)
        _register(db, h, position=1)
        _register(db, h, position=2)
        r = ex.substrate_reuse(db, h)
        assert r.is_reused is True and r.spans_multiple_trials is False

    def test_total_uses_aggregates_across_experiments(self, db, ledger):
        h = _sealed(db, ledger)
        a = _register(db, h, position=1, trial="battery-a")
        b = _register(db, h, position=1, trial="battery-b")
        ex.record_use(db, a.experiment_id, consuming_ref="r1")
        ex.record_use(db, b.experiment_id, consuming_ref="r2")
        ex.record_use(db, b.experiment_id, consuming_ref="r3")
        assert ex.substrate_reuse(db, h).total_uses == 3

    def test_unused_substrate(self, db, ledger):
        h = _sealed(db, ledger)
        r = ex.substrate_reuse(db, h)
        assert r.experiment_count == 0 and r.total_uses == 0


# ===========================================================================
# ADR-I011 DI8 — promotion consumption
# ===========================================================================

class TestPromotionUses:
    def test_promotion_uses_recorded(self, db, ledger):
        h = _sealed(db, ledger)
        e = _register(db, h)
        ex.record_use(db, e.experiment_id, consuming_ref="promo-1", purpose="promotion")
        assert len(ex.promotion_uses(db, h)) == 1

    def test_repeated_promotion_is_visible_not_refused(self, db, ledger):
        """DI8 makes repeated promotion **visible**; §13.1 keeps the decision
        human. The builder supplies use-counted evidence and never decides."""
        h = _sealed(db, ledger)
        e = _register(db, h)
        ex.record_use(db, e.experiment_id, consuming_ref="promo-1", purpose="promotion")
        ex.record_use(db, e.experiment_id, consuming_ref="promo-2", purpose="promotion")
        promos = ex.promotion_uses(db, h)
        assert len(promos) == 2
        assert [p.consuming_ref for p in promos] == ["promo-1", "promo-2"]

    def test_evaluation_uses_excluded_from_promotion_view(self, db, ledger):
        h = _sealed(db, ledger)
        e = _register(db, h)
        ex.record_use(db, e.experiment_id, consuming_ref="r1", purpose="evaluation")
        assert ex.promotion_uses(db, h) == []

    def test_promotion_across_trials_aggregated(self, db, ledger):
        h = _sealed(db, ledger)
        a = _register(db, h, position=1, trial="battery-a")
        b = _register(db, h, position=1, trial="battery-b")
        ex.record_use(db, a.experiment_id, consuming_ref="p1", purpose="promotion")
        ex.record_use(db, b.experiment_id, consuming_ref="p2", purpose="promotion")
        assert len(ex.promotion_uses(db, h)) == 2


# ===========================================================================
# Immutability and lineage
# ===========================================================================

class TestImmutability:
    def test_experiment_cannot_be_updated(self, db, ledger):
        e = _register(db, _sealed(db, ledger))
        con = sqlite3.connect(db)
        try:
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                con.execute("UPDATE experiment_datasets SET denominator_position = 9")
        finally:
            con.close()

    def test_experiment_cannot_be_deleted(self, db, ledger):
        e = _register(db, _sealed(db, ledger))
        con = sqlite3.connect(db)
        try:
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                con.execute("DELETE FROM experiment_datasets")
        finally:
            con.close()

    def test_position_cannot_be_moved_after_a_result(self, db, ledger):
        """The abuse DG-8 prevents: moving a denominator position once the
        result is known."""
        e = _register(db, _sealed(db, ledger))
        ex.record_use(db, e.experiment_id, consuming_ref="run-1")
        con = sqlite3.connect(db)
        try:
            with pytest.raises(sqlite3.IntegrityError):
                con.execute(
                    "UPDATE experiment_datasets SET denominator_position = 2 "
                    "WHERE experiment_id = ?", (e.experiment_id,))
        finally:
            con.close()

    def test_no_orphan_experiments(self, db, ledger):
        _register(db, _sealed(db, ledger))
        assert ex.find_orphan_experiments(db) == []


class TestLineageAndContext:
    def test_trial_context_carries_everything_section_9_requires(self, db, ledger):
        h = _sealed(db, ledger)
        e = _register(db, h, position=7)
        ex.record_use(db, e.experiment_id, consuming_ref="run-a")
        ctx = ex.trial_context(db, e.experiment_id)
        assert ctx["trial_registration"] == TRIAL
        assert ctx["denominator_position"] == 7
        assert ctx["use_count"] == 1
        assert ctx["consuming_refs"] == ["run-a"]

    def test_trial_context_includes_substrate_citations(self, db, ledger):
        h = _sealed(db, ledger)
        e = _register(db, h)
        ctx = ex.trial_context(db, e.experiment_id)
        assert set(ctx["substrate_citations"]) == {"evidence", "feature", "label"}

    def test_trial_context_unknown_experiment(self, db):
        with pytest.raises(ex.UnknownExperimentError):
            ex.trial_context(db, "0" * 64)

    def test_experiments_in_trial_ordered_by_position(self, db, ledger):
        a = _sealed(db, ledger); b = _second_sealed(db, ledger)
        _register(db, b, position=5); _register(db, a, position=2)
        assert [e.denominator_position for e in ex.experiments_in_trial(db, TRIAL)] == [2, 5]

    def test_experiments_for_dataset(self, db, ledger):
        h = _sealed(db, ledger)
        _register(db, h, position=1, trial="battery-a")
        _register(db, h, position=1, trial="battery-b")
        assert len(ex.experiments_for_dataset(db, h)) == 2


# ===========================================================================
# TV3 sabotage
# ===========================================================================

class TestTV3Sabotage:
    def test_use_count_is_not_vacuous(self, db, ledger):
        e = _register(db, _sealed(db, ledger))
        assert ex.use_count(db, e.experiment_id) == 0
        ex.record_use(db, e.experiment_id, consuming_ref="r")
        assert ex.use_count(db, e.experiment_id) == 1

    def test_reuse_detection_is_not_vacuous(self, db, ledger):
        h = _sealed(db, ledger)
        _register(db, h, position=1, trial="a")
        assert ex.substrate_reuse(db, h).is_reused is False
        _register(db, h, position=1, trial="b")
        assert ex.substrate_reuse(db, h).is_reused is True

    def test_denominator_guard_is_not_vacuous(self, db, ledger):
        h = _sealed(db, ledger)
        _register(db, h, position=1, size=34)
        with pytest.raises(ex.DenominatorError):
            _register(db, _second_sealed(db, ledger), position=1, size=34)

    def test_phase_guard_is_not_vacuous(self, db):
        d = valid_declaration()
        h = res.declare(db, d, lineage="l", declared_by="r").spec_hash
        with pytest.raises(ex.NotBindableError):
            _register(db, h)

    def test_promotion_filter_is_not_vacuous(self, db, ledger):
        h = _sealed(db, ledger)
        e = _register(db, h)
        ex.record_use(db, e.experiment_id, consuming_ref="r", purpose="evaluation")
        assert ex.promotion_uses(db, h) == []
        ex.record_use(db, e.experiment_id, consuming_ref="p", purpose="promotion")
        assert len(ex.promotion_uses(db, h)) == 1


# ===========================================================================
# Regression — 4.1–4.8 invariants
# ===========================================================================

class TestRegression:
    def test_rf1_to_rf6_unchanged(self):
        assert set(res.DEFERRED_REFUSALS) == {"RF4"}
        assert val.DEFERRED_CHECKS == {}

    def test_registry_phase_untouched_by_registration(self, db, ledger):
        h = _sealed(db, ledger)
        _register(db, h)
        assert reg.get_specification(db, h)["phase"] == "Sealed"

    def test_citation_graph_intact(self, db, ledger):
        h = _sealed(db, ledger)
        _register(db, h)
        ok, errors = ci.verify_bidirectional_consistency(db)
        assert ok is True and errors == []

    def test_sealed_immutability_still_enforced(self, db, ledger):
        h = _sealed(db, ledger)
        _register(db, h)
        con = sqlite3.connect(db)
        try:
            with pytest.raises(sqlite3.IntegrityError, match="DG-3"):
                con.execute("UPDATE sealed_datasets SET sealed_by='x'")
        finally:
            con.close()

    def test_materialisation_still_deterministic(self, db, ledger):
        """Reproducibility must survive the new tables."""
        from tests.test_materialisation import rich_provider
        h = _sealed(db, ledger)
        _register(db, h)
        a = mat.materialise(db, h, rich_provider())
        b = mat.materialise(db, h, rich_provider())
        assert a.snapshot.sha256 == b.snapshot.sha256

    def test_declaration_hash_unchanged(self, db, ledger):
        h = _sealed(db, ledger)
        _register(db, h)
        assert reg.declaration_hash(
            reg.get_specification(db, h)["declaration"]) == h

    def test_receipt_chain_unaffected(self, db, ledger):
        h = _sealed(db, ledger)
        _register(db, h)
        assert rl.verify_chain(ledger).ok is True

    def test_result_invariance_still_holds(self):
        import hashlib, json
        baseline = json.loads((ROOT / "BASELINE_46.json").read_text())
        for rel, was in baseline.items():
            assert hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() == was, rel

    def test_full_pipeline_to_experiment(self, db, ledger):
        from tests.test_materialisation import rich_provider
        h = _sealed(db, ledger)
        r = mat.materialise(db, h, rich_provider())
        e = _register(db, h, position=12)
        ex.record_use(db, e.experiment_id, consuming_ref="eval-run-1")
        assert reg.is_citable(db, h) is True
        assert r.ok is True
        assert ex.use_count(db, e.experiment_id) == 1
        assert ex.verify_denominator(db, TRIAL) == (True, [])

    def test_no_other_store_touched(self, tmp_path):
        p = tmp_path / "d.db"; led = tmp_path / "l.db"
        reg.init_db(p); ci.init_db(p); sl.init_db(p); mat.init_db(p); ex.init_db(p)
        rl.init_db(led)
        h = _sealed(p, led)
        _register(p, h)
        assert {f.name for f in tmp_path.rglob("*.db")} == {"d.db", "l.db"}
