"""WBS 4.11 — retirement, supersession, and `--as-of` reconstruction (L3)
(ADR-I012 §10.2, §11, §16 L3, DG-5; resolves HZ-4.5-2).

The load-bearing claim is that history is recorded by DATABASE TRIGGERS rather
than by Python calls, so a phase change made through *any* path — including a
direct `sqlite3` write that bypasses this package entirely — is captured.
`TestTriggerDrivenHistory` proves that by writing behind the module's back.
"""

from __future__ import annotations

import ast
import sqlite3
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "research", ROOT / "research" / "admissibility",
          ROOT / "research" / "evidence_library"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import receipt_ledger as rl  # noqa: E402
from lattice import Provenance  # noqa: E402

from dataset_builder import citation_index as ci  # noqa: E402
from dataset_builder import experiments as ex  # noqa: E402
from dataset_builder import history as hist  # noqa: E402
from dataset_builder import materialisation as mat  # noqa: E402
from dataset_builder import registry as reg  # noqa: E402
from dataset_builder import resolution as res  # noqa: E402
from dataset_builder import sealing as sl  # noqa: E402
from dataset_builder import training as tr  # noqa: E402
from dataset_builder import validation as val  # noqa: E402
from tests.test_dataset_registry import valid_declaration  # noqa: E402
from tests.test_dataset_resolution import good_resolver  # noqa: E402
from tests.test_dataset_validation import adm, good_inputs  # noqa: E402
from tests.test_materialisation import rich_provider  # noqa: E402
from tests.test_training import windowed_declaration  # noqa: E402


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    p = tmp_path / "dataset_builder.db"
    reg.init_db(p); ci.init_db(p); sl.init_db(p)
    mat.init_db(p); ex.init_db(p); tr.init_db(p); hist.init_db(p)
    return p


@pytest.fixture()
def ledger(tmp_path: Path) -> Path:
    p = tmp_path / "ledger.db"
    rl.init_db(p)
    return p


def _declared(db, decl=None, lineage="line-a") -> str:
    d = decl if decl is not None else valid_declaration()
    return res.declare(db, d, lineage=lineage, declared_by="rohith").spec_hash


def _sealed(db, ledger, decl=None, lineage="line-a") -> str:
    h = _declared(db, decl, lineage)
    res.resolve(db, h, good_resolver())
    report = val.validate(db, h, good_inputs())
    sl.seal_dataset(db, h, report, sealed_by="rohith", ledger_db=ledger)
    return h


def _pinned(db, ledger, decl=None, lineage="line-a") -> str:
    h = _sealed(db, ledger, decl, lineage)
    r = mat.materialise(db, h, rich_provider())
    mat.pin_snapshot(db, h, r, pinned_by="rohith")
    return h


def _phases(db, spec_hash):
    return [t.to_phase for t in hist.phase_history(db, spec_hash)]


# ---------------------------------------------------------------------------
# Build and scope
# ---------------------------------------------------------------------------

class TestBuildAndScope:
    def test_module_imports_no_production_source(self):
        src = (ROOT / "research" / "dataset_builder" / "history.py").read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("src."), node.module
            if isinstance(node, ast.Import):
                for a in node.names:
                    assert not a.name.startswith("src."), a.name

    def test_init_db_is_idempotent(self, db):
        hist.init_db(db); hist.init_db(db)
        con = sqlite3.connect(str(db))
        n = con.execute("SELECT COUNT(*) FROM dataset_transitions").fetchone()[0]
        con.close()
        assert n == 0

    def test_init_db_refuses_a_non_builder_store(self, tmp_path):
        p = tmp_path / "empty.db"
        sqlite3.connect(str(p)).close()
        with pytest.raises(reg.NotADatasetBuilderStore):
            hist.init_db(p)

    def test_history_functions_refuse_a_store_without_the_ledger(self, tmp_path):
        p = tmp_path / "no_hist.db"
        reg.init_db(p); ci.init_db(p)
        with pytest.raises(reg.NotADatasetBuilderStore):
            hist.phase_history(p, "deadbeef")

    def test_no_repair_function_exists(self):
        """Assert-never-repair: no backfill of missing history."""
        src = (ROOT / "research" / "dataset_builder" / "history.py").read_text()
        tree = ast.parse(src)
        names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        for bad in ("backfill", "repair", "rebuild_history", "fix_history"):
            assert not any(bad in n for n in names), f"{bad} must not exist"


# ---------------------------------------------------------------------------
# The ledger is trigger-driven, not call-driven
# ---------------------------------------------------------------------------

