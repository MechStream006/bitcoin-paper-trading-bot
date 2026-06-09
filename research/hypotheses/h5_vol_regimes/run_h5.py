"""
H5 Volatility Regimes -- Phase B.1B Evidence Battery
=====================================================

H5a (primary — H4 explanation):
    Null:        Realized-vol regime does not stratify basis→funding OOS
                 predictability; OOS R² is uniform across vol regimes.
    Alternative: Low-vol regime exhibits positive OOS R², explaining H4's
                 overall OOS R² = −0.122 collapse.

H5b (filter viability):
    Null:        Vol regimes are not Markov-persistent; transition matrix is
                 uniform (no diagonal dominance).
    Alternative: Vol regimes exhibit persistence (half-life ≥ 3 days,
                 diagonal dominance ≥ 0.20), making them a viable pre-filter
                 for funding-rate strategies.

Volatility measure
------------------
21-day rolling realized vol from daily close-to-close log returns, annualized:
    RV_21(d) = std( log(close[d] / close[d-1]) for d' in [d-20..d] ) × √252

Anti-lookahead:
    RV_21(d)    : uses close[d-20..d].           Available at d.  [OK]
    regime(d)   : rolling 252-day tercile of RV_21[d-251..d].  [OK]
    OOS step t  : model trained on [0..t-1].     Available at t. [OK]
    Market reg. : SMA200 + 20-day slope.          Available at d. [OK]
    Excl. mask  : uses only fixed historical event dates.       [OK]

Run:
    python research/hypotheses/h5_vol_regimes/run_h5.py
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
from scipy.stats import spearmanr, chi2_contingency, t as t_dist

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=RuntimeWarning)

# ---- Path setup --------------------------------------------------------
_H5_DIR       = pathlib.Path(__file__).resolve().parent
_RESEARCH_DIR = _H5_DIR.parent.parent
if str(_RESEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_DIR))

from storage.duck import open_db, query, register_view          # noqa: E402
from hypotheses.shared.market_regimes import build_market_regimes   # noqa: E402
from hypotheses.shared.structural_events import (               # noqa: E402
    events_df, build_exclusion_mask_ms, build_exclusion_mask_dates,
)

# ---- Directories --------------------------------------------------------
BASE_DIR   = _RESEARCH_DIR / "data" / "raw"
REPORT_DIR = _RESEARCH_DIR / "reports" / "h5_vol_regimes"
CHART_DIR  = REPORT_DIR / "charts"
SYMBOL     = "BTCUSDT"

# ---- Analysis parameters ------------------------------------------------
TRAIN_WINDOW    = 1_000    # same as H4 — min OOS training observations
N_BOOTSTRAP     = 2_000
RV_WINDOW       = 21       # trading days for rolling realized vol
REGIME_WINDOW   = 252      # trading days for rolling regime tercile
REGIME_PCTILE   = 33       # tercile split (low = bottom 33%, high = top 33%)
NW_MAXLAGS      = 3
MIN_HRS_8H      = 4
MIN_MINS_1H     = 30

# ---- Pass/fail thresholds -----------------------------------------------
THRESH_DELTA_RHO    = 0.05   # |ρ_low_vol − ρ_high_vol| to flag rho stratification
THRESH_OOS_LOW_VOL  = 0.00   # OOS R² in low_vol must exceed this (> 0 = positive)
THRESH_RHO_P        = 0.001
THRESH_OLS_P        = 0.05
THRESH_CHI2_P       = 0.01
THRESH_HALFLIFE     = 3.0    # days
THRESH_DIAG_DOM     = 0.20

VOL_LABELS  = ["low_vol", "medium_vol", "high_vol"]
VOL_COLORS  = {"low_vol": "#27ae60", "medium_vol": "#95a5a6", "high_vol": "#e74c3c", "unknown": "#ffffff"}


# =========================================================================
# Statistical helpers  (match H4 signatures exactly)
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
    """OLS with Newey-West HAC standard errors (Bartlett kernel)."""
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
        beta_intercept=float(beta[0]),
        beta_x=float(beta[1]),
        se_x=float(se[1]),
        t_x=float(tstat[1]),
        p_x=float(pval[1]),
        r2=float(r2),
        n=n,
    )


def _oos_r2_tagged(
    basis: np.ndarray,
    funding: np.ndarray,
    vol_regime_tags: np.ndarray,
    train_window: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Expanding-window OLS walk-forward. Returns (actuals, predictions, tags)
    for the OOS period [train_window..n-1].

    Vectorised via cumulative sums (O(n)). Regime tags allow per-regime
    OOS R² decomposition without re-running the walk-forward.
    """
    n = len(basis)
    cum_x  = np.concatenate([[0.0], np.cumsum(basis)])
    cum_y  = np.concatenate([[0.0], np.cumsum(funding)])
    cum_xy = np.concatenate([[0.0], np.cumsum(basis * funding)])
    cum_xx = np.concatenate([[0.0], np.cumsum(basis ** 2)])

    t_arr = np.arange(train_window, n)
    n_t   = t_arr.astype(float)

    # Cumulative sums at position t give sum over [0..t-1] (train set)
    sx  = cum_x[t_arr]
    sy  = cum_y[t_arr]
    sxy = cum_xy[t_arr]
    sxx = cum_xx[t_arr]

    denom = n_t * sxx - sx ** 2
    ok    = np.abs(denom) > 1e-15
    b1    = np.where(ok, (n_t * sxy - sx * sy) / np.where(ok, denom, 1.0), 0.0)
    b0    = np.where(ok, (sy - b1 * sx) / n_t, 0.0)

    y_hat = np.where(ok, b0 + b1 * basis[t_arr], np.nan)
    y_act = funding[t_arr]
    tags  = vol_regime_tags[t_arr]

    valid = ~np.isnan(y_hat)
    return y_act[valid], y_hat[valid], tags[valid]


