"""
Tests for research/feature_store/evidence_engine_bindings.py (engineering
roadmap Phase 2, WBS 2.4 -- Feature Specification migration).

Two tiers:
  - Portable, mocked tests: engine.load_master() is monkeypatched with a
    small synthetic DataFrame, proving each binding extracts exactly the
    right column and nothing else -- runs anywhere, no real data required.
  - Real-data-gated tests (TestAgainstRealLocalData): skipped unless the
    real local research corpus is present (research/data/raw/), mirroring
    tests/test_migration_harness.py's REAL_LEDGER.exists()-style local-only
    pattern. These are what actually demonstrates behavioural equivalence
    against the genuine, unmodified research/evidence/engine.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")

RESEARCH_ROOT = Path(__file__).resolve().parents[1] / "research"
for _p in (RESEARCH_ROOT, RESEARCH_ROOT / "evidence", RESEARCH_ROOT / "feature_store"):
    sys.path.insert(0, str(_p))

import evidence_engine_bindings as beb  # noqa: E402

# NOT "import engine" -- sys.modules is process-global and research/dq/
# engine.py (a different module) also registers under the bare name
# "engine"; whichever wins the race shadows the other for every later bare
# import in the process (see evidence_engine_bindings.py's
# _load_evidence_engine() docstring). Using beb's own loaded module object
# guarantees this test observes/patches the same module the bindings
# actually call, regardless of pytest collection order.
engine = beb.engine

_REAL_DATA_PRESENT = (RESEARCH_ROOT / "data" / "raw" / "spot" / "BTCUSDT").exists()


def _fake_master(**columns) -> pd.DataFrame:
    return pd.DataFrame(columns)


class TestBindingsExtractTheRightColumn:
    """Each binding must extract exactly its own named column -- the ONLY
    logic this module adds over calling load_master() directly."""

    @pytest.mark.parametrize("name,fn", sorted(beb.BINDINGS.items()))
    def test_binding_extracts_its_named_column(self, monkeypatch, name, fn):
        marker = [1.0, 2.0, 3.0]
        monkeypatch.setattr(engine, "load_master", lambda: _fake_master(**{name: marker}))
        assert fn() == marker

    def test_binding_does_not_accidentally_extract_a_different_column(self, monkeypatch):
        monkeypatch.setattr(
            engine, "load_master",
            lambda: _fake_master(rv_21d=[1.0, 2.0], vol_regime=["high", "low"]),
        )
        assert beb.rv_21d() == [1.0, 2.0]
        assert beb.vol_regime() == ["high", "low"]

    def test_nan_values_round_trip_through_the_extraction(self, monkeypatch):
        monkeypatch.setattr(engine, "load_master", lambda: _fake_master(rv_21d=[float("nan"), 1.0]))
        result = beb.rv_21d()
        assert len(result) == 2
        assert result[0] != result[0]  # NaN != NaN, confirming it IS NaN, not silently dropped


class TestBindingsRegistry:
    def test_seventeen_bindings_are_declared(self):
        assert len(beb.BINDINGS) == 17

    def test_ret_fwd1_is_not_among_the_bindings(self):
        # ADR-I011 Central Asymmetry: ret_fwd1 depends on data after t --
        # it is a label, not a feature. Must never be wrapped here.
        assert "ret_fwd1" not in beb.BINDINGS

    def test_every_declared_binding_is_independently_callable(self):
        for name, fn in beb.BINDINGS.items():
            assert callable(fn), f"{name} is not callable"


@pytest.mark.skipif(not _REAL_DATA_PRESENT, reason="real local research corpus not present")
class TestAgainstRealLocalData:
    """The genuine behavioural-equivalence demonstration: every binding
    compared against calling load_master() directly, over real data."""

    @pytest.mark.parametrize("name,fn", sorted(beb.BINDINGS.items()))
    def test_binding_matches_direct_load_master_call(self, name, fn):
        import json
        from_binding = fn()
        from_direct = engine.load_master()[name].tolist()
        assert json.dumps(from_binding, sort_keys=True) == json.dumps(from_direct, sort_keys=True)

    def test_real_data_has_nontrivial_row_count(self):
        assert len(engine.load_master()) > 1000
