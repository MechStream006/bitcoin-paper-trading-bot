"""WBS 4.10 — Training Dataset structure and partition roles
(ADR-I012 §7.1, §15, §15.1, §15.2, DG-7; resolves PS5 structurally).

`P4-6` requires proving RF4 **correctly refuses every declaration today**.
The roadmap's own warning governs how: *"a test that passes because the
feature is missing is worthless; it must fail if RF4 stops refusing."*
`TestRF4RefusesEveryDeclarationToday` therefore proves both directions —
today's declarations refuse, and the same declaration passes once its meet
reaches the forward lane.
"""

from __future__ import annotations

import ast
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "research", ROOT / "research" / "admissibility",
          ROOT / "research" / "evidence_library"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import receipt_ledger as rl  # noqa: E402
from lattice import Certification, Provenance, Readiness  # noqa: E402

from dataset_builder import citation_index as ci  # noqa: E402
from dataset_builder import experiments as ex  # noqa: E402
from dataset_builder import materialisation as mat  # noqa: E402
from dataset_builder import registry as reg  # noqa: E402
from dataset_builder import resolution as res  # noqa: E402
from dataset_builder import sealing as sl  # noqa: E402
from dataset_builder import training as tr  # noqa: E402
from dataset_builder import validation as val  # noqa: E402
from tests.test_dataset_registry import valid_declaration  # noqa: E402
from tests.test_dataset_resolution import good_resolver  # noqa: E402
from tests.test_dataset_validation import adm, ev, feat, good_inputs, lab  # noqa: E402
from tests.test_materialisation import rich_provider  # noqa: E402

GROUP = "btc-h6-2026"


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    p = tmp_path / "dataset_builder.db"
    reg.init_db(p); ci.init_db(p); sl.init_db(p)
    mat.init_db(p); ex.init_db(p); tr.init_db(p)
    return p


@pytest.fixture()
def ledger(tmp_path: Path) -> Path:
    p = tmp_path / "ledger.db"
    rl.init_db(p)
    return p


def windowed_declaration(start, end, tag=""):
    d = valid_declaration()
    d["temporal_scope"]["window_start"] = start
    d["temporal_scope"]["window_end"] = end
    d["temporal_scope"]["as_of"] = "2026-08-08"
    if tag:
        d["intent"]["rationale"] = tag
    return d


def _sealed(db, ledger, decl=None, lineage="line-a") -> str:
    d = decl if decl is not None else valid_declaration()
    h = res.declare(db, d, lineage=lineage, declared_by="rohith").spec_hash
    res.resolve(db, h, good_resolver())
    report = val.validate(db, h, good_inputs())
    sl.seal_dataset(db, h, report, sealed_by="rohith", ledger_db=ledger)
    return h


def _pinned(db, ledger, decl=None, lineage="line-a") -> str:
    h = _sealed(db, ledger, decl, lineage)
    r = mat.materialise(db, h, rich_provider())
    mat.pin_snapshot(db, h, r, pinned_by="rohith")
    return h


def _three_partitions(db, ledger):
    """A complete, non-overlapping sibling group."""
    a = _pinned(db, ledger, windowed_declaration("2021-01-01", "2023-12-31", "train"),
                lineage="g-train")
    b = _pinned(db, ledger, windowed_declaration("2024-01-01", "2024-12-31", "val"),
                lineage="g-val")
    c = _pinned(db, ledger, windowed_declaration("2025-01-01", "2025-05-31", "hold"),
                lineage="g-hold")
    return a, b, c


def _register(db, h, role, group=GROUP, **kw):
    kw.setdefault("registered_by", "rohith")
    return tr.register_partition(db, h, sibling_group=group, role=role, **kw)


# ===========================================================================
# P4-6 — RF4 refuses every declaration today
# ===========================================================================

