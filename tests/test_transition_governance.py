"""
State Transition Governance — PR-8 of the Governance Architecture.

Covers: legal/illegal transitions, duplicate transitions, promotion receipts
(and their discretionary nature), replay, point-in-time reconstruction from
receipts, receipt integrity, transition ordering, governance validation, and
backward compatibility.

Scope discipline: nothing here emits, or asserts the existence of, any receipt
decision_type other than family.transition / family.promote / family.demote.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research" / "evidence_library"))

import evidence_governance as eg
import lifecycle
import receipt_ledger as rl
import receipts as rcp
import record
import transition_governance as tg

THRESHOLDS = {
    "candidate": {
        "hard_dq": {"knife_edge_sensitivity": 0.30},
        "edge": {"min_sharpe": 0.30, "min_wf_agreement": 0.60, "min_sensitivity": 0.60,
                 "min_loo_consistency": 0.60, "min_breakeven_bps": 10},
        "feature": {"min_sensitivity": 0.60},
    },
    "validated": {
        "edge": {"requires_non_offline_provenance": True, "min_dsr_prob": 0.50, "max_bh_p": 0.10},
    },
}

PASSING = {"sharpe": 0.9, "oos_sharpe": 0.4, "sens_pass_frac": 1.0, "wf_pass_frac": 0.8,
           "loo_sign_consistency": 1.0, "breakeven_cost_bps": 100, "struct_sign_ok": True,
           "n_trades": 50}
FAILING = {"sharpe": 0.1, "oos_sharpe": -0.5, "sens_pass_frac": 0.0, "wf_pass_frac": 0.1,
           "loo_sign_consistency": 0.1, "breakeven_cost_bps": 1, "struct_sign_ok": False,
           "n_trades": 5}
EDGE = {"role": ["edge"]}


@pytest.fixture
def db(tmp_path) -> Path:
    p = tmp_path / "ledger.db"
    eg.init_db(p)
    return p


def add_run(db, family_id="fam_a", metrics=None, provenance="offline-corpus",
            run_kind="discovery", governance=None):
    return record.append_run(db, family_id, metrics or dict(PASSING), provenance, run_kind,
                             governance=governance or {})


def admit(db, run_id, family_meta=EDGE):
    return eg.admit_evidence(db, run_id, family_meta, THRESHOLDS, reason="passes gate")


def reject(db, run_id, family_meta=EDGE):
    return eg.reject_evidence(db, run_id, family_meta, THRESHOLDS, reason="fails gate")


def candidate(db, family_id="fam_a"):
    """Get a family to `candidate` through the governed front door."""
    run_id = add_run(db, family_id)
    admit(db, run_id)
    tg.transition_with_receipt(db, family_id, "candidate", justifying_run_id=run_id,
                               actor="rohith", reason="passes the edge gate",
                               family_meta=EDGE, thresholds=THRESHOLDS)
    return run_id


def forward_run(db, family_id="fam_a", dsr=0.9, bh=0.01, ready=True):
    """A forward run suitable for Validated promotion. PR-9 armed the readiness
    gate at Validated, so a promotable forward run now carries a READY readiness
    block (G3's shape); pass ready=False to exercise the block firing."""
    metrics = dict(PASSING)
    if ready:
        metrics["readiness"] = {"days_total": 30, "days_ready": 30, "days_non_ready": []}
    return add_run(db, family_id, metrics=metrics, provenance="live-forward",
                   run_kind="forward", governance={"dsr_prob": dsr, "bh_p": bh})


# ---------------------------------------------------------------------------
# Legal transitions
# ---------------------------------------------------------------------------

class TestLegalTransitions:
    def test_transition_emits_a_receipt(self, db):
        run_id = add_run(db)
        admit(db, run_id)
        result = tg.transition_with_receipt(
            db, "fam_a", "candidate", justifying_run_id=run_id,
            actor="rohith", reason="passes the edge gate",
            family_meta=EDGE, thresholds=THRESHOLDS,
        )
        receipt = result["receipt"]
        assert receipt["decision_type"] == "family.transition"
        assert receipt["decision_class"] == "discretionary"
        assert receipt["action"] == "transition"
        assert receipt["subject_kind"] == "family"
        assert receipt["subject_id"] == "fam_a"
        assert receipt["from_state"] == "discovery"
        assert receipt["to_state"] == "candidate"
        assert receipt["body"]["reason_text"] == "passes the edge gate"

    def test_the_receipt_pins_the_exact_transition_row(self, db):
        run_id = add_run(db)
        admit(db, run_id)
        result = tg.transition_with_receipt(
            db, "fam_a", "candidate", justifying_run_id=run_id, actor="rohith",
            reason="r", family_meta=EDGE, thresholds=THRESHOLDS,
        )
        assert result["receipt"]["subject_version"] == str(result["transition_id"])
        found = tg.get_receipt_for_transition(db, "fam_a", result["transition_id"])
        assert found["receipt_id"] == result["receipt"]["receipt_id"]

    def test_the_ledger_state_actually_moves(self, db):
        candidate(db)
        state, pinned, _ts = record.derive_state_at(db, "fam_a")
        assert state == "candidate"
        assert pinned is not None

    def test_receipt_records_the_preconditions_it_checked(self, db):
        run_id = add_run(db)
        admit(db, run_id)
        r = tg.transition_with_receipt(db, "fam_a", "candidate", justifying_run_id=run_id,
                                       actor="rohith", reason="r", family_meta=EDGE,
                                       thresholds=THRESHOLDS)
        checks = {e["check_id"] for e in r["receipt"]["body"]["evaluation"]}
        assert {"TG-CUR.current_state", "TG-DST.destination_state", "TG-LEG.legality",
                "TG-PIN.pinned_evidence", "TG-ADM.admissibility", "TG-RDY.readiness",
                "TG-PRE.prerequisites"} <= checks

    def test_receipt_joins_the_existing_chain(self, db):
        candidate(db, "fam_a")
        candidate(db, "fam_b")
        chain = rl.verify_chain(db)
        assert chain.ok
        # 2 evidence.admit (PR-6) + 2 family.transition (PR-8), one chain.
        assert chain.n_receipts == 4

    def test_disqualification_needs_a_rejection_receipt(self, db):
        run_id = add_run(db, metrics=dict(FAILING))
        reject(db, run_id)
        r = tg.transition_with_receipt(db, "fam_a", "disqualified", justifying_run_id=run_id,
                                       actor="rohith", reason="knife-edge sensitivity",
                                       family_meta=EDGE, thresholds=THRESHOLDS)
        assert r["receipt"]["decision_type"] == "family.transition"
        assert record.derive_state_at(db, "fam_a")[0] == "disqualified"

    def test_retire_needs_no_run(self, db):
        candidate(db)
        r = tg.transition_with_receipt(db, "fam_a", "retired", actor="rohith",
                                       reason="superseded by fam_b")
        assert r["to_state"] == "retired"

    def test_repin_is_a_legal_self_transition(self, db):
        candidate(db)
        run2 = add_run(db)
        admit(db, run2)
        r = tg.transition_with_receipt(db, "fam_a", "candidate", justifying_run_id=run2,
                                       actor="rohith", reason="re-pinned to fresher evidence",
                                       family_meta=EDGE, thresholds=THRESHOLDS)
        assert r["from_state"] == "candidate" and r["to_state"] == "candidate"
        assert record.derive_state_at(db, "fam_a")[1] == run2


# ---------------------------------------------------------------------------
# Illegal transitions — refused BEFORE execution
# ---------------------------------------------------------------------------

class TestIllegalTransitions:
    def test_illegal_arc_is_refused(self, db):
        run_id = add_run(db)
        admit(db, run_id)
        with pytest.raises(tg.IllegalTransitionError, match="illegal arc"):
            tg.transition_with_receipt(db, "fam_a", "validated", justifying_run_id=run_id,
                                       actor="rohith", reason="skipping ahead",
                                       family_meta=EDGE, thresholds=THRESHOLDS)

    def test_a_refused_transition_writes_NOTHING(self, db):
        """There is no state in which an illegal transition exists in the ledger
        'flagged as bad'. It never enters."""
        run_id = add_run(db)
        admit(db, run_id)
        before_t = len(record.get_all_transitions(db))
        before_r = len(rl.get_all_receipts(db))
        with pytest.raises(tg.IllegalTransitionError):
            tg.transition_with_receipt(db, "fam_a", "validated", justifying_run_id=run_id,
                                       actor="rohith", reason="r", family_meta=EDGE,
                                       thresholds=THRESHOLDS)
        assert len(record.get_all_transitions(db)) == before_t
        assert len(rl.get_all_receipts(db)) == before_r

    def test_unknown_destination_state(self, db):
        with pytest.raises(tg.IllegalTransitionError, match="unknown destination state"):
            tg.transition_with_receipt(db, "fam_a", "vibes", actor="rohith", reason="r")

    def test_transition_without_an_admission_receipt_is_refused(self, db):
        """PR-6's admission receipt is a precondition, enforced here."""
        run_id = add_run(db)   # never admitted
        with pytest.raises(tg.IllegalTransitionError, match="no admission receipt"):
            tg.transition_with_receipt(db, "fam_a", "candidate", justifying_run_id=run_id,
                                       actor="rohith", reason="r", family_meta=EDGE,
                                       thresholds=THRESHOLDS)

    def test_wrong_admission_receipt_type_is_refused(self, db):
        run_id = add_run(db, metrics=dict(FAILING))
        reject(db, run_id)     # a REJECTION cannot justify a candidate pin
        with pytest.raises(tg.IllegalTransitionError, match="requires a 'evidence.admit'"):
            tg.transition_with_receipt(db, "fam_a", "candidate", justifying_run_id=run_id,
                                       actor="rohith", reason="r", family_meta=EDGE,
                                       thresholds=THRESHOLDS)

    def test_run_from_another_family_is_refused(self, db):
        run_id = add_run(db, family_id="fam_b")
        admit(db, run_id)
        with pytest.raises(tg.IllegalTransitionError, match="pinned evidence invalid"):
            tg.transition_with_receipt(db, "fam_a", "candidate", justifying_run_id=run_id,
                                       actor="rohith", reason="r", family_meta=EDGE,
                                       thresholds=THRESHOLDS)

    def test_nonexistent_run_is_refused(self, db):
        with pytest.raises(tg.IllegalTransitionError, match="not found"):
            tg.transition_with_receipt(db, "fam_a", "candidate", justifying_run_id="nope",
                                       actor="rohith", reason="r", family_meta=EDGE,
                                       thresholds=THRESHOLDS)

    def test_empty_reason_is_refused(self, db):
        """Every decision type here is discretionary: the ledger records WHY,
        not merely what."""
        run_id = add_run(db)
        admit(db, run_id)
        with pytest.raises(tg.IllegalTransitionError, match="REQUIRES a non-empty reason"):
            tg.transition_with_receipt(db, "fam_a", "candidate", justifying_run_id=run_id,
                                       actor="rohith", reason="   ", family_meta=EDGE,
                                       thresholds=THRESHOLDS)

    def test_stale_from_state_is_caught(self, db):
        """A lost-update guard: the caller asserts what it believes the current
        state to be, so a concurrent transition that moved the family underneath
        it is caught rather than silently overwritten."""
        candidate(db)
        run2 = add_run(db)
        admit(db, run2)
        with pytest.raises(tg.IllegalTransitionError, match="moved underneath"):
            tg.transition_with_receipt(db, "fam_a", "candidate", justifying_run_id=run2,
                                       actor="rohith", reason="r", from_state="discovery",
                                       family_meta=EDGE, thresholds=THRESHOLDS)

    def test_terminal_states_are_terminal(self, db):
        run_id = add_run(db, metrics=dict(FAILING))
        reject(db, run_id)
        tg.transition_with_receipt(db, "fam_a", "disqualified", justifying_run_id=run_id,
                                   actor="rohith", reason="dq", family_meta=EDGE,
                                   thresholds=THRESHOLDS)
        run2 = add_run(db)
        admit(db, run2)
        with pytest.raises(tg.IllegalTransitionError, match="illegal arc"):
            tg.transition_with_receipt(db, "fam_a", "candidate", justifying_run_id=run2,
                                       actor="rohith", reason="resurrect", family_meta=EDGE,
                                       thresholds=THRESHOLDS)

    def test_validate_transition_is_side_effect_free(self, db):
        """Safe to call speculatively — e.g. to render 'why can't I promote
        this?' without attempting the promotion."""
        run_id = add_run(db)
        admit(db, run_id)
        before = (len(record.get_all_transitions(db)), len(rl.get_all_receipts(db)))
        v = tg.validate_transition(db, "fam_a", "validated", run_id,
                                   family_meta=EDGE, thresholds=THRESHOLDS)
        assert not v.ok
        assert (len(record.get_all_transitions(db)), len(rl.get_all_receipts(db))) == before


# ---------------------------------------------------------------------------
# Promotion — discretionary, never derived
# ---------------------------------------------------------------------------

class TestPromotion:
    def promote_to_validated(self, db, **kw):
        candidate(db)
        run_fwd = forward_run(db)
        admit(db, run_fwd)
        return tg.promote_with_receipt(
            db, "fam_a", "validated", justifying_run_id=run_fwd,
            actor=kw.pop("actor", "rohith"),
            reason=kw.pop("reason", "forward evidence confirms the edge"),
            family_meta=EDGE, thresholds=THRESHOLDS, **kw,
        )

    def test_promotion_emits_a_promote_receipt(self, db):
        r = self.promote_to_validated(db)
        assert r["receipt"]["decision_type"] == "family.promote"
        assert r["receipt"]["action"] == "promote"
        assert r["receipt"]["decision_class"] == "discretionary"
        assert record.derive_state_at(db, "fam_a")[0] == "validated"

    def test_promotion_is_never_derived_there_is_no_auto_promote(self):
        """Passing the gates makes a promotion LEGAL, never AUTOMATIC. The gates
        are necessary conditions, not a decision procedure."""
        for name in ("auto_promote", "promote_if_eligible", "maybe_promote",
                     "promote_all", "evaluate_promotions"):
            assert not hasattr(tg, name), f"{name}() would derive a promotion"

    def test_a_service_cannot_promote(self, db):
        """The platform must not be able to promote itself."""
        candidate(db)
        run_fwd = forward_run(db)
        admit(db, run_fwd)
        with pytest.raises(tg.IllegalTransitionError, match="requires a human actor"):
            tg.promote_with_receipt(db, "fam_a", "validated", justifying_run_id=run_fwd,
                                    actor="scheduler", reason="thresholds crossed",
                                    family_meta=EDGE, thresholds=THRESHOLDS,
                                    actor_type="service")

    def test_promotion_refuses_a_non_promotion_arc(self, db):
        candidate(db)
        run2 = add_run(db)
        admit(db, run2)
        with pytest.raises(tg.IllegalTransitionError, match="not a promotion arc"):
            tg.promote_with_receipt(db, "fam_a", "retired", justifying_run_id=run2,
                                    actor="rohith", reason="r", family_meta=EDGE,
                                    thresholds=THRESHOLDS)

    def test_validated_enforces_dsr_and_bh_preconditions(self, db):
        """The same hard-fail, no-silent-bypass semantics as the existing
        NULL-DSR check (§16 R1)."""
        candidate(db)
        run_fwd = add_run(db, provenance="live-forward", run_kind="forward",
                          governance={"dsr_prob": None, "bh_p": None})
        admit(db, run_fwd)
        with pytest.raises(tg.IllegalTransitionError, match="DSR probability is absent"):
            tg.promote_with_receipt(db, "fam_a", "validated", justifying_run_id=run_fwd,
                                    actor="rohith", reason="r", family_meta=EDGE,
                                    thresholds=THRESHOLDS)

    def test_validated_refuses_offline_provenance(self, db):
        candidate(db)
        run_off = add_run(db, provenance="offline-corpus", run_kind="confirmatory",
                          governance={"dsr_prob": 0.9, "bh_p": 0.01})
        admit(db, run_off)
        with pytest.raises(tg.IllegalTransitionError, match="requires live-forward or mainnet"):
            tg.promote_with_receipt(db, "fam_a", "validated", justifying_run_id=run_off,
                                    actor="rohith", reason="r", family_meta=EDGE,
                                    thresholds=THRESHOLDS)

    def test_promotion_replays_as_envelope_verified_never_recomputed(self, db):
        """Replay must never attempt to reproduce discretionary human judgment."""
        r = self.promote_to_validated(db)
        result = tg.replay_transition_receipt(db, r["receipt"]["receipt_id"])
        assert result.ok
        assert result.replay.mode == "envelope-verified"

    def test_promotion_survives_a_later_threshold_change(self, db):
        """Re-running today's thresholds against a past promotion would not
        AUDIT that decision, it would silently REPLACE it with a different one
        and call the difference a failure."""
        r = self.promote_to_validated(db)
        # governance tightens after the fact
        THRESHOLDS["validated"]["edge"]["min_dsr_prob"] = 0.999
        try:
            result = tg.replay_transition_receipt(db, r["receipt"]["receipt_id"])
            assert result.ok, "a discretionary decision must not be re-judged by new thresholds"
        finally:
            THRESHOLDS["validated"]["edge"]["min_dsr_prob"] = 0.50

    def test_get_promotions(self, db):
        self.promote_to_validated(db)
        promos = tg.get_promotions(db)
        assert len(promos) == 1
        assert promos[0]["subject_id"] == "fam_a"

    def test_demotion_is_classified_as_demote(self, db):
        self.promote_to_validated(db)
        run3 = forward_run(db)
        admit(db, run3)
        tg.transition_with_receipt(db, "fam_a", "promotion_ready", justifying_run_id=run3,
                                   actor="rohith", reason="ready", family_meta=EDGE,
                                   thresholds=THRESHOLDS)
        run4 = forward_run(db)
        admit(db, run4)
        r = tg.transition_with_receipt(db, "fam_a", "validated", justifying_run_id=run4,
                                       actor="rohith", reason="forward decayed",
                                       family_meta=EDGE, thresholds=THRESHOLDS)
        assert r["receipt"]["decision_type"] == "family.demote"
        assert r["receipt"]["action"] == "demote"

    def test_arc_classification_is_pure(self):
        assert tg.classify_arc("candidate", "validated") == "family.promote"
        assert tg.classify_arc("promotion_ready", "validated") == "family.demote"
        assert tg.classify_arc("discovery", "candidate") == "family.transition"
        assert tg.classify_arc("candidate", "retired") == "family.transition"


# ---------------------------------------------------------------------------
# Readiness hook (PR-7 framework; PR-9 arms it at Validated only)
#
# G2 names the VALIDATED gate specifically. So for a NON-validated target
# (candidate here) readiness is REPORTED, never blocking — a present block that
# is not READY does not stop a candidate pin. Enforcement lives at Validated,
# and is covered by TestReadinessGate in test_readiness_gate.py.
# ---------------------------------------------------------------------------

class TestReadinessHook:
    def test_absent_readiness_at_candidate_is_reported_not_blocking(self, db):
        run_id = add_run(db)
        admit(db, run_id)
        v = tg.validate_transition(db, "fam_a", "candidate", run_id,
                                   family_meta=EDGE, thresholds=THRESHOLDS)
        rdy = next(c for c in v.checks if c.check_id == "TG-RDY.readiness")
        assert rdy.passed
        assert "not_computed" in rdy.observed

    def test_a_non_ready_block_does_not_block_a_non_validated_target(self, db):
        """PR-9 asymmetry: at candidate, readiness is reported, never enforced.
        The identical block DOES block at Validated — see TestReadinessGate."""
        metrics = dict(PASSING)
        metrics["readiness"] = {"days_total": 30, "days_ready": 28,
                                "days_non_ready": ["2026-06-14", "2026-06-15"]}
        run_id = add_run(db, metrics=metrics)
        admit(db, run_id)
        v = tg.validate_transition(db, "fam_a", "candidate", run_id,
                                   family_meta=EDGE, thresholds=THRESHOLDS)
        rdy = next(c for c in v.checks if c.check_id == "TG-RDY.readiness")
        assert v.ok
        assert rdy.passed
        assert "not enforced" in rdy.expected


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------

class TestReplay:
    def test_replay_verifies_integrity_legality_and_envelope(self, db):
        run_id = add_run(db)
        admit(db, run_id)
        r = tg.transition_with_receipt(db, "fam_a", "candidate", justifying_run_id=run_id,
                                       actor="rohith", reason="r", family_meta=EDGE,
                                       thresholds=THRESHOLDS)
        result = tg.replay_transition_receipt(db, r["receipt"]["receipt_id"])
        assert result.ok
        assert result.hash_ok
        assert result.legality_ok
        assert result.replay.mode == "envelope-verified"
        assert result.transition_id == r["transition_id"]

    def test_replay_detects_a_tampered_body(self, db):
        run_id = add_run(db)
        admit(db, run_id)
        r = tg.transition_with_receipt(db, "fam_a", "candidate", justifying_run_id=run_id,
                                       actor="rohith", reason="r", family_meta=EDGE,
                                       thresholds=THRESHOLDS)
        con = sqlite3.connect(db)
        try:
            con.execute("DROP TRIGGER trg_receipts_no_update")
            body = r["receipt"]["body"]
            body["to_state"] = "validated"   # forge a promotion
            import json
            con.execute("UPDATE governance_receipts SET body_json=? WHERE receipt_id=?",
                        (json.dumps(body, sort_keys=True), r["receipt"]["receipt_id"]))
            con.commit()
        finally:
            con.close()
        result = tg.replay_transition_receipt(db, r["receipt"]["receipt_id"])
        assert not result.hash_ok
        assert not result.ok

    def test_replay_detects_an_illegal_arc_in_a_forged_receipt(self, db):
        """If the state machine ever changed under recorded history, replay says
        so rather than quietly accepting it."""
        run_id = add_run(db)
        admit(db, run_id)
        r = tg.transition_with_receipt(db, "fam_a", "candidate", justifying_run_id=run_id,
                                       actor="rohith", reason="r", family_meta=EDGE,
                                       thresholds=THRESHOLDS)
        original = dict(lifecycle.ALLOWED_TRANSITIONS)
        try:
            lifecycle.ALLOWED_TRANSITIONS["discovery"] = frozenset({"retired"})
            result = tg.replay_transition_receipt(db, r["receipt"]["receipt_id"])
            assert not result.legality_ok
            assert not result.ok
            assert any("not legal under the current state machine" in e for e in result.errors)
        finally:
            lifecycle.ALLOWED_TRANSITIONS.clear()
            lifecycle.ALLOWED_TRANSITIONS.update(original)

    def test_replay_refuses_a_non_transition_receipt(self, db):
        run_id = add_run(db)
        adm = admit(db, run_id)
        with pytest.raises(tg.TransitionGovernanceError, match="not a transition receipt"):
            tg.replay_transition_receipt(db, adm["receipt_id"])

    def test_replay_of_unknown_receipt(self, db):
        with pytest.raises(tg.TransitionNotFoundError):
            tg.replay_transition_receipt(db, "deadbeef")

    def test_replay_supplies_no_rule_fn(self):
        """Supplying one would be a category error — it would recompute a
        human's choice. Asserted structurally, not by docstring."""
        import ast
        src = (ROOT / "research" / "evidence_library" / "transition_governance.py").read_text(
            encoding="utf-8")
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.FunctionDef) and node.name == "replay_transition_receipt":
                for call in ast.walk(node):
                    if isinstance(call, ast.Call):
                        name = getattr(call.func, "id", None) or getattr(call.func, "attr", None)
                        if name == "replay_receipt_body":
                            kws = {k.arg for k in call.keywords}
                            assert "rule_fn" not in kws, (
                                "replay must not recompute a discretionary decision")
                            return
        pytest.fail("replay_receipt_body call not found")


