"""
Tests for the Evidence Matrix reporting layer (PR-5 of
docs/governance/evidence_reporting_architecture.md), report.py + the C1-C9
invariants in lifecycle.validate_registry().

Covers: rendering semantics (rule 2's four-way distinction), flags, --as-of
reconstruction, historical reports, invariant checking (one targeted
failure per invariant), and a golden-file-style determinism check.
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
_EVLIB = ROOT / "research" / "evidence_library"
sys.path.insert(0, str(_EVLIB))

import record
import lifecycle
import report


def _run(db, family_id="fam_a", provenance="offline-corpus", run_kind="discovery", **metrics):
    return record.append_run(db, family_id, metrics, provenance, run_kind, n_trials=34)


@pytest.fixture
def env(tmp_path):
    """A minimal (ledger, registry, thresholds) triple."""
    db = tmp_path / "ledger.db"
    record.init_db(db)
    reg_path = tmp_path / "registry.yaml"
    reg_path.write_text(yaml.safe_dump({}), encoding="utf-8")
    thr_path = tmp_path / "thresholds.yaml"
    shutil.copy(_EVLIB / "thresholds.yaml", thr_path)
    return db, reg_path, thr_path


def _write_registry(reg_path: Path, registry: dict) -> None:
    reg_path.write_text(yaml.safe_dump(registry), encoding="utf-8")


# ---------------------------------------------------------------------------
# normalize_as_of
# ---------------------------------------------------------------------------

class TestNormalizeAsOf:
    def test_none_passthrough(self):
        assert report.normalize_as_of(None) is None

    def test_bare_date_normalised_to_end_of_day(self):
        assert report.normalize_as_of("2026-06-14") == "2026-06-14T23:59:59.999999+00:00"

    def test_full_datetime_passthrough(self):
        v = "2026-06-14T09:37:56+00:00"
        assert report.normalize_as_of(v) == v

    def test_bare_date_includes_same_day_events(self, env):
        db, reg_path, thr_path = env
        r1 = _run(db, "fam_a", run_kind="discovery")
        r1_row = record.get_run(db, r1)
        con = sqlite3.connect(db)
        con.execute("UPDATE runs SET run_ts = '2026-06-14T09:37:56+00:00' WHERE run_id = ?", (r1,))
        con.commit()
        con.close()

        as_of = report.normalize_as_of("2026-06-14")
        runs = record.get_current_runs_by_lane(db, as_of=as_of)
        assert "fam_a" in runs, "same-day event must be included, not excluded by a naive string compare"


# ---------------------------------------------------------------------------
# Rendering semantics (rule 2 — the single most important rule)
# ---------------------------------------------------------------------------

class TestRenderingSemantics:
    def test_no_run_renders_em_dash(self):
        assert report._fmt_l0_cell(None) == report.NO_RUN
        assert report._fmt_l2l3_cell(None, __import__("datetime").datetime(2026, 7, 13)) == report.NO_RUN

    def test_insufficient_for_low_n_forward(self):
        from datetime import datetime, timezone
        run = {"sharpe": None, "n_trades": 1, "run_ts": "2026-07-01T00:00:00+00:00"}
        cell = report._fmt_l2l3_cell(run, datetime(2026, 7, 13, tzinfo=timezone.utc))
        assert cell.startswith("insufficient")
        assert "insufficient" != report.NO_RUN

    def test_not_computed_for_null_dsr_on_existing_run(self):
        run = {"dsr_prob": None}
        assert report._fmt_pinned_stat(run, "dsr_prob") == "not-computed"

    def test_all_four_render_distinctly(self):
        """
        Rule 2: '—' (no evidence) vs 'insufficient' (n<2) vs 'not-computed'
        (DSR/BH on a bridge run) vs 'n/a' are FOUR DISTINCT strings. Never
        collapse to '-'.
        """
        from datetime import datetime, timezone
        no_evidence = report._fmt_l2l3_cell(None, datetime(2026, 7, 13, tzinfo=timezone.utc))
        insufficient_run = {"sharpe": None, "n_trades": 1, "run_ts": "2026-07-01T00:00:00+00:00"}
        insufficient = report._fmt_l2l3_cell(insufficient_run, datetime(2026, 7, 13, tzinfo=timezone.utc))
        not_computed = report._fmt_pinned_stat({"dsr_prob": None}, "dsr_prob")

        rendered = {no_evidence, insufficient.split(" / ")[0], not_computed}
        assert rendered == {"—", "insufficient", "not-computed"}
        assert "-" not in rendered  # bare hyphen must never appear as a rendered reason

    def test_l1_renders_as_delta_not_absolute(self):
        l0 = {"sharpe": 0.50, "run_ts": "2026-06-13T00:00:00+00:00"}
        l1 = {"sharpe": 0.52, "run_ts": "2026-06-14T09:37:56+00:00"}
        cell = report._fmt_l1_cell(l1, l0)
        assert cell == "+0.02 (2026-06-14)"
        assert "0.52" not in cell  # never renders the absolute L1 value

    def test_l1_missing_renders_no_run(self):
        assert report._fmt_l1_cell(None, {"sharpe": 0.5}) == report.NO_RUN

    def test_l2l3_cell_always_carries_n_and_days(self):
        from datetime import datetime, timezone
        run = {"sharpe": 0.41, "n_trades": 3, "run_ts": "2026-06-19T00:00:00+00:00"}
        cell = report._fmt_l2l3_cell(run, datetime(2026, 7, 13, tzinfo=timezone.utc))
        assert cell == "+0.41 / 3 / 24d"


# ---------------------------------------------------------------------------
# build_context / rows
# ---------------------------------------------------------------------------

class TestBuildContext:
    def test_pin_stale_detected(self, env):
        db, reg_path, thr_path = env
        r1 = _run(db, "fam_a", run_kind="discovery")
        record.append_transition(db, "fam_a", "discovery", "candidate", reason="r", justifying_run_id=r1)
        r2 = _run(db, "fam_a", run_kind="confirmatory")  # supersedes r1 within L0? no -- different lane
        # Force staleness: a second L0 run exists, current L0 != pinned r1.
        r3 = record.append_run(db, "fam_a", {"sharpe": 0.2}, "offline-corpus", "discovery", n_trials=34)
        _write_registry(reg_path, {"fam_a": {"state": "candidate", "role": ["edge"], "pinned_run": r1}})

        ctx = report.build_context(reg_path, thr_path, db)
        assert ctx.rows["fam_a"].is_pin_stale is True
        assert "PIN-STALE" in ctx.rows["fam_a"].flags

    def test_pin_not_stale_when_current(self, env):
        db, reg_path, thr_path = env
        r1 = _run(db, "fam_a", run_kind="discovery")
        record.append_transition(db, "fam_a", "discovery", "candidate", reason="r", justifying_run_id=r1)
        _write_registry(reg_path, {"fam_a": {"state": "candidate", "role": ["edge"], "pinned_run": r1}})
        ctx = report.build_context(reg_path, thr_path, db)
        assert ctx.rows["fam_a"].is_pin_stale is False

    def test_no_pin_for_pure_discovery_family(self, env):
        db, reg_path, thr_path = env
        _write_registry(reg_path, {"fam_a": {"state": "discovery", "role": ["edge"]}})
        ctx = report.build_context(reg_path, thr_path, db)
        assert ctx.rows["fam_a"].pinned_run is None
        assert ctx.rows["fam_a"].state == "discovery"


# ---------------------------------------------------------------------------
# Flags (compute_flags) — trigger and non-trigger for each
# ---------------------------------------------------------------------------

class TestFlags:
    def test_pin_stale_flag(self, env):
        db, reg_path, thr_path = env
        r1 = _run(db, "fam_a", run_kind="discovery")
        r2 = record.append_run(db, "fam_a", {"sharpe": 0.2}, "offline-corpus", "discovery", n_trials=34)
        record.append_transition(db, "fam_a", "discovery", "candidate", reason="r", justifying_run_id=r1)
        _write_registry(reg_path, {"fam_a": {"state": "candidate", "role": ["edge"], "pinned_run": r1}})
        ctx = report.build_context(reg_path, thr_path, db)
        assert "PIN-STALE" in ctx.rows["fam_a"].flags

    def test_repro_drift_triggers_above_threshold(self, env):
        db, reg_path, thr_path = env
        _run(db, "fam_a", run_kind="discovery", sharpe=0.50)
        _run(db, "fam_a", run_kind="confirmatory", sharpe=0.65)  # delta 0.15 > 0.10
        _write_registry(reg_path, {"fam_a": {"state": "discovery", "role": ["edge"]}})
        ctx = report.build_context(reg_path, thr_path, db)
        assert "REPRO-DRIFT" in ctx.rows["fam_a"].flags

    def test_repro_drift_does_not_trigger_within_threshold(self, env):
        db, reg_path, thr_path = env
        _run(db, "fam_a", run_kind="discovery", sharpe=0.50)
        _run(db, "fam_a", run_kind="confirmatory", sharpe=0.55)  # delta 0.05 < 0.10
        _write_registry(reg_path, {"fam_a": {"state": "discovery", "role": ["edge"]}})
        ctx = report.build_context(reg_path, thr_path, db)
        assert "REPRO-DRIFT" not in ctx.rows["fam_a"].flags

    def test_fwd_contra_triggers_on_sign_disagreement(self, env):
        db, reg_path, thr_path = env
        r1 = _run(db, "fam_a", run_kind="discovery", sharpe=0.50)
        record.append_transition(db, "fam_a", "discovery", "candidate", reason="r", justifying_run_id=r1)
        _run(db, "fam_a", provenance="live-forward", run_kind="forward", sharpe=-0.30, n_trades=5)
        _write_registry(reg_path, {"fam_a": {"state": "candidate", "role": ["edge"], "pinned_run": r1}})
        ctx = report.build_context(reg_path, thr_path, db)
        assert "FWD-CONTRA" in ctx.rows["fam_a"].flags

    def test_fwd_contra_does_not_trigger_on_sign_agreement(self, env):
        db, reg_path, thr_path = env
        r1 = _run(db, "fam_a", run_kind="discovery", sharpe=0.50)
        record.append_transition(db, "fam_a", "discovery", "candidate", reason="r", justifying_run_id=r1)
        _run(db, "fam_a", provenance="live-forward", run_kind="forward", sharpe=0.30, n_trades=5)
        _write_registry(reg_path, {"fam_a": {"state": "candidate", "role": ["edge"], "pinned_run": r1}})
        ctx = report.build_context(reg_path, thr_path, db)
        assert "FWD-CONTRA" not in ctx.rows["fam_a"].flags

    def test_fwd_thin_triggers_below_min_trades(self, env):
        db, reg_path, thr_path = env
        _run(db, "fam_a", provenance="live-forward", run_kind="forward", sharpe=None, n_trades=1)
        _write_registry(reg_path, {"fam_a": {"state": "discovery", "role": ["edge"]}})
        ctx = report.build_context(reg_path, thr_path, db)
        assert "FWD-THIN" in ctx.rows["fam_a"].flags

    def test_fwd_thin_does_not_trigger_at_min_trades(self, env):
        db, reg_path, thr_path = env
        _run(db, "fam_a", provenance="live-forward", run_kind="forward", sharpe=0.1, n_trades=2)
        _write_registry(reg_path, {"fam_a": {"state": "discovery", "role": ["edge"]}})
        ctx = report.build_context(reg_path, thr_path, db)
        assert "FWD-THIN" not in ctx.rows["fam_a"].flags

    def test_fwd_stale_triggers_after_3_days(self, env):
        db, reg_path, thr_path = env
        run_id = _run(db, "fam_a", provenance="live-forward", run_kind="forward", sharpe=0.1, n_trades=5)
        con = sqlite3.connect(db)
        con.execute("UPDATE runs SET run_ts = '2026-07-01T00:00:00+00:00' WHERE run_id = ?", (run_id,))
        con.commit()
        con.close()
        _write_registry(reg_path, {"fam_a": {"state": "discovery", "role": ["edge"]}})
        ctx = report.build_context(reg_path, thr_path, db, as_of="2026-07-10")
        assert "FWD-STALE" in ctx.rows["fam_a"].flags

    def test_fwd_stale_does_not_trigger_within_window(self, env):
        db, reg_path, thr_path = env
        run_id = _run(db, "fam_a", provenance="live-forward", run_kind="forward", sharpe=0.1, n_trades=5)
        con = sqlite3.connect(db)
        con.execute("UPDATE runs SET run_ts = '2026-07-09T00:00:00+00:00' WHERE run_id = ?", (run_id,))
        con.commit()
        con.close()
        _write_registry(reg_path, {"fam_a": {"state": "discovery", "role": ["edge"]}})
        ctx = report.build_context(reg_path, thr_path, db, as_of="2026-07-10")
        assert "FWD-STALE" not in ctx.rows["fam_a"].flags

    def test_gate_blocked_triggers_for_candidate_with_null_dsr_forward(self, env):
        db, reg_path, thr_path = env
        r1 = _run(db, "fam_a", run_kind="discovery")
        record.append_transition(db, "fam_a", "discovery", "candidate", reason="r", justifying_run_id=r1)
        _run(db, "fam_a", provenance="live-forward", run_kind="forward", sharpe=0.1, n_trades=5)  # dsr_prob NULL
        _write_registry(reg_path, {"fam_a": {"state": "candidate", "role": ["edge"], "pinned_run": r1}})
        ctx = report.build_context(reg_path, thr_path, db)
        assert "GATE-BLOCKED" in ctx.rows["fam_a"].flags

    def test_gate_blocked_does_not_trigger_without_forward_evidence(self, env):
        db, reg_path, thr_path = env
        r1 = _run(db, "fam_a", run_kind="discovery")
        record.append_transition(db, "fam_a", "discovery", "candidate", reason="r", justifying_run_id=r1)
        _write_registry(reg_path, {"fam_a": {"state": "candidate", "role": ["edge"], "pinned_run": r1}})
        ctx = report.build_context(reg_path, thr_path, db)
        assert "GATE-BLOCKED" not in ctx.rows["fam_a"].flags

    def test_gate_blocked_does_not_trigger_for_non_candidate(self, env):
        db, reg_path, thr_path = env
        _run(db, "fam_a", provenance="live-forward", run_kind="forward", sharpe=0.1, n_trades=5)
        _write_registry(reg_path, {"fam_a": {"state": "discovery", "role": ["edge"]}})
        ctx = report.build_context(reg_path, thr_path, db)
        assert "GATE-BLOCKED" not in ctx.rows["fam_a"].flags


# ---------------------------------------------------------------------------
# Acceptance test: a forward run cannot change any offline number
# ---------------------------------------------------------------------------

class TestForwardRunCannotChangeOfflineNumbers:
    def test_l0_l1_untouched_by_new_forward_run(self, env, tmp_path):
        db, reg_path, thr_path = env
        r1 = _run(db, "fam_a", run_kind="discovery", sharpe=0.13, oos_sharpe=0.68, n_trades=30)
        record.append_transition(db, "fam_a", "discovery", "candidate", reason="r", justifying_run_id=r1)
        _write_registry(reg_path, {"fam_a": {"state": "candidate", "role": ["feature"], "pinned_run": r1}})

        ctx_before = report.build_context(reg_path, thr_path, db)
        out_before = tmp_path / "before.md"
        report.generate_state_board(ctx_before, out_before, generated_at="T")
        before_text = out_before.read_text(encoding="utf-8")

        _run(db, "fam_a", provenance="live-forward", run_kind="forward", sharpe=0.41, n_trades=3)

        ctx_after = report.build_context(reg_path, thr_path, db)
        out_after = tmp_path / "after.md"
        report.generate_state_board(ctx_after, out_after, generated_at="T")
        after_text = out_after.read_text(encoding="utf-8")

        # The decision-anchor row's EVIDENCE columns (State/Lane/Sharpe/DSR/BH-p)
        # must be byte-identical. Flags are explicitly exempt: they exist
        # precisely to react to new evidence (GATE-BLOCKED legitimately
        # appears once L2 evidence exists for a candidate) -- what must
        # never move is the evidence itself, not whether a human's
        # attention is being directed to it.
        before_anchor_line = [l for l in before_text.splitlines() if l.startswith("| fam_a")][0]
        after_anchor_line = [l for l in after_text.splitlines() if l.startswith("| fam_a")][0]
        before_cols = before_anchor_line.split("|")[:-2]  # drop Flags + trailing empty
        after_cols = after_anchor_line.split("|")[:-2]
        assert before_cols == after_cols

        assert "+0.13" in after_text  # L0 still visible
        assert "+0.41" in after_text  # L2 additionally visible


# ---------------------------------------------------------------------------
# --as-of reconstruction / historical reports
# ---------------------------------------------------------------------------

class TestAsOfReconstruction:
    def test_state_board_as_of_past_date(self, env, tmp_path):
        db, reg_path, thr_path = env
        r1 = _run(db, "fam_a", run_kind="discovery")
        record.append_transition(db, "fam_a", "discovery", "candidate", reason="r", justifying_run_id=r1,
                                  ts="2026-06-13T00:00:00+00:00")
        record.append_transition(db, "fam_a", "candidate", "validated", reason="promoted",
                                  justifying_run_id=r1, ts="2026-06-20T00:00:00+00:00")
        _write_registry(reg_path, {"fam_a": {"state": "validated", "role": ["edge"], "pinned_run": r1}})

        ctx_past = report.build_context(reg_path, thr_path, db, as_of="2026-06-15")
        assert ctx_past.rows["fam_a"].state == "candidate"

        ctx_now = report.build_context(reg_path, thr_path, db)
        assert ctx_now.rows["fam_a"].state == "validated"

    def test_reconstruction_is_pure_ledger_derivation_not_registry_read(self, env):
        """Even though registry.yaml says 'validated' (the current head),
        an as-of in the past must show the ledger's historical position,
        never the registry's current cache."""
        db, reg_path, thr_path = env
        r1 = _run(db, "fam_a", run_kind="discovery")
        record.append_transition(db, "fam_a", "discovery", "candidate", reason="r", justifying_run_id=r1,
                                  ts="2026-06-13T00:00:00+00:00")
        _write_registry(reg_path, {"fam_a": {"state": "candidate", "role": ["edge"], "pinned_run": r1}})
        ctx = report.build_context(reg_path, thr_path, db, as_of="2026-06-01")
        assert ctx.rows["fam_a"].state == "discovery"
        assert ctx.rows["fam_a"].pinned_run_id is None