class TestTriggerDrivenHistory:
    def test_declaration_records_a_genesis_transition(self, db):
        h = _declared(db)
        history = hist.phase_history(db, h)
        assert len(history) == 1
        assert history[0].from_phase is None
        assert history[0].to_phase == "Declared"
        assert history[0].source == "declare"

    def test_every_lifecycle_step_is_recorded(self, db, ledger):
        h = _sealed(db, ledger)
        assert _phases(db, h) == ["Declared", "Resolved", "Validated", "Sealed"]

    def test_sealing_is_recorded_though_it_bypasses_registry_transition(
        self, db, ledger
    ):
        """Sealing writes `phase` with its own UPDATE, not via `_transition`.

        A Python-side hook in `registry._transition` would have missed this
        entirely. The trigger does not.
        """
        h = _sealed(db, ledger)
        seal = [t for t in hist.phase_history(db, h) if t.to_phase == "Sealed"]
        assert len(seal) == 1
        assert seal[0].from_phase == "Validated"

    def test_a_direct_sqlite_write_is_still_captured(self, db, ledger):
        """The strongest form of the claim: history cannot be bypassed."""
        h = _sealed(db, ledger)
        con = sqlite3.connect(str(db))
        con.execute(
            "UPDATE dataset_specifications SET phase = 'Retired' WHERE spec_hash = ?",
            (h,),
        )
        con.commit(); con.close()
        assert _phases(db, h)[-1] == "Retired"
        assert hist.phase_history(db, h)[-1].from_phase == "Sealed"

    def test_a_no_op_phase_write_records_nothing(self, db):
        """`WHEN OLD.phase <> NEW.phase` — history records changes, not writes.

        Exercised on a Declared row: WBS 4.1's DATASET_TAMPER guard refuses a
        Sealed row's no-op write outright, so a sealed spec cannot reach this
        trigger at all.
        """
        h = _declared(db)
        before = len(hist.phase_history(db, h))
        con = sqlite3.connect(str(db))
        con.execute(
            "UPDATE dataset_specifications SET phase = 'Declared' "
            "WHERE spec_hash = ?", (h,),
        )
        con.commit(); con.close()
        assert len(hist.phase_history(db, h)) == before

    def test_transitions_are_append_only_no_update(self, db):
        _declared(db)
        con = sqlite3.connect(str(db))
        with pytest.raises(sqlite3.IntegrityError, match="HISTORY_TAMPER"):
            con.execute("UPDATE dataset_transitions SET to_phase = 'Sealed'")
        con.close()

    def test_transitions_are_append_only_no_delete(self, db):
        _declared(db)
        con = sqlite3.connect(str(db))
        with pytest.raises(sqlite3.IntegrityError, match="HISTORY_TAMPER"):
            con.execute("DELETE FROM dataset_transitions")
        con.close()

    def test_history_is_ordered_by_ts_then_id(self, db, ledger):
        """Two transitions can share a millisecond; `id` breaks the tie."""
        h = _sealed(db, ledger)
        history = hist.phase_history(db, h)
        assert [t.id for t in history] == sorted(t.id for t in history)
        assert [t.ts for t in history] == sorted(t.ts for t in history)

    def test_each_transition_chains_from_the_previous(self, db, ledger):
        h = _sealed(db, ledger)
        history = hist.phase_history(db, h)
        for prev, nxt in zip(history, history[1:]):
            assert nxt.from_phase == prev.to_phase

    def test_independent_datasets_have_independent_history(self, db, ledger):
        a = _sealed(db, ledger, lineage="line-a")
        b = _declared(db, windowed_declaration("2022-01-01", "2022-12-31", "b"),
                      lineage="line-b")
        assert len(hist.phase_history(db, a)) == 4
        assert len(hist.phase_history(db, b)) == 1


# ---------------------------------------------------------------------------
# --as-of derivation (L3)
# ---------------------------------------------------------------------------

class TestAsOfDerivation:
    def test_phase_now_matches_the_registry(self, db, ledger):
        h = _sealed(db, ledger)
        assert hist.derive_phase_at(db, h) == "Sealed"
        assert reg.get_specification(db, h)["phase"] == "Sealed"

    def test_phase_at_each_past_instant(self, db, ledger):
        h = _sealed(db, ledger)
        for t in hist.phase_history(db, h):
            assert hist.derive_phase_at(db, h, t.ts) == t.to_phase

    def test_phase_before_declaration_is_none(self, db, ledger):
        h = _sealed(db, ledger)
        assert hist.derive_phase_at(db, h, "2020-01-01T00:00:00.000000Z") is None

    def test_phase_is_derived_from_the_ledger_not_the_mutable_column(
        self, db, ledger
    ):
        """The whole point of L3: reading `phase` would answer 'now' always."""
        h = _sealed(db, ledger)
        declared_ts = hist.phase_history(db, h)[0].ts
        assert hist.derive_phase_at(db, h, declared_ts) == "Declared"
        assert reg.get_specification(db, h)["phase"] == "Sealed"

    def test_as_of_is_inclusive_of_its_own_instant(self, db, ledger):
        h = _sealed(db, ledger)
        sealed_ts = hist.phase_history(db, h)[-1].ts
        assert hist.derive_phase_at(db, h, sealed_ts) == "Sealed"

    def test_a_later_retirement_does_not_change_the_past(self, db, ledger):
        h = _sealed(db, ledger)
        sealed_ts = hist.phase_history(db, h)[-1].ts
        time.sleep(0.005)
        hist.retire_dataset(db, h, actor="rohith")
        assert hist.derive_phase_at(db, h, sealed_ts) == "Sealed"
        assert hist.derive_phase_at(db, h) == "Retired"

    def test_unknown_spec_has_no_phase(self, db):
        assert hist.derive_phase_at(db, "0" * 64) is None


