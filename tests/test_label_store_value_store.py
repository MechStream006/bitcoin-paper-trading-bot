"""
Tests for research/label_store/value_store.py (Phase 3 Label Store roadmap
v1.1.0, WBS 3.2 -- the second Label Store milestone).

Per WBS 3.2's own exit criterion: "Two tiers live; a matured label cannot
be updated by ANY SQL path, proven by an adversarial direct-sqlite3 test;
every allowed maturity arc tested, every forbidden arc refused; Void
recorded permanently."

Pure stdlib module (sqlite3/json/hashlib) -- no research dependency, runs
in any environment.
"""
from __future__ import annotations

import ast
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

# importlib, not sys.path-insert + bare `import value_store`, for the same
# reason as tests/test_label_store_registry.py: this module's bare name
# collides in sys.modules with research/feature_store/value_store.py,
# which test_feature_store_value_store.py also bare-imports as
# `value_store` in the same pytest process.
_spec = importlib.util.spec_from_file_location(
    "label_store_value_store",
    Path(__file__).resolve().parents[1] / "research" / "label_store" / "value_store.py",
)
vs = importlib.util.module_from_spec(_spec)
sys.modules["label_store_value_store"] = vs
_spec.loader.exec_module(vs)

NOW = "2026-08-05T00:00:00+00:00"
RESEARCH_ADDR = dict(spec_hash="deadbeef" * 8, corpus_version="corpus-v1", as_of="2026-08-05")
FORWARD_ADDR = dict(spec_hash="deadbeef" * 8, subject_id="trade-001", declared_at="2026-08-01T00:00:00+00:00")


@pytest.fixture
def db(tmp_path) -> Path:
    p = tmp_path / "label_store.db"
    vs.init_db(p)
    return p


def _declare(db, **overrides):
    kwargs = dict(declared_by="rohith", **FORWARD_ADDR)
    kwargs.update(overrides)
    vs.declare_forward_label(db, **kwargs)


# ---------------------------------------------------------------------------
# Research tier
# ---------------------------------------------------------------------------

class TestResearchTier:
    def test_put_then_get_round_trips(self, db):
        vs.put_research_value(db, computed_at=NOW, values=[1, 2, 3], **RESEARCH_ADDR)
        assert vs.get_research_value(db, **RESEARCH_ADDR) == [1, 2, 3]

    def test_get_on_empty_store_returns_none(self, db):
        assert vs.get_research_value(db, **RESEARCH_ADDR) is None

    def test_identical_content_rewrite_is_idempotent(self, db):
        first = vs.put_research_value(db, computed_at=NOW, values=[1, 2, 3], **RESEARCH_ADDR)
        second = vs.put_research_value(db, computed_at=NOW, values=[1, 2, 3], **RESEARCH_ADDR)
        assert first == second
        assert vs.get_research_value(db, **RESEARCH_ADDR) == [1, 2, 3]

    def test_differing_content_at_the_same_address_raises_divergence(self, db):
        vs.put_research_value(db, computed_at=NOW, values=[1, 2, 3], **RESEARCH_ADDR)
        with pytest.raises(vs.LabelDivergenceEvent):
            vs.put_research_value(db, computed_at=NOW, values=[9, 9, 9], **RESEARCH_ADDR)

    def test_divergent_write_never_overwrites_the_original(self, db):
        vs.put_research_value(db, computed_at=NOW, values=[1, 2, 3], **RESEARCH_ADDR)
        try:
            vs.put_research_value(db, computed_at=NOW, values=[9, 9, 9], **RESEARCH_ADDR)
        except vs.LabelDivergenceEvent:
            pass
        assert vs.get_research_value(db, **RESEARCH_ADDR) == [1, 2, 3]

    def test_different_addresses_are_independent(self, db):
        vs.put_research_value(db, computed_at=NOW, values=[1], **RESEARCH_ADDR)
        other = {**RESEARCH_ADDR, "as_of": "2026-08-06"}
        vs.put_research_value(db, computed_at=NOW, values=[2], **other)
        assert vs.get_research_value(db, **RESEARCH_ADDR) == [1]
        assert vs.get_research_value(db, **other) == [2]

    def test_evict_removes_the_entry(self, db):
        vs.put_research_value(db, computed_at=NOW, values=[1, 2, 3], **RESEARCH_ADDR)
        vs.evict_research_value(db, **RESEARCH_ADDR)
        assert vs.get_research_value(db, **RESEARCH_ADDR) is None

    def test_evict_on_nonexistent_entry_does_not_raise(self, db):
        vs.evict_research_value(db, **RESEARCH_ADDR)  # must not raise

    def test_evict_then_recompute_fresh_is_not_a_divergence(self, db):
        # §7: "if lost, nothing is lost" -- eviction followed by a fresh
        # (even differing) recomputation is expected, not a divergence.
        vs.put_research_value(db, computed_at=NOW, values=[1, 2, 3], **RESEARCH_ADDR)
        vs.evict_research_value(db, **RESEARCH_ADDR)
        vs.put_research_value(db, computed_at=NOW, values=[9, 9, 9], **RESEARCH_ADDR)
        assert vs.get_research_value(db, **RESEARCH_ADDR) == [9, 9, 9]

    def test_corrupted_stored_bytes_raise_divergence_on_read(self, db):
        vs.put_research_value(db, computed_at=NOW, values=[1, 2, 3], **RESEARCH_ADDR)
        con = sqlite3.connect(db)
        con.execute(
            "UPDATE label_value_research SET values_json = '[9,9,9]' "
            "WHERE spec_hash = ? AND corpus_version = ? AND as_of = ?",
            (RESEARCH_ADDR["spec_hash"], RESEARCH_ADDR["corpus_version"], RESEARCH_ADDR["as_of"]),
        )
        con.commit()
        con.close()
        with pytest.raises(vs.LabelDivergenceEvent):
            vs.get_research_value(db, **RESEARCH_ADDR)


