"""
Tests for the point-in-time substrate (PR-5 of
docs/governance/evidence_reporting_architecture.md §7): derive_state_at(),
derive_all_states_at(), the justifying_run_id requirement, and the re-pin
(X -> X self-transition) mechanism -- the one state-machine touch in this
design.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research" / "evidence_library"))

import record
import lifecycle


@pytest.fixture
def db_path(tmp_path) -> Path:
    p = tmp_path / "ledger_test.db"
    record.init_db(p)
    return p


def _run(db, family_id="fam_a", provenance="offline-corpus", run_kind="discovery", **metrics):
    return record.append_run(db, family_id, metrics, provenance, run_kind, n_trials=34)


# ---------------------------------------------------------------------------
# derive_state_at / derive_all_states_at
# ---------------------------------------------------------------------------

class TestDeriveStateAt:
    def test_no_transitions_yields_none(self, db_path):
        state, pinned, ts = record.derive_state_at(db_path, "unknown_fam")
        assert (state, pinned, ts) == (None, None, None)

    def test_single_transition(self, db_path):
        r1 = _run(db_path)
        record.append_transition(db_path, "fam_a", "discovery", "candidate", reason="r", justifying_run_id=r1)
        state, pinned, ts = record.derive_state_at(db_path, "fam_a")
        assert state == "candidate"
        assert pinned == r1

    def test_latest_of_multiple_transitions_wins(self, db_path):
        r1 = _run(db_path)
        record.append_transition(db_path, "fam_a", "discovery", "candidate", reason="r", justifying_run_id=r1,
                                  ts="2026-06-13T00:00:00+00:00")
        record.append_transition(db_path, "fam_a", "candidate", "rejected", reason="r2",
                                  ts="2026-06-20T00:00:00+00:00")
        state, pinned, ts = record.derive_state_at(db_path, "fam_a")
        assert state == "rejected"

    def test_as_of_reconstructs_historical_state(self, db_path):
        r1 = _run(db_path)
        record.append_transition(db_path, "fam_a", "discovery", "candidate", reason="r", justifying_run_id=r1,
                                  ts="2026-06-13T00:00:00+00:00")
        record.append_transition(db_path, "fam_a", "candidate", "validated", reason="promoted",
                                  justifying_run_id=r1, ts="2026-06-20T00:00:00+00:00")

        past = record.derive_state_at(db_path, "fam_a", as_of="2026-06-15T00:00:00+00:00")
        assert past[0] == "candidate"

        present = record.derive_state_at(db_path, "fam_a", as_of="2026-06-25T00:00:00+00:00")
        assert present[0] == "validated"

    def test_as_of_before_any_transition_yields_none(self, db_path):
        r1 = _run(db_path)
        record.append_transition(db_path, "fam_a", "discovery", "candidate", reason="r", justifying_run_id=r1,
                                  ts="2026-06-13T00:00:00+00:00")
        state, pinned, ts = record.derive_state_at(db_path, "fam_a", as_of="2026-06-01T00:00:00+00:00")
        assert state is None


class TestDeriveAllStatesAt:
    def test_multiple_families(self, db_path):
        r1 = _run(db_path, "fam_a")
        r2 = _run(db_path, "fam_b")
        record.append_transition(db_path, "fam_a", "discovery", "candidate", reason="r", justifying_run_id=r1)
        record.append_transition(db_path, "fam_b", "discovery", "disqualified", reason="dq", justifying_run_id=r2)
        states = record.derive_all_states_at(db_path)
        assert states["fam_a"][0] == "candidate"
        assert states["fam_b"][0] == "disqualified"

    def test_family_with_no_transitions_absent(self, db_path):
        _run(db_path, "fam_a")
        states = record.derive_all_states_at(db_path)
        assert "fam_a" not in states  # no transition ever recorded for it

    def test_as_of_over_real_13_transition_shape(self, db_path):
        """
        Mirrors the real ledger's shape: 11 bootstrap transitions at
        2026-06-13T00:00:00, one at 2026-06-21 (IVRV), one at 2026-06-25
        (ATR). Reconstructing as of 2026-06-14 must show only the bootstrap
        cohort; as of 2026-06-22 must additionally show IVRV; as of now
        must show all 13.
        """
        for i in range(11):
            r = _run(db_path, f"boot_{i}")
            record.append_transition(
                db_path, f"boot_{i}", "discovery", "candidate", reason="bootstrap",
                justifying_run_id=r, ts="2026-06-13T00:00:00+00:00", actor="bootstrap",
            )
        r_ivrv = _run(db_path, "ivrv_vrp_spread")
        record.append_transition(db_path, "ivrv_vrp_spread", "discovery", "candidate", reason="ivrv",
                                  justifying_run_id=r_ivrv, ts="2026-06-21T14:08:41+00:00")
        r_atr = _run(db_path, "g3_atr_expansion")
        record.append_transition(db_path, "g3_atr_expansion", "discovery", "candidate", reason="atr",
                                  justifying_run_id=r_atr, ts="2026-06-25T19:01:26+00:00")

        as_of_14 = record.derive_all_states_at(db_path, as_of="2026-06-14T23:59:59+00:00")
        assert len(as_of_14) == 11
        assert "ivrv_vrp_spread" not in as_of_14

        as_of_22 = record.derive_all_states_at(db_path, as_of="2026-06-22T00:00:00+00:00")
        assert len(as_of_22) == 12
        assert "ivrv_vrp_spread" in as_of_22
        assert "g3_atr_expansion" not in as_of_22

        as_of_now = record.derive_all_states_at(db_path)
        assert len(as_of_now) == 13


# ---------------------------------------------------------------------------
# justifying_run_id requirement (append_transition)
# ---------------------------------------------------------------------------

class TestJustifyingRunIdRequirement:
    def test_transition_into_candidate_requires_justifying_run_id(self, db_path):
        with pytest.raises(ValueError, match="justifying_run_id"):
            record.append_transition(db_path, "fam_a", "discovery", "candidate", reason="r")

    def test_transition_into_validated_requires_justifying_run_id(self, db_path):
        r1 = _run(db_path)
        record.append_transition(db_path, "fam_a", "discovery", "candidate", reason="r", justifying_run_id=r1)
        with pytest.raises(ValueError, match="justifying_run_id"):
            record.append_transition(db_path, "fam_a", "candidate", "validated", reason="r")

    def test_transition_into_disqualified_does_not_require_run(self, db_path):
        # disqualified is not in STATES_REQUIRING_RUN
        record.append_transition(db_path, "fam_a", "discovery", "disqualified", reason="dq")

    def test_grandfathered_bootstrap_row_allowed(self, db_path):
        # ts < cutoff (2026-07-01) with no justifying_run_id is grandfathered.
        record.append_transition(
            db_path, "fam_a", "discovery", "candidate", reason="bootstrap",
            ts="2026-06-13T00:00:00+00:00", actor="bootstrap",
        )
        state, pinned, _ts = record.derive_state_at(db_path, "fam_a")
        assert state == "candidate"
        assert pinned is None

    def test_not_grandfathered_after_cutoff(self, db_path):
        with pytest.raises(ValueError, match="justifying_run_id"):
            record.append_transition(
                db_path, "fam_a", "discovery", "candidate", reason="late",
                ts="2026-07-05T00:00:00+00:00",
            )


# ---------------------------------------------------------------------------
# Re-pin: X -> X self-transition
# ---------------------------------------------------------------------------

class TestRepin:
    def test_can_transition_allows_self_loop(self):
        assert lifecycle.can_transition("candidate", "candidate") is True
        assert lifecycle.can_transition("validated", "validated") is True
        assert lifecycle.can_transition("discovery", "discovery") is True

    def test_self_loop_does_not_add_a_new_arc_between_distinct_states(self):
        # The pre-existing graph between DISTINCT states is untouched.
        assert lifecycle.can_transition("retired", "candidate") is False
        assert lifecycle.can_transition("disqualified", "candidate") is False
        assert lifecycle.can_transition("rejected", "validated") is False

    def test_repin_requires_justifying_run_id(self, db_path):
        r1 = _run(db_path)
        record.append_transition(db_path, "fam_a", "discovery", "candidate", reason="r", justifying_run_id=r1)
        with pytest.raises(ValueError, match="re-pin"):
            record.append_transition(db_path, "fam_a", "candidate", "candidate", reason="re-pin attempt")

    def test_repin_recorded_and_reconstructible(self, db_path):
        r1 = _run(db_path, run_kind="discovery")
        record.append_transition(db_path, "fam_a", "discovery", "candidate", reason="r", justifying_run_id=r1,
                                  ts="2026-06-13T00:00:00+00:00")
        r2 = _run(db_path, provenance="live-forward", run_kind="forward")
        record.append_transition(
            db_path, "fam_a", "candidate", "candidate", reason="re-pin to forward evidence",
            justifying_run_id=r2, ts="2026-06-20T00:00:00+00:00",
        )

        before = record.derive_state_at(db_path, "fam_a", as_of="2026-06-15T00:00:00+00:00")
        assert before == ("candidate", r1, "2026-06-13T00:00:00+00:00")

        after = record.derive_state_at(db_path, "fam_a", as_of="2026-06-25T00:00:00+00:00")
        assert after[0] == "candidate"
        assert after[1] == r2  # the re-pin took effect

    def test_every_other_arc_unchanged_after_repin_extension(self):
        # Spot-check the existing lifecycle graph is untouched.
        assert lifecycle.can_transition("discovery", "candidate") is True
        assert lifecycle.can_transition("candidate", "validated") is True
        assert lifecycle.can_transition("validated", "rejected") is True
        assert lifecycle.can_transition("candidate", "disqualified") is True
        assert lifecycle.can_transition("promotion_ready", "production_ready") is True
        assert lifecycle.can_transition("production_ready", "validated") is True
        assert not lifecycle.can_transition("retired", "candidate")
