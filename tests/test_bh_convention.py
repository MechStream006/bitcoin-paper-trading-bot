"""
FDB-13 -- `bh_p` convention identification across every presentation surface.

`runs.bh_p` carries two statistics: legacy in-sample raw p-values written before
the FB-10b contract, and BH-adjusted q-values written under it. These tests fix
the classifier (FDB-13.3), prove each renderer surfaces the convention
(FDB-13.4), and prove nothing about historical evidence moves.
"""
from __future__ import annotations

import ast
import hashlib
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_EVLIB = ROOT / "research" / "evidence_library"
sys.path.insert(0, str(_EVLIB))

import bh_convention  # noqa: E402
import lifecycle  # noqa: E402
import report  # noqa: E402

REAL_LEDGER = _EVLIB / "ledger.db"


def _fdb9_block() -> dict:
    """A complete FDB-9 block. Values are placeholders -- the classifier reads
    key presence only, never the numbers."""
    return {f: "x" for f in bh_convention.FDB9_REQUIRED_FIELDS}


# ---------------------------------------------------------------------------
# 1-3. The three-way classification (FDB-13.3)
# ---------------------------------------------------------------------------

class TestClassification:
    def test_null_is_uncomputed(self):
        assert bh_convention.classify({"bh_p": None}) == bh_convention.UNCOMPUTED

    def test_missing_key_is_uncomputed(self):
        assert bh_convention.classify({}) == bh_convention.UNCOMPUTED

    def test_nan_is_uncomputed(self):
        """Mirrors check_validated_preconditions, which already refuses to
        distinguish NULL from NaN."""
        assert bh_convention.classify({"bh_p": float("nan")}) == bh_convention.UNCOMPUTED

    def test_value_without_fdb9_block_is_legacy(self):
        run = {"bh_p": 0.2893, "metrics": {"sharpe": 0.26, "oos_sharpe": 0.50}}
        assert bh_convention.classify(run) == bh_convention.LEGACY_RAW_P

    def test_value_with_no_metrics_at_all_is_legacy(self):
        assert bh_convention.classify({"bh_p": 0.2073}) == bh_convention.LEGACY_RAW_P

    def test_value_with_complete_fdb9_block_is_forward_q(self):
        run = {"bh_p": 0.04, "metrics": _fdb9_block()}
        assert bh_convention.classify(run) == bh_convention.FORWARD_ADJUSTED_Q

    def test_nested_fdb9_block_is_found(self):
        """FDB-9 requires the fields be recorded without fixing where, so the
        classifier must not impose a location the contract does not state."""
        run = {"bh_p": 0.04, "metrics": {"forward_dsr_bh": _fdb9_block()}}
        assert bh_convention.classify(run) == bh_convention.FORWARD_ADJUSTED_Q

    @pytest.mark.parametrize("dropped", bh_convention.FDB9_REQUIRED_FIELDS)
    def test_partial_block_is_not_forward_q(self, dropped):
        """FDB-13.3: the block must be COMPLETE. FDB-9 already makes a partial
        result invalid, so no row may legitimately be written in this state --
        but if one ever were, it must not be presented as a valid q-value."""
        block = _fdb9_block()
        del block[dropped]
        assert bh_convention.classify({"bh_p": 0.04, "metrics": block}) != \
            bh_convention.FORWARD_ADJUSTED_Q

    def test_field_set_matches_fdb9_exactly(self):
        assert len(bh_convention.FDB9_REQUIRED_FIELDS) == 12
        assert set(bh_convention.FDB9_REQUIRED_FIELDS) == {
            "battery_members", "battery_excluded", "sr_trials_std",
            "sr_trials_std_basis", "n_trials", "q", "psr0", "sr_star",
            "return_basis", "n", "as_of", "calc_version",
        }

    def test_three_conventions_are_distinct(self):
        assert len({bh_convention.UNCOMPUTED, bh_convention.LEGACY_RAW_P,
                    bh_convention.FORWARD_ADJUSTED_Q}) == 3


# ---------------------------------------------------------------------------
# 4. Numeric values are never touched
# ---------------------------------------------------------------------------

class TestValuePreservation:
    def test_classify_never_reads_the_number(self):
        """The same metrics with wildly different bh_p values classify the
        same, proving the value is not consulted beyond null/NaN."""
        block = _fdb9_block()
        for v in (0.0, 1e-9, 0.2893, 0.9999, 1.0, -5.0, 1e12):
            assert bh_convention.classify({"bh_p": v, "metrics": block}) == \
                bh_convention.FORWARD_ADJUSTED_Q
            assert bh_convention.classify({"bh_p": v, "metrics": {}}) == \
                bh_convention.LEGACY_RAW_P

    def test_rendered_legacy_value_is_numerically_unchanged(self):
        run = {"bh_p": 0.2073, "metrics": {}}
        rendered = report._fmt_pinned_stat(run, "bh_p")
        assert rendered.startswith("0.2073")

    def test_rendered_forward_value_is_numerically_unchanged(self):
        run = {"bh_p": 0.0412, "metrics": _fdb9_block()}
        assert "0.0412" in report._fmt_pinned_stat(run, "bh_p")