# ---------------------------------------------------------------------------
# Point-in-time reconstruction from RECEIPTS
# ---------------------------------------------------------------------------

class TestPointInTime:
    def test_reconstruct_from_receipts_matches_the_ledger(self, db):
        run_id = candidate(db)
        assert tg.reconstruct_state_at(db, "fam_a")[0] == "candidate"
        assert tg.reconstruct_state_at(db, "fam_a")[1] == run_id
        assert tg.reconstruct_state_at(db, "fam_a")[0] == record.derive_state_at(db, "fam_a")[0]

    def test_as_of_returns_the_state_then_not_now(self, db):
        run_id = add_run(db)
        admit(db, run_id)
        tg.transition_with_receipt(db, "fam_a", "candidate", justifying_run_id=run_id,
                                   actor="rohith", reason="r", family_meta=EDGE,
                                   thresholds=THRESHOLDS, ts="2026-07-01T00:00:00+00:00")
        run2 = forward_run(db)
        admit(db, run2)
        tg.transition_with_receipt(db, "fam_a", "validated", justifying_run_id=run2,
                                   actor="rohith", reason="promoted", family_meta=EDGE,
                                   thresholds=THRESHOLDS, ts="2026-07-10T00:00:00+00:00")

        assert tg.reconstruct_state_at(db, "fam_a", "2026-07-05T00:00:00+00:00")[0] == "candidate"
        assert tg.reconstruct_state_at(db, "fam_a", "2026-07-20T00:00:00+00:00")[0] == "validated"
        assert tg.reconstruct_state_at(db, "fam_a", "2026-06-01T00:00:00+00:00")[0] is None

    def test_reconstruct_all_states(self, db):
        candidate(db, "fam_a")
        run_b = add_run(db, "fam_b", metrics=dict(FAILING))
        reject(db, run_b)
        tg.transition_with_receipt(db, "fam_b", "disqualified", justifying_run_id=run_b,
                                   actor="rohith", reason="dq", family_meta=EDGE,
                                   thresholds=THRESHOLDS)
        states = tg.reconstruct_all_states_at(db)
        assert states["fam_a"][0] == "candidate"
        assert states["fam_b"][0] == "disqualified"

    def test_unknown_family(self, db):
        assert tg.reconstruct_state_at(db, "nope") == (None, None, None)