def _oos_r2_scalar(actual: np.ndarray, predicted: np.ndarray) -> float:
    if len(actual) == 0:
        return float("nan")
    ss_res = float(np.sum((actual - predicted) ** 2))
    ss_tot = float(np.sum((actual - actual.mean()) ** 2))
    return 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")


def _oos_r2_by_group(
    actuals: np.ndarray,
    predictions: np.ndarray,
    tags: np.ndarray,
) -> dict[str, float | int]:
    """Compute OOS R² decomposed by vol regime tag."""
    results: dict[str, float | int] = {}
    results["overall"] = _oos_r2_scalar(actuals, predictions)
    results["n_overall"] = int(len(actuals))
    for lbl in VOL_LABELS:
        mask = tags == lbl
        results[f"r2_{lbl}"] = _oos_r2_scalar(actuals[mask], predictions[mask])
        results[f"n_{lbl}"] = int(mask.sum())
    return results


def _vol_regime_persistence(regime_series: pd.Series) -> dict:
    """Compute vol regime transition matrix, chi-squared, Cramér's V, half-lives."""
    labels = VOL_LABELS
    s = regime_series[regime_series != "unknown"]

    trans = pd.crosstab(s.iloc[:-1].values, s.iloc[1:].values)
    for lbl in labels:
        if lbl not in trans.index:
            trans.loc[lbl] = 0
        if lbl not in trans.columns:
            trans[lbl] = 0
    trans = trans.reindex(index=labels, columns=labels, fill_value=0)

    chi2, p_chi2, dof, _ = chi2_contingency(trans.values)
    n_trans = int(trans.values.sum())
    cramers_v = float(np.sqrt(chi2 / (n_trans * (len(labels) - 1)))) if n_trans > 0 else 0.0

    row_sums = trans.sum(axis=1).replace(0, np.nan)
    prob = trans.div(row_sums, axis=0).fillna(0.0)
    diag_dom = float(np.diag(prob.values).mean() - 1.0 / len(labels))

    half_lives: dict[str, float] = {}
    for lbl in labels:
        runs: list[int] = []
        cur = 0
        for val in (s == lbl).astype(int):
            if val:
                cur += 1
            elif cur:
                runs.append(cur)
                cur = 0
        if cur:
            runs.append(cur)
        half_lives[lbl] = float(np.mean(runs)) if runs else 0.0

    return dict(
        transition_counts={r: {c: int(trans.loc[r, c]) for c in labels} for r in labels},
        prob_matrix={r: {c: float(prob.loc[r, c]) for c in labels} for r in labels},
        chi2=float(chi2),
        p_chi2=float(p_chi2),
        dof=int(dof),
        cramers_v=cramers_v,
        diag_dominance=diag_dom,
        half_lives=half_lives,
        n_transitions=n_trans,
    )


# =========================================================================
# Volatility regime builder
# =========================================================================

def _build_vol_regimes(df_spot_daily: pd.DataFrame) -> pd.DataFrame:
    """
    Compute 21-day rolling realized vol (annualized) and 252-day tercile
    vol regime from daily close-to-close log returns.

    Returns DataFrame indexed by calendar date with columns:
        rv_21d      : annualized realized vol
        vol_regime  : 'low_vol' | 'medium_vol' | 'high_vol' | 'unknown'
        daily_close : spot close (passed through)
    Anti-lookahead: regime(d) uses RV values from [d-251..d] only.
    """
    df = df_spot_daily.sort_values("dt").copy()
    df["log_ret"] = np.log(df["daily_close"] / df["daily_close"].shift(1))

    rv = df["log_ret"].rolling(RV_WINDOW, min_periods=RV_WINDOW).std() * np.sqrt(252)
    df["rv_21d"] = rv

    lo = rv.rolling(REGIME_WINDOW, min_periods=REGIME_WINDOW).quantile(REGIME_PCTILE / 100)
    hi = rv.rolling(REGIME_WINDOW, min_periods=REGIME_WINDOW).quantile(1.0 - REGIME_PCTILE / 100)

    regime = pd.Series("medium_vol", index=df.index, dtype=object)
    regime[rv > hi]                      = "high_vol"
    regime[rv < lo]                      = "low_vol"
    regime[rv.isna() | lo.isna() | hi.isna()] = "unknown"

    df["vol_regime"] = regime

    # Plain date for merge key (timezone-naive)
    df["date"] = df["dt"].dt.normalize().dt.tz_localize(None).dt.date

    return df[["date", "rv_21d", "vol_regime", "daily_close"]].reset_index(drop=True)


# =========================================================================
# Data loading via DuckDB
# =========================================================================

