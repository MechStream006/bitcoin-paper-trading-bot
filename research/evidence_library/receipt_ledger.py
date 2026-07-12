"""
Governance Receipt Foundation — persistence layer (PR-1).

Append-only SQLite store for governance_receipts, per
docs/governance/governance_receipts.md §1.2. Mirrors the existing style of
record.py (function-based, sqlite3 stdlib, no ORM) rather than introducing a
new pattern.

Nothing on the platform calls append_receipt() yet. This PR only makes it
possible to open a ledger, append a receipt, and query/verify the chain.
Wiring any real decision path (family transitions, contract activation,
readiness verdicts, ...) into this ledger is out of scope here.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from receipts import (
    ReceiptBody,
    ReceiptError,
    compute_receipt_id,
    validate_body_or_raise,
    verify_receipt,
)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS governance_receipts (
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
    signature_json    TEXT NOT NULL DEFAULT '[]',
    CHECK (decision_class IN ('derived','discretionary'))
);
CREATE INDEX IF NOT EXISTS ix_receipt_subject
    ON governance_receipts(subject_kind, subject_id, seq);
CREATE INDEX IF NOT EXISTS ix_receipt_type
    ON governance_receipts(decision_type, seq);

-- Append-only, enforced at the database layer (not just by convention).
CREATE TRIGGER IF NOT EXISTS trg_receipts_no_update
BEFORE UPDATE ON governance_receipts
BEGIN
    SELECT RAISE(ABORT, 'governance_receipts is append-only: UPDATE forbidden');
END;

CREATE TRIGGER IF NOT EXISTS trg_receipts_no_delete
BEFORE DELETE ON governance_receipts
BEGIN
    SELECT RAISE(ABORT, 'governance_receipts is append-only: DELETE forbidden');
END;
"""


class DuplicateReceiptError(ReceiptError):
    """Raised when a receipt with an identical receipt_id already exists."""


class ChainIntegrityError(ReceiptError):
    """Raised by append_receipt() if the chain head cannot be established
    consistently (should not happen under single-writer use; defense in
    depth against concurrent writers stomping on each other)."""


@dataclass(frozen=True)
class ChainVerificationResult:
    ok: bool
    n_receipts: int
    errors: list[str]


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
    d = dict(row)
    d["body"] = json.loads(d["body_json"])
    d["signatures"] = json.loads(d["signature_json"])
    return d