# ---------------------------------------------------------------------------
# Governance validation
# ---------------------------------------------------------------------------

class TestGovernanceValidation:
    def test_clean_ledger_passes(self, db):
        candidate(db, "fam_a")
        candidate(db, "fam_b")
        assert tg.validate_transition_governance(db) == []

    def test_TG1_detects_a_transition_written_via_the_raw_primitive(self, db):
        """The governed front door is transition_with_receipt(). A bypass is
        detected rather than made impossible — the raw primitive must remain
        for bootstrap/migration, exactly as contract_lifecycle's does."""
        run_id = add_run(db)
        admit(db, run_id)
        record.append_transition(db, "fam_a", "discovery", "candidate",
                                 reason="raw", justifying_run_id=run_id)
        errors = tg.validate_transition_governance(db)
        assert any(e.startswith("TG1") for e in errors)

    def test_TG2_detects_a_duplicate_receipt(self, db):
        run_id = add_run(db)
        admit(db, run_id)
        r = tg.transition_with_receipt(db, "fam_a", "candidate", justifying_run_id=run_id,
                                       actor="rohith", reason="r", family_meta=EDGE,
                                       thresholds=THRESHOLDS)
        t = record.get_all_transitions(db)[0]
        tg.bootstrap_transition_receipt(db, t, "a second receipt for the same transition")
        errors = tg.validate_transition_governance(db)
        assert any(e.startswith("TG2") and "2 governance" in e for e in errors)

    def test_TG2_detects_an_orphan_receipt(self, db):
        candidate(db)
        t = dict(record.get_all_transitions(db)[0])
        t["id"] = 9999
        tg.bootstrap_transition_receipt(db, t, "points at a transition that does not exist")
        errors = tg.validate_transition_governance(db)
        assert any(e.startswith("TG2") and "does not exist" in e for e in errors)

    def test_TG4_detects_a_broken_chain(self, db):
        """Each from_state must equal the previous to_state."""
        run_id = add_run(db)
        admit(db, run_id)
        con = sqlite3.connect(db)
        try:
            con.execute(
                "INSERT INTO state_transitions (family_id, from_state, to_state, ts, reason, "
                "justifying_run_id, actor) VALUES (?,?,?,?,?,?,?)",
                ("fam_a", "validated", "promotion_ready", "2026-07-01T00:00:00+00:00",
                 "hand-written", run_id, "hacker"),
            )
            con.commit()
        finally:
            con.close()
        errors = tg.validate_transition_governance(db)
        assert any(e.startswith("TG4") and "chain is broken" in e for e in errors)

    def test_TG4_detects_an_ordering_violation(self, db):
        run_id = add_run(db)
        admit(db, run_id)
        tg.transition_with_receipt(db, "fam_a", "candidate", justifying_run_id=run_id,
                                   actor="rohith", reason="r", family_meta=EDGE,
                                   thresholds=THRESHOLDS, ts="2026-07-10T00:00:00+00:00")
        con = sqlite3.connect(db)
        try:
            con.execute(
                "INSERT INTO state_transitions (family_id, from_state, to_state, ts, reason, "
                "justifying_run_id, actor) VALUES (?,?,?,?,?,?,?)",
                ("fam_a", "candidate", "retired", "2026-07-01T00:00:00+00:00",
                 "backdated", None, "hacker"),
            )
            con.commit()
        finally:
            con.close()
        errors = tg.validate_transition_governance(db)
        assert any("TG4" in e for e in errors)

    def test_TG3_detects_an_illegal_recorded_arc(self, db):
        con = sqlite3.connect(db)
        try:
            con.execute(
                "INSERT INTO state_transitions (family_id, from_state, to_state, ts, reason, "
                "justifying_run_id, actor) VALUES (?,?,?,?,?,?,?)",
                ("fam_a", "retired", "candidate", "2026-07-01T00:00:00+00:00",
                 "resurrection", None, "hacker"),
            )
            con.commit()
        finally:
            con.close()
        errors = tg.validate_transition_governance(db)
        assert any(e.startswith("TG3") for e in errors)

    def test_TG5_detects_disagreement_between_the_two_derivations(self, db):
        """state_transitions and the receipt chain are two INDEPENDENT records of
        the same decision. If they disagree, one is forged."""
        candidate(db)
        run2 = forward_run(db)
        admit(db, run2)
        # a transition written WITHOUT a receipt moves the ledger but not the
        # receipt-derived state
        record.append_transition(db, "fam_a", "candidate", "validated",
                                 reason="raw", justifying_run_id=run2)
        errors = tg.validate_transition_governance(db)
        assert any(e.startswith("TG5") and "disagree" in e for e in errors)

    def test_get_receipt_for_transition_raises_on_duplicates(self, db):
        candidate(db)
        t = record.get_all_transitions(db)[0]
        tg.bootstrap_transition_receipt(db, t, "duplicate")
        with pytest.raises(tg.DuplicateTransitionReceiptError):
            tg.get_receipt_for_transition(db, "fam_a", t["id"])


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

