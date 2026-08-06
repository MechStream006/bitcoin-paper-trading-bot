"""
Equivalence demonstration for the audited dual-implementation risk (PS1),
resolved per ADR-I010 §8.3 (engineering roadmap Phase 2, WBS 2.5).

ADR-I010 §1 audits the platform's "5 decision functions, each SHA-256
hash-pinned and verified before every run" (research/forward_evidence/
decision_function_*.py, gated by pre_reg_guard.verify_fn_hash() via
runner_base.py, one per signal_id registered in a runner_*.py file):

    g1_market_regime_sma200  -> decision_function_g1_market_regime.py
    h5_vol_regime            -> decision_function_h5.py
    g3_momentum_tsmom        -> decision_function_g3_tsmom.py
    g3_atr_expansion         -> decision_function_g3_atr_expansion.py
    ivrv_vrp_spread          -> decision_function_ivrv.py

Each frozen file is a self-contained, hash-pinned SECOND implementation of
a computation that also exists in research/evidence/signals.py -- the
"dual implementation without an equivalence guarantee" ADR-I010 names as
PS1. §8.3's resolution: the research implementation is authoritative; the
frozen implementation is a conformant binding that "must demonstrate
equivalence against the authoritative one." This module is that
demonstration -- for all 5, not just the one (`g3_atr_expansion`) that
already had a cross-validation test (`research/forward_evidence/tests/
test_atr_expansion.py::TestDecisionFnAtr::test_DFA07_cross_validation_
vs_signals_py`, synthetic data only).

NEITHER SIDE IS REWRITTEN. Every comparison below calls the existing,
unmodified `compute_signal()` from the frozen file and the existing,
unmodified `sig_*()` from signals.py, over the SAME real corpus data
(via research/evidence/engine.py's load_master(), the same corpus WBS 2.4
already migrated 17 features from). Three of the five decision functions
(g1, h5, g3_momentum) take a plain price Series and compare directly. The
other two need a documented indirection, both stated in the frozen files'
own docstrings, not invented here:

  - g3_atr_expansion: takes an OHLCV frame; compared via signals.py's
    sig_atr_expansion(m, n, lag), same as the existing DFA07 test.
  - ivrv_vrp_spread: its own docstring explicitly forbids comparing
    against sig_vrp_spread's column-consuming form in isolation, since
    that function only reads engine.py's already-computed `vrp_spread`
    column rather than recomputing it. The real equivalence question is
    whether decision_function_ivrv's SELF-CONTAINED rv30/dvol_frac/vrp
    reconstruction (from raw closes + raw dvol) agrees with engine.py's
    OWN vrp_spread construction -- i.e. whether the frozen file correctly
    replicates engine.py's pipeline, not just signals.py's gating logic.
    This test exercises exactly that: `m["vrp_spread"]` (engine.py's own
    column, the same one WBS 2.4 registered as an Active Feature
    Specification) is fed through sig_vrp_spread's tercile gate, and
    compared against decision_function_ivrv.compute_signal() being given
    the raw `m["close"]`/`m["dvol_close"]` series and reconstructing rv30
    and vrp itself. A divergence here would mean the frozen file's own
    "replicated to be identical to the engine's HV-2 VRP Spread
    construction" claim (its own docstring) is false.

g1's and h5's frozen files carry an explicit, analogous warning: "DO NOT
IMPORT or call sig_market_regime / sig_h5_vol_regimes from signals.py" --
NOT because those functions are wrong, but because sig_h5_vol_regimes
specifically reads a precomputed column (the same "not the thing being
hashed" reasoning as ivrv). g1's warning is about the ENGINE'S
preprocessing pipeline, not about column-reading -- sig_market_regime
IS self-contained and IS the correct comparison target; the two decision
functions' docstrings are read carefully below rather than a blanket
"never touch signals.py" rule.

IMPORT SAFETY (EH-002/INC-0003 precedent). signals.py itself does
`from engine import SignalSpec` at module level -- a second, transitive
instance of the exact `sys.modules['engine']` collision INC-0003 found
and fixed in evidence_engine_bindings.py. Importing signals.py naively
here would reintroduce the identical failure mode this milestone's own
predecessor already root-caused. _import_signals() below performs a
scoped, save-and-restore sys.modules['engine'] override -- exactly long
enough for signals.py's own import statement to resolve correctly,
never leaving a mutated global state behind for any other test file
(dq's own consumers of the bare "engine" name) to trip over.
"""
from __future__ import annotations