class TestRF4RefusesEveryDeclarationToday:
    """The roadmap's warning: *"a test that passes because the feature is
    missing is worthless; it must fail if RF4 stops refusing."*"""

    def test_offline_corpus_meet_is_refused(self, db, ledger):
        h = _pinned(db, ledger)
        spec = reg.get_specification(db, h)
        meet = val.compose_admissibility(good_inputs())   # OFFLINE_CORPUS
        findings = tr.check_training_admissibility(spec["declaration"],
                                                   good_inputs(), meet)
        assert findings and findings[0].code == "RF4"
        assert "PROVENANCE" in findings[0].detail

    def test_registration_is_refused_on_todays_evidence(self, db, ledger):
        h = _pinned(db, ledger)
        meet = val.compose_admissibility(good_inputs())
        with pytest.raises(tr.AdmissibilityFloorError, match="RF4 refuses"):
            _register(db, h, "train", inputs=good_inputs(), computed_meet=meet)

    def test_the_same_declaration_passes_at_the_forward_lane(self, db, ledger):
        """The other direction, which makes the refusal meaningful rather than
        vacuous: raise the meet to LIVE_FORWARD and RF4 stops refusing."""
        h = _pinned(db, ledger)
        spec = reg.get_specification(db, h)
        forward_inputs = [ev(ceiling=adm(p=Provenance.LIVE_FORWARD)),
                          feat(ceiling=adm(p=Provenance.LIVE_FORWARD)),
                          lab(ceiling=adm(p=Provenance.LIVE_FORWARD))]
        meet = val.compose_admissibility(forward_inputs)
        assert tr.check_training_admissibility(
            spec["declaration"], forward_inputs, meet) == []

    def test_registration_succeeds_at_the_forward_lane(self, db, ledger):
        h = _pinned(db, ledger)
        forward_inputs = [ev(ceiling=adm(p=Provenance.LIVE_FORWARD))]
        meet = val.compose_admissibility(forward_inputs)
        p = _register(db, h, "train", inputs=forward_inputs, computed_meet=meet)
        assert p.role == "train"

    def test_mainnet_also_clears_the_floor(self, db, ledger):
        """The floor is *at or above* the forward lane."""
        h = _pinned(db, ledger)
        spec = reg.get_specification(db, h)
        inputs = [ev(ceiling=adm(p=Provenance.MAINNET))]
        meet = val.compose_admissibility(inputs)
        assert tr.check_training_admissibility(spec["declaration"], inputs, meet) == []

    def test_unknown_provenance_is_refused(self, db, ledger):
        h = _pinned(db, ledger)
        spec = reg.get_specification(db, h)
        inputs = [ev(ceiling=adm(p=Provenance.UNKNOWN))]
        meet = val.compose_admissibility(inputs)
        assert tr.check_training_admissibility(spec["declaration"], inputs, meet)

    def test_the_refusal_is_about_provenance_only(self, db, ledger):
        """A weak certification must NOT be what refuses — the floor
        constrains the lane and only the lane (§15.2)."""
        h = _pinned(db, ledger)
        spec = reg.get_specification(db, h)
        inputs = [ev(ceiling=adm(p=Provenance.LIVE_FORWARD,
                                 c=Certification.UNKNOWN,
                                 r=Readiness.NOT_READY))]
        meet = val.compose_admissibility(inputs)
        assert tr.check_training_admissibility(spec["declaration"], inputs, meet) == []


class TestFloor:
    def test_floor_is_the_forward_lane(self):
        assert tr.training_floor().provenance == Provenance.LIVE_FORWARD

    def test_floor_constrains_nothing_else(self):
        f = tr.training_floor()
        assert f.certification == Certification.UNKNOWN
        assert f.readiness == Readiness.UNKNOWN
        assert int(f.maturity) == 0 and int(f.integrity) == 0

    def test_floor_is_immutable(self):
        f = tr.training_floor()
        with pytest.raises(Exception):
            f.provenance = Provenance.MAINNET


# ===========================================================================
# RF4 is the sole admissibility authority
# ===========================================================================

class TestRF4SoleAuthority:
    def test_runtime_guard(self):
        ok, errors = tr.verify_rf4_is_sole_authority()
        assert ok is True, errors

    def test_module_does_not_import_satisfies(self):
        src = (ROOT / "research/dataset_builder/training.py").read_text(encoding="utf-8")
        imported: set[str] = set()
        for n in ast.walk(ast.parse(src)):
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                imported |= {a.asname or a.name for a in n.names}
        assert "satisfies" not in imported
        assert "meet" not in imported

    def test_delegates_to_validation_check_tier_c(self):
        src = (ROOT / "research/dataset_builder/training.py").read_text(encoding="utf-8")
        assert "val.check_tier_c(" in src

    def test_only_rf4_findings_are_returned(self, db, ledger):
        """A caller must not mistake an unrelated Tier C finding for an
        admissibility refusal."""
        h = _pinned(db, ledger)
        spec = reg.get_specification(db, h)
        inputs = [ev(certification_verdict=None, lane=None,
                     ceiling=adm(p=Provenance.LIVE_FORWARD))]
        meet = val.compose_admissibility(inputs)
        findings = tr.check_training_admissibility(spec["declaration"], inputs, meet)
        assert all(f.code == "RF4" for f in findings)
        raw = val.check_tier_c(spec["declaration"], inputs, meet, tr.training_floor())
        assert any(f.code in {"TC1", "TC2"} for f in raw)   # they existed