def append_receipt(db_path: Path | str, body: ReceiptBody) -> dict:
    """
    Append a receipt to the chain.

    Assigns prev_receipt_hash from the current chain head and seq =
    head.seq + 1 (or prev_receipt_hash=None, seq=1 for the genesis receipt),
    then computes receipt_id over the resulting body. Validates the body
    before writing. Raises DuplicateReceiptError if the resulting
    receipt_id already exists (defense in depth; content-addressing means
    this can only happen for a byte-identical body).

    Returns the persisted row as a dict (including parsed 'body' and
    'signatures').
    """
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        con.execute("BEGIN IMMEDIATE")
        cur = con.execute(
            "SELECT receipt_id, seq FROM governance_receipts ORDER BY seq DESC LIMIT 1"
        )
        head = cur.fetchone()
        prev_hash = head["receipt_id"] if head else None
        next_seq = (head["seq"] if head else 0) + 1

        chained_body = replace(body, prev_receipt_hash=prev_hash)
        validate_body_or_raise(chained_body)
        body_dict = chained_body.to_dict()
        receipt_id = compute_receipt_id(body_dict)

        subject = body_dict["subject"]
        authority_hash = body_dict["authority"]["policy_hash"]
        rule_hash = body_dict["rule"]["rule_hash"]
        engine_version = body_dict["rule"]["engine_version"]

        try:
            con.execute(
                """
                INSERT INTO governance_receipts (
                    receipt_id, seq, prev_receipt_hash, schema_version,
                    decision_type, decision_class, action,
                    subject_kind, subject_id, subject_version,
                    from_state, to_state, as_of, decided_at,
                    actor_type, actor_id, authority_hash, rule_hash,
                    engine_version, body_json, signature_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    receipt_id, next_seq, prev_hash, body_dict["schema_version"],
                    body_dict["decision_type"], body_dict["decision_class"], body_dict["action"],
                    subject["kind"], subject["id"], subject["version_or_hash"],
                    body_dict["from_state"], body_dict["to_state"],
                    body_dict["as_of"], body_dict["decided_at"],
                    body_dict["actor"]["type"], body_dict["actor"]["id"],
                    authority_hash, rule_hash, engine_version,
                    json.dumps(body_dict, sort_keys=True), "[]",
                ),
            )
        except sqlite3.IntegrityError as exc:
            con.rollback()
            raise DuplicateReceiptError(
                f"receipt_id {receipt_id} already exists in the ledger "
                f"(byte-identical body was submitted twice)"
            ) from exc

        con.commit()
    finally:
        con.close()

    return get_receipt(db_path, receipt_id)


def get_receipt(db_path: Path | str, receipt_id: str) -> dict | None:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute(
            "SELECT * FROM governance_receipts WHERE receipt_id = ?", (receipt_id,)
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row is not None else None
    finally:
        con.close()


def get_by_seq(db_path: Path | str, seq: int) -> dict | None:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute("SELECT * FROM governance_receipts WHERE seq = ?", (seq,))
        row = cur.fetchone()
        return _row_to_dict(row) if row is not None else None
    finally:
        con.close()


def get_chain_head(db_path: Path | str) -> dict | None:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute(
            "SELECT * FROM governance_receipts ORDER BY seq DESC LIMIT 1"
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row is not None else None
    finally:
        con.close()


def get_all_receipts(db_path: Path | str) -> list[dict]:
    if not Path(db_path).exists():
        return []
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute("SELECT * FROM governance_receipts ORDER BY seq")
        return [_row_to_dict(r) for r in cur.fetchall()]
    finally:
        con.close()


def get_receipts_by_subject(db_path: Path | str, subject_kind: str, subject_id: str) -> list[dict]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute(
            """
            SELECT * FROM governance_receipts
            WHERE subject_kind = ? AND subject_id = ?
            ORDER BY seq
            """,
            (subject_kind, subject_id),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]
    finally:
        con.close()


def get_receipts_by_type(db_path: Path | str, decision_type: str) -> list[dict]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute(
            "SELECT * FROM governance_receipts WHERE decision_type = ? ORDER BY seq",
            (decision_type,),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]
    finally:
        con.close()


def verify_chain(db_path: Path | str) -> ChainVerificationResult:
    """
    Walk the entire chain in seq order and check:
      1. receipt_id recomputes from the stored body (detects tampering with
         body_json / any indexed column drifting from it).
      2. seq is strictly monotonic starting at 1 with no gaps.
      3. prev_receipt_hash links to the previous receipt's receipt_id (NULL
         only for seq=1, the genesis receipt).

    This is the --verify-chain primitive from governance_receipts.md §7.
    """
    rows = get_all_receipts(db_path)
    errors: list[str] = []
    prev_id: str | None = None

    for i, row in enumerate(rows):
        expected_seq = i + 1
        if row["seq"] != expected_seq:
            errors.append(f"seq gap/out-of-order at position {i}: expected {expected_seq}, got {row['seq']}")

        if not verify_receipt(row["body"], row["receipt_id"]):
            errors.append(f"receipt_id mismatch at seq={row['seq']}: stored body does not hash to stored receipt_id (TAMPERING)")

        if row["prev_receipt_hash"] != prev_id:
            errors.append(
                f"chain break at seq={row['seq']}: prev_receipt_hash={row['prev_receipt_hash']!r} "
                f"does not match previous receipt's receipt_id={prev_id!r}"
            )

        prev_id = row["receipt_id"]

    return ChainVerificationResult(ok=(len(errors) == 0), n_receipts=len(rows), errors=errors)
