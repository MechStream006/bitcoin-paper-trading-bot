"""
DVOL-family evidence runs + governance overlays (HV-1, HV-2, HV-3).

Lockbox discipline: selection runs on 2021-03 .. 2025-05 only. The lockbox
(2025-06 .. 2026-05) is sliced out of the master before any evaluation and is
never touched here.

Pipeline:
  1. Evidence battery (engine.evaluate_signal) on the train slice.
  2. Continuous-correlation audit (Pearson + Spearman) of DVOL features vs the
     existing vol/funding features.
  3. VIF audit.
  4. DSR (deflated Sharpe, trial-count aware) + PSR.
  5. BH-FDR across the DVOL hypotheses.
  6. Disposition: KEEP / CONDITIONING / REJECT, with the HV-2 spread-vs-logratio
     diagnostic (true VRP vs residual vol-level exposure).

Run from research/:  python evidence/run_dvol_family.py
"""
from __future__ import annotations

import json
import pathlib
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

_THIS = pathlib.Path(__file__).resolve().parent
for p in (str(_THIS), str(_THIS.parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

from engine import (load_master, evaluate_signal, _apply_hold, _net_daily,   # noqa: E402
                    REALISTIC_COST, ANN)
import signals as S                                                            # noqa: E402
import governance as G                                                         # noqa: E402

REPORT_DIR = _THIS.parent / "reports" / "evidence_library"
LIB_JSON   = REPORT_DIR / "evidence_library.json"
OUT_JSON   = REPORT_DIR / "dvol_family_report.json"

LOCKBOX_START_MS = int(datetime(2025, 6, 1, tzinfo=timezone.utc).timestamp() * 1000)

# Continuous features for the correlation / VIF audit
EXISTING_FEATS = ["rv_21d", "rv30", "atr_ratio", "daily_mean_funding_bps"]
DVOL_FEATS     = ["dvol_frac", "dvol_pctile", "vol_of_vol", "vrp_spread", "vrp_logratio"]
# Map each hypothesis to its primary continuous feature (for independence)
SIG_FEATURE = {
    "HV-1 Implied Vol Regime": "dvol_pctile",
    "HV-2 VRP Spread":         "vrp_spread",
    "HV-2 VRP LogRatio":       "vrp_logratio",
    "HV-3 Vol-of-Vol":         "vol_of_vol",
}
INDEP_THRESH = 0.70
DSR_CONF     = 0.95


def _net_returns(m: pd.DataFrame, spec) -> np.ndarray:
    pos = pd.Series(spec.fn(m, **spec.params), index=m.index).reindex(m.index).fillna(0.0)
    pos = _apply_hold(pos.to_numpy(float), spec.hold_days)
    ret = m["ret_fwd1"].to_numpy(float)
    valid = ~np.isnan(ret)
    net = _net_daily(pos, np.where(valid, ret, 0.0), REALISTIC_COST)
    return net[valid]


def main() -> None:
    print("=" * 92)
    print("DVOL-FAMILY EVIDENCE RUN  (HV-1, HV-2, HV-3)  --  lockbox 2025-06..2026-05 SEALED")
    print(f"Run date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 92)

    m_full = load_master()
    m = m_full[m_full["day_ts"] < LOCKBOX_START_MS].reset_index(drop=True)
    print(f"Train slice: {m['dt'].iloc[0].date()} .. {m['dt'].iloc[-1].date()}  "
          f"({len(m)} rows; full={len(m_full)}, lockbox held out={len(m_full)-len(m)})")

    specs = S.DVOL_FAMILY
    # ---- 1. Evidence battery ----
    rows = {sp.name: evaluate_signal(m, sp) for sp in specs}
    nets = {sp.name: _net_returns(m, sp) for sp in specs}

    # ---- 2. Correlation audit (continuous features) ----
    cols = EXISTING_FEATS + DVOL_FEATS
    corr = G.continuous_correlation(m, cols)
    pear = corr["pearson"]; spear = corr["spearman"]

    # independence of each DVOL feature vs existing vol/funding features
    indep = {}
    for name, feat in SIG_FEATURE.items():
        cmax = max(abs(pear.loc[feat, e]) for e in EXISTING_FEATS)
        indep[name] = float(cmax)

    # ---- 3. VIF ----
    vif = G.vif_table(m, cols)

    # ---- 4. DSR / PSR ----
    # cross-trial SR dispersion: library annualized sharpes -> daily, plus DVOL
    lib = json.loads(LIB_JSON.read_text(encoding="utf-8")) if LIB_JSON.exists() else {}
    lib_daily_sr = [r["sharpe"] / np.sqrt(ANN) for r in lib.values()
                    if isinstance(r.get("sharpe"), (int, float)) and not np.isnan(r["sharpe"])]
    dvol_daily_sr = []
    for nm, net in nets.items():
        if len(net) > 5 and net.std(ddof=1) > 0:
            dvol_daily_sr.append(float(net.mean() / net.std(ddof=1)))
    sr_trials_std = float(np.std(np.array(lib_daily_sr + dvol_daily_sr), ddof=1))
    n_trials = len(lib) + len(specs)        # headline trial count (34 + 4)

    dsr = {}
    pvals = {}
    for nm, net in nets.items():
        prob, sr_star, sr = G.deflated_sr(net, sr_trials_std, n_trials)
        psr0, _ = G.psr(net, 0.0)
        dsr[nm] = {"dsr_prob": prob, "sr_star_daily": sr_star, "sr_daily": sr,
                   "psr0": psr0}
        pvals[nm] = 1.0 - psr0 if not np.isnan(psr0) else 1.0

    # ---- 5. BH-FDR ----
    bh = G.bh_fdr(pvals, q=0.10)

    # ---- 6. Disposition ----
    disp = {}
    for nm in rows:
        r = rows[nm]
        oos_ok = bool(r["oos_sign_ok"]) or (r["wf_pass_frac"] >= 0.60)
        dsr_pass = (not np.isnan(dsr[nm]["dsr_prob"])) and dsr[nm]["dsr_prob"] > DSR_CONF
        indep_ok = indep[nm] < INDEP_THRESH
        if oos_ok and dsr_pass and indep_ok:
            d = "KEEP"
        elif (oos_ok or r["verdict"] in ("Strong", "Moderate")) and (not indep_ok or not dsr_pass):
            d = "CONDITIONING"
        else:
            d = "REJECT"
        disp[nm] = {"disposition": d, "oos_ok": oos_ok, "dsr_pass": dsr_pass,
                    "indep_ok": indep_ok, "max_corr_existing": indep[nm]}

    # ---- HV-2 family diagnostic ----
    sp_corr_rv = abs(pear.loc["vrp_spread", "rv_21d"])
    lr_corr_rv = abs(pear.loc["vrp_logratio", "rv_21d"])
    sp_lr_corr = abs(pear.loc["vrp_spread", "vrp_logratio"])
    sp_oos = rows["HV-2 VRP Spread"]["oos_sharpe"]
    lr_oos = rows["HV-2 VRP LogRatio"]["oos_sharpe"]
    sign_consistent = (np.sign(rows["HV-2 VRP Spread"]["sharpe"]) ==
                       np.sign(rows["HV-2 VRP LogRatio"]["sharpe"]))
    if sign_consistent and (disp["HV-2 VRP Spread"]["disposition"] != "REJECT" or
                            disp["HV-2 VRP LogRatio"]["disposition"] != "REJECT"):
        if lr_corr_rv < sp_corr_rv - 0.10 and disp["HV-2 VRP LogRatio"]["indep_ok"]:
            hv2_diag = "true-VRP (log-ratio is level-neutral and carries the edge)"
        else:
            hv2_diag = "true-VRP (sign-consistent across both forms)"
    elif (not sign_consistent):
        hv2_diag = "AMBIGUOUS (forms disagree in sign)"
    else:
        hv2_diag = "residual-vol-level or no edge (forms do not survive)"

    # =================== REPORT ===================
    def f(x, fmt="+.3f"):
        try:
            return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:{fmt}}"
        except (TypeError, ValueError):
            return "n/a"

    print(f"\n{'-'*92}\n1. EVIDENCE BATTERY (train slice, {REALISTIC_COST}bps cost)\n{'-'*92}")
    print(f"  {'Hypothesis':26s} {'Sh':>6} {'Sortino':>8} {'Calmar':>7} {'MDD%':>6} "
          f"{'Win%':>6} {'N':>4} {'OOS':>6} {'WF':>6} {'Ev':>5} {'Verdict':>9}")
    for nm, r in rows.items():
        print(f"  {nm:26s} {f(r['sharpe'],'+.2f'):>6} {f(r['sortino'],'+.2f'):>8} "
              f"{f(r['calmar'],'+.2f'):>7} {f(r['max_dd_pct'],'.0f'):>6} "
              f"{f(r['win_rate_pct'],'.0f'):>6} {r['n_trades']:>4} "
              f"{f(r['oos_sharpe'],'+.2f'):>6} {r['wf_pass']:>6} "
              f"{f(r['evidence_score'],'.0f'):>5} {r['verdict']:>9}")

    print(f"\n{'-'*92}\n2. CONTINUOUS-CORRELATION AUDIT (Pearson; n={corr['n']})\n{'-'*92}")
    print("  DVOL feature vs existing vol/funding (max |r| drives independence):")
    print(f"  {'feature':14s} " + " ".join(f"{e[:10]:>11s}" for e in EXISTING_FEATS))
    for feat in DVOL_FEATS:
        print(f"  {feat:14s} " + " ".join(f"{f(pear.loc[feat,e],'+.2f'):>11}" for e in EXISTING_FEATS))
    print(f"\n  VRP diagnostic: |r(spread,rv21)|={sp_corr_rv:.2f}  "
          f"|r(logratio,rv21)|={lr_corr_rv:.2f}  |r(spread,logratio)|={sp_lr_corr:.2f}")
    hp = G.high_pairs(pear, INDEP_THRESH)
    print(f"  pairs |r|>={INDEP_THRESH}: " + (", ".join(f"{a}~{b}={r:+.2f}" for a, b, r in hp) or "none"))

    print(f"\n{'-'*92}\n3. VIF TABLE\n{'-'*92}")
    for c in cols:
        flag = "  <-- high" if vif[c] >= 5 else ""
        print(f"  {c:24s} VIF={vif[c]:6.2f}{flag}")

    print(f"\n{'-'*92}\n4. DEFLATED SHARPE (per-period; N_trials={n_trials}, "
          f"sr_trials_std={sr_trials_std:.4f})\n{'-'*92}")
    print(f"  {'Hypothesis':26s} {'sr_daily':>9} {'sr*':>8} {'PSR(0)':>8} {'DSR':>8} {'pass>0.95':>9}")
    for nm in rows:
        dd = dsr[nm]
        print(f"  {nm:26s} {f(dd['sr_daily'],'+.4f'):>9} {f(dd['sr_star_daily'],'+.4f'):>8} "
              f"{f(dd['psr0'],'.3f'):>8} {f(dd['dsr_prob'],'.3f'):>8} "
              f"{str(dd['dsr_prob']>DSR_CONF if not np.isnan(dd['dsr_prob']) else False):>9}")

    print(f"\n{'-'*92}\n5. BH-FDR (q=0.10; p = 1-PSR(0))\n{'-'*92}")
    for nm in sorted(bh, key=lambda k: bh[k]["rank"]):
        b = bh[nm]
        print(f"  {nm:26s} p={b['p']:.4f}  rank={b['rank']}  "
              f"thr={b['bh_threshold']:.4f}  reject_null={b['reject']}")

    print(f"\n{'-'*92}\n6. DISPOSITION\n{'-'*92}")
    for nm in rows:
        d = disp[nm]
        print(f"  {nm:26s} -> {d['disposition']:13s} "
              f"(oos_ok={d['oos_ok']}, dsr_pass={d['dsr_pass']}, "
              f"indep_ok={d['indep_ok']} maxcorr={d['max_corr_existing']:.2f})")
    print(f"\n  HV-2 family diagnostic: {hv2_diag}")

    # ---- updated independent feature library (KEEP set) ----
    prior_keep = [n for n, r in lib.items() if r.get("verdict") == "Strong"] if lib else []
    dvol_keep  = [nm for nm in rows if disp[nm]["disposition"] == "KEEP"]
    dvol_cond  = [nm for nm in rows if disp[nm]["disposition"] == "CONDITIONING"]

    print(f"\n{'='*92}\nUPDATED INDEPENDENT FEATURE LIBRARY\n{'='*92}")
    print("  Prior KEEP (from final audit): H6, Market Regime, ATR Expansion, "
          "ATR Compression, Funding Level, H5 Vol Regimes")
    print(f"  DVOL KEEP (independent edge): {dvol_keep or 'none'}")
    print(f"  DVOL CONDITIONING:           {dvol_cond or 'none'}")
    print("=" * 92)

    # ---- save ----
    out = {
        "run_date": datetime.now(timezone.utc).isoformat(),
        "train_span": [str(m["dt"].iloc[0].date()), str(m["dt"].iloc[-1].date())],
        "lockbox_held_out": int(len(m_full) - len(m)),
        "n_trials": n_trials, "sr_trials_std": sr_trials_std,
        "battery": {nm: {k: (None if isinstance(v, float) and np.isnan(v) else v)
                         for k, v in r.items()
                         if k in ("sharpe", "sortino", "calmar", "max_dd_pct",
                                  "win_rate_pct", "n_trades", "oos_sharpe", "is_sharpe",
                                  "wf_pass", "wf_pass_frac", "evidence_score",
                                  "stability_score", "verdict")}
                    for nm, r in rows.items()},
        "independence_maxcorr": indep,
        "vif": {k: (None if np.isinf(v) else round(v, 3)) for k, v in vif.items()},
        "dsr": dsr,
        "bh_fdr": bh,
        "disposition": disp,
        "hv2_diagnostic": hv2_diag,
        "correlation_pearson": json.loads(pear.round(3).to_json()),
        "dvol_keep": dvol_keep, "dvol_conditioning": dvol_cond,
    }
    OUT_JSON.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"\nReport saved -> {OUT_JSON}")


if __name__ == "__main__":
    main()
