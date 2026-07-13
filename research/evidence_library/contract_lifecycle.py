"""
Contract Lifecycle — activation, retirement, monotonic versioning.

Implements PR-3 of docs/governance/contract_versioning.md: contract
activation and retirement as atomic, lock-serialized transactions on top of
PR-2's contract_registry.py archive.

Explicitly NOT implemented in this PR (deferred to PR-4 per the task spec):
  - governance receipt emission. activate_contract() and retire_contract()
    do not construct, validate, or write a governance_receipts row. The
    only trace of PR-4's eventual involvement is that activate_contract()
    writes activated_receipt_id = NULL -- a placeholder column PR-2 already
    added for exactly this purpose. Nothing here imports receipt_ledger.py.
  - receipt signing, receipt anchoring.
  - the impact report / change-class classifier (PR-C): change_class and
    impact_report_hash are accepted as optional caller-supplied values and
    stored as-is; this module never computes or infers a change class.
  - Evidence Ledger / reporting / Data Completeness Gate / Feature Store /
    ML integration.

Dependency check (per the task's explicit stop condition): activation and
retirement are fully expressible as an atomic SQLite transaction against
contract_registry alone. Neither operation needs a governance_receipts row
to exist, to be constructed, or to be referenced by anything other than a
NULL placeholder column that already exists. Receipt emission is therefore
cleanly separable and this PR does not implement it.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import contract_registry as reg
from contract_registry import (
    ContractNotFoundError,
    ContractRegistryError,
    ContractRecord,
    ContractValidationError,
    DuplicateContractError,
    canonicalize_contract_body,
    compute_contract_hash,
    now_iso,
    validate_record,
)

# Lifecycle arcs (contract_versioning.md §2.1):
#
#    draft --activate--> active --supersede--> superseded --> retired
#      |                   |                                      ^
#      |                   +------------------retire---------------+
#
# Mirrors the ALLOWED_TRANSITIONS / can_transition() pattern already used
# for family lifecycle in evidence_library/lifecycle.py -- same shape,
# same discipline, for the same reason: one place that says what is legal,
# consulted by both validation and the guard trigger's own (independent)
# enforcement in contract_registry.py.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"active"}),
    "active": frozenset({"superseded", "retired"}),
    "superseded": frozenset({"retired"}),
    "retired": frozenset(),
}


class ContractTamperError(ContractRegistryError):
    """
    Raised when an attempted mutation touches a frozen field or performs an
    illegal phase transition. Mirrors (does not replace) the database-level
    CONTRACT_TAMPER guard in contract_registry.trg_contracts_guard_update --
    this exception is raised by Python-level pre-checks in this module,
    and sqlite3.IntegrityError with a 'CONTRACT_TAMPER:' message is raised
    directly by the trigger for anything that bypasses this module.
    """


class InvalidTransitionError(ContractRegistryError):
    """Raised for a lifecycle arc not present in ALLOWED_TRANSITIONS."""


def can_transition(from_phase: str, to_phase: str) -> bool:
    return to_phase in ALLOWED_TRANSITIONS.get(from_phase, frozenset())


def next_version(con: sqlite3.Connection, contract_id: str) -> int:
    """
    Monotonic version assignment (item 3).

    Must be called on a connection that already holds at least a RESERVED
    lock (i.e. after BEGIN IMMEDIATE) so that two concurrent activators for
    the same contract_id are serialized by SQLite and never compute the
    same version number -- the second caller's query only proceeds once the
    first has committed (or rolled back), and therefore sees the first's
    row (or doesn't, if it rolled back).
    """
    row = con.execute(
        "SELECT COALESCE(MAX(version), 0) FROM contract_registry WHERE contract_id = ?",
        (contract_id,),
    ).fetchone()
    return row[0] + 1


def _busy_connect(db_path: Path | str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA busy_timeout = 5000")
    return con


# ---------------------------------------------------------------------------
# Activation (items 1, 3, 4, 6, 7)
# ---------------------------------------------------------------------------

def activate_contract(
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
    Atomically activate a new contract version (item 7).

    Steps, all inside a single SQLite transaction opened with BEGIN
    IMMEDIATE (so concurrent activators for the same contract_id serialize
    rather than race):

      1. validate draft         -- structural validation of the candidate
                                    (contract_registry.validate_record,
                                    reused, not reimplemented)
      2. assign version          -- next_version(), computed under lock
      3. supersede previous active version, if any -- legality checked
                                    against ALLOWED_TRANSITIONS before the
                                    UPDATE is issued
      4. activate new version    -- INSERT with phase='active'
      5. activated_receipt_id stays NULL -- PR-4's placeholder, not set here
      6. commit

    If ANY step fails -- validation, a duplicate contract_hash surfacing at
    INSERT time, an illegal supersede arc, any sqlite3 error -- the entire
    transaction is rolled back. Nothing partially applies: a failed
    activation never leaves a previously-active contract superseded with no
    successor, and never leaves an orphaned new row.

    contract_hash is computed here, from the canonicalized body -- exactly
    as contract_registry.append_contract() does. Never supplied by the
    caller.
    """
    canonical_body = canonicalize_contract_body(body)
    contract_hash = compute_contract_hash(canonical_body)

    # Structural validation happens before any lock is taken (fail fast for
    # a doomed call). `version=1` here is a placeholder for the validator's
    # shape check only -- the real, monotonic version is assigned below,
    # under the transaction's lock, and is never taken from this candidate.
    candidate = ContractRecord(
        contract_id=contract_id,
        version=1,
        contract_schema_version=contract_schema_version,
        body=canonical_body,
        phase="active",
        actor=actor,
        reason=reason,
        change_class=change_class,
        impact_report_hash=impact_report_hash,
    )
    errors = validate_record(candidate)
    if errors:
        raise ContractValidationError("; ".join(errors))

    con = _busy_connect(db_path)
    try:
        con.execute("BEGIN IMMEDIATE")
        try:
            version = next_version(con, contract_id)

            prev_active_row = con.execute(
                "SELECT contract_hash, phase FROM contract_registry "
                "WHERE contract_id = ? AND phase = 'active'",
                (contract_id,),
            ).fetchone()

            if prev_active_row is not None:
                prev_hash, prev_phase = prev_active_row
                if not can_transition(prev_phase, "superseded"):
                    # Structurally unreachable given the partial unique index
                    # (only one row can ever be phase='active'), but checked
                    # explicitly rather than assumed -- lifecycle invariants
                    # (item 6/8) are enforced, not merely hoped for.
                    raise InvalidTransitionError(
                        f"cannot supersede contract_id '{contract_id}': "
                        f"existing active row is in phase '{prev_phase}', not 'active'"
                    )
                con.execute(
                    "UPDATE contract_registry SET phase = 'superseded', superseded_by = ? "
                    "WHERE contract_hash = ?",
                    (contract_hash, prev_hash),
                )

            created_at = now_iso()
            try:
                con.execute(
                    """
                    INSERT INTO contract_registry (
                        contract_hash, contract_id, version, contract_schema_v,
                        body, phase, change_class, impact_report_hash,
                        superseded_by, actor, reason, created_at,
                        activated_at, retired_at, activated_receipt_id
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        contract_hash, contract_id, version, contract_schema_version,
                        canonical_body, "active", change_class, impact_report_hash,
                        None, actor, reason, created_at,
                        created_at, None, None,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise DuplicateContractError(
                    f"contract_hash {contract_hash} already exists in the registry "
                    f"(byte-identical body submitted twice)"
                ) from exc
        except Exception:
            con.rollback()
            raise
        else:
            con.commit()
    finally:
        con.close()

    return reg.get_by_hash(db_path, contract_hash)


# ---------------------------------------------------------------------------
# Retirement (items 2, 5)
# ---------------------------------------------------------------------------

def retire_contract(db_path: Path | str, contract_hash: str, actor: str, reason: str) -> dict:
    """
    Retire an active or superseded contract version.

    Validation (item 5): contract_hash must resolve; the current phase must
    have a legal arc to 'retired' (active or superseded -- see
    ALLOWED_TRANSITIONS); actor/reason must be non-empty. Only `phase` and
    `retired_at` change; superseded_by (if already set) is left untouched.

    A retired contract's bytes remain permanently resolvable via
    resolve_by_hash() -- retirement never removes a row, it only advances
    its phase (contract_versioning.md §7: "Bytes persist forever").
    """
    if not actor:
        raise ContractValidationError("actor is required and must be non-empty")
    if not (reason or "").strip():
        raise ContractValidationError("reason is required and must be non-empty")

    con = _busy_connect(db_path)
    try:
        con.execute("BEGIN IMMEDIATE")
        try:
            row = con.execute(
                "SELECT phase FROM contract_registry WHERE contract_hash = ?",
                (contract_hash,),
            ).fetchone()
            if row is None:
                raise ContractNotFoundError(
                    f"contract_hash {contract_hash} not found in registry"
                )
            current_phase = row[0]
            if not can_transition(current_phase, "retired"):
                raise InvalidTransitionError(
                    f"cannot transition '{current_phase}' -> 'retired'; "
                    f"allowed destinations from '{current_phase}': "
                    f"{sorted(ALLOWED_TRANSITIONS.get(current_phase, frozenset()))}"
                )
            retired_at = now_iso()
            con.execute(
                "UPDATE contract_registry SET phase = 'retired', retired_at = ? "
                "WHERE contract_hash = ?",
                (retired_at, contract_hash),
            )
        except Exception:
            con.rollback()
            raise
        else:
            con.commit()
    finally:
        con.close()

    return reg.get_by_hash(db_path, contract_hash)
