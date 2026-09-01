"""Tests for the signal-state run-length characterisation.

The pure core (run segmentation, rank position, run description) is tested
here; the parquet/decision-function loading is I/O and is exercised on the
host, not in unit tests.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_FWD = ROOT / "research" / "forward_evidence"
for _p in (ROOT / "research", _FWD):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import signal_state_analysis as S  # noqa: E402


class TestRuns:
    def test_segments_maximal_constant_runs(self):
        assert S.runs([0, 0, 1, 1, 1, 0, -1]) == [(0, 2), (1, 3), (0, 1), (-1, 1)]

    def test_single_value(self):
        assert S.runs([1, 1, 1]) == [(1, 3)]

    def test_empty(self):
        assert S.runs([]) == []

    def test_alternating_never_passes_through_flat(self):
        """g3_momentum's observed shape: +1 <-> -1 directly, no flat state."""
        r = S.runs([1, -1, 1, -1])
        assert r == [(1, 1), (-1, 1), (1, 1), (-1, 1)]
        assert not [n for v, n in r if v == 0]


class TestRankPosition:
    def test_rank_and_longer_count(self):
        rp = S.rank_position([1, 5, 10, 20, 50], 10)
        assert rp["n_observed_runs"] == 5
        assert rp["percentile_le"] == pytest.approx(60.0)
        assert rp["n_longer"] == 2 and rp["fraction_longer"] == pytest.approx(0.4)

    def test_empty_distribution_is_none(self):
        assert S.rank_position([], 5) is None

    def test_carries_an_anti_prediction_caveat(self):
        rp = S.rank_position([1, 2, 3], 2)
        assert "not a probability" in rp["caveat"]
        assert "hazard rate" in rp["caveat"]

    def test_value_longer_than_everything_is_100th(self):
        assert S.rank_position([1, 2, 3], 99)["percentile_le"] == 100.0
        assert S.rank_position([1, 2, 3], 99)["n_longer"] == 0


class TestDescribeRuns:
    def test_flat_and_active_structure(self):
        d = S.describe_runs([0.0, 0.0, 1.0, 1.0, 1.0, 0.0, -1.0])
        assert d["days"] == 7 and d["flat_days"] == 3
        assert d["flat_fraction"] == pytest.approx(3 / 7)
        assert d["flat_runs"]["n"] == 2 and d["active_runs"]["n"] == 2
        assert d["long_runs"]["n"] == 1 and d["short_runs"]["n"] == 1
        assert d["distinct_positions"] == [-1.0, 0.0, 1.0]

    def test_transitions_counted(self):
        d = S.describe_runs([0.0, 1.0, 0.0, -1.0])
        assert d["transitions"] == {"+0 -> +1": 1, "+1 -> +0": 1, "+0 -> -1": 1}

    def test_flat_runs_ge_7d(self):
        d = S.describe_runs([0.0] * 8 + [1.0] + [0.0] * 3)
        assert d["flat_runs_ge_7d"] == 1

    def test_long_only_signal_has_no_short_runs(self):
        d = S.describe_runs([0.0, 1.0, 1.0, 0.0])
        assert d["short_runs"] is None and d["long_runs"]["n"] == 1


class TestBoundaries:
    def test_train_end_is_the_non_lockbox_boundary(self):
        assert S.TRAIN_START == "2021-01-01" and S.TRAIN_END == "2025-05-31"

    def test_no_lockbox_date_in_executable_code(self):
        tree = ast.parse((_FWD / "signal_state_analysis.py").read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and \
                    ast.get_docstring(n):
                n.body = n.body[1:] or [ast.Pass()]
        code = ast.unparse(tree)
        assert "2026-05-31" not in code and "2025-06-01" not in code

    def test_computes_no_performance_statistic(self):
        src = (_FWD / "signal_state_analysis.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        called = {n.func.id for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        called |= {n.func.attr for n in ast.walk(tree)
                   if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        for banned in ("deflated_sr", "psr", "bh_fdr", "bh_adjusted_p", "pct_change"):
            assert banned not in called, f"must not call {banned}"
        for mod in ("governance", "forward_dsr_bh"):
            assert f"import {mod}" not in src

    def test_writes_nothing(self):
        src = (_FWD / "signal_state_analysis.py").read_text(encoding="utf-8")
        for banned in ("INSERT", "UPDATE", "DELETE", "write_text(", "append_run"):
            assert banned not in src, banned

    def test_every_collected_family_is_mapped_to_its_frozen_function(self):
        import yaml
        cfg = yaml.safe_load((_FWD / "collection_config.yaml").read_text(encoding="utf-8"))
        assert set(S.DECISION_MODULES) == set(cfg["signals"])
        for fam, mod in S.DECISION_MODULES.items():
            declared = cfg["signals"][fam]["decision_function_module"]
            assert declared.endswith(mod), f"{fam}: {declared} vs {mod}"

    def test_ascii_only(self):
        src = (_FWD / "signal_state_analysis.py").read_text(encoding="utf-8")
        assert all(ord(c) < 128 for c in src)