# ---------------------------------------------------------------------------
# Determinism (byte-identical for identical inputs)
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_repeated_generation_is_byte_identical(self, env, tmp_path):
        db, reg_path, thr_path = env
        r1 = _run(db, "fam_a", run_kind="discovery", sharpe=0.5)
        record.append_transition(db, "fam_a", "discovery", "candidate", reason="r", justifying_run_id=r1)
        _write_registry(reg_path, {"fam_a": {"state": "candidate", "role": ["edge"], "pinned_run": r1}})

        ctx1 = report.build_context(reg_path, thr_path, db)
        ctx2 = report.build_context(reg_path, thr_path, db)
        out1, out2 = tmp_path / "a.md", tmp_path / "b.md"
        report.generate_state_board(ctx1, out1, generated_at="FIXED")
        report.generate_state_board(ctx2, out2, generated_at="FIXED")
        assert out1.read_bytes() == out2.read_bytes()

    def test_sort_order_does_not_depend_on_a_mutable_metric(self, env):
        db, reg_path, thr_path = env
        _write_registry(reg_path, {
            "fam_z": {"state": "candidate", "role": ["edge"], "pinned_run": None},
            "fam_a": {"state": "candidate", "role": ["edge"], "pinned_run": None},
        })
        ordered = report._sort_families(["fam_z", "fam_a"], {"fam_z": "candidate", "fam_a": "candidate"})
        assert ordered == ["fam_a", "fam_z"]  # alphabetical within same state rank


