"""
One-time backfill: bootstrap-attest an evidence.admit/.reject governance
receipt for every family's CURRENTLY PINNED run, for families whose
transition predates PR-6 (docs/governance/governance_receipts.md §6,
"bootstrap honesty").

WHAT IS TOUCHED:    a new row per pinned run in governance_receipts only.
WHAT IS NEVER TOUCHED: runs, gate_results, state_transitions, registry.yaml.
                    This is a pure ADDITION of governance receipts for
                    decisions that were already made -- it does not revise,
                    re-judge, or reinterpret any of them.

Each receipt is written with `attestation='bootstrap'`
(receipts.ATTESTATION_BOOTSTRAP): the gate evaluation that originally
justified the admission/rejection was not captured at the time in a
replayable form (no guarantee the exact gate_results rows this PR's
duplicate-checked write path expects predate PR-6), so `replay()` on these
receipts REFUSES rather than approximating -- exactly the precedent PR-1's
own `migrate_bootstrap_transitions.py` set for state_transitions.
`verify()` (hash-only) still holds forever.

Usage:
    python migrate_bootstrap_evidence_admission.py             # dry run
    python migrate_bootstrap_evidence_admission.py --commit     # writes

Properties:
    - Idempotent: skips any pinned run that already has an admission/
      rejection receipt (bootstrap or live).
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

import yaml

_THIS_DIR = Path(__file__).resolve().parent
_DB_PATH = _THIS_DIR / "ledger.db"
_REGISTRY_PATH = _THIS_DIR / "registry.yaml"

sys.path.insert(0, str(_THIS_DIR))
import evidence_governance as eg  # noqa: E402
import record  # noqa: E402

# Mirrors evidence_governance._REQUIRED_RECEIPT_TYPE_FOR_STATE's verdict side.
_VERDICT_FOR_STATE = {
    "candidate": "admitted", "validated": "admitted",
    "promotion_ready": "admitted", "production_ready": "admitted",
    "disqualified": "rejected", "rejected": "rejected",
}


def plan(db_path: Path, registry: dict) -> list[tuple[str, str, str, str, str]]:
    """
    Returns [(family_id, pinned_run_id, state, decision_type, verdict), ...]
    for every family whose current derived state requires a receipt
    (eg._REQUIRED_RECEIPT_TYPE_FOR_STATE) and whose pinned run does not yet
    have one. Pure computation -- does not write anything.
    """
    out = []
    for fid in registry:
        state, pinned_run_id, _ts = record.derive_state_at(db_path, fid)
        if state is None or not pinned_run_id:
            continue
        decision_type = eg._REQUIRED_RECEIPT_TYPE_FOR_STATE.get(state)
        if decision_type is None:
            continue
        if eg.get_admission_receipt(db_path, pinned_run_id) is not None:
            continue  # already has one (bootstrap or live) -- idempotent skip
        out.append((fid, pinned_run_id, state, decision_type, _VERDICT_FOR_STATE[state]))
    return out


def apply(db_path: Path, items: list[tuple[str, str, str, str, str]]) -> None:
    for fid, run_id, state, decision_type, verdict in items:
        note = (
            f"Reconstructed {datetime.now(timezone.utc).isoformat()} from registry.yaml "
            f"(state={state!r}) + state_transitions for {fid!r}. The gate evaluation and "
            f"engine version that originally justified this decision were not captured "
            f"at the time in a form this PR's admit_evidence()/reject_evidence() can "
            f"replay. verify() holds. replay() is unavailable and will refuse."
        )
        eg.bootstrap_admission_receipt(db_path, run_id, decision_type, verdict, note)


def print_table(items: list[tuple[str, str, str, str, str]]) -> None:
    if not items:
        print("0 pinned runs need a bootstrap admission receipt (already fully backfilled, or none pinned yet).")
        return
    print(f"{'family_id':<28} {'run_id':<10} {'state':<16} {'decision_type':<16} verdict")
    for fid, run_id, state, decision_type, verdict in items:
        print(f"{fid:<28} {run_id[:8]:<10} {state:<16} {decision_type:<16} {verdict}")
    print(f"\n{len(items)} receipt(s) would be written.")


def backup(db_path: Path) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = db_path.with_name(f"{db_path.stem}.pre-evidence-admission-migration.{ts}.db")
    shutil.copy2(db_path, backup_path)
    return backup_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--commit", action="store_true", help="Write the receipts (default: dry run)")
    parser.add_argument("--db", default=str(_DB_PATH), help="Path to ledger.db")
    parser.add_argument("--registry", default=str(_REGISTRY_PATH), help="Path to registry.yaml")
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    registry_path = Path(args.registry)
    if not db_path.exists():
        print(f"ERROR: {db_path} not found.", file=sys.stderr)
        return 1
    if not registry_path.exists():
        print(f"ERROR: {registry_path} not found.", file=sys.stderr)
        return 1

    eg.init_db(db_path)  # ensures governance_receipts exists (additive, idempotent)
    registry = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}

    items = plan(db_path, registry)
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
