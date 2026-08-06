"""
Tests for the Governance Receipt Foundation (PR-1 of
docs/governance/governance_receipts.md).

Covers: canonical serialization, hash stability, decimal canonicalization,
replay-foundation loading, duplicate detection, append-only enforcement,
corruption detection, malformed receipts, and registry queries.

Nothing exercised here is wired into any production decision path -- these
tests exist solely to validate the foundation module in isolation, per the
PR-1 scope (docs/governance/governance_receipts.md, "Do NOT implement"
integration with Contract Registry / Evidence Library / etc).
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research" / "evidence_library"))

import receipts as r
import receipt_ledger as rl


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def make_body(**overrides) -> r.ReceiptBody:
    defaults = dict(
        decision_type="family.transition",
        decision_class="discretionary",
        action="transition",
        subject=r.Subject(kind="family", id="h5_vol_regime"),
        from_state="discovery",
        to_state="candidate",
        as_of="2026-07-13T00:00:00+00:00",
        decided_at="2026-07-13T00:00:05+00:00",
        actor=r.Actor(type="human", id="rohith", key_id="rohith-dev-1"),
        authority=r.Authority(policy_id="lifecycle.ALLOWED_TRANSITIONS", policy_hash="abc123"),
        verdict=r.Verdict(result="allowed"),
        reason_text="passed edge gate; sensitivity 100%",
    )
    defaults.update(overrides)
    return r.ReceiptBody(**defaults)


def make_derived_body(**overrides) -> r.ReceiptBody:
    defaults = dict(
        decision_type="readiness.verdict",
        decision_class="derived",
        action="admit",
        subject=r.Subject(kind="evidence_run", id="run-3f2a91c4"),
        as_of="2026-07-12T00:05:00+00:00",
        decided_at="2026-07-12T00:05:01+00:00",
        actor=r.Actor(type="service", id="daily_job", key_id="svc-daily-1"),
        authority=r.Authority(policy_id="data_completeness_gate", policy_hash="def456"),
        rule=r.RuleRef(rule_id="dq_engine", rule_hash="rulehash1", engine_version="v2"),
        evaluation=(
            r.EvaluationItem(check_id="coverage", observed="1.0", expected=">=0.99", passed=True),
        ),
        verdict=r.Verdict(result="READY"),
    )
    defaults.update(overrides)
    return r.ReceiptBody(**defaults)


@pytest.fixture
def db_path(tmp_path) -> Path:
    p = tmp_path / "receipts_test.db"
    rl.init_db(p)
    return p


# ---------------------------------------------------------------------------
# Decimal canonicalization
# ---------------------------------------------------------------------------

class TestDecimalCanonicalization:
    def test_float_becomes_fixed_decimal_string(self):
        assert r.decimal_str(0.1646) == "0.1646"

    def test_int_becomes_decimal_string(self):
        assert r.decimal_str(5) == "5"

    def test_string_passthrough_normalised(self):
        assert r.decimal_str("0.30") == "0.30"

    def test_quantize_to_places(self):
        assert r.decimal_str(0.1, places=4) == "0.1000"

    def test_bool_rejected(self):
        with pytest.raises(r.NonDecimalNumberError):
            r.decimal_str(True)

    def test_no_exponent_notation_for_tiny_values(self):
        # A naive str(Decimal) of a very small float can render as 1E-7;
        # decimal_str must never emit exponent notation.
        s = r.decimal_str(0.0000005)
        assert "E" not in s.upper()


# ---------------------------------------------------------------------------
# Canonical serialization
# ---------------------------------------------------------------------------

class TestCanonicalSerialization:
    def test_keys_sorted_and_no_whitespace(self):
        obj = {"b": 1, "a": "x"}
        out = r.canonical_json(obj)
        assert out == b'{"a":"x","b":1}'

    def test_raw_float_anywhere_is_rejected(self):
        with pytest.raises(r.NonDecimalNumberError):
            r.canonical_json({"sharpe": 0.42})

    def test_raw_float_nested_in_list_is_rejected(self):
        with pytest.raises(r.NonDecimalNumberError):
            r.canonical_json({"evaluation": [{"observed": 0.5}]})

    def test_decimal_strings_pass_through_unrejected(self):
        out = r.canonical_json({"sharpe": "0.42"})
        assert out == b'{"sharpe":"0.42"}'

    def test_same_content_different_key_order_produces_identical_bytes(self):
        a = r.canonical_json({"x": 1, "y": 2})
        b = r.canonical_json({"y": 2, "x": 1})
        assert a == b


# ---------------------------------------------------------------------------
# Hash stability / receipt identifiers
# ---------------------------------------------------------------------------

class TestHashStability:
    def test_identical_body_yields_identical_receipt_id(self):
        body = make_body()
        d = body.to_dict()
        assert r.compute_receipt_id(d) == r.compute_receipt_id(d)

    def test_receipt_id_changes_if_any_field_changes(self):
        b1 = make_body()
        b2 = make_body(reason_text="different reason")
        assert r.compute_receipt_id(b1.to_dict()) != r.compute_receipt_id(b2.to_dict())

    def test_receipt_id_is_bare_hex_sha256(self):
        body = make_body()
        rid = r.compute_receipt_id(body.to_dict())
        assert len(rid) == 64
        int(rid, 16)  # must be valid hex

    def test_crlf_and_lf_bodies_hash_identically(self):
        # sha256_lf normalises CRLF -> LF before hashing; simulate via raw bytes.
        crlf = b'{"a":"line1\r\nline2"}'
        lf = b'{"a":"line1\nline2"}'
        assert r.sha256_lf(crlf) == r.sha256_lf(lf)

    def test_stable_across_repeated_calls(self):
        body = make_body()
        ids = {r.compute_receipt_id(body.to_dict()) for _ in range(10)}
        assert len(ids) == 1

    def test_receipt_id_changes_with_attestation_basis_alone(self):
        # IQROS-P5: identity is a content hash. attestation_basis must
        # participate in it like any other field, or two receipts that
        # differ only in "where the fact came from" would be indistinguishable.
        plain = make_body()
        with_basis = make_body(attestation_basis="platform-observed")
        assert r.compute_receipt_id(plain.to_dict()) != r.compute_receipt_id(with_basis.to_dict())

    def test_receipt_id_changes_with_contract_version_alone(self):
        plain = make_body()
        versioned = make_body(contract_version="1.0.0")
        assert r.compute_receipt_id(plain.to_dict()) != r.compute_receipt_id(versioned.to_dict())


# ---------------------------------------------------------------------------
# Validation / malformed receipts
# ---------------------------------------------------------------------------

class TestValidation:
    def test_valid_discretionary_body_has_no_errors(self):
        assert r.validate_body(make_body()) == []

    def test_valid_derived_body_has_no_errors(self):
        assert r.validate_body(make_derived_body()) == []

    def test_unknown_decision_type_rejected(self):
        body = make_body(decision_type="not.a.real.type")
        errors = r.validate_body(body)
        assert any("unknown decision_type" in e for e in errors)

    def test_decision_class_mismatch_with_taxonomy_rejected(self):
        body = make_body(decision_type="readiness.verdict", decision_class="discretionary",
                          action="admit", subject=r.Subject(kind="evidence_run", id="x"))
        errors = r.validate_body(body)
        assert any("does not match taxonomy" in e for e in errors)

    def test_discretionary_without_reason_text_rejected(self):
        body = make_body(reason_text=None)
        errors = r.validate_body(body)
        assert any("reason_text is required" in e for e in errors)

    def test_discretionary_with_blank_reason_text_rejected(self):
        body = make_body(reason_text="   ")
        errors = r.validate_body(body)
        assert any("reason_text is required" in e for e in errors)

    def test_derived_receipt_does_not_require_reason_text(self):
        body = make_derived_body()
        assert body.reason_text is None
        assert r.validate_body(body) == []

    def test_missing_subject_id_rejected(self):
        body = make_body(subject=r.Subject(kind="family", id=""))
        errors = r.validate_body(body)
        assert any("subject.id is required" in e for e in errors)

    def test_bad_actor_type_rejected(self):
        body = make_body(actor=r.Actor(type="robot", id="x"))
        errors = r.validate_body(body)
        assert any("actor.type must be one of" in e for e in errors)

    def test_bad_action_rejected(self):
        body = make_body(action="teleport")
        errors = r.validate_body(body)
        assert any("action must be one of" in e for e in errors)

    def test_malformed_as_of_rejected(self):
        body = make_body(as_of="not-a-date")
        errors = r.validate_body(body)
        assert any("as_of is not a valid ISO-8601" in e for e in errors)

    def test_bootstrap_without_note_rejected(self):
        body = make_body(attestation="bootstrap", attestation_note=None)
        errors = r.validate_body(body)
        assert any("attestation_note is required" in e for e in errors)

    def test_bootstrap_with_note_accepted(self):
        body = make_body(attestation="bootstrap", attestation_note="reconstructed from state_transitions")
        assert r.validate_body(body) == []

    def test_invalid_attestation_value_rejected(self):
        body = make_body(attestation="totally-legit")
        errors = r.validate_body(body)
        assert any("attestation, if set" in e for e in errors)

    def test_attestation_basis_defaults_to_none_and_is_valid(self):
        body = make_body()
        assert body.attestation_basis is None
        assert body.contract_version is None
        assert r.validate_body(body) == []

    def test_each_attestation_basis_value_accepted(self):
        for basis in sorted(r.ATTESTATION_BASES):
            body = make_body(attestation_basis=basis)
            assert r.validate_body(body) == [], f"{basis!r} should be a valid attestation_basis"

    def test_invalid_attestation_basis_value_rejected(self):
        body = make_body(attestation_basis="carrier-pigeon")
        errors = r.validate_body(body)
        assert any("attestation_basis, if set" in e for e in errors)

    def test_contract_version_accepts_any_non_validated_string(self):
        # ADR-I015 does not constrain contract_version's format beyond "the
        # receipt contract version this receipt conforms to" -- no enum, no
        # regex. This test documents that absence of a format check is
        # deliberate, not an oversight.
        body = make_body(contract_version="1.0.0")
        assert r.validate_body(body) == []

    def test_validate_body_or_raise_raises_on_error(self):
        with pytest.raises(r.BodyValidationError):
            r.validate_body_or_raise(make_body(action="teleport"))

    def test_validate_body_or_raise_silent_on_valid(self):
        r.validate_body_or_raise(make_body())  # must not raise


# ---------------------------------------------------------------------------
# Execution decision type -- reserved (WBS 0.6), NOT yet emitted (RG3)
# ---------------------------------------------------------------------------

class TestExecutionDecisionTypeReserved:
    def test_all_three_execution_types_are_reserved_as_discretionary(self):
        for decision_type in ("execution.order", "execution.fill", "execution.reconcile"):
            assert r.DECISION_TAXONOMY[decision_type] == "discretionary"

    def test_a_reserved_execution_body_validates_structurally(self):
        # Reserved does not mean unusable -- RG1/RG3 distinguish "reserved,
        # not yet emitted" from "unreserved, refused". A structurally valid
        # body of this type must pass validation today, even though nothing
        # in the platform constructs one yet.
        body = make_body(
            decision_type="execution.order",
            action="transition",
            subject=r.Subject(kind="contract", id="placeholder-not-a-real-order"),
        )
        assert r.validate_body(body) == []

    def test_no_existing_module_constructs_an_execution_receipt(self):
        # RG3: "Reserved-but-unemitted types are normal." Confirmed
        # structurally, not merely asserted -- a repo-wide search for the
        # three literal decision-type strings outside this test file and the
        # taxonomy declaration itself must find nothing.
        research_dir = ROOT / "research"
        needles = ("execution.order", "execution.fill", "execution.reconcile")
        hits: list[str] = []
        for path in research_dir.rglob("*.py"):
            if path.name in ("receipts.py", "test_governance_receipts.py"):
                continue
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for needle in needles:
                if needle in text:
                    hits.append(f"{path.relative_to(ROOT)}: {needle!r}")
        assert hits == [], f"Execution decision type(s) already emitted somewhere: {hits}"


# ---------------------------------------------------------------------------
# Replay foundation
# ---------------------------------------------------------------------------

class TestReplayFoundation:
    def test_verify_receipt_true_for_untampered(self):
        body = make_body().to_dict()
        rid = r.compute_receipt_id(body)
        assert r.verify_receipt(body, rid) is True

    def test_verify_receipt_false_for_tampered(self):
        body = make_body().to_dict()
        rid = r.compute_receipt_id(body)
        tampered = dict(body)
        tampered["verdict"] = {"result": "denied", "reason_code": None}
        assert r.verify_receipt(tampered, rid) is False

    def test_replay_bootstrap_refuses(self):
        body = make_body(attestation="bootstrap", attestation_note="migrated").to_dict()
        result = r.replay(body)
        assert result.ok is False
        assert result.mode == "refused"

    def test_replay_discretionary_verifies_envelope_only(self):
        body = make_body().to_dict()
        result = r.replay(body)
        assert result.ok is True
        assert result.mode == "envelope-verified"

    def test_replay_discretionary_missing_reason_text_diverges(self):
        body = make_body().to_dict()
        body["reason_text"] = None
        result = r.replay(body)
        assert result.ok is False
        assert result.mode == "diverged"

    def test_replay_derived_without_rule_fn_raises_not_implemented(self):
        body = make_derived_body().to_dict()
        with pytest.raises(NotImplementedError):
            r.replay(body)

    def test_rm3_new_fields_do_not_create_a_rule_substitution_path(self):
        # ADR-I015 RM3: "Substituting a current rule for an unresolvable
        # historical one is prohibited." Engineering roadmap WBS 0.7 --
        # confirms the additive attestation_basis/contract_version fields
        # give replay() no new way to satisfy a derived receipt without an
        # explicit rule_fn. The concrete historical-rule-resolution guard
        # (get_rule() raising UnknownRuleError) lives in evidence_governance.py
        # and is unaffected by this milestone -- verified unchanged by the
        # full regression suite; this test covers the generic foundation
        # replay() itself, which is what this module owns.
        body = make_derived_body(
            attestation_basis="platform-observed", contract_version="1.0.0"
        ).to_dict()
        with pytest.raises(NotImplementedError):
            r.replay(body)

    def test_replay_derived_with_rule_fn_matching_verdict(self):
        body = make_derived_body().to_dict()
        result = r.replay(body, rule_fn=lambda b: r.Verdict(result="READY"))
        assert result.ok is True
        assert result.mode == "verified"

    def test_replay_derived_with_rule_fn_diverging_verdict(self):
        body = make_derived_body().to_dict()
        result = r.replay(body, rule_fn=lambda b: r.Verdict(result="INCOMPLETE_DATA"))
        assert result.ok is False
        assert result.mode == "diverged"

    def test_fold_reconstruct_last_write_wins(self):
        bodies = [
            make_body(as_of="2026-07-10T00:00:00+00:00", to_state="candidate").to_dict(),
            make_body(as_of="2026-07-12T00:00:00+00:00", to_state="validated").to_dict(),
        ]
        state = r.fold_reconstruct(bodies, as_of="2026-07-13T00:00:00+00:00")
        key = ("family", "h5_vol_regime")
        assert state[key]["to_state"] == "validated"

    def test_fold_reconstruct_respects_as_of_cutoff(self):
        bodies = [
            make_body(as_of="2026-07-10T00:00:00+00:00", to_state="candidate").to_dict(),
            make_body(as_of="2026-07-12T00:00:00+00:00", to_state="validated").to_dict(),
        ]
        state = r.fold_reconstruct(bodies, as_of="2026-07-11T00:00:00+00:00")
        key = ("family", "h5_vol_regime")
        assert state[key]["to_state"] == "candidate"


# ---------------------------------------------------------------------------
# Persistence / registry loading & querying
# ---------------------------------------------------------------------------

class TestRegistryPersistence:
    def test_init_db_idempotent(self, db_path):
        rl.init_db(db_path)  # second call must not raise
        rl.init_db(db_path)

    def test_append_genesis_receipt(self, db_path):
        row = rl.append_receipt(db_path, make_body())
        assert row["seq"] == 1
        assert row["prev_receipt_hash"] is None
        assert row["body"]["prev_receipt_hash"] is None

    def test_append_second_receipt_chains_to_first(self, db_path):
        first = rl.append_receipt(db_path, make_body())
        second = rl.append_receipt(db_path, make_body(reason_text="second decision"))
        assert second["seq"] == 2
        assert second["prev_receipt_hash"] == first["receipt_id"]
        assert second["body"]["prev_receipt_hash"] == first["receipt_id"]

    def test_get_receipt_roundtrip(self, db_path):
        row = rl.append_receipt(db_path, make_body())
        fetched = rl.get_receipt(db_path, row["receipt_id"])
        assert fetched is not None
        assert fetched["receipt_id"] == row["receipt_id"]
        assert fetched["body"]["decision_type"] == "family.transition"

    def test_get_receipt_missing_returns_none(self, db_path):
        assert rl.get_receipt(db_path, "0" * 64) is None

    def test_get_by_seq(self, db_path):
        row = rl.append_receipt(db_path, make_body())
        assert rl.get_by_seq(db_path, 1)["receipt_id"] == row["receipt_id"]
        assert rl.get_by_seq(db_path, 99) is None

    def test_get_chain_head(self, db_path):
        rl.append_receipt(db_path, make_body())
        second = rl.append_receipt(db_path, make_body(reason_text="second"))
        head = rl.get_chain_head(db_path)
        assert head["receipt_id"] == second["receipt_id"]

    def test_get_all_receipts_ordered_by_seq(self, db_path):
        rl.append_receipt(db_path, make_body())
        rl.append_receipt(db_path, make_body(reason_text="second"))
        rl.append_receipt(db_path, make_body(reason_text="third"))
        rows = rl.get_all_receipts(db_path)
        assert [row["seq"] for row in rows] == [1, 2, 3]

    def test_get_all_receipts_on_nonexistent_db_returns_empty(self, tmp_path):
        assert rl.get_all_receipts(tmp_path / "does_not_exist.db") == []

    def test_get_receipts_by_subject(self, db_path):
        rl.append_receipt(db_path, make_body(subject=r.Subject(kind="family", id="fam_a")))
        rl.append_receipt(db_path, make_body(subject=r.Subject(kind="family", id="fam_b"), reason_text="other family"))
        rows = rl.get_receipts_by_subject(db_path, "family", "fam_a")
        assert len(rows) == 1
        assert rows[0]["subject_id"] == "fam_a"

    def test_get_receipts_by_type(self, db_path):
        rl.append_receipt(db_path, make_body())
        rl.append_receipt(db_path, make_derived_body())
        rows = rl.get_receipts_by_type(db_path, "readiness.verdict")
        assert len(rows) == 1
        assert rows[0]["decision_type"] == "readiness.verdict"

    def test_attestation_basis_and_contract_version_round_trip(self, db_path):
        row = rl.append_receipt(
            db_path,
            make_body(attestation_basis="platform-observed", contract_version="1.0.0"),
        )
        assert row["attestation_basis"] == "platform-observed"
        assert row["contract_version"] == "1.0.0"
        assert row["body"]["attestation_basis"] == "platform-observed"
        assert row["body"]["contract_version"] == "1.0.0"

        fetched = rl.get_receipt(db_path, row["receipt_id"])
        assert fetched["attestation_basis"] == "platform-observed"
        assert fetched["contract_version"] == "1.0.0"

    def test_attestation_basis_and_contract_version_default_to_null_column(self, db_path):
        row = rl.append_receipt(db_path, make_body())
        assert row["attestation_basis"] is None
        assert row["contract_version"] is None

    def test_init_db_on_pre_existing_table_without_new_columns_does_not_alter_it(self, tmp_path):
        # Reproduces a ledger created before this milestone: governance_receipts
        # exists but lacks attestation_basis/contract_version. Mirrors
        # test_migration_harness.py's reproduction style for runs.run_kind.
        #
        # init_db() must NOT add the columns here -- that ALTER is deliberately
        # NOT performed by init_db() (see its docstring): init_db() is reachable
        # from governance_verification.verify_governance()'s documented
        # "read-only" sweep via evidence_governance.init_db()'s cascade, and an
        # ALTER there would make read-only verification silently mutate a real
        # ledger's schema. Column-ensure lives only in
        # migrate_receipt_attestation_basis.ensure_columns(), covered in
        # tests/test_migrate_receipt_attestation_basis.py.
        p = tmp_path / "pre_existing.db"
        con = sqlite3.connect(p)
        con.executescript("""
            CREATE TABLE governance_receipts (
                receipt_id        TEXT PRIMARY KEY,
                seq               INTEGER NOT NULL UNIQUE,
                prev_receipt_hash TEXT,
                schema_version    INTEGER NOT NULL,
                decision_type     TEXT NOT NULL,
                decision_class    TEXT NOT NULL,
                action            TEXT NOT NULL,
                subject_kind      TEXT NOT NULL,
                subject_id        TEXT NOT NULL,
                subject_version   TEXT,
                from_state        TEXT,
                to_state          TEXT,
                as_of             TEXT NOT NULL,
                decided_at        TEXT NOT NULL,
                actor_type        TEXT NOT NULL,
                actor_id          TEXT NOT NULL,
                authority_hash    TEXT NOT NULL,
                rule_hash         TEXT,
                engine_version    TEXT,
                body_json         TEXT NOT NULL,
                signature_json    TEXT NOT NULL DEFAULT '[]'
            );
        """)
        con.commit()
        con.close()

        columns_before = {row[1] for row in sqlite3.connect(p).execute(
            "PRAGMA table_info(governance_receipts)")}
        assert "attestation_basis" not in columns_before
        assert "contract_version" not in columns_before

        rl.init_db(p)  # must not raise; must NOT add either column

        columns_after = {row[1] for row in sqlite3.connect(p).execute(
            "PRAGMA table_info(governance_receipts)")}
        assert "attestation_basis" not in columns_after
        assert "contract_version" not in columns_after


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------

class TestDuplicateDetection:
    def test_byte_identical_body_rejected_on_second_insert(self, db_path):
        body = make_body()
        rl.append_receipt(db_path, body)
        # Force the exact same prev_receipt_hash/seq collision by hand-building
        # a second registry state that would hash identically: simplest way
        # to prove this is to attempt to insert the very same already-chained
        # body dict again as a synthetic duplicate row.
        con = sqlite3.connect(db_path)
        try:
            first = rl.get_chain_head(db_path)
            with pytest.raises(sqlite3.IntegrityError):
                con.execute(
                    "INSERT INTO governance_receipts (receipt_id, seq, prev_receipt_hash, "
                    "schema_version, decision_type, decision_class, action, subject_kind, "
                    "subject_id, subject_version, from_state, to_state, as_of, decided_at, "
                    "actor_type, actor_id, authority_hash, rule_hash, engine_version, "
                    "body_json, signature_json) VALUES (?,2,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        first["receipt_id"], first["prev_receipt_hash"], first["schema_version"],
                        first["decision_type"], first["decision_class"], first["action"],
                        first["subject_kind"], first["subject_id"], first["subject_version"],
                        first["from_state"], first["to_state"], first["as_of"], first["decided_at"],
                        first["actor_type"], first["actor_id"], first["authority_hash"],
                        first["rule_hash"], first["engine_version"], first["body_json"], "[]",
                    ),
                )
        finally:
            con.close()


# ---------------------------------------------------------------------------
# Append-only enforcement
# ---------------------------------------------------------------------------

class TestAppendOnlyEnforcement:
    def test_raw_update_is_blocked_by_trigger(self, db_path):
        row = rl.append_receipt(db_path, make_body())
        con = sqlite3.connect(db_path)
        try:
            with pytest.raises(sqlite3.IntegrityError):
                con.execute(
                    "UPDATE governance_receipts SET to_state = 'validated' WHERE receipt_id = ?",
                    (row["receipt_id"],),
                )
        finally:
            con.close()

    def test_raw_delete_is_blocked_by_trigger(self, db_path):
        row = rl.append_receipt(db_path, make_body())
        con = sqlite3.connect(db_path)
        try:
            with pytest.raises(sqlite3.IntegrityError):
                con.execute(
                    "DELETE FROM governance_receipts WHERE receipt_id = ?", (row["receipt_id"],)
                )
        finally:
            con.close()

    def test_no_update_or_delete_functions_exposed_in_module(self):
        assert not hasattr(rl, "update_receipt")
        assert not hasattr(rl, "delete_receipt")


# ---------------------------------------------------------------------------
# Corruption detection / chain verification
# ---------------------------------------------------------------------------

class TestCorruptionDetection:
    def test_verify_chain_ok_on_untampered_chain(self, db_path):
        rl.append_receipt(db_path, make_body())
        rl.append_receipt(db_path, make_body(reason_text="second"))
        rl.append_receipt(db_path, make_body(reason_text="third"))
        result = rl.verify_chain(db_path)
        assert result.ok is True
        assert result.n_receipts == 3
        assert result.errors == []

    def test_verify_chain_ok_on_empty_ledger(self, db_path):
        result = rl.verify_chain(db_path)
        assert result.ok is True
        assert result.n_receipts == 0

    def test_verify_receipt_row_detects_tampered_body(self):
        body = make_body().to_dict()
        rid = r.compute_receipt_id(body)
        assert rl_verify_row(body, rid) is True
        body["verdict"]["result"] = "denied"
        assert rl_verify_row(body, rid) is False

    def test_verify_chain_detects_body_json_tamper_via_trigger_bypassed_row(self, db_path, monkeypatch):
        # The append-only trigger makes it impossible to corrupt a row through
        # normal SQL from this process. To prove verify_chain() *would* catch
        # corruption if it ever occurred (e.g. direct file-level tampering,
        # a bug in a future migration, or a trigger being dropped), construct
        # an independent on-disk row set with the trigger removed and confirm
        # detection.
        rl.append_receipt(db_path, make_body())
        con = sqlite3.connect(db_path)
        try:
            con.execute("DROP TRIGGER trg_receipts_no_update")
            con.execute(
                "UPDATE governance_receipts SET body_json = "
                "REPLACE(body_json, '\"allowed\"', '\"DENIED\"')"
            )
            con.commit()
        finally:
            con.close()
        result = rl.verify_chain(db_path)
        assert result.ok is False
        assert any("TAMPERING" in e for e in result.errors)

    def test_verify_chain_detects_broken_prev_hash_link(self, db_path):
        rl.append_receipt(db_path, make_body())
        rl.append_receipt(db_path, make_body(reason_text="second"))
        con = sqlite3.connect(db_path)
        try:
            con.execute("DROP TRIGGER trg_receipts_no_update")
            con.execute(
                "UPDATE governance_receipts SET prev_receipt_hash = '" + "f" * 64 + "' WHERE seq = 2"
            )
            con.commit()
        finally:
            con.close()
        result = rl.verify_chain(db_path)
        assert result.ok is False
        assert any("chain break" in e for e in result.errors)


def rl_verify_row(body: dict, receipt_id: str) -> bool:
    return r.verify_receipt(body, receipt_id)


# ---------------------------------------------------------------------------
# No production behaviour touched
# ---------------------------------------------------------------------------

class TestNoProductionWiring:
    def test_no_existing_module_imports_receipts(self):
        # PR-1 scope: nothing should begin emitting receipts yet. This is a
        # coarse guard, not exhaustive -- it only checks the modules this PR
        # explicitly must not touch.
        untouched = [
            ROOT / "research" / "evidence_library" / "record.py",
            ROOT / "research" / "evidence_library" / "lifecycle.py",
            ROOT / "research" / "evidence_library" / "report.py",
        ]
        for path in untouched:
            text = path.read_text(encoding="utf-8")
            assert "import receipts" not in text
            assert "import receipt_ledger" not in text
