"""
Tests for migrate_receipt_attestation_basis.py (engineering roadmap Phase 0,
WBS 0.8) -- the one-time SCHEMA-ONLY migration adding
attestation_basis/contract_version to governance_receipts.

This is schema-only, not a value backfill, because governance_receipts is
unconditionally append-only (ADR-I015 RIM2, enforced by
trg_receipts_no_update/trg_receipts_no_delete) -- no UPDATE against an
existing row is ever permitted, including by a migration. The first draft of
the migration script attempted a classify/apply value backfill (mirroring
migrate_run_kind.py's pattern for the mutable `runs` table) and was correctly
refused by the database trigger; this test suite covers the corrected,
schema-only design.

Covers: missing_columns()/ensure_columns()'s idempotent ALTER, the dry-run/
--commit CLI split, that no UPDATE is ever issued (proven via the append-only
trigger itself, not merely asserted), and -- gated, local-only, skipped in
CI -- a real-ledger-safe run using a tmp_path copy, with the same hash-guard
discipline as test_migration_harness.py.
"""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EVLIB = ROOT / "research" / "evidence_library"
REAL_LEDGER = EVLIB / "ledger.db"

sys.path.insert(0, str(EVLIB))
import migrate_receipt_attestation_basis as mrab  # noqa: E402
import receipt_ledger as rl  # noqa: E402
import receipts as r  # noqa: E402


def make_body(**overrides) -> r.ReceiptBody:
    defaults = dict(
        decision_type="family.transition",
        decision_class="discretionary",
        action="transition",
        subject=r.Subject(kind="family", id="fam_a"),
        from_state="discovery",
        to_state="candidate",
        as_of="2026-07-13T00:00:00+00:00",
        decided_at="2026-07-13T00:00:05+00:00",
        actor=r.Actor(type="human", id="rohith"),
        authority=r.Authority(policy_id="lifecycle.ALLOWED_TRANSITIONS", policy_hash="abc123"),
        verdict=r.Verdict(result="allowed"),
        reason_text="test fixture",
    )
    defaults.update(overrides)
    return r.ReceiptBody(**defaults)


def _pre_existing_table_without_new_columns(tmp_path) -> Path:
    p = tmp_path / "pre_existing.db"
    con = sqlite3.connect(p)
    con.executescript("""
        CREATE TABLE governance_receipts (
            receipt_id TEXT PRIMARY KEY, seq INTEGER NOT NULL UNIQUE,
            schema_version INTEGER NOT NULL, decision_type TEXT NOT NULL,
            decision_class TEXT NOT NULL, action TEXT NOT NULL,
            subject_kind TEXT NOT NULL, subject_id TEXT NOT NULL,
            as_of TEXT NOT NULL, decided_at TEXT NOT NULL,
            actor_type TEXT NOT NULL, actor_id TEXT NOT NULL,
            authority_hash TEXT NOT NULL, body_json TEXT NOT NULL,
            signature_json TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TRIGGER trg_receipts_no_update
        BEFORE UPDATE ON governance_receipts
        BEGIN
            SELECT RAISE(ABORT, 'governance_receipts is append-only: UPDATE forbidden');
        END;
    """)
    con.commit()
    con.close()
    return p


@pytest.fixture
def db_path(tmp_path) -> Path:
    p = tmp_path / "ledger.db"
    rl.init_db(p)
    return p


# ---------------------------------------------------------------------------
# missing_columns() / ensure_columns()
# ---------------------------------------------------------------------------

