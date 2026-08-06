"""
Tests for research/evidence_library/migrate_bootstrap_feature_receipts.py
(Phase 3 Label Store roadmap v1.1.0, WBS 3.8b -- the ninth Label Store
roadmap milestone; `FS-GAP-004` closure, `ADR-I010` FG1).

Mirrors tests/test_migrate_receipt_attestation_basis.py's own conventions:
a synthetic tmp_path fixture for the plan/apply/CLI mechanics, plus a
real-data-gated tier using the SAME hash-guard discipline (an autouse
module-scoped fixture proves the real local feature_store.db is
byte-unchanged after every test in this file, including the real
--commit-against-a-COPY tests below).
"""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EVLIB = ROOT / "research" / "evidence_library"
FEATURE_STORE_DIR = ROOT / "research" / "feature_store"
REAL_FEATURE_STORE_DB = FEATURE_STORE_DIR / "feature_store.db"

sys.path.insert(0, str(EVLIB))
import migrate_bootstrap_feature_receipts as mbfr  # noqa: E402
import receipt_ledger as rl  # noqa: E402
import receipts  # noqa: E402

sys.path.insert(0, str(FEATURE_STORE_DIR))
import registry as freg  # noqa: E402

NOW = "2026-08-06T00:00:00+00:00"


def _register_real_spec(db_path, *, definition_name="basis_bps", binding_id="test.binding"):
    d = freg.register_definition(db_path, definition_name, "test rationale", "test intended role", "rohith", NOW)
    return freg.register_specification(
        db_path,
        definition_name=d.name, definition_version=d.version,
        parameters={}, input_requirements=[{"source": "spot_1m", "fields": ["close"]}],
        warmup_periods=30, conditioning_rule="preserving",
        binding_id=binding_id, engine_version="v1",
        validity_rule="valid once warmup elapses", sensitivity_variants=[],
        actor="migrate_evidence_engine_features", registered_at=NOW,
    )


@pytest.fixture
def db_path(tmp_path) -> Path:
    p = tmp_path / "feature_store.db"
    freg.init_db(p)
    return p


# ---------------------------------------------------------------------------
# plan() / _all_specifications() / idempotency
# ---------------------------------------------------------------------------

class TestPlan:
    def test_fresh_db_has_nothing_to_plan(self, db_path):
        assert mbfr.plan(db_path) == []

    def test_one_unreceipted_specification_is_planned(self, db_path):
        spec_hash = _register_real_spec(db_path)
        rl.init_db(db_path)
        planned = mbfr.plan(db_path)
        assert len(planned) == 1
        assert planned[0]["spec_hash"] == spec_hash

    def test_two_specifications_both_planned(self, db_path):
        _register_real_spec(db_path, definition_name="feat_a", binding_id="binding.a")
        _register_real_spec(db_path, definition_name="feat_b", binding_id="binding.b")
        rl.init_db(db_path)
        assert len(mbfr.plan(db_path)) == 2

    def test_already_receipted_specification_is_not_planned(self, db_path):
        spec = _register_real_spec(db_path)
        rl.init_db(db_path)
        mbfr.apply(db_path, mbfr.plan(db_path))
        assert mbfr.plan(db_path) == []


# ---------------------------------------------------------------------------
# apply() -- the bootstrap receipt itself
# ---------------------------------------------------------------------------

