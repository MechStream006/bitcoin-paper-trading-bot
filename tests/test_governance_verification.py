"""
Operational Governance — the read-only verification engine (PR-9).

Covers: full-sweep verification, receipt-chain verification, the composition of
every subsystem verifier, fail-closed behaviour, point-in-time correctness,
cross-subsystem consistency (GV1-GV3), state-machine evolution diagnostics (the
deferred PR-8 enhancement), and that the engine writes NOTHING.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research" / "evidence_library"))
sys.path.insert(0, str(ROOT / "research" / "dq"))

import evidence_governance as eg
import governance_verification as gv
import lifecycle
import receipt_ledger as rl
import record
import transition_governance as tg

THRESHOLDS = {
    "candidate": {
        "hard_dq": {"knife_edge_sensitivity": 0.30},
        "edge": {"min_sharpe": 0.30, "min_wf_agreement": 0.60, "min_sensitivity": 0.60,
                 "min_loo_consistency": 0.60, "min_breakeven_bps": 10},
    },
    "validated": {
        "edge": {"requires_non_offline_provenance": True, "min_dsr_prob": 0.50, "max_bh_p": 0.10},
    },
}
PASSING = {"sharpe": 0.9, "oos_sharpe": 0.4, "sens_pass_frac": 1.0, "wf_pass_frac": 0.8,
           "loo_sign_consistency": 1.0, "breakeven_cost_bps": 100, "struct_sign_ok": True,
           "n_trades": 50}
EDGE = {"role": ["edge"]}


@pytest.fixture
def db(tmp_path) -> Path:
    p = tmp_path / "ledger.db"
    eg.init_db(p)
    return p


def add_run(db, family_id="fam_a"):
    return record.append_run(db, family_id, dict(PASSING), "offline-corpus", "discovery")


def governed_candidate(db, family_id="fam_a"):
    run_id = add_run(db, family_id)
    # C1-C9 (lifecycle.validate_registry) requires a candidate's pinned run to
    # carry gate_results in the ledger — the real seeding flow writes them, so
    # the helper does too, otherwise the registry check legitimately fails.
    record.append_gate_result(db, run_id, "B_SHARPE", "edge", True, 0.9, 0.30)
    eg.admit_evidence(db, run_id, EDGE, THRESHOLDS, reason="passes gate")
    tg.transition_with_receipt(db, family_id, "candidate", justifying_run_id=run_id,
                               actor="rohith", reason="passes the edge gate",
                               family_meta=EDGE, thresholds=THRESHOLDS)
    return run_id


def registry_for(db):
    return {fid: {"state": st, "role": ["edge"], "pinned_run": pinned}
            for fid, (st, pinned, _ts) in record.derive_all_states_at(db).items()}


# ---------------------------------------------------------------------------
# Full sweep
# ---------------------------------------------------------------------------

class TestFullSweep:
    def test_clean_ledger_passes(self, db):
        governed_candidate(db, "fam_a")
        governed_candidate(db, "fam_b")
        rep = gv.verify_governance(db, registry=registry_for(db), thresholds=THRESHOLDS)
        assert rep.ok
        assert rep.errors == []
        assert "receipt_chain" in rep.subsystems_run
        assert "transition_governance" in rep.subsystems_run
        assert "cross_subsystem" in rep.subsystems_run

    def test_report_is_serialisable_and_summarised(self, db):
        governed_candidate(db)
        rep = gv.verify_governance(db, registry=registry_for(db), thresholds=THRESHOLDS)
        d = rep.to_dict()
        assert d["ok"] is True
        assert "counts" in d and d["counts"]["receipts"] > 0
        assert "governance OK" in rep.summary()

    def test_composes_every_subsystem(self, db):
        governed_candidate(db)
        rep = gv.verify_governance(db, registry=registry_for(db), thresholds=THRESHOLDS)
        # C1-C9, EG, TG all ran (all clean).
        assert {"receipt_chain", "registry_c1_c9", "evidence_governance",
                "transition_governance", "cross_subsystem"} <= set(rep.subsystems_run)

    def test_missing_registry_skips_dependent_checks_without_crashing(self, db):
        governed_candidate(db)
        rep = gv.verify_governance(db)   # no registry/thresholds
        assert rep.ok
        assert any(f.code == "GV-SKIP" and f.subsystem == "registry_c1_c9" for f in rep.findings)

    def test_no_readiness_substrate_is_skipped_not_errored(self, db):
        """The real ledger has no data_readiness table. That is not a failure,
        and PR-9 must not create the table to 'check' it."""
        governed_candidate(db)
        rep = gv.verify_governance(db, registry=registry_for(db), thresholds=THRESHOLDS)
        assert rep.ok
        assert any(f.subsystem == "data_readiness" and f.severity == "info" for f in rep.findings)


# ---------------------------------------------------------------------------
# Subsystem failures surface, and preserve native codes
# ---------------------------------------------------------------------------

class TestSubsystemFindings:
    def test_a_tg_violation_surfaces_with_its_native_code(self, db):
        run_id = add_run(db)
        eg.admit_evidence(db, run_id, EDGE, THRESHOLDS, reason="ok")
        # raw primitive -> ungoverned transition -> TG1
        record.append_transition(db, "fam_a", "discovery", "candidate",
                                 reason="raw", justifying_run_id=run_id)
        rep = gv.verify_governance(db, registry=registry_for(db), thresholds=THRESHOLDS)
        assert not rep.ok
        assert any(f.code == "TG1" and f.subsystem == "transition_governance" for f in rep.errors)

    def test_a_broken_chain_surfaces(self, db):
        governed_candidate(db)
        con = sqlite3.connect(db)
        try:
            con.execute("DROP TRIGGER trg_receipts_no_update")
            # tamper a body so it no longer hashes
            rid = rl.get_all_receipts(db)[0]["receipt_id"]
            con.execute("UPDATE governance_receipts SET body_json='{\"x\":1}' WHERE receipt_id=?", (rid,))
            con.commit()
        finally:
            con.close()
        rep = gv.verify_governance(db, registry=registry_for(db), thresholds=THRESHOLDS)
        assert not rep.ok
        assert any(f.subsystem == "receipt_chain" for f in rep.errors)

    def test_fail_closed_a_raising_verifier_is_an_error_not_a_pass(self, db, monkeypatch):
        """A verification that cannot run is a FAILURE, never a silent pass."""
        governed_candidate(db)

        def boom(*a, **kw):
            raise RuntimeError("verifier exploded")

        monkeypatch.setattr(tg, "validate_transition_governance", boom)
        rep = gv.verify_governance(db, registry=registry_for(db), thresholds=THRESHOLDS)
        assert not rep.ok
        assert any(f.code == "GV-ERR" and "transition_governance" in f.subsystem
                   for f in rep.errors)
        assert "transition_governance" not in rep.subsystems_run


# ---------------------------------------------------------------------------
# Cross-subsystem consistency (GV1-GV3)
# ---------------------------------------------------------------------------

class TestCrossSubsystem:
    def test_GV3_detects_diverging_state_derivations(self, db):
        governed_candidate(db)
        # a transition written WITHOUT a receipt: ledger moves, receipt chain does not
        run2 = record.append_run(db, "fam_a", dict(PASSING), "live-forward", "forward",
                                 governance={"dsr_prob": 0.9, "bh_p": 0.01})
        eg.admit_evidence(db, run2, EDGE, THRESHOLDS, reason="ok")
        record.append_transition(db, "fam_a", "candidate", "validated",
                                 reason="raw", justifying_run_id=run2)
        rep = gv.verify_governance(db, thresholds=THRESHOLDS)
        assert not rep.ok
        assert any(f.code == "GV3" for f in rep.errors)

    def test_GV1_replays_every_transition_receipt(self, db):
        governed_candidate(db, "fam_a")
        governed_candidate(db, "fam_b")
        rep = gv.verify_governance(db, registry=registry_for(db), thresholds=THRESHOLDS)
        # clean ledger -> no GV1 errors
        assert not any(f.code == "GV1" for f in rep.errors)

    def test_GV1_catches_a_forged_transition_receipt(self, db):
        res = None
        run_id = add_run(db)
        eg.admit_evidence(db, run_id, EDGE, THRESHOLDS, reason="ok")
        res = tg.transition_with_receipt(db, "fam_a", "candidate", justifying_run_id=run_id,
                                         actor="rohith", reason="r", family_meta=EDGE,
                                         thresholds=THRESHOLDS)
        con = sqlite3.connect(db)
        try:
            con.execute("DROP TRIGGER trg_receipts_no_update")
            import json
            body = res["receipt"]["body"]
            body["to_state"] = "validated"  # forge
            con.execute("UPDATE governance_receipts SET body_json=? WHERE receipt_id=?",
                        (json.dumps(body, sort_keys=True), res["receipt"]["receipt_id"]))
            con.commit()
        finally:
            con.close()
        rep = gv.verify_governance(db, registry=registry_for(db), thresholds=THRESHOLDS)
        assert not rep.ok
        assert any(f.code in ("GV1",) or f.subsystem in ("receipt_chain", "cross_subsystem")
                   for f in rep.errors)


# ---------------------------------------------------------------------------
# Point-in-time
# ---------------------------------------------------------------------------

class TestPointInTime:
    def test_as_of_skips_now_only_checks(self, db):
        governed_candidate(db)
        rep = gv.verify_governance(db, registry=registry_for(db), thresholds=THRESHOLDS,
                                   as_of="2026-07-01T00:00:00+00:00")
        assert rep.as_of == "2026-07-01T00:00:00+00:00"
        # receipt-consuming checks are now-only -> skipped, surfaced as info
        assert any(f.code == "GV-SKIP" and f.subsystem == "receipt_consumers"
                   for f in rep.findings)
        assert "transition_governance" not in rep.subsystems_run

    def test_as_of_still_runs_registry_invariants(self, db):
        governed_candidate(db)
        rep = gv.verify_governance(db, registry=registry_for(db), thresholds=THRESHOLDS,
                                   as_of="2027-01-01T00:00:00+00:00")
        assert "registry_c1_c9" in rep.subsystems_run


# ---------------------------------------------------------------------------
# Chain-only entry point
# ---------------------------------------------------------------------------

class TestChainOnly:
    def test_chain_verification(self, db):
        governed_candidate(db)
        rep = gv.verify_receipt_chain(db)
        assert rep.ok
        assert rep.counts["receipts"] > 0

    def test_chain_verification_detects_tampering(self, db):
        governed_candidate(db)
        con = sqlite3.connect(db)
        try:
            con.execute("DROP TRIGGER trg_receipts_no_update")
            rid = rl.get_all_receipts(db)[0]["receipt_id"]
            con.execute("UPDATE governance_receipts SET body_json='{}' WHERE receipt_id=?", (rid,))
            con.commit()
        finally:
            con.close()
        assert not gv.verify_receipt_chain(db).ok


# ---------------------------------------------------------------------------
# State machine evolution diagnostics (deferred PR-8 enhancement)
# ---------------------------------------------------------------------------

class TestStateMachineEvolution:
    def test_v2_receipt_reports_unchanged_when_machine_is_stable(self, db):
        governed_candidate(db)
        diags = gv.diagnose_state_machine_evolution(db)
        assert len(diags) == 1
        d = diags[0]
        assert d.engine_version == "transition_governance.v2"
        assert d.separable is True
        assert d.machine_changed is False

    def test_v2_receipt_detects_a_changed_state_machine(self, db):
        governed_candidate(db)
        original = dict(lifecycle.ALLOWED_TRANSITIONS)
        try:
            lifecycle.ALLOWED_TRANSITIONS["discovery"] = frozenset(
                original["discovery"] | {"production_ready"})
            diags = gv.diagnose_state_machine_evolution(db)
            d = diags[0]
            assert d.separable is True
            assert d.machine_changed is True
            assert "CHANGED" in d.detail
        finally:
            lifecycle.ALLOWED_TRANSITIONS.clear()
            lifecycle.ALLOWED_TRANSITIONS.update(original)

    def test_a_changed_machine_is_a_warning_not_an_error(self, db):
        governed_candidate(db)
        original = dict(lifecycle.ALLOWED_TRANSITIONS)
        try:
            lifecycle.ALLOWED_TRANSITIONS["discovery"] = frozenset(
                original["discovery"] | {"production_ready"})
            findings = gv.state_machine_evolution_findings(db)
            assert findings
            assert all(f.severity == "warning" for f in findings)
            # and the receipt still REPLAYS — a machine change does not invalidate it
            rid = [r for r in rl.get_all_receipts(db)
                   if r["decision_type"] in tg.TRANSITION_DECISION_TYPES][0]["receipt_id"]
            assert tg.replay_transition_receipt(db, rid).ok
        finally:
            lifecycle.ALLOWED_TRANSITIONS.clear()
            lifecycle.ALLOWED_TRANSITIONS.update(original)

    def test_v1_bootstrap_receipt_is_non_separable_honest_non_answer(self, db):
        """A pre-v2 receipt's combined hash cannot answer 'did the machine
        change?'. It is reported as non-separable, not mis-compared."""
        run_id = add_run(db)
        eg.admit_evidence(db, run_id, EDGE, THRESHOLDS, reason="ok")
        record.append_transition(db, "fam_a", "discovery", "candidate",
                                 reason="pre-PR-8", justifying_run_id=run_id)
        t = record.get_all_transitions(db)[0]
        tg.bootstrap_transition_receipt(db, t, "predates the hash split")
        diags = gv.diagnose_state_machine_evolution(db)
        d = next(x for x in diags if x.engine_version is None)
        assert d.separable is False
        assert d.machine_changed is None
        assert "non-separable" in d.detail

    def test_the_split_hashes_are_actually_distinct(self, db):
        """PR-9's core fix: rule_hash (state machine) and thresholds_hash
        (thresholds) are now different digests, where PR-8 made them identical."""
        run_id = add_run(db)
        eg.admit_evidence(db, run_id, EDGE, THRESHOLDS, reason="ok")
        res = tg.transition_with_receipt(db, "fam_a", "candidate", justifying_run_id=run_id,
                                         actor="rohith", reason="r", family_meta=EDGE,
                                         thresholds=THRESHOLDS)
        rule = res["receipt"]["body"]["rule"]
        assert rule["rule_hash"] == tg.state_machine_hash()
        assert rule["thresholds_hash"] == tg.thresholds_hash(THRESHOLDS)
        assert rule["rule_hash"] != rule["thresholds_hash"]
        # and the combined policy hash is still recorded as authority.policy_hash
        assert res["receipt"]["body"]["authority"]["policy_hash"] == tg._policy_hash(THRESHOLDS)


