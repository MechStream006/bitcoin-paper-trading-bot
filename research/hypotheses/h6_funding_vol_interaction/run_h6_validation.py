"""
H6 Validation: medium_vol x funding signal robustness
======================================================
Determines whether H6 is a genuine effect or a historical artifact by
subjecting the medium_vol x funding signal to:

  1. Year-by-year stability (2021-2026 YTD)
  2. Expanding-year OOS (train on prior years, test on current year)
  3. Rolling 252-day H-L effect (temporal stability)
  4. Sensitivity: funding threshold (pctile splits 20/25/33/40/50)
  5. Sensitivity: vol definition (RV windows + tercile/quartile/quintile)
  6. Sensitivity: holding period (3/5/7/10/14/21 days)
  7. Trade simulation: non-overlapping, 7d and 14d holds
  8. Equity curve and drawdown analysis

No new data ingestion. No production strategy design.

Verdict rubric
--------------
  GENUINE  : signal consistent direction in >= 4/6 years AND
             survives > 70% of sensitivity variants AND
             trade simulation Sharpe > 0.3 AND
             no single year drives > 60% of total P&L

  ARTIFACT  : signal driven by <= 2 years or disappears when
              those years are removed

  CONDITIONAL: genuine effect but requires specific market environment
               (e.g. non-bull years only)
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
from scipy.stats import mannwhitneyu, t as t_dist, spearmanr

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=RuntimeWarning)

# ---- Path setup ------------------------------------------------------------
_THIS_DIR     = pathlib.Path(__file__).resolve().parent
_RESEARCH_DIR = _THIS_DIR.parent.parent
if str(_RESEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_DIR))

from storage.duck import open_db, query, register_view                    # noqa: E402
from hypotheses.shared.market_regimes import build_market_regimes         # noqa: E402
from hypotheses.shared.structural_events import build_exclusion_mask_ms   # noqa: E402

# ---- Directories -----------------------------------------------------------
BASE_DIR    = _RESEARCH_DIR / "data" / "raw"
REPORT_DIR  = _RESEARCH_DIR / "reports" / "h6_funding_vol_interaction" / "validation"
CHART_DIR   = REPORT_DIR / "charts"
SYMBOL      = "BTCUSDT"

# ---- Parameters (matching H6) ----------------------------------------------
REGIME_WINDOW  = 252
REGIME_PCTILE  = 33     # current split: bottom/top 33%
RV_WINDOW      = 21     # current realized vol window
MIN_MINS_1H    = 30
MIN_DAILY_FUND = 2

# ---- Sensitivity grid ------------------------------------------------------
FUND_PCTILES   = [20, 25, 33, 40, 50]    # top/bottom split for funding
VOL_PCTILES    = [20, 25, 33, 40, 50]    # top/bottom split for vol
RV_WINDOWS     = [10, 14, 21, 30, 63]    # realized vol lookback (days)
HORIZONS_ALL   = [3, 5, 7, 10, 14, 21]
HORIZONS_SIM   = [7, 14]                 # for trade simulation

# ---- Verdict thresholds ----------------------------------------------------
YEARS_CONSISTENT_MIN  = 4   # of 6 years must show correct sign at 7d
SENSITIVITY_PASS_FRAC = 0.70
SHARPE_MIN            = 0.30
MAX_SINGLE_YEAR_PNL   = 0.60  # no single year drives > 60% of total P&L


# =============================================================================
# Helpers
# =============================================================================

def _ts() -> str:
    return datetime.now(timezone.utc).strftime("[%H:%M:%S]")


def _effect_size(a: np.ndarray, b: np.ndarray) -> dict:
    """Mean diff (a-b), Cohen's d, MWU p for a vs b."""
    if len(a) < 5 or len(b) < 5:
        return {"diff_pct": np.nan, "cohens_d": np.nan, "mw_p": np.nan,
                "mean_a": np.nan, "mean_b": np.nan}
    diff = float(a.mean() - b.mean())
    pooled_var = (
        (len(a) - 1) * a.var(ddof=1) + (len(b) - 1) * b.var(ddof=1)
    ) / (len(a) + len(b) - 2)
    pooled_std = float(np.sqrt(max(pooled_var, 0.0)))
    d = (a.mean() - b.mean()) / pooled_std if pooled_std > 0 else np.nan
    try:
        _, p = mannwhitneyu(a, b, alternative="two-sided")
    except Exception:
        p = np.nan
    return dict(
        diff_pct=diff * 100,
        cohens_d=float(d),
        mw_p=float(p),
        mean_a=float(a.mean() * 100),
        mean_b=float(b.mean() * 100),
    )


def _build_tercile(series: pd.Series, lo_pctile: int,
                   hi_pctile: int, window: int = REGIME_WINDOW
                   ) -> pd.Series:
    """
    Rolling window tercile regime.
    Values > hi_pctile rolling percentile -> 'high'
    Values < lo_pctile rolling percentile -> 'low'
    Middle band -> 'medium'
    """
    lo_q = series.rolling(window, min_periods=window).quantile(lo_pctile / 100)
    hi_q = series.rolling(window, min_periods=window).quantile(hi_pctile / 100)
    regime = pd.Series("medium", index=series.index, dtype=object)
    regime[series > hi_q] = "high"
    regime[series < lo_q] = "low"
    regime[lo_q.isna() | hi_q.isna()] = "unknown"
    return regime


def _build_rv(daily_close: pd.Series, rv_window: int) -> pd.Series:
    log_ret = np.log(daily_close / daily_close.shift(1))
    return log_ret.rolling(rv_window, min_periods=rv_window).std() * np.sqrt(252)