class TestRegistryReconstruction:
    def test_reconstructs_the_whole_registry_now(self, db, ledger):
        a = _sealed(db, ledger, lineage="a")
        b = _declared(db, windowed_declaration("2022-01-01", "2022-06-30", "b"),
                      lineage="b")
        report = hist.reconstruct_registry_at(db)
        assert report.phases[a] == "Sealed"
        assert report.phases[b] == "Declared"

    def test_a_dataset_declared_later_is_absent_not_defaulted(self, db, ledger):
        a = _sealed(db, ledger, lineage="a")
        cutoff = hist.phase_history(db, a)[-1].ts
        time.sleep(0.005)
        b = _declared(db, windowed_declaration("2022-01-01", "2022-06-30", "b"),
                      lineage="b")
        report = hist.reconstruct_registry_at(db, cutoff)
        assert a in report.phases
        assert b not in report.phases
        assert b in report.absent

    def test_citable_set_is_reconstructed_at_the_instant(self, db, ledger):
        a = _sealed(db, ledger, lineage="a")
        history = hist.phase_history(db, a)
        assert a not in hist.reconstruct_registry_at(db, history[0].ts).citable()
        assert a in hist.reconstruct_registry_at(db, history[-1].ts).citable()

    def test_reconstruct_specification_reports_reconstructed_phase(
        self, db, ledger
    ):
        h = _sealed(db, ledger)
        declared_ts = hist.phase_history(db, h)[0].ts
        record = hist.reconstruct_specification_at(db, h, declared_ts)
        assert record["phase"] == "Declared"
        assert record["phase_is_reconstructed"] is True
        assert record["was_sealed"] is False

    def test_reconstruct_specification_reports_seal_when_it_had_happened(
        self, db, ledger
    ):
        h = _sealed(db, ledger)
        record = hist.reconstruct_specification_at(db, h)
        assert record["was_sealed"] is True

    def test_reconstruct_specification_is_none_before_existence(self, db, ledger):
        h = _sealed(db, ledger)
        assert hist.reconstruct_specification_at(
            db, h, "2020-01-01T00:00:00.000000Z"
        ) is None

    def test_declaration_body_is_identical_across_reconstructions(
        self, db, ledger
    ):
        """DG-3: only `phase` is time-varying; the body is content-addressed."""
        h = _sealed(db, ledger)
        history = hist.phase_history(db, h)
        bodies = {
            hist.reconstruct_specification_at(db, h, t.ts)["declaration_json"]
            for t in history
        }
        assert len(bodies) == 1


class TestHistoryCompleteness:
    def test_a_complete_store_reports_ok(self, db, ledger):
        _sealed(db, ledger)
        ok, errors = hist.history_completeness(db)
        assert ok and errors == []

    def test_a_spec_predating_the_triggers_is_reported_not_repaired(
        self, tmp_path
    ):
        p = tmp_path / "late.db"
        reg.init_db(p); ci.init_db(p)
        h = _declared(p)
        hist.init_db(p)
        ok, errors = hist.history_completeness(p)
        assert not ok
        assert h[:12] in errors[0]
        assert "not reconstructable" in errors[0]
        assert hist.phase_history(p, h) == []

    def test_a_gap_is_not_silently_filled(self, tmp_path):
        p = tmp_path / "late.db"
        reg.init_db(p); ci.init_db(p)
        h = _declared(p)
        hist.init_db(p)
        assert hist.derive_phase_at(p, h) is None


# ---------------------------------------------------------------------------
# Retirement (DG-5) — discharges the deferral registry.retire() recorded
# ---------------------------------------------------------------------------

