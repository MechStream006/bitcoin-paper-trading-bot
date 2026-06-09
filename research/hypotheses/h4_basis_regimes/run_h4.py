"""
H4 Basis Regimes -- Phase B.1A Evidence Battery
================================================

Tests whether the BTCUSDT perpetual-spot basis predicts the next 8-hour
funding settlement (H4a) and whether basis regimes exhibit Markov
persistence (H4b).

Anti-lookahead audit
--------------------
H4a predictor  : mean_basis_bps over [t-8h, t)
                 Computed from 1m OHLCV for minutes strictly before t.
                 Available at settlement time t: YES [OK]
H4a target     : funding_rate at settlement t
                 The rate being paid in this settlement event.
                 Observed at t: YES [OK]
H4b regime(d)  : rolling 252-day tercile of daily_mean_basis_bps
                 Uses [d-251, d] only. Available at d: YES [OK]
OOS prediction : at each step t, model trained on [0, t-1].
                 Prediction for t made before observing t: YES [OK]
Market regime  : SMA200(d) uses close[d-199, d]; slope uses [d-219, d-20].
                 Available at d: YES [OK]

Run from repo root:
    run_h4.bat
Or directly:
    python research/hypotheses/h4_basis_regimes/run_h4.py
"""
from __future__ import annotations

import json
import pathlib
import sys
import time
import warnings
from datetime import datetime, timezone

import matplotlib
matplotlib.use("Agg")  # non-interactive backend — must precede pyplot import
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import pandas as pd
from scipy.stats import spearmanr, chi2_contingency

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=RuntimeWarning)

# ---- Path setup --------------------------------------------------------
_H4_DIR       = pathlib.Path(__file__).resolve().parent
_RESEARCH_DIR = _H4_DIR.parent.parent
if str(_RESEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_DIR))

from storage.duck import open_db, query, register_view   # noqa: E402
from hypotheses.shared.market_regimes import (            # noqa: E402
    build_market_regimes, REGIME_COLORS,
)
from hypotheses.shared.structural_events import (         # noqa: E402
    events_df, build_exclusion_mask_ms, build_exclusion_mask_dates,
)

# ---- Directories --------------------------------------------------------
BASE_DIR   = _RESEARCH_DIR / "data" / "raw"
REPORT_DIR = _RESEARCH_DIR / "reports" / "h4_basis_regimes"
CHART_DIR  = REPORT_DIR / "charts"
SYMBOL     = "BTCUSDT"

# ---- Analysis parameters ------------------------------------------------
TRAIN_WINDOW    = 1_000   # minimum funding events before OOS predictions start
N_BOOTSTRAP     = 2_000   # bootstrap iterations for Spearman CI
ROLLING_WINDOW  = 270     # ~90 days x 3 settlements/day  (quarterly ρ)
REGIME_PCTILE   = 33      # tercile split for H4b regimes
MIN_HRS_8H      = 4       # min hours in 8h window to accept basis estimate
MIN_MINS_1H     = 30      # min 1m candles per hour to accept hourly basis
SMA_LONG        = 200
SMA_SLOPE_LAG   = 20
NW_MAXLAGS      = 3       # lags for Newey-West HAC

# ---- Pass/fail thresholds -----------------------------------------------
THRESH_RHO_MIN  = 0.30
THRESH_RHO_P    = 0.001
THRESH_OLS_P    = 0.05
THRESH_OOS_FRAC = 0.75    # fraction of quarters with OOS R2 > 0
THRESH_CHI2_P   = 0.01
THRESH_HALFLIFE = 3.0     # days
THRESH_DIAG_DOM = 0.20


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
    """OLS with Newey-West HAC standard errors (Bartlett kernel)."""
    from scipy.stats import t as t_dist
    n = len(y)
    X = np.column_stack([np.ones(n), x])
    k = 2
    XtX_inv = np.linalg.inv(X.T @ X)
    beta = XtX_inv @ (X.T @ y)
    e = y - X @ beta
    Xe = X * e[:, None]          # (n, k)
    S = (Xe.T @ Xe) / n          # lag-0 (White)
    for lag in range(1, maxlags + 1):
        w = 1.0 - lag / (maxlags + 1)   # Bartlett weight
        G = (Xe[lag:].T @ Xe[:-lag]) / n
        S += w * (G + G.T)
    V = (XtX_inv @ S @ XtX_inv) / n
    se = np.sqrt(np.diag(V))
    tstat = beta / se
    pval = 2.0 * t_dist.sf(np.abs(tstat), df=n - k)
    r2 = float(1.0 - np.sum(e**2) / np.sum((y - y.mean())**2))
    return dict(
        beta_intercept=float(beta[0]),
        beta_x=float(beta[1]),
        se_x=float(se[1]),
        t_x=float(tstat[1]),
        p_x=float(pval[1]),
        r2=float(r2),
        n=n,
    )


