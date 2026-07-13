"""
Contract Governance Receipt Emission — PR-4 of docs/governance/governance_receipts.md
and docs/governance/contract_versioning.md.

Wires PR-1's receipt ledger into PR-3's contract lifecycle for exactly two
decision types: contract.activate and contract.retire. No other governance
event is emitted here -- see the "Do NOT Implement" note below and in
docs/governance/contract_receipts_schema_reference.md.

Dependency check (per the task's explicit stop condition): before writing
any code, the question was whether contract.activate/.retire receipt
emission requires any OTHER receipt type to exist first. It does not.
Both decision types are already present in receipts.DECISION_TAXONOMY
(added in PR-1, unused until now), both are 'discretionary' (no derived
verdict to recompute), and neither needs a prior receipt as an input to be
constructed -- only, optionally, the PREVIOUS CONTRACT VERSION's hash (when
one exists), which is not a receipt. Implementation proceeded without
expanding scope.

Atomic transaction shape (item 7), followed literally:

    BEGIN
      -> activate contract              (contract_lifecycle._activate_on_connection,
                                          written with activation_receipt_status=
                                          'PENDING_RECEIPT', activated_receipt_id=NULL --
                                          the receipt does not exist yet)
      -> generate receipt                (build a receipts.ReceiptBody)
      -> validate receipt                (receipts.validate_body_or_raise,
                                          inside receipt_ledger.append_receipt_on_connection)
      -> persist receipt                 (receipt_ledger.append_receipt_on_connection,
                                          SAME connection/transaction)
      -> store receipt reference         (UPDATE contract_registry SET
                                          activated_receipt_id=<receipt_id>,
                                          activation_receipt_status='RECORDED')
    COMMIT

If ANY step raises, the outer try/except/rollback undoes everything --
including the "activate contract" step, which is not yet committed. A
partially-governed contract (activated but receipt-less, or receipted but
not marked RECORDED) never survives to COMMIT.

Both the contract write and the receipt write share ONE sqlite3 connection
and ONE BEGIN IMMEDIATE lock -- this is only possible because PR-1's
receipt_ledger.py and PR-3's contract_lifecycle.py were each refactored
(minimally) to expose a *_on_connection() primitive that assumes the caller
owns the transaction. Neither module's public, connection-owning functions
(append_receipt(), activate_contract(), retire_contract()) changed behavior.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import contract_lifecycle as lc
import contract_registry as reg
import receipt_ledger as rl
from contract_lifecycle import (
    ALLOWED_TRANSITIONS,
    ContractNotFoundError,
    InvalidTransitionError,
    _activate_on_connection,
    _retire_on_connection,
    can_transition,
    now_iso,
)
from contract_registry import (
    ContractRecord,
    ContractRegistryError,
    ContractValidationError,
    canonicalize_contract_body,
    compute_contract_hash,
    validate_record,
)
from receipts import (
    Actor,
    Authority,
    EvaluationItem,
    InputRef,
    ReceiptBody,
    ReplayResult,
    RuleRef,
    Subject,
    Verdict,
    canonical_json,
    replay as replay_receipt_body,
    sha256_lf,
    verify_receipt,
)

ENGINE_VERSION = "contract_lifecycle.v1"

# authority.policy_hash: a real, deterministic hash of the actual lifecycle
# rules in code -- not an invented placeholder. Changes automatically (and
# correctly) if ALLOWED_TRANSITIONS ever changes, which is exactly the
# property a policy_hash should have.
_POLICY_DOC = {"engine_version": ENGINE_VERSION,
               "allowed_transitions": {k: sorted(v) for k, v in ALLOWED_TRANSITIONS.items()}}
_POLICY_HASH = sha256_lf(canonical_json(_POLICY_DOC))


class ReceiptEmissionError(ContractRegistryError):
    """Raised when receipt generation/validation/persistence fails inside
    the atomic activate/retire-with-receipt transaction. The transaction is
    always rolled back before this propagates -- see the module docstring."""


def _busy_connect(db_path: Path | str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 5000")
    return con


def init_db(db_path: Path | str) -> None:
    """Initializes both schemas this module needs in one file: contract_registry
    (PR-2/3/4) and governance_receipts (PR-1). Atomic activate-with-receipt
    requires both tables to live in the same SQLite file, sharing one
    connection/transaction -- SQLite cannot commit atomically across two
    separate database files without a heavier mechanism this PR does not
    introduce."""
    reg.init_db(db_path)
    rl.init_db(db_path)


# ---------------------------------------------------------------------------
# Receipt construction (items 1-2)
# ---------------------------------------------------------------------------

def _build_activate_receipt_body(
    contract_id: str,
    contract_hash: str,
    version: int,
    actor: str,
    reason: str,
    prev_hash: str | None,
    as_of: str,
    decided_at: str,
):
    inputs = ()
    if prev_hash is not None:
        inputs = (InputRef(kind="contract", id=contract_id, hash=prev_hash),)

    return dict(
        decision_type="contract.activate",
        decision_class="discretionary",
        action="activate",
        subject=Subject(kind="contract", id=contract_hash, version_or_hash=str(version)),
        from_state="draft",
        to_state="active",
        as_of=as_of,
        decided_at=decided_at,
        actor=Actor(type="human", id=actor, key_id=None),
        authority=Authority(policy_id="contract_lifecycle.activate", policy_hash=_POLICY_HASH, role=None),
        inputs=inputs,
        rule=RuleRef(
            rule_id="contract_lifecycle.ALLOWED_TRANSITIONS",
            rule_hash=_POLICY_HASH,
            engine_version=ENGINE_VERSION,
            thresholds_hash=None,
        ),
        evaluation=(
            EvaluationItem(
                check_id="lifecycle.transition_legal",
                observed="draft->active",
                expected="in ALLOWED_TRANSITIONS",
                passed=True,
            ),
        ),
        verdict=Verdict(result="activated", reason_code=None),
        reason_text=reason,
    )


def _build_retire_receipt_body(
    contract_id: str,
    contract_hash: str,
    version: int,
    actor: str,
    reason: str,
    from_phase: str,
    as_of: str,
    decided_at: str,
):
    return dict(
        decision_type="contract.retire",
        decision_class="discretionary",
        action="retire",
        subject=Subject(kind="contract", id=contract_hash, version_or_hash=str(version)),
        from_state=from_phase,
        to_state="retired",
        as_of=as_of,
        decided_at=decided_at,
        actor=Actor(type="human", id=actor, key_id=None),
        authority=Authority(policy_id="contract_lifecycle.retire", policy_hash=_POLICY_HASH, role=None),
        inputs=(InputRef(kind="contract", id=contract_id, hash=contract_hash),),
        rule=RuleRef(
            rule_id="contract_lifecycle.ALLOWED_TRANSITIONS",
            rule_hash=_POLICY_HASH,
            engine_version=ENGINE_VERSION,
            thresholds_hash=None,
        ),
        evaluation=(
            EvaluationItem(
                check_id="lifecycle.transition_legal",
                observed=f"{from_phase}->retired",
                expected="in ALLOWED_TRANSITIONS",
                passed=True,
            ),
        ),
        verdict=Verdict(result="retired", reason_code=None),
        reason_text=reason,
    )


def _receipt_body_kwargs_to_dataclass(kwargs: dict) -> ReceiptBody:
    return ReceiptBody(**kwargs)


# ---------------------------------------------------------------------------
# Atomic activation + receipt (items 1, 3, 5, 6, 7)
# ---------------------------------------------------------------------------

def activate_contract_with_receipt(
    db_path: Path | str,
    contract_id: str,
    body: str | dict,
    actor: str,
    reason: str,
    contract_schema_version: int = 1,
    change_class: str | None = None,
    impact_report_hash: str | None = None,
) -> dict:
    """
    Atomically activate a contract version AND emit its governance receipt.

    Returns {"contract": <contract_registry row>, "receipt": <governance_receipts row>}.

    Any failure at any step -- contract validation, the contract insert,
    receipt construction, receipt validation, receipt persistence, or
    storing the receipt reference back onto the contract row -- rolls back
    the ENTIRE transaction. There is no state where a contract is active
    without a receipt, or a receipt exists without its contract being
    active, once this function returns successfully; and no state where
    either exists alone if it raises.
    """
    canonical_body = canonicalize_contract_body(body)
    contract_hash = compute_contract_hash(canonical_body)

    # Pre-lock structural validation (fail fast for a doomed call), same
    # placeholder-value convention as contract_lifecycle.activate_contract():
    # 'PENDING_RECEIPT' stands in for the shape check only. The real
    # terminal value ('RECORDED' + the real receipt_id) is written inside
    # the transaction below, once the receipt actually exists.
    candidate = ContractRecord(
        contract_id=contract_id, version=1, contract_schema_version=contract_schema_version,
        body=canonical_body, phase="active", actor=actor, reason=reason,
        change_class=change_class, impact_report_hash=impact_report_hash,
        activation_receipt_status="PENDING_RECEIPT",
    )
    errors = validate_record(candidate)
    if errors:
        raise ContractValidationError("; ".join(errors))
    if not (reason or "").strip():
        raise ContractValidationError("reason is required and must be non-empty")

    con = _busy_connect(db_path)
    try:
        con.execute("BEGIN IMMEDIATE")
        try:
            # -- activate contract --------------------------------------
            prev_active = con.execute(
                "SELECT contract_hash FROM contract_registry "
                "WHERE contract_id = ? AND phase = 'active'",
                (contract_id,),
            ).fetchone()
            prev_hash = prev_active["contract_hash"] if prev_active is not None else None

            version = _activate_on_connection(
                con, contract_id, canonical_body, contract_hash, actor, reason,
                contract_schema_version, change_class, impact_report_hash,
                activated_receipt_id=None, activation_receipt_status="PENDING_RECEIPT",
            )

            # -- generate receipt ----------------------------------------
            as_of = decided_at = now_iso()
            body_kwargs = _build_activate_receipt_body(
                contract_id, contract_hash, version, actor, reason, prev_hash, as_of, decided_at,
            )
            receipt_body = _receipt_body_kwargs_to_dataclass(body_kwargs)

            # -- validate receipt + persist receipt ----------------------
            # (both inside append_receipt_on_connection -- PR-1's own logic,
            # reused verbatim, not reimplemented; see module docstring)
            try:
                receipt_row = rl.append_receipt_on_connection(con, receipt_body)
            except Exception as exc:
                raise ReceiptEmissionError(f"receipt generation/validation/persistence failed: {exc}") from exc

            # -- store receipt reference ----------------------------------
            _store_activation_receipt_reference(con, contract_hash, receipt_row["receipt_id"])
        except Exception:
            con.rollback()
            raise
        else:
            con.commit()
    finally:
        con.close()

    return {
        "contract": reg.get_by_hash(db_path, contract_hash),
        "receipt": rl.get_receipt(db_path, receipt_row["receipt_id"]),
    }


def _store_activation_receipt_reference(con: sqlite3.Connection, contract_hash: str, receipt_id: str) -> None:
    """Item 3/7's 'store receipt reference' step, as its own named function
    (not inlined) so it is independently testable and so a failure at
    exactly this step can be exercised without needing to fail an earlier
    one."""
    con.execute(
        "UPDATE contract_registry SET activated_receipt_id = ?, "
        "activation_receipt_status = 'RECORDED' WHERE contract_hash = ?",
        (receipt_id, contract_hash),
    )


# ---------------------------------------------------------------------------
# Atomic retirement + receipt (items 2, 4, 5, 6, 7)
# ---------------------------------------------------------------------------

def retire_contract_with_receipt(
    db_path: Path | str,
    contract_hash: str,
    actor: str,
    reason: str,
) -> dict:
    """
    Atomically retire a contract version AND emit its governance receipt.

    Same all-or-nothing guarantee as activate_contract_with_receipt(): any
    failure rolls back the retirement UPDATE along with the receipt write.
    """
    if not actor:
        raise ContractValidationError("actor is required and must be non-empty")
    if not (reason or "").strip():
        raise ContractValidationError("reason is required and must be non-empty")

    con = _busy_connect(db_path)
    try:
        con.execute("BEGIN IMMEDIATE")
        try:
            existing = con.execute(
                "SELECT phase, version FROM contract_registry WHERE contract_hash = ?",
                (contract_hash,),
            ).fetchone()
            if existing is None:
                raise ContractNotFoundError(f"contract_hash {contract_hash} not found in registry")
            from_phase = existing["phase"]
            version = existing["version"]
            if not can_transition(from_phase, "retired"):
                raise InvalidTransitionError(
                    f"cannot transition '{from_phase}' -> 'retired'; "
                    f"allowed destinations from '{from_phase}': "
                    f"{sorted(ALLOWED_TRANSITIONS.get(from_phase, frozenset()))}"
                )

            # -- retire contract ------------------------------------------
            # (validation of the transition happened above; _retire_on_connection
            # re-validates it too -- cheap, and keeps this module honest if the
            # transition rules ever change under it)
            _retire_on_connection(
                con, contract_hash,
                retired_receipt_id=None, retirement_receipt_status="PENDING_RECEIPT",
            )

            # -- generate + validate + persist receipt ---------------------
            as_of = decided_at = now_iso()
            contract_id = con.execute(
                "SELECT contract_id FROM contract_registry WHERE contract_hash = ?",
                (contract_hash,),
            ).fetchone()["contract_id"]
            body_kwargs = _build_retire_receipt_body(
                contract_id, contract_hash, version, actor, reason, from_phase, as_of, decided_at,
            )
            receipt_body = _receipt_body_kwargs_to_dataclass(body_kwargs)
            try:
                receipt_row = rl.append_receipt_on_connection(con, receipt_body)
            except Exception as exc:
                raise ReceiptEmissionError(f"receipt generation/validation/persistence failed: {exc}") from exc

            # -- store receipt reference ------------------------------------
            _store_retirement_receipt_reference(con, contract_hash, receipt_row["receipt_id"])
        except Exception:
            con.rollback()
            raise
        else:
            con.commit()
    finally:
        con.close()

    return {
        "contract": reg.get_by_hash(db_path, contract_hash),
        "receipt": rl.get_receipt(db_path, receipt_row["receipt_id"]),
    }


def _store_retirement_receipt_reference(con: sqlite3.Connection, contract_hash: str, receipt_id: str) -> None:
    """Retirement counterpart to _store_activation_receipt_reference()."""
    con.execute(
        "UPDATE contract_registry SET retired_receipt_id = ?, "
        "retirement_receipt_status = 'RECORDED' WHERE contract_hash = ?",
        (receipt_id, contract_hash),
    )


# ---------------------------------------------------------------------------
# Receipt replay verification (item 9) -- reuses the PR-1 replay framework
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ContractReceiptVerification:
    contract_hash: str
    event: str  # "activate" | "retire"
    receipt_id: str
    hash_ok: bool           # receipts.verify_receipt: receipt_id recomputes from stored body
    replay: ReplayResult    # receipts.replay: envelope verification for a discretionary receipt


def verify_activation_receipt(db_path: Path | str, contract_hash: str) -> ContractReceiptVerification:
    """
    Verifies the contract.activate receipt for contract_hash using PR-1's
    replay framework directly (receipts.verify_receipt + receipts.replay) --
    not a reimplementation of either.
    """
    contract = reg.get_by_hash(db_path, contract_hash)
    if contract is None:
        raise ContractNotFoundError(f"contract_hash {contract_hash} not found in registry")
    receipt_id = contract["activated_receipt_id"]
    if receipt_id is None:
        raise ReceiptEmissionError(
            f"contract_hash {contract_hash} has activation_receipt_status="
            f"'{contract['activation_receipt_status']}' -- no receipt to verify "
            f"(only 'RECORDED' rows have an activated_receipt_id)"
        )
    receipt = rl.get_receipt(db_path, receipt_id)
    if receipt is None:
        raise ReceiptEmissionError(
            f"contract_hash {contract_hash} references receipt_id {receipt_id}, "
            f"which does not resolve in governance_receipts (CORRUPTED REFERENCE)"
        )
    hash_ok = verify_receipt(receipt["body"], receipt["receipt_id"])
    replay_result = replay_receipt_body(receipt["body"])
    return ContractReceiptVerification(
        contract_hash=contract_hash, event="activate", receipt_id=receipt_id,
        hash_ok=hash_ok, replay=replay_result,
    )


def verify_retirement_receipt(db_path: Path | str, contract_hash: str) -> ContractReceiptVerification:
    contract = reg.get_by_hash(db_path, contract_hash)
    if contract is None:
        raise ContractNotFoundError(f"contract_hash {contract_hash} not found in registry")
    receipt_id = contract["retired_receipt_id"]
    if receipt_id is None:
        raise ReceiptEmissionError(
            f"contract_hash {contract_hash} has retirement_receipt_status="
            f"'{contract['retirement_receipt_status']}' -- no receipt to verify"
        )
    receipt = rl.get_receipt(db_path, receipt_id)
    if receipt is None:
        raise ReceiptEmissionError(
            f"contract_hash {contract_hash} references receipt_id {receipt_id}, "
            f"which does not resolve in governance_receipts (CORRUPTED REFERENCE)"
        )
    hash_ok = verify_receipt(receipt["body"], receipt["receipt_id"])
    replay_result = replay_receipt_body(receipt["body"])
    return ContractReceiptVerification(
        contract_hash=contract_hash, event="retire", receipt_id=receipt_id,
        hash_ok=hash_ok, replay=replay_result,
    )
