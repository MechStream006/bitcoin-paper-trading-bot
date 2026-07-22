"""Proposal 1 — Validated family independence enforcement (CLAUDE.md §16).

Enforces the ALREADY-FROZEN `max_pairwise_r = 0.70`, which had been declared
in thresholds.yaml since 2026-06-13 and enforced nowhere. No new governance
rule is introduced by this module or these tests.

Test structure mirrors the enforcement topology:
  * storage        — append-only table, canonical ordering, write validation
  * gate logic     — check_validated_independence() in isolation
  * registry gate  — report.py --check path (validate_registry)
  * promotion gate — the path that actually blocks a promotion
  * real ledger    — the live ledger must remain clean

Run:
    conda run -n btc_bot pytest tests/test_pairwise_independence.py -v
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EVL = ROOT / "research" / "evidence_library"
sys.path.insert(0, str(EVL))

import record            # noqa: E402
import lifecycle         # noqa: E402
import transition_governance as tg   # noqa: E402
import evidence_governance as eg   # noqa: E402

# Mirrors tests/test_transition_governance.py's fixture so a family can be
# taken through the real governed front door (admit -> candidate -> promote);
# only the pairwise dimension varies between these tests.
PASSING = {"sharpe": 0.9, "oos_sharpe": 0.4, "sens_pass_frac": 1.0, "wf_pass_frac": 0.8,
           "loo_sign_consistency": 1.0, "breakeven_cost_bps": 100, "struct_sign_ok": True,
           "n_trades": 50}

THRESHOLDS = {
    "candidate": {
        "hard_dq": {"knife_edge_sensitivity": 0.30},
        "edge": {"min_sharpe": 0.30, "min_wf_agreement": 0.60, "min_sensitivity": 0.60,
                 "min_loo_consistency": 0.60, "min_breakeven_bps": 10},
        "feature": {"min_sensitivity": 0.60},
    },
    "validated": {
        "edge":    {"max_pairwise_r": 0.70, "requires_non_offline_provenance": True,
                    "min_dsr_prob": 0.50, "max_bh_p": 0.10},
        "feature": {"max_pairwise_r": 0.70, "requires_non_offline_provenance": True,
                    "min_dsr_prob": 0.50, "max_bh_p": 0.10},
        "strict":  {"max_pairwise_r": 0.30},
    }
}


@pytest.fixture
def ledger(tmp_path):
    db = tmp_path / "ledger.db"
    eg.init_db(db)          # creates runs/transitions/receipts AND pairwise_r
    return db


def _meta(*roles):
    return {"role": list(roles) or ["edge"]}


# ---------------------------------------------------------------------------
# Storage layer
# ---------------------------------------------------------------------------

class TestPairwiseStorage:
    def test_append_and_read_back(self, ledger):
        record.append_pairwise_r(ledger, "A", "B", 0.42, "pearson_daily_returns")
        rows = record.get_pairwise_r(ledger)
        assert rows[("A", "B")]["r"] == 0.42
        assert rows[("A", "B")]["method"] == "pearson_daily_returns"

    def test_unsorted_pair_is_refused(self, ledger):
        """Canonical ordering is what makes a pair have exactly one row."""
        with pytest.raises(ValueError, match="must sort before"):
            record.append_pairwise_r(ledger, "Z", "A", 0.1, "pearson")

    def test_self_pair_is_refused(self, ledger):
        with pytest.raises(ValueError, match="correlation with itself"):
            record.append_pairwise_r(ledger, "A", "A", 1.0, "pearson")

    def test_nan_is_refused_at_write_time(self, ledger):
        """A NaN correlation is a failed computation, not a measurement."""
        with pytest.raises(ValueError, match="real number"):
            record.append_pairwise_r(ledger, "A", "B", float("nan"), "pearson")

    @pytest.mark.parametrize("bad", [1.5, -1.5, 2.0])
    def test_out_of_range_r_refused(self, ledger, bad):
        with pytest.raises(ValueError, match=r"\[-1, 1\]"):
            record.append_pairwise_r(ledger, "A", "B", bad, "pearson")

    def test_method_is_mandatory(self, ledger):
        with pytest.raises(ValueError, match="method is required"):
            record.append_pairwise_r(ledger, "A", "B", 0.1, "   ")

    def test_db_check_constraint_blocks_direct_unsorted_insert(self, ledger):
        """Defence in depth: the guarantee must not depend on the Python API."""
        con = sqlite3.connect(str(ledger))
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO pairwise_r (ts, family_id_a, family_id_b, r, method) "
                "VALUES ('2026-07-19T00:00:00Z','Z','A',0.1,'pearson')"
            )
        con.close()

    def test_table_is_append_only_update_blocked(self, ledger):
        record.append_pairwise_r(ledger, "A", "B", 0.42, "pearson")
        con = sqlite3.connect(str(ledger))
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            con.execute("UPDATE pairwise_r SET r = 0.01")
        con.close()

    def test_table_is_append_only_delete_blocked(self, ledger):
        record.append_pairwise_r(ledger, "A", "B", 0.42, "pearson")
        con = sqlite3.connect(str(ledger))
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            con.execute("DELETE FROM pairwise_r")
        con.close()

    def test_remeasurement_appends_and_latest_wins(self, ledger):
        record.append_pairwise_r(ledger, "A", "B", 0.9, "v1", ts="2026-01-01T00:00:00Z")
        record.append_pairwise_r(ledger, "A", "B", 0.2, "v2", ts="2026-06-01T00:00:00Z")
        assert record.get_pairwise_r(ledger)[("A", "B")]["r"] == 0.2

    def test_as_of_returns_historical_measurement(self, ledger):
        """Replay safety: a historical check sees the evidence of its time."""
        record.append_pairwise_r(ledger, "A", "B", 0.9, "v1", ts="2026-01-01T00:00:00Z")
        record.append_pairwise_r(ledger, "A", "B", 0.2, "v2", ts="2026-06-01T00:00:00Z")
        rows = record.get_pairwise_r(ledger, as_of="2026-03-01T00:00:00Z")
        assert rows[("A", "B")]["r"] == 0.9

    def test_same_ts_resolves_deterministically_by_id(self, ledger):
        ts = "2026-01-01T00:00:00Z"
        record.append_pairwise_r(ledger, "A", "B", 0.9, "first", ts=ts)
        record.append_pairwise_r(ledger, "A", "B", 0.1, "second", ts=ts)
        for _ in range(5):
            assert record.get_pairwise_r(ledger)[("A", "B")]["method"] == "second"

    def test_missing_table_returns_empty_not_error(self, tmp_path):
        """A ledger predating Proposal 1 must not crash the readers."""
        db = tmp_path / "old.db"
        sqlite3.connect(str(db)).close()
        assert record.get_pairwise_r(db) == {}


# ---------------------------------------------------------------------------
# Gate logic in isolation
# ---------------------------------------------------------------------------

class TestIndependenceGate:
    def test_independent_family_passes(self, ledger):
        record.append_pairwise_r(ledger, "A", "B", 0.31, "pearson")
        ok, errs = lifecycle.check_validated_independence(
            "B", _meta("edge"), THRESHOLDS, record.get_pairwise_r(ledger), {"A"})
        assert ok and errs == []

    def test_correlated_family_is_blocked(self, ledger):
        record.append_pairwise_r(ledger, "A", "B", 0.85, "pearson")
        ok, errs = lifecycle.check_validated_independence(
            "B", _meta("edge"), THRESHOLDS, record.get_pairwise_r(ledger), {"A"})
        assert not ok
        assert "0.8500" in errs[0] and "max_pairwise_r=0.7" in errs[0]

    def test_strong_negative_correlation_is_blocked(self, ledger):
        """|r| is the comparison. r = -0.95 is a near-perfect inverse duplicate;
        comparing raw r would admit it, defeating the threshold's purpose."""
        record.append_pairwise_r(ledger, "A", "B", -0.95, "pearson")
        ok, errs = lifecycle.check_validated_independence(
            "B", _meta("edge"), THRESHOLDS, record.get_pairwise_r(ledger), {"A"})
        assert not ok
        assert "0.9500" in errs[0]

    def test_missing_measurement_fails_closed(self, ledger):
        """The property that makes this enforcement rather than decoration:
        a rule that applies only when data is volunteered can always be
        satisfied by volunteering nothing. Mirrors R1's NULL discipline."""
        ok, errs = lifecycle.check_validated_independence(
            "B", _meta("edge"), THRESHOLDS, {}, {"A"})
        assert not ok
        assert "no pairwise correlation recorded" in errs[0]

    def test_partial_coverage_fails_on_the_missing_peer_only(self, ledger):
        record.append_pairwise_r(ledger, "A", "C", 0.1, "pearson")
        ok, errs = lifecycle.check_validated_independence(
            "C", _meta("edge"), THRESHOLDS, record.get_pairwise_r(ledger), {"A", "B"})
        assert not ok
        assert len(errs) == 1 and "'B'" in errs[0]

    def test_no_validated_peers_passes_vacuously(self, ledger):
        """The first family to reach Validated has nothing to be correlated
        with -- exactly today's real state (zero Validated families)."""
        ok, errs = lifecycle.check_validated_independence(
            "A", _meta("edge"), THRESHOLDS, {}, set())
        assert ok and errs == []

    def test_self_is_never_compared(self, ledger):
        ok, errs = lifecycle.check_validated_independence(
            "A", _meta("edge"), THRESHOLDS, {}, {"A"})
        assert ok and errs == []

    def test_pair_found_regardless_of_argument_order(self, ledger):
        record.append_pairwise_r(ledger, "A", "B", 0.85, "pearson")
        rows = record.get_pairwise_r(ledger)
        for me, peer in (("A", "B"), ("B", "A")):
            ok, _ = lifecycle.check_validated_independence(
                me, _meta("edge"), THRESHOLDS, rows, {peer})
            assert not ok, f"lookup failed for {me} vs {peer}"

    def test_boundary_exactly_at_threshold_passes(self, ledger):
        record.append_pairwise_r(ledger, "A", "B", 0.70, "pearson")
        ok, _ = lifecycle.check_validated_independence(
            "B", _meta("edge"), THRESHOLDS, record.get_pairwise_r(ledger), {"A"})
        assert ok, "max_pairwise_r is a ceiling: r == threshold must pass"

    def test_multi_role_uses_most_conservative_ceiling(self, ledger):
        record.append_pairwise_r(ledger, "A", "B", 0.50, "pearson")
        rows = record.get_pairwise_r(ledger)
        assert lifecycle.check_validated_independence(
            "B", _meta("edge"), THRESHOLDS, rows, {"A"})[0]
        ok, errs = lifecycle.check_validated_independence(
            "B", _meta("edge", "strict"), THRESHOLDS, rows, {"A"})
        assert not ok and "max_pairwise_r=0.3" in errs[0]

    def test_no_declared_ceiling_means_nothing_to_enforce(self, ledger):
        ok, errs = lifecycle.check_validated_independence(
            "B", _meta("edge"), {"validated": {"edge": {}}}, {}, {"A"})
        assert ok and errs == []

    def test_error_names_the_pair_and_the_measurement(self, ledger):
        """Audit evidence: the rejection must be explicable years later."""
        record.append_pairwise_r(ledger, "A", "B", 0.91, "pearson_daily_returns",
                                 ts="2026-07-19T00:00:00Z")
        _, errs = lifecycle.check_validated_independence(
            "B", _meta("edge"), THRESHOLDS, record.get_pairwise_r(ledger), {"A"})
        for token in ("'A'", "0.9100", "2026-07-19", "pearson_daily_returns", "row id="):
            assert token in errs[0], f"missing {token!r} in: {errs[0]}"


