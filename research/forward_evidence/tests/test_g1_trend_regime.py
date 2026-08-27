"""
Gate 6A.16 — g1_trend_regime_ma_cross implementation validation.

Validates decision_function_g1_trend_regime against the contract frozen at Gate
6A.15 (docs/research/g1_trend_regime_ma_cross_6a15_specification.md). Nothing
here evaluates the strategy: no Sharpe, DSR, BH-p, drawdown, forward return or
hit rate is computed anywhere in this file, and the sealed lockbox
(2025-06-01..2026-05-31) is never read.

Groups
    U*  unit — frozen constants, polarity, boundaries, warm-up, NaN
    E*  equivalence — independent reference, hand-computed fixtures,
        prefix invariance, and the byte-level guarantee that the standalone
        file does not import signals.py
    R*  real-corpus sanity, TRAIN SLICE ONLY
    P*  protected-artifact and lockbox integrity

Adversarial mutation testing was run against this suite at Gate 6A.16: 19
mutants of decision_function_g1_trend_regime.py (parameter changes, polarity
inversion, comparison-operator relaxation, warm-up-guard removal, min_periods
leak, centred and shifted windows, fast/slow swap, a standalone violation, and
two cosmetic edits). Result: 16/19 killed behaviourally, 19/19 killed once the
hash pin is included.

The three behavioural survivors are provably behaviour-preserving, not coverage
gaps, and each is recorded in a test rather than left implicit:
    M07 deleting `pos[sf.isna() | ss.isna()] = 0.0`  -> test_U12b
    M18 adding `.astype(float)`                       -> hash-only
    M19 reordering the two disjoint assignments       -> hash-only
Two real test weaknesses were found by the mutants and fixed before this suite
was committed: test_U02 was satisfiable by the module docstring's prose (M10
survived it) and is now structural, and the first mutation run's own anchors
matched the docstring's parameter table instead of the code.

Run from research/:
    pytest forward_evidence/tests/test_g1_trend_regime.py -v
"""
from __future__ import annotations

import ast
import inspect
import pathlib
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from forward_evidence import decision_function_g1_trend_regime as G1T
from forward_evidence.pre_reg_guard import _sha256_lf

_RESEARCH = pathlib.Path(__file__).resolve().parents[2]
_REPO = _RESEARCH.parent
_FN_PATH = _RESEARCH / "forward_evidence" / "decision_function_g1_trend_regime.py"

TRAIN_START, TRAIN_END = "2021-01-01", "2025-05-31"
LOCKBOX = ("2025-06-01", "2026-05-31")


# ===========================================================================
# Fixture helpers
# ===========================================================================

def days(n: int, start: str = "2021-01-01") -> pd.Index:
    return pd.date_range(start, periods=n, freq="D").strftime("%Y-%m-%d")


def series(values) -> pd.Series:
    v = np.asarray(values, dtype=float)
    return pd.Series(v, index=days(len(v)))


def reference_positions(close: pd.Series, fast: int, slow: int) -> pd.Series:
    """
    Independent reference, written from the frozen contract text rather than
    from the implementation: an explicit per-row loop over trailing means, with
    no pandas rolling call and no boolean masking. If the vectorised
    implementation and this loop agree, they agree for structural reasons.
    """
    v = close.to_numpy(dtype=float)
    out = np.zeros(len(v), dtype=float)
    for i in range(len(v)):
        if i + 1 < slow:
            out[i] = 0.0
            continue
        wf = v[i + 1 - fast:i + 1]
        ws = v[i + 1 - slow:i + 1]
        if np.isnan(wf).any() or np.isnan(ws).any():
            out[i] = 0.0
            continue
        mf, ms = wf.mean(), ws.mean()
        out[i] = 1.0 if mf > ms else (-1.0 if mf < ms else 0.0)
    return pd.Series(out, index=close.index)


# ===========================================================================
# U — unit
# ===========================================================================

