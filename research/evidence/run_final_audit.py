"""
Final Pre-Phase-C Audit
=======================
Capstone of the Phase B Evidence Discovery Program. No ML work may begin until
this audit is complete.

Identifies, across the full Evidence Library:
  * noisy features            (low evidence, random OOS)
  * redundant features        (re-representations of another signal)
  * highly correlated features (|position correlation| >= 0.70 clusters)
  * unstable features         (fail walk-forward / high LOO dispersion / OOS sign flip)
  * features to EXCLUDE from ML  vs  the minimal KEEP set for Phase C

Method: rebuild every signal's held daily position, compute the full correlation
matrix, greedily cluster at |r|>=0.70 (highest-evidence signal is the cluster
head), then cross with the stored evidence metrics to classify each signal.

Outputs:
  reports/evidence_library/FINAL_AUDIT.md
  reports/evidence_library/correlation_matrix.csv
"""
from __future__ import annotations

import json
import pathlib
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

_THIS_DIR = pathlib.Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

from engine import load_master, evaluate_signal, _apply_hold
import signals as S

REPORT_DIR = _THIS_DIR.parent / "reports" / "evidence_library"
LIB_JSON   = REPORT_DIR / "evidence_library.json"
AUDIT_MD   = REPORT_DIR / "FINAL_AUDIT.md"
CORR_CSV   = REPORT_DIR / "correlation_matrix.csv"

CORR_REDUNDANT = 0.70
CORR_OVERLAP   = 0.50

# Features explicitly retained as conditioning/explanatory variables even when
# weak as standalone strategies (per Rohith's Group-2 / Group-5 guidance).
CONDITIONING = {
    "H5 Volatility Regimes", "Volatility Regime",
    "Funding Level", "Market Regime (SMA200 slope)",
}

# Signals whose construction makes them duplicates regardless of measured
# correlation (correlation is understated when the signal is sparse).
DISGUISED_DUPLICATE = {
    "Inside Bar": "disguised trend-follower: construction is sign(close-SMA50) "
                  "on inside-bar days; correlation understated by sparsity",
}


def _arr(m, s):
    return pd.Series(s, index=m.index).reindex(m.index).fillna(0.0).to_numpy(float)


