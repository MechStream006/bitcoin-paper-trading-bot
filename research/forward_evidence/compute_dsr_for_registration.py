"""
compute_dsr_for_registration.py
===============================
Compute IS DSR (Deflated Sharpe Ratio) and BH-FDR p-values for the three
signals being registered: h5_vol_regime, g1_market_regime_sma200,
g3_momentum_tsmom.

Governance constraints (FROZEN):
  n_trials   = 34   Phase B battery size — do NOT change
  cost_bps   = 15   Matches the IS battery evaluation (REALISTIC_COST)
  sr_denominator = std of all 34 per-period Sharpe values from evidence_library.json

Output is formatted for direct copy-paste into pre-registration draft JSONs.
Values recorded are IS (in-sample) statistics.  They describe the historical
signal quality BEFORE forward collection begins; they do NOT gate registration.
The Validated gate will recompute DSR/BH-FDR on forward trade returns.

Run:
  cd research
  python forward_evidence/compute_dsr_for_registration.py
"""
from __future__ import annotations

import json
import math
import pathlib
import re
import sys

import numpy as np

# ── Path setup (works when run from research/ or from any directory) ─────────
_THIS         = pathlib.Path(__file__).resolve()
_FWD_DIR      = _THIS.parent
_RESEARCH_DIR = _FWD_DIR.parent
_EVIDENCE_DIR = _RESEARCH_DIR / "evidence"

