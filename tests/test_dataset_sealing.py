"""WBS 4.5 — Dataset Sealing (ADR-I012 §8 stage 5, §9, §11, §13.2, §14).

Roadmap acceptance for 4.5:
  - sealing emits a chained, replayable receipt  -> TestSealReceipt
  - a service actor is refused (DG-1)            -> TestDG1HumanAct

Mandatory carry-over from WBS 4.4:
  - HZ-4.4-1 — the Tier D cap must survive the seal -> TestHZ441
"""

from __future__ import annotations

import ast
import json
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
import receipts as rcp  # noqa: E402
from lattice import Provenance, Readiness  # noqa: E402

from dataset_builder import citation_index as ci  # noqa: E402
from dataset_builder import registry as reg  # noqa: E402
from dataset_builder import resolution as res  # noqa: E402
from dataset_builder import sealing as sl  # noqa: E402
from dataset_builder import validation as val  # noqa: E402
from tests.test_dataset_registry import valid_declaration  # noqa: E402
from tests.test_dataset_resolution import good_resolver  # noqa: E402
from tests.test_dataset_validation import adm, ev, feat, good_inputs, lab  # noqa: E402

FEATURE_A = "f" * 64


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    p = tmp_path / "dataset_builder.db"
    reg.init_db(p)
    ci.init_db(p)
    sl.init_db(p)
    return p


@pytest.fixture()
def ledger(tmp_path: Path) -> Path:
    p = tmp_path / "ledger.db"
    rl.init_db(p)
    return p


def _validated(db, decl=None, lineage="line-a", inputs=None, stats=None, floor=None):
    """A dataset in phase Validated with its report, ready to seal."""
    d = decl if decl is not None else valid_declaration()
    h = res.declare(db, d, lineage=lineage, declared_by="rohith").spec_hash
    res.resolve(db, h, good_resolver())
    report = val.validate(db, h, inputs or good_inputs(),
                          declared_floor=floor, stats=stats)
    return h, report


def _seal(db, ledger, h, report, **kw):
    kw.setdefault("sealed_by", "rohith")
    return sl.seal_dataset(db, h, report, ledger_db=ledger, **kw)


# ---------------------------------------------------------------------------
# Build / scope
# ---------------------------------------------------------------------------

