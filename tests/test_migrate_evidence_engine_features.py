"""
Tests for research/feature_store/migrate_evidence_engine_features.py
(engineering roadmap Phase 2, WBS 2.4 -- Feature Specification migration).

Three tiers:
  - Portable, mocked tests: engine.load_master() is monkeypatched with a
    small synthetic DataFrame, proving run() registers/activates all 17
    features against a throwaway tmp_path database -- no real data needed.
  - Idempotency regression test (test_migration_is_actually_idempotent_on_a_real_run):
    directly closes INC-0002 (docs/engineering/incidents/INC-0002-feature-
    migration-idempotency.md) by running run() twice against the same
    database and asserting identical spec_hashes and stable row counts on
    the second run.
  - Real-data-gated end-to-end test (TestAgainstRealLocalData): skipped
    unless the real local research corpus is present, mirroring
    tests/test_evidence_engine_bindings.py's own gating pattern.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")

RESEARCH_ROOT = Path(__file__).resolve().parents[1] / "research"
FEATURE_STORE_DIR = RESEARCH_ROOT / "feature_store"
for _p in (RESEARCH_ROOT, RESEARCH_ROOT / "evidence", FEATURE_STORE_DIR):
    sys.path.insert(0, str(_p))

import registry as reg  # noqa: E402
import evidence_engine_bindings as bindings  # noqa: E402
import migrate_evidence_engine_features as migrate  # noqa: E402

# NOT "import engine" -- see evidence_engine_bindings.py's
# _load_evidence_engine() docstring: sys.modules is process-global and
# research/dq/engine.py collides on the same bare name depending on pytest
# collection order. Use the exact module object bindings/migrate already use.
engine = bindings.engine

_REAL_DATA_PRESENT = (RESEARCH_ROOT / "data" / "raw" / "spot" / "BTCUSDT").exists()


def _fake_master() -> pd.DataFrame:
    n = 400
    cols = {
        name: [float(i) + (hash(name) % 1000) / 1000.0 for i in range(n)]
        for name in bindings.BINDINGS
    }
    # market_regime/vol_regime/funding_regime are string-typed in the real
    # engine output; keep at least one non-numeric column to prove the
    # equivalence check's JSON-string comparison is dtype-agnostic.
    cols["market_regime"] = ["bull" if i % 2 == 0 else "bear" for i in range(n)]
    cols["vol_regime"] = ["high" if i % 3 == 0 else "low" for i in range(n)]
    cols["funding_regime"] = ["high" if i % 5 == 0 else "low" for i in range(n)]
    return pd.DataFrame(cols)


@pytest.fixture(autouse=True)
def _mocked_engine(monkeypatch):
    """Every test in this module runs against a fast synthetic load_master(),
    never the real one, unless explicitly overridden (TestAgainstRealLocalData
    below undoes this via its own fixture)."""
    monkeypatch.setattr(engine, "load_master", _fake_master)
    yield


class TestFeaturesTableConsistency:
    def test_features_table_matches_bindings_registry_exactly(self):
        names_in_table = {f["name"] for f in migrate.FEATURES}
        assert names_in_table == set(bindings.BINDINGS.keys())

    def test_seventeen_features_declared(self):
        assert len(migrate.FEATURES) == 17

    def test_ret_fwd1_excluded(self):
        names_in_table = {f["name"] for f in migrate.FEATURES}
        assert "ret_fwd1" not in names_in_table

    def test_every_feature_has_required_fields(self):
        for f in migrate.FEATURES:
            assert f["name"]
            assert f["rationale"]
            assert f["intended_role"]
            assert f["input_requirements"]
            assert f["warmup_periods"] >= 0
            assert f["conditioning_rule"]  # WBS 2.7

    def test_every_feature_declares_a_known_conditioning_rule(self):
        import conditioning as cond
        for f in migrate.FEATURES:
            assert f["conditioning_rule"] in cond.RULES

    def test_all_seventeen_declare_preserving(self):
        """As of WBS 2.7, every migrated feature's conditioning rule is
        "preserving" -- none narrows or replaces conditioning. Documented
        explicitly (not just implicitly true) so a future feature that
        genuinely needs "narrowing"/"cross-sectional" is a deliberate,
        visible addition rather than an unnoticed drift from this table."""
        assert all(f["conditioning_rule"] == "preserving" for f in migrate.FEATURES)


class TestPlan:
    def test_plan_is_pure_and_does_not_touch_a_database(self, tmp_path):
        db_path = tmp_path / "should_not_exist.db"
        rows = migrate.plan()
        assert not db_path.exists()
        assert len(rows) == 17
        assert {r["name"] for r in rows} == set(bindings.BINDINGS.keys())

    def test_print_plan_runs_without_error(self, capsys):
        migrate.print_plan()
        out = capsys.readouterr().out
        assert "ret_fwd1" in out
        assert "17 feature(s)" in out


class TestRunAgainstThrowawayDatabase:
    def test_run_registers_and_activates_all_seventeen(self, tmp_path):
        db_path = tmp_path / "feature_store.db"
        results = migrate.run(db_path)
        assert len(results) == 17
        assert all(r["phase"] == "Active" for r in results), results

    def test_run_writes_one_definition_and_one_specification_per_feature(self, tmp_path):
        db_path = tmp_path / "feature_store.db"
        migrate.run(db_path)
        import sqlite3
        con = sqlite3.connect(db_path)
        try:
            n_defs = con.execute("SELECT COUNT(*) FROM feature_definitions").fetchone()[0]
            n_specs = con.execute("SELECT COUNT(*) FROM feature_specifications").fetchone()[0]
        finally:
            con.close()
        assert n_defs == 17
        assert n_specs == 17

    def test_main_dry_run_writes_nothing(self, tmp_path, capsys):
        db_path = tmp_path / "feature_store.db"
        rc = migrate.main(["--db", str(db_path)])
        assert rc == 0
        assert not db_path.exists()
        assert "Dry run only" in capsys.readouterr().out

    def test_main_commit_writes_and_returns_zero(self, tmp_path):
        db_path = tmp_path / "feature_store.db"
        rc = migrate.main(["--db", str(db_path), "--commit"])
        assert rc == 0
        assert db_path.exists()


class TestMigrationIsActuallyIdempotent:
    """Directly regression-tests INC-0002: the second --commit run must
    reuse the same Definition version and produce the SAME spec_hash for
    every feature, not double the registry."""

    def test_migration_is_actually_idempotent_on_a_real_run(self, tmp_path):
        db_path = tmp_path / "feature_store.db"

        first = migrate.run(db_path)
        second = migrate.run(db_path)

        first_by_name = {r["name"]: r["spec_hash"] for r in first}
        second_by_name = {r["name"]: r["spec_hash"] for r in second}
        assert first_by_name == second_by_name

        assert all(r["phase"] == "Active" for r in second)

        import sqlite3
        con = sqlite3.connect(db_path)
        try:
            n_defs = con.execute("SELECT COUNT(*) FROM feature_definitions").fetchone()[0]
            n_specs = con.execute("SELECT COUNT(*) FROM feature_specifications").fetchone()[0]
        finally:
            con.close()
        assert n_defs == 17, "re-running must not create new Definition versions (INC-0002)"
        assert n_specs == 17, "re-running must not create new Specifications (INC-0002)"

    def test_get_or_register_definition_reuses_unchanged_content(self, tmp_path):
        db_path = tmp_path / "feature_store.db"
        reg.init_db(db_path)

        first = migrate._get_or_register_definition(db_path, "basis_bps", "same rationale", "same role")
        second = migrate._get_or_register_definition(db_path, "basis_bps", "same rationale", "same role")

        assert first.version == second.version == 1

    def test_get_or_register_definition_creates_new_version_on_real_content_change(self, tmp_path):
        db_path = tmp_path / "feature_store.db"
        reg.init_db(db_path)

        first = migrate._get_or_register_definition(db_path, "basis_bps", "rationale A", "role A")
        second = migrate._get_or_register_definition(db_path, "basis_bps", "rationale B (changed)", "role A")

        assert first.version == 1
        assert second.version == 2


class TestEquivalenceCheck:
    def test_equivalence_check_passes_for_a_correct_binding(self):
        assert migrate._equivalence_check(bindings.rv_21d, "rv_21d") is True

    def test_equivalence_check_fails_for_a_mismatched_column(self):
        assert migrate._equivalence_check(bindings.rv_21d, "log_ret") is False


class TestWarmupIntegration:
    """WBS 2.6: warm-up as a governed contract term, exercised via run()'s
    real integration point. _fake_master()'s 400 rows satisfy every
    FEATURES entry's warmup_periods (max 273) -- these tests deliberately
    shrink the mocked corpus below several features' declared warm-up to
    exercise all three modes for real, not just via warmup.py's own unit
    tests."""

    def _small_master(self, n: int):
        full = _fake_master()
        return full.iloc[:n].reset_index(drop=True)

    def test_default_mode_all_sufficient_reports_proceed(self, tmp_path):
        """400 rows >> every declared warmup_periods (max 273) -- nothing
        is refused or warned in the default (unshrunk) mocked corpus."""
        db_path = tmp_path / "feature_store.db"
        results = migrate.run(db_path)
        assert all(r["phase"] == "Active" for r in results)
        assert all(r["warmup_action"] == "proceed" for r in results)

    def test_refuse_mode_skips_only_insufficient_features(self, tmp_path, monkeypatch):
        monkeypatch.setattr(engine, "load_master", lambda: self._small_master(50))
        db_path = tmp_path / "feature_store.db"
        results = migrate.run(db_path, warmup_mode="refuse")

        refused = [r for r in results if r["phase"] == "Warmup-Refused"]
        active = [r for r in results if r["phase"] == "Active"]
        assert len(results) == 17
        assert len(refused) + len(active) == 17
        assert len(refused) > 0, "at least one of the 17 declares warmup_periods > 50"
        assert len(active) > 0, "at least one of the 17 declares warmup_periods <= 50"
        for r in refused:
            assert r["spec_hash"] is None
            assert r["warmup_action"] == "refused"
        for r in active:
            assert r["warmup_action"] == "proceed"

    def test_refuse_mode_does_not_register_a_specification_for_refused_features(self, tmp_path, monkeypatch):
        monkeypatch.setattr(engine, "load_master", lambda: self._small_master(50))
        db_path = tmp_path / "feature_store.db"
        results = migrate.run(db_path, warmup_mode="refuse")

        refused_names = {r["name"] for r in results if r["phase"] == "Warmup-Refused"}
        assert refused_names, "test setup expects at least one refusal"

        import sqlite3
        con = sqlite3.connect(db_path)
        try:
            registered_names = {
                row[0] for row in con.execute("SELECT DISTINCT name FROM feature_definitions")
            }
        finally:
            con.close()
        assert registered_names.isdisjoint(refused_names), (
            "a refused feature must not have a Definition registered for it"
        )

    def test_warn_mode_proceeds_and_activates_despite_insufficient_history(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(engine, "load_master", lambda: self._small_master(50))
        db_path = tmp_path / "feature_store.db"
        results = migrate.run(db_path, warmup_mode="warn")

        assert all(r["phase"] == "Active" for r in results), (
            "warn mode must never block registration/activation"
        )
        warned = [r for r in results if r["warmup_action"] == "proceed_with_warning"]
        assert len(warned) > 0
        assert "WARNING" in capsys.readouterr().out

    def test_disabled_mode_proceeds_silently_despite_insufficient_history(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(engine, "load_master", lambda: self._small_master(50))
        db_path = tmp_path / "feature_store.db"
        results = migrate.run(db_path, warmup_mode="disabled")

        assert all(r["phase"] == "Active" for r in results)
        out = capsys.readouterr().out
        assert "WARNING" not in out
        assert "REFUSED" not in out

    def test_real_policy_file_governs_when_mode_not_passed(self, tmp_path, monkeypatch, capsys):
        """No warmup_mode argument -> falls back to warmup_policy.yaml,
        which ships as "warn" (the rollout default, roadmap R5) -- proves
        run()'s default argument actually reaches the real shipped policy,
        not just a hardcoded fallback inside run() itself."""
        monkeypatch.setattr(engine, "load_master", lambda: self._small_master(50))
        db_path = tmp_path / "feature_store.db"
        results = migrate.run(db_path)  # no warmup_mode -> reads warmup_policy.yaml

        assert all(r["phase"] == "Active" for r in results)
        assert "WARNING" in capsys.readouterr().out

    def test_main_returns_nonzero_when_a_feature_is_refused(self, tmp_path, monkeypatch):
        monkeypatch.setattr(engine, "load_master", lambda: self._small_master(50))
        db_path = tmp_path / "feature_store.db"
        rc = migrate.main(["--db", str(db_path), "--commit", "--warmup-mode", "refuse"])
        assert rc == 1

    def test_main_returns_zero_in_warn_mode_even_with_insufficient_history(self, tmp_path, monkeypatch):
        monkeypatch.setattr(engine, "load_master", lambda: self._small_master(50))
        db_path = tmp_path / "feature_store.db"
        rc = migrate.main(["--db", str(db_path), "--commit", "--warmup-mode", "warn"])
        assert rc == 0


class TestConditioningIntegration:
    """WBS 2.7: conditioning propagation, exercised via run()'s real
    integration point (the same one warm-up uses -- see TestWarmupIntegration)."""

    def test_every_active_feature_gets_a_propagated_conditioning_statement(self, tmp_path):
        db_path = tmp_path / "feature_store.db"
        results = migrate.run(db_path)
        assert all(r["phase"] == "Active" for r in results)
        for r in results:
            assert r["conditioning_rule"] == "preserving"
            assert r["conditioning_statement"]
            assert r["conditioning_input_count"] >= 1

    def test_dual_source_feature_gets_two_input_statements(self, tmp_path):
        db_path = tmp_path / "feature_store.db"
        results = migrate.run(db_path)
        by_name = {r["name"]: r for r in results}
        # basis_bps, vrp_spread, vrp_logratio each declare two input sources
        for name in ("basis_bps", "vrp_spread", "vrp_logratio"):
            assert by_name[name]["conditioning_input_count"] == 2, name

    def test_single_source_feature_gets_one_input_statement(self, tmp_path):
        db_path = tmp_path / "feature_store.db"
        results = migrate.run(db_path)
        by_name = {r["name"]: r for r in results}
        assert by_name["rv_21d"]["conditioning_input_count"] == 1

    def test_unresolvable_source_is_refused_and_does_not_abort_the_batch(self, tmp_path, monkeypatch):
        """Monkeypatches the FEATURES table's rv_21d entry to reference an
        unknown source for the duration of this test only, proving run()
        skips only that feature, reports it distinctly (phase
        "Conditioning-Refused"), and continues to the other 16 -- the
        pytest-level counterpart to the live-script TV3 exercise recorded
        in the WBS 2.7 verification record."""
        original_features = migrate.FEATURES
        patched_features = [dict(f) for f in original_features]
        for f in patched_features:
            if f["name"] == "rv_21d":
                f["input_requirements"] = [{"source": "bogus_source", "fields": ["close"]}]
        monkeypatch.setattr(migrate, "FEATURES", patched_features)

        db_path = tmp_path / "feature_store.db"
        results = migrate.run(db_path)

        by_name = {r["name"]: r for r in results}
        assert by_name["rv_21d"]["phase"] == "Conditioning-Refused"
        assert by_name["rv_21d"]["spec_hash"] is None
        others = [r for r in results if r["name"] != "rv_21d"]
        assert len(others) == 16
        assert all(r["phase"] == "Active" for r in others)


@pytest.mark.skipif(not _REAL_DATA_PRESENT, reason="real local research corpus not present")
class TestAgainstRealLocalData:
    """End-to-end run against the genuine, unmodified engine.load_master(),
    mirroring the actual --commit run already performed manually against
    the real local feature_store.db."""

    @pytest.fixture(autouse=True)
    def _real_engine(self, monkeypatch):
        monkeypatch.undo()  # undo module-level _mocked_engine for this class only
        yield

    def test_run_against_real_data_activates_all_seventeen(self, tmp_path):
        db_path = tmp_path / "feature_store.db"
        results = migrate.run(db_path)
        assert len(results) == 17
        assert all(r["phase"] == "Active" for r in results), results

    def test_real_corpus_satisfies_every_declared_warmup_even_in_refuse_mode(self, tmp_path):
        """The real local corpus (1987 rows) exceeds every FEATURES entry's
        declared warmup_periods (max 273) -- confirms, against genuine
        data rather than a mock, that hard refusal mode produces zero
        refusals today. This is the real-corpus counterpart to the
        manual TV3 exercise recorded in the WBS 2.6 verification record."""
        db_path = tmp_path / "feature_store.db"
        results = migrate.run(db_path, warmup_mode="refuse")
        assert len(results) == 17
        assert all(r["phase"] == "Active" for r in results), results
        assert all(r["warmup_action"] == "proceed" for r in results)

    def test_real_sources_resolve_conditioning_for_all_seventeen(self, tmp_path):
        """Every one of the 17 migrated features' declared input_requirements
        resolve to a real conditioning statement from the real ingest
        writers -- confirms, against genuine source text rather than a
        mock, that conditioning propagation never refuses any currently-
        migrated feature."""
        db_path = tmp_path / "feature_store.db"
        results = migrate.run(db_path)
        assert len(results) == 17
        assert all(r["phase"] == "Active" for r in results), results
        for r in results:
            assert r["conditioning_statement"]
            assert r["conditioning_rule"] == "preserving"
