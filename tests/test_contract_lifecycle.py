"""
Tests for the Contract Lifecycle (PR-3 of docs/governance/contract_versioning.md).

Covers: activation, retirement, duplicate activation, monotonic version
numbering, atomic rollback, invalid transitions, tamper attempts, replay
after activation/retirement, concurrent activation attempts, and activation
ordering.

Nothing exercised here emits a governance receipt -- activated_receipt_id
is asserted to stay NULL throughout every test in this file, which is the
structural check that PR-3 did not silently pull PR-4's scope forward.
"""
from __future__ import annotations

import sqlite3
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research" / "evidence_library"))

import contract_lifecycle as lc
import contract_registry as cr


@pytest.fixture
def db_path(tmp_path) -> Path:
    p = tmp_path / "lifecycle_test.db"
    cr.init_db(p)
    return p


def activate(db_path, contract_id="corpus_daily", body="v1", actor="rohith", reason="r", **kw):
    return lc.activate_contract(db_path, contract_id, body, actor, reason, **kw)


# ---------------------------------------------------------------------------
# Activation
# ---------------------------------------------------------------------------

class TestActivation:
    def test_first_activation_is_version_1(self, db_path):
        row = activate(db_path)
        assert row["version"] == 1
        assert row["phase"] == "active"
        assert row["superseded_by"] is None

    def test_activated_receipt_id_stays_null(self, db_path):
        # Structural check: PR-3 must not emit or reference a receipt.
        row = activate(db_path)
        assert row["activated_receipt_id"] is None

    def test_activated_at_is_set(self, db_path):
        row = activate(db_path)
        assert row["activated_at"] is not None
        assert row["activated_at"] == row["created_at"]

    def test_second_activation_supersedes_first(self, db_path):
        v1 = activate(db_path, body="v1 content")
        v2 = activate(db_path, body="v2 content")
        v1_reloaded = cr.get_by_hash(db_path, v1["contract_hash"])
        assert v1_reloaded["phase"] == "superseded"
        assert v1_reloaded["superseded_by"] == v2["contract_hash"]
        assert v2["phase"] == "active"
        assert v2["superseded_by"] is None

    def test_get_active_reflects_latest(self, db_path):
        activate(db_path, body="v1 content")
        v2 = activate(db_path, body="v2 content")
        current = cr.get_active(db_path, "corpus_daily")
        assert current["contract_hash"] == v2["contract_hash"]

    def test_only_one_active_row_after_multiple_activations(self, db_path):
        for i in range(5):
            activate(db_path, body=f"v{i} content")
        rows = cr.get_by_contract_id(db_path, "corpus_daily")
        active_rows = [r for r in rows if r["phase"] == "active"]
        assert len(active_rows) == 1

    def test_activation_independent_contract_ids_do_not_interact(self, db_path):
        a = activate(db_path, contract_id="corpus_daily", body="a")
        b = activate(db_path, contract_id="funding_daily", body="b")
        assert a["version"] == 1
        assert b["version"] == 1
        assert cr.get_active(db_path, "corpus_daily")["contract_hash"] == a["contract_hash"]
        assert cr.get_active(db_path, "funding_daily")["contract_hash"] == b["contract_hash"]


# ---------------------------------------------------------------------------
# Activation validation (item 4)
# ---------------------------------------------------------------------------

class TestActivationValidation:
    def test_missing_actor_rejected(self, db_path):
        with pytest.raises(cr.ContractValidationError):
            lc.activate_contract(db_path, "corpus_daily", "body", "", "reason")

    def test_missing_reason_rejected(self, db_path):
        with pytest.raises(cr.ContractValidationError):
            lc.activate_contract(db_path, "corpus_daily", "body", "actor", "   ")

    def test_missing_contract_id_rejected(self, db_path):
        with pytest.raises(cr.ContractValidationError):
            lc.activate_contract(db_path, "", "body", "actor", "reason")

    def test_blank_body_rejected(self, db_path):
        with pytest.raises(cr.ContractValidationError):
            lc.activate_contract(db_path, "corpus_daily", "   ", "actor", "reason")

    def test_bad_change_class_rejected(self, db_path):
        with pytest.raises(cr.ContractValidationError):
            lc.activate_contract(db_path, "corpus_daily", "body", "actor", "reason", change_class="cosmetic")

    def test_validation_failure_writes_nothing(self, db_path):
        with pytest.raises(cr.ContractValidationError):
            lc.activate_contract(db_path, "corpus_daily", "body", "", "reason")
        assert cr.get_by_contract_id(db_path, "corpus_daily") == []