class TestBootstrap:
    def test_bootstrap_receipt_refuses_replay_but_verifies(self, db):
        run_id = add_run(db)
        admit(db, run_id)
        record.append_transition(db, "fam_a", "discovery", "candidate",
                                 reason="pre-PR-8", justifying_run_id=run_id)
        t = record.get_all_transitions(db)[0]
        receipt = tg.bootstrap_transition_receipt(db, t, "predates PR-8; never captured")
        result = tg.replay_transition_receipt(db, receipt["receipt_id"])
        assert result.hash_ok          # verify() holds forever
        assert result.legality_ok      # so does the arc check
        assert result.replay.mode == "refused"   # but the decision is not reproducible

    def test_bootstrap_satisfies_TG1_and_the_ledger_validates(self, db):
        run_id = add_run(db)
        admit(db, run_id)
        record.append_transition(db, "fam_a", "discovery", "candidate",
                                 reason="pre-PR-8", justifying_run_id=run_id)
        assert any(e.startswith("TG1") for e in tg.validate_transition_governance(db))
        t = record.get_all_transitions(db)[0]
        tg.bootstrap_transition_receipt(db, t, "predates PR-8")
        assert tg.validate_transition_governance(db) == []

    def test_bootstrap_carries_the_justifying_run_it_already_had(self, db):
        """The run is a fact already recorded on the transition row, not a
        reconstruction. Omitting it would make the receipt chain a weaker record
        of history than the table it governs — and TG5, which compares only
        `state`, would not catch that."""
        run_id = add_run(db)
        admit(db, run_id)
        record.append_transition(db, "fam_a", "discovery", "candidate",
                                 reason="pre-PR-8", justifying_run_id=run_id)
        t = record.get_all_transitions(db)[0]
        tg.bootstrap_transition_receipt(db, t, "predates PR-8")
        assert tg.reconstruct_state_at(db, "fam_a") == record.derive_state_at(db, "fam_a")
        assert tg.reconstruct_state_at(db, "fam_a")[1] == run_id

    def test_bootstrap_records_no_evaluation(self, db):
        """Those preconditions were never checked at the time; inventing them is
        the lie bootstrap honesty exists to prevent."""
        run_id = add_run(db)
        admit(db, run_id)
        record.append_transition(db, "fam_a", "discovery", "candidate",
                                 reason="pre-PR-8", justifying_run_id=run_id)
        t = record.get_all_transitions(db)[0]
        receipt = tg.bootstrap_transition_receipt(db, t, "predates PR-8")
        assert receipt["body"]["evaluation"] == []

    def test_bootstrap_requires_a_note(self, db):
        candidate(db)
        t = record.get_all_transitions(db)[0]
        with pytest.raises(rcp.BodyValidationError, match="attestation_note"):
            tg.bootstrap_transition_receipt(db, t, "")

    def test_migration_is_idempotent(self, db):
        import migrate_bootstrap_transition_receipts as mig
        run_id = add_run(db)
        admit(db, run_id)
        record.append_transition(db, "fam_a", "discovery", "candidate",
                                 reason="pre-PR-8", justifying_run_id=run_id)
        assert len(mig.plan(db)) == 1
        mig.apply(db, mig.plan(db))
        assert mig.plan(db) == []          # idempotent
        assert tg.validate_transition_governance(db) == []
        assert rl.verify_chain(db).ok


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    def test_the_raw_primitive_still_works_unchanged(self, db):
        """PR-6's pin_evidence() and every migration script call
        record.append_transition() verbatim. It must keep working."""
        run_id = add_run(db)
        admit(db, run_id)
        record.append_transition(db, "fam_a", "discovery", "candidate",
                                 reason="r", justifying_run_id=run_id)
        assert record.derive_state_at(db, "fam_a")[0] == "candidate"

    def test_append_transition_now_returns_the_id_additively(self, db):
        run_id = add_run(db)
        admit(db, run_id)
        tid = record.append_transition(db, "fam_a", "discovery", "candidate",
                                       reason="r", justifying_run_id=run_id)
        assert isinstance(tid, int)
        assert record.get_all_transitions(db)[0]["id"] == tid

    def test_pr6_pin_evidence_still_works(self, db):
        run_id = add_run(db)
        admit(db, run_id)
        result = eg.pin_evidence(db, "fam_a", "candidate", run_id, "rohith", "pinned")
        assert result["to_state"] == "candidate"

    def test_append_transition_still_validates_arcs(self, db):
        with pytest.raises(ValueError, match="Invalid lifecycle arc"):
            record.append_transition(db, "fam_a", "retired", "candidate", reason="r")

    def test_append_transition_still_requires_a_run_for_repins(self, db):
        with pytest.raises(ValueError, match="re-pin"):
            record.append_transition(db, "fam_a", "candidate", "candidate", reason="r")

    def test_append_transition_still_grandfathers_old_bootstrap_rows(self, db):
        record.append_transition(db, "fam_a", "discovery", "candidate", reason="bootstrap",
                                 ts="2026-01-01T00:00:00+00:00")
        assert record.derive_state_at(db, "fam_a")[0] == "candidate"

    def test_a_failed_transition_rolls_back_the_row_too(self, db, monkeypatch):
        """Transition and receipt commit in ONE transaction: there is no state
        where a transition exists without its receipt."""
        run_id = add_run(db)
        admit(db, run_id)
        before = len(record.get_all_transitions(db))

        def explode(*a, **kw):
            raise RuntimeError("receipt write failed")

        monkeypatch.setattr(tg.rl, "append_receipt_on_connection", explode)
        with pytest.raises(RuntimeError):
            tg.transition_with_receipt(db, "fam_a", "candidate", justifying_run_id=run_id,
                                       actor="rohith", reason="r", family_meta=EDGE,
                                       thresholds=THRESHOLDS)
        assert len(record.get_all_transitions(db)) == before
        assert record.derive_state_at(db, "fam_a")[0] is None