class TestResolveResearch:
    def test_computes_and_stores_on_first_call(self, db):
        calls = {"n": 0}

        def compute():
            calls["n"] += 1
            return [1, 2, 3]

        result = vs.resolve_research(db, compute_fn=compute, computed_at=NOW, **RESEARCH_ADDR)
        assert result.values == [1, 2, 3]
        assert result.source == "computed"
        assert calls["n"] == 1
        assert vs.get_research_value(db, **RESEARCH_ADDR) == [1, 2, 3]

    def test_second_call_serves_from_store_without_recomputing(self, db):
        calls = {"n": 0}

        def compute():
            calls["n"] += 1
            return [1, 2, 3]

        vs.resolve_research(db, compute_fn=compute, computed_at=NOW, **RESEARCH_ADDR)
        result = vs.resolve_research(db, compute_fn=compute, computed_at=NOW, **RESEARCH_ADDR)
        assert result.source == "cached"
        assert calls["n"] == 1


# ---------------------------------------------------------------------------
# Forward tier -- declaration, addressing, field validation
# ---------------------------------------------------------------------------

class TestForwardDeclaration:
    def test_declare_then_get_round_trips_as_declared(self, db):
        _declare(db)
        label = vs.get_forward_label(db, **FORWARD_ADDR)
        assert label["maturity_state"] == "Declared"
        assert label["declared_by"] == "rohith"
        assert label["outcome_json"] is None

    def test_get_on_nonexistent_label_returns_none(self, db):
        assert vs.get_forward_label(db, **FORWARD_ADDR) is None

    @pytest.mark.parametrize("field", ["spec_hash", "subject_id", "declared_at", "declared_by"])
    def test_blank_required_field_is_refused(self, db, field):
        kwargs = dict(declared_by="rohith", **FORWARD_ADDR)
        kwargs[field] = "   "
        with pytest.raises(vs.LabelValueStoreError):
            vs.declare_forward_label(db, **kwargs)

    def test_addressing_is_the_spec_subject_declared_at_triple(self, db):
        # §9.1: different declared_at for the same (spec_hash, subject_id)
        # is a DIFFERENT forward label, not an update to the same one.
        _declare(db)
        other = {**FORWARD_ADDR, "declared_at": "2026-08-02T00:00:00+00:00"}
        vs.declare_forward_label(db, declared_by="rohith", **other)
        assert vs.get_forward_label(db, **FORWARD_ADDR)["declared_at"] == FORWARD_ADDR["declared_at"]
        assert vs.get_forward_label(db, **other)["declared_at"] == other["declared_at"]


# ---------------------------------------------------------------------------
# Forward tier -- every allowed arc, exercised end to end
# ---------------------------------------------------------------------------

