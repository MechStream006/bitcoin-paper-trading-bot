"""
Tests for research/feature_store/conditioning.py (engineering roadmap
Phase 2, WBS 2.7 -- Conditioning Propagation Rule).

Two tiers:
  - Pure-logic tests (TestPropagateConditioning*, TestConditioningFor
    SpecificationWithMockedSourceMap): exercise propagate_conditioning()
    and conditioning_for_specification() with synthetic input strings and
    an injected source_map -- conditioning.py itself has no module-level
    pandas dependency (unlike evidence_engine_bindings.py/warmup.py's own
    ingest-writer imports, which are function-local here), so these run
    in the base env with no gating needed at all.
  - Real-source tests (TestSourceConditioningMap, TestConditioningFor
    SpecificationRealSources): call _source_conditioning_map(), which
    imports the real research/ingest/*.py writers (pandas-dependent) --
    individually gated with a class-level skipif, not a module-level
    importorskip, so the pure-logic tests above are never skipped
    alongside them.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

FEATURE_STORE_DIR = Path(__file__).resolve().parents[1] / "research" / "feature_store"
sys.path.insert(0, str(FEATURE_STORE_DIR))

import conditioning as cond  # noqa: E402

_PANDAS_AVAILABLE = importlib.util.find_spec("pandas") is not None


class TestPropagateConditioningPreserving:
    def test_single_input_is_unchanged(self):
        result = cond.propagate_conditioning("preserving", ["statement A"])
        assert result.statement == "statement A"
        assert result.input_statements == ("statement A",)

    def test_multiple_distinct_inputs_are_all_carried_forward(self):
        result = cond.propagate_conditioning("preserving", ["statement A", "statement B"])
        assert "statement A" in result.statement
        assert "statement B" in result.statement
        assert result.input_statements == ("statement A", "statement B")

    def test_duplicate_inputs_are_deduplicated(self):
        result = cond.propagate_conditioning("preserving", ["statement A", "statement A"])
        assert result.input_statements == ("statement A",)
        assert result.statement == "statement A"

    def test_order_is_preserved(self):
        result = cond.propagate_conditioning("preserving", ["Z", "A", "M"])
        assert result.input_statements == ("Z", "A", "M")

    def test_empty_inputs_raises(self):
        with pytest.raises(cond.ConditioningPropagationError):
            cond.propagate_conditioning("preserving", [])


class TestPropagateConditioningNarrowing:
    def test_requires_a_restriction(self):
        with pytest.raises(cond.ConditioningPropagationError):
            cond.propagate_conditioning("narrowing", ["statement A"])

    def test_restriction_is_appended(self):
        result = cond.propagate_conditioning(
            "narrowing", ["statement A"], restriction="ATM +/- 5 strikes",
        )
        assert "statement A" in result.statement
        assert "ATM +/- 5 strikes" in result.statement

    def test_empty_inputs_raises_even_with_restriction(self):
        with pytest.raises(cond.ConditioningPropagationError):
            cond.propagate_conditioning("narrowing", [], restriction="x")


class TestPropagateConditioningCrossSectional:
    def test_requires_a_universe_statement(self):
        with pytest.raises(cond.ConditioningPropagationError):
            cond.propagate_conditioning("cross-sectional", ["statement A"])

    def test_universe_statement_replaces_inputs_in_the_output(self):
        result = cond.propagate_conditioning(
            "cross-sectional", ["per-instrument A", "per-instrument B"],
            universe_statement="Top-50-by-market-cap universe, daily rebalanced",
        )
        assert result.statement == "Top-50-by-market-cap universe, daily rebalanced"
        assert "per-instrument A" not in result.statement

    def test_input_statements_still_recorded_for_audit_despite_replacement(self):
        """Conditioning is never DROPPED (ADR-I010 §9.1) -- cross-sectional
        replaces the output STATEMENT, but the inputs consulted are still
        on the result for audit."""
        result = cond.propagate_conditioning(
            "cross-sectional", ["per-instrument A", "per-instrument B"],
            universe_statement="universe X",
        )
        assert result.input_statements == ("per-instrument A", "per-instrument B")

    def test_empty_inputs_is_valid_for_cross_sectional(self):
        """Unlike preserving/narrowing, cross-sectional's output does not
        depend on having any input statements at all -- it replaces them."""
        result = cond.propagate_conditioning(
            "cross-sectional", [], universe_statement="universe X",
        )
        assert result.statement == "universe X"
        assert result.input_statements == ()


class TestPropagateConditioningRuleValidation:
    def test_unknown_rule_raises(self):
        with pytest.raises(cond.ConditioningPropagationError):
            cond.propagate_conditioning("bogus-rule", ["statement A"])


class TestConditioningForSpecificationWithMockedSourceMap:
    """conditioning_for_specification() with an injected source_map --
    proves the resolution logic without needing the real, pandas-dependent
    ingest writers."""

    def test_resolves_declared_sources_to_their_conditioning_text(self):
        source_map = {"fake_source": "fake conditioning text"}
        result = cond.conditioning_for_specification(
            "preserving",
            [{"source": "fake_source", "fields": ["x"]}],
            source_map=source_map,
        )
        assert result.statement == "fake conditioning text"

    def test_multi_source_specification_resolves_both(self):
        source_map = {"a": "conditioning A", "b": "conditioning B"}
        result = cond.conditioning_for_specification(
            "preserving",
            [{"source": "a", "fields": ["x"]}, {"source": "b", "fields": ["y"]}],
            source_map=source_map,
        )
        assert result.input_statements == ("conditioning A", "conditioning B")

    def test_unknown_source_raises(self):
        source_map = {"known_source": "text"}
        with pytest.raises(cond.ConditioningPropagationError):
            cond.conditioning_for_specification(
                "preserving",
                [{"source": "unknown_source", "fields": ["x"]}],
                source_map=source_map,
            )

    def test_narrowing_with_mocked_source_map(self):
        source_map = {"a": "conditioning A"}
        result = cond.conditioning_for_specification(
            "narrowing",
            [{"source": "a", "fields": ["x"]}],
            restriction="only Tuesdays",
            source_map=source_map,
        )
        assert "conditioning A" in result.statement
        assert "only Tuesdays" in result.statement


@pytest.mark.skipif(not _PANDAS_AVAILABLE, reason="pandas not installed (needed transitively by the real ingest writers)")
class TestSourceConditioningMap:
    """Real, pandas-dependent: imports the actual ingest writers."""

    def test_all_four_known_sources_present(self):
        m = cond._source_conditioning_map()
        assert set(m.keys()) == {"spot_1m", "perp_1m", "funding_raw", "dvol_daily"}

    def test_every_conditioning_text_is_nonempty(self):
        m = cond._source_conditioning_map()
        for source, text in m.items():
            assert text and len(text) > 20, f"{source} conditioning text looks empty/trivial"

    def test_spot_and_perp_are_distinct(self):
        m = cond._source_conditioning_map()
        assert m["spot_1m"] != m["perp_1m"]
        assert "spot" in m["spot_1m"].lower()
        assert "perpetual" in m["perp_1m"].lower()

    def test_does_not_shadow_stdlib_calendar(self):
        """Regression test for INC-0004: importing the ingest writers must
        never break a subsequent stdlib `import calendar`."""
        cond._source_conditioning_map()
        import calendar
        assert hasattr(calendar, "day_abbr"), (
            "stdlib calendar module was shadowed -- research/ingest/ must "
            "never be inserted at sys.path[0] (see INC-0004)"
        )

    def test_ingest_dir_appended_not_inserted_at_front(self):
        """Structural guard: research/ingest/ must be appended to sys.path,
        never inserted at index 0, per INC-0004."""
        cond._source_conditioning_map()
        assert str(cond._INGEST_DIR) in sys.path
        assert sys.path[0] != str(cond._INGEST_DIR)


@pytest.mark.skipif(not _PANDAS_AVAILABLE, reason="pandas not installed (needed transitively by the real ingest writers)")
class TestConditioningForSpecificationRealSources:
    def test_single_source_feature(self):
        result = cond.conditioning_for_specification(
            "preserving", [{"source": "spot_1m", "fields": ["close"]}],
        )
        assert "Binance spot" in result.statement

    def test_dual_source_feature_carries_both(self):
        result = cond.conditioning_for_specification(
            "preserving",
            [
                {"source": "dvol_daily", "fields": ["close"]},
                {"source": "spot_1m", "fields": ["close"]},
            ],
        )
        assert len(result.input_statements) == 2
        assert "Deribit DVOL" in result.statement
        assert "Binance spot" in result.statement