def _oos_r2(basis: np.ndarray, funding: np.ndarray, train_window: int) -> dict:
    """
    Expanding-window walk-forward OOS R2.
    Vectorised via cumulative sums — O(n) time.
    """
    n = len(basis)
    cum_x  = np.concatenate([[0.0], np.cumsum(basis)])
    cum_y  = np.concatenate([[0.0], np.cumsum(funding)])
    cum_xy = np.concatenate([[0.0], np.cumsum(basis * funding)])
    cum_xx = np.concatenate([[0.0], np.cumsum(basis ** 2)])

    t_arr = np.arange(train_window, n)
    n_t   = t_arr.astype(float)
    sx    = cum_x[t_arr];   sy  = cum_y[t_arr]
    sxy   = cum_xy[t_arr];  sxx = cum_xx[t_arr]

    denom  = n_t * sxx - sx ** 2
    ok     = np.abs(denom) > 1e-15
    beta1  = np.where(ok, (n_t * sxy - sx * sy) / np.where(ok, denom, 1.0), 0.0)
    beta0  = np.where(ok, (sy - beta1 * sx) / n_t, 0.0)

    y_hat  = np.where(ok, beta0 + beta1 * basis[t_arr], np.nan)
    y_act  = funding[t_arr]

    valid  = ~np.isnan(y_hat)
    y_hat  = y_hat[valid];  y_act = y_act[valid]

    ss_res = float(np.sum((y_act - y_hat) ** 2))
    ss_tot = float(np.sum((y_act - y_act.mean()) ** 2))
    overall_r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    quarterly_r2: list[float] = []
    q = ROLLING_WINDOW
    n_oos = len(y_act)
    for i in range(n_oos // q):
        sl = slice(i * q, (i + 1) * q)
        yq, hq = y_act[sl], y_hat[sl]
        ss_r = float(np.sum((yq - hq) ** 2))
        ss_t = float(np.sum((yq - yq.mean()) ** 2))
        quarterly_r2.append(float(1.0 - ss_r / ss_t) if ss_t > 0 else float("nan"))

    frac_pos = float(np.nanmean([r > 0 for r in quarterly_r2])) if quarterly_r2 else 0.0
    return dict(
        overall_r2=float(overall_r2),
        frac_positive=frac_pos,
        quarterly_r2=quarterly_r2,
        n_oos=n_oos,
    )


def _classify_regimes(daily_bps: pd.Series, window: int = 252) -> pd.Series:
    """
    Assign basis regime per day using rolling tercile thresholds.
    Anti-lookahead: threshold at day d uses [d-window+1, d] only.

    Returns Series: 'contango' | 'flat' | 'backwardation' | 'unknown'
    """
    lo = daily_bps.rolling(window, min_periods=window).quantile(REGIME_PCTILE / 100)
    hi = daily_bps.rolling(window, min_periods=window).quantile(1 - REGIME_PCTILE / 100)

    regime = pd.Series("flat", index=daily_bps.index, dtype=object)
    regime[daily_bps > hi] = "contango"
    regime[daily_bps < lo] = "backwardation"
    regime[lo.isna() | hi.isna()] = "unknown"
    return regime


def _regime_persistence(regime_series: pd.Series) -> dict:
    labels = ["contango", "flat", "backwardation"]
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
# Data loading via DuckDB
# =========================================================================

def _load_all_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Returns
    -------
    df_aligned  : one row per funding settlement with 8h mean basis
    df_daily    : one row per calendar day with daily mean basis
    df_spot_daily: one row per calendar day with closing spot price
    """
    print(f"{_ts()} Loading data via DuckDB...")
    con = open_db()

    n_spot = register_view(con, "spot_1m",     BASE_DIR, "spot",    SYMBOL)
    n_perp = register_view(con, "perp_1m",     BASE_DIR, "perp",    SYMBOL)
    n_fund = register_view(con, "funding_raw", BASE_DIR, "funding", SYMBOL)
    print(f"  spot_1m     : {n_spot} partitions")
    print(f"  perp_1m     : {n_perp} partitions")
    print(f"  funding_raw : {n_fund} partitions")

    # ---- Hourly basis (avg of per-minute differences) -------------------
    # Use // (DuckDB integer-division) — plain / on BIGINT returns DOUBLE,
    # which makes every minute a unique GROUP BY bucket.
    con.execute(f"""
    CREATE OR REPLACE VIEW basis_1h AS
    SELECT
        (s.open_time // 3600000) * 3600000 AS hour_ts,
        AVG(p.close - s.close)              AS basis_abs,
        AVG((p.close / s.close - 1.0) * 10000.0) AS basis_bps,
        AVG(s.close)                        AS spot_price,
        COUNT(*)                            AS n_min
    FROM spot_1m s
    JOIN perp_1m p ON s.open_time = p.open_time
    GROUP BY s.open_time // 3600000
    HAVING COUNT(*) >= {MIN_MINS_1H}
    """)

    # ---- Align basis with funding settlements (8h preceding window) -----
    df_aligned = query(con, f"""
    SELECT
        f.settlement_ts,
        f.funding_rate,
        AVG(b.basis_abs)   AS mean_basis_abs_8h,
        AVG(b.basis_bps)   AS mean_basis_bps_8h,
        STDDEV(b.basis_bps) AS std_basis_bps_8h,
        COUNT(*)            AS n_hours_in_window,
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

    # ---- Daily mean basis -----------------------------------------------
    df_daily = query(con, """
    SELECT
        (hour_ts // 86400000) * 86400000 AS day_ts,
        AVG(basis_bps)   AS daily_mean_basis_bps,
        AVG(basis_abs)   AS daily_mean_basis_abs,
        AVG(spot_price)  AS daily_mean_spot,
        COUNT(*)         AS n_hours
    FROM basis_1h
    GROUP BY hour_ts // 86400000
    HAVING COUNT(*) >= 12
    ORDER BY day_ts
    """)

    # ---- Daily spot close (last 1m candle per UTC day) ------------------
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

    # Convert timestamps to datetime
    df_aligned["dt"] = pd.to_datetime(df_aligned["settlement_ts"], unit="ms", utc=True)
    df_daily["dt"]   = pd.to_datetime(df_daily["day_ts"],          unit="ms", utc=True)
    df_spot_daily["dt"] = pd.to_datetime(df_spot_daily["day_ts"],  unit="ms", utc=True)

    return df_aligned, df_daily, df_spot_daily


# =========================================================================
# Stage functions
# =========================================================================

def stage1_summary(df_aligned: pd.DataFrame, df_daily: pd.DataFrame) -> None:
    n_fund_total = len(df_aligned) + 0  # pre-filter count not separately tracked
    print(f"\n{_ts()} STAGE 1  Sample summary")
    print(f"  Aligned funding-basis pairs : {len(df_aligned):,}")
    print(f"  Date range (aligned)        : "
          f"{df_aligned['dt'].min().date()} to {df_aligned['dt'].max().date()}")
    print(f"  Daily basis observations    : {len(df_daily):,}")
    print(f"  Mean basis (bps)            : {df_aligned['mean_basis_bps_8h'].mean():+.2f}")
    print(f"  Std  basis (bps)            : {df_aligned['mean_basis_bps_8h'].std():.2f}")
    print(f"  Funding rate mean  (bps)    : "
          f"{df_aligned['funding_rate'].mean()*10000:+.2f}")
    print(f"  Funding rate std   (bps)    : "
          f"{df_aligned['funding_rate'].std()*10000:.2f}")
    print(f"  Coverage check: n_hours_min={df_aligned['n_hours_in_window'].min()}, "
          f"mean={df_aligned['n_hours_in_window'].mean():.1f}")


def stage2_h4a(
    df: pd.DataFrame,
) -> dict:
    """Full-sample H4a: Spearman, Newey-West OLS, OOS R2."""
    print(f"\n{_ts()} STAGE 2  H4a -- Basis predicts next funding rate (full sample)")

    basis   = df["mean_basis_bps_8h"].to_numpy()
    funding = df["funding_rate"].to_numpy() * 10_000  # convert to bps for interpretability

    # Spearman with bootstrap CI
    print(f"  Running Spearman with {N_BOOTSTRAP} bootstrap resamples...")
    sp = _spearman_ci(basis, funding)
    verdict_rho = "[PASS]" if (abs(sp["rho"]) >= THRESH_RHO_MIN and sp["pval"] < THRESH_RHO_P) else "[FAIL]"
    print(f"  Spearman rho = {sp['rho']:+.4f}  "
          f"95% CI [{sp['ci_lo']:+.4f}, {sp['ci_hi']:+.4f}]  "
          f"p = {sp['pval']:.2e}  "
          f"n = {sp['n']:,}  {verdict_rho}")

    # Newey-West OLS
    nw = _newey_west_ols(funding, basis)
    verdict_ols = "[PASS]" if nw["p_x"] < THRESH_OLS_P else "[FAIL]"
    print(f"  NW-OLS  beta={nw['beta_x']:+.5f}  "
          f"se={nw['se_x']:.5f}  t={nw['t_x']:+.2f}  "
          f"p={nw['p_x']:.2e}  R2={nw['r2']:.4f}  {verdict_ols}")

    # OOS R2
    print(f"  Computing walk-forward OOS R2 (train window = {TRAIN_WINDOW})...")
    oos = _oos_r2(basis, funding, TRAIN_WINDOW)
    verdict_oos = "[PASS]" if oos["frac_positive"] >= THRESH_OOS_FRAC else "[FAIL]"
    print(f"  OOS R2 overall = {oos['overall_r2']:+.4f}  "
          f"positive quarters = {oos['frac_positive']*100:.0f}%  "
          f"n_oos = {oos['n_oos']:,}  {verdict_oos}")

    return dict(h4a_full=dict(spearman=sp, nw_ols=nw, oos=oos))


def stage3_market_regimes(
    df: pd.DataFrame,
    df_spot_daily: pd.DataFrame,
) -> dict:
    """H4a re-run within each market regime."""
    print(f"\n{_ts()} STAGE 3  Market regime stratification")

    close = df_spot_daily.set_index("dt")["daily_close"]
    regimes = build_market_regimes(close)

    # Map regime to each funding settlement
    df = df.copy()
    df["date"] = df["dt"].dt.normalize()
    regime_map = regimes.rename_axis("dt").reset_index()
    regime_map["date"] = regime_map["dt"].dt.normalize()
    df = df.merge(
        regime_map[["date", "dt"]].rename(columns={"dt": "_reg_dt"}),
        on="date", how="left",
    )
    # Simpler: use .map via a date-indexed dict
    regime_dict = regimes.to_dict()
    df["market_regime"] = df["dt"].dt.normalize().map(
        {k.normalize(): v for k, v in regime_dict.items()}
    ).fillna("unknown")

    freq = df["market_regime"].value_counts()
    print(f"  Regime distribution in funding sample:")
    for reg, cnt in freq.items():
        print(f"    {reg:12s} : {cnt:,} ({cnt/len(df)*100:.1f}%)")

    by_regime: dict[str, dict] = {}
    for reg in ["bull", "bear", "sideways"]:
        sub = df[df["market_regime"] == reg]
        if len(sub) < 100:
            print(f"  {reg:12s} : too few observations ({len(sub)}) -- skip")
            by_regime[reg] = {"n": len(sub), "skipped": True}
            continue
        b = sub["mean_basis_bps_8h"].to_numpy()
        f = sub["funding_rate"].to_numpy() * 10_000
        sp = _spearman_ci(b, f, n_boot=500)   # fewer boots for sub-samples
        print(f"  {reg:12s} : n={len(sub):,}  "
              f"rho={sp['rho']:+.4f}  p={sp['pval']:.2e}")
        by_regime[reg] = {"spearman": sp, "n": len(sub)}

    # Store regime labels for charting
    df["market_regime_stored"] = df["market_regime"]
    return dict(h4a_by_regime=by_regime, _df_with_regime=df)


def stage4_h4b(df_daily: pd.DataFrame) -> dict:
    """H4b: regime persistence via transition matrix and chi-squared test."""
    print(f"\n{_ts()} STAGE 4  H4b -- Basis regime persistence")

    bps = df_daily.set_index("dt")["daily_mean_basis_bps"]
    regimes = _classify_regimes(bps)

    known = regimes[regimes != "unknown"]
    freq = known.value_counts()
    total = len(known)
    print(f"  Regime frequencies (n={total:,}, excl. warmup):")
    for reg in ["contango", "flat", "backwardation"]:
        cnt = freq.get(reg, 0)
        print(f"    {reg:14s} : {cnt:,} ({cnt/total*100:.1f}%)")

    pers = _regime_persistence(regimes)

    verdict_chi2 = "[PASS]" if pers["p_chi2"] < THRESH_CHI2_P else "[FAIL]"
    verdict_hl   = "[PASS]" if min(pers["half_lives"].values()) >= THRESH_HALFLIFE else "[FAIL]"
    verdict_dd   = "[PASS]" if pers["diag_dominance"] >= THRESH_DIAG_DOM else "[FAIL]"

    print(f"\n  Transition matrix (row = from, col = to):")
    labels = ["contango", "flat", "backwardation"]
    header = "               " + "".join(f"{'':>14s}" for _ in labels)
    hdr2   = "               " + "".join(f"{l:>14s}" for l in labels)
    print(f"  {hdr2}")
    for r in labels:
        row = "  " + f"{r:14s}" + "".join(
            f"{pers['prob_matrix'][r][c]*100:13.1f}%" for c in labels
        )
        print(row)

    print(f"\n  Chi-squared = {pers['chi2']:.2f}  "
          f"dof = {pers['dof']}  "
          f"p = {pers['p_chi2']:.2e}  "
          f"Cramer's V = {pers['cramers_v']:.4f}  {verdict_chi2}")
    print(f"  Diagonal dominance = {pers['diag_dominance']:+.4f}  {verdict_dd}")
    print(f"  Half-lives (mean consecutive days per regime):")
    for reg in labels:
        hl = pers["half_lives"][reg]
        print(f"    {reg:14s} : {hl:.1f} days")
    print(f"  Min half-life = {min(pers['half_lives'].values()):.1f} days  {verdict_hl}")

    return dict(h4b=pers, _regimes=regimes)


def stage5_structural(df: pd.DataFrame) -> dict:
    """Re-run H4a on clean sample excluding structural event windows."""
    print(f"\n{_ts()} STAGE 5  Structural event conditioning (clean sample)")

    ts_arr = df["settlement_ts"].to_numpy()
    excl   = build_exclusion_mask_ms(ts_arr)
    n_excl = int(excl.sum())
    df_clean = df[~excl].copy()

    print(f"  Excluded {n_excl} settlements within +/-7d of structural events")
    print(f"  Clean sample n = {len(df_clean):,} ({len(df_clean)/len(df)*100:.1f}%)")

    if len(df_clean) < 500:
        print("  [WARN] Clean sample too small -- skipping")
        return {}

    b = df_clean["mean_basis_bps_8h"].to_numpy()
    f = df_clean["funding_rate"].to_numpy() * 10_000
    sp = _spearman_ci(b, f, n_boot=500)
    nw = _newey_west_ols(f, b)

    print(f"  Clean Spearman rho = {sp['rho']:+.4f}  p = {sp['pval']:.2e}")
    print(f"  Clean NW-OLS  beta = {nw['beta_x']:+.5f}  p = {nw['p_x']:.2e}")
    print(f"  Sensitivity: delta_rho vs full = "
          f"{sp['rho'] - 0:.4f}"   # placeholder; will compare in report
    )
    return dict(clean_sample=dict(spearman=sp, nw_ols=nw, n=len(df_clean)))


# =========================================================================
# Charts
# =========================================================================

def _shade_regimes(ax, dates, regimes, colors):
    """Fill background bands for market/basis regimes."""
    prev_reg = None
    prev_dt  = None
    for dt, reg in zip(dates, regimes):
        if reg != prev_reg:
            if prev_reg is not None and prev_reg in colors:
                ax.axvspan(prev_dt, dt, alpha=0.15,
                           color=colors[prev_reg], linewidth=0)
            prev_reg = reg
            prev_dt  = dt
    if prev_reg and prev_reg in colors and prev_dt is not None:
        ax.axvspan(prev_dt, dates[-1], alpha=0.15,
                   color=colors[prev_reg], linewidth=0)


def _mark_events(ax, ymin_frac=0.0):
    ev = events_df()
    for _, row in ev.iterrows():
        ax.axvline(row["date"], color="#999999", linewidth=0.7, linestyle="--", alpha=0.6)
        ax.text(row["date"], ax.get_ylim()[0] +
                (ax.get_ylim()[1]-ax.get_ylim()[0]) * 0.02,
                row["label"], rotation=90, fontsize=6, color="#555555",
                va="bottom", ha="right")


def stage6_charts(
    df_aligned: pd.DataFrame,
    df_daily:   pd.DataFrame,
    df_spot_daily: pd.DataFrame,
    h4a_full: dict,
    h4a_by_regime: dict,
    h4b_pers: dict,
    h4b_regimes: pd.Series,
    clean_result: dict,
) -> None:
    print(f"\n{_ts()} STAGE 6  Generating charts -> {CHART_DIR}")
    CHART_DIR.mkdir(parents=True, exist_ok=True)

    plt.rcParams.update({
        "figure.dpi": 120, "font.size": 9,
        "axes.grid": True, "grid.alpha": 0.25,
        "axes.spines.top": False, "axes.spines.right": False,
    })
    close_s   = df_spot_daily.set_index("dt")["daily_close"]
    mkt_reg   = build_market_regimes(close_s)
    basis_s   = df_daily.set_index("dt")["daily_mean_basis_bps"]
    basis_smooth = basis_s.rolling(24, min_periods=1).mean()

    # ---- Chart 1: price + basis overview --------------------------------
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 7), sharex=True)

    ax1.semilogy(close_s.index, close_s.values, color="#2c3e50", linewidth=0.8)
    _shade_regimes(ax1, mkt_reg.index, mkt_reg.values, REGIME_COLORS)
    ax1.set_ylabel("BTC Spot Price (log)")
    ax1.set_title("BTCUSDT: Market Regime + Spot Price  /  Perp-Spot Basis (Phase B.1A H4)")
    patches = [mpatches.Patch(color=c, alpha=0.4, label=r)
               for r, c in REGIME_COLORS.items() if r != "unknown"]
    ax1.legend(handles=patches, loc="upper left", fontsize=7)

    BASIS_COLORS = {
        "contango":      "#3498db",
        "flat":          "#95a5a6",
        "backwardation": "#e67e22",
        "unknown":       "#ffffff",
    }
    ax2.plot(basis_smooth.index, basis_smooth.values,
             color="#2c3e50", linewidth=0.7, label="24h rolling mean")
    ax2.axhline(0, color="#c0392b", linewidth=0.8, linestyle="--")
    _shade_regimes(ax2, h4b_regimes.index, h4b_regimes.values, BASIS_COLORS)
    _mark_events(ax2)
    ax2.set_ylabel("Basis (bps)")
    ax2.set_xlabel("Date (UTC)")
    bpatches = [mpatches.Patch(color=c, alpha=0.4, label=r)
                for r, c in BASIS_COLORS.items() if r != "unknown"]
    ax2.legend(handles=bpatches, loc="upper right", fontsize=7)

    plt.tight_layout()
    plt.savefig(CHART_DIR / "h4_01_overview.png", bbox_inches="tight")
    plt.close()
    print("  [OK] h4_01_overview.png")

    # ---- Chart 2: scatter basis vs funding ------------------------------
    fig, ax = plt.subplots(figsize=(9, 6))
    sp = h4a_full["spearman"]
    basis_arr   = df_aligned["mean_basis_bps_8h"].values
    funding_bps = df_aligned["funding_rate"].values * 10_000
    years = df_aligned["dt"].dt.year.values
    cmap  = plt.cm.get_cmap("tab10")
    yr_set = sorted(set(years))
    for i, yr in enumerate(yr_set):
        m = years == yr
        ax.scatter(basis_arr[m], funding_bps[m],
                   s=4, alpha=0.35, color=cmap(i / 10), label=str(yr))
    # OLS line
    nw = h4a_full["nw_ols"]
    xlim = np.array([np.percentile(basis_arr, 1), np.percentile(basis_arr, 99)])
    ax.plot(xlim, nw["beta_intercept"] + nw["beta_x"] * xlim,
            color="#e74c3c", linewidth=1.5, linestyle="--", label="OLS (NW SE)")
    ax.set_xlabel("8h Mean Basis (bps)")
    ax.set_ylabel("Next Funding Rate (bps)")
    ax.set_title(
        f"H4a: Basis vs Next Funding  --  "
        f"Spearman rho={sp['rho']:+.4f}  p={sp['pval']:.2e}  n={sp['n']:,}"
    )
    ax.legend(fontsize=7, markerscale=3)
    plt.tight_layout()
    plt.savefig(CHART_DIR / "h4_02_scatter.png", bbox_inches="tight")
    plt.close()
    print("  [OK] h4_02_scatter.png")

    # ---- Chart 3: rolling Spearman rho ----------------------------------
    fig, ax = plt.subplots(figsize=(12, 4))
    basis_arr = df_aligned["mean_basis_bps_8h"].values
    funding_bps = df_aligned["funding_rate"].values * 10_000
    win = ROLLING_WINDOW
    roll_rho, roll_dt = [], []
    for i in range(win, len(basis_arr)):
        sl = slice(i - win, i)
        r, _ = spearmanr(basis_arr[sl], funding_bps[sl])
        roll_rho.append(r)
        roll_dt.append(df_aligned["dt"].iloc[i])
    ax.fill_between(roll_dt, 0, roll_rho,
                    where=[r >= 0 for r in roll_rho], alpha=0.4, color="#27ae60")
    ax.fill_between(roll_dt, 0, roll_rho,
                    where=[r < 0 for r in roll_rho],  alpha=0.4, color="#e74c3c")
    ax.plot(roll_dt, roll_rho, color="#2c3e50", linewidth=0.8)
    ax.axhline(sp["rho"], color="#3498db", linestyle="--", linewidth=1.0,
               label=f"Overall rho = {sp['rho']:+.4f}")
    ax.axhline(0, color="#7f8c8d", linewidth=0.6)
    _mark_events(ax)
    ax.set_ylabel("Spearman rho")
    ax.set_title(f"H4a: Rolling Spearman rho ({win}-event window ~quarterly)")
    ax.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(CHART_DIR / "h4_03_rolling_rho.png", bbox_inches="tight")
    plt.close()
    print("  [OK] h4_03_rolling_rho.png")

    # ---- Chart 4: OOS R2 quarterly bars ---------------------------------
    oos = h4a_full["oos"]
    fig, ax = plt.subplots(figsize=(12, 4))
    q_r2 = oos["quarterly_r2"]
    n_valid = oos["n_oos"]
    q_size  = ROLLING_WINDOW
    q_idx   = [
        df_aligned["dt"].iloc[min(TRAIN_WINDOW + (q + 1) * q_size - 1, len(df_aligned) - 1)]
        for q in range(len(q_r2))
    ]
    colors_bar = ["#27ae60" if r > 0 else "#e74c3c" for r in q_r2]
    ax.bar(range(len(q_r2)), q_r2, color=colors_bar, alpha=0.7, width=0.8)
    ax.axhline(0, color="#2c3e50", linewidth=1.0)
    ax.axhline(oos["overall_r2"], color="#3498db", linestyle="--", linewidth=1.2,
               label=f"Overall OOS R2 = {oos['overall_r2']:+.4f}")
    ax.set_xlabel("Quarter index (after training warmup)")
    ax.set_ylabel("Quarterly OOS R2")
    ax.set_title(
        f"H4a: Walk-forward OOS R2 by Quarter  --  "
        f"{oos['frac_positive']*100:.0f}% positive  "
        f"(threshold: {THRESH_OOS_FRAC*100:.0f}%)"
    )
    ax.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(CHART_DIR / "h4_04_oos_r2.png", bbox_inches="tight")
    plt.close()
    print("  [OK] h4_04_oos_r2.png")

    # ---- Chart 5: transition matrix heatmap ----------------------------
    labels = ["contango", "flat", "backwardation"]
    short  = ["Contango", "Flat", "Backwd."]
    prob   = h4b_pers["prob_matrix"]
    mat    = np.array([[prob[r][c] for c in labels] for r in labels])
    fig, ax = plt.subplots(figsize=(6, 5))
    im = ax.imshow(mat, cmap="Blues", vmin=0, vmax=1)
    plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    ax.set_xticks(range(3)); ax.set_xticklabels(short)
    ax.set_yticks(range(3)); ax.set_yticklabels(short)
    ax.set_xlabel("To regime")
    ax.set_ylabel("From regime")
    for i in range(3):
        for j in range(3):
            ax.text(j, i, f"{mat[i,j]*100:.1f}%",
                    ha="center", va="center",
                    color="white" if mat[i, j] > 0.5 else "black",
                    fontsize=10)
    ax.set_title(
        f"H4b: Basis Regime Transition Matrix\n"
        f"chi2={h4b_pers['chi2']:.1f}  p={h4b_pers['p_chi2']:.2e}  "
        f"V={h4b_pers['cramers_v']:.3f}  diag_dom={h4b_pers['diag_dominance']:+.3f}"
    )
    plt.tight_layout()
    plt.savefig(CHART_DIR / "h4_05_transition_matrix.png", bbox_inches="tight")
    plt.close()
    print("  [OK] h4_05_transition_matrix.png")


