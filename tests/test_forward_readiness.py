"""
FB-12e — generalized forward readiness producer.

Proves the properties the gate requires, including end-to-end consumption
through the real DefaultReadinessProvider and the real TG-RDY path.
"""
from __future__ import annotations

import ast
import datetime
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in ("research/dq", "research/evidence_library", "research"):
    sys.path.insert(0, str(ROOT / p))

import contract as dqc  # noqa: E402
import contract_lifecycle as cl  # noqa: E402
import contract_registry as cr  # noqa: E402
import readiness as dqr  # noqa: E402
import readiness_gate  # noqa: E402
import states as dq_states  # noqa: E402

from forward_evidence import forward_readiness as fr  # noqa: E402

GAP_START, GAP_END = "2026-07-18", "2026-08-05"
W_START, W_END = "2026-06-15", "2026-08-29"


def _forward_db(tmp_path, family="h5_vol_regime", start=W_START, end=W_END,
                skip=(), extra_gap=()):
    db = tmp_path / "fwd.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE run_log (id INTEGER PRIMARY KEY, signal_id TEXT, "
                "run_date TEXT, status TEXT)")
    d0 = datetime.date.fromisoformat(start)
    d1 = datetime.date.fromisoformat(end)
    for i in range((d1 - d0).days + 1):
        day = (d0 + datetime.timedelta(days=i)).isoformat()
        if GAP_START <= day <= GAP_END:
            continue                                   # INC-0005: nothing written
        if day in skip or day in extra_gap:
            continue
        con.execute("INSERT INTO run_log (signal_id,run_date,status) VALUES (?,?,'ok')",
                    (family, day))
    con.commit()
    con.close()
    return db


def _ledger(tmp_path, family="h5_vol_regime"):
    db = tmp_path / "ledger.db"
    cr.init_db(db)
    dqr.init_db(db)
    cl.activate_contract(db, contract_id=family, body=fr.build_contract_doc(family),
                         actor="tester", reason="FB-12e test")
    return db


# ---------------------------------------------------------------------------
# 1. all seven families generalize; contract_id == family_id
# ---------------------------------------------------------------------------

class TestGeneralization:
    @pytest.mark.parametrize("family", fr.FAMILIES)
    def test_every_family_builds_a_valid_contract(self, family):
        doc = fr.build_contract_doc(family)
        assert dqc.validate_contract(dqc.parse_contract(doc)) == []

    @pytest.mark.parametrize("family", fr.FAMILIES)
    def test_contract_id_equals_family_id(self, family):
        doc = fr.build_contract_doc(family)
        assert doc["contract_id"] == family
        assert doc["produces"] == [family]

    def test_seven_families(self):
        assert len(fr.FAMILIES) == 7 and len(set(fr.FAMILIES)) == 7

    def test_roster_matches_run_daily(self):
        """Drift guard: duplicating run_daily's roster is what once let
        g1_trend_regime_ma_cross be wired in but missing from the audit."""
        from forward_evidence import run_daily  # noqa: PLC0415
        close_only = set(run_daily._CLOSE_ONLY_SIGNAL_IDS)
        assert close_only <= set(fr.FAMILIES)
        assert set(fr.FAMILIES) - close_only == {
            "ivrv_vrp_spread", "g2_funding_level", "g3_atr_expansion"}

    def test_unknown_family_is_refused(self):
        with pytest.raises(ValueError):
            fr.build_contract_doc("not_a_family")

    def test_no_family_specific_branching_in_contract_builder(self):
        """Generalization is real, not a switch statement."""
        src = (ROOT / "research/forward_evidence/forward_readiness.py").read_text("utf-8")
        fn = next(n for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.FunctionDef) and n.name == "build_contract_doc")
        for node in ast.walk(fn):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert node.value not in fr.FAMILIES, "per-family literal in the builder"


# ---------------------------------------------------------------------------
# 2/3. contemporaneous evidence; READY days
# ---------------------------------------------------------------------------

class TestContemporaneousEvidence:
    def test_collected_day_is_ready(self, tmp_path):
        fdb = _forward_db(tmp_path)
        db = _ledger(tmp_path)
        r = fr.evaluate_day(db, "h5_vol_regime", "2026-08-29",
                            "2026-08-30T00:05:00+00:00", "tester", forward_db=fdb)
        assert r.verdict.state == dq_states.READY and r.may_derive

    def test_block_matches_production_shape(self, tmp_path):
        fdb = _forward_db(tmp_path)
        b = fr.build_readiness_block("h5_vol_regime", W_START, W_END, forward_db=fdb)
        assert b["days_total"] == 76
        assert b["days_ready"] == 57
        assert len(b["days_excluded"]) == 19
        assert b["days_non_ready"] == []

    def test_source_is_run_log_not_a_corpus_probe(self, tmp_path):
        fdb = _forward_db(tmp_path)
        b = fr.build_readiness_block("h5_vol_regime", W_START, W_END, forward_db=fdb)
        assert b["source"] == "run_log:status=ok"

    def test_non_ok_status_is_not_evidence(self, tmp_path):
        fdb = _forward_db(tmp_path, skip={"2026-08-20"})
        con = sqlite3.connect(fdb)
        con.execute("INSERT INTO run_log (signal_id,run_date,status) VALUES "
                    "('h5_vol_regime','2026-08-20','error')")
        con.commit(); con.close()
        b = fr.build_readiness_block("h5_vol_regime", W_START, W_END, forward_db=fdb)
        assert "2026-08-20" in b["days_non_ready"]