# ===========================================================================
# Structure — three separate sealed datasets (§15.1)
# ===========================================================================

class TestPartitionStructure:
    def test_three_roles(self):
        assert tr.PARTITION_ROLES == ("train", "validation", "holdout")

    def test_registers_all_three(self, db, ledger):
        a, b, c = _three_partitions(db, ledger)
        _register(db, a, "train"); _register(db, b, "validation")
        _register(db, c, "holdout")
        assert len(tr.sibling_group(db, GROUP)) == 3

    def test_siblings_known_by_hash(self, db, ledger):
        a, b, c = _three_partitions(db, ledger)
        _register(db, a, "train"); _register(db, b, "validation")
        _register(db, c, "holdout")
        assert tr.sibling_hashes(db, a) == {"train": a, "validation": b, "holdout": c}

    def test_group_ordered_by_role(self, db, ledger):
        a, b, c = _three_partitions(db, ledger)
        _register(db, c, "holdout"); _register(db, a, "train")
        _register(db, b, "validation")
        assert [p.role for p in tr.sibling_group(db, GROUP)] == list(tr.PARTITION_ROLES)

    def test_unknown_role_refused(self, db, ledger):
        h = _pinned(db, ledger)
        with pytest.raises(tr.TrainingError, match="role must be"):
            _register(db, h, "test")

    def test_duplicate_role_in_group_refused(self, db, ledger):
        a, b, _ = _three_partitions(db, ledger)
        _register(db, a, "train")
        with pytest.raises(tr.TrainingError, match="already has a 'train'"):
            _register(db, b, "train")

    def test_sibling_group_required(self, db, ledger):
        h = _pinned(db, ledger)
        with pytest.raises(tr.TrainingError, match="sibling_group"):
            _register(db, h, "train", group="")

    def test_registered_by_required(self, db, ledger):
        h = _pinned(db, ledger)
        with pytest.raises(tr.TrainingError, match="registered_by"):
            tr.register_partition(db, h, sibling_group=GROUP, role="train",
                                  registered_by="")

    def test_idempotent(self, db, ledger):
        a, _, _ = _three_partitions(db, ledger)
        assert _register(db, a, "train").spec_hash == _register(db, a, "train").spec_hash
        assert len(tr.sibling_group(db, GROUP)) == 1

    def test_group_complete_check(self, db, ledger):
        a, b, c = _three_partitions(db, ledger)
        _register(db, a, "train")
        ok, errors = tr.verify_group_complete(db, GROUP)
        assert ok is False and any("missing" in e for e in errors)
        _register(db, b, "validation"); _register(db, c, "holdout")
        assert tr.verify_group_complete(db, GROUP) == (True, [])

    def test_empty_group_reported(self, db):
        ok, errors = tr.verify_group_complete(db, "nothing")
        assert ok is False and errors


