"""
Governance overlays for the evidence program (Phase C readiness machinery).

Pure statistical functions, no engine dependency:
  * continuous_correlation  — Pearson + Spearman on continuous features
  * vif_table               — variance inflation factors
  * psr / deflated_sr       — Probabilistic & Deflated Sharpe (Bailey & Lopez de Prado)
  * bh_fdr                  — Benjamini-Hochberg false-discovery control

All operate on plain arrays / DataFrames so they can be reused in Phase C.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import norm, skew, kurtosis

EULER = 0.5772156649015329


# ---------------------------------------------------------------------------
# Correlation
# ---------------------------------------------------------------------------

def continuous_correlation(df: pd.DataFrame, cols: list[str]) -> dict:
    """Pearson + Spearman correlation matrices over the given continuous columns,
    computed on complete cases (rows with all cols present)."""
    sub = df[cols].replace([np.inf, -np.inf], np.nan).dropna()
    return {
        "n": int(len(sub)),
        "pearson": sub.corr(method="pearson"),
        "spearman": sub.corr(method="spearman"),
    }


def high_pairs(corr: pd.DataFrame, thresh: float = 0.70) -> list[tuple[str, str, float]]:
    """List upper-triangle pairs with |r| >= thresh."""
    out = []
    cols = list(corr.columns)
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            r = corr.iloc[i, j]
            if not np.isnan(r) and abs(r) >= thresh:
                out.append((cols[i], cols[j], float(r)))
    return sorted(out, key=lambda t: -abs(t[2]))


# ---------------------------------------------------------------------------
# VIF
# ---------------------------------------------------------------------------

def vif_table(df: pd.DataFrame, cols: list[str]) -> dict[str, float]:
    """Variance inflation factor for each column regressed on the others
    (OLS via least squares, intercept included). VIF = 1/(1-R^2)."""
    sub = df[cols].replace([np.inf, -np.inf], np.nan).dropna()
    X = sub.to_numpy(dtype=float)
    n, k = X.shape
    out: dict[str, float] = {}
    for j in range(k):
        y = X[:, j]
        others = np.delete(X, j, axis=1)
        A = np.column_stack([np.ones(n), others])
        beta, *_ = np.linalg.lstsq(A, y, rcond=None)
        resid = y - A @ beta
        ss_res = float(np.sum(resid ** 2))
        ss_tot = float(np.sum((y - y.mean()) ** 2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
        out[cols[j]] = float(1.0 / (1.0 - r2)) if r2 < 1.0 else float("inf")
    return out


# ---------------------------------------------------------------------------
# Probabilistic / Deflated Sharpe
# ---------------------------------------------------------------------------

def _sr_stats(returns: np.ndarray) -> tuple[float, int, float, float]:
    r = returns[~np.isnan(returns)]
    n = len(r)
    if n < 5 or r.std(ddof=1) == 0:
        return 0.0, n, 0.0, 3.0
    sr = float(r.mean() / r.std(ddof=1))            # per-period (non-annualized)
    g3 = float(skew(r))
    g4 = float(kurtosis(r, fisher=False))           # non-excess kurtosis
    return sr, n, g3, g4


def psr(returns: np.ndarray, sr_benchmark: float = 0.0) -> tuple[float, float]:
    """Probabilistic Sharpe Ratio: P(true SR > sr_benchmark). Returns (prob, sr)."""
    sr, n, g3, g4 = _sr_stats(returns)
    if n < 5:
        return float("nan"), sr
    denom = np.sqrt(max(1.0 - g3 * sr + (g4 - 1.0) / 4.0 * sr ** 2, 1e-12))
    z = (sr - sr_benchmark) * np.sqrt(n - 1) / denom
    return float(norm.cdf(z)), sr


def deflated_sr(returns: np.ndarray, sr_trials_std: float, n_trials: int
                ) -> tuple[float, float, float]:
    """
    Deflated Sharpe Ratio. Benchmark = expected maximum Sharpe under n_trials
    independent trials given the cross-trial SR dispersion.
    Returns (dsr_prob, sr_star, sr_observed) all per-period.
    """
    if n_trials < 2 or sr_trials_std <= 0:
        p, sr = psr(returns, 0.0)
        return p, 0.0, sr
    sr_star = sr_trials_std * (
        (1 - EULER) * norm.ppf(1 - 1.0 / n_trials)
        + EULER * norm.ppf(1 - 1.0 / (n_trials * np.e))
    )
    p, sr = psr(returns, sr_star)
    return p, float(sr_star), sr


# ---------------------------------------------------------------------------
# Benjamini-Hochberg FDR
# ---------------------------------------------------------------------------

def bh_fdr(pvals: dict[str, float], q: float = 0.10) -> dict[str, dict]:
    """Benjamini-Hochberg step-up. Returns per-name {p, rank, bh_threshold, reject}."""
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m = len(items)
    kmax = 0
    for i, (_, p) in enumerate(items, start=1):
        if p <= (i / m) * q:
            kmax = i
    out: dict[str, dict] = {}
    for i, (name, p) in enumerate(items, start=1):
        out[name] = {
            "p": float(p),
            "rank": i,
            "bh_threshold": float((i / m) * q),
            "reject": i <= kmax,
        }
    return out