class TestAllowedMaturityArcs:
    def test_declared_to_pending(self, db):
        _declare(db)
        vs.observe_forward_label(db, observed_at=NOW, **FORWARD_ADDR)
        label = vs.get_forward_label(db, **FORWARD_ADDR)
        assert label["maturity_state"] == "Pending"
        assert label["observed_at"] == NOW

    def test_pending_to_matured(self, db):
        _declare(db)
        vs.observe_forward_label(db, observed_at=NOW, **FORWARD_ADDR)
        vs.mature_forward_label(
            db, outcome={"forward_return": 0.031}, input_revision_set=["rev-1", "rev-2"],
            matured_at="2026-08-06T00:00:00+00:00", **FORWARD_ADDR,
        )
        label = vs.get_forward_label(db, **FORWARD_ADDR)
        assert label["maturity_state"] == "Matured"
        assert label["outcome"] == {"forward_return": 0.031}
        assert label["input_revision_set"] == ["rev-1", "rev-2"]

    def test_pending_to_void(self, db):
        _declare(db)
        vs.observe_forward_label(db, observed_at=NOW, **FORWARD_ADDR)
        vs.void_forward_label(db, void_at=NOW, void_reason="subject invalidated", **FORWARD_ADDR)
        label = vs.get_forward_label(db, **FORWARD_ADDR)
        assert label["maturity_state"] == "Void"
        assert label["void_reason"] == "subject invalidated"

    def test_matured_to_flagged(self, db):
        _declare(db)
        vs.observe_forward_label(db, observed_at=NOW, **FORWARD_ADDR)
        vs.mature_forward_label(db, outcome={"r": 1}, input_revision_set=["rev-1"],
                                 matured_at=NOW, **FORWARD_ADDR)
        vs.flag_forward_label(db, flagged_at="2026-09-01T00:00:00+00:00",
                               flag_reason="cited revision corrected", **FORWARD_ADDR)
        label = vs.get_forward_label(db, **FORWARD_ADDR)
        assert label["maturity_state"] == "Flagged"
        # §11.3: neither rewritten nor trusted -- the outcome is untouched.
        assert label["outcome"] == {"r": 1}

    def test_flagged_to_adjudicated(self, db):
        _declare(db)
        vs.observe_forward_label(db, observed_at=NOW, **FORWARD_ADDR)
        vs.mature_forward_label(db, outcome={"r": 1}, input_revision_set=["rev-1"],
                                 matured_at=NOW, **FORWARD_ADDR)
        vs.flag_forward_label(db, flagged_at=NOW, flag_reason="x", **FORWARD_ADDR)
        vs.adjudicate_forward_label(
            db, adjudicated_at="2026-09-02T00:00:00+00:00",
            adjudication_decision="admissible despite flag", **FORWARD_ADDR,
        )
        label = vs.get_forward_label(db, **FORWARD_ADDR)
        assert label["maturity_state"] == "Adjudicated"
        assert label["adjudication_decision"] == "admissible despite flag"


# ---------------------------------------------------------------------------
# Forward tier -- every forbidden arc refused (Python-level pre-check)
# ---------------------------------------------------------------------------

