"""
Phase B.5 — Evidence Harmonization.

Applies the full governance framework uniformly to the KEEP-6 library, the same
bar the DVOL family faced:
  * DSR (deflated Sharpe, trial-count aware)   * BH-FDR
  * continuous-correlation audit               * VIF
  * lockbox discipline (2025-06..2026-05 sealed)

H6 is additionally evaluated in its NATIVE non-overlapping 7-day representation
(how it was validated and would trade), because the engine's daily-rebalance
lens under-measures hold-based signals.

No new signals, features, or hypotheses. Re-adjudication only.

Run from research/:  python evidence/run_harmonization.py
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
OUT_JSON   = REPORT_DIR / "harmonized_library.json"

LOCKBOX_START_MS = int(datetime(2025, 6, 1, tzinfo=timezone.utc).timestamp() * 1000)
DSR_CONF   = 0.95
INDEP_THRESH = 0.70

KEEP_NAMES = [
    "H6 Funding x Vol x Regime",
    "Market Regime (SMA200 slope)",
    "ATR Expansion",
    "ATR Compression",
    "Funding Level",
    "H5 Volatility Regimes",
]
# Each KEEP feature's continuous base-axis proxy (H6 is an interaction -> exempt)
KEEP_FEATURE = {
    "Market Regime (SMA200 slope)": "trend_dist",
    "H5 Volatility Regimes":        "rv_21d",
    "ATR Expansion":                "atr_ratio",
    "ATR Compression":              "atr_ratio",
    "Funding Level":                "daily_mean_funding_bps",
}
BASE_AXES = ["trend_dist", "rv_21d", "atr_ratio", "daily_mean_funding_bps"]


def _spec_by_name(name):
    for grp in S.REGISTRY.values():
        for sp in grp:
            if sp.name == name:
                return sp
    raise KeyError(name)


def _net_returns(m, spec):
    pos = pd.Series(spec.fn(m, **spec.params), index=m.index).reindex(m.index).fillna(0.0)
    pos = _apply_hold(pos.to_numpy(float), spec.hold_days)
    ret = m["ret_fwd1"].to_numpy(float)
    valid = ~np.isnan(ret)
    return _net_daily(pos, np.where(valid, ret, 0.0), REALISTIC_COST)[valid]


def _h6_native_trades(m, cost_bps=REALISTIC_COST, h=7):
    """Non-overlapping 7-day long trades for H6 (its validated representation)."""
    pos = pd.Series(S.sig_h6_funding_vol_regime(m), index=m.index).fillna(0.0).to_numpy()
    close = m["close"].to_numpy(float)
    n = len(m)
    cost = cost_bps / 10_000.0
    trades, last_exit = [], -1
    for t in range(n):
        if pos[t] != 1.0 or t <= last_exit or t + h >= n:
            continue
        r = close[t + h] / close[t] - 1.0 - cost
        trades.append(r)
        last_exit = t + h
    return np.array(trades)


def main():
    print("=" * 92)
    print("PHASE B.5 — EVIDENCE HARMONIZATION  (uniform governance on KEEP-6)")
    print(f"Run: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}  | lockbox SEALED")
    print("=" * 92)

    m_full = load_master()
    m = m_full[m_full["day_ts"] < LOCKBOX_START_MS].reset_index(drop=True)
    # continuous trend distance (base-axis proxy for Market Regime)
    sma200 = m["close"].rolling(200, min_periods=200).mean()
    m = m.assign(trend_dist=((m["close"] - sma200) / sma200).values)
    print(f"Train slice: {m['dt'].iloc[0].date()} .. {m['dt'].iloc[-1].date()} "
          f"({len(m)} rows; lockbox held out={len(m_full)-len(m)})")

    specs = {nm: _spec_by_name(nm) for nm in KEEP_NAMES}
    rows  = {nm: evaluate_signal(m, sp) for nm, sp in specs.items()}
    nets  = {nm: _net_returns(m, sp) for nm, sp in specs.items()}

    # ---- DSR setup (consistent with DVOL run: N=38, daily units) ----
    lib = json.loads(LIB_JSON.read_text(encoding="utf-8")) if LIB_JSON.exists() else {}
    lib_daily_sr = [r["sharpe"] / np.sqrt(ANN) for r in lib.values()
                    if isinstance(r.get("sharpe"), (int, float)) and not np.isnan(r["sharpe"])]
    sr_trials_std = float(np.std(np.array(lib_daily_sr), ddof=1))
    n_trials = len(lib) + 4   # 34 library + 4 DVOL headline = 38

    dsr, pvals = {}, {}
    for nm, net in nets.items():
        prob, sr_star, sr = G.deflated_sr(net, sr_trials_std, n_trials)
        psr0, _ = G.psr(net, 0.0)
        dsr[nm] = {"dsr_prob": prob, "sr_star_daily": sr_star, "sr_daily": sr, "psr0": psr0}
        pvals[nm] = 1.0 - psr0 if not np.isnan(psr0) else 1.0

    # ---- H6 native (non-overlapping 7d) ----
    h6t = _h6_native_trades(m)
    h6_psr0, h6_sr = G.psr(h6t, 0.0)
    h6_ann = float(h6_sr * np.sqrt(252.0 / 7)) if not np.isnan(h6_sr) else np.nan
    # native deflation in per-trade units: sr* scaled to per-trade trial dispersion
    sr_trials_std_trade = sr_trials_std * np.sqrt(7.0)   # rough per-7d scaling
    h6_dsr, h6_srstar, _ = G.deflated_sr(h6t, sr_trials_std_trade, n_trials)

    # ---- continuous-correlation + VIF on base axes ----
    corr = G.continuous_correlation(m, BASE_AXES)
    pear = corr["pearson"]
    vif  = G.vif_table(m, BASE_AXES)
    indep = {}
    for nm, feat in KEEP_FEATURE.items():
        others = [a for a in BASE_AXES if a != feat]
        indep[nm] = float(max(abs(pear.loc[feat, o]) for o in others))

    # ---- BH-FDR across KEEP-6 ----
    bh = G.bh_fdr(pvals, q=0.10)

    # ---- disposition ----
    disp = {}
    for nm, r in rows.items():
        oos_ok = bool(r["oos_sign_ok"]) or (r["wf_pass_frac"] >= 0.60)
        if nm == "H6 Funding x Vol x Regime":
            dsr_pass = (not np.isnan(h6_dsr)) and h6_dsr > DSR_CONF
            indep_ok = True   # interaction; exempt from base-axis collinearity gate
        else:
            dsr_pass = (not np.isnan(dsr[nm]["dsr_prob"])) and dsr[nm]["dsr_prob"] > DSR_CONF
            indep_ok = indep.get(nm, 1.0) < INDEP_THRESH
        if oos_ok and dsr_pass and indep_ok:
            d = "PREDICTIVE-KEEP"
        elif oos_ok or r["verdict"] in ("Strong", "Moderate"):
            d = "CONDITIONING"
        else:
            d = "REMOVE"
        disp[nm] = {"disposition": d, "oos_ok": oos_ok, "dsr_pass": dsr_pass,
                    "indep_ok": indep_ok, "max_corr": indep.get(nm)}

    # =================== REPORT ===================
    def f(x, fmt="+.3f"):
        try:
            return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:{fmt}}"
        except (TypeError, ValueError):
            return "n/a"

    print(f"\n{'-'*92}\n1. BATTERY (train, {REALISTIC_COST}bps) + DAILY DSR (N={n_trials}, "
          f"sr*={dsr[KEEP_NAMES[1]]['sr_star_daily']:.4f}/day)\n{'-'*92}")
    print(f"  {'Feature':30s} {'Sh':>6} {'OOS':>6} {'WF':>5} {'Ev':>4} "
          f"{'sr_day':>7} {'PSR0':>6} {'DSR':>6} {'pass':>5}")
    for nm, r in rows.items():
        dd = dsr[nm]
        print(f"  {nm:30s} {f(r['sharpe'],'+.2f'):>6} {f(r['oos_sharpe'],'+.2f'):>6} "
              f"{r['wf_pass']:>5} {f(r['evidence_score'],'.0f'):>4} "
              f"{f(dd['sr_daily'],'+.4f'):>7} {f(dd['psr0'],'.2f'):>6} "
              f"{f(dd['dsr_prob'],'.2f'):>6} "
              f"{str(dd['dsr_prob']>DSR_CONF if not np.isnan(dd['dsr_prob']) else False):>5}")

    print(f"\n{'-'*92}\n2. H6 NATIVE REPRESENTATION (non-overlapping 7d trades)\n{'-'*92}")
    print(f"  trades={len(h6t)}  per-trade SR={f(h6_sr,'+.3f')}  annualized Sharpe={f(h6_ann,'+.2f')}  "
          f"PSR(0)={f(h6_psr0,'.3f')}  DSR={f(h6_dsr,'.3f')}  pass={h6_dsr>DSR_CONF if not np.isnan(h6_dsr) else False}")
    print(f"  mean/trade={f(h6t.mean()*100,'+.2f')}%  win%={f((h6t>0).mean()*100,'.0f')}")

    print(f"\n{'-'*92}\n3. CONTINUOUS-CORRELATION (base axes; n={corr['n']})\n{'-'*92}")
    print(f"  {'':22s}" + " ".join(f"{a[:10]:>12s}" for a in BASE_AXES))
    for a in BASE_AXES:
        print(f"  {a:22s}" + " ".join(f"{f(pear.loc[a,b],'+.2f'):>12}" for b in BASE_AXES))
    hp = G.high_pairs(pear, INDEP_THRESH)
    print(f"  pairs |r|>={INDEP_THRESH}: " + (", ".join(f"{a}~{b}={r:+.2f}" for a,b,r in hp) or "none"))
    print("  (ATR Expansion & ATR Compression both map to atr_ratio -> one axis)")

    print(f"\n{'-'*92}\n4. VIF (base axes)\n{'-'*92}")
    for a in BASE_AXES:
        print(f"  {a:24s} VIF={vif[a]:6.2f}{'  <-- high' if vif[a]>=5 else ''}")

    print(f"\n{'-'*92}\n5. BH-FDR (q=0.10, daily PSR)\n{'-'*92}")
    for nm in sorted(bh, key=lambda k: bh[k]["rank"]):
        b = bh[nm]
        print(f"  {nm:30s} p={b['p']:.4f} rank={b['rank']} thr={b['bh_threshold']:.4f} "
              f"reject_null={b['reject']}")

    print(f"\n{'='*92}\n6. HARMONIZED DISPOSITION\n{'='*92}")
    for nm in KEEP_NAMES:
        d = disp[nm]
        mc = f", maxcorr={d['max_corr']:.2f}" if d["max_corr"] is not None else ""
        print(f"  {nm:30s} -> {d['disposition']:16s} "
              f"(oos={d['oos_ok']}, dsr={d['dsr_pass']}, indep={d['indep_ok']}{mc})")

    predictive = [nm for nm in KEEP_NAMES if disp[nm]["disposition"] == "PREDICTIVE-KEEP"]
    conditioning = [nm for nm in KEEP_NAMES if disp[nm]["disposition"] == "CONDITIONING"]
    removed = [nm for nm in KEEP_NAMES if disp[nm]["disposition"] == "REMOVE"]

    print(f"\n  PREDICTIVE core : {predictive or 'none'}")
    print(f"  CONDITIONING    : {conditioning or 'none'}")
    print(f"  REMOVE          : {removed or 'none'}")
    print(f"  Predictive core collapses to H6 alone: "
          f"{predictive == ['H6 Funding x Vol x Regime']}")
    print("=" * 92)

    out = {
        "run_date": datetime.now(timezone.utc).isoformat(),
        "train_span": [str(m["dt"].iloc[0].date()), str(m["dt"].iloc[-1].date())],
        "lockbox_held_out": int(len(m_full) - len(m)),
        "n_trials": n_trials, "sr_trials_std_daily": sr_trials_std,
        "daily_dsr": dsr,
        "h6_native": {"trades": int(len(h6t)), "per_trade_sr": h6_sr,
                      "annualized_sharpe": h6_ann, "psr0": h6_psr0, "dsr": h6_dsr,
                      "mean_trade_pct": float(h6t.mean()*100), "win_pct": float((h6t>0).mean()*100)},
        "base_axis_correlation": json.loads(pear.round(3).to_json()),
        "vif": {k: (None if np.isinf(v) else round(v,3)) for k,v in vif.items()},
        "independence_maxcorr": indep,
        "bh_fdr": bh,
        "disposition": disp,
        "predictive": predictive, "conditioning": conditioning, "removed": removed,
    }
    OUT_JSON.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"\nHarmonized library saved -> {OUT_JSON}")


if __name__ == "__main__":
    main()
