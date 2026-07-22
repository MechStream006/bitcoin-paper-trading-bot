"""
Contract Registry — append-only archive of Data Completeness Gate contracts.

Implements PR-2 of docs/governance/contract_versioning.md, scoped down to the
storage layer only (per the PR-2 task): the registry is the immutable,
hash-addressed archive. The filesystem (a `contracts/` working directory) is
NOT read by this module at all -- there is no file-reading code path here,
which is what makes "replay never reads the filesystem" trivially true
rather than merely a convention.

PR-3 (contract_lifecycle.py) builds directly on this module: activation,
retirement, monotonic version assignment, and tamper detection live there,
not here. This file's schema changed for PR-3 in exactly the way its own
PR-2 comments said it would have to: the blanket "no UPDATE at all" trigger
is replaced with a guarded one that permits ONLY the documented lifecycle
fields (phase, superseded_by, activated_at, retired_at,
activated_receipt_id) to change, each write-once except phase (which may
only advance along the allowed lifecycle arcs) -- see trg_contracts_guard_update
below. Every other column remains fully frozen post-insert; an attempt to
change one raises CONTRACT_TAMPER.

PR-4 (contract_receipts.py) wires governance receipt emission into
activation/retirement and, correspondingly, this file's schema grew again:
retired_receipt_id (mirrors activated_receipt_id for the retirement event)
and activation_receipt_status / retirement_receipt_status (an explicit
enum -- PENDING_RECEIPT | RECORDED | LEGACY_IMPORT | BOOTSTRAP -- replacing
the bare-NULL ambiguity PR-3's own review flagged: is a NULL
*_receipt_id a legacy row, a pending write, a migration artifact, or a
defect?). See RECEIPT_STATUSES and the CHECK constraints below.

Still explicitly NOT implemented (deferred to later PRs per
contract_versioning.md §11):
  - the impact report / change-class classifier (PR-C)
  - engine/check versioning (PR-B)
  - reprocessing policy (PR-D)
  - Evidence Ledger / Feature Store / Dataset wiring (PR-E)

This module makes it possible to durably, immutably store a contract's
frozen bytes, query them back by hash/version/contract_id, and (as of PR-3,
via contract_lifecycle.py) legitimately advance a row's lifecycle metadata
under the guarded trigger below. It still never reads a contract from the
filesystem.

Hashing: reuses receipts.sha256_lf / receipts.canonical_json verbatim (PR-1)
-- itself byte-identical to forward_evidence/pre_reg_guard.py's
_sha256_lf() convention. One hashing rule on this platform, not two.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, replace
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


RECEIPT_STATUSES = frozenset({"PENDING_RECEIPT", "RECORDED", "LEGACY_IMPORT", "BOOTSTRAP"})


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
    activated_at       TEXT,               -- set at INSERT time by contract_lifecycle.activate_contract()
    retired_at         TEXT,               -- set by contract_lifecycle.retire_contract(); NULL until retired
    activated_receipt_id TEXT,             -- ref into governance_receipts (contract.activate); see receipt_status below
    retired_receipt_id   TEXT,             -- ref into governance_receipts (contract.retire); PR-4
    -- PR-4 item 8: explicit receipt lifecycle status -- replaces bare NULL
    -- ambiguity on *_receipt_id. See contract_receipts.py / trg_contracts_guard_update.
    activation_receipt_status TEXT,        -- NULL only for phase='draft' (never activated)
    retirement_receipt_status TEXT,        -- NULL unless phase='retired'
    CHECK (phase IN ('draft','active','superseded','retired')),
    CHECK (change_class IS NULL OR change_class IN ('editorial','relaxing','tightening','corrective')),
    CHECK (activation_receipt_status IS NULL OR activation_receipt_status IN
        ('PENDING_RECEIPT','RECORDED','LEGACY_IMPORT','BOOTSTRAP')),
    CHECK (retirement_receipt_status IS NULL OR retirement_receipt_status IN
        ('PENDING_RECEIPT','RECORDED','LEGACY_IMPORT','BOOTSTRAP')),
    CHECK (phase = 'draft' OR activation_receipt_status IS NOT NULL),
    CHECK (phase != 'retired' OR retirement_receipt_status IS NOT NULL),
    CHECK (activation_receipt_status != 'RECORDED' OR activated_receipt_id IS NOT NULL),
    CHECK (activation_receipt_status != 'LEGACY_IMPORT' OR activated_receipt_id IS NULL),
    CHECK (retirement_receipt_status != 'RECORDED' OR retired_receipt_id IS NOT NULL),
    CHECK (retirement_receipt_status != 'LEGACY_IMPORT' OR retired_receipt_id IS NULL),
    UNIQUE (contract_id, version)
);

-- contract_versioning.md §2.1: exactly one active version per contract_id.
-- Enforced by the database, not in Python.
CREATE UNIQUE INDEX IF NOT EXISTS ux_one_active_per_contract
    ON contract_registry(contract_id) WHERE phase = 'active';

CREATE INDEX IF NOT EXISTS ix_contract_id ON contract_registry(contract_id, version);
CREATE INDEX IF NOT EXISTS ix_contract_phase ON contract_registry(contract_id, phase);

CREATE TRIGGER IF NOT EXISTS trg_contracts_no_delete
BEFORE DELETE ON contract_registry
BEGIN
    SELECT RAISE(ABORT, 'contract_registry is append-only: DELETE forbidden');
END;
"""