class TestForbiddenMaturityArcs:
    def test_declared_to_matured_is_refused(self, db):
        _declare(db)
        with pytest.raises(vs.LabelTransitionError):
            vs.mature_forward_label(db, outcome={}, input_revision_set=[], matured_at=NOW, **FORWARD_ADDR)

    def test_declared_to_void_is_refused(self, db):
        _declare(db)
        with pytest.raises(vs.LabelTransitionError):
            vs.void_forward_label(db, void_at=NOW, void_reason="x", **FORWARD_ADDR)

    def test_declared_to_flagged_is_refused(self, db):
        _declare(db)
        with pytest.raises(vs.LabelTransitionError):
            vs.flag_forward_label(db, flagged_at=NOW, flag_reason="x", **FORWARD_ADDR)

    def test_pending_to_flagged_is_refused(self, db):
        _declare(db)
        vs.observe_forward_label(db, observed_at=NOW, **FORWARD_ADDR)
        with pytest.raises(vs.LabelTransitionError):
            vs.flag_forward_label(db, flagged_at=NOW, flag_reason="x", **FORWARD_ADDR)

    def test_pending_to_adjudicated_is_refused(self, db):
        _declare(db)
        vs.observe_forward_label(db, observed_at=NOW, **FORWARD_ADDR)
        with pytest.raises(vs.LabelTransitionError):
            vs.adjudicate_forward_label(db, adjudicated_at=NOW, adjudication_decision="x", **FORWARD_ADDR)

    def test_matured_to_void_is_refused(self, db):
        _declare(db)
        vs.observe_forward_label(db, observed_at=NOW, **FORWARD_ADDR)
        vs.mature_forward_label(db, outcome={}, input_revision_set=[], matured_at=NOW, **FORWARD_ADDR)
        with pytest.raises(vs.LabelTransitionError):
            vs.void_forward_label(db, void_at=NOW, void_reason="x", **FORWARD_ADDR)

    def test_matured_to_adjudicated_is_refused(self, db):
        _declare(db)
        vs.observe_forward_label(db, observed_at=NOW, **FORWARD_ADDR)
        vs.mature_forward_label(db, outcome={}, input_revision_set=[], matured_at=NOW, **FORWARD_ADDR)
        with pytest.raises(vs.LabelTransitionError):
            vs.adjudicate_forward_label(db, adjudicated_at=NOW, adjudication_decision="x", **FORWARD_ADDR)

    def test_void_permits_no_further_transition(self, db):
        _declare(db)
        vs.observe_forward_label(db, observed_at=NOW, **FORWARD_ADDR)
        vs.void_forward_label(db, void_at=NOW, void_reason="x", **FORWARD_ADDR)
        with pytest.raises(vs.LabelTransitionError):
            vs.observe_forward_label(db, observed_at=NOW, **FORWARD_ADDR)
        with pytest.raises(vs.LabelTransitionError):
            vs.mature_forward_label(db, outcome={}, input_revision_set=[], matured_at=NOW, **FORWARD_ADDR)

    def test_adjudicated_permits_no_further_transition(self, db):
        _declare(db)
        vs.observe_forward_label(db, observed_at=NOW, **FORWARD_ADDR)
        vs.mature_forward_label(db, outcome={}, input_revision_set=[], matured_at=NOW, **FORWARD_ADDR)
        vs.flag_forward_label(db, flagged_at=NOW, flag_reason="x", **FORWARD_ADDR)
        vs.adjudicate_forward_label(db, adjudicated_at=NOW, adjudication_decision="x", **FORWARD_ADDR)
        with pytest.raises(vs.LabelTransitionError):
            vs.flag_forward_label(db, flagged_at=NOW, flag_reason="y", **FORWARD_ADDR)

    def test_transition_on_undeclared_label_raises(self, db):
        with pytest.raises(vs.LabelValueStoreError):
            vs.observe_forward_label(db, observed_at=NOW, **FORWARD_ADDR)

    def test_re_observing_an_already_pending_label_is_refused(self, db):
        _declare(db)
        vs.observe_forward_label(db, observed_at=NOW, **FORWARD_ADDR)
        with pytest.raises(vs.LabelTransitionError):
            vs.observe_forward_label(db, observed_at="2026-08-02T00:00:00+00:00", **FORWARD_ADDR)


class TestForwardFieldValidation:
    def test_blank_void_reason_is_refused(self, db):
        _declare(db)
        vs.observe_forward_label(db, observed_at=NOW, **FORWARD_ADDR)
        with pytest.raises(vs.LabelValueStoreError):
            vs.void_forward_label(db, void_at=NOW, void_reason="  ", **FORWARD_ADDR)

    def test_missing_input_revision_set_is_refused(self, db):
        _declare(db)
        vs.observe_forward_label(db, observed_at=NOW, **FORWARD_ADDR)
        with pytest.raises(vs.LabelValueStoreError):
            vs.mature_forward_label(db, outcome={}, input_revision_set=None, matured_at=NOW, **FORWARD_ADDR)

    def test_empty_input_revision_set_is_a_valid_declaration(self, db):
        _declare(db)
        vs.observe_forward_label(db, observed_at=NOW, **FORWARD_ADDR)
        vs.mature_forward_label(db, outcome={}, input_revision_set=[], matured_at=NOW, **FORWARD_ADDR)
        assert vs.get_forward_label(db, **FORWARD_ADDR)["input_revision_set"] == []

    def test_blank_flag_reason_is_refused(self, db):
        _declare(db)
        vs.observe_forward_label(db, observed_at=NOW, **FORWARD_ADDR)
        vs.mature_forward_label(db, outcome={}, input_revision_set=[], matured_at=NOW, **FORWARD_ADDR)
        with pytest.raises(vs.LabelValueStoreError):
            vs.flag_forward_label(db, flagged_at=NOW, flag_reason="", **FORWARD_ADDR)

    def test_blank_adjudication_decision_is_refused(self, db):
        _declare(db)
        vs.observe_forward_label(db, observed_at=NOW, **FORWARD_ADDR)
        vs.mature_forward_label(db, outcome={}, input_revision_set=[], matured_at=NOW, **FORWARD_ADDR)
        vs.flag_forward_label(db, flagged_at=NOW, flag_reason="x", **FORWARD_ADDR)
        with pytest.raises(vs.LabelValueStoreError):
            vs.adjudicate_forward_label(db, adjudicated_at=NOW, adjudication_decision=" ", **FORWARD_ADDR)