# ---------------------------------------------------------------------------
# Backward compatibility of check_validated_preconditions()
# ---------------------------------------------------------------------------

class TestPreconditionsWiring:
    RUN = {"provenance": "live-forward", "dsr_prob": 0.9, "bh_p": 0.01}

    def test_omitting_peers_skips_the_check(self):
        """`None` means 'caller cannot determine peers', not 'no peers'. All
        pre-Proposal-1 callers keep their exact previous behaviour."""
        ok, errs = lifecycle.check_validated_preconditions(
            _meta("edge"), self.RUN, THRESHOLDS)
        assert ok and errs == []

    def test_empty_peer_set_is_distinct_from_none(self):
        ok, _ = lifecycle.check_validated_preconditions(
            _meta("edge"), self.RUN, THRESHOLDS,
            pairwise_rows={}, validated_peers=set(), family_id="B")
        assert ok

    def test_independence_failure_surfaces_through_preconditions(self, ledger):
        record.append_pairwise_r(ledger, "A", "B", 0.99, "pearson")
        ok, errs = lifecycle.check_validated_preconditions(
            _meta("edge"), self.RUN, THRESHOLDS,
            pairwise_rows=record.get_pairwise_r(ledger),
            validated_peers={"A"}, family_id="B")
        assert not ok
        assert any("0.9900" in e for e in errs)

    def test_dsr_and_bh_checks_are_unchanged(self):
        """Regression: Proposal 1 must not alter the existing R1 enforcement."""
        ok, errs = lifecycle.check_validated_preconditions(
            _meta("edge"), {"provenance": "live-forward", "dsr_prob": None, "bh_p": 0.01},
            THRESHOLDS)
        assert not ok and any("DSR" in e for e in errs)