# ---------------------------------------------------------------------------
# 4/5. INC-0005 excluded, never imputed; producer cannot invent exclusions
# ---------------------------------------------------------------------------

class TestDeclaredGap:
    def test_gap_days_come_from_policy(self):
        days = fr.declared_gap_days()
        assert len(days) == 19
        assert min(days) == GAP_START and max(days) == GAP_END

    def test_gap_days_are_excluded_not_ready(self, tmp_path):
        fdb = _forward_db(tmp_path)
        b = fr.build_readiness_block("h5_vol_regime", W_START, W_END, forward_db=fdb)
        assert set(b["days_excluded"]) == fr.declared_gap_days()
        assert not set(b["days_excluded"]) & set(b["days_non_ready"])
        assert b["days_ready"] + len(b["days_excluded"]) == b["days_total"]

    def test_exclusion_basis_names_the_declaration(self, tmp_path):
        fdb = _forward_db(tmp_path)
        b = fr.build_readiness_block("h5_vol_regime", W_START, W_END, forward_db=fdb)
        assert b["exclusion_basis"] == "run_date_policy.yaml#immutable_gaps"

    def test_no_basis_when_nothing_excluded(self, tmp_path):
        fdb = _forward_db(tmp_path, start="2026-08-10", end="2026-08-29")
        b = fr.build_readiness_block("h5_vol_regime", "2026-08-10", "2026-08-29",
                                     forward_db=fdb)
        assert b["days_excluded"] == [] and b["exclusion_basis"] is None

    def test_gap_day_is_never_evaluated(self, tmp_path):
        fdb = _forward_db(tmp_path)
        db = _ledger(tmp_path)
        with pytest.raises(fr.UndeclaredGapError):
            fr.evaluate_day(db, "h5_vol_regime", "2026-07-20",
                            "2026-08-30T00:05:00+00:00", "tester", forward_db=fdb)

    def test_gap_day_receives_no_receipt(self, tmp_path):
        fdb = _forward_db(tmp_path)
        db = _ledger(tmp_path)
        with pytest.raises(fr.UndeclaredGapError):
            fr.evaluate_day(db, "h5_vol_regime", "2026-07-20",
                            "2026-08-30T00:05:00+00:00", "tester", forward_db=fdb)
        assert dqr.get_readiness(db, "h5_vol_regime", "2026-07-20") is None

    def test_producer_cannot_invent_an_exclusion(self):
        forged = {"days_total": 2, "days_ready": 1, "days_non_ready": [],
                  "days_excluded": ["2026-08-20"],
                  "exclusion_basis": "run_date_policy.yaml#immutable_gaps"}
        with pytest.raises(fr.UndeclaredGapError):
            fr.assert_exclusions_declared(forged)

    def test_real_block_passes_the_invariant(self, tmp_path):
        fdb = _forward_db(tmp_path)
        fr.assert_exclusions_declared(
            fr.build_readiness_block("h5_vol_regime", W_START, W_END, forward_db=fdb))


# ---------------------------------------------------------------------------
# 6. undeclared missing day fails closed
# ---------------------------------------------------------------------------

class TestFailClosed:
    def test_undeclared_missing_day_is_non_ready(self, tmp_path):
        fdb = _forward_db(tmp_path, skip={"2026-08-20"})
        b = fr.build_readiness_block("h5_vol_regime", W_START, W_END, forward_db=fdb)
        assert b["days_non_ready"] == ["2026-08-20"]

    def test_undeclared_missing_day_blocks_the_gate(self, tmp_path):
        fdb = _forward_db(tmp_path, skip={"2026-08-20"})
        block = fr.build_readiness_block("h5_vol_regime", W_START, W_END, forward_db=fdb)
        run = {"family_id": "h5_vol_regime", "window_start": W_START,
               "window_end": W_END, "run_ts": "2026-08-30T01:00:00+00:00",
               "metrics": {"readiness": block}}
        assert readiness_gate.DEFAULT_PROVIDER.assess(tmp_path / "x.db", run).ok is False

    def test_long_past_missing_day_is_backfill_required(self, tmp_path):
        fdb = _forward_db(tmp_path)
        db = _ledger(tmp_path)
        r = fr.evaluate_day(db, "h5_vol_regime", "2026-05-01",
                            "2026-08-30T00:05:00+00:00", "tester", forward_db=fdb)
        assert r.verdict.state == dq_states.BACKFILL_REQUIRED
        assert r.may_derive is False


