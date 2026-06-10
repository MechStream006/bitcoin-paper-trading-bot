"""
H6 -- Funding x Volatility Interaction
Phase B.1D Evidence Battery

Tests whether the funding-rate signal has economically meaningful directional
predictive power for BTC forward returns *conditional on the prevailing
volatility regime*.

Motivation
----------
H1 (unconditional funding -> returns): FAIL -- Cohen's d ~0.05, OOS R2 < 0.
H5 (vol regime -> basis OOS):          PASS -- high-vol OOS R2 = +0.251.
H1 Stage 6 secondary observation:
    medium_vol x funding at 7d: H-L diff = -2.97%, Cohen's d = -0.394.
    high_vol   x funding at 14d: H-L diff = +2.56%, Cohen's d = +0.375.

H6 gives these cross-study hints a dedicated, pre-specified test with OOS
validation and structural-event robustness checks.

Null hypothesis
---------------
    The vol-conditioning of funding produces no additional directional
    predictability vs unconditional funding.  Formally: within each vol
    stratum, OOS R2 <= 0 and |H-L diff| < 2.0% (7d) / 3.0% (14d).

Alternative hypothesis
----------------------
    At least one vol stratum shows:
    (1) OOS R2 > 0 at one or more horizons, AND
    (2) |H-L diff| >= 2.0% (7d) or 3.0% (14d) -- economic significance.

Anti-lookahead
--------------
vol_regime(d)       : uses RV_21[d-251..d].             Available at d. [OK]
funding_regime(d)   : uses daily_mean_funding[d-251..d]. Available at d. [OK]
ret_h(d)            : close[d+h] / close[d] - 1.        Future outcome.  [OK]
OOS step t          : model trained on [0..t-1].          Available at t.  [OK]
Stratum-specific OOS: expanding window over same-stratum obs in time order. [OK]

Pass criteria (pre-specified)
------------------------------
H6a: At least one vol stratum x 7d shows global-tagged OOS R2 > 0 AND
     abs(Cohen's d) > 0.20 (small-medium boundary).
H6b: At least one vol stratum x horizon meets economic threshold:
     |H-L diff| >= 2.0% at 7d, or >= 3.0% at 14d.
H6c (robustness): The stratum-specific OOS corroborates H6a (same
     stratum, positive R2, MWU p < 0.05).
Overall PASS: H6a AND H6b. H6c is a robustness gate.

Run:
    python research/hypotheses/h6_funding_vol_interaction/run_h6.py
"""
from __future__ import annotations

import json
import pathlib
import sys
import time
import warnings
from datetime import datetime, timezone

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import numpy as np
import pandas as pd
from scipy.stats import (
    spearmanr, chi2_contingency, mannwhitneyu, t as t_dist, binomtest,
)

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=RuntimeWarning)

# ---- Path setup ------------------------------------------------------------
_H6_DIR       = pathlib.Path(__file__).resolve().parent
_RESEARCH_DIR = _H6_DIR.parent.parent
if str(_RESEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_DIR))

from storage.duck import open_db, query, register_view                    # noqa: E402
from hypotheses.shared.market_regimes import build_market_regimes         # noqa: E402
from hypotheses.shared.structural_events import (                         # noqa: E402
    events_df, build_exclusion_mask_ms,
)

# ---- Directories -----------------------------------------------------------
BASE_DIR   = _RESEARCH_DIR / "data" / "raw"
REPORT_DIR = _RESEARCH_DIR / "reports" / "h6_funding_vol_interaction"
CHART_DIR  = REPORT_DIR / "charts"
SYMBOL     = "BTCUSDT"

# ---- Analysis parameters ---------------------------------------------------
HORIZONS       = [1, 3, 7, 14]
TRAIN_WINDOW   = 126    # 6-month expanding window (shorter for within-stratum OOS)
N_BOOTSTRAP    = 2_000
REGIME_WINDOW  = 252
REGIME_PCTILE  = 33
RV_WINDOW      = 21
NW_MAXLAGS     = 3
MIN_MINS_1H    = 30
MIN_DAILY_FUND = 2

# ---- Pass/fail thresholds --------------------------------------------------
THRESH_EFFECT_7D   = 0.020   # 2.0%
THRESH_EFFECT_14D  = 0.030   # 3.0%
THRESH_COHENS_D    = 0.20    # small-medium boundary
THRESH_P           = 0.05
THRESH_OOS_R2      = 0.000
THRESH_CHI2_P      = 0.01
THRESH_DIAG_DOM    = 0.20
THRESH_HALFLIFE    = 5.0

VOL_LABELS  = ["low_vol", "medium_vol", "high_vol"]
FUND_LABELS = ["high_funding", "neutral_funding", "low_funding"]

VOL_COLORS = {
    "low_vol":    "#3498db",
    "medium_vol": "#f39c12",
    "high_vol":   "#e74c3c",
}
FUND_COLORS = {
    "high_funding":    "#c0392b",
    "neutral_funding": "#95a5a6",
    "low_funding":     "#27ae60",
}


# =============================================================================
# Statistical helpers
# =============================================================================

def _ts() -> str:
    return datetime.now(timezone.utc).strftime("[%H:%M:%S]")


def _spearman_ci(x: np.ndarray, y: np.ndarray, n_boot: int = N_BOOTSTRAP) -> dict:
    rng = np.random.default_rng(42)
    rho_full, pval = spearmanr(x, y)
    boot = np.empty(n_boot)
    for i in range(n_boot):
        idx = rng.integers(0, len(x), size=len(x))
        boot[i], _ = spearmanr(x[idx], y[idx])
    return dict(
        rho=float(rho_full), pval=float(pval),
        ci_lo=float(np.percentile(boot, 2.5)),
        ci_hi=float(np.percentile(boot, 97.5)),
        n=int(len(x)),
    )