# ---------------------------------------------------------------------------
# Adversarial storage-layer tests -- the milestone's central claim.
# Every write below goes through a RAW sqlite3 connection, bypassing this
# module's Python functions entirely.
# ---------------------------------------------------------------------------

class TestStorageLayerFillOnce:
    def _matured_row(self, db):
        _declare(db)
        vs.observe_forward_label(db, observed_at=NOW, **FORWARD_ADDR)
        vs.mature_forward_label(
            db, outcome={"r": 1}, input_revision_set=["rev-1"], matured_at=NOW, **FORWARD_ADDR,
        )

    def test_direct_sql_cannot_rewrite_outcome_of_a_matured_label(self, db):
        self._matured_row(db)
        con = sqlite3.connect(db)
        with pytest.raises(sqlite3.IntegrityError, match="LABEL_TAMPER"):
            con.execute(
                "UPDATE label_value_forward SET outcome_json = '{\"r\": 999}' "
                "WHERE spec_hash = ? AND subject_id = ? AND declared_at = ?",
                (FORWARD_ADDR["spec_hash"], FORWARD_ADDR["subject_id"], FORWARD_ADDR["declared_at"]),
            )
        con.close()
        # The original must be completely intact.
        assert vs.get_forward_label(db, **FORWARD_ADDR)["outcome"] == {"r": 1}

    def test_direct_sql_cannot_rewrite_matured_at(self, db):
        self._matured_row(db)
        con = sqlite3.connect(db)
        with pytest.raises(sqlite3.IntegrityError, match="LABEL_TAMPER"):
            con.execute(
                "UPDATE label_value_forward SET matured_at = '2099-01-01T00:00:00+00:00' "
                "WHERE spec_hash = ? AND subject_id = ? AND declared_at = ?",
                (FORWARD_ADDR["spec_hash"], FORWARD_ADDR["subject_id"], FORWARD_ADDR["declared_at"]),
            )
        con.close()

    def test_direct_sql_cannot_rewrite_input_revision_set_of_a_matured_label(self, db):
        self._matured_row(db)
        con = sqlite3.connect(db)
        with pytest.raises(sqlite3.IntegrityError, match="LABEL_TAMPER"):
            con.execute(
                "UPDATE label_value_forward SET input_revision_set_json = '[\"tampered\"]' "
                "WHERE spec_hash = ? AND subject_id = ? AND declared_at = ?",
                (FORWARD_ADDR["spec_hash"], FORWARD_ADDR["subject_id"], FORWARD_ADDR["declared_at"]),
            )
        con.close()

    def test_direct_sql_cannot_rewrite_outcome_after_flagging(self, db):
        # §11.3: Flagged is still "neither rewritten nor trusted" -- the
        # fill-once guard must hold in Flagged and Adjudicated states too,
        # not just Matured.
        self._matured_row(db)
        vs.flag_forward_label(db, flagged_at=NOW, flag_reason="x", **FORWARD_ADDR)
        con = sqlite3.connect(db)
        with pytest.raises(sqlite3.IntegrityError, match="LABEL_TAMPER"):
            con.execute(
                "UPDATE label_value_forward SET outcome_json = '{\"r\": 999}' "
                "WHERE spec_hash = ? AND subject_id = ? AND declared_at = ?",
                (FORWARD_ADDR["spec_hash"], FORWARD_ADDR["subject_id"], FORWARD_ADDR["declared_at"]),
            )
        con.close()

    def test_direct_sql_cannot_perform_an_illegal_arc(self, db):
        self._matured_row(db)
        con = sqlite3.connect(db)
        with pytest.raises(sqlite3.IntegrityError, match="LABEL_TAMPER"):
            con.execute(
                "UPDATE label_value_forward SET maturity_state = 'Pending' "
                "WHERE spec_hash = ? AND subject_id = ? AND declared_at = ?",
                (FORWARD_ADDR["spec_hash"], FORWARD_ADDR["subject_id"], FORWARD_ADDR["declared_at"]),
            )
        con.close()

    def test_direct_sql_cannot_move_out_of_void(self, db):
        _declare(db)
        vs.observe_forward_label(db, observed_at=NOW, **FORWARD_ADDR)
        vs.void_forward_label(db, void_at=NOW, void_reason="x", **FORWARD_ADDR)
        con = sqlite3.connect(db)
        with pytest.raises(sqlite3.IntegrityError, match="LABEL_TAMPER"):
            con.execute(
                "UPDATE label_value_forward SET maturity_state = 'Pending' "
                "WHERE spec_hash = ? AND subject_id = ? AND declared_at = ?",
                (FORWARD_ADDR["spec_hash"], FORWARD_ADDR["subject_id"], FORWARD_ADDR["declared_at"]),
            )
        con.close()
        assert vs.get_forward_label(db, **FORWARD_ADDR)["maturity_state"] == "Void"

    def test_direct_sql_cannot_change_identity_fields(self, db):
        self._matured_row(db)
        con = sqlite3.connect(db)
        with pytest.raises(sqlite3.IntegrityError, match="LABEL_TAMPER"):
            con.execute(
                "UPDATE label_value_forward SET declared_by = 'someone-else' "
                "WHERE spec_hash = ? AND subject_id = ? AND declared_at = ?",
                (FORWARD_ADDR["spec_hash"], FORWARD_ADDR["subject_id"], FORWARD_ADDR["declared_at"]),
            )
        con.close()

    def test_direct_sql_cannot_rewrite_void_at_once_set(self, db):
        _declare(db)
        vs.observe_forward_label(db, observed_at=NOW, **FORWARD_ADDR)
        vs.void_forward_label(db, void_at=NOW, void_reason="x", **FORWARD_ADDR)
        con = sqlite3.connect(db)
        with pytest.raises(sqlite3.IntegrityError, match="LABEL_TAMPER"):
            con.execute(
                "UPDATE label_value_forward SET void_at = '2099-01-01T00:00:00+00:00' "
                "WHERE spec_hash = ? AND subject_id = ? AND declared_at = ?",
                (FORWARD_ADDR["spec_hash"], FORWARD_ADDR["subject_id"], FORWARD_ADDR["declared_at"]),
            )
        con.close()

    def test_no_datetime_now_anywhere_in_the_maturation_code_path(self):
        # roadmap §9 Verification Strategy, BV row (LR5, re-rated): a
        # structural AST guard against datetime.now() in maturation code,
        # not a convention -- two prior flakes in this repository
        # (CLAUDE.md §18, Increments 2 and 4) stemmed from exactly this
        # pattern elsewhere. Injected-clock discipline is satisfied here
        # by construction: every timestamp (declared_at, observed_at,
        # matured_at, flagged_at, adjudicated_at, void_at) is a caller-
        # supplied string; this module never imports `datetime` at all.
        source = Path(__file__).resolve().parents[1] / "research" / "label_store" / "value_store.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "now":
                pytest.fail(f"found a '.now' attribute access at line {node.lineno} -- "
                            f"maturation code must never call a wall clock internally")
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = {alias.name for alias in node.names}
                if "datetime" in names or (isinstance(node, ast.ImportFrom) and node.module == "datetime"):
                    pytest.fail(f"found a datetime import at line {node.lineno} -- "
                                f"maturation code must never call a wall clock internally")

    def test_void_is_recorded_permanently_end_to_end(self, db):
        # LG4: negative records persist. The row survives, unmodified and
        # unremovable via the module's own API (no delete function exists
        # at all -- confirmed structurally, not just by omission of a test).
        _declare(db)
        vs.observe_forward_label(db, observed_at=NOW, **FORWARD_ADDR)
        vs.void_forward_label(db, void_at=NOW, void_reason="permanent record test", **FORWARD_ADDR)
        assert not hasattr(vs, "delete_forward_label")
        label = vs.get_forward_label(db, **FORWARD_ADDR)
        assert label is not None
        assert label["maturity_state"] == "Void"
        assert label["void_reason"] == "permanent record test"
