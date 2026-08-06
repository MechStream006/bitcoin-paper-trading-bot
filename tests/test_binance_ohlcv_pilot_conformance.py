"""
End-to-end conformance tests for research/ingest/binance_ohlcv.py -- WBS 1.6,
the second (and final) repetition of the dvol.py pilot pattern (engineering
roadmap Phase 1, `docs/engineering/implementation_roadmap_v1.md` v1.0.1 §6).

binance_ohlcv.py is the largest-volume ingest path (1,440 rows/day,
~43,200/month), rolled out last -- after dvol.py (1/day) and funding.py
(3/day) -- per the roadmap's own smallest-volume-first ordering, extended
consistently to the remaining paths.

Structural notes distinguishing this file:
  - `dataset` ("spot"/"perp") is a per-call parameter, so `_conditioning()`
    is a function here, not a module constant (unlike dvol.CONDITIONING /
    funding.CONDITIONING) -- covered by TestConditioningMandatory below.
  - The network boundary has two functions (_fetch_bulk primary,
    _fetch_ccxt fallback); these tests patch _fetch_bulk only, matching
    tests/test_ingest_normalization_contract_version.py's existing pattern
    from the prior milestone.

Mirrors test_dvol_pilot_conformance.py / test_funding_pilot_conformance.py's
structure and monkeypatch discipline throughout.
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

from ingest import binance_ohlcv as bo  # noqa: E402
from storage import raw_lake  # noqa: E402


def _full_month(year: int, month: int, offset: float = 0.0) -> pd.DataFrame:
    import calendar
    days = calendar.monthrange(year, month)[1]
    base = int(datetime(year, month, 1, tzinfo=timezone.utc).timestamp() * 1_000)
    n = 1_440 * days
    return pd.DataFrame({
        "open_time": [base + i * 60_000 for i in range(n)],
        "open":  [60_000.0 + offset] * n,
        "high":  [60_010.0 + offset] * n,
        "low":   [59_990.0 + offset] * n,
        "close": [60_005.0 + offset] * n,
        "volume": [1.0] * n,
    })


def _sparse_month(year: int, month: int) -> pd.DataFrame:
    base = int(datetime(year, month, 1, tzinfo=timezone.utc).timestamp() * 1_000)
    return pd.DataFrame({
        "open_time": [base], "open": [60_000.0], "high": [60_010.0],
        "low": [59_990.0], "close": [60_005.0], "volume": [1.0],
    })


def _manifest(tmp_path, dataset="spot", symbol="BTCUSDT", year=2024, month=6) -> dict:
    p = tmp_path / dataset / symbol / f"year={year}" / f"month={month:02d}" / "_manifest.json"
    return json.loads(p.read_text(encoding="utf-8"))


class TestRawCanonicalSeparation:
    """WBS 1.2 pattern, reused."""

    def test_raw_lake_and_canonical_both_written(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bo, "_fetch_bulk", lambda url: _full_month(2024, 6))
        bo.ingest_month("spot", "BTCUSDT", 2024, 6, tmp_path)

        canonical = tmp_path / "spot" / "BTCUSDT" / "year=2024" / "month=06" / "data.parquet"
        raw_dir = raw_lake.raw_partition_dir(tmp_path, "spot", "BTCUSDT", 2024, 6)
        assert canonical.exists()
        assert list(raw_dir.glob("*.parquet"))

    def test_canonical_path_and_shape_unchanged_from_pre_milestone(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bo, "_fetch_bulk", lambda url: _full_month(2024, 6))
        result = bo.ingest_month("spot", "BTCUSDT", 2024, 6, tmp_path)
        canonical = tmp_path / "spot" / "BTCUSDT" / "year=2024" / "month=06" / "data.parquet"
        df = pd.read_parquet(canonical)
        assert len(df) == result["rows"]
        assert list(df.columns) == ["open_time", "open", "high", "low", "close", "volume"]

    def test_perp_dataset_lands_under_its_own_tree(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bo, "_fetch_bulk", lambda url: _full_month(2024, 6))
        bo.ingest_month("perp", "BTCUSDT", 2024, 6, tmp_path)
        assert (tmp_path / "perp" / "BTCUSDT" / "year=2024" / "month=06" / "data.parquet").exists()
        assert raw_lake.raw_partition_dir(tmp_path, "perp", "BTCUSDT", 2024, 6).exists()


class TestContentHashRevisioning:
    """WBS 1.3 pattern, reused."""

    def test_reingest_identical_content_creates_no_new_raw_revision(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bo, "_fetch_bulk", lambda url: _full_month(2024, 6))
        bo.ingest_month("spot", "BTCUSDT", 2024, 6, tmp_path)
        second = bo.ingest_month("spot", "BTCUSDT", 2024, 6, tmp_path, force=True)
        assert second["raw_is_new_revision"] is False
        assert len(raw_lake.read_revisions(tmp_path, "spot", "BTCUSDT", 2024, 6)) == 1

    def test_reingest_different_content_creates_a_new_revision_never_overwriting(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bo, "_fetch_bulk", lambda url: _full_month(2024, 6))
        first = bo.ingest_month("spot", "BTCUSDT", 2024, 6, tmp_path)

        monkeypatch.setattr(bo, "_fetch_bulk", lambda url: _full_month(2024, 6, offset=500.0))
        second = bo.ingest_month("spot", "BTCUSDT", 2024, 6, tmp_path, force=True)

        assert second["raw_is_new_revision"] is True
        assert second["raw_hash"] != first["raw_hash"]

        raw_dir = raw_lake.raw_partition_dir(tmp_path, "spot", "BTCUSDT", 2024, 6)
        assert (raw_dir / f"{first['raw_hash']}.parquet").exists()
        assert (raw_dir / f"{second['raw_hash']}.parquet").exists()

    def test_manifest_records_the_raw_content_hash(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bo, "_fetch_bulk", lambda url: _full_month(2024, 6))
        result = bo.ingest_month("spot", "BTCUSDT", 2024, 6, tmp_path)
        assert _manifest(tmp_path)["raw_content_hash"] == result["raw_hash"]


class TestCertificationWiring:
    """WBS 1.4 pattern, reused."""

    def test_clean_full_coverage_month_is_green_ready(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bo, "_fetch_bulk", lambda url: _full_month(2024, 6))
        result = bo.ingest_month("spot", "BTCUSDT", 2024, 6, tmp_path)
        assert result["certification"] == "GREEN"
        assert result["readiness"] == "READY"
        assert result["admissibility"]["certification"] == "GREEN"

    def test_severely_incomplete_month_is_red_not_ready(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bo, "_fetch_bulk", lambda url: _sparse_month(2024, 6))
        result = bo.ingest_month("spot", "BTCUSDT", 2024, 6, tmp_path)
        assert result["certification"] == "RED"
        assert result["readiness"] == "NOT_READY"

    def test_a_low_ceiling_never_blocks_the_write(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bo, "_fetch_bulk", lambda url: _sparse_month(2024, 6))
        bo.ingest_month("spot", "BTCUSDT", 2024, 6, tmp_path)
        canonical = tmp_path / "spot" / "BTCUSDT" / "year=2024" / "month=06" / "data.parquet"
        assert canonical.exists()
        assert (canonical.parent / "_manifest.json").exists()


class TestConditioningMandatory:
    """WBS 1.5 pattern, reused. _conditioning() is a function here (dataset
    varies per call), unlike dvol.py/funding.py's module-level constants."""

    def test_canonical_manifest_always_carries_conditioning(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bo, "_fetch_bulk", lambda url: _full_month(2024, 6))
        bo.ingest_month("spot", "BTCUSDT", 2024, 6, tmp_path)
        manifest = _manifest(tmp_path)
        assert manifest["conditioning"] == bo._conditioning("spot")
        assert manifest["conditioning"].strip() != ""

    def test_spot_and_perp_conditioning_differ(self):
        assert bo._conditioning("spot") != bo._conditioning("perp")
        assert bo._conditioning("spot").strip() != ""
        assert bo._conditioning("perp").strip() != ""

    def test_raw_lake_revision_always_carries_conditioning(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bo, "_fetch_bulk", lambda url: _full_month(2024, 6))
        bo.ingest_month("spot", "BTCUSDT", 2024, 6, tmp_path)
        revisions = raw_lake.read_revisions(tmp_path, "spot", "BTCUSDT", 2024, 6)
        assert revisions[0]["conditioning"] == bo._conditioning("spot")