# ---------------------------------------------------------------------------
# Read-only guarantee
# ---------------------------------------------------------------------------

class TestReadOnly:
    def test_verification_writes_nothing(self, db):
        governed_candidate(db)
        before = _snapshot(db)
        gv.verify_governance(db, registry=registry_for(db), thresholds=THRESHOLDS)
        gv.verify_receipt_chain(db)
        gv.diagnose_state_machine_evolution(db)
        gv.state_machine_evolution_findings(db)
        assert _snapshot(db) == before

    def test_verification_does_not_create_a_data_readiness_table(self, db):
        """PR-9 is read-only. It must not stand up PR-7's table to 'check' it."""
        governed_candidate(db)
        gv.verify_governance(db, registry=registry_for(db), thresholds=THRESHOLDS)
        con = sqlite3.connect(db)
        try:
            exists = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='data_readiness'"
            ).fetchone()
        finally:
            con.close()
        assert exists is None

    def test_module_has_no_write_verbs(self):
        """Structural: the verification module must not INSERT/UPDATE/DELETE."""
        src = (ROOT / "research" / "evidence_library" / "governance_verification.py").read_text(
            encoding="utf-8").upper()
        for verb in ("INSERT INTO", "UPDATE ", "DELETE FROM", "DROP ", "ALTER "):
            assert verb not in src, f"verification module contains a write verb: {verb!r}"