class TestEnsureColumns:
    def test_missing_columns_on_fresh_db_is_empty(self, db_path):
        # rl.init_db()'s _SCHEMA_SQL already includes both columns for a
        # brand-new table.
        assert mrab.missing_columns(db_path) == []

    def test_missing_columns_on_pre_existing_table(self, tmp_path):
        p = _pre_existing_table_without_new_columns(tmp_path)
        assert mrab.missing_columns(p) == ["attestation_basis", "contract_version"]

    def test_ensure_columns_adds_both_to_a_pre_existing_table(self, tmp_path):
        p = _pre_existing_table_without_new_columns(tmp_path)
        added = mrab.ensure_columns(p)
        assert added == ["attestation_basis", "contract_version"]
        assert mrab.missing_columns(p) == []

    def test_ensure_columns_idempotent_on_a_table_that_already_has_them(self, db_path):
        assert mrab.ensure_columns(db_path) == []  # already present; nothing to add
        assert mrab.ensure_columns(db_path) == []  # second call: still nothing, no raise

    def test_ensure_columns_does_not_disturb_existing_rows(self, tmp_path):
        p = _pre_existing_table_without_new_columns(tmp_path)
        con = sqlite3.connect(p)
        con.execute(
            "INSERT INTO governance_receipts (receipt_id, seq, schema_version, "
            "decision_type, decision_class, action, subject_kind, subject_id, "
            "as_of, decided_at, actor_type, actor_id, authority_hash, body_json) "
            "VALUES ('abc', 1, 1, 'family.transition', 'discretionary', "
            "'transition', 'family', 'fam_a', '2026-01-01T00:00:00+00:00', "
            "'2026-01-01T00:00:00+00:00', 'human', 'rohith', 'hash', '{}')"
        )
        con.commit()
        con.close()

        mrab.ensure_columns(p)

        row = sqlite3.connect(p).execute(
            "SELECT receipt_id, attestation_basis, contract_version FROM governance_receipts"
        ).fetchone()
        assert row == ("abc", None, None)


# ---------------------------------------------------------------------------
# The append-only invariant this migration is bound by
# ---------------------------------------------------------------------------

class TestNoValueBackfillIsAttempted:
    def test_governance_receipts_rejects_a_plain_update(self, db_path):
        # Confirms the constraint this script is designed around still holds
        # -- if this trigger were ever removed, this test (not the migration
        # script) would be the first thing to notice.
        rl.append_receipt(db_path, make_body())
        con = sqlite3.connect(db_path)
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            con.execute("UPDATE governance_receipts SET attestation_basis = 'platform-observed'")

    def test_module_issues_no_update_statement(self):
        # Structural guard, not just a docstring claim: the module's own
        # source contains no "UPDATE governance_receipts" SQL statement
        # anywhere (the substring the prose/docstrings deliberately DO use,
        # e.g. "no UPDATE is ever issued", is "UPDATE " alone -- checking for
        # the table name immediately after distinguishes an actual SQL
        # statement from prose describing the constraint).
        source = Path(mrab.__file__).read_text(encoding="utf-8")
        assert "UPDATE GOVERNANCE_RECEIPTS" not in source.upper()

    def test_existing_receipts_stay_null_after_ensure_columns(self, tmp_path):
        p = _pre_existing_table_without_new_columns(tmp_path)
        con = sqlite3.connect(p)
        con.execute(
            "INSERT INTO governance_receipts (receipt_id, seq, schema_version, "
            "decision_type, decision_class, action, subject_kind, subject_id, "
            "as_of, decided_at, actor_type, actor_id, authority_hash, body_json) "
            "VALUES ('bootstrap-era', 1, 1, 'family.transition', 'discretionary', "
            "'transition', 'family', 'fam_a', '2026-01-01T00:00:00+00:00', "
            "'2026-01-01T00:00:00+00:00', 'human', 'rohith', 'hash', "
            "'{\"attestation\": \"bootstrap\"}')"
        )
        con.commit()
        con.close()

        mrab.ensure_columns(p)

        row = sqlite3.connect(p).execute(
            "SELECT attestation_basis FROM governance_receipts WHERE receipt_id = 'bootstrap-era'"
        ).fetchone()
        # Correct and permanent: even a receipt whose body_json records
        # attestation='bootstrap' gets NO retroactive attestation_basis.
        assert row == (None,)


# ---------------------------------------------------------------------------
# CLI (main())
# ---------------------------------------------------------------------------