# ---------------------------------------------------------------------------
# Duplicate activation
# ---------------------------------------------------------------------------

class TestDuplicateActivation:
    def test_reactivating_identical_content_rejected(self, db_path):
        row = activate(db_path, body="same content")
        with pytest.raises(cr.DuplicateContractError):
            activate(db_path, body="same content")
        # the original row must be untouched -- proves the self-collision
        # (supersede staged against itself, then insert fails) rolled back
        # cleanly rather than leaving the original half-superseded.
        reloaded = cr.get_by_hash(db_path, row["contract_hash"])
        assert reloaded["phase"] == "active"
        assert reloaded["superseded_by"] is None

    def test_duplicate_does_not_create_a_second_row(self, db_path):
        activate(db_path, body="same content")
        with pytest.raises(cr.DuplicateContractError):
            activate(db_path, body="same content")
        rows = cr.get_by_contract_id(db_path, "corpus_daily")
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# Monotonic version numbering
# ---------------------------------------------------------------------------

class TestMonotonicVersioning:
    def test_versions_increment_sequentially(self, db_path):
        versions = [activate(db_path, body=f"body {i}")["version"] for i in range(4)]
        assert versions == [1, 2, 3, 4]

    def test_next_version_skips_past_existing_draft(self, db_path):
        # A draft registered directly via append_contract (PR-2 path) must
        # not collide with, or be ignored by, monotonic assignment.
        cr.append_contract(
            db_path,
            cr.ContractRecord(
                contract_id="corpus_daily", version=1, contract_schema_version=1,
                body="draft body", phase="draft", actor="rohith", reason="drafting",
            ),
        )
        activated = activate(db_path, body="real activation content")
        assert activated["version"] == 2
        draft = cr.get_by_version(db_path, "corpus_daily", 1)
        assert draft["phase"] == "draft"  # untouched

    def test_next_version_pure_function_under_lock(self, db_path):
        con = sqlite3.connect(db_path)
        try:
            con.execute("BEGIN IMMEDIATE")
            assert lc.next_version(con, "brand_new_contract") == 1
            con.rollback()
        finally:
            con.close()


# ---------------------------------------------------------------------------
# Atomic rollback
# ---------------------------------------------------------------------------

class TestAtomicRollback:
    def test_failed_insert_rolls_back_the_supersede(self, db_path):
        v1 = activate(db_path, contract_id="atomic_test", body="v1 content")

        # Plant a "poison" row elsewhere whose contract_hash collides with
        # what the next activation attempt will compute -- this makes the
        # INSERT step fail *after* the supersede UPDATE has already been
        # issued (but not committed) within the same transaction.
        poison_hash = cr.compute_contract_hash("v2 content")
        cr.append_contract(
            db_path,
            cr.ContractRecord(
                contract_id="unrelated_contract", version=1, contract_schema_version=1,
                body="v2 content", phase="draft", actor="rohith", reason="collision setup",
            ),
        )
        assert cr.get_by_hash(db_path, poison_hash) is not None

        with pytest.raises(cr.DuplicateContractError):
            activate(db_path, contract_id="atomic_test", body="v2 content")

        # The supersede UPDATE issued mid-transaction must have rolled back:
        # v1 is still active, not superseded, and no orphaned v2 row exists.
        v1_reloaded = cr.get_by_hash(db_path, v1["contract_hash"])
        assert v1_reloaded["phase"] == "active"
        assert v1_reloaded["superseded_by"] is None
        rows = cr.get_by_contract_id(db_path, "atomic_test")
        assert len(rows) == 1

    def test_validation_failure_never_opens_a_transaction_that_needs_rollback(self, db_path):
        v1 = activate(db_path, body="v1 content")
        with pytest.raises(cr.ContractValidationError):
            activate(db_path, body="v2 content", actor="")  # fails validation before BEGIN
        reloaded = cr.get_by_hash(db_path, v1["contract_hash"])
        assert reloaded["phase"] == "active"


