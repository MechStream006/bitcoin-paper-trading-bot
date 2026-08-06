"""
End-to-end conformance tests for research/ingest/funding.py -- WBS 1.6, the
first repetition of the dvol.py pilot pattern (engineering roadmap Phase 1,
`docs/engineering/implementation_roadmap_v1.md` v1.0.1 §6).

funding.py is the second-smallest-volume ingest path (3 rows/day, ~90/month
via CADENCE_MS), rolled out after dvol.py (1 row/day) and before
binance_ohlcv.py (1,440 rows/day) -- same smallest-volume-first ordering
used to pick the original pilot.

Structural note distinguishing this file from
tests/test_dvol_pilot_conformance.py: funding.py fetches and lands ONE
partition per ingest_month() call (no multi-month batched _fetch_range()),
so raw landing here lands the full per-call fetch directly -- there is no
month-slicing step to test.

Mirrors test_dvol_pilot_conformance.py's structure and monkeypatch
discipline (patch only the network boundary, _fetch_ccxt) throughout.
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

from ingest import funding  # noqa: E402
from storage import manifest as m  # noqa: E402
from storage import raw_lake  # noqa: E402


def _full_month(year: int, month: int, since_ms: int, until_ms: int, offset: float = 0.0) -> pd.DataFrame:
    """A complete set of funding payments on-grid for the whole month --
    matches gauntlet's coverage expectation for a clean GREEN/READY result."""
    ts = []
    t = since_ms
    while t <= until_ms:
        ts.append(t)
        t += funding.CADENCE_MS
    return pd.DataFrame({
        "timestamp_ms": ts,
        "funding_rate": [0.0001 * (i % 5) + offset for i in range(len(ts))],
    })


def _sparse_month(since_ms: int) -> pd.DataFrame:
    return pd.DataFrame({"timestamp_ms": [since_ms], "funding_rate": [0.0001]})


def _patch_full_month(monkeypatch, offset: float = 0.0):
    def fake(since_ms, until_ms):
        return _full_month(2024, 6, since_ms, until_ms, offset=offset)
    monkeypatch.setattr(funding, "_fetch_ccxt", fake)


class TestRawCanonicalSeparation:
    """WBS 1.2 pattern, reused."""

    def test_raw_lake_and_canonical_both_written(self, tmp_path, monkeypatch):
        _patch_full_month(monkeypatch)
        funding.ingest_month("BTCUSDT", 2024, 6, tmp_path)

        canonical = tmp_path / "funding" / "BTCUSDT" / "year=2024" / "month=06" / "data.parquet"
        raw_dir = raw_lake.raw_partition_dir(tmp_path, "funding", "BTCUSDT", 2024, 6)
        assert canonical.exists()
        assert list(raw_dir.glob("*.parquet"))

    def test_canonical_path_and_shape_unchanged_from_pre_milestone(self, tmp_path, monkeypatch):
        _patch_full_month(monkeypatch)
        result = funding.ingest_month("BTCUSDT", 2024, 6, tmp_path)
        canonical = tmp_path / "funding" / "BTCUSDT" / "year=2024" / "month=06" / "data.parquet"
        df = pd.read_parquet(canonical)
        assert len(df) == result["rows"]
        assert list(df.columns) == ["timestamp_ms", "funding_rate"]


class TestContentHashRevisioning:
    """WBS 1.3 pattern, reused."""

    def test_reingest_identical_content_creates_no_new_raw_revision(self, tmp_path, monkeypatch):
        _patch_full_month(monkeypatch)
        funding.ingest_month("BTCUSDT", 2024, 6, tmp_path)
        second = funding.ingest_month("BTCUSDT", 2024, 6, tmp_path, force=True)
        assert second["raw_is_new_revision"] is False
        assert len(raw_lake.read_revisions(tmp_path, "funding", "BTCUSDT", 2024, 6)) == 1

    def test_reingest_different_content_creates_a_new_revision_never_overwriting(self, tmp_path, monkeypatch):
        _patch_full_month(monkeypatch, offset=0.0)
        first = funding.ingest_month("BTCUSDT", 2024, 6, tmp_path)

        _patch_full_month(monkeypatch, offset=5.0)
        second = funding.ingest_month("BTCUSDT", 2024, 6, tmp_path, force=True)

        assert second["raw_is_new_revision"] is True
        assert second["raw_hash"] != first["raw_hash"]

        raw_dir = raw_lake.raw_partition_dir(tmp_path, "funding", "BTCUSDT", 2024, 6)
        assert (raw_dir / f"{first['raw_hash']}.parquet").exists()
        assert (raw_dir / f"{second['raw_hash']}.parquet").exists()

    def test_manifest_records_the_raw_content_hash(self, tmp_path, monkeypatch):
        _patch_full_month(monkeypatch)
        result = funding.ingest_month("BTCUSDT", 2024, 6, tmp_path)
        manifest_path = tmp_path / "funding" / "BTCUSDT" / "year=2024" / "month=06" / "_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["raw_content_hash"] == result["raw_hash"]


