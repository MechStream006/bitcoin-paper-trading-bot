"""
Tests for research/label_store/label_receipts.py (ADR-I011 LG1, §9
"registration receipt", IQROS-P8, IQROS-P10; Phase 3 Label Store roadmap
v1.1.0, WBS 3.8a -- the eighth Label Store milestone).

Per WBS 3.8a's own exit criterion: "Receipts emitted, chained, replayable;
verify_chain() clean on the real ledger; a derived-path replay re-executes
and matches."

No pandas gating anywhere in this file: label_receipts.py, receipts.py,
and receipt_ledger.py are all pure stdlib (unlike conditioning.py/
raw_lake.py's real, structural pandas dependencies at WBS 3.6/3.7) -- every
test here runs in both environments identically.
"""
from __future__ import annotations

import ast
import importlib.util
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_LABEL_STORE_DIR = _REPO_ROOT / "research" / "label_store"
_EVIDENCE_LIBRARY_DIR = _REPO_ROOT / "research" / "evidence_library"


def _load(module_file: Path, registered_name: str):
    if registered_name in sys.modules:
        return sys.modules[registered_name]
    spec = importlib.util.spec_from_file_location(registered_name, module_file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[registered_name] = mod
    spec.loader.exec_module(mod)
    return mod


reg = _load(_LABEL_STORE_DIR / "registry.py", "label_store_registry")
vs = _load(_LABEL_STORE_DIR / "value_store.py", "label_store_value_store")
lr = _load(_LABEL_STORE_DIR / "label_receipts.py", "label_store_label_receipts")

sys.path.insert(0, str(_EVIDENCE_LIBRARY_DIR))
import receipts  # noqa: E402
import receipt_ledger as rl  # noqa: E402

NOW = "2026-08-06T00:00:00+00:00"
LATER = "2026-08-13T00:00:00+00:00"
REAL_LABEL_STORE_DB = _LABEL_STORE_DIR / "label_store.db"


@pytest.fixture
def db(tmp_path) -> Path:
    p = tmp_path / "label_store.db"
    lr.init_db(p)
    return p


def _valid_kwargs(db_path, **overrides) -> dict:
    d = reg.register_definition(db_path, "trade_forward_return", "test rationale", "test interpretation", "rohith", NOW)
    defaults = dict(
        definition_name=d.name, definition_version=d.version,
        horizon_days=5, horizon_governance="calendar_span",
        fill_convention="same-day spot close, both sides",
        cost_convention="flat 15bps, charged once at exit",
        direction_convention="long-only",
        maturity_rule="matured once horizon_days have elapsed on the venue calendar",
        validity_condition="valid once the subject's entry is recorded",
        conditioning_propagation="preserving",
        binding_id="test.binding", engine_version="v1",
        actor="rohith", registered_at=NOW,
    )
    defaults.update(overrides)
    return defaults


def _declare_flagged(db, spec_hash, subject_id):
    vs.declare_forward_label(db, spec_hash, subject_id, NOW, declared_by="live-test-declaration")
    vs.observe_forward_label(db, spec_hash, subject_id, NOW, observed_at=NOW)
    vs.mature_forward_label(
        db, spec_hash, subject_id, NOW,
        outcome={"forward_return": 0.05}, input_revision_set=["placeholder"], matured_at=NOW,
    )
    vs.flag_forward_label(db, spec_hash, subject_id, NOW, flagged_at=NOW, flag_reason="test flag")


# ---------------------------------------------------------------------------
# receipts.py additive extension -- purely additive, existing rows untouched
# ---------------------------------------------------------------------------

class TestReceiptsTaxonomyExtension:
    def test_label_is_a_known_subject_kind(self):
        assert "label" in receipts.SUBJECT_KINDS

    def test_register_and_adjudicate_are_known_actions(self):
        assert "register" in receipts.ACTIONS
        assert "adjudicate" in receipts.ACTIONS

    def test_label_register_is_derived(self):
        assert receipts.DECISION_TAXONOMY["label.register"] == "derived"

    def test_label_adjudicate_is_discretionary(self):
        assert receipts.DECISION_TAXONOMY["label.adjudicate"] == "discretionary"

    def test_existing_taxonomy_entries_unchanged(self):
        assert receipts.DECISION_TAXONOMY["evidence.admit"] == "derived"
        assert receipts.DECISION_TAXONOMY["family.promote"] == "discretionary"
        assert receipts.DECISION_TAXONOMY["contract.activate"] == "discretionary"
        assert "evaluate" in receipts.ACTIONS
        assert "feature" in receipts.SUBJECT_KINDS


# ---------------------------------------------------------------------------
# TierARule -- the default registration rule
# ---------------------------------------------------------------------------

class TestTierARule:
    def test_valid_content_passes(self, db):
        kwargs = _valid_kwargs(db)
        content = {f: kwargs[f] for f in lr._TIER_A_CONTENT_FIELDS}
        passed, checks = lr.DEFAULT_REGISTRATION_RULE.evaluate(db, content)
        assert passed is True
        assert all(c["passed"] for c in checks)

    def test_missing_horizon_days_fails_tier_a(self, db):
        kwargs = _valid_kwargs(db, horizon_days=None)
        content = {f: kwargs[f] for f in lr._TIER_A_CONTENT_FIELDS}
        passed, checks = lr.DEFAULT_REGISTRATION_RULE.evaluate(db, content)
        assert passed is False
        tiera = next(c for c in checks if c["check_id"] == "LREG-TIERA")
        assert tiera["passed"] is False

    def test_def_exists_check_skipped_when_tier_a_already_failed(self, db):
        kwargs = _valid_kwargs(db, horizon_days=None)
        content = {f: kwargs[f] for f in lr._TIER_A_CONTENT_FIELDS}
        _passed, checks = lr.DEFAULT_REGISTRATION_RULE.evaluate(db, content)
        defcheck = next(c for c in checks if c["check_id"] == "LREG-DEFEXISTS")
        assert defcheck["passed"] is True
        assert "not evaluated" in defcheck["observed"]

    def test_nonexistent_definition_fails_defexists(self, db):
        kwargs = _valid_kwargs(db, definition_name="never_registered", definition_version=99)
        content = {f: kwargs[f] for f in lr._TIER_A_CONTENT_FIELDS}
        passed, checks = lr.DEFAULT_REGISTRATION_RULE.evaluate(db, content)
        assert passed is False
        defcheck = next(c for c in checks if c["check_id"] == "LREG-DEFEXISTS")
        assert defcheck["passed"] is False


# ---------------------------------------------------------------------------
# register_specification_with_receipt
# ---------------------------------------------------------------------------

class TestRegisterSpecificationWithReceipt:
    def test_success_emits_a_derived_receipt(self, db):
        kwargs = _valid_kwargs(db)
        receipt = lr.register_specification_with_receipt(db, **kwargs)
        assert receipt["decision_type"] == "label.register"
        assert receipt["decision_class"] == "derived"
        assert receipt["body"]["verdict"]["result"] == "registered"

    def test_underlying_specification_is_actually_registered(self, db):
        kwargs = _valid_kwargs(db)
        lr.register_specification_with_receipt(db, **kwargs)
        specs = sqlite3.connect(db).execute("SELECT COUNT(*) FROM label_specifications").fetchone()[0]
        assert specs == 1

    def test_rejection_still_re_raises_specification_rejected(self, db):
        kwargs = _valid_kwargs(db, horizon_days=None)
        # Resolved via lr._load_registry() -- the SAME resolution
        # label_receipts.py's own internals use -- rather than this
        # file's own module-level `reg`, which several pre-existing
        # label_store test files (e.g. test_label_store_registry.py) can
        # invalidate at collection time: they load registry.py without
        # first checking sys.modules, so whichever test file is collected
        # LAST wins and earlier files' eagerly-cached `reg` can point to
        # an orphaned copy with a class identity distinct from what
        # label_receipts.py resolves at runtime. Discovered via a
        # full-suite-only failure (passed in isolation and in every
        # smaller combination tried) -- logged as technical debt in this
        # milestone's verification record, not fixed in the other test
        # files (out of scope: "no unrelated refactoring").
        with pytest.raises(lr._load_registry().SpecificationRejected):
            lr.register_specification_with_receipt(db, **kwargs)

    def test_rejection_still_emits_a_receipt(self, db):
        kwargs = _valid_kwargs(db, horizon_days=None)
        spec_hash = None
        try:
            lr.register_specification_with_receipt(db, **kwargs)
        except lr._load_registry().SpecificationRejected as exc:
            spec_hash = exc.spec_hash
        assert spec_hash is not None
        receipt = lr.get_registration_receipt(db, spec_hash)
        assert receipt is not None
        assert receipt["body"]["verdict"]["result"] == "rejected"

    def test_idempotent_resubmission_returns_the_same_receipt(self, db):
        kwargs = _valid_kwargs(db)
        first = lr.register_specification_with_receipt(db, **kwargs)
        second = lr.register_specification_with_receipt(db, **kwargs)
        assert first["receipt_id"] == second["receipt_id"]

    def test_idempotent_resubmission_does_not_duplicate_the_receipt(self, db):
        kwargs = _valid_kwargs(db)
        lr.register_specification_with_receipt(db, **kwargs)
        lr.register_specification_with_receipt(db, **kwargs)
        spec_hash = reg.register_specification(db, **{k: v for k, v in kwargs.items()})
        rows = rl.get_receipts_by_subject(db, "label", spec_hash)
        assert len([r for r in rows if r["decision_type"] == "label.register"]) == 1

    def test_two_distinct_specifications_get_two_distinct_receipts(self, db):
        r1 = lr.register_specification_with_receipt(db, **_valid_kwargs(db, binding_id="binding.one"))
        r2 = lr.register_specification_with_receipt(db, **_valid_kwargs(db, binding_id="binding.two"))
        assert r1["receipt_id"] != r2["receipt_id"]

    def test_receipts_are_chained(self, db):
        lr.register_specification_with_receipt(db, **_valid_kwargs(db, binding_id="binding.a"))
        lr.register_specification_with_receipt(db, **_valid_kwargs(db, binding_id="binding.b"))
        result = rl.verify_chain(db)
        assert result.ok is True
        assert result.n_receipts == 2


# ---------------------------------------------------------------------------
# adjudicate_forward_label_with_receipt
# ---------------------------------------------------------------------------

class TestAdjudicateForwardLabelWithReceipt:
    def test_success_emits_a_discretionary_receipt(self, db):
        kwargs = _valid_kwargs(db)
        spec_hash = reg.register_specification(db, **kwargs)
        _declare_flagged(db, spec_hash, "sig-1")
        receipt = lr.adjudicate_forward_label_with_receipt(
            db, spec_hash, "sig-1", NOW,
            adjudicated_at=LATER, adjudication_decision="Reviewed: admissible.",
            actor="rohith",
        )
        assert receipt["decision_type"] == "label.adjudicate"
        assert receipt["decision_class"] == "discretionary"
        assert receipt["body"]["reason_text"] == "Reviewed: admissible."

    def test_underlying_label_is_actually_adjudicated(self, db):
        kwargs = _valid_kwargs(db)
        spec_hash = reg.register_specification(db, **kwargs)
        _declare_flagged(db, spec_hash, "sig-1")
        lr.adjudicate_forward_label_with_receipt(
            db, spec_hash, "sig-1", NOW,
            adjudicated_at=LATER, adjudication_decision="Reviewed: admissible.",
            actor="rohith",
        )
        label = vs.get_forward_label(db, spec_hash, "sig-1", NOW)
        assert label["maturity_state"] == "Adjudicated"

    def test_service_actor_is_refused(self, db):
        kwargs = _valid_kwargs(db)
        spec_hash = reg.register_specification(db, **kwargs)
        _declare_flagged(db, spec_hash, "sig-1")
        with pytest.raises(lr.HumanActRequiredError):
            lr.adjudicate_forward_label_with_receipt(
                db, spec_hash, "sig-1", NOW,
                adjudicated_at=LATER, adjudication_decision="auto",
                actor="scheduler", actor_type="service",
            )

    def test_service_actor_refusal_writes_no_receipt_and_no_transition(self, db):
        kwargs = _valid_kwargs(db)
        spec_hash = reg.register_specification(db, **kwargs)
        _declare_flagged(db, spec_hash, "sig-1")
        with pytest.raises(lr.HumanActRequiredError):
            lr.adjudicate_forward_label_with_receipt(
                db, spec_hash, "sig-1", NOW,
                adjudicated_at=LATER, adjudication_decision="auto",
                actor="scheduler", actor_type="service",
            )
        label = vs.get_forward_label(db, spec_hash, "sig-1", NOW)
        assert label["maturity_state"] == "Flagged"
        assert lr.get_adjudication_receipt(db, spec_hash, "sig-1", NOW) is None

    def test_non_flagged_label_is_refused_by_the_storage_layer(self, db):
        kwargs = _valid_kwargs(db)
        spec_hash = reg.register_specification(db, **kwargs)
        vs.declare_forward_label(db, spec_hash, "sig-declared-only", NOW, declared_by="x")
        with pytest.raises(lr._load_value_store().LabelTransitionError):
            lr.adjudicate_forward_label_with_receipt(
                db, spec_hash, "sig-declared-only", NOW,
                adjudicated_at=LATER, adjudication_decision="premature",
                actor="rohith",
            )

    def test_missing_label_raises(self, db):
        kwargs = _valid_kwargs(db)
        spec_hash = reg.register_specification(db, **kwargs)
        with pytest.raises(lr.LabelReceiptError):
            lr.adjudicate_forward_label_with_receipt(
                db, spec_hash, "no-such-subject", NOW,
                adjudicated_at=LATER, adjudication_decision="x",
                actor="rohith",
            )

    def test_receipt_is_chained_after_a_prior_registration_receipt(self, db):
        kwargs = _valid_kwargs(db)
        spec_hash = reg.register_specification(db, **kwargs)  # no receipt for this one -- direct call
        _declare_flagged(db, spec_hash, "sig-1")
        lr.register_specification_with_receipt(db, **_valid_kwargs(db, binding_id="binding.other"))
        lr.adjudicate_forward_label_with_receipt(
            db, spec_hash, "sig-1", NOW,
            adjudicated_at=LATER, adjudication_decision="Reviewed: admissible.",
            actor="rohith",
        )
        result = rl.verify_chain(db)
        assert result.ok is True
        assert result.n_receipts == 2


# ---------------------------------------------------------------------------
# Replay -- "a derived-path replay re-executes and matches" (exit criterion)
# ---------------------------------------------------------------------------

class TestReplayRegistrationReceipt:
    def test_derived_path_replay_matches(self, db):
        kwargs = _valid_kwargs(db)
        lr.register_specification_with_receipt(db, **kwargs)
        spec_hash = reg.register_specification(db, **kwargs)
        result = lr.replay_registration_receipt(db, spec_hash)
        assert result.hash_ok is True
        assert result.replay.ok is True
        assert result.replay.mode == "verified"

    def test_replay_of_a_rejection_also_matches(self, db):
        # horizon_governance (TEXT, non-nullable) with an invalid-but-non-
        # NULL value: Tier A flags it as an error, but the INSERT itself
        # still succeeds -- unlike horizon_days=None (an INTEGER NOT NULL
        # column), which trips a NOT NULL constraint on INSERT, is caught
        # by register_specification()'s own "duplicate content" except
        # branch, and rolls back WITHOUT writing the row its own docstring
        # promises ("a permanent Rejected row IS written") -- a real,
        # narrow, pre-existing gap in WBS 3.1's registry.py discovered
        # while writing this test, logged as technical debt in this
        # milestone's own verification record, not fixed here (out of
        # scope: "no unrelated refactoring" of a prior milestone's code).
        kwargs = _valid_kwargs(db, horizon_governance="not_a_real_governance_value")
        try:
            lr.register_specification_with_receipt(db, **kwargs)
        except lr._load_registry().SpecificationRejected as exc:
            spec_hash = exc.spec_hash
        result = lr.replay_registration_receipt(db, spec_hash)
        assert result.replay.ok is True
        assert result.replay.mode == "verified"

    def test_append_only_trigger_blocks_in_place_tampering(self, db):
        """governance_receipts is append-only at the storage layer
        (receipt_ledger.py, PR-1) -- re-confirmed here in this milestone's
        own real usage path, not just PR-1's original tests."""
        kwargs = _valid_kwargs(db)
        lr.register_specification_with_receipt(db, **kwargs)
        spec_hash = reg.register_specification(db, **kwargs)
        con = sqlite3.connect(db)
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            con.execute(
                "UPDATE governance_receipts SET body_json = 'tampered' WHERE subject_id = ?",
                (spec_hash,),
            )
        con.close()

    def test_a_forged_body_fails_hash_verification(self, db):
        kwargs = _valid_kwargs(db)
        lr.register_specification_with_receipt(db, **kwargs)
        spec_hash = reg.register_specification(db, **kwargs)
        receipt = lr.get_registration_receipt(db, spec_hash)
        forged_body = dict(receipt["body"])
        forged_body["verdict"] = {"result": "tampered", "reason_code": None}
        assert receipts.verify_receipt(forged_body, receipt["receipt_id"]) is False

    def test_unknown_rule_raises_on_replay(self, db):
        """A receipt naming a (rule_id, engine_version) pair with no
        registered implementation must refuse replay, never silently
        substitute the current default -- mirrors
        evidence_governance.py's identical guarantee for
        `evidence.admit`/`.reject`."""
        kwargs = _valid_kwargs(db)
        spec_hash = reg.register_specification(db, **kwargs)  # direct call -- no receipt yet

        content = {f: kwargs[f] for f in lr._TIER_A_CONTENT_FIELDS}
        fake_rule = type("R", (), {"rule_id": "totally.unregistered.rule", "engine_version": "vX"})()
        body = lr._build_registration_body(
            spec_hash, content, True,
            [{"check_id": "x", "observed": "x", "expected": "x", "passed": True}],
            fake_rule, NOW, "rohith", "human",
        )
        rl.append_receipt(db, body)

        with pytest.raises(lr.UnknownRegistrationRuleError):
            lr.replay_registration_receipt(db, spec_hash)


class TestReplayAdjudicationReceipt:
    def test_discretionary_replay_is_envelope_verified(self, db):
        kwargs = _valid_kwargs(db)
        spec_hash = reg.register_specification(db, **kwargs)
        _declare_flagged(db, spec_hash, "sig-1")
        receipt = lr.adjudicate_forward_label_with_receipt(
            db, spec_hash, "sig-1", NOW,
            adjudicated_at=LATER, adjudication_decision="Reviewed: admissible.",
            actor="rohith",
        )
        result = lr.replay_adjudication_receipt(db, receipt["receipt_id"])
        assert result.hash_ok is True
        assert result.replay.ok is True
        assert result.replay.mode == "envelope-verified"

    def test_replay_never_re_derives_the_human_judgment(self):
        """Structural guard: replay_adjudication_receipt() must never pass
        a rule_fn to receipts.replay() -- mirrors
        transition_governance.replay_transition_receipt()'s own discipline."""
        source = (_LABEL_STORE_DIR / "label_receipts.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "replay_adjudication_receipt":
                found = True
                for call in ast.walk(node):
                    if isinstance(call, ast.Call) and any(
                        isinstance(kw, ast.keyword) and kw.arg == "rule_fn" for kw in call.keywords
                    ):
                        pytest.fail("replay_adjudication_receipt() must never pass rule_fn")
        assert found


# ---------------------------------------------------------------------------
# validate_label_governance -- LREC1/LREC2
# ---------------------------------------------------------------------------

class TestValidateLabelGovernance:
    def test_clean_ledger_reports_no_errors(self, db):
        kwargs = _valid_kwargs(db)
        spec_hash = reg.register_specification(db, **kwargs)
        # register a receipt for it AFTER the fact via idempotent resubmission
        lr.register_specification_with_receipt(db, **kwargs)
        errors = lr.validate_label_governance(db)
        assert errors == []

    def test_lrec1_detects_a_specification_with_no_receipt(self, db):
        kwargs = _valid_kwargs(db)
        reg.register_specification(db, **kwargs)  # direct call -- bypasses the receipt wrapper
        errors = lr.validate_label_governance(db)
        assert any(e.startswith("LREC1") for e in errors)

    def test_lrec2_detects_an_adjudicated_label_with_no_receipt(self, db):
        kwargs = _valid_kwargs(db)
        spec_hash = reg.register_specification(db, **kwargs)
        lr.register_specification_with_receipt(db, **kwargs)
        _declare_flagged(db, spec_hash, "sig-1")
        vs.adjudicate_forward_label(  # direct call -- bypasses the receipt wrapper
            db, spec_hash, "sig-1", NOW,
            adjudicated_at=LATER, adjudication_decision="direct, no receipt",
        )
        errors = lr.validate_label_governance(db)
        assert any(e.startswith("LREC2") for e in errors)

    def test_flagged_but_not_adjudicated_label_is_not_flagged_by_lrec2(self, db):
        kwargs = _valid_kwargs(db)
        spec_hash = reg.register_specification(db, **kwargs)
        lr.register_specification_with_receipt(db, **kwargs)
        _declare_flagged(db, spec_hash, "sig-1")
        errors = lr.validate_label_governance(db)
        assert not any(e.startswith("LREC2") for e in errors)


# ---------------------------------------------------------------------------
# Structural guards
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Real database integration -- read-only, against a disposable copy
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not REAL_LABEL_STORE_DB.exists(), reason="real local label_store.db not present (gitignored)")
class TestRealLabelStoreIntegration:
    def test_lrec1_is_clear_for_all_real_specifications_after_the_3_8c_backfill(self, tmp_path):
        """The scope boundary this test originally documented -- WBS
        3.3/3.4's 8 real Specifications predating this receipt module and
        honestly showing up as LREC1 -- was CLOSED by WBS 3.8c
        (`migrate_bootstrap_label_receipts.py`, `LS-GAP-001`). All 8 now
        carry a bootstrap-attested `label.register` receipt, so LREC1 is
        clear. The gap was never silently ignored; it was named, tracked,
        and then closed by an authorised milestone."""
        copy_path = tmp_path / "label_store_copy.db"
        shutil.copy2(REAL_LABEL_STORE_DB, copy_path)
        lr.init_db(copy_path)  # ensure governance_receipts exists on the copy

        con = sqlite3.connect(copy_path)
        real_spec_count = con.execute("SELECT COUNT(*) FROM label_specifications").fetchone()[0]
        con.close()
        assert real_spec_count == 8

        errors = lr.validate_label_governance(copy_path)
        lrec1_errors = [e for e in errors if e.startswith("LREC1")]
        assert lrec1_errors == []

    def test_no_lrec2_against_real_data(self, tmp_path):
        """0 Adjudicated (and 0 Flagged) real forward-tier labels exist,
        independently confirmed at WBS 3.6's own DBV3 -- so LREC2 must be
        silent on the real data, unaffected by the LREC1 gap above."""
        copy_path = tmp_path / "label_store_copy.db"
        shutil.copy2(REAL_LABEL_STORE_DB, copy_path)
        lr.init_db(copy_path)
        errors = lr.validate_label_governance(copy_path)
        assert not any(e.startswith("LREC2") for e in errors)

    def test_real_copy_is_untouched_by_the_read_only_validation_call(self, tmp_path):
        copy_path = tmp_path / "label_store_copy.db"
        shutil.copy2(REAL_LABEL_STORE_DB, copy_path)
        lr.init_db(copy_path)
        before = copy_path.read_bytes()
        lr.validate_label_governance(copy_path)
        after = copy_path.read_bytes()
        assert before == after

        # The REAL file itself, never touched at all.
        import hashlib
        real_hash_before = hashlib.sha256(REAL_LABEL_STORE_DB.read_bytes()).hexdigest()
        lr.validate_label_governance(copy_path)
        real_hash_after = hashlib.sha256(REAL_LABEL_STORE_DB.read_bytes()).hexdigest()
        assert real_hash_before == real_hash_after
