"""
Contract Registry — append-only archive of Data Completeness Gate contracts.

Implements PR-2 of docs/governance/contract_versioning.md, scoped down to the
storage layer only (per the PR-2 task): the registry is the immutable,
hash-addressed archive. The filesystem (a `contracts/` working directory) is
NOT read by this module at all -- there is no file-reading code path here,
which is what makes "replay never reads the filesystem" trivially true
rather than merely a convention.

Explicitly NOT implemented in this PR (deferred to later PRs per
contract_versioning.md §11):
  - contract activation (hash computation from a draft file, monotonic
    version stamping, atomic supersede-the-incumbent, tamper detection/HALT)
  - contract retirement business rules (superseded_by requirements,
    frozen-feature semantics, pinned_run retirement blocking)
  - governance receipt emission (contract.activate / contract.retire)
  - the impact report / change-class classifier (PR-C)
  - engine/check versioning (PR-B)
  - reprocessing policy (PR-D)
  - Evidence Ledger / Feature Store / Dataset wiring (PR-E)

This module only makes it possible to durably, immutably store a contract's
frozen bytes and query them back by hash, version, or contract_id. Nothing
in the platform calls append_contract() yet.

Hashing: reuses receipts.sha256_lf / receipts.canonical_json verbatim (PR-1)
-- itself byte-identical to forward_evidence/pre_reg_guard.py's
_sha256_lf() convention. One hashing rule on this platform, not two.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from receipts import ReceiptError, canonical_json, sha256_lf

# phase vocabulary. 'draft' is included (beyond contract_versioning.md's
# original 3-value DDL comment of active|superseded|retired) so this PR's
# pure-archive registry can hold a contract at any point in its declared
# lifecycle without requiring the activation orchestration that is out of
# scope here. Nothing in this module transitions a row between phases --
# every row is written once, by the caller, with whatever phase it declares.
PHASES = frozenset({"draft", "active", "superseded", "retired"})

CHANGE_CLASSES = frozenset({"editorial", "relaxing", "tightening", "corrective"})


class ContractRegistryError(ReceiptError):
    """Base class for contract registry failures."""


class ContractValidationError(ContractRegistryError):
    """Raised for a malformed contract record."""


class DuplicateContractError(ContractRegistryError):
    """Raised when a contract with an identical contract_hash already exists."""


class ContractNotFoundError(ContractRegistryError):
    """
    Raised by resolve_by_hash() when a hash does not resolve in the registry.

    This is a hard failure, not a fallback. Replay must never substitute a
    filesystem read for a hash that fails to resolve.
    """


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS contract_registry (
    contract_hash      TEXT PRIMARY KEY,   -- sha256(LF-normalised canonical bytes) -- THE identity
    contract_id        TEXT NOT NULL,      -- stable human identifier ("contract type"), e.g. "corpus_daily"
    version            INTEGER NOT NULL,   -- human label. NOT auto-assigned in this PR (no activation tool yet).
    contract_schema_v  INTEGER NOT NULL,   -- version of the CONTRACT FORMAT itself
    body               TEXT NOT NULL,      -- full frozen canonical text -- this IS the archive
    phase              TEXT NOT NULL,      -- draft | active | superseded | retired
    change_class       TEXT,               -- editorial|relaxing|tightening|corrective; NULL pre-PR-C
    impact_report_hash TEXT,               -- NULL until PR-C computes one
    superseded_by      TEXT REFERENCES contract_registry(contract_hash),
    actor              TEXT NOT NULL,
    reason             TEXT NOT NULL,
    created_at         TEXT NOT NULL,      -- when this row was appended to the registry
    activated_at       TEXT,               -- activation placeholder -- NULL until a later PR implements activate()
    retired_at         TEXT,               -- retirement placeholder -- NULL until a later PR implements retire()
    CHECK (phase IN ('draft','active','superseded','retired')),
    CHECK (change_class IS NULL OR change_class IN ('editorial','relaxing','tightening','corrective')),
    UNIQUE (contract_id, version)
);

-- contract_versioning.md §2.1: exactly one active version per contract_id.
-- Enforced by the database, not in Python.
CREATE UNIQUE INDEX IF NOT EXISTS ux_one_active_per_contract
    ON contract_registry(contract_id) WHERE phase = 'active';

CREATE INDEX IF NOT EXISTS ix_contract_id ON contract_registry(contract_id, version);
CREATE INDEX IF NOT EXISTS ix_contract_phase ON contract_registry(contract_id, phase);

-- Append-only. No exception is carved out yet for phase mutation (the one
-- exception contract_versioning.md §11 documents for the eventual
-- activate()/supersede()/retire() operations) because those operations do
-- not exist in this PR. A later PR that implements them must replace this
-- blanket block with the documented "phase only advances" CHECK-guarded
-- UPDATE path -- do not silently loosen this trigger without that change
-- landing alongside it.
CREATE TRIGGER IF NOT EXISTS trg_contracts_no_update
BEFORE UPDATE ON contract_registry
BEGIN
    SELECT RAISE(ABORT, 'contract_registry is append-only: UPDATE forbidden (no phase-mutation exception exists yet)');
END;

CREATE TRIGGER IF NOT EXISTS trg_contracts_no_delete
BEFORE DELETE ON contract_registry
BEGIN
    SELECT RAISE(ABORT, 'contract_registry is append-only: DELETE forbidden');
END;
"""