class TestRetirement:
    def test_an_uncited_sealed_dataset_retires(self, db, ledger):
        h = _sealed(db, ledger)
        report = hist.retire_dataset(db, h, actor="rohith")
        assert report.retired
        assert reg.get_specification(db, h)["phase"] == "Retired"

    def test_retirement_is_recorded_in_history(self, db, ledger):
        h = _sealed(db, ledger)
        hist.retire_dataset(db, h, actor="rohith")
        assert _phases(db, h)[-1] == "Retired"

    def test_retirement_refuses_when_cited_by_a_run(self, db, ledger):
        h = _sealed(db, ledger)
        e = ex.register_experiment(
            db, h, trial_registration="t1", denominator_position=1,
            denominator_size=34, created_by="rohith",
        )
        ex.record_use(db, e.experiment_id, consuming_ref="run-77",
                      purpose="evaluation")
        with pytest.raises(hist.RetirementRefused, match="run-77"):
            hist.retire_dataset(db, h, actor="rohith")

    def test_retirement_refuses_when_cited_by_a_decision(self, db, ledger):
        a, b, c = (_pinned(db, ledger, windowed_declaration(s, e, t), lineage=t)
                   for s, e, t in (("2021-01-01", "2023-12-31", "train"),
                                   ("2024-01-01", "2024-12-31", "val"),
                                   ("2025-01-01", "2025-05-31", "hold")))
        for spec, role in ((a, "train"), (b, "validation"), (c, "holdout")):
            tr.register_partition(db, spec, sibling_group="g", role=role,
                                  registered_by="rohith", computed_meet=adm(p=Provenance.MAINNET))
        tr.consume_holdout(db, c, decision_ref="promo-1")
        with pytest.raises(hist.RetirementRefused, match="promo-1"):
            hist.retire_dataset(db, c, actor="rohith")

    def test_retirement_refuses_when_cited_by_a_model(self, db, ledger):
        h = _pinned(db, ledger)
        tr.register_partition(db, h, sibling_group="g", role="train",
                              registered_by="rohith", computed_meet=adm(p=Provenance.MAINNET))
        with pytest.raises(hist.RetirementRefused, match="DG-5"):
            hist.retire_dataset(db, h, actor="rohith")

    def test_the_refusal_names_every_citer(self, db, ledger):
        h = _sealed(db, ledger)
        e = ex.register_experiment(
            db, h, trial_registration="t1", denominator_position=1,
            denominator_size=34, created_by="rohith",
        )
        ex.record_use(db, e.experiment_id, consuming_ref="run-a", purpose="evaluation")
        ex.record_use(db, e.experiment_id, consuming_ref="run-b", purpose="evaluation")
        with pytest.raises(hist.RetirementRefused) as exc:
            hist.retire_dataset(db, h, actor="rohith")
        assert "run-a" in str(exc.value) and "run-b" in str(exc.value)

    def test_a_refused_retirement_leaves_the_phase_untouched(self, db, ledger):
        h = _sealed(db, ledger)
        e = ex.register_experiment(
            db, h, trial_registration="t1", denominator_position=1,
            denominator_size=34, created_by="rohith",
        )
        ex.record_use(db, e.experiment_id, consuming_ref="run-1", purpose="evaluation")
        before = len(hist.phase_history(db, h))
        with pytest.raises(hist.RetirementRefused):
            hist.retire_dataset(db, h, actor="rohith")
        assert reg.get_specification(db, h)["phase"] == "Sealed"
        assert len(hist.phase_history(db, h)) == before

    def test_a_binding_without_uses_does_not_block_but_is_reported(
        self, db, ledger
    ):
        """DG-5 names runs, models and decisions. A trial binding is none of
        those yet — so it is surfaced, not treated as a refusal."""
        h = _sealed(db, ledger)
        e = ex.register_experiment(
            db, h, trial_registration="t1", denominator_position=1,
            denominator_size=34, created_by="rohith",
        )
        report = hist.retire_dataset(db, h, actor="rohith")
        assert report.retired
        assert e.experiment_id in report.bindings_without_uses

    def test_is_retirable_reports_without_attempting(self, db, ledger):
        h = _sealed(db, ledger)
        e = ex.register_experiment(
            db, h, trial_registration="t1", denominator_position=1,
            denominator_size=34, created_by="rohith",
        )
        ex.record_use(db, e.experiment_id, consuming_ref="r", purpose="evaluation")
        ok, errors = hist.is_retirable(db, h)
        assert not ok and "DG-5" in errors[0]
        assert reg.get_specification(db, h)["phase"] == "Sealed"

    def test_retirement_requires_an_actor(self, db, ledger):
        h = _sealed(db, ledger)
        with pytest.raises(hist.HistoryError, match="actor"):
            hist.retire_dataset(db, h, actor="  ")

    def test_retirement_of_an_unknown_spec_refuses(self, db):
        with pytest.raises(reg.UnknownSpecificationError):
            hist.retire_dataset(db, "0" * 64, actor="rohith")

    def test_retiring_an_unsealed_dataset_refuses_on_the_arc(self, db):
        h = _declared(db)
        with pytest.raises(reg.TransitionError):
            hist.retire_dataset(db, h, actor="rohith")

    def test_retirement_preserves_traceability(self, db, ledger):
        """A retired dataset keeps its declaration, seal and citations."""
        h = _sealed(db, ledger)
        cites_before = ci.cites(db, h)
        hist.retire_dataset(db, h, actor="rohith")
        assert reg.get_specification(db, h) is not None
        assert ci.cites(db, h) == cites_before
        con = sqlite3.connect(str(db))
        n = con.execute(
            "SELECT COUNT(*) FROM sealed_datasets WHERE spec_hash = ?", (h,)
        ).fetchone()[0]
        con.close()
        assert n == 1

    def test_a_retired_dataset_is_no_longer_citable(self, db, ledger):
        h = _sealed(db, ledger)
        hist.retire_dataset(db, h, actor="rohith")
        assert not reg.is_citable(db, h)

    def test_but_its_past_citability_is_still_reconstructable(self, db, ledger):
        h = _sealed(db, ledger)
        sealed_ts = hist.phase_history(db, h)[-1].ts
        time.sleep(0.005)
        hist.retire_dataset(db, h, actor="rohith")
        assert h in hist.reconstruct_registry_at(db, sealed_ts).citable()