class TestMainCLI:
    def test_dry_run_on_fresh_db_reports_nothing_to_do(self, db_path, capsys):
        rc = mrab.main(["--db", str(db_path)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "already present" in out
        assert "Dry run only" in out

    def test_dry_run_on_pre_existing_table_writes_nothing(self, tmp_path, capsys):
        p = _pre_existing_table_without_new_columns(tmp_path)
        rc = mrab.main(["--db", str(p)])
        assert rc == 0
        assert mrab.missing_columns(p) == ["attestation_basis", "contract_version"]  # unchanged
        out = capsys.readouterr().out
        assert "attestation_basis" in out
        assert "Dry run only" in out

    def test_commit_on_fresh_db_is_a_no_op_with_no_backup(self, db_path):
        rc = mrab.main(["--db", str(db_path), "--commit"])
        assert rc == 0
        assert list(db_path.parent.glob("*.pre-attestation-basis-migration.*.db")) == []

    def test_commit_on_pre_existing_table_adds_columns_and_backs_up(self, tmp_path):
        p = _pre_existing_table_without_new_columns(tmp_path)
        rc = mrab.main(["--db", str(p), "--commit"])
        assert rc == 0
        assert mrab.missing_columns(p) == []

        backups = list(p.parent.glob("pre_existing.pre-attestation-basis-migration.*.db"))
        assert len(backups) == 1
        notes = list(p.parent.glob("*.audit.txt"))
        assert len(notes) == 1
        assert "SCHEMA-ONLY" in notes[0].read_text(encoding="utf-8")

    def test_second_commit_is_idempotent_and_backs_up_nothing_new(self, tmp_path):
        p = _pre_existing_table_without_new_columns(tmp_path)
        mrab.main(["--db", str(p), "--commit"])
        first_backup_count = len(list(p.parent.glob("*.pre-attestation-basis-migration.*.db")))

        rc = mrab.main(["--db", str(p), "--commit"])
        assert rc == 0
        second_backup_count = len(list(p.parent.glob("*.pre-attestation-basis-migration.*.db")))
        assert second_backup_count == first_backup_count  # nothing missing -> no new backup

    def test_missing_db_is_an_error(self, tmp_path):
        rc = mrab.main(["--db", str(tmp_path / "does_not_exist.db")])
        assert rc == 1


# ---------------------------------------------------------------------------
# Real-ledger-safe run -- local-development only, mirrors
# test_migration_harness.py's hash-guard discipline exactly.
# ---------------------------------------------------------------------------

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _assert_not_real_ledger(path: Path) -> None:
    assert Path(path).resolve() != REAL_LEDGER.resolve(), (
        f"REFUSING: {path} resolves to the real ledger -- this test must "
        f"never operate on it directly."
    )


@pytest.fixture(scope="module", autouse=True)
def _guard_real_ledger_untouched():
    if REAL_LEDGER.exists():
        before = _sha256(REAL_LEDGER)
    yield
    if REAL_LEDGER.exists():
        after = _sha256(REAL_LEDGER)
        assert before == after, (
            "the real local ledger.db changed during this test module's run -- "
            "tests must operate ONLY on tmp_path copies"
        )


@pytest.mark.skipif(not REAL_LEDGER.exists(), reason="real ledger not present (gitignored; absent in CI)")
class TestRealLedgerCopy:
    def test_real_ledger_already_has_both_columns(self):
        # Documented fact, not a defect: an earlier verification-path call
        # during this milestone's own development (governance_verification.
        # verify_governance() -> evidence_governance.init_db() cascade, before
        # this script's design was corrected) already added these two nullable
        # columns to the real local ledger. Confirmed harmless at the time
        # (zero row-count change, zero value mutation, chain still verifies)
        # and recorded in this milestone's verification report. This test
        # documents the real ledger's actual current state rather than
        # asserting a false "still pre-migration" premise.
        assert mrab.missing_columns(REAL_LEDGER) == []

    def test_dry_run_against_a_copy_of_the_real_ledger_is_clean(self, tmp_path, capsys):
        copy = tmp_path / "ledger.db"
        shutil.copy2(REAL_LEDGER, copy)
        _assert_not_real_ledger(copy)

        before_hash = _sha256(copy)
        rc = mrab.main(["--db", str(copy)])
        assert rc == 0
        assert _sha256(copy) == before_hash  # dry run touches nothing

    def test_no_real_receipt_has_a_backfilled_attestation_basis(self):
        # The permanent, correct state per this script's design: no historical
        # receipt is ever backfilled.
        #
        # The count is a floor, not an equality. 26 was the chain's length when
        # the bootstrap migrations completed; the ledger is an append-only
        # governance chain and legitimately grows as real decisions are made
        # (the first `dataset.seal` was appended at Phase 4 operational
        # closure). Pinning equality would make every genuine governance action
        # fail this test. `non_null == 0` is the assertion that carries the
        # meaning, and it is unchanged.
        con = sqlite3.connect(REAL_LEDGER)
        try:
            total = con.execute("SELECT COUNT(*) FROM governance_receipts").fetchone()[0]
            non_null = con.execute(
                "SELECT COUNT(*) FROM governance_receipts WHERE attestation_basis IS NOT NULL"
            ).fetchone()[0]
        finally:
            con.close()
        assert total >= 26
        assert non_null == 0

    def test_chain_still_verifies_on_the_real_ledger(self):
        result = rl.verify_chain(REAL_LEDGER)
        assert result.ok
        assert result.n_receipts >= 26
