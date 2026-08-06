"""
Tests for research/label_store/superseded.py (Phase 3 Label Store roadmap
v1.1.0, WBS 3.6 -- the sixth Label Store milestone).

Per WBS 3.6's own exit criterion: "A simulated correction flags exactly
the right labels and neither rewrites nor trusts them; Flagged labels are
inadmissible until adjudicated; adjudication is human-gated and recorded."

TWO TIERS, mirroring tests/test_conditioning.py's own established split --
with one correction found while writing these tests (recorded here, not
silently worked around): unlike conditioning.py, raw_lake.py imports
pandas at MODULE level (`import pandas as pd`, top of file), not
function-locally -- so merely importing raw_lake.py requires pandas, even
though the one function this module actually calls
(read_revisions()) is itself pure JSON and needs none. This is a real,
structural fact about raw_lake.py's own design, not something this
milestone can route around without touching raw_lake.py itself (out of
scope -- "no unrelated refactoring" -- it is WBS 1.2/1.3's already-shipped
module). Consequently, gating here is class-level on "does this test call
find_affected_labels()/flag_affected_labels() at all" (which transitively
imports raw_lake.py), not on "does this test write real Parquet" as
test_conditioning.py's own split does:
  - Base-env-portable, no gating: TestIsStructuredReference (pure),
    TestNoDatetimeNow (AST-only), and the structural half of
    TestAdjudicationIsHumanGated that never calls into raw_lake.py.
  - Pandas-gated (class-level skipif, individually, not a module-level
    importorskip): every class whose tests call find_affected_labels()/
    flag_affected_labels(), including the hand-constructed-fixture ones --
    they exercise superseded.py's own pure detection LOGIC, but that logic
    lives behind raw_lake.py's module-level pandas import regardless of
    which raw_lake.py function is actually called.
  - TestRealRawLakeIntegration additionally exercises raw_lake.land_raw()
    for real (genuine Parquet writes) -- the roadmap's own entry
    criterion ("a disposable copy of a revisioned partition ... to
    simulate a correction against"), exercised literally.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_LABEL_STORE_DIR = _REPO_ROOT / "research" / "label_store"

_vs_spec = importlib.util.spec_from_file_location("label_store_value_store", _LABEL_STORE_DIR / "value_store.py")
vs = importlib.util.module_from_spec(_vs_spec)
sys.modules["label_store_value_store"] = vs
_vs_spec.loader.exec_module(vs)

_sup_spec = importlib.util.spec_from_file_location("label_store_superseded", _LABEL_STORE_DIR / "superseded.py")
superseded = importlib.util.module_from_spec(_sup_spec)
sys.modules["label_store_superseded"] = superseded
_sup_spec.loader.exec_module(superseded)

_PANDAS_AVAILABLE = importlib.util.find_spec("pandas") is not None

SPEC_HASH = "deadbeef" * 8
NOW = "2026-08-06T00:00:00+00:00"
FLAGGED_AT = "2026-08-06T12:00:00+00:00"


def _write_revisions(base_dir: Path, dataset: str, symbol: str, year: int, month: int, hashes: list[str]) -> None:
    """Hand-constructs a _revisions.json in raw_lake.py's exact schema and
    directory layout, without calling raw_lake.py at all -- keeps
    detection-logic tests pandas-free (raw_lake.read_revisions() itself
    only does json.loads(), which this fixture exercises for real)."""
    part_dir = base_dir / "_raw_lake" / dataset / symbol / f"year={year}" / f"month={month:02d}"
    part_dir.mkdir(parents=True, exist_ok=True)
    entries = [
        {
            "revision_ordinal": i + 1, "hash": h, "rows": 100,
            "dataset": dataset, "symbol": symbol, "year": year, "month": month,
            "source": "test", "source_url": None, "conditioning": "preserving",
            "fetched_at": NOW,
        }
        for i, h in enumerate(hashes)
    ]
    (part_dir / "_revisions.json").write_text(json.dumps(entries, indent=2), encoding="utf-8")


def _structured_ref(dataset: str, symbol: str, year: int, month: int, h: str) -> dict:
    return {"dataset": dataset, "symbol": symbol, "year": year, "month": month, "hash": h}


@pytest.fixture
def db(tmp_path) -> Path:
    p = tmp_path / "label_store.db"
    vs.init_db(p)
    return p


def _declare_matured(db, subject_id, *, revision_set, outcome=None):
    vs.declare_forward_label(db, SPEC_HASH, subject_id, NOW, declared_by="live-test-declaration")
    vs.observe_forward_label(db, SPEC_HASH, subject_id, NOW, observed_at=NOW)
    vs.mature_forward_label(
        db, SPEC_HASH, subject_id, NOW,
        outcome=outcome or {"forward_return": 0.05},
        input_revision_set=revision_set, matured_at=NOW,
    )


# ---------------------------------------------------------------------------
# _is_structured_reference
# ---------------------------------------------------------------------------

class TestIsStructuredReference:
    def test_well_formed_dict_is_structured(self):
        assert superseded._is_structured_reference(_structured_ref("d", "s", 2026, 1, "h1")) is True

    def test_plain_string_is_not_structured(self):
        # WBS 3.4's own H6 placeholder shape.
        assert superseded._is_structured_reference("research/data/forward_evidence.db (predates ...)") is False

    def test_dict_missing_keys_is_not_structured(self):
        assert superseded._is_structured_reference({"dataset": "d", "hash": "h1"}) is False

    def test_none_is_not_structured(self):
        assert superseded._is_structured_reference(None) is False


# ---------------------------------------------------------------------------
# find_affected_labels -- pure detection, hand-constructed revisions.json
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _PANDAS_AVAILABLE, reason="raw_lake.py imports pandas at module level")
class TestFindAffectedLabels:
    def test_no_partition_landed_returns_empty(self, db, tmp_path):
        result = superseded.find_affected_labels(db, tmp_path, "dvol", "BTC", 2026, 1)
        assert result == []

    def test_single_revision_no_correction_finds_nothing(self, db, tmp_path):
        _write_revisions(tmp_path, "dvol", "BTC", 2026, 1, ["hash-a"])
        _declare_matured(db, "sig-1", revision_set=[_structured_ref("dvol", "BTC", 2026, 1, "hash-a")])
        result = superseded.find_affected_labels(db, tmp_path, "dvol", "BTC", 2026, 1)
        assert result == []

    def test_correction_finds_exactly_the_stale_citing_label(self, db, tmp_path):
        _write_revisions(tmp_path, "dvol", "BTC", 2026, 1, ["hash-a", "hash-b"])  # corrected: b supersedes a
        _declare_matured(db, "sig-stale", revision_set=[_structured_ref("dvol", "BTC", 2026, 1, "hash-a")])
        _declare_matured(db, "sig-current", revision_set=[_structured_ref("dvol", "BTC", 2026, 1, "hash-b")])

        result = superseded.find_affected_labels(db, tmp_path, "dvol", "BTC", 2026, 1)
        assert len(result) == 1
        assert result[0]["subject_id"] == "sig-stale"
        assert result[0]["cited_hash"] == "hash-a"
        assert result[0]["latest_hash"] == "hash-b"

    def test_unrelated_partition_is_not_affected(self, db, tmp_path):
        _write_revisions(tmp_path, "dvol", "BTC", 2026, 1, ["hash-a", "hash-b"])
        _write_revisions(tmp_path, "funding", "BTC", 2026, 1, ["hash-x"])
        _declare_matured(db, "sig-other", revision_set=[_structured_ref("funding", "BTC", 2026, 1, "hash-x")])
        result = superseded.find_affected_labels(db, tmp_path, "dvol", "BTC", 2026, 1)
        assert result == []

    def test_unstructured_revision_set_is_never_matched(self, db, tmp_path):
        # WBS 3.4's own real H6 placeholder string, verbatim.
        _write_revisions(tmp_path, "dvol", "BTC", 2026, 1, ["hash-a", "hash-b"])
        _declare_matured(
            db, "h6-trade-1",
            revision_set=[
                "research/data/forward_evidence.db (predates Phase 1 raw_lake.py "
                "revisioning; WBS 3.6 will reconcile against a real revision index)"
            ],
        )
        result = superseded.find_affected_labels(db, tmp_path, "dvol", "BTC", 2026, 1)
        assert result == []

    def test_only_matured_labels_are_considered(self, db, tmp_path):
        _write_revisions(tmp_path, "dvol", "BTC", 2026, 1, ["hash-a", "hash-b"])
        vs.declare_forward_label(db, SPEC_HASH, "sig-declared-only", NOW, declared_by="x")
        result = superseded.find_affected_labels(db, tmp_path, "dvol", "BTC", 2026, 1)
        assert result == []

    def test_already_flagged_label_is_not_rediscovered(self, db, tmp_path):
        _write_revisions(tmp_path, "dvol", "BTC", 2026, 1, ["hash-a", "hash-b"])
        _declare_matured(db, "sig-stale", revision_set=[_structured_ref("dvol", "BTC", 2026, 1, "hash-a")])
        vs.flag_forward_label(db, SPEC_HASH, "sig-stale", NOW, flagged_at=FLAGGED_AT, flag_reason="x")
        result = superseded.find_affected_labels(db, tmp_path, "dvol", "BTC", 2026, 1)
        assert result == []


# ---------------------------------------------------------------------------
# flag_affected_labels -- dry-run / commit / idempotency
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _PANDAS_AVAILABLE, reason="raw_lake.py imports pandas at module level")
class TestFlagAffectedLabels:
    def _setup_one_stale(self, db, tmp_path):
        _write_revisions(tmp_path, "dvol", "BTC", 2026, 1, ["hash-a", "hash-b"])
        _declare_matured(db, "sig-stale", revision_set=[_structured_ref("dvol", "BTC", 2026, 1, "hash-a")])

    def test_dry_run_flags_nothing(self, db, tmp_path):
        self._setup_one_stale(db, tmp_path)
        result = superseded.flag_affected_labels(db, tmp_path, "dvol", "BTC", 2026, 1, FLAGGED_AT, commit=False)
        assert result["would_flag"] == 1
        assert result["flagged"] == 0
        label = vs.get_forward_label(db, SPEC_HASH, "sig-stale", NOW)
        assert label["maturity_state"] == "Matured"

    def test_commit_flags_the_affected_label(self, db, tmp_path):
        self._setup_one_stale(db, tmp_path)
        result = superseded.flag_affected_labels(db, tmp_path, "dvol", "BTC", 2026, 1, FLAGGED_AT, commit=True)
        assert result["flagged"] == 1
        label = vs.get_forward_label(db, SPEC_HASH, "sig-stale", NOW)
        assert label["maturity_state"] == "Flagged"
        assert "hash-a" in label["flag_reason"]
        assert "hash-b" in label["flag_reason"]

    def test_second_commit_run_is_idempotent(self, db, tmp_path):
        self._setup_one_stale(db, tmp_path)
        first = superseded.flag_affected_labels(db, tmp_path, "dvol", "BTC", 2026, 1, FLAGGED_AT, commit=True)
        second = superseded.flag_affected_labels(db, tmp_path, "dvol", "BTC", 2026, 1, FLAGGED_AT, commit=True)
        assert first["flagged"] == 1
        assert second["would_flag"] == 0
        assert second["flagged"] == 0

    def test_cli_dry_run_then_commit(self, db, tmp_path, capsys):
        self._setup_one_stale(db, tmp_path)
        rc = superseded.main([
            "--label-store-db", str(db), "--base-dir", str(tmp_path),
            "--dataset", "dvol", "--symbol", "BTC", "--year", "2026", "--month", "1",
            "--flagged-at", FLAGGED_AT,
        ])
        assert rc == 0
        assert "Dry run only" in capsys.readouterr().out

        rc = superseded.main([
            "--label-store-db", str(db), "--base-dir", str(tmp_path),
            "--dataset", "dvol", "--symbol", "BTC", "--year", "2026", "--month", "1",
            "--flagged-at", FLAGGED_AT, "--commit",
        ])
        assert rc == 0
        label = vs.get_forward_label(db, SPEC_HASH, "sig-stale", NOW)
        assert label["maturity_state"] == "Flagged"


# ---------------------------------------------------------------------------
# "Neither rewritten nor trusted" -- the exit criterion's central claim
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _PANDAS_AVAILABLE, reason="raw_lake.py imports pandas at module level")
class TestNeitherRewrittenNorTrusted:
    def test_flagging_does_not_change_outcome_or_matured_at(self, db, tmp_path):
        _write_revisions(tmp_path, "dvol", "BTC", 2026, 1, ["hash-a", "hash-b"])
        _declare_matured(
            db, "sig-stale", revision_set=[_structured_ref("dvol", "BTC", 2026, 1, "hash-a")],
            outcome={"forward_return": 0.0421},
        )
        before = vs.get_forward_label(db, SPEC_HASH, "sig-stale", NOW)

        superseded.flag_affected_labels(db, tmp_path, "dvol", "BTC", 2026, 1, FLAGGED_AT, commit=True)

        after = vs.get_forward_label(db, SPEC_HASH, "sig-stale", NOW)
        assert after["outcome"] == before["outcome"] == {"forward_return": 0.0421}
        assert after["matured_at"] == before["matured_at"]
        assert after["input_revision_set"] == before["input_revision_set"]

    def test_direct_sql_still_cannot_rewrite_outcome_after_flagging(self, db, tmp_path):
        # Re-confirms WBS 3.2's own guarantee holds in THIS milestone's
        # real usage path, not just its original hand-written test.
        _write_revisions(tmp_path, "dvol", "BTC", 2026, 1, ["hash-a", "hash-b"])
        _declare_matured(db, "sig-stale", revision_set=[_structured_ref("dvol", "BTC", 2026, 1, "hash-a")])
        superseded.flag_affected_labels(db, tmp_path, "dvol", "BTC", 2026, 1, FLAGGED_AT, commit=True)

        con = sqlite3.connect(db)
        with pytest.raises(sqlite3.IntegrityError, match="LABEL_TAMPER"):
            con.execute(
                "UPDATE label_value_forward SET outcome_json = '{\"forward_return\": 999}' "
                "WHERE spec_hash = ? AND subject_id = ? AND declared_at = ?",
                (SPEC_HASH, "sig-stale", NOW),
            )
        con.close()

    def test_flagged_label_is_excluded_from_a_matured_only_query(self, db, tmp_path):
        _write_revisions(tmp_path, "dvol", "BTC", 2026, 1, ["hash-a", "hash-b"])
        _declare_matured(db, "sig-stale", revision_set=[_structured_ref("dvol", "BTC", 2026, 1, "hash-a")])
        _declare_matured(db, "sig-current", revision_set=[_structured_ref("dvol", "BTC", 2026, 1, "hash-b")])
        superseded.flag_affected_labels(db, tmp_path, "dvol", "BTC", 2026, 1, FLAGGED_AT, commit=True)

        con = sqlite3.connect(db)
        matured_subjects = {
            r[0] for r in con.execute("SELECT subject_id FROM label_value_forward WHERE maturity_state = 'Matured'")
        }
        con.close()
        assert matured_subjects == {"sig-current"}   # the flagged one is excluded -- "not trusted"


# ---------------------------------------------------------------------------
# Adjudication -- human-gated, never automatic
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _PANDAS_AVAILABLE, reason="raw_lake.py imports pandas at module level")
class TestAdjudicationLifecycle:
    def test_full_lifecycle_flag_then_separate_adjudication(self, db, tmp_path):
        _write_revisions(tmp_path, "dvol", "BTC", 2026, 1, ["hash-a", "hash-b"])
        _declare_matured(db, "sig-stale", revision_set=[_structured_ref("dvol", "BTC", 2026, 1, "hash-a")])
        superseded.flag_affected_labels(db, tmp_path, "dvol", "BTC", 2026, 1, FLAGGED_AT, commit=True)

        label = vs.get_forward_label(db, SPEC_HASH, "sig-stale", NOW)
        assert label["maturity_state"] == "Flagged"
        assert label["adjudicated_at"] is None

        # A SEPARATE, explicit, human-initiated call -- never triggered by superseded.py itself.
        vs.adjudicate_forward_label(
            db, SPEC_HASH, "sig-stale", NOW,
            adjudicated_at="2026-08-07T00:00:00+00:00",
            adjudication_decision="Reviewed: correction was a minor precision fix, outcome unaffected. Admissible.",
        )
        label = vs.get_forward_label(db, SPEC_HASH, "sig-stale", NOW)
        assert label["maturity_state"] == "Adjudicated"
        assert "Admissible" in label["adjudication_decision"]


class TestAdjudicationNeverAutomated:
    def test_superseded_module_never_calls_adjudicate_forward_label(self):
        source = (_LABEL_STORE_DIR / "superseded.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "adjudicate_forward_label":
                pytest.fail(f"superseded.py must never call adjudicate_forward_label (line {node.lineno}) -- "
                            f"adjudication is a human act (LG1/IQROS-P8), never automated")
            if isinstance(node, ast.Name) and node.id == "adjudicate_forward_label":
                pytest.fail(f"superseded.py must never reference adjudicate_forward_label (line {node.lineno})")


# ---------------------------------------------------------------------------
# LR5 injected-clock discipline
# ---------------------------------------------------------------------------

class TestNoDatetimeNow:
    def test_no_datetime_now_anywhere_in_superseded_py(self):
        source = (_LABEL_STORE_DIR / "superseded.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "now":
                pytest.fail(f"found a '.now' attribute access at line {node.lineno}")


# ---------------------------------------------------------------------------
# Real raw_lake.py integration -- "a disposable copy of a revisioned
# partition ... to simulate a correction against" (the roadmap's own
# entry criterion, exercised literally, not just via a hand-built fixture)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _PANDAS_AVAILABLE, reason="pandas/pyarrow not installed (needed by raw_lake.py for real)")
class TestRealRawLakeIntegration:
    def test_real_land_raw_correction_is_detected_and_flagged(self, db, tmp_path):
        import pandas as pd
        sys.path.insert(0, str(_REPO_ROOT / "research" / "storage"))
        import raw_lake

        df_v1 = pd.DataFrame({"day_ts": [1, 2, 3], "close": [100.0, 101.0, 102.0]})
        df_v2 = pd.DataFrame({"day_ts": [1, 2, 3], "close": [100.0, 101.0, 103.0]})  # a real correction

        r1 = raw_lake.land_raw(
            df_v1, tmp_path, dataset="dvol", symbol="BTC", year=2026, month=1,
            source="test", source_url=None, conditioning="preserving", fetched_at=NOW,
        )
        assert r1["is_new_revision"] is True

        _declare_matured(
            db, "sig-real-v1",
            revision_set=[_structured_ref("dvol", "BTC", 2026, 1, r1["hash"])],
        )

        r2 = raw_lake.land_raw(
            df_v2, tmp_path, dataset="dvol", symbol="BTC", year=2026, month=1,
            source="test", source_url=None, conditioning="preserving", fetched_at=FLAGGED_AT,
        )
        assert r2["is_new_revision"] is True
        assert r2["hash"] != r1["hash"]

        result = superseded.flag_affected_labels(
            db, tmp_path, "dvol", "BTC", 2026, 1, FLAGGED_AT, commit=True,
        )
        assert result["flagged"] == 1
        label = vs.get_forward_label(db, SPEC_HASH, "sig-real-v1", NOW)
        assert label["maturity_state"] == "Flagged"

    def test_real_landing_of_identical_content_is_not_a_correction(self, db, tmp_path):
        import pandas as pd
        sys.path.insert(0, str(_REPO_ROOT / "research" / "storage"))
        import raw_lake

        df = pd.DataFrame({"day_ts": [1, 2, 3], "close": [100.0, 101.0, 102.0]})
        r1 = raw_lake.land_raw(
            df, tmp_path, dataset="dvol", symbol="BTC", year=2026, month=1,
            source="test", source_url=None, conditioning="preserving", fetched_at=NOW,
        )
        _declare_matured(db, "sig-real", revision_set=[_structured_ref("dvol", "BTC", 2026, 1, r1["hash"])])

        r2 = raw_lake.land_raw(  # byte-identical re-fetch -- raw_lake.py's own idempotency
            df, tmp_path, dataset="dvol", symbol="BTC", year=2026, month=1,
            source="test", source_url=None, conditioning="preserving", fetched_at=FLAGGED_AT,
        )
        assert r2["is_new_revision"] is False
        assert r2["hash"] == r1["hash"]

        result = superseded.find_affected_labels(db, tmp_path, "dvol", "BTC", 2026, 1)
        assert result == []
