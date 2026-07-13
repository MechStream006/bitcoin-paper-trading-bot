"""
Tests for Contract Governance Receipt Emission (PR-4 of
docs/governance/governance_receipts.md + contract_versioning.md).

Covers: activation receipt emission, retirement receipt emission, rollback
on receipt failure, replay verification, duplicate receipt prevention,
receipt reference integrity, receipt lifecycle status, corrupted receipt
detection, and transaction atomicity.

Scope discipline: nothing here emits, or asserts the existence of, any
receipt decision_type other than contract.activate / contract.retire.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research" / "evidence_library"))

import contract_lifecycle as lc
import contract_receipts as cre
import contract_registry as cr
import receipt_ledger as rl
import receipts as r


@pytest.fixture
def db_path(tmp_path) -> Path:
    p = tmp_path / "contract_receipts_test.db"
    cre.init_db(p)
    return p


def activate(db_path, contract_id="corpus_daily", body="body v1", actor="rohith", reason="reason", **kw):
    return cre.activate_contract_with_receipt(db_path, contract_id, body, actor, reason, **kw)


def retire(db_path, contract_hash, actor="rohith", reason="discontinued"):
    return cre.retire_contract_with_receipt(db_path, contract_hash, actor, reason)


# ---------------------------------------------------------------------------
# init_db (both schemas, one file)
# ---------------------------------------------------------------------------

class TestInitDb:
    def test_creates_both_tables(self, db_path):
        con = sqlite3.connect(db_path)
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        con.close()
        assert "contract_registry" in tables
        assert "governance_receipts" in tables

    def test_idempotent(self, db_path):
        cre.init_db(db_path)  # must not raise


# ---------------------------------------------------------------------------
# Activation receipt emission (items 1, 3)
# ---------------------------------------------------------------------------

class TestActivationReceiptEmission:
    def test_returns_contract_and_receipt(self, db_path):
        result = activate(db_path)
        assert "contract" in result
        assert "receipt" in result

    def test_receipt_decision_type_is_contract_activate(self, db_path):
        result = activate(db_path)
        assert result["receipt"]["decision_type"] == "contract.activate"
        assert result["receipt"]["decision_class"] == "discretionary"
        assert result["receipt"]["action"] == "activate"

    def test_receipt_subject_is_the_contract_hash(self, db_path):
        result = activate(db_path)
        assert result["receipt"]["subject_id"] == result["contract"]["contract_hash"]
        assert result["receipt"]["subject_kind"] == "contract"

    def test_receipt_reason_text_matches_caller_reason(self, db_path):
        result = activate(db_path, reason="specific activation reason")
        assert result["receipt"]["body"]["reason_text"] == "specific activation reason"

    def test_contract_is_active(self, db_path):
        result = activate(db_path)
        assert result["contract"]["phase"] == "active"

    def test_second_activation_receipt_references_predecessor(self, db_path):
        first = activate(db_path, body="v1")
        second = activate(db_path, body="v2")
        inputs = second["receipt"]["body"]["inputs"]
        assert len(inputs) == 1
        assert inputs[0]["hash"] == first["contract"]["contract_hash"]

    def test_first_activation_receipt_has_no_inputs(self, db_path):
        result = activate(db_path)
        assert result["receipt"]["body"]["inputs"] == []

    def test_activation_validation_failure_emits_no_receipt(self, db_path):
        with pytest.raises(cr.ContractValidationError):
            activate(db_path, actor="")
        assert rl.get_all_receipts(db_path) == []
        assert cr.get_by_contract_id(db_path, "corpus_daily") == []


# ---------------------------------------------------------------------------
# Retirement receipt emission (items 2, 4)
# ---------------------------------------------------------------------------

class TestRetirementReceiptEmission:
    def test_returns_contract_and_receipt(self, db_path):
        activated = activate(db_path)
        result = retire(db_path, activated["contract"]["contract_hash"])
        assert result["contract"]["phase"] == "retired"
        assert result["receipt"]["decision_type"] == "contract.retire"

    def test_retirement_receipt_from_state_is_prior_phase(self, db_path):
        v1 = activate(db_path, body="v1")
        activate(db_path, body="v2")  # supersedes v1
        result = retire(db_path, v1["contract"]["contract_hash"])
        assert result["receipt"]["body"]["from_state"] == "superseded"
        assert result["receipt"]["body"]["to_state"] == "retired"

    def test_retiring_active_contract_from_state_is_active(self, db_path):
        activated = activate(db_path)
        result = retire(db_path, activated["contract"]["contract_hash"])
        assert result["receipt"]["body"]["from_state"] == "active"

    def test_retirement_reason_text_matches_caller_reason(self, db_path):
        activated = activate(db_path)
        result = retire(db_path, activated["contract"]["contract_hash"], reason="feature discontinued")
        assert result["receipt"]["body"]["reason_text"] == "feature discontinued"

    def test_retiring_draft_raises_and_emits_no_receipt(self, db_path):
        draft = cr.append_contract(
            db_path,
            cr.ContractRecord(
                contract_id="corpus_daily", version=1, contract_schema_version=1,
                body="draft body", phase="draft", actor="rohith", reason="drafting",
            ),
        )
        with pytest.raises(lc.InvalidTransitionError):
            retire(db_path, draft["contract_hash"])
        assert rl.get_all_receipts(db_path) == []

    def test_retiring_unknown_hash_raises_and_emits_no_receipt(self, db_path):
        with pytest.raises(cr.ContractNotFoundError):
            retire(db_path, "f" * 64)
        assert rl.get_all_receipts(db_path) == []


# ---------------------------------------------------------------------------
# Populate activated_receipt_id / retired_receipt_id (items 3, 4)
# ---------------------------------------------------------------------------

class TestReceiptReferenceIntegrity:
    def test_activated_receipt_id_equals_receipt_id(self, db_path):
        result = activate(db_path)
        assert result["contract"]["activated_receipt_id"] == result["receipt"]["receipt_id"]

    def test_retired_receipt_id_equals_receipt_id(self, db_path):
        activated = activate(db_path)
        result = retire(db_path, activated["contract"]["contract_hash"])
        assert result["contract"]["retired_receipt_id"] == result["receipt"]["receipt_id"]

    def test_activated_receipt_id_resolves_in_ledger(self, db_path):
        result = activate(db_path)
        fetched = rl.get_receipt(db_path, result["contract"]["activated_receipt_id"])
        assert fetched is not None
        assert fetched["receipt_id"] == result["receipt"]["receipt_id"]

    def test_retired_receipt_id_resolves_in_ledger(self, db_path):
        activated = activate(db_path)
        result = retire(db_path, activated["contract"]["contract_hash"])
        fetched = rl.get_receipt(db_path, result["contract"]["retired_receipt_id"])
        assert fetched is not None

    def test_activated_receipt_id_immutable_after_write(self, db_path):
        result = activate(db_path)
        con = sqlite3.connect(db_path)
        try:
            with pytest.raises(sqlite3.IntegrityError, match="CONTRACT_TAMPER"):
                con.execute(
                    "UPDATE contract_registry SET activated_receipt_id = ? WHERE contract_hash = ?",
                    ("f" * 64, result["contract"]["contract_hash"]),
                )
        finally:
            con.close()


# ---------------------------------------------------------------------------
# Receipt lifecycle status (item 8)
# ---------------------------------------------------------------------------

class TestReceiptLifecycleStatus:
    def test_governed_activation_yields_recorded(self, db_path):
        result = activate(db_path)
        assert result["contract"]["activation_receipt_status"] == "RECORDED"

    def test_governed_retirement_yields_recorded(self, db_path):
        activated = activate(db_path)
        result = retire(db_path, activated["contract"]["contract_hash"])
        assert result["contract"]["retirement_receipt_status"] == "RECORDED"

    def test_bare_activation_yields_pending_receipt_not_null(self, db_path):
        # contract_lifecycle.activate_contract() (PR-3, receipt-less, kept
        # for backward compatibility) must not leave bare-NULL ambiguity
        # either -- it writes PENDING_RECEIPT, never NULL.
        row = lc.activate_contract(db_path, "corpus_daily", "bare body", "rohith", "bare activation")
        assert row["activation_receipt_status"] == "PENDING_RECEIPT"
        assert row["activated_receipt_id"] is None

    def test_bare_retirement_yields_pending_receipt(self, db_path):
        row = lc.activate_contract(db_path, "corpus_daily", "bare body", "rohith", "bare activation")
        retired = lc.retire_contract(db_path, row["contract_hash"], "rohith", "bare retirement")
        assert retired["retirement_receipt_status"] == "PENDING_RECEIPT"
        assert retired["retired_receipt_id"] is None

    def test_legacy_import_migration_backfill(self, tmp_path):
        # Simulate a pre-PR-4 database: build the contract_registry table
        # with the PR-3-era shape (no retired_receipt_id, no *_receipt_status
        # columns) and a row already in it, then run the current init_db()
        # and confirm the migration stamps LEGACY_IMPORT rather than
        # leaving activation_receipt_status NULL.
        legacy_db = tmp_path / "legacy.db"
        con = sqlite3.connect(legacy_db)
        con.executescript("""
            CREATE TABLE contract_registry (
                contract_hash TEXT PRIMARY KEY,
                contract_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                contract_schema_v INTEGER NOT NULL,
                body TEXT NOT NULL,
                phase TEXT NOT NULL,
                change_class TEXT,
                impact_report_hash TEXT,
                superseded_by TEXT,
                actor TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL,
                activated_at TEXT,
                retired_at TEXT,
                activated_receipt_id TEXT
            );
        """)
        con.execute(
            "INSERT INTO contract_registry (contract_hash, contract_id, version, "
            "contract_schema_v, body, phase, actor, reason, created_at, activated_at) "
            "VALUES ('deadbeef', 'legacy_contract', 1, 1, 'legacy body', 'active', "
            "'rohith', 'pre-PR-4 activation', '2026-07-13T00:00:00+00:00', '2026-07-13T00:00:00+00:00')"
        )
        con.commit()
        con.close()

        cr.init_db(legacy_db)  # runs the migration

        row = cr.get_by_hash(legacy_db, "deadbeef")
        assert row["activation_receipt_status"] == "LEGACY_IMPORT"
        assert row["activated_receipt_id"] is None

    def test_receipt_statuses_enum_matches_task_vocabulary(self):
        assert cr.RECEIPT_STATUSES == {"PENDING_RECEIPT", "RECORDED", "LEGACY_IMPORT", "BOOTSTRAP"}


# ---------------------------------------------------------------------------
# Rollback on receipt failure / transaction atomicity (item 7)
# ---------------------------------------------------------------------------

class TestRollbackAndAtomicity:
    def test_receipt_persistence_failure_rolls_back_the_contract_write(self, db_path, monkeypatch):
        def boom(con, body):
            raise RuntimeError("simulated receipt persistence failure")

        monkeypatch.setattr(cre.rl, "append_receipt_on_connection", boom)

        with pytest.raises(cre.ReceiptEmissionError):
            activate(db_path)

        assert cr.get_by_contract_id(db_path, "corpus_daily") == []
        assert rl.get_all_receipts(db_path) == []

    def test_receipt_failure_on_second_activation_leaves_first_active(self, db_path, monkeypatch):
        first = activate(db_path, body="v1")

        def boom(con, body):
            raise RuntimeError("simulated receipt persistence failure")

        monkeypatch.setattr(cre.rl, "append_receipt_on_connection", boom)
        with pytest.raises(cre.ReceiptEmissionError):
            activate(db_path, body="v2")

        # the supersede UPDATE issued mid-transaction must have rolled back
        reloaded = cr.get_by_hash(db_path, first["contract"]["contract_hash"])
        assert reloaded["phase"] == "active"
        assert reloaded["superseded_by"] is None
        rows = cr.get_by_contract_id(db_path, "corpus_daily")
        assert len(rows) == 1  # the failed second attempt left no row

    def test_retirement_receipt_failure_rolls_back_retirement(self, db_path, monkeypatch):
        activated = activate(db_path)

        def boom(con, body):
            raise RuntimeError("simulated receipt persistence failure")

        monkeypatch.setattr(cre.rl, "append_receipt_on_connection", boom)
        with pytest.raises(cre.ReceiptEmissionError):
            retire(db_path, activated["contract"]["contract_hash"])

        reloaded = cr.get_by_hash(db_path, activated["contract"]["contract_hash"])
        assert reloaded["phase"] == "active"
        assert reloaded["retired_at"] is None
        assert reloaded["retirement_receipt_status"] is None

    def test_reference_storage_failure_rolls_back_everything(self, db_path, monkeypatch):
        # Fail the LAST step ("store receipt reference") specifically --
        # by this point the contract row AND the receipt row have both
        # already been written (uncommitted) in this transaction.
        def boom(con, contract_hash, receipt_id):
            raise RuntimeError("simulated failure storing receipt reference")

        monkeypatch.setattr(cre, "_store_activation_receipt_reference", boom)
        with pytest.raises(RuntimeError):
            activate(db_path)

        assert cr.get_by_contract_id(db_path, "corpus_daily") == []
        assert rl.get_all_receipts(db_path) == []

    def test_retirement_reference_storage_failure_rolls_back_everything(self, db_path, monkeypatch):
        activated = activate(db_path)

        def boom(con, contract_hash, receipt_id):
            raise RuntimeError("simulated failure storing receipt reference")

        monkeypatch.setattr(cre, "_store_retirement_receipt_reference", boom)
        with pytest.raises(RuntimeError):
            retire(db_path, activated["contract"]["contract_hash"])

        reloaded = cr.get_by_hash(db_path, activated["contract"]["contract_hash"])
        assert reloaded["phase"] == "active"
        assert reloaded["retired_at"] is None
        # only the one activation receipt exists -- the retirement receipt
        # that was persisted mid-transaction must have rolled back too
        assert len(rl.get_all_receipts(db_path)) == 1

    def test_successful_activation_leaves_no_pending_receipt_status_at_rest(self, db_path):
        result = activate(db_path)
        assert result["contract"]["activation_receipt_status"] != "PENDING_RECEIPT"


# ---------------------------------------------------------------------------
# Duplicate receipt prevention
# ---------------------------------------------------------------------------

class TestDuplicateReceiptPrevention:
    def test_duplicate_activation_rejected_and_no_extra_receipt_written(self, db_path):
        activate(db_path, body="same content")
        with pytest.raises(cr.DuplicateContractError):
            activate(db_path, body="same content")
        assert len(rl.get_all_receipts(db_path)) == 1

    def test_direct_duplicate_receipt_id_rejected_at_persistence_layer(self, db_path):
        # A receipt is content-addressed: two DIFFERENT activations chain
        # onto different prev_receipt_hash values (append_receipt_on_connection
        # always recomputes prev_receipt_hash from the CURRENT chain head)
        # and therefore never naturally collide through the normal append
        # path -- see test_duplicate_activation_rejected_and_no_extra_receipt_written
        # for the realistic duplicate-prevention path, where the CONTRACT
        # collision is what actually happens in practice. What must still
        # hold, tested directly here by bypassing the chaining logic, is
        # that a byte-identical receipt_id is rejected by the PRIMARY KEY
        # constraint at the persistence layer -- PR-1's own guarantee.
        result = activate(db_path)
        stored = rl.get_receipt(db_path, result["receipt"]["receipt_id"])
        con = sqlite3.connect(db_path)
        try:
            with pytest.raises(sqlite3.IntegrityError):
                con.execute(
                    "INSERT INTO governance_receipts ("
                    "receipt_id, seq, prev_receipt_hash, schema_version, decision_type, "
                    "decision_class, action, subject_kind, subject_id, subject_version, "
                    "from_state, to_state, as_of, decided_at, actor_type, actor_id, "
                    "authority_hash, rule_hash, engine_version, body_json, signature_json"
                    ") VALUES (?,99,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        stored["receipt_id"], stored["prev_receipt_hash"], stored["schema_version"],
                        stored["decision_type"], stored["decision_class"], stored["action"],
                        stored["subject_kind"], stored["subject_id"], stored["subject_version"],
                        stored["from_state"], stored["to_state"], stored["as_of"], stored["decided_at"],
                        stored["actor_type"], stored["actor_id"], stored["authority_hash"],
                        stored["rule_hash"], stored["engine_version"], stored["body_json"], "[]",
                    ),
                )
        finally:
            con.close()


# ---------------------------------------------------------------------------
# Receipt replay verification (item 9)
# ---------------------------------------------------------------------------

class TestReplayVerification:
    def test_verify_activation_receipt_ok(self, db_path):
        result = activate(db_path)
        verification = cre.verify_activation_receipt(db_path, result["contract"]["contract_hash"])
        assert verification.hash_ok is True
        assert verification.replay.ok is True
        assert verification.replay.mode == "envelope-verified"

    def test_verify_retirement_receipt_ok(self, db_path):
        activated = activate(db_path)
        retire(db_path, activated["contract"]["contract_hash"])
        verification = cre.verify_retirement_receipt(db_path, activated["contract"]["contract_hash"])
        assert verification.hash_ok is True
        assert verification.replay.ok is True

    def test_verify_activation_receipt_uses_pr1_replay_framework(self, db_path):
        # Structural check: verify_activation_receipt must call into
        # receipts.replay / receipts.verify_receipt, not reimplement them.
        result = activate(db_path)
        direct_verify = r.verify_receipt(result["receipt"]["body"], result["receipt"]["receipt_id"])
        direct_replay = r.replay(result["receipt"]["body"])
        verification = cre.verify_activation_receipt(db_path, result["contract"]["contract_hash"])
        assert verification.hash_ok == direct_verify
        assert verification.replay == direct_replay

    def test_verify_activation_receipt_no_receipt_raises(self, db_path):
        row = lc.activate_contract(db_path, "corpus_daily", "bare body", "rohith", "bare activation")
        with pytest.raises(cre.ReceiptEmissionError):
            cre.verify_activation_receipt(db_path, row["contract_hash"])

    def test_verify_retirement_receipt_no_receipt_raises(self, db_path):
        row = lc.activate_contract(db_path, "corpus_daily", "bare body", "rohith", "bare activation")
        retired = lc.retire_contract(db_path, row["contract_hash"], "rohith", "bare retirement")
        with pytest.raises(cre.ReceiptEmissionError):
            cre.verify_retirement_receipt(db_path, retired["contract_hash"])

    def test_verify_unknown_contract_hash_raises(self, db_path):
        with pytest.raises(cr.ContractNotFoundError):
            cre.verify_activation_receipt(db_path, "0" * 64)


# ---------------------------------------------------------------------------
# Corrupted receipt detection
# ---------------------------------------------------------------------------

class TestCorruptedReceiptDetection:
    def test_tampered_receipt_body_fails_hash_verification(self, db_path):
        result = activate(db_path)
        con = sqlite3.connect(db_path)
        try:
            con.execute("DROP TRIGGER trg_receipts_no_update")
            con.execute(
                "UPDATE governance_receipts SET body_json = "
                "REPLACE(body_json, '\"activated\"', '\"DENIED\"') WHERE receipt_id = ?",
                (result["receipt"]["receipt_id"],),
            )
            con.commit()
        finally:
            con.close()

        verification = cre.verify_activation_receipt(db_path, result["contract"]["contract_hash"])
        assert verification.hash_ok is False

    def test_corrupted_reference_to_nonexistent_receipt_raises(self, db_path):
        # Construct, via the raw archive primitive, a contract row claiming
        # RECORDED status with a receipt_id that was never actually written
        # to governance_receipts -- simulating a corrupted cross-table
        # reference (e.g. a bug, or a receipts DB restored from an older
        # backup than the contracts DB).
        row = cr.append_contract(
            db_path,
            cr.ContractRecord(
                contract_id="corrupted_ref_test", version=1, contract_schema_version=1,
                body="body", phase="active", actor="rohith", reason="reason",
                activation_receipt_status="RECORDED",
                activated_receipt_id="f" * 64,
            ),
        )
        with pytest.raises(cre.ReceiptEmissionError, match="CORRUPTED REFERENCE"):
            cre.verify_activation_receipt(db_path, row["contract_hash"])

    def test_verify_chain_still_detects_tamper_through_this_path(self, db_path):
        activate(db_path)
        result2 = activate(db_path, body="v2")
        con = sqlite3.connect(db_path)
        try:
            con.execute("DROP TRIGGER trg_receipts_no_update")
            con.execute(
                "UPDATE governance_receipts SET body_json = "
                "REPLACE(body_json, '\"activated\"', '\"DENIED\"') WHERE receipt_id = ?",
                (result2["receipt"]["receipt_id"],),
            )
            con.commit()
        finally:
            con.close()
        chain_result = rl.verify_chain(db_path)
        assert chain_result.ok is False
        assert any("TAMPERING" in e for e in chain_result.errors)


# ---------------------------------------------------------------------------
# Dependency-check / scope discipline
# ---------------------------------------------------------------------------

class TestScopeDiscipline:
    def test_no_receipt_types_beyond_activate_and_retire(self, db_path):
        activated = activate(db_path)
        retire(db_path, activated["contract"]["contract_hash"])
        activate(db_path, contract_id="funding_daily", body="other contract")
        decision_types = {row["decision_type"] for row in rl.get_all_receipts(db_path)}
        assert decision_types <= {"contract.activate", "contract.retire"}

    def test_module_does_not_import_evidence_or_readiness_concepts(self):
        src = Path(cre.__file__).read_text(encoding="utf-8")
        for forbidden in ("evidence_run", "readiness.verdict", "data_completeness", "feature_store"):
            assert forbidden not in src