class TestNonOverlap:
    """§15.1: *"leakage becomes structurally impossible, not a review
    finding."*"""

    def test_overlapping_windows_refused(self, db, ledger):
        a = _pinned(db, ledger, windowed_declaration("2021-01-01", "2024-06-30", "a"),
                    lineage="g1")
        b = _pinned(db, ledger, windowed_declaration("2024-01-01", "2025-01-01", "b"),
                    lineage="g2")
        _register(db, a, "train")
        with pytest.raises(tr.PartitionOverlapError, match="overlaps"):
            _register(db, b, "validation")

    def test_single_shared_day_is_overlap(self, db, ledger):
        """One leaked observation is still leakage."""
        a = _pinned(db, ledger, windowed_declaration("2021-01-01", "2024-01-01", "a"),
                    lineage="g1")
        b = _pinned(db, ledger, windowed_declaration("2024-01-01", "2025-01-01", "b"),
                    lineage="g2")
        _register(db, a, "train")
        with pytest.raises(tr.PartitionOverlapError):
            _register(db, b, "validation")

    def test_adjacent_windows_are_allowed(self, db, ledger):
        a = _pinned(db, ledger, windowed_declaration("2021-01-01", "2023-12-31", "a"),
                    lineage="g1")
        b = _pinned(db, ledger, windowed_declaration("2024-01-01", "2025-01-01", "b"),
                    lineage="g2")
        _register(db, a, "train")
        assert _register(db, b, "validation").role == "validation"

    def test_different_groups_may_overlap(self, db, ledger):
        a = _pinned(db, ledger, windowed_declaration("2021-01-01", "2024-06-30", "a"),
                    lineage="g1")
        b = _pinned(db, ledger, windowed_declaration("2024-01-01", "2025-01-01", "b"),
                    lineage="g2")
        _register(db, a, "train", group="group-1")
        assert _register(db, b, "train", group="group-2").role == "train"

    def test_windows_overlap_helper(self):
        assert tr.windows_overlap(("2021-01-01", "2022-01-01"),
                                  ("2021-06-01", "2023-01-01")) is True
        assert tr.windows_overlap(("2021-01-01", "2021-12-31"),
                                  ("2022-01-01", "2022-12-31")) is False
        assert tr.windows_overlap(("2021-01-01", "2022-01-01"),
                                  ("2022-01-01", "2023-01-01")) is True

    def test_group_complete_reports_overlap(self, db, ledger):
        a, b, c = _three_partitions(db, ledger)
        _register(db, a, "train"); _register(db, b, "validation")
        _register(db, c, "holdout")
        ok, _ = tr.verify_group_complete(db, GROUP)
        assert ok is True


# ===========================================================================
# The pin obligation (§15)
# ===========================================================================

class TestPinRequired:
    def test_unpinned_dataset_refused(self, db, ledger):
        h = _sealed(db, ledger)          # sealed but never pinned
        with pytest.raises(tr.NotPinnedError, match="exact-bytes"):
            _register(db, h, "train")

    def test_materialised_but_unpinned_refused(self, db, ledger):
        h = _sealed(db, ledger)
        mat.materialise(db, h, rich_provider())   # no pin
        with pytest.raises(tr.NotPinnedError):
            _register(db, h, "train")

    def test_pinned_dataset_accepted(self, db, ledger):
        h = _pinned(db, ledger)
        assert _register(db, h, "train").snapshot_sha256

    def test_snapshot_hash_recorded(self, db, ledger):
        h = _pinned(db, ledger)
        p = _register(db, h, "train")
        assert p.snapshot_sha256 == mat.get_pin(db, h)["snapshot_sha256"]

    def test_unsealed_refused_before_pin_check(self, db):
        d = valid_declaration()
        h = res.declare(db, d, lineage="l", declared_by="r").spec_hash
        with pytest.raises(tr.TrainingError, match="must be sealed"):
            _register(db, h, "train")

    def test_governed_lifecycle_is_the_only_path(self, db, ledger):
        """declare -> resolve -> validate -> seal -> materialise -> pin ->
        register. Skipping any step is refused by name."""
        d = valid_declaration()
        h = res.declare(db, d, lineage="l", declared_by="r").spec_hash
        with pytest.raises(tr.TrainingError):        # unsealed
            _register(db, h, "train")
        res.resolve(db, h, good_resolver())
        with pytest.raises(tr.TrainingError):        # unvalidated/unsealed
            _register(db, h, "train")
        report = val.validate(db, h, good_inputs())
        sl.seal_dataset(db, h, report, sealed_by="r", ledger_db=ledger)
        with pytest.raises(tr.NotPinnedError):       # unpinned
            _register(db, h, "train")
        r = mat.materialise(db, h, rich_provider())
        mat.pin_snapshot(db, h, r, pinned_by="r")
        assert _register(db, h, "train").role == "train"


# ===========================================================================
# DG-7 — holdout is single-use
# ===========================================================================