def _newey_west_ols(y: np.ndarray, x: np.ndarray, maxlags: int = NW_MAXLAGS) -> dict:
    n = len(y)
    X = np.column_stack([np.ones(n), x])
    XtX_inv = np.linalg.inv(X.T @ X)
    beta = XtX_inv @ (X.T @ y)
    e = y - X @ beta
    Xe = X * e[:, None]
    S = (Xe.T @ Xe) / n
    for lag in range(1, maxlags + 1):
        w = 1.0 - lag / (maxlags + 1)
        G = (Xe[lag:].T @ Xe[:-lag]) / n
        S += w * (G + G.T)
    V = (XtX_inv @ S @ XtX_inv) / n
    se = np.sqrt(np.diag(V))
    tstat = beta / se
    pval = 2.0 * t_dist.sf(np.abs(tstat), df=n - 2)
    r2 = float(1.0 - np.sum(e ** 2) / np.sum((y - y.mean()) ** 2))
    return dict(
        beta_x=float(beta[1]), se_x=float(se[1]),
        t_x=float(tstat[1]), p_x=float(pval[1]),
        r2=float(r2), n=n,
    )


def _effect_size(high_ret: np.ndarray, low_ret: np.ndarray) -> dict:
    if len(high_ret) < 10 or len(low_ret) < 10:
        return {"diff_pct": np.nan, "cohens_d": np.nan, "mw_p": np.nan}
    diff = float((high_ret.mean() - low_ret.mean()) * 100)
    pooled_var = (
        (len(high_ret) - 1) * high_ret.var(ddof=1) +
        (len(low_ret)  - 1) * low_ret.var(ddof=1)
    ) / (len(high_ret) + len(low_ret) - 2)
    pooled_std = float(np.sqrt(max(pooled_var, 0.0)))
    cohens_d   = (high_ret.mean() - low_ret.mean()) / pooled_std if pooled_std > 0 else np.nan
    try:
        _, mw_p = mannwhitneyu(high_ret, low_ret, alternative="two-sided")
    except Exception:
        mw_p = np.nan
    return dict(diff_pct=diff, cohens_d=float(cohens_d), mw_p=float(mw_p))


def _oos_r2_tagged(
    signal: np.ndarray,
    ret: np.ndarray,
    vol_tags: np.ndarray,
    train_window: int,
) -> dict:
    """
    Global expanding-window OOS: signal -> ret.
    Returns overall R2 plus per-vol-regime R2 (tagged decomposition).
    No lookahead: predictions at t use only data from [0..t-1].
    vol_tags are contemporaneous -- known at prediction time. [OK]
    """
    valid = ~(np.isnan(signal) | np.isnan(ret))
    x    = signal[valid]
    y    = ret[valid]
    tags = vol_tags[valid]
    n    = len(x)
    if n < train_window + 30:
        return {"overall_r2": np.nan, "n_oos": 0, "by_vol": {}}

    # Vectorized cumulative sums for O(n) expanding OLS
    cum_x  = np.concatenate([[0.0], np.cumsum(x)])
    cum_y  = np.concatenate([[0.0], np.cumsum(y)])
    cum_xy = np.concatenate([[0.0], np.cumsum(x * y)])
    cum_xx = np.concatenate([[0.0], np.cumsum(x ** 2)])

    t_arr = np.arange(train_window, n)
    n_t   = t_arr.astype(float)
    sx    = cum_x[t_arr];   sy  = cum_y[t_arr]
    sxy   = cum_xy[t_arr];  sxx = cum_xx[t_arr]

    denom = n_t * sxx - sx ** 2
    ok    = np.abs(denom) > 1e-15
    b1    = np.where(ok, (n_t * sxy - sx * sy) / np.where(ok, denom, 1.0), 0.0)
    b0    = np.where(ok, (sy - b1 * sx) / n_t, 0.0)

    y_hat     = np.where(ok, b0 + b1 * x[t_arr], np.nan)
    y_act     = y[t_arr]
    tags_oos  = tags[t_arr]

    valid2 = ~np.isnan(y_hat)
    y_hat  = y_hat[valid2]
    y_act  = y_act[valid2]
    tags_oos = tags_oos[valid2]

    # Overall R2
    ss_res = float(np.sum((y_act - y_hat) ** 2))
    ss_tot = float(np.sum((y_act - y_act.mean()) ** 2))
    overall_r2 = float(1.0 - ss_res / ss_tot) if ss_tot > 0 else 0.0

    # Per-vol-regime R2
    by_vol: dict = {}
    for lbl in VOL_LABELS:
        mask = tags_oos == lbl
        if mask.sum() < 20:
            by_vol[lbl] = {"r2": np.nan, "n": int(mask.sum())}
            continue
        ya = y_act[mask]; yh = y_hat[mask]
        ssr = float(np.sum((ya - yh) ** 2))
        sst = float(np.sum((ya - ya.mean()) ** 2))
        r2  = float(1.0 - ssr / sst) if sst > 0 else 0.0
        hits = int(np.sum((yh > 0) == (ya > 0)))
        try:
            bp = float(binomtest(hits, int(mask.sum()), 0.5, alternative="greater").pvalue)
        except Exception:
            bp = np.nan
        by_vol[lbl] = {
            "r2": r2, "n": int(mask.sum()),
            "hit_rate": hits / int(mask.sum()),
            "binom_p": bp,
        }

    return dict(overall_r2=overall_r2, n_oos=len(y_act), by_vol=by_vol)


