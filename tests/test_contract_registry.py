"""
Tests for the Contract Registry (PR-2 of docs/governance/contract_versioning.md,
scoped to the storage layer per the PR-2 task).

Covers: canonical hashing, duplicate detection, registry loading, registry
queries, malformed contracts, replay lookup by hash, version lookup,
append-only guarantees, and immutability of activated contracts.

Nothing exercised here wires into activation, retirement, receipts, or any
other subsystem -- this PR is the archive only.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research" / "evidence_library"))

import contract_registry as cr


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

SAMPLE_BODY = """\
contract_id: corpus_daily
grain: day
produces: [spot_close, rv_21d, atr14, atr_ratio]
checks:
  - coverage.min_count: 3
"""


def make_record(**overrides) -> cr.ContractRecord:
    defaults = dict(
        contract_id="corpus_daily",
        version=1,
        contract_schema_version=1,
        body=SAMPLE_BODY,
        phase="active",
        actor="rohith",
        reason="initial activation of the corpus_daily contract",
    )
    defaults.update(overrides)
    return cr.ContractRecord(**defaults)


@pytest.fixture
def db_path(tmp_path) -> Path:
    p = tmp_path / "contracts_test.db"
    cr.init_db(p)
    return p


# ---------------------------------------------------------------------------
# Canonical hashing
# ---------------------------------------------------------------------------

class TestCanonicalHashing:
    def test_str_body_hashes_deterministically(self):
        h1 = cr.compute_contract_hash(SAMPLE_BODY)
        h2 = cr.compute_contract_hash(SAMPLE_BODY)
        assert h1 == h2

    def test_hash_is_bare_hex_sha256(self):
        h = cr.compute_contract_hash(SAMPLE_BODY)
        assert len(h) == 64
        int(h, 16)

    def test_different_body_different_hash(self):
        h1 = cr.compute_contract_hash(SAMPLE_BODY)
        h2 = cr.compute_contract_hash(SAMPLE_BODY + "\nextra: true\n")
        assert h1 != h2

    def test_crlf_and_lf_bodies_hash_identically(self):
        # contract_versioning.md §2.2 / §11 PR-A: CRLF<->LF round-trip must
        # NOT change the hash -- Windows-dev / Linux-deploy is not hypothetical.
        lf_body = "contract_id: x\nline2\n"
        crlf_body = "contract_id: x\r\nline2\r\n"
        assert cr.compute_contract_hash(lf_body) == cr.compute_contract_hash(crlf_body)

    def test_dict_body_canonicalizes_before_hashing(self):
        h1 = cr.compute_contract_hash({"a": 1, "b": 2})
        h2 = cr.compute_contract_hash({"b": 2, "a": 1})
        assert h1 == h2  # key order must not matter

    def test_dict_with_raw_float_rejected(self):
        with pytest.raises(Exception):
            cr.compute_contract_hash({"threshold": 0.5})

    def test_hash_reuses_receipts_sha256_lf(self):
        # Prove real code reuse, not a parallel reimplementation.
        import receipts
        assert cr.compute_contract_hash(SAMPLE_BODY) == receipts.sha256_lf(SAMPLE_BODY.encode("utf-8"))

    def test_verify_contract_hash_true_for_untampered(self, db_path):
        row = cr.append_contract(db_path, make_record())
        assert cr.verify_contract_hash(row) is True

    def test_verify_contract_hash_false_for_tampered(self, db_path):
        row = cr.append_contract(db_path, make_record())
        tampered = dict(row)
        tampered["body"] = tampered["body"] + "\ntampered: true\n"
        assert cr.verify_contract_hash(tampered) is False


# ---------------------------------------------------------------------------
# Validation / malformed contracts
# ---------------------------------------------------------------------------

class TestValidation:
    def test_valid_record_has_no_errors(self):
        assert cr.validate_record(make_record()) == []

    def test_missing_contract_id_rejected(self):
        errors = cr.validate_record(make_record(contract_id=""))
        assert any("contract_id is required" in e for e in errors)

    def test_negative_version_rejected(self):
        errors = cr.validate_record(make_record(version=-1))
        assert any("version must be a non-negative int" in e for e in errors)

    def test_bad_schema_version_rejected(self):
        errors = cr.validate_record(make_record(contract_schema_version=0))
        assert any("contract_schema_version must be a positive int" in e for e in errors)

    def test_blank_body_rejected(self):
        errors = cr.validate_record(make_record(body="   "))
        assert any("body is required" in e for e in errors)

    def test_bad_phase_rejected(self):
        errors = cr.validate_record(make_record(phase="published"))
        assert any("phase must be one of" in e for e in errors)

    def test_bad_change_class_rejected(self):
        errors = cr.validate_record(make_record(change_class="cosmetic"))
        assert any("change_class, if set, must be one of" in e for e in errors)

    def test_missing_actor_rejected(self):
        errors = cr.validate_record(make_record(actor=""))
        assert any("actor is required" in e for e in errors)

    def test_missing_reason_rejected(self):
        errors = cr.validate_record(make_record(reason="   "))
        assert any("reason is required" in e for e in errors)

    def test_validate_record_or_raise_raises(self):
        with pytest.raises(cr.ContractValidationError):
            cr.validate_record_or_raise(make_record(phase="bogus"))

    def test_validate_record_or_raise_silent_on_valid(self):
        cr.validate_record_or_raise(make_record())  # must not raise

    def test_append_rejects_malformed_record(self, db_path):
        with pytest.raises(cr.ContractValidationError):
            cr.append_contract(db_path, make_record(phase="bogus"))


# ---------------------------------------------------------------------------
# Registry loading / persistence
# ---------------------------------------------------------------------------

class TestRegistryPersistence:
    def test_init_db_idempotent(self, db_path):
        cr.init_db(db_path)
        cr.init_db(db_path)

    def test_append_contract_computes_hash(self, db_path):
        row = cr.append_contract(db_path, make_record())
        assert row["contract_hash"] == cr.compute_contract_hash(SAMPLE_BODY)

    def test_append_contract_sets_created_at(self, db_path):
        row = cr.append_contract(db_path, make_record())
        assert row["created_at"]

    def test_activation_and_retirement_placeholders_null_by_default(self, db_path):
        row = cr.append_contract(db_path, make_record())
        assert row["activated_at"] is None
        assert row["retired_at"] is None


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------

class TestDuplicateDetection:
    def test_byte_identical_body_rejected(self, db_path):
        cr.append_contract(db_path, make_record())
        with pytest.raises(cr.DuplicateContractError):
            cr.append_contract(
                db_path,
                make_record(actor="someone_else", reason="a second attempt at the exact same bytes"),
            )

    def test_same_contract_id_and_version_twice_rejected(self, db_path):
        cr.append_contract(db_path, make_record(version=1, body=SAMPLE_BODY))
        with pytest.raises(cr.ContractRegistryError):
            cr.append_contract(
                db_path,
                make_record(version=1, body=SAMPLE_BODY + "\nzz: 1\n", phase="draft"),
            )

    def test_second_active_version_for_same_contract_id_rejected_by_db(self, db_path):
        cr.append_contract(db_path, make_record(version=1, body=SAMPLE_BODY, phase="active"))
        with pytest.raises(cr.ContractRegistryError):
            cr.append_contract(
                db_path,
                make_record(version=2, body=SAMPLE_BODY + "\nv2: true\n", phase="active"),
            )

    def test_two_active_versions_allowed_for_different_contract_ids(self, db_path):
        cr.append_contract(db_path, make_record(contract_id="corpus_daily", version=1, phase="active"))
        row2 = cr.append_contract(
            db_path,
            make_record(contract_id="funding_daily", version=1, body=SAMPLE_BODY + "\nx: 1\n", phase="active"),
        )
        assert row2["contract_id"] == "funding_daily"


# ---------------------------------------------------------------------------
# Registry querying
# ---------------------------------------------------------------------------

class TestRegistryQuerying:
    def test_get_by_hash(self, db_path):
        row = cr.append_contract(db_path, make_record())
        fetched = cr.get_by_hash(db_path, row["contract_hash"])
        assert fetched is not None
        assert fetched["contract_id"] == "corpus_daily"

    def test_get_by_hash_missing_returns_none(self, db_path):
        assert cr.get_by_hash(db_path, "0" * 64) is None

    def test_get_by_version(self, db_path):
        cr.append_contract(db_path, make_record(version=1, body=SAMPLE_BODY, phase="draft"))
        row = cr.get_by_version(db_path, "corpus_daily", 1)
        assert row is not None
        assert row["version"] == 1

    def test_get_by_version_missing_returns_none(self, db_path):
        assert cr.get_by_version(db_path, "corpus_daily", 99) is None

    def test_get_by_contract_id_returns_all_versions_ordered(self, db_path):
        cr.append_contract(db_path, make_record(version=1, body=SAMPLE_BODY, phase="superseded"))
        cr.append_contract(db_path, make_record(version=2, body=SAMPLE_BODY + "\nv:2\n", phase="active"))
        rows = cr.get_by_contract_id(db_path, "corpus_daily")
        assert [r["version"] for r in rows] == [1, 2]

    def test_get_by_contract_id_empty_for_unknown_db(self, tmp_path):
        assert cr.get_by_contract_id(tmp_path / "nope.db", "corpus_daily") == []

    def test_get_active(self, db_path):
        cr.append_contract(db_path, make_record(version=1, body=SAMPLE_BODY, phase="superseded"))
        cr.append_contract(db_path, make_record(version=2, body=SAMPLE_BODY + "\nv:2\n", phase="active"))
        row = cr.get_active(db_path, "corpus_daily")
        assert row["version"] == 2

    def test_get_active_none_when_no_active_version(self, db_path):
        cr.append_contract(db_path, make_record(version=1, body=SAMPLE_BODY, phase="draft"))
        assert cr.get_active(db_path, "corpus_daily") is None

    def test_get_all_activated_excludes_drafts(self, db_path):
        cr.append_contract(db_path, make_record(version=1, body=SAMPLE_BODY, phase="draft"))
        cr.append_contract(db_path, make_record(version=2, body=SAMPLE_BODY + "\nv:2\n", phase="active"))
        rows = cr.get_all_activated(db_path, "corpus_daily")
        assert [r["version"] for r in rows] == [2]

    def test_get_all_activated_includes_superseded_and_retired(self, db_path):
        cr.append_contract(db_path, make_record(version=1, body=SAMPLE_BODY, phase="superseded"))
        cr.append_contract(db_path, make_record(version=2, body=SAMPLE_BODY + "\nv:2\n", phase="retired"))
        rows = cr.get_all_activated(db_path, "corpus_daily")
        assert [r["version"] for r in rows] == [1, 2]

    def test_get_latest_draft(self, db_path):
        cr.append_contract(db_path, make_record(version=1, body=SAMPLE_BODY, phase="draft"))
        cr.append_contract(db_path, make_record(version=2, body=SAMPLE_BODY + "\nv:2\n", phase="draft"))
        cr.append_contract(db_path, make_record(version=3, body=SAMPLE_BODY + "\nv:3\n", phase="active"))
        latest = cr.get_latest_draft(db_path, "corpus_daily")
        assert latest["version"] == 2

    def test_get_latest_draft_none_when_no_drafts(self, db_path):
        cr.append_contract(db_path, make_record(version=1, phase="active"))
        assert cr.get_latest_draft(db_path, "corpus_daily") is None

    def test_get_all_contracts(self, db_path):
        cr.append_contract(db_path, make_record(contract_id="a", version=1, body=SAMPLE_BODY))
        cr.append_contract(db_path, make_record(contract_id="b", version=1, body=SAMPLE_BODY + "\nb:1\n"))
        rows = cr.get_all_contracts(db_path)
        assert {r["contract_id"] for r in rows} == {"a", "b"}


# ---------------------------------------------------------------------------
# Replay lookup by hash
# ---------------------------------------------------------------------------

class TestReplayLookupByHash:
    def test_resolve_by_hash_returns_record(self, db_path):
        row = cr.append_contract(db_path, make_record())
        resolved = cr.resolve_by_hash(db_path, row["contract_hash"])
        assert resolved["contract_hash"] == row["contract_hash"]
        assert resolved["body"] == SAMPLE_BODY

    def test_resolve_by_hash_missing_raises_not_fallback(self, db_path):
        with pytest.raises(cr.ContractNotFoundError):
            cr.resolve_by_hash(db_path, "f" * 64)

    def test_resolve_by_hash_works_after_superseded(self, db_path):
        # A superseded contract must remain dereferenceable forever.
        row = cr.append_contract(db_path, make_record(version=1, body=SAMPLE_BODY, phase="superseded"))
        resolved = cr.resolve_by_hash(db_path, row["contract_hash"])
        assert resolved["phase"] == "superseded"

    def test_resolve_by_hash_works_after_retired(self, db_path):
        row = cr.append_contract(db_path, make_record(version=1, body=SAMPLE_BODY, phase="retired"))
        resolved = cr.resolve_by_hash(db_path, row["contract_hash"])
        assert resolved["phase"] == "retired"

    def test_module_has_no_filesystem_read_of_contract_bodies(self):
        # Structural guard: this module must not import anything that reads
        # a contracts/*.yaml working-copy file. Path is used only for the
        # sqlite db_path itself.
        import contract_registry as mod
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "read_text" not in src
        assert "open(" not in src
        assert "yaml.safe_load" not in src


# ---------------------------------------------------------------------------
# Version lookup
# ---------------------------------------------------------------------------

class TestVersionLookup:
    def test_lookup_specific_version(self, db_path):
        cr.append_contract(db_path, make_record(version=1, body=SAMPLE_BODY, phase="superseded"))
        cr.append_contract(db_path, make_record(version=2, body=SAMPLE_BODY + "\nv:2\n", phase="active"))
        v1 = cr.get_by_version(db_path, "corpus_daily", 1)
        v2 = cr.get_by_version(db_path, "corpus_daily", 2)
        assert v1["phase"] == "superseded"
        assert v2["phase"] == "active"
        assert v1["contract_hash"] != v2["contract_hash"]


# ---------------------------------------------------------------------------
# Append-only guarantees
# ---------------------------------------------------------------------------

class TestAppendOnlyEnforcement:
    def test_raw_update_blocked(self, db_path):
        row = cr.append_contract(db_path, make_record())
        con = sqlite3.connect(db_path)
        try:
            with pytest.raises(sqlite3.IntegrityError):
                con.execute(
                    "UPDATE contract_registry SET reason = 'edited' WHERE contract_hash = ?",
                    (row["contract_hash"],),
                )
        finally:
            con.close()

    def test_raw_delete_blocked(self, db_path):
        row = cr.append_contract(db_path, make_record())
        con = sqlite3.connect(db_path)
        try:
            with pytest.raises(sqlite3.IntegrityError):
                con.execute(
                    "DELETE FROM contract_registry WHERE contract_hash = ?", (row["contract_hash"],)
                )
        finally:
            con.close()

    def test_no_update_or_delete_functions_exposed(self):
        assert not hasattr(cr, "update_contract")
        assert not hasattr(cr, "delete_contract")
        assert not hasattr(cr, "activate")
        assert not hasattr(cr, "retire")
        assert not hasattr(cr, "supersede")


# ---------------------------------------------------------------------------
# Immutability of activated contracts
# ---------------------------------------------------------------------------

class TestImmutabilityOfActivatedContracts:
    def test_active_contract_content_cannot_be_updated(self, db_path):
        # NOTE: as of PR-3 (contract_lifecycle.py), phase IS a legitimate
        # mutable field along documented lifecycle arcs (e.g. active ->
        # retired) -- see tests/test_contract_lifecycle.py for that coverage
        # and for the CONTRACT_TAMPER guard on illegal arcs. What must
        # remain permanently frozen, checked here, is the contract's
        # CONTENT: body, contract_hash, contract_id, version, and the other
        # write-once identity/audit fields.
        row = cr.append_contract(db_path, make_record(phase="active"))
        con = sqlite3.connect(db_path)
        try:
            with pytest.raises(sqlite3.IntegrityError):
                con.execute(
                    "UPDATE contract_registry SET body = 'tampered' WHERE contract_hash = ?",
                    (row["contract_hash"],),
                )
        finally:
            con.close()
        # still active and still hash-verifiable after the attempted mutation
        reloaded = cr.get_by_hash(db_path, row["contract_hash"])
        assert reloaded["phase"] == "active"
        assert cr.verify_contract_hash(reloaded) is True

    def test_active_contract_body_immutable_across_reads(self, db_path):
        row = cr.append_contract(db_path, make_record(phase="active"))
        first = cr.get_by_hash(db_path, row["contract_hash"])
        second = cr.get_by_hash(db_path, row["contract_hash"])
        assert first["body"] == second["body"] == SAMPLE_BODY

    def test_superseded_contract_still_resolves_and_verifies(self, db_path):
        row = cr.append_contract(db_path, make_record(version=1, phase="superseded"))
        resolved = cr.resolve_by_hash(db_path, row["contract_hash"])
        assert cr.verify_contract_hash(resolved) is True