@dataclass(frozen=True)
class ContractRecord:
    """The contract metadata model (docs/governance/contract_versioning.md §2.2, §11 PR-A)."""
    contract_id: str
    version: int
    contract_schema_version: int
    body: str
    phase: str
    actor: str
    reason: str
    change_class: str | None = None
    impact_report_hash: str | None = None
    superseded_by: str | None = None
    created_at: str | None = None
    activated_at: str | None = None
    retired_at: str | None = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Canonical serialization / hashing (items 4-5: reuse receipts.py verbatim)
# ---------------------------------------------------------------------------

def canonicalize_contract_body(body: str | dict) -> str:
    """
    Produce the canonical text form of a contract body.

    - str input (e.g. the literal text of a working-copy YAML file) is
      accepted as-is -- it is already the source-of-truth bytes.
    - dict input (a contract expressed as structured metadata) is passed
      through receipts.canonical_json() -- sorted keys, no whitespace,
      floats forbidden -- so two callers building the same contract
      programmatically always converge on identical bytes.
    """
    if isinstance(body, str):
        return body
    if isinstance(body, dict):
        return canonical_json(body).decode("utf-8")
    raise ContractValidationError(f"contract body must be str or dict, got {type(body)!r}")


def compute_contract_hash(body: str | dict) -> str:
    """contract_hash = sha256_lf(canonical body bytes). Reuses receipts.sha256_lf verbatim."""
    canonical_text = canonicalize_contract_body(body)
    return sha256_lf(canonical_text.encode("utf-8"))


def verify_contract_hash(record: dict) -> bool:
    """
    Recompute contract_hash from the stored body and compare. The
    hash-verification counterpart to receipts.verify_receipt() -- needs
    nothing but the record itself.
    """
    return compute_contract_hash(record["body"]) == record["contract_hash"]


# ---------------------------------------------------------------------------
# Validation (item 8)
# ---------------------------------------------------------------------------