def _stratum_specific_oos(
    m: pd.DataFrame,
    stratum: str,
    ret_col: str,
    train_window: int,
) -> dict:
    """
    Expanding-window OOS *entirely within* the given vol stratum.
    Trains only on same-stratum observations (chronological order).
    Smaller sample, but the cleanest test of within-regime predictability.
    """
    sub = m[
        (m["vol_regime"] == stratum) &
        m["daily_mean_funding_bps"].notna() &
        m[ret_col].notna()
    ].copy().sort_values("day_ts").reset_index(drop=True)

    x = sub["daily_mean_funding_bps"].to_numpy()
    y = sub[ret_col].to_numpy()
    n = len(x)

    if n < train_window + 20:
        return {"r2": np.nan, "n_oos": 0, "hit_rate": np.nan, "binom_p": np.nan}

    cum_x  = np.concatenate([[0.0], np.cumsum(x)])
    cum_y  = np.concatenate([[0.0], np.cumsum(y)])
    cum_xy = np.concatenate([[0.0], np.cumsum(x * y)])
    cum_xx = np.concatenate([[0.0], np.cumsum(x ** 2)])

    t_arr = np.arange(train_window, n)
    n_t   = t_arr.astype(float)
    sx    = cum_x[t_arr];   sy  = cum_y[t_arr]
    sxy   = cum_xy[t_arr];  sxx = cum_xx[t_arr]

    denom = n_t * sxx - sx ** 2
    ok    = np.abs(denom) > 1e-15
    b1    = np.where(ok, (n_t * sxy - sx * sy) / np.where(ok, denom, 1.0), 0.0)
    b0    = np.where(ok, (sy - b1 * sx) / n_t, 0.0)

    y_hat = np.where(ok, b0 + b1 * x[t_arr], np.nan)
    y_act = y[t_arr]
    valid = ~np.isnan(y_hat)
    y_hat = y_hat[valid]; y_act = y_act[valid]

    ss_res = float(np.sum((y_act - y_hat) ** 2))
    ss_tot = float(np.sum((y_act - y_act.mean()) ** 2))
    r2 = float(1.0 - ss_res / ss_tot) if ss_tot > 0 else 0.0

    hits = int(np.sum((y_hat > 0) == (y_act > 0)))
    n_valid = len(y_act)
    hr = hits / n_valid if n_valid > 0 else np.nan
    try:
        bp = float(binomtest(hits, n_valid, 0.5, alternative="greater").pvalue)
    except Exception:
        bp = np.nan

    return dict(r2=r2, n_oos=n_valid, hit_rate=hr, binom_p=bp)


def _regime_persistence(regime_series: pd.Series, labels: list[str]) -> dict:
    s = regime_series[regime_series.isin(labels)]
    trans = pd.crosstab(s.iloc[:-1].values, s.iloc[1:].values)
    for lbl in labels:
        if lbl not in trans.index:   trans.loc[lbl] = 0
        if lbl not in trans.columns: trans[lbl] = 0
    trans = trans.reindex(index=labels, columns=labels, fill_value=0)
    chi2, p_chi2, dof, _ = chi2_contingency(trans.values)
    n_trans = int(trans.values.sum())
    cramers_v = float(np.sqrt(chi2 / (n_trans * (len(labels) - 1)))) if n_trans > 0 else 0.0
    row_sums = trans.sum(axis=1).replace(0, np.nan)
    prob = trans.div(row_sums, axis=0).fillna(0.0)
    diag_dom = float(np.diag(prob.values).mean() - 1.0 / len(labels))
    half_lives: dict = {}
    for lbl in labels:
        runs = []; cur = 0
        for val in (s == lbl).astype(int):
            if val:   cur += 1
            elif cur: runs.append(cur); cur = 0
        if cur: runs.append(cur)
        half_lives[lbl] = float(np.mean(runs)) if runs else 0.0
    return dict(
        prob_matrix={r: {c: float(prob.loc[r, c]) for c in labels} for r in labels},
        chi2=float(chi2), p_chi2=float(p_chi2), dof=int(dof),
        cramers_v=cramers_v, diag_dominance=diag_dom,
        half_lives=half_lives, n_transitions=n_trans,
    )


# =============================================================================
# Regime builders (identical to H1/H5, no shared import to avoid coupling)
# =============================================================================

def _build_tercile_regime(series: pd.Series, labels: list[str],
                          window: int = REGIME_WINDOW) -> pd.Series:
    lo = series.rolling(window, min_periods=window).quantile(REGIME_PCTILE / 100)
    hi = series.rolling(window, min_periods=window).quantile(1.0 - REGIME_PCTILE / 100)
    regime = pd.Series(labels[1], index=series.index, dtype=object)
    regime[series > hi] = labels[0]
    regime[series < lo] = labels[2]
    regime[lo.isna() | hi.isna()] = "unknown"
    return regime


def _build_vol_regimes(daily_close: pd.Series) -> tuple[pd.Series, pd.Series]:
    log_ret = np.log(daily_close / daily_close.shift(1))
    rv = log_ret.rolling(RV_WINDOW, min_periods=RV_WINDOW).std() * np.sqrt(252)
    vol_regime = _build_tercile_regime(rv, ["high_vol", "medium_vol", "low_vol"])
    return rv, vol_regime


# =============================================================================
# Data loading
# =============================================================================

