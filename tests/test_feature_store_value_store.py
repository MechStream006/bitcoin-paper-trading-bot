"""
Tests for research/feature_store/value_store.py (engineering roadmap Phase 2,
WBS 2.2 -- part of the first Feature Store milestone).

Pure stdlib module (sqlite3/json/hashlib) -- no research dependency, runs
in any environment.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research" / "feature_store"))

import value_store as vs  # noqa: E402

NOW = "2026-08-05T00:00:00+00:00"
ADDR = dict(spec_hash="deadbeef" * 8, corpus_version="corpus-v1", as_of="2026-08-05")


@pytest.fixture
def db(tmp_path) -> Path:
    p = tmp_path / "value_store.db"
    vs.init_db(p)
    return p


class TestCacheTier:
    def test_put_then_get_round_trips(self, db):
        vs.put_cache(db, computed_at=NOW, values=[1, 2, 3], **ADDR)
        assert vs.get_cache(db, **ADDR) == [1, 2, 3]

    def test_get_on_empty_cache_returns_none(self, db):
        assert vs.get_cache(db, **ADDR) is None

    def test_put_overwrites_freely_no_obligation(self, db):
        vs.put_cache(db, computed_at=NOW, values=[1, 2, 3], **ADDR)
        vs.put_cache(db, computed_at=NOW, values=[9, 9, 9], **ADDR)
        assert vs.get_cache(db, **ADDR) == [9, 9, 9]

    def test_evict_removes_the_entry(self, db):
        vs.put_cache(db, computed_at=NOW, values=[1, 2, 3], **ADDR)
        vs.evict_cache(db, **ADDR)
        assert vs.get_cache(db, **ADDR) is None

    def test_evict_on_nonexistent_entry_does_not_raise(self, db):
        vs.evict_cache(db, **ADDR)  # must not raise

    def test_different_addresses_are_independent(self, db):
        vs.put_cache(db, computed_at=NOW, values=[1], **ADDR)
        other = {**ADDR, "as_of": "2026-08-06"}
        vs.put_cache(db, computed_at=NOW, values=[2], **other)
        assert vs.get_cache(db, **ADDR) == [1]
        assert vs.get_cache(db, **other) == [2]


class TestMaterializationTier:
    def test_first_write_succeeds_and_round_trips(self, db):
        h = vs.materialize(
            db, values=[1, 2, 3], citing_artifact="training-dataset-1",
            retention_obligation="bound-to-training-dataset-1", materialized_at=NOW, **ADDR,
        )
        assert len(h) == 64
        assert vs.get_materialized(db, **ADDR) == [1, 2, 3]

    def test_identical_content_rewrite_is_idempotent(self, db):
        first = vs.materialize(db, values=[1, 2, 3], citing_artifact="a",
                                retention_obligation="x", materialized_at=NOW, **ADDR)
        second = vs.materialize(db, values=[1, 2, 3], citing_artifact="a",
                                 retention_obligation="x", materialized_at=NOW, **ADDR)
        assert first == second
        assert vs.get_materialized(db, **ADDR) == [1, 2, 3]

    def test_differing_content_at_the_same_address_raises_divergence(self, db):
        vs.materialize(db, values=[1, 2, 3], citing_artifact="a",
                        retention_obligation="x", materialized_at=NOW, **ADDR)
        with pytest.raises(vs.DivergenceEvent):
            vs.materialize(db, values=[9, 9, 9], citing_artifact="a",
                            retention_obligation="x", materialized_at=NOW, **ADDR)

    def test_divergent_write_never_overwrites_the_original(self, db):
        vs.materialize(db, values=[1, 2, 3], citing_artifact="a",
                        retention_obligation="x", materialized_at=NOW, **ADDR)
        try:
            vs.materialize(db, values=[9, 9, 9], citing_artifact="a",
                            retention_obligation="x", materialized_at=NOW, **ADDR)
        except vs.DivergenceEvent:
            pass
        # The original must be completely intact.
        assert vs.get_materialized(db, **ADDR) == [1, 2, 3]

    def test_get_materialized_on_empty_store_returns_none(self, db):
        assert vs.get_materialized(db, **ADDR) is None

    def test_corrupted_stored_bytes_raise_divergence_on_read(self, db):
        vs.materialize(db, values=[1, 2, 3], citing_artifact="a",
                        retention_obligation="x", materialized_at=NOW, **ADDR)
        # Simulate on-disk corruption: tamper with values_json directly,
        # leaving the recorded content_hash stale.
        con = sqlite3.connect(db)
        con.execute(
            "UPDATE feature_value_materialized SET values_json = '[9,9,9]' "
            "WHERE spec_hash = ? AND corpus_version = ? AND as_of = ?",
            (ADDR["spec_hash"], ADDR["corpus_version"], ADDR["as_of"]),
        )
        con.commit()
        con.close()
        with pytest.raises(vs.DivergenceEvent):
            vs.get_materialized(db, **ADDR)


class TestResolve:
    def test_computes_and_caches_on_first_call(self, db):
        calls = {"n": 0}

        def compute():
            calls["n"] += 1
            return [1, 2, 3]

        result = vs.resolve(db, compute_fn=compute, computed_at=NOW, **ADDR)
        assert result.values == [1, 2, 3]
        assert result.source == "computed"
        assert calls["n"] == 1
        assert vs.get_cache(db, **ADDR) == [1, 2, 3]

    def test_second_call_serves_from_cache_without_recomputing(self, db):
        calls = {"n": 0}

        def compute():
            calls["n"] += 1
            return [1, 2, 3]

        vs.resolve(db, compute_fn=compute, computed_at=NOW, **ADDR)
        result = vs.resolve(db, compute_fn=compute, computed_at=NOW, **ADDR)
        assert result.source == "cache"
        assert calls["n"] == 1  # compute_fn not called the second time

    def test_materialized_takes_priority_over_cache(self, db):
        vs.put_cache(db, computed_at=NOW, values=["stale-cache"], **ADDR)
        vs.materialize(db, values=["authoritative"], citing_artifact="a",
                        retention_obligation="x", materialized_at=NOW, **ADDR)

        def compute():
            raise AssertionError("compute_fn must not be called when materialized")

        result = vs.resolve(db, compute_fn=compute, computed_at=NOW, **ADDR)
        assert result.values == ["authoritative"]
        assert result.source == "materialized"

    def test_materialized_takes_priority_over_freshly_computed(self, db):
        vs.materialize(db, values=["authoritative"], citing_artifact="a",
                        retention_obligation="x", materialized_at=NOW, **ADDR)
        calls = {"n": 0}

        def compute():
            calls["n"] += 1
            return ["computed"]

        result = vs.resolve(db, compute_fn=compute, computed_at=NOW, **ADDR)
        assert result.source == "materialized"
        assert calls["n"] == 0
