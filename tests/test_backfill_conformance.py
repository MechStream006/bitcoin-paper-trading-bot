"""
Tests for research/ingest/backfill_conformance.py (Phase 4 roadmap
v1.0.2, WBS 4.0, Sprint D0a -- the dvol corpus conformance pilot).

THE CENTRAL TEST IS TestConditionC4. Condition C4 requires every landed
content hash to equal the partition's pre-existing `parquet_sha256`.
That is what makes `DR1` -- "the corpus backfill changes a published
result" -- structurally impossible rather than procedurally mitigated: a
content change becomes a FAILED PRECONDITION that aborts before landing,
not a regression detected afterwards. These tests exist to prove the
abort actually happens, not merely that conformance works when content
agrees.

Second in importance is TestHistoricalInstants: `ingest_ts` and
`fetched_at` must be the ORIGINAL ingest instants carried forward, never
`datetime.now()` -- the same discipline WBS 3.4 applied to the 148 H6
forward labels.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import shutil
import sys
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")

_REPO_ROOT = Path(__file__).resolve().parents[1]
_RESEARCH = _REPO_ROOT / "research"
if str(_RESEARCH) not in sys.path:
    sys.path.insert(0, str(_RESEARCH))

from ingest import backfill_conformance as bc  # noqa: E402
from storage.lake import write_partition       # noqa: E402
from storage.manifest import write_manifest    # noqa: E402
from storage.raw_lake import content_hash      # noqa: E402

REAL_DVOL = _RESEARCH / "data" / "raw" / "dvol" / "BTC"
ORIGINAL_TS = "2026-06-10T09:59:50.292886+00:00"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _make_partition(base_dir: Path, year: int, month: int, rows: int = 5,
                    dataset: str = "dvol", symbol: str = "BTC") -> Path:
    """A synthetic partition in the real pre-conformance shape: canonical
    parquet plus a schema_version-1 manifest carrying none of the three
    ADR-I012 fields."""
    df = pd.DataFrame({
        "open_time": [1_600_000_000_000 + i * 86_400_000 for i in range(rows)],
        "open": [10.0 + i for i in range(rows)],
        "high": [11.0 + i for i in range(rows)],
        "low": [9.0 + i for i in range(rows)],
        "close": [10.5 + i for i in range(rows)],
    })
    out = write_partition(df, base_dir, dataset, symbol, year, month)
    write_manifest(
        out, dataset=dataset, symbol=symbol, year=year, month=month,
        source="deribit", source_url="https://example.invalid/api",
        rows=len(df), min_ts=int(df["open_time"].min()), max_ts=int(df["open_time"].max()),
        ts_column="open_time", ingest_ts=ORIGINAL_TS, config_version=1,
    )
    return out


@pytest.fixture
def base_dir(tmp_path) -> Path:
    d = tmp_path / "raw"
    d.mkdir()
    return d


@pytest.fixture
def spec() -> dict:
    return bc.source_spec("dvol")


# ---------------------------------------------------------------------------
# Source registry
# ---------------------------------------------------------------------------

class TestSourceRegistry:
    def test_dvol_is_registered(self):
        s = bc.source_spec("dvol")
        assert s["dataset"] == "dvol" and s["symbol"] == "BTC"
        assert s["ts_column"] == "open_time"
        assert s["conditioning"].strip()
        assert isinstance(s["normalization_contract_version"], int)

    def test_unregistered_dataset_refuses_by_name(self):
        """D0a is dvol only; funding/perp/spot are D0b's, added additively."""
        for ds in ("funding", "perp", "spot", "nonsense"):
            with pytest.raises(bc.ConformanceError, match="no conformance source registered"):
                bc.source_spec(ds)

    def test_constants_come_from_the_owning_module_not_restated(self):
        """A change in dvol.py must not silently diverge from this script."""
        from ingest import dvol
        s = bc.source_spec("dvol")
        assert s["conditioning"] == dvol.CONDITIONING
        assert s["normalization_contract_version"] == dvol.NORMALIZATION_CONTRACT_VERSION
        assert s["dataset"] == dvol.DATASET and s["symbol"] == dvol.SYMBOL_LAKE


# ---------------------------------------------------------------------------
# plan() / is_conformed()
# ---------------------------------------------------------------------------