def _load_all_data() -> pd.DataFrame:
    print(f"{_ts()} Loading data via DuckDB...")
    con = open_db()
    n_spot = register_view(con, "spot_1m",     BASE_DIR, "spot",    SYMBOL)
    n_perp = register_view(con, "perp_1m",     BASE_DIR, "perp",    SYMBOL)
    n_fund = register_view(con, "funding_raw", BASE_DIR, "funding", SYMBOL)
    print(f"  spot_1m: {n_spot}  perp_1m: {n_perp}  funding_raw: {n_fund} partitions")

    con.execute(f"""
    CREATE OR REPLACE VIEW basis_1h AS
    SELECT
        (s.open_time // 3600000) * 3600000        AS hour_ts,
        AVG(p.close - s.close)                    AS basis_abs,
        AVG((p.close / s.close - 1.0) * 10000.0) AS basis_bps,
        COUNT(*)                                  AS n_min
    FROM spot_1m s
    JOIN perp_1m p ON s.open_time = p.open_time
    GROUP BY s.open_time // 3600000
    HAVING COUNT(*) >= {MIN_MINS_1H}
    """)

    df_fund_daily = query(con, f"""
    SELECT
        (timestamp_ms // 86400000) * 86400000 AS day_ts,
        AVG(funding_rate) * 10000.0           AS daily_mean_funding_bps,
        COUNT(*)                              AS n_settlements
    FROM funding_raw
    GROUP BY timestamp_ms // 86400000
    HAVING COUNT(*) >= {MIN_DAILY_FUND}
    ORDER BY day_ts
    """)

    df_spot_daily = query(con, """
    SELECT day_ts, last_close AS daily_close
    FROM (
        SELECT
            (open_time // 86400000) * 86400000 AS day_ts,
            close AS last_close,
            ROW_NUMBER() OVER (
                PARTITION BY open_time // 86400000
                ORDER BY open_time DESC
            ) AS rn
        FROM spot_1m
    )
    WHERE rn = 1
    ORDER BY day_ts
    """)
    con.close()

    df_fund_daily["dt"] = pd.to_datetime(df_fund_daily["day_ts"], unit="ms", utc=True)
    df_spot_daily["dt"] = pd.to_datetime(df_spot_daily["day_ts"], unit="ms", utc=True)

    master = df_spot_daily.merge(
        df_fund_daily[["day_ts", "daily_mean_funding_bps", "n_settlements"]],
        on="day_ts", how="left",
    )
    master["dt"]   = pd.to_datetime(master["day_ts"], unit="ms", utc=True)
    master["date"] = master["dt"].dt.normalize().dt.tz_localize(None).dt.date
    master = master.sort_values("day_ts").reset_index(drop=True)

    close_s = master["daily_close"]
    rv, vol_r = _build_vol_regimes(close_s)
    master["rv_21d"]     = rv.values
    master["vol_regime"] = vol_r.values

    master["funding_regime"] = _build_tercile_regime(
        master["daily_mean_funding_bps"],
        ["high_funding", "neutral_funding", "low_funding"],
    ).values

    close_dt = pd.Series(close_s.values, index=master["dt"].values)
    mkt_reg  = build_market_regimes(close_dt)
    mkt_map  = {k.normalize(): v for k, v in mkt_reg.to_dict().items()}
    master["market_regime"] = (
        master["dt"].dt.normalize().dt.tz_localize(None).map(mkt_map).fillna("unknown")
    )

    for h in HORIZONS:
        master[f"ret_{h}d"] = close_s.shift(-h).values / close_s.values - 1.0

    return master


# =============================================================================
# Stage functions
# =============================================================================

def stage1_summary(m: pd.DataFrame) -> None:
    print(f"\n{_ts()} STAGE 1  Sample summary")
    known = m[m["vol_regime"].isin(VOL_LABELS) & m["funding_regime"].isin(FUND_LABELS)]
    print(f"  Total rows: {len(m):,}  |  vol+funding known: {len(known):,}")
    print(f"  Date range: {m['date'].iloc[0]} to {m['date'].iloc[-1]}")
    print()
    print("  Vol regime distribution:")
    for lbl in VOL_LABELS:
        n = (m["vol_regime"] == lbl).sum()
        print(f"    {lbl:12s}: {n:,}")
    print()
    print("  Funding regime distribution:")
    for lbl in FUND_LABELS:
        n = (m["funding_regime"] == lbl).sum()
        print(f"    {lbl:18s}: {n:,}")
    print()
    print("  Cross-table (vol x funding, n):")
    ct = pd.crosstab(m["vol_regime"], m["funding_regime"])
    print("  " + ct.to_string().replace("\n", "\n  "))
    print()
    print("-" * 70)


def stage2_spearman_by_vol(m: pd.DataFrame) -> dict:
    """Spearman rho (continuous funding -> forward returns) within each vol stratum."""
    print(f"\n{_ts()} STAGE 2  Spearman (continuous funding -> returns) by vol stratum")
    results: dict = {}
    for vol in VOL_LABELS:
        sub = m[
            (m["vol_regime"] == vol) &
            m["daily_mean_funding_bps"].notna()
        ]
        print(f"  {vol} (n={len(sub):,}):")
        row: dict = {}
        for h in HORIZONS:
            col = f"ret_{h}d"
            sub_h = sub[sub[col].notna()]
            if len(sub_h) < 50:
                print(f"    {h:2d}d: n too small")
                continue
            sp = _spearman_ci(
                sub_h["daily_mean_funding_bps"].to_numpy(),
                sub_h[col].to_numpy(),
            )
            nw = _newey_west_ols(
                sub_h[col].to_numpy(),
                sub_h["daily_mean_funding_bps"].to_numpy(),
                maxlags=max(1, h - 1),
            )
            flag = "[PASS]" if sp["pval"] < THRESH_P else "[FAIL]"
            print(f"    {h:2d}d: rho={sp['rho']:+.4f}  p={sp['pval']:.3f}  "
                  f"R2={nw['r2']:.4f}  n={sp['n']:,}  {flag}")
            row[f"h{h}d"] = {**sp, "nw_r2": nw["r2"], "nw_beta": nw["beta_x"]}
        results[vol] = row
    print("-" * 70)
    return results