# =========================================================================
# JSON report
# =========================================================================

def stage7_report(results: dict, elapsed_s: float) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    results["run_date"] = datetime.now(timezone.utc).isoformat()
    results["elapsed_s"] = round(elapsed_s, 1)
    out = REPORT_DIR / "h4_summary.json"
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2, default=str)
    print(f"\n{_ts()} Report written -> {out}")


# =========================================================================
# Verdict
# =========================================================================

def _verdict(results: dict) -> str:
    sp  = results["h4a_full"]["spearman"]
    nw  = results["h4a_full"]["nw_ols"]
    oos = results["h4a_full"]["oos"]
    h4b = results["h4b"]

    rows = [
        ("H4a Spearman rho",    abs(sp["rho"]) >= THRESH_RHO_MIN and sp["pval"] < THRESH_RHO_P),
        ("H4a NW-OLS beta",     nw["p_x"] < THRESH_OLS_P),
        ("H4a OOS R2 fraction", oos["frac_positive"] >= THRESH_OOS_FRAC),
        ("H4b Chi-squared",     h4b["p_chi2"] < THRESH_CHI2_P),
        ("H4b Diagonal dom.",   h4b["diag_dominance"] >= THRESH_DIAG_DOM),
        ("H4b Half-life min",   min(h4b["half_lives"].values()) >= THRESH_HALFLIFE),
    ]

    print("\n" + "=" * 70)
    print("H4 VERDICT")
    print("-" * 70)
    for label, passed in rows:
        print(f"  {label:30s} : {'[PASS]' if passed else '[FAIL]'}")
    overall = all(p for _, p in rows)
    print("-" * 70)
    print(f"  {'Overall H4':30s} : {'[PASS]' if overall else '[FAIL]'}")
    print("=" * 70)

    # Store verdicts for JSON
    results["verdict"] = {k: ("PASS" if v else "FAIL") for k, v in rows}
    results["verdict"]["overall"] = "PASS" if overall else "FAIL"
    return "PASS" if overall else "FAIL"


