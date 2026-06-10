"""
Group 5 independence analysis.

Objective (per Rohith): determine whether multi-timeframe structure contributes
INDEPENDENT information beyond the trend/regime signals already in the library.
Focus is independence + incremental information content, not raw Sharpe.

Two tests
---------
1. Position correlation: Pearson correlation of each MTF signal's daily position
   against existing trend/regime signals. High |r| => redundant.
2. Incremental information: does gating an existing daily-trend signal by
   multi-timeframe alignment improve it, or merely reduce exposure?
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np
import pandas as pd

_THIS_DIR = pathlib.Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

from engine import load_master, _apply_hold, _metrics, REALISTIC_COST
import signals as S

m   = load_master()
ret = m["ret_fwd1"].to_numpy(float)
ts  = m["day_ts"].to_numpy()

# --- MTF signals under test ---
mtf = {
    "1H Trend":           S.sig_trend_1h(m),
    "4H Trend":           S.sig_trend_4h(m),
    "1D Trend":           S.sig_trend_1d(m),
    "Trend Alignment":    S.sig_trend_alignment(m),
    "Trend Misalignment": S.sig_trend_misalignment(m),
}
# --- existing trend/regime reference signals already in the library ---
ref = {
    "Trend Regime (MAx)": S.sig_trend_regime(m, 20, 100),
    "Market Regime":      S.sig_market_regime(m, 200, 20),
    "Momentum":           S.sig_momentum(m, 30),
    "ATR Expansion":      S.sig_atr_expansion(m, 14, 10),
}

def _arr(s):
    return pd.Series(s, index=m.index).reindex(m.index).fillna(0.0).to_numpy(float)

print("=" * 78)
print("GROUP 5 INDEPENDENCE ANALYSIS")
print("=" * 78)

# ---------- Test 1: position correlation ----------
print("\nTest 1 - Position correlation (Pearson) of MTF vs existing signals")
print(f"  {'MTF signal':20s} | " + " | ".join(f"{k[:12]:12s}" for k in ref) + " | max|r|")
print("  " + "-" * 76)
all_arrs_ref = {k: _arr(v) for k, v in ref.items()}
redundancy = {}
for name, sig in mtf.items():
    a = _arr(sig)
    cors = {}
    for rk, rv in all_arrs_ref.items():
        if a.std() > 0 and rv.std() > 0:
            cors[rk] = float(np.corrcoef(a, rv)[0, 1])
        else:
            cors[rk] = np.nan
    maxr = max((abs(v) for v in cors.values() if not np.isnan(v)), default=np.nan)
    redundancy[name] = (cors, maxr)
    cells = " | ".join(f"{cors[k]:+12.2f}" for k in ref)
    flag = "  <-- REDUNDANT" if (not np.isnan(maxr) and maxr >= 0.70) else \
           ("  (overlap)" if (not np.isnan(maxr) and maxr >= 0.50) else "  INDEPENDENT")
    print(f"  {name:20s} | {cells} | {maxr:.2f}{flag}")

# also cross-correlation within MTF family
print("\n  Within-family correlation (MTF vs MTF):")
mtf_arrs = {k: _arr(v) for k, v in mtf.items()}
names = list(mtf.keys())
print("  " + " " * 20 + " | " + " | ".join(f"{n[:10]:10s}" for n in names))
for n1 in names:
    row = []
    for n2 in names:
        a, b = mtf_arrs[n1], mtf_arrs[n2]
        r = float(np.corrcoef(a, b)[0, 1]) if (a.std() > 0 and b.std() > 0) else np.nan
        row.append(f"{r:+10.2f}")
    print(f"  {n1:20s} | " + " | ".join(row))

# ---------- Test 2: incremental information ----------
print("\nTest 2 - Incremental information: does MTF alignment improve a daily-trend signal?")
base_trend = _arr(S.sig_trend_regime(m, 20, 100))           # existing daily trend
align      = _arr(S.sig_trend_alignment(m))                 # MTF confluence
trend_1d   = _arr(S.sig_trend_1d(m))

def _show(label, pos):
    d = _metrics(_apply_hold(pos, 1), ret, ts, REALISTIC_COST)
    print(f"  {label:42s} Sharpe={d['sharpe']:+.2f}  Calmar={d['calmar']:+.2f}  "
          f"MaxDD={d['max_dd_pct']:.0f}%  N={d['n_trades']}  active_days={d['n_active_days']}")

# Daily trend alone
_show("daily trend alone", base_trend)
# Daily trend gated by full MTF alignment (only hold when 1H/4H/1D all agree with it)
gated = np.where((align != 0) & (np.sign(align) == np.sign(base_trend)), base_trend, 0.0)
_show("daily trend AND aligned (1H+4H+1D agree)", gated)
# Daily trend gated by 4H agreement only
tr4 = _arr(S.sig_trend_4h(m))
gated4 = np.where(np.sign(tr4) == np.sign(base_trend), base_trend, 0.0)
_show("daily trend AND 4H agrees", gated4)

print("\n  Interpretation: if gating only lowers exposure without raising risk-")
print("  adjusted return, the MTF layer adds no incremental information.")
print("=" * 78)