# ---------------------------------------------------------------------------
# Supersession (§10.2)
# ---------------------------------------------------------------------------

class TestSupersession:
    def _pair(self, db, ledger):
        old = _sealed(db, ledger, lineage="line-a")
        new = _sealed(db, ledger,
                      windowed_declaration("2021-01-01", "2024-12-31", "v2"),
                      lineage="line-a")
        return old, new

    def test_supersession_succeeds(self, db, ledger):
        old, new = self._pair(db, ledger)
        result = hist.supersede_dataset(db, old, new,
                                        change_class="extension", actor="rohith")
        assert result["superseded_by"] == new
        assert reg.get_specification(db, old)["phase"] == "Superseded"

    def test_the_predecessor_remains_citable(self, db, ledger):
        old, new = self._pair(db, ledger)
        hist.supersede_dataset(db, old, new, change_class="extension",
                               actor="rohith")
        assert reg.is_citable(db, old)

    def test_the_predecessor_remains_resolvable(self, db, ledger):
        old, new = self._pair(db, ledger)
        hist.supersede_dataset(db, old, new, change_class="refinement",
                               actor="rohith")
        assert reg.get_specification(db, old) is not None

    def test_the_predecessors_sealed_record_is_untouched(self, db, ledger):
        old, new = self._pair(db, ledger)
        con = sqlite3.connect(str(db)); con.row_factory = sqlite3.Row
        before = dict(con.execute(
            "SELECT * FROM sealed_datasets WHERE spec_hash = ?", (old,)
        ).fetchone())
        con.close()
        hist.supersede_dataset(db, old, new, change_class="extension",
                               actor="rohith")
        con = sqlite3.connect(str(db)); con.row_factory = sqlite3.Row
        after = dict(con.execute(
            "SELECT * FROM sealed_datasets WHERE spec_hash = ?", (old,)
        ).fetchone())
        con.close()
        assert after == before

    def test_the_predecessors_citations_are_untouched(self, db, ledger):
        old, new = self._pair(db, ledger)
        before = ci.cites(db, old)
        hist.supersede_dataset(db, old, new, change_class="extension",
                               actor="rohith")
        assert ci.cites(db, old) == before

    def test_supersession_appends_history_and_rewrites_none(self, db, ledger):
        old, new = self._pair(db, ledger)
        before = hist.phase_history(db, old)
        hist.supersede_dataset(db, old, new, change_class="extension",
                               actor="rohith")
        after = hist.phase_history(db, old)
        assert after[:len(before)] == before
        assert after[-1].to_phase == "Superseded"

    def test_an_experiment_can_still_bind_a_superseded_dataset(self, db, ledger):
        old, new = self._pair(db, ledger)
        hist.supersede_dataset(db, old, new, change_class="extension",
                               actor="rohith")
        e = ex.register_experiment(
            db, old, trial_registration="t1", denominator_position=1,
            denominator_size=34, created_by="rohith",
        )
        assert e.experiment_id

    def test_a_superseded_dataset_that_is_cited_cannot_be_retired(
        self, db, ledger
    ):
        old, new = self._pair(db, ledger)
        hist.supersede_dataset(db, old, new, change_class="extension",
                               actor="rohith")
        e = ex.register_experiment(
            db, old, trial_registration="t1", denominator_position=1,
            denominator_size=34, created_by="rohith",
        )
        ex.record_use(db, e.experiment_id, consuming_ref="run-9",
                      purpose="evaluation")
        with pytest.raises(hist.RetirementRefused):
            hist.retire_dataset(db, old, actor="rohith")

    def test_supersession_requires_an_actor(self, db, ledger):
        old, new = self._pair(db, ledger)
        with pytest.raises(hist.HistoryError, match="actor"):
            hist.supersede_dataset(db, old, new, change_class="extension",
                                   actor="")

    def test_redefinition_is_still_refused_by_the_registry(self, db, ledger):
        old, new = self._pair(db, ledger)
        with pytest.raises(reg.DatasetRegistryError):
            hist.supersede_dataset(db, old, new, change_class="redefinition",
                                   actor="rohith")

    def test_verify_supersession_preserved_detects_a_lost_predecessor(
        self, db, ledger
    ):
        old, new = self._pair(db, ledger)
        hist.supersede_dataset(db, old, new, change_class="extension",
                               actor="rohith")
        ok, errors = hist.verify_supersession_preserved(db, old)
        assert ok and errors == []
        ok2, errors2 = hist.verify_supersession_preserved(db, "0" * 64)
        assert not ok2 and "resolvable" in errors2[0]

    def test_the_past_of_a_superseded_dataset_is_still_sealed(self, db, ledger):
        old, new = self._pair(db, ledger)
        sealed_ts = hist.phase_history(db, old)[-1].ts
        time.sleep(0.005)
        hist.supersede_dataset(db, old, new, change_class="extension",
                               actor="rohith")
        assert hist.derive_phase_at(db, old, sealed_ts) == "Sealed"
        assert hist.derive_phase_at(db, old) == "Superseded"