import contextlib
import sys
from pathlib import Path

import numpy as np
import pytest

pd = pytest.importorskip("pandas")

RESEARCH_ROOT = Path(__file__).resolve().parents[1] / "research"
FEATURE_STORE_DIR = RESEARCH_ROOT / "feature_store"
EVIDENCE_DIR = RESEARCH_ROOT / "evidence"
FORWARD_EVIDENCE_DIR = RESEARCH_ROOT / "forward_evidence"
for _p in (RESEARCH_ROOT, FEATURE_STORE_DIR, EVIDENCE_DIR, FORWARD_EVIDENCE_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import evidence_engine_bindings as beb  # noqa: E402 -- see beb.engine's own import-isolation docstring

engine = beb.engine

import decision_function_g1_market_regime as df_g1  # noqa: E402
import decision_function_h5 as df_h5  # noqa: E402
import decision_function_g3_tsmom as df_g3t  # noqa: E402
import decision_function_g3_atr_expansion as df_g3a  # noqa: E402
import decision_function_ivrv as df_ivrv  # noqa: E402


@contextlib.contextmanager
def _scoped_engine_module():
    """Makes sys.modules['engine'] resolve to the correct research/evidence/
    engine.py for exactly as long as it takes to import signals.py, then
    restores whatever was there before (or removes the key entirely if
    nothing was there) -- never a permanent global mutation."""
    had_prior = "engine" in sys.modules
    prior = sys.modules.get("engine")
    sys.modules["engine"] = engine
    try:
        yield
    finally:
        if had_prior:
            sys.modules["engine"] = prior
        else:
            del sys.modules["engine"]


def _import_signals():
    if "signals" in sys.modules:
        return sys.modules["signals"]
    with _scoped_engine_module():
        import signals  # noqa: PLC0415
    return signals


signals = _import_signals()

_REAL_DATA_PRESENT = (RESEARCH_ROOT / "data" / "raw" / "spot" / "BTCUSDT").exists()

pytestmark = pytest.mark.skipif(
    not _REAL_DATA_PRESENT, reason="real local research corpus not present"
)


@pytest.fixture(scope="module")
def master():
    return engine.load_master()


class TestG1MarketRegimeEquivalence:
    """g1_market_regime_sma200: decision_function_g1_market_regime.compute_signal()
    vs signals.sig_market_regime() -- both self-contained SMA(200)+slope(20)
    formulas over the same close series, per decision_function_g1_market_
    regime.py's own instruction that sig_market_regime is the correct
    comparison target (its warning is about the engine's preprocessing
    pipeline, not about sig_market_regime being column-derived -- it isn't)."""

    def test_matches_sig_market_regime_over_real_corpus(self, master):
        ref = signals.sig_market_regime(master, win=df_g1.WIN, lag=df_g1.LAG)
        got = df_g1.compute_signal(master["close"])
        np.testing.assert_array_equal(
            ref.values, got.values,
            err_msg="decision_function_g1_market_regime.compute_signal() diverges "
                    "from signals.sig_market_regime() on the real corpus",
        )

    def test_nontrivial_signal_present(self, master):
        got = df_g1.compute_signal(master["close"])
        assert got.abs().sum() > 0, "equivalence over an all-flat signal would be vacuous"


class TestH5VolRegimeEquivalence:
    """h5_vol_regime: decision_function_h5.compute_signal() vs signals.
    sig_volatility_regime() -- decision_function_h5.py's own docstring
    names sig_volatility_regime (not sig_h5_vol_regimes, which reads a
    precomputed column) as mathematically identical, with pctile=33
    equivalent to its own HIGH_PCTILE=67 (100 - 33 = 67), stated explicitly
    in that file's sensitivity_variants() docstring."""

    def test_matches_sig_volatility_regime_over_real_corpus(self, master):
        pctile = 100 - df_h5.HIGH_PCTILE
        ref = signals.sig_volatility_regime(
            master, rv_win=df_h5.RV_WINDOW, pctile=pctile, regime_win=df_h5.REGIME_WINDOW,
        )
        got = df_h5.compute_signal(master["close"])
        np.testing.assert_array_equal(
            ref.values, got.values,
            err_msg="decision_function_h5.compute_signal() diverges from "
                    "signals.sig_volatility_regime() on the real corpus",
        )

    def test_nontrivial_signal_present(self, master):
        got = df_h5.compute_signal(master["close"])
        assert (got == 1.0).sum() > 0
        assert (got == 0.0).sum() > 0


class TestG3MomentumTsmomEquivalence:
    """g3_momentum_tsmom: decision_function_g3_tsmom.compute_signal() vs
    signals.sig_momentum() -- both `close / close.shift(K) - 1` sign,
    self-contained, no precomputed-column indirection (unlike h5/ivrv)."""

    def test_matches_sig_momentum_over_real_corpus(self, master):
        ref = signals.sig_momentum(master, k=df_g3t.K)
        got = df_g3t.compute_signal(master["close"])
        np.testing.assert_array_equal(
            ref.values, got.values,
            err_msg="decision_function_g3_tsmom.compute_signal() diverges "
                    "from signals.sig_momentum() on the real corpus",
        )

    def test_nontrivial_signal_present(self, master):
        got = df_g3t.compute_signal(master["close"])
        assert got.abs().sum() > 0


class TestG3AtrExpansionEquivalence:
    """g3_atr_expansion: decision_function_g3_atr_expansion.compute_signal()
    vs signals.sig_atr_expansion() -- the one decision function that
    already had a cross-validation test (research/forward_evidence/tests/
    test_atr_expansion.py's DFA07), but only against synthetic data. This
    adds the real-corpus leg, per this milestone's explicit "use the
    existing research corpus where possible" requirement."""

    def test_matches_sig_atr_expansion_over_real_corpus(self, master):
        ref = signals.sig_atr_expansion(master, n=df_g3a.N, lag=df_g3a.LAG)
        got = df_g3a.compute_signal(master[["high", "low", "close"]])
        np.testing.assert_array_equal(
            ref.values, got.values,
            err_msg="decision_function_g3_atr_expansion.compute_signal() diverges "
                    "from signals.sig_atr_expansion() on the real corpus",
        )

    def test_nontrivial_signal_present(self, master):
        got = df_g3a.compute_signal(master[["high", "low", "close"]])
        assert got.abs().sum() > 0


class TestIvrvVrpSpreadEquivalence:
    """ivrv_vrp_spread: decision_function_ivrv.compute_signal() -- given
    raw closes + raw dvol, reconstructing rv30/dvol_frac/vrp itself --
    vs signals.sig_vrp_spread() applied to engine.py's OWN precomputed
    m["vrp_spread"] column (the same column WBS 2.4 registered as the
    Active `evidence_engine.vrp_spread` Feature Specification). Divergence
    here would mean the frozen file's self-contained reconstruction of
    engine.py's HV-2 VRP Spread pipeline is not actually identical to it,
    despite its own docstring's claim that it is."""

    def test_matches_sig_vrp_spread_over_real_corpus(self, master):
        ref = signals.sig_vrp_spread(master, pctile=df_ivrv.PCTILE, rw=df_ivrv.RW)
        got = df_ivrv.compute_signal(master["close"], master["dvol_close"])
        np.testing.assert_array_equal(
            ref.values, got.values,
            err_msg="decision_function_ivrv.compute_signal() diverges from "
                    "signals.sig_vrp_spread() (over engine.py's own vrp_spread "
                    "column) on the real corpus",
        )

    def test_nontrivial_signal_present(self, master):
        got = df_ivrv.compute_signal(master["close"], master["dvol_close"])
        assert got.abs().sum() > 0