class TestPlan:
    def test_fresh_partitions_all_need_conformance(self, base_dir):
        _make_partition(base_dir, 2021, 3)
        _make_partition(base_dir, 2021, 4)
        assert len(bc.plan(base_dir, "dvol", "BTC")) == 2

    def test_conformed_partition_is_not_replanned(self, base_dir, spec):
        _make_partition(base_dir, 2021, 3)
        parts = bc.list_partitions(base_dir, "dvol", "BTC")
        bc.conform_partition(parts[0], base_dir, spec)
        assert bc.plan(base_dir, "dvol", "BTC") == []

    def test_is_conformed_requires_all_three_fields(self):
        assert not bc.is_conformed({"admissibility": {}, "conditioning": "x", "raw_content_hash": None})
        assert not bc.is_conformed({"admissibility": None, "conditioning": "x", "raw_content_hash": "h"})
        assert not bc.is_conformed({"admissibility": {}, "conditioning": None, "raw_content_hash": "h"})
        assert bc.is_conformed({"admissibility": {}, "conditioning": "x", "raw_content_hash": "h"})

    def test_plan_writes_nothing(self, base_dir):
        _make_partition(base_dir, 2021, 3)
        before = _sha(base_dir / "dvol/BTC/year=2021/month=03/data.parquet")
        bc.plan(base_dir, "dvol", "BTC")
        assert _sha(base_dir / "dvol/BTC/year=2021/month=03/data.parquet") == before
        assert not (base_dir / "_raw_lake").exists()


# ---------------------------------------------------------------------------
# THE CENTRAL SAFETY PROPERTY
# ---------------------------------------------------------------------------

class TestConditionC4:
    def test_content_mismatch_aborts_before_landing(self, base_dir, spec):
        """A partition whose content disagrees with its manifest must be
        REFUSED, not conformed. This is what makes DR1 structural."""
        _make_partition(base_dir, 2021, 3)
        part = bc.list_partitions(base_dir, "dvol", "BTC")[0]
        part["manifest"]["parquet_sha256"] = "0" * 64   # simulate disagreement

        with pytest.raises(bc.ConformanceError, match="C4 VIOLATION"):
            bc.conform_partition(part, base_dir, spec)

    def test_nothing_is_landed_when_c4_fails(self, base_dir, spec):
        _make_partition(base_dir, 2021, 3)
        part = bc.list_partitions(base_dir, "dvol", "BTC")[0]
        part["manifest"]["parquet_sha256"] = "0" * 64
        with pytest.raises(bc.ConformanceError):
            bc.conform_partition(part, base_dir, spec)
        assert not (base_dir / "_raw_lake").exists()

    def test_landed_hash_equals_recorded_parquet_sha256(self, base_dir, spec):
        _make_partition(base_dir, 2021, 3)
        part = bc.list_partitions(base_dir, "dvol", "BTC")[0]
        recorded = part["manifest"]["parquet_sha256"]
        result = bc.conform_partition(part, base_dir, spec)
        assert result["content_hash"] == recorded

        man = json.loads((part["dir"] / "_manifest.json").read_text(encoding="utf-8"))
        assert man["raw_content_hash"] == recorded

    def test_c4_holds_for_every_real_dvol_partition(self):
        """The invariant this milestone's method depends on, checked
        against the real corpus rather than a synthetic fixture."""
        if not REAL_DVOL.exists():
            pytest.skip("real dvol corpus not present")
        checked = 0
        for man_path in sorted(REAL_DVOL.glob("year=*/month=*/_manifest.json")):
            man = json.loads(man_path.read_text(encoding="utf-8"))
            df = pd.read_parquet(man_path.parent / "data.parquet")
            assert content_hash(df) == man["parquet_sha256"], man_path
            checked += 1
        assert checked == 64


# ---------------------------------------------------------------------------
# Condition C2 -- canonical data is never mutated
# ---------------------------------------------------------------------------

class TestConditionC2:
    def test_data_parquet_is_byte_identical_after_conformance(self, base_dir, spec):
        _make_partition(base_dir, 2021, 3)
        pq = base_dir / "dvol/BTC/year=2021/month=03/data.parquet"
        before = _sha(pq)
        bc.conform_partition(bc.list_partitions(base_dir, "dvol", "BTC")[0], base_dir, spec)
        assert _sha(pq) == before

    def test_fingerprint_detects_a_changed_parquet(self, base_dir):
        _make_partition(base_dir, 2021, 3)
        before = bc.parquet_fingerprint(base_dir, "dvol", "BTC")
        pq = base_dir / "dvol/BTC/year=2021/month=03/data.parquet"
        pq.write_bytes(pq.read_bytes() + b"x")
        after = bc.parquet_fingerprint(base_dir, "dvol", "BTC")
        assert before != after

    def test_only_manifest_fields_are_added_never_removed(self, base_dir, spec):
        _make_partition(base_dir, 2021, 3)
        part = bc.list_partitions(base_dir, "dvol", "BTC")[0]
        before = dict(part["manifest"])
        bc.conform_partition(part, base_dir, spec)
        after = json.loads((part["dir"] / "_manifest.json").read_text(encoding="utf-8"))

        for key in ("dataset", "symbol", "year", "month", "parquet_file", "parquet_sha256",
                    "rows", "min_ts", "max_ts", "ts_column", "source", "source_url",
                    "ingest_ts", "config_version"):
            assert after[key] == before[key], f"{key} changed"
        assert after["admissibility"] is not None
        assert after["conditioning"] is not None
        assert after["raw_content_hash"] is not None


