"""
Tests for the corpus builders' manifest-upgrade path (WBS 4.0.1).

Covers the two defects found at the D0b pre-flight review:

  F1-A  The skip gate hardcoded `schema_version == 1`. When Phase 1 bumped
        storage.manifest.SCHEMA_VERSION 1 -> 3, the gate silently inverted
        polarity: current-schema manifests stopped being skipped and were
        re-upgraded instead. INC-0005.

  F1-B  The re-upgrade called write_manifest() without the four conformance
        parameters, which default to None and OVERWRITE the sidecar --
        destroying whatever stage 1 (the ingest writers) or
        backfill_conformance.py had recorded.

THE CENTRAL TEST IS TestStaleVersionRegression. It fails if anyone ever
reintroduces a hardcoded version literal, by driving the real builder with
a manifest at the CURRENT schema and asserting it is left alone. It is
written so that a future SCHEMA_VERSION bump cannot make it pass vacuously
-- it reads the constant rather than restating it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")

_REPO_ROOT = Path(__file__).resolve().parents[1]
_RESEARCH = _REPO_ROOT / "research"
if str(_RESEARCH) not in sys.path:
    sys.path.insert(0, str(_RESEARCH))

import build_corpus                                  # noqa: E402
import build_perp_corpus                             # noqa: E402
from storage.lake import write_partition             # noqa: E402
from storage.manifest import SCHEMA_VERSION, write_manifest  # noqa: E402

REAL_RAW = _RESEARCH / "data" / "raw"
ORIGINAL_TS = "2026-06-10T17:28:58.590858+00:00"

CONFORMANCE_FIELDS = (
    "normalization_contract_version",
    "conditioning",
    "admissibility",
    "raw_content_hash",
)
SAMPLE_ADMISSIBILITY = {
    "certification": "GREEN", "readiness": "READY", "lattice_version": "1.0.0",
}


def _frame(ts_col: str, rows: int = 5) -> pd.DataFrame:
    return pd.DataFrame({
        ts_col: [1_600_000_000_000 + i * 60_000 for i in range(rows)],
        "open": [1.0 + i for i in range(rows)],
        "high": [2.0 + i for i in range(rows)],
        "low": [0.5 + i for i in range(rows)],
        "close": [1.5 + i for i in range(rows)],
        "volume": [10.0 + i for i in range(rows)],
    })


def _stage1_partition(base: Path, dataset: str, symbol: str, year: int, month: int,
                      ts_col: str, *, conformance: bool) -> Path:
    """A stage-1 sidecar exactly as ingest writes it: raw json.dumps, no
    schema_version, no parquet_sha256. Optionally carrying the four
    conformance fields (which ingest has written since Phase 1)."""
    out = write_partition(_frame(ts_col), base, dataset, symbol, year, month)
    meta = {
        "dataset": dataset, "symbol": symbol, "year": year, "month": month,
        "interval": "1m", "source": "bulk",
        "source_url": "https://example.invalid/x.zip",
        "rows": 5, "ingest_ts": ORIGINAL_TS, "skipped": False,
    }
    if conformance:
        meta.update({
            "normalization_contract_version": 1,
            "conditioning": "stage-1 conditioning text",
            "raw_content_hash": "a" * 64,
            "admissibility": dict(SAMPLE_ADMISSIBILITY),
        })
    (out.parent / "_manifest.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return out


def _stage2_partition(base: Path, dataset: str, symbol: str, year: int, month: int,
                      ts_col: str, *, conformance: bool) -> Path:
    """A fully-upgraded, current-schema partition -- what the builders must
    leave alone."""
    df = _frame(ts_col)
    out = write_partition(df, base, dataset, symbol, year, month)
    extra = {}
    if conformance:
        extra = {
            "normalization_contract_version": 1,
            "conditioning": "stage-2 conditioning text",
            "admissibility": dict(SAMPLE_ADMISSIBILITY),
            "raw_content_hash": "b" * 64,
        }
    write_manifest(
        out, dataset=dataset, symbol=symbol, year=year, month=month,
        source="bulk", source_url="https://example.invalid/x.zip",
        rows=len(df), min_ts=int(df[ts_col].min()), max_ts=int(df[ts_col].max()),
        ts_column=ts_col, ingest_ts=ORIGINAL_TS, config_version=1, **extra,
    )
    return out


def _man(parquet: Path) -> dict:
    return json.loads((parquet.parent / "_manifest.json").read_text(encoding="utf-8"))


@pytest.fixture
def spot_base(tmp_path, monkeypatch) -> Path:
    base = tmp_path / "raw"
    base.mkdir()
    monkeypatch.setattr(build_corpus, "BASE_DIR", base)
    monkeypatch.setattr(build_corpus, "SYMBOL", "BTCUSDT")
    return base


@pytest.fixture
def perp_base(tmp_path, monkeypatch) -> Path:
    base = tmp_path / "raw"
    base.mkdir()
    monkeypatch.setattr(build_perp_corpus, "BASE_DIR", base)
    monkeypatch.setattr(build_perp_corpus, "SYMBOL", "BTCUSDT")
    return base


# ---------------------------------------------------------------------------
# F1-A -- the stale-version regression
# ---------------------------------------------------------------------------

class TestStaleVersionRegression:
    def test_current_schema_partition_is_skipped_not_reupgraded(self, spot_base):
        """The exact defect: with a hardcoded `== 1`, a schema-3 manifest
        fell through and was re-upgraded."""
        _stage2_partition(spot_base, "spot", "BTCUSDT", 2024, 1, "open_time", conformance=True)
        result = build_corpus.stage_manifest_upgrade()
        assert result["already_wi6"] == 1
        assert result["upgraded"] == 0

    def test_perp_current_schema_partition_is_skipped(self, perp_base):
        _stage2_partition(perp_base, "perp", "BTCUSDT", 2024, 1, "open_time", conformance=True)
        result = build_perp_corpus.stage_manifest_upgrade()
        assert result["already_wi6"] == 1
        assert result["upgraded"] == 0

    def test_gate_is_bound_to_the_constant_not_a_literal(self):
        """Source-level guard: no builder may compare schema_version against
        a bare integer again. Fails loudly if the literal returns."""
        for mod in ("build_corpus.py", "build_perp_corpus.py", "smoke_test_phaseA1.py"):
            src = (_RESEARCH / mod).read_text(encoding="utf-8")
            assert 'schema_version") == 1' not in src, f"{mod} reintroduced a stale literal"
            assert "SCHEMA_VERSION" in src, f"{mod} does not reference the authoritative constant"

    def test_a_bumped_schema_version_would_not_silently_invert_the_gate(self, spot_base, monkeypatch):
        """Simulates the next bump. A partition written at the OLD schema
        must be upgraded, not skipped -- proving the gate tracks the
        constant rather than any fixed number."""
        out = _stage2_partition(spot_base, "spot", "BTCUSDT", 2024, 1, "open_time", conformance=True)
        man = _man(out)
        man["schema_version"] = SCHEMA_VERSION + 1        # pretend the constant moved on
        (out.parent / "_manifest.json").write_text(json.dumps(man), encoding="utf-8")

        result = build_corpus.stage_manifest_upgrade()
        assert result["upgraded"] == 1 and result["already_wi6"] == 0
        assert _man(out)["schema_version"] == SCHEMA_VERSION


# ---------------------------------------------------------------------------
# F1-B -- conformance-field preservation / carry-forward
# ---------------------------------------------------------------------------

class TestConformancePreservation:
    def test_upgrade_carries_conformance_forward(self, spot_base):
        """A stage-1 partition carrying conformance must keep all four
        fields through the stage-2 upgrade."""
        out = _stage1_partition(spot_base, "spot", "BTCUSDT", 2024, 1, "open_time", conformance=True)
        build_corpus.stage_manifest_upgrade()
        man = _man(out)
        assert man["normalization_contract_version"] == 1
        assert man["conditioning"] == "stage-1 conditioning text"
        assert man["admissibility"] == SAMPLE_ADMISSIBILITY
        assert man["raw_content_hash"] == "a" * 64

    def test_perp_upgrade_carries_conformance_forward(self, perp_base):
        out = _stage1_partition(perp_base, "perp", "BTCUSDT", 2024, 1, "open_time", conformance=True)
        build_perp_corpus.stage_manifest_upgrade()
        man = _man(out)
        for f in CONFORMANCE_FIELDS:
            assert man[f] is not None, f

    def test_forced_reupgrade_of_a_conformed_partition_loses_nothing(self, spot_base):
        """The destructive scenario: a conformed partition whose hash no
        longer verifies falls through to re-upgrade. It must keep its
        conformance fields."""
        out = _stage2_partition(spot_base, "spot", "BTCUSDT", 2024, 1, "open_time", conformance=True)
        man = _man(out)
        man["parquet_sha256"] = "0" * 64          # force verify_hash to fail
        (out.parent / "_manifest.json").write_text(json.dumps(man), encoding="utf-8")

        result = build_corpus.stage_manifest_upgrade()
        assert result["upgraded"] == 1
        after = _man(out)
        assert after["conditioning"] == "stage-2 conditioning text"
        assert after["admissibility"] == SAMPLE_ADMISSIBILITY
        assert after["raw_content_hash"] == "b" * 64
        assert after["normalization_contract_version"] == 1

    def test_absent_conformance_stays_absent_not_fabricated(self, spot_base):
        """Carry-forward must not invent values for a partition that never
        had them."""
        out = _stage1_partition(spot_base, "spot", "BTCUSDT", 2024, 1, "open_time", conformance=False)
        build_corpus.stage_manifest_upgrade()
        man = _man(out)
        for f in CONFORMANCE_FIELDS:
            assert man[f] is None, f


# ---------------------------------------------------------------------------
# Stage-1 -> Stage-2 upgrade, and idempotence
# ---------------------------------------------------------------------------

class TestStageUpgrade:
    def test_stage1_partition_gains_schema_and_hash(self, spot_base):
        out = _stage1_partition(spot_base, "spot", "BTCUSDT", 2024, 1, "open_time", conformance=False)
        before = _man(out)
        assert "schema_version" not in before and "parquet_sha256" not in before

        result = build_corpus.stage_manifest_upgrade()
        assert result["upgraded"] == 1
        after = _man(out)
        assert after["schema_version"] == SCHEMA_VERSION
        assert len(after["parquet_sha256"]) == 64

    def test_upgrade_preserves_the_original_ingest_ts(self, spot_base):
        out = _stage1_partition(spot_base, "spot", "BTCUSDT", 2024, 1, "open_time", conformance=True)
        build_corpus.stage_manifest_upgrade()
        assert _man(out)["ingest_ts"] == ORIGINAL_TS

    def test_upgrade_never_touches_the_parquet(self, spot_base):
        import hashlib
        out = _stage1_partition(spot_base, "spot", "BTCUSDT", 2024, 1, "open_time", conformance=True)
        before = hashlib.sha256(out.read_bytes()).hexdigest()
        build_corpus.stage_manifest_upgrade()
        assert hashlib.sha256(out.read_bytes()).hexdigest() == before


class TestStage2Idempotence:
    def test_second_run_upgrades_nothing(self, spot_base):
        _stage1_partition(spot_base, "spot", "BTCUSDT", 2024, 1, "open_time", conformance=True)
        first = build_corpus.stage_manifest_upgrade()
        assert first["upgraded"] == 1

        second = build_corpus.stage_manifest_upgrade()
        assert second["upgraded"] == 0
        assert second["already_wi6"] == 1

    def test_second_run_leaves_the_manifest_byte_identical(self, spot_base):
        out = _stage1_partition(spot_base, "spot", "BTCUSDT", 2024, 1, "open_time", conformance=True)
        build_corpus.stage_manifest_upgrade()
        after_first = (out.parent / "_manifest.json").read_bytes()
        build_corpus.stage_manifest_upgrade()
        assert (out.parent / "_manifest.json").read_bytes() == after_first

    def test_perp_second_run_upgrades_nothing(self, perp_base):
        _stage1_partition(perp_base, "perp", "BTCUSDT", 2024, 1, "open_time", conformance=True)
        assert build_perp_corpus.stage_manifest_upgrade()["upgraded"] == 1
        assert build_perp_corpus.stage_manifest_upgrade()["upgraded"] == 0


# ---------------------------------------------------------------------------
# The three real stage-1-only partitions (F2)
# ---------------------------------------------------------------------------

real_corpus = pytest.mark.skipif(not REAL_RAW.exists(), reason="real corpus not present")

LEGACY_THREE = [("funding", "BTCUSDT", 2026, 6), ("perp", "BTCUSDT", 2026, 6), ("spot", "BTCUSDT", 2026, 6)]


@real_corpus
class TestRealLegacyPartitions:
    def test_all_three_are_now_at_current_schema(self):
        """F2 resolved via option (d): the designed stage-2 upgrade, not a
        recomputed-and-adopted hash and not a special case."""
        for ds, sym, y, m in LEGACY_THREE:
            man = json.loads(
                (REAL_RAW / ds / sym / f"year={y}" / f"month={m:02d}" / "_manifest.json")
                .read_text(encoding="utf-8")
            )
            assert man["schema_version"] == SCHEMA_VERSION, f"{ds} {y}-{m:02d}"
            assert len(man["parquet_sha256"]) == 64, f"{ds} {y}-{m:02d}"

    def test_their_original_ingest_ts_survived_the_upgrade(self):
        for ds, sym, y, m in LEGACY_THREE:
            man = json.loads(
                (REAL_RAW / ds / sym / f"year={y}" / f"month={m:02d}" / "_manifest.json")
                .read_text(encoding="utf-8")
            )
            assert man["ingest_ts"].startswith("2026-06-10T17:28:5"), f"{ds}: {man['ingest_ts']}"

    def test_no_partition_in_the_corpus_lacks_a_hash_any_more(self):
        missing = []
        for man_path in REAL_RAW.rglob("_manifest.json"):
            if "_backup" in str(man_path) or "_raw_lake" in str(man_path):
                continue
            man = json.loads(man_path.read_text(encoding="utf-8"))
            if "parquet_sha256" not in man:
                missing.append(man_path.parent.relative_to(REAL_RAW))
        assert missing == [], f"{len(missing)} partition(s) still lack parquet_sha256: {missing[:5]}"

    def test_dvol_conformance_from_d0a_is_untouched(self):
        """WBS 4.0.1 must not disturb D0a's output."""
        dvol = sorted((REAL_RAW / "dvol" / "BTC").glob("year=*/month=*/_manifest.json"))
        assert len(dvol) == 64
        for p in dvol:
            man = json.loads(p.read_text(encoding="utf-8"))
            assert man["admissibility"] is not None
            assert man["raw_content_hash"] == man["parquet_sha256"]