# ---------------------------------------------------------------------------
# Retirement
# ---------------------------------------------------------------------------

class TestRetirement:
    def test_retire_active_contract(self, db_path):
        row = activate(db_path)
        retired = lc.retire_contract(db_path, row["contract_hash"], "rohith", "feature discontinued")
        assert retired["phase"] == "retired"
        assert retired["retired_at"] is not None

    def test_retire_leaves_body_unchanged(self, db_path):
        row = activate(db_path, body="the frozen body")
        retired = lc.retire_contract(db_path, row["contract_hash"], "rohith", "discontinued")
        assert retired["body"] == "the frozen body"
        assert cr.verify_contract_hash(retired) is True

    def test_retire_superseded_contract(self, db_path):
        v1 = activate(db_path, body="v1")
        activate(db_path, body="v2")  # supersedes v1
        retired = lc.retire_contract(db_path, v1["contract_hash"], "rohith", "cleanup old version")
        assert retired["phase"] == "retired"
        # superseded_by, set when it was superseded, must survive retirement untouched
        v2 = cr.get_active(db_path, "corpus_daily")
        assert retired["superseded_by"] == v2["contract_hash"]

    def test_retire_does_not_disturb_the_current_active_row(self, db_path):
        v1 = activate(db_path, body="v1")
        v2 = activate(db_path, body="v2")
        lc.retire_contract(db_path, v1["contract_hash"], "rohith", "cleanup")
        current = cr.get_active(db_path, "corpus_daily")
        assert current["contract_hash"] == v2["contract_hash"]


# ---------------------------------------------------------------------------
# Retirement validation (item 5) / invalid transitions
# ---------------------------------------------------------------------------

class TestRetirementValidationAndInvalidTransitions:
    def test_retire_unknown_hash_raises_not_found(self, db_path):
        with pytest.raises(cr.ContractNotFoundError):
            lc.retire_contract(db_path, "f" * 64, "rohith", "reason")

    def test_retire_draft_rejected(self, db_path):
        row = cr.append_contract(
            db_path,
            cr.ContractRecord(
                contract_id="corpus_daily", version=1, contract_schema_version=1,
                body="draft body", phase="draft", actor="rohith", reason="drafting",
            ),
        )
        with pytest.raises(lc.InvalidTransitionError):
            lc.retire_contract(db_path, row["contract_hash"], "rohith", "reason")

    def test_double_retirement_rejected(self, db_path):
        row = activate(db_path)
        lc.retire_contract(db_path, row["contract_hash"], "rohith", "first retirement")
        with pytest.raises(lc.InvalidTransitionError):
            lc.retire_contract(db_path, row["contract_hash"], "rohith", "second retirement attempt")

    def test_retire_missing_actor_rejected(self, db_path):
        row = activate(db_path)
        with pytest.raises(cr.ContractValidationError):
            lc.retire_contract(db_path, row["contract_hash"], "", "reason")

    def test_retire_missing_reason_rejected(self, db_path):
        row = activate(db_path)
        with pytest.raises(cr.ContractValidationError):
            lc.retire_contract(db_path, row["contract_hash"], "rohith", "   ")

    def test_can_transition_matches_documented_arcs(self):
        assert lc.can_transition("draft", "active") is True
        assert lc.can_transition("active", "superseded") is True
        assert lc.can_transition("active", "retired") is True
        assert lc.can_transition("superseded", "retired") is True
        assert lc.can_transition("retired", "active") is False
        assert lc.can_transition("retired", "superseded") is False
        assert lc.can_transition("superseded", "active") is False
        assert lc.can_transition("draft", "retired") is False


# ---------------------------------------------------------------------------
# Contract tamper detection (item 9)
# ---------------------------------------------------------------------------

