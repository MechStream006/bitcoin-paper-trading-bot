"""
Tests for research/label_store/standing.py (Phase 3 Label Store roadmap
v1.1.0, WBS 3.5 -- the fifth Label Store milestone).

Per WBS 3.5's own exit criterion: "Standing resolved per-signal via
pre_reg_guard.py across all four boundaries; an adversarial test proves
standing cannot be changed after declaration (LG3); two consumers cannot
reach different conclusions."

Pure stdlib + registry.py/value_store.py -- no pandas dependency, runs in
any environment.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_LABEL_STORE_DIR = _REPO_ROOT / "research" / "label_store"

_vs_spec = importlib.util.spec_from_file_location("label_store_value_store", _LABEL_STORE_DIR / "value_store.py")
vs = importlib.util.module_from_spec(_vs_spec)
sys.modules["label_store_value_store"] = vs
_vs_spec.loader.exec_module(vs)

_standing_spec = importlib.util.spec_from_file_location("label_store_standing", _LABEL_STORE_DIR / "standing.py")
standing = importlib.util.module_from_spec(_standing_spec)
sys.modules["label_store_standing"] = standing
_standing_spec.loader.exec_module(standing)

SPEC_HASH = "deadbeef" * 8

# H6's real pre-registration boundary, verified this session against the
# real research/forward_evidence/pre_registration.json file.
H6_BOUNDARY = "2026-06-10T16:50:41.111245+00:00"
BEFORE_H6_BOUNDARY = "2026-06-01T00:00:00+00:00"
AFTER_H6_BOUNDARY = "2026-06-15T00:00:00+00:00"


@pytest.fixture
def db(tmp_path) -> Path:
    p = tmp_path / "label_store.db"
    vs.init_db(p)
    return p


# ---------------------------------------------------------------------------
# resolve_signal_key -- subject_id prefix convention
# ---------------------------------------------------------------------------

class TestResolveSignalKey:
    def test_h6_prefix_resolves_to_h6(self):
        assert standing.resolve_signal_key("h6-trade-1") == "h6"
        assert standing.resolve_signal_key("h6-trade-9999") == "h6"

    def test_unknown_prefix_raises(self):
        with pytest.raises(standing.UnknownSubjectError):
            standing.resolve_signal_key("g1-trade-1")

    def test_unknown_prefix_does_not_guess_a_default(self):
        with pytest.raises(standing.UnknownSubjectError, match="refusing to guess"):
            standing.resolve_signal_key("totally-unrecognised-subject")


# ---------------------------------------------------------------------------
# resolve_standing -- pure, deterministic resolution
# ---------------------------------------------------------------------------

class TestResolveStanding:
    def test_bootstrap_marker_always_yields_context_regardless_of_date(self):
        # Even a date AFTER the boundary must still resolve to Context
        # under the bootstrap override -- the whole point of the override.
        result = standing.resolve_standing(
            "h6-trade-1", AFTER_H6_BOUNDARY,
            "migrate_h6_forward_labels.py (bootstrap, migrated 2026-08-06)",
        )
        assert result == "Context"

    def test_live_declaration_before_boundary_is_context(self):
        result = standing.resolve_standing("h6-trade-1", BEFORE_H6_BOUNDARY, "live-test-declaration")
        assert result == "Context"

    def test_live_declaration_after_boundary_is_forward_evidence(self):
        result = standing.resolve_standing("h6-trade-1", AFTER_H6_BOUNDARY, "live-test-declaration")
        assert result == "Forward evidence"

    def test_never_returns_promotion_evidence(self):
        for declared_at in (BEFORE_H6_BOUNDARY, AFTER_H6_BOUNDARY):
            assert standing.resolve_standing("h6-trade-1", declared_at, "live-test-declaration") != "Promotion evidence"

    def test_deterministic_across_repeated_calls(self):
        # "Two consumers cannot reach different conclusions" (§10.1) --
        # the resolution function itself, called independently, always
        # agrees with itself.
        results = {
            standing.resolve_standing("h6-trade-1", AFTER_H6_BOUNDARY, "live-test-declaration")
            for _ in range(5)
        }
        assert results == {"Forward evidence"}

    def test_unknown_subject_with_non_bootstrap_declared_by_raises(self):
        with pytest.raises(standing.UnknownSubjectError):
            standing.resolve_standing("mystery-trade-1", AFTER_H6_BOUNDARY, "live-test-declaration")


# ---------------------------------------------------------------------------
# declare_with_resolved_standing -- the live-declaration entry point
# ---------------------------------------------------------------------------

class TestDeclareWithResolvedStanding:
    def test_sets_standing_inline_at_declaration(self, db):
        result = standing.declare_with_resolved_standing(
            db, SPEC_HASH, "h6-trade-1", AFTER_H6_BOUNDARY, "live-test-declaration",
        )
        assert result == "Forward evidence"
        label = vs.get_forward_label(db, SPEC_HASH, "h6-trade-1", AFTER_H6_BOUNDARY)
        assert label["standing"] == "Forward evidence"

    def test_bootstrap_declared_by_yields_context_inline(self, db):
        standing.declare_with_resolved_standing(
            db, SPEC_HASH, "h6-trade-1", AFTER_H6_BOUNDARY,
            "some-script.py (bootstrap, migrated 2026-08-06)",
        )
        label = vs.get_forward_label(db, SPEC_HASH, "h6-trade-1", AFTER_H6_BOUNDARY)
        assert label["standing"] == "Context"


# ---------------------------------------------------------------------------
# value_store.py backward compatibility -- WBS 3.2/3.4 callers unaffected
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    def test_declare_forward_label_without_standing_kwarg_still_works(self, db):
        vs.declare_forward_label(db, SPEC_HASH, "h6-trade-1", H6_BOUNDARY, declared_by="wbs-3.4-style-call")
        label = vs.get_forward_label(db, SPEC_HASH, "h6-trade-1", H6_BOUNDARY)
        assert label["standing"] is None
        assert label["maturity_state"] == "Declared"

    def test_declare_forward_label_rejects_invalid_standing(self, db):
        with pytest.raises(vs.LabelValueStoreError):
            vs.declare_forward_label(
                db, SPEC_HASH, "h6-trade-1", H6_BOUNDARY,
                declared_by="x", standing="not-a-real-standing-value",
            )


# ---------------------------------------------------------------------------
# set_standing -- the one-time bootstrap backfill primitive
# ---------------------------------------------------------------------------

class TestSetStanding:
    def test_backfills_null_standing(self, db):
        vs.declare_forward_label(db, SPEC_HASH, "h6-trade-1", H6_BOUNDARY, declared_by="bootstrap-style")
        vs.set_standing(db, SPEC_HASH, "h6-trade-1", H6_BOUNDARY, "Context")
        label = vs.get_forward_label(db, SPEC_HASH, "h6-trade-1", H6_BOUNDARY)
        assert label["standing"] == "Context"

    def test_refuses_when_already_set(self, db):
        vs.declare_forward_label(db, SPEC_HASH, "h6-trade-1", H6_BOUNDARY, declared_by="x")
        vs.set_standing(db, SPEC_HASH, "h6-trade-1", H6_BOUNDARY, "Context")
        with pytest.raises(vs.LabelTransitionError, match="immutable"):
            vs.set_standing(db, SPEC_HASH, "h6-trade-1", H6_BOUNDARY, "Forward evidence")

    def test_refuses_for_nonexistent_label(self, db):
        with pytest.raises(vs.LabelValueStoreError):
            vs.set_standing(db, SPEC_HASH, "h6-trade-999", H6_BOUNDARY, "Context")

    def test_rejects_invalid_standing_value(self, db):
        vs.declare_forward_label(db, SPEC_HASH, "h6-trade-1", H6_BOUNDARY, declared_by="x")
        with pytest.raises(vs.LabelValueStoreError):
            vs.set_standing(db, SPEC_HASH, "h6-trade-1", H6_BOUNDARY, "not-a-real-value")


# ---------------------------------------------------------------------------
# LG3 adversarial storage-layer test -- the milestone's central claim
# ---------------------------------------------------------------------------

class TestStorageLayerImmutability:
    def test_direct_sql_cannot_rewrite_standing_once_set(self, db):
        vs.declare_forward_label(db, SPEC_HASH, "h6-trade-1", H6_BOUNDARY, declared_by="x")
        vs.set_standing(db, SPEC_HASH, "h6-trade-1", H6_BOUNDARY, "Context")

        con = sqlite3.connect(db)
        with pytest.raises(sqlite3.IntegrityError, match="LABEL_TAMPER"):
            con.execute(
                "UPDATE label_value_forward SET standing = 'Forward evidence' "
                "WHERE spec_hash = ? AND subject_id = ? AND declared_at = ?",
                (SPEC_HASH, "h6-trade-1", H6_BOUNDARY),
            )
        con.close()
        # The original must be completely intact.
        label = vs.get_forward_label(db, SPEC_HASH, "h6-trade-1", H6_BOUNDARY)
        assert label["standing"] == "Context"

    def test_direct_sql_cannot_set_an_invalid_standing_value(self, db):
        vs.declare_forward_label(db, SPEC_HASH, "h6-trade-1", H6_BOUNDARY, declared_by="x")
        con = sqlite3.connect(db)
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "UPDATE label_value_forward SET standing = 'not-a-real-value' "
                "WHERE spec_hash = ? AND subject_id = ? AND declared_at = ?",
                (SPEC_HASH, "h6-trade-1", H6_BOUNDARY),
            )
        con.close()

    def test_two_reads_of_the_same_row_always_agree(self, db):
        # "Two consumers cannot reach different conclusions" (§10.1) --
        # standing is STORED, not re-derived, so two independent
        # connections reading it always see the same value.
        vs.declare_forward_label(db, SPEC_HASH, "h6-trade-1", H6_BOUNDARY, declared_by="x")
        vs.set_standing(db, SPEC_HASH, "h6-trade-1", H6_BOUNDARY, "Forward evidence")

        con_a = sqlite3.connect(db)
        con_b = sqlite3.connect(db)
        try:
            a = con_a.execute(
                "SELECT standing FROM label_value_forward WHERE spec_hash=? AND subject_id=? AND declared_at=?",
                (SPEC_HASH, "h6-trade-1", H6_BOUNDARY),
            ).fetchone()[0]
            b = con_b.execute(
                "SELECT standing FROM label_value_forward WHERE spec_hash=? AND subject_id=? AND declared_at=?",
                (SPEC_HASH, "h6-trade-1", H6_BOUNDARY),
            ).fetchone()[0]
        finally:
            con_a.close()
            con_b.close()
        assert a == b == "Forward evidence"


# ---------------------------------------------------------------------------
# Migration -- init_db() adds `standing` to a pre-existing (WBS 3.4-shaped) table
# ---------------------------------------------------------------------------

class TestSchemaMigration:
    def test_preexisting_table_without_standing_column_gets_migrated(self, tmp_path):
        p = tmp_path / "pre_existing_label_store.db"
        con = sqlite3.connect(p)
        # WBS 3.4-shaped table: every column value_store.py had BEFORE
        # this milestone, no `standing` column at all.
        con.executescript("""
            CREATE TABLE label_value_forward (
                spec_hash TEXT NOT NULL, subject_id TEXT NOT NULL,
                declared_at TEXT NOT NULL, declared_by TEXT NOT NULL,
                maturity_state TEXT NOT NULL, observed_at TEXT,
                outcome_json TEXT, input_revision_set_json TEXT,
                matured_at TEXT, flagged_at TEXT, flag_reason TEXT,
                adjudicated_at TEXT, adjudication_decision TEXT,
                void_at TEXT, void_reason TEXT,
                PRIMARY KEY (spec_hash, subject_id, declared_at)
            );
        """)
        con.execute(
            "INSERT INTO label_value_forward "
            "(spec_hash, subject_id, declared_at, declared_by, maturity_state, "
            " outcome_json, matured_at) VALUES (?,?,?,?,?,?,?)",
            (SPEC_HASH, "h6-trade-1", H6_BOUNDARY, "migrate_h6_forward_labels.py (bootstrap)",
             "Matured", '{"forward_return": 0.05}', H6_BOUNDARY),
        )
        con.commit()
        con.close()

        vs.init_db(p)   # the migration under test

        con = sqlite3.connect(p)
        columns = {row[1] for row in con.execute("PRAGMA table_info(label_value_forward)")}
        assert "standing" in columns
        row = con.execute(
            "SELECT standing, maturity_state, outcome_json FROM label_value_forward "
            "WHERE subject_id = 'h6-trade-1'"
        ).fetchone()
        con.close()
        assert row[0] is None                          # new column, NULL for existing rows
        assert row[1] == "Matured"                      # existing data untouched
        assert row[2] == '{"forward_return": 0.05}'    # existing data untouched

    def test_migration_is_idempotent(self, tmp_path):
        p = tmp_path / "twice.db"
        vs.init_db(p)
        vs.init_db(p)   # must not raise (ALTER TABLE only fires once)
        con = sqlite3.connect(p)
        columns = [row[1] for row in con.execute("PRAGMA table_info(label_value_forward)")]
        con.close()
        assert columns.count("standing") == 1


# ---------------------------------------------------------------------------
# Backfill -- plan / dry-run / --commit / idempotency
# ---------------------------------------------------------------------------

class TestBackfill:
    def _declare_three_bootstrap_rows(self, db):
        for i in (1, 2, 3):
            vs.declare_forward_label(
                db, SPEC_HASH, f"h6-trade-{i}", H6_BOUNDARY,
                declared_by="migrate_h6_forward_labels.py (bootstrap, migrated 2026-08-06)",
            )

    def test_plan_backfill_reflects_null_standing_rows(self, db):
        self._declare_three_bootstrap_rows(db)
        plan = standing.plan_backfill(db)
        assert len(plan) == 3
        assert all(item["resolved_standing"] == "Context" for item in plan)

    def test_dry_run_writes_nothing(self, db):
        self._declare_three_bootstrap_rows(db)
        result = standing.backfill_bootstrap_standing(db, commit=False)
        assert result == {"would_assign": 3, "assigned": 0, "commit": False}
        con = sqlite3.connect(db)
        n = con.execute("SELECT COUNT(*) FROM label_value_forward WHERE standing IS NOT NULL").fetchone()[0]
        con.close()
        assert n == 0

    def test_commit_assigns_standing_to_all_null_rows(self, db):
        self._declare_three_bootstrap_rows(db)
        result = standing.backfill_bootstrap_standing(db, commit=True)
        assert result == {"would_assign": 3, "assigned": 3, "commit": True}
        con = sqlite3.connect(db)
        rows = con.execute("SELECT standing FROM label_value_forward").fetchall()
        con.close()
        assert all(r[0] == "Context" for r in rows)

    def test_second_commit_run_is_idempotent(self, db):
        self._declare_three_bootstrap_rows(db)
        standing.backfill_bootstrap_standing(db, commit=True)
        second = standing.backfill_bootstrap_standing(db, commit=True)
        assert second == {"would_assign": 0, "assigned": 0, "commit": True}

    def test_cli_dry_run_then_commit(self, db, capsys):
        self._declare_three_bootstrap_rows(db)
        rc = standing.main(["--label-store-db", str(db)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "3 forward-tier row(s)" in out
        assert "Dry run only" in out

        rc = standing.main(["--label-store-db", str(db), "--commit"])
        assert rc == 0
        con = sqlite3.connect(db)
        n = con.execute("SELECT COUNT(*) FROM label_value_forward WHERE standing = 'Context'").fetchone()[0]
        con.close()
        assert n == 3