def stage3_categorical_by_vol(m: pd.DataFrame) -> dict:
    """
    H-L mean return diff (high_funding - low_funding) within each
    vol stratum x horizon.  Primary economic significance table.
    """
    print(f"\n{_ts()} STAGE 3  H-L effect size by vol stratum x horizon")
    results: dict = {}

    # Header row
    header = f"  {'vol_regime':14s}" + "".join(f"  {h}d(H-L/d/p)" .ljust(20) for h in HORIZONS)
    print(header)

    for vol in VOL_LABELS:
        sub_vol = m[m["vol_regime"] == vol]
        row: dict = {"n": len(sub_vol)}
        line = f"  {vol:14s}"
        for h in HORIZONS:
            col  = f"ret_{h}d"
            sub_h = sub_vol[sub_vol[col].notna() & sub_vol["funding_regime"].isin(FUND_LABELS)]
            high_r = sub_h.loc[sub_h["funding_regime"] == "high_funding", col].to_numpy()
            low_r  = sub_h.loc[sub_h["funding_regime"] == "low_funding",  col].to_numpy()
            eff    = _effect_size(high_r, low_r)

            thresh = THRESH_EFFECT_7D if h == 7 else (THRESH_EFFECT_14D if h == 14 else 0.0)
            econ_sig = (not np.isnan(eff["diff_pct"])) and (abs(eff["diff_pct"]) / 100 >= thresh) and thresh > 0
            p_str  = f"{eff['mw_p']:.3f}" if not np.isnan(eff.get("mw_p", np.nan)) else " nan"
            d_str  = f"{eff['cohens_d']:+.3f}" if not np.isnan(eff.get("cohens_d", np.nan)) else " nan"
            v_str  = "*" if econ_sig else " "
            pct_str = f"{eff['diff_pct']:+.2f}%" if not np.isnan(eff["diff_pct"]) else " nan%"
            line += f"  {pct_str}/{d_str}/p={p_str}{v_str}".ljust(22)

            row[f"h{h}d"] = {
                "diff_pct":  eff["diff_pct"],
                "cohens_d":  eff["cohens_d"],
                "mw_p":      eff["mw_p"],
                "econ_sig":  econ_sig,
                "high_n":    len(high_r),
                "low_n":     len(low_r),
            }
        results[vol] = row
        print(line)

    print()
    # Also print full breakdown: for each vol+horizon, show all 3 funding regime means
    print(f"  {_ts()} Mean returns by funding regime within each vol stratum")
    for vol in VOL_LABELS:
        sub_vol = m[m["vol_regime"] == vol]
        print(f"  {vol}:")
        for h in HORIZONS:
            col = f"ret_{h}d"
            parts = []
            for fr in FUND_LABELS:
                sub_f = sub_vol[sub_vol["funding_regime"] == fr][col].dropna()
                mean_pct = float(sub_f.mean() * 100) if len(sub_f) >= 10 else float("nan")
                parts.append(f"{fr.replace('_funding','')}={mean_pct:+.2f}%")
            print(f"    {h:2d}d: " + "  ".join(parts))
    print("-" * 70)
    return results


def stage4_tagged_oos(m: pd.DataFrame) -> dict:
    """
    Global expanding-window OOS, decomposed by contemporaneous vol regime.
    Primary OOS R2 test (H6a).
    """
    print(f"\n{_ts()} STAGE 4  Tagged OOS R2 (global model, decomposed by vol regime)")
    results: dict = {}

    sub = m[m["daily_mean_funding_bps"].notna()].copy()
    x    = sub["daily_mean_funding_bps"].to_numpy()
    tags = sub["vol_regime"].to_numpy()

    for h in [1, 7, 14]:
        col  = f"ret_{h}d"
        y    = sub[col].to_numpy()
        oos  = _oos_r2_tagged(x, y, tags, train_window=TRAIN_WINDOW)
        results[f"h{h}d"] = oos

        overall_flag = "[PASS]" if oos["overall_r2"] > THRESH_OOS_R2 else "[FAIL]"
        print(f"  {h:2d}d overall OOS R2 = {oos['overall_r2']:+.4f}  n_oos={oos['n_oos']:,}  {overall_flag}")
        for lbl in VOL_LABELS:
            bv = oos["by_vol"].get(lbl, {})
            r2  = bv.get("r2", np.nan)
            n   = bv.get("n", 0)
            hr  = bv.get("hit_rate", np.nan)
            bp  = bv.get("binom_p", np.nan)
            flag = "[PASS]" if (not np.isnan(r2)) and r2 > THRESH_OOS_R2 else "[FAIL]"
            print(f"    {lbl:12s}: R2={r2:+.4f}  n={n:,}  hit={hr:.3f}  binom_p={bp:.3f}  {flag}")
        print()

    print("-" * 70)
    return results


def stage5_stratum_specific_oos(m: pd.DataFrame) -> dict:
    """
    Stratum-specific expanding-window OOS: trains and evaluates exclusively
    within each vol regime (chronological order, no cross-stratum contamination).
    H6c robustness check.
    """
    print(f"\n{_ts()} STAGE 5  Stratum-specific OOS (within-regime walk-forward)")
    results: dict = {}

    for vol in VOL_LABELS:
        print(f"  {vol}:")
        row: dict = {}
        for h in [1, 7, 14]:
            col  = f"ret_{h}d"
            oos  = _stratum_specific_oos(m, vol, col, train_window=TRAIN_WINDOW)
            r2   = oos["r2"]
            flag = "[PASS]" if (not np.isnan(r2)) and r2 > THRESH_OOS_R2 else "[FAIL]"
            hr   = oos["hit_rate"]
            bp   = oos["binom_p"]
            print(f"    {h:2d}d: OOS R2={r2:+.4f}  hit={hr:.3f}  binom_p={bp:.4f}  "
                  f"n_oos={oos['n_oos']:,}  {flag}")
            row[f"h{h}d"] = oos
        results[vol] = row

    print("-" * 70)
    return results


def stage6_structural_robustness(m: pd.DataFrame) -> dict:
    """
    Repeat H-L effect sizes on structural-event-clean sample within each vol stratum.
    Confirms or refutes that the main-sample findings are event-contaminated.
    """
    print(f"\n{_ts()} STAGE 6  Structural event robustness (clean sample)")
    excl_arr  = build_exclusion_mask_ms(m["day_ts"].to_numpy())
    clean = m[~excl_arr].copy()
    n_excl = int(excl_arr.sum())
    print(f"  Excluded {n_excl:,} event-window days.  Clean n={len(clean):,}")
    results: dict = {}

    for vol in VOL_LABELS:
        sub_v = clean[clean["vol_regime"] == vol]
        row: dict = {"n_clean": len(sub_v)}
        print(f"  {vol} (clean n={len(sub_v):,}):")
        for h in [3, 7, 14]:
            col = f"ret_{h}d"
            sub_h  = sub_v[sub_v[col].notna() & sub_v["funding_regime"].isin(FUND_LABELS)]
            high_r = sub_h.loc[sub_h["funding_regime"] == "high_funding", col].to_numpy()
            low_r  = sub_h.loc[sub_h["funding_regime"] == "low_funding",  col].to_numpy()
            eff    = _effect_size(high_r, low_r)
            thresh = THRESH_EFFECT_7D if h == 7 else (THRESH_EFFECT_14D if h == 14 else 0.0)
            flag   = "*" if (not np.isnan(eff["diff_pct"])) and (abs(eff["diff_pct"]) / 100 >= thresh) and thresh > 0 else " "
            d_str  = f"{eff['cohens_d']:+.3f}" if not np.isnan(eff.get("cohens_d", np.nan)) else " nan"
            p_str  = f"{eff['mw_p']:.3f}" if not np.isnan(eff.get("mw_p", np.nan)) else " nan"
            pct_s  = f"{eff['diff_pct']:+.2f}%" if not np.isnan(eff["diff_pct"]) else " nan%"
            print(f"    {h:2d}d clean: H-L={pct_s}  d={d_str}  p={p_str}{flag}")
            row[f"h{h}d"] = eff
        results[vol] = row

    print("-" * 70)
    return results