for _p in [str(_RESEARCH_DIR), str(_EVIDENCE_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import engine as E
import governance as G
from signals import sig_volatility_regime, sig_market_regime, sig_momentum

# ── Constants ─────────────────────────────────────────────────────────────────
N_TRIALS = 34         # FROZEN: Phase B battery size
COST_BPS = E.REALISTIC_COST   # 15 bps — matches IS battery
ANN      = E.ANN               # 365.0

# ── Signal specs (hold_days=1 matches IS battery evaluation) ─────────────────
SIGNAL_SPECS = {
    "h5_vol_regime": {
        "fn":        sig_volatility_regime,
        "params":    {"rv_win": 21, "pctile": 33, "regime_win": 252},
        "hold_days": 1,
        "lib_name":  "H5 Volatility Regimes",
    },
    "g1_market_regime_sma200": {
        "fn":        sig_market_regime,
        "params":    {"win": 200, "lag": 20},
        "hold_days": 1,
        "lib_name":  "Market Regime (SMA200 slope)",
    },
    "g3_momentum_tsmom": {
        "fn":        sig_momentum,
        "params":    {"k": 30},
        "hold_days": 1,
        "lib_name":  "Momentum (TSMOM)",
    },
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_library() -> dict[str, float]:
    """
    Load evidence_library.json; return {signal_name: sharpe_at_15bps_annualized}.
    Handles the Python-style bare NaN values in the JSON (non-standard).
    """
    lib_path = _RESEARCH_DIR / "reports" / "evidence_library" / "evidence_library.json"
    raw = lib_path.read_text(encoding="utf-8")
    # Replace bare NaN (Python-style) with null so json.loads accepts it
    raw = re.sub(r':\s*NaN\b', ': null', raw)
    lib = json.loads(raw)

    sharpes: dict[str, float] = {}
    for name, rec in lib.items():
        s = rec.get("sharpe")
        if s is not None:
            sharpes[name] = float(s)
    return sharpes


def _get_net_returns(fn, params: dict, hold_days: int) -> np.ndarray:
    """
    Re-evaluate a signal and return the full daily net-return array (after
    hold mechanics and 15 bps cost).  NaN forward-return days are excluded.
    """
    m        = E.load_master()
    pos_raw  = fn(m, **params)
    pos_full = pos_raw.reindex(m.index).fillna(0.0).to_numpy(dtype=float)
    position = E._apply_hold(pos_full, hold_days)
    ret      = m["ret_fwd1"].to_numpy(dtype=float)
    valid    = ~np.isnan(ret)
    net      = E._net_daily(position, np.where(valid, ret, 0.0), COST_BPS)
    return net[valid]


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 62)
    print(" DSR / BH-FDR Registration Pre-computation")
    print(f" n_trials={N_TRIALS} (FROZEN)  cost_bps={COST_BPS}")
    print("=" * 62)

    # 1. Load all 34 Sharpe values; compute sr_trials_std ─────────────────────
    lib_sharpes = _load_library()
    n_found = len(lib_sharpes)
    print(f"\n[1] evidence_library.json: {n_found} signals loaded")
    if n_found != N_TRIALS:
        print(f"    WARNING: expected {N_TRIALS}, got {n_found}. Check library.")

    daily_srs = np.array([s / math.sqrt(ANN) for s in lib_sharpes.values()])
    sr_trials_std = float(np.std(daily_srs, ddof=1))
    print(f"    sr_trials_std (per-period, ddof=1): {sr_trials_std:.6f}")

    # 2. Re-evaluate h5, g1, g3 to get exact daily net-return arrays ──────────
    print("\n[2] Re-evaluating signals against IS corpus …")
    print("    (Requires research/data/raw/ corpus. Takes ~30-60s.)")
    nets: dict[str, np.ndarray] = {}
    for sig_id, spec in SIGNAL_SPECS.items():
        nets[sig_id] = _get_net_returns(spec["fn"], spec["params"], spec["hold_days"])
        n   = len(nets[sig_id])
        mu  = nets[sig_id].mean()
        std = nets[sig_id].std(ddof=1)
        sr_annual = float(mu / std * math.sqrt(ANN)) if std > 0 else float("nan")
        print(f"    {sig_id}: n={n}, daily_mean={mu:.6f}, "
              f"daily_std={std:.6f}, sharpe_annual={sr_annual:.4f}")

    # 3. Compute DSR and BH-FDR p-value for each signal ───────────────────────
    print("\n[3] Computing DSR and BH-FDR p-values …")
    results: dict[str, dict] = {}
    for sig_id, net in nets.items():
        dsr_prob, sr_star, sr_obs = G.deflated_sr(net, sr_trials_std, N_TRIALS)
        psr_prob, _  = G.psr(net, sr_benchmark=0.0)
        bh_p         = float(1.0 - psr_prob) if not math.isnan(psr_prob) else float("nan")
        results[sig_id] = {
            "dsr_prob": dsr_prob,
            "sr_star":  sr_star,
            "sr_obs":   sr_obs,
            "bh_p":     bh_p,
        }

    # 4. BH-FDR across all 34 signals (approximation for non-registered 31) ──
    # For each signal not re-evaluated exactly, compute a PSR approximation:
    #   psr_approx = Φ(sr_daily × √(n−1))  [ignores skew/kurtosis]
    # This is sufficient to establish that no signal rejects BH-FDR at q=0.10.
    from scipy.stats import norm as _norm
    n_obs_est = int(len(next(iter(nets.values()))))  # corpus length ≈ same for all

    all_pvals: dict[str, float] = {}
    # Map library name → exact p-value for the three registered signals
    exact_by_lib: dict[str, float] = {
        SIGNAL_SPECS[s]["lib_name"]: results[s]["bh_p"]
        for s in SIGNAL_SPECS
    }
    for lib_name, annual_sr in lib_sharpes.items():
        if lib_name in exact_by_lib:
            all_pvals[lib_name] = exact_by_lib[lib_name]
        else:
            sr_daily  = annual_sr / math.sqrt(ANN)
            z         = sr_daily * math.sqrt(max(n_obs_est - 1, 1))
            all_pvals[lib_name] = float(1.0 - _norm.cdf(z))

    bh_result = G.bh_fdr(all_pvals, q=0.10)

    # 5. Print results ─────────────────────────────────────────────────────────
    print("\n" + "=" * 62)
    print(" RESULTS — paste into pre-registration draft JSONs")
    print("=" * 62)

    for sig_id in SIGNAL_SPECS:
        r     = results[sig_id]
        bhr   = bh_result.get(SIGNAL_SPECS[sig_id]["lib_name"], {})
        print(f"\n── {sig_id} ──")
        print(f'  "dsr_prob": {r["dsr_prob"]:.4f},')
        print(f'  "bh_p":     {r["bh_p"]:.4f},')
        print(f"  (sr_obs={r['sr_obs']:.6f}, sr_star={r['sr_star']:.6f})")
        if bhr:
            print(f"  BH-FDR: rank={bhr['rank']}/{N_TRIALS}, "
                  f"bh_threshold={bhr['bh_threshold']:.5f}, "
                  f"reject={bhr['reject']}")

    # 6. Gate summary ──────────────────────────────────────────────────────────
    print("\n" + "=" * 62)
    print(" GATE SUMMARY (IS data — informational, not gating)")
    print("=" * 62)
    print(f"  Validated gate requires: dsr_prob >= 0.50 AND bh_p <= 0.10")
    print(f"  (Evaluated on FORWARD trade returns at Validated promotion time)")
    for sig_id, r in results.items():
        dsr_pass = r["dsr_prob"] >= 0.50
        bh_pass  = r["bh_p"] <= 0.10
        status   = "PASS" if (dsr_pass and bh_pass) else "FAIL (IS -- expected)"
        print(f"  {sig_id}: dsr={r['dsr_prob']:.4f} {'ok' if dsr_pass else 'NO'}  "
              f"bh_p={r['bh_p']:.4f} {'ok' if bh_pass else 'NO'}  [{status}]")

    # 7. Full BH-FDR table ─────────────────────────────────────────────────────
    print("\n" + "=" * 62)
    print(f" FULL BH-FDR TABLE ({N_TRIALS} signals, q=0.10)")
    print("=" * 62)
    print(f"  {'Signal':<40} {'p':>8}  {'rank':>4}  {'BH_thr':>8}  {'reject'}")
    for name, d in bh_result.items():
        marker = " ← REGISTERED" if name in exact_by_lib else ""
        exact_flag = "*" if name in exact_by_lib else " "
        print(f"  {exact_flag}{name:<39} {d['p']:>8.4f}  {d['rank']:>4}  "
              f"{d['bh_threshold']:>8.5f}  {d['reject']}{marker}")
    print(f"\n  * = exact PSR from re-evaluated return series")
    print(f"    = approximated PSR (Φ(SR/√365 × √(n-1)), no skew/kurtosis)")
    any_reject = any(d["reject"] for d in bh_result.values())
    print(f"\n  Any signal rejects BH-FDR at q=0.10: {any_reject}")


if __name__ == "__main__":
    main()
