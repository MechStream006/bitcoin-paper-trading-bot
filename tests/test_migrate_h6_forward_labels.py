"""
Tests for research/label_store/migrate_h6_forward_labels.py (Phase 3 Label
Store roadmap v1.1.0, WBS 3.4 -- the fourth Label Store milestone, and the
first to touch forward-tier data).

Uses a SYNTHETIC, hand-built H6-shaped fixture database (trade_entries +
trade_labels, matching research/forward_evidence/schema.sql's real column
set) -- never the real local research/data/forward_evidence.db, so these
tests are hermetic and reproducible in CI, where that file does not exist
(gitignored). Mirrors tests/test_migration_harness.py's identical
synthetic-fixture discipline for the same reason.

Pure stdlib + registry.py/value_store.py -- no pandas dependency, runs in
any environment.
"""
from __future__ import annotations

import ast
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_LABEL_STORE_DIR = _REPO_ROOT / "research" / "label_store"

_reg_spec = importlib.util.spec_from_file_location("label_store_registry", _LABEL_STORE_DIR / "registry.py")
reg = importlib.util.module_from_spec(_reg_spec)
sys.modules["label_store_registry"] = reg
_reg_spec.loader.exec_module(reg)

_vs_spec = importlib.util.spec_from_file_location("label_store_value_store", _LABEL_STORE_DIR / "value_store.py")
vs = importlib.util.module_from_spec(_vs_spec)
sys.modules["label_store_value_store"] = vs
_vs_spec.loader.exec_module(vs)

_mig_spec = importlib.util.spec_from_file_location(
    "migrate_h6_forward_labels", _LABEL_STORE_DIR / "migrate_h6_forward_labels.py"
)
migrate = importlib.util.module_from_spec(_mig_spec)
sys.modules["migrate_h6_forward_labels"] = migrate
_mig_spec.loader.exec_module(migrate)

NOW = "2026-08-05T00:00:00+00:00"

# Two trades, four horizons each, all matured -- small but structurally
# faithful (real epoch-ms UTC timestamps, real horizon set).
_ENTRY_DAY_TS_1 = 1749513600000   # 2025-06-10T00:00:00Z
_ENTRY_DAY_TS_2 = 1750118400000   # 2025-06-17T00:00:00Z
_DAY_MS = 86_400_000


