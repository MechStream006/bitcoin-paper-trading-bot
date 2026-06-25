"""
One-time migration: admit g3_atr_expansion as Candidate edge (2026-06-26).

Inserts the discovery -> candidate state_transition in ledger.db.
Guards against double-execution by checking for an existing transition first.

Run exactly once:
    conda run -n btc_bot python research/evidence_library/migrate_g3_atr_admission.py
"""
import pathlib
import sys

_THIS_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_THIS_DIR))

import record

FAMILY_ID         = "g3_atr_expansion"
FROM_STATE        = "discovery"
TO_STATE          = "candidate"
JUSTIFYING_RUN_ID = "0c7e1bf9-31e2-4662-a843-eef7f98c9331"
ACTOR             = "operator"
REASON            = (
    "Candidate edge gate: all 7 checks PASS on R2 (0c7e1bf9). "
    "sens_pass_frac=0.60 (3/5 variants, v5 {n=21,lag=20} added 2026-06-25, commit e3ca225). "
    "IS DSR=0.270, BH-p=0.207 (IS only; Validated gate evaluated on forward returns). "
    "Pre-registration locked: b76c5af9455be3cf91d42697106560887716e2ee1d8e0b8faa146c5a3f1d6ebd."
)

DB = _THIS_DIR / "ledger.db"


def main() -> None:
    existing = record.get_all_transitions(DB)
    already_done = [
        t for t in existing
        if t["family_id"] == FAMILY_ID
        and t["from_state"] == FROM_STATE
        and t["to_state"] == TO_STATE
    ]
    if already_done:
        print(
            f"SKIP: {FAMILY_ID} {FROM_STATE} -> {TO_STATE} transition already in ledger "
            f"(ts={already_done[0]['ts']}). Do not re-run."
        )
        sys.exit(0)

    record.append_transition(
        db_path=DB,
        family_id=FAMILY_ID,
        from_state=FROM_STATE,
        to_state=TO_STATE,
        reason=REASON,
        justifying_run_id=JUSTIFYING_RUN_ID,
        actor=ACTOR,
    )

    transitions = record.get_all_transitions(DB)
    inserted = [
        t for t in transitions
        if t["family_id"] == FAMILY_ID and t["to_state"] == TO_STATE
    ]
    assert inserted, "Transition insert failed — not found in ledger after write."
    print(f"OK: {FAMILY_ID} {FROM_STATE} -> {TO_STATE}")
    print(f"    ts:  {inserted[0]['ts']}")
    print(f"    run: {inserted[0]['justifying_run_id']}")
    print(f"    actor: {inserted[0]['actor']}")
    print(f"\nDone. Run report.py --check to verify registry consistency.")


if __name__ == "__main__":
    main()