class TestUnit:

    def test_U01_frozen_constants(self):
        """The 6A.15 freeze, asserted as values. Changing any of these here is
        a protocol violation, not a test failure."""
        assert G1T.FAST == 20
        assert G1T.SLOW == 100
        assert G1T.HOLD_DAYS == 7
        assert G1T.MIN_HISTORY == 100
        assert G1T.MIN_HISTORY == G1T.SLOW
        assert G1T.ANN == 365.0

    def test_U02_min_history_is_derived_not_literal(self):
        """MIN_HISTORY must be ASSIGNED from SLOW, so the two can never drift.

        Checked structurally, not textually: mutation M10 survived an earlier
        substring form of this test because the module docstring also contains
        the words "MIN_HISTORY = SLOW". Prose must not be able to satisfy it.
        """
        mod = ast.parse(_FN_PATH.read_text(encoding="utf-8"))
        assigns = {t.id: n.value for n in mod.body
                   if isinstance(n, ast.Assign)
                   for t in n.targets if isinstance(t, ast.Name)}
        rhs = assigns["MIN_HISTORY"]
        assert isinstance(rhs, ast.Name) and rhs.id == "SLOW", ast.dump(rhs)

    def test_U02b_frozen_constants_are_plain_literals_in_code(self):
        """FAST/SLOW/HOLD_DAYS/ANN must be literal assignments at module level,
        so the hash covers them and nothing can compute them at import."""
        mod = ast.parse(_FN_PATH.read_text(encoding="utf-8"))
        assigns = {t.id: n.value for n in mod.body
                   if isinstance(n, ast.Assign)
                   for t in n.targets if isinstance(t, ast.Name)}
        for name, want in (("FAST", 20), ("SLOW", 100),
                           ("HOLD_DAYS", 7), ("ANN", 365.0)):
            node = assigns[name]
            assert isinstance(node, ast.Constant), (name, ast.dump(node))
            assert node.value == want, (name, node.value)

    def test_U03_output_alphabet_is_exactly_three_values(self):
        rng = np.random.default_rng(0)
        close = series(100.0 + np.cumsum(rng.normal(0, 1, 600)))
        pos = G1T.compute_signal(close)
        assert set(np.unique(pos.to_numpy())) <= {-1.0, 0.0, 1.0}

    def test_U04_polarity_long_when_fast_above_slow(self):
        close = series(np.linspace(100.0, 300.0, 400))
        pos = G1T.compute_signal(close)
        sf = close.rolling(G1T.FAST, min_periods=G1T.FAST).mean()
        ss = close.rolling(G1T.SLOW, min_periods=G1T.SLOW).mean()
        assert pos.iloc[-1] == 1.0
        assert sf.iloc[-1] > ss.iloc[-1]

    def test_U05_polarity_short_when_fast_below_slow(self):
        close = series(np.linspace(300.0, 100.0, 400))
        pos = G1T.compute_signal(close)
        sf = close.rolling(G1T.FAST, min_periods=G1T.FAST).mean()
        ss = close.rolling(G1T.SLOW, min_periods=G1T.SLOW).mean()
        assert pos.iloc[-1] == -1.0
        assert sf.iloc[-1] < ss.iloc[-1]

    def test_U06_polarity_is_not_inverted_anywhere(self):
        """+1 must coincide with sf>ss and -1 with sf<ss on EVERY row, not just
        at the tail. This is the check a sign flip cannot survive."""
        rng = np.random.default_rng(7)
        close = series(100.0 + np.cumsum(rng.normal(0, 2, 800)))
        pos = G1T.compute_signal(close)
        sf = close.rolling(G1T.FAST, min_periods=G1T.FAST).mean()
        ss = close.rolling(G1T.SLOW, min_periods=G1T.SLOW).mean()
        defined = sf.notna() & ss.notna()
        assert ((pos == 1.0) == ((sf > ss) & defined)).all()
        assert ((pos == -1.0) == ((sf < ss) & defined)).all()

    def test_U07_exact_equality_is_flat(self):
        """A constant series makes SMA(20) == SMA(100) exactly. Neither strict
        comparison matches, so the row stays 0.0."""
        close = series(np.full(200, 50.0))
        pos = G1T.compute_signal(close)
        sf = close.rolling(G1T.FAST, min_periods=G1T.FAST).mean()
        ss = close.rolling(G1T.SLOW, min_periods=G1T.SLOW).mean()
        assert sf.iloc[-1] == ss.iloc[-1]
        assert pos.iloc[-1] == 0.0

    def test_U08_comparisons_are_strict_not_relaxed(self):
        """A `>=` in place of `>` would make the constant-series row +1. Guards
        the boundary the contract puts at exact equality."""
        src = inspect.getsource(G1T.compute_signal)
        assert "pos[sf > ss] = 1.0" in src
        assert "pos[sf < ss] = -1.0" in src
        assert ">=" not in src and "<=" not in src

    def test_U09_warmup_rows_before_slow_are_flat(self):
        close = series(np.linspace(100.0, 300.0, 400))
        pos = G1T.compute_signal(close)
        assert (pos.iloc[:G1T.SLOW - 1] == 0.0).all()

    def test_U10_first_decidable_row_is_exactly_slow(self):
        """Boundary: row index SLOW-1 (the SLOW'th row) is the first that can be
        non-zero. This is what makes MIN_HISTORY = SLOW exact."""
        close = series(np.linspace(100.0, 300.0, 400))
        pos = G1T.compute_signal(close)
        ss = close.rolling(G1T.SLOW, min_periods=G1T.SLOW).mean()
        assert pd.isna(ss.iloc[G1T.SLOW - 2])
        assert not pd.isna(ss.iloc[G1T.SLOW - 1])
        assert pos.iloc[G1T.SLOW - 1] != 0.0

    def test_U11_exactly_min_history_rows_is_decidable(self):
        close = series(np.linspace(100.0, 300.0, G1T.MIN_HISTORY))
        pos = G1T.compute_signal(close)
        assert len(close) == G1T.MIN_HISTORY
        assert pos.iloc[-1] == 1.0

    def test_U12_one_row_short_of_min_history_is_flat(self):
        """The manufactured-FLAT branch, demonstrated firing. This is exactly
        what the upstream MIN_HISTORY guard exists to make unreachable."""
        close = series(np.linspace(100.0, 300.0, G1T.MIN_HISTORY - 1))
        pos = G1T.compute_signal(close)
        assert pos.iloc[-1] == 0.0

    def test_U12b_the_isna_line_is_redundant_and_retained_deliberately(self):
        """Adversarial mutation M07 deleted `pos[sf.isna() | ss.isna()] = 0.0`
        and the whole behavioural battery still passed. That is CORRECT, not a
        coverage gap: `pos` is initialised to 0.0 and neither strict comparison
        matches NaN, so the line can never change a value.

        It is retained VERBATIM because the frozen 6A.15 contract contains it,
        and because it states the manufactured-FLAT hazard where a reader will
        look for it. Recorded here so a future maintainer does not "simplify"
        it away believing they found dead code — it is dead code, and it is
        frozen dead code.
        """
        rng = np.random.default_rng(23)
        close = series(100.0 + np.cumsum(rng.normal(0, 2, 400)))
        close.iloc[150] = np.nan
        sf = close.rolling(G1T.FAST, min_periods=G1T.FAST).mean()
        ss = close.rolling(G1T.SLOW, min_periods=G1T.SLOW).mean()
        undefined = sf.isna() | ss.isna()
        assert undefined.any()
        assert not (sf[undefined] > ss[undefined]).any()
        assert not (sf[undefined] < ss[undefined]).any()
        assert (G1T.compute_signal(close)[undefined] == 0.0).all()

    def test_U13_nan_close_at_the_final_row_yields_flat_not_a_decision(self):
        """Documents the hazard rather than blessing it: the function CANNOT
        distinguish a missing close from a flat view. Fail-closed is therefore
        an upstream invariant, asserted in test_U14/U15."""
        close = series(np.linspace(100.0, 300.0, 400))
        close.iloc[-1] = np.nan
        assert G1T.compute_signal(close).iloc[-1] == 0.0

    def test_U14_runner_refuses_a_present_but_nan_close(self):
        """The B-02 guard (gate 6B.1) lives in runner_base.run_signal, not here.
        Asserted against the real runner source so the two cannot drift."""
        from forward_evidence import runner_base
        rs = inspect.getsource(runner_base.run_signal)
        assert "pd.isna(closes_to_date.loc[run_date])" in rs
        assert "Missing price data must " in rs
        assert "not be recorded as a flat decision." in rs

    def test_U15_runner_and_loader_both_enforce_min_history(self):
        from forward_evidence import runner_base
        assert "len(closes_to_date) < min_history" in inspect.getsource(
            runner_base.run_signal)
        assert "len(daily) < min_rows" in inspect.getsource(
            runner_base.load_daily_closes)

    def test_U16_nan_propagates_only_forward_through_the_windows(self):
        """A NaN at row i makes rows i..i+SLOW-1 undefined and therefore flat,
        and leaves every earlier row untouched."""
        close = series(np.linspace(100.0, 300.0, 400))
        clean = G1T.compute_signal(close)
        dirty_in = close.copy()
        dirty_in.iloc[200] = np.nan
        dirty = G1T.compute_signal(dirty_in)
        assert (clean.iloc[:200].to_numpy() == dirty.iloc[:200].to_numpy()).all()
        assert (dirty.iloc[200:200 + G1T.SLOW] == 0.0).all()
        assert (clean.iloc[300:].to_numpy() == dirty.iloc[300:].to_numpy()).all()

    def test_U17_index_and_length_are_preserved(self):
        close = series(np.linspace(100.0, 300.0, 400))
        pos = G1T.compute_signal(close)
        assert len(pos) == len(close)
        assert list(pos.index) == list(close.index)
        assert pos.dtype == np.float64

    def test_U18_function_does_no_io_and_no_validation(self):
        """compute_signal must be a pure transform: the loader owns
        completeness, the runner owns fail-closed."""
        tree = ast.parse(inspect.getsource(G1T.compute_signal))
        called = {n.func.attr for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        assert not ({"read_parquet", "read_csv", "open", "connect"} & called)
        assert "raise" not in inspect.getsource(G1T.compute_signal)


# ===========================================================================
# E — equivalence and invariants
# ===========================================================================

class TestEquivalence:

    @pytest.mark.parametrize("seed", [0, 1, 2, 3, 4])
    def test_E01_matches_independent_loop_reference(self, seed):
        """Vectorised implementation vs an explicit per-row loop written from
        the contract text. No pandas rolling, no masking, no shared code."""
        rng = np.random.default_rng(seed)
        close = series(100.0 + np.cumsum(rng.normal(0, 2, 900)))
        got = G1T.compute_signal(close)
        want = reference_positions(close, G1T.FAST, G1T.SLOW)
        assert np.allclose(got.to_numpy(), want.to_numpy())

    def test_E02_matches_sig_trend_regime_on_synthetic_data(self):
        """THE equivalence requirement of Gate 6A.16. signals.py is imported
        only HERE, in the test — never by the decision function."""
        sys.path.insert(0, str(_RESEARCH / "evidence"))
        import signals as S

        rng = np.random.default_rng(11)
        for n in (100, 101, 250, 900):
            close = series(100.0 + np.cumsum(rng.normal(0, 2, n)))
            mine = G1T.compute_signal(close)
            theirs = S.sig_trend_regime(pd.DataFrame({"close": close}), 20, 100)
            assert np.array_equal(mine.to_numpy(), theirs.to_numpy()), n
            assert list(mine.index) == list(theirs.index)

    def test_E03_matches_sig_trend_regime_on_pathological_inputs(self):
        """Equivalence must hold where the two could plausibly diverge: exact
        ties, embedded NaN, monotone runs and a single-value series."""
        sys.path.insert(0, str(_RESEARCH / "evidence"))
        import signals as S

        cases = {
            "constant": np.full(300, 42.0),
            "ties_then_break": np.r_[np.full(200, 10.0), np.linspace(10, 20, 100)],
            "embedded_nan": np.r_[np.linspace(1, 2, 150), [np.nan],
                                  np.linspace(2, 3, 149)],
            "monotone_down": np.linspace(500.0, 1.0, 300),
            "single_row": np.array([7.0]),
            "exactly_slow": np.linspace(1.0, 2.0, 100),
            "slow_minus_one": np.linspace(1.0, 2.0, 99),
        }
        for name, arr in cases.items():
            close = series(arr)
            mine = G1T.compute_signal(close)
            theirs = S.sig_trend_regime(pd.DataFrame({"close": close}), 20, 100)
            assert np.array_equal(mine.to_numpy(), theirs.to_numpy()), name

    def test_E04_hand_computed_fixture_long(self):
        """Hand-computed, no library rolling. 100 rows: the first 80 are 10.0,
        the last 20 are 20.0.
            SMA(20) over the final 20 rows  = 20.0
            SMA(100) over all 100 rows      = (80*10 + 20*20) / 100 = 12.0
            20.0 > 12.0  ->  +1
        """
        close = series(np.r_[np.full(80, 10.0), np.full(20, 20.0)])
        assert close.iloc[-20:].mean() == 20.0
        assert close.mean() == 12.0
        assert G1T.compute_signal(close).iloc[-1] == 1.0

    def test_E05_hand_computed_fixture_short(self):
        """Mirror image: last 20 rows are 5.0, first 80 are 10.0.
            SMA(20)  = 5.0
            SMA(100) = (80*10 + 20*5) / 100 = 9.0
            5.0 < 9.0  ->  -1
        """
        close = series(np.r_[np.full(80, 10.0), np.full(20, 5.0)])
        assert close.iloc[-20:].mean() == 5.0
        assert close.mean() == 9.0
        assert G1T.compute_signal(close).iloc[-1] == -1.0

    def test_E06_hand_computed_fixture_exact_tie(self):
        """A tie the arithmetic makes exact, not approximate: 100 rows
        alternating 8.0/12.0 with the last 20 also alternating.
            SMA(20)  = 10.0 exactly (10 of each)
            SMA(100) = 10.0 exactly (50 of each)
            neither strict comparison matches  ->  0
        """
        close = series(np.tile([8.0, 12.0], 50))
        assert close.iloc[-20:].mean() == 10.0
        assert close.mean() == 10.0
        assert G1T.compute_signal(close).iloc[-1] == 0.0

    def test_E07_hand_computed_boundary_row(self):
        """Row 99 (the 100th) is decidable; row 98 is not. Values chosen so the
        100-row mean is computable by hand: 99 rows of 1.0 then one 101.0.
            SMA(100) at row 99 = (99*1 + 101) / 100 = 2.0
            SMA(20)  at row 99 = (19*1 + 101) / 20  = 6.0
            6.0 > 2.0  ->  +1
        """
        close = series(np.r_[np.full(99, 1.0), [101.0]])
        assert close.mean() == 2.0
        assert close.iloc[-20:].mean() == 6.0
        pos = G1T.compute_signal(close)
        assert pos.iloc[99] == 1.0
        assert pos.iloc[98] == 0.0

    @pytest.mark.parametrize("cut", [120, 250, 500, 899])
    def test_E08_prefix_invariance_no_lookahead(self, cut):
        """Truncating the future must not change any position at or before the
        cut. Run at several cuts so a single lucky index cannot pass it."""
        rng = np.random.default_rng(3)
        close = series(100.0 + np.cumsum(rng.normal(0, 2, 900)))
        full = G1T.compute_signal(close)
        trunc = G1T.compute_signal(close.iloc[:cut + 1])
        assert np.array_equal(full.iloc[:cut + 1].to_numpy(), trunc.to_numpy())

    def test_E09_future_values_cannot_alter_an_earlier_position(self):
        rng = np.random.default_rng(5)
        close = series(100.0 + np.cumsum(rng.normal(0, 2, 900)))
        full = G1T.compute_signal(close)
        mutated = close.copy()
        mutated.iloc[500:] = mutated.iloc[500:] * 7.0
        after = G1T.compute_signal(mutated)
        assert np.array_equal(full.iloc[:500].to_numpy(), after.iloc[:500].to_numpy())

    def test_E10_present_values_do_reach_the_position(self):
        """Anti-vacuity companion to E08/E09: prove the function is not simply
        insensitive to its input."""
        close = series(np.r_[np.full(99, 1.0), [101.0]])
        assert G1T.compute_signal(close).iloc[99] == 1.0
        close2 = close.copy()
        close2.iloc[99] = 0.01
        assert G1T.compute_signal(close2).iloc[99] == -1.0

    def test_E11_windows_are_trailing_only(self):
        src = inspect.getsource(G1T.compute_signal)
        assert "center" not in src
        assert "shift(-" not in src
        assert ".shift(" not in src

    def test_E12_decision_function_never_imports_signals(self):
        """Structural, not textual: parse the module's import graph. The
        platform convention forbids the standalone file depending on the engine
        pipeline it is meant to be independent of."""
        tree = ast.parse(_FN_PATH.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        assert imported == {"__future__", "pandas"}, imported
        assert not any("signal" in m or "engine" in m for m in imported)

    def test_E13_module_has_no_side_effects_at_import(self):
        """Top level must be docstring, imports, constants and defs only."""
        tree = ast.parse(_FN_PATH.read_text(encoding="utf-8"))
        for node in tree.body:
            assert isinstance(node, (ast.Expr, ast.Import, ast.ImportFrom,
                                     ast.Assign, ast.AnnAssign,
                                     ast.FunctionDef)), type(node).__name__

    def test_E14_frozen_parameters_are_module_constants_not_arguments(self):
        """20/100 must be immutable in the hashed file. A caller must not be
        able to pass different periods."""
        params = inspect.signature(G1T.compute_signal).parameters
        assert list(params) == ["close"]


# ===========================================================================
# R — real corpus, TRAIN SLICE ONLY
# ===========================================================================

def _real_closes():
    from forward_evidence.runner_base import load_daily_closes
    d = _RESEARCH / "data" / "raw" / "spot" / "BTCUSDT"
    if not d.exists():
        pytest.skip("real spot corpus not present")
    return load_daily_closes(d, TRAIN_END, min_rows=G1T.MIN_HISTORY)


class TestRealCorpus:

    def test_R01_runs_on_real_corpus_and_stops_at_train_end(self):
        closes = _real_closes()
        assert closes.index[-1] == TRAIN_END
        pos = G1T.compute_signal(closes)
        assert len(pos) == len(closes)

    def test_R02_never_reads_the_sealed_lockbox(self):
        closes = _real_closes()
        assert (closes.index <= TRAIN_END).all()
        assert not ((closes.index >= LOCKBOX[0]) & (closes.index <= LOCKBOX[1])).any()

    def test_R03_positions_are_only_the_three_legal_values(self):
        pos = G1T.compute_signal(_real_closes())
        assert set(np.unique(pos.to_numpy())) <= {-1.0, 0.0, 1.0}

    def test_R04_equivalence_holds_on_real_data(self):
        """The equivalence requirement, re-run on the real train slice rather
        than on synthetic series only."""
        sys.path.insert(0, str(_RESEARCH / "evidence"))
        import signals as S
        closes = _real_closes()
        mine = G1T.compute_signal(closes)
        theirs = S.sig_trend_regime(pd.DataFrame({"close": closes}), 20, 100)
        assert np.array_equal(mine.to_numpy(), theirs.to_numpy())

    def test_R05_prefix_invariance_holds_on_real_data(self):
        closes = _real_closes()
        cut = len(closes) - 30
        full = G1T.compute_signal(closes)
        trunc = G1T.compute_signal(closes.iloc[:cut])
        assert np.array_equal(full.iloc[:cut].to_numpy(), trunc.to_numpy())

    def test_R06_only_the_warmup_prefix_is_flat_for_missing_history(self):
        pos = G1T.compute_signal(_real_closes())
        assert (pos.iloc[:G1T.SLOW - 1] == 0.0).all()
        assert (pos.iloc[G1T.SLOW - 1:] != 0.0).any()


# ===========================================================================
# P — protected artifacts, hash pinning, lockbox
# ===========================================================================

class TestProtected:

    def test_P01_decision_function_hash_matches_the_pinned_draft(self):
        """The draft's decision_function_hash must be this file's _sha256_lf.
        A drifted function or a drifted draft both fail here."""
        import json
        draft = json.loads(
            (_RESEARCH / "forward_evidence" / "drafts"
             / "g1_trend_regime_ma_cross_prereg_draft.json").read_text(encoding="utf-8"))
        spec = draft["function_spec"]
        assert spec["decision_function_file"] == (
            "forward_evidence/decision_function_g1_trend_regime.py")
        assert spec["decision_function_hash"] == _sha256_lf(_FN_PATH)

    def test_P02_draft_parameters_match_the_module_constants(self):
        import json
        draft = json.loads(
            (_RESEARCH / "forward_evidence" / "drafts"
             / "g1_trend_regime_ma_cross_prereg_draft.json").read_text(encoding="utf-8"))
        p = draft["function_spec"]["parameters"]
        assert p["FAST"] == G1T.FAST
        assert p["SLOW"] == G1T.SLOW
        assert p["HOLD_DAYS"] == G1T.HOLD_DAYS
        assert p["MIN_HISTORY"] == G1T.MIN_HISTORY
        assert p["ANN"] == G1T.ANN
        assert p["COST_BPS"] == 15.0

    def test_P03_draft_remains_unadmitted(self):
        """6A.16 implements; it does not admit. The status and the empty
        platform-new list are 6A.15 decisions and must be untouched."""
        import json
        draft = json.loads(
            (_RESEARCH / "forward_evidence" / "drafts"
             / "g1_trend_regime_ma_cross_prereg_draft.json").read_text(encoding="utf-8"))
        assert draft["status"] == "SPECIFICATION_FROZEN_NOT_ADMITTED"
        assert draft["function_spec"]["platform_new_choices"] == []
        assert draft["governance"]["lockbox"]["spent"] is False

    def test_P04_signal_is_wired_into_collection(self):
        """Inverted at Gate 6A.18. Through 6A.16 this asserted the signal was
        NOT wired, which was correct for that stage; 6A.18 is the gate that
        wires it, so the assertion becomes positive rather than being deleted.
        """
        import yaml
        cfg = yaml.safe_load(
            (_RESEARCH / "forward_evidence" / "collection_config.yaml")
            .read_text(encoding="utf-8"))
        entry = cfg["signals"]["g1_trend_regime_ma_cross"]
        assert entry["cost_bps"] == 15.0
        assert entry["pre_reg_file"] == (
            "forward_evidence/pre_registration_g1_trend_regime.json")
        assert entry["decision_function_file"] == (
            "forward_evidence/decision_function_g1_trend_regime.py")
        assert entry["decision_function_module"] == (
            "forward_evidence.decision_function_g1_trend_regime")

        from forward_evidence import run_daily
        assert "g1_trend_regime_ma_cross" in run_daily._CLOSE_ONLY_SIGNAL_IDS
        assert dict(run_daily._CLOSE_ONLY_RUNNERS)["g1_trend_regime_ma_cross"] \
            is run_daily.runner_g1tr

    def test_P04b_governance_failure_audit_covers_the_new_signal(self):
        """Regression for the defect this gate's own tests caught: the signal
        was wired into run_all() but missing from _blocked_signal_ids(), so a
        governance-check failure would have written an error row for every
        signal EXCEPT this one -- a silent hole in the audit trail."""
        import yaml
        from forward_evidence import run_daily
        cfg = yaml.safe_load(
            (_RESEARCH / "forward_evidence" / "collection_config.yaml")
            .read_text(encoding="utf-8"))
        assert "g1_trend_regime_ma_cross" in run_daily._blocked_signal_ids(
            cfg["signals"])

    def test_P04c_absent_config_entry_skips_only_that_signal(self):
        """A wired-but-unconfigured signal must not take down the whole run.
        Indexing sigs[...] directly raised KeyError BEFORE the per-signal loop,
        which is the opposite of run_daily's documented DQ3 isolation."""
        from forward_evidence import run_daily
        ids = run_daily._blocked_signal_ids(
            {"h5_vol_regime": {}, "g3_momentum_tsmom": {}})
        assert ids == ["h5_vol_regime", "g3_momentum_tsmom"]

    def test_P05_runner_module_is_correct(self):
        """Inverted at Gate 6A.18, for the same reason as P04. The runner must
        point at the pinned decision function and pass the frozen constants
        through unchanged."""
        from forward_evidence import runner_g1_trend_regime as R
        from forward_evidence import decision_function_g1_trend_regime as D
        assert R.SIGNAL_ID == "g1_trend_regime_ma_cross"
        assert R._FN_FILE == _FN_PATH
        assert R._PRE_REG == (_RESEARCH / "forward_evidence"
                              / "pre_registration_g1_trend_regime.json")
        assert R._df is D
        src = inspect.getsource(R.collect)
        assert "hold_days=_df.HOLD_DAYS" in src
        assert "min_history=_df.MIN_HISTORY" in src
        assert "compute_fn=_df.compute_signal" in src

    def test_P05b_runner_introduces_no_engine_or_duckdb_dependency(self):
        """The forward path must stay free of the offline engine and DuckDB."""
        tree = ast.parse((_RESEARCH / "forward_evidence"
                          / "runner_g1_trend_regime.py").read_text(
                              encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        assert not any(m.startswith("duckdb") or m == "engine"
                       or m.endswith(".engine") or "signals" in m
                       for m in imported), imported

    def test_P05c_active_pre_registration_pins_the_frozen_hash(self):
        """verify_fn_hash reads signal_id and function_spec.decision_function_hash
        from the ACTIVE pre-registration, so those two fields are what actually
        gate every run."""
        import json
        from forward_evidence.pre_reg_guard import verify_fn_hash
        pre = (_RESEARCH / "forward_evidence"
               / "pre_registration_g1_trend_regime.json")
        d = json.loads(pre.read_text(encoding="utf-8"))
        assert d["signal_id"] == "g1_trend_regime_ma_cross"
        assert d["version"] == "1.0.0"
        assert d["function_spec"]["decision_function_hash"] == _sha256_lf(_FN_PATH)
        assert verify_fn_hash(pre, _FN_PATH,
                              "g1_trend_regime_ma_cross") == _sha256_lf(_FN_PATH)

    def test_P05d_observation_floors_are_frozen(self):
        """U-1 (6A.17) and U-3 (6A.18). Fixed before any forward observation
        exists; after that they cannot be set honestly."""
        import json
        d = json.loads((_RESEARCH / "forward_evidence"
                        / "pre_registration_g1_trend_regime.json")
                       .read_text(encoding="utf-8"))
        f = d["observation_floors"]
        assert f["minimum_trades"] == 30
        assert f["minimum_calendar_months_before_assessment"] == 12
        assert f["minimum_both_long_and_short_trades"] is True

    def test_P05e_frozen_draft_is_untouched_by_wiring(self):
        """The 6A.15 draft is the freeze record and must survive wiring
        byte-identical, including its NOT_ADMITTED artifact status."""
        import json
        draft = json.loads(
            (_RESEARCH / "forward_evidence" / "drafts"
             / "g1_trend_regime_ma_cross_prereg_draft.json").read_text(
                 encoding="utf-8"))
        active = json.loads(
            (_RESEARCH / "forward_evidence"
             / "pre_registration_g1_trend_regime.json").read_text(
                 encoding="utf-8"))
        assert draft["status"] == "SPECIFICATION_FROZEN_NOT_ADMITTED"
        for k in ("function_spec", "hypothesis", "governance",
                  "integrity_rules", "provenance_contract",
                  "return_and_cost_convention", "independence",
                  "implementation", "admission"):
            assert active[k] == draft[k], k

    def test_P06_registry_entry_state_is_untouched(self):
        import yaml
        reg = yaml.safe_load(
            (_RESEARCH / "evidence_library" / "registry.yaml").read_text(
                encoding="utf-8"))
        fams = reg.get("families", reg)
        e = fams["g1_trend_regime_ma_cross"]
        assert e["state"] == "candidate"
        assert e["role"] == ["feature"]
        assert e["preregistration"] is None

    def test_P07_shared_infrastructure_is_unmodified(self):
        """runner_base, engine and signals must be byte-identical to the hashes
        pinned at 6A.15. This is what makes 'no shared infrastructure changed'
        a verified claim rather than an assertion."""
        expected = {
            _RESEARCH / "evidence" / "signals.py":
                "85644baf6e737f060016098a8907c0d7a6e49004aaf9afe328a45440a85e87b7",
            _RESEARCH / "evidence" / "engine.py":
                "aa2cb34fc9793b81466f253065b5a0f7791cd096b58d95e9dd991fdb1ce4ffdb",
            _RESEARCH / "forward_evidence" / "runner_base.py":
                "a96fe714c7a92576fbe062ce9591359313748384ec69799f12b89a68473c3094",
            # Updated at Gate 6A.18: wiring necessarily modifies run_daily.
            # runner_base, signals.py, engine.py and run_date_policy are still
            # asserted at their 6A.15 values -- wiring touched none of them.
            _RESEARCH / "forward_evidence" / "run_daily.py":
                "578b1acf4fd81f35d6dd8ff5609097b9cb213ecb748cda8c0696d9d131c57414",
            _RESEARCH / "forward_evidence" / "run_date_policy.py":
                "d89599e08fb9b01de09ee27bd6d1ee60cd6c7f941d09c24e786a123167c15dc6",
        }
        for path, want in expected.items():
            assert _sha256_lf(path) == want, path.name

    def test_P08_gate_touched_no_file_outside_its_allowlist(self):
        """Every tracked change in the working tree must belong to 6A.16."""
        allow = {
            # 6A.16
            "research/forward_evidence/decision_function_g1_trend_regime.py",
            "research/forward_evidence/tests/test_g1_trend_regime.py",
            "research/forward_evidence/drafts/g1_trend_regime_ma_cross_prereg_draft.json",
            "docs/research/g1_trend_regime_ma_cross_6a15_specification.md",
            # 6A.17
            "research/evidence_library/registry.yaml",
            "docs/governance/g1_trend_regime_admission_decision.md",
            # 6A.18
            "research/forward_evidence/pre_registration_g1_trend_regime.json",
            "research/forward_evidence/runner_g1_trend_regime.py",
            "research/forward_evidence/collection_config.yaml",
            "research/forward_evidence/run_daily.py",
            "research/forward_evidence/tests/test_funding_loader.py",
            "research/forward_evidence/tests/test_run_date_policy.py",
            "docs/governance/g1_trend_regime_wiring_record.md",
        }
        out = subprocess.run(["git", "status", "--porcelain"], cwd=_REPO,
                             capture_output=True, text=True).stdout
        changed = {l[3:].strip().strip('"') for l in out.splitlines()
                   if not l.startswith("??")}
        assert changed <= allow, changed - allow

    def test_P09_no_performance_statistic_is_computed(self):
        """The decision function must produce a POSITION and nothing else. Any
        return, dispersion or annualisation arithmetic here would mean the gate
        computed performance, which it is forbidden to do."""
        tree = ast.parse(inspect.getsource(G1T.compute_signal))
        fn = tree.body[0]
        if (isinstance(fn.body[0], ast.Expr)
                and isinstance(fn.body[0].value, ast.Constant)):
            fn.body = fn.body[1:]          # drop the docstring; scan CODE only
        code = ast.unparse(fn)
        for banned in (".std(", ".pct_change(", ".cumsum(", ".prod(",
                       "sqrt", "ANN", "log(", "np.log", "**"):
            assert banned not in code, (banned, code)
        mod = ast.parse(_FN_PATH.read_text(encoding="utf-8"))
        loaded = {n.id for n in ast.walk(mod)
                  if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        assert "ANN" not in loaded, "ANN is declared metadata, never read"
        assert "HOLD_DAYS" not in loaded, "HOLD_DAYS is declared metadata, never read"
        assert "SLOW" in loaded, "SLOW is read — MIN_HISTORY derives from it"
        assert G1T.ANN == 365.0

    def test_P10_hold_and_cost_are_metadata_not_behaviour(self):
        """HOLD_DAYS and the 15 bps cost are preserved in the frozen contract
        but must not influence the computation."""
        fn_src = inspect.getsource(G1T.compute_signal)
        assert "HOLD_DAYS" not in fn_src
        assert "cost" not in fn_src.lower()
        assert G1T.HOLD_DAYS == 7
        import json
        draft = json.loads(
            (_RESEARCH / "forward_evidence" / "drafts"
             / "g1_trend_regime_ma_cross_prereg_draft.json").read_text(encoding="utf-8"))
        conv = draft["return_and_cost_convention"]
        assert conv["adopted_spec_hash"].startswith("57ce016e51a0")
        assert conv["horizon_days"] == G1T.HOLD_DAYS
        assert draft["function_spec"]["parameters"]["COST_BPS"] == 15.0