def _load_all_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Returns
    -------
    df_aligned   : funding settlements with 8h mean basis + vol_regime column
    df_spot_daily: daily closing spot prices
    vol_daily    : daily rv_21d + vol_regime
    """
    print(f"{_ts()} Loading data via DuckDB...")
    con = open_db()

    n_spot = register_view(con, "spot_1m",     BASE_DIR, "spot",    SYMBOL)
    n_perp = register_view(con, "perp_1m",     BASE_DIR, "perp",    SYMBOL)
    n_fund = register_view(con, "funding_raw", BASE_DIR, "funding", SYMBOL)
    print(f"  spot_1m     : {n_spot} partitions")
    print(f"  perp_1m     : {n_perp} partitions")
    print(f"  funding_raw : {n_fund} partitions")

    # Hourly basis — use // for integer division (DuckDB BIGINT / INT = DOUBLE otherwise)
    con.execute(f"""
    CREATE OR REPLACE VIEW basis_1h AS
    SELECT
        (s.open_time // 3600000) * 3600000  AS hour_ts,
        AVG(p.close - s.close)              AS basis_abs,
        AVG((p.close / s.close - 1.0) * 10000.0) AS basis_bps,
        AVG(s.close)                        AS spot_price,
        COUNT(*)                            AS n_min
    FROM spot_1m s
    JOIN perp_1m p ON s.open_time = p.open_time
    GROUP BY s.open_time // 3600000
    HAVING COUNT(*) >= {MIN_MINS_1H}
    """)

    # Align basis with 8h funding settlements
    df_aligned = query(con, f"""
    SELECT
        f.settlement_ts,
        f.funding_rate,
        AVG(b.basis_abs)    AS mean_basis_abs_8h,
        AVG(b.basis_bps)    AS mean_basis_bps_8h,
        STDDEV(b.basis_bps) AS std_basis_bps_8h,
        COUNT(*)             AS n_hours_in_window,
        AVG(b.spot_price)   AS mean_spot_8h
    FROM (
        SELECT
            CAST(timestamp_ms  AS BIGINT) AS settlement_ts,
            CAST(funding_rate  AS DOUBLE) AS funding_rate
        FROM funding_raw
    ) f
    JOIN basis_1h b
        ON b.hour_ts >= f.settlement_ts - 28800000
       AND b.hour_ts <  f.settlement_ts
    GROUP BY f.settlement_ts, f.funding_rate
    HAVING COUNT(*) >= {MIN_HRS_8H}
    ORDER BY f.settlement_ts
    """)

    # Daily spot close (last 1m candle per UTC day)
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

    # Datetime columns
    df_aligned["dt"]      = pd.to_datetime(df_aligned["settlement_ts"], unit="ms", utc=True)
    df_spot_daily["dt"]   = pd.to_datetime(df_spot_daily["day_ts"],     unit="ms", utc=True)

    # Build vol regimes from daily spot close
    vol_daily = _build_vol_regimes(df_spot_daily)

    # Merge vol regime into df_aligned on calendar date
    df_aligned["date"] = df_aligned["dt"].dt.normalize().dt.tz_localize(None).dt.date
    df_aligned = df_aligned.merge(
        vol_daily[["date", "rv_21d", "vol_regime"]], on="date", how="left"
    )
    df_aligned["vol_regime"] = df_aligned["vol_regime"].fillna("unknown")

    return df_aligned, df_spot_daily, vol_daily


# =========================================================================
# Stage functions
# =========================================================================

def stage1_summary(df_aligned: pd.DataFrame, vol_daily: pd.DataFrame) -> None:
    print(f"\n{_ts()} STAGE 1  Sample summary")
    print(f"  Aligned funding-basis pairs : {len(df_aligned):,}")
    print(f"  Date range (aligned)        : "
          f"{df_aligned['dt'].min().date()} to {df_aligned['dt'].max().date()}")
    n_known = (df_aligned["vol_regime"] != "unknown").sum()
    print(f"  Pairs with vol regime known : {n_known:,} ({n_known/len(df_aligned)*100:.1f}%)")
    print(f"  Mean basis (bps)            : {df_aligned['mean_basis_bps_8h'].mean():+.2f}")
    print(f"  Funding rate mean  (bps)    : {df_aligned['funding_rate'].mean()*10000:+.2f}")
    print(f"  Funding rate std   (bps)    : {df_aligned['funding_rate'].std()*10000:.2f}")
    mean_rv = vol_daily["rv_21d"].dropna().mean()
    print(f"  Mean RV_21d (annualized)    : {mean_rv:.3f}  ({mean_rv*100:.1f}%)")
    regime_freq = vol_daily["vol_regime"].value_counts()
    print(f"  Vol regime distribution (days):")
    for reg in VOL_LABELS + ["unknown"]:
        cnt = int(regime_freq.get(reg, 0))
        print(f"    {reg:12s} : {cnt:,}")


def stage2_insample_by_vol_regime(df: pd.DataFrame) -> dict:
    """Spearman ρ and NW-OLS within each vol regime (in-sample stratification)."""
    print(f"\n{_ts()} STAGE 2  In-sample stratification by vol regime")

    results: dict[str, dict] = {}

    for reg in VOL_LABELS:
        sub = df[df["vol_regime"] == reg].copy()
        if len(sub) < 100:
            print(f"  {reg:12s} : too few obs ({len(sub)}) -- skip")
            results[reg] = {"n": len(sub), "skipped": True}
            continue
        b = sub["mean_basis_bps_8h"].to_numpy()
        f = sub["funding_rate"].to_numpy() * 10_000
        n_boot = 500 if len(sub) < 2000 else N_BOOTSTRAP
        sp = _spearman_ci(b, f, n_boot=n_boot)
        nw = _newey_west_ols(f, b)
        print(f"  {reg:12s} : n={len(sub):,}  "
              f"rho={sp['rho']:+.4f}  CI[{sp['ci_lo']:+.4f},{sp['ci_hi']:+.4f}]  "
              f"p={sp['pval']:.2e}  beta={nw['beta_x']:+.5f}")
        results[reg] = {"spearman": sp, "nw_ols": nw, "n": len(sub)}

    # Rho stratification check
    lo_rho = results.get("low_vol",  {}).get("spearman", {}).get("rho", float("nan"))
    hi_rho = results.get("high_vol", {}).get("spearman", {}).get("rho", float("nan"))
    delta_rho = abs(lo_rho - hi_rho) if not (np.isnan(lo_rho) or np.isnan(hi_rho)) else float("nan")
    verdict = "[PASS]" if delta_rho >= THRESH_DELTA_RHO else "[FAIL]"
    print(f"\n  Rho stratification: |rho_low - rho_high| = {delta_rho:.4f}  "
          f"(threshold {THRESH_DELTA_RHO})  {verdict}")
    results["delta_rho"] = float(delta_rho)
    results["rho_stratification_pass"] = (delta_rho >= THRESH_DELTA_RHO)
    return results


def stage3_oos_by_vol_regime(df: pd.DataFrame) -> dict:
    """
    Walk-forward OOS R² from H4's expanding-window linear model, decomposed by
    vol regime. Primary test: does vol regime explain H4's OOS R² = −0.122?

    H5a passes if the BEST-performing vol regime has positive OOS R².
    (The direction — low_vol vs high_vol — is empirically determined.)
    """
    print(f"\n{_ts()} STAGE 3  OOS R2 by vol regime (H4 explanation test)")

    basis   = df["mean_basis_bps_8h"].to_numpy()
    funding = df["funding_rate"].to_numpy() * 10_000
    tags    = df["vol_regime"].to_numpy()

    if len(basis) < TRAIN_WINDOW + 30:
        print(f"  [WARN] Insufficient observations -- skipping OOS")
        return {}

    print(f"  Running walk-forward OOS (train_window={TRAIN_WINDOW}, "
          f"n_oos={len(basis)-TRAIN_WINDOW:,})...")

    actuals, predictions, oos_tags = _oos_r2_tagged(basis, funding, tags, TRAIN_WINDOW)
    r2_by_regime = _oos_r2_by_group(actuals, predictions, oos_tags)

    overall_r2 = r2_by_regime["overall"]
    print(f"  Overall OOS R2  = {overall_r2:+.4f}  n={r2_by_regime['n_overall']:,}")
    print(f"  OOS R2 by vol regime:")
    regime_r2s: dict[str, float] = {}
    for lbl in VOL_LABELS:
        r2 = r2_by_regime[f"r2_{lbl}"]
        n  = r2_by_regime[f"n_{lbl}"]
        regime_r2s[lbl] = float(r2) if not np.isnan(r2) else -999.0
        print(f"    {lbl:12s} : R2={r2:+.4f}  n={n:,}")

    # H5a: does any regime have positive OOS R²? (direction is empirical)
    best_lbl = max(regime_r2s, key=lambda k: regime_r2s[k])
    best_r2  = r2_by_regime.get(f"r2_{best_lbl}", float("nan"))
    worst_lbl = min(regime_r2s, key=lambda k: regime_r2s[k])
    worst_r2  = r2_by_regime.get(f"r2_{worst_lbl}", float("nan"))

    h5a_pass = (not np.isnan(best_r2)) and (best_r2 > THRESH_OOS_LOW_VOL)
    verdict  = "[PASS]" if h5a_pass else "[FAIL]"
    print(f"\n  H5a verdict: best regime OOS R2 ({best_lbl}) = {best_r2:+.4f}  "
          f"(threshold > {THRESH_OOS_LOW_VOL})  {verdict}")

    # Spread — quantifies how much the regime explains the overall OOS failure
    if not np.isnan(best_r2) and not np.isnan(worst_r2):
        spread = best_r2 - worst_r2
        print(f"  OOS R2 spread (best - worst) = {spread:+.4f}")
        print(f"  -> Predictable regime: {best_lbl}  |  "
              f"Unpredictable regime: {worst_lbl}")

    r2_by_regime["h5a_pass"]   = h5a_pass
    r2_by_regime["best_regime"] = best_lbl
    r2_by_regime["worst_regime"] = worst_lbl
    return r2_by_regime


def stage4_vol_regime_persistence(vol_daily: pd.DataFrame) -> dict:
    """H5b: Is vol regime Markov-persistent? Transition matrix + chi-squared."""
    print(f"\n{_ts()} STAGE 4  H5b -- Vol regime persistence")

    reg_series = vol_daily.set_index("date")["vol_regime"]
    known = reg_series[reg_series != "unknown"]
    freq  = known.value_counts()
    total = len(known)

    print(f"  Regime frequencies (n={total:,}, excl. warmup):")
    for reg in VOL_LABELS:
        cnt = int(freq.get(reg, 0))
        print(f"    {reg:12s} : {cnt:,} ({cnt/total*100:.1f}%)")

    pers = _vol_regime_persistence(reg_series)

    verdict_chi2 = "[PASS]" if pers["p_chi2"] < THRESH_CHI2_P else "[FAIL]"
    verdict_dd   = "[PASS]" if pers["diag_dominance"] >= THRESH_DIAG_DOM else "[FAIL]"
    verdict_hl   = "[PASS]" if min(pers["half_lives"].values()) >= THRESH_HALFLIFE else "[FAIL]"

    print(f"\n  Transition matrix (row = from, col = to):")
    print(f"  {'':16s}" + "".join(f"{l:>14s}" for l in VOL_LABELS))
    for r in VOL_LABELS:
        row = f"  {r:16s}" + "".join(
            f"{pers['prob_matrix'][r][c]*100:13.1f}%" for c in VOL_LABELS
        )
        print(row)

    print(f"\n  Chi-squared = {pers['chi2']:.2f}  "
          f"dof = {pers['dof']}  "
          f"p = {pers['p_chi2']:.2e}  "
          f"Cramer's V = {pers['cramers_v']:.4f}  {verdict_chi2}")
    print(f"  Diagonal dominance = {pers['diag_dominance']:+.4f}  {verdict_dd}")
    print(f"  Half-lives (mean consecutive days per regime):")
    for reg in VOL_LABELS:
        hl = pers["half_lives"][reg]
        print(f"    {reg:12s} : {hl:.1f} days")
    print(f"  Min half-life = {min(pers['half_lives'].values()):.1f} days  {verdict_hl}")

    pers["verdicts"] = dict(chi2=verdict_chi2, diag_dom=verdict_dd, halflife=verdict_hl)
    return pers


def stage5_market_regime_stratification(
    df: pd.DataFrame,
    df_spot_daily: pd.DataFrame,
) -> dict:
    """Bull/Bear/Sideways segmentation within each vol regime."""
    print(f"\n{_ts()} STAGE 5  Market regime x vol regime stratification")

    close = df_spot_daily.set_index("dt")["daily_close"]
    mkt_regimes = build_market_regimes(close)

    df = df.copy()
    df["date_ts"] = df["dt"].dt.normalize()
    mkt_map = {k.normalize(): v for k, v in mkt_regimes.to_dict().items()}
    df["market_regime"] = df["date_ts"].map(mkt_map).fillna("unknown")

    mkt_freq = df["market_regime"].value_counts()
    print(f"  Market regime distribution:")
    for reg in ["bull", "bear", "sideways", "unknown"]:
        cnt = int(mkt_freq.get(reg, 0))
        print(f"    {reg:10s} : {cnt:,} ({cnt/len(df)*100:.1f}%)")

    results: dict = {}
    for mkt in ["bull", "bear", "sideways"]:
        sub_mkt = df[df["market_regime"] == mkt]
        if len(sub_mkt) < 100:
            continue
        row: dict = {"n": len(sub_mkt)}
        for vol in VOL_LABELS:
            sub = sub_mkt[sub_mkt["vol_regime"] == vol]
            if len(sub) < 50:
                row[vol] = {"n": len(sub), "skipped": True}
                continue
            b = sub["mean_basis_bps_8h"].to_numpy()
            f = sub["funding_rate"].to_numpy() * 10_000
            sp = _spearman_ci(b, f, n_boot=500)
            row[vol] = {"rho": float(sp["rho"]), "n": len(sub), "p": float(sp["pval"])}
        results[mkt] = row
        print(f"  {mkt:10s}:", end="")
        for vol in VOL_LABELS:
            v = row.get(vol, {})
            if v.get("skipped"):
                print(f"  {vol}=n/a", end="")
            else:
                print(f"  {vol}:rho={v.get('rho', float('nan')):+.3f}(n={v.get('n',0)})", end="")
        print()

    return results


def stage6_structural_conditioning(df: pd.DataFrame) -> dict:
    """Clean-sample sensitivity: exclude ±7d windows around structural events."""
    print(f"\n{_ts()} STAGE 6  Structural event conditioning (clean sample)")

    ts_arr = df["settlement_ts"].to_numpy()
    excl   = build_exclusion_mask_ms(ts_arr)
    df_clean = df[~excl].copy()
    n_excl = int(excl.sum())

    print(f"  Excluded {n_excl} settlements within +/-7d of structural events")
    print(f"  Clean sample n = {len(df_clean):,} ({len(df_clean)/len(df)*100:.1f}%)")

    results: dict[str, dict] = {}
    for reg in ["full_clean"] + VOL_LABELS:
        if reg == "full_clean":
            sub = df_clean
            label = "clean (all)"
        else:
            sub = df_clean[df_clean["vol_regime"] == reg]
            label = f"clean {reg}"
        if len(sub) < 100:
            print(f"  {label:20s} : too few obs ({len(sub)}) -- skip")
            continue
        b = sub["mean_basis_bps_8h"].to_numpy()
        f = sub["funding_rate"].to_numpy() * 10_000
        sp = _spearman_ci(b, f, n_boot=500)
        nw = _newey_west_ols(f, b)
        print(f"  {label:20s} : n={len(sub):,}  "
              f"rho={sp['rho']:+.4f}  beta={nw['beta_x']:+.5f}")
        results[reg] = {"spearman": sp, "nw_ols": nw, "n": len(sub)}

    return results


# =========================================================================
# Charts
# =========================================================================

def _shade_vol_regimes(ax, vol_daily: pd.DataFrame) -> None:
    """Shade background by vol regime."""
    dates   = pd.to_datetime(vol_daily["date"].astype(str), utc=True)
    regimes = vol_daily["vol_regime"].values
    prev_reg = None
    prev_dt  = None
    for dt, reg in zip(dates, regimes):
        if reg != prev_reg:
            if prev_reg is not None and prev_reg in VOL_COLORS and prev_reg != "unknown":
                ax.axvspan(prev_dt, dt, alpha=0.12,
                           color=VOL_COLORS[prev_reg], linewidth=0)
            prev_reg = reg
            prev_dt  = dt
    if prev_reg and prev_reg in VOL_COLORS and prev_reg != "unknown" and prev_dt is not None:
        ax.axvspan(prev_dt, dates.iloc[-1], alpha=0.12,
                   color=VOL_COLORS[prev_reg], linewidth=0)


def _mark_events(ax) -> None:
    ev = events_df()
    ylim = ax.get_ylim()
    for _, row in ev.iterrows():
        ax.axvline(row["date"], color="#999999", linewidth=0.7, linestyle="--", alpha=0.6)
        ax.text(row["date"],
                ylim[0] + (ylim[1] - ylim[0]) * 0.02,
                row["label"], rotation=90, fontsize=6, color="#555555",
                va="bottom", ha="right")


def stage7_charts(
    df_aligned: pd.DataFrame,
    df_spot_daily: pd.DataFrame,
    vol_daily: pd.DataFrame,
    insample_results: dict,
    oos_results: dict,
    persistence_results: dict,
) -> None:
    print(f"\n{_ts()} STAGE 7  Generating charts -> {CHART_DIR}")
    CHART_DIR.mkdir(parents=True, exist_ok=True)

    plt.rcParams.update({
        "figure.dpi": 120, "font.size": 9,
        "axes.grid": True, "grid.alpha": 0.25,
        "axes.spines.top": False, "axes.spines.right": False,
    })

    close_s   = df_spot_daily.set_index("dt")["daily_close"]
    rv_s      = vol_daily.set_index("date")["rv_21d"]
    vol_reg_s = vol_daily.set_index("date")["vol_regime"]
    dates_vol = pd.to_datetime(vol_daily["date"].astype(str), utc=True)

    # ---- Chart 1: BTC price + RV_21d with vol regime shading --------------
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 7), sharex=True)
    ax1.semilogy(close_s.index, close_s.values, color="#2c3e50", linewidth=0.8)
    _shade_vol_regimes(ax1, vol_daily)
    ax1.set_ylabel("BTC Price (log scale)")
    ax1.set_title("H5 Overview: BTC Price & Realized Volatility by Vol Regime")

    ax2.plot(dates_vol, rv_s.values * 100, color="#2980b9", linewidth=0.8, label="RV_21d (%)")
    _shade_vol_regimes(ax2, vol_daily)
    ax2.set_ylabel("Annualized RV_21d (%)")
    ax2.set_xlabel("Date")
    # Regime legend
    patches = [
        plt.Rectangle((0, 0), 1, 1, color=VOL_COLORS[l], alpha=0.5, label=l)
        for l in VOL_LABELS
    ]
    ax2.legend(handles=patches, loc="upper left", fontsize=8)
    _mark_events(ax2)

    fig.tight_layout()
    fig.savefig(CHART_DIR / "h5_01_overview.png")
    plt.close(fig)
    print("  [OK] h5_01_overview.png")

    # ---- Chart 2: Spearman ρ by vol regime (with CI) ----------------------
    fig, ax = plt.subplots(figsize=(9, 4))
    labels_plot = VOL_LABELS
    rhos   = [insample_results.get(l, {}).get("spearman", {}).get("rho",    float("nan")) for l in labels_plot]
    ci_lo  = [insample_results.get(l, {}).get("spearman", {}).get("ci_lo",  float("nan")) for l in labels_plot]
    ci_hi  = [insample_results.get(l, {}).get("spearman", {}).get("ci_hi",  float("nan")) for l in labels_plot]
    colors_bar = [VOL_COLORS[l] for l in labels_plot]
    err_lo = [r - lo for r, lo in zip(rhos, ci_lo)]
    err_hi = [hi - r  for r, hi in zip(rhos, ci_hi)]
    bars = ax.bar(labels_plot, rhos, color=colors_bar, alpha=0.8, width=0.5)
    ax.errorbar(labels_plot, rhos,
                yerr=[err_lo, err_hi],
                fmt="none", color="black", capsize=5, linewidth=1.5)
    ax.set_ylim(0.85, 1.0)
    ax.set_ylabel("Spearman rho (basis -> funding)")
    ax.set_title("H5 In-Sample: Spearman rho by Vol Regime\n"
                 "(basis bps over 8h window -> next funding rate)")
    for bar, rho in zip(bars, rhos):
        if not np.isnan(rho):
            ax.text(bar.get_x() + bar.get_width() / 2, rho + 0.002,
                    f"{rho:.4f}", ha="center", va="bottom", fontsize=9, fontweight="bold")
    fig.tight_layout()
    fig.savefig(CHART_DIR / "h5_02_rho_by_vol_regime.png")
    plt.close(fig)
    print("  [OK] h5_02_rho_by_vol_regime.png")

    # ---- Chart 3: OOS R² by vol regime (H4 explanation) ------------------
    fig, ax = plt.subplots(figsize=(9, 4))
    oos_labels = ["overall"] + VOL_LABELS
    oos_r2_vals = [
        oos_results.get("overall", float("nan")),
        oos_results.get("r2_low_vol",    float("nan")),
        oos_results.get("r2_medium_vol", float("nan")),
        oos_results.get("r2_high_vol",   float("nan")),
    ]
    oos_colors = ["#7f8c8d"] + [VOL_COLORS[l] for l in VOL_LABELS]
    bars2 = ax.bar(oos_labels, oos_r2_vals, color=oos_colors, alpha=0.8, width=0.6)
    ax.axhline(0, color="black", linewidth=1.0, linestyle="-")
    ax.axhline(-0.1215, color="#e74c3c", linewidth=1.2, linestyle="--",
               label="H4 overall OOS R2 = -0.122")
    ax.axhline(THRESH_OOS_LOW_VOL, color="#27ae60", linewidth=1.2, linestyle=":",
               label=f"H5a threshold = {THRESH_OOS_LOW_VOL:.2f}")
    ax.set_ylabel("Out-of-Sample R²")
    ax.set_title("H5 OOS R2 by Vol Regime\n"
                 "(H4 overall OOS R2 = -0.122 is a weighted avg of regimes)")
    ax.legend(fontsize=8)
    for bar, val in zip(bars2, oos_r2_vals):
        if not np.isnan(val):
            ypos = val + 0.01 if val >= 0 else val - 0.04
            ax.text(bar.get_x() + bar.get_width() / 2, ypos,
                    f"{val:+.4f}", ha="center", va="bottom", fontsize=9, fontweight="bold")
    fig.tight_layout()
    fig.savefig(CHART_DIR / "h5_03_oos_r2_by_regime.png")
    plt.close(fig)
    print("  [OK] h5_03_oos_r2_by_regime.png")

    # ---- Chart 4: Vol regime transition matrix heatmap --------------------
    prob_matrix = persistence_results.get("prob_matrix", {})
    if prob_matrix:
        fig, ax = plt.subplots(figsize=(6, 5))
        mat = np.array([[prob_matrix.get(r, {}).get(c, 0) for c in VOL_LABELS]
                        for r in VOL_LABELS])
        im = ax.imshow(mat, vmin=0, vmax=1, cmap="Blues")
        for i in range(3):
            for j in range(3):
                ax.text(j, i, f"{mat[i,j]*100:.1f}%",
                        ha="center", va="center",
                        fontsize=11,
                        color="white" if mat[i, j] > 0.6 else "black")
        ax.set_xticks(range(3)); ax.set_xticklabels(VOL_LABELS, fontsize=8)
        ax.set_yticks(range(3)); ax.set_yticklabels(VOL_LABELS, fontsize=8)
        ax.set_xlabel("To"); ax.set_ylabel("From")
        ax.set_title(f"H5b Vol Regime Transition Probabilities\n"
                     f"Cramer's V={persistence_results.get('cramers_v',0):.3f}  "
                     f"p={persistence_results.get('p_chi2',1):.2e}")
        plt.colorbar(im, ax=ax, shrink=0.8)
        fig.tight_layout()
        fig.savefig(CHART_DIR / "h5_04_transition_matrix.png")
        plt.close(fig)
        print("  [OK] h5_04_transition_matrix.png")

    # ---- Chart 5: RV_21d time series with regime boundary lines ----------
    fig, ax = plt.subplots(figsize=(14, 4))
    rv_pct = rv_s * 100
    # Color each segment by regime
    dates_arr = dates_vol.values
    rv_arr    = rv_pct.values
    reg_arr   = vol_reg_s.values
    for i in range(len(dates_arr) - 1):
        color = VOL_COLORS.get(str(reg_arr[i]), "#aaaaaa")
        ax.plot(dates_arr[i:i+2], rv_arr[i:i+2], color=color, linewidth=0.8)

    # Rolling 33rd/67th percentile boundary lines (use full series — NaNs are fine)
    rv_full = vol_daily["rv_21d"]
    boundary_dates = pd.to_datetime(vol_daily["date"].astype(str), utc=True)
    if rv_full.notna().sum() >= REGIME_WINDOW:
        lo_pct = rv_full.rolling(REGIME_WINDOW, min_periods=REGIME_WINDOW).quantile(REGIME_PCTILE / 100) * 100
        hi_pct = rv_full.rolling(REGIME_WINDOW, min_periods=REGIME_WINDOW).quantile(1 - REGIME_PCTILE / 100) * 100
        ax.plot(boundary_dates, lo_pct.values, color="#27ae60",
                linewidth=1.2, linestyle="--", alpha=0.7, label="33rd pctile (lo/med boundary)")
        ax.plot(boundary_dates, hi_pct.values, color="#e74c3c",
                linewidth=1.2, linestyle="--", alpha=0.7, label="67th pctile (med/hi boundary)")

    patches = [
        plt.Rectangle((0, 0), 1, 1, color=VOL_COLORS[l], alpha=0.8, label=l)
        for l in VOL_LABELS
    ]
    ax.legend(handles=patches, loc="upper right", fontsize=8)
    ax.set_ylabel("Annualized RV_21d (%)")
    ax.set_xlabel("Date")
    ax.set_title("H5 Realized Vol (RV_21d) with Rolling Regime Boundaries")
    _mark_events(ax)
    fig.tight_layout()
    fig.savefig(CHART_DIR / "h5_05_rv_regime_boundaries.png")
    plt.close(fig)
    print("  [OK] h5_05_rv_regime_boundaries.png")


# =========================================================================
# Report and verdict
# =========================================================================

def build_verdict(insample: dict, oos: dict, persistence: dict) -> dict:
    """Compute per-criterion pass/fail and overall verdict."""
    # H5a: OOS R² in low_vol > 0
    h5a_oos_pass = bool(oos.get("h5a_pass", False))
    # H5a supplemental: rho stratification
    h5a_rho_pass = bool(insample.get("rho_stratification_pass", False))
    # H5b: persistence criteria
    verdicts = persistence.get("verdicts", {})
    h5b_chi2_pass = verdicts.get("chi2",    "[FAIL]") == "[PASS]"
    h5b_dd_pass   = verdicts.get("diag_dom","[FAIL]") == "[PASS]"
    h5b_hl_pass   = verdicts.get("halflife", "[FAIL]") == "[PASS]"

    criteria = {
        "H5a OOS best-regime R2 > 0": "[PASS]" if h5a_oos_pass else "[FAIL]",
        "H5a Rho stratification":     "[PASS]" if h5a_rho_pass else "[FAIL]",
        "H5b Chi-squared":        "[PASS]" if h5b_chi2_pass else "[FAIL]",
        "H5b Diagonal dom.":      "[PASS]" if h5b_dd_pass else "[FAIL]",
        "H5b Half-life min":      "[PASS]" if h5b_hl_pass else "[FAIL]",
    }
    # Overall: H5a (OOS) AND H5b (persistence)
    overall_pass = h5a_oos_pass and h5b_chi2_pass and h5b_dd_pass
    criteria["overall"] = "PASS" if overall_pass else "FAIL"
    return criteria


def stage8_report(
    df_aligned: pd.DataFrame,
    vol_daily: pd.DataFrame,
    insample: dict,
    oos: dict,
    persistence: dict,
    structural: dict,
    market_regime: dict,
    elapsed_s: float,
) -> None:
    verdict = build_verdict(insample, oos, persistence)

    print("\n" + "=" * 70)
    print("H5 VERDICT")
    print("-" * 70)
    for k, v in verdict.items():
        if k != "overall":
            print(f"  {k:38s}: {v}")
    print("-" * 70)
    overall_str = verdict["overall"]
    print(f"  Overall H5                             : [{overall_str}]")
    print("=" * 70)

    # Write JSON summary
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    summary = dict(
        hypothesis="H5 Volatility Regimes",
        symbol=SYMBOL,
        n_aligned=int(len(df_aligned)),
        n_aligned_vol_known=int((df_aligned["vol_regime"] != "unknown").sum()),
        date_start=str(df_aligned["dt"].min().date()),
        date_end=str(df_aligned["dt"].max().date()),
        rv_window=RV_WINDOW,
        regime_window=REGIME_WINDOW,
        insample_by_vol=insample,
        oos_by_vol=oos,
        persistence=persistence,
        structural_conditioning=structural,
        market_regime_stratification=market_regime,
        verdict=verdict,
        run_date=datetime.now(timezone.utc).isoformat(),
        elapsed_s=round(elapsed_s, 1),
    )

    out_path = REPORT_DIR / "h5_summary.json"
    with open(out_path, "w") as fh:
        json.dump(summary, fh, indent=2, default=str)

    print(f"\n[{verdict['overall']}] H5 COMPLETE  elapsed: {elapsed_s/60:.1f} min")
    print(f"Charts -> {CHART_DIR}")
    print(f"Report -> {out_path}")
    print("=" * 70)


# =========================================================================
# Entry point
# =========================================================================

def main() -> None:
    t0 = time.time()
    print("=" * 70)
    print("H5 VOLATILITY REGIMES -- Phase B.1B Evidence Battery")
    print(f"{SYMBOL}  |  research corpus 2021-01 to 2026-05")
    print(f"Run date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 70)

    df_aligned, df_spot_daily, vol_daily = _load_all_data()

    stage1_summary(df_aligned, vol_daily)

    sep = "\n" + "-" * 70
    print(sep)

    insample = stage2_insample_by_vol_regime(df_aligned)
    print(sep)

    oos = stage3_oos_by_vol_regime(df_aligned)
    print(sep)

    persistence = stage4_vol_regime_persistence(vol_daily)
    print(sep)

    market_regime = stage5_market_regime_stratification(df_aligned, df_spot_daily)
    print(sep)

    structural = stage6_structural_conditioning(df_aligned)
    print(sep)

    stage7_charts(df_aligned, df_spot_daily, vol_daily, insample, oos, persistence)
    print(sep)

    elapsed = time.time() - t0
    stage8_report(df_aligned, vol_daily, insample, oos, persistence,
                  structural, market_regime, elapsed)


if __name__ == "__main__":
    main()