class TestHoldoutSingleUse:
    def _holdout(self, db, ledger):
        _, _, c = _three_partitions(db, ledger)
        _register(db, c, "holdout")
        return c

    def test_first_consumption_recorded(self, db, ledger):
        h = self._holdout(db, ledger)
        c = tr.consume_holdout(db, h, decision_ref="promo-1")
        assert c.decision_ref == "promo-1"
        assert tr.is_holdout_spent(db, h) is True

    def test_second_consumption_refused(self, db, ledger):
        h = self._holdout(db, ledger)
        tr.consume_holdout(db, h, decision_ref="promo-1")
        with pytest.raises(tr.HoldoutSpentError, match="already consumed"):
            tr.consume_holdout(db, h, decision_ref="promo-2")

    def test_refusal_names_the_spending_decision(self, db, ledger):
        h = self._holdout(db, ledger)
        tr.consume_holdout(db, h, decision_ref="promo-1")
        with pytest.raises(tr.HoldoutSpentError, match="promo-1"):
            tr.consume_holdout(db, h, decision_ref="promo-2")

    def test_decision_ref_required(self, db, ledger):
        h = self._holdout(db, ledger)
        with pytest.raises(tr.TrainingError, match="decision_ref"):
            tr.consume_holdout(db, h, decision_ref="")

    def test_non_holdout_roles_are_not_single_use(self, db, ledger):
        a, _, _ = _three_partitions(db, ledger)
        _register(db, a, "train")
        with pytest.raises(tr.TrainingError, match="not single-use"):
            tr.consume_holdout(db, a, decision_ref="d")

    def test_unknown_partition_refused(self, db):
        with pytest.raises(tr.TrainingError, match="not a training partition"):
            tr.consume_holdout(db, "0" * 64, decision_ref="d")

    def test_consumptions_are_append_only(self, db, ledger):
        h = self._holdout(db, ledger)
        tr.consume_holdout(db, h, decision_ref="promo-1")
        con = sqlite3.connect(db)
        try:
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                con.execute("DELETE FROM holdout_consumptions")
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                con.execute("UPDATE holdout_consumptions SET decision_ref='x'")
        finally:
            con.close()

    def test_unspent_holdout(self, db, ledger):
        h = self._holdout(db, ledger)
        assert tr.is_holdout_spent(db, h) is False
        assert tr.holdout_consumptions(db, h) == []


# ===========================================================================
# Immutability
# ===========================================================================

class TestImmutability:
    def test_partition_cannot_be_updated(self, db, ledger):
        a, _, _ = _three_partitions(db, ledger)
        _register(db, a, "train")
        con = sqlite3.connect(db)
        try:
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                con.execute("UPDATE training_partitions SET role='holdout'")
        finally:
            con.close()

    def test_partition_cannot_be_deleted(self, db, ledger):
        a, _, _ = _three_partitions(db, ledger)
        _register(db, a, "train")
        con = sqlite3.connect(db)
        try:
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                con.execute("DELETE FROM training_partitions")
        finally:
            con.close()

    def test_role_cannot_be_relabelled_after_consumption(self, db, ledger):
        """The abuse the single-use rule prevents: relabelling a holdout once
        its result is known."""
        _, _, c = _three_partitions(db, ledger)
        _register(db, c, "holdout")
        tr.consume_holdout(db, c, decision_ref="promo-1")
        con = sqlite3.connect(db)
        try:
            with pytest.raises(sqlite3.IntegrityError):
                con.execute("UPDATE training_partitions SET role='train' "
                            "WHERE spec_hash=?", (c,))
        finally:
            con.close()

    def test_db_check_rejects_an_invalid_role(self, db, ledger):
        a, _, _ = _three_partitions(db, ledger)
        con = sqlite3.connect(db)
        try:
            with pytest.raises(sqlite3.IntegrityError):
                con.execute(
                    "INSERT INTO training_partitions (spec_hash, sibling_group,"
                    " role, window_start, window_end, snapshot_sha256,"
                    " registered_at, registered_by) "
                    "VALUES (?, 'g', 'test', '2021-01-01', '2021-12-31', 's', 'now', 'r')",
                    (a,))
        finally:
            con.close()


# ===========================================================================
# TV3 sabotage
# ===========================================================================

