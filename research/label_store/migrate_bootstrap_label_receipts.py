"""
One-time backfill: bootstrap-attest a `label.register` governance receipt
for every `label_specifications` row that predates the receipt substrate
reaching the Label Store (docs/governance/governance_receipts.md §6,
"bootstrap honesty"; Phase 3 Label Store roadmap v1.1.0, WBS 3.8c --
`LS-GAP-001` closure, `ADR-I011` LG1 and §9's "registration receipt"
Specification-metadata requirement).

WHAT IS TOUCHED:       a new row per pre-existing Label Specification in
                        `label_store.db`'s OWN `governance_receipts` table
                        only -- the same per-store placement WBS 3.8a
                        established for Label receipts and WBS 3.8b used
                        for `feature_store.db`.
WHAT IS NEVER TOUCHED: `label_specifications`, `label_definitions`,
                        `label_value_forward`, `label_value_research`.
                        This is a pure ADDITION of governance receipts for
                        Specifications that were already registered -- it
                        does not revise, re-judge, re-register, or
                        reinterpret any of them, and it changes no
                        Specification's `phase`.

NO ACTIVATION LOGIC IS TOUCHED OR IMPLIED. `registry.activate_
specification()` is neither called nor modified here. Every Specification
keeps whatever phase it already had; a bootstrap receipt attests that a
registration happened, never that a binding was demonstrated equivalent.
Activation is WBS 3.11's separate, separately-authorised concern.

Each receipt is written with `attestation='bootstrap'`
(`receipts.ATTESTATION_BOOTSTRAP`): the Tier A evaluation that justified
each of these real Specifications was not captured at the time in a
replayable form, so `replay()` on them REFUSES rather than approximating --
`receipts.replay()`'s own bootstrap check fires before `decision_class` is
even consulted, exactly the precedent PR-1's `migrate_bootstrap_
transitions.py`, PR-6's `migrate_bootstrap_evidence_admission.py`, PR-8's
`migrate_bootstrap_transition_receipts.py`, and WBS 3.8b's `migrate_
bootstrap_feature_receipts.py` all already set. `verify()` (hash-only)
holds forever regardless.

WHY NO `evaluation[]` IS FABRICATED. `label_receipts._build_registration_
body()` records a real Tier A `evaluation[]` because it observes the check
as it happens. Re-running Tier A today over the stored Specification
content would produce a verdict, but recording it as though it were the
original registration's evaluation would be a lie with a valid hash -- the
receipt would claim to attest a 2026-08-05 decision while actually
carrying a 2026-08-06 recomputation. The bootstrap attestation states the
honest thing instead: the registration fact and its outcome are attested;
the computation is not reproducible.

Usage:
    python migrate_bootstrap_label_receipts.py             # dry run
    python migrate_bootstrap_label_receipts.py --commit     # writes
    python migrate_bootstrap_label_receipts.py --db PATH [--commit]

Properties:
    - Idempotent: skips any Specification that already has a receipt.
    - Requires --commit to write anything; the default dry run performs no
      write of any kind, including no schema write (see plan()).
    - Asserts the target really is a Label Store before writing (`LR4`).
    - Takes a timestamped backup of label_store.db before writing.
    - Prints a full plan before writing.
"""
from __future__ import annotations

import argparse
import importlib.util
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_DB_PATH = _THIS_DIR / "label_store.db"


def _load_sibling(module_file_name: str, registered_name: str):
    """Collision-safe sibling loader -- see migrate_h6_forward_labels.py's
    identical helper and its docstring for why the sys.modules check is
    not optional in this repository."""
    if registered_name in sys.modules:
        return sys.modules[registered_name]
    spec = importlib.util.spec_from_file_location(registered_name, _THIS_DIR / module_file_name)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[registered_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_label_receipts():
    return _load_sibling("label_receipts.py", "label_store_label_receipts")


def _infra():
    """(receipts, receipt_ledger, label_receipts). The decision type,
    subject kind, and single-receipt-per-Specification lookup are REUSED
    from label_receipts.py (WBS 3.8a) rather than re-derived -- unlike WBS
    3.8b, which had to mirror them by shape because its subject kind and
    decision type differed."""
    lr = _load_label_receipts()
    receipts, ledger = lr._import_receipt_infra()
    return receipts, ledger, lr


def _governance_receipts_table_exists(db_path: Path) -> bool:
    """True dry-run safety: the real label_store.db has no
    governance_receipts table at all (WBS 3.8a only ever created one on
    disposable copies). plan() must never call init_db() to find out,
    since CREATE TABLE IF NOT EXISTS is itself a schema write and a dry
    run must write nothing, not even an empty table. This is WBS 3.8b's
    own corrected pattern, adopted here deliberately: 3.8b's first draft
    created an empty table on a real database during what was supposed to
    be a read-only dry run."""
    con = sqlite3.connect(db_path)
    try:
        row = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'governance_receipts'"
        ).fetchone()
        return row is not None
    finally:
        con.close()