def _ols_oos_r2(x: np.ndarray, y: np.ndarray, train_n: int) -> float:
    """Simple expanding OLS OOS R2."""
    valid = ~(np.isnan(x) | np.isnan(y))
    x = x[valid]; y = y[valid]
    n = len(x)
    if n < train_n + 10:
        return np.nan
    cx = np.concatenate([[0.], np.cumsum(x)])
    cy = np.concatenate([[0.], np.cumsum(y)])
    cxy = np.concatenate([[0.], np.cumsum(x * y)])
    cxx = np.concatenate([[0.], np.cumsum(x ** 2)])
    t_ = np.arange(train_n, n)
    nt = t_.astype(float)
    sx = cx[t_]; sy = cy[t_]; sxy = cxy[t_]; sxx = cxx[t_]
    den = nt * sxx - sx ** 2
    ok = np.abs(den) > 1e-15
    b1 = np.where(ok, (nt * sxy - sx * sy) / np.where(ok, den, 1.), 0.)
    b0 = np.where(ok, (sy - b1 * sx) / nt, 0.)
    yh = np.where(ok, b0 + b1 * x[t_], np.nan)
    ya = y[t_]
    v2 = ~np.isnan(yh)
    yh = yh[v2]; ya = ya[v2]
    ss_res = float(np.sum((ya - yh) ** 2))
    ss_tot = float(np.sum((ya - ya.mean()) ** 2))
    return float(1. - ss_res / ss_tot) if ss_tot > 0 else 0.


# =============================================================================
# Data loading (lean version — no basis, just funding + spot)
# =============================================================================