# ---------------------------------------------------------------------------
# HZ-4.5-2 — stale ceilings are flagged, never recomputed
# ---------------------------------------------------------------------------

class TestStaleCeilings:
    def test_a_flag_is_recorded(self, db, ledger):
        h = _sealed(db, ledger)
        fid = hist.flag_stale_ceiling(
            db, h, cited_kind="feature", cited_id="f-1",
            reason="input superseded", flagged_by="rohith",
        )
        assert fid > 0
        flags = hist.stale_ceilings(db, h)
        assert len(flags) == 1 and flags[0].cited_id == "f-1"

    def test_flagging_does_not_mutate_the_sealed_record(self, db, ledger):
        """The resolution of HZ-4.5-2: record the doubt beside the seal."""
        h = _sealed(db, ledger)
        con = sqlite3.connect(str(db)); con.row_factory = sqlite3.Row
        before = dict(con.execute(
            "SELECT * FROM sealed_datasets WHERE spec_hash = ?", (h,)
        ).fetchone())
        con.close()
        hist.flag_stale_ceiling(db, h, cited_kind="label", cited_id="l-1",
                                reason="matured", flagged_by="rohith")
        con = sqlite3.connect(str(db)); con.row_factory = sqlite3.Row
        after = dict(con.execute(
            "SELECT * FROM sealed_datasets WHERE spec_hash = ?", (h,)
        ).fetchone())
        con.close()
        assert after == before

    def test_no_recompute_function_exists(self):
        src = (ROOT / "research" / "dataset_builder" / "history.py").read_text()
        tree = ast.parse(src)
        names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        for bad in ("recompute_ceiling", "update_ceiling", "recompute"):
            assert not any(bad in n for n in names), f"{bad} must not exist"

    def test_flags_are_append_only(self, db, ledger):
        h = _sealed(db, ledger)
        hist.flag_stale_ceiling(db, h, cited_kind="feature", cited_id="f-1",
                                reason="r", flagged_by="rohith")
        con = sqlite3.connect(str(db))
        with pytest.raises(sqlite3.IntegrityError, match="HISTORY_TAMPER"):
            con.execute("DELETE FROM ceiling_staleness_flags")
        with pytest.raises(sqlite3.IntegrityError, match="HISTORY_TAMPER"):
            con.execute("UPDATE ceiling_staleness_flags SET reason = 'x'")
        con.close()

    def test_an_identical_flag_is_idempotent(self, db, ledger):
        h = _sealed(db, ledger)
        a = hist.flag_stale_ceiling(db, h, cited_kind="feature", cited_id="f-1",
                                    reason="r", flagged_by="rohith")
        b = hist.flag_stale_ceiling(db, h, cited_kind="feature", cited_id="f-1",
                                    reason="r", flagged_by="rohith")
        assert a == b
        assert len(hist.stale_ceilings(db, h)) == 1

    def test_a_flag_requires_a_flagger_and_a_reason(self, db, ledger):
        h = _sealed(db, ledger)
        with pytest.raises(hist.HistoryError, match="flagger"):
            hist.flag_stale_ceiling(db, h, cited_kind="f", cited_id="x",
                                    reason="r", flagged_by=" ")
        with pytest.raises(hist.HistoryError, match="reason"):
            hist.flag_stale_ceiling(db, h, cited_kind="f", cited_id="x",
                                    reason="", flagged_by="rohith")

    def test_a_flag_on_an_unknown_spec_refuses(self, db):
        with pytest.raises(reg.UnknownSpecificationError):
            hist.flag_stale_ceiling(db, "0" * 64, cited_kind="f", cited_id="x",
                                    reason="r", flagged_by="rohith")

    def test_detect_uses_the_citation_index_blast_radius(self, db, ledger):
        h = _sealed(db, ledger)
        cited = ci.cites(db, h)
        assert cited, "fixture must cite at least one input"
        target = cited[0]
        flagged = hist.detect_stale_ceilings(
            db, cited_kind=target.cited_kind, cited_id=target.cited_id,
            flagged_by="rohith",
        )
        assert h in flagged
        assert len(hist.stale_ceilings(db, h)) == 1

    def test_detect_skips_datasets_that_are_not_citable(self, db, ledger):
        h = _sealed(db, ledger)
        cited = ci.cites(db, h)[0]
        hist.retire_dataset(db, h, actor="rohith")
        flagged = hist.detect_stale_ceilings(
            db, cited_kind=cited.cited_kind, cited_id=cited.cited_id,
            flagged_by="rohith",
        )
        assert h not in flagged

    def test_detect_on_an_uncited_input_flags_nothing(self, db, ledger):
        _sealed(db, ledger)
        assert hist.detect_stale_ceilings(
            db, cited_kind="feature", cited_id="never-cited", flagged_by="r"
        ) == []

    def test_stale_ceilings_lists_across_all_datasets(self, db, ledger):
        a = _sealed(db, ledger, lineage="a")
        b = _sealed(db, ledger,
                    windowed_declaration("2022-01-01", "2022-12-31", "b"),
                    lineage="b")
        hist.flag_stale_ceiling(db, a, cited_kind="f", cited_id="x",
                                reason="r", flagged_by="rohith")
        hist.flag_stale_ceiling(db, b, cited_kind="f", cited_id="x",
                                reason="r", flagged_by="rohith")
        assert len(hist.stale_ceilings(db)) == 2