# ---------------------------------------------------------------------------
# No invented values
# ---------------------------------------------------------------------------

class TestHistoricalInstants:
    def test_ingest_ts_is_preserved_never_restamped(self, base_dir, spec):
        _make_partition(base_dir, 2021, 3)
        part = bc.list_partitions(base_dir, "dvol", "BTC")[0]
        bc.conform_partition(part, base_dir, spec)
        man = json.loads((part["dir"] / "_manifest.json").read_text(encoding="utf-8"))
        assert man["ingest_ts"] == ORIGINAL_TS

    def test_raw_lake_revision_carries_the_original_fetched_at(self, base_dir, spec):
        _make_partition(base_dir, 2021, 3)
        bc.conform_partition(bc.list_partitions(base_dir, "dvol", "BTC")[0], base_dir, spec)
        idx = json.loads(
            (base_dir / "_raw_lake/dvol/BTC/year=2021/month=03/_revisions.json").read_text(encoding="utf-8")
        )
        entries = idx if isinstance(idx, list) else idx.get("revisions", [])
        assert entries and entries[0]["fetched_at"] == ORIGINAL_TS

    def test_conform_partition_never_calls_datetime_now(self):
        """Structural guard, mirroring WBS 3.4's identical AST discipline."""
        src = inspect.getsource(bc.conform_partition)
        assert "datetime.now" not in src
        assert "utcnow" not in src

    def test_source_and_config_version_are_preserved(self, base_dir, spec):
        _make_partition(base_dir, 2021, 3)
        part = bc.list_partitions(base_dir, "dvol", "BTC")[0]
        bc.conform_partition(part, base_dir, spec)
        man = json.loads((part["dir"] / "_manifest.json").read_text(encoding="utf-8"))
        assert man["source"] == "deribit"
        assert man["source_url"] == "https://example.invalid/api"
        assert man["config_version"] == 1


# ---------------------------------------------------------------------------
# Conformance output
# ---------------------------------------------------------------------------

class TestConformanceOutput:
    def test_admissibility_block_has_the_three_expected_keys(self, base_dir, spec):
        _make_partition(base_dir, 2021, 3)
        part = bc.list_partitions(base_dir, "dvol", "BTC")[0]
        bc.conform_partition(part, base_dir, spec)
        adm = json.loads((part["dir"] / "_manifest.json").read_text(encoding="utf-8"))["admissibility"]
        assert set(adm) == {"certification", "readiness", "lattice_version"}
        assert adm["certification"] in {"GREEN", "AMBER", "RED", "UNKNOWN"}

    def test_conditioning_matches_the_owning_module(self, base_dir, spec):
        from ingest import dvol
        _make_partition(base_dir, 2021, 3)
        part = bc.list_partitions(base_dir, "dvol", "BTC")[0]
        bc.conform_partition(part, base_dir, spec)
        man = json.loads((part["dir"] / "_manifest.json").read_text(encoding="utf-8"))
        assert man["conditioning"] == dvol.CONDITIONING
        assert man["normalization_contract_version"] == dvol.NORMALIZATION_CONTRACT_VERSION


# ---------------------------------------------------------------------------
# Idempotency and CLI
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_second_conformance_adds_no_new_revision(self, base_dir, spec):
        _make_partition(base_dir, 2021, 3)
        part = bc.list_partitions(base_dir, "dvol", "BTC")[0]
        first = bc.conform_partition(part, base_dir, spec)
        assert first["is_new_revision"] is True

        reread = bc.list_partitions(base_dir, "dvol", "BTC")[0]
        second = bc.conform_partition(reread, base_dir, spec)
        assert second["is_new_revision"] is False
        assert second["revision_ordinal"] == first["revision_ordinal"]

    def test_second_commit_run_is_a_no_op(self, base_dir):
        _make_partition(base_dir, 2021, 3)
        assert bc.main(["--dataset", "dvol", "--base-dir", str(base_dir), "--commit"]) == 0
        backups = list(base_dir.glob("_backup_dvol_*"))
        assert bc.main(["--dataset", "dvol", "--base-dir", str(base_dir), "--commit"]) == 0
        assert list(base_dir.glob("_backup_dvol_*")) == backups   # no second backup