# PR-3: the guarded UPDATE trigger. Introduced exactly as PR-2's own comments
# said a later PR would have to: it replaces the blanket "no UPDATE at all"
# trigger (trg_contracts_no_update, dropped by init_db() below for any
# pre-existing database) with one that permits ONLY the documented lifecycle
# metadata fields to change, and only along legal arcs. Any other column
# changing, or an illegal phase transition, or a second write to a
# write-once lineage field, raises CONTRACT_TAMPER.
#
# Created separately from _SCHEMA_SQL (rather than folded into it) so
# init_db() can DROP the old trigger and CREATE this one in a well-defined
# order for both fresh and pre-existing databases.
#
# RAISE() error-message arguments below are static string literals only --
# no `||` concatenation of OLD./NEW. values. Older SQLite (observed: the
# version bundled with GitHub Actions' hosted Python 3.10 image) rejects
# concatenation there with "near '||': syntax error", even though newer
# SQLite (3.51.x, this dev machine) accepts it. The dynamic old/new values
# aren't lost: contract_lifecycle.py's Python-level pre-checks (which run
# first on every call through this module) raise the detailed message; this
# trigger is only the last-resort backstop for a write that bypasses Python
# entirely, where a static 'CONTRACT_TAMPER: ...' prefix is all any caller
# or test asserts on.
_GUARD_TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS trg_contracts_guard_update
BEFORE UPDATE ON contract_registry
BEGIN
    SELECT CASE
        WHEN NEW.contract_hash      != OLD.contract_hash
          OR NEW.contract_id        != OLD.contract_id
          OR NEW.version            != OLD.version
          OR NEW.contract_schema_v  != OLD.contract_schema_v
          OR NEW.body               != OLD.body
          OR NEW.change_class       IS NOT OLD.change_class
          OR NEW.impact_report_hash IS NOT OLD.impact_report_hash
          OR NEW.actor              != OLD.actor
          OR NEW.reason             != OLD.reason
          OR NEW.created_at         != OLD.created_at
        THEN RAISE(ABORT, 'CONTRACT_TAMPER: immutable field changed')
    END;
    SELECT CASE
        WHEN OLD.superseded_by IS NOT NULL AND NEW.superseded_by IS NOT OLD.superseded_by
        THEN RAISE(ABORT, 'CONTRACT_TAMPER: superseded_by is write-once')
    END;
    SELECT CASE
        WHEN OLD.activated_receipt_id IS NOT NULL AND NEW.activated_receipt_id IS NOT OLD.activated_receipt_id
        THEN RAISE(ABORT, 'CONTRACT_TAMPER: activated_receipt_id is write-once')
    END;
    SELECT CASE
        WHEN OLD.retired_receipt_id IS NOT NULL AND NEW.retired_receipt_id IS NOT OLD.retired_receipt_id
        THEN RAISE(ABORT, 'CONTRACT_TAMPER: retired_receipt_id is write-once')
    END;
    -- activation_receipt_status: NULL -> anything (first write, e.g. the
    -- LEGACY_IMPORT migration backfill), or PENDING_RECEIPT -> RECORDED
    -- (item 3/7: "store receipt reference", the second half of the atomic
    -- activate-with-receipt transaction attaching a just-persisted
    -- receipt_id to a row inserted moments earlier in the same transaction).
    -- Every other transition -- including RECORDED/LEGACY_IMPORT/BOOTSTRAP
    -- changing to anything else -- is tamper.
    SELECT CASE
        WHEN NEW.activation_receipt_status IS NOT OLD.activation_receipt_status
         AND NOT (
              OLD.activation_receipt_status IS NULL
           OR (OLD.activation_receipt_status = 'PENDING_RECEIPT' AND NEW.activation_receipt_status = 'RECORDED')
         )
        THEN RAISE(ABORT, 'CONTRACT_TAMPER: illegal activation_receipt_status transition')
    END;
    SELECT CASE
        WHEN NEW.retirement_receipt_status IS NOT OLD.retirement_receipt_status
         AND NOT (
              OLD.retirement_receipt_status IS NULL
           OR (OLD.retirement_receipt_status = 'PENDING_RECEIPT' AND NEW.retirement_receipt_status = 'RECORDED')
         )
        THEN RAISE(ABORT, 'CONTRACT_TAMPER: illegal retirement_receipt_status transition')
    END;
    SELECT CASE
        WHEN OLD.retired_at IS NOT NULL AND NEW.retired_at IS NOT OLD.retired_at
        THEN RAISE(ABORT, 'CONTRACT_TAMPER: retired_at is write-once')
    END;
    SELECT CASE
        WHEN NEW.phase != OLD.phase AND NOT (
             (OLD.phase = 'draft'      AND NEW.phase = 'active')
          OR (OLD.phase = 'active'     AND NEW.phase IN ('superseded','retired'))
          OR (OLD.phase = 'superseded' AND NEW.phase = 'retired')
        )
        THEN RAISE(ABORT, 'CONTRACT_TAMPER: illegal phase transition')
    END;
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
    activated_receipt_id: str | None = None
    retired_receipt_id: str | None = None
    activation_receipt_status: str | None = None
    retirement_receipt_status: str | None = None


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

    # PR-4 item 8: receipt lifecycle status. Mirrors the DB CHECK constraints
    # exactly, so a malformed record fails here with a clear message rather
    # than a bare sqlite3.IntegrityError deep in append_contract().
    if record.activation_receipt_status is not None and record.activation_receipt_status not in RECEIPT_STATUSES:
        errors.append(
            f"activation_receipt_status, if set, must be one of {sorted(RECEIPT_STATUSES)}, "
            f"got '{record.activation_receipt_status}'"
        )
    if record.retirement_receipt_status is not None and record.retirement_receipt_status not in RECEIPT_STATUSES:
        errors.append(
            f"retirement_receipt_status, if set, must be one of {sorted(RECEIPT_STATUSES)}, "
            f"got '{record.retirement_receipt_status}'"
        )
    if record.phase != "draft" and record.activation_receipt_status is None:
        errors.append(
            f"activation_receipt_status is required for phase '{record.phase}' "
            f"(only 'draft' may leave it NULL — never rely on bare NULL ambiguity)"
        )
    if record.phase == "retired" and record.retirement_receipt_status is None:
        errors.append("retirement_receipt_status is required for phase 'retired'")
    if record.activation_receipt_status == "RECORDED" and record.activated_receipt_id is None:
        errors.append("activation_receipt_status='RECORDED' requires a non-NULL activated_receipt_id")
    if record.activation_receipt_status == "LEGACY_IMPORT" and record.activated_receipt_id is not None:
        errors.append("activation_receipt_status='LEGACY_IMPORT' requires activated_receipt_id to be NULL")
    if record.retirement_receipt_status == "RECORDED" and record.retired_receipt_id is None:
        errors.append("retirement_receipt_status='RECORDED' requires a non-NULL retired_receipt_id")
    if record.retirement_receipt_status == "LEGACY_IMPORT" and record.retired_receipt_id is not None:
        errors.append("retirement_receipt_status='LEGACY_IMPORT' requires retired_receipt_id to be NULL")

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
    """
    Idempotent. Safe to call against a fresh path or a pre-existing PR-2/PR-3
    database: migrates in any missing columns (activated_receipt_id from
    PR-3; retired_receipt_id / activation_receipt_status /
    retirement_receipt_status from PR-4), backfills receipt-status on
    pre-existing rows so nothing is ever left with the bare-NULL ambiguity
    item 8 exists to eliminate, drops PR-2's blanket update-blocking trigger
    if present, and installs the current guarded trigger.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    try:
        con.executescript(_SCHEMA_SQL)

        columns = {row[1] for row in con.execute("PRAGMA table_info(contract_registry)")}

        if "activated_receipt_id" not in columns:
            con.execute("ALTER TABLE contract_registry ADD COLUMN activated_receipt_id TEXT")

        if "retired_receipt_id" not in columns:
            con.execute("ALTER TABLE contract_registry ADD COLUMN retired_receipt_id TEXT")

        if "activation_receipt_status" not in columns:
            # SQLite validates a CHECK added via ALTER TABLE ADD COLUMN
            # against EVERY existing row immediately -- confirmed empirically
            # -- so a cross-column CHECK here (e.g. referencing `phase`)
            # would fail at ALTER time for any pre-existing non-draft row,
            # before the backfill below ever runs. Only a single-column CHECK
            # (satisfied trivially by NULL) is safe to add this way. The
            # cross-column invariants ARE still enforced -- in _SCHEMA_SQL
            # for any freshly-created table (which starts empty, so the
            # ordering problem doesn't arise), and in Python by
            # validate_record() for every write through append_contract(),
            # regardless of which schema path the database took.
            con.execute(
                "ALTER TABLE contract_registry ADD COLUMN activation_receipt_status TEXT "
                "CHECK (activation_receipt_status IS NULL OR activation_receipt_status IN "
                "('PENDING_RECEIPT','RECORDED','LEGACY_IMPORT','BOOTSTRAP'))"
            )
            # Pre-existing rows predate this column entirely -- they are, by
            # definition, LEGACY_IMPORT (never RECORDED, since no receipt was
            # ever emitted for them; the migration itself is not a governance
            # decision, just a historical fact about when this column arrived).
            con.execute(
                "UPDATE contract_registry SET activation_receipt_status = 'LEGACY_IMPORT' "
                "WHERE activation_receipt_status IS NULL AND phase != 'draft'"
            )

        if "retirement_receipt_status" not in columns:
            con.execute(
                "ALTER TABLE contract_registry ADD COLUMN retirement_receipt_status TEXT "
                "CHECK (retirement_receipt_status IS NULL OR retirement_receipt_status IN "
                "('PENDING_RECEIPT','RECORDED','LEGACY_IMPORT','BOOTSTRAP'))"
            )
            con.execute(
                "UPDATE contract_registry SET retirement_receipt_status = 'LEGACY_IMPORT' "
                "WHERE retirement_receipt_status IS NULL AND phase = 'retired'"
            )

        con.execute("DROP TRIGGER IF EXISTS trg_contracts_no_update")
        con.executescript(_GUARD_TRIGGER_SQL)

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

    receipt-status defaulting (PR-4 item 8): this is the raw, low-level
    archive primitive -- it has no lifecycle-orchestration or
    receipt-emission guarantee attached, so a caller who doesn't specify
    activation_receipt_status / retirement_receipt_status explicitly gets
    'PENDING_RECEIPT' (never 'LEGACY_IMPORT' -- that value is reserved
    exclusively for init_db()'s one-time schema-migration backfill of rows
    that predate these columns; it is never chosen by a live code path).
    This keeps every PR-2/PR-3 caller of append_contract() working
    unchanged -- they get an honest, non-committal status rather than a
    validation error for a field they don't know exists.
    """
    if record.phase != "draft" and record.activation_receipt_status is None:
        record = replace(record, activation_receipt_status="PENDING_RECEIPT")
    if record.phase == "retired" and record.retirement_receipt_status is None:
        record = replace(record, retirement_receipt_status="PENDING_RECEIPT")

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
                    superseded_by, actor, reason, created_at, activated_at, retired_at,
                    activated_receipt_id, retired_receipt_id,
                    activation_receipt_status, retirement_receipt_status
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    contract_hash, record.contract_id, record.version,
                    record.contract_schema_version, canonical_body, record.phase,
                    record.change_class, record.impact_report_hash,
                    record.superseded_by, record.actor, record.reason,
                    created_at, record.activated_at, record.retired_at,
                    record.activated_receipt_id, record.retired_receipt_id,
                    record.activation_receipt_status, record.retirement_receipt_status,
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