# ---------------------------------------------------------------------------
# 5. Renderers call no statistical machinery
# ---------------------------------------------------------------------------

class TestNoStatisticalCoupling:
    def test_module_imports_nothing_statistical(self):
        tree = ast.parse((_EVLIB / "bh_convention.py").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        banned = {"governance", "numpy", "scipy", "pandas", "sqlite3",
                  "compute_dsr_for_registration", "run_harmonization", "record"}
        assert not (imported & banned), f"bh_convention imports {imported & banned}"

    def test_module_performs_no_arithmetic_on_bh_p(self):
        """No add/sub/mul/div anywhere in the module -- it is key presence only."""
        tree = ast.parse((_EVLIB / "bh_convention.py").read_text(encoding="utf-8"))
        arith = [n for n in ast.walk(tree)
                 if isinstance(n, ast.BinOp)
                 and isinstance(n.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow))]
        assert arith == []


# ---------------------------------------------------------------------------
# 6. Every discovered renderer exposes the convention (FDB-13.4)
# ---------------------------------------------------------------------------

class TestRenderersExposeConvention:
    def test_report_marks_legacy(self):
        out = report._fmt_pinned_stat({"bh_p": 0.2893, "metrics": {}}, "bh_p")
        assert "legacy raw p" in out

    def test_report_marks_forward_q(self):
        out = report._fmt_pinned_stat({"bh_p": 0.04, "metrics": _fdb9_block()}, "bh_p")
        assert "BH-adjusted q" in out

    def test_report_uncomputed_is_unchanged_and_unmarked(self):
        assert report._fmt_pinned_stat({"bh_p": None}, "bh_p") == "not-computed"

    def test_report_leaves_dsr_prob_untouched(self):
        """The marker is bh_p-only; DSR rendering must not change."""
        assert report._fmt_pinned_stat({"dsr_prob": None}, "dsr_prob") == "not-computed"
        # dsr_prob uses fmt=".3f" (unsigned) -- rendered exactly as before,
        # with no convention marker appended.
        assert report._fmt_pinned_stat(
            {"dsr_prob": 0.27, "metrics": {}}, "dsr_prob") == "0.270"
        assert report._fmt_pinned_stat(
            {"dsr_prob": 0.27, "metrics": _fdb9_block()}, "dsr_prob") == "0.270"

    def test_report_no_pinned_run_is_unchanged(self):
        assert report._fmt_pinned_stat(None, "bh_p") == report.NO_RUN

    def _gate_errors(self, run, thresholds):
        ok, errs = lifecycle.check_validated_preconditions(
            {"role": ["feature"]}, run, thresholds)
        return errs

    def test_lifecycle_message_names_legacy_convention(self):
        thresholds = {"validated": {"feature": {"max_bh_p": 0.10}}}
        errs = self._gate_errors(
            {"bh_p": 0.2893, "provenance": "live-forward", "metrics": {}}, thresholds)
        bh_err = [e for e in errs if "BH-FDR p" in e]
        assert len(bh_err) == 1
        assert "0.2893" in bh_err[0]
        assert "legacy raw p-value" in bh_err[0]
        assert "> q=0.1" in bh_err[0]

    def test_lifecycle_message_names_forward_convention(self):
        thresholds = {"validated": {"feature": {"max_bh_p": 0.10}}}
        errs = self._gate_errors(
            {"bh_p": 0.5, "provenance": "live-forward", "metrics": _fdb9_block()},
            thresholds)
        bh_err = [e for e in errs if "BH-FDR p" in e]
        assert len(bh_err) == 1
        assert "BH-adjusted q-value" in bh_err[0]

    def test_api_serialises_the_convention(self):
        """tests/test_api.py cannot be collected in any local conda env
        (pre-existing fastapi gap, CLAUDE.md sec.18 Increment 1), so the API
        wiring is asserted structurally rather than over HTTP."""
        src = (ROOT / "src" / "api" / "services.py").read_text(encoding="utf-8")
        assert '"bh_p_convention": report_mod.bh_convention.classify(pinned)' in src
        schema = (ROOT / "src" / "api" / "schemas.py").read_text(encoding="utf-8")
        assert "bh_p_convention: str | None = None" in schema

    def test_api_adds_no_stored_field(self):
        """FDB-13.3 forbids a new stored field. The API model gains a derived
        one; record.py's schema must be untouched."""
        schema_sql = (_EVLIB / "record.py").read_text(encoding="utf-8")
        assert "bh_p_convention" not in schema_sql