class TestBackwardCompatibility:
    def test_skip_logic_unaffected_no_raw_or_gauntlet_call_on_skip(self, tmp_path, monkeypatch):
        calls = {"fetch": 0}

        def fake(url):
            calls["fetch"] += 1
            return _full_month(2024, 6)

        monkeypatch.setattr(bo, "_fetch_bulk", fake)
        bo.ingest_month("spot", "BTCUSDT", 2024, 6, tmp_path)
        second = bo.ingest_month("spot", "BTCUSDT", 2024, 6, tmp_path)  # force=False default
        assert second["skipped"] is True
        assert calls["fetch"] == 1

    def test_all_pre_existing_manifest_fields_still_present(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bo, "_fetch_bulk", lambda url: _full_month(2024, 6))
        bo.ingest_month("spot", "BTCUSDT", 2024, 6, tmp_path)
        manifest = _manifest(tmp_path)
        pre_existing = [
            "dataset", "symbol", "year", "month", "interval", "source",
            "source_url", "rows", "ingest_ts", "skipped",
            "normalization_contract_version",
        ]
        for field in pre_existing:
            assert field in manifest, f"pre-existing field {field!r} missing"

    def test_pre_existing_result_dict_keys_still_present(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bo, "_fetch_bulk", lambda url: _full_month(2024, 6))
        result = bo.ingest_month("spot", "BTCUSDT", 2024, 6, tmp_path)
        for key in ("skipped", "dataset", "symbol", "year", "month", "rows"):
            assert key in result

    def test_ccxt_fallback_path_still_reachable_when_bulk_returns_none(self, tmp_path, monkeypatch):
        # Pre-existing behaviour: bulk returning None triggers the CCXT
        # fallback. Confirmed still wired correctly end to end.
        monkeypatch.setattr(bo, "_fetch_bulk", lambda url: None)
        monkeypatch.setattr(bo, "_fetch_ccxt", lambda dataset, sym, y, m: _full_month(2024, 6))
        result = bo.ingest_month("spot", "BTCUSDT", 2024, 6, tmp_path)
        assert result["source"] == "ccxt"
        assert result["source_url"] is None
        assert result["certification"] == "GREEN"
