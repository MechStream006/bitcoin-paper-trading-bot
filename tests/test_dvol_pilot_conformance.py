"""
End-to-end conformance tests for research/ingest/dvol.py as the Phase 1
pilot ingest path (engineering roadmap WBS 1.2-1.5; ADR-I009).

dvol.py was chosen as the pilot per the roadmap's own instruction to start
with the smallest-volume ingest path: DVOL is 1 row/day (~30/month), versus
funding's 3/day (~90/month) and binance_ohlcv's 1,440/day (~43,200/month) --
confirmed via research/quality/gauntlet.py's own _RECORDS_PER_DAY table.

These tests exercise the real, unmodified ingest_range() through its public
entry point, monkeypatching only the network boundary (_fetch_range) --
matching tests/test_ingest_normalization_contract_version.py's established
pattern from the prior milestone.

Covers, together, WBS 1.2 (raw/canonical separation), WBS 1.3 (content-hash
revisioning, never overwriting), WBS 1.4 (certification wired into an
admissibility ceiling), WBS 1.5 (mandatory conditioning), and backward
compatibility with the pre-existing canonical output path and its real
downstream consumer, research/build_dvol_corpus.py.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")

RESEARCH_ROOT = Path(__file__).resolve().parents[1] / "research"
sys.path.insert(0, str(RESEARCH_ROOT))

from ingest import dvol  # noqa: E402
from storage import manifest as m  # noqa: E402
from storage import raw_lake  # noqa: E402


def _full_month(year: int, month: int, offset: float = 0.0) -> pd.DataFrame:
    """A complete, clean 30-day June (matches gauntlet's coverage
    expectation exactly, so the pilot's certification/readiness path can be
    tested honestly at GREEN/READY as well as at degraded levels)."""
    import calendar
    days = calendar.monthrange(year, month)[1]
    base = int(datetime(year, month, 1, tzinfo=timezone.utc).timestamp() * 1_000)
    return pd.DataFrame({
        "open_time": [base + i * dvol.DAY_MS for i in range(days)],
        "open":  [50.0 + i + offset for i in range(days)],
        "high":  [51.0 + i + offset for i in range(days)],
        "low":   [49.0 + i + offset for i in range(days)],
        "close": [50.5 + i + offset for i in range(days)],
    })


def _sparse_month(year: int, month: int) -> pd.DataFrame:
    """Severely incomplete -- deliberately triggers gauntlet CRITICAL
    coverage, for testing the honestly-degraded path."""
    base = int(datetime(year, month, 1, tzinfo=timezone.utc).timestamp() * 1_000)
    return pd.DataFrame({
        "open_time": [base],
        "open": [50.0], "high": [51.0], "low": [49.0], "close": [50.5],
    })


class TestRawCanonicalSeparation:
    """WBS 1.2."""

    def test_raw_lake_and_canonical_both_written(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dvol, "_fetch_range", lambda s, e: _full_month(2024, 6))
        dvol.ingest_range("BTC", (2024, 6), (2024, 6), tmp_path)

        canonical = tmp_path / "dvol" / "BTC" / "year=2024" / "month=06" / "data.parquet"
        raw_dir = raw_lake.raw_partition_dir(tmp_path, "dvol", "BTC", 2024, 6)
        assert canonical.exists()
        assert raw_dir.exists()
        assert list(raw_dir.glob("*.parquet"))

    def test_raw_and_canonical_are_physically_separate_trees(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dvol, "_fetch_range", lambda s, e: _full_month(2024, 6))
        dvol.ingest_range("BTC", (2024, 6), (2024, 6), tmp_path)

        canonical_dir = tmp_path / "dvol"
        raw_dir = tmp_path / "_raw_lake"
        assert canonical_dir.exists() and raw_dir.exists()
        # Neither is nested inside the other.
        assert raw_dir not in canonical_dir.parents
        assert canonical_dir not in raw_dir.parents

    def test_canonical_path_and_shape_unchanged_from_pre_milestone(self, tmp_path, monkeypatch):
        # Backward compatibility: existing consumers (research/build_dvol_corpus.py)
        # read canonical data at this exact fixed path.
        monkeypatch.setattr(dvol, "_fetch_range", lambda s, e: _full_month(2024, 6))
        results = dvol.ingest_range("BTC", (2024, 6), (2024, 6), tmp_path)
        canonical = tmp_path / "dvol" / "BTC" / "year=2024" / "month=06" / "data.parquet"
        df = pd.read_parquet(canonical)
        assert len(df) == results[0]["rows"] == 30
        assert list(df.columns) == ["open_time", "open", "high", "low", "close"]


class TestContentHashRevisioning:
    """WBS 1.3."""

    def test_reingest_identical_content_creates_no_new_raw_revision(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dvol, "_fetch_range", lambda s, e: _full_month(2024, 6))
        dvol.ingest_range("BTC", (2024, 6), (2024, 6), tmp_path)
        second = dvol.ingest_range("BTC", (2024, 6), (2024, 6), tmp_path, force=True)
        assert second[0]["raw_is_new_revision"] is False

        revisions = raw_lake.read_revisions(tmp_path, "dvol", "BTC", 2024, 6)
        assert len(revisions) == 1

    def test_reingest_different_content_creates_a_new_revision_never_overwriting(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dvol, "_fetch_range", lambda s, e: _full_month(2024, 6))
        first = dvol.ingest_range("BTC", (2024, 6), (2024, 6), tmp_path)

        monkeypatch.setattr(dvol, "_fetch_range", lambda s, e: _full_month(2024, 6, offset=500.0))
        second = dvol.ingest_range("BTC", (2024, 6), (2024, 6), tmp_path, force=True)

        assert second[0]["raw_is_new_revision"] is True
        assert second[0]["raw_hash"] != first[0]["raw_hash"]

        revisions = raw_lake.read_revisions(tmp_path, "dvol", "BTC", 2024, 6)
        assert len(revisions) == 2
        # The first revision's file still exists -- never overwritten.
        raw_dir = raw_lake.raw_partition_dir(tmp_path, "dvol", "BTC", 2024, 6)
        assert (raw_dir / f"{first[0]['raw_hash']}.parquet").exists()
        assert (raw_dir / f"{second[0]['raw_hash']}.parquet").exists()

    def test_canonical_manifest_records_the_raw_content_hash(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dvol, "_fetch_range", lambda s, e: _full_month(2024, 6))
        results = dvol.ingest_range("BTC", (2024, 6), (2024, 6), tmp_path)
        manifest = m.read_manifest(tmp_path / "dvol" / "BTC" / "year=2024" / "month=06" / "data.parquet")
        assert manifest["raw_content_hash"] == results[0]["raw_hash"]


class TestCertificationWiring:
    """WBS 1.4."""

    def test_clean_full_coverage_month_is_green_ready(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dvol, "_fetch_range", lambda s, e: _full_month(2024, 6))
        results = dvol.ingest_range("BTC", (2024, 6), (2024, 6), tmp_path)
        assert results[0]["certification"] == "GREEN"
        assert results[0]["readiness"] == "READY"

        manifest = m.read_manifest(tmp_path / "dvol" / "BTC" / "year=2024" / "month=06" / "data.parquet")
        assert manifest["admissibility"]["certification"] == "GREEN"
        assert manifest["admissibility"]["readiness"] == "READY"
        assert manifest["admissibility"]["lattice_version"]

    def test_severely_incomplete_month_is_red_not_ready(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dvol, "_fetch_range", lambda s, e: _sparse_month(2024, 6))
        results = dvol.ingest_range("BTC", (2024, 6), (2024, 6), tmp_path)
        assert results[0]["certification"] == "RED"
        assert results[0]["readiness"] == "NOT_READY"

    def test_a_low_ceiling_never_blocks_the_write(self, tmp_path, monkeypatch):
        # ADR-I012 DA3: "A dataset seals successfully at any ceiling. A low
        # ceiling is a label, not a failure." -- the canonical partition and
        # its manifest must exist even when certification is RED.
        monkeypatch.setattr(dvol, "_fetch_range", lambda s, e: _sparse_month(2024, 6))
        dvol.ingest_range("BTC", (2024, 6), (2024, 6), tmp_path)
        canonical = tmp_path / "dvol" / "BTC" / "year=2024" / "month=06" / "data.parquet"
        assert canonical.exists()
        assert (canonical.parent / "_manifest.json").exists()


class TestConditioningMandatory:
    """WBS 1.5."""

    def test_canonical_manifest_always_carries_conditioning(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dvol, "_fetch_range", lambda s, e: _full_month(2024, 6))
        dvol.ingest_range("BTC", (2024, 6), (2024, 6), tmp_path)
        manifest = m.read_manifest(tmp_path / "dvol" / "BTC" / "year=2024" / "month=06" / "data.parquet")
        assert manifest["conditioning"] == dvol.CONDITIONING
        assert manifest["conditioning"].strip() != ""

    def test_raw_lake_revision_always_carries_conditioning(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dvol, "_fetch_range", lambda s, e: _full_month(2024, 6))
        dvol.ingest_range("BTC", (2024, 6), (2024, 6), tmp_path)
        revisions = raw_lake.read_revisions(tmp_path, "dvol", "BTC", 2024, 6)
        assert revisions[0]["conditioning"] == dvol.CONDITIONING


class TestBackwardCompatibility:
    def test_skip_logic_unaffected_no_raw_or_gauntlet_call_on_skip(self, tmp_path, monkeypatch):
        calls = {"fetch": 0}

        def fake_fetch(s, e):
            calls["fetch"] += 1
            return _full_month(2024, 6)

        monkeypatch.setattr(dvol, "_fetch_range", fake_fetch)
        dvol.ingest_range("BTC", (2024, 6), (2024, 6), tmp_path)
        second = dvol.ingest_range("BTC", (2024, 6), (2024, 6), tmp_path)  # force=False (default)
        assert second[0]["skipped"] is True
        assert calls["fetch"] == 1  # no second network call for the skipped month

    def test_all_pre_existing_manifest_fields_still_present(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dvol, "_fetch_range", lambda s, e: _full_month(2024, 6))
        dvol.ingest_range("BTC", (2024, 6), (2024, 6), tmp_path)
        manifest = m.read_manifest(tmp_path / "dvol" / "BTC" / "year=2024" / "month=06" / "data.parquet")
        pre_existing = [
            "schema_version", "dataset", "symbol", "year", "month",
            "parquet_file", "parquet_sha256", "rows", "min_ts", "max_ts",
            "ts_column", "source", "source_url", "ingest_ts", "config_version",
            "normalization_contract_version",
        ]
        for field in pre_existing:
            assert field in manifest, f"pre-existing field {field!r} missing"

    def test_pre_existing_result_dict_keys_still_present(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dvol, "_fetch_range", lambda s, e: _full_month(2024, 6))
        results = dvol.ingest_range("BTC", (2024, 6), (2024, 6), tmp_path)
        for key in ("skipped", "dataset", "symbol", "year", "month", "rows"):
            assert key in results[0]

    def test_verify_hash_still_works_on_canonical_output(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dvol, "_fetch_range", lambda s, e: _full_month(2024, 6))
        dvol.ingest_range("BTC", (2024, 6), (2024, 6), tmp_path)
        canonical = tmp_path / "dvol" / "BTC" / "year=2024" / "month=06" / "data.parquet"
        assert m.verify_hash(canonical) is True