class TestCLI:
    def test_dry_run_is_the_default_and_writes_nothing(self, base_dir):
        _make_partition(base_dir, 2021, 3)
        pq = base_dir / "dvol/BTC/year=2021/month=03/data.parquet"
        before = _sha(pq)
        assert bc.main(["--dataset", "dvol", "--base-dir", str(base_dir)]) == 0
        assert _sha(pq) == before
        assert not (base_dir / "_raw_lake").exists()
        assert not list(base_dir.glob("_backup_dvol_*"))

    def test_commit_takes_a_backup_first(self, base_dir):
        _make_partition(base_dir, 2021, 3)
        assert bc.main(["--dataset", "dvol", "--base-dir", str(base_dir), "--commit"]) == 0
        assert len(list(base_dir.glob("_backup_dvol_*"))) == 1

    def test_unregistered_dataset_returns_1(self, base_dir):
        assert bc.main(["--dataset", "funding", "--base-dir", str(base_dir)]) == 1

    def test_missing_corpus_returns_1(self, base_dir):
        assert bc.main(["--dataset", "dvol", "--base-dir", str(base_dir)]) == 1


# ---------------------------------------------------------------------------
# Upstream drift check -- reports, never lands
# ---------------------------------------------------------------------------

class TestVerifySample:
    def test_drift_is_reported_not_landed(self, base_dir, monkeypatch):
        _make_partition(base_dir, 2021, 3)
        from ingest import dvol

        def fake_fetch(lo, hi):
            return pd.DataFrame({
                "open_time": [1_600_000_000_000], "open": [99.0],
                "high": [99.0], "low": [99.0], "close": [99.0],
            })
        monkeypatch.setattr(dvol, "_fetch_range", fake_fetch)

        findings = bc.verify_sample(base_dir, "dvol", "BTC", 1)
        assert len(findings) == 1 and findings[0]["agrees"] is False
        assert not (base_dir / "_raw_lake").exists()   # nothing landed

    def test_fetch_failure_is_a_finding_not_a_crash(self, base_dir, monkeypatch):
        _make_partition(base_dir, 2021, 3)
        from ingest import dvol

        def boom(lo, hi):
            raise ConnectionError("network down")
        monkeypatch.setattr(dvol, "_fetch_range", boom)

        findings = bc.verify_sample(base_dir, "dvol", "BTC", 1)
        assert findings[0]["error"].startswith("ConnectionError")
        assert findings[0]["agrees"] is None

    def test_verify_sample_refuses_an_unwired_dataset(self, base_dir):
        with pytest.raises(bc.ConformanceError, match="no sampled re-fetch"):
            bc.verify_sample(base_dir, "funding", "BTCUSDT", 1)


# ---------------------------------------------------------------------------
# Real corpus -- read-only, hash-guarded
# ---------------------------------------------------------------------------

real_corpus = pytest.mark.skipif(not REAL_DVOL.exists(), reason="real dvol corpus not present")


@real_corpus
class TestRealDvolCorpus:
    def test_all_64_partitions_are_conformed(self):
        parts = bc.list_partitions(_RESEARCH / "data" / "raw", "dvol", "BTC")
        assert len(parts) == 64
        unconformed = [p for p in parts if not bc.is_conformed(p["manifest"])]
        assert unconformed == [], f"{len(unconformed)} partition(s) still unconformed"

    def test_every_partition_carries_a_certification_verdict(self):
        parts = bc.list_partitions(_RESEARCH / "data" / "raw", "dvol", "BTC")
        for p in parts:
            adm = p["manifest"]["admissibility"]
            assert adm is not None and adm["certification"]

    def test_raw_content_hash_equals_parquet_sha256_everywhere(self):
        """Condition C4, asserted against the real conformed corpus."""
        parts = bc.list_partitions(_RESEARCH / "data" / "raw", "dvol", "BTC")
        for p in parts:
            assert p["manifest"]["raw_content_hash"] == p["manifest"]["parquet_sha256"]

    def test_raw_lake_has_one_revision_per_partition(self):
        lake = _RESEARCH / "data" / "raw" / "_raw_lake" / "dvol" / "BTC"
        assert lake.exists()
        assert len(list(lake.glob("year=*/month=*/_revisions.json"))) == 64

    def test_plan_against_the_real_corpus_is_empty(self):
        assert bc.plan(_RESEARCH / "data" / "raw", "dvol", "BTC") == []

    def test_a_pre_conformance_backup_exists(self):
        assert list((_RESEARCH / "data" / "raw").glob("_backup_dvol_BTC_*"))