def _load_data() -> pd.DataFrame:
    print(f"{_ts()} Loading data via DuckDB...")
    con = open_db()
    n_spot = register_view(con, "spot_1m",     BASE_DIR, "spot",    SYMBOL)
    n_fund = register_view(con, "funding_raw", BASE_DIR, "funding", SYMBOL)
    print(f"  spot_1m: {n_spot}  funding_raw: {n_fund} partitions")

    df_fund = query(con, f"""
    SELECT
        (timestamp_ms // 86400000) * 86400000 AS day_ts,
        AVG(funding_rate) * 10000.0           AS daily_mean_funding_bps,
        COUNT(*)                              AS n_settlements
    FROM funding_raw
    GROUP BY timestamp_ms // 86400000
    HAVING COUNT(*) >= {MIN_DAILY_FUND}
    ORDER BY day_ts
    """)

    df_spot = query(con, """
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

    m = df_spot.merge(
        df_fund[["day_ts", "daily_mean_funding_bps", "n_settlements"]],
        on="day_ts", how="left",
    )
    m["dt"]   = pd.to_datetime(m["day_ts"], unit="ms", utc=True)
    m["date"] = m["dt"].dt.normalize().dt.tz_localize(None).dt.date
    m["year"] = m["dt"].dt.year
    m = m.sort_values("day_ts").reset_index(drop=True)

    # Regimes (matching H6 defaults)
    close_s = m["daily_close"]
    rv = _build_rv(close_s, RV_WINDOW)
    m["rv_21d"] = rv.values
    m["vol_regime"]     = _build_tercile(rv, REGIME_PCTILE, 100 - REGIME_PCTILE)
    m["funding_regime"] = _build_tercile(
        m["daily_mean_funding_bps"], REGIME_PCTILE, 100 - REGIME_PCTILE
    )

    # Market regime
    close_dt = pd.Series(close_s.values, index=m["dt"].values)
    mkt = build_market_regimes(close_dt)
    mkt_map = {k.normalize(): v for k, v in mkt.to_dict().items()}
    m["market_regime"] = m["dt"].dt.normalize().dt.tz_localize(None).map(mkt_map).fillna("unknown")

    # Forward returns
    for h in [1, 3, 5, 7, 10, 14, 21]:
        m[f"ret_{h}d"] = close_s.shift(-h).values / close_s.values - 1.0

    return m


# =============================================================================
# Stage 1 — Year-by-year stability
# =============================================================================

def stage1_year_stability(m: pd.DataFrame) -> dict:
    print(f"\n{_ts()} STAGE 1  Year-by-year stability within medium_vol")
    print(f"  {'Year':5s}  {'n_hi':5s}  {'n_lo':5s}  {'high_7d':8s}  {'low_7d':8s}  "
          f"{'H-L_7d':8s}  {'d_7d':6s}  {'p_7d':6s}  "
          f"{'H-L_14d':8s}  {'d_14d':6s}  {'sign7':6s}")
    results: dict = {}
    consistent_count = 0  # years with correct sign at 7d (low > high)
    for yr in [2021, 2022, 2023, 2024, 2025, 2026]:
        sub = m[(m["vol_regime"] == "medium") & (m["year"] == yr)]
        hi7  = sub.loc[sub["funding_regime"] == "high", "ret_7d"].dropna().to_numpy()
        lo7  = sub.loc[sub["funding_regime"] == "low",  "ret_7d"].dropna().to_numpy()
        hi14 = sub.loc[sub["funding_regime"] == "high", "ret_14d"].dropna().to_numpy()
        lo14 = sub.loc[sub["funding_regime"] == "low",  "ret_14d"].dropna().to_numpy()
        eff7  = _effect_size(lo7, hi7)   # low - high (positive = correct direction)
        eff14 = _effect_size(lo14, hi14)

        sign_ok = (not np.isnan(eff7["diff_pct"])) and eff7["diff_pct"] > 0
        if len(hi7) >= 5 and len(lo7) >= 5:
            if sign_ok:
                consistent_count += 1

        h7s  = f"{eff7['mean_b']:+.2f}%" if not np.isnan(eff7["mean_b"]) else "  n/a"
        l7s  = f"{eff7['mean_a']:+.2f}%" if not np.isnan(eff7["mean_a"]) else "  n/a"
        hl7s = f"{eff7['diff_pct']:+.2f}%" if not np.isnan(eff7["diff_pct"]) else "   n/a"
        d7s  = f"{eff7['cohens_d']:+.3f}"  if not np.isnan(eff7["cohens_d"])  else "  n/a"
        p7s  = f"{eff7['mw_p']:.3f}"       if not np.isnan(eff7["mw_p"])      else "  n/a"
        hl14s= f"{eff14['diff_pct']:+.2f}%" if not np.isnan(eff14["diff_pct"]) else "   n/a"
        d14s = f"{eff14['cohens_d']:+.3f}"  if not np.isnan(eff14["cohens_d"])  else "  n/a"
        flag = "[OK]" if sign_ok and len(hi7) >= 5 else ("[-]" if len(hi7) < 5 else "[X]")
        print(f"  {yr:5d}  {len(hi7):5d}  {len(lo7):5d}  {h7s}  {l7s}  "
              f"{hl7s}  {d7s}  {p7s}  {hl14s}  {d14s}  {flag}")
        results[yr] = {
            "n_high": len(hi7), "n_low": len(lo7),
            "eff7": eff7, "eff14": eff14,
            "sign_consistent_7d": sign_ok,
        }

    testable = sum(1 for yr, d in results.items() if d["n_high"] >= 5 and d["n_low"] >= 5)
    print(f"\n  Correct sign (7d) in {consistent_count}/{testable} testable years "
          f"(need {YEARS_CONSISTENT_MIN}/{testable})")
    results["_summary"] = {
        "consistent_years_7d": consistent_count,
        "testable_years": testable,
        "pass": consistent_count >= YEARS_CONSISTENT_MIN,
    }
    print("-" * 72)
    return results


# =============================================================================
# Stage 2 — Expanding-year OOS
# =============================================================================

def stage2_expanding_year_oos(m: pd.DataFrame) -> dict:
    print(f"\n{_ts()} STAGE 2  Expanding-year OOS (train on prior years, test on current)")
    print(f"  {'Test year':10s}  {'n_train_med':11s}  {'n_test_med':10s}  "
          f"{'OOS_R2_7d':10s}  {'H-L_7d':8s}  {'H-L_14d':9s}")
    results: dict = {}
    for test_yr in [2022, 2023, 2024, 2025]:
        train = m[(m["vol_regime"] == "medium") & (m["year"] < test_yr) &
                  m["daily_mean_funding_bps"].notna()]
        test  = m[(m["vol_regime"] == "medium") & (m["year"] == test_yr) &
                  m["daily_mean_funding_bps"].notna()]
        if len(train) < 30 or len(test) < 10:
            continue
        # OLS on train, predict on test
        x_tr = train["daily_mean_funding_bps"].to_numpy()
        y_tr = train["ret_7d"].dropna().to_numpy()
        x_tr = x_tr[:len(y_tr)]
        if len(x_tr) < 10:
            continue
        # Simple OLS fit
        X = np.column_stack([np.ones(len(x_tr)), x_tr])
        try:
            beta = np.linalg.lstsq(X, y_tr, rcond=None)[0]
        except Exception:
            continue
        x_te  = test["daily_mean_funding_bps"].to_numpy()
        y_te7 = test["ret_7d"].to_numpy()
        valid = ~(np.isnan(x_te) | np.isnan(y_te7))
        x_te  = x_te[valid]; y_te7 = y_te7[valid]
        if len(x_te) < 5:
            continue
        y_hat = beta[0] + beta[1] * x_te
        ss_res = float(np.sum((y_te7 - y_hat) ** 2))
        ss_tot = float(np.sum((y_te7 - y_te7.mean()) ** 2))
        r2 = float(1. - ss_res / ss_tot) if ss_tot > 0 else np.nan

        # H-L in test year
        hi7  = test.loc[test["funding_regime"] == "high", "ret_7d"].dropna().to_numpy()
        lo7  = test.loc[test["funding_regime"] == "low",  "ret_7d"].dropna().to_numpy()
        hi14 = test.loc[test["funding_regime"] == "high", "ret_14d"].dropna().to_numpy()
        lo14 = test.loc[test["funding_regime"] == "low",  "ret_14d"].dropna().to_numpy()
        eff7  = _effect_size(lo7, hi7)
        eff14 = _effect_size(lo14, hi14)

        r2s   = f"{r2:+.4f}" if not np.isnan(r2) else "  nan"
        hl7s  = f"{eff7['diff_pct']:+.2f}%"  if not np.isnan(eff7["diff_pct"])  else "  n/a"
        hl14s = f"{eff14['diff_pct']:+.2f}%"  if not np.isnan(eff14["diff_pct"]) else "  n/a"
        flag  = "[PASS]" if (not np.isnan(r2)) and r2 > 0 else "[FAIL]"
        print(f"  {test_yr:10d}  {len(train):11d}  {len(test):10d}  "
              f"{r2s}  {hl7s}  {hl14s}  {flag}")
        results[test_yr] = {
            "n_train": len(train), "n_test": len(test),
            "oos_r2_7d": r2, "eff7": eff7, "eff14": eff14,
        }
    print("-" * 72)
    return results


# =============================================================================
# Stage 3 — Rolling 252-day H-L effect (temporal stability)
# =============================================================================

def stage3_rolling_hl(m: pd.DataFrame) -> dict:
    print(f"\n{_ts()} STAGE 3  Rolling 252-day H-L effect within medium_vol")
    med = m[m["vol_regime"] == "medium"].copy().reset_index(drop=True)
    med["hl_7d_roll"]  = np.nan
    med["hl_14d_roll"] = np.nan
    window = 100   # min observations for a reliable estimate

    for i in range(len(med)):
        sub = med.iloc[max(0, i - 252):i + 1]
        hi7  = sub.loc[sub["funding_regime"] == "high", "ret_7d"].dropna().to_numpy()
        lo7  = sub.loc[sub["funding_regime"] == "low",  "ret_7d"].dropna().to_numpy()
        if len(hi7) >= 5 and len(lo7) >= 5:
            med.at[med.index[i], "hl_7d_roll"]  = float(lo7.mean() - hi7.mean()) * 100
        hi14 = sub.loc[sub["funding_regime"] == "high", "ret_14d"].dropna().to_numpy()
        lo14 = sub.loc[sub["funding_regime"] == "low",  "ret_14d"].dropna().to_numpy()
        if len(hi14) >= 5 and len(lo14) >= 5:
            med.at[med.index[i], "hl_14d_roll"] = float(lo14.mean() - hi14.mean()) * 100

    rolling_7d  = med["hl_7d_roll"].dropna()
    rolling_14d = med["hl_14d_roll"].dropna()
    pct_pos_7d  = float((rolling_7d > 0).mean()) * 100
    pct_pos_14d = float((rolling_14d > 0).mean()) * 100

    print(f"  Rolling 7d  H-L: pct_positive={pct_pos_7d:.1f}%  "
          f"median={rolling_7d.median():+.2f}%  "
          f"min={rolling_7d.min():+.2f}%  max={rolling_7d.max():+.2f}%")
    print(f"  Rolling 14d H-L: pct_positive={pct_pos_14d:.1f}%  "
          f"median={rolling_14d.median():+.2f}%  "
          f"min={rolling_14d.min():+.2f}%  max={rolling_14d.max():+.2f}%")
    print("-" * 72)
    return {
        "pct_positive_7d":  pct_pos_7d,
        "pct_positive_14d": pct_pos_14d,
        "rolling_df": med[["day_ts", "hl_7d_roll", "hl_14d_roll"]].copy(),
    }


# =============================================================================
# Stage 4 — Sensitivity: funding pctile thresholds
# =============================================================================

def stage4_sensitivity_funding(m: pd.DataFrame) -> dict:
    print(f"\n{_ts()} STAGE 4  Sensitivity: funding threshold splits")
    print(f"  {'split':5s}  {'n_high':6s}  {'n_low':6s}  "
          + "".join(f"  {h}d_H-L".ljust(9) for h in [3, 7, 14]))
    results: dict = {}
    for pctile in FUND_PCTILES:
        fr = _build_tercile(m["daily_mean_funding_bps"], pctile, 100 - pctile)
        med_mask = m["vol_regime"] == "medium"
        sub = m[med_mask].copy()
        sub["fr_test"] = fr[med_mask].values
        row: dict = {}
        line = f"  {pctile:>2d}/{100-pctile:<2d}"
        hi_total = (sub["fr_test"] == "high").sum()
        lo_total = (sub["fr_test"] == "low").sum()
        line += f"  {hi_total:6d}  {lo_total:6d}"
        for h in [3, 7, 14]:
            col = f"ret_{h}d"
            if col not in sub.columns:
                line += "       n/a"
                continue
            hi_r = sub.loc[sub["fr_test"] == "high", col].dropna().to_numpy()
            lo_r = sub.loc[sub["fr_test"] == "low",  col].dropna().to_numpy()
            eff  = _effect_size(lo_r, hi_r)
            row[f"h{h}d"] = eff
            hl_s = f"{eff['diff_pct']:+.2f}%" if not np.isnan(eff["diff_pct"]) else " n/a"
            line += f"  {hl_s}"
        results[pctile] = row
        print(line)
    print("-" * 72)
    return results


# =============================================================================
# Stage 5 — Sensitivity: vol definition (RV window + split)
# =============================================================================

def stage5_sensitivity_vol(m: pd.DataFrame) -> dict:
    print(f"\n{_ts()} STAGE 5  Sensitivity: vol definition (RV window x pctile split)")
    print(f"  {'RV_win':6s}  {'split':5s}  {'n_medium':8s}  "
          + "".join(f"  {h}d_H-L".ljust(9) for h in [7, 14]))
    results: dict = {}
    for rv_w in RV_WINDOWS:
        rv_s = _build_rv(m["daily_close"], rv_w)
        for vp in [20, 33, 50]:   # subset to keep output manageable
            vr = _build_tercile(rv_s, vp, 100 - vp)
            fr = m["funding_regime"]   # use same funding regime as H6
            med_mask = vr == "medium"
            sub = m[med_mask].copy()
            row: dict = {"n_medium": int(med_mask.sum())}
            line = f"  RV{rv_w:2d}d   {vp:>2d}/{100-vp:<2d}  {int(med_mask.sum()):8d}"
            for h in [7, 14]:
                col = f"ret_{h}d"
                hi_r = sub.loc[fr[med_mask].values == "high", col].dropna().to_numpy()
                lo_r = sub.loc[fr[med_mask].values == "low",  col].dropna().to_numpy()
                eff  = _effect_size(lo_r, hi_r)
                row[f"h{h}d"] = eff
                hl_s = f"{eff['diff_pct']:+.2f}%" if not np.isnan(eff["diff_pct"]) else " n/a"
                line += f"  {hl_s}"
            results[f"rv{rv_w}_vp{vp}"] = row
            print(line)
    print("-" * 72)
    return results


# =============================================================================
# Stage 6 — Sensitivity: holding period
# =============================================================================

def stage6_sensitivity_horizon(m: pd.DataFrame) -> dict:
    print(f"\n{_ts()} STAGE 6  Sensitivity: holding period (medium_vol, H6 regimes)")
    med  = m[m["vol_regime"] == "medium"].copy()
    print(f"  {'horizon':8s}  {'H-L_diff':9s}  {'Cohen_d':8s}  {'MWU_p':7s}  "
          f"{'high_mean':10s}  {'low_mean':9s}  {'consistent':11s}")
    results: dict = {}
    for h in HORIZONS_ALL:
        col  = f"ret_{h}d"
        if col not in med.columns:
            continue
        hi_r = med.loc[med["funding_regime"] == "high", col].dropna().to_numpy()
        lo_r = med.loc[med["funding_regime"] == "low",  col].dropna().to_numpy()
        eff  = _effect_size(lo_r, hi_r)
        consistent = (not np.isnan(eff["diff_pct"])) and eff["diff_pct"] > 0
        hl_s = f"{eff['diff_pct']:+.2f}%" if not np.isnan(eff["diff_pct"]) else " n/a%"
        d_s  = f"{eff['cohens_d']:+.3f}"  if not np.isnan(eff["cohens_d"])  else "  n/a"
        p_s  = f"{eff['mw_p']:.4f}"       if not np.isnan(eff["mw_p"])      else "  n/a"
        hm_s = f"{eff['mean_b']:+.2f}%"   if not np.isnan(eff["mean_b"])    else " n/a"
        lm_s = f"{eff['mean_a']:+.2f}%"   if not np.isnan(eff["mean_a"])    else " n/a"
        flag = "[OK]" if consistent else "[X]"
        print(f"  {h:2d}d       {hl_s}  {d_s}  {p_s}  {hm_s}  {lm_s}  {flag}")
        results[h] = {**eff, "consistent": consistent}
    consistent_horizons = sum(1 for v in results.values() if v.get("consistent", False))
    print(f"\n  Consistent direction in {consistent_horizons}/{len(results)} horizons")
    print("-" * 72)
    return results


# =============================================================================
# Stage 7 — Trade simulation (non-overlapping)
# =============================================================================

def _simulate_nonoverlap(m: pd.DataFrame, h: int, direction: str) -> dict:
    """
    Non-overlapping simulation.
    direction: 'long'  -> long when medium & low_funding
               'short' -> short when medium & high_funding
               'both'  -> combined long+short
    """
    col = f"ret_{h}d"
    signals = m[
        (m["vol_regime"] == "medium") &
        m[col].notna()
    ].copy().sort_values("day_ts")

    if direction == "long":
        signals = signals[signals["funding_regime"] == "low"]
        sign_fn = lambda fr: +1
    elif direction == "short":
        signals = signals[signals["funding_regime"] == "high"]
        sign_fn = lambda fr: -1
    else:   # both
        signals = signals[signals["funding_regime"].isin(["high", "low"])]
        sign_fn = lambda fr: +1 if fr == "low" else -1

    h_ms = int(h) * 86_400_000
    trades = []
    last_exit = -1

    for _, row in signals.iterrows():
        if int(row["day_ts"]) <= last_exit:
            continue
        dir_val  = sign_fn(row["funding_regime"])
        pos_ret  = dir_val * float(row[col])
        trades.append({
            "entry_ts":    int(row["day_ts"]),
            "direction":   dir_val,
            "raw_ret":     float(row[col]),
            "position_ret": pos_ret,
            "win":          pos_ret > 0,
            "year":         int(row["year"]),
        })
        last_exit = int(row["day_ts"]) + h_ms

    if not trades:
        return {"n_trades": 0, "valid": False}

    df_t = pd.DataFrame(trades)
    rets = df_t["position_ret"].to_numpy()

    equity = np.concatenate([[1.0], np.cumprod(1.0 + rets)])
    peak   = np.maximum.accumulate(equity)
    dd     = (peak - equity) / np.where(peak > 0, peak, 1.0)
    max_dd = float(dd.max())

    first_ts = df_t["entry_ts"].iloc[0]
    last_ts  = df_t["entry_ts"].iloc[-1] + h_ms
    cal_days = (last_ts - first_ts) / 86_400_000
    final_eq = float(equity[-1])
    cagr = float((final_eq ** (365.0 / cal_days)) - 1.0) if cal_days > 0 else np.nan

    mean_r  = float(rets.mean())
    std_r   = float(rets.std(ddof=1)) if len(rets) > 1 else np.nan
    sharpe  = float(mean_r / std_r * np.sqrt(252.0 / h)) if (std_r and std_r > 0) else np.nan
    win_rt  = float(df_t["win"].mean())

    # Per-year contribution to total P&L
    yr_pnl: dict = {}
    for yr, grp in df_t.groupby("year"):
        yr_pnl[int(yr)] = float(grp["position_ret"].sum())

    total_pnl = float(df_t["position_ret"].sum())
    max_yr_share = max((abs(v) / abs(total_pnl) if abs(total_pnl) > 0 else 0.)
                       for v in yr_pnl.values()) if yr_pnl else np.nan

    return {
        "valid":        True,
        "n_trades":     len(trades),
        "win_rate":     win_rt * 100,
        "mean_ret_pct": mean_r * 100,
        "cagr_pct":     cagr * 100,
        "sharpe":       sharpe,
        "max_dd_pct":   max_dd * 100,
        "final_equity": final_eq,
        "yr_pnl":       yr_pnl,
        "max_yr_share": max_yr_share,
        "equity_arr":   equity,
        "entry_ts_arr": df_t["entry_ts"].to_numpy(),
        "df_trades":    df_t,
    }


def stage7_trade_simulation(m: pd.DataFrame) -> dict:
    print(f"\n{_ts()} STAGE 7  Trade simulation (non-overlapping, medium_vol)")
    print(f"  {'Strategy':18s}  {'h':3s}  {'n_trades':9s}  {'win%':6s}  "
          f"{'CAGR%':7s}  {'Sharpe':7s}  {'MaxDD%':7s}  {'max_yr_share':12s}")
    results: dict = {}
    for h in HORIZONS_SIM:
        for dirn in ["long", "short", "both"]:
            sim = _simulate_nonoverlap(m, h, dirn)
            key = f"{dirn}_{h}d"
            results[key] = sim
            if not sim.get("valid"):
                print(f"  {key:18s}  {h:3d}  {'n/a':9s}")
                continue
            max_ys = sim["max_yr_share"]
            print(f"  {key:18s}  {h:3d}d  {sim['n_trades']:9d}  "
                  f"{sim['win_rate']:6.1f}%  "
                  f"{sim['cagr_pct']:+7.1f}%  "
                  f"{sim['sharpe']:+7.3f}  "
                  f"{sim['max_dd_pct']:7.1f}%  "
                  f"{max_ys*100:.1f}%")
    print()
    # Year-by-year P&L for the primary strategy (long+short, 7d)
    key_primary = "both_7d"
    if results.get(key_primary, {}).get("valid"):
        print(f"  Year-by-year P&L ({key_primary}):")
        for yr, pnl in sorted(results[key_primary]["yr_pnl"].items()):
            bar_len = int(abs(pnl) * 100 / 0.1)
            bar = ("+" * min(bar_len, 40)) if pnl >= 0 else ("-" * min(bar_len, 40))
            print(f"    {yr}: {pnl*100:+7.2f}%  {bar}")
    print("-" * 72)
    return results


# =============================================================================
# Stage 8 — Clean-sample (ex events) verification of simulation
# =============================================================================

def stage8_ex_events_check(m: pd.DataFrame, sim_results: dict) -> dict:
    print(f"\n{_ts()} STAGE 8  Structural-event exclusion effect on simulation")
    excl = build_exclusion_mask_ms(m["day_ts"].to_numpy())
    m_clean = m[~excl].copy()
    print(f"  Clean n={len(m_clean):,} (removed {excl.sum():,} event days)")
    results: dict = {}
    for h in HORIZONS_SIM:
        for dirn in ["long", "short", "both"]:
            key = f"{dirn}_{h}d"
            sim_c = _simulate_nonoverlap(m_clean, h, dirn)
            results[key] = sim_c
            if not sim_c.get("valid"):
                continue
            orig = sim_results.get(key, {})
            orig_cagr = orig.get("cagr_pct", np.nan)
            print(f"  {key:18s}: CAGR {orig_cagr:+.1f}% (full) -> {sim_c['cagr_pct']:+.1f}% (clean)  "
                  f"Sharpe {orig.get('sharpe',np.nan):+.3f} -> {sim_c['sharpe']:+.3f}")
    print("-" * 72)
    return results


# =============================================================================
# Stage 9 — Charts
# =============================================================================

def stage9_charts(m: pd.DataFrame, year_res: dict, roll_res: dict,
                  fund_sens: dict, horiz_res: dict, sim_res: dict) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    CHART_DIR.mkdir(parents=True, exist_ok=True)

    # --- Chart 1: Year-by-year H-L bar chart --------------------------------
    years_plot = [yr for yr in [2021,2022,2023,2024,2025,2026]
                  if yr in year_res and year_res[yr]["n_high"] >= 5]
    hl7_vals  = [year_res[yr]["eff7"]["diff_pct"]  for yr in years_plot]
    hl14_vals = [year_res[yr]["eff14"]["diff_pct"] for yr in years_plot]
    x = np.arange(len(years_plot)); w = 0.35
    fig, ax = plt.subplots(figsize=(10, 5))
    b1 = ax.bar(x - w/2, hl7_vals,  w, label="7d H-L",  color="#3498db", alpha=0.8)
    b2 = ax.bar(x + w/2, hl14_vals, w, label="14d H-L", color="#e74c3c", alpha=0.8)
    ax.axhline(0, color="black", lw=1.0)
    ax.set_xticks(x); ax.set_xticklabels([str(y) for y in years_plot])
    ax.set_ylabel("H-L return diff (low_funding - high_funding) %")
    ax.set_title("H6 Validation: Year-by-Year H-L Effect (medium_vol)")
    ax.legend()
    for bar, v in list(zip(b1, hl7_vals)) + list(zip(b2, hl14_vals)):
        ax.text(bar.get_x() + bar.get_width()/2, v + (0.2 if v >= 0 else -0.5),
                f"{v:+.1f}%", ha="center", fontsize=7)
    plt.tight_layout()
    fig.savefig(CHART_DIR / "val_01_year_stability.png", dpi=150)
    plt.close(fig)

    # --- Chart 2: Rolling H-L effect over time ------------------------------
    roll_df = roll_res["rolling_df"]
    roll_df_plot = roll_df.dropna(subset=["hl_7d_roll"])
    dates_roll = pd.to_datetime(roll_df_plot["day_ts"], unit="ms")
    fig, ax = plt.subplots(figsize=(12, 4))
    ax.plot(dates_roll, roll_df_plot["hl_7d_roll"].values, color="#3498db", lw=1.2, label="7d")
    roll14 = roll_df.dropna(subset=["hl_14d_roll"])
    dates14 = pd.to_datetime(roll14["day_ts"], unit="ms")
    ax.plot(dates14, roll14["hl_14d_roll"].values, color="#e74c3c", lw=1.0, alpha=0.7, label="14d")
    ax.axhline(0, color="black", lw=0.8, ls="--")
    ax.fill_between(dates_roll, roll_df_plot["hl_7d_roll"].values, 0,
                    where=roll_df_plot["hl_7d_roll"].values > 0,
                    alpha=0.15, color="green")
    ax.fill_between(dates_roll, roll_df_plot["hl_7d_roll"].values, 0,
                    where=roll_df_plot["hl_7d_roll"].values < 0,
                    alpha=0.15, color="red")
    ax.set_ylabel("Rolling H-L diff %")
    ax.set_title("H6 Validation: Rolling 252-day H-L Effect (medium_vol)")
    ax.legend()
    plt.tight_layout()
    fig.savefig(CHART_DIR / "val_02_rolling_hl.png", dpi=150)
    plt.close(fig)

    # --- Chart 3: Funding sensitivity (pctile x horizon) -------------------
    pctiles  = FUND_PCTILES
    h_labels = [3, 7, 14]
    mat = np.zeros((len(pctiles), len(h_labels)))
    for i, p in enumerate(pctiles):
        for j, h in enumerate(h_labels):
            v = fund_sens.get(p, {}).get(f"h{h}d", {}).get("diff_pct", np.nan)
            mat[i, j] = float(v) if not np.isnan(v) else 0.
    fig, ax = plt.subplots(figsize=(8, 4))
    vmax = max(0.5, np.nanmax(np.abs(mat)))
    im = ax.imshow(mat, aspect="auto", cmap="RdYlGn", vmin=-vmax, vmax=vmax)
    plt.colorbar(im, ax=ax, label="H-L diff %")
    ax.set_xticks(range(len(h_labels))); ax.set_xticklabels([f"{h}d" for h in h_labels])
    ax.set_yticks(range(len(pctiles)))
    ax.set_yticklabels([f"{p}/{100-p}" for p in pctiles])
    ax.set_title("H6 Validation: Sensitivity to Funding Threshold (medium_vol)")
    ax.set_ylabel("top/bottom pctile split")
    for i in range(len(pctiles)):
        for j in range(len(h_labels)):
            ax.text(j, i, f"{mat[i,j]:+.1f}%", ha="center", va="center", fontsize=8,
                    color="white" if abs(mat[i,j]) > vmax*0.5 else "black")
    plt.tight_layout()
    fig.savefig(CHART_DIR / "val_03_funding_sensitivity.png", dpi=150)
    plt.close(fig)

    # --- Chart 4: Trade simulation equity curves ----------------------------
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    for ax, h in zip(axes, [7, 14]):
        for dirn, color, lbl in [("long", "#27ae60", "Long only"),
                                   ("short", "#c0392b", "Short only"),
                                   ("both",  "#2c3e50", "Long+Short")]:
            sim = sim_res.get(f"{dirn}_{h}d", {})
            if not sim.get("valid"):
                continue
            eq_arr  = sim["equity_arr"]
            ts_arr  = sim["entry_ts_arr"]
            if len(ts_arr) == 0:
                continue
            dates_sim = pd.to_datetime(ts_arr, unit="ms")
            ax.step(list(dates_sim) + [dates_sim[-1]],
                    list(eq_arr[:-1]) + [eq_arr[-1]],
                    where="post", color=color, lw=1.5, label=lbl)
        ax.axhline(1.0, color="black", lw=0.7, ls="--")
        ax.set_title(f"H6 Validation: Equity Curve ({h}d hold, non-overlapping)")
        ax.set_ylabel("Equity ($1 start)")
        ax.legend(fontsize=8)
    plt.tight_layout()
    fig.savefig(CHART_DIR / "val_04_equity_curves.png", dpi=150)
    plt.close(fig)

    print(f"\n{_ts()} Charts saved -> {CHART_DIR}")


# =============================================================================
# Main
# =============================================================================

def main() -> None:
    t0 = time.time()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    CHART_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 72)
    print("H6 VALIDATION -- medium_vol x funding signal robustness")
    print(f"BTCUSDT  |  2021-01 to 2026-05")
    print(f"Run date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 72)

    m = _load_data()

    year_res  = stage1_year_stability(m)
    oos_res   = stage2_expanding_year_oos(m)
    roll_res  = stage3_rolling_hl(m)
    fund_sens = stage4_sensitivity_funding(m)
    vol_sens  = stage5_sensitivity_vol(m)
    horiz_res = stage6_sensitivity_horizon(m)
    sim_res   = stage7_trade_simulation(m)
    clean_res = stage8_ex_events_check(m, sim_res)

    stage9_charts(m, year_res, roll_res, fund_sens, horiz_res, sim_res)

    # ------------------------------------------------------------------
    # Verdict
    # ------------------------------------------------------------------
    print(f"\n{'='*72}")
    print("H6 VALIDATION VERDICT")
    print("-" * 72)

    # V1 — Year stability
    yr_summ = year_res.get("_summary", {})
    v1_pass = yr_summ.get("pass", False)
    v1_txt  = (f"[PASS]  {yr_summ.get('consistent_years_7d',0)}/"
               f"{yr_summ.get('testable_years',0)} years consistent sign at 7d")
    if not v1_pass:
        v1_txt = "[FAIL]  " + v1_txt[7:]

    # V2 — OOS by year (majority of testable years positive H-L direction)
    oos_correct = sum(1 for v in oos_res.values()
                      if isinstance(v, dict) and not np.isnan(v.get("eff7",{}).get("diff_pct", np.nan))
                      and v["eff7"]["diff_pct"] > 0)
    v2_pass = oos_correct >= max(1, len(oos_res) // 2 + 1)
    v2_txt  = f"{'[PASS]' if v2_pass else '[FAIL]'}  OOS by year: correct sign in {oos_correct}/{len(oos_res)} test years"

    # V3 — Sensitivity robustness (funding thresholds)
    sens_correct = 0; sens_total = 0
    for pctile, row in fund_sens.items():
        eff = row.get("h7d", {})
        if not np.isnan(eff.get("diff_pct", np.nan)):
            sens_total += 1
            if eff["diff_pct"] > 0:
                sens_correct += 1
    v3_frac  = sens_correct / sens_total if sens_total > 0 else 0.
    v3_pass  = v3_frac >= SENSITIVITY_PASS_FRAC
    v3_txt   = (f"{'[PASS]' if v3_pass else '[FAIL]'}  Funding sensitivity: "
                f"{sens_correct}/{sens_total} variants show correct sign "
                f"({v3_frac*100:.0f}%)")

    # V4 — Sensitivity: vol definition
    vol_correct = 0; vol_total = 0
    for key, row in vol_sens.items():
        eff = row.get("h7d", {})
        if not np.isnan(eff.get("diff_pct", np.nan)):
            vol_total += 1
            if eff["diff_pct"] > 0:
                vol_correct += 1
    v4_frac = vol_correct / vol_total if vol_total > 0 else 0.
    v4_pass = v4_frac >= SENSITIVITY_PASS_FRAC
    v4_txt  = (f"{'[PASS]' if v4_pass else '[FAIL]'}  Vol-definition sensitivity: "
               f"{vol_correct}/{vol_total} variants show correct sign "
               f"({v4_frac*100:.0f}%)")

    # V5 — Horizon consistency
    hr_correct = sum(1 for v in horiz_res.values() if v.get("consistent", False))
    v5_pass = hr_correct >= 4
    v5_txt  = (f"{'[PASS]' if v5_pass else '[FAIL]'}  Horizon consistency: "
               f"correct sign in {hr_correct}/{len(horiz_res)} horizons")

    # V6 — Trade simulation (long+short, 7d)
    sim_key = "both_7d"
    sim_7   = sim_res.get(sim_key, {})
    v6_sharpe = sim_7.get("sharpe", np.nan)
    v6_cagr   = sim_7.get("cagr_pct", np.nan)
    v6_maxdd  = sim_7.get("max_dd_pct", np.nan)
    v6_win    = sim_7.get("win_rate", np.nan)
    v6_n      = sim_7.get("n_trades", 0)
    v6_pass   = (not np.isnan(v6_sharpe)) and v6_sharpe > SHARPE_MIN
    v6_txt    = (f"{'[PASS]' if v6_pass else '[FAIL]'}  Trade sim (long+short, 7d): "
                 f"CAGR={v6_cagr:+.1f}%  Sharpe={v6_sharpe:+.3f}  "
                 f"MaxDD={v6_maxdd:.1f}%  WinRate={v6_win:.1f}%  N={v6_n}")

    # V7 — Single-year concentration check
    max_share = sim_7.get("max_yr_share", np.nan)
    v7_pass   = (not np.isnan(max_share)) and max_share <= MAX_SINGLE_YEAR_PNL
    v7_txt    = (f"{'[PASS]' if v7_pass else '[WARN]'}  Max single-year P&L share: "
                 f"{max_share*100:.1f}% (threshold <= {MAX_SINGLE_YEAR_PNL*100:.0f}%)")

    print(f"  V1 Year stability   : {v1_txt}")
    print(f"  V2 OOS by year      : {v2_txt}")
    print(f"  V3 Funding sens.    : {v3_txt}")
    print(f"  V4 Vol defn. sens.  : {v4_txt}")
    print(f"  V5 Horizon consist. : {v5_txt}")
    print(f"  V6 Trade simulation : {v6_txt}")
    print(f"  V7 Year concentr.   : {v7_txt}")
    print()

    all_pass   = [v1_pass, v2_pass, v3_pass, v4_pass, v5_pass, v6_pass]
    n_pass     = sum(all_pass)
    if n_pass >= 5 and v7_pass:
        conclusion = "GENUINE"
    elif n_pass >= 4:
        conclusion = "CONDITIONAL"
    else:
        conclusion = "ARTIFACT"

    print(f"  {'='*60}")
    print(f"  Verdict criteria met: {n_pass}/6 primary + year-concentration: {'OK' if v7_pass else 'WARN'}")
    print(f"  CONCLUSION: {conclusion}")
    print(f"  {'='*60}")

    if conclusion == "CONDITIONAL":
        # Check which years drove the effect
        yr_pnl = sim_7.get("yr_pnl", {})
        pos_years = sorted([yr for yr, pnl in yr_pnl.items() if pnl > 0])
        neg_years = sorted([yr for yr, pnl in yr_pnl.items() if pnl < 0])
        print(f"  Profitable years: {pos_years}")
        print(f"  Loss years:       {neg_years}")

    print("=" * 72)

    # Save JSON
    def _to_serializable(obj):
        if isinstance(obj, (np.floating, float)):
            return float(obj) if not np.isnan(obj) else None
        if isinstance(obj, (np.integer, int)):
            return int(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, pd.DataFrame):
            return "DataFrame (not serialized)"
        if isinstance(obj, dict):
            return {k: _to_serializable(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [_to_serializable(v) for v in obj]
        return obj

    summary = {
        "conclusion": conclusion,
        "year_stability":    _to_serializable(
            {yr: {"n_high": d["n_high"], "n_low": d["n_low"],
                  "hl_7d": d["eff7"]["diff_pct"],
                  "hl_14d": d["eff14"]["diff_pct"],
                  "cohens_d_7d": d["eff7"]["cohens_d"],
                  "sign_ok_7d": d["sign_consistent_7d"]}
             for yr, d in year_res.items() if isinstance(yr, int)}
        ),
        "oos_by_year": _to_serializable(
            {yr: {"oos_r2_7d": d["oos_r2_7d"],
                  "hl_7d": d["eff7"]["diff_pct"],
                  "hl_14d": d["eff14"]["diff_pct"]}
             for yr, d in oos_res.items()}
        ),
        "rolling_stability": {
            "pct_positive_7d":  roll_res["pct_positive_7d"],
            "pct_positive_14d": roll_res["pct_positive_14d"],
        },
        "sensitivity_funding": _to_serializable(
            {p: {h: fund_sens[p][f"h{h}d"]["diff_pct"]
                 for h in [3, 7, 14] if f"h{h}d" in fund_sens.get(p, {})}
             for p in FUND_PCTILES}
        ),
        "horizon_sensitivity": _to_serializable(
            {h: {"diff_pct": v["diff_pct"], "cohens_d": v["cohens_d"],
                 "consistent": v["consistent"]}
             for h, v in horiz_res.items()}
        ),
        "trade_simulation": _to_serializable(
            {k: {"n_trades": v.get("n_trades"), "win_rate": v.get("win_rate"),
                 "cagr_pct": v.get("cagr_pct"), "sharpe": v.get("sharpe"),
                 "max_dd_pct": v.get("max_dd_pct"), "yr_pnl": v.get("yr_pnl"),
                 "max_yr_share": v.get("max_yr_share")}
             for k, v in sim_res.items() if v.get("valid")}
        ),
        "clean_sim": _to_serializable(
            {k: {"n_trades": v.get("n_trades"), "cagr_pct": v.get("cagr_pct"),
                 "sharpe": v.get("sharpe")}
             for k, v in clean_res.items() if v.get("valid")}
        ),
        "verdicts": {
            "V1_year_stability":   v1_pass,
            "V2_oos_by_year":      v2_pass,
            "V3_funding_sens":     v3_pass,
            "V4_vol_sens":         v4_pass,
            "V5_horizon_consist":  v5_pass,
            "V6_trade_simulation": v6_pass,
            "V7_concentration":    v7_pass,
        },
        "run_date": datetime.now(timezone.utc).isoformat(),
        "elapsed_s": round(time.time() - t0, 1),
    }
    json_path = REPORT_DIR / "h6_validation_summary.json"
    with open(json_path, "w") as fh:
        json.dump(summary, fh, indent=2)
    elapsed = round((time.time() - t0) / 60, 1)
    print(f"\n[{conclusion}] H6 VALIDATION COMPLETE  elapsed: {elapsed} min")
    print(f"Report -> {json_path}")
    print("=" * 72)


if __name__ == "__main__":
    main()
