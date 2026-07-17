"""
Tests for Evidence Library Governance Integration (PR-6 of the Governance
Architecture): evidence_governance.py, wiring PR-1's receipt ledger and
PR-5's ledger-derived lifecycle state into the pre-existing Evidence
Library for exactly `evidence.admit` / `evidence.reject`.

Covers: evidence admission, evidence rejection, evidence pinning, evidence
re-pinning, replay correctness (the first real derived-receipt replay in
this codebase), point-in-time reconstruction, governance validation,
duplicate prevention, and invalid pin detection.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
_EVLIB = ROOT / "research" / "evidence_library"
sys.path.insert(0, str(_EVLIB))

import evidence_governance as eg
import migrate_bootstrap_evidence_admission as mbea
import receipt_ledger as rl
import record
import report


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

EDGE_PASS = {
    "sharpe": 0.90, "oos_sharpe": -0.15, "sens_pass_frac": 1.0,
    "wf_pass_frac": 0.67, "loo_sign_consistency": 1.0,
    "breakeven_cost_bps": 100, "struct_sign_ok": True, "n_trades": 22,
}
EDGE_FAIL_KNIFE_EDGE = {
    "sharpe": 0.66, "oos_sharpe": 0.53, "sens_pass_frac": 0.0,
    "wf_pass_frac": 0.67, "loo_sign_consistency": 1.0,
    "breakeven_cost_bps": 50, "struct_sign_ok": True, "n_trades": 148,
}
EDGE_ROLE = {"role": ["edge"]}


@pytest.fixture
def thresholds() -> dict:
    return yaml.safe_load((_EVLIB / "thresholds.yaml").read_text(encoding="utf-8"))


@pytest.fixture
def db_path(tmp_path) -> Path:
    p = tmp_path / "ledger_test.db"
    eg.init_db(p)
    return p


def _passing_run(db, family_id="fam_a", run_kind="discovery"):
    return record.append_run(db, family_id, dict(EDGE_PASS), "offline-corpus", run_kind, n_trials=34)


def _failing_run(db, family_id="fam_dq", run_kind="discovery"):
    return record.append_run(db, family_id, dict(EDGE_FAIL_KNIFE_EDGE), "offline-corpus", run_kind, n_trials=34)


# ---------------------------------------------------------------------------
# init_db
# ---------------------------------------------------------------------------

class TestInitDb:
    def test_creates_both_schemas(self, db_path):
        con = sqlite3.connect(db_path)
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        con.close()
        assert "runs" in tables
        assert "governance_receipts" in tables

    def test_idempotent(self, db_path):
        eg.init_db(db_path)  # must not raise


# ---------------------------------------------------------------------------
# Evidence admission
# ---------------------------------------------------------------------------

class TestEvidenceAdmission:
    def test_admit_passing_run(self, db_path, thresholds):
        run_id = _passing_run(db_path)
        receipt = eg.admit_evidence(db_path, run_id, EDGE_ROLE, thresholds, actor="rohith", reason="admit")
        assert receipt["decision_type"] == "evidence.admit"
        assert receipt["decision_class"] == "derived"
        assert receipt["body"]["verdict"]["result"] == "admitted"
        assert receipt["body"]["subject"]["id"] == run_id
        assert receipt["body"]["subject"]["kind"] == "evidence_run"

    def test_admit_records_gate_evaluation(self, db_path, thresholds):
        run_id = _passing_run(db_path)
        receipt = eg.admit_evidence(db_path, run_id, EDGE_ROLE, thresholds, actor="rohith", reason="admit")
        checks = {e["check_id"] for e in receipt["body"]["evaluation"]}
        assert "E3_KNIFE_EDGE" in checks
        assert "B_SHARPE" in checks

    def test_admit_failing_run_refused(self, db_path, thresholds):
        run_id = _failing_run(db_path)
        with pytest.raises(eg.EvidenceGovernanceError, match="does not pass"):
            eg.admit_evidence(db_path, run_id, EDGE_ROLE, thresholds)

    def test_admit_unknown_run_raises(self, db_path, thresholds):
        with pytest.raises(eg.EvidenceNotFoundError):
            eg.admit_evidence(db_path, "f" * 36, EDGE_ROLE, thresholds)

    def test_admit_actor_type_defaults_to_service(self, db_path, thresholds):
        run_id = _passing_run(db_path)
        receipt = eg.admit_evidence(db_path, run_id, EDGE_ROLE, thresholds)
        assert receipt["body"]["actor"]["type"] == "service"

    def test_admit_policy_hash_reflects_thresholds(self, db_path, thresholds):
        run_id = _passing_run(db_path)
        receipt = eg.admit_evidence(db_path, run_id, EDGE_ROLE, thresholds, actor="rohith", reason="x")
        assert len(receipt["body"]["rule"]["rule_hash"]) == 64
        int(receipt["body"]["rule"]["rule_hash"], 16)


# ---------------------------------------------------------------------------
# Evidence rejection
# ---------------------------------------------------------------------------

class TestEvidenceRejection:
    def test_reject_failing_run(self, db_path, thresholds):
        run_id = _failing_run(db_path)
        receipt = eg.reject_evidence(db_path, run_id, EDGE_ROLE, thresholds, actor="rohith", reason="DQ")
        assert receipt["decision_type"] == "evidence.reject"
        assert receipt["body"]["verdict"]["result"] == "rejected"

    def test_reject_records_the_failing_check(self, db_path, thresholds):
        run_id = _failing_run(db_path)
        receipt = eg.reject_evidence(db_path, run_id, EDGE_ROLE, thresholds)
        checks = {e["check_id"]: e["passed"] for e in receipt["body"]["evaluation"]}
        assert checks["E3_KNIFE_EDGE"] is False

    def test_reject_passing_run_refused(self, db_path, thresholds):
        run_id = _passing_run(db_path)
        with pytest.raises(eg.EvidenceGovernanceError, match="passes"):
            eg.reject_evidence(db_path, run_id, EDGE_ROLE, thresholds)

    def test_reject_unknown_run_raises(self, db_path, thresholds):
        with pytest.raises(eg.EvidenceNotFoundError):
            eg.reject_evidence(db_path, "f" * 36, EDGE_ROLE, thresholds)


# ---------------------------------------------------------------------------
# Duplicate prevention
# ---------------------------------------------------------------------------

class TestDuplicatePrevention:
    def test_double_admit_rejected(self, db_path, thresholds):
        run_id = _passing_run(db_path)
        eg.admit_evidence(db_path, run_id, EDGE_ROLE, thresholds)
        with pytest.raises(eg.DuplicateAdmissionError):
            eg.admit_evidence(db_path, run_id, EDGE_ROLE, thresholds)

    def test_reject_after_admit_rejected(self, db_path, thresholds):
        run_id = _passing_run(db_path)
        eg.admit_evidence(db_path, run_id, EDGE_ROLE, thresholds)
        with pytest.raises(eg.DuplicateAdmissionError):
            eg.reject_evidence(db_path, run_id, EDGE_ROLE, thresholds)

    def test_double_reject_rejected(self, db_path, thresholds):
        run_id = _failing_run(db_path)
        eg.reject_evidence(db_path, run_id, EDGE_ROLE, thresholds)
        with pytest.raises(eg.DuplicateAdmissionError):
            eg.reject_evidence(db_path, run_id, EDGE_ROLE, thresholds)

    def test_duplicate_leaves_only_one_receipt(self, db_path, thresholds):
        run_id = _passing_run(db_path)
        eg.admit_evidence(db_path, run_id, EDGE_ROLE, thresholds)
        with pytest.raises(eg.DuplicateAdmissionError):
            eg.admit_evidence(db_path, run_id, EDGE_ROLE, thresholds)
        rows = rl.get_receipts_by_subject(db_path, "evidence_run", run_id)
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# Evidence pinning
# ---------------------------------------------------------------------------

class TestEvidencePinning:
    def test_pin_after_admission(self, db_path, thresholds):
        run_id = _passing_run(db_path)
        eg.admit_evidence(db_path, run_id, EDGE_ROLE, thresholds)
        result = eg.pin_evidence(db_path, "fam_a", "candidate", run_id, actor="rohith", reason="promote")
        assert result["to_state"] == "candidate"
        assert result["pinned_run_id"] == run_id

    def test_pin_dq_after_rejection(self, db_path, thresholds):
        run_id = _failing_run(db_path)
        eg.reject_evidence(db_path, run_id, EDGE_ROLE, thresholds)
        result = eg.pin_evidence(db_path, "fam_dq", "disqualified", run_id, actor="rohith", reason="DQ")
        assert result["to_state"] == "disqualified"

    def test_pin_reflected_in_derive_state_at(self, db_path, thresholds):
        run_id = _passing_run(db_path)
        eg.admit_evidence(db_path, run_id, EDGE_ROLE, thresholds)
        eg.pin_evidence(db_path, "fam_a", "candidate", run_id, actor="rohith", reason="promote")
        state, pinned, _ts = record.derive_state_at(db_path, "fam_a")
        assert state == "candidate"
        assert pinned == run_id

    def test_pin_same_state_rejected(self, db_path, thresholds):
        run_id = _passing_run(db_path)
        eg.admit_evidence(db_path, run_id, EDGE_ROLE, thresholds)
        eg.pin_evidence(db_path, "fam_a", "candidate", run_id, actor="rohith", reason="promote")
        run_id2 = _passing_run(db_path, run_kind="confirmatory")
        eg.admit_evidence(db_path, run_id2, EDGE_ROLE, thresholds)
        with pytest.raises(eg.InvalidPinError, match="repin_evidence"):
            eg.pin_evidence(db_path, "fam_a", "candidate", run_id2, actor="rohith", reason="again")


# ---------------------------------------------------------------------------
# Invalid pin detection
# ---------------------------------------------------------------------------

class TestInvalidPinDetection:
    def test_pin_without_admission_rejected(self, db_path, thresholds):
        run_id = _passing_run(db_path)
        with pytest.raises(eg.InvalidPinError, match="no admission/rejection receipt"):
            eg.pin_evidence(db_path, "fam_a", "candidate", run_id, actor="rohith", reason="too soon")

    def test_pin_unknown_run_rejected(self, db_path, thresholds):
        with pytest.raises(eg.EvidenceNotFoundError):
            eg.pin_evidence(db_path, "fam_a", "candidate", "f" * 36, actor="rohith", reason="x")

    def test_pin_run_from_different_family_rejected(self, db_path, thresholds):
        run_id = _passing_run(db_path, family_id="other_family")
        eg.admit_evidence(db_path, run_id, EDGE_ROLE, thresholds)
        with pytest.raises(eg.InvalidPinError, match="belongs to family"):
            eg.pin_evidence(db_path, "fam_a", "candidate", run_id, actor="rohith", reason="mismatched")

    def test_pin_wrong_receipt_type_rejected(self, db_path, thresholds):
        run_id = _passing_run(db_path)
        eg.admit_evidence(db_path, run_id, EDGE_ROLE, thresholds)  # admitted, not rejected
        with pytest.raises(eg.InvalidPinError, match="requires 'evidence.reject'"):
            eg.pin_evidence(db_path, "fam_a", "disqualified", run_id, actor="rohith", reason="wrong type")

    def test_repin_with_no_prior_transition_rejected(self, db_path, thresholds):
        run_id = _passing_run(db_path)
        eg.admit_evidence(db_path, run_id, EDGE_ROLE, thresholds)
        with pytest.raises(eg.InvalidPinError, match="no prior transition"):
            eg.repin_evidence(db_path, "fam_a", run_id, actor="rohith", reason="nothing to re-pin")


# ---------------------------------------------------------------------------
# Evidence re-pinning
# ---------------------------------------------------------------------------

class TestEvidenceRepinning:
    def test_repin_to_new_evidence(self, db_path, thresholds):
        run_id = _passing_run(db_path)
        eg.admit_evidence(db_path, run_id, EDGE_ROLE, thresholds)
        eg.pin_evidence(db_path, "fam_a", "candidate", run_id, actor="rohith", reason="promote")

        run_id2 = _passing_run(db_path, run_kind="confirmatory")
        eg.admit_evidence(db_path, run_id2, EDGE_ROLE, thresholds)
        result = eg.repin_evidence(db_path, "fam_a", run_id2, actor="rohith", reason="re-pin to reconfirmed run")

        assert result["from_state"] == "candidate"
        assert result["to_state"] == "candidate"
        assert result["pinned_run_id"] == run_id2

    def test_repin_recorded_in_state_transitions(self, db_path, thresholds):
        run_id = _passing_run(db_path)
        eg.admit_evidence(db_path, run_id, EDGE_ROLE, thresholds)
        eg.pin_evidence(db_path, "fam_a", "candidate", run_id, actor="rohith", reason="promote")
        run_id2 = _passing_run(db_path, run_kind="confirmatory")
        eg.admit_evidence(db_path, run_id2, EDGE_ROLE, thresholds)
        eg.repin_evidence(db_path, "fam_a", run_id2, actor="rohith", reason="re-pin")

        transitions = record.get_all_transitions(db_path, family_id="fam_a")
        assert len(transitions) == 2
        assert transitions[1]["from_state"] == transitions[1]["to_state"] == "candidate"

    def test_repin_without_admission_rejected(self, db_path, thresholds):
        run_id = _passing_run(db_path)
        eg.admit_evidence(db_path, run_id, EDGE_ROLE, thresholds)
        eg.pin_evidence(db_path, "fam_a", "candidate", run_id, actor="rohith", reason="promote")
        run_id2 = _passing_run(db_path, run_kind="confirmatory")  # not admitted
        with pytest.raises(eg.InvalidPinError):
            eg.repin_evidence(db_path, "fam_a", run_id2, actor="rohith", reason="premature repin")


# ---------------------------------------------------------------------------
# Pinned evidence validation (validate_pin)
# ---------------------------------------------------------------------------

class TestValidatePin:
    def test_validate_pin_ok_after_proper_pin(self, db_path, thresholds):
        run_id = _passing_run(db_path)
        eg.admit_evidence(db_path, run_id, EDGE_ROLE, thresholds)
        eg.pin_evidence(db_path, "fam_a", "candidate", run_id, actor="rohith", reason="promote")
        ok, errors = eg.validate_pin(db_path, "fam_a")
        assert ok is True
        assert errors == []

    def test_validate_pin_never_transitioned_is_ok(self, db_path):
        ok, errors = eg.validate_pin(db_path, "never_touched")
        assert ok is True
        assert errors == []

    def test_validate_pin_detects_missing_receipt(self, db_path, thresholds):
        # Bypass eg.pin_evidence's own validation via the raw, unvalidated
        # record.append_transition() -- simulating a pin made outside this
        # module (e.g. a direct script) to prove validate_pin() catches it
        # independently of the write path.
        run_id = _passing_run(db_path)
        record.append_transition(db_path, "fam_a", "discovery", "candidate", reason="raw", justifying_run_id=run_id)
        ok, errors = eg.validate_pin(db_path, "fam_a")
        assert ok is False
        assert "no admission/rejection" in errors[0]


# ---------------------------------------------------------------------------
# Governance-aware evidence queries
# ---------------------------------------------------------------------------

class TestGovernanceAwareQueries:
    def test_get_admitted_runs(self, db_path, thresholds):
        run_id = _passing_run(db_path)
        eg.admit_evidence(db_path, run_id, EDGE_ROLE, thresholds)
        assert eg.get_admitted_runs(db_path) == [run_id]

    def test_get_rejected_runs(self, db_path, thresholds):
        run_id = _failing_run(db_path)
        eg.reject_evidence(db_path, run_id, EDGE_ROLE, thresholds)
        assert eg.get_rejected_runs(db_path) == [run_id]

    def test_get_evidence_lineage_interleaves_chronologically(self, db_path, thresholds):
        run_id = _passing_run(db_path)
        eg.admit_evidence(db_path, run_id, EDGE_ROLE, thresholds)
        eg.pin_evidence(db_path, "fam_a", "candidate", run_id, actor="rohith", reason="promote")
        lineage = eg.get_evidence_lineage(db_path, "fam_a")
        kinds = [e["kind"] for e in lineage]
        assert kinds == ["run", "receipt", "transition"]
        ts_values = [e["ts"] for e in lineage]
        assert ts_values == sorted(ts_values)

    def test_get_evidence_lineage_empty_family(self, db_path):
        assert eg.get_evidence_lineage(db_path, "nonexistent") == []


# ---------------------------------------------------------------------------
# Point-in-time evidence reconstruction
# ---------------------------------------------------------------------------

class TestPointInTimeReconstruction:
    def test_reconstruct_now(self, db_path, thresholds):
        run_id = _passing_run(db_path)
        eg.admit_evidence(db_path, run_id, EDGE_ROLE, thresholds)
        eg.pin_evidence(db_path, "fam_a", "candidate", run_id, actor="rohith", reason="promote")
        recon = eg.reconstruct_evidence_at(db_path, "fam_a")
        assert recon["state"] == "candidate"
        assert recon["pinned_run_id"] == run_id
        assert len(recon["admission_receipts"]) == 1

    def test_reconstruct_as_of_past_date_excludes_later_receipt(self, db_path, thresholds):
        run_id = _passing_run(db_path)
        receipt = eg.admit_evidence(db_path, run_id, EDGE_ROLE, thresholds)
        con = sqlite3.connect(db_path)
        con.execute("DROP TRIGGER trg_receipts_no_update")
        con.execute(
            "UPDATE governance_receipts SET decided_at = '2026-06-01T00:00:00+00:00', "
            "as_of = '2026-06-01T00:00:00+00:00' WHERE receipt_id = ?",
            (receipt["receipt_id"],),
        )
        con.execute(
            "UPDATE runs SET run_ts = '2026-06-01T00:00:00+00:00' WHERE run_id = ?", (run_id,)
        )
        con.commit()
        con.close()
        record.append_transition(
            db_path, "fam_a", "discovery", "candidate", reason="promote",
            justifying_run_id=run_id, ts="2026-06-05T00:00:00+00:00",
        )

        past = eg.reconstruct_evidence_at(db_path, "fam_a", as_of="2026-05-01T00:00:00+00:00")
        assert past["state"] is None
        assert past["admission_receipts"] == []

        present = eg.reconstruct_evidence_at(db_path, "fam_a", as_of="2026-07-01T00:00:00+00:00")
        assert present["state"] == "candidate"
        assert len(present["admission_receipts"]) == 1


# ---------------------------------------------------------------------------
# Replay correctness (item 8) -- the first real derived-receipt replay
# ---------------------------------------------------------------------------

class TestReplayCorrectness:
    def test_verify_admission_receipt_reproduces_bit_for_bit(self, db_path, thresholds):
        run_id = _passing_run(db_path)
        eg.admit_evidence(db_path, run_id, EDGE_ROLE, thresholds)
        verification = eg.verify_admission_receipt(db_path, run_id, EDGE_ROLE, thresholds)
        assert verification.hash_ok is True
        assert verification.replay.ok is True
        assert verification.replay.mode == "verified"

    def test_verify_rejection_receipt_reproduces_bit_for_bit(self, db_path, thresholds):
        run_id = _failing_run(db_path)
        eg.reject_evidence(db_path, run_id, EDGE_ROLE, thresholds)
        verification = eg.verify_admission_receipt(db_path, run_id, EDGE_ROLE, thresholds)
        assert verification.replay.ok is True
        assert verification.replay.mode == "verified"

    def test_replay_diverges_if_thresholds_change(self, db_path, thresholds):
        run_id = _passing_run(db_path)
        eg.admit_evidence(db_path, run_id, EDGE_ROLE, thresholds)
        mutated = yaml.safe_load(yaml.safe_dump(thresholds))
        mutated["candidate"]["edge"]["min_sharpe"] = 999.0  # now impossible to pass
        verification = eg.verify_admission_receipt(db_path, run_id, EDGE_ROLE, mutated)
        assert verification.replay.ok is False
        assert verification.replay.mode == "diverged"

    def test_tampered_receipt_fails_hash_verification(self, db_path, thresholds):
        run_id = _passing_run(db_path)
        receipt = eg.admit_evidence(db_path, run_id, EDGE_ROLE, thresholds)
        con = sqlite3.connect(db_path)
        con.execute("DROP TRIGGER trg_receipts_no_update")
        con.execute(
            "UPDATE governance_receipts SET body_json = "
            "REPLACE(body_json, '\"admitted\"', '\"DENIED\"') WHERE receipt_id = ?",
            (receipt["receipt_id"],),
        )
        con.commit()
        con.close()
        verification = eg.verify_admission_receipt(db_path, run_id, EDGE_ROLE, thresholds)
        assert verification.hash_ok is False

    def test_verify_no_receipt_raises(self, db_path, thresholds):
        run_id = _passing_run(db_path)
        with pytest.raises(eg.EvidenceGovernanceError):
            eg.verify_admission_receipt(db_path, run_id, EDGE_ROLE, thresholds)

    def test_bootstrap_receipt_refuses_replay(self, db_path, thresholds):
        run_id = _passing_run(db_path)
        eg.bootstrap_admission_receipt(db_path, run_id, "evidence.admit", "admitted", "reconstructed, no gate_results")
        verification = eg.verify_admission_receipt(db_path, run_id, EDGE_ROLE, thresholds)
        assert verification.hash_ok is True  # hash always verifiable
        assert verification.replay.ok is False
        assert verification.replay.mode == "refused"


# ---------------------------------------------------------------------------
# Admissibility rule interface (post-review correction): item 1 (genuine
# substitution point, no direct dependency on check_candidate_gate),
# item 2 (default provider, no behavioral change), item 3 (replay
# dispatches on the recorded rule; fails loudly if unresolvable).
# ---------------------------------------------------------------------------

class FakeAdmissibilityRule:
    """A standalone AdmissibilityRule implementation, defined entirely
    outside evidence_governance.py -- exactly the shape a PR-7 Data
    Completeness Gate rule would take. Used to prove the substitution
    point works without editing the module under test."""
    rule_id = "test.fake_rule"
    engine_version = "test.v1"

    def __init__(self, verdict: bool = True):
        self._verdict = verdict

    def evaluate(self, family_meta, run_metrics, thresholds):
        return self._verdict, [
            {"gate_code": "FAKE_CHECK", "role": None, "passed": self._verdict,
             "observed": str(self._verdict), "threshold": "True"}
        ]


@pytest.fixture(autouse=True)
def _clean_fake_rule_registration():
    """FakeAdmissibilityRule is registered by some tests below; ensure it
    never leaks into a different test via the shared module-level registry."""
    yield
    eg._RULE_REGISTRY.pop((FakeAdmissibilityRule.rule_id, FakeAdmissibilityRule.engine_version), None)


class TestAdmissibilityRuleInterface:
    def test_default_rule_is_candidate_gate_rule(self):
        assert eg.DEFAULT_RULE.rule_id == "lifecycle.check_candidate_gate"
        assert isinstance(eg.DEFAULT_RULE, eg.CandidateGateRule)

    def test_candidate_gate_rule_matches_lifecycle_directly_no_behavioral_change(self, thresholds):
        # item 2: CandidateGateRule must be a pure pass-through, byte-for-byte
        # equivalent to calling lifecycle.check_candidate_gate() directly.
        import lifecycle
        direct = lifecycle.check_candidate_gate(EDGE_ROLE, EDGE_PASS, thresholds)
        via_rule = eg.CandidateGateRule().evaluate(EDGE_ROLE, EDGE_PASS, thresholds)
        assert direct == via_rule

    def test_admit_evidence_default_rule_recorded_in_receipt(self, db_path, thresholds):
        run_id = _passing_run(db_path)
        receipt = eg.admit_evidence(db_path, run_id, EDGE_ROLE, thresholds)
        assert receipt["body"]["rule"]["rule_id"] == "lifecycle.check_candidate_gate"
        assert receipt["body"]["rule"]["engine_version"] == eg.ENGINE_VERSION

    def test_admit_evidence_accepts_injected_rule(self, db_path, thresholds):
        run_id = _passing_run(db_path)
        fake = FakeAdmissibilityRule(verdict=True)
        receipt = eg.admit_evidence(db_path, run_id, EDGE_ROLE, thresholds, rule=fake)
        assert receipt["body"]["rule"]["rule_id"] == "test.fake_rule"
        assert receipt["body"]["evaluation"][0]["check_id"] == "FAKE_CHECK"

    def test_reject_evidence_accepts_injected_rule(self, db_path, thresholds):
        run_id = _passing_run(db_path)
        fake = FakeAdmissibilityRule(verdict=False)
        receipt = eg.reject_evidence(db_path, run_id, EDGE_ROLE, thresholds, rule=fake)
        assert receipt["body"]["rule"]["rule_id"] == "test.fake_rule"
        assert receipt["body"]["verdict"]["result"] == "rejected"

    def test_injected_rule_still_refuses_false_verdict(self, db_path, thresholds):
        run_id = _passing_run(db_path)
        fake_fails = FakeAdmissibilityRule(verdict=False)
        with pytest.raises(eg.EvidenceGovernanceError):
            eg.admit_evidence(db_path, run_id, EDGE_ROLE, thresholds, rule=fake_fails)

    def test_evidence_governance_does_not_call_check_candidate_gate_outside_default_rule(self):
        # Structural guard (AST-based, not a docstring-fooled substring
        # match): after the correction, lifecycle.check_candidate_gate must
        # be CALLED from exactly one place -- CandidateGateRule.evaluate().
        # admit_evidence()/reject_evidence()/verify_admission_receipt() may
        # all still MENTION it in prose (they do, in docstrings explaining
        # the default), but none may call it directly.
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(eg))
        call_sites = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "check_candidate_gate"
        ]
        assert len(call_sites) == 1

    def test_register_rule_and_get_rule_roundtrip(self):
        fake = FakeAdmissibilityRule()
        eg.register_rule(fake)
        resolved = eg.get_rule("test.fake_rule", "test.v1")
        assert resolved is fake

    def test_get_rule_unregistered_raises_unknown_rule_error(self):
        with pytest.raises(eg.UnknownRuleError):
            eg.get_rule("nonexistent.rule", "v99")

    def test_get_rule_none_engine_version_raises(self):
        with pytest.raises(eg.UnknownRuleError):
            eg.get_rule("lifecycle.check_candidate_gate", None)

    def test_replay_dispatches_via_recorded_rule_not_current_default(self, db_path, thresholds):
        run_id = _passing_run(db_path)
        fake = FakeAdmissibilityRule(verdict=True)
        eg.register_rule(fake)
        eg.admit_evidence(db_path, run_id, EDGE_ROLE, thresholds, rule=fake)

        verification = eg.verify_admission_receipt(db_path, run_id, EDGE_ROLE, thresholds)
        assert verification.replay.ok is True
        assert verification.replay.mode == "verified"

    def test_replay_fails_loudly_when_historical_rule_unregistered(self, db_path, thresholds):
        run_id = _passing_run(db_path)
        fake = FakeAdmissibilityRule(verdict=True)
        eg.register_rule(fake)
        eg.admit_evidence(db_path, run_id, EDGE_ROLE, thresholds, rule=fake)

        # Simulate the historical implementation becoming unavailable (e.g.
        # this process never loaded the module that registers it).
        eg._RULE_REGISTRY.pop(("test.fake_rule", "test.v1"))

        with pytest.raises(eg.UnknownRuleError):
            eg.verify_admission_receipt(db_path, run_id, EDGE_ROLE, thresholds)

    def test_replay_never_silently_substitutes_default_rule(self, db_path, thresholds):
        """
        The core regression this correction exists to prevent: a receipt
        produced under a NON-default rule must never be replayed against
        CandidateGateRule just because that happens to be the default.
        Constructed so the two rules would reach OPPOSITE verdicts on the
        same run -- if replay silently substituted the default, this
        would report a false 'verified'/'diverged' instead of refusing.
        """
        run_id = _passing_run(db_path)  # passes the real candidate gate
        fake_fails = FakeAdmissibilityRule(verdict=False)
        eg.register_rule(fake_fails)
        # A rule that would fail this run is the one recorded on the receipt.
        eg.reject_evidence(db_path, run_id, EDGE_ROLE, thresholds, rule=fake_fails)

        eg._RULE_REGISTRY.pop(("test.fake_rule", "test.v1"))
        with pytest.raises(eg.UnknownRuleError):
            eg.verify_admission_receipt(db_path, run_id, EDGE_ROLE, thresholds)

    def test_coexistence_old_and_new_rules_both_replay_correctly(self, db_path, thresholds):
        # item 4: introducing a new rule must not disturb an existing one.
        run_default = _passing_run(db_path, family_id="fam_default")
        eg.admit_evidence(db_path, run_default, EDGE_ROLE, thresholds)  # DEFAULT_RULE

        fake = FakeAdmissibilityRule(verdict=True)
        eg.register_rule(fake)
        run_fake = _passing_run(db_path, family_id="fam_fake")
        eg.admit_evidence(db_path, run_fake, EDGE_ROLE, thresholds, rule=fake)

        v_default = eg.verify_admission_receipt(db_path, run_default, EDGE_ROLE, thresholds)
        v_fake = eg.verify_admission_receipt(db_path, run_fake, EDGE_ROLE, thresholds)
        assert v_default.replay.mode == "verified"
        assert v_fake.replay.mode == "verified"


# ---------------------------------------------------------------------------
# Bootstrap migration script
# ---------------------------------------------------------------------------

class TestBootstrapMigration:
    def test_plan_identifies_pinned_runs_needing_receipts(self, db_path, thresholds):
        run_id = _passing_run(db_path)
        record.append_transition(db_path, "fam_a", "discovery", "candidate", reason="r", justifying_run_id=run_id)
        registry = {"fam_a": {"state": "candidate", "role": ["edge"], "pinned_run": run_id}}
        items = mbea.plan(db_path, registry)
        assert len(items) == 1
        assert items[0][:4] == ("fam_a", run_id, "candidate", "evidence.admit")

    def test_apply_writes_bootstrap_receipts(self, db_path, thresholds):
        run_id = _passing_run(db_path)
        record.append_transition(db_path, "fam_a", "discovery", "candidate", reason="r", justifying_run_id=run_id)
        registry = {"fam_a": {"state": "candidate", "role": ["edge"], "pinned_run": run_id}}
        items = mbea.plan(db_path, registry)
        mbea.apply(db_path, items)
        receipt = eg.get_admission_receipt(db_path, run_id)
        assert receipt["body"]["attestation"] == "bootstrap"
        assert receipt["decision_type"] == "evidence.admit"

    def test_idempotent_second_plan_is_empty(self, db_path, thresholds):
        run_id = _passing_run(db_path)
        record.append_transition(db_path, "fam_a", "discovery", "candidate", reason="r", justifying_run_id=run_id)
        registry = {"fam_a": {"state": "candidate", "role": ["edge"], "pinned_run": run_id}}
        mbea.apply(db_path, mbea.plan(db_path, registry))
        assert mbea.plan(db_path, registry) == []

    def test_disqualified_family_gets_reject_receipt(self, db_path):
        run_id = _failing_run(db_path)
        record.append_transition(db_path, "fam_dq", "discovery", "disqualified", reason="dq", justifying_run_id=run_id)
        registry = {"fam_dq": {"state": "disqualified", "role": [], "pinned_run": run_id}}
        items = mbea.plan(db_path, registry)
        assert items[0][3] == "evidence.reject"
        mbea.apply(db_path, items)
        receipt = eg.get_admission_receipt(db_path, run_id)
        assert receipt["decision_type"] == "evidence.reject"

    def test_main_dry_run_writes_nothing(self, tmp_path, thresholds):
        db = tmp_path / "ledger.db"
        eg.init_db(db)
        run_id = _passing_run(db)
        record.append_transition(db, "fam_a", "discovery", "candidate", reason="r", justifying_run_id=run_id)
        registry_path = tmp_path / "registry.yaml"
        registry_path.write_text(yaml.safe_dump({"fam_a": {"state": "candidate", "role": ["edge"], "pinned_run": run_id}}))
        rc = mbea.main(["--db", str(db), "--registry", str(registry_path)])
        assert rc == 0
        assert eg.get_admission_receipt(db, run_id) is None

    def test_main_commit_writes_and_backs_up(self, tmp_path, thresholds):
        db = tmp_path / "ledger.db"
        eg.init_db(db)
        run_id = _passing_run(db)
        record.append_transition(db, "fam_a", "discovery", "candidate", reason="r", justifying_run_id=run_id)
        registry_path = tmp_path / "registry.yaml"
        registry_path.write_text(yaml.safe_dump({"fam_a": {"state": "candidate", "role": ["edge"], "pinned_run": run_id}}))
        rc = mbea.main(["--db", str(db), "--registry", str(registry_path), "--commit"])
        assert rc == 0
        assert eg.get_admission_receipt(db, run_id) is not None
        backups = list(tmp_path.glob("ledger.pre-evidence-admission-migration.*.db"))
        assert len(backups) == 1


# ---------------------------------------------------------------------------
# Governance validation hook, and its wiring into report.py --check
# ---------------------------------------------------------------------------

class TestGovernanceValidationHook:
    def test_validate_evidence_governance_clean(self, db_path, thresholds):
        run_id = _passing_run(db_path)
        eg.admit_evidence(db_path, run_id, EDGE_ROLE, thresholds)
        eg.pin_evidence(db_path, "fam_a", "candidate", run_id, actor="rohith", reason="promote")
        registry = {"fam_a": {"state": "candidate", "role": ["edge"], "pinned_run": run_id}}
        assert eg.validate_evidence_governance(db_path, registry) == []

    def test_validate_evidence_governance_detects_gap(self, db_path):
        run_id = _passing_run(db_path)
        record.append_transition(db_path, "fam_a", "discovery", "candidate", reason="raw", justifying_run_id=run_id)
        registry = {"fam_a": {"state": "candidate", "role": ["edge"], "pinned_run": run_id}}
        errors = eg.validate_evidence_governance(db_path, registry)
        assert any(e.startswith("EG1") for e in errors)

    def test_report_check_fails_without_admission(self, tmp_path, thresholds):
        db = tmp_path / "ledger.db"
        record.init_db(db)
        run_id = _passing_run(db)
        record.append_gate_result(db, run_id, "B_SHARPE", "edge", True, 0.9, 0.3)
        record.append_transition(db, "fam_a", "discovery", "candidate", reason="r", justifying_run_id=run_id)
        registry_path = tmp_path / "registry.yaml"
        registry_path.write_text(yaml.safe_dump({"fam_a": {"state": "candidate", "role": ["edge"], "pinned_run": run_id}}))
        thr_path = tmp_path / "thresholds.yaml"
        thr_path.write_text(yaml.safe_dump(thresholds))
        rc = report.run_check(registry_path, db, thr_path)
        assert rc == 1

    def test_report_check_passes_with_admission(self, tmp_path, thresholds):
        db = tmp_path / "ledger.db"
        eg.init_db(db)
        run_id = _passing_run(db)
        record.append_gate_result(db, run_id, "B_SHARPE", "edge", True, 0.9, 0.3)
        record.append_transition(db, "fam_a", "discovery", "candidate", reason="r", justifying_run_id=run_id)
        eg.admit_evidence(db, run_id, EDGE_ROLE, thresholds, actor="rohith", reason="ok")
        registry_path = tmp_path / "registry.yaml"
        registry_path.write_text(yaml.safe_dump({"fam_a": {"state": "candidate", "role": ["edge"], "pinned_run": run_id}}))
        thr_path = tmp_path / "thresholds.yaml"
        thr_path.write_text(yaml.safe_dump(thresholds))
        # check_transition_governance=False scopes this test to what it is
        # actually about: PR-6's EG hook. PR-8 added a second, independent hook
        # (TG) which correctly flags this fixture's raw record.append_transition()
        # call as an ungoverned transition. That is the new check working, not a
        # regression in this one -- the fixture predates the governed front door.
        # PR-8's own suite covers the TG hook directly.
        rc = report.run_check(registry_path, db, thr_path, check_transition_governance=False)
        assert rc == 0

    def test_report_check_as_of_skips_evidence_governance(self, tmp_path, thresholds):
        db = tmp_path / "ledger.db"
        record.init_db(db)
        run_id = _passing_run(db)
        record.append_gate_result(db, run_id, "B_SHARPE", "edge", True, 0.9, 0.3)
        record.append_transition(db, "fam_a", "discovery", "candidate", reason="r", justifying_run_id=run_id)
        registry_path = tmp_path / "registry.yaml"
        registry_path.write_text(yaml.safe_dump({"fam_a": {"state": "candidate", "role": ["edge"], "pinned_run": run_id}}))
        thr_path = tmp_path / "thresholds.yaml"
        thr_path.write_text(yaml.safe_dump(thresholds))
        # No admission receipt exists, but an --as-of run must not apply the
        # (inherently "now"-only) evidence-governance hook.
        rc = report.run_check(registry_path, db, thr_path, as_of="2026-06-13")
        assert rc == 0


# ---------------------------------------------------------------------------
# The real ledger: bootstrap-backfilled, must be fully governed
# ---------------------------------------------------------------------------

class TestRealLedgerIsFullyGoverned:
    def test_all_13_real_transitions_have_receipts(self):
        real_db = _EVLIB / "ledger.db"
        real_registry_path = _EVLIB / "registry.yaml"
        if not (real_db.exists() and real_registry_path.exists()):
            pytest.skip("real ledger/registry not present in this checkout")
        registry = yaml.safe_load(real_registry_path.read_text(encoding="utf-8"))
        errors = eg.validate_evidence_governance(real_db, registry)
        assert errors == [], f"real ledger must be fully governed post-migration: {errors}"

    def test_real_ledger_check_passes(self):
        real_db = _EVLIB / "ledger.db"
        real_registry_path = _EVLIB / "registry.yaml"
        real_thr_path = _EVLIB / "thresholds.yaml"
        if not (real_db.exists() and real_registry_path.exists()):
            pytest.skip("real ledger/registry not present in this checkout")
        rc = report.run_check(real_registry_path, real_db, real_thr_path)
        assert rc == 0