# =========================================================================
# Main
# =========================================================================

def main() -> None:
    t0 = time.time()
    print("=" * 70)
    print("H4 BASIS REGIMES -- Phase B.1A Evidence Battery")
    print(f"BTCUSDT  |  research corpus 2021-01 to 2026-05")
    print(f"Run date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 70)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    # ---- Load ----
    df_aligned, df_daily, df_spot_daily = _load_all_data()
    stage1_summary(df_aligned, df_daily)

    # ---- H4a full sample ----
    print("\n" + "-" * 70)
    h4a_result = stage2_h4a(df_aligned)

    # ---- Market regime stratification ----
    print("\n" + "-" * 70)
    regime_result = stage3_market_regimes(df_aligned, df_spot_daily)

    # ---- H4b regime persistence ----
    print("\n" + "-" * 70)
    h4b_result = stage4_h4b(df_daily)

    # ---- Structural event clean sample ----
    print("\n" + "-" * 70)
    clean_result = stage5_structural(df_aligned)

    # ---- Charts ----
    print("\n" + "-" * 70)
    stage6_charts(
        df_aligned, df_daily, df_spot_daily,
        h4a_result["h4a_full"],
        regime_result["h4a_by_regime"],
        h4b_result["h4b"],
        h4b_result["_regimes"],
        clean_result,
    )

    # ---- Compile results dict ----
    results = {
        "hypothesis":    "H4 Basis Regimes",
        "symbol":        SYMBOL,
        "n_aligned":     len(df_aligned),
        "n_daily":       len(df_daily),
        "date_start":    str(df_aligned["dt"].min().date()),
        "date_end":      str(df_aligned["dt"].max().date()),
        **h4a_result,
        "h4a_by_regime": regime_result["h4a_by_regime"],
        **h4b_result,
        "clean_sample":  clean_result,
    }
    # Remove internal keys not JSON-friendly
    results.pop("_regimes",          None)
    results.pop("_df_with_regime",   None)
    results.pop("h4b",               None)
    results["h4b"] = h4b_result["h4b"]

    # ---- Verdict ----
    print("\n" + "-" * 70)
    verdict = _verdict(results)

    # ---- Save report ----
    elapsed = time.time() - t0
    stage7_report(results, elapsed)

    print(f"\n[{verdict}] H4 COMPLETE  elapsed: {elapsed/60:.1f} min")
    print(f"Charts -> {CHART_DIR}")
    print(f"Report -> {REPORT_DIR / 'h4_summary.json'}")
    print("=" * 70)


if __name__ == "__main__":
    main()
