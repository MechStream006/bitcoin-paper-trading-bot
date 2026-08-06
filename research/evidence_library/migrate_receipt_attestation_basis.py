"""
One-time schema migration: add attestation_basis/contract_version columns to
a pre-existing governance_receipts table (ADR-I015 §5.3, §6; engineering
roadmap Phase 0 WBS 0.8).

THIS IS A SCHEMA-ONLY MIGRATION. It does NOT classify or backfill values for
any existing receipt, and it never will -- governance_receipts is
unconditionally append-only, enforced at the database layer
(trg_receipts_no_update / trg_receipts_no_delete in receipt_ledger.py). A
plain UPDATE against any existing row -- including one issued by a migration
script -- is refused by the database itself with "governance_receipts is
append-only: UPDATE forbidden". This was discovered empirically while
building this script's first draft, which attempted exactly such an UPDATE
and was correctly refused by the trigger; the classify/apply approach used by
migrate_run_kind.py (a genuinely mutable table) does not and cannot apply
here. This is exactly ADR-I015 RIM2: "No update path exists. Not for
corrections, not for annotations, not for migrations."

CONSEQUENCE: attestation_basis and contract_version are, correctly and
permanently, NULL on every receipt written before this migration ran. This is
not a gap to be closed later -- it is the honest state of the record. Those
26 real receipts were genuinely written before this concept existed, and no
retroactive UPDATE may assert otherwise (the same reasoning already applied
to contract_version in the first draft of this script, now extended to
attestation_basis as well, for the same underlying reason). Only receipts
emitted AFTER this migration can carry a non-NULL attestation_basis or
contract_version, by setting the field at construction (receipts.ReceiptBody)
the same way every other field is set -- never by a later mutation.

WHAT IS TOUCHED:      the table's SCHEMA only (two nullable columns added via
                       ALTER TABLE, if not already present).
WHAT IS NEVER TOUCHED: any existing row, any existing column value, receipt_id,
                       body_json. Zero UPDATE statements exist anywhere in
                       this script.

Usage:
    python migrate_receipt_attestation_basis.py              # dry run: reports
                                                               # whether the
                                                               # columns already
                                                               # exist, writes
                                                               # nothing
    python migrate_receipt_attestation_basis.py --commit      # backs up
                                                               # ledger.db, then
                                                               # adds any
                                                               # missing column

Properties:
    - Idempotent: ALTER TABLE ADD COLUMN only fires for a column that is
      actually absent. Re-running after a successful --commit is a safe
      no-op ("both columns already present").
    - Requires --commit to write anything; the default is always a dry run.
    - Takes a timestamped backup of ledger.db before writing, only when there
      is something to add.
    - Writes an audit note alongside the backup, matching
      migrate_run_kind.py's precedent.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_DB_PATH = _THIS_DIR / "ledger.db"

sys.path.insert(0, str(_THIS_DIR))
import receipt_ledger as rl  # noqa: E402  (needs sys.path set first)

_COLUMNS = ("attestation_basis", "contract_version")


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=_THIS_DIR, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return "unknown"


def missing_columns(db_path: Path) -> list[str]:
    """Pure read: which of _COLUMNS are absent from governance_receipts."""
    con = sqlite3.connect(db_path)
    try:
        existing = {row[1] for row in con.execute("PRAGMA table_info(governance_receipts)")}
    finally:
        con.close()
    return [c for c in _COLUMNS if c not in existing]


def ensure_columns(db_path: Path) -> list[str]:
    """
    ALTER TABLE ADD COLUMN for any column in _COLUMNS that is missing.
    Idempotent: a column already present is left untouched. Returns the list
    of columns actually added (empty if none were needed).

    Deliberately lives HERE and not in receipt_ledger.init_db() -- this
    milestone found that init_db() is reachable from governance_verification.
    verify_governance() / report.py --check's documented "read-only" sweep
    (via evidence_governance.init_db()'s cascade), so an ALTER placed there
    fires the first time that sweep runs against a pre-migration ledger,
    before any migration has been explicitly authorised. Putting the ALTER
    only in this script's own explicit, human-invoked main() keeps schema
    mutation behind exactly the same gate as everything else --commit does.
    """
    to_add = missing_columns(db_path)
    if not to_add:
        return []
    con = sqlite3.connect(db_path)
    try:
        for col in to_add:
            con.execute(f"ALTER TABLE governance_receipts ADD COLUMN {col} TEXT")
        con.commit()
    finally:
        con.close()
    return to_add


def backup(db_path: Path) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = db_path.with_name(f"{db_path.stem}.pre-attestation-basis-migration.{ts}.db")
    shutil.copy2(db_path, backup_path)
    return backup_path


def write_audit_note(backup_path: Path, added: list[str]) -> Path:
    note_path = backup_path.with_suffix(".audit.txt")
    lines = [
        "migrate_receipt_attestation_basis.py audit note",
        f"ts (UTC): {datetime.now(timezone.utc).isoformat()}",
        f"git_sha: {_git_sha()}",
        f"backup: {backup_path.name}",
        f"columns added: {added}",
        "SCHEMA-ONLY migration: no existing row's attestation_basis or "
        "contract_version was set. governance_receipts is unconditionally "
        "append-only (ADR-I015 RIM2) -- no UPDATE is ever issued by this "
        "script. Both columns remain NULL on every pre-existing receipt, "
        "permanently and correctly.",
    ]
    note_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return note_path


def print_report(to_add: list[str]) -> None:
    if not to_add:
        print("Both columns already present. Nothing to do.")
        return
    print(f"Column(s) to add: {to_add}")
    print(
        "This is a SCHEMA-ONLY change. No existing receipt's attestation_basis "
        "or contract_version will be set -- governance_receipts is "
        "unconditionally append-only; this script issues no UPDATE."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--commit", action="store_true", help="Add the missing column(s) (default: dry run)")
    parser.add_argument("--db", default=str(_DB_PATH), help="Path to ledger.db (default: evidence_library/ledger.db)")
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: {db_path} not found.", file=sys.stderr)
        return 1

    rl.init_db(db_path)  # CREATE-only; safe no-op if the table already exists

    to_add = missing_columns(db_path)
    print_report(to_add)

    if not args.commit:
        print("\nDry run only. Re-run with --commit to write.")
        return 0

    if not to_add:
        return 0

    backup_path = backup(db_path)
    print(f"\nBacked up {db_path} -> {backup_path}")
    added = ensure_columns(db_path)
    note_path = write_audit_note(backup_path, added)
    print(f"Audit note -> {note_path}")
    print(f"Added column(s): {added}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
