"""
H1 Funding Regimes -- Phase B.1C Evidence Battery
==================================================

Does the BTC perpetual funding regime predict future spot returns?

H1 null:
    Funding regime has no economically significant predictive power for
    BTC forward spot returns over any horizon (1d, 3d, 7d, 14d).

H1 alternative:
    Persistent high funding (crowded longs) precedes below-average
    returns; persistent low/negative funding precedes above-average
    returns, with effect size increasing from 1d to 14d.

Signal
------
Daily mean funding rate (bps): average of up to 3 x 8-hour settlements.
Funding regime: rolling 252-day tercile.
    high_funding    = top third    (excess long positioning -> bearish signal)
    neutral_funding = middle third
    low_funding     = bottom third (excess short positioning -> bullish signal)

Stratifiers
-----------
H4 basis_regime  : contango / flat / backwardation  (from daily mean basis)
H5 vol_regime    : low_vol / medium_vol / high_vol  (from 21-day RV)

Anti-lookahead
--------------
funding_regime(d) : uses daily_mean_funding[d-251..d].   Available at d. [OK]
basis_regime(d)   : uses daily_mean_basis[d-251..d].     Available at d. [OK]
vol_regime(d)     : uses RV_21[d-251..d].                Available at d. [OK]
ret_h(d)          : close[d+h]/close[d]-1.               Future outcome.  [OK]
OOS step t        : model trained on [0..t-1].            Available at t.  [OK]

Economic significance thresholds (primary criterion per design)
---------------------------------------------------------------
1d  : |mean_high - mean_low| >= 0.5%
7d  : |mean_high - mean_low| >= 2.0%
14d : |mean_high - mean_low| >= 3.0%

Run:
    python research/hypotheses/h1_funding_regimes/run_h1.py
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
import numpy as np
import pandas as pd
from scipy.stats import (
    spearmanr, chi2_contingency, ttest_1samp, mannwhitneyu, t as t_dist,
    binomtest,
)

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=RuntimeWarning)

# ---- Path setup --------------------------------------------------------
_H1_DIR       = pathlib.Path(__file__).resolve().parent
_RESEARCH_DIR = _H1_DIR.parent.parent
if str(_RESEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_DIR))

from storage.duck import open_db, query, register_view          # noqa: E402
from hypotheses.shared.market_regimes import build_market_regimes   # noqa: E402
from hypotheses.shared.structural_events import (               # noqa: E402
    events_df, build_exclusion_mask_dates,
)

# ---- Directories --------------------------------------------------------
BASE_DIR   = _RESEARCH_DIR / "data" / "raw"
REPORT_DIR = _RESEARCH_DIR / "reports" / "h1_funding_regimes"
CHART_DIR  = REPORT_DIR / "charts"
SYMBOL     = "BTCUSDT"

# ---- Analysis parameters ------------------------------------------------
HORIZONS        = [1, 3, 7, 14]        # forward-return horizons (days)
TRAIN_WINDOW    = 252                   # min days before OOS walk-forward starts
N_BOOTSTRAP     = 2_000
REGIME_WINDOW   = 252                   # rolling window for tercile classification
REGIME_PCTILE   = 33                    # tercile split
RV_WINDOW       = 21                    # days for realized-vol computation
NW_MAXLAGS      = 3                     # Newey-West lags (for overlapping returns use h-1)
MIN_MINS_1H     = 30
MIN_DAILY_FUND  = 2                     # min settlements per day to accept daily mean

# ---- Pass/fail thresholds (economic significance primary) ---------------
THRESH_EFFECT_1D  = 0.005   # 0.5%  mean return diff (high - low) per 1-day horizon
THRESH_EFFECT_7D  = 0.020   # 2.0%  per 7-day horizon
THRESH_EFFECT_14D = 0.030   # 3.0%  per 14-day horizon
THRESH_P          = 0.05
THRESH_OOS_R2     = 0.000   # any positive OOS R2 counts
THRESH_CHI2_P     = 0.01
THRESH_HALFLIFE   = 3.0
THRESH_DIAG_DOM   = 0.20
THRESH_HIT_RATE   = 0.50    # directional hit rate > 50%

FUND_LABELS    = ["high_funding", "neutral_funding", "low_funding"]
FUND_COLORS    = {
    "high_funding":    "#e74c3c",
    "neutral_funding": "#95a5a6",
    "low_funding":     "#27ae60",
    "unknown":         "#ffffff",
}
BASIS_LABELS = ["contango", "flat", "backwardation"]
VOL_LABELS   = ["low_vol", "medium_vol", "high_vol"]


# =========================================================================
# Statistical helpers
# =========================================================================

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
        rho=float(rho_full),
        pval=float(pval),
        ci_lo=float(np.percentile(boot, 2.5)),
        ci_hi=float(np.percentile(boot, 97.5)),
        n=int(len(x)),
    )


def _newey_west_ols(y: np.ndarray, x: np.ndarray, maxlags: int = NW_MAXLAGS) -> dict:
    """OLS with Newey-West HAC standard errors."""
    n = len(y)
    X = np.column_stack([np.ones(n), x])
    k = 2
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
    pval = 2.0 * t_dist.sf(np.abs(tstat), df=n - k)
    r2 = float(1.0 - np.sum(e ** 2) / np.sum((y - y.mean()) ** 2))
    return dict(
        beta_x=float(beta[1]),
        se_x=float(se[1]),
        t_x=float(tstat[1]),
        p_x=float(pval[1]),
        r2=float(r2),
        n=n,
    )


def _return_stats(ret: np.ndarray) -> dict:
    """Mean, std, t-test vs zero, 95% CI for a return series."""
    n = len(ret)
    if n < 10:
        return {"n": n, "mean_pct": np.nan, "std_pct": np.nan, "t": np.nan, "p": np.nan}
    mean = float(ret.mean())
    std  = float(ret.std(ddof=1))
    se   = std / np.sqrt(n)
    tstat = mean / se if se > 0 else np.nan
    pval  = float(2.0 * t_dist.sf(abs(tstat), df=n - 1)) if not np.isnan(tstat) else np.nan
    ci_lo = mean - 1.96 * se
    ci_hi = mean + 1.96 * se
    return dict(
        n=n,
        mean_pct=float(mean * 100),
        std_pct=float(std * 100),
        se_pct=float(se * 100),
        ci_lo_pct=float(ci_lo * 100),
        ci_hi_pct=float(ci_hi * 100),
        t=float(tstat),
        p=float(pval),
    )


def _effect_size(high_ret: np.ndarray, low_ret: np.ndarray) -> dict:
    """Effect size between high and low funding regime returns."""
    if len(high_ret) < 10 or len(low_ret) < 10:
        return {"diff_pct": np.nan, "cohens_d": np.nan, "mw_p": np.nan}
    diff = float((high_ret.mean() - low_ret.mean()) * 100)
    pooled_var = (
        (len(high_ret) - 1) * high_ret.var(ddof=1) +
        (len(low_ret)  - 1) * low_ret.var(ddof=1)
    ) / (len(high_ret) + len(low_ret) - 2)
    pooled_std = float(np.sqrt(pooled_var)) if pooled_var > 0 else np.nan
    cohens_d   = (high_ret.mean() - low_ret.mean()) / pooled_std if pooled_std else np.nan
    try:
        _, mw_p = mannwhitneyu(high_ret, low_ret, alternative="two-sided")
    except Exception:
        mw_p = np.nan
    return dict(
        diff_pct=diff,
        cohens_d=float(cohens_d),
        mw_p=float(mw_p),
    )


def _oos_walk_forward(
    signal: np.ndarray,
    ret: np.ndarray,
    train_window: int,
    nw_lags: int = 1,
) -> dict:
    """
    Expanding-window OLS: signal -> ret.  Returns OOS R2 (vectorized cumsum).
    For 1d non-overlapping returns. Set nw_lags=h-1 for h-day overlapping returns.
    """
    valid = ~(np.isnan(signal) | np.isnan(ret))
    x = signal[valid]
    y = ret[valid]
    n = len(x)
    if n < train_window + 30:
        return {"overall_r2": np.nan, "n_oos": 0}

    cum_x  = np.concatenate([[0.0], np.cumsum(x)])
    cum_y  = np.concatenate([[0.0], np.cumsum(y)])
    cum_xy = np.concatenate([[0.0], np.cumsum(x * y)])
    cum_xx = np.concatenate([[0.0], np.cumsum(x ** 2)])

    t_arr = np.arange(train_window, n)
    n_t   = t_arr.astype(float)
    sx    = cum_x[t_arr];  sy  = cum_y[t_arr]
    sxy   = cum_xy[t_arr]; sxx = cum_xx[t_arr]

    denom = n_t * sxx - sx ** 2
    ok    = np.abs(denom) > 1e-15
    b1    = np.where(ok, (n_t * sxy - sx * sy) / np.where(ok, denom, 1.0), 0.0)
    b0    = np.where(ok, (sy - b1 * sx) / n_t, 0.0)

    y_hat = np.where(ok, b0 + b1 * x[t_arr], np.nan)
    y_act = y[t_arr]
    valid2 = ~np.isnan(y_hat)
    y_hat  = y_hat[valid2]; y_act = y_act[valid2]

    ss_res = float(np.sum((y_act - y_hat) ** 2))
    ss_tot = float(np.sum((y_act - y_act.mean()) ** 2))
    overall_r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    # Directional hit rate
    hits  = int(np.sum((y_hat > 0) == (y_act > 0)))
    total = len(y_act)
    hit_rate = hits / total if total > 0 else np.nan
    try:
        binom = binomtest(hits, total, 0.5, alternative="greater")
        binom_p = float(binom.pvalue)
    except Exception:
        binom_p = np.nan

    return dict(
        overall_r2=float(overall_r2),
        n_oos=total,
        hit_rate=hit_rate,
        binom_p=binom_p,
        y_hat=y_hat,
        y_act=y_act,
    )


def _regime_persistence(regime_series: pd.Series, labels: list[str]) -> dict:
    """Generic Markov persistence analysis for any 3-state regime."""
    s = regime_series[~regime_series.isin(["unknown"])]
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

    half_lives: dict[str, float] = {}
    for lbl in labels:
        runs: list[int] = []; cur = 0
        for val in (s == lbl).astype(int):
            if val:   cur += 1
            elif cur: runs.append(cur); cur = 0
        if cur: runs.append(cur)
        half_lives[lbl] = float(np.mean(runs)) if runs else 0.0

    return dict(
        transition_counts={r: {c: int(trans.loc[r, c]) for c in labels} for r in labels},
        prob_matrix={r: {c: float(prob.loc[r, c]) for c in labels} for r in labels},
        chi2=float(chi2), p_chi2=float(p_chi2), dof=int(dof),
        cramers_v=cramers_v, diag_dominance=diag_dom,
        half_lives=half_lives, n_transitions=n_trans,
    )


# =========================================================================
# Regime builders
# =========================================================================

def _build_tercile_regime(series: pd.Series, labels: list[str], window: int = REGIME_WINDOW) -> pd.Series:
    """
    Rolling 252-day tercile regime for any continuous series.
    labels = [top_label, mid_label, bottom_label].
    """
    lo = series.rolling(window, min_periods=window).quantile(REGIME_PCTILE / 100)
    hi = series.rolling(window, min_periods=window).quantile(1.0 - REGIME_PCTILE / 100)
    regime = pd.Series(labels[1], index=series.index, dtype=object)
    regime[series > hi] = labels[0]
    regime[series < lo] = labels[2]
    regime[lo.isna() | hi.isna()] = "unknown"
    return regime


def _build_vol_regimes(daily_close: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Return (rv_21d, vol_regime) from daily close prices."""
    log_ret = np.log(daily_close / daily_close.shift(1))
    rv = log_ret.rolling(RV_WINDOW, min_periods=RV_WINDOW).std() * np.sqrt(252)
    vol_regime = _build_tercile_regime(rv, ["high_vol", "medium_vol", "low_vol"])
    return rv, vol_regime