# ---------------------------------------------------------------------------
# L3 composes with L1/L2
# ---------------------------------------------------------------------------

class TestL3Composition:
    def test_a_pinned_dataset_composes(self, db, ledger):
        h = _pinned(db, ledger)
        ok, errors = hist.verify_l3_composes(db, h)
        assert ok and errors == []

    def test_a_sealed_but_unpinned_dataset_is_reported(self, db, ledger):
        h = _sealed(db, ledger)
        ok, errors = hist.verify_l3_composes(db, h)
        assert not ok
        assert "L1 cannot recover its bytes" in errors[0]

    def test_a_dataset_absent_at_the_instant_is_reported(self, db, ledger):
        h = _pinned(db, ledger)
        ok, errors = hist.verify_l3_composes(
            db, h, "2020-01-01T00:00:00.000000Z"
        )
        assert not ok and "did not exist" in errors[0]

    def test_l1_reverification_is_unavailable_after_retirement(self, db, ledger):
        """Retirement ends L1 *re-verification*, and that is survivable.

        §8.2 gates materialisation on a Sealed phase, so `verify_l1` refuses a
        Retired dataset. The pin row survives — traceability is intact — but the
        bytes can no longer be re-derived through the governed path.

        This is safe only because DG-5 makes it unreachable for anything that
        matters: a dataset any run, model or decision cited can never be
        retired, so no evaluation can be stranded by it. Recorded as
        `TD-4.11-2` rather than left as an assumption.
        """
        h = _pinned(db, ledger)
        hist.retire_dataset(db, h, actor="rohith")
        with pytest.raises(mat.NotMaterialisableError, match="Retired"):
            mat.verify_l1(db, h, rich_provider())

        con = sqlite3.connect(str(db))
        n = con.execute(
            "SELECT COUNT(*) FROM snapshot_pins WHERE spec_hash = ?", (h,)
        ).fetchone()[0]
        con.close()
        assert n == 1, "the pin must survive retirement even though L1 cannot re-run"

    def test_l2_rematerialisation_is_unaffected_by_supersession(
        self, db, ledger
    ):
        old = _pinned(db, ledger, lineage="line-a")
        new = _sealed(db, ledger,
                      windowed_declaration("2021-01-01", "2024-12-31", "v2"),
                      lineage="line-a")
        hist.supersede_dataset(db, old, new, change_class="extension",
                               actor="rohith")
        assert mat.verify_l1(db, old, rich_provider())


# ---------------------------------------------------------------------------
# TV3 — non-vacuity guards
# ---------------------------------------------------------------------------

