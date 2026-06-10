"""
M0 — Pre-Registration Gate.

Run ONCE before any forward evidence is collected.
Computes SHA-256 hashes of every frozen artifact, records the full
specification, and writes pre_registration.json alongside this file.

After running:
  1. Commit pre_registration.json to git.
  2. Never modify decision_function.py or schema.sql without creating
     a new version (v1.1.0 / v2.0.0) and running this script again.

Run from research/:
    python forward_evidence/m0_pre_register.py
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys
from datetime import datetime, timezone

_HERE = pathlib.Path(__file__).resolve().parent
_RESEARCH = _HERE.parent

sys.path.insert(0, str(_RESEARCH))

from forward_evidence.decision_function import FUNCTION_SPEC, VERSION   # noqa: E402


# ============================================================================
# Artifacts to hash
# ============================================================================

_ARTIFACTS = {
    "decision_function.py":         _HERE / "decision_function.py",
    "schema.sql":                   _HERE / "schema.sql",
    "signals.py":                   _RESEARCH / "evidence" / "signals.py",
    "engine.py":                    _RESEARCH / "evidence" / "engine.py",
    "governance.py":                _RESEARCH / "evidence" / "governance.py",
    "market_regimes.py":            _RESEARCH / "hypotheses" / "shared" / "market_regimes.py",
}


def _sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


# ============================================================================
# Promotion criteria (from Phase E Gold Standard, frozen here)
# ============================================================================

PROMOTION_CRITERIA = {
    "description": (
        "Criteria to promote H6 from demo evidence to production consideration. "
        "All gates must pass simultaneously. Evaluated only after minimum sample reached."
    ),
    "minimum_trades": 30,
    "gates": {
        "G1_direction": {
            "description": "Mean trade return > 0 (net of 15 bps cost)",
            "threshold": "mean_net_return > 0",
        },
        "G2_power": {
            "description": "t-test one-sided p < 0.10 (n=30 gives ~40% power at true SR=0.37/trade)",
            "threshold": "t_test_p < 0.10",
        },
        "G3_regime_presence": {
            "description": "At least 15 signal-days in non-bear conditions (regime not absent)",
            "threshold": "non_bear_signal_days >= 15",
        },
        "G4_drawdown": {
            "description": "Max drawdown < 25% during collection window",
            "threshold": "max_drawdown < 0.25",
        },
        "G5_regime_stability": {
            "description": "Regimes stable (vol, funding, market) for ≥ 80% of hold days",
            "threshold": "regime_stability_pct >= 0.80",
        },
    },
    "failure_analysis_required": True,
    "notes": (
        "n=30 is underpowered (power ~40% at per-trade SR=0.37). "
        "Failure to reject H0 at p<0.10 is NOT proof of no edge; "
        "record as 'insufficient evidence' not 'disproven'."
    ),
}


# ============================================================================
# Evidence collection spec (what is logged and when)
# ============================================================================

COLLECTION_SPEC = {
    "daily_job": {
        "trigger": "once per day, after daily close is available (00:05 UTC)",
        "data_inputs": [
            "Binance BTCUSDT spot close (from 1m OHLCV, last bar of day)",
            "Binance USDT-perp daily mean funding rate",
            "Deribit DVOL close (if available)",
            "StrataTrade funding bps (if API accessible; optional)",
        ],
        "steps": [
            "1. Load last MIN_HISTORY rows from corpus",
            "2. Call compute_h6_decision() -> signal + regimes",
            "3. Write to corpus_daily, regime_states, signal_log",
            "4. If h6_canonical=1 and not in_hold: open trade_entries row",
            "5. If open trade and today = planned_exit_day: close trade_exits row",
            "6. Fill forward labels for any horizon that has elapsed",
            "7. Update mae_mfe_path for all open trades",
            "8. Compute attribution at exit",
            "9. Write run_log row with fn_hash + day_features_hash",
        ],
    },
    "evidence_snapshots": {
        "trigger": "monthly (first day of each month)",
        "content": [
            "Running IS metrics on all completed trades",
            "DSR on trade returns (non-overlapping 7d)",
            "Power analysis update (current n vs required n)",
            "Regime-stability summary",
            "Source-divergence summary (canonical vs shadow, if shadow available)",
        ],
    },
    "dual_logging": {
        "canonical": "Binance data → h6_canonical → all trade decisions",
        "shadow":    "StrataTrade funding bps (if available) → h6_shadow → logged only, never acts",
        "divergence_metric": "regime_diverges in regime_states; agreement_rate in monthly snapshot",
    },
    "determinism_check": {
        "mechanism": "fn_hash in run_log must match hash in pre_registration.json every run",
        "alert": "if fn_hash mismatch: abort run, alert operator, do NOT write to signal_log",
    },
}


# ============================================================================
# Main
# ============================================================================

def main() -> None:
    out_path = _HERE / "pre_registration.json"

    if out_path.exists():
        existing = json.loads(out_path.read_text(encoding="utf-8"))
        if existing.get("version") == VERSION:
            print(f"[WARN] pre_registration.json already exists for version {VERSION}.")
            print("       Delete it first if you want to re-register.")
            print("       Never re-register without changing VERSION in decision_function.py.")
            sys.exit(1)
        print(f"[INFO] Existing registration is version {existing.get('version')}; creating {VERSION}.")

    print("=" * 70)
    print(f"  M0 PRE-REGISTRATION  —  version {VERSION}")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 70)

    hashes: dict[str, str] = {}
    for name, path in _ARTIFACTS.items():
        if not path.exists():
            print(f"  [ERROR] artifact not found: {path}")
            sys.exit(1)
        h = _sha256(path)
        hashes[name] = h
        print(f"  {name:45s}  {h[:16]}...")

    reg: dict = {
        "schema_version":        1,
        "version":               VERSION,
        "pre_registration_date": datetime.now(timezone.utc).isoformat(),
        "function_spec":         FUNCTION_SPEC,
        "artifact_hashes":       hashes,
        "decision_function_hash": hashes["decision_function.py"],
        "promotion_criteria":    PROMOTION_CRITERIA,
        "collection_spec":       COLLECTION_SPEC,
        "source_divergence_assessment": {
            "date": "2026-06-10",
            "conclusion": "Binance canonical; StrataTrade is execution venue only",
            "market_regime_agreement":    ">99% (SMA200 insensitive to <0.1% cross-exchange spread)",
            "vol_regime_agreement":       ">97% (RV21 insensitive to <0.1% spread)",
            "funding_regime_agreement":   "85-90% (cross-exchange funding correlation ~0.90-0.95 daily)",
            "h6_trigger_agreement":       "~84% (~4-5 disagreement days/year; ~1 trade/year timing diff)",
            "resolution":                 "dual-logging: canonical from Binance; shadow from StrataTrade when available",
        },
        "lockbox_status": {
            "window":  "2025-06-01 to 2026-05-31",
            "spent":   True,
            "verdict": "REJECT BOTH (Pure H6 and H6+ATR): 2 trades, both losses, regime absent in -30.3% bear year",
        },
        "notes": [
            "Do not modify decision_function.py after this registration.",
            "Any parameter change requires VERSION bump + new registration.",
            "fn_hash in run_log must match decision_function_hash every run.",
            "h6_shadow is logged for divergence tracking only; never drives decisions.",
            "Forward evidence window: from first collection day onward.",
        ],
    }

    out_path.write_text(json.dumps(reg, indent=2, default=str), encoding="utf-8")

    print()
    print(f"  -> Wrote: {out_path}")
    print(f"  -> decision_function_hash = {reg['decision_function_hash'][:32]}...")
    print()
    print("  NEXT STEPS:")
    print("  1. Commit pre_registration.json to git (this is the timestamp record).")
    print("  2. Build M1 (SQLite schema initializer).")
    print("  3. Build M2-M6 (daily job, trade lifecycle, labels, reporting).")
    print("  4. Never modify decision_function.py; treat as sealed.")
    print("=" * 70)


if __name__ == "__main__":
    main()