# ---------------------------------------------------------------------------
# Concurrency regression (PR-8 review correction)
#
# The architecture audit found a TOCTOU race: the state was derived BEFORE
# BEGIN IMMEDIATE, so two concurrent transitioners both read 'discovery', both
# validated against it, and both wrote — leaving a permanently inconsistent
# chain in an append-only table. TG4 detected it afterwards; nothing could
# repair it.
#
# Fixed by deriving the authoritative state ON the locked connection, verbatim
# contract_lifecycle.next_version()'s contract (PR-3). These tests use REAL
# threads and a real SQLite lock — a mocked test would prove nothing here.
# ---------------------------------------------------------------------------

class TestConcurrency:
    def _race(self, db, fn, n=2):
        import threading
        barrier = threading.Barrier(n)
        out = []

        def go(i):
            barrier.wait()          # maximise the overlap
            try:
                out.append(("ok", fn(i)))
            except Exception as exc:
                out.append(("refused", type(exc).__name__))

        threads = [threading.Thread(target=go, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return out

    def test_concurrent_identical_transitions_do_not_corrupt_the_chain(self, db):
        """THE regression. Two writers, same family, same arc, at once."""
        runs = []
        for _ in range(2):
            r = add_run(db)
            admit(db, r)
            runs.append(r)

        results = self._race(db, lambda i: tg.transition_with_receipt(
            db, "fam_a", "candidate", justifying_run_id=runs[i], actor="rohith",
            reason="concurrent", family_meta=EDGE, thresholds=THRESHOLDS)["from_state"])

        transitions = record.get_all_transitions(db)
        assert len(transitions) == 2, "both writers should be serialised, not lost"

        # The second writer must have SEEN the first: its from_state is
        # 'candidate' (a legal re-pin), never a second 'discovery' -> 'candidate'.
        from_states = sorted(t["from_state"] for t in transitions)
        assert from_states == ["candidate", "discovery"], (
            f"chain corrupted: {from_states} — the second writer read a stale state")

        # And the ledger is internally consistent, which is what the audit's
        # reproduction failed.
        assert tg.validate_transition_governance(db) == []
        assert rl.verify_chain(db).ok

    def test_concurrent_transitions_each_get_exactly_one_receipt(self, db):
        runs = []
        for _ in range(2):
            r = add_run(db)
            admit(db, r)
            runs.append(r)
        self._race(db, lambda i: tg.transition_with_receipt(
            db, "fam_a", "candidate", justifying_run_id=runs[i], actor="rohith",
            reason="concurrent", family_meta=EDGE, thresholds=THRESHOLDS))
        for t in record.get_all_transitions(db):
            assert tg.get_receipt_for_transition(db, "fam_a", t["id"]) is not None

    def test_a_concurrent_move_makes_a_promotion_refuse_not_reclassify(self, db):
        """A promotion validated as candidate->validated must never land inside
        the lock as a legal validated->validated re-pin and be recorded as a
        family.transition. Refusing is the only correct answer."""
        candidate(db)
        r1, r2 = forward_run(db), forward_run(db)
        admit(db, r1)
        admit(db, r2)

        results = self._race(db, lambda i: tg.promote_with_receipt(
            db, "fam_a", "validated", justifying_run_id=[r1, r2][i], actor="rohith",
            reason="forward confirms", family_meta=EDGE, thresholds=THRESHOLDS))

        outcomes = sorted(o[0] for o in results)
        assert outcomes == ["ok", "refused"], f"expected one to refuse, got {results}"
        promos = tg.get_promotions(db, "fam_a")
        assert len(promos) == 1, "exactly one promotion may be recorded"
        assert record.derive_state_at(db, "fam_a")[0] == "validated"
        assert tg.validate_transition_governance(db) == []

    def test_serialised_writers_produce_a_valid_chain_under_load(self, db):
        """Five concurrent writers; whatever interleaving occurs, the ledger must
        end internally consistent."""
        runs = []
        for _ in range(5):
            r = add_run(db)
            admit(db, r)
            runs.append(r)
        self._race(db, lambda i: tg.transition_with_receipt(
            db, "fam_a", "candidate", justifying_run_id=runs[i], actor="rohith",
            reason="concurrent", family_meta=EDGE, thresholds=THRESHOLDS), n=5)

        assert tg.validate_transition_governance(db) == []
        assert rl.verify_chain(db).ok
        # exactly one 'discovery' start; every subsequent write saw its predecessor
        starts = [t for t in record.get_all_transitions(db) if t["from_state"] == "discovery"]
        assert len(starts) == 1

    def test_the_authoritative_read_happens_under_the_lock(self):
        """Structural guard: BEGIN IMMEDIATE must precede the authoritative state
        derivation inside transition_with_receipt(). Asserted by AST, so this
        cannot silently regress back into a TOCTOU."""
        import ast
        src = (ROOT / "research" / "evidence_library" / "transition_governance.py").read_text(
            encoding="utf-8")
        fn = next(n for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.FunctionDef) and n.name == "transition_with_receipt")
        begin_line = derive_line = write_line = None
        for node in ast.walk(fn):
            if isinstance(node, ast.Constant) and node.value == "BEGIN IMMEDIATE":
                begin_line = node.lineno
            if isinstance(node, ast.Call):
                name = getattr(node.func, "attr", None)
                if name == "derive_state_on_connection":
                    derive_line = node.lineno
                elif name == "append_transition_on_connection":
                    write_line = node.lineno
        assert begin_line and derive_line and write_line
        assert begin_line < derive_line < write_line, (
            "the authoritative state derivation must happen AFTER BEGIN IMMEDIATE "
            "and BEFORE the write")

    def test_transition_with_receipt_never_derives_state_off_the_lock(self):
        """It must not call the connection-less record.derive_state_at()."""
        import ast
        src = (ROOT / "research" / "evidence_library" / "transition_governance.py").read_text(
            encoding="utf-8")
        fn = next(n for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.FunctionDef) and n.name == "transition_with_receipt")
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "derive_state_at":
                pytest.fail("transition_with_receipt() derives state off the write lock")


# ---------------------------------------------------------------------------
# The real ledger
# ---------------------------------------------------------------------------

REAL_LEDGER = ROOT / "research" / "evidence_library" / "ledger.db"


@pytest.mark.skipif(not REAL_LEDGER.exists(), reason="real ledger not present")
class TestRealLedger:
    def test_every_real_transition_is_governed(self):
        errors = tg.validate_transition_governance(REAL_LEDGER)
        assert errors == [], f"real ledger has ungoverned transitions: {errors[:5]}"

    def test_real_receipt_chain_is_valid(self):
        assert rl.verify_chain(REAL_LEDGER).ok

    def test_the_two_derivations_agree_on_the_real_ledger(self):
        """state_transitions and the receipt chain are two independent records of
        the same 13 decisions. They must agree on state AND pinned run."""
        assert tg.reconstruct_all_states_at(REAL_LEDGER) == record.derive_all_states_at(REAL_LEDGER)