def stage7_within_highvol_market_regime(m: pd.DataFrame) -> dict:
    """
    Within the best-candidate vol stratum (high_vol), further stratify
    by bull/bear/sideways market regime.  Tests whether the funding signal
    is regime-conditional at a second level.
    """
    print(f"\n{_ts()} STAGE 7  Market regime conditioning within each vol stratum (7d H-L)")
    results: dict = {}

    for vol in VOL_LABELS:
        sub_v = m[m["vol_regime"] == vol]
        row: dict = {}
        line_parts = []
        for mkt in ["bull", "bear", "sideways"]:
            sub_m  = sub_v[sub_v["market_regime"] == mkt]
            sub_h  = sub_m[sub_m["ret_7d"].notna() & sub_m["funding_regime"].isin(FUND_LABELS)]
            high_r = sub_h.loc[sub_h["funding_regime"] == "high_funding", "ret_7d"].to_numpy()
            low_r  = sub_h.loc[sub_h["funding_regime"] == "low_funding",  "ret_7d"].to_numpy()
            eff    = _effect_size(high_r, low_r)
            row[mkt] = {"n": len(sub_h), **eff}
            d_str  = f"{eff.get('cohens_d', np.nan):+.3f}" if not np.isnan(eff.get("cohens_d", np.nan)) else " nan"
            pct_s  = f"{eff['diff_pct']:+.2f}%" if not np.isnan(eff["diff_pct"]) else " nan%"
            line_parts.append(f"{mkt}:{pct_s}(d={d_str})")
        results[vol] = row
        print(f"  {vol}: " + "  |  ".join(line_parts))

    print("-" * 70)
    return results


def stage8_joint_persistence(m: pd.DataFrame) -> dict:
    """
    Persistence of the joint (vol_regime, funding_regime) state.
    Informs how long a conditional position can expect its regime to hold.
    """
    print(f"\n{_ts()} STAGE 8  Joint vol x funding regime persistence")
    results: dict = {}

    # Joint state label
    joint = m.apply(
        lambda r: f"{r['vol_regime']}|{r['funding_regime']}"
        if r["vol_regime"] in VOL_LABELS and r["funding_regime"] in FUND_LABELS
        else "unknown",
        axis=1,
    )
    joint_labels = [f"{v}|{f}" for v in VOL_LABELS for f in FUND_LABELS]

    # Per-axis persistence
    print("  Vol regime persistence:")
    vp = _regime_persistence(m["vol_regime"], VOL_LABELS)
    for lbl in VOL_LABELS:
        hl = vp["half_lives"][lbl]
        stay = vp["prob_matrix"][lbl][lbl] * 100
        print(f"    {lbl:12s}: stay_prob={stay:.1f}%  half_life={hl:.1f}d")
    print(f"  Cramer_V={vp['cramers_v']:.4f}  diag_dom={vp['diag_dominance']:+.4f}")
    results["vol_persistence"] = vp

    print()
    print("  Funding regime persistence:")
    fp = _regime_persistence(m["funding_regime"], FUND_LABELS)
    for lbl in FUND_LABELS:
        hl = fp["half_lives"][lbl]
        stay = fp["prob_matrix"][lbl][lbl] * 100
        print(f"    {lbl:18s}: stay_prob={stay:.1f}%  half_life={hl:.1f}d")
    print(f"  Cramer_V={fp['cramers_v']:.4f}  diag_dom={fp['diag_dominance']:+.4f}")
    results["funding_persistence"] = fp

    # Joint: runs per cell (how long does a specific vol+fund state persist?)
    print()
    print("  Joint state run-lengths (mean consecutive days in each cell):")
    joint_runs: dict = {}
    for cell in joint_labels:
        runs = []; cur = 0
        for val in (joint == cell).astype(int):
            if val:   cur += 1
            elif cur: runs.append(cur); cur = 0
        if cur: runs.append(cur)
        mean_run = float(np.mean(runs)) if runs else 0.0
        joint_runs[cell] = mean_run
        if mean_run >= 2.0:
            print(f"    {cell:30s}: mean_run={mean_run:.1f}d  n_episodes={len(runs):,}")
    results["joint_run_lengths"] = joint_runs

    print("-" * 70)
    return results


