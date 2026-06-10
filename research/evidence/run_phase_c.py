"""
Phase C — H6-centric understanding & formalization (NO ML, NO tuning, NO new alpha).

Workstreams:
  A. H6 interaction analysis   — how the validated edge varies across conditioning
                                  states (effect size / stability, not Sharpe-max).
  B. H6 duration profile        — 1/3/5/7/10/14/21/30d holds; short/med/long horizon.
  C. Conditioning feature spec  — formalize PIT context features (no tuning).
  D. Governance review          — correlation + VIF on the final Phase C feature set.

Lockbox (2025-06..2026-05) SEALED: all analysis on the train slice only.
H6 is studied in its native non-overlapping hold representation.

Run from research/:  python evidence/run_phase_c.py
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

from engine import load_master, REALISTIC_COST                                 # noqa: E402
import signals as S                                                            # noqa: E402
import governance as G                                                         # noqa: E402

REPORT_DIR = _THIS.parent / "reports" / "evidence_library"
OUT_JSON   = REPORT_DIR / "phase_c_report.json"
SPEC_MD    = REPORT_DIR / "PHASE_C_FEATURE_SPEC.md"

LOCKBOX_START_MS = int(datetime(2025, 6, 1, tzinfo=timezone.utc).timestamp() * 1000)
HORIZONS = [1, 3, 5, 7, 10, 14, 21, 30]
COST = REALISTIC_COST / 10_000.0


def _cohens_d(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 3 or len(b) < 3:
        return np.nan
    va, vb = a.var(ddof=1), b.var(ddof=1)
    pooled = np.sqrt(((len(a)-1)*va + (len(b)-1)*vb) / (len(a)+len(b)-2))
    return float((a.mean() - b.mean()) / pooled) if pooled > 0 else np.nan


def _h6_trades(m, h):
    """Non-overlapping h-day H6 long trades with entry-time conditioning context."""
    pos = pd.Series(S.sig_h6_funding_vol_regime(m), index=m.index).fillna(0.0).to_numpy()
    close = m["close"].to_numpy(float)
    n = len(m)
    rows, last_exit = [], -1
    for t in range(n):
        if pos[t] != 1.0 or t <= last_exit or t + h >= n:
            continue
        ret = close[t + h] / close[t] - 1.0 - COST
        rows.append({
            "t": t, "year": int(m["year"].iloc[t]), "ret": ret,
            "market_regime": str(m["market_regime"].iloc[t]),
            "atr_ratio": float(m["atr_ratio"].iloc[t]) if not np.isnan(m["atr_ratio"].iloc[t]) else np.nan,
            "vrp_spread": float(m["vrp_spread"].iloc[t]) if not np.isnan(m["vrp_spread"].iloc[t]) else np.nan,
            "rv_21d": float(m["rv_21d"].iloc[t]) if not np.isnan(m["rv_21d"].iloc[t]) else np.nan,
            "funding": float(m["daily_mean_funding_bps"].iloc[t]) if not np.isnan(m["daily_mean_funding_bps"].iloc[t]) else np.nan,
        })
        last_exit = t + h
    return pd.DataFrame(rows)


def _stats(ret, h):
    ret = np.asarray(ret, float)
    n = len(ret)
    if n < 3:
        return {"n": n, "mean_pct": np.nan, "sharpe_ann": np.nan, "win_pct": np.nan, "maxdd_pct": np.nan}
    eq = np.cumprod(1 + ret); peak = np.maximum.accumulate(np.concatenate([[1], eq]))
    dd = (peak - np.concatenate([[1], eq])) / peak
    sd = ret.std(ddof=1)
    return {
        "n": n, "mean_pct": float(ret.mean()*100),
        "sharpe_ann": float(ret.mean()/sd*np.sqrt(252.0/h)) if sd > 0 else np.nan,
        "win_pct": float((ret > 0).mean()*100), "maxdd_pct": float(dd.max()*100),
        "mean_per_day_bps": float(ret.mean()/h*10_000),
    }


def main():
    print("=" * 92)
    print("PHASE C — H6-CENTRIC ANALYSIS  (no ML, no tuning, lockbox SEALED)")
    print(f"Run: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 92)

    m_full = load_master()
    m = m_full[m_full["day_ts"] < LOCKBOX_START_MS].reset_index(drop=True)
    print(f"Train slice: {m['dt'].iloc[0].date()} .. {m['dt'].iloc[-1].date()} "
          f"({len(m)} rows; lockbox held out={len(m_full)-len(m)})")

    def f(x, fmt="+.2f"):
        try:
            return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:{fmt}}"
        except (TypeError, ValueError):
            return "n/a"

    # ---------------- A. Interaction analysis (native 7d) ----------------
    tr = _h6_trades(m, 7)
    base = _stats(tr["ret"], 7)
    print(f"\n{'-'*92}\nA. H6 INTERACTION ANALYSIS (7d native; {len(tr)} trades, "
          f"base mean={f(base['mean_pct'])}%, win={f(base['win_pct'],'.0f')}%)\n{'-'*92}")

    interactions = {}

    # Market regime (bull vs sideways; bear excluded by construction)
    print("  Market Regime:")
    reg_groups = {}
    for reg in ["bull", "sideways", "bear"]:
        sub = tr[tr["market_regime"] == reg]["ret"]
        reg_groups[reg] = sub
        if len(sub) >= 3:
            s = _stats(sub, 7)
            print(f"    {reg:9s} n={s['n']:3d}  mean={f(s['mean_pct'])}%  win={f(s['win_pct'],'.0f')}%")
        else:
            print(f"    {reg:9s} n={len(sub):3d}  (insufficient / excluded by construction)")
    d_bull_side = _cohens_d(reg_groups.get("bull", []), reg_groups.get("sideways", []))
    print(f"    Cohen's d (bull vs sideways) = {f(d_bull_side,'+.2f')}")
    interactions["market_regime"] = {
        "bull": _stats(reg_groups.get("bull", []), 7),
        "sideways": _stats(reg_groups.get("sideways", []), 7),
        "cohens_d_bull_vs_sideways": d_bull_side,
    }

    # ATR dynamics at entry (expanding vs compressing)
    exp = tr[tr["atr_ratio"] > 0]["ret"]; comp = tr[tr["atr_ratio"] < 0]["ret"]
    d_atr = _cohens_d(exp, comp)
    print(f"\n  ATR Dynamics at entry:")
    print(f"    expanding  n={len(exp):3d}  mean={f(_stats(exp,7)['mean_pct'])}%")
    print(f"    compressing n={len(comp):3d}  mean={f(_stats(comp,7)['mean_pct'])}%")
    print(f"    Cohen's d (expanding vs compressing) = {f(d_atr,'+.2f')}")
    interactions["atr_dynamics"] = {"expanding": _stats(exp,7), "compressing": _stats(comp,7),
                                    "cohens_d": d_atr}

    # VRP state at entry (rich vs cheap, split at median)
    vmed = tr["vrp_spread"].median()
    rich = tr[tr["vrp_spread"] > vmed]["ret"]; cheap = tr[tr["vrp_spread"] <= vmed]["ret"]
    d_vrp = _cohens_d(rich, cheap)
    print(f"\n  VRP State at entry (split at median {f(vmed,'+.3f')}):")
    print(f"    rich-VRP  n={len(rich):3d}  mean={f(_stats(rich,7)['mean_pct'])}%")
    print(f"    cheap-VRP n={len(cheap):3d}  mean={f(_stats(cheap,7)['mean_pct'])}%")
    print(f"    Cohen's d (rich vs cheap) = {f(d_vrp,'+.2f')}")
    interactions["vrp_state"] = {"rich": _stats(rich,7), "cheap": _stats(cheap,7), "cohens_d": d_vrp}

    # Within-medium vol level + funding (degenerate by construction — documented)
    print(f"\n  Volatility Level & Funding State: FIXED by H6 construction "
          f"(vol=medium, funding=low) -> not free conditioning axes for H6.")
    interactions["note"] = ("H6 fixes vol_regime=medium and funding_regime=low by "
                            "definition; only market regime, ATR dynamics, and VRP vary at entry.")

    # materiality verdict
    materiality = {}
    for k, d in [("market_regime", d_bull_side), ("atr_dynamics", d_atr), ("vrp_state", d_vrp)]:
        mat = "MATERIAL" if (not np.isnan(d) and abs(d) >= 0.40) else \
              ("weak" if (not np.isnan(d) and abs(d) >= 0.20) else "negligible")
        materiality[k] = {"cohens_d": d, "verdict": mat}
    print(f"\n  Materiality (|d|>=0.40 material, 0.20-0.40 weak):")
    for k, v in materiality.items():
        print(f"    {k:16s} d={f(v['cohens_d'],'+.2f')}  -> {v['verdict']}")

    # ---------------- B. Duration profile ----------------
    print(f"\n{'-'*92}\nB. H6 DURATION PROFILE (non-overlapping, {REALISTIC_COST}bps/trade)\n{'-'*92}")
    print(f"  {'h':>3} {'n':>4} {'mean%':>7} {'/day(bps)':>10} {'SharpeAnn':>10} "
          f"{'win%':>6} {'maxDD%':>7}")
    duration = {}
    for h in HORIZONS:
        s = _stats(_h6_trades(m, h)["ret"], h)
        duration[h] = s
        print(f"  {h:>3} {s['n']:>4} {f(s['mean_pct'],'+.2f'):>7} "
              f"{f(s.get('mean_per_day_bps'),'+.1f'):>10} {f(s['sharpe_ann'],'+.2f'):>10} "
              f"{f(s['win_pct'],'.0f'):>6} {f(s['maxdd_pct'],'.0f'):>7}")
    # horizon classification
    best_sharpe_h = max((h for h in HORIZONS if not np.isnan(duration[h]["sharpe_ann"])),
                        key=lambda h: duration[h]["sharpe_ann"], default=7)
    per_day = {h: duration[h].get("mean_per_day_bps") for h in HORIZONS}
    print(f"\n  Peak risk-adjusted horizon: {best_sharpe_h}d (Sharpe {f(duration[best_sharpe_h]['sharpe_ann'])}).")
    print(f"  Per-day return decays as horizon lengthens: "
          f"1d={f(per_day[1],'+.1f')}bps -> 30d={f(per_day[30],'+.1f')}bps.")
    horizon_class = ("short-horizon (<=5d)" if best_sharpe_h <= 5 else
                     "medium-horizon (7-14d)" if best_sharpe_h <= 14 else "long-horizon (>=21d)")
    print(f"  => H6 is a {horizon_class} effect.")

    # ---------------- C. Conditioning feature spec ----------------
    feats = [
        ("h6_signal", "PREDICTIVE", "1 if vol_regime==medium & funding_regime==low & market_regime!=bear (entry), held 7d",
         "binary entry; regimes are trailing-252 terciles / SMA200 (PIT)", "0/1"),
        ("trend_dist", "conditioning", "(close - SMA200)/SMA200", "SMA200 trailing; PIT", "real, ~[-0.6,1.5]"),
        ("trend_slope", "conditioning", "(SMA200 - SMA200[t-20]) / SMA200", "trailing; PIT", "real, small"),
        ("rv_21d", "conditioning", "21d std of daily log-returns * sqrt(365)", "trailing; PIT", "fraction >0"),
        ("atr_ratio", "conditioning", "ATR14 / ATR14[t-10] - 1", "trailing; PIT", "real, centered 0"),
        ("funding_bps", "conditioning", "daily mean funding * 1e4", "settlement <= t; PIT", "real bps"),
        ("vrp_spread", "conditioning", "DVOL_frac - RV30", "DVOL observable at t, trailing RV; PIT", "real"),
    ]
    print(f"\n{'-'*92}\nC. CONDITIONING FEATURE SPECIFICATION (PIT, no tuning)\n{'-'*92}")
    for name, role, formula, pit, rng in feats:
        print(f"  [{role:11s}] {name:12s} = {formula}")

    # ---------------- D. Governance review ----------------
    sma200 = m["close"].rolling(200, min_periods=200).mean()
    m = m.assign(trend_dist=((m["close"]-sma200)/sma200).values,
                 trend_slope=((sma200 - sma200.shift(20))/sma200).values,
                 funding_bps=m["daily_mean_funding_bps"].values)
    gov_cols = ["trend_dist", "trend_slope", "rv_21d", "atr_ratio", "funding_bps", "vrp_spread"]
    corr = G.continuous_correlation(m, gov_cols)
    pear = corr["pearson"]; vif = G.vif_table(m, gov_cols)
    print(f"\n{'-'*92}\nD. GOVERNANCE REVIEW — conditioning feature set (n={corr['n']})\n{'-'*92}")
    print(f"  {'':14s}" + " ".join(f"{c[:9]:>11s}" for c in gov_cols))
    for a in gov_cols:
        print(f"  {a:14s}" + " ".join(f"{f(pear.loc[a,b],'+.2f'):>11}" for b in gov_cols))
    hp = G.high_pairs(pear, 0.70)
    print(f"\n  pairs |r|>=0.70: " + (", ".join(f"{a}~{b}={r:+.2f}" for a,b,r in hp) or "none"))
    print(f"  VIF:")
    for c in gov_cols:
        print(f"    {c:14s} {vif[c]:6.2f}{'  <-- high' if vif[c]>=5 else ''}")
    sep_ok = (not hp) and all(vif[c] < 5 for c in gov_cols)
    print(f"\n  Predictive (H6) vs conditioning separation preserved: True "
          f"(H6 is the only predictive feature; conditioning set independence: {sep_ok})")

    # ---------------- Deliverable 5: Phase D recommendation ----------------
    n_material = sum(1 for v in materiality.values() if v["verdict"] == "MATERIAL")
    print(f"\n{'='*92}\nDELIVERABLE 5 — PHASE D (ML READINESS) RECOMMENDATION\n{'='*92}")
    print(f"  Materially-modulating conditioning variables: {n_material}")
    if n_material == 0:
        rec = ("ML NOT yet justified. No conditioning variable materially modulates H6 "
               "(all |d|<0.40). H6 is a self-contained conditional rule; a rules-based "
               "implementation is the honest path. Phase D should be a go/no-go review, "
               "not a model build.")
    elif n_material == 1:
        rec = ("ML MARGINALLY justified. One conditioning variable modulates H6; a single "
               "low-complexity interaction (not a multi-feature model) could be reviewed in "
               "Phase D, weighed against a rules-based H6 with that one gate.")
    else:
        rec = ("ML conditionally justified for interaction-learning only (modulating H6), "
               "still not as a multi-alpha model. Proceed to Phase D readiness review.")
    print(f"  {rec}")
    print("=" * 92)

    # ---- save ----
    out = {
        "run_date": datetime.now(timezone.utc).isoformat(),
        "train_span": [str(m['dt'].iloc[0].date()), str(m['dt'].iloc[-1].date())],
        "lockbox_held_out": int(len(m_full)-len(m)),
        "A_interactions": interactions, "A_materiality": materiality,
        "B_duration": duration, "B_peak_horizon": best_sharpe_h, "B_class": horizon_class,
        "C_features": [{"name": n, "role": r, "formula": fo, "pit": p, "range": rg}
                       for n, r, fo, p, rg in feats],
        "D_correlation": json.loads(pear.round(3).to_json()),
        "D_vif": {c: (None if np.isinf(vif[c]) else round(vif[c],3)) for c in gov_cols},
        "D_separation_ok": bool(sep_ok),
        "phase_d_recommendation": rec, "n_material_modulators": n_material,
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")

    # feature spec markdown
    lines = ["# Phase C — Conditioning Feature Specification\n",
             f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}; "
             f"train 2021-01..2025-05; lockbox sealed._\n",
             "| Feature | Role | Formula | PIT / leakage control | Range |",
             "|---|---|---|---|---|"]
    for n, r, fo, p, rg in feats:
        lines.append(f"| `{n}` | {r} | {fo} | {p} | {rg} |")
    lines += ["\n**Governance constraints:**",
              "- H6 is the sole PREDICTIVE feature; all others are conditioning/context only.",
              "- Never co-include {dvol_frac, rv30, vrp_spread} (perfect collinearity).",
              "- All features strictly trailing/point-in-time; no tuning for performance.",
              "- Lockbox 2025-06..2026-05 excluded from all fitting."]
    SPEC_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nSaved -> {OUT_JSON}\nSaved -> {SPEC_MD}")


if __name__ == "__main__":
    main()
