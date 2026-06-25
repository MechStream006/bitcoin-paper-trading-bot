"""
Local-environment repair for ivrv_vrp_spread ledger inconsistency.

CONTEXT
-------
migrate_ivrv_admission.py was run on the server on 2026-06-21, writing the
ivrv_vrp_spread run + gate_results + state_transition to the server's ledger.db
and printing run_id b98c9685-134b-410f-ac98-684b0ee01da6. That UUID was then
committed into registry.yaml (commit 2bd1ebd). Because ledger.db is untracked,
the local dev machine's ledger.db never received those rows. This causes
report.py --check to permanently fail locally with:

    ivrv_vrp_spread: pinned_run 'b98c9685...' not found in ledger.db

This script repairs the local ledger by inserting exactly the rows the server
wrote, using the same hardcoded UUID so the registry.yaml reference is satisfied.

SOURCE OF TRUTH
---------------
All metric values, gate specs, window dates, engine_version, and reason text are
sourced verbatim from migrate_ivrv_admission.py (the script that ran on the server).
The run_ts approximates the server run time (inferred from commit 2bd1ebd:
"Sun Jun 21 19:38:41 2026 +0530" = 2026-06-21T14:08:41+00:00 UTC).

IDEMPOTENCY
-----------
Two independent guards:
  1. If run_id b98c9685... already exists in runs -> SKIP (already repaired)
  2. If ivrv_vrp_spread already has any state_transition -> SKIP

Run exactly once on each environment that needs repair. Safe to re-run.

CONSTRAINTS
-----------
- Does NOT modify registry.yaml, pre-registration files, or any other tracked file.
- Uses direct SQLite inserts to preserve the exact UUID (record.append_run() would
  generate a new random UUID, which would NOT match registry.yaml).
- All values are sourced from migrate_ivrv_admission.py; no values are invented here.

USAGE
-----
    conda run -n btc_bot python research/evidence_library/migrate_ivrv_repair_local.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_THIS_DIR))

DB = _THIS_DIR / "ledger.db"

# ── Constants (sourced verbatim from migrate_ivrv_admission.py) ───────────────

PINNED_RUN_ID = "b98c9685-134b-410f-ac98-684b0ee01da6"
FAMILY_ID     = "ivrv_vrp_spread"

# Timestamp approximated from commit 2bd1ebd (2026-06-21 19:38:41 +0530 = 14:08:41 UTC).
RUN_TS = "2026-06-21T14:08:41+00:00"

_METRICS = {
    "sharpe":         0.2649,
    "oos_sharpe":     0.4998,
    "evidence_score": 60.9,
    "verdict":        "Moderate",
    "n_trades":       109,
}
_GOVERNANCE = {
    "dsr_prob":              0.1646,
    "bh_p":                  0.2893,
    "sens_pass_frac":        1.0,
    "wf_pass_frac":          0.60,
    "loo_sign_consistency":  1.0,
    "breakeven_cost_bps":    30,
    "struct_sign_ok":        True,
    "full_sample_sharpe_note": (
        "0.3294 full-sample (incl. sealed lockbox); not used for gating"
    ),
}
_METRICS_JSON = json.dumps({**_METRICS, **_GOVERNANCE})

# Gate results: mirror _GATES tuple in migrate_ivrv_admission.py
# (gate_code, role, passed, observed, threshold)
_GATES = [
    ("E3_KNIFE_EDGE",  None,      True, "1.0",    "0.4"),
    ("B_SENSITIVITY",  "feature", True, "1.0",    "0.6"),
    ("B_OOS_POSITIVE", "feature", True, "0.4998", ">0"),
]

_REASON = (
    "Passes Feature-Candidate gate on lockbox-train slice (2021-01..2025-05): "
    "sensitivity 100% >= 60%, OOS Sharpe +0.50 > 0, clone_r 0.37 < 0.90. "
    "Edge-ineligible (train Sharpe 0.265 < 0.30; full-sample 0.329 spends the "
    "sealed lockbox, not used). Governance classification 2026-06-19. n_trials=34."
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _counts(con: sqlite3.Connection) -> dict[str, int]:
    return {
        tbl: con.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        for tbl in ("runs", "gate_results", "state_transitions")
    }


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    if not DB.exists():
        print(f"ERROR: {DB} not found. Run seed_from_scoreboard.py first.",
              file=sys.stderr)
        return 1

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    before = _counts(con)
    print(f"Before repair — runs: {before['runs']}, "
          f"gate_results: {before['gate_results']}, "
          f"state_transitions: {before['state_transitions']}")

    # ── Guard 1: pinned run already present ───────────────────────────────────
    existing_run = con.execute(
        "SELECT run_id FROM runs WHERE run_id=?", (PINNED_RUN_ID,)
    ).fetchone()
    if existing_run:
        print(f"\nSKIP: run {PINNED_RUN_ID[:8]}... already exists in ledger.db.")
        print("Nothing to do — local ledger is already in sync with registry.yaml.")
        con.close()
        return 0

    # ── Guard 2: transition already present ───────────────────────────────────
    existing_trans = con.execute(
        "SELECT id FROM state_transitions WHERE family_id=?", (FAMILY_ID,)
    ).fetchone()
    if existing_trans:
        print(f"\nSKIP: {FAMILY_ID} already has a state_transition in ledger.db.")
        print("The run row may still be missing — check manually if report.py --check fails.")
        con.close()
        return 1

    # ── Insert runs row (direct SQL to preserve the exact UUID) ───────────────
    con.execute(
        """
        INSERT INTO runs (
            run_id, family_id, run_ts, git_sha, fn_hash, provenance,
            window_start, window_end, engine_version, n_trials,
            sharpe, oos_sharpe, evidence_score, verdict, n_trades,
            dsr_prob, bh_p, metrics_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            PINNED_RUN_ID, FAMILY_ID, RUN_TS, None, None, "offline-corpus",
            "2021-01-01", "2025-05-31", "ivrv-1.0.0", 34,
            _METRICS["sharpe"],
            _METRICS["oos_sharpe"],
            _METRICS["evidence_score"],
            _METRICS["verdict"],
            _METRICS["n_trades"],
            _GOVERNANCE["dsr_prob"],
            _GOVERNANCE["bh_p"],
            _METRICS_JSON,
        ),
    )

    # ── Insert gate_results rows ───────────────────────────────────────────────
    for gate_code, role, passed, observed, threshold in _GATES:
        con.execute(
            """
            INSERT INTO gate_results (run_id, gate_code, role, passed, observed, threshold)
            VALUES (?,?,?,?,?,?)
            """,
            (PINNED_RUN_ID, gate_code, role, int(passed), observed, threshold),
        )

    # ── Insert state_transition row ───────────────────────────────────────────
    # Validate arc before writing (mirrors record.append_transition behaviour).
    from lifecycle import can_transition
    if not can_transition("discovery", "candidate"):
        print("ERROR: 'discovery' -> 'candidate' is not a valid arc.", file=sys.stderr)
        con.rollback()
        con.close()
        return 1

    con.execute(
        """
        INSERT INTO state_transitions
            (family_id, from_state, to_state, ts, reason, justifying_run_id, actor)
        VALUES (?,?,?,?,?,?,?)
        """,
        (FAMILY_ID, "discovery", "candidate", RUN_TS, _REASON, PINNED_RUN_ID, "operator"),
    )

    con.commit()

    # ── Verify ────────────────────────────────────────────────────────────────
    after = _counts(con)

    run_ok    = con.execute("SELECT run_id FROM runs WHERE run_id=?",
                            (PINNED_RUN_ID,)).fetchone() is not None
    gates_ok  = con.execute("SELECT COUNT(*) FROM gate_results WHERE run_id=?",
                            (PINNED_RUN_ID,)).fetchone()[0] == len(_GATES)
    trans_ok  = con.execute(
        "SELECT id FROM state_transitions WHERE family_id=? AND to_state='candidate'",
        (FAMILY_ID,),
    ).fetchone() is not None
    con.close()

    print(f"\nRepair complete:")
    print(f"  runs row inserted:            {'OK' if run_ok else 'MISSING'}")
    print(f"  gate_results rows inserted:   {'OK' if gates_ok else 'MISSING'} ({len(_GATES)} rows)")
    print(f"  state_transition inserted:    {'OK' if trans_ok else 'MISSING'}")
    print(f"\nAfter repair  — runs: {after['runs']}, "
          f"gate_results: {after['gate_results']}, "
          f"state_transitions: {after['state_transitions']}")
    print(f"\nDelta: runs +{after['runs']-before['runs']}, "
          f"gate_results +{after['gate_results']-before['gate_results']}, "
          f"state_transitions +{after['state_transitions']-before['state_transitions']}")
    print(f"\nNext: run  python report.py --check  (expect 0 errors).")

    if not (run_ok and gates_ok and trans_ok):
        print("WARNING: one or more rows were not confirmed. Investigate before proceeding.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