# =========================================================================
# Data loading via DuckDB
# =========================================================================

def _load_all_data() -> pd.DataFrame:
    """
    Build the master daily analysis frame with columns:
      day_ts, dt, date, daily_close,
      daily_mean_funding_bps, daily_mean_basis_bps,
      rv_21d, funding_regime, basis_regime, vol_regime,
      market_regime,
      ret_1d, ret_3d, ret_7d, ret_14d
    """
    print(f"{_ts()} Loading data via DuckDB...")
    con = open_db()
    n_spot = register_view(con, "spot_1m",     BASE_DIR, "spot",    SYMBOL)
    n_perp = register_view(con, "perp_1m",     BASE_DIR, "perp",    SYMBOL)
    n_fund = register_view(con, "funding_raw", BASE_DIR, "funding", SYMBOL)
    print(f"  spot_1m: {n_spot}  perp_1m: {n_perp}  funding_raw: {n_fund} partitions")

    # Hourly basis (integer division with //)
    con.execute(f"""
    CREATE OR REPLACE VIEW basis_1h AS
    SELECT
        (s.open_time // 3600000) * 3600000          AS hour_ts,
        AVG(p.close - s.close)                      AS basis_abs,
        AVG((p.close / s.close - 1.0) * 10000.0)   AS basis_bps,
        AVG(s.close)                                AS spot_price,
        COUNT(*)                                    AS n_min
    FROM spot_1m s
    JOIN perp_1m p ON s.open_time = p.open_time
    GROUP BY s.open_time // 3600000
    HAVING COUNT(*) >= {MIN_MINS_1H}
    """)

    # Daily mean basis
    df_basis_daily = query(con, """
    SELECT
        (hour_ts // 86400000) * 86400000 AS day_ts,
        AVG(basis_bps) AS daily_mean_basis_bps
    FROM basis_1h
    GROUP BY hour_ts // 86400000
    HAVING COUNT(*) >= 12
    ORDER BY day_ts
    """)

    # Daily mean funding rate (bps)
    df_fund_daily = query(con, f"""
    SELECT
        (timestamp_ms // 86400000) * 86400000       AS day_ts,
        AVG(funding_rate) * 10000.0                 AS daily_mean_funding_bps,
        COUNT(*)                                    AS n_settlements
    FROM funding_raw
    GROUP BY timestamp_ms // 86400000
    HAVING COUNT(*) >= {MIN_DAILY_FUND}
    ORDER BY day_ts
    """)

    # Daily spot close (last 1m candle per UTC day)
    df_spot_daily = query(con, """
    SELECT day_ts, last_close AS daily_close
    FROM (
        SELECT
            (open_time // 86400000) * 86400000  AS day_ts,
            close                               AS last_close,
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

    # Convert day_ts to datetime
    for df in [df_basis_daily, df_fund_daily, df_spot_daily]:
        df["dt"] = pd.to_datetime(df["day_ts"], unit="ms", utc=True)

    # Merge to master frame (outer join on day_ts, use spot_daily as spine)
    master = df_spot_daily.merge(
        df_fund_daily[["day_ts", "daily_mean_funding_bps", "n_settlements"]],
        on="day_ts", how="left",
    ).merge(
        df_basis_daily[["day_ts", "daily_mean_basis_bps"]],
        on="day_ts", how="left",
    )
    master["dt"]   = pd.to_datetime(master["day_ts"], unit="ms", utc=True)
    master["date"] = master["dt"].dt.normalize().dt.tz_localize(None).dt.date
    master = master.sort_values("day_ts").reset_index(drop=True)

    # Regime classifications (all rolling, anti-lookahead)
    close_s   = master["daily_close"]
    rv, vol_r = _build_vol_regimes(close_s)
    master["rv_21d"]     = rv.values
    master["vol_regime"] = vol_r.values

    master["basis_regime"] = _build_tercile_regime(
        master["daily_mean_basis_bps"],
        ["contango", "flat", "backwardation"],
    ).values

    master["funding_regime"] = _build_tercile_regime(
        master["daily_mean_funding_bps"],
        ["high_funding", "neutral_funding", "low_funding"],
    ).values

    # Market regime (SMA200 + slope) — needs DatetimeIndex
    close_dt = pd.Series(close_s.values, index=master["dt"].values)
    mkt_reg = build_market_regimes(close_dt)
    master["market_regime"] = "unknown"
    if not mkt_reg.empty:
        mkt_map = {k.normalize(): v for k, v in mkt_reg.to_dict().items()}
        master["market_regime"] = (
            master["dt"].dt.normalize().dt.tz_localize(None).map(mkt_map).fillna("unknown")
        )

    # Forward returns: ret_h(d) = close[d+h] / close[d] - 1
    for h in HORIZONS:
        master[f"ret_{h}d"] = close_s.shift(-h).values / close_s.values - 1.0

    return master


# =========================================================================
# Stage functions
# =========================================================================

def stage1_summary(m: pd.DataFrame) -> None:
    print(f"\n{_ts()} STAGE 1  Sample summary")
    known_fund = (m["funding_regime"] != "unknown") & m["daily_mean_funding_bps"].notna()
    print(f"  Total daily rows           : {len(m):,}")
    print(f"  Date range                 : {m['dt'].min().date()} to {m['dt'].max().date()}")
    print(f"  Days with funding regime   : {known_fund.sum():,}")
    print(f"  Mean funding (bps/day)     : {m['daily_mean_funding_bps'].mean():+.3f}")
    print(f"  Funding regime dist:")
    for reg in FUND_LABELS + ["unknown"]:
        cnt = int((m["funding_regime"] == reg).sum())
        print(f"    {reg:18s} : {cnt:,}")
    print(f"  Mean RV_21d (annualized)   : {m['rv_21d'].dropna().mean():.3f}  "
          f"({m['rv_21d'].dropna().mean()*100:.1f}%)")


def stage2_spearman(m: pd.DataFrame) -> dict:
    """H1a: Spearman rho between daily funding level and forward returns."""
    print(f"\n{_ts()} STAGE 2  H1a -- Spearman rho (funding -> forward returns)")
    results: dict = {}
    for h in HORIZONS:
        col = f"ret_{h}d"
        sub = m[m["daily_mean_funding_bps"].notna() & m[col].notna()].copy()
        if len(sub) < 100:
            print(f"  {h:2d}d : too few obs ({len(sub)}) -- skip")
            continue
        x = sub["daily_mean_funding_bps"].to_numpy()
        y = sub[col].to_numpy()
        n_boot = 500
        sp = _spearman_ci(x, y, n_boot=n_boot)
        # NW-OLS with h-1 lags for overlapping returns
        nw = _newey_west_ols(y, x, maxlags=max(1, h - 1))
        verdict = "[PASS]" if (abs(sp["rho"]) >= 0.05 and sp["pval"] < THRESH_P) else "[FAIL]"
        print(f"  {h:2d}d : rho={sp['rho']:+.4f}  p={sp['pval']:.3f}  "
              f"beta_NW={nw['beta_x']:+.6f}  R2={nw['r2']:.4f}  n={sp['n']:,}  {verdict}")
        results[f"h{h}d"] = {"spearman": sp, "nw_ols": nw}
    return results


def stage3_categorical_effects(m: pd.DataFrame) -> dict:
    """
    H1b: Mean forward return per funding regime for each horizon.
    Primary economic significance test.
    """
    print(f"\n{_ts()} STAGE 3  H1b -- Mean return by funding regime (economic significance)")
    results: dict = {}

    for h in HORIZONS:
        col = f"ret_{h}d"
        sub = m[m["funding_regime"].isin(FUND_LABELS) & m[col].notna()]
        if len(sub) < 50:
            continue

        row: dict[str, dict] = {}
        for reg in FUND_LABELS:
            r = sub.loc[sub["funding_regime"] == reg, col].to_numpy()
            row[reg] = _return_stats(r)

        high_r = sub.loc[sub["funding_regime"] == "high_funding", col].to_numpy()
        low_r  = sub.loc[sub["funding_regime"] == "low_funding",  col].to_numpy()
        row["effect"] = _effect_size(high_r, low_r)

        diff  = row["effect"]["diff_pct"]
        thresh = {1: THRESH_EFFECT_1D * 100, 3: 1.0, 7: THRESH_EFFECT_7D * 100,
                  14: THRESH_EFFECT_14D * 100}[h]
        economic_sig = abs(diff) >= thresh
        sign_ok = diff < 0   # theory: high_funding -> negative return (below low_funding)

        print(f"  {h:2d}d : "
              f"high={row['high_funding']['mean_pct']:+.2f}%  "
              f"neutral={row['neutral_funding']['mean_pct']:+.2f}%  "
              f"low={row['low_funding']['mean_pct']:+.2f}%  "
              f"diff(H-L)={diff:+.2f}%  "
              f"MWU_p={row['effect']['mw_p']:.3f}  "
              f"CohenD={row['effect']['cohens_d']:+.3f}  "
              f"{'[ECON_SIG]' if economic_sig else '[small]'}  "
              f"{'[correct_sign]' if sign_ok else '[reversed_sign]'}")

        row["economic_significant"] = economic_sig
        row["correct_sign"] = sign_ok
        row["threshold_pct"] = thresh
        results[f"h{h}d"] = row

    # Summary: how many horizons pass economic significance?
    n_econ = sum(1 for h in HORIZONS if results.get(f"h{h}d", {}).get("economic_significant", False))
    n_corr = sum(1 for h in HORIZONS if results.get(f"h{h}d", {}).get("correct_sign", False))
    print(f"\n  Economic significance: {n_econ}/{len(HORIZONS)} horizons meet threshold")
    print(f"  Correct sign (high->neg): {n_corr}/{len(HORIZONS)} horizons")
    results["n_econ_sig"] = n_econ
    results["n_correct_sign"] = n_corr
    return results


def stage4_oos(m: pd.DataFrame) -> dict:
    """
    H1c: Walk-forward OOS R2 for daily funding -> 1d and 7d forward return.
    1d: non-overlapping, clean OOS.
    7d: overlapping (flagged), NW-1 lags.
    """
    print(f"\n{_ts()} STAGE 4  H1c -- Walk-forward OOS R2")

    sub = m[m["daily_mean_funding_bps"].notna()].copy()
    x   = sub["daily_mean_funding_bps"].to_numpy()
    results: dict = {}

    for h in [1, 7]:
        col = f"ret_{h}d"
        y_full = sub[col].to_numpy()
        oos = _oos_walk_forward(x, y_full, train_window=TRAIN_WINDOW, nw_lags=max(1, h - 1))
        pass_r2   = (not np.isnan(oos["overall_r2"])) and (oos["overall_r2"] > THRESH_OOS_R2)
        pass_hit  = (not np.isnan(oos["hit_rate"]))   and (oos["hit_rate"] > THRESH_HIT_RATE)
        overlap_note = "(overlapping -- t-stats inflated)" if h > 1 else ""
        print(f"  {h:2d}d OOS R2 = {oos['overall_r2']:+.4f}  "
              f"hit_rate = {oos['hit_rate']:.3f}  "
              f"binom_p = {oos['binom_p']:.4f}  "
              f"n_oos = {oos['n_oos']:,}  "
              f"{'[PASS]' if pass_r2 else '[FAIL]'} R2  "
              f"{'[PASS]' if pass_hit else '[FAIL]'} hit  {overlap_note}")
        results[f"h{h}d"] = {
            "overall_r2": oos["overall_r2"],
            "hit_rate":   oos["hit_rate"],
            "binom_p":    oos["binom_p"],
            "n_oos":      oos["n_oos"],
            "pass_r2":    pass_r2,
            "pass_hit":   pass_hit,
        }

    return results


def stage5_basis_stratification(m: pd.DataFrame) -> dict:
    """Effect size (high - low funding, 7d return) within each basis regime."""
    print(f"\n{_ts()} STAGE 5  Basis regime stratification (H4 stratifier)")
    results: dict = {}

    for basis in BASIS_LABELS:
        sub = m[(m["basis_regime"] == basis) & m["ret_7d"].notna()]
        if len(sub) < 50:
            continue
        row: dict = {"n": len(sub)}
        for h in HORIZONS:
            col = f"ret_{h}d"
            sub_h = sub[sub[col].notna() & sub["funding_regime"].isin(FUND_LABELS)]
            high_r = sub_h.loc[sub_h["funding_regime"] == "high_funding", col].to_numpy()
            low_r  = sub_h.loc[sub_h["funding_regime"] == "low_funding",  col].to_numpy()
            eff = _effect_size(high_r, low_r)
            row[f"h{h}d_diff_pct"] = eff["diff_pct"]
            row[f"h{h}d_cohens_d"] = eff["cohens_d"]

        results[basis] = row
        line = f"  {basis:15s} n={len(sub):,}"
        for h in HORIZONS:
            d = row.get(f"h{h}d_diff_pct", np.nan)
            line += f"  {h}d:{d:+.2f}%"
        print(line)

    return results


def stage6_vol_stratification(m: pd.DataFrame) -> dict:
    """Effect size (high - low funding, 7d return) within each vol regime."""
    print(f"\n{_ts()} STAGE 6  Vol regime stratification (H5 stratifier)")
    results: dict = {}

    for vol in VOL_LABELS:
        sub = m[(m["vol_regime"] == vol) & m["ret_7d"].notna()]
        if len(sub) < 50:
            continue
        row: dict = {"n": len(sub)}
        for h in HORIZONS:
            col = f"ret_{h}d"
            sub_h = sub[sub[col].notna() & sub["funding_regime"].isin(FUND_LABELS)]
            high_r = sub_h.loc[sub_h["funding_regime"] == "high_funding", col].to_numpy()
            low_r  = sub_h.loc[sub_h["funding_regime"] == "low_funding",  col].to_numpy()
            eff = _effect_size(high_r, low_r)
            row[f"h{h}d_diff_pct"] = eff["diff_pct"]
            row[f"h{h}d_cohens_d"] = eff["cohens_d"]

        results[vol] = row
        line = f"  {vol:12s} n={len(sub):,}"
        for h in HORIZONS:
            d = row.get(f"h{h}d_diff_pct", np.nan)
            line += f"  {h}d:{d:+.2f}%"
        print(line)

    # Cross-table: funding x vol at 7d
    print(f"\n  Cross-table (funding regime x vol regime, 7d effect size):")
    print(f"  {'':22s}" + "".join(f"{v:>14s}" for v in VOL_LABELS))
    for fund in FUND_LABELS:
        row_vals = []
        for vol in VOL_LABELS:
            sub = m[(m["funding_regime"] == fund) & (m["vol_regime"] == vol) &
                    m["ret_7d"].notna()]
            mean_7d = float(sub["ret_7d"].mean() * 100) if len(sub) >= 20 else float("nan")
            row_vals.append(mean_7d)
        print(f"  {fund:22s}" + "".join(f"{v:+12.2f}%" for v in row_vals))

    return results


def stage7_market_regime(m: pd.DataFrame) -> dict:
    """Effect sizes within each market regime (bull/bear/sideways)."""
    print(f"\n{_ts()} STAGE 7  Market regime stratification")
    results: dict = {}

    for mkt in ["bull", "bear", "sideways"]:
        sub = m[(m["market_regime"] == mkt) & m["ret_7d"].notna()]
        if len(sub) < 50:
            continue
        row: dict = {"n": len(sub)}
        for h in [1, 7, 14]:
            col = f"ret_{h}d"
            sub_h = sub[sub[col].notna() & sub["funding_regime"].isin(FUND_LABELS)]
            high_r = sub_h.loc[sub_h["funding_regime"] == "high_funding", col].to_numpy()
            low_r  = sub_h.loc[sub_h["funding_regime"] == "low_funding",  col].to_numpy()
            eff = _effect_size(high_r, low_r)
            row[f"h{h}d_diff_pct"] = eff["diff_pct"]
        results[mkt] = row
        line = f"  {mkt:10s} n={len(sub):,}"
        for h in [1, 7, 14]:
            d = row.get(f"h{h}d_diff_pct", np.nan)
            line += f"  {h}d:{d:+.2f}%"
        print(line)

    return results


def stage8_persistence(m: pd.DataFrame) -> dict:
    """H1d: Funding regime transition matrix."""
    print(f"\n{_ts()} STAGE 8  Funding regime persistence")

    reg_series = m.set_index("date")["funding_regime"]
    known = reg_series[reg_series.isin(FUND_LABELS)]
    freq  = known.value_counts()
    total = len(known)
    print(f"  Regime frequencies (n={total:,}, excl. warmup):")
    for reg in FUND_LABELS:
        cnt = int(freq.get(reg, 0))
        print(f"    {reg:18s} : {cnt:,} ({cnt/total*100:.1f}%)")

    pers = _regime_persistence(reg_series, FUND_LABELS)

    v_chi2 = "[PASS]" if pers["p_chi2"] < THRESH_CHI2_P else "[FAIL]"
    v_dd   = "[PASS]" if pers["diag_dominance"] >= THRESH_DIAG_DOM else "[FAIL]"
    v_hl   = "[PASS]" if min(pers["half_lives"].values()) >= THRESH_HALFLIFE else "[FAIL]"

    print(f"\n  Transition matrix (row=from, col=to):")
    print(f"  {'':22s}" + "".join(f"{l:>18s}" for l in FUND_LABELS))
    for r in FUND_LABELS:
        row = f"  {r:22s}" + "".join(
            f"{pers['prob_matrix'][r][c]*100:16.1f}%" for c in FUND_LABELS
        )
        print(row)

    print(f"\n  Chi2={pers['chi2']:.2f}  p={pers['p_chi2']:.2e}  "
          f"Cramer_V={pers['cramers_v']:.4f}  {v_chi2}")
    print(f"  Diagonal dominance={pers['diag_dominance']:+.4f}  {v_dd}")
    print(f"  Half-lives:")
    for reg in FUND_LABELS:
        print(f"    {reg:18s} : {pers['half_lives'][reg]:.1f} days")
    print(f"  Min half-life = {min(pers['half_lives'].values()):.1f} days  {v_hl}")

    pers["verdicts"] = dict(chi2=v_chi2, diag_dom=v_dd, halflife=v_hl)
    return pers


def stage9_structural(m: pd.DataFrame) -> dict:
    """Clean sample: exclude +/-7d windows around structural events."""
    print(f"\n{_ts()} STAGE 9  Structural event conditioning (clean sample)")

    excl = build_exclusion_mask_dates(
        pd.DatetimeIndex(pd.to_datetime(m["date"].astype(str), utc=True))
    )
    m_clean = m[~excl.values]
    print(f"  Excluded {excl.sum()} days within +/-7d of structural events")
    print(f"  Clean sample n = {len(m_clean):,} ({len(m_clean)/len(m)*100:.1f}%)")

    results: dict = {}
    for h in HORIZONS:
        col = f"ret_{h}d"
        sub = m_clean[m_clean["funding_regime"].isin(FUND_LABELS) & m_clean[col].notna()]
        if len(sub) < 30:
            continue
        high_r = sub.loc[sub["funding_regime"] == "high_funding", col].to_numpy()
        low_r  = sub.loc[sub["funding_regime"] == "low_funding",  col].to_numpy()
        eff = _effect_size(high_r, low_r)
        results[f"h{h}d"] = eff
        print(f"  {h:2d}d clean : diff(H-L)={eff['diff_pct']:+.2f}%  "
              f"CohenD={eff['cohens_d']:+.3f}  MWU_p={eff['mw_p']:.3f}  n={len(sub):,}")

    return results


# =========================================================================
# Charts
# =========================================================================

def _shade_fund_regimes(ax, m: pd.DataFrame) -> None:
    dates   = m["dt"].values
    regimes = m["funding_regime"].values
    prev_reg = None; prev_dt = None
    for dt, reg in zip(dates, regimes):
        if reg != prev_reg:
            if prev_reg is not None and prev_reg in FUND_COLORS and prev_reg != "unknown":
                ax.axvspan(prev_dt, dt, alpha=0.12,
                           color=FUND_COLORS[prev_reg], linewidth=0)
            prev_reg = reg; prev_dt = dt
    if prev_reg and prev_reg in FUND_COLORS and prev_reg != "unknown" and prev_dt is not None:
        ax.axvspan(prev_dt, dates[-1], alpha=0.12,
                   color=FUND_COLORS[prev_reg], linewidth=0)


def _mark_events(ax) -> None:
    ev = events_df()
    ylim = ax.get_ylim()
    for _, row in ev.iterrows():
        ax.axvline(row["date"], color="#999999", linewidth=0.7, linestyle="--", alpha=0.5)
        ax.text(row["date"], ylim[0] + (ylim[1] - ylim[0]) * 0.02,
                row["label"], rotation=90, fontsize=6, color="#555555",
                va="bottom", ha="right")


def stage10_charts(
    m: pd.DataFrame,
    spearman_res: dict,
    categorical_res: dict,
    oos_res: dict,
    persistence_res: dict,
    vol_strat_res: dict,
) -> None:
    print(f"\n{_ts()} STAGE 10  Generating charts -> {CHART_DIR}")
    CHART_DIR.mkdir(parents=True, exist_ok=True)

    plt.rcParams.update({
        "figure.dpi": 120, "font.size": 9,
        "axes.grid": True, "grid.alpha": 0.25,
        "axes.spines.top": False, "axes.spines.right": False,
    })

    close_s   = m.set_index("dt")["daily_close"]
    funding_s = m.set_index("dt")["daily_mean_funding_bps"]

    # ---- Chart 1: BTC price + funding rate with regime shading ------------
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 7), sharex=True)
    ax1.semilogy(close_s.index, close_s.values, color="#2c3e50", linewidth=0.8)
    _shade_fund_regimes(ax1, m)
    ax1.set_ylabel("BTC Price (log scale)")
    ax1.set_title("H1 Overview: BTC Price & Funding Rate by Funding Regime")
    patches = [
        plt.Rectangle((0, 0), 1, 1, color=FUND_COLORS[l], alpha=0.5, label=l)
        for l in FUND_LABELS
    ]
    ax1.legend(handles=patches, loc="upper left", fontsize=8)

    ax2.plot(funding_s.index, funding_s.values, color="#2980b9", linewidth=0.6)
    ax2.axhline(0, color="black", linewidth=0.8, linestyle="-")
    _shade_fund_regimes(ax2, m)
    ax2.set_ylabel("Daily Mean Funding (bps)")
    ax2.set_xlabel("Date")
    _mark_events(ax2)
    fig.tight_layout()
    fig.savefig(CHART_DIR / "h1_01_overview.png")
    plt.close(fig)
    print("  [OK] h1_01_overview.png")

    # ---- Chart 2: Mean forward return by funding regime (2x2 grid) --------
    fig, axes = plt.subplots(2, 2, figsize=(12, 8))
    for idx, h in enumerate(HORIZONS):
        ax = axes[idx // 2][idx % 2]
        row = categorical_res.get(f"h{h}d", {})
        labels_p = FUND_LABELS
        means  = [row.get(reg, {}).get("mean_pct", np.nan) for reg in labels_p]
        se_arr = [row.get(reg, {}).get("se_pct",   np.nan) for reg in labels_p]
        colors_bar = [FUND_COLORS[l] for l in labels_p]
        x_pos = range(len(labels_p))
        bars = ax.bar(x_pos, means, color=colors_bar, alpha=0.8, width=0.6)
        ax.errorbar(x_pos, means, yerr=[1.96 * s for s in se_arr],
                    fmt="none", color="black", capsize=4, linewidth=1.2)
        ax.axhline(0, color="black", linewidth=0.8)
        ax.set_xticks(list(x_pos))
        ax.set_xticklabels([l.replace("_funding", "") for l in labels_p], fontsize=8)
        ax.set_ylabel("Mean Return (%)")
        ax.set_title(f"{h}-Day Forward Return by Funding Regime")
        # Annotate diff
        diff = row.get("effect", {}).get("diff_pct", np.nan)
        if not np.isnan(diff):
            ax.set_xlabel(f"H-L diff = {diff:+.2f}%  "
                          f"{'[ECON_SIG]' if row.get('economic_significant') else ''}")
    fig.suptitle("H1 Effect Size: Mean Forward Return per Funding Regime", fontsize=11)
    fig.tight_layout()
    fig.savefig(CHART_DIR / "h1_02_returns_by_regime.png")
    plt.close(fig)
    print("  [OK] h1_02_returns_by_regime.png")

    # ---- Chart 3: OOS walk-forward (1d) -----------------------------------
    oos_1d = oos_res.get("h1d", {})
    if oos_1d.get("y_hat") is not None and len(oos_1d["y_hat"]) > 0:
        y_hat = oos_1d["y_hat"]
        y_act = oos_1d["y_act"]
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 6))
        ax1.scatter(y_hat, y_act, alpha=0.2, s=4, color="#2980b9")
        ax1.axhline(0, color="black", linewidth=0.8)
        ax1.axvline(0, color="black", linewidth=0.8)
        ax1.set_xlabel("Predicted 1d Return"); ax1.set_ylabel("Actual 1d Return")
        ax1.set_title(f"H1 OOS Walk-Forward (1d): R2={oos_1d['overall_r2']:+.4f}  "
                      f"Hit Rate={oos_1d['hit_rate']:.3f}")
        # Rolling hit rate
        window = 60
        hits_roll = pd.Series((y_hat > 0) == (y_act > 0)).astype(float)
        rolling_hr = hits_roll.rolling(window, min_periods=window).mean()
        ax2.plot(rolling_hr.values, color="#e74c3c", linewidth=1.0)
        ax2.axhline(0.5, color="black", linewidth=0.8, linestyle="--")
        ax2.set_ylabel(f"{window}-day Rolling Hit Rate"); ax2.set_xlabel("OOS Day Index")
        ax2.set_title("Rolling Directional Hit Rate (60-day window)")
        fig.tight_layout()
        fig.savefig(CHART_DIR / "h1_03_oos_1d.png")
        plt.close(fig)
        print("  [OK] h1_03_oos_1d.png")

    # ---- Chart 4: Funding regime transition matrix -----------------------
    prob_matrix = persistence_res.get("prob_matrix", {})
    if prob_matrix:
        fig, ax = plt.subplots(figsize=(7, 5))
        mat = np.array([[prob_matrix.get(r, {}).get(c, 0) for c in FUND_LABELS]
                        for r in FUND_LABELS])
        im = ax.imshow(mat, vmin=0, vmax=1, cmap="RdYlGn")
        short_labels = [l.replace("_funding", "") for l in FUND_LABELS]
        for i in range(3):
            for j in range(3):
                ax.text(j, i, f"{mat[i,j]*100:.1f}%",
                        ha="center", va="center", fontsize=11,
                        color="white" if mat[i, j] > 0.6 else "black")
        ax.set_xticks(range(3)); ax.set_xticklabels(short_labels, fontsize=9)
        ax.set_yticks(range(3)); ax.set_yticklabels(short_labels, fontsize=9)
        ax.set_xlabel("To"); ax.set_ylabel("From")
        ax.set_title(f"Funding Regime Transition Probabilities\n"
                     f"Cramer's V={persistence_res.get('cramers_v',0):.3f}  "
                     f"p={persistence_res.get('p_chi2',1):.2e}")
        plt.colorbar(im, ax=ax, shrink=0.8)
        fig.tight_layout()
        fig.savefig(CHART_DIR / "h1_04_transition_matrix.png")
        plt.close(fig)
        print("  [OK] h1_04_transition_matrix.png")

    # ---- Chart 5: Effect size heatmap (funding x vol, 7d return) ---------
    fig, axes = plt.subplots(1, 2, figsize=(13, 4))
    for ax_idx, (strat_labels, strat_name, strat_col) in enumerate([
        (VOL_LABELS,   "Vol Regime",   "vol_regime"),
        (BASIS_LABELS, "Basis Regime", "basis_regime"),
    ]):
        ax = axes[ax_idx]
        # Build effect-size matrix: rows = funding regime, cols = stratifier
        mat_data = []
        for h in HORIZONS:
            col = f"ret_{h}d"
            row_data = []
            for strat in strat_labels:
                sub = m[(m[strat_col] == strat) &
                        m["funding_regime"].isin(FUND_LABELS) &
                        m[col].notna()]
                high_r = sub.loc[sub["funding_regime"] == "high_funding", col].to_numpy()
                low_r  = sub.loc[sub["funding_regime"] == "low_funding",  col].to_numpy()
                if len(high_r) >= 10 and len(low_r) >= 10:
                    row_data.append(float((high_r.mean() - low_r.mean()) * 100))
                else:
                    row_data.append(np.nan)
            mat_data.append(row_data)
        mat = np.array(mat_data)
        vmax = max(abs(np.nanmin(mat)), abs(np.nanmax(mat)), 1.0)
        im = ax.imshow(mat, vmin=-vmax, vmax=vmax, cmap="RdYlGn_r", aspect="auto")
        short_s = [l.replace("_funding", "").replace("_vol", "") for l in strat_labels]
        for i in range(mat.shape[0]):
            for j in range(mat.shape[1]):
                v = mat[i, j]
                ax.text(j, i, f"{v:+.1f}%" if not np.isnan(v) else "n/a",
                        ha="center", va="center", fontsize=9,
                        color="white" if abs(v) > vmax * 0.6 else "black")
        ax.set_xticks(range(len(strat_labels))); ax.set_xticklabels(short_s, fontsize=8)
        ax.set_yticks(range(len(HORIZONS)));
        ax.set_yticklabels([f"{h}d" for h in HORIZONS], fontsize=8)
        ax.set_xlabel(strat_name); ax.set_ylabel("Horizon")
        ax.set_title(f"H-L Effect Size (%) by Horizon x {strat_name}")
        plt.colorbar(im, ax=ax, shrink=0.8, label="H-L diff (%)")
    fig.suptitle("H1 Stratified Effect Sizes: H-L Return Diff by Horizon and Regime",
                 fontsize=10)
    fig.tight_layout()
    fig.savefig(CHART_DIR / "h1_05_stratification_heatmap.png")
    plt.close(fig)
    print("  [OK] h1_05_stratification_heatmap.png")


# =========================================================================
# Verdict and report
# =========================================================================

def build_verdict(
    categorical: dict,
    oos: dict,
    persistence: dict,
) -> dict:
    # H1a: economic significance at any horizon
    h1a_pass = categorical.get("n_econ_sig", 0) >= 1
    # H1b: correct sign (high funding -> lower return) at majority of horizons
    h1b_sign = categorical.get("n_correct_sign", 0) >= 3
    # H1c: OOS performance
    h1c_oos_pass = oos.get("h1d", {}).get("pass_r2", False) or \
                   oos.get("h1d", {}).get("pass_hit", False)
    # H1d: persistence
    vp = persistence.get("verdicts", {})
    h1d_pers = (vp.get("chi2", "[FAIL]") == "[PASS]" and
                vp.get("diag_dom", "[FAIL]") == "[PASS]")

    criteria = {
        "H1a Economic significance (>=1 horizon)": "[PASS]" if h1a_pass else "[FAIL]",
        "H1b Correct sign (high->low, >=3 horizon)": "[PASS]" if h1b_sign else "[FAIL]",
        "H1c OOS R2 or hit rate":                  "[PASS]" if h1c_oos_pass else "[FAIL]",
        "H1d Persistence (chi2+diag_dom)":          "[PASS]" if h1d_pers else "[FAIL]",
    }
    # Overall: economic significance + persistence
    overall = h1a_pass and h1d_pers
    criteria["overall"] = "PASS" if overall else "FAIL"
    return criteria


def stage11_report(
    m: pd.DataFrame,
    spearman: dict,
    categorical: dict,
    oos: dict,
    basis_strat: dict,
    vol_strat: dict,
    mkt_strat: dict,
    persistence: dict,
    structural: dict,
    elapsed_s: float,
) -> None:
    verdict = build_verdict(categorical, oos, persistence)

    print("\n" + "=" * 70)
    print("H1 VERDICT")
    print("-" * 70)
    for k, v in verdict.items():
        if k != "overall":
            print(f"  {k:50s}: {v}")
    print("-" * 70)
    print(f"  {'Overall H1':50s}: [{verdict['overall']}]")
    print("=" * 70)

    # Serialize (strip non-serializable keys like y_hat / y_act arrays)
    def _clean(d):
        if isinstance(d, dict):
            return {k: _clean(v) for k, v in d.items()
                    if k not in ("y_hat", "y_act")}
        if isinstance(d, np.ndarray):
            return d.tolist()
        if isinstance(d, (np.integer, np.floating)):
            return float(d)
        return d

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    summary = _clean(dict(
        hypothesis="H1 Funding Regimes",
        symbol=SYMBOL,
        n_days=len(m),
        date_start=str(m["dt"].min().date()),
        date_end=str(m["dt"].max().date()),
        horizons=HORIZONS,
        spearman=spearman,
        categorical_effects=categorical,
        oos=oos,
        basis_stratification=basis_strat,
        vol_stratification=vol_strat,
        market_stratification=mkt_strat,
        persistence=persistence,
        structural_conditioning=structural,
        verdict=verdict,
        run_date=datetime.now(timezone.utc).isoformat(),
        elapsed_s=round(elapsed_s, 1),
    ))
    out_path = REPORT_DIR / "h1_summary.json"
    with open(out_path, "w") as fh:
        json.dump(summary, fh, indent=2, default=str)

    print(f"\n[{verdict['overall']}] H1 COMPLETE  elapsed: {elapsed_s/60:.1f} min")
    print(f"Charts -> {CHART_DIR}")
    print(f"Report -> {out_path}")
    print("=" * 70)


# =========================================================================
# Entry point
# =========================================================================

def main() -> None:
    t0 = time.time()
    print("=" * 70)
    print("H1 FUNDING REGIMES -- Phase B.1C Evidence Battery")
    print(f"{SYMBOL}  |  research corpus 2021-01 to 2026-05")
    print(f"Run date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 70)

    m = _load_all_data()

    sep = "\n" + "-" * 70
    stage1_summary(m)
    print(sep)

    spearman = stage2_spearman(m)
    print(sep)

    categorical = stage3_categorical_effects(m)
    print(sep)

    oos = stage4_oos(m)
    print(sep)

    basis_strat = stage5_basis_stratification(m)
    print(sep)

    vol_strat = stage6_vol_stratification(m)
    print(sep)

    mkt_strat = stage7_market_regime(m)
    print(sep)

    persistence = stage8_persistence(m)
    print(sep)

    structural = stage9_structural(m)
    print(sep)

    stage10_charts(m, spearman, categorical, oos, persistence, vol_strat)
    print(sep)

    elapsed = time.time() - t0
    stage11_report(m, spearman, categorical, oos, basis_strat, vol_strat,
                   mkt_strat, persistence, structural, elapsed)


if __name__ == "__main__":
    main()