def _assert_label_store_schema(db_path: Path) -> None:
    """Refuse to write to anything that is not a Label Store (`LR4`: no
    Phase 3 script may write to the wrong database; assert the expected
    schema shape and refuse on mismatch, rather than relying on the
    operator getting --db right)."""
    con = sqlite3.connect(db_path)
    try:
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        con.close()
    required = {"label_specifications", "label_definitions"}
    missing = required - names
    if missing:
        raise ValueError(
            f"{db_path} does not look like a Label Store database "
            f"(missing table(s): {', '.join(sorted(missing))}). Refusing to write."
        )


def _all_specifications(db_path: Path) -> list[dict]:
    """Read-only: every row in label_specifications. No public bulk-read
    function exists in registry.py (only get_specification() by single
    spec_hash) -- a direct SELECT is the minimal, honest choice for a
    one-time migration script, mirroring migrate_bootstrap_feature_
    receipts.py's identical decision."""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT * FROM label_specifications ORDER BY registered_at, spec_hash"
        ).fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]


def plan(db_path: Path) -> list[dict]:
    """Every Label Specification with no `label.register` receipt. Pure
    computation, no writes -- including no schema write: if
    governance_receipts does not exist yet, every Specification is planned
    directly, with no attempt to query a table that is not there."""
    specs = _all_specifications(db_path)
    if not _governance_receipts_table_exists(db_path):
        return specs
    _, _, lr = _infra()
    return [
        spec for spec in specs
        if lr.get_registration_receipt(db_path, spec["spec_hash"]) is None
    ]


def _bootstrap_label_receipt(db_path: Path, spec: dict, attestation_note: str, actor: str = "bootstrap") -> dict:
    receipts, ledger, lr = _infra()
    verdict_result = "rejected" if spec["phase"] == "Rejected" else "registered"
    body = receipts.ReceiptBody(
        decision_type=lr.DECISION_REGISTER,
        decision_class="derived",
        action="register",
        subject=receipts.Subject(kind=lr.SUBJECT_KIND, id=spec["spec_hash"], version_or_hash=None),
        as_of=spec["registered_at"],
        decided_at=receipts.now_iso(),
        actor=receipts.Actor(type="service", id=actor, key_id=None),
        authority=receipts.Authority(
            policy_id="bootstrap", policy_hash=receipts.sha256_lf(b"bootstrap"), role=None,
        ),
        rule=receipts.RuleRef(rule_id="bootstrap", rule_hash=None, engine_version=None, thresholds_hash=None),
        verdict=receipts.Verdict(result=verdict_result, reason_code=None),
        attestation=receipts.ATTESTATION_BOOTSTRAP,
        attestation_note=attestation_note,
    )
    return ledger.append_receipt(db_path, body)


def apply(db_path: Path, items: list[dict]) -> None:
    for spec in items:
        note = (
            f"Reconstructed {datetime.now(timezone.utc).isoformat()} from label_specifications "
            f"row spec_hash={spec['spec_hash'][:16]}... (definition_name={spec['definition_name']!r}, "
            f"definition_version={spec['definition_version']}, horizon_days={spec['horizon_days']}, "
            f"phase={spec['phase']!r}, registered_by={spec['registered_by']!r}, "
            f"registered_at={spec['registered_at']}). This Specification predates the governance "
            f"receipt substrate reaching the Label Store (WBS 3.8a/3.8c), so the Tier A evaluation "
            f"that justified it was never captured in a replayable form. The registration fact and "
            f"outcome are attested; the computation is not reproducible. verify() holds; replay() "
            f"refuses."
        )
        _bootstrap_label_receipt(db_path, spec, note)


def print_table(items: list[dict]) -> None:
    if not items:
        print("0 Label Specifications need a bootstrap receipt (already fully backfilled, or none exist).")
        return
    print(f"{'spec_hash':<20} {'definition_name':<30} {'h':>3} {'phase':<11} registered_at")
    for spec in items:
        short_hash = spec["spec_hash"][:16] + "..."
        print(
            f"{short_hash:<20} {spec['definition_name']:<30} {spec['horizon_days']:>3} "
            f"{spec['phase']:<11} {spec['registered_at'][:19]}"
        )
    print(f"\n{len(items)} receipt(s) would be written.")


def backup(db_path: Path) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = db_path.with_name(f"{db_path.stem}.pre-label-receipts-migration.{ts}.db")
    shutil.copy2(db_path, backup_path)
    return backup_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--commit", action="store_true", help="Write the receipts (default: dry run)")
    parser.add_argument("--db", default=str(_DB_PATH), help="Path to label_store.db")
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: {db_path} not found.", file=sys.stderr)
        return 1

    items = plan(db_path)  # read-only, including schema -- see plan()'s own docstring
    print_table(items)

    if not args.commit:
        print("\nDry run only. Re-run with --commit to write.")
        return 0

    if not items:
        return 0

    _assert_label_store_schema(db_path)

    backup_path = backup(db_path)
    print(f"\nBacked up {db_path} -> {backup_path}")

    # Ensures governance_receipts exists (additive, idempotent) -- only now,
    # after the schema assertion and the backup, and only because there is
    # something to commit. WBS 3.8b's own corrected ordering.
    _infra()[2].init_db(db_path)

    apply(db_path, items)
    print(f"Committed {len(items)} bootstrap receipt(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
