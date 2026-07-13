"""
One-time seed: import Phase B evaluation results into ledger.db.

Reads:  research/reports/evidence_library/evidence_library.json
Writes: research/evidence_library/ledger.db  (runs + gate_results per signal)

After running:
  1. Ledger contains one offline-corpus run per signal.
  2. The script prints a YAML block with pinned_run UUIDs.
  3. Copy those UUIDs into registry.yaml under the matching signal's pinned_run field.
  4. Run `python report.py --check` to validate.

The gate results written here reflect the frozen Candidate gate thresholds
(thresholds.yaml). They are stored point-in-time; re-seeding is idempotent
(new UUIDs are generated — old records are preserved, only the latest run is used
for reporting).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_THIS_DIR   = Path(__file__).resolve().parent
_REPORTS    = _THIS_DIR.parent / "reports" / "evidence_library"
_DB_PATH    = _THIS_DIR / "ledger.db"

sys.path.insert(0, str(_THIS_DIR))
import record
import lifecycle

# Display name → family_id in registry.yaml
DISPLAY_TO_FAMILY = {
    "H5 Volatility Regimes":           "h5_vol_regime",
    "H6 Funding x Vol x Regime":       "h6_funding_vol_regime",
    "Market Regime (SMA200 slope)":    "g1_market_regime_sma200",
    "ATR Expansion":                   "g3_atr_expansion",
    "ATR Compression":                 "g3_atr_compression",
    "Funding Level":                   "g2_funding_level",
    "Inside Bar":                      "g4_inside_bar",
    "4H Trend":                        "g5_trend_4h",
    "Trend Regime (MA cross)":         "g1_trend_regime_ma_cross",
    "Momentum (TSMOM)":                "g3_momentum_tsmom",
    "Volatility Regime":               "g1_volatility_regime",
    "Funding-Basis Composite":         "g6_funding_basis_composite",
    "Basis Expansion":                 "g2_basis_expansion",
    "Morning Star":                    "g4_morning_star",
    "Pin Bar":                         "g4_pin_bar",
    "Outside Bar":                     "g4_outside_bar",
    "Funding Acceleration":            "g2_funding_acceleration",
    "Mean Reversion (RSI)":            "g3_mean_reversion_rsi",
    "Trend Misalignment":              "g5_trend_misalignment",
    "Volume Surge":                    "g6_volume_surge",
    "Trend Continuation (ER)":        "g3_trend_continuation_er",
    "Evening Star":                    "g4_evening_star",
    "Engulfing":                       "g4_engulfing",
    "1H Trend":                        "g5_trend_1h",
    "CLV Pressure":                    "g6_clv_pressure",
    "Basis Compression":               "g2_basis_compression",
    "Range Regime (BB fade)":          "g1_range_regime_bb",
    "Funding Divergence":              "g2_funding_divergence",
    "OBV Trend":                       "g6_obv_trend",
    "Breakout Regime (Donchian)":      "g1_breakout_donchian",
    "Chaikin Money Flow":              "g6_chaikin_money_flow",
    "1D Trend":                        "g5_trend_1d",
    "Trend Alignment":                 "g5_trend_alignment",
    "Basis Level":                     "g2_basis_level",
}

# Role map for gate evaluation
FAMILY_ROLES = {
    "h6_funding_vol_regime":      ["edge"],
    "g1_market_regime_sma200":    ["feature"],
    "h5_vol_regime":              ["feature"],
    "g2_funding_level":           ["feature"],
    "g5_trend_4h":                ["feature"],
    "g3_momentum_tsmom":          ["edge"],
    "g1_trend_regime_ma_cross":   ["feature"],
    "g3_atr_expansion":           ["edge"],
    "g3_atr_compression":         ["feature"],
    "g6_funding_basis_composite": ["feature"],
    "g3_trend_continuation_er":   [],
}


def _map_metrics(raw: dict) -> dict:
    """Normalise evidence_library.json field names to match record.append_run expectations."""
    return {
        "sharpe":               raw.get("sharpe"),
        "oos_sharpe":           raw.get("oos_sharpe"),
        "evidence_score":       raw.get("evidence_score"),
        "verdict":              raw.get("verdict"),
        "n_trades":             raw.get("n_trades"),
        "sens_pass_frac":       raw.get("sens_pass_frac"),
        "wf_pass_frac":         raw.get("wf_pass_frac"),
        "loo_sign_consistency": raw.get("loo_sign_consistency"),
        "breakeven_cost_bps":   raw.get("breakeven_cost_bps"),
        "struct_sign_ok":       raw.get("struct_sign_ok"),
        "oos_sign_ok":          raw.get("oos_sign_ok"),
        "stability_score":      raw.get("stability_score"),
        "cagr_pct":             raw.get("cagr_pct"),
        "max_dd_pct":           raw.get("max_dd_pct"),
        "is_sharpe":            raw.get("is_sharpe"),
    }


def main() -> int:
    src = _REPORTS / "evidence_library.json"
    if not src.exists():
        print(f"ERROR: {src} not found. Run run_evidence.py first.", file=sys.stderr)
        return 1

    with open(src) as f:
        library: dict = json.load(f)

    record.init_db(_DB_PATH)

    import yaml
    thr_path = _THIS_DIR / "thresholds.yaml"
    with open(thr_path) as f:
        thresholds = yaml.safe_load(f)

    print(f"Seeding {len(library)} signals into {_DB_PATH}")
    print()

    run_ids: dict[str, str] = {}
    skipped: list[str] = []

    for display_name, raw in library.items():
        family_id = DISPLAY_TO_FAMILY.get(display_name)
        if family_id is None:
            print(f"  SKIP: no family_id mapping for '{display_name}'")
            skipped.append(display_name)
            continue

        metrics = _map_metrics(raw)

        # First offline-corpus run for this family -> discovery (L0, carries
        # the multiple-testing burden). Every later one (a re-seed, e.g. the
        # 2026-06-14 run) -> confirmatory (L1, a reproducibility check, not
        # new evidence). At most one L0 run may ever exist per family
        # (evidence_reporting_architecture.md §3, L0 admissibility).
        run_kind = "discovery" if record.get_latest_run(_DB_PATH, family_id, "discovery") is None else "confirmatory"

        run_id = record.append_run(
            db_path        = _DB_PATH,
            family_id      = family_id,
            metrics        = metrics,
            provenance     = "offline-corpus",
            run_kind       = run_kind,
            window_start   = "2021-01-01",
            window_end     = "2026-05-31",
            engine_version = "phase_b_v1",
            n_trials       = len(library),
        )
        run_ids[family_id] = run_id

        # Evaluate and store gate results
        roles = FAMILY_ROLES.get(family_id, [])
        fam_meta = {"role": roles}
        passed, gate_results = lifecycle.check_candidate_gate(fam_meta, metrics, thresholds)

        for gr in gate_results:
            record.append_gate_result(
                db_path   = _DB_PATH,
                run_id    = run_id,
                gate_code = gr["gate_code"],
                role      = gr["role"],
                passed    = bool(gr["passed"]),
                observed  = gr["observed"],
                threshold = gr["threshold"],
            )

        verdict_str = raw.get("verdict", "-")
        status = "CANDIDATE" if passed else "discovery/DQ"
        print(f"  {family_id:<40} run={run_id[:8]}  verdict={verdict_str:<10} gate={status}")

    print()
    if skipped:
        print(f"Skipped {len(skipped)} unrecognised display names: {skipped}")
        print()

    print("=" * 70)
    print("Add the following pinned_run values to registry.yaml:")
    print("=" * 70)
    print()
    print("# Copy these into registry.yaml under the matching signal's pinned_run field.")
    print()
    for family_id, run_id in sorted(run_ids.items()):
        print(f"{family_id}:")
        print(f"  pinned_run: {run_id}")
        print()

    print("After updating registry.yaml, run:")
    print("  python report.py --check")
    print("to validate registry consistency.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
