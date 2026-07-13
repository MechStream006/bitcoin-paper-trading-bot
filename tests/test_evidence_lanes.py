"""
Tests for the Evidence Matrix lane schema (PR-5 of
docs/governance/evidence_reporting_architecture.md), record.py side:
run_kind, the frozen lane table, current_run_per_lane selection and its
tiebreak, and the migrate_run_kind.py backfill.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_EVLIB = ROOT / "research" / "evidence_library"
sys.path.insert(0, str(_EVLIB))

import record
import migrate_run_kind as mrk


@pytest.fixture
def db_path(tmp_path) -> Path:
    p = tmp_path / "ledger_test.db"
    record.init_db(p)
    return p


def _run(db, family_id="fam_a", provenance="offline-corpus", run_kind="discovery", **metrics):
    return record.append_run(db, family_id, metrics, provenance, run_kind, n_trials=34)


# ---------------------------------------------------------------------------
# Lane validation
# ---------------------------------------------------------------------------

class TestLaneValidation:
    def test_valid_lane_accepted(self, db_path):
        run_id = _run(db_path, provenance="offline-corpus", run_kind="discovery")
        run = record.get_run(db_path, run_id)
        assert run["run_kind"] == "discovery"

    def test_all_four_frozen_lanes_accepted(self, db_path):
        for provenance, run_kind in record.LANES.values():
            run_id = record.append_run(
                db_path, f"fam_{run_kind}", {"sharpe": 0.1}, provenance, run_kind, n_trials=34
            )
            assert record.get_run(db_path, run_id)["run_kind"] == run_kind

    def test_invalid_run_kind_rejected(self, db_path):
        with pytest.raises(ValueError, match="Invalid run_kind"):
            record.append_run(db_path, "fam_a", {}, "offline-corpus", "bogus")

    def test_invalid_provenance_rejected(self, db_path):
        with pytest.raises(ValueError, match="Invalid provenance"):
            record.append_run(db_path, "fam_a", {}, "bogus-provenance", "discovery")

    def test_illegal_lane_pair_rejected(self, db_path):
        # offline-corpus + forward is a contradiction (§3): forward is only
        # ever live-forward.
        with pytest.raises(ValueError, match="Invalid lane"):
            record.append_run(db_path, "fam_a", {}, "offline-corpus", "forward")

    def test_illegal_lane_pair_mainnet_discovery_rejected(self, db_path):
        with pytest.raises(ValueError, match="Invalid lane"):
            record.append_run(db_path, "fam_a", {}, "mainnet", "discovery")

    def test_run_kind_is_required_positional(self, db_path):
        with pytest.raises(TypeError):
            record.append_run(db_path, "fam_a", {}, "offline-corpus")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# current_run_per_lane / get_current_run(s)_by_lane — Law 3: within-lane selection
# ---------------------------------------------------------------------------

class TestCurrentRunPerLane:
    def test_one_row_per_family_lane(self, db_path):
        _run(db_path, "fam_a", "offline-corpus", "discovery")
        _run(db_path, "fam_a", "offline-corpus", "confirmatory")
        by_lane = record.get_current_runs_by_lane(db_path)
        assert set(by_lane["fam_a"].keys()) == {"discovery", "confirmatory"}

    def test_selects_latest_within_lane(self, db_path):
        first = _run(db_path, "fam_a", "offline-corpus", "discovery", sharpe=0.1)
        second = _run(db_path, "fam_a", "offline-corpus", "discovery", sharpe=0.2)
        # Note: at most one L0/discovery is a governance invariant (C3), not
        # enforced by append_run() itself -- this test exercises the
        # *selection* mechanism in isolation from that invariant.
        current = record.get_current_run(db_path, "fam_a", "discovery")
        assert current["run_id"] == second

    def test_lanes_never_cross_contaminate(self, db_path):
        _run(db_path, "fam_a", "offline-corpus", "discovery", sharpe=0.1)
        _run(db_path, "fam_a", "live-forward", "forward", sharpe=0.9)
        by_lane = record.get_current_runs_by_lane(db_path)
        assert by_lane["fam_a"]["discovery"]["sharpe"] == 0.1
        assert by_lane["fam_a"]["forward"]["sharpe"] == 0.9

    def test_get_current_run_missing_lane_returns_none(self, db_path):
        _run(db_path, "fam_a", "offline-corpus", "discovery")
        assert record.get_current_run(db_path, "fam_a", "forward") is None

    def test_deterministic_tiebreak_on_identical_run_ts(self, db_path, monkeypatch):
        """
        evidence_reporting_architecture.md §1.4: two runs sharing an
        identical run_ts must produce a deterministic, stable winner (the
        explicit run_id DESC tiebreak) -- not whichever the cursor happens
        to yield last.
        """
        con = sqlite3.connect(db_path)
        shared_ts = "2026-06-21T14:08:41+00:00"
        con.execute(
            "INSERT INTO runs (run_id, family_id, run_ts, provenance, run_kind, sharpe, metrics_json) "
            "VALUES ('aaaa0000', 'fam_a', ?, 'offline-corpus', 'discovery', 0.1, '{}')",
            (shared_ts,),
        )
        con.execute(
            "INSERT INTO runs (run_id, family_id, run_ts, provenance, run_kind, sharpe, metrics_json) "
            "VALUES ('zzzz9999', 'fam_a', ?, 'offline-corpus', 'discovery', 0.2, '{}')",
            (shared_ts,),
        )
        con.commit()
        con.close()

        # run_id DESC -> 'zzzz9999' > 'aaaa0000' lexicographically -> wins.
        winner_1 = record.get_current_run(db_path, "fam_a", "discovery")
        winner_2 = record.get_current_run(db_path, "fam_a", "discovery")
        assert winner_1["run_id"] == "zzzz9999"
        assert winner_1["run_id"] == winner_2["run_id"], "must be stable across repeated calls"

    def test_as_of_reflects_only_runs_up_to_that_point(self, db_path):
        early = _run(db_path, "fam_a", "offline-corpus", "discovery")
        con = sqlite3.connect(db_path)
        con.execute("UPDATE runs SET run_ts = '2026-06-13T00:00:00+00:00' WHERE run_id = ?", (early,))
        con.commit()
        con.close()
        late = record.append_run(
            db_path, "fam_a", {"sharpe": 0.5}, "offline-corpus", "confirmatory", n_trials=34
        )
        con = sqlite3.connect(db_path)
        con.execute("UPDATE runs SET run_ts = '2026-06-20T00:00:00+00:00' WHERE run_id = ?", (late,))
        con.commit()
        con.close()

        before = record.get_current_runs_by_lane(db_path, as_of="2026-06-14T00:00:00+00:00")
        assert "confirmatory" not in before.get("fam_a", {})
        assert "discovery" in before["fam_a"]

        after = record.get_current_runs_by_lane(db_path, as_of="2026-06-25T00:00:00+00:00")
        assert "confirmatory" in after["fam_a"]


# ---------------------------------------------------------------------------
# get_latest_run — must now require a lane
# ---------------------------------------------------------------------------

class TestGetLatestRunRequiresLane:
    def test_get_latest_run_requires_run_kind_argument(self):
        import inspect
        sig = inspect.signature(record.get_latest_run)
        assert "run_kind" in sig.parameters
        assert sig.parameters["run_kind"].default is inspect.Parameter.empty

    def test_get_latest_run_within_lane(self, db_path):
        _run(db_path, "fam_a", "offline-corpus", "discovery", sharpe=0.1)
        _run(db_path, "fam_a", "offline-corpus", "confirmatory", sharpe=0.2)
        assert record.get_latest_run(db_path, "fam_a", "discovery")["sharpe"] == 0.1
        assert record.get_latest_run(db_path, "fam_a", "confirmatory")["sharpe"] == 0.2

    def test_get_latest_run_bad_lane_rejected(self, db_path):
        with pytest.raises(ValueError):
            record.get_latest_run(db_path, "fam_a", "bogus")


# ---------------------------------------------------------------------------
# Deletions: get_all_latest_runs / latest_run_per_family must be GONE
# ---------------------------------------------------------------------------

class TestDeprecatedSelectionDeleted:
    def test_get_all_latest_runs_does_not_exist(self):
        assert not hasattr(record, "get_all_latest_runs")

    def test_latest_run_per_family_view_does_not_exist(self, db_path):
        con = sqlite3.connect(db_path)
        views = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='view'")}
        con.close()
        assert "latest_run_per_family" not in views
        assert "current_run_per_lane" in views


# ---------------------------------------------------------------------------
# has_live_evidence redefinition
# ---------------------------------------------------------------------------

class TestHasLiveEvidence:
    def test_empty_forward_row_does_not_count(self, db_path):
        _run(db_path, "fam_a", "live-forward", "forward", n_trades=0)
        con = sqlite3.connect(db_path)
        rows = con.execute("SELECT family_id FROM has_live_evidence").fetchall()
        con.close()
        assert rows == []

    def test_nonempty_forward_row_counts(self, db_path):
        _run(db_path, "fam_a", "live-forward", "forward", n_trades=3, sharpe=0.4)
        con = sqlite3.connect(db_path)
        rows = con.execute("SELECT family_id FROM has_live_evidence").fetchall()
        con.close()
        assert rows == [("fam_a",)]

    def test_offline_corpus_never_counts(self, db_path):
        _run(db_path, "fam_a", "offline-corpus", "discovery", n_trades=100, sharpe=0.4)
        con = sqlite3.connect(db_path)
        rows = con.execute("SELECT family_id FROM has_live_evidence").fetchall()
        con.close()
        assert rows == []


# ---------------------------------------------------------------------------
# migrate_run_kind.py backfill
# ---------------------------------------------------------------------------

class TestMigrateRunKindBackfill:
    def _seed_legacy_rows(self, db_path: Path) -> None:
        """Insert rows the OLD way (no run_kind), mimicking a pre-PR-5 ledger."""
        con = sqlite3.connect(db_path)
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY, family_id TEXT NOT NULL, run_ts TEXT NOT NULL,
                git_sha TEXT, fn_hash TEXT, provenance TEXT NOT NULL,
                window_start TEXT, window_end TEXT, engine_version TEXT, n_trials INTEGER,
                sharpe REAL, oos_sharpe REAL, evidence_score REAL, verdict TEXT,
                n_trades INTEGER, dsr_prob REAL, bh_p REAL, metrics_json TEXT NOT NULL
            );
            """
        )
        rows = [
            ("r1", "fam_a", "2026-06-13T00:00:00+00:00", "offline-corpus"),
            ("r2", "fam_a", "2026-06-14T00:00:00+00:00", "offline-corpus"),
            ("r3", "fam_a", "2026-06-25T00:00:00+00:00", "offline-corpus"),
            ("r4", "fam_b", "2026-06-13T00:00:00+00:00", "offline-corpus"),
            ("r5", "fam_b", "2026-06-20T00:00:00+00:00", "live-forward"),
        ]
        for run_id, fam, ts, prov in rows:
            con.execute(
                "INSERT INTO runs (run_id, family_id, run_ts, provenance, metrics_json) VALUES (?,?,?,?,'{}')",
                (run_id, fam, ts, prov),
            )
        con.commit()
        con.close()
        # Realistic migration flow always runs the schema migration (adds
        # the nullable run_kind column) before classification -- main()
        # does this via record.init_db(); tests calling classify()/apply()
        # directly must do the same to simulate a pre-PR-5 ledger correctly.
        record.init_db(db_path)

    def test_classify_produces_expected_split(self, tmp_path):
        db = tmp_path / "legacy.db"
        self._seed_legacy_rows(db)
        plan = mrk.classify(db)
        by_id = {p[0]: p[4] for p in plan}
        assert by_id["r1"] == "discovery"     # earliest offline-corpus for fam_a
        assert by_id["r2"] == "confirmatory"  # later offline-corpus for fam_a
        assert by_id["r3"] == "confirmatory"  # later still
        assert by_id["r4"] == "discovery"     # earliest (only) offline-corpus for fam_b
        assert by_id["r5"] == "forward"       # live-forward -> forward

    def test_apply_writes_the_plan(self, tmp_path):
        db = tmp_path / "legacy.db"
        self._seed_legacy_rows(db)
        plan = mrk.classify(db)
        mrk.apply(db, plan)
        con = sqlite3.connect(db)
        got = dict(con.execute("SELECT run_id, run_kind FROM runs").fetchall())
        con.close()
        assert got == {"r1": "discovery", "r2": "confirmatory", "r3": "confirmatory",
                        "r4": "discovery", "r5": "forward"}

    def test_idempotent_second_classify_is_empty(self, tmp_path):
        db = tmp_path / "legacy.db"
        self._seed_legacy_rows(db)
        mrk.apply(db, mrk.classify(db))
        assert mrk.classify(db) == []

    def test_refuses_to_overwrite_non_null_run_kind(self, tmp_path):
        db = tmp_path / "legacy.db"
        self._seed_legacy_rows(db)  # already migrates in the run_kind column
        con = sqlite3.connect(db)
        con.execute("UPDATE runs SET run_kind = 'discovery' WHERE run_id = 'r1'")
        con.commit()
        con.close()
        plan = mrk.classify(db)
        assert "r1" not in {p[0] for p in plan}
        mrk.apply(db, plan)
        con = sqlite3.connect(db)
        assert con.execute("SELECT run_kind FROM runs WHERE run_id='r1'").fetchone()[0] == "discovery"
        con.close()

    def test_backup_created_before_commit(self, tmp_path, monkeypatch):
        db = tmp_path / "ledger.db"
        self._seed_legacy_rows(db)
        rc = mrk.main(["--db", str(db), "--commit"])
        assert rc == 0
        backups = list(tmp_path.glob("ledger.pre-run_kind-migration.*.db"))
        assert len(backups) == 1

    def test_dry_run_writes_nothing(self, tmp_path):
        db = tmp_path / "ledger.db"
        self._seed_legacy_rows(db)
        rc = mrk.main(["--db", str(db)])
        assert rc == 0
        con = sqlite3.connect(db)
        run_kinds = [row[0] for row in con.execute("SELECT run_kind FROM runs").fetchall()]
        con.close()
        assert all(rk is None for rk in run_kinds)
        assert list(tmp_path.glob("*.audit.txt")) == []

    def test_unrecognised_provenance_refuses_commit(self, tmp_path):
        db = tmp_path / "legacy.db"
        self._seed_legacy_rows(db)
        con = sqlite3.connect(db)
        con.execute(
            "INSERT INTO runs (run_id, family_id, run_ts, provenance, metrics_json) "
            "VALUES ('r6', 'fam_c', '2026-06-13T00:00:00+00:00', 'weird-prov', '{}')"
        )
        con.commit()
        con.close()
        rc = mrk.main(["--db", str(db), "--commit"])
        assert rc == 1
        con = sqlite3.connect(db)
        assert con.execute("SELECT run_kind FROM runs WHERE run_id='r1'").fetchone()[0] is None
        con.close()


# ---------------------------------------------------------------------------
# The real ledger, post-migration -- matches the documented known-correct split
# ---------------------------------------------------------------------------

class TestRealLedgerBackfillMatchesKnownHistory:
    """
    evidence_reporting_architecture.md §9: "This exactly reproduces the
    known history: the 2026-06-13 bootstrap seed is L0; the 2026-06-14
    re-seed and the 2026-06-21 IVRV / 2026-06-25 ATR admission runs are L1."
    """

    def test_no_unclassified_rows_remain(self):
        db = _EVLIB / "ledger.db"
        if not db.exists():
            pytest.skip("real ledger.db not present in this checkout")
        plan = mrk.classify(db)
        assert plan == [], "the real ledger must already be fully backfilled by this PR"

    def test_g3_atr_expansion_split(self):
        db = _EVLIB / "ledger.db"
        if not db.exists():
            pytest.skip("real ledger.db not present in this checkout")
        rows = record.get_all_runs(db, family_id="g3_atr_expansion")
        by_id = {r["run_id"][:8]: r["run_kind"] for r in rows}
        assert by_id["be1849d4"] == "discovery"
        assert by_id["ed84b4a6"] == "confirmatory"
        assert by_id["0c7e1bf9"] == "confirmatory"