class TestCertificationWiring:
    """WBS 1.4 pattern, reused."""

    def test_clean_full_coverage_month_is_green_ready(self, tmp_path, monkeypatch):
        _patch_full_month(monkeypatch)
        result = funding.ingest_month("BTCUSDT", 2024, 6, tmp_path)
        assert result["certification"] == "GREEN"
        assert result["readiness"] == "READY"
        assert result["admissibility"]["certification"] == "GREEN"

    def test_severely_incomplete_month_is_red_not_ready(self, tmp_path, monkeypatch):
        since_ms, _until_ms = funding._month_bounds_ms(2024, 6)
        monkeypatch.setattr(funding, "_fetch_ccxt", lambda s, e: _sparse_month(since_ms))
        result = funding.ingest_month("BTCUSDT", 2024, 6, tmp_path)
        assert result["certification"] == "RED"
        assert result["readiness"] == "NOT_READY"

    def test_a_low_ceiling_never_blocks_the_write(self, tmp_path, monkeypatch):
        since_ms, _until_ms = funding._month_bounds_ms(2024, 6)
        monkeypatch.setattr(funding, "_fetch_ccxt", lambda s, e: _sparse_month(since_ms))
        funding.ingest_month("BTCUSDT", 2024, 6, tmp_path)
        canonical = tmp_path / "funding" / "BTCUSDT" / "year=2024" / "month=06" / "data.parquet"
        assert canonical.exists()
        assert (canonical.parent / "_manifest.json").exists()


class TestConditioningMandatory:
    """WBS 1.5 pattern, reused."""

    def test_canonical_manifest_always_carries_conditioning(self, tmp_path, monkeypatch):
        _patch_full_month(monkeypatch)
        funding.ingest_month("BTCUSDT", 2024, 6, tmp_path)
        manifest = json.loads(
            (tmp_path / "funding" / "BTCUSDT" / "year=2024" / "month=06" / "_manifest.json").read_text())
        assert manifest["conditioning"] == funding.CONDITIONING
        assert manifest["conditioning"].strip() != ""

    def test_raw_lake_revision_always_carries_conditioning(self, tmp_path, monkeypatch):
        _patch_full_month(monkeypatch)
        funding.ingest_month("BTCUSDT", 2024, 6, tmp_path)
        revisions = raw_lake.read_revisions(tmp_path, "funding", "BTCUSDT", 2024, 6)
        assert revisions[0]["conditioning"] == funding.CONDITIONING


class TestBackwardCompatibility:
    def test_skip_logic_unaffected_no_raw_or_gauntlet_call_on_skip(self, tmp_path, monkeypatch):
        calls = {"fetch": 0}

        def fake(since_ms, until_ms):
            calls["fetch"] += 1
            return _full_month(2024, 6, since_ms, until_ms)

        monkeypatch.setattr(funding, "_fetch_ccxt", fake)
        funding.ingest_month("BTCUSDT", 2024, 6, tmp_path)
        second = funding.ingest_month("BTCUSDT", 2024, 6, tmp_path)  # force=False default
        assert second["skipped"] is True
        assert calls["fetch"] == 1

    def test_all_pre_existing_manifest_fields_still_present(self, tmp_path, monkeypatch):
        _patch_full_month(monkeypatch)
        funding.ingest_month("BTCUSDT", 2024, 6, tmp_path)
        manifest = json.loads(
            (tmp_path / "funding" / "BTCUSDT" / "year=2024" / "month=06" / "_manifest.json").read_text())
        pre_existing = [
            "dataset", "symbol", "year", "month", "cadence_ms", "source",
            "ccxt_exchange", "ccxt_symbol", "rows", "ingest_ts", "skipped",
            "normalization_contract_version",
        ]
        for field in pre_existing:
            assert field in manifest, f"pre-existing field {field!r} missing"

    def test_pre_existing_result_dict_keys_still_present(self, tmp_path, monkeypatch):
        _patch_full_month(monkeypatch)
        result = funding.ingest_month("BTCUSDT", 2024, 6, tmp_path)
        for key in ("skipped", "dataset", "symbol", "year", "month", "rows"):
            assert key in result
