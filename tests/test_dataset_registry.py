"""WBS 4.1 — Dataset Registry (ADR-I012 §6, §7, §10, §11, §12 Tier A).

Verification standard: §3 BV (import boundary), §4 TV (positive, negative,
TV3 sabotage, TV5 content-address stability), §5 REG, §8 BC, §9 RBV.

The roadmap's acceptance criteria for 4.1, each covered below:
  - registry accepts a hand-written declaration           -> TestDeclare
  - Tier A refuses on each mandatory field, ONE NEGATIVE
    TEST PER FIELD                                        -> TestTierAPerField
  - a sealed record cannot be edited, proven by a direct
    sqlite3 attempt                                       -> TestSealedImmutability
  - no default --db                                       -> TestNoDefaultDb
  - a schema assertion refuses a non-dataset database
    before any write (LR4)                                -> TestLR4SchemaAssertion
"""

from __future__ import annotations

import ast
import copy
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "research") not in sys.path:
    sys.path.insert(0, str(ROOT / "research"))

from dataset_builder import registry as reg  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def valid_declaration() -> dict:
    """A complete, hand-written declaration carrying every §7.2 field."""
    return {
        "intent": {
            "research_question": "Does the IV-RV spread time spot direction?",
            "declaring_actor": "rohith",
            "rationale": "H-IVRV is a feature candidate; this is its substrate.",
        },
        "temporal_scope": {
            "window_start": "2021-01-01",
            "window_end": "2025-05-31",
            "as_of": "2026-08-07",
            "timezone": "UTC",
            "calendar_convention": "exchange_daily_1200Z",
        },
        "conditioning": {
            "statement": "P(observed | near-ATM and near-dated at sample time)",
        },
        "evidence_references": {
            "sources": ["dvol", "spot", "funding", "perp"],
            "partition_revision_sets": {"dvol": "rev2", "spot": "rev2"},
            "required_certification_verdict": "GREEN",
            "required_lane": "offline-corpus",
        },
        "feature_references": ["f" * 64, "e" * 64],
        "label_references": [
            {"spec_hash": "a" * 64, "maturity_requirement": "matured"},
            {"spec_hash": "b" * 64, "maturity_requirement": "matured"},
        ],
        "sealed_region_exclusions": [
            {"start": "2025-06-01", "end": "2026-05-31", "reason": "lockbox"},
        ],
        "admissibility_floor": "offline-corpus",
        "lane": "offline-corpus",
    }


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    p = tmp_path / "dataset_builder.db"
    reg.init_db(p)
    return p


def _declare(db: Path, decl: dict | None = None, **kw):
    kw.setdefault("lineage", "ivrv-substrate")
    kw.setdefault("declared_by", "rohith")
    return reg.declare_dataset(db, decl if decl is not None else valid_declaration(), **kw)


def _seal(db: Path, h: str) -> None:
    reg.mark_resolved(db, h)
    reg.mark_validated(db, h)
    reg.seal(db, h)


# ---------------------------------------------------------------------------
# §3 BV — build / import boundary
# ---------------------------------------------------------------------------