# ---------------------------------------------------------------------------
# 7/8. end-to-end consumption: producer -> receipt -> projection -> provider
# ---------------------------------------------------------------------------

class TestEndToEnd:
    def test_get_readiness_finds_it_by_family_id(self, tmp_path):
        """The FB-12b defect: the verdict was written under contract_id."""
        fdb = _forward_db(tmp_path)
        db = _ledger(tmp_path)
        fr.evaluate_day(db, "h5_vol_regime", "2026-08-29",
                        "2026-08-30T00:05:00+00:00", "tester", forward_db=fdb)
        row = dqr.get_readiness(db, "h5_vol_regime", "2026-08-29",
                                as_of="2026-08-30T12:00:00+00:00")
        assert row is not None
        assert row["feature_id"] == "h5_vol_regime"
        assert row["state"] == dq_states.READY

    def test_provider_consumes_receipts_for_a_gap_free_window(self, tmp_path):
        fdb = _forward_db(tmp_path, start="2026-08-10", end="2026-08-12")
        db = _ledger(tmp_path)
        for day in ("2026-08-10", "2026-08-11", "2026-08-12"):
            fr.evaluate_day(db, "h5_vol_regime", day, "2026-08-13T00:05:00+00:00",
                            "tester", forward_db=fdb)
        run = {"family_id": "h5_vol_regime", "window_start": "2026-08-10",
               "window_end": "2026-08-12", "run_ts": "2026-08-14T00:00:00+00:00",
               "metrics": {}}
        a = readiness_gate.DEFAULT_PROVIDER.assess(db, run)
        assert a.ok is True and a.source == "readiness_receipts"
        assert a.days_ready == 3

    def test_provider_consumes_the_block_for_a_gap_spanning_window(self, tmp_path):
        fdb = _forward_db(tmp_path)
        block = fr.build_readiness_block("h5_vol_regime", W_START, W_END, forward_db=fdb)
        run = {"family_id": "h5_vol_regime", "window_start": W_START,
               "window_end": W_END, "run_ts": "2026-08-30T01:00:00+00:00",
               "metrics": {"readiness": block}}
        a = readiness_gate.DEFAULT_PROVIDER.assess(tmp_path / "none.db", run)
        assert a.ok is True and a.source == "run_block"

    def test_tg_rdy_requires_readiness_only_for_validated(self):
        assert readiness_gate.STATES_REQUIRING_READINESS == frozenset({"validated"})
        assert readiness_gate.requires_readiness("validated") is True
        assert readiness_gate.requires_readiness("candidate") is False

    def test_late_receipt_is_invisible_to_an_earlier_run(self, tmp_path):
        """No accumulating readiness after the fact."""
        fdb = _forward_db(tmp_path, start="2026-08-10", end="2026-08-10")
        db = _ledger(tmp_path)
        fr.evaluate_day(db, "h5_vol_regime", "2026-08-10",
                        "2026-08-20T00:00:00+00:00", "tester", forward_db=fdb)
        assert dqr.get_readiness(db, "h5_vol_regime", "2026-08-10",
                                 as_of="2026-08-11T00:00:00+00:00") is None


# ---------------------------------------------------------------------------
# 9/10/11. no side effects on protected surfaces
# ---------------------------------------------------------------------------

