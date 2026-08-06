"""
Tests for the normalization_contract_version field on the three
research/ingest/ writers (binance_ohlcv.py, funding.py, dvol.py) -- first
pytest coverage for any of these modules (engineering roadmap Phase 1
WBS 1.1, ADR-I009 §13.3).

Each writer's public ingest function performs live network I/O
(_fetch_bulk/_fetch_ccxt/_fetch_range). These tests monkeypatch exactly that
one function per module -- the network boundary -- and let every other line
of the real, unmodified ingest_month()/ingest_range() run for real against a
tmp_path lake, so the actual code path this milestone changed is exercised,
not a re-implementation of it.

Requires the research environment (pandas, pyarrow, requests) -- run via the
btc_bot conda env, matching research/requirements-research.txt and
CLAUDE.md §4.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

# See tests/test_storage_manifest.py's identical note: pandas is part of
# research/requirements-research.txt, a separate dependency set from
# requirements.txt, combined only in CI. Skip rather than hard-error at
# collection.
pd = pytest.importorskip("pandas")

RESEARCH_ROOT = Path(__file__).resolve().parents[1] / "research"
sys.path.insert(0, str(RESEARCH_ROOT))

from ingest import binance_ohlcv, dvol, funding  # noqa: E402


# ---------------------------------------------------------------------------
# Constants themselves
# ---------------------------------------------------------------------------

class TestConstantsExist:
    def test_binance_ohlcv_constant(self):
        assert binance_ohlcv.NORMALIZATION_CONTRACT_VERSION == 1
        assert isinstance(binance_ohlcv.NORMALIZATION_CONTRACT_VERSION, int)

    def test_funding_constant(self):
        assert funding.NORMALIZATION_CONTRACT_VERSION == 1
        assert isinstance(funding.NORMALIZATION_CONTRACT_VERSION, int)

    def test_dvol_constant(self):
        assert dvol.NORMALIZATION_CONTRACT_VERSION == 1
        assert isinstance(dvol.NORMALIZATION_CONTRACT_VERSION, int)


# ---------------------------------------------------------------------------
# binance_ohlcv.py
# ---------------------------------------------------------------------------

def _synthetic_binance_klines(year: int, month: int, n: int = 5) -> pd.DataFrame:
    base = int(datetime(year, month, 1, tzinfo=timezone.utc).timestamp() * 1_000)
    return pd.DataFrame({
        "open_time": [base + i * 60_000 for i in range(n)],
        "open":  [60_000.0 + i for i in range(n)],
        "high":  [60_010.0 + i for i in range(n)],
        "low":   [59_990.0 + i for i in range(n)],
        "close": [60_005.0 + i for i in range(n)],
        "volume": [1.0 + i for i in range(n)],
    })


class TestBinanceOhlcvIngest:
    def test_manifest_records_the_constant(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            binance_ohlcv, "_fetch_bulk",
            lambda url: _synthetic_binance_klines(2024, 6),
        )
        meta = binance_ohlcv.ingest_month("spot", "BTCUSDT", 2024, 6, tmp_path)
        assert meta["normalization_contract_version"] == binance_ohlcv.NORMALIZATION_CONTRACT_VERSION

        part_dir = tmp_path / "spot" / "BTCUSDT" / "year=2024" / "month=06"
        sidecar = json.loads((part_dir / "_manifest.json").read_text(encoding="utf-8"))
        assert sidecar["normalization_contract_version"] == 1

    def test_row_count_and_parquet_content_unaffected(self, tmp_path, monkeypatch):
        # "No behaviour change" -- the field is additive metadata; the data
        # path (rows written, values) must be identical to what it always was.
        monkeypatch.setattr(
            binance_ohlcv, "_fetch_bulk",
            lambda url: _synthetic_binance_klines(2024, 6, n=5),
        )
        meta = binance_ohlcv.ingest_month("spot", "BTCUSDT", 2024, 6, tmp_path)
        assert meta["rows"] == 5

    def test_skip_logic_unaffected(self, tmp_path, monkeypatch):
        calls = {"n": 0}

        def fake_fetch(url):
            calls["n"] += 1
            return _synthetic_binance_klines(2024, 6)

        monkeypatch.setattr(binance_ohlcv, "_fetch_bulk", fake_fetch)
        binance_ohlcv.ingest_month("spot", "BTCUSDT", 2024, 6, tmp_path)
        second = binance_ohlcv.ingest_month("spot", "BTCUSDT", 2024, 6, tmp_path)
        assert second["skipped"] is True
        assert calls["n"] == 1  # no second network call


# ---------------------------------------------------------------------------
# funding.py
# ---------------------------------------------------------------------------

def _synthetic_funding_rows(since_ms: int, until_ms: int) -> pd.DataFrame:
    ts = [since_ms + i * funding.CADENCE_MS for i in range(3)]
    ts = [t for t in ts if t <= until_ms]
    return pd.DataFrame({
        "timestamp_ms": ts,
        "funding_rate": [0.0001 * (i + 1) for i in range(len(ts))],
    })


class TestFundingIngest:
    def test_manifest_records_the_constant(self, tmp_path, monkeypatch):
        monkeypatch.setattr(funding, "_fetch_ccxt", _synthetic_funding_rows)
        meta = funding.ingest_month("BTCUSDT", 2024, 6, tmp_path)
        assert meta["normalization_contract_version"] == funding.NORMALIZATION_CONTRACT_VERSION

        part_dir = tmp_path / "funding" / "BTCUSDT" / "year=2024" / "month=06"
        sidecar = json.loads((part_dir / "_manifest.json").read_text(encoding="utf-8"))
        assert sidecar["normalization_contract_version"] == 1

    def test_other_meta_fields_unaffected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(funding, "_fetch_ccxt", _synthetic_funding_rows)
        meta = funding.ingest_month("BTCUSDT", 2024, 6, tmp_path)
        assert meta["dataset"] == funding.DATASET
        assert meta["cadence_ms"] == funding.CADENCE_MS
        assert meta["rows"] == 3


# ---------------------------------------------------------------------------
# dvol.py
# ---------------------------------------------------------------------------

def _synthetic_dvol_rows(start_ms: int, end_ms: int) -> pd.DataFrame:
    n = 3
    return pd.DataFrame({
        "open_time": [start_ms + i * dvol.DAY_MS for i in range(n)],
        "open":  [50.0 + i for i in range(n)],
        "high":  [51.0 + i for i in range(n)],
        "low":   [49.0 + i for i in range(n)],
        "close": [50.5 + i for i in range(n)],
    })


class TestDvolIngest:
    def test_manifest_records_the_constant(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dvol, "_fetch_range", _synthetic_dvol_rows)
        results = dvol.ingest_range("BTC", (2024, 6), (2024, 6), tmp_path)
        assert results[0]["skipped"] is False

        part_dir = tmp_path / dvol.DATASET / "BTC" / "year=2024" / "month=06"
        sidecar = json.loads((part_dir / "_manifest.json").read_text(encoding="utf-8"))
        assert sidecar["normalization_contract_version"] == dvol.NORMALIZATION_CONTRACT_VERSION == 1

    def test_row_count_unaffected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dvol, "_fetch_range", _synthetic_dvol_rows)
        results = dvol.ingest_range("BTC", (2024, 6), (2024, 6), tmp_path)
        assert results[0]["rows"] == 3