class TestApply:
    def test_receipt_is_bootstrap_attested(self, db_path):
        _register_real_spec(db_path)
        rl.init_db(db_path)
        mbfr.apply(db_path, mbfr.plan(db_path))
        row = rl.get_all_receipts(db_path)[0]
        assert row["body"]["attestation"] == "bootstrap"
        assert row["decision_type"] == "feature.register"
        assert row["subject_kind"] == "feature"

    def test_active_phase_verdict_is_registered(self, db_path):
        spec_hash = _register_real_spec(db_path)
        freg._transition_phase(db_path, spec_hash, "Registered", "Active")
        rl.init_db(db_path)
        mbfr.apply(db_path, mbfr.plan(db_path))
        row = rl.get_all_receipts(db_path)[0]
        assert row["body"]["verdict"]["result"] == "registered"

    def test_rejected_phase_verdict_is_rejected(self, db_path):
        d = freg.register_definition(db_path, "bad_feature", "r", "i", "rohith", NOW)
        with pytest.raises(freg.SpecificationRejected):
            freg.register_specification(
                db_path,
                definition_name=d.name, definition_version=d.version,
                parameters={}, input_requirements=[],  # empty -- Tier A failure
                warmup_periods=30, conditioning_rule="preserving",
                binding_id="x", engine_version="v1",
                validity_rule="x", sensitivity_variants=[],
                actor="test", registered_at=NOW,
            )
        rl.init_db(db_path)
        planned = mbfr.plan(db_path)
        assert len(planned) == 1
        assert planned[0]["phase"] == "Rejected"
        mbfr.apply(db_path, planned)
        row = rl.get_all_receipts(db_path)[0]
        assert row["body"]["verdict"]["result"] == "rejected"

    def test_as_of_matches_the_specifications_own_registered_at(self, db_path):
        _register_real_spec(db_path)
        rl.init_db(db_path)
        mbfr.apply(db_path, mbfr.plan(db_path))
        row = rl.get_all_receipts(db_path)[0]
        assert row["body"]["as_of"] == NOW

    def test_replay_refuses_a_bootstrap_receipt(self, db_path):
        """The exact 'bootstrap honesty' guarantee this module's own
        docstring claims -- receipts.replay()'s bootstrap check fires
        before decision_class is ever consulted."""
        _register_real_spec(db_path)
        rl.init_db(db_path)
        mbfr.apply(db_path, mbfr.plan(db_path))
        row = rl.get_all_receipts(db_path)[0]
        result = receipts.replay(row["body"])
        assert result.ok is False
        assert result.mode == "refused"

    def test_verify_still_holds_on_a_bootstrap_receipt(self, db_path):
        _register_real_spec(db_path)
        rl.init_db(db_path)
        mbfr.apply(db_path, mbfr.plan(db_path))
        row = rl.get_all_receipts(db_path)[0]
        assert receipts.verify_receipt(row["body"], row["receipt_id"]) is True

    def test_multiple_receipts_are_chained(self, db_path):
        _register_real_spec(db_path, definition_name="feat_a", binding_id="binding.a")
        _register_real_spec(db_path, definition_name="feat_b", binding_id="binding.b")
        rl.init_db(db_path)
        mbfr.apply(db_path, mbfr.plan(db_path))
        result = rl.verify_chain(db_path)
        assert result.ok is True
        assert result.n_receipts == 2

    def test_feature_specifications_table_is_never_written_to(self, db_path):
        _register_real_spec(db_path)
        rl.init_db(db_path)
        before = sqlite3.connect(db_path).execute(
            "SELECT spec_hash, phase, registered_by, registered_at FROM feature_specifications"
        ).fetchall()
        mbfr.apply(db_path, mbfr.plan(db_path))
        after = sqlite3.connect(db_path).execute(
            "SELECT spec_hash, phase, registered_by, registered_at FROM feature_specifications"
        ).fetchall()
        assert before == after


# ---------------------------------------------------------------------------
# Structural guard: no UPDATE against the source tables
# ---------------------------------------------------------------------------

class TestNoMutationOfSourceTables:
    def test_module_issues_no_update_statement_against_feature_tables(self):
        source = Path(mbfr.__file__).read_text(encoding="utf-8")
        upper = source.upper()
        assert "UPDATE FEATURE_SPECIFICATIONS" not in upper
        assert "UPDATE FEATURE_DEFINITIONS" not in upper
        assert "DELETE FROM FEATURE_SPECIFICATIONS" not in upper
        assert "DELETE FROM FEATURE_DEFINITIONS" not in upper


# ---------------------------------------------------------------------------
# CLI (main())
# ---------------------------------------------------------------------------