def main() -> None:
    m = load_master()

    # ---- rebuild every signal's HELD position (reflects real exposure overlap) ----
    specs = []
    for group in S.REGISTRY.values():
        specs.extend(group)
    positions = {}
    for sp in specs:
        raw = _arr(m, sp.fn(m, **sp.params))
        positions[sp.name] = _apply_hold(raw, sp.hold_days)
    names = list(positions.keys())
    P = pd.DataFrame(positions, index=m.index)

    # ---- correlation matrix ----
    corr = P.corr()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    corr.round(3).to_csv(CORR_CSV)

    # ---- load stored evidence metrics ----
    lib = {}
    if LIB_JSON.exists():
        with open(LIB_JSON) as fh:
            lib = json.load(fh)

    def ev(name, key, default=np.nan):
        return lib.get(name, {}).get(key, default)

    # ---- greedy correlation clustering (cluster head = highest evidence) ----
    order = sorted(names, key=lambda n: ev(n, "evidence_score", 0) or 0, reverse=True)
    assigned = {}
    clusters = {}
    for n in order:
        if n in assigned:
            continue
        clusters[n] = [n]
        assigned[n] = n
        for o in order:
            if o in assigned or o == n:
                continue
            r = corr.loc[n, o]
            if not np.isnan(r) and abs(r) >= CORR_REDUNDANT:
                clusters[n].append(o)
                assigned[o] = n

    # ---- classify every signal ----
    rows = []
    for n in names:
        verdict = ev(n, "verdict", "?")
        evs     = ev(n, "evidence_score", np.nan)
        stab    = ev(n, "stability_score", np.nan)
        sharpe  = ev(n, "sharpe", np.nan)
        oos     = ev(n, "oos_sharpe", np.nan)
        wf      = ev(n, "wf_pass_frac", np.nan)
        loocv   = ev(n, "loo_sharpe_cv", np.nan)
        oos_agree = ev(n, "oos_is_agree", False)
        head    = assigned.get(n, n)
        is_head = head == n

        redundant = (not is_head)
        unstable  = ((not np.isnan(wf)) and wf < 0.50) or (not oos_agree) \
                    or (isinstance(loocv, (int, float)) and not np.isnan(loocv) and loocv > 1.5)
        noisy     = (not np.isnan(evs)) and evs < 35 and verdict in ("Weak", "Reject")
        negative  = (not np.isnan(sharpe)) and sharpe < 0
        conditioning = n in CONDITIONING
        disguised = n in DISGUISED_DUPLICATE

        # KEEP only the minimal independent set: a cluster head that is either a
        # validated edge (Strong/Moderate, positive) or a retained conditioning
        # axis. Everything else is excluded. Redundant members are dropped even
        # when conditioning (the cluster head already carries that axis).
        reasons = []
        keep = False
        if disguised:
            reasons.append(DISGUISED_DUPLICATE[n])
        elif redundant:
            reasons.append(f"redundant with '{head}' (|r|>={CORR_REDUNDANT})")
        else:
            if verdict in ("Strong", "Moderate") and not negative:
                keep = True
                reasons.append("KEEP (independent edge)")
            elif conditioning and not negative:
                keep = True
                reasons.append("KEEP (conditioning/explanatory)")
            else:
                if verdict == "Reject":
                    reasons.append("rejected")
                if negative:
                    reasons.append("negative standalone edge")
                if noisy:
                    reasons.append("noisy/low-evidence")
                if not reasons:
                    reasons.append("weak; not worth ML inclusion")
        exclude = not keep

        rows.append(dict(
            name=n, group=ev(n, "group", "?"), verdict=verdict, evidence=evs,
            stability=stab, sharpe=sharpe, oos=oos, wf=wf,
            redundant=redundant, unstable=unstable, noisy=noisy, negative=negative,
            conditioning=conditioning, head=head, keep=keep, exclude=exclude,
            reasons="; ".join(reasons),
        ))
    R = pd.DataFrame(rows)

    keep_set    = R[R["keep"]].sort_values("evidence", ascending=False)
    exclude_set = R[R["exclude"]].sort_values("evidence", ascending=False)

    # =====================================================================
    # console summary
    # =====================================================================
    print("=" * 90)
    print("FINAL PRE-PHASE-C AUDIT")
    print("=" * 90)
    print(f"Signals audited: {len(names)}   "
          f"KEEP: {len(keep_set)}   EXCLUDE: {len(exclude_set)}")

    print(f"\nHighly-correlated clusters (|position r| >= {CORR_REDUNDANT}):")
    multi = {h: c for h, c in clusters.items() if len(c) > 1}
    if not multi:
        print("  (none)")
    for head, members in sorted(multi.items(),
                                key=lambda kv: ev(kv[0], "evidence_score", 0) or 0,
                                reverse=True):
        others = [x for x in members if x != head]
        print(f"  [{head}]  <- {', '.join(others)}")

    print(f"\nKEEP set (Phase C feature candidates):")
    for _, r in keep_set.iterrows():
        tag = "conditioning" if r["conditioning"] else "independent"
        print(f"  + {r['name']:32s} {r['verdict']:9s} ev={r['evidence']:>5.0f}  [{tag}]")

    print(f"\nEXCLUDE set ({len(exclude_set)}):")
    for _, r in exclude_set.iterrows():
        print(f"  - {r['name']:32s} {r['verdict']:9s}  {r['reasons']}")

    # =====================================================================
    # markdown report
    # =====================================================================
    L = []
    L.append("# Phase B — Final Pre-Phase-C Audit\n")
    L.append(f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_  ")
    L.append(f"_Signals audited: {len(names)} | KEEP: {len(keep_set)} | EXCLUDE: {len(exclude_set)}_\n")
    L.append("No ML work begins until this audit is accepted. Scoring is robustness-"
             "weighted; the goal is a minimal, independent, reproducible feature set.\n")

    L.append("## 1. Recommended KEEP set (Phase C inputs)\n")
    L.append("| Feature | Group | Verdict | Evidence | Stability | Role |")
    L.append("|---|---|---|---|---|---|")
    for _, r in keep_set.iterrows():
        role = "conditioning/explanatory" if r["conditioning"] else "independent edge"
        L.append(f"| {r['name']} | {r['group']} | {r['verdict']} | "
                 f"{r['evidence']:.0f} | {r['stability']:.2f} | {role} |")

    L.append("\n## 2. Highly-correlated / redundant clusters (|position r| >= 0.70)\n")
    if not multi:
        L.append("_No clusters above 0.70 besides trivial self._")
    for head, members in sorted(multi.items(),
                                key=lambda kv: ev(kv[0], "evidence_score", 0) or 0, reverse=True):
        others = [x for x in members if x != head]
        L.append(f"- **{head}** (cluster head) absorbs: {', '.join(others)}")

    L.append("\n## 3. Exclusion list\n")
    L.append("| Feature | Verdict | Reason(s) |")
    L.append("|---|---|---|")
    for _, r in exclude_set.iterrows():
        L.append(f"| {r['name']} | {r['verdict']} | {r['reasons']} |")

    L.append("\n## 4. Classification flags (all signals)\n")
    L.append("| Feature | Verdict | Ev | Sharpe | OOS | Redundant | Unstable | Noisy | Negative |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for _, r in R.sort_values("evidence", ascending=False).iterrows():
        def b(x): return "yes" if x else "-"
        L.append(f"| {r['name']} | {r['verdict']} | {r['evidence']:.0f} | "
                 f"{r['sharpe']:+.2f} | {r['oos']:+.2f} | {b(r['redundant'])} | "
                 f"{b(r['unstable'])} | {b(r['noisy'])} | {b(r['negative'])} |")

    L.append("\n## 5. Negative findings (high-confidence)\n")
    L.append("- **Candlestick reversal family** (Engulfing, Pin Bar, Outside Bar, "
             "Morning/Evening Star): no standalone daily edge; Engulfing actively loses.")
    L.append("- **Mean-reversion family** (RSI, Bollinger fade): two independent "
             "constructions both fail -> BTC daily mean-reversion has no edge.")
    L.append("- **Multi-timeframe structure**: adds no incremental information; "
             "components are redundant with daily trend/regime or are independent noise.")
    L.append("- **Volume/order-flow proxies**: volume is a statistically independent "
             "axis but carries no tradable signal (all negative Sharpe).")
    L.append("- **Unconditional funding & basis**: weak standalone; value is "
             "conditional/explanatory only (consistent with H1/H4).")

    with open(AUDIT_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))

    print(f"\nAudit report -> {AUDIT_MD}")
    print(f"Correlation matrix -> {CORR_CSV}")
    print("=" * 90)


if __name__ == "__main__":
    main()