def _h6_fixture(path: Path, *, include_unmatured: bool = False) -> Path:
    """Builds a synthetic H6-shaped fixture: trade_entries + trade_labels
    only (the two tables migrate_h6_forward_labels.py actually reads),
    matching schema.sql's real column names and types."""
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE trade_entries (
            trade_id             INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_day_ts         INTEGER NOT NULL,
            entry_price          REAL NOT NULL,
            direction             INTEGER NOT NULL DEFAULT 1,
            planned_exit_day_ts INTEGER NOT NULL,
            decision_hash         TEXT NOT NULL,
            vol_regime_entry     TEXT NOT NULL,
            funding_regime_entry TEXT NOT NULL,
            market_regime_entry TEXT NOT NULL,
            created_at             INTEGER NOT NULL
        );
        CREATE TABLE trade_labels (
            trade_id        INTEGER NOT NULL,
            horizon_days    INTEGER NOT NULL,
            label_day_ts    INTEGER NOT NULL,
            forward_return  REAL,
            label_available INTEGER NOT NULL DEFAULT 0,
            labeled_at      INTEGER,
            PRIMARY KEY (trade_id, horizon_days)
        );
    """)
    for trade_id, entry_ts, entry_price in (
        (1, _ENTRY_DAY_TS_1, 65000.0),
        (2, _ENTRY_DAY_TS_2, 67000.0),
    ):
        con.execute(
            "INSERT INTO trade_entries (trade_id, entry_day_ts, entry_price, "
            "planned_exit_day_ts, decision_hash, vol_regime_entry, "
            "funding_regime_entry, market_regime_entry, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (trade_id, entry_ts, entry_price, entry_ts + 7 * _DAY_MS,
             "deadbeef", "medium", "medium", "bull", entry_ts),
        )
        for horizon in (5, 7, 14, 30):
            label_ts = entry_ts + horizon * _DAY_MS
            forward_return = 0.01 * horizon
            con.execute(
                "INSERT INTO trade_labels "
                "(trade_id, horizon_days, label_day_ts, forward_return, "
                " label_available, labeled_at) VALUES (?,?,?,?,?,?)",
                (trade_id, horizon, label_ts, forward_return, 1, label_ts + 3_600_000),
            )
    if include_unmatured:
        con.execute(
            "INSERT INTO trade_entries (trade_id, entry_day_ts, entry_price, "
            "planned_exit_day_ts, decision_hash, vol_regime_entry, "
            "funding_regime_entry, market_regime_entry, created_at) "
            "VALUES (3, ?, 70000.0, ?, 'cafebabe', 'low', 'low', 'sideways', ?)",
            (_ENTRY_DAY_TS_2, _ENTRY_DAY_TS_2 + 7 * _DAY_MS, _ENTRY_DAY_TS_2),
        )
        con.execute(
            "INSERT INTO trade_labels (trade_id, horizon_days, label_day_ts, "
            "forward_return, label_available, labeled_at) "
            "VALUES (3, 5, ?, NULL, 0, NULL)",
            (_ENTRY_DAY_TS_2 + 5 * _DAY_MS,),
        )
    con.commit()
    con.close()
    return path


def _register_ret_fwd1_definition(label_store_db: Path) -> None:
    """Mirrors register_conventions.py's own registration of
    spot_one_day_forward_return -- migrate_h6_forward_labels.py requires
    this Definition to already exist (WBS 3.3 must precede WBS 3.4)."""
    reg.init_db(label_store_db)
    reg.register_definition(
        label_store_db, "spot_one_day_forward_return",
        "rationale", "interpretation", "register_conventions", NOW,
    )


@pytest.fixture
def h6_db(tmp_path) -> Path:
    return _h6_fixture(tmp_path / "forward_evidence.db")


@pytest.fixture
def label_store_db(tmp_path) -> Path:
    p = tmp_path / "label_store.db"
    _register_ret_fwd1_definition(p)
    return p


# ---------------------------------------------------------------------------
# plan() / dry run
# ---------------------------------------------------------------------------

class TestPlan:
    def test_plan_reflects_fixture_content(self, h6_db):
        p = migrate.plan(h6_db)
        assert p["n_labels"] == 8   # 2 trades x 4 horizons
        assert p["n_trades"] == 2
        assert p["horizons"] == [5, 7, 14, 30]

    def test_dry_run_writes_nothing(self, h6_db, label_store_db):
        result = migrate.main(["--h6-db", str(h6_db), "--label-store-db", str(label_store_db)])
        assert result == 0
        con = sqlite3.connect(label_store_db)
        # Dry run never calls run()/vs.init_db() at all -- the forward-tier
        # table does not even exist yet, the strongest possible proof that
        # nothing was written.
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "label_value_forward" not in tables
        # Only the pre-registered Definition exists, no Specifications yet.
        assert con.execute("SELECT COUNT(*) FROM label_specifications").fetchone()[0] == 0

    def test_missing_h6_source_is_a_hard_error(self, tmp_path, label_store_db):
        result = migrate.main([
            "--h6-db", str(tmp_path / "does-not-exist.db"),
            "--label-store-db", str(label_store_db),
        ])
        assert result == 1


# ---------------------------------------------------------------------------
# Specification registration -- four horizons, Rehorizon under one Definition
# ---------------------------------------------------------------------------

class TestSpecificationRegistration:
    def test_fails_closed_if_ret_fwd1_definition_missing(self, h6_db, tmp_path):
        empty_label_store = tmp_path / "empty_label_store.db"
        with pytest.raises(RuntimeError, match="spot_one_day_forward_return"):
            migrate.run(h6_db, empty_label_store)

    def test_four_horizons_produce_four_distinct_specifications(self, h6_db, label_store_db):
        result = migrate.run(h6_db, label_store_db)
        hashes = result["spec_hashes"]
        assert set(hashes.keys()) == {5, 7, 14, 30}
        assert len(set(hashes.values())) == 4

    def test_all_four_specifications_share_the_ret_fwd1_definition(self, h6_db, label_store_db):
        migrate.run(h6_db, label_store_db)
        con = sqlite3.connect(label_store_db)
        con.row_factory = sqlite3.Row
        defs = {r["definition_name"] for r in con.execute("SELECT DISTINCT definition_name FROM label_specifications")}
        assert defs == {"spot_one_day_forward_return"}

    def test_registering_twice_does_not_create_a_second_definition_version(self, h6_db, label_store_db):
        migrate.run(h6_db, label_store_db)
        migrate.run(h6_db, label_store_db)
        con = sqlite3.connect(label_store_db)
        n = con.execute(
            "SELECT COUNT(*) FROM label_definitions WHERE name = 'spot_one_day_forward_return'"
        ).fetchone()[0]
        assert n == 1


# ---------------------------------------------------------------------------
# Wrapping -- declare -> observe -> mature, true historical instants
# ---------------------------------------------------------------------------

class TestWrapping:
    def test_commit_wraps_all_matured_labels(self, h6_db, label_store_db):
        result = migrate.run(h6_db, label_store_db)
        assert result["wrapped"] == 8
        assert result["skipped_already_present"] == 0
        con = sqlite3.connect(label_store_db)
        assert con.execute("SELECT COUNT(*) FROM label_value_forward").fetchone()[0] == 8

    def test_wrapped_row_uses_true_historical_declared_at_not_now(self, h6_db, label_store_db):
        migrate.run(h6_db, label_store_db)
        expected_declared_at = migrate._epoch_ms_to_iso(_ENTRY_DAY_TS_1)
        label = vs.get_forward_label(
            label_store_db,
            migrate._register_horizon_specs(label_store_db)[5],
            "h6-trade-1", expected_declared_at,
        )
        assert label is not None
        assert label["declared_at"] == expected_declared_at
        assert label["maturity_state"] == "Matured"

    def test_wrapped_row_outcome_matches_h6_forward_return(self, h6_db, label_store_db):
        migrate.run(h6_db, label_store_db)
        spec_hash = migrate._register_horizon_specs(label_store_db)[5]
        declared_at = migrate._epoch_ms_to_iso(_ENTRY_DAY_TS_1)
        label = vs.get_forward_label(label_store_db, spec_hash, "h6-trade-1", declared_at)
        assert label["outcome"] == {"forward_return": 0.05}   # 0.01 * horizon(5)

    def test_matured_at_uses_true_historical_labeled_at(self, h6_db, label_store_db):
        migrate.run(h6_db, label_store_db)
        spec_hash = migrate._register_horizon_specs(label_store_db)[5]
        declared_at = migrate._epoch_ms_to_iso(_ENTRY_DAY_TS_1)
        label = vs.get_forward_label(label_store_db, spec_hash, "h6-trade-1", declared_at)
        expected_matured_at = migrate._epoch_ms_to_iso(_ENTRY_DAY_TS_1 + 5 * _DAY_MS + 3_600_000)
        assert label["matured_at"] == expected_matured_at

    def test_every_wrapped_label_carries_the_bootstrap_marker(self, h6_db, label_store_db):
        migrate.run(h6_db, label_store_db)
        con = sqlite3.connect(label_store_db)
        declared_by_values = {r[0] for r in con.execute("SELECT DISTINCT declared_by FROM label_value_forward")}
        assert declared_by_values == {migrate.BOOTSTRAP_DECLARED_BY}
        assert all(migrate.BOOTSTRAP_MARKER in v for v in declared_by_values)

    def test_unmatured_label_is_not_wrapped(self, tmp_path, label_store_db):
        h6_with_unmatured = _h6_fixture(tmp_path / "forward_evidence_unmatured.db", include_unmatured=True)
        result = migrate.run(h6_with_unmatured, label_store_db)
        assert result["wrapped"] == 8   # still only the 8 matured rows
        con = sqlite3.connect(label_store_db)
        n = con.execute(
            "SELECT COUNT(*) FROM label_value_forward WHERE subject_id = 'h6-trade-3'"
        ).fetchone()[0]
        assert n == 0


# ---------------------------------------------------------------------------
# Idempotency (DBV2/DBV3) -- three consecutive --commit runs
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_three_consecutive_commit_runs_converge(self, h6_db, label_store_db):
        r1 = migrate.run(h6_db, label_store_db)
        r2 = migrate.run(h6_db, label_store_db)
        r3 = migrate.run(h6_db, label_store_db)

        assert r1["wrapped"] == 8 and r1["skipped_already_present"] == 0
        assert r2["wrapped"] == 0 and r2["skipped_already_present"] == 8
        assert r3["wrapped"] == 0 and r3["skipped_already_present"] == 8

        assert r1["spec_hashes"] == r2["spec_hashes"] == r3["spec_hashes"]

        con = sqlite3.connect(label_store_db)
        assert con.execute("SELECT COUNT(*) FROM label_value_forward").fetchone()[0] == 8
        assert con.execute("SELECT COUNT(*) FROM label_specifications").fetchone()[0] == 4

    def test_second_run_does_not_raise_on_the_primary_key(self, h6_db, label_store_db):
        migrate.run(h6_db, label_store_db)
        migrate.run(h6_db, label_store_db)   # must not raise sqlite3.IntegrityError


# ---------------------------------------------------------------------------
# Zero mutation of H6 (read-only enforcement)
# ---------------------------------------------------------------------------

class TestReadOnlyAgainstH6:
    def test_h6_source_hash_unchanged_after_commit(self, h6_db, label_store_db):
        import hashlib
        before = hashlib.sha256(h6_db.read_bytes()).hexdigest()
        migrate.run(h6_db, label_store_db)
        after = hashlib.sha256(h6_db.read_bytes()).hexdigest()
        assert before == after

    def test_readonly_connection_refuses_a_write_attempt(self, h6_db):
        con = migrate._open_h6_readonly(h6_db)
        with pytest.raises(sqlite3.OperationalError):
            con.execute("INSERT INTO trade_entries (entry_day_ts, entry_price, "
                        "planned_exit_day_ts, decision_hash, vol_regime_entry, "
                        "funding_regime_entry, market_regime_entry, created_at) "
                        "VALUES (0,0,0,'x','x','x','x',0)")
        con.close()


# ---------------------------------------------------------------------------
# LR2 / "no label may be assigned Forward evidence" -- structural proof
# ---------------------------------------------------------------------------

class TestNoForwardEvidenceCapability:
    def test_script_never_writes_forward_evidence_or_promotion_evidence(self):
        source = (_LABEL_STORE_DIR / "migrate_h6_forward_labels.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        docstring_end = tree.body[0].end_lineno if (
            tree.body and isinstance(tree.body[0], ast.Expr) and isinstance(tree.body[0].value, ast.Constant)
        ) else 0
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if getattr(node, "lineno", 0) <= docstring_end:
                    continue  # the module docstring discusses these terms in prose
                assert "Forward evidence" not in node.value
                assert "Promotion evidence" not in node.value

    def test_label_value_forward_has_no_standing_column(self, label_store_db):
        con = sqlite3.connect(label_store_db)
        columns = {row[1] for row in con.execute("PRAGMA table_info(label_value_forward)")}
        assert "standing" not in columns

    def test_no_datetime_now_anywhere_in_the_migration(self):
        # Importing the datetime CLASS (for fromtimestamp()) is fine and
        # expected; calling .now() anywhere is not (LR5, injected-clock
        # discipline -- every timestamp here must derive from H6's own
        # stored epoch-ms values, never the wall clock).
        source = _LABEL_STORE_DIR / "migrate_h6_forward_labels.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "now":
                pytest.fail(f"found a '.now' attribute access at line {node.lineno}")