class TestMainCLI:
    def test_dry_run_on_fresh_db_reports_nothing(self, db_path, capsys):
        rc = mbfr.main(["--db", str(db_path)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "0 Feature Specifications need a bootstrap receipt" in out
        assert "Dry run only" in out

    def test_dry_run_writes_nothing(self, db_path, capsys):
        _register_real_spec(db_path)
        rc = mbfr.main(["--db", str(db_path)])
        assert rc == 0
        # A true dry run creates no schema either -- governance_receipts
        # must not exist at all on a fresh db (see plan()'s own docstring).
        assert mbfr._governance_receipts_table_exists(db_path) is False

    def test_commit_on_fresh_db_is_a_no_op_with_no_backup(self, db_path):
        rc = mbfr.main(["--db", str(db_path), "--commit"])
        assert rc == 0
        assert list(db_path.parent.glob("*.pre-feature-receipts-migration.*.db")) == []

    def test_commit_writes_receipts_and_backs_up(self, db_path):
        _register_real_spec(db_path, definition_name="feat_a", binding_id="binding.a")
        _register_real_spec(db_path, definition_name="feat_b", binding_id="binding.b")
        rc = mbfr.main(["--db", str(db_path), "--commit"])
        assert rc == 0
        assert len(rl.get_all_receipts(db_path)) == 2
        backups = list(db_path.parent.glob("feature_store.pre-feature-receipts-migration.*.db"))
        assert len(backups) == 1

    def test_second_commit_is_idempotent_and_backs_up_nothing_new(self, db_path):
        _register_real_spec(db_path)
        mbfr.main(["--db", str(db_path), "--commit"])
        first_count = len(rl.get_all_receipts(db_path))
        first_backups = len(list(db_path.parent.glob("*.pre-feature-receipts-migration.*.db")))

        rc = mbfr.main(["--db", str(db_path), "--commit"])
        assert rc == 0
        assert len(rl.get_all_receipts(db_path)) == first_count
        assert len(list(db_path.parent.glob("*.pre-feature-receipts-migration.*.db"))) == first_backups

    def test_missing_db_is_an_error(self, tmp_path):
        rc = mbfr.main(["--db", str(tmp_path / "does_not_exist.db")])
        assert rc == 1


# ---------------------------------------------------------------------------
# Real feature_store.db -- local-development only, hash-guarded exactly
# like test_migrate_receipt_attestation_basis.py's own real-ledger tier.
# ---------------------------------------------------------------------------

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _assert_not_real_feature_store(path: Path) -> None:
    assert Path(path).resolve() != REAL_FEATURE_STORE_DB.resolve(), (
        f"REFUSING: {path} resolves to the real feature_store.db -- this test must "
        f"never operate on it directly."
    )


@pytest.fixture(scope="module", autouse=True)
def _guard_real_feature_store_untouched():
    if REAL_FEATURE_STORE_DB.exists():
        before = _sha256(REAL_FEATURE_STORE_DB)
    yield
    if REAL_FEATURE_STORE_DB.exists():
        after = _sha256(REAL_FEATURE_STORE_DB)
        assert before == after, (
            "the real local feature_store.db changed during this test module's run -- "
            "tests must operate ONLY on tmp_path copies"
        )


@pytest.mark.skipif(not REAL_FEATURE_STORE_DB.exists(), reason="real feature_store.db not present (gitignored; absent in CI)")
class TestRealFeatureStoreCopy:
    """
    The real local feature_store.db was migrated for real during this
    milestone's own verification pass (`python
    migrate_bootstrap_feature_receipts.py --commit`, executed directly
    against it, per the roadmap's own WBS 3.8b exit criterion: "17
    receipts written ... FS-GAP-004 closed in both stores" -- unlike WBS
    3.8a, whose own deliverable was a live emitter with no backfill
    mandate, this milestone's entire point IS the real backfill). A
    timestamped backup was taken automatically before that write
    (`feature_store.pre-feature-receipts-migration.<ts>.db`, alongside
    the real file). These tests therefore assert the real file's
    POST-migration state, not a "would write" premise -- mirroring
    test_migrate_receipt_attestation_basis.py's own real-ledger tier
    (`test_all_26_real_receipts_have_null_attestation_basis`,
    `test_chain_still_verifies_on_the_real_ledger`), which also asserts
    an already-migrated real file's state rather than a hypothetical one.
    """

    def test_dry_run_against_a_copy_reports_zero_pending(self, tmp_path, capsys):
        copy = tmp_path / "feature_store.db"
        shutil.copy2(REAL_FEATURE_STORE_DB, copy)
        _assert_not_real_feature_store(copy)

        before_hash = _sha256(copy)
        rc = mbfr.main(["--db", str(copy)])
        assert rc == 0
        assert _sha256(copy) == before_hash  # dry run touches nothing
        out = capsys.readouterr().out
        assert "0 Feature Specifications need a bootstrap receipt" in out

    def test_idempotent_commit_against_a_copy_writes_nothing_new(self, tmp_path):
        copy = tmp_path / "feature_store.db"
        shutil.copy2(REAL_FEATURE_STORE_DB, copy)
        _assert_not_real_feature_store(copy)

        rc = mbfr.main(["--db", str(copy), "--commit"])
        assert rc == 0
        assert len(rl.get_all_receipts(copy)) == 17
        assert list(copy.parent.glob("*.pre-feature-receipts-migration.*.db")) == []

    def test_real_feature_store_db_has_exactly_17_bootstrap_receipts(self):
        rows = rl.get_all_receipts(REAL_FEATURE_STORE_DB)
        assert len(rows) == 17
        assert all(r["decision_type"] == "feature.register" for r in rows)
        assert all(r["subject_kind"] == "feature" for r in rows)
        assert all(r["body"]["attestation"] == "bootstrap" for r in rows)
        assert all(r["body"]["verdict"]["result"] == "registered" for r in rows)

    def test_real_feature_store_db_chain_is_valid(self):
        result = rl.verify_chain(REAL_FEATURE_STORE_DB)
        assert result.ok is True
        assert result.n_receipts == 17

    def test_real_receipts_replay_refuses_as_bootstrap(self):
        for row in rl.get_all_receipts(REAL_FEATURE_STORE_DB):
            result = receipts.replay(row["body"])
            assert result.ok is False
            assert result.mode == "refused"

    def test_a_pre_migration_backup_was_taken(self):
        backups = list(REAL_FEATURE_STORE_DB.parent.glob("feature_store.pre-feature-receipts-migration.*.db"))
        assert len(backups) >= 1
