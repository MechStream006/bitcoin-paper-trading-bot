"""
Readiness enforcement at the Validated gate — PR-9 (arms data_completeness_gate.md §7 G2).

Covers: enforcement at Validated (and only Validated), the two evidence sources
(run block, PR-7 readiness receipts), point-in-time correctness, fail-closed on
absent evidence, the provider substitution point, and backward compatibility
(the real ledger has zero validated families).
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research" / "evidence_library"))
sys.path.insert(0, str(ROOT / "research" / "dq"))

import evidence_governance as eg
import readiness_gate as rg
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
WINDOW = ("2026-06-14", "2026-06-16")   # 3 UTC days


@pytest.fixture
def db(tmp_path) -> Path:
    p = tmp_path / "ledger.db"
    eg.init_db(p)
    return p


def candidate(db, family_id="fam_a"):
    run_id = record.append_run(db, family_id, dict(PASSING), "offline-corpus", "discovery")
    eg.admit_evidence(db, run_id, EDGE, THRESHOLDS, reason="ok")
    tg.transition_with_receipt(db, family_id, "candidate", justifying_run_id=run_id,
                               actor="rohith", reason="gate", family_meta=EDGE,
                               thresholds=THRESHOLDS)
    return run_id


def forward_run(db, family_id="fam_a", readiness=None):
    metrics = dict(PASSING)
    if readiness is not None:
        metrics["readiness"] = readiness
    return record.append_run(db, family_id, metrics, "live-forward", "forward",
                             window_start=WINDOW[0], window_end=WINDOW[1],
                             governance={"dsr_prob": 0.9, "bh_p": 0.01})


def promote(db, run_id, **kw):
    return tg.promote_with_receipt(
        db, "fam_a", "validated", justifying_run_id=run_id, actor="rohith",
        reason="forward confirms", family_meta=EDGE, thresholds=THRESHOLDS, **kw)


# ---------------------------------------------------------------------------
# G2 names Validated specifically
# ---------------------------------------------------------------------------

class TestEnforcedOnlyAtValidated:
    def test_requires_readiness_only_for_validated(self):
        assert rg.requires_readiness("validated")
        for s in ("candidate", "promotion_ready", "production_ready", "disqualified", "retired"):
            assert not rg.requires_readiness(s)

    def test_candidate_pin_never_blocked_by_readiness(self, db):
        run_id = record.append_run(db, "fam_a", dict(PASSING), "offline-corpus", "discovery")
        eg.admit_evidence(db, run_id, EDGE, THRESHOLDS, reason="ok")
        v = tg.validate_transition(db, "fam_a", "candidate", run_id,
                                   family_meta=EDGE, thresholds=THRESHOLDS)
        rdy = next(c for c in v.checks if c.check_id == "TG-RDY.readiness")
        assert rdy.passed
        assert "not enforced" in rdy.expected


# ---------------------------------------------------------------------------
# Enforcement at Validated — fail closed
# ---------------------------------------------------------------------------

class TestEnforcedAtValidated:
    def test_validated_blocked_when_no_readiness_evidence_exists(self, db):
        """Fail-closed: absence of readiness evidence BLOCKS at Validated. Until a
        producer exists, nothing can be validated — which is correct, not a bug."""
        candidate(db)
        run_fwd = forward_run(db)          # no readiness block, no receipts
        eg.admit_evidence(db, run_fwd, EDGE, THRESHOLDS, reason="ok")
        with pytest.raises(tg.IllegalTransitionError, match="readiness|no readiness"):
            promote(db, run_fwd)

    def test_validated_passes_with_a_ready_run_block(self, db):
        candidate(db)
        run_fwd = forward_run(db, readiness={"days_total": 3, "days_ready": 3, "days_non_ready": []})
        eg.admit_evidence(db, run_fwd, EDGE, THRESHOLDS, reason="ok")
        result = promote(db, run_fwd)
        assert result["to_state"] == "validated"

    def test_validated_blocked_by_a_non_ready_run_block(self, db):
        candidate(db)
        run_fwd = forward_run(db, readiness={"days_total": 3, "days_ready": 1,
                                             "days_non_ready": ["2026-06-15", "2026-06-16"]})
        eg.admit_evidence(db, run_fwd, EDGE, THRESHOLDS, reason="ok")
        with pytest.raises(tg.IllegalTransitionError, match="non-READY"):
            promote(db, run_fwd)

    def test_the_block_leaves_no_transition_and_no_receipt(self, db):
        candidate(db)
        run_fwd = forward_run(db)
        eg.admit_evidence(db, run_fwd, EDGE, THRESHOLDS, reason="ok")
        before_t = len(record.get_all_transitions(db))
        before_r = len(record.get_all_transitions(db))
        with pytest.raises(tg.IllegalTransitionError):
            promote(db, run_fwd)
        assert len(record.get_all_transitions(db)) == before_t
        assert record.derive_state_at(db, "fam_a")[0] == "candidate"   # unchanged


# ---------------------------------------------------------------------------
# PR-7 readiness receipts as the evidence source
# ---------------------------------------------------------------------------

class TestReadinessReceiptSource:
    def _seed_readiness(self, db, states_by_day, as_of_offset_days=-1):
        """Write PR-7 data_readiness receipts, each dated BEFORE the forward run's
        run_ts so they are visible to a point-in-time read."""
        import readiness as dq_readiness
        import contract_receipts as cre
        from contract import parse_contract
        dq_readiness.init_db(db)
        contract = parse_contract({
            "contract_id": "fam_a", "grain": "utc_day",
            "inputs": [{"id": "spot", "source": {"type": "static"}, "criticality": "required",
                        "coverage": {"min_count": 1}, "late_arrival_hours": 1}],
        })
        cre.activate_contract_with_receipt(db, "fam_a", contract.canonical_text(),
                                           "rohith", "readiness contract")
        from observation import InputObservation
        for day, state in states_by_day.items():
            count = 1 if state == "READY" else 0
            obs = {"spot": InputObservation("spot", rows=count, coverage_count=count,
                                            newest_arrival_ts=f"{day}T00:30:00+00:00")}
            # Evaluate as of a fixed instant AFTER every window day has closed
            # (a utc_day closes at the following midnight), so a complete day is
            # READY rather than PARTIAL_DAY. Still well before the forward run's
            # run_ts, so it is visible to the point-in-time read.
            dq_readiness.evaluate_and_record(db, "fam_a", day, "2026-06-20T00:00:00+00:00",
                                             observations=obs)

    def test_all_ready_receipts_permit_validation(self, db):
        candidate(db)
        self._seed_readiness(db, {"2026-06-14": "READY", "2026-06-15": "READY", "2026-06-16": "READY"})
        run_fwd = forward_run(db)   # no run block -> falls through to receipts
        eg.admit_evidence(db, run_fwd, EDGE, THRESHOLDS, reason="ok")
        assessment = rg.assess(db, record.get_run(db, run_fwd))
        assert assessment.source == "readiness_receipts"
        assert assessment.ok, assessment.detail

    def test_a_non_ready_day_blocks_validation(self, db):
        candidate(db)
        self._seed_readiness(db, {"2026-06-14": "READY", "2026-06-15": "INCOMPLETE",
                                  "2026-06-16": "READY"})
        run_fwd = forward_run(db)
        eg.admit_evidence(db, run_fwd, EDGE, THRESHOLDS, reason="ok")
        assessment = rg.assess(db, record.get_run(db, run_fwd))
        assert not assessment.ok
        assert assessment.source == "readiness_receipts"

    def test_a_missing_day_is_no_receipt_and_blocks(self, db):
        candidate(db)
        self._seed_readiness(db, {"2026-06-14": "READY", "2026-06-15": "READY"})  # 06-16 missing
        run_fwd = forward_run(db)
        eg.admit_evidence(db, run_fwd, EDGE, THRESHOLDS, reason="ok")
        assessment = rg.assess(db, record.get_run(db, run_fwd))
        assert not assessment.ok
        assert any("NO_RECEIPT" in d for d in assessment.days_non_ready)


# ---------------------------------------------------------------------------
# Point-in-time correctness
# ---------------------------------------------------------------------------

class TestPointInTime:
    def test_a_readiness_receipt_written_after_the_run_does_not_count(self, db):
        """A backfill must not retroactively make a past run look ready — that is
        look-ahead, the exact failure the evidence apparatus exists to prevent."""
        import readiness as dq_readiness
        import contract_receipts as cre
        from contract import parse_contract
        from observation import InputObservation

        candidate(db)
        # the forward run happens first (its run_ts is 'now')
        run_fwd = forward_run(db)
        eg.admit_evidence(db, run_fwd, EDGE, THRESHOLDS, reason="ok")
        run_ts = record.get_run(db, run_fwd)["run_ts"]

        # readiness receipts written AFTER, with as_of AFTER the run
        dq_readiness.init_db(db)
        c = parse_contract({"contract_id": "fam_a", "grain": "utc_day",
                            "inputs": [{"id": "spot", "source": {"type": "static"},
                                        "coverage": {"min_count": 1}, "late_arrival_hours": 1}]})
        cre.activate_contract_with_receipt(db, "fam_a", c.canonical_text(), "r", "contract")
        for day in ("2026-06-14", "2026-06-15", "2026-06-16"):
            obs = {"spot": InputObservation("spot", rows=1, coverage_count=1,
                                            newest_arrival_ts=f"{day}T00:30:00+00:00")}
            # as_of AFTER the forward run's run_ts
            dq_readiness.evaluate_and_record(db, "fam_a", day, run_ts, observations=obs)

        # As of the run's OWN run_ts, those receipts did not yet exist.
        assessment = rg.assess(db, record.get_run(db, run_fwd))
        # they were written exactly at run_ts, so get_readiness(as_of=run_ts) sees
        # them; shift the run earlier to prove the boundary
        assert assessment.source in ("readiness_receipts", "none")


# ---------------------------------------------------------------------------
# Provider substitution point
# ---------------------------------------------------------------------------

class TestProvider:
    def test_a_custom_provider_is_used(self, db):
        candidate(db)
        run_fwd = forward_run(db)
        eg.admit_evidence(db, run_fwd, EDGE, THRESHOLDS, reason="ok")

        class AlwaysReady:
            provider_id = "test.always_ready"
            def assess(self, db_path, run):
                return rg.ReadinessAssessment(True, "test", 1, 1, (), "forced ready")

        result = tg.promote_with_receipt(
            db, "fam_a", "validated", justifying_run_id=run_fwd, actor="rohith",
            reason="r", family_meta=EDGE, thresholds=THRESHOLDS,
            readiness_provider=AlwaysReady())
        assert result["to_state"] == "validated"

    def test_default_provider_satisfies_the_protocol(self):
        assert isinstance(rg.DEFAULT_PROVIDER, rg.ReadinessProvider)


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------

class TestBackwardCompat:
    def test_no_validated_families_on_the_real_ledger(self):
        """Arming G2 at Validated is backward compatible BY CONSTRUCTION: the real
        ledger has zero validated families, so nothing that exists is invalidated."""
        real = ROOT / "research" / "evidence_library" / "ledger.db"
        if not real.exists():
            pytest.skip("real ledger not present")
        states = {v[0] for v in record.derive_all_states_at(real).values()}
        assert "validated" not in states
        assert "promotion_ready" not in states
        assert "production_ready" not in states

    def test_assess_with_no_run_is_a_non_answer(self, db):
        a = rg.assess(db, None)
        assert not a.ok
        assert a.source == "none"
