"""
Tests for research/quality/admissibility.py -- the certification-to-
admissibility bridge (engineering roadmap Phase 1, WBS 1.4; ADR-I009 §18,
ADR-I014).

Covers: the gauntlet-report-to-Certification/Readiness mapping against both
fabricated report dicts (exhaustive branch coverage) and a REAL
research/quality/gauntlet.py run (proving the bridge against the actual
function it wires, not only a mock of its output shape), partition_ceiling's
construction (including the not-applicable Maturity dimension), and
corpus_ceiling's delegation to research/admissibility/composition.meet().

Requires the research environment (pandas) for the real-gauntlet tests;
the fabricated-report tests need no research dependency at all (this
module imports pandas nowhere).
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

RESEARCH_ROOT = Path(__file__).resolve().parents[1] / "research"
sys.path.insert(0, str(RESEARCH_ROOT))

from quality import admissibility as qa  # noqa: E402
from lattice import Certification, Dimension, Integrity, Maturity, Provenance, Readiness  # noqa: E402


def report(critical=0, warning=0, findings=None):
    return {
        "critical_count": critical,
        "warning_count": warning,
        "findings": findings or [],
    }


class TestCertificationFromGauntlet:
    def test_no_findings_is_green(self):
        assert qa.certification_from_gauntlet(report()) == Certification.GREEN

    def test_warning_only_is_amber(self):
        assert qa.certification_from_gauntlet(report(warning=1)) == Certification.AMBER

    def test_any_critical_is_red(self):
        assert qa.certification_from_gauntlet(report(critical=1)) == Certification.RED

    def test_critical_outranks_warning(self):
        assert qa.certification_from_gauntlet(report(critical=1, warning=5)) == Certification.RED


class TestReadinessFromGauntlet:
    def test_no_coverage_finding_is_ready(self):
        assert qa.readiness_from_gauntlet(report()) == Readiness.READY

    def test_coverage_warning_is_partial(self):
        r = report(warning=1, findings=[{"check": "coverage_validation", "severity": "WARNING"}])
        assert qa.readiness_from_gauntlet(r) == Readiness.PARTIAL

    def test_coverage_critical_is_not_ready(self):
        r = report(critical=1, findings=[{"check": "coverage_validation", "severity": "CRITICAL"}])
        assert qa.readiness_from_gauntlet(r) == Readiness.NOT_READY

    def test_non_coverage_findings_do_not_affect_readiness(self):
        r = report(warning=1, findings=[{"check": "outlier_annotation", "severity": "WARNING"}])
        assert qa.readiness_from_gauntlet(r) == Readiness.READY


class TestPartitionCeiling:
    def test_defaults_and_shape(self):
        ceiling = qa.partition_ceiling(report())
        assert ceiling.provenance == Provenance.OFFLINE_CORPUS
        assert ceiling.certification == Certification.GREEN
        assert ceiling.readiness == Readiness.READY
        assert ceiling.integrity == Integrity.CLEAN

    def test_maturity_is_not_applicable(self):
        # ADR-I014 §5.1: a market-data partition has no maturity concept.
        ceiling = qa.partition_ceiling(report())
        assert ceiling.is_not_applicable(Dimension.MATURITY)
        assert ceiling.maturity == Maturity.FULL  # top rung, per §5.1's "not Bottom" rule

    def test_provenance_override(self):
        ceiling = qa.partition_ceiling(report(), provenance=Provenance.MAINNET)
        assert ceiling.provenance == Provenance.MAINNET

    def test_degraded_report_yields_a_degraded_ceiling(self):
        r = report(critical=1, findings=[{"check": "coverage_validation", "severity": "CRITICAL"}])
        ceiling = qa.partition_ceiling(r)
        assert ceiling.certification == Certification.RED
        assert ceiling.readiness == Readiness.NOT_READY


class TestCorpusCeiling:
    def test_meet_of_a_single_ceiling_is_itself(self):
        c = qa.partition_ceiling(report())
        assert qa.corpus_ceiling(c) == c

    def test_weakest_partition_determines_the_corpus_ceiling(self):
        clean = qa.partition_ceiling(report())
        broken = qa.partition_ceiling(report(critical=1))
        corpus = qa.corpus_ceiling(clean, broken)
        assert corpus.certification == Certification.RED

    def test_delegates_to_composition_meet(self):
        # Not a re-implementation -- a thin wrapper. Confirmed by identical
        # output to calling composition.meet() directly.
        import composition
        a = qa.partition_ceiling(report())
        b = qa.partition_ceiling(report(warning=1, findings=[
            {"check": "coverage_validation", "severity": "WARNING"}]))
        assert qa.corpus_ceiling(a, b) == composition.meet(a, b)


class TestAgainstRealGauntlet:
    """The bridge exercised against the real gauntlet.py, not a fabricated
    report shape -- proves the mapping holds for what run_gauntlet()
    actually returns."""

    def test_a_fully_covered_clean_month_is_green_ready(self):
        pd = pytest.importorskip("pandas")
        from quality.gauntlet import run_gauntlet

        base = int(datetime(2024, 6, 1, tzinfo=timezone.utc).timestamp() * 1_000)
        df = pd.DataFrame({
            "open_time": [base + i * 86_400_000 for i in range(30)],  # June has 30 days
            "close": [50.0 + i * 0.1 for i in range(30)],
        })
        rep = run_gauntlet(df, dataset="dvol", symbol="BTC", year=2024, month=6, ts_column="open_time")
        ceiling = qa.partition_ceiling(rep)
        assert ceiling.certification == Certification.GREEN
        assert ceiling.readiness == Readiness.READY

    def test_a_severely_incomplete_month_is_red_not_ready(self):
        pd = pytest.importorskip("pandas")
        from quality.gauntlet import run_gauntlet

        base = int(datetime(2024, 6, 1, tzinfo=timezone.utc).timestamp() * 1_000)
        df = pd.DataFrame({
            "open_time": [base],  # 1 of 30 expected days
            "close": [50.0],
        })
        rep = run_gauntlet(df, dataset="dvol", symbol="BTC", year=2024, month=6, ts_column="open_time")
        ceiling = qa.partition_ceiling(rep)
        assert ceiling.certification == Certification.RED
        assert ceiling.readiness == Readiness.NOT_READY