def validate_record(record: ContractRecord) -> list[str]:
    """Structural validation. Returns a list of errors; empty means valid."""
    errors: list[str] = []

    if not record.contract_id:
        errors.append("contract_id is required and must be non-empty")
    if not isinstance(record.version, int) or record.version < 0:
        errors.append(f"version must be a non-negative int, got {record.version!r}")
    if not isinstance(record.contract_schema_version, int) or record.contract_schema_version < 1:
        errors.append(f"contract_schema_version must be a positive int, got {record.contract_schema_version!r}")
    if not (record.body or "").strip():
        errors.append("body is required and must be non-empty")
    if record.phase not in PHASES:
        errors.append(f"phase must be one of {sorted(PHASES)}, got '{record.phase}'")
    if record.change_class is not None and record.change_class not in CHANGE_CLASSES:
        errors.append(f"change_class, if set, must be one of {sorted(CHANGE_CLASSES)}, got '{record.change_class}'")
    if not record.actor:
        errors.append("actor is required and must be non-empty")
    if not (record.reason or "").strip():
        errors.append("reason is required and must be non-empty")

    # Canonical-serialization / malformed-contract defense in depth: confirm
    # the body is actually hashable (rejects e.g. a dict body containing a
    # raw float) before it is ever handed to the registry.
    try:
        compute_contract_hash(record.body)
    except ContractValidationError as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001 -- surfaced as a validation error, not a crash
        errors.append(f"body does not canonicalize/hash: {exc}")

    return errors


def validate_record_or_raise(record: ContractRecord) -> None:
    errors = validate_record(record)
    if errors:
        raise ContractValidationError("; ".join(errors))


# ---------------------------------------------------------------------------
# Persistence (items 1-3)
# ---------------------------------------------------------------------------

def init_db(path: Path | str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    try:
        con.executescript(_SCHEMA_SQL)
        con.commit()
    finally:
        con.close()


def _row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)


