"""
Tests for research/label_store/migrate_bootstrap_label_receipts.py
(Phase 3 Label Store roadmap v1.1.0, WBS 3.8c -- `LS-GAP-001` closure,
`ADR-I011` LG1 and §9's "registration receipt" Specification-metadata
requirement).

Mirrors tests/test_migrate_bootstrap_feature_receipts.py's conventions: a
synthetic tmp_path fixture for the plan/apply/CLI mechanics, plus a
real-data tier guarded by an autouse module-scoped fixture that proves the
real local label_store.db is byte-unchanged by every test in this file.

THE CENTRAL SAFETY TEST is TestDryRunWritesNothing: WBS 3.8b's own first
draft silently created an empty governance_receipts table on a real
database during what was documented as a read-only dry run. label_store.db
is the identical exposure (it has no such table), so that defect is
regression-tested here rather than merely avoided by inspection.
"""
from __future__ import annotations

import hashlib
import importlib.util
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_LABEL_STORE_DIR = _REPO_ROOT / "research" / "label_store"
_EVIDENCE_LIBRARY_DIR = _REPO_ROOT / "research" / "evidence_library"
REAL_LABEL_STORE_DB = _LABEL_STORE_DIR / "label_store.db"


def _load(module_file: Path, registered_name: str):
    if registered_name in sys.modules:
        return sys.modules[registered_name]
    spec = importlib.util.spec_from_file_location(registered_name, module_file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[registered_name] = mod
    spec.loader.exec_module(mod)
    return mod


reg = _load(_LABEL_STORE_DIR / "registry.py", "label_store_registry")
lr = _load(_LABEL_STORE_DIR / "label_receipts.py", "label_store_label_receipts")
mblr = _load(_LABEL_STORE_DIR / "migrate_bootstrap_label_receipts.py", "label_store_migrate_bootstrap_label_receipts")

if str(_EVIDENCE_LIBRARY_DIR) not in sys.path:
    sys.path.insert(0, str(_EVIDENCE_LIBRARY_DIR))
import receipt_ledger as rl  # noqa: E402
import receipts  # noqa: E402

NOW = "2026-08-06T00:00:00+00:00"


def _register_spec(db_path, *, horizon_days=5, binding_id="test.binding", definition_name="test_outcome"):
    """Registers via the BARE registry (no receipt) -- reproducing exactly
    the pre-3.8a condition this migration exists to backfill."""
    existing = reg.get_latest_definition(db_path, definition_name)
    if existing is None:
        d = reg.register_definition(
            db_path, name=definition_name, rationale="r" * 40,
            interpretation="i" * 40, actor="rohith", registered_at=NOW,
        )
        version = d.version
    else:
        version = existing["version"]
    return reg.register_specification(
        db_path,
        definition_name=definition_name, definition_version=version,
        horizon_days=horizon_days, horizon_governance="calendar_span",
        fill_convention="f" * 20, cost_convention="c" * 20,
        direction_convention="d" * 20, maturity_rule="m" * 20,
        validity_condition="v" * 20, conditioning_propagation="preserving",
        binding_id=binding_id, engine_version="v1",
        actor="register_conventions", registered_at=NOW,
    )


@pytest.fixture
def db_path(tmp_path) -> Path:
    p = tmp_path / "label_store.db"
    reg.init_db(p)
    return p


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# plan()
# ---------------------------------------------------------------------------

class TestPlan:
    def test_empty_database_plans_nothing(self, db_path):
        assert mblr.plan(db_path) == []

    def test_plans_every_specification_when_no_receipts_table_exists(self, db_path):
        _register_spec(db_path, horizon_days=5, binding_id="b.one")
        _register_spec(db_path, horizon_days=7, binding_id="b.two")
        assert not mblr._governance_receipts_table_exists(db_path)
        assert len(mblr.plan(db_path)) == 2

    def test_skips_specifications_that_already_have_a_receipt(self, db_path):
        h1 = _register_spec(db_path, horizon_days=5, binding_id="b.one")
        _register_spec(db_path, horizon_days=7, binding_id="b.two")
        lr.init_db(db_path)
        mblr.apply(db_path, [s for s in mblr._all_specifications(db_path) if s["spec_hash"] == h1])
        remaining = mblr.plan(db_path)
        assert len(remaining) == 1
        assert remaining[0]["spec_hash"] != h1

    def test_plan_is_empty_after_full_backfill(self, db_path):
        _register_spec(db_path, horizon_days=5, binding_id="b.one")
        _register_spec(db_path, horizon_days=7, binding_id="b.two")
        lr.init_db(db_path)
        mblr.apply(db_path, mblr.plan(db_path))
        assert mblr.plan(db_path) == []

    def test_plan_performs_no_write_of_any_kind(self, db_path):
        _register_spec(db_path)
        before = _sha256(db_path)
        mblr.plan(db_path)
        assert _sha256(db_path) == before


# ---------------------------------------------------------------------------
# apply() -- receipt shape
# ---------------------------------------------------------------------------

class TestApply:
    def test_writes_one_receipt_per_specification(self, db_path):
        _register_spec(db_path, horizon_days=5, binding_id="b.one")
        _register_spec(db_path, horizon_days=7, binding_id="b.two")
        lr.init_db(db_path)
        mblr.apply(db_path, mblr.plan(db_path))
        con = sqlite3.connect(db_path)
        n = con.execute("SELECT COUNT(*) FROM governance_receipts").fetchone()[0]
        con.close()
        assert n == 2

    def test_receipt_uses_the_reused_label_register_taxonomy(self, db_path):
        h = _register_spec(db_path)
        lr.init_db(db_path)
        mblr.apply(db_path, mblr.plan(db_path))
        r = lr.get_registration_receipt(db_path, h)
        assert r is not None
        assert r["decision_type"] == lr.DECISION_REGISTER == "label.register"
        assert r["subject_kind"] == lr.SUBJECT_KIND == "label"
        assert r["subject_id"] == h
        assert r["decision_class"] == "derived"
        assert r["action"] == "register"

    def test_receipt_is_bootstrap_attested(self, db_path):
        h = _register_spec(db_path)
        lr.init_db(db_path)
        mblr.apply(db_path, mblr.plan(db_path))
        body = lr.get_registration_receipt(db_path, h)["body"]
        assert body["attestation"] == receipts.ATTESTATION_BOOTSTRAP
        assert "not reproducible" in body["attestation_note"]

    def test_receipt_as_of_is_the_original_registration_instant(self, db_path):
        h = _register_spec(db_path)
        lr.init_db(db_path)
        mblr.apply(db_path, mblr.plan(db_path))
        assert lr.get_registration_receipt(db_path, h)["body"]["as_of"] == NOW

    def test_registered_phase_yields_registered_verdict(self, db_path):
        h = _register_spec(db_path)
        lr.init_db(db_path)
        mblr.apply(db_path, mblr.plan(db_path))
        assert lr.get_registration_receipt(db_path, h)["body"]["verdict"]["result"] == "registered"

    def test_rejected_phase_yields_rejected_verdict(self, db_path):
        """The verdict is derived from the row's real phase, never assumed."""
        h = _register_spec(db_path)
        con = sqlite3.connect(db_path)
        con.execute("UPDATE label_specifications SET phase = 'Rejected' WHERE spec_hash = ?", (h,))
        con.commit()
        con.close()
        lr.init_db(db_path)
        mblr.apply(db_path, mblr.plan(db_path))
        assert lr.get_registration_receipt(db_path, h)["body"]["verdict"]["result"] == "rejected"

    def test_no_fabricated_evaluation_is_recorded(self, db_path):
        """A bootstrap receipt must not carry a re-run Tier A evaluation --
        that would claim to attest the original decision while actually
        recording today's recomputation."""
        h = _register_spec(db_path)
        lr.init_db(db_path)
        mblr.apply(db_path, mblr.plan(db_path))
        body = lr.get_registration_receipt(db_path, h)["body"]
        assert not body.get("evaluation")
        assert body["rule"]["rule_id"] == "bootstrap"


# ---------------------------------------------------------------------------
# The WBS 3.8b regression: a dry run must write NOTHING, including schema
# ---------------------------------------------------------------------------

class TestDryRunWritesNothing:
    def test_dry_run_creates_no_governance_receipts_table(self, db_path):
        _register_spec(db_path)
        assert mblr.main(["--db", str(db_path)]) == 0
        con = sqlite3.connect(db_path)
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        con.close()
        assert "governance_receipts" not in names

    def test_dry_run_leaves_the_file_byte_identical(self, db_path):
        _register_spec(db_path)
        before = _sha256(db_path)
        assert mblr.main(["--db", str(db_path)]) == 0
        assert _sha256(db_path) == before

    def test_dry_run_writes_no_receipt_even_when_table_exists(self, db_path):
        _register_spec(db_path)
        lr.init_db(db_path)
        before = _sha256(db_path)
        assert mblr.main(["--db", str(db_path)]) == 0
        assert _sha256(db_path) == before


# ---------------------------------------------------------------------------
# LR4: refuse to write to something that is not a Label Store
# ---------------------------------------------------------------------------

class TestSchemaAssertion:
    def test_refuses_a_database_without_label_tables(self, tmp_path):
        foreign = tmp_path / "not_a_label_store.db"
        con = sqlite3.connect(foreign)
        con.execute("CREATE TABLE trade_labels (trade_id INTEGER)")
        con.commit()
        con.close()
        with pytest.raises(ValueError, match="does not look like a Label Store"):
            mblr._assert_label_store_schema(foreign)

    def test_accepts_a_real_label_store(self, db_path):
        mblr._assert_label_store_schema(db_path)


# ---------------------------------------------------------------------------
# Source tables are never touched
# ---------------------------------------------------------------------------

class TestNoMutationOfLabelStoreTables:
    def test_specifications_and_definitions_are_unchanged(self, db_path):
        _register_spec(db_path, horizon_days=5, binding_id="b.one")
        _register_spec(db_path, horizon_days=7, binding_id="b.two")
        con = sqlite3.connect(db_path)
        before_specs = con.execute("SELECT * FROM label_specifications ORDER BY spec_hash").fetchall()
        before_defs = con.execute("SELECT * FROM label_definitions ORDER BY name, version").fetchall()
        con.close()

        lr.init_db(db_path)
        mblr.apply(db_path, mblr.plan(db_path))

        con = sqlite3.connect(db_path)
        after_specs = con.execute("SELECT * FROM label_specifications ORDER BY spec_hash").fetchall()
        after_defs = con.execute("SELECT * FROM label_definitions ORDER BY name, version").fetchall()
        con.close()
        assert before_specs == after_specs
        assert before_defs == after_defs

    def test_no_specification_phase_is_changed(self, db_path):
        """WBS 3.8c must not activate anything -- phases are untouched."""
        _register_spec(db_path)
        lr.init_db(db_path)
        mblr.apply(db_path, mblr.plan(db_path))
        con = sqlite3.connect(db_path)
        phases = {r[0] for r in con.execute("SELECT phase FROM label_specifications")}
        con.close()
        assert phases == {"Registered"}

    def test_module_never_references_activation(self):
        source = (_LABEL_STORE_DIR / "migrate_bootstrap_label_receipts.py").read_text(encoding="utf-8")
        code = source.split('"""', 2)[2]  # strip the module docstring
        assert "activate_specification" not in code
        assert "_transition_phase" not in code


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_second_commit_writes_nothing_new(self, db_path):
        _register_spec(db_path, horizon_days=5, binding_id="b.one")
        _register_spec(db_path, horizon_days=7, binding_id="b.two")
        assert mblr.main(["--db", str(db_path), "--commit"]) == 0
        con = sqlite3.connect(db_path)
        first = con.execute("SELECT COUNT(*) FROM governance_receipts").fetchone()[0]
        con.close()

        assert mblr.main(["--db", str(db_path), "--commit"]) == 0
        con = sqlite3.connect(db_path)
        second = con.execute("SELECT COUNT(*) FROM governance_receipts").fetchone()[0]
        con.close()
        assert first == second == 2

    def test_second_commit_takes_no_backup(self, db_path, tmp_path):
        _register_spec(db_path)
        assert mblr.main(["--db", str(db_path), "--commit"]) == 0
        backups_after_first = list(tmp_path.glob("*pre-label-receipts-migration*"))
        assert mblr.main(["--db", str(db_path), "--commit"]) == 0
        assert list(tmp_path.glob("*pre-label-receipts-migration*")) == backups_after_first


# ---------------------------------------------------------------------------
# Chain integrity and replay
# ---------------------------------------------------------------------------

class TestChainAndReplay:
    def test_chain_is_valid_after_backfill(self, db_path):
        for i, h in enumerate((5, 7, 14, 30)):
            _register_spec(db_path, horizon_days=h, binding_id=f"b.{i}")
        assert mblr.main(["--db", str(db_path), "--commit"]) == 0
        result = rl.verify_chain(db_path)
        assert result.ok
        assert result.n_receipts == 4

    def test_every_receipt_verifies_by_hash(self, db_path):
        h = _register_spec(db_path)
        assert mblr.main(["--db", str(db_path), "--commit"]) == 0
        stored = lr.get_registration_receipt(db_path, h)
        assert receipts.verify_receipt(stored["body"], stored["receipt_id"])

    def test_replay_refuses_a_bootstrap_receipt(self, db_path):
        """`receipts.replay()` REFUSES a bootstrap receipt by returning
        ReplayResult(ok=False, mode='refused') -- it does not raise. The
        hash check still holds forever, which is the whole point of the
        bootstrap contract: verify() yes, replay() no."""
        h = _register_spec(db_path)
        assert mblr.main(["--db", str(db_path), "--commit"]) == 0
        result = lr.replay_registration_receipt(db_path, h)
        assert result.hash_ok
        assert result.replay.mode == "refused"
        assert not result.replay.ok
        assert "bootstrap" in result.replay.detail.lower()


# ---------------------------------------------------------------------------
# The requirement this milestone exists to close
# ---------------------------------------------------------------------------

class TestGovernanceValidation:
    def test_lrec1_is_reported_before_the_backfill(self, db_path):
        _register_spec(db_path, horizon_days=5, binding_id="b.one")
        _register_spec(db_path, horizon_days=7, binding_id="b.two")
        lr.init_db(db_path)
        errors = [e for e in lr.validate_label_governance(db_path) if e.startswith("LREC1")]
        assert len(errors) == 2

    def test_lrec1_is_cleared_after_the_backfill(self, db_path):
        _register_spec(db_path, horizon_days=5, binding_id="b.one")
        _register_spec(db_path, horizon_days=7, binding_id="b.two")
        assert mblr.main(["--db", str(db_path), "--commit"]) == 0
        errors = [e for e in lr.validate_label_governance(db_path) if e.startswith("LREC1")]
        assert errors == []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class TestMainCLI:
    def test_missing_database_returns_1(self, tmp_path):
        assert mblr.main(["--db", str(tmp_path / "nope.db")]) == 1

    def test_commit_on_empty_database_returns_0_and_creates_nothing(self, db_path):
        before = _sha256(db_path)
        assert mblr.main(["--db", str(db_path), "--commit"]) == 0
        assert _sha256(db_path) == before

    def test_commit_takes_a_backup(self, db_path, tmp_path):
        _register_spec(db_path)
        assert mblr.main(["--db", str(db_path), "--commit"]) == 0
        assert len(list(tmp_path.glob("*pre-label-receipts-migration*"))) == 1

    def test_dry_run_is_the_default(self, db_path):
        _register_spec(db_path)
        before = _sha256(db_path)
        assert mblr.main(["--db", str(db_path)]) == 0
        assert _sha256(db_path) == before


# ---------------------------------------------------------------------------
# Real-data tier -- the real local label_store.db, hash-guarded
# ---------------------------------------------------------------------------

def _assert_not_real_label_store(path: Path) -> None:
    assert path.resolve() != REAL_LABEL_STORE_DB.resolve(), "refusing to write to the real label_store.db"


@pytest.fixture(scope="module", autouse=True)
def _guard_real_label_store_untouched():
    """Proves every test in this file leaves the real label_store.db
    byte-identical -- the same discipline WBS 3.4-3.9 each applied."""
    if not REAL_LABEL_STORE_DB.exists():
        yield
        return
    before = _sha256(REAL_LABEL_STORE_DB)
    yield
    assert _sha256(REAL_LABEL_STORE_DB) == before, "the real label_store.db was modified by a test"


real_db = pytest.mark.skipif(
    not REAL_LABEL_STORE_DB.exists(), reason="real label_store.db not present (gitignored; absent in CI)"
)


@real_db
class TestRealLabelStore:
    def test_real_db_has_exactly_8_specifications(self):
        con = sqlite3.connect(REAL_LABEL_STORE_DB)
        n = con.execute("SELECT COUNT(*) FROM label_specifications").fetchone()[0]
        con.close()
        assert n == 8

    def test_real_db_has_a_registration_receipt_for_every_specification(self):
        errors = [e for e in lr.validate_label_governance(REAL_LABEL_STORE_DB) if e.startswith("LREC1")]
        assert errors == [], f"LS-GAP-001 not closed: {errors}"

    def test_real_db_receipts_are_all_bootstrap_label_register(self):
        con = sqlite3.connect(REAL_LABEL_STORE_DB)
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute(
            "SELECT decision_type, COUNT(*) c FROM governance_receipts GROUP BY 1"
        )]
        con.close()
        assert rows == [{"decision_type": "label.register", "c": 8}]

    def test_real_db_chain_is_valid(self):
        result = rl.verify_chain(REAL_LABEL_STORE_DB)
        assert result.ok
        assert result.n_receipts == 8

    def test_real_receipts_refuse_replay_as_bootstrap(self):
        con = sqlite3.connect(REAL_LABEL_STORE_DB)
        hashes = [r[0] for r in con.execute("SELECT spec_hash FROM label_specifications")]
        con.close()
        for h in hashes:
            result = lr.replay_registration_receipt(REAL_LABEL_STORE_DB, h)
            assert result.hash_ok, f"{h[:12]} failed hash verification"
            assert result.replay.mode == "refused"
            assert "bootstrap" in result.replay.detail.lower()

    def test_a_pre_migration_backup_exists(self):
        assert list(_LABEL_STORE_DIR.glob("label_store.pre-label-receipts-migration.*.db"))

    def test_dry_run_against_a_copy_reports_zero_pending(self, tmp_path):
        copy = tmp_path / "label_store_copy.db"
        _assert_not_real_label_store(copy)
        shutil.copy2(REAL_LABEL_STORE_DB, copy)
        assert mblr.plan(copy) == []

    def test_idempotent_commit_against_a_copy_writes_nothing_new(self, tmp_path):
        copy = tmp_path / "label_store_copy.db"
        _assert_not_real_label_store(copy)
        shutil.copy2(REAL_LABEL_STORE_DB, copy)
        before = _sha256(copy)
        assert mblr.main(["--db", str(copy), "--commit"]) == 0
        assert _sha256(copy) == before

    def test_real_specification_phases_are_untouched_by_this_milestone(self):
        """WBS 3.8c must not activate anything, and must never Reject.

        The four `Active` Specifications on the real database were set by
        WBS 3.11 (activation via behavioural equivalence), not by this
        milestone. That 3.8c itself changes no phase is proven directly by
        `test_no_specification_phase_is_changed` and by the AST guard
        `test_module_never_references_activation`; what is asserted here,
        on real data, is the invariant 3.8c genuinely preserves -- no
        Specification is ever driven to the permanent `Rejected` state."""
        con = sqlite3.connect(REAL_LABEL_STORE_DB)
        phases = {r[0] for r in con.execute("SELECT DISTINCT phase FROM label_specifications")}
        con.close()
        assert "Rejected" not in phases
        assert phases <= {"Registered", "Active"}