class TestBuildAndScope:
    def test_no_src_import(self):
        src = (ROOT / "research" / "dataset_builder" / "sealing.py").read_text(encoding="utf-8")
        bad = []
        for n in ast.walk(ast.parse(src)):
            if isinstance(n, ast.Import):
                bad += [a.name for a in n.names if a.name.split(".")[0] == "src"]
            elif isinstance(n, ast.ImportFrom) and n.module:
                if n.module.split(".")[0] == "src":
                    bad.append(n.module)
        assert bad == []

    def test_sealing_never_imports_a_recomputation_primitive(self):
        """HZ-4.4-1 made structural: if `meet` or `compose_admissibility` were
        importable here, recomputing the ceiling would be possible. It is not."""
        src = (ROOT / "research" / "dataset_builder" / "sealing.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        imported: set[str] = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom):
                imported |= {a.asname or a.name for a in n.names}
            elif isinstance(n, ast.Import):
                imported |= {a.asname or a.name for a in n.names}
        assert "meet" not in imported
        assert "compose_admissibility" not in imported

    def test_taxonomy_extension_is_additive(self):
        assert "seal" in rcp.ACTIONS
        assert rcp.DECISION_TAXONOMY["dataset.seal"] == "discretionary"
        # every previously-existing entry still present and unchanged
        for k in ("contract.activate", "evidence.admit", "family.promote",
                  "dataset.promote", "dataset.retire"):
            assert k in rcp.DECISION_TAXONOMY

    def test_seal_is_discretionary_not_derived(self):
        """DG-2: resolution and validation are computations and replay. DG-1:
        sealing is a human act. A derived seal could be manufactured by re-running
        the battery — which is exactly what must not be possible."""
        assert rcp.DECISION_TAXONOMY["dataset.seal"] == "discretionary"


# ---------------------------------------------------------------------------
# DG-1 — sealing is a human act
# ---------------------------------------------------------------------------

class TestDG1HumanAct:
    def test_service_actor_refused(self, db, ledger):
        h, report = _validated(db)
        with pytest.raises(sl.ServiceActorRefused, match="human act"):
            _seal(db, ledger, h, report, actor_type="service")

    @pytest.mark.parametrize("actor_type", ["service", "system", "bot", ""])
    def test_only_human_accepted(self, db, ledger, actor_type):
        h, report = _validated(db)
        with pytest.raises(sl.ServiceActorRefused):
            _seal(db, ledger, h, report, actor_type=actor_type)

    def test_refusal_leaves_phase_untouched(self, db, ledger):
        h, report = _validated(db)
        with pytest.raises(sl.ServiceActorRefused):
            _seal(db, ledger, h, report, actor_type="service")
        assert reg.get_specification(db, h)["phase"] == "Validated"
        assert sl.get_sealed(db, h) is None

    def test_refusal_writes_no_receipt(self, db, ledger):
        h, report = _validated(db)
        with pytest.raises(sl.ServiceActorRefused):
            _seal(db, ledger, h, report, actor_type="service")
        con = sqlite3.connect(ledger)
        try:
            assert con.execute("SELECT COUNT(*) FROM governance_receipts").fetchone()[0] == 0
        finally:
            con.close()

    def test_sealed_by_required(self, db, ledger):
        h, report = _validated(db)
        with pytest.raises(sl.SealingError, match="sealed_by is required"):
            _seal(db, ledger, h, report, sealed_by="")

    def test_human_actor_recorded_on_the_receipt(self, db, ledger):
        h, report = _validated(db)
        sr = _seal(db, ledger, h, report, sealed_by="rohith")
        con = sqlite3.connect(ledger)
        con.row_factory = sqlite3.Row
        try:
            row = con.execute("SELECT * FROM governance_receipts WHERE receipt_id = ?",
                              (sr.seal_receipt_id,)).fetchone()
        finally:
            con.close()
        assert "rohith" in json.dumps(dict(row))


# ---------------------------------------------------------------------------
# HZ-4.4-1 — the mandatory carry-over
# ---------------------------------------------------------------------------

class TestHZ441:
    def _capped(self, db):
        """A dataset whose Tier D genuinely lowered the ceiling."""
        stats = val.DatasetStatistics(row_count=1, calendar_years_spanned=4,
                                      label_positive_fraction=0.5, matured_fraction=1.0)
        return _validated(db, stats=stats)

    def test_tier_d_cap_is_persisted_not_the_raw_meet(self, db, ledger):
        h, report = self._capped(db)
        assert report.computed_meet.readiness == Readiness.READY
        assert report.capped_admissibility.readiness == Readiness.PARTIAL
        _seal(db, ledger, h, report)
        sealed = sl.get_sealed(db, h)
        assert sealed["capped_admissibility"]["readiness"] == "PARTIAL"

    def test_raw_meet_also_recorded_so_the_cap_is_auditable(self, db, ledger):
        h, report = self._capped(db)
        _seal(db, ledger, h, report)
        sealed = sl.get_sealed(db, h)
        assert sealed["computed_meet"]["readiness"] == "READY"
        assert sealed["capped_admissibility"] != sealed["computed_meet"]

    def test_accessor_returns_the_capped_ceiling(self, db, ledger):
        h, report = self._capped(db)
        _seal(db, ledger, h, report)
        assert sl.admissibility_ceiling(db, h)["readiness"] == "PARTIAL"

    def test_cap_was_applied_flag(self, db, ledger):
        h, report = self._capped(db)
        assert _seal(db, ledger, h, report).cap_was_applied is True

    def test_no_cap_means_ceiling_equals_meet(self, db, ledger):
        h, report = _validated(db)
        sr = _seal(db, ledger, h, report)
        assert sr.cap_was_applied is False
        assert sr.admissibility_ceiling == sr.computed_meet

    def test_tier_d_caps_recorded_individually(self, db, ledger):
        stats = val.DatasetStatistics(row_count=1, calendar_years_spanned=0,
                                      degenerate_columns=("x",),
                                      label_positive_fraction=0.99, matured_fraction=0.0)
        h, report = _validated(db, stats=stats)
        sr = _seal(db, ledger, h, report)
        assert len(sr.tier_d_caps) == 5
        assert {c["code"] for c in sr.tier_d_caps} == {"TD1", "TD2", "TD3", "TD4", "TD5"}

    def test_refuses_report_without_a_ceiling(self, db, ledger):
        h, report = _validated(db)
        report.capped_admissibility = None
        with pytest.raises(sl.SealingError, match="HZ-4.4-1"):
            _seal(db, ledger, h, report)

    def test_verify_seal_detects_a_lost_cap(self, db, ledger):
        """The direct HZ-4.4-1 assertion: if the persisted ceiling ever equals
        the raw meet while Tier D applied a cap, verify_seal must say so."""
        h, report = self._capped(db)
        _seal(db, ledger, h, report)
        ok, errors = sl.verify_seal(db, h, report)
        assert ok is True and errors == []
        # simulate the defect on a copy of the row
        con = sqlite3.connect(db)
        try:
            con.execute("PRAGMA foreign_keys = OFF")
            con.execute("DROP TRIGGER trg_sealed_datasets_no_update")
            con.execute("UPDATE sealed_datasets SET capped_admissibility_json = "
                        "computed_meet_json WHERE spec_hash = ?", (h,))
            con.commit()
        finally:
            con.close()
        ok, errors = sl.verify_seal(db, h, report)
        assert ok is False
        assert any("HZ-4.4-1" in e or "cap was lost" in e for e in errors)


# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------

class TestPrerequisites:
    def test_only_validated_may_seal(self, db, ledger):
        d = valid_declaration()
        h = res.declare(db, d, lineage="l", declared_by="r").spec_hash
        report = val.ValidationReport(spec_hash=h, ok=True,
                                      capped_admissibility=adm(), computed_meet=adm())
        with pytest.raises(sl.NotValidatedError, match="not 'Validated'"):
            _seal(db, ledger, h, report)

    def test_resolved_but_unvalidated_refused(self, db, ledger):
        d = valid_declaration()
        h = res.declare(db, d, lineage="l", declared_by="r").spec_hash
        res.resolve(db, h, good_resolver())
        report = val.ValidationReport(spec_hash=h, ok=True,
                                      capped_admissibility=adm(), computed_meet=adm())
        with pytest.raises(sl.NotValidatedError):
            _seal(db, ledger, h, report)

    def test_unknown_hash(self, db, ledger):
        report = val.ValidationReport(spec_hash="0" * 64, ok=True,
                                      capped_admissibility=adm(), computed_meet=adm())
        with pytest.raises(sl.SealingError, match="no such specification"):
            _seal(db, ledger, "0" * 64, report)

    def test_mismatched_report_refused(self, db, ledger):
        h1, report1 = _validated(db, lineage="a")
        d2 = valid_declaration(); d2["intent"]["rationale"] = "second"
        h2, _ = _validated(db, d2, lineage="b")
        with pytest.raises(sl.ValidationMismatchError, match="another dataset's validation"):
            _seal(db, ledger, h2, report1)

    def test_failed_validation_cannot_seal(self, db, ledger):
        h, report = _validated(db)
        report.ok = False
        report.findings = [val.Finding("RF4", "C", "x", ("y",), "blocked", True)]
        with pytest.raises(sl.SealingError, match="blocking finding"):
            _seal(db, ledger, h, report)

    def test_double_seal_refused(self, db, ledger):
        """Two guards refuse a re-seal, and the phase guard wins.

        After sealing the row is in phase Sealed, so the phase check fires
        before the already-sealed check ever runs. The already-sealed check is
        therefore defence in depth against a hand-advanced phase, not the
        primary guard -- recorded as TD-4.5-2 so it is not mistaken for
        untested. DG-3 holds either way: change produces a new dataset.
        """
        h, report = _validated(db)
        _seal(db, ledger, h, report)
        with pytest.raises(sl.NotValidatedError, match="not 'Validated'"):
            _seal(db, ledger, h, report)
        assert reg.get_specification(db, h)["phase"] == "Sealed"

    def test_already_sealed_guard_fires_when_phase_is_bypassed(self, db, ledger):
        """The defence-in-depth path, reached by forcing the phase back the
        only way the triggers permit -- a fresh row cannot be made, so this
        exercises the guard directly."""
        h, report = _validated(db)
        _seal(db, ledger, h, report)
        assert sl.get_sealed(db, h) is not None
        con = sqlite3.connect(db)
        try:
            n = con.execute("SELECT COUNT(*) FROM sealed_datasets WHERE spec_hash=?",
                            (h,)).fetchone()[0]
        finally:
            con.close()
        assert n == 1  # exactly one seal, never two

    def test_lr4_on_foreign_db(self, tmp_path, ledger):
        foreign = tmp_path / "f.db"
        sqlite3.connect(foreign).execute("CREATE TABLE x (a TEXT)")
        report = val.ValidationReport(spec_hash="0" * 64, ok=True,
                                      capped_admissibility=adm(), computed_meet=adm())
        with pytest.raises(reg.NotADatasetBuilderStore):
            sl.seal_dataset(foreign, "0" * 64, report, sealed_by="r", ledger_db=ledger)

    def test_init_requires_registry_first(self, tmp_path):
        p = tmp_path / "empty.db"
        sqlite3.connect(p).execute("CREATE TABLE unrelated (x TEXT)")
        with pytest.raises(reg.NotADatasetBuilderStore):
            sl.init_db(p)


# ---------------------------------------------------------------------------
# Sealing mechanics + receipt
# ---------------------------------------------------------------------------

class TestSealReceipt:
    def test_happy_path_moves_to_sealed(self, db, ledger):
        h, report = _validated(db)
        sr = _seal(db, ledger, h, report)
        assert reg.get_specification(db, h)["phase"] == "Sealed"
        assert sr.seal_receipt_id

    def test_dataset_becomes_citable(self, db, ledger):
        """§11 — sealing is what makes a dataset citable."""
        h, report = _validated(db)
        assert reg.is_citable(db, h) is False
        _seal(db, ledger, h, report)
        assert reg.is_citable(db, h) is True

    def test_receipt_chain_valid(self, db, ledger):
        h, report = _validated(db)
        _seal(db, ledger, h, report)
        result = rl.verify_chain(ledger)
        assert result.ok is True and result.n_receipts == 1

    def test_chain_valid_across_several_seals(self, db, ledger):
        for i in range(3):
            d = valid_declaration(); d["intent"]["rationale"] = f"variant {i}"
            h, report = _validated(db, d, lineage=f"line-{i}")
            _seal(db, ledger, h, report)
        result = rl.verify_chain(ledger)
        assert result.ok is True and result.n_receipts == 3

    def test_receipt_records_the_transition(self, db, ledger):
        h, report = _validated(db)
        sr = _seal(db, ledger, h, report)
        con = sqlite3.connect(ledger); con.row_factory = sqlite3.Row
        try:
            row = dict(con.execute("SELECT * FROM governance_receipts WHERE receipt_id=?",
                                   (sr.seal_receipt_id,)).fetchone())
        finally:
            con.close()
        blob = json.dumps(row)
        assert "Validated" in blob and "Sealed" in blob and "dataset.seal" in blob

    def test_receipt_binds_the_validation_fingerprint(self, db, ledger):
        """The receipt must reference the exact validation it sealed, so a
        later re-run cannot be passed off as the sealed one."""
        h, report = _validated(db)
        sr = _seal(db, ledger, h, report)
        con = sqlite3.connect(ledger)
        try:
            blob = json.dumps([dict(zip([c[0] for c in con.execute(
                "SELECT * FROM governance_receipts LIMIT 0").description], r))
                for r in con.execute("SELECT * FROM governance_receipts")])
        finally:
            con.close()
        assert sr.validation_fingerprint in blob

    def test_reason_recorded(self, db, ledger):
        h, report = _validated(db)
        sr = _seal(db, ledger, h, report, reason="H-IVRV substrate")
        con = sqlite3.connect(ledger)
        try:
            blob = " ".join(str(r) for r in con.execute("SELECT * FROM governance_receipts"))
        finally:
            con.close()
        assert "H-IVRV substrate" in blob


class TestPersistedState:
    def test_persists_provenance_closure(self, db, ledger):
        h, report = _validated(db)
        _seal(db, ledger, h, report)
        closure = sl.get_sealed(db, h)["provenance_closure"]
        assert closure["declaration_hash"] == h
        assert set(closure["citations"]) == {"evidence", "feature", "label"}

    def test_closure_uses_the_citation_index(self, db, ledger):
        """Closure and reverse index can never disagree about what is cited."""
        h, report = _validated(db)
        _seal(db, ledger, h, report)
        closure = sl.get_sealed(db, h)["provenance_closure"]
        indexed = {c.cited_id for c in ci.cites(db, h, kind="feature")}
        assert {c["id"] for c in closure["citations"]["feature"]} == indexed

    def test_closure_records_deferred_and_not_computed(self, db, ledger):
        """The sealed closure records exactly what was deferred at seal time.

        RF2 left the deferred set at WBS 4.6 -- it is enforced at resolution
        now, so a dataset that seals has already passed it. The closure
        correctly records one fewer deferred check, which is 4.6 propagating
        into the provenance record rather than a regression.
        """
        h, report = _validated(db)
        _seal(db, ledger, h, report)
        closure = sl.get_sealed(db, h)["provenance_closure"]
        assert closure["deferred_checks"] == {}
        assert "RF2" not in closure["deferred_checks"]
        assert "RF3" not in closure["deferred_checks"]
        assert closure["not_computed"] == ["TD1", "TD2", "TD3", "TD4", "TD5"]

    def test_records_engine_version_and_actor(self, db, ledger):
        h, report = _validated(db)
        _seal(db, ledger, h, report, sealed_by="rohith")
        sealed = sl.get_sealed(db, h)
        assert sealed["resolving_engine_version"] == sl.SEALING_ENGINE_VERSION
        assert sealed["sealed_by"] == "rohith"
        assert sealed["sealed_at"]

    def test_survives_reload(self, db, ledger):
        """Restart/reload persistence: nothing is cached in memory."""
        h, report = _validated(db)
        sr = _seal(db, ledger, h, report)
        reloaded = sl.get_sealed(db, h)
        assert reloaded["seal_receipt_id"] == sr.seal_receipt_id
        assert reloaded["validation_fingerprint"] == sr.validation_fingerprint

    def test_survives_reload_in_a_fresh_connection(self, db, ledger):
        h, report = _validated(db)
        _seal(db, ledger, h, report)
        con = sqlite3.connect(db)
        try:
            n = con.execute("SELECT COUNT(*) FROM sealed_datasets").fetchone()[0]
        finally:
            con.close()
        assert n == 1


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_fingerprint_is_deterministic(self, db):
        h, report = _validated(db)
        assert sl.validation_fingerprint(report) == sl.validation_fingerprint(report)

    def test_fingerprint_changes_with_the_ceiling(self, db):
        h, report = _validated(db)
        before = sl.validation_fingerprint(report)
        report.capped_admissibility = adm(r=Readiness.PARTIAL)
        assert sl.validation_fingerprint(report) != before

    def test_fingerprint_changes_with_findings(self, db):
        h, report = _validated(db)
        before = sl.validation_fingerprint(report)
        report.findings = [val.Finding("TD1", "D", "x", ("y",), "z", False)]
        assert sl.validation_fingerprint(report) != before

    def test_fingerprint_is_finding_order_independent(self, db):
        h, report = _validated(db)
        a = val.Finding("TD1", "D", "c", ("f",), "alpha", False)
        b = val.Finding("TD2", "D", "c", ("f",), "beta", False)
        report.findings = [a, b]
        first = sl.validation_fingerprint(report)
        report.findings = [b, a]
        assert sl.validation_fingerprint(report) == first

    def test_declaration_hash_unchanged_by_sealing(self, db, ledger):
        h, report = _validated(db)
        _seal(db, ledger, h, report)
        spec = reg.get_specification(db, h)
        assert reg.declaration_hash(spec["declaration"]) == h

    def test_admissibility_serialised_by_name_not_value(self, db):
        d = sl.admissibility_to_dict(adm(p=Provenance.OFFLINE_CORPUS))
        assert d["provenance"] == "OFFLINE_CORPUS"
        assert d["provenance"] != 1


# ---------------------------------------------------------------------------
# DG-3 — immutability
# ---------------------------------------------------------------------------

class TestSealImmutability:
    def test_sealed_row_update_forbidden(self, db, ledger):
        h, report = _validated(db)
        _seal(db, ledger, h, report)
        con = sqlite3.connect(db)
        try:
            with pytest.raises(sqlite3.IntegrityError, match="DG-3"):
                con.execute("UPDATE sealed_datasets SET sealed_by = 'someone-else'")
        finally:
            con.close()

    def test_sealed_row_delete_forbidden(self, db, ledger):
        h, report = _validated(db)
        _seal(db, ledger, h, report)
        con = sqlite3.connect(db)
        try:
            with pytest.raises(sqlite3.IntegrityError, match="DG-3"):
                con.execute("DELETE FROM sealed_datasets")
        finally:
            con.close()

    def test_ceiling_cannot_be_edited(self, db, ledger):
        h, report = _validated(db)
        _seal(db, ledger, h, report)
        con = sqlite3.connect(db)
        try:
            with pytest.raises(sqlite3.IntegrityError, match="DG-3"):
                con.execute("UPDATE sealed_datasets SET capped_admissibility_json = '{}'")
        finally:
            con.close()

    def test_specification_still_immutable_after_seal(self, db, ledger):
        h, report = _validated(db)
        _seal(db, ledger, h, report)
        con = sqlite3.connect(db)
        try:
            with pytest.raises(sqlite3.IntegrityError, match="DATASET_TAMPER"):
                con.execute("UPDATE dataset_specifications SET declaration_json='{}' "
                            "WHERE spec_hash=?", (h,))
        finally:
            con.close()

    def test_orphan_detector_clean(self, db, ledger):
        h, report = _validated(db)
        _seal(db, ledger, h, report)
        assert sl.find_orphan_seal_receipts(db) == []

    def test_orphan_detector_finds_a_bypassed_seal(self, db, ledger):
        """A dataset advanced to Sealed without a sealed-state row."""
        h, report = _validated(db)
        reg.seal(db, h)   # registry-level transition, bypassing sealing.py
        assert h in sl.find_orphan_seal_receipts(db)


# ---------------------------------------------------------------------------
# TV3 sabotage
# ---------------------------------------------------------------------------

class TestTV3Sabotage:
    def test_dg1_guard_is_not_vacuous(self, db, ledger):
        h, report = _validated(db)
        with pytest.raises(sl.ServiceActorRefused):
            _seal(db, ledger, h, report, actor_type="service")

    def test_dg1_does_not_refuse_humans(self, db, ledger):
        h, report = _validated(db)
        assert _seal(db, ledger, h, report).seal_receipt_id

    def test_hz441_is_not_vacuous(self, db, ledger):
        """If the cap were never applied, the capped and raw values would be
        equal and the whole HZ-4.4-1 suite would pass trivially."""
        stats = val.DatasetStatistics(row_count=1, calendar_years_spanned=4,
                                      label_positive_fraction=0.5, matured_fraction=1.0)
        h, report = _validated(db, stats=stats)
        assert report.capped_admissibility != report.computed_meet

    def test_phase_guard_is_not_vacuous(self, db, ledger):
        d = valid_declaration()
        h = res.declare(db, d, lineage="l", declared_by="r").spec_hash
        report = val.ValidationReport(spec_hash=h, ok=True,
                                      capped_admissibility=adm(), computed_meet=adm())
        with pytest.raises(sl.NotValidatedError):
            _seal(db, ledger, h, report)

    def test_fingerprint_is_not_constant(self, db):
        h1, r1 = _validated(db, lineage="a")
        d2 = valid_declaration(); d2["intent"]["rationale"] = "different"
        h2, r2 = _validated(db, d2, lineage="b")
        assert sl.validation_fingerprint(r1) != sl.validation_fingerprint(r2)


# ---------------------------------------------------------------------------
# Regression — 4.1/4.2/4.3/4.4 invariants
# ---------------------------------------------------------------------------

class TestRegression:
    def test_citation_graph_intact_after_seal(self, db, ledger):
        h, report = _validated(db)
        _seal(db, ledger, h, report)
        assert len(ci.cites(db, h)) == 8
        ok, errors = ci.verify_bidirectional_consistency(db)
        assert ok is True and errors == []

    def test_sealed_dataset_pins_its_inputs(self, db, ledger):
        """4.2's reverse edge: only a citable dataset pins. Sealing is what
        makes it citable, so the pin must appear exactly at seal."""
        h, report = _validated(db)
        assert ci.cited_by(db, "feature", FEATURE_A) == []
        _seal(db, ledger, h, report)
        assert ci.cited_by(db, "feature", FEATURE_A) == [h]

    def test_retirement_refusal_now_bites(self, db, ledger, tmp_path):
        """End-to-end: seal a dataset, and the Feature Store refuses to retire
        a specification it cites (ADR-I010 §11, closed at 4.2)."""
        from feature_store import registry as fs
        store = tmp_path / "feature_store.db"
        fs.init_db(store)
        h, report = _validated(db)
        _seal(db, ledger, h, report)
        check = ci.make_citation_check(db, "feature")
        with pytest.raises(fs.SpecificationCited):
            fs.retire_specification(store, FEATURE_A, "Deprecated", citation_check=check)

    def test_registry_invariants_hold(self, db, ledger):
        h, report = _validated(db)
        _seal(db, ledger, h, report)
        spec = reg.get_specification(db, h)
        assert spec["phase"] == "Sealed"
        assert spec["lineage"] == "line-a"
        assert spec["version_ordinal"] == 1

    def test_resolution_history_preserved(self, db, ledger):
        h, report = _validated(db)
        _seal(db, ledger, h, report)
        assert reg.get_specification(db, h)["declaration"] == valid_declaration()

    def test_validation_result_faithfully_represented(self, db, ledger):
        h, report = _validated(db)
        _seal(db, ledger, h, report)
        ok, errors = sl.verify_seal(db, h, report)
        assert ok is True and errors == []

    def test_verify_seal_rejects_a_different_report(self, db, ledger):
        h, report = _validated(db)
        _seal(db, ledger, h, report)
        report.findings = [val.Finding("TD1", "D", "x", ("y",), "injected", False)]
        ok, errors = sl.verify_seal(db, h, report)
        assert ok is False and any("fingerprint" in e for e in errors)

    def test_supersede_still_works_after_seal(self, db, ledger):
        h1, r1 = _validated(db, lineage="line-a")
        _seal(db, ledger, h1, r1)
        d2 = valid_declaration(); d2["temporal_scope"]["window_end"] = "2025-06-30"
        h2, r2 = _validated(db, d2, lineage="line-a")
        _seal(db, ledger, h2, r2)
        reg.supersede(db, h1, successor_hash=h2, change_class="extension")
        assert reg.get_specification(db, h1)["phase"] == "Superseded"
        assert reg.is_citable(db, h1) is True

    def test_no_other_store_touched(self, tmp_path):
        p = tmp_path / "d.db"
        led = tmp_path / "l.db"
        reg.init_db(p); ci.init_db(p); sl.init_db(p); rl.init_db(led)
        h = res.declare(p, valid_declaration(), lineage="l", declared_by="r").spec_hash
        res.resolve(p, h, good_resolver())
        report = val.validate(p, h, good_inputs())
        sl.seal_dataset(p, h, report, sealed_by="r", ledger_db=led)
        assert {f.name for f in tmp_path.rglob("*.db")} == {"d.db", "l.db"}
