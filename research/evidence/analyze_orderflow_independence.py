"""
Group 6 independence analysis.

Objective (per Rohith): does Group 6 add a genuinely new information axis, or is
it another representation of funding / basis / volatility / trend? If redundant,
document and exclude.

Volume is the only data axis untested before Group 6, so the volume proxies are
the real test of "new axis".
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np
import pandas as pd

_THIS_DIR = pathlib.Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

from engine import load_master
import signals as S

m = load_master()

def _arr(s):
    return pd.Series(s, index=m.index).reindex(m.index).fillna(0.0).to_numpy(float)

# Group 6 signals under test
g6 = {
    "CLV Pressure":            S.sig_clv_pressure(m, 10),
    "Chaikin Money Flow":      S.sig_chaikin_money_flow(m, 20),
    "OBV Trend":               S.sig_obv_trend(m, 20),
    "Volume Surge":            S.sig_volume_surge(m, 20, 1.5),
    "Funding-Basis Composite": S.sig_funding_basis_composite(m, 252),
}
# the four canonical axes the program is built around
axes = {
    "Funding":    S.sig_funding_level(m, 33, 252),
    "Basis":      S.sig_basis_level(m, 33, 252),
    "Volatility": S.sig_volatility_regime(m, 21, 33),
    "Trend":      S.sig_trend_regime(m, 20, 100),
    "Momentum":   S.sig_momentum(m, 30),
}

print("=" * 80)
print("GROUP 6 INDEPENDENCE ANALYSIS")
print("Question: new information axis, or re-representation of funding/basis/vol/trend?")
print("=" * 80)
axis_arrs = {k: _arr(v) for k, v in axes.items()}

print(f"\n  {'G6 signal':24s} | " + " | ".join(f"{k[:9]:9s}" for k in axes) + " | max|r|  verdict")
print("  " + "-" * 84)
for name, sig in g6.items():
    a = _arr(sig)
    cors = {}
    for k, v in axis_arrs.items():
        cors[k] = float(np.corrcoef(a, v)[0, 1]) if (a.std() > 0 and v.std() > 0) else np.nan
    maxr = max((abs(v) for v in cors.values() if not np.isnan(v)), default=np.nan)
    cells = " | ".join(f"{cors[k]:+9.2f}" for k in axes)
    if np.isnan(maxr):
        verd = "n/a"
    elif maxr >= 0.70:
        verd = "REDUNDANT"
    elif maxr >= 0.50:
        verd = "overlap"
    else:
        verd = "INDEPENDENT-axis"
    print(f"  {name:24s} | {cells} | {maxr:.2f}  {verd}")

# Explicit check: composite vs its two ingredients
print("\n  Funding-Basis Composite vs ingredients:")
comp = _arr(S.sig_funding_basis_composite(m, 252))
for k, sig in [("Funding Level", S.sig_funding_level(m, 33, 252)),
               ("Basis Level",   S.sig_basis_level(m, 33, 252))]:
    v = _arr(sig)
    r = float(np.corrcoef(comp, v)[0, 1]) if (comp.std() > 0 and v.std() > 0) else np.nan
    print(f"    r(composite, {k:14s}) = {r:+.2f}")

print("\n  Reading: an INDEPENDENT-axis row with a negative standalone Sharpe (see")
print("  scoreboard) is independent NOISE, not new tradable information.")
print("=" * 80)