# ---------------------------------------------------------------------------
# Invariants C1-C9 (one targeted failure case each)
# ---------------------------------------------------------------------------

class TestInvariants:
    def _base_registry_and_db(self, tmp_path):
        db = tmp_path / "ledger.db"
        record.init_db(db)
        r1 = _run(db, "fam_a", run_kind="discovery")
        record.append_transition(db, "fam_a", "discovery", "candidate", reason="r", justifying_run_id=r1)
        registry = {"fam_a": {"state": "candidate", "role": ["edge"], "pinned_run": r1}}
        thr = yaml.safe_load((_EVLIB / "thresholds.yaml").read_text())
        return db, registry, thr, r1

    def test_c1_invalid_run_kind(self, tmp_path):
        db, registry, thr, r1 = self._base_registry_and_db(tmp_path)
        con = sqlite3.connect(db)
        con.execute("UPDATE runs SET run_kind = NULL WHERE run_id = ?", (r1,))
        con.commit()
        con.close()
        errors = lifecycle.validate_registry(registry, db, thr)
        assert any(e.startswith("C1") for e in errors)

    def test_c2_duplicate_current_run_impossible_via_pk_but_checked(self, tmp_path):
        # C2 is structurally guaranteed by run_id being a PK + a total order;
        # assert the check itself reports zero violations on clean data
        # rather than trying to force an actual duplicate (which the schema
        # prevents by construction).
        db, registry, thr, r1 = self._base_registry_and_db(tmp_path)
        errors = lifecycle.validate_registry(registry, db, thr)
        assert not any(e.startswith("C2") for e in errors)

    def test_c3_two_discovery_runs_for_one_family(self, tmp_path):
        db, registry, thr, r1 = self._base_registry_and_db(tmp_path)
        record.append_run(db, "fam_a", {"sharpe": 0.2}, "offline-corpus", "discovery", n_trials=34)
        errors = lifecycle.validate_registry(registry, db, thr)
        assert any(e.startswith("C3") for e in errors)

    def test_c4_registry_state_disagrees_with_ledger(self, tmp_path):
        db, registry, thr, r1 = self._base_registry_and_db(tmp_path)
        registry["fam_a"]["state"] = "validated"
        errors = lifecycle.validate_registry(registry, db, thr)
        assert any(e.startswith("C4") for e in errors)

    def test_c5_pinned_run_disagrees_with_ledger(self, tmp_path):
        db, registry, thr, r1 = self._base_registry_and_db(tmp_path)
        r2 = record.append_run(db, "fam_a", {"sharpe": 0.9}, "offline-corpus", "confirmatory", n_trials=34)
        registry["fam_a"]["pinned_run"] = r2  # never recorded as a re-pin
        errors = lifecycle.validate_registry(registry, db, thr)
        assert any(e.startswith("C5") for e in errors)

    def test_c6_transition_missing_justifying_run_id(self, tmp_path):
        db, registry, thr, r1 = self._base_registry_and_db(tmp_path)
        con = sqlite3.connect(db)
        con.execute(
            "INSERT INTO state_transitions (family_id, from_state, to_state, ts, justifying_run_id) "
            "VALUES ('fam_b', 'discovery', 'candidate', '2026-07-10T00:00:00+00:00', NULL)"
        )
        con.commit()
        con.close()
        registry["fam_b"] = {"state": "candidate", "role": ["edge"], "pinned_run": None}
        errors = lifecycle.validate_registry(registry, db, thr)
        assert any(e.startswith("C6") for e in errors)

    def test_c7_validated_pinned_on_offline_lane(self, tmp_path):
        db, registry, thr, r1 = self._base_registry_and_db(tmp_path)
        record.append_transition(db, "fam_a", "candidate", "validated", reason="promoted", justifying_run_id=r1)
        registry["fam_a"]["state"] = "validated"
        errors = lifecycle.validate_registry(registry, db, thr)
        assert any(e.startswith("C7") for e in errors)

    def test_c7_passes_when_pinned_on_forward_lane(self, tmp_path):
        db, registry, thr, r1 = self._base_registry_and_db(tmp_path)
        r_fwd = record.append_run(db, "fam_a", {"sharpe": 0.5, "n_trades": 10}, "live-forward", "forward", n_trials=34)
        record.append_transition(db, "fam_a", "candidate", "validated", reason="promoted", justifying_run_id=r_fwd)
        registry["fam_a"]["state"] = "validated"
        registry["fam_a"]["pinned_run"] = r_fwd
        errors = lifecycle.validate_registry(registry, db, thr)
        assert not any(e.startswith("C7") for e in errors)

    def test_c8_provenance_field_present(self, tmp_path):
        db, registry, thr, r1 = self._base_registry_and_db(tmp_path)
        registry["fam_a"]["provenance"] = "offline-corpus"
        errors = lifecycle.validate_registry(registry, db, thr)
        assert any(e.startswith("C8") for e in errors)

    def test_c9_orphan_transition_family_not_in_registry(self, tmp_path):
        db, registry, thr, r1 = self._base_registry_and_db(tmp_path)
        r2 = record.append_run(db, "ghost_family", {"sharpe": 0.1}, "offline-corpus", "discovery", n_trials=34)
        record.append_transition(db, "ghost_family", "discovery", "candidate", reason="r", justifying_run_id=r2)
        errors = lifecycle.validate_registry(registry, db, thr)
        assert any(e.startswith("C9") and "ghost_family" in e for e in errors)

    def test_c9_registry_family_with_state_but_no_transitions(self, tmp_path):
        db, registry, thr, r1 = self._base_registry_and_db(tmp_path)
        registry["fam_b"] = {"state": "candidate", "role": ["edge"], "pinned_run": None}
        errors = lifecycle.validate_registry(registry, db, thr)
        assert any(e.startswith("C9") and "fam_b" in e for e in errors)

    def test_c9_pure_discovery_family_exempt(self, tmp_path):
        db, registry, thr, r1 = self._base_registry_and_db(tmp_path)
        registry["fam_never_touched"] = {"state": "discovery", "role": ["edge"]}
        errors = lifecycle.validate_registry(registry, db, thr)
        assert not any("fam_never_touched" in e for e in errors)

    def test_clean_registry_passes_all_nine(self, tmp_path):
        db, registry, thr, r1 = self._base_registry_and_db(tmp_path)
        errors = lifecycle.validate_registry(registry, db, thr)
        # gate_results absence would trigger the pre-existing candidate check;
        # add one so this is a genuinely clean, all-invariants-pass case.
        record.append_gate_result(db, r1, "B_SHARPE", "edge", True, 0.9, 0.3)
        errors = lifecycle.validate_registry(registry, db, thr)
        assert errors == []


# ---------------------------------------------------------------------------
# The real registry passes all nine (post-migration)
# ---------------------------------------------------------------------------

class TestRealRegistryPassesAllInvariants:
    def test_real_check_is_clean(self):
        reg_path = _EVLIB / "registry.yaml"
        ledger_path = _EVLIB / "ledger.db"
        thr_path = _EVLIB / "thresholds.yaml"
        if not (reg_path.exists() and ledger_path.exists()):
            pytest.skip("real registry/ledger not present in this checkout")
        registry = yaml.safe_load(reg_path.read_text(encoding="utf-8"))
        thresholds = yaml.safe_load(thr_path.read_text(encoding="utf-8"))
        errors = lifecycle.validate_registry(registry, ledger_path, thresholds)
        assert errors == [], f"real registry must pass all nine invariants: {errors}"

    def test_real_registry_has_no_provenance_field(self):
        reg_path = _EVLIB / "registry.yaml"
        if not reg_path.exists():
            pytest.skip("real registry not present in this checkout")
        registry = yaml.safe_load(reg_path.read_text(encoding="utf-8"))
        offenders = [fid for fid, fam in registry.items() if "provenance" in fam]
        assert offenders == []