def append_contract(db_path: Path | str, record: ContractRecord) -> dict:
    """
    Append a contract to the registry.

    contract_hash is computed here, from the canonicalized body -- callers
    never supply it. A byte-identical body submitted twice collides on the
    primary key and raises DuplicateContractError (content-addressing
    working as designed, not an error condition to work around).

    Does not assign `version` -- the caller supplies it. Automatic monotonic
    version stamping is the activation tool's job (contract_versioning.md
    §2.2), which is out of scope for this PR.
    """
    validate_record_or_raise(record)

    canonical_body = canonicalize_contract_body(record.body)
    contract_hash = sha256_lf(canonical_body.encode("utf-8"))
    created_at = record.created_at or now_iso()

    con = sqlite3.connect(db_path)
    try:
        # Proactive duplicate-hash check, ahead of the INSERT: when a
        # byte-identical body collides AND the (contract_id, version) unique
        # constraint would also collide, SQLite reports only one of the two
        # violated constraints (implementation-dependent which), which makes
        # message-sniffing the IntegrityError unreliable for this case.
        # Checking first makes the duplicate-hash outcome deterministic.
        existing = con.execute(
            "SELECT 1 FROM contract_registry WHERE contract_hash = ?", (contract_hash,)
        ).fetchone()
        if existing is not None:
            raise DuplicateContractError(
                f"contract_hash {contract_hash} already exists in the registry "
                f"(byte-identical body submitted twice)"
            )

        try:
            con.execute(
                """
                INSERT INTO contract_registry (
                    contract_hash, contract_id, version, contract_schema_v,
                    body, phase, change_class, impact_report_hash,
                    superseded_by, actor, reason, created_at, activated_at, retired_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    contract_hash, record.contract_id, record.version,
                    record.contract_schema_version, canonical_body, record.phase,
                    record.change_class, record.impact_report_hash,
                    record.superseded_by, record.actor, record.reason,
                    created_at, record.activated_at, record.retired_at,
                ),
            )
        except sqlite3.IntegrityError as exc:
            con.rollback()
            msg = str(exc)
            if "contract_hash" in msg or "PRIMARY KEY" in msg or "UNIQUE constraint failed: contract_registry.contract_hash" in msg:
                raise DuplicateContractError(
                    f"contract_hash {contract_hash} already exists in the registry "
                    f"(byte-identical body submitted twice)"
                ) from exc
            if "ux_one_active_per_contract" in msg:
                raise ContractRegistryError(
                    f"contract_id '{record.contract_id}' already has an active version; "
                    f"exactly one active version per contract_id is enforced by the database"
                ) from exc
            if "contract_id, version" in msg or "UNIQUE constraint failed: contract_registry.contract_id" in msg:
                raise ContractRegistryError(
                    f"contract_id '{record.contract_id}' version {record.version} already registered"
                ) from exc
            raise ContractRegistryError(msg) from exc

        con.commit()
    finally:
        con.close()

    return get_by_hash(db_path, contract_hash)


# ---------------------------------------------------------------------------
# Registry querying (item 7)
# ---------------------------------------------------------------------------

def get_by_hash(db_path: Path | str, contract_hash: str) -> dict | None:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute(
            "SELECT * FROM contract_registry WHERE contract_hash = ?", (contract_hash,)
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row is not None else None
    finally:
        con.close()


def get_by_version(db_path: Path | str, contract_id: str, version: int) -> dict | None:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute(
            "SELECT * FROM contract_registry WHERE contract_id = ? AND version = ?",
            (contract_id, version),
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row is not None else None
    finally:
        con.close()


def get_by_contract_id(db_path: Path | str, contract_id: str) -> list[dict]:
    """Every version ever registered for this contract_id ('lookup by contract type')."""
    if not Path(db_path).exists():
        return []
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute(
            "SELECT * FROM contract_registry WHERE contract_id = ? ORDER BY version",
            (contract_id,),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]
    finally:
        con.close()


def get_active(db_path: Path | str, contract_id: str) -> dict | None:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute(
            "SELECT * FROM contract_registry WHERE contract_id = ? AND phase = 'active'",
            (contract_id,),
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row is not None else None
    finally:
        con.close()


def get_all_activated(db_path: Path | str, contract_id: str) -> list[dict]:
    """
    Every version of contract_id that has ever been active, superseded, or
    retired -- i.e. every "immutable activated version" -- ordered by
    version. Excludes drafts (never activated, not yet dereferenceable).
    """
    if not Path(db_path).exists():
        return []
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute(
            """
            SELECT * FROM contract_registry
            WHERE contract_id = ? AND phase IN ('active','superseded','retired')
            ORDER BY version
            """,
            (contract_id,),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]
    finally:
        con.close()


def get_latest_draft(db_path: Path | str, contract_id: str) -> dict | None:
    """Highest-version draft-phase row for contract_id, if any."""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute(
            """
            SELECT * FROM contract_registry
            WHERE contract_id = ? AND phase = 'draft'
            ORDER BY version DESC LIMIT 1
            """,
            (contract_id,),
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row is not None else None
    finally:
        con.close()


def get_all_contracts(db_path: Path | str) -> list[dict]:
    if not Path(db_path).exists():
        return []
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute("SELECT * FROM contract_registry ORDER BY contract_id, version")
        return [_row_to_dict(r) for r in cur.fetchall()]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Registry replay support (item 9)
#
# resolve_by_hash() is the ONLY function replay may use to obtain a
# contract's bytes. There is no file-reading code anywhere in this module --
# "replay never reads the filesystem" is therefore not a convention that
# could be violated by a stray filesystem-reading call, it is a structural
# property of what this file imports and does.
# ---------------------------------------------------------------------------

def resolve_by_hash(db_path: Path | str, contract_hash: str) -> dict:
    """
    Dereference a contract_hash to its frozen bytes and metadata. Raises
    ContractNotFoundError if the hash does not resolve -- a hard failure,
    never a fallback to reading a working-copy file.
    """
    record = get_by_hash(db_path, contract_hash)
    if record is None:
        raise ContractNotFoundError(
            f"contract_hash {contract_hash} does not resolve in the registry. "
            f"Replay never falls back to the filesystem."
        )
    return record