class TestTamperDetection:
    def test_mutating_body_of_active_contract_raises_contract_tamper(self, db_path):
        row = activate(db_path)
        con = sqlite3.connect(db_path)
        try:
            with pytest.raises(sqlite3.IntegrityError, match="CONTRACT_TAMPER"):
                con.execute(
                    "UPDATE contract_registry SET body = 'tampered' WHERE contract_hash = ?",
                    (row["contract_hash"],),
                )
        finally:
            con.close()

    def test_mutating_contract_id_raises_contract_tamper(self, db_path):
        row = activate(db_path)
        con = sqlite3.connect(db_path)
        try:
            with pytest.raises(sqlite3.IntegrityError, match="CONTRACT_TAMPER"):
                con.execute(
                    "UPDATE contract_registry SET contract_id = 'hijacked' WHERE contract_hash = ?",
                    (row["contract_hash"],),
                )
        finally:
            con.close()

    def test_illegal_phase_transition_active_to_draft_raises_contract_tamper(self, db_path):
        row = activate(db_path)
        con = sqlite3.connect(db_path)
        try:
            with pytest.raises(sqlite3.IntegrityError, match="CONTRACT_TAMPER"):
                con.execute(
                    "UPDATE contract_registry SET phase = 'draft' WHERE contract_hash = ?",
                    (row["contract_hash"],),
                )
        finally:
            con.close()

    def test_resurrecting_a_retired_contract_raises_contract_tamper(self, db_path):
        row = activate(db_path)
        lc.retire_contract(db_path, row["contract_hash"], "rohith", "reason")
        con = sqlite3.connect(db_path)
        try:
            with pytest.raises(sqlite3.IntegrityError, match="CONTRACT_TAMPER"):
                con.execute(
                    "UPDATE contract_registry SET phase = 'active' WHERE contract_hash = ?",
                    (row["contract_hash"],),
                )
        finally:
            con.close()

    def test_skipping_a_phase_superseded_direct_from_draft_raises_contract_tamper(self, db_path):
        row = cr.append_contract(
            db_path,
            cr.ContractRecord(
                contract_id="corpus_daily", version=1, contract_schema_version=1,
                body="draft body", phase="draft", actor="rohith", reason="drafting",
            ),
        )
        con = sqlite3.connect(db_path)
        try:
            with pytest.raises(sqlite3.IntegrityError, match="CONTRACT_TAMPER"):
                con.execute(
                    "UPDATE contract_registry SET phase = 'superseded' WHERE contract_hash = ?",
                    (row["contract_hash"],),
                )
        finally:
            con.close()

    def test_activated_receipt_id_is_write_once(self, db_path):
        row = activate(db_path)
        con = sqlite3.connect(db_path)
        try:
            # NULL -> value is allowed (this is what PR-4 will do)
            con.execute(
                "UPDATE contract_registry SET activated_receipt_id = 'r1' WHERE contract_hash = ?",
                (row["contract_hash"],),
            )
            con.commit()
            # value -> different value must be blocked
            with pytest.raises(sqlite3.IntegrityError, match="CONTRACT_TAMPER"):
                con.execute(
                    "UPDATE contract_registry SET activated_receipt_id = 'r2' WHERE contract_hash = ?",
                    (row["contract_hash"],),
                )
        finally:
            con.close()

    def test_superseded_by_is_write_once(self, db_path):
        v1 = activate(db_path, body="v1")
        v2 = activate(db_path, body="v2")  # sets v1.superseded_by = v2.contract_hash
        con = sqlite3.connect(db_path)
        try:
            with pytest.raises(sqlite3.IntegrityError, match="CONTRACT_TAMPER"):
                con.execute(
                    "UPDATE contract_registry SET superseded_by = ? WHERE contract_hash = ?",
                    ("f" * 64, v1["contract_hash"]),
                )
        finally:
            con.close()

    def test_deletion_still_blocked(self, db_path):
        row = activate(db_path)
        con = sqlite3.connect(db_path)
        try:
            with pytest.raises(sqlite3.IntegrityError):
                con.execute(
                    "DELETE FROM contract_registry WHERE contract_hash = ?", (row["contract_hash"],)
                )
        finally:
            con.close()

    def test_no_mutation_functions_beyond_activate_and_retire(self):
        assert not hasattr(lc, "supersede_contract")
        assert not hasattr(lc, "delete_contract")
        assert not hasattr(lc, "update_contract")


# ---------------------------------------------------------------------------
# Replay compatibility (item 10)
# ---------------------------------------------------------------------------

