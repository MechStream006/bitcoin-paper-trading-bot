"""
One-time admission migration for ivrv_vrp_spread (H-IVRV, HV-2 VRP Spread).

H-IVRV is the FIRST registry entry outside the original Phase B seeding. This
script logs its ledger evidence so registry.yaml state=candidate is supported:

  1. append_run(...)          -> a run row (offline-corpus, train-slice metrics) -> run_id
  2. append_gate_result(...)  -> the Feature-Candidate gate checks for that run
  3. append_transition(...)   -> discovery -> candidate (validated arc)

After running, paste the printed run_id into registry.yaml:
    ivrv_vrp_spread.pinned_run: <run_id>
then run `python report.py --check` (expect green).

Idempotent: skips if ivrv_vrp_spread already has a transition.

GATING BASIS (governance 2026-06-19): lockbox-train slice 2021-01-01..2025-05-31,
lockbox 2025-06-01..2026-05-31 SEALED. Feature role. n_trials=34 (frozen).

NOT run automatically. Execute manually at deploy time:
    cd research && python evidence_library/migrate_ivrv_admission.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_DB_PATH  = _THIS_DIR / "ledger.db"

sys.path.insert(0, str(_THIS_DIR))
import record  # noqa: E402

FAMILY_ID = "ivrv_vrp_spread"

# IS metrics — lockbox-train slice, hold_days=1, cost 15bps, n_trials=34 (frozen).
_METRICS = {
    "sharpe":         0.2649,    # train-slice canonical (governing basis)
    "oos_sharpe":     0.4998,
    "evidence_score": 60.9,
    "verdict":        "Moderate",
    "n_trades":       109,
}
_GOVERNANCE = {
    "dsr_prob": 0.1646,          # IS, n_trials=34, train slice — fails Validated (expected)
    "bh_p":     0.2893,          # IS, 1 - PSR(0) — fails Validated (expected)
    "sens_pass_frac": 1.0,
    "wf_pass_frac":   0.60,
    "loo_sign_consistency": 1.0,
    "breakeven_cost_bps": 30,
    "struct_sign_ok": True,
    "full_sample_sharpe_note": "0.3294 full-sample (incl. sealed lockbox); not used for gating",
}
# Feature-Candidate gate checks (mirror lifecycle.check_candidate_gate feature path).
_GATES = [
    ("E3_KNIFE_EDGE",  None,      True, 1.0,    0.40),   # sensitivity >= 0.40 hard-DQ floor
    ("B_SENSITIVITY",  "feature", True, 1.0,    0.60),
    ("B_OOS_POSITIVE", "feature", True, 0.4998, ">0"),
]
_REASON = (
    "Passes Feature-Candidate gate on lockbox-train slice (2021-01..2025-05): "
    "sensitivity 100% >= 60%, OOS Sharpe +0.50 > 0, clone_r 0.37 < 0.90. "
    "Edge-ineligible (train Sharpe 0.265 < 0.30; full-sample 0.329 spends the "
    "sealed lockbox, not used). Governance classification 2026-06-19. n_trials=34."
)


def main() -> int:
    if not _DB_PATH.exists():
        print(f"ERROR: {_DB_PATH} not found. Run seed_from_scoreboard.py first.",
              file=sys.stderr)
        return 1

    existing = {t["family_id"] for t in record.get_all_transitions(_DB_PATH)}
    if FAMILY_ID in existing:
        print(f"SKIP: {FAMILY_ID} already has a transition. Nothing to do.")
        return 0

    run_id = record.append_run(
        db_path        = _DB_PATH,
        family_id      = FAMILY_ID,
        metrics        = _METRICS,
        provenance     = "offline-corpus",
        window_start   = "2021-01-01",
        window_end     = "2025-05-31",
        engine_version = "ivrv-1.0.0",
        n_trials       = 34,
        governance     = _GOVERNANCE,
    )
    for code, role, passed, observed, threshold in _GATES:
        record.append_gate_result(_DB_PATH, run_id, code, role, passed, observed, threshold)

    record.append_transition(
        db_path           = _DB_PATH,
        family_id         = FAMILY_ID,
        from_state        = "discovery",
        to_state          = "candidate",
        reason            = _REASON,
        justifying_run_id = run_id,
        actor             = "operator",
    )

    print(f"LOGGED: {FAMILY_ID}  discovery -> candidate")
    print(f"run_id = {run_id}")
    print(f"\nNext: set  ivrv_vrp_spread.pinned_run: {run_id}  in registry.yaml,")
    print("then run:  python report.py --check")
    return 0


if __name__ == "__main__":
    sys.exit(main())