# ---------------------------------------------------------------------------
# Promotion gate — the requirement: block BEFORE promotion
# ---------------------------------------------------------------------------

class TestPromotionGate:
    def _seed(self, ledger, family, r_to_A=None):
        """Build a real promotable state through the governed front door:
        peer 'A' is Validated, `family` sits at candidate with an admitted
        forward run. Only the pairwise dimension varies across these tests.
        Returns {family_id: run_id}.
        """
        runs = {}

        def _forward(fid):
            m = dict(PASSING)
            m["readiness"] = {"days_total": 30, "days_ready": 30, "days_non_ready": []}
            return record.append_run(ledger, fid, m, "live-forward", "forward",
                                     governance={"dsr_prob": 0.9, "bh_p": 0.01})

        for fid in ("A", family):
            disc = record.append_run(ledger, fid, dict(PASSING), "offline-corpus", "discovery")
            eg.admit_evidence(ledger, disc, _meta("edge"), THRESHOLDS, reason="passes gate")
            tg.transition_with_receipt(ledger, fid, "candidate", justifying_run_id=disc,
                                       actor="rohith", reason="passes the edge gate",
                                       family_meta=_meta("edge"), thresholds=THRESHOLDS)
            runs[fid] = _forward(fid)
            eg.admit_evidence(ledger, runs[fid], _meta("edge"), THRESHOLDS, reason="forward ok")

        # Peer 'A' reaches Validated BEFORE the pair is recorded, so it is a
        # genuine incumbent that `family` must now be shown independent of.
        tg.promote_with_receipt(ledger, "A", "validated", runs["A"],
                                actor="rohith", reason="first validated family",
                                family_meta=_meta("edge"), thresholds=THRESHOLDS)

        if r_to_A is not None:
            a, b = sorted(["A", family])
            record.append_pairwise_r(ledger, a, b, r_to_A, "pearson_daily_returns")
        return runs

    def test_correlated_candidate_cannot_be_promoted(self, ledger):
        runs = self._seed(ledger, "B", r_to_A=0.95)
        v = tg.validate_transition(
            ledger, "B", "validated", runs["B"],
            family_meta=_meta("edge"), thresholds=THRESHOLDS)
        assert not v.ok
        pre = [c for c in v.checks if c.check_id == "TG-PRE.prerequisites"][0]
        assert not pre.passed
        assert "0.9500" in pre.observed

    def test_unmeasured_candidate_cannot_be_promoted(self, ledger):
        """Skipping the measurement must not be a way past the gate."""
        runs = self._seed(ledger, "B", r_to_A=None)
        v = tg.validate_transition(
            ledger, "B", "validated", runs["B"],
            family_meta=_meta("edge"), thresholds=THRESHOLDS)
        assert not v.ok
        pre = [c for c in v.checks if c.check_id == "TG-PRE.prerequisites"][0]
        assert "no pairwise correlation recorded" in pre.observed

    def test_independent_candidate_passes_the_independence_check(self, ledger):
        runs = self._seed(ledger, "B", r_to_A=0.15)
        v = tg.validate_transition(
            ledger, "B", "validated", runs["B"],
            family_meta=_meta("edge"), thresholds=THRESHOLDS)
        pre = [c for c in v.checks if c.check_id == "TG-PRE.prerequisites"][0]
        assert pre.passed, pre.observed
        assert "independent of 1 Validated peer" in pre.observed

    def test_promotion_is_refused_end_to_end(self, ledger):
        """The requirement, stated directly: enforce before promotion."""
        runs = self._seed(ledger, "B", r_to_A=0.95)
        with pytest.raises(Exception) as exc:
            tg.promote_with_receipt(
                ledger, "B", "validated", runs["B"],
                actor="rohith", reason="test",
                family_meta=_meta("edge"), thresholds=THRESHOLDS,
            )
        assert "0.9500" in str(exc.value) or "pairwise" in str(exc.value).lower()
        # And the ledger must be unchanged by the refusal.
        state, _, _ = record.derive_state_at(ledger, "B")
        assert state == "candidate"

    def test_peers_come_from_ledger_not_the_meta_argument(self, ledger):
        """A caller that passes no registry must not thereby skip the check."""
        runs = self._seed(ledger, "B", r_to_A=0.99)
        v = tg.validate_transition(
            ledger, "B", "validated", runs["B"],
            family_meta={"role": ["edge"]}, thresholds=THRESHOLDS)
        assert not v.ok

    def test_non_validated_destination_is_unaffected(self, ledger):
        runs = self._seed(ledger, "B", r_to_A=None)
        v = tg.validate_transition(
            ledger, "B", "rejected", runs["B"],
            family_meta=_meta("edge"), thresholds=THRESHOLDS)
        pre = [c for c in v.checks if c.check_id == "TG-PRE.prerequisites"][0]
        assert pre.passed and pre.observed == "n/a"


