"""
One-time backfill: bootstrap-attest a family.transition/.promote/.demote
governance receipt for every state_transitions row that predates PR-8
(docs/governance/governance_receipts.md §6, "bootstrap honesty").

WHAT IS TOUCHED:    a new row per pre-existing transition in
                    governance_receipts only.
WHAT IS NEVER TOUCHED: state_transitions, runs, gate_results, registry.yaml.
                    This is a pure ADDITION of governance receipts for
                    transitions that were already made -- it does not revise,
                    re-judge, reorder, or reinterpret any of them.

Each receipt is written with `attestation='bootstrap'`
(receipts.ATTESTATION_BOOTSTRAP): the validation that justified these
transitions was not captured at the time in a replayable form, so `replay()`
on them REFUSES rather than approximating -- exactly the precedent PR-1's own
`migrate_bootstrap_transitions.py` set for state_transitions and PR-6's
`migrate_bootstrap_evidence_admission.py` followed for evidence admission.
`verify()` (hash-only) and the legality check still hold forever.

Note the honest asymmetry: these transitions get a receipt saying "this
happened, and here is the arc", NOT "this was validated". Fabricating an
evaluation[] for a decision whose preconditions were never recorded would be
a lie with a valid hash -- which is worse than an admitted gap.

Usage:
    python migrate_bootstrap_transition_receipts.py             # dry run
    python migrate_bootstrap_transition_receipts.py --commit     # writes

Properties:
    - Idempotent: skips any transition that already has a receipt.
    - Requires --commit to write anything; default is always a dry run.
    - Takes a timestamped backup of ledger.db before writing.
    - Prints a full before/after table.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_DB_PATH = _THIS_DIR / "ledger.db"

sys.path.insert(0, str(_THIS_DIR))
import evidence_governance as eg  # noqa: E402
import record  # noqa: E402
import transition_governance as tg  # noqa: E402


def plan(db_path: Path) -> list[dict]:
    """Every transition with no governance receipt. Pure computation."""
    out = []
    for t in record.get_all_transitions(db_path):
        if tg.get_receipt_for_transition(db_path, t["family_id"], t["id"]) is None:
            out.append(t)
    return out


def apply(db_path: Path, items: list[dict]) -> None:
    for t in items:
        note = (
            f"Reconstructed {datetime.now(timezone.utc).isoformat()} from state_transitions "
            f"row #{t['id']} ({t['family_id']}: {t['from_state']} -> {t['to_state']}, "
            f"ts={t['ts']}, actor={t['actor']!r}). This transition predates PR-8, so the "
            f"validation and governance envelope that justified it were never captured in a "
            f"replayable form. The arc and its recorded reason are attested; the decision is "
            f"not reproducible. verify() and the legality check hold; replay() refuses."
        )
        tg.bootstrap_transition_receipt(db_path, t, note)


def print_table(items: list[dict]) -> None:
    if not items:
        print("0 transitions need a bootstrap receipt (already fully backfilled, or none exist).")
        return
    print(f"{'#':<5} {'family_id':<28} {'arc':<34} {'decision_type':<20} ts")
    for t in items:
        arc = f"{t['from_state']} -> {t['to_state']}"
        print(f"{t['id']:<5} {t['family_id']:<28} {arc:<34} "
              f"{tg.classify_arc(t['from_state'], t['to_state']):<20} {t['ts'][:19]}")
    print(f"\n{len(items)} receipt(s) would be written.")


def backup(db_path: Path) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = db_path.with_name(f"{db_path.stem}.pre-transition-receipts-migration.{ts}.db")
    shutil.copy2(db_path, backup_path)
    return backup_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--commit", action="store_true", help="Write the receipts (default: dry run)")
    parser.add_argument("--db", default=str(_DB_PATH), help="Path to ledger.db")
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: {db_path} not found.", file=sys.stderr)
        return 1

    eg.init_db(db_path)  # ensures governance_receipts exists (additive, idempotent)

    items = plan(db_path)
    print_table(items)

    if not args.commit:
        print("\nDry run only. Re-run with --commit to write.")
        return 0

    if not items:
        return 0

    backup_path = backup(db_path)
    print(f"\nBacked up {db_path} -> {backup_path}")

    apply(db_path, items)
    print(f"Committed {len(items)} bootstrap receipt(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
