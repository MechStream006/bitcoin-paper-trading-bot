"""
One-time migration: log the 11 bootstrap state transitions that were performed
on 2026-06-13 when the Evidence Library was initially seeded.

The seeder (seed_from_scoreboard.py) correctly wrote runs and gate_results but
did NOT call append_transition(), leaving state_transitions empty despite 11
signals having been moved out of discovery state. This migration backfills
those records with the correct backdated timestamp.

Idempotent: checks for existing transitions per family_id before inserting.
Run once; re-running is safe (skips families that already have a transition).
"""
from __future__ import annotations

import sys
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_DB_PATH  = _THIS_DIR / "ledger.db"

sys.path.insert(0, str(_THIS_DIR))
import record

# Backdated timestamp — the date the initial seeding was performed.
_BOOTSTRAP_TS = "2026-06-13T00:00:00+00:00"
_ACTOR        = "bootstrap"

# (family_id, from_state, to_state, pinned_run_uuid, dq_reason_or_none)
_TRANSITIONS = [
    # --- 7 candidates (discovery → candidate) ---
    (
        "h6_funding_vol_regime",
        "discovery", "candidate",
        "6b29cb25-d5c0-458a-913b-de714344802b",
        "Passes Edge-Candidate gate: Sharpe 0.90, WF 67%, Sens 100%, LOO 100%, "
        "breakeven 100bps, structural OK. Initial Phase B seeding 2026-06-13.",
    ),
    (
        "g1_market_regime_sma200",
        "discovery", "candidate",
        "e4074104-be78-46b0-896d-8f6e374f4981",
        "Passes Feature-Candidate gate: OOS +0.49 > 0, Sens 100%. "
        "Initial Phase B seeding 2026-06-13.",
    ),
    (
        "h5_vol_regime",
        "discovery", "candidate",
        "036349a1-d49f-465e-9533-62d547d566db",
        "Passes Feature-Candidate gate: OOS +0.68 > 0, Sens 100%. "
        "Initial Phase B seeding 2026-06-13.",
    ),
    (
        "g2_funding_level",
        "discovery", "candidate",
        "cc916a89-ba48-4816-8bbe-73af6f3ae315",
        "Passes Feature-Candidate gate: OOS +0.11 > 0, Sens 75%. "
        "Initial Phase B seeding 2026-06-13.",
    ),
    (
        "g5_trend_4h",
        "discovery", "candidate",
        "ca05220e-457d-4735-b009-40a8746ad5a4",
        "Passes Feature-Candidate gate: OOS +0.54 > 0, Sens 100%. "
        "Initial Phase B seeding 2026-06-13.",
    ),
    (
        "g3_momentum_tsmom",
        "discovery", "candidate",
        "c75f6a4e-256a-47c0-8840-7fe8714a9dbf",
        "Passes Edge-Candidate gate: Sharpe 0.35, WF 50%, Sens 100%, LOO 83%, "
        "breakeven 30bps, structural OK. Initial Phase B seeding 2026-06-13.",
    ),
    (
        "g1_trend_regime_ma_cross",
        "discovery", "candidate",
        "abc6078e-0dce-4c89-b73f-c1aa9467e795",
        "Passes Feature-Candidate gate: OOS +0.87 > 0, Sens 100%. "
        "Initial Phase B seeding 2026-06-13.",
    ),
    # --- 4 disqualified (discovery → disqualified) ---
    (
        "g4_inside_bar",
        "discovery", "disqualified",
        "3b8bcaee-a116-4b98-b631-ae7e1e22c11d",
        "Hard DQ E3: sensitivity 0% < 40% knife-edge threshold. "
        "Initial Phase B seeding 2026-06-13.",
    ),
    (
        "g6_obv_trend",
        "discovery", "disqualified",
        "15a98761-8ba1-4635-8dd2-9c0007abf042",
        "Hard DQ E3: sensitivity 33% < 40% knife-edge threshold. "
        "Initial Phase B seeding 2026-06-13.",
    ),
    (
        "g1_breakout_donchian",
        "discovery", "disqualified",
        "4e6cae28-8a1a-4c9c-bd2b-1147b496a21b",
        "Hard DQ E3: sensitivity 25% < 40% knife-edge threshold. "
        "Initial Phase B seeding 2026-06-13.",
    ),
    (
        "g1_volatility_regime",
        "discovery", "disqualified",
        "8261cea1-253d-4e37-a631-52a28fffbf65",
        "Clone DQ: identical metrics to h5_vol_regime, r≈1.0 ≥ 0.90 threshold. "
        "Initial Phase B seeding 2026-06-13.",
    ),
]


def main() -> int:
    if not _DB_PATH.exists():
        print(f"ERROR: {_DB_PATH} not found. Run seed_from_scoreboard.py first.",
              file=sys.stderr)
        return 1

    existing = record.get_all_transitions(_DB_PATH)
    already_logged = {t["family_id"] for t in existing}

    inserted = 0
    skipped  = 0

    for family_id, from_state, to_state, run_id, reason in _TRANSITIONS:
        if family_id in already_logged:
            print(f"  SKIP (already has transition): {family_id}")
            skipped += 1
            continue

        record.append_transition(
            db_path            = _DB_PATH,
            family_id          = family_id,
            from_state         = from_state,
            to_state           = to_state,
            reason             = reason,
            justifying_run_id  = run_id,
            actor              = _ACTOR,
            ts                 = _BOOTSTRAP_TS,
        )
        print(f"  LOGGED: {family_id}  {from_state} → {to_state}")
        inserted += 1

    print()
    print(f"Done: {inserted} inserted, {skipped} skipped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
