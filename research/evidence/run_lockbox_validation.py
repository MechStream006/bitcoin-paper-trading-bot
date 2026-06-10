"""
Phase D.5 — Lockbox Validation (ONE-TIME, frozen rules only).

Opens the sealed lockbox (2025-06-01 .. 2026-05-31) exactly once to validate the
frozen H6 strategy family. NO parameter/threshold/feature/signal changes, no
optimization, no retraining.

  A. Pure H6                : long when medium-vol & low-funding & non-bear, 7d hold
  B. H6 + ATR-expansion gate: A, restricted to entries where atr_ratio > 0

Everything is computed from the same frozen definitions used in train/harmonization.
Regime labels for lockbox rows use trailing-252 windows (backward-looking, PIT) —
that is how the strategy would run live and is not lockbox leakage.

Run from research/:  python evidence/run_lockbox_validation.py
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

LB_START = int(datetime(2025, 6, 1,  tzinfo=timezone.utc).timestamp() * 1000)
LB_END   = int(datetime(2026, 5, 31, tzinfo=timezone.utc).timestamp() * 1000)
HOLD = 7
COST = REALISTIC_COST / 10_000.0

# Train-period expectations (from Phase C, frozen reference — not re-computed here)
TRAIN_A = {"n": 31, "mean_pct": 2.64, "win_pct": 52.0, "sharpe_ann": 2.22}
TRAIN_B = {"n": 14, "mean_pct": 4.30}


def _h6_trades(m, lo_ms, hi_ms):
    """Frozen non-overlapping 7d H6 trades that ENTER within [lo,hi]."""
    pos = pd.Series(S.sig_h6_funding_vol_regime(m), index=m.index).fillna(0.0).to_numpy()
    close = m["close"].to_numpy(float)
    day = m["day_ts"].to_numpy()
    atr = m["atr_ratio"].to_numpy(float)
    yr = m["year"].to_numpy()
    n = len(m); rows = []; last_exit = -1
    for t in range(n):
        if pos[t] != 1.0 or t <= last_exit:
            continue
        if day[t] < lo_ms or day[t] > hi_ms:
            continue
        if t + HOLD >= n:                      # exit must be observable
            continue
        ret = close[t + HOLD] / close[t] - 1.0 - COST
        rows.append({"year": int(yr[t]), "ret": ret,
                     "atr_ratio": float(atr[t]) if not np.isnan(atr[t]) else np.nan,
                     "day_ts": int(day[t])})
        last_exit = t + HOLD
    return pd.DataFrame(rows)


def _metrics(ret, cal_days):
    ret = np.asarray(ret, float)
    nt = len(ret)
    if nt == 0:
        return {"n": 0}
    eq = np.cumprod(1 + ret)
    ext = np.concatenate([[1.0], eq])
    peak = np.maximum.accumulate(ext)
    dd = (peak - ext) / peak
    maxdd = float(dd.max())
    final = float(eq[-1])
    cagr = float(final ** (365.0 / cal_days) - 1.0) if (cal_days > 0 and final > 0) else float(final - 1.0)
    mu, sd = float(ret.mean()), float(ret.std(ddof=1)) if nt > 1 else np.nan
    ann = np.sqrt(252.0 / HOLD)
    sharpe = float(mu / sd * ann) if (sd and sd > 0) else np.nan
    neg = ret[ret < 0]
    dstd = float(np.sqrt(np.mean(neg ** 2))) if len(neg) >= 2 else np.nan
    sortino = float(mu / dstd * ann) if (dstd and dstd > 0) else np.nan
    calmar = float(cagr / maxdd) if maxdd > 0 else np.nan
    gp = float(ret[ret > 0].sum()); gl = float(-ret[ret < 0].sum())
    pf = float(gp / gl) if gl > 0 else (np.inf if gp > 0 else np.nan)
    return {"n": nt, "mean_pct": mu * 100, "total_ret_pct": (final - 1) * 100,
            "cagr_pct": cagr * 100, "sharpe": sharpe, "sortino": sortino,
            "calmar": calmar, "maxdd_pct": maxdd * 100,
            "win_pct": float((ret > 0).mean() * 100), "profit_factor": pf,
            "exposure_pct": nt * HOLD / cal_days * 100}


def main():
    print("=" * 90)
    print("PHASE D.5 — LOCKBOX VALIDATION (ONE-TIME, frozen H6 rules)")
    print(f"Lockbox: 2025-06-01 .. 2026-05-31  |  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 90)

    m = load_master()
    cal_days = (LB_END - LB_START) / 86_400_000 + 1

    trA = _h6_trades(m, LB_START, LB_END)
    trB = trA[trA["atr_ratio"] > 0].copy()

    A = _metrics(trA["ret"].to_numpy(), cal_days)
    B = _metrics(trB["ret"].to_numpy(), cal_days)

    def f(x, fmt="+.2f"):
        try:
            return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:{fmt}}"
        except (TypeError, ValueError):
            return "n/a"

    print(f"\n{'-'*90}\n1-3. LOCKBOX PERFORMANCE (7d hold, {REALISTIC_COST}bps/trade, "
          f"{int(cal_days)} calendar days)\n{'-'*90}")
    hdr = f"  {'Metric':16s} {'A: Pure H6':>14s} {'B: H6 + ATR-exp':>16s}"
    print(hdr)
    for label, key in [("Trades", "n"), ("Total return %", "total_ret_pct"),
                       ("CAGR %", "cagr_pct"), ("Sharpe", "sharpe"),
                       ("Sortino", "sortino"), ("Calmar", "calmar"),
                       ("MaxDD %", "maxdd_pct"), ("Win %", "win_pct"),
                       ("Profit Factor", "profit_factor"), ("Mean/trade %", "mean_pct"),
                       ("Exposure %", "exposure_pct")]:
        av = A.get(key); bv = B.get(key)
        fmt = ".0f" if key == "n" else "+.2f"
        print(f"  {label:16s} {f(av,fmt):>14s} {f(bv,fmt):>16s}")

    print(f"\n{'-'*90}\n4. YEAR CONTRIBUTION (P&L sum by calendar year)\n{'-'*90}")
    for label, tr in [("A: Pure H6", trA), ("B: H6+ATR", trB)]:
        print(f"  {label}:")
        if len(tr):
            for y, g in tr.groupby("year"):
                print(f"    {y}: n={len(g):2d}  pnl={f(g['ret'].sum()*100):>7}%  "
                      f"mean={f(g['ret'].mean()*100):>6}%  win={f((g['ret']>0).mean()*100,'.0f')}%")
        else:
            print("    (no trades)")

    print(f"\n{'-'*90}\n5. LOCKBOX vs TRAIN EXPECTATIONS\n{'-'*90}")
    print(f"  A Pure H6     train: n={TRAIN_A['n']} mean={TRAIN_A['mean_pct']:+.2f}% "
          f"win={TRAIN_A['win_pct']:.0f}% Sharpe={TRAIN_A['sharpe_ann']:+.2f}")
    print(f"  A Pure H6   lockbox: n={A['n']} mean={f(A.get('mean_pct'))}% "
          f"win={f(A.get('win_pct'),'.0f')}% Sharpe={f(A.get('sharpe'))}")
    print(f"  B H6+ATR      train: n={TRAIN_B['n']} mean={TRAIN_B['mean_pct']:+.2f}%")
    print(f"  B H6+ATR    lockbox: n={B['n']} mean={f(B.get('mean_pct'))}%")

    # ---- 6. ATR modulator survival ----
    a_mean = A.get("mean_pct", np.nan); b_mean = B.get("mean_pct", np.nan)
    atr_survives = (B.get("n", 0) >= 3 and not np.isnan(b_mean) and not np.isnan(a_mean)
                    and b_mean > a_mean)
    print(f"\n{'-'*90}\n6. ATR MODULATOR SURVIVAL\n{'-'*90}")
    print(f"  Train: ATR-expansion lifted mean/trade {TRAIN_A['mean_pct']:+.2f}% -> {TRAIN_B['mean_pct']:+.2f}%")
    print(f"  Lockbox: A mean={f(a_mean)}%  B mean={f(b_mean)}%  (B n={B.get('n',0)})")
    print(f"  ATR survives as a genuine modulator: {atr_survives}"
          + ("" if B.get("n", 0) >= 3 else "  (insufficient B trades to judge)"))

    # ---- 7. Recommendation ----
    a_ok = (A.get("n", 0) >= 4 and not np.isnan(A.get("sharpe", np.nan))
            and A["sharpe"] > 0 and A.get("mean_pct", 0) > 0)
    print(f"\n{'='*90}\n7. FINAL RECOMMENDATION\n{'='*90}")
    if not a_ok:
        rec = "REJECT BOTH — Pure H6 did not generalize positively on the lockbox."
    elif atr_survives and B.get("sharpe", -9) >= A.get("sharpe", 9):
        rec = ("FREEZE B — H6 generalized AND the ATR-expansion gate improved it "
               "out-of-sample (caveat: small n).")
    else:
        rec = ("FREEZE A — Pure H6 generalized; the ATR gate did not add value "
               "out-of-sample (or B sample too thin). Keep the simplest validated rule.")
    print(f"  {rec}")
    print(f"  NOTE: lockbox is ~1 year (~{A.get('n',0)} non-overlapping 7d trades) -> "
          f"directional confirmation, not a precise Sharpe estimate.")
    print("=" * 90)

    out = {"run_date": datetime.now(timezone.utc).isoformat(),
           "lockbox": ["2025-06-01", "2026-05-31"], "calendar_days": int(cal_days),
           "A_pure_h6": A, "B_h6_atr": B,
           "year_contribution": {
               "A": {int(y): float(g["ret"].sum()*100) for y, g in trA.groupby("year")},
               "B": {int(y): float(g["ret"].sum()*100) for y, g in trB.groupby("year")}},
           "atr_survives": bool(atr_survives), "recommendation": rec}
    (_THIS.parent / "reports" / "evidence_library" / "lockbox_validation.json").write_text(
        json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"\nSaved -> reports/evidence_library/lockbox_validation.json")


if __name__ == "__main__":
    main()