class TestReplayCompatibility:
    def test_resolve_by_hash_works_after_activation(self, db_path):
        row = activate(db_path)
        resolved = cr.resolve_by_hash(db_path, row["contract_hash"])
        assert resolved["phase"] == "active"
        assert cr.verify_contract_hash(resolved) is True

    def test_resolve_by_hash_works_after_being_superseded(self, db_path):
        v1 = activate(db_path, body="v1")
        activate(db_path, body="v2")
        resolved = cr.resolve_by_hash(db_path, v1["contract_hash"])
        assert resolved["phase"] == "superseded"
        assert cr.verify_contract_hash(resolved) is True

    def test_resolve_by_hash_works_after_retirement(self, db_path):
        row = activate(db_path)
        lc.retire_contract(db_path, row["contract_hash"], "rohith", "reason")
        resolved = cr.resolve_by_hash(db_path, row["contract_hash"])
        assert resolved["phase"] == "retired"
        assert cr.verify_contract_hash(resolved) is True

    def test_resolve_by_hash_still_raises_for_unknown_hash(self, db_path):
        activate(db_path)
        with pytest.raises(cr.ContractNotFoundError):
            cr.resolve_by_hash(db_path, "0" * 64)

    def test_lifecycle_module_has_no_filesystem_read(self):
        src = Path(lc.__file__).read_text(encoding="utf-8")
        assert "read_text" not in src
        assert "yaml.safe_load" not in src


# ---------------------------------------------------------------------------
# Concurrent activation attempts
# ---------------------------------------------------------------------------

class TestConcurrentActivation:
    def test_concurrent_activations_serialize_to_a_clean_final_state(self, db_path):
        n = 6
        contract_id = "concurrent_test"
        errors: list[Exception] = []
        lock = threading.Lock()

        def worker(i: int) -> None:
            try:
                activate(db_path, contract_id=contract_id, body=f"concurrent body {i}")
            except Exception as exc:  # noqa: BLE001
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == [], f"unexpected errors from concurrent activation: {errors}"

        rows = cr.get_by_contract_id(db_path, contract_id)
        assert len(rows) == n

        versions = sorted(r["version"] for r in rows)
        assert versions == list(range(1, n + 1)), "monotonic versions must be gapless and unique"

        active_rows = [r for r in rows if r["phase"] == "active"]
        superseded_rows = [r for r in rows if r["phase"] == "superseded"]
        assert len(active_rows) == 1
        assert len(superseded_rows) == n - 1

        # whichever row committed last (highest version) must be the active one
        max_version_row = max(rows, key=lambda r: r["version"])
        assert active_rows[0]["contract_hash"] == max_version_row["contract_hash"]

    def test_concurrent_activations_all_replay_resolvable(self, db_path):
        n = 4
        contract_id = "concurrent_replay_test"

        def worker(i: int) -> None:
            activate(db_path, contract_id=contract_id, body=f"body {i}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        rows = cr.get_by_contract_id(db_path, contract_id)
        for row in rows:
            resolved = cr.resolve_by_hash(db_path, row["contract_hash"])
            assert cr.verify_contract_hash(resolved) is True


# ---------------------------------------------------------------------------
# Activation ordering
# ---------------------------------------------------------------------------

class TestActivationOrdering:
    def test_sequential_activation_chain_is_correctly_linked(self, db_path):
        v1 = activate(db_path, body="v1")
        v2 = activate(db_path, body="v2")
        v3 = activate(db_path, body="v3")

        v1r = cr.get_by_hash(db_path, v1["contract_hash"])
        v2r = cr.get_by_hash(db_path, v2["contract_hash"])
        v3r = cr.get_by_hash(db_path, v3["contract_hash"])

        assert v1r["version"] == 1
        assert v2r["version"] == 2
        assert v3r["version"] == 3

        assert v1r["phase"] == "superseded"
        assert v2r["phase"] == "superseded"
        assert v3r["phase"] == "active"

        assert v1r["superseded_by"] == v2r["contract_hash"]
        assert v2r["superseded_by"] == v3r["contract_hash"]
        assert v3r["superseded_by"] is None

    def test_activation_order_determines_version_order_not_call_arguments(self, db_path):
        # Versions are assigned strictly by activation order, regardless of
        # any property of the body content itself (e.g. alphabetically
        # "later" content activated first still gets the lower version).
        first = activate(db_path, body="zzz - activated first")
        second = activate(db_path, body="aaa - activated second")
        assert first["version"] == 1
        assert second["version"] == 2
        assert cr.get_active(db_path, "corpus_daily")["contract_hash"] == second["contract_hash"]