def _snapshot(db) -> tuple:
    con = sqlite3.connect(db)
    try:
        receipts = con.execute("SELECT receipt_id, body_json FROM governance_receipts ORDER BY seq").fetchall()
        trans = con.execute("SELECT * FROM state_transitions ORDER BY id").fetchall()
        runs = con.execute("SELECT run_id FROM runs ORDER BY run_id").fetchall()
    finally:
        con.close()
    return (tuple(receipts), tuple(trans), tuple(runs))


# ---------------------------------------------------------------------------
# The real ledger
# ---------------------------------------------------------------------------

REAL_LEDGER = ROOT / "research" / "evidence_library" / "ledger.db"
REAL_REGISTRY = ROOT / "research" / "evidence_library" / "registry.yaml"
REAL_THRESHOLDS = ROOT / "research" / "evidence_library" / "thresholds.yaml"


@pytest.mark.skipif(not REAL_LEDGER.exists(), reason="real ledger not present")
class TestRealLedger:
    def test_real_ledger_passes_full_verification(self):
        import yaml
        reg = yaml.safe_load(REAL_REGISTRY.read_text(encoding="utf-8"))
        thr = yaml.safe_load(REAL_THRESHOLDS.read_text(encoding="utf-8"))
        rep = gv.verify_governance(REAL_LEDGER, registry=reg, thresholds=thr)
        assert rep.ok, f"real ledger failed governance verification: {[f.message for f in rep.errors]}"

    def test_real_ledger_chain(self):
        assert gv.verify_receipt_chain(REAL_LEDGER).ok

    def test_real_ledger_bootstrap_receipts_are_non_separable(self):
        """All 13 real receipts predate the v2 split; each honestly reports it
        cannot answer whether the state machine changed."""
        diags = gv.diagnose_state_machine_evolution(REAL_LEDGER)
        assert diags
        assert all(not d.separable for d in diags)