def stage9_charts(m: pd.DataFrame, cat_results: dict, oos_results: dict,
                  stratum_oos: dict) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    CHART_DIR.mkdir(parents=True, exist_ok=True)

    # --- Chart 1: Effect-size heatmap (vol x horizon) -----------------------
    fig, ax = plt.subplots(figsize=(10, 4))
    eff_matrix = np.zeros((len(VOL_LABELS), len(HORIZONS)))
    for i, vol in enumerate(VOL_LABELS):
        for j, h in enumerate(HORIZONS):
            d = cat_results.get(vol, {}).get(f"h{h}d", {}).get("cohens_d", np.nan)
            eff_matrix[i, j] = float(d) if not np.isnan(d) else 0.0
    vmax = max(0.5, np.nanmax(np.abs(eff_matrix)))
    im = ax.imshow(eff_matrix, aspect="auto", cmap="RdYlGn_r",
                   vmin=-vmax, vmax=vmax)
    plt.colorbar(im, ax=ax, label="Cohen's d (H-L)")
    ax.set_xticks(range(len(HORIZONS))); ax.set_xticklabels([f"{h}d" for h in HORIZONS])
    ax.set_yticks(range(len(VOL_LABELS))); ax.set_yticklabels(VOL_LABELS)
    ax.set_title("H6 Effect Size: Cohen's d (High - Low Funding) by Vol Regime x Horizon")
    for i in range(len(VOL_LABELS)):
        for j in range(len(HORIZONS)):
            v = eff_matrix[i, j]
            pct = cat_results.get(VOL_LABELS[i], {}).get(f"h{HORIZONS[j]}d", {}).get("diff_pct", np.nan)
            txt = f"{v:+.2f}\n({pct:+.1f}%)" if not np.isnan(pct) else f"{v:+.2f}"
            ax.text(j, i, txt, ha="center", va="center", fontsize=8,
                    color="white" if abs(v) > vmax * 0.5 else "black")
    plt.tight_layout()
    fig.savefig(CHART_DIR / "h6_01_effect_heatmap.png", dpi=150)
    plt.close(fig)

    # --- Chart 2: Return distributions (7d, by vol+funding regime) ----------
    fig, axes = plt.subplots(1, 3, figsize=(14, 5), sharey=True)
    for ax, vol in zip(axes, VOL_LABELS):
        sub_vol = m[(m["vol_regime"] == vol) & m["ret_7d"].notna()]
        for fr, color in FUND_COLORS.items():
            if fr == "unknown":
                continue
            vals = sub_vol.loc[sub_vol["funding_regime"] == fr, "ret_7d"] * 100
            if len(vals) > 0:
                ax.hist(vals, bins=30, alpha=0.5, color=color, label=fr.replace("_funding", ""),
                        density=True)
        ax.axvline(0, color="black", lw=0.8, ls="--")
        ax.set_title(f"{vol}")
        ax.set_xlabel("7d return (%)")
        if ax == axes[0]:
            ax.set_ylabel("Density")
        ax.legend(fontsize=7)
    fig.suptitle("H6: 7d Return Distributions by Funding Regime within each Vol Stratum")
    plt.tight_layout()
    fig.savefig(CHART_DIR / "h6_02_return_distributions.png", dpi=150)
    plt.close(fig)

    # --- Chart 3: OOS R2 comparison across strata ---------------------------
    horizons_oos = [1, 7, 14]
    fig, axes = plt.subplots(1, len(horizons_oos), figsize=(13, 5))
    for ax, h in zip(axes, horizons_oos):
        key = f"h{h}d"
        labels_plot = ["overall"] + VOL_LABELS
        r2_vals = [oos_results.get(key, {}).get("overall_r2", np.nan)]
        for vol in VOL_LABELS:
            r2_vals.append(oos_results.get(key, {}).get("by_vol", {}).get(vol, {}).get("r2", np.nan))
        colors_plot = ["#7f8c8d"] + [VOL_COLORS[v] for v in VOL_LABELS]
        bars = ax.bar(range(len(labels_plot)), r2_vals, color=colors_plot, alpha=0.8, edgecolor="black", lw=0.7)
        ax.axhline(0, color="black", lw=1.0)
        ax.set_xticks(range(len(labels_plot)))
        ax.set_xticklabels(["overall"] + [v.replace("_vol", "") for v in VOL_LABELS],
                           rotation=15, fontsize=8)
        ax.set_title(f"{h}d horizon")
        ax.set_ylabel("OOS R2" if h == 1 else "")
        for bar_obj, val in zip(bars, r2_vals):
            if not np.isnan(val):
                ax.text(bar_obj.get_x() + bar_obj.get_width() / 2, val + 0.003,
                        f"{val:+.3f}", ha="center", va="bottom", fontsize=7)
    fig.suptitle("H6: Tagged OOS R2 (Global Model) Decomposed by Vol Regime")
    plt.tight_layout()
    fig.savefig(CHART_DIR / "h6_03_oos_r2_by_vol.png", dpi=150)
    plt.close(fig)

    # --- Chart 4: Stratum-specific OOS R2 -----------------------------------
    fig, ax = plt.subplots(figsize=(9, 5))
    x_pos = np.arange(len(VOL_LABELS))
    width = 0.25
    for k, h in enumerate([1, 7, 14]):
        r2_vals = [stratum_oos.get(vol, {}).get(f"h{h}d", {}).get("r2", np.nan) for vol in VOL_LABELS]
        ax.bar(x_pos + k * width - width, r2_vals, width=width, label=f"{h}d",
               alpha=0.8, edgecolor="black", lw=0.6)
    ax.axhline(0, color="black", lw=1.0)
    ax.set_xticks(x_pos)
    ax.set_xticklabels(VOL_LABELS, rotation=10)
    ax.set_ylabel("OOS R2")
    ax.set_title("H6: Stratum-Specific OOS R2 (Training only on same-vol observations)")
    ax.legend()
    plt.tight_layout()
    fig.savefig(CHART_DIR / "h6_04_stratum_specific_oos.png", dpi=150)
    plt.close(fig)

    print(f"\n{_ts()} Charts saved -> {CHART_DIR}")


# =============================================================================
# Main
# =============================================================================

