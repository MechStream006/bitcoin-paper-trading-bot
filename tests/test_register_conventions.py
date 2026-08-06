"""
Tests for research/label_store/register_conventions.py (Phase 3 Label
Store roadmap v1.1.0, WBS 3.3 -- the third Label Store milestone).

Per WBS 3.3's own exit criterion: "Four Specifications registered;
C1/C2/C3 produce three distinct hashes from otherwise-identical inputs;
ret_fwd1 no longer unclaimed. No forward data touched."

TWO INVOCATION STYLES, DELIBERATELY. register_conventions.py's own
`import registry as reg` (unqualified, sys.path-inserted) is the
established migrate_*.py convention (matches
migrate_evidence_engine_features.py and its own test file exactly) and is
safe for its real, standalone `python register_conventions.py` invocation.
It is NOT safe to import in-process in the same pytest session as
research/feature_store/registry.py's tests, which bare-import a DIFFERENT
module under the identical name "registry" -- reproduced and fixed for
research/label_store/registry.py and value_store.py at WBS 3.1/3.2 (see
those modules' test files). Rather than touch register_conventions.py's
already-correct standalone-script convention, or the pre-existing
tests/test_migrate_evidence_engine_features.py (unrelated refactoring),
this file tests the script the way it is actually run -- via subprocess,
which gives every invocation its own fresh sys.modules and sidesteps the
collision entirely. The one test that needs direct, in-process access to
registry.py's content-hash function (the isolated "otherwise-identical
inputs" proof) uses the same importlib-under-a-distinct-name pattern
already established at WBS 3.1.

Pure stdlib module -- no research dependency, runs in any environment.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import sqlite3
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO_ROOT / "research" / "label_store" / "register_conventions.py"

_spec = importlib.util.spec_from_file_location(
    "label_store_registry", _REPO_ROOT / "research" / "label_store" / "registry.py",
)
reg = importlib.util.module_from_spec(_spec)
sys.modules["label_store_registry"] = reg
_spec.loader.exec_module(reg)

NOW = "2026-08-05T00:00:00+00:00"


def _run_script(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        capture_output=True, text=True, timeout=60,
    )


@pytest.fixture
def db(tmp_path) -> Path:
    return tmp_path / "label_store.db"


# ---------------------------------------------------------------------------
# Dry run -- no database written
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_dry_run_writes_nothing(self, db):
        result = _run_script("--db", str(db))
        assert result.returncode == 0
        assert not db.exists()

    def test_dry_run_lists_all_four_labels(self, db):
        result = _run_script("--db", str(db))
        for label in ("C1", "C2", "C3", "ret_fwd1"):
            assert label in result.stdout

    def test_dry_run_says_dry_run_only(self, db):
        result = _run_script("--db", str(db))
        assert "Dry run only" in result.stdout


# ---------------------------------------------------------------------------
# --commit -- real registration
# ---------------------------------------------------------------------------

class TestCommit:
    def test_commit_registers_four_specifications_under_two_definitions(self, db):
        result = _run_script("--commit", "--db", str(db))
        assert result.returncode == 0
        con = sqlite3.connect(db)
        try:
            assert con.execute("SELECT COUNT(*) FROM label_definitions").fetchone()[0] == 2
            assert con.execute("SELECT COUNT(*) FROM label_specifications").fetchone()[0] == 4
        finally:
            con.close()

    def test_commit_reports_four_distinct_hashes(self, db):
        result = _run_script("--commit", "--db", str(db))
        assert "4 specification(s) registered, 4 distinct hash(es)." in result.stdout

    def test_all_four_specifications_are_registered_phase(self, db):
        _run_script("--commit", "--db", str(db))
        con = sqlite3.connect(db)
        try:
            phases = [r[0] for r in con.execute("SELECT phase FROM label_specifications")]
        finally:
            con.close()
        assert phases == ["Registered"] * 4

    def test_c1_c2_c3_share_one_definition_ret_fwd1_has_its_own(self, db):
        _run_script("--commit", "--db", str(db))
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        try:
            rows = {
                r["binding_id"]: r["definition_name"]
                for r in con.execute("SELECT binding_id, definition_name FROM label_specifications")
            }
        finally:
            con.close()
        c1_def = rows["forward_evidence.daily_job.close_trade"]
        c2_def = rows["research_evidence.engine.net_daily"]
        c3_def = rows["forward_evidence.runner_base.trade_returns_view"]
        ret_fwd1_def = rows["research_evidence.engine.ret_fwd1"]
        assert c1_def == c2_def == c3_def == "trade_forward_return"
        assert ret_fwd1_def == "spot_one_day_forward_return"
        assert ret_fwd1_def != c1_def

    def test_ret_fwd1_is_no_longer_unclaimed(self, db):
        # S-6: ret_fwd1 migrates to the Label Store. "Unclaimed" meant no
        # Specification anywhere referenced it; after --commit, one does.
        _run_script("--commit", "--db", str(db))
        con = sqlite3.connect(db)
        try:
            row = con.execute(
                "SELECT COUNT(*) FROM label_specifications WHERE binding_id = ?",
                ("research_evidence.engine.ret_fwd1",),
            ).fetchone()
        finally:
            con.close()
        assert row[0] == 1


# ---------------------------------------------------------------------------
# Idempotency (DBV2/DBV3) -- run twice, zero new writes the second time
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_second_commit_run_writes_nothing_new(self, db):
        first = _run_script("--commit", "--db", str(db))
        assert first.returncode == 0
        con = sqlite3.connect(db)
        try:
            defs_before = con.execute("SELECT COUNT(*) FROM label_definitions").fetchone()[0]
            specs_before = con.execute("SELECT COUNT(*) FROM label_specifications").fetchone()[0]
        finally:
            con.close()

        second = _run_script("--commit", "--db", str(db))
        assert second.returncode == 0
        con = sqlite3.connect(db)
        try:
            defs_after = con.execute("SELECT COUNT(*) FROM label_definitions").fetchone()[0]
            specs_after = con.execute("SELECT COUNT(*) FROM label_specifications").fetchone()[0]
        finally:
            con.close()

        assert defs_after == defs_before == 2
        assert specs_after == specs_before == 4

    def test_second_commit_run_produces_identical_hashes(self, db):
        first = _run_script("--commit", "--db", str(db))
        second = _run_script("--commit", "--db", str(db))
        first_hashes = [line for line in first.stdout.splitlines() if "->" in line]
        second_hashes = [line for line in second.stdout.splitlines() if "->" in line]
        assert first_hashes == second_hashes


# ---------------------------------------------------------------------------
# C1/C2/C3 -- three distinct hashes from otherwise-identical inputs
# (isolated unit-level proof, not just the end-to-end script result above)
# ---------------------------------------------------------------------------

class TestReconventionProducesDistinctHashes:
    def test_c1_c2_c3_end_to_end_hashes_are_pairwise_distinct(self, db):
        _run_script("--commit", "--db", str(db))
        con = sqlite3.connect(db)
        try:
            hashes = [
                r[0] for r in con.execute(
                    "SELECT spec_hash FROM label_specifications "
                    "WHERE binding_id IN (?,?,?)",
                    (
                        "forward_evidence.daily_job.close_trade",
                        "research_evidence.engine.net_daily",
                        "forward_evidence.runner_base.trade_returns_view",
                    ),
                )
            ]
        finally:
            con.close()
        assert len(hashes) == 3
        assert len(set(hashes)) == 3

    def test_isolated_fill_cost_direction_alone_are_sufficient_to_diverge(self, db):
        """§11.1 Reconvention, proven directly against registry.py: holding
        EVERY other field fixed (definition, horizon, validity_condition,
        conditioning_propagation, binding) and varying ONLY
        fill/cost/direction convention text -- the exact three fields PS1
        names -- is sufficient, by itself, to produce three distinct
        content hashes. This isolates the mechanism from the real C1/C2/C3
        registrations above, which also happen to differ in horizon_days
        and binding_id."""
        reg.init_db(db)
        d = reg.register_definition(db, "isolated_test_outcome", "r", "i", "rohith", NOW)

        def _spec_kwargs(**overrides):
            base = dict(
                definition_name=d.name,
                definition_version=d.version,
                horizon_days=7,
                horizon_governance="calendar_span",
                fill_convention="same-day close",
                cost_convention="flat 15bps",
                direction_convention="long-only",
                maturity_rule="matures at fixed exit",
                validity_condition="valid once entry and exit recorded",
                conditioning_propagation="preserving",
                binding_id="test.binding",
                engine_version="v1",
                actor="rohith",
                registered_at=NOW,
            )
            base.update(overrides)
            return base

        a = reg.register_specification(db, **_spec_kwargs(
            fill_convention="same-day close", cost_convention="flat 15bps",
            direction_convention="long-only",
        ))
        b = reg.register_specification(db, **_spec_kwargs(
            fill_convention="close-to-close daily", cost_convention="turnover-based",
            direction_convention="signed continuous",
        ))
        c = reg.register_specification(db, **_spec_kwargs(
            fill_convention="close-to-close per trade", cost_convention="flat cost per trade",
            direction_convention="explicit +-1",
        ))

        assert len({a, b, c}) == 3


# ---------------------------------------------------------------------------
# No forward data touched (this milestone's own exit criterion)
# ---------------------------------------------------------------------------

class TestNoForwardDataTouched:
    def test_script_never_imports_value_store(self):
        # AST-based, not a raw substring search: the module's own docstring
        # mentions "value_store" in prose (explaining what it deliberately
        # does NOT do), which a substring check would false-positive on.
        import ast
        tree = ast.parse(_SCRIPT.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not any("value_store" in alias.name for alias in node.names)
            if isinstance(node, ast.ImportFrom):
                assert node.module is None or "value_store" not in node.module

    def test_script_never_references_forward_databases_outside_the_docstring(self):
        import ast
        tree = ast.parse(_SCRIPT.read_text(encoding="utf-8"))
        docstring = ast.get_docstring(tree) or ""
        body_without_docstring = tree.body[1:] if (
            tree.body and isinstance(tree.body[0], ast.Expr)
            and isinstance(tree.body[0].value, ast.Constant)
        ) else tree.body
        for node in ast.walk(ast.Module(body=body_without_docstring, type_ignores=[])):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert "forward_evidence.db" not in node.value
                assert "evidence_forward.db" not in node.value