class TestTV3Sabotage:
    def test_rf4_refusal_is_not_vacuous(self, db, ledger):
        """If check_training_admissibility always returned [], every P4-6 test
        would pass for the wrong reason."""
        h = _pinned(db, ledger)
        spec = reg.get_specification(db, h)
        meet = val.compose_admissibility(good_inputs())
        assert tr.check_training_admissibility(spec["declaration"], good_inputs(), meet)

    def test_rf4_does_not_refuse_everything(self, db, ledger):
        h = _pinned(db, ledger)
        spec = reg.get_specification(db, h)
        inputs = [ev(ceiling=adm(p=Provenance.LIVE_FORWARD))]
        meet = val.compose_admissibility(inputs)
        assert tr.check_training_admissibility(spec["declaration"], inputs, meet) == []

    def test_overlap_guard_is_not_vacuous(self, db, ledger):
        a = _pinned(db, ledger, windowed_declaration("2021-01-01", "2024-06-30", "a"),
                    lineage="g1")
        b = _pinned(db, ledger, windowed_declaration("2024-01-01", "2025-01-01", "b"),
                    lineage="g2")
        _register(db, a, "train")
        with pytest.raises(tr.PartitionOverlapError):
            _register(db, b, "validation")

    def test_pin_guard_is_not_vacuous(self, db, ledger):
        h = _sealed(db, ledger)
        with pytest.raises(tr.NotPinnedError):
            _register(db, h, "train")

    def test_single_use_guard_is_not_vacuous(self, db, ledger):
        _, _, c = _three_partitions(db, ledger)
        _register(db, c, "holdout")
        tr.consume_holdout(db, c, decision_ref="d1")
        with pytest.raises(tr.HoldoutSpentError):
            tr.consume_holdout(db, c, decision_ref="d2")


# ===========================================================================
# Regression — 4.1–4.9 invariants
# ===========================================================================

class TestRegression:
    def test_rf1_to_rf6_unchanged(self):
        assert set(res.DEFERRED_REFUSALS) == {"RF4"}
        assert val.DEFERRED_CHECKS == {}

    def test_no_operational_import(self):
        src = (ROOT / "research/dataset_builder/training.py").read_text(encoding="utf-8")
        for n in ast.walk(ast.parse(src)):
            if isinstance(n, ast.ImportFrom) and n.module:
                assert n.module.split(".")[0] != "src"
            elif isinstance(n, ast.Import):
                assert all(a.name.split(".")[0] != "src" for a in n.names)

    def test_registry_phase_untouched(self, db, ledger):
        a, _, _ = _three_partitions(db, ledger)
        _register(db, a, "train")
        assert reg.get_specification(db, a)["phase"] == "Sealed"

    def test_materialisation_still_deterministic(self, db, ledger):
        a, _, _ = _three_partitions(db, ledger)
        _register(db, a, "train")
        assert (mat.materialise(db, a, rich_provider()).snapshot.sha256
                == mat.materialise(db, a, rich_provider()).snapshot.sha256)

    def test_pin_still_verifies_l1(self, db, ledger):
        a, _, _ = _three_partitions(db, ledger)
        _register(db, a, "train")
        assert mat.verify_l1(db, a, rich_provider()).ok is True

    def test_experiment_datasets_still_work(self, db, ledger):
        """§15.3: the roles are orthogonal — a Training Dataset may also be an
        Experiment Dataset."""
        a, _, _ = _three_partitions(db, ledger)
        _register(db, a, "train")
        e = ex.register_experiment(db, a, trial_registration="t1",
                                   denominator_position=1, denominator_size=34,
                                   created_by="rohith")
        ex.record_use(db, e.experiment_id, consuming_ref="run-1")
        assert ex.use_count(db, e.experiment_id) == 1

    def test_citation_graph_intact(self, db, ledger):
        a, _, _ = _three_partitions(db, ledger)
        _register(db, a, "train")
        ok, errors = ci.verify_bidirectional_consistency(db)
        assert ok is True and errors == []

    def test_sealed_immutability_still_enforced(self, db, ledger):
        a, _, _ = _three_partitions(db, ledger)
        _register(db, a, "train")
        con = sqlite3.connect(db)
        try:
            with pytest.raises(sqlite3.IntegrityError, match="DG-3"):
                con.execute("UPDATE sealed_datasets SET sealed_by='x'")
        finally:
            con.close()

    def test_receipt_chain_unaffected(self, db, ledger):
        a, _, _ = _three_partitions(db, ledger)
        _register(db, a, "train")
        assert rl.verify_chain(ledger).ok is True

    def test_result_invariance_still_holds(self):
        import hashlib, json
        baseline = json.loads((ROOT / "BASELINE_46.json").read_text())
        for rel, was in baseline.items():
            assert hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() == was, rel

    def test_no_other_store_touched(self, tmp_path):
        p = tmp_path / "d.db"; led = tmp_path / "l.db"
        reg.init_db(p); ci.init_db(p); sl.init_db(p)
        mat.init_db(p); ex.init_db(p); tr.init_db(p); rl.init_db(led)
        h = _pinned(p, led)
        _register(p, h, "train")
        assert {f.name for f in tmp_path.rglob("*.db")} == {"d.db", "l.db"}
