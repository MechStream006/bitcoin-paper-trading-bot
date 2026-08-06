"""
Tests for research/storage/manifest.py -- first pytest coverage for this
module (previously exercised only by the standalone validate_wi6.py smoke
script, which stays untouched apart from one cosmetic label fix caused
directly by this milestone's SCHEMA_VERSION bump).

Covers: write_manifest()'s existing behaviour (backward compatibility,
unaffected by this milestone) plus the new normalization_contract_version
field (ADR-I009 §13.3, engineering roadmap Phase 1 WBS 1.1) -- additive,
optional, defaults to None, and does not alter parquet_sha256, rows, or any
other existing field.

Requires the research environment (pandas, pyarrow) -- run via the btc_bot
conda env, matching research/requirements-research.txt and CLAUDE.md §4.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

# research/requirements-research.txt (pandas, pyarrow) is a separate
# dependency set from requirements.txt (CLAUDE.md §4); CI combines both into
# one job, but a local runtime env may have only one installed. Skip rather
# than hard-error at collection, matching CLAUDE.md's own documented
# environment split -- collection-time ImportError would abort the entire
# `pytest tests/` run, not just this file.
pd = pytest.importorskip("pandas")
pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")

RESEARCH_ROOT = Path(__file__).resolve().parents[1] / "research"
sys.path.insert(0, str(RESEARCH_ROOT))

from storage import manifest as m  # noqa: E402


def _write_synthetic_parquet(path: Path, rows: int = 10) -> tuple[int, int, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    ts_base = int(datetime(2024, 6, 1, tzinfo=timezone.utc).timestamp() * 1_000)
    df = pd.DataFrame({
        "open_time": [ts_base + i * 60_000 for i in range(rows)],
        "open":  [60_000.0 + i for i in range(rows)],
        "close": [60_001.0 + i for i in range(rows)],
    })
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), path, compression="zstd")
    return rows, ts_base, ts_base + (rows - 1) * 60_000


@pytest.fixture
def parquet_path(tmp_path) -> tuple[Path, int, int, int]:
    p = tmp_path / "data.parquet"
    rows, min_ts, max_ts = _write_synthetic_parquet(p)
    return p, rows, min_ts, max_ts


def _known_meta(rows: int, min_ts: int, max_ts: int, **overrides) -> dict:
    defaults = dict(
        dataset="test_dataset", symbol="BTCUSDT", year=2024, month=6,
        source="synthetic", source_url=None, rows=rows, min_ts=min_ts,
        max_ts=max_ts, ts_column="open_time",
        ingest_ts=datetime.now(timezone.utc).isoformat(), config_version=1,
    )
    defaults.update(overrides)
    return defaults


class TestBackwardCompatibility:
    """Every pre-existing behaviour of write_manifest(), unaffected by this
    milestone -- normalization_contract_version is additive and optional."""

    def test_write_manifest_without_the_new_kwarg_still_works(self, parquet_path):
        p, rows, min_ts, max_ts = parquet_path
        manifest = m.write_manifest(p, **_known_meta(rows, min_ts, max_ts))
        assert manifest["dataset"] == "test_dataset"
        assert manifest["rows"] == rows

    def test_all_pre_existing_fields_still_present(self, parquet_path):
        p, rows, min_ts, max_ts = parquet_path
        manifest = m.write_manifest(p, **_known_meta(rows, min_ts, max_ts))
        pre_existing_fields = [
            "schema_version", "dataset", "symbol", "year", "month",
            "parquet_file", "parquet_sha256", "rows", "min_ts", "max_ts",
            "ts_column", "source", "source_url", "ingest_ts", "config_version",
        ]
        for field in pre_existing_fields:
            assert field in manifest, f"pre-existing field {field!r} missing"

    def test_sidecar_still_round_trips(self, parquet_path):
        p, rows, min_ts, max_ts = parquet_path
        manifest = m.write_manifest(p, **_known_meta(rows, min_ts, max_ts))
        on_disk = json.loads((p.parent / m.SIDECAR_NAME).read_text(encoding="utf-8"))
        assert on_disk == manifest

    def test_verify_hash_unaffected(self, parquet_path):
        p, rows, min_ts, max_ts = parquet_path
        m.write_manifest(p, **_known_meta(rows, min_ts, max_ts))
        assert m.verify_hash(p) is True


class TestNormalizationContractVersion:
    def test_defaults_to_none_when_omitted(self, parquet_path):
        p, rows, min_ts, max_ts = parquet_path
        manifest = m.write_manifest(p, **_known_meta(rows, min_ts, max_ts))
        assert manifest["normalization_contract_version"] is None

    def test_records_the_supplied_value(self, parquet_path):
        p, rows, min_ts, max_ts = parquet_path
        manifest = m.write_manifest(
            p, **_known_meta(rows, min_ts, max_ts),
            normalization_contract_version=3,
        )
        assert manifest["normalization_contract_version"] == 3

    def test_recorded_value_round_trips_through_the_sidecar(self, parquet_path):
        p, rows, min_ts, max_ts = parquet_path
        m.write_manifest(
            p, **_known_meta(rows, min_ts, max_ts),
            normalization_contract_version=2,
        )
        read_back = m.read_manifest(p)
        assert read_back["normalization_contract_version"] == 2

    def test_does_not_alter_the_hash_or_row_count(self, parquet_path):
        # The whole point of WBS 1.1: a recorded version string, zero
        # behaviour change to the artifact it describes.
        p, rows, min_ts, max_ts = parquet_path
        without = m.write_manifest(p, **_known_meta(rows, min_ts, max_ts))
        with_version = m.write_manifest(
            p, **_known_meta(rows, min_ts, max_ts),
            normalization_contract_version=1,
        )
        assert without["parquet_sha256"] == with_version["parquet_sha256"]
        assert without["rows"] == with_version["rows"]


class TestConditioningAdmissibilityRawContentHash:
    """WBS 1.4/1.5 -- storage.manifest.write_manifest()'s three newest
    optional fields. Full end-to-end exercise of these lives in
    tests/test_dvol_pilot_conformance.py; this class covers the shared
    function's own contract in isolation, independent of any one ingest
    writer."""

    def test_all_three_default_to_none_when_omitted(self, parquet_path):
        p, rows, min_ts, max_ts = parquet_path
        manifest = m.write_manifest(p, **_known_meta(rows, min_ts, max_ts))
        assert manifest["conditioning"] is None
        assert manifest["admissibility"] is None
        assert manifest["raw_content_hash"] is None

    def test_records_supplied_values(self, parquet_path):
        p, rows, min_ts, max_ts = parquet_path
        manifest = m.write_manifest(
            p, **_known_meta(rows, min_ts, max_ts),
            conditioning="test population statement",
            admissibility={"certification": "GREEN", "readiness": "READY", "lattice_version": "1.0.0"},
            raw_content_hash="a" * 64,
        )
        assert manifest["conditioning"] == "test population statement"
        assert manifest["admissibility"] == {
            "certification": "GREEN", "readiness": "READY", "lattice_version": "1.0.0",
        }
        assert manifest["raw_content_hash"] == "a" * 64

    def test_round_trip_through_the_sidecar(self, parquet_path):
        p, rows, min_ts, max_ts = parquet_path
        m.write_manifest(
            p, **_known_meta(rows, min_ts, max_ts),
            conditioning="x", admissibility={"certification": "RED"}, raw_content_hash="b" * 64,
        )
        read_back = m.read_manifest(p)
        assert read_back["conditioning"] == "x"
        assert read_back["admissibility"] == {"certification": "RED"}
        assert read_back["raw_content_hash"] == "b" * 64

    def test_existing_pre_wbs1_1_1_caller_shape_still_works(self, parquet_path):
        # research/build_corpus.py / build_perp_corpus.py's exact call
        # shape -- none of these three keywords, none of WBS 1.1's either.
        p, rows, min_ts, max_ts = parquet_path
        manifest = m.write_manifest(p, **_known_meta(rows, min_ts, max_ts))
        assert manifest["dataset"] == "test_dataset"  # unaffected


class TestSchemaVersionBump:
    def test_schema_version_is_3(self):
        # research/storage/manifest.py's own docstring: "increment when
        # fields are added or removed". Was 1 pre-WBS-1.1, bumped to 2 by
        # WBS 1.1 (normalization_contract_version), bumped again to 3 by
        # WBS 1.4/1.5 (conditioning, admissibility, raw_content_hash) --
        # updated here to match, exactly as this milestone's own
        # verification record documents (the same staleness class already
        # fixed once in research/validate_wi6.py's dynamic-label form).
        assert m.SCHEMA_VERSION == 3

    def test_manifest_carries_the_current_schema_version(self, parquet_path):
        # Compares against the live constant, not a hardcoded literal, so
        # this test cannot go stale the way its predecessor did.
        p, rows, min_ts, max_ts = parquet_path
        manifest = m.write_manifest(p, **_known_meta(rows, min_ts, max_ts))
        assert manifest["schema_version"] == m.SCHEMA_VERSION