def main() -> None:
    t0 = time.time()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    CHART_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("H6 FUNDING x VOLATILITY INTERACTION -- Phase B.1D Evidence Battery")
    print(f"BTCUSDT  |  research corpus 2021-01 to 2026-05")
    print(f"Run date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 70)

    m = _load_all_data()

    stage1_summary(m)
    sp_results  = stage2_spearman_by_vol(m)
    cat_results = stage3_categorical_by_vol(m)
    oos_results = stage4_tagged_oos(m)
    str_results = stage5_stratum_specific_oos(m)
    rob_results = stage6_structural_robustness(m)
    mkt_results = stage7_within_highvol_market_regime(m)
    per_results = stage8_joint_persistence(m)
    stage9_charts(m, cat_results, oos_results, str_results)

    # ------------------------------------------------------------------
    # Verdict
    # ------------------------------------------------------------------
    print(f"\n{'='*70}")
    print("H6 VERDICT")
    print("-" * 70)

    # H6a: tagged OOS R2 > 0 + Cohen's d > 0.20 in at least one stratum x horizon
    h6a_pass = False
    h6a_detail = []
    for h in [7, 14]:
        key = f"h{h}d"
        for vol in VOL_LABELS:
            r2  = oos_results.get(key, {}).get("by_vol", {}).get(vol, {}).get("r2", np.nan)
            d   = cat_results.get(vol, {}).get(key, {}).get("cohens_d", np.nan)
            if (not np.isnan(r2)) and r2 > THRESH_OOS_R2 and (not np.isnan(d)) and abs(d) > THRESH_COHENS_D:
                h6a_pass = True
                h6a_detail.append(f"{vol}/{h}d: R2={r2:+.4f} d={d:+.3f}")

    # H6b: economic significance in at least one stratum x horizon
    h6b_pass = False
    h6b_detail = []
    for vol in VOL_LABELS:
        for h in HORIZONS:
            diff = cat_results.get(vol, {}).get(f"h{h}d", {}).get("diff_pct", np.nan)
            thresh = THRESH_EFFECT_7D if h == 7 else (THRESH_EFFECT_14D if h == 14 else 0.0)
            if thresh > 0 and (not np.isnan(diff)) and abs(diff) / 100 >= thresh:
                h6b_pass = True
                h6b_detail.append(f"{vol}/{h}d: diff={diff:+.2f}%")

    # H6c: stratum-specific OOS corroborates
    h6c_pass = False
    h6c_detail = []
    for h in [7, 14]:
        for vol in VOL_LABELS:
            r2  = str_results.get(vol, {}).get(f"h{h}d", {}).get("r2", np.nan)
            mwp = rob_results.get(vol, {}).get(f"h{h}d", {}).get("mw_p", np.nan)
            if (not np.isnan(r2)) and r2 > THRESH_OOS_R2 and (not np.isnan(mwp)) and mwp < THRESH_P:
                h6c_pass = True
                h6c_detail.append(f"{vol}/{h}d: R2={r2:+.4f} mwp={mwp:.3f}")

    overall = h6a_pass and h6b_pass

    v_h6a = "[PASS]" if h6a_pass else "[FAIL]"
    v_h6b = "[PASS]" if h6b_pass else "[FAIL]"
    v_h6c = "[PASS]" if h6c_pass else "[FAIL]"
    v_ovr = "[PASS]" if overall else "[FAIL]"

    print(f"  H6a: Tagged OOS R2>0 + |d|>0.20 (>=1 stratum/horizon)  : {v_h6a}")
    for det in h6a_detail:
        print(f"       -> {det}")
    print(f"  H6b: Economic significance (>=1 stratum/horizon)        : {v_h6b}")
    for det in h6b_detail:
        print(f"       -> {det}")
    print(f"  H6c: Stratum OOS + MWU p<0.05 (robustness gate)         : {v_h6c}")
    for det in h6c_detail:
        print(f"       -> {det}")
    print("-" * 70)
    print(f"  Overall H6                                               : {v_ovr}")
    print("=" * 70)

    elapsed = round((time.time() - t0) / 60, 1)
    print(f"\n{v_ovr} H6 COMPLETE  elapsed: {elapsed} min")
    print(f"Charts -> {CHART_DIR}")

    # Save JSON summary
    summary = {
        "hypothesis": "H6 Funding x Volatility Interaction",
        "symbol": SYMBOL,
        "n_days": int(len(m)),
        "date_start": str(m["date"].iloc[0]),
        "date_end":   str(m["date"].iloc[-1]),
        "spearman_by_vol": {
            v: {k: {kk: (float(vv) if isinstance(vv, (int, float, np.floating)) else vv)
                    for kk, vv in inner.items()}
                for k, inner in row.items()}
            for v, row in sp_results.items()
        },
        "categorical_effects": {
            v: {
                k: {kk: (float(vv) if isinstance(vv, (int, float, np.floating)) else vv)
                    for kk, vv in cell.items()}
                for k, cell in row.items() if isinstance(row, dict) and isinstance(cell, dict)
            }
            for v, row in cat_results.items()
        },
        "tagged_oos": {
            k: {
                "overall_r2": float(v.get("overall_r2", np.nan)),
                "n_oos": int(v.get("n_oos", 0)),
                "by_vol": {
                    vol: {kk: (float(vv) if isinstance(vv, (int, float, np.floating)) else vv)
                          for kk, vv in bv.items()}
                    for vol, bv in v.get("by_vol", {}).items()
                },
            }
            for k, v in oos_results.items()
        },
        "stratum_specific_oos": {
            vol: {
                hk: {kk: (float(vv) if isinstance(vv, (int, float, np.floating)) else vv)
                     for kk, vv in hv.items()}
                for hk, hv in row.items()
            }
            for vol, row in str_results.items()
        },
        "structural_robustness": {
            vol: {
                hk: {kk: (float(vv) if isinstance(vv, (int, float, np.floating)) else vv)
                     for kk, vv in hv.items()}
                for hk, hv in row.items() if isinstance(hv, dict)
            }
            for vol, row in rob_results.items()
        },
        "verdict": {
            "H6a tagged_oos_and_cohens_d": v_h6a,
            "H6b economic_significance":   v_h6b,
            "H6c stratum_oos_robustness":  v_h6c,
            "overall": v_ovr,
        },
        "run_date": datetime.now(timezone.utc).isoformat(),
        "elapsed_s": round(time.time() - t0, 1),
    }
    json_path = REPORT_DIR / "h6_summary.json"
    with open(json_path, "w") as fh:
        json.dump(summary, fh, indent=2, allow_nan=True)
    print(f"Report -> {json_path}")
    print("=" * 70)


if __name__ == "__main__":
    main()