# ---------------------------------------------------------------------------
# Registry gate (report.py --check) and the real ledger
# ---------------------------------------------------------------------------

class TestRegistryGate:
    def test_two_correlated_validated_families_are_reported(self, ledger):
        runs = {}
        for fid in ("A", "B"):
            runs[fid] = record.append_run(
                ledger, family_id=fid, metrics={}, provenance="live-forward",
                run_kind="forward", governance={"dsr_prob": 0.9, "bh_p": 0.01})
        record.append_pairwise_r(ledger, "A", "B", 0.93, "pearson")
        registry = {
            "A": {"state": "validated", "pinned_run": runs["A"], "role": ["edge"]},
            "B": {"state": "validated", "pinned_run": runs["B"], "role": ["edge"]},
        }
        errors = lifecycle.validate_registry(registry, ledger, THRESHOLDS)
        assert any("0.9300" in e for e in errors)
        # Reported symmetrically -- both families are implicated, not just one.
        assert any(e.startswith("A:") for e in errors)
        assert any(e.startswith("B:") for e in errors)

    def test_candidate_families_are_not_subject_to_the_gate(self, ledger):
        rid = record.append_run(ledger, family_id="A", metrics={},
                                provenance="offline-corpus", run_kind="discovery")
        registry = {"A": {"state": "candidate", "pinned_run": rid, "role": ["edge"]}}
        errors = lifecycle.validate_registry(registry, ledger, THRESHOLDS)
        assert not any("pairwise" in e for e in errors)


class TestRealLedger:
    """The live governance ledger must remain clean. Read-only."""

    def test_real_ledger_check_still_passes(self):
        import report
        rc = report.run_check(EVL / "registry.yaml", EVL / "ledger.db",
                              EVL / "thresholds.yaml")
        assert rc == 0, "Proposal 1 must not disturb the existing clean --check"

    def test_real_ledger_has_no_validated_families_yet(self):
        """Context for the above: the gate is vacuous today (zero Validated
        families), which is exactly why it is safe to land now and why it
        must be landed BEFORE the first promotion, per CLAUDE.md §16."""
        states = record.derive_all_states_at(EVL / "ledger.db")
        assert not [f for f, (s, *_) in states.items() if s == "validated"]
