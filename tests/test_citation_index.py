"""WBS 4.2 — Bidirectional Citation Index (ADR-I012 §6.1, §6.2; DG-5; T-6).

Roadmap acceptance for 4.2, each covered below:
  - forward and reverse both answerable        -> TestForward / TestReverse
  - `retire_specification()` in BOTH upstream
    stores refuses while cited, closing
    ADR-I010 §11 and ADR-I011 LG5              -> TestRetirementRefusal
"""

from __future__ import annotations

import ast
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "research") not in sys.path:
    sys.path.insert(0, str(ROOT / "research"))

from dataset_builder import citation_index as ci  # noqa: E402
from dataset_builder import registry as reg  # noqa: E402
from tests.test_dataset_registry import valid_declaration  # noqa: E402

FEATURE_A = "f" * 64
FEATURE_B = "e" * 64
LABEL_A = "a" * 64
LABEL_B = "b" * 64


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    p = tmp_path / "dataset_builder.db"
    reg.init_db(p)
    ci.init_db(p)
    return p


def _declare(db: Path, decl: dict | None = None, lineage: str = "line-a") -> str:
    return reg.declare_dataset(
        db, decl if decl is not None else valid_declaration(),
        lineage=lineage, declared_by="rohith",
    ).spec_hash


def _seal(db: Path, h: str) -> None:
    reg.mark_resolved(db, h)
    reg.mark_validated(db, h)
    reg.seal(db, h)


def _sealed_dataset(db: Path, decl: dict | None = None, lineage: str = "line-a") -> str:
    h = _declare(db, decl, lineage)
    ci.index_declaration(db, h, decl if decl is not None else valid_declaration())
    _seal(db, h)
    return h


# ---------------------------------------------------------------------------
# §3 BV — build / dependency direction
# ---------------------------------------------------------------------------