class TestNoSideEffects:
    def test_protected_files_unmodified(self):
        proc = subprocess.run(["git", "diff", "HEAD", "--name-only"],
                              cwd=ROOT, capture_output=True, text=True)
        assert proc.returncode == 0
        touched = set(proc.stdout.split())
        for frozen in (
            "research/forward_evidence/decision_function.py",
            "research/forward_evidence/run_daily.py",
            "research/forward_evidence/runner_base.py",
            "research/forward_evidence/daily_job.py",
            "research/forward_evidence/run_date_policy.py",
            "research/forward_evidence/run_date_policy.yaml",
            "research/evidence_library/readiness_gate.py",
            "research/evidence_library/transition_governance.py",
            "research/evidence_library/registry.yaml",
            "research/evidence_library/thresholds.yaml",
            "research/dq/states.py", "research/dq/policy.py",
            "research/dq/engine.py", "research/dq/checks.py",
            "research/dq/readiness.py", "research/dq/contract.py",
        ):
            assert frozen not in touched, f"{frozen} must not be modified"

    def test_h6_decide_unchanged(self):
        from forward_evidence.decision_function import h6_decide
        labels = ("low", "medium", "high", "unknown", "bear", "sideways", "bull")
        for v in labels:
            for f in labels:
                for m in labels:
                    assert h6_decide(v, f, m) == int(
                        v == "medium" and f == "low" and m not in ("bear", "unknown"))

    def test_module_never_imports_a_decision_function(self):
        src = (ROOT / "research/forward_evidence/forward_readiness.py").read_text("utf-8")
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "decision_function" not in node.module

    def test_no_collector_or_scheduler_imports_this_module(self):
        """Collectors and the scheduler must never reach this module.

        `forward_snapshot.py` was on this list until FB-12g and is now
        deliberately off it: it is an operator-invoked producer, not a
        collector, and embedding the block at run creation is the approved
        design. Every scheduled path stays guarded.
        """
        for name in ("run_daily.py", "daily_job.py", "runner_base.py",
                     "ledger_sync.py"):
            path = ROOT / "research/forward_evidence" / name
            if path.exists():
                assert "forward_readiness" not in path.read_text("utf-8")
        for unit in (ROOT / "deploy").glob("*.service"):
            assert "forward_readiness" not in unit.read_text("utf-8")

    def test_creates_no_evidence_ledger_tables(self, tmp_path):
        fdb = _forward_db(tmp_path, start="2026-08-10", end="2026-08-10")
        db = _ledger(tmp_path)
        fr.evaluate_day(db, "h5_vol_regime", "2026-08-10",
                        "2026-08-11T00:05:00+00:00", "tester", forward_db=fdb)
        con = sqlite3.connect(db)
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        con.close()
        for forbidden in ("runs", "state_transitions", "gate_results", "pairwise_r"):
            assert forbidden not in tables

    def test_exposes_no_promotion_surface(self):
        for banned in ("promote", "admit", "reject", "transition", "pin"):
            assert not any(n.startswith(banned) for n in dir(fr))

    def test_forward_db_is_opened_read_only(self, tmp_path):
        fdb = _forward_db(tmp_path, start="2026-08-10", end="2026-08-10")
        before = fdb.read_bytes()
        fr.build_readiness_block("h5_vol_regime", "2026-08-10", "2026-08-10",
                                 forward_db=fdb)
        assert fdb.read_bytes() == before, "the forward corpus must never be written"


# ---------------------------------------------------------------------------
# 12. sabotage the fail-open guards
# ---------------------------------------------------------------------------

class TestSabotage:
    def test_treating_gap_days_as_ready_would_change_the_count(self, tmp_path):
        """If the gap were imputed rather than excluded, days_ready would jump
        from 57 to 76 and the distinction would vanish."""
        fdb = _forward_db(tmp_path)
        b = fr.build_readiness_block("h5_vol_regime", W_START, W_END, forward_db=fdb)
        assert b["days_ready"] == 57
        assert b["days_ready"] != b["days_total"], "gap days must never count as ready"

    def test_widening_the_declared_gap_is_what_admits_a_day(self, tmp_path, monkeypatch):
        """A day is excluded only because policy declares it. Widen the
        declaration and the same day flips from non-ready to excluded --
        proving the policy file, not the producer, is load-bearing."""
        fdb = _forward_db(tmp_path, skip={"2026-08-20"})
        strict = fr.build_readiness_block("h5_vol_regime", W_START, W_END, forward_db=fdb)
        assert strict["days_non_ready"] == ["2026-08-20"]

        widened = fr.declared_gap_days() | {"2026-08-20"}
        monkeypatch.setattr(fr, "declared_gap_days", lambda *a, **k: widened)
        loose = fr.build_readiness_block("h5_vol_regime", W_START, W_END, forward_db=fdb)
        assert loose["days_non_ready"] == []
        assert "2026-08-20" in loose["days_excluded"]

    def test_invariant_catches_the_widened_exclusion(self, tmp_path, monkeypatch):
        """And the invariant, reading the REAL policy, rejects that payload."""
        fdb = _forward_db(tmp_path, skip={"2026-08-20"})
        widened = fr.declared_gap_days() | {"2026-08-20"}
        real = fr.declared_gap_days
        monkeypatch.setattr(fr, "declared_gap_days", lambda *a, **k: widened)
        loose = fr.build_readiness_block("h5_vol_regime", W_START, W_END, forward_db=fdb)
        monkeypatch.setattr(fr, "declared_gap_days", real)
        with pytest.raises(fr.UndeclaredGapError):
            fr.assert_exclusions_declared(loose)

    def test_policy_file_is_byte_identical_after_all_of_this(self):
        proc = subprocess.run(
            ["git", "diff", "HEAD", "--", "research/forward_evidence/run_date_policy.yaml"],
            cwd=ROOT, capture_output=True, text=True)
        assert proc.stdout == "", "run_date_policy.yaml must be untouched"
