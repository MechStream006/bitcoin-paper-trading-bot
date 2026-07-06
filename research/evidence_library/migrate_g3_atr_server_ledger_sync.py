"""
migrate_g3_atr_server_ledger_sync.py
=====================================
One-time server-ledger sync for g3_atr_expansion run 0c7e1bf9.

Context
-------
Run 0c7e1bf9-31e2-4662-a843-eef7f98c9331 was created on the local development
machine on 2026-06-25 after the v5 sensitivity variant was added (commit
e3ca225). It is the first run where B_SENSITIVITY passed (sens_pass_frac=0.6),
making g3_atr_expansion eligible for Candidate admission. registry.yaml pins
this run_id, and migrate_g3_atr_admission.py references it as the justifying
run for the discovery->candidate state transition.

The server's ledger.db only has the two earlier runs (be1849d4, ed84b4a6) where
B_SENSITIVITY failed. This script syncs the missing run and its gate_results
to the server before the transition migration is applied.

Remediation order (run all three, in order):
    1. python migrate_g3_atr_server_ledger_sync.py   ← this script
    2. python migrate_g3_atr_admission.py             ← state transition
    3. python report.py --check                       ← verify governance

Idempotency guards
------------------
Guard 1 (runs): skips INSERT if run_id already exists.
Guard 2 (gate_results): skips INSERT if any gate_results row exists for this
    run_id (all-or-nothing — if any gate exists we treat the set as complete).

Run exactly once per environment. Safe to re-run (both guards are checked
before any write).
"""
import pathlib
import sqlite3
import sys

_THIS_DIR = pathlib.Path(__file__).resolve().parent
DB = _THIS_DIR / "ledger.db"

RUN_ID    = "0c7e1bf9-31e2-4662-a843-eef7f98c9331"
FAMILY_ID = "g3_atr_expansion"

RUN_ROW = (
    RUN_ID,
    FAMILY_ID,
    "2026-06-25T18:27:53.128687+00:00",  # run_ts
    None,                                  # git_sha
    None,                                  # fn_hash
    "offline-corpus",                      # provenance
    "2021-01-01",                          # window_start
    "2026-05-31",                          # window_end
    "phase_b_v2",                          # engine_version
    34,                                    # n_trials
    0.489697105300451,                     # sharpe
    0.705329737648683,                     # oos_sharpe
    64.5,                                  # evidence_score
    "Moderate",                            # verdict
    153,                                   # n_trades
    0.270351331758991,                     # dsr_prob
    0.207290163339303,                     # bh_p
    '{"sharpe": 0.4896971053004508, "oos_sharpe": 0.7053297376486833, '
    '"is_sharpe": 0.41728016358955966, "sens_pass_frac": 0.6, '
    '"wf_pass_frac": 0.6666666666666666, "loo_sign_consistency": 1.0, '
    '"breakeven_cost_bps": 50, "struct_sign_ok": true, "n_trades": 153, '
    '"evidence_score": 64.5, "verdict": "Moderate", "stability_score": 0.817, '
    '"dsr_prob": 0.27035133175899134, "bh_p": 0.20729016333930295}',  # metrics_json
)

# (gate_code, role, passed, observed, threshold)
GATE_ROWS = [
    ("E3_KNIFE_EDGE", None,   1, "0.6",                  "0.4"),
    ("B_SHARPE",      "edge", 1, "0.4896971053004508",   "0.3"),
    ("B_WF",          "edge", 1, "0.6666666666666666",   "0.5"),
    ("B_SENSITIVITY", "edge", 1, "0.6",                  "0.6"),
    ("B_LOO",         "edge", 1, "1.0",                  "0.67"),
    ("B_BREAKEVEN",   "edge", 1, "50.0",                 "15"),
    ("B_STRUCTURAL",  "edge", 1, "True",                 "True"),
]


def main() -> None:
    if not DB.exists():
        print(f"ERROR: ledger.db not found at {DB}")
        sys.exit(1)

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    # ── Guard 1: runs row ─────────────────────────────────────────────────────
    existing_run = con.execute(
        "SELECT run_id FROM runs WHERE run_id=?", (RUN_ID,)
    ).fetchone()

    if existing_run:
        print(f"SKIP runs: {RUN_ID[:8]} already in ledger (idempotent).")
    else:
        con.execute(
            """
            INSERT INTO runs
              (run_id, family_id, run_ts, git_sha, fn_hash, provenance,
               window_start, window_end, engine_version, n_trials, sharpe,
               oos_sharpe, evidence_score, verdict, n_trades, dsr_prob, bh_p,
               metrics_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            RUN_ROW,
        )
        con.commit()
        print(f"OK   runs: inserted {RUN_ID[:8]} ({FAMILY_ID}, offline-corpus, "
              f"sharpe={RUN_ROW[10]:.3f}, dsr={RUN_ROW[15]:.3f})")

    # ── Guard 2: gate_results rows ────────────────────────────────────────────
    existing_gates = con.execute(
        "SELECT COUNT(*) FROM gate_results WHERE run_id=?", (RUN_ID,)
    ).fetchone()[0]

    if existing_gates:
        print(f"SKIP gate_results: {existing_gates} row(s) already present for "
              f"{RUN_ID[:8]} (idempotent).")
    else:
        con.executemany(
            """
            INSERT INTO gate_results (run_id, gate_code, role, passed, observed, threshold)
            VALUES (?,?,?,?,?,?)
            """,
            [(RUN_ID, gate, role, passed, obs, thr)
             for gate, role, passed, obs, thr in GATE_ROWS],
        )
        con.commit()
        gates_inserted = len(GATE_ROWS)
        print(f"OK   gate_results: inserted {gates_inserted} rows for {RUN_ID[:8]}")
        for gate, role, passed, obs, _ in GATE_ROWS:
            print(f"     {gate:20s}  role={role or '':4s}  passed={passed}  obs={obs}")

    con.close()

    # ── Verify ────────────────────────────────────────────────────────────────
    con2 = sqlite3.connect(DB)
    run_check   = con2.execute(
        "SELECT family_id, provenance FROM runs WHERE run_id=?", (RUN_ID,)
    ).fetchone()
    gates_check = con2.execute(
        "SELECT COUNT(*) FROM gate_results WHERE run_id=?", (RUN_ID,)
    ).fetchone()[0]
    con2.close()

    if run_check and gates_check == len(GATE_ROWS):
        print(f"\nVerified: run {RUN_ID[:8]} present in runs "
              f"({run_check[0]}, {run_check[1]}) "
              f"with {gates_check} gate_results.")
        print("\nNext step: python migrate_g3_atr_admission.py")
    else:
        print(f"\nERROR: post-write verification failed "
              f"(run={'found' if run_check else 'MISSING'}, "
              f"gates={gates_check}/{len(GATE_ROWS)})")
        sys.exit(1)


if __name__ == "__main__":
    main()