class TestImportBoundary:
    def test_module_imports(self):
        assert reg.PHASES

    def test_no_src_import_anywhere(self):
        """P4-8 / DG9: the builder reads no operational data. Proven
        structurally by parsing the AST, not by grepping a docstring."""
        src = (ROOT / "research" / "dataset_builder" / "registry.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                offenders += [a.name for a in node.names if a.name.split(".")[0] == "src"]
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.split(".")[0] == "src":
                    offenders.append(node.module)
        assert offenders == [], f"operational imports found: {offenders}"

    def test_seven_phases_exactly(self):
        assert reg.PHASES == {
            "Declared", "Resolved", "Validated", "Sealed",
            "Superseded", "Retired", "Rejected",
        }

    def test_allowed_arcs_match_adr_diagram(self):
        assert reg.ALLOWED_TRANSITIONS == {
            ("Declared", "Resolved"), ("Declared", "Rejected"),
            ("Resolved", "Validated"), ("Resolved", "Rejected"),
            ("Validated", "Sealed"), ("Validated", "Rejected"),
            ("Sealed", "Superseded"), ("Sealed", "Retired"),
        }

    def test_no_arc_leaves_a_terminal_phase(self):
        for frm, _to in reg.ALLOWED_TRANSITIONS:
            assert frm not in reg.TERMINAL_PHASES, f"{frm} is terminal but has an outgoing arc"


# ---------------------------------------------------------------------------
# Positive path
# ---------------------------------------------------------------------------

class TestDeclare:
    def test_accepts_a_hand_written_declaration(self, db):
        h = _declare(db)
        assert h.phase == "Declared"
        assert len(h.spec_hash) == 64
        assert h.version_ordinal == 1

    def test_specification_is_retrievable_by_hash_alone(self, db):
        h = _declare(db)
        spec = reg.get_specification(db, h.spec_hash)
        assert spec["phase"] == "Declared"
        assert spec["declaration"] == valid_declaration()

    def test_unknown_hash_returns_none(self, db):
        assert reg.get_specification(db, "0" * 64) is None

    def test_version_ordinal_increments_within_lineage(self, db):
        a = _declare(db)
        d2 = valid_declaration()
        d2["temporal_scope"]["window_end"] = "2025-06-30"
        b = _declare(db, d2)
        assert (a.version_ordinal, b.version_ordinal) == (1, 2)

    def test_separate_lineages_number_independently(self, db):
        _declare(db, lineage="line-a")
        d2 = valid_declaration()
        d2["intent"]["research_question"] = "different"
        h = _declare(db, d2, lineage="line-b")
        assert h.version_ordinal == 1

    def test_lineage_listing_is_version_ordered(self, db):
        _declare(db)
        d2 = valid_declaration()
        d2["temporal_scope"]["window_end"] = "2025-06-30"
        _declare(db, d2)
        rows = reg.get_lineage(db, "ivrv-substrate")
        assert [r["version_ordinal"] for r in rows] == [1, 2]

    def test_lineage_required(self, db):
        with pytest.raises(reg.DatasetRegistryError, match="lineage"):
            reg.declare_dataset(db, valid_declaration(), lineage="", declared_by="rohith")

    def test_declared_by_required(self, db):
        with pytest.raises(reg.DatasetRegistryError, match="declared_by"):
            reg.declare_dataset(db, valid_declaration(), lineage="l", declared_by="")


# ---------------------------------------------------------------------------
# TV5 — content-address stability
# ---------------------------------------------------------------------------

class TestContentAddressing:
    def test_hash_is_deterministic(self):
        assert reg.declaration_hash(valid_declaration()) == reg.declaration_hash(valid_declaration())

    def test_hash_is_key_order_independent(self):
        d = valid_declaration()
        reordered = {k: d[k] for k in reversed(list(d))}
        assert reg.declaration_hash(d) == reg.declaration_hash(reordered)

    def test_any_declared_field_change_changes_the_hash(self):
        base = reg.declaration_hash(valid_declaration())
        d = valid_declaration()
        d["temporal_scope"]["window_end"] = "2025-06-01"
        assert reg.declaration_hash(d) != base

    def test_redeclaring_identical_content_is_idempotent(self, db):
        a = _declare(db)
        b = _declare(db)
        assert a.spec_hash == b.spec_hash
        con = sqlite3.connect(db)
        try:
            n = con.execute("SELECT COUNT(*) FROM dataset_specifications").fetchone()[0]
        finally:
            con.close()
        assert n == 1

    def test_hash_survives_lifecycle_transitions(self, db):
        h = _declare(db)
        before = h.spec_hash
        _seal(db, before)
        spec = reg.get_specification(db, before)
        assert spec["spec_hash"] == before
        assert reg.declaration_hash(spec["declaration"]) == before


# ---------------------------------------------------------------------------
# Tier A — ONE NEGATIVE TEST PER MANDATORY FIELD (roadmap acceptance)
# ---------------------------------------------------------------------------

TOP_LEVEL_FIELDS = list(reg.MANDATORY_FIELDS)

NESTED_FIELDS = [
    ("intent", "research_question"),
    ("intent", "declaring_actor"),
    ("intent", "rationale"),
    ("temporal_scope", "window_start"),
    ("temporal_scope", "window_end"),
    ("temporal_scope", "as_of"),
    ("temporal_scope", "timezone"),
    ("temporal_scope", "calendar_convention"),
    ("conditioning", "statement"),
    ("evidence_references", "sources"),
    ("evidence_references", "partition_revision_sets"),
    ("evidence_references", "required_certification_verdict"),
    ("evidence_references", "required_lane"),
]


class TestTierAPerField:
    @pytest.mark.parametrize("field", TOP_LEVEL_FIELDS)
    def test_missing_top_level_field_is_refused_by_name(self, field):
        d = valid_declaration()
        del d[field]
        ok, errors = reg.validate_tier_a(d)
        assert ok is False
        assert any(e.startswith(f"{field}:") for e in errors), errors

    @pytest.mark.parametrize("field", TOP_LEVEL_FIELDS)
    def test_empty_top_level_field_is_refused_by_name(self, field):
        d = valid_declaration()
        d[field] = "" if isinstance(d[field], str) else type(d[field])()
        ok, errors = reg.validate_tier_a(d)
        assert ok is False
        assert any(e.startswith(f"{field}:") for e in errors), errors

    @pytest.mark.parametrize("parent,sub", NESTED_FIELDS)
    def test_missing_nested_field_is_refused_by_name(self, parent, sub):
        d = valid_declaration()
        del d[parent][sub]
        ok, errors = reg.validate_tier_a(d)
        assert ok is False
        assert any(e.startswith(f"{parent}.{sub}:") for e in errors), errors

    @pytest.mark.parametrize("parent,sub", NESTED_FIELDS)
    def test_empty_nested_field_is_refused_by_name(self, parent, sub):
        d = valid_declaration()
        d[parent][sub] = ""
        ok, errors = reg.validate_tier_a(d)
        assert ok is False
        assert any(e.startswith(f"{parent}.{sub}:") for e in errors), errors

    @pytest.mark.parametrize("key", reg.MANDATORY_LABEL_REF_KEYS)
    def test_label_reference_missing_key_is_refused_by_name(self, key):
        d = valid_declaration()
        del d["label_references"][0][key]
        ok, errors = reg.validate_tier_a(d)
        assert ok is False
        assert any(f"label_references[0].{key}" in e for e in errors), errors

    def test_valid_declaration_passes(self):
        ok, errors = reg.validate_tier_a(valid_declaration())
        assert ok is True and errors == []


class TestTierAStructuralRules:
    def test_inverted_window_refused(self):
        d = valid_declaration()
        d["temporal_scope"]["window_start"] = "2026-01-01"
        ok, errors = reg.validate_tier_a(d)
        assert ok is False and any("window is empty or inverted" in e for e in errors)

    def test_zero_length_window_refused(self):
        d = valid_declaration()
        d["temporal_scope"]["window_start"] = d["temporal_scope"]["window_end"]
        ok, errors = reg.validate_tier_a(d)
        assert ok is False and any("window is empty or inverted" in e for e in errors)

    def test_as_of_before_window_end_refused(self):
        d = valid_declaration()
        d["temporal_scope"]["as_of"] = "2024-01-01"
        ok, errors = reg.validate_tier_a(d)
        assert ok is False and any("precedes window_end" in e for e in errors)

    def test_duplicate_feature_reference_refused(self):
        d = valid_declaration()
        d["feature_references"] = ["f" * 64, "f" * 64]
        ok, errors = reg.validate_tier_a(d)
        assert ok is False and any("duplicate" in e for e in errors)

    def test_duplicate_label_spec_hash_refused(self):
        d = valid_declaration()
        d["label_references"][1]["spec_hash"] = d["label_references"][0]["spec_hash"]
        ok, errors = reg.validate_tier_a(d)
        assert ok is False and any("duplicate spec_hash" in e for e in errors)

    def test_non_mapping_declaration_refused(self):
        ok, errors = reg.validate_tier_a(["not", "a", "mapping"])
        assert ok is False and "mapping" in errors[0]

    def test_wrong_type_for_list_field_refused(self):
        d = valid_declaration()
        d["feature_references"] = {"not": "a list"}
        ok, errors = reg.validate_tier_a(d)
        assert ok is False and any("must be a list" in e for e in errors)

    def test_errors_accumulate(self):
        d = valid_declaration()
        del d["lane"]
        del d["conditioning"]
        ok, errors = reg.validate_tier_a(d)
        assert ok is False and len(errors) >= 2

    def test_falsy_but_legitimate_values_are_not_blank(self):
        d = valid_declaration()
        d["admissibility_floor"] = 0
        ok, errors = reg.validate_tier_a(d)
        assert ok is True, errors


class TestRejectionIsPermanent:
    def test_rejected_declaration_is_persisted_before_raising(self, db):
        d = valid_declaration()
        del d["lane"]
        h = reg.declaration_hash(d)
        with pytest.raises(reg.SpecificationRejected):
            _declare(db, d)
        spec = reg.get_specification(db, h)
        assert spec is not None and spec["phase"] == "Rejected"
        assert "lane" in spec["rejection_reason"]

    def test_rejection_names_every_failure(self, db):
        d = valid_declaration()
        del d["lane"]
        del d["conditioning"]
        with pytest.raises(reg.SpecificationRejected) as exc:
            _declare(db, d)
        assert len(exc.value.errors) >= 2

    def test_redeclaring_a_rejected_declaration_still_raises(self, db):
        d = valid_declaration()
        del d["lane"]
        with pytest.raises(reg.SpecificationRejected):
            _declare(db, d)
        with pytest.raises(reg.SpecificationRejected):
            _declare(db, d)

    def test_no_arc_out_of_rejected(self, db):
        d = valid_declaration()
        del d["lane"]
        h = reg.declaration_hash(d)
        with pytest.raises(reg.SpecificationRejected):
            _declare(db, d)
        for target in ("Declared", "Resolved", "Validated", "Sealed"):
            with pytest.raises(reg.TransitionError):
                reg._transition(db, h, "Rejected", target)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

class TestLifecycle:
    def test_full_happy_path(self, db):
        h = _declare(db).spec_hash
        reg.mark_resolved(db, h)
        assert reg.get_specification(db, h)["phase"] == "Resolved"
        reg.mark_validated(db, h)
        assert reg.get_specification(db, h)["phase"] == "Validated"
        reg.seal(db, h)
        assert reg.get_specification(db, h)["phase"] == "Sealed"

    def test_not_citable_before_seal(self, db):
        h = _declare(db).spec_hash
        assert reg.is_citable(db, h) is False
        reg.mark_resolved(db, h)
        assert reg.is_citable(db, h) is False
        reg.mark_validated(db, h)
        assert reg.is_citable(db, h) is False

    def test_citable_once_sealed(self, db):
        h = _declare(db).spec_hash
        _seal(db, h)
        assert reg.is_citable(db, h) is True

    def test_cannot_skip_a_phase(self, db):
        """Two independent guards refuse a skip, and it matters which fires.

        `mark_validated` on a Declared row asks for Resolved -> Validated,
        which IS an arc §11 draws -- so the arc table permits it and the
        ROW-PHASE guard is what refuses, because the row is not in Resolved.
        A skip is caught by state, not by the arc table.
        """
        h = _declare(db).spec_hash
        with pytest.raises(reg.TransitionError, match="was not in phase 'Resolved'"):
            reg.mark_validated(db, h)
        with pytest.raises(reg.TransitionError, match="was not in phase 'Validated'"):
            reg.seal(db, h)

    def test_arc_table_refuses_a_pair_the_adr_does_not_draw(self, db):
        """The other guard, in isolation: Declared -> Sealed is not an arc at
        all, so it is refused before any row is even consulted."""
        h = _declare(db).spec_hash
        with pytest.raises(reg.TransitionError, match="not an arc"):
            reg._transition(db, h, "Declared", "Sealed")

    def test_both_guards_are_independently_load_bearing(self, db):
        """A skip that is ALSO a non-arc is refused by the arc table first."""
        h = _declare(db).spec_hash
        with pytest.raises(reg.TransitionError, match="not an arc"):
            reg._transition(db, h, "Declared", "Superseded")

    def test_transition_from_wrong_phase_refuses(self, db):
        h = _declare(db).spec_hash
        reg.mark_resolved(db, h)
        with pytest.raises(reg.TransitionError, match="was not in phase"):
            reg.mark_resolved(db, h)

    def test_transition_on_unknown_hash_refuses(self, db):
        with pytest.raises(reg.TransitionError, match="was not in phase"):
            reg.mark_resolved(db, "0" * 64)

    def test_reject_requires_a_reason(self, db):
        h = _declare(db).spec_hash
        with pytest.raises(reg.DatasetRegistryError, match="reason"):
            reg.reject(db, h, "Declared", "")

    def test_reject_from_each_legal_phase(self, db):
        for i, phase in enumerate(("Declared", "Resolved", "Validated")):
            d = valid_declaration()
            d["intent"]["rationale"] = f"variant {i}"
            h = _declare(db, d).spec_hash
            if phase in ("Resolved", "Validated"):
                reg.mark_resolved(db, h)
            if phase == "Validated":
                reg.mark_validated(db, h)
            reg.reject(db, h, phase, "hard validation failure")
            assert reg.get_specification(db, h)["phase"] == "Rejected"


class TestSupersession:
    def _two_sealed(self, db):
        a = _declare(db).spec_hash
        d2 = valid_declaration()
        d2["temporal_scope"]["window_end"] = "2025-06-30"
        b = _declare(db, d2).spec_hash
        _seal(db, a)
        _seal(db, b)
        return a, b

    def test_supersede_records_successor_and_class(self, db):
        a, b = self._two_sealed(db)
        reg.supersede(db, a, successor_hash=b, change_class="extension")
        spec = reg.get_specification(db, a)
        assert spec["phase"] == "Superseded"
        assert spec["superseded_by"] == b
        assert spec["change_class"] == "extension"

    def test_superseded_remains_resolvable_and_citable(self, db):
        """§10.2 — supersession never invalidates."""
        a, b = self._two_sealed(db)
        reg.supersede(db, a, successor_hash=b, change_class="refinement")
        assert reg.get_specification(db, a) is not None
        assert reg.is_citable(db, a) is True

    def test_redefinition_refused_as_supersession(self, db):
        a, b = self._two_sealed(db)
        with pytest.raises(reg.DatasetRegistryError, match="new lineage"):
            reg.supersede(db, a, successor_hash=b, change_class="redefinition")

    def test_unknown_change_class_refused(self, db):
        a, b = self._two_sealed(db)
        with pytest.raises(reg.DatasetRegistryError, match="change_class"):
            reg.supersede(db, a, successor_hash=b, change_class="tweak")

    def test_successor_must_exist(self, db):
        a, _ = self._two_sealed(db)
        with pytest.raises(reg.UnknownSpecificationError):
            reg.supersede(db, a, successor_hash="0" * 64, change_class="extension")

    def test_successor_must_be_sealed(self, db):
        a = _declare(db).spec_hash
        _seal(db, a)
        d2 = valid_declaration()
        d2["temporal_scope"]["window_end"] = "2025-07-31"
        b = _declare(db, d2).spec_hash
        with pytest.raises(reg.TransitionError, match="requires a successor to be Sealed"):
            reg.supersede(db, a, successor_hash=b, change_class="extension")

    def test_cannot_supersede_itself(self, db):
        a = _declare(db).spec_hash
        _seal(db, a)
        with pytest.raises(reg.DatasetRegistryError, match="cannot supersede itself"):
            reg.supersede(db, a, successor_hash=a, change_class="extension")


class TestRetire:
    def test_retire_from_sealed(self, db):
        h = _declare(db).spec_hash
        _seal(db, h)
        reg.retire(db, h)
        assert reg.get_specification(db, h)["phase"] == "Retired"

    def test_retired_is_not_citable(self, db):
        h = _declare(db).spec_hash
        _seal(db, h)
        reg.retire(db, h)
        assert reg.is_citable(db, h) is False

    def test_cannot_retire_before_seal(self, db):
        """Sealed -> Retired is a legal arc, so the row-phase guard is what
        refuses here: the row is still Declared."""
        h = _declare(db).spec_hash
        with pytest.raises(reg.TransitionError, match="was not in phase 'Sealed'"):
            reg.retire(db, h)
        assert reg.get_specification(db, h)["phase"] == "Declared"


# ---------------------------------------------------------------------------
# Storage-layer immutability — the roadmap's P4-D1 acceptance
# ---------------------------------------------------------------------------

class TestSealedImmutability:
    """Every claim proven ADVERSARIALLY against a direct sqlite3 connection,
    bypassing this module entirely. If immutability held only in Python it
    would not hold at all."""

    def test_direct_sqlite_cannot_edit_a_sealed_declaration(self, db):
        h = _declare(db).spec_hash
        _seal(db, h)
        con = sqlite3.connect(db)
        try:
            with pytest.raises(sqlite3.IntegrityError, match="DATASET_TAMPER"):
                con.execute(
                    "UPDATE dataset_specifications SET declaration_json = ? WHERE spec_hash = ?",
                    (json.dumps({"tampered": True}), h),
                )
        finally:
            con.close()

    def test_direct_sqlite_cannot_change_spec_hash(self, db):
        h = _declare(db).spec_hash
        con = sqlite3.connect(db)
        try:
            with pytest.raises(sqlite3.IntegrityError, match="spec_hash is immutable"):
                con.execute(
                    "UPDATE dataset_specifications SET spec_hash = ? WHERE spec_hash = ?",
                    ("9" * 64, h),
                )
        finally:
            con.close()

    def test_direct_sqlite_cannot_rewrite_a_declared_declaration(self, db):
        """The declaration is frozen from the moment it is written, not merely
        from seal -- content addressing would otherwise be a lie."""
        h = _declare(db).spec_hash
        con = sqlite3.connect(db)
        try:
            with pytest.raises(sqlite3.IntegrityError, match="declaration is immutable"):
                con.execute(
                    "UPDATE dataset_specifications SET declaration_json = ? WHERE spec_hash = ?",
                    ("{}", h),
                )
        finally:
            con.close()

    @pytest.mark.parametrize("column,value", [
        ("lineage", "other"),
        ("version_ordinal", 99),
        ("declared_by", "someone-else"),
        ("declared_at", "1999-01-01T00:00:00+00:00"),
    ])
    def test_direct_sqlite_cannot_change_frozen_column(self, db, column, value):
        h = _declare(db).spec_hash
        con = sqlite3.connect(db)
        try:
            with pytest.raises(sqlite3.IntegrityError, match="DATASET_TAMPER"):
                con.execute(
                    f"UPDATE dataset_specifications SET {column} = ? WHERE spec_hash = ?",
                    (value, h),
                )
        finally:
            con.close()

    def test_sealed_cannot_regress_to_an_earlier_phase(self, db):
        h = _declare(db).spec_hash
        _seal(db, h)
        con = sqlite3.connect(db)
        try:
            for target in ("Declared", "Resolved", "Validated", "Rejected"):
                with pytest.raises(sqlite3.IntegrityError, match="only advance"):
                    con.execute(
                        "UPDATE dataset_specifications SET phase = ? WHERE spec_hash = ?",
                        (target, h),
                    )
        finally:
            con.close()

    def test_sealed_may_still_advance_to_superseded_or_retired(self, db):
        h = _declare(db).spec_hash
        _seal(db, h)
        con = sqlite3.connect(db)
        try:
            con.execute(
                "UPDATE dataset_specifications SET phase = 'Retired' WHERE spec_hash = ?", (h,)
            )
            con.commit()
        finally:
            con.close()
        assert reg.get_specification(db, h)["phase"] == "Retired"

    def test_terminal_phase_cannot_be_left(self, db):
        h = _declare(db).spec_hash
        _seal(db, h)
        reg.retire(db, h)
        con = sqlite3.connect(db)
        try:
            with pytest.raises(sqlite3.IntegrityError, match="terminal phase"):
                con.execute(
                    "UPDATE dataset_specifications SET phase = 'Sealed' WHERE spec_hash = ?", (h,)
                )
        finally:
            con.close()

    def test_delete_forbidden(self, db):
        h = _declare(db).spec_hash
        con = sqlite3.connect(db)
        try:
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                con.execute("DELETE FROM dataset_specifications WHERE spec_hash = ?", (h,))
        finally:
            con.close()

    def test_check_constraint_rejects_an_unknown_phase(self, db):
        h = _declare(db).spec_hash
        con = sqlite3.connect(db)
        try:
            with pytest.raises(sqlite3.IntegrityError):
                con.execute(
                    "UPDATE dataset_specifications SET phase = 'Invented' WHERE spec_hash = ?", (h,)
                )
        finally:
            con.close()


# ---------------------------------------------------------------------------
# LR4 — schema assertion before any write
# ---------------------------------------------------------------------------

class TestLR4SchemaAssertion:
    def test_refuses_a_foreign_database(self, tmp_path):
        foreign = tmp_path / "label_store.db"
        con = sqlite3.connect(foreign)
        con.execute("CREATE TABLE label_specifications (spec_hash TEXT)")
        con.commit()
        con.close()
        with pytest.raises(reg.NotADatasetBuilderStore, match="does not look like"):
            _declare(foreign)

    def test_refuses_before_writing_anything(self, tmp_path):
        """The assertion must fire BEFORE any write, so a foreign database is
        never polluted with a stray table."""
        foreign = tmp_path / "trading.db"
        con = sqlite3.connect(foreign)
        con.execute("CREATE TABLE option_quotes (ts TEXT)")
        con.commit()
        con.close()
        before = {r[0] for r in sqlite3.connect(foreign).execute(
            "SELECT name FROM sqlite_master")}
        with pytest.raises(reg.NotADatasetBuilderStore):
            _declare(foreign)
        after = {r[0] for r in sqlite3.connect(foreign).execute(
            "SELECT name FROM sqlite_master")}
        assert before == after, "a foreign database was modified before the assertion"

    def test_refuses_a_nonexistent_path(self, tmp_path):
        with pytest.raises(reg.NotADatasetBuilderStore, match="does not exist"):
            _declare(tmp_path / "nope.db")

    def test_transitions_also_assert(self, tmp_path):
        foreign = tmp_path / "foreign.db"
        sqlite3.connect(foreign).execute("CREATE TABLE x (a TEXT)")
        with pytest.raises(reg.NotADatasetBuilderStore):
            reg.mark_resolved(foreign, "0" * 64)

    def test_init_db_is_idempotent(self, tmp_path):
        p = tmp_path / "d.db"
        reg.init_db(p)
        reg.init_db(p)
        _declare(p)
        assert reg.get_lineage(p, "ivrv-substrate")


# ---------------------------------------------------------------------------
# CLI — no default --db
# ---------------------------------------------------------------------------

class TestNoDefaultDb:
    def test_cli_requires_db(self):
        with pytest.raises(SystemExit) as exc:
            reg._cli([])
        assert exc.value.code == 2

    def test_no_default_db_constant_in_source(self):
        """A default would let a caller write to the wrong store by omission."""
        src = (ROOT / "research" / "dataset_builder" / "registry.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "add_argument":
                names = [a.value for a in node.args if isinstance(a, ast.Constant)]
                if "--db" in names:
                    kw = {k.arg for k in node.keywords}
                    assert "default" not in kw, "--db must not carry a default"
                    assert "required" in kw

    def test_cli_summary(self, db, capsys):
        _declare(db)
        assert reg._cli(["--db", str(db)]) == 0
        assert "Declared" in capsys.readouterr().out

    def test_cli_refuses_foreign_db(self, tmp_path, capsys):
        foreign = tmp_path / "f.db"
        sqlite3.connect(foreign).execute("CREATE TABLE y (a TEXT)")
        assert reg._cli(["--db", str(foreign)]) == 2

    def test_cli_spec_lookup(self, db, capsys):
        h = _declare(db).spec_hash
        assert reg._cli(["--db", str(db), "--spec-hash", h]) == 0
        assert h in capsys.readouterr().out

    def test_cli_unknown_spec_returns_1(self, db):
        assert reg._cli(["--db", str(db), "--spec-hash", "0" * 64]) == 1

    def test_cli_lineage(self, db, capsys):
        _declare(db)
        assert reg._cli(["--db", str(db), "--lineage", "ivrv-substrate"]) == 0
        assert "Declared" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# TV3 — sabotage: prove the guards are load-bearing
# ---------------------------------------------------------------------------

class TestTV3Sabotage:
    def test_tier_a_is_not_vacuous(self):
        """If MANDATORY_FIELDS were emptied, the per-field tests would pass
        against an empty declaration. Prove they would not."""
        ok, errors = reg.validate_tier_a({})
        assert ok is False
        assert len(errors) >= len(reg.MANDATORY_FIELDS)

    def test_every_mandatory_field_is_actually_checked(self):
        """Removing any one field must produce an error naming it. A field
        listed but unchecked would be caught here."""
        for field in reg.MANDATORY_FIELDS:
            d = valid_declaration()
            del d[field]
            ok, errors = reg.validate_tier_a(d)
            assert ok is False and any(field in e for e in errors), field

    def test_arc_table_is_load_bearing(self, db):
        """Every non-arc pair must be refused, not merely the ones tested
        above by name."""
        h = _declare(db).spec_hash
        for frm in reg.PHASES:
            for to in reg.PHASES:
                if (frm, to) in reg.ALLOWED_TRANSITIONS:
                    continue
                with pytest.raises((reg.TransitionError, reg.DatasetRegistryError)):
                    reg._transition(db, h, frm, to)


# ---------------------------------------------------------------------------
# §9 RBV — rollback / §8 BC — backward compatibility
# ---------------------------------------------------------------------------

class TestRollbackAndCompatibility:
    def test_module_creates_no_file_outside_the_given_db(self, tmp_path):
        p = tmp_path / "sub" / "d.db"
        reg.init_db(p)
        _declare(p)
        assert {f.name for f in (tmp_path / "sub").iterdir()} <= {
            "d.db", "d.db-wal", "d.db-shm", "d.db-journal",
        }

    def test_registry_is_self_contained(self, db):
        """Dropping the module leaves a readable SQLite file; the registry is
        not a proprietary format."""
        _declare(db)
        con = sqlite3.connect(db)
        try:
            rows = con.execute("SELECT spec_hash, phase FROM dataset_specifications").fetchall()
        finally:
            con.close()
        assert len(rows) == 1

    def test_no_other_store_is_touched(self, tmp_path):
        """Import-boundary in practice: using the registry must not create or
        modify a Feature Store, Label Store or ledger anywhere."""
        p = tmp_path / "d.db"
        reg.init_db(p)
        _declare(p)
        names = {f.name for f in tmp_path.rglob("*.db")}
        assert names == {"d.db"}


# ---------------------------------------------------------------------------
# AF-1 -- rejection_reason and superseded_by are write-once
#
# Found by the Phase 4 final audit: the guard protected six columns, and these
# two were mutable by direct sqlite3. DG-4 (IQROS-P15) makes a rejection a
# permanent record; ADR-I012 10.2 requires a superseded dataset to stay
# reconstructable. Both were enforceable only by convention until now.
#
# Every test here writes through raw sqlite3 deliberately: no Python API
# exposes these mutations, so a test driven through the module API could pass
# while the database itself remained open.
# ---------------------------------------------------------------------------


def _rejected(db) -> str:
    # Distinct content: spec_hash is the content hash of the declaration, so a
    # fixture sharing a body with another would collide regardless of lineage.
    d = valid_declaration()
    d["intent"]["rationale"] = "rejected fixture"
    h = reg.declare_dataset(
        db, d, lineage="af1-rej", declared_by="rohith"
    ).spec_hash
    reg.reject(db, h, "Declared", "RF6: warm-up unavailable")
    return h


def _superseded(db) -> tuple[str, str]:
    old = reg.declare_dataset(
        db, valid_declaration(), lineage="af1-sup", declared_by="rohith"
    ).spec_hash
    reg.mark_resolved(db, old)
    reg.mark_validated(db, old)
    reg.seal(db, old)
    new = reg.declare_dataset(
        db, _other_declaration(), lineage="af1-sup", declared_by="rohith"
    ).spec_hash
    reg.mark_resolved(db, new)
    reg.mark_validated(db, new)
    reg.seal(db, new)
    reg.supersede(db, old, successor_hash=new, change_class="extension")
    return old, new


def _other_declaration() -> dict:
    d = valid_declaration()
    d["intent"]["rationale"] = "successor"
    return d


class TestRejectionReasonIsWriteOnce:
    """DG-4 / IQROS-P15 -- a rejection is a permanent record, reason included."""

    def test_reason_cannot_be_erased(self, db):
        h = _rejected(db)
        con = sqlite3.connect(str(db))
        with pytest.raises(sqlite3.IntegrityError, match="rejection_reason is write-once"):
            con.execute(
                "UPDATE dataset_specifications SET rejection_reason = '' "
                "WHERE spec_hash = ?", (h,)
            )
        con.close()

    def test_reason_cannot_be_nulled(self, db):
        h = _rejected(db)
        con = sqlite3.connect(str(db))
        with pytest.raises(sqlite3.IntegrityError, match="rejection_reason is write-once"):
            con.execute(
                "UPDATE dataset_specifications SET rejection_reason = NULL "
                "WHERE spec_hash = ?", (h,)
            )
        con.close()

    def test_reason_cannot_be_rewritten_to_something_milder(self, db):
        """The dangerous edit is not deletion but revision."""
        h = _rejected(db)
        con = sqlite3.connect(str(db))
        with pytest.raises(sqlite3.IntegrityError, match="rejection_reason is write-once"):
            con.execute(
                "UPDATE dataset_specifications SET rejection_reason = 'minor issue' "
                "WHERE spec_hash = ?", (h,)
            )
        con.close()

    def test_the_reason_survives_a_failed_edit(self, db):
        h = _rejected(db)
        con = sqlite3.connect(str(db))
        try:
            con.execute(
                "UPDATE dataset_specifications SET rejection_reason = 'x' "
                "WHERE spec_hash = ?", (h,)
            )
        except sqlite3.IntegrityError:
            pass
        con.rollback()
        con.close()
        assert "RF6" in reg.get_specification(db, h)["rejection_reason"]

    def test_writing_the_reason_the_first_time_is_permitted(self, db):
        """Write-once, not never-written: `reject()` must still work."""
        h = _rejected(db)
        assert reg.get_specification(db, h)["phase"] == "Rejected"
        assert reg.get_specification(db, h)["rejection_reason"]

    def test_a_null_reason_row_is_not_frozen_by_the_guard(self, db):
        """A non-rejected row has NULL here; the guard must not fire on it,
        or ordinary lifecycle transitions would be blocked."""
        h = reg.declare_dataset(
            db, valid_declaration(), lineage="af1-live", declared_by="rohith"
        ).spec_hash
        reg.mark_resolved(db, h)
        assert reg.get_specification(db, h)["phase"] == "Resolved"


class TestSupersededByIsWriteOnce:
    """ADR-I012 10.2 -- supersession must not rewrite history."""

    def test_successor_cannot_be_repointed(self, db):
        old, _ = _superseded(db)
        con = sqlite3.connect(str(db))
        with pytest.raises(sqlite3.IntegrityError, match="superseded_by is write-once"):
            con.execute(
                "UPDATE dataset_specifications SET superseded_by = ? "
                "WHERE spec_hash = ?", ("f" * 64, old)
            )
        con.close()

    def test_successor_cannot_be_erased(self, db):
        old, _ = _superseded(db)
        con = sqlite3.connect(str(db))
        with pytest.raises(sqlite3.IntegrityError, match="superseded_by is write-once"):
            con.execute(
                "UPDATE dataset_specifications SET superseded_by = NULL "
                "WHERE spec_hash = ?", (old,)
            )
        con.close()

    def test_the_pointer_survives_a_failed_edit(self, db):
        old, new = _superseded(db)
        con = sqlite3.connect(str(db))
        try:
            con.execute(
                "UPDATE dataset_specifications SET superseded_by = NULL "
                "WHERE spec_hash = ?", (old,)
            )
        except sqlite3.IntegrityError:
            pass
        con.rollback()
        con.close()
        assert reg.get_specification(db, old)["superseded_by"] == new

    def test_recording_the_successor_the_first_time_is_permitted(self, db):
        old, new = _superseded(db)
        assert reg.get_specification(db, old)["phase"] == "Superseded"
        assert reg.get_specification(db, old)["superseded_by"] == new

    def test_a_sealed_row_can_still_be_superseded_after_the_guard(self, db):
        """Regression: the new clause must not break the Sealed -> Superseded
        arc, which is the only writer of this column."""
        old, new = _superseded(db)
        assert reg.is_citable(db, old)


class TestGuardUpgradeOnExistingStores:
    """The guard is DROPped and recreated, so a store built under an older
    schema gains the new clauses. Created IF NOT EXISTS, it would not."""

    def test_schema_drops_before_creating_the_guard(self):
        src = (ROOT / "research" / "dataset_builder" / "registry.py").read_text()
        drop = src.index("DROP TRIGGER IF EXISTS trg_dataset_specifications_guard_update")
        create = src.index("CREATE TRIGGER trg_dataset_specifications_guard_update")
        assert drop < create

    def test_guard_is_not_created_if_not_exists(self):
        src = (ROOT / "research" / "dataset_builder" / "registry.py").read_text()
        assert (
            "CREATE TRIGGER IF NOT EXISTS trg_dataset_specifications_guard_update"
            not in src
        )

    def test_an_old_store_gains_the_new_clauses(self, tmp_path):
        """Simulates a pre-fix database: install the weaker guard by hand, then
        re-run init_db and prove the mutation is refused afterwards."""
        p = tmp_path / "old_store.db"
        reg.init_db(p)
        con = sqlite3.connect(str(p))
        con.executescript(
            "DROP TRIGGER trg_dataset_specifications_guard_update;\n"
            "CREATE TRIGGER trg_dataset_specifications_guard_update\n"
            "BEFORE UPDATE ON dataset_specifications\n"
            "BEGIN\n"
            "    SELECT CASE\n"
            "        WHEN NEW.spec_hash IS NOT OLD.spec_hash\n"
            "            THEN RAISE(ABORT, 'DATASET_TAMPER: spec_hash is immutable')\n"
            "    END;\n"
            "END;"
        )
        con.commit()
        h = reg.declare_dataset(
            p, valid_declaration(), lineage="old", declared_by="rohith"
        ).spec_hash
        reg.reject(p, h, "Declared", "original reason")

        con.execute(
            "UPDATE dataset_specifications SET rejection_reason = 'erased' "
            "WHERE spec_hash = ?", (h,)
        )
        con.commit()
        con.close()
        assert reg.get_specification(p, h)["rejection_reason"] == "erased"

        reg.init_db(p)

        con = sqlite3.connect(str(p))
        with pytest.raises(sqlite3.IntegrityError, match="rejection_reason is write-once"):
            con.execute(
                "UPDATE dataset_specifications SET rejection_reason = 'again' "
                "WHERE spec_hash = ?", (h,)
            )
        con.close()

    def test_reinstalling_the_guard_preserves_every_row(self, tmp_path):
        p = tmp_path / "reinit.db"
        reg.init_db(p)
        h = _rejected(p)
        before = reg.get_specification(p, h)
        reg.init_db(p)
        assert reg.get_specification(p, h) == before


class TestAF1TV3:
    """Non-vacuity: these must fail if the new clauses are removed."""

    def test_both_columns_are_named_in_the_guard(self):
        src = (ROOT / "research" / "dataset_builder" / "registry.py").read_text()
        guard = src[src.index("CREATE TRIGGER trg_dataset_specifications_guard_update"):]
        guard = guard[:guard.index("END;")]
        assert "OLD.rejection_reason IS NOT NULL" in guard
        assert "OLD.superseded_by IS NOT NULL" in guard

    def test_the_six_original_protections_are_intact(self, db):
        """Regression: the audit's fix must not weaken what already worked."""
        h = reg.declare_dataset(
            db, valid_declaration(), lineage="af1-orig", declared_by="rohith"
        ).spec_hash
        con = sqlite3.connect(str(db))
        for col, val in (("spec_hash", "'z'"), ("declaration_json", "'{}'"),
                         ("lineage", "'other'"), ("version_ordinal", "99"),
                         ("declared_by", "'someone'"), ("declared_at", "'2020-01-01'")):
            with pytest.raises(sqlite3.IntegrityError, match="DATASET_TAMPER"):
                con.execute(
                    f"UPDATE dataset_specifications SET {col} = {val} "
                    "WHERE spec_hash = ?", (h,)
                )
        con.close()

    def test_delete_is_still_forbidden(self, db):
        h = _rejected(db)
        con = sqlite3.connect(str(db))
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            con.execute("DELETE FROM dataset_specifications WHERE spec_hash = ?", (h,))
        con.close()


# ---------------------------------------------------------------------------
# EB-1 -- change_class is write-once
#
# ADR-I012 6: the Registry is append-only with storage-level immutability
# enforcement. 10.1: `extension` says prior results remain valid, `refinement`
# says they may change and comparison needs explicit acknowledgment. supersede()
# validated the value on write; nothing protected it afterwards, so a one-line
# UPDATE could silently convert the second into the first.
# ---------------------------------------------------------------------------


class TestChangeClassIsWriteOnce:

    def test_change_class_cannot_be_flipped(self, db):
        old, _ = _superseded(db)
        assert reg.get_specification(db, old)["change_class"] == "extension"
        con = sqlite3.connect(str(db))
        with pytest.raises(sqlite3.IntegrityError, match="change_class is write-once"):
            con.execute(
                "UPDATE dataset_specifications SET change_class = 'refinement' "
                "WHERE spec_hash = ?", (old,)
            )
        con.close()

    def test_change_class_cannot_be_nulled(self, db):
        old, _ = _superseded(db)
        con = sqlite3.connect(str(db))
        with pytest.raises(sqlite3.IntegrityError, match="change_class is write-once"):
            con.execute(
                "UPDATE dataset_specifications SET change_class = NULL "
                "WHERE spec_hash = ?", (old,)
            )
        con.close()

    def test_the_recorded_class_survives_a_failed_edit(self, db):
        old, _ = _superseded(db)
        con = sqlite3.connect(str(db))
        try:
            con.execute(
                "UPDATE dataset_specifications SET change_class = 'refinement' "
                "WHERE spec_hash = ?", (old,)
            )
        except sqlite3.IntegrityError:
            pass
        con.rollback()
        con.close()
        assert reg.get_specification(db, old)["change_class"] == "extension"

    def test_recording_the_class_the_first_time_is_permitted(self, db):
        """Write-once, not never-written: supersede() must still work."""
        old, _ = _superseded(db)
        assert reg.get_specification(db, old)["change_class"] == "extension"

    def test_a_refinement_supersession_is_equally_protected(self, db):
        old = reg.declare_dataset(
            db, valid_declaration(), lineage="eb1-ref", declared_by="rohith"
        ).spec_hash
        reg.mark_resolved(db, old); reg.mark_validated(db, old); reg.seal(db, old)
        new = reg.declare_dataset(
            db, _other_declaration(), lineage="eb1-ref", declared_by="rohith"
        ).spec_hash
        reg.mark_resolved(db, new); reg.mark_validated(db, new); reg.seal(db, new)
        reg.supersede(db, old, successor_hash=new, change_class="refinement")
        con = sqlite3.connect(str(db))
        with pytest.raises(sqlite3.IntegrityError, match="change_class is write-once"):
            con.execute(
                "UPDATE dataset_specifications SET change_class = 'extension' "
                "WHERE spec_hash = ?", (old,)
            )
        con.close()

    def test_ordinary_transitions_are_unaffected(self, db):
        """The clause must not fire on transitions that leave it NULL.

        `_transition` COALESCEs every metadata column on every arc, so a
        careless rule would break the whole lifecycle.
        """
        h = reg.declare_dataset(
            db, valid_declaration(), lineage="eb1-life", declared_by="rohith"
        ).spec_hash
        reg.mark_resolved(db, h)
        reg.mark_validated(db, h)
        reg.seal(db, h)
        reg.retire(db, h)
        assert reg.get_specification(db, h)["phase"] == "Retired"


class TestFullColumnSweep:
    """Standing guard against the enumeration error that produced EB-1.

    AF-1 named two columns and missed a third. This asserts the protected set
    directly, so any column added later without a decision fails here rather
    than being discovered by a future audit.
    """

    IMMUTABLE = ("spec_hash", "lineage", "version_ordinal", "declaration_json",
                 "declared_by", "declared_at")
    WRITE_ONCE = ("rejection_reason", "superseded_by", "change_class")

    def test_every_column_is_accounted_for(self, db):
        cols = [c[1] for c in sqlite3.connect(str(db)).execute(
            "PRAGMA table_info(dataset_specifications)")]
        accounted = set(self.IMMUTABLE) | set(self.WRITE_ONCE) | {"phase"}
        assert set(cols) == accounted, (
            f"unclassified column(s): {set(cols) - accounted}. Every column must be "
            "immutable, write-once, or the phase itself -- decide which before adding one."
        )

    def test_immutable_columns_are_refused_on_a_superseded_row(self, db):
        old, _ = _superseded(db)
        values = {"spec_hash": "'z'", "lineage": "'other'", "version_ordinal": "99",
                  "declaration_json": "'{}'", "declared_by": "'x'",
                  "declared_at": "'2020-01-01'"}
        con = sqlite3.connect(str(db))
        for col in self.IMMUTABLE:
            with pytest.raises(sqlite3.IntegrityError, match="DATASET_TAMPER"):
                con.execute(
                    f"UPDATE dataset_specifications SET {col} = {values[col]} "
                    "WHERE spec_hash = ?", (old,)
                )
        con.close()

    def test_write_once_columns_are_refused_once_set(self, db):
        """Each write-once column, probed on the row whose transition set it."""
        rej = _rejected(db)
        sup, _ = _superseded(db)
        con = sqlite3.connect(str(db))
        for target, col, val in ((rej, "rejection_reason", "'other'"),
                                 (sup, "superseded_by", "'f'"),
                                 (sup, "change_class", "'refinement'")):
            with pytest.raises(sqlite3.IntegrityError, match="write-once"):
                con.execute(
                    f"UPDATE dataset_specifications SET {col} = {val} "
                    "WHERE spec_hash = ?", (target,)
                )
        con.close()


# ---------------------------------------------------------------------------
# EB-3 -- the schema swap is atomic
#
# The guard is DROPped before being recreated so old stores gain new clauses.
# `executescript` runs statements in autocommit, so without an explicit
# transaction an interruption after the DROP left the store with NO guard --
# and, once unguarded, even the declaration became editable.
# ---------------------------------------------------------------------------


class TestSchemaSwapIsAtomic:

    def test_schema_script_is_wrapped_in_a_transaction(self):
        sql = reg._SCHEMA_SQL.strip()
        assert sql.startswith("BEGIN;"), "the DROP must not run outside a transaction"
        assert sql.endswith("COMMIT;")

    def test_the_drop_is_inside_the_transaction(self):
        sql = reg._SCHEMA_SQL
        assert sql.index("BEGIN;") < sql.index("DROP TRIGGER") < sql.index("COMMIT;")

    def test_an_interrupted_swap_leaves_the_old_guard_intact(self, tmp_path):
        """The failure EB-3 named, reproduced against the real script shape.

        A statement that fails after the DROP must roll the DROP back, so the
        store is never left with no immutability enforcement.
        """
        p = tmp_path / "atomic.db"
        reg.init_db(p)
        con = sqlite3.connect(str(p))
        try:
            con.executescript(
                "BEGIN;\n"
                "DROP TRIGGER IF EXISTS trg_dataset_specifications_guard_update;\n"
                "CREATE TRIGGER deliberately_invalid ON ;\n"
                "COMMIT;"
            )
        except sqlite3.Error:
            pass
        try:
            con.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        con.close()

        present = sqlite3.connect(str(p)).execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE name = 'trg_dataset_specifications_guard_update'"
        ).fetchone()[0]
        assert present == 1, "an interrupted swap left the store unguarded"

    def test_immutability_survives_an_interrupted_swap(self, tmp_path):
        """The consequence, not just the trigger count: with the guard gone the
        declaration itself became editable. It must not."""
        p = tmp_path / "atomic2.db"
        reg.init_db(p)
        h = reg.declare_dataset(
            p, valid_declaration(), lineage="eb3", declared_by="rohith"
        ).spec_hash
        con = sqlite3.connect(str(p))
        try:
            con.executescript(
                "BEGIN;\n"
                "DROP TRIGGER IF EXISTS trg_dataset_specifications_guard_update;\n"
                "CREATE TRIGGER deliberately_invalid ON ;\n"
                "COMMIT;"
            )
        except sqlite3.Error:
            pass
        try:
            con.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        with pytest.raises(sqlite3.IntegrityError, match="declaration is immutable"):
            con.execute(
                "UPDATE dataset_specifications SET declaration_json = '{}' "
                "WHERE spec_hash = ?", (h,)
            )
        con.close()

    def test_init_db_remains_idempotent_under_the_transaction(self, tmp_path):
        p = tmp_path / "idem.db"
        reg.init_db(p)
        h = _rejected(p)
        before = reg.get_specification(p, h)

        def triggers():
            return sorted(r[0] for r in sqlite3.connect(str(p)).execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"))

        t1 = triggers()
        reg.init_db(p)
        reg.init_db(p)
        assert triggers() == t1
        assert reg.get_specification(p, h) == before