class TestDependencyDirection:
    def test_no_src_import(self):
        src = (ROOT / "research" / "dataset_builder" / "citation_index.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        bad = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                bad += [a.name for a in n.names if a.name.split(".")[0] == "src"]
            elif isinstance(n, ast.ImportFrom) and n.module:
                if n.module.split(".")[0] == "src":
                    bad.append(n.module)
        assert bad == []

    @pytest.mark.parametrize("store", ["feature_store", "label_store"])
    def test_upstream_store_never_imports_the_builder(self, store):
        """The authority is INJECTED, not imported. If a store imported
        dataset_builder the dependency arrow would invert."""
        src = (ROOT / "research" / store / "registry.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        bad = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                bad += [a.name for a in n.names if "dataset_builder" in a.name]
            elif isinstance(n, ast.ImportFrom) and n.module:
                if "dataset_builder" in n.module:
                    bad.append(n.module)
        assert bad == [], f"{store} imports the builder: {bad}"

    def test_cited_kinds_are_the_three_reference_classes(self):
        assert ci.CITED_KINDS == {"evidence", "feature", "label"}

    def test_init_requires_registry_schema_first(self, tmp_path):
        p = tmp_path / "empty.db"
        sqlite3.connect(p).execute("CREATE TABLE unrelated (x TEXT)")
        with pytest.raises(reg.NotADatasetBuilderStore):
            ci.init_db(p)


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------

class TestRecordCitation:
    def test_records_an_edge(self, db):
        h = _declare(db)
        assert ci.record_citation(db, h, "feature", FEATURE_A) is True

    def test_duplicate_is_idempotent_not_an_error(self, db):
        h = _declare(db)
        assert ci.record_citation(db, h, "feature", FEATURE_A) is True
        assert ci.record_citation(db, h, "feature", FEATURE_A) is False
        assert len(ci.cites(db, h)) == 1

    def test_same_id_under_different_kinds_is_two_edges(self, db):
        h = _declare(db)
        ci.record_citation(db, h, "feature", "shared")
        ci.record_citation(db, h, "label", "shared")
        assert len(ci.cites(db, h)) == 2

    def test_unknown_kind_refused(self, db):
        h = _declare(db)
        with pytest.raises(ci.CitationIndexError, match="cited_kind"):
            ci.record_citation(db, h, "sourcecode", FEATURE_A)

    def test_blank_cited_id_refused_as_reverse_orphan(self, db):
        h = _declare(db)
        with pytest.raises(ci.CitationIndexError, match="orphan in the reverse"):
            ci.record_citation(db, h, "feature", "   ")

    def test_blank_dataset_hash_refused(self, db):
        with pytest.raises(ci.CitationIndexError, match="dataset_hash"):
            ci.record_citation(db, "", "feature", FEATURE_A)

    def test_detail_is_stored(self, db):
        h = _declare(db)
        ci.record_citation(db, h, "label", LABEL_A, cited_detail="matured")
        assert ci.cites(db, h)[0].cited_detail == "matured"


class TestForwardOrphanPrevention:
    def test_citation_from_unknown_dataset_refused(self, db):
        with pytest.raises(ci.OrphanCitationError, match="not in this registry"):
            ci.record_citation(db, "0" * 64, "feature", FEATURE_A)

    def test_nothing_written_when_refused(self, db):
        with pytest.raises(ci.OrphanCitationError):
            ci.record_citation(db, "0" * 64, "feature", FEATURE_A)
        con = sqlite3.connect(db)
        try:
            assert con.execute("SELECT COUNT(*) FROM citations").fetchone()[0] == 0
        finally:
            con.close()

    def test_foreign_key_blocks_a_direct_insert(self, db):
        """Belt and braces: even bypassing record_citation(), the FK refuses."""
        con = sqlite3.connect(db)
        con.execute("PRAGMA foreign_keys = ON")
        try:
            with pytest.raises(sqlite3.IntegrityError):
                con.execute(
                    "INSERT INTO citations (dataset_hash, cited_kind, cited_id, recorded_at) "
                    "VALUES (?, 'feature', ?, '2026-01-01')",
                    ("0" * 64, FEATURE_A),
                )
        finally:
            con.close()

    def test_find_orphans_empty_on_a_healthy_index(self, db):
        h = _declare(db)
        ci.record_citation(db, h, "feature", FEATURE_A)
        assert ci.find_orphans(db) == []


# ---------------------------------------------------------------------------
# index_declaration
# ---------------------------------------------------------------------------

class TestIndexDeclaration:
    def test_indexes_all_three_reference_classes(self, db):
        d = valid_declaration()
        h = _declare(db, d)
        written = ci.index_declaration(db, h, d)
        kinds = {c.cited_kind for c in ci.cites(db, h)}
        assert kinds == {"evidence", "feature", "label"}
        assert written == 4 + 2 + 2  # 4 sources, 2 features, 2 labels

    def test_label_maturity_carried_as_detail(self, db):
        d = valid_declaration()
        h = _declare(db, d)
        ci.index_declaration(db, h, d)
        labels = ci.cites(db, h, kind="label")
        assert all(c.cited_detail == "matured" for c in labels)

    def test_evidence_revision_carried_as_detail(self, db):
        d = valid_declaration()
        h = _declare(db, d)
        ci.index_declaration(db, h, d)
        by_id = {c.cited_id: c.cited_detail for c in ci.cites(db, h, kind="evidence")}
        assert by_id["dvol"] == "rev2"
        assert by_id["funding"] is None  # no revision declared for it

    def test_reindexing_is_idempotent(self, db):
        d = valid_declaration()
        h = _declare(db, d)
        first = ci.index_declaration(db, h, d)
        second = ci.index_declaration(db, h, d)
        assert first == 8 and second == 0
        assert len(ci.cites(db, h)) == 8

    def test_blank_feature_reference_refused(self, db):
        d = valid_declaration()
        d["feature_references"] = [""]
        h = _declare(db, d)
        with pytest.raises(ci.CitationIndexError, match="orphan in the reverse"):
            ci.index_declaration(db, h, d)

    def test_blank_source_refused(self, db):
        d = valid_declaration()
        d["evidence_references"]["sources"] = ["dvol", "  "]
        h = _declare(db, d)
        with pytest.raises(ci.CitationIndexError, match="orphan in the reverse"):
            ci.index_declaration(db, h, d)

    def test_label_reference_without_spec_hash_refused(self, db):
        # Declare a valid dataset, then hand index_declaration a malformed
        # declaration directly -- Tier A would reject this shape at declare
        # time, so the only way to exercise the indexer's own guard is to
        # call it with the bad payload.
        h = _declare(db)
        bad = valid_declaration()
        bad["label_references"] = [{"maturity_requirement": "matured"}]
        with pytest.raises(ci.CitationIndexError, match="no spec_hash"):
            ci.index_declaration(db, h, bad)

    def test_non_mapping_label_reference_refused(self, db):
        h = _declare(db)
        bad = valid_declaration()
        bad["label_references"] = ["just-a-hash"]
        with pytest.raises(ci.CitationIndexError, match="must be mappings"):
            ci.index_declaration(db, h, bad)

    def test_non_mapping_declaration_refused(self, db):
        h = _declare(db)
        with pytest.raises(ci.CitationIndexError, match="must be a mapping"):
            ci.index_declaration(db, h, ["nope"])


# ---------------------------------------------------------------------------
# FORWARD
# ---------------------------------------------------------------------------

class TestForward:
    def test_what_does_this_dataset_cite(self, db):
        d = valid_declaration()
        h = _declare(db, d)
        ci.index_declaration(db, h, d)
        ids = {c.cited_id for c in ci.cites(db, h, kind="feature")}
        assert ids == {FEATURE_A, FEATURE_B}

    def test_empty_for_a_dataset_with_no_citations(self, db):
        assert ci.cites(db, _declare(db)) == []

    def test_empty_for_an_unknown_dataset(self, db):
        assert ci.cites(db, "0" * 64) == []

    def test_kind_filter(self, db):
        d = valid_declaration()
        h = _declare(db, d)
        ci.index_declaration(db, h, d)
        assert len(ci.cites(db, h, kind="label")) == 2

    def test_unknown_kind_filter_refused(self, db):
        with pytest.raises(ci.CitationIndexError):
            ci.cites(db, _declare(db), kind="nonsense")

    def test_deterministic_order(self, db):
        d = valid_declaration()
        h = _declare(db, d)
        ci.index_declaration(db, h, d)
        a = [(c.cited_kind, c.cited_id) for c in ci.cites(db, h)]
        assert a == sorted(a)


# ---------------------------------------------------------------------------
# REVERSE — the edge that carries the architecture
# ---------------------------------------------------------------------------

class TestReverse:
    def test_what_cites_this_input(self, db):
        h = _sealed_dataset(db)
        assert ci.cited_by(db, "feature", FEATURE_A) == [h]

    def test_unsealed_dataset_does_not_pin_an_input(self, db):
        """ADR-I010 §11 says 'cited by any SEALED artifact'. A Declared
        dataset may still be Rejected, so it must not block retirement."""
        d = valid_declaration()
        h = _declare(db, d)
        ci.index_declaration(db, h, d)
        assert ci.cited_by(db, "feature", FEATURE_A) == []
        assert ci.is_cited(db, "feature", FEATURE_A) is False

    def test_sealing_makes_the_citation_binding(self, db):
        d = valid_declaration()
        h = _declare(db, d)
        ci.index_declaration(db, h, d)
        assert ci.is_cited(db, "feature", FEATURE_A) is False
        _seal(db, h)
        assert ci.is_cited(db, "feature", FEATURE_A) is True

    def test_superseded_still_cites(self, db):
        """§10.2 — supersession never invalidates."""
        a = _sealed_dataset(db)
        d2 = valid_declaration()
        d2["temporal_scope"]["window_end"] = "2025-06-30"
        b = _sealed_dataset(db, d2)
        reg.supersede(db, a, successor_hash=b, change_class="extension")
        assert a in ci.cited_by(db, "feature", FEATURE_A)

    def test_retired_dataset_stops_pinning(self, db):
        h = _sealed_dataset(db)
        reg.retire(db, h)
        assert ci.cited_by(db, "feature", FEATURE_A) == []

    def test_multiple_datasets_citing_one_input(self, db):
        a = _sealed_dataset(db)
        d2 = valid_declaration()
        d2["intent"]["rationale"] = "second line"
        b = _sealed_dataset(db, d2, lineage="line-b")
        assert set(ci.cited_by(db, "feature", FEATURE_A)) == {a, b}

    def test_include_phases_widening(self, db):
        d = valid_declaration()
        h = _declare(db, d)
        ci.index_declaration(db, h, d)
        assert ci.cited_by(db, "feature", FEATURE_A) == []
        assert ci.cited_by(db, "feature", FEATURE_A, include_phases=reg.PHASES) == [h]

    def test_unknown_phase_refused(self, db):
        with pytest.raises(ci.CitationIndexError, match="unknown phase"):
            ci.cited_by(db, "feature", FEATURE_A, include_phases={"Imaginary"})

    def test_empty_phase_set_returns_empty(self, db):
        _sealed_dataset(db)
        assert ci.cited_by(db, "feature", FEATURE_A, include_phases=set()) == []

    def test_unknown_kind_refused(self, db):
        with pytest.raises(ci.CitationIndexError, match="cited_kind"):
            ci.cited_by(db, "nope", FEATURE_A)

    def test_uncited_input_returns_empty(self, db):
        _sealed_dataset(db)
        assert ci.cited_by(db, "feature", "z" * 64) == []

    def test_blast_radius_spans_all_phases(self, db):
        d = valid_declaration()
        declared = _declare(db, d)
        ci.index_declaration(db, declared, d)
        d2 = valid_declaration()
        d2["intent"]["rationale"] = "sealed one"
        sealed = _sealed_dataset(db, d2, lineage="line-b")
        radius = ci.blast_radius(db, "feature", FEATURE_A)
        assert radius["Declared"] == [declared]
        assert radius["Sealed"] == [sealed]


# ---------------------------------------------------------------------------
# Retirement refusal — the roadmap's headline acceptance for 4.2
# ---------------------------------------------------------------------------

class TestRetirementRefusal:
    """Closes ADR-I010 §11 and ADR-I011 LG5. Both stores shipped with a
    docstring deferring this check to 'whichever future milestone builds the
    index'. These tests are that milestone honouring it."""

    def _feature_store(self, tmp_path):
        from feature_store import registry as fs
        p = tmp_path / "feature_store.db"
        fs.init_db(p)
        return fs, p

    def _label_store(self, tmp_path):
        from label_store import registry as ls
        p = tmp_path / "label_store.db"
        ls.init_db(p)
        return ls, p

    def test_feature_store_refuses_while_cited(self, db, tmp_path):
        from feature_store import registry as fs
        _sealed_dataset(db)
        check = ci.make_citation_check(db, "feature")
        with pytest.raises(fs.SpecificationCited, match="refusing to retire"):
            fs.retire_specification(
                tmp_path / "unused.db", FEATURE_A, "Deprecated", citation_check=check
            )

    def test_label_store_refuses_while_cited(self, db, tmp_path):
        from label_store import registry as ls
        _sealed_dataset(db)
        check = ci.make_citation_check(db, "label")
        with pytest.raises(ls.SpecificationCited, match="refusing to retire"):
            ls.retire_specification(
                tmp_path / "unused.db", LABEL_A, "Deprecated", citation_check=check
            )

    def test_refusal_happens_before_any_write(self, db, tmp_path):
        """The refusal must precede the transition, or a cited Specification
        would be retired and then complained about."""
        from feature_store import registry as fs
        _sealed_dataset(db)
        missing = tmp_path / "does-not-exist.db"
        check = ci.make_citation_check(db, "feature")
        with pytest.raises(fs.SpecificationCited):
            fs.retire_specification(missing, FEATURE_A, "Deprecated", citation_check=check)
        assert not missing.exists(), "a write was attempted before the refusal"

    @pytest.mark.parametrize("store_kind", ["feature", "label"])
    def test_uncited_specification_is_not_refused(self, db, tmp_path, store_kind):
        """The check must not refuse everything -- an uncited Specification
        passes the citation gate and proceeds to the real transition, which
        then fails for its own (unrelated) reason: no such row."""
        from feature_store import registry as fs
        from label_store import registry as ls
        mod = fs if store_kind == "feature" else ls
        store = tmp_path / f"{store_kind}_store.db"
        mod.init_db(store)
        _sealed_dataset(db)
        check = ci.make_citation_check(db, store_kind)
        with pytest.raises(mod.TransitionError):
            mod.retire_specification(
                store, "z" * 64, "Deprecated", citation_check=check
            )

    def test_unsealed_citation_does_not_block_retirement(self, db, tmp_path):
        from feature_store import registry as fs
        store = tmp_path / "feature_store.db"
        fs.init_db(store)
        d = valid_declaration()
        h = _declare(db, d)
        ci.index_declaration(db, h, d)  # Declared, never sealed
        check = ci.make_citation_check(db, "feature")
        # TransitionError (no such row), NOT SpecificationCited -- the point
        # is that the citation gate did not fire.
        with pytest.raises(fs.TransitionError):
            fs.retire_specification(
                store, FEATURE_A, "Deprecated", citation_check=check
            )

    @pytest.mark.parametrize("store", ["feature_store", "label_store"])
    def test_backward_compatible_default(self, store, tmp_path):
        """citation_check=None preserves the pre-4.2 signature exactly."""
        import importlib
        mod = importlib.import_module(f"{store}.registry")
        import inspect
        sig = inspect.signature(mod.retire_specification)
        assert sig.parameters["citation_check"].default is None

    def test_assert_retirable_names_the_citing_datasets(self, db):
        h = _sealed_dataset(db)
        with pytest.raises(ci.CitedSpecificationError) as exc:
            ci.assert_retirable(db, "feature", FEATURE_A)
        assert exc.value.citing == [h]
        assert exc.value.cited_kind == "feature"

    def test_assert_retirable_passes_when_uncited(self, db):
        _sealed_dataset(db)
        ci.assert_retirable(db, "feature", "z" * 64)

    def test_make_citation_check_rejects_unknown_kind(self, db):
        with pytest.raises(ci.CitationIndexError):
            ci.make_citation_check(db, "sourcecode")


# ---------------------------------------------------------------------------
# Append-only / immutability
# ---------------------------------------------------------------------------

class TestAppendOnly:
    def test_update_forbidden(self, db):
        h = _declare(db)
        ci.record_citation(db, h, "feature", FEATURE_A)
        con = sqlite3.connect(db)
        try:
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                con.execute("UPDATE citations SET cited_id = 'x'")
        finally:
            con.close()

    def test_delete_forbidden(self, db):
        h = _declare(db)
        ci.record_citation(db, h, "feature", FEATURE_A)
        con = sqlite3.connect(db)
        try:
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                con.execute("DELETE FROM citations")
        finally:
            con.close()

    def test_citations_survive_rejection(self, db):
        """A Rejected dataset keeps its citation rows -- the record of what it
        would have cited is itself evidence."""
        d = valid_declaration()
        h = _declare(db, d)
        ci.index_declaration(db, h, d)
        reg.reject(db, h, "Declared", "hard failure")
        assert len(ci.cites(db, h)) == 8

    def test_check_constraint_rejects_unknown_kind_directly(self, db):
        h = _declare(db)
        con = sqlite3.connect(db)
        try:
            with pytest.raises(sqlite3.IntegrityError):
                con.execute(
                    "INSERT INTO citations (dataset_hash, cited_kind, cited_id, recorded_at) "
                    "VALUES (?, 'bogus', 'x', '2026-01-01')", (h,)
                )
        finally:
            con.close()


# ---------------------------------------------------------------------------
# Bidirectional consistency
# ---------------------------------------------------------------------------

class TestBidirectionalConsistency:
    def test_healthy_index_verifies(self, db):
        _sealed_dataset(db)
        ok, errors = ci.verify_bidirectional_consistency(db)
        assert ok is True and errors == []

    def test_every_forward_edge_is_answerable_in_reverse(self, db):
        h = _sealed_dataset(db)
        for c in ci.cites(db, h):
            assert h in ci.cited_by(db, c.cited_kind, c.cited_id, include_phases=reg.PHASES)

    def test_every_reverse_edge_is_answerable_forward(self, db):
        h = _sealed_dataset(db)
        for kind, cid in [("feature", FEATURE_A), ("label", LABEL_A), ("evidence", "dvol")]:
            for ds in ci.cited_by(db, kind, cid):
                assert (kind, cid) in {(c.cited_kind, c.cited_id) for c in ci.cites(db, ds)}

    def test_empty_index_verifies(self, db):
        ok, errors = ci.verify_bidirectional_consistency(db)
        assert ok is True and errors == []

    def test_detects_an_injected_orphan(self, db):
        """Simulate a hand-edited or partially-restored database. The FK is
        disabled for the insert so the auditor has something to find."""
        con = sqlite3.connect(db)
        try:
            con.execute("PRAGMA foreign_keys = OFF")
            con.execute(
                "INSERT INTO citations (dataset_hash, cited_kind, cited_id, recorded_at) "
                "VALUES ('deadbeef', 'feature', ?, '2026-01-01')", (FEATURE_A,)
            )
            con.commit()
        finally:
            con.close()
        assert len(ci.find_orphans(db)) == 1
        ok, errors = ci.verify_bidirectional_consistency(db)
        assert ok is False and any("orphan" in e for e in errors)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class TestCLI:
    def test_requires_db(self):
        with pytest.raises(SystemExit) as exc:
            ci._cli([])
        assert exc.value.code == 2

    def test_no_default_on_db_arg(self):
        src = (ROOT / "research" / "dataset_builder" / "citation_index.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "add_argument":
                names = [a.value for a in n.args if isinstance(a, ast.Constant)]
                if "--db" in names:
                    kw = {k.arg for k in n.keywords}
                    assert "default" not in kw and "required" in kw

    def test_summary(self, db, capsys):
        _sealed_dataset(db)
        assert ci._cli(["--db", str(db)]) == 0
        assert "Citation index" in capsys.readouterr().out

    def test_cites(self, db, capsys):
        h = _sealed_dataset(db)
        assert ci._cli(["--db", str(db), "--cites", h]) == 0
        assert FEATURE_A in capsys.readouterr().out

    def test_cited_by(self, db, capsys):
        h = _sealed_dataset(db)
        assert ci._cli(["--db", str(db), "--cited-by", "feature", FEATURE_A]) == 0
        assert h in capsys.readouterr().out

    def test_cited_by_unknown_kind_returns_2(self, db):
        assert ci._cli(["--db", str(db), "--cited-by", "bogus", "x"]) == 2

    def test_blast_radius(self, db, capsys):
        _sealed_dataset(db)
        assert ci._cli(["--db", str(db), "--blast-radius", "feature", FEATURE_A]) == 0
        assert "Sealed" in capsys.readouterr().out

    def test_verify_ok(self, db, capsys):
        _sealed_dataset(db)
        assert ci._cli(["--db", str(db), "--verify"]) == 0
        assert "OK" in capsys.readouterr().out

    def test_verify_detects_orphan(self, db):
        con = sqlite3.connect(db)
        con.execute("PRAGMA foreign_keys = OFF")
        con.execute(
            "INSERT INTO citations (dataset_hash, cited_kind, cited_id, recorded_at) "
            "VALUES ('nope', 'feature', 'x', '2026-01-01')")
        con.commit(); con.close()
        assert ci._cli(["--db", str(db), "--verify"]) == 1

    def test_refuses_store_without_citation_table(self, tmp_path):
        p = tmp_path / "d.db"
        reg.init_db(p)  # registry only, no citation schema
        assert ci._cli(["--db", str(p)]) == 2


# ---------------------------------------------------------------------------
# TV3 — sabotage
# ---------------------------------------------------------------------------

class TestTV3Sabotage:
    def test_reverse_edge_is_not_vacuous(self, db):
        """If cited_by() always returned [], every retirement test would pass
        for the wrong reason. Prove it returns something."""
        h = _sealed_dataset(db)
        assert ci.cited_by(db, "feature", FEATURE_A) == [h]
        assert ci.cited_by(db, "label", LABEL_A) == [h]
        assert ci.cited_by(db, "evidence", "dvol") == [h]

    def test_phase_gate_is_not_vacuous(self, db):
        """If the phase filter were dropped, the unsealed-does-not-pin test
        would pass trivially. Prove both sides of the gate."""
        d = valid_declaration()
        h = _declare(db, d)
        ci.index_declaration(db, h, d)
        assert ci.cited_by(db, "feature", FEATURE_A) == []
        _seal(db, h)
        assert ci.cited_by(db, "feature", FEATURE_A) == [h]

    def test_orphan_detector_is_not_vacuous(self, db):
        assert ci.find_orphans(db) == []
        con = sqlite3.connect(db)
        con.execute("PRAGMA foreign_keys = OFF")
        con.execute(
            "INSERT INTO citations (dataset_hash, cited_kind, cited_id, recorded_at) "
            "VALUES ('ghost', 'feature', 'x', '2026-01-01')")
        con.commit(); con.close()
        assert len(ci.find_orphans(db)) == 1


# ---------------------------------------------------------------------------
# §9 RBV / regression
# ---------------------------------------------------------------------------

class TestRegression:
    def test_registry_behaviour_unchanged(self, db):
        """4.1's registry must behave identically with the citation schema
        present -- 4.2 is additive."""
        h = _declare(db)
        assert reg.get_specification(db, h)["phase"] == "Declared"
        _seal(db, h)
        assert reg.is_citable(db, h) is True

    def test_citation_schema_is_additive(self, tmp_path):
        p = tmp_path / "d.db"
        reg.init_db(p)
        before = {r[0] for r in sqlite3.connect(p).execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        ci.init_db(p)
        after = {r[0] for r in sqlite3.connect(p).execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert before < after and after - before == {"citations", "sqlite_sequence"}

    def test_init_db_idempotent(self, db):
        ci.init_db(db)
        ci.init_db(db)
        h = _declare(db)
        assert ci.record_citation(db, h, "feature", FEATURE_A) is True

    def test_no_other_store_touched(self, tmp_path):
        p = tmp_path / "d.db"
        reg.init_db(p); ci.init_db(p)
        _sealed_dataset(p)
        assert {f.name for f in tmp_path.rglob("*.db")} == {"d.db"}