class TestTV3Guards:
    def test_dg5_guard_is_non_vacuous(self, db, ledger):
        """If the DG-5 check stopped refusing, this would pass."""
        h = _sealed(db, ledger)
        e = ex.register_experiment(
            db, h, trial_registration="t1", denominator_position=1,
            denominator_size=34, created_by="rohith",
        )
        ex.record_use(db, e.experiment_id, consuming_ref="r", purpose="evaluation")
        assert hist.dg5_citations(db, h), "the citation must be detectable"
        with pytest.raises(hist.RetirementRefused):
            hist.retire_dataset(db, h, actor="rohith")

    def test_as_of_guard_is_non_vacuous(self, db, ledger):
        """If as_of were ignored, past and present would agree."""
        h = _sealed(db, ledger)
        first, last = hist.phase_history(db, h)[0].ts, hist.phase_history(db, h)[-1].ts
        assert first != last
        assert hist.derive_phase_at(db, h, first) != hist.derive_phase_at(db, h, last)

    def test_trigger_guard_is_non_vacuous(self, db, ledger):
        """If the triggers were absent, no history would exist at all."""
        h = _sealed(db, ledger)
        assert len(hist.phase_history(db, h)) == 4

    def test_supersession_preservation_guard_is_non_vacuous(self, db, ledger):
        old = _sealed(db, ledger, lineage="line-a")
        new = _sealed(db, ledger,
                      windowed_declaration("2021-01-01", "2024-12-31", "v2"),
                      lineage="line-a")
        hist.supersede_dataset(db, old, new, change_class="extension",
                               actor="rohith")
        ok, errors = hist.verify_supersession_preserved(db, old)
        assert ok and errors == []

        never_superseded = _sealed(
            db, ledger, windowed_declaration("2020-01-01", "2020-12-31", "c"),
            lineage="line-c",
        )
        ok2, errors2 = hist.verify_supersession_preserved(db, never_superseded)
        assert not ok2
        assert any("Superseded" in e for e in errors2)

    def test_staleness_flag_guard_is_non_vacuous(self, db, ledger):
        h = _sealed(db, ledger)
        assert hist.stale_ceilings(db, h) == []
        hist.flag_stale_ceiling(db, h, cited_kind="f", cited_id="x",
                                reason="r", flagged_by="rohith")
        assert len(hist.stale_ceilings(db, h)) == 1


# ---------------------------------------------------------------------------
# Regression — RF1–RF6 and every prior milestone still hold
# ---------------------------------------------------------------------------

class TestRegression:
    def test_rf1_to_rf6_unchanged(self):
        assert set(res.DEFERRED_REFUSALS) == {"RF4"}
        assert val.DEFERRED_CHECKS == {}
        for rf in ("RF1", "RF2", "RF3", "RF5", "RF6"):
            assert hasattr(res, f"_check_{rf.lower()}")

    def test_phases_and_transitions_unchanged(self):
        assert reg.CITABLE_PHASES == frozenset({"Sealed", "Superseded"})
        assert ("Sealed", "Retired") in reg.ALLOWED_TRANSITIONS
        assert ("Sealed", "Superseded") in reg.ALLOWED_TRANSITIONS

    def test_citation_index_stays_bidirectionally_consistent(self, db, ledger):
        h = _sealed(db, ledger)
        hist.retire_dataset(db, h, actor="rohith")
        ok, errors = ci.verify_bidirectional_consistency(db)
        assert ok, errors

    def test_no_orphan_citations_after_lifecycle_changes(self, db, ledger):
        old = _sealed(db, ledger, lineage="line-a")
        new = _sealed(db, ledger,
                      windowed_declaration("2021-01-01", "2024-12-31", "v2"),
                      lineage="line-a")
        hist.supersede_dataset(db, old, new, change_class="extension",
                               actor="rohith")
        assert ci.find_orphans(db) == []

    def test_lineage_ordering_survives_supersession(self, db, ledger):
        old = _sealed(db, ledger, lineage="line-a")
        new = _sealed(db, ledger,
                      windowed_declaration("2021-01-01", "2024-12-31", "v2"),
                      lineage="line-a")
        hist.supersede_dataset(db, old, new, change_class="extension",
                               actor="rohith")
        lineage = reg.get_lineage(db, "line-a")
        assert [r["spec_hash"] for r in lineage] == [old, new]

    def test_sealed_immutability_still_enforced(self, db, ledger):
        h = _sealed(db, ledger)
        hist.flag_stale_ceiling(db, h, cited_kind="f", cited_id="x",
                                reason="r", flagged_by="rohith")
        con = sqlite3.connect(str(db))
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("UPDATE dataset_specifications SET phase = 'Draft' "
                        "WHERE spec_hash = ?", (h,))
        con.close()

    def test_materialisation_is_still_byte_deterministic(self, db, ledger):
        h = _sealed(db, ledger)
        a = mat.materialise(db, h, rich_provider())
        b = mat.materialise(db, h, rich_provider())
        assert a.snapshot.sha256 == b.snapshot.sha256

    def test_experiment_use_counting_unaffected(self, db, ledger):
        h = _sealed(db, ledger)
        e = ex.register_experiment(
            db, h, trial_registration="t1", denominator_position=1,
            denominator_size=34, created_by="rohith",
        )
        ex.record_use(db, e.experiment_id, consuming_ref="r", purpose="evaluation")
        assert ex.use_count(db, e.experiment_id) == 1

    def test_training_floor_unchanged(self):
        assert tr.PARTITION_ROLES == ("train", "validation", "holdout")
        assert tr.verify_rf4_is_sole_authority()

    def test_receipt_chain_still_valid(self, db, ledger):
        _sealed(db, ledger)
        assert rl.verify_chain(ledger).ok

    def test_history_tables_do_not_disturb_the_schema_guard(self, db):
        reg._assert_dataset_builder_schema(db)
        ci._assert_citation_schema(db)