# ---------------------------------------------------------------------------
# 7-9. Historical rows, the gate, and the ledger
# ---------------------------------------------------------------------------

class TestHistoricalSafety:
    def test_gate_still_consumes_the_stored_numeric_field(self):
        """The gate compares the number, exactly as before. Classification is
        presentation and must never change a pass/fail outcome."""
        thresholds = {"validated": {"feature": {"max_bh_p": 0.10}}}
        legacy_pass = {"bh_p": 0.05, "provenance": "live-forward", "metrics": {}}
        forward_pass = {"bh_p": 0.05, "provenance": "live-forward",
                        "metrics": _fdb9_block()}
        for run in (legacy_pass, forward_pass):
            errs = lifecycle.check_validated_preconditions(
                {"role": ["feature"]}, run, thresholds)[1]
            assert not [e for e in errs if "BH-FDR p" in e]

        legacy_fail = {"bh_p": 0.5, "provenance": "live-forward", "metrics": {}}
        forward_fail = {"bh_p": 0.5, "provenance": "live-forward",
                        "metrics": _fdb9_block()}
        for run in (legacy_fail, forward_fail):
            errs = lifecycle.check_validated_preconditions(
                {"role": ["feature"]}, run, thresholds)[1]
            assert [e for e in errs if "BH-FDR p" in e]

    def test_null_still_hard_fails_the_gate(self):
        """R1's NULL discipline is untouched by FDB-13."""
        thresholds = {"validated": {"feature": {"max_bh_p": 0.10}}}
        errs = lifecycle.check_validated_preconditions(
            {"role": ["feature"]}, {"bh_p": None, "provenance": "live-forward"},
            thresholds)[1]
        assert any("BH-FDR p-value is absent" in e for e in errs)

    @pytest.mark.skipif(not REAL_LEDGER.exists(), reason="real ledger not present")
    def test_real_legacy_rows_classify_as_legacy_and_are_unchanged(self, tmp_path):
        before = hashlib.sha256(REAL_LEDGER.read_bytes()).hexdigest()
        copy = tmp_path / "ledger.db"
        shutil.copy2(REAL_LEDGER, copy)

        con = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT * FROM runs WHERE bh_p IS NOT NULL").fetchall()
        con.close()

        import json
        assert len(rows) == 3, "the three known legacy rows"
        values = []
        for r in rows:
            run = dict(r)
            run["metrics"] = json.loads(run.pop("metrics_json"))
            assert bh_convention.classify(run) == bh_convention.LEGACY_RAW_P
            assert "legacy raw p" in report._fmt_pinned_stat(run, "bh_p")
            values.append(round(run["bh_p"], 4))
        assert sorted(values) == [0.2073, 0.2893, 0.2893]

        assert hashlib.sha256(REAL_LEDGER.read_bytes()).hexdigest() == before, \
            "the real ledger must be byte-identical after classification"

    @pytest.mark.skipif(not REAL_LEDGER.exists(), reason="real ledger not present")
    def test_classification_issues_no_sql(self, tmp_path):
        """Rendering a run must not open, read or mutate any database."""
        copy = tmp_path / "ledger.db"
        shutil.copy2(REAL_LEDGER, copy)
        before = hashlib.sha256(copy.read_bytes()).hexdigest()

        run = {"bh_p": 0.2073, "metrics": {"sharpe": 0.49}}
        report._fmt_pinned_stat(run, "bh_p")
        bh_convention.classify(run)

        assert hashlib.sha256(copy.read_bytes()).hexdigest() == before
        assert not (tmp_path / "ledger.db-wal").exists()
        assert not (tmp_path / "ledger.db-shm").exists()


# ---------------------------------------------------------------------------
# FDB-13 contract text
# ---------------------------------------------------------------------------

class TestContractAddendum:
    CONTRACT = ROOT / "docs" / "governance" / "fb10b_forward_dsr_bh_contract.md"

    def test_fdb13_exists(self):
        text = self.CONTRACT.read_text(encoding="utf-8")
        assert "## FDB-13" in text
        for sub in ("FDB-13.1", "FDB-13.2", "FDB-13.3", "FDB-13.4", "FDB-13.5"):
            assert sub in text

    def test_fdb1_to_fdb12_still_present(self):
        text = self.CONTRACT.read_text(encoding="utf-8")
        for i in range(1, 13):
            assert f"**FDB-{i} —" in text or f"**FDB-{i}a —" in text, f"FDB-{i} missing"

    def test_fdb13_forbids_a_new_stored_field(self):
        text = self.CONTRACT.read_text(encoding="utf-8")
        assert "No new stored field is created for this purpose" in text
