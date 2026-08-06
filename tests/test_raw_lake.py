"""
Tests for research/storage/raw_lake.py -- the Z2 raw-lake landing module
(engineering roadmap Phase 1, WBS 1.2/1.3; ADR-I009 §9, §13.1, §16.1).

Covers: deterministic content hashing, first-landing revision creation,
idempotent re-landing of identical content (no new revision, no new file),
non-overwriting revisioning when content differs (a new file alongside the
old, never replacing it), the mandatory (non-defaultable) conditioning
field, and the revision index's shape.

Requires the research environment (pandas, pyarrow) -- run via the btc_bot
conda env, matching research/requirements-research.txt and CLAUDE.md §4.
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

from storage import raw_lake as rl  # noqa: E402

CONDITIONING = "test fixture conditioning statement"


def _df(rows: int = 5, offset: float = 0.0) -> pd.DataFrame:
    base = int(datetime(2024, 6, 1, tzinfo=timezone.utc).timestamp() * 1_000)
    return pd.DataFrame({
        "open_time": [base + i * 86_400_000 for i in range(rows)],
        "close": [50.0 + i + offset for i in range(rows)],
    })


def _land(df, tmp_path, **overrides):
    kwargs = dict(
        dataset="dvol", symbol="BTC", year=2024, month=6,
        source="deribit", source_url="https://example.test",
        conditioning=CONDITIONING,
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )
    kwargs.update(overrides)
    return rl.land_raw(df, tmp_path, **kwargs)


class TestPartitionDir:
    def test_path_shape(self, tmp_path):
        p = rl.raw_partition_dir(tmp_path, "dvol", "BTC", 2024, 6)
        assert p == tmp_path / "_raw_lake" / "dvol" / "BTC" / "year=2024" / "month=06"


class TestContentHash:
    def test_deterministic_for_identical_content(self):
        a, b = _df(), _df()
        assert rl.content_hash(a) == rl.content_hash(b)

    def test_differs_for_different_content(self):
        a, b = _df(), _df(offset=1.0)
        assert rl.content_hash(a) != rl.content_hash(b)

    def test_is_hex_sha256(self):
        h = rl.content_hash(_df())
        assert len(h) == 64
        int(h, 16)  # valid hex


class TestReadRevisions:
    def test_empty_for_never_landed_partition(self, tmp_path):
        assert rl.read_revisions(tmp_path, "dvol", "BTC", 2024, 6) == []


class TestLandRaw:
    def test_first_landing_creates_revision_1(self, tmp_path):
        receipt = _land(_df(), tmp_path)
        assert receipt["revision_ordinal"] == 1
        assert receipt["is_new_revision"] is True
        assert Path(receipt["path"]).exists()

    def test_revision_index_written(self, tmp_path):
        _land(_df(), tmp_path)
        revisions = rl.read_revisions(tmp_path, "dvol", "BTC", 2024, 6)
        assert len(revisions) == 1
        assert revisions[0]["revision_ordinal"] == 1
        assert revisions[0]["conditioning"] == CONDITIONING

    def test_relanding_identical_content_is_a_no_op(self, tmp_path):
        first = _land(_df(), tmp_path)
        second = _land(_df(), tmp_path)
        assert second["is_new_revision"] is False
        assert second["hash"] == first["hash"]
        assert second["revision_ordinal"] == first["revision_ordinal"]
        assert len(rl.read_revisions(tmp_path, "dvol", "BTC", 2024, 6)) == 1

    def test_relanding_different_content_creates_a_new_revision_alongside(self, tmp_path):
        first = _land(_df(), tmp_path)
        second = _land(_df(offset=100.0), tmp_path)
        assert second["is_new_revision"] is True
        assert second["hash"] != first["hash"]
        assert second["revision_ordinal"] == 2

        # ADR-I009 §16.1: alongside, never overwriting -- both files exist.
        assert Path(first["path"]).exists()
        assert Path(second["path"]).exists()
        assert first["path"] != second["path"]

        revisions = rl.read_revisions(tmp_path, "dvol", "BTC", 2024, 6)
        assert len(revisions) == 2
        assert [r["revision_ordinal"] for r in revisions] == [1, 2]

    def test_first_revisions_content_is_never_mutated_by_a_second_landing(self, tmp_path):
        first = _land(_df(), tmp_path)
        first_bytes = Path(first["path"]).read_bytes()
        _land(_df(offset=100.0), tmp_path)
        assert Path(first["path"]).read_bytes() == first_bytes

    def test_landed_file_round_trips_the_data(self, tmp_path):
        df = _df(rows=7)
        receipt = _land(df, tmp_path)
        read_back = pd.read_parquet(receipt["path"])
        assert len(read_back) == 7
        assert list(read_back["close"]) == list(df["close"])


class TestConditioningMandatory:
    def test_missing_conditioning_kwarg_raises_typeerror(self, tmp_path):
        # No default exists -- Python's own enforcement (Ph6: no field is
        # defaultable), distinct from storage.manifest.write_manifest()'s
        # deliberately-optional counterpart fields.
        with pytest.raises(TypeError):
            rl.land_raw(
                _df(), tmp_path, dataset="dvol", symbol="BTC", year=2024, month=6,
                source="deribit", source_url=None,
                fetched_at=datetime.now(timezone.utc).isoformat(),
            )

    def test_blank_conditioning_raises_valueerror(self, tmp_path):
        with pytest.raises(ValueError):
            _land(_df(), tmp_path, conditioning="")

    def test_whitespace_only_conditioning_raises_valueerror(self, tmp_path):
        with pytest.raises(ValueError):
            _land(_df(), tmp_path, conditioning="   ")

    def test_none_conditioning_raises_valueerror(self, tmp_path):
        with pytest.raises(ValueError):
            _land(_df(), tmp_path, conditioning=None)
