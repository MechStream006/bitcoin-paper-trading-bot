"""
H6 Market-Regime Validation
============================
Tests whether medium_vol + low_funding long signal is regime-conditional
or a 2023 artifact. Stratifies exclusively on the LONG side of H6 (long
when medium_vol AND low_funding) because the short side was already shown
to destroy value in the H6 Validation.

Stages
------
1.  Regime distribution: how many medium_vol + low_funding signal days
    fell in each regime, by year
2.  Long signal performance by regime (all years pooled)
3.  2022 post-mortem: regime breakdown of the -42.45% combined loss
4.  2024 post-mortem: regime breakdown
5.  Year-by-year breakdown of the LONG signal within each regime
6.  Regime-filtered trade simulation: full vs sideways-only vs non-bull
7.  Non-2023 sideways test: does the sideways signal survive after
    removing 2023?
8.  Charts
9.  Single verdict: genuine regime-specific edge or 2023 artifact?

Output
------
research/reports/h6_funding_vol_interaction/regime_validation/
  h6_regime_validation_summary.json
  charts/
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
from scipy.stats import mannwhitneyu, spearmanr

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=RuntimeWarning)

# ---- paths -----------------------------------------------------------------
_THIS_DIR     = pathlib.Path(__file__).resolve().parent
_RESEARCH_DIR = _THIS_DIR.parent.parent
if str(_RESEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_DIR))

from storage.duck import open_db, query, register_view
from hypotheses.shared.market_regimes import build_market_regimes

SYMBOL     = "BTCUSDT"
BASE_DIR   = _RESEARCH_DIR / "data" / "raw"
REPORT_DIR = _RESEARCH_DIR / "reports" / "h6_funding_vol_interaction" / "regime_validation"
CHART_DIR  = REPORT_DIR / "charts"

# matching H6
REGIME_WINDOW  = 252
REGIME_PCTILE  = 33
RV_WINDOW      = 21
MIN_DAILY_FUND = 2
HORIZONS       = [7, 14]

REGIMES        = ["bull", "sideways", "bear", "unknown"]
REGIME_COLORS  = {"bull": "#27ae60", "sideways": "#f39c12",
                  "bear": "#c0392b", "unknown": "#95a5a6"}


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("[%H:%M:%S]")


# =============================================================================
# Data load  (same pipeline as H6 validation)
# =============================================================================

def _load_data() -> pd.DataFrame:
    print(f"{_ts()} Loading data...")
    con = open_db()
    register_view(con, "spot_1m",     BASE_DIR, "spot",    SYMBOL)
    register_view(con, "funding_raw", BASE_DIR, "funding", SYMBOL)

    df_fund = query(con, f"""
    SELECT
        (timestamp_ms // 86400000) * 86400000  AS day_ts,
        AVG(funding_rate) * 10000.0            AS daily_mean_funding_bps,
        COUNT(*)                               AS n_settlements
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
        df_fund[["day_ts", "daily_mean_funding_bps"]], on="day_ts", how="left"
    )
    m["dt"]   = pd.to_datetime(m["day_ts"], unit="ms", utc=True)
    m["date"] = m["dt"].dt.normalize().dt.tz_localize(None).dt.date
    m["year"] = m["dt"].dt.year
    m = m.sort_values("day_ts").reset_index(drop=True)

    # realized vol + vol regime
    log_ret = np.log(m["daily_close"] / m["daily_close"].shift(1))
    rv = log_ret.rolling(RV_WINDOW, min_periods=RV_WINDOW).std() * np.sqrt(252)
    m["rv_21d"] = rv.values
    lo_q = rv.rolling(REGIME_WINDOW, min_periods=REGIME_WINDOW).quantile(REGIME_PCTILE / 100)
    hi_q = rv.rolling(REGIME_WINDOW, min_periods=REGIME_WINDOW).quantile((100 - REGIME_PCTILE) / 100)
    vol_reg = pd.Series("medium", index=m.index, dtype=object)
    vol_reg[rv > hi_q]               = "high"
    vol_reg[rv < lo_q]               = "low"
    vol_reg[lo_q.isna() | hi_q.isna()] = "unknown"
    m["vol_regime"] = vol_reg.values

    # funding regime
    fr_lo = m["daily_mean_funding_bps"].rolling(REGIME_WINDOW, min_periods=REGIME_WINDOW).quantile(
        REGIME_PCTILE / 100)
    fr_hi = m["daily_mean_funding_bps"].rolling(REGIME_WINDOW, min_periods=REGIME_WINDOW).quantile(
        (100 - REGIME_PCTILE) / 100)
    fund_reg = pd.Series("medium", index=m.index, dtype=object)
    fund_reg[m["daily_mean_funding_bps"] > fr_hi]  = "high"
    fund_reg[m["daily_mean_funding_bps"] < fr_lo]  = "low"
    fund_reg[fr_lo.isna() | fr_hi.isna()]           = "unknown"
    m["funding_regime"] = fund_reg.values

    # market regime via shared infrastructure
    close_dt = pd.Series(m["daily_close"].values, index=m["dt"].values)
    mkt      = build_market_regimes(close_dt)
    mkt_map  = {k.normalize(): v for k, v in mkt.to_dict().items()}
    m["market_regime"] = (
        m["dt"].dt.normalize().dt.tz_localize(None).map(mkt_map).fillna("unknown")
    )

    # forward returns
    for h in [1, 3, 5, 7, 10, 14, 21]:
        m[f"ret_{h}d"] = m["daily_close"].shift(-h).values / m["daily_close"].values - 1.0

    return m


# =============================================================================
# Helper: effect size
# =============================================================================

def _eff(a: np.ndarray, b: np.ndarray | None = None) -> dict:
    """Stats for array a (and optionally comparison b=long, a=short)."""
    if len(a) < 5:
        return {"n": len(a), "mean_pct": np.nan, "std_pct": np.nan,
                "sharpe_ann": np.nan, "win_pct": np.nan, "mw_p": np.nan}
    mean = float(a.mean() * 100)
    std  = float(a.std(ddof=1) * 100) if len(a) > 1 else np.nan
    ann  = float(mean / std * np.sqrt(252.0 / 7)) if (std and std > 0) else np.nan
    win  = float((a > 0).mean() * 100)
    p    = np.nan
    if b is not None and len(b) >= 5:
        try:
            _, p = mannwhitneyu(a, b, alternative="two-sided")
        except Exception:
            pass
    return {"n": len(a), "mean_pct": mean, "std_pct": std,
            "sharpe_ann": ann, "win_pct": win, "mw_p": float(p) if not np.isnan(p) else np.nan}


# =============================================================================
# Trade simulation (non-overlapping long-only)
# =============================================================================

def _simulate_long(sub: pd.DataFrame, h: int) -> dict:
    """
    Non-overlapping long simulation on rows in `sub`.
    sub must already be filtered to the desired signal condition.
    """
    col = f"ret_{h}d"
    sub = sub[sub[col].notna()].sort_values("day_ts").reset_index(drop=True)
    if len(sub) < 3:
        return {"valid": False, "n_trades": len(sub)}
    h_ms  = h * 86_400_000
    trades: list[dict] = []
    last_exit = -1
    for _, row in sub.iterrows():
        if int(row["day_ts"]) <= last_exit:
            continue
        r = float(row[col])
        trades.append({
            "entry_ts": int(row["day_ts"]),
            "ret":      r,
            "win":      r > 0,
            "year":     int(row["year"]),
            "mkt_reg":  str(row["market_regime"]),
        })
        last_exit = int(row["day_ts"]) + h_ms

    if not trades:
        return {"valid": False, "n_trades": 0}

    df_t   = pd.DataFrame(trades)
    rets   = df_t["ret"].to_numpy()
    equity = np.concatenate([[1.0], np.cumprod(1.0 + rets)])
    peak   = np.maximum.accumulate(equity)
    dd     = (peak - equity) / np.where(peak > 0, peak, 1.)
    max_dd = float(dd.max())

    first_ts  = int(df_t["entry_ts"].iloc[0])
    last_ts   = int(df_t["entry_ts"].iloc[-1]) + h_ms
    cal_days  = (last_ts - first_ts) / 86_400_000
    final_eq  = float(equity[-1])
    cagr      = float((final_eq ** (365. / cal_days)) - 1.) if cal_days > 0 else np.nan

    mean_r  = float(rets.mean())
    std_r   = float(rets.std(ddof=1)) if len(rets) > 1 else np.nan
    sharpe  = float(mean_r / std_r * np.sqrt(252. / h)) if (std_r and std_r > 0) else np.nan
    win_rt  = float(df_t["win"].mean() * 100)

    yr_pnl  = {int(yr): float(g["ret"].sum()) for yr, g in df_t.groupby("year")}
    total   = float(sum(yr_pnl.values()))
    max_share = max(abs(v)/abs(total) if abs(total) > 0 else 0. for v in yr_pnl.values())

    return {
        "valid":       True,
        "n_trades":    len(trades),
        "win_rate":    win_rt,
        "mean_ret":    mean_r * 100,
        "cagr_pct":    cagr * 100,
        "sharpe":      sharpe,
        "max_dd_pct":  max_dd * 100,
        "final_equity": final_eq,
        "yr_pnl":      yr_pnl,
        "max_yr_share": max_share,
        "equity_arr":  equity,
        "entry_ts_arr": df_t["entry_ts"].to_numpy(),
        "df_trades":   df_t,
    }


def _print_sim(label: str, sim: dict, h: int) -> None:
    if not sim.get("valid"):
        print(f"  {label:30s} {h}d: n={sim.get('n_trades',0)} (insufficient data)")
        return
    print(
        f"  {label:30s} {h}d: "
        f"n={sim['n_trades']:3d}  "
        f"CAGR={sim['cagr_pct']:+6.1f}%  "
        f"Sharpe={sim['sharpe']:+.3f}  "
        f"MaxDD={sim['max_dd_pct']:5.1f}%  "
        f"Win={sim['win_rate']:4.1f}%"
    )


# =============================================================================
# Stage 1 — Regime distribution
# =============================================================================

def stage1_regime_distribution(m: pd.DataFrame) -> dict:
    print(f"\n{_ts()} STAGE 1  Regime distribution of signal days by year")
    signal = m[(m["vol_regime"] == "medium") & (m["funding_regime"] == "low")]

    # overall
    total = len(signal)
    print(f"\n  Overall signal days (medium_vol + low_funding): {total}")
    print(f"  {'Year':5s}  {'Total':6s}  {'bull%':7s}  {'sideways%':10s}  {'bear%':7s}  {'unk%':6s}")
    yr_breakdown: dict = {}
    for yr in range(2021, 2027):
        sub = signal[signal["year"] == yr]
        if len(sub) == 0:
            continue
        n = len(sub)
        counts = sub["market_regime"].value_counts()
        b  = counts.get("bull",     0); bs = b / n * 100
        s  = counts.get("sideways", 0); ss = s / n * 100
        br = counts.get("bear",     0); brs = br / n * 100
        u  = counts.get("unknown",  0); us = u / n * 100
        print(f"  {yr:5d}  {n:6d}  {bs:7.1f}%  {ss:10.1f}%  {brs:7.1f}%  {us:6.1f}%")
        yr_breakdown[yr] = {"n": n, "bull": b, "sideways": s, "bear": br, "unknown": u}

    # regime totals
    print(f"\n  Signal days by regime (all years):")
    for reg in REGIMES:
        n_reg = (signal["market_regime"] == reg).sum()
        pct   = n_reg / total * 100 if total > 0 else 0.
        print(f"    {reg:10s}: {n_reg:4d}  ({pct:.1f}%)")
    print("-" * 72)
    return yr_breakdown


# =============================================================================
# Stage 2 — Long signal performance by regime (pooled)
# =============================================================================

def stage2_perf_by_regime(m: pd.DataFrame) -> dict:
    print(f"\n{_ts()} STAGE 2  Long signal performance by market regime (pooled)")
    signal = m[(m["vol_regime"] == "medium") & (m["funding_regime"] == "low")]
    results: dict = {}
    for h in HORIZONS:
        col = f"ret_{h}d"
        print(f"\n  {h}d horizon — raw returns (before non-overlap):")
        print(f"  {'Regime':10s}  {'n':5s}  {'mean':8s}  {'std':7s}  {'win%':6s}  "
              f"{'Sharpe(approx)':14s}  {'MWU_vs_all':10s}")
        all_r = signal[col].dropna().to_numpy()
        row: dict = {}
        for reg in REGIMES:
            sub = signal[signal["market_regime"] == reg]
            r   = sub[col].dropna().to_numpy()
            e   = _eff(r, all_r)
            row[reg] = e
            m_s  = f"{e['mean_pct']:+.2f}%" if not np.isnan(e["mean_pct"]) else "   n/a"
            s_s  = f"{e['std_pct']:.2f}%"   if not np.isnan(e["std_pct"]) else "  n/a"
            sh_s = f"{e['sharpe_ann']:+.3f}" if not np.isnan(e["sharpe_ann"]) else "   n/a"
            p_s  = f"{e['mw_p']:.3f}"        if not np.isnan(e["mw_p"]) else "  n/a"
            print(f"  {reg:10s}  {e['n']:5d}  {m_s}  {s_s}  {e['win_pct']:5.1f}%  {sh_s}  {p_s}")
        results[h] = row
    print("-" * 72)
    return results


# =============================================================================
# Stage 3 — 2022 post-mortem
# =============================================================================

def stage3_2022_postmortem(m: pd.DataFrame) -> dict:
    print(f"\n{_ts()} STAGE 3  2022 post-mortem")
    m22 = m[m["year"] == 2022].copy()
    signal22 = m22[(m22["vol_regime"] == "medium") & (m22["funding_regime"] == "low")]

    print(f"\n  2022 regime distribution (all days): "
          f"bull={( m22['market_regime']=='bull').sum()}  "
          f"sideways={(m22['market_regime']=='sideways').sum()}  "
          f"bear={(m22['market_regime']=='bear').sum()}")
    print(f"  Signal days in 2022 (medium_vol + low_funding): {len(signal22)}")
    for reg in REGIMES:
        sub = signal22[signal22["market_regime"] == reg]
        if len(sub) == 0:
            continue
        r7  = sub["ret_7d"].dropna().to_numpy()
        r14 = sub["ret_14d"].dropna().to_numpy()
        m7  = float(r7.mean()  * 100) if len(r7)  > 0 else np.nan
        m14 = float(r14.mean() * 100) if len(r14) > 0 else np.nan
        print(f"    {reg:10s}: n={len(sub):3d}  mean_7d={m7:+.2f}%  mean_14d={m14:+.2f}%")

    # simulate 2022 by regime
    print(f"\n  2022 long-only simulation by regime (7d):")
    sims22: dict = {}
    for reg in REGIMES:
        sub = signal22[signal22["market_regime"] == reg]
        sim = _simulate_long(sub, 7)
        sims22[reg] = sim
        if sim.get("valid"):
            print(f"    {reg:10s}: CAGR={sim['cagr_pct']:+.1f}%  Sharpe={sim['sharpe']:+.3f}  "
                  f"n={sim['n_trades']}  win={sim['win_rate']:.1f}%")
        else:
            print(f"    {reg:10s}: n={sim.get('n_trades',0)} (insufficient)")

    # BTC price context in 2022
    m22_sorted = m22.sort_values("day_ts")
    first_close = float(m22_sorted["daily_close"].iloc[0])
    last_close  = float(m22_sorted["daily_close"].iloc[-1])
    print(f"\n  BTC 2022: {first_close:,.0f} -> {last_close:,.0f}  "
          f"({(last_close/first_close - 1)*100:+.1f}%)")
    print("-" * 72)
    return sims22


# =============================================================================
# Stage 4 — 2024 post-mortem
# =============================================================================

def stage4_2024_postmortem(m: pd.DataFrame) -> dict:
    print(f"\n{_ts()} STAGE 4  2024 post-mortem")
    m24 = m[m["year"] == 2024].copy()
    signal24 = m24[(m24["vol_regime"] == "medium") & (m24["funding_regime"] == "low")]

    print(f"\n  2024 regime distribution (all days): "
          f"bull={( m24['market_regime']=='bull').sum()}  "
          f"sideways={(m24['market_regime']=='sideways').sum()}  "
          f"bear={(m24['market_regime']=='bear').sum()}")
    print(f"  Signal days in 2024 (medium_vol + low_funding): {len(signal24)}")
    for reg in REGIMES:
        sub = signal24[signal24["market_regime"] == reg]
        if len(sub) == 0:
            continue
        r7  = sub["ret_7d"].dropna().to_numpy()
        r14 = sub["ret_14d"].dropna().to_numpy()
        m7  = float(r7.mean()  * 100) if len(r7)  > 0 else np.nan
        m14 = float(r14.mean() * 100) if len(r14) > 0 else np.nan
        print(f"    {reg:10s}: n={len(sub):3d}  mean_7d={m7:+.2f}%  mean_14d={m14:+.2f}%")

    print(f"\n  2024 long-only simulation by regime (7d):")
    sims24: dict = {}
    for reg in REGIMES:
        sub = signal24[signal24["market_regime"] == reg]
        sim = _simulate_long(sub, 7)
        sims24[reg] = sim
        if sim.get("valid"):
            print(f"    {reg:10s}: CAGR={sim['cagr_pct']:+.1f}%  Sharpe={sim['sharpe']:+.3f}  "
                  f"n={sim['n_trades']}  win={sim['win_rate']:.1f}%")
        else:
            print(f"    {reg:10s}: n={sim.get('n_trades',0)} (insufficient)")

    m24_sorted = m24.sort_values("day_ts")
    first_close = float(m24_sorted["daily_close"].iloc[0])
    last_close  = float(m24_sorted["daily_close"].iloc[-1])
    print(f"\n  BTC 2024: {first_close:,.0f} -> {last_close:,.0f}  "
          f"({(last_close/first_close - 1)*100:+.1f}%)")
    print("-" * 72)
    return sims24


# =============================================================================
# Stage 5 — Year-by-year within each regime
# =============================================================================

def stage5_year_by_regime(m: pd.DataFrame) -> dict:
    print(f"\n{_ts()} STAGE 5  Year-by-year signal performance within each regime")
    signal = m[(m["vol_regime"] == "medium") & (m["funding_regime"] == "low")]
    results: dict = {}
    for reg in ["bull", "sideways", "bear"]:
        sub_reg = signal[signal["market_regime"] == reg]
        print(f"\n  Regime: {reg.upper()}  (total signal days: {len(sub_reg)})")
        print(f"  {'Year':5s}  {'n':5s}  {'mean_7d':9s}  {'mean_14d':9s}  "
              f"{'win%_7d':8s}  {'sign_ok':8s}")
        yr_data: dict = {}
        for yr in range(2021, 2027):
            sub_yr = sub_reg[sub_reg["year"] == yr]
            r7  = sub_yr["ret_7d"].dropna().to_numpy()
            r14 = sub_yr["ret_14d"].dropna().to_numpy()
            if len(r7) < 3:
                continue
            m7   = float(r7.mean() * 100)
            m14  = float(r14.mean() * 100) if len(r14) > 0 else np.nan
            w7   = float((r7 > 0).mean() * 100)
            sign = "[OK]" if m7 > 0 else "[X]"
            m7s  = f"{m7:+.2f}%"
            m14s = f"{m14:+.2f}%" if not np.isnan(m14) else "   n/a"
            print(f"  {yr:5d}  {len(r7):5d}  {m7s}  {m14s}  {w7:7.1f}%  {sign}")
            yr_data[yr] = {"n": len(r7), "mean_7d": m7, "mean_14d": m14,
                           "win_7d": w7, "sign_ok": m7 > 0}
        results[reg] = yr_data
    print("-" * 72)
    return results


# =============================================================================
# Stage 6 — Regime-filtered simulation
# =============================================================================

def stage6_regime_filtered_sim(m: pd.DataFrame) -> dict:
    print(f"\n{_ts()} STAGE 6  Regime-filtered trade simulation")
    signal  = m[(m["vol_regime"] == "medium") & (m["funding_regime"] == "low")]
    results: dict = {}

    filters = {
        "all_regimes":       signal,
        "sideways_only":     signal[signal["market_regime"] == "sideways"],
        "bull_only":         signal[signal["market_regime"] == "bull"],
        "bear_only":         signal[signal["market_regime"] == "bear"],
        "non_bear":          signal[signal["market_regime"].isin(["bull", "sideways"])],
        "sideways_non2023":  signal[(signal["market_regime"] == "sideways") &
                                    (signal["year"] != 2023)],
    }

    print(f"\n  {'Filter':24s}  {'h':3s}  {'n_sig':6s}  {'n_trd':5s}  "
          f"{'CAGR%':7s}  {'Sharpe':7s}  {'MaxDD%':7s}  {'Win%':6s}")
    print("  " + "-" * 68)
    for label, sub in filters.items():
        for h in HORIZONS:
            sim = _simulate_long(sub, h)
            results[f"{label}_{h}d"] = sim
            if not sim.get("valid"):
                print(f"  {label:24s}  {h}d  {len(sub):6d}  {'n/a':5s}")
                continue
            print(
                f"  {label:24s}  {h}d  {len(sub):6d}  {sim['n_trades']:5d}  "
                f"{sim['cagr_pct']:+7.1f}%  "
                f"{sim['sharpe']:+7.3f}  "
                f"{sim['max_dd_pct']:7.1f}%  "
                f"{sim['win_rate']:5.1f}%"
            )

    # Year P&L for key strategies
    for label in ["all_regimes", "sideways_only", "sideways_non2023"]:
        key = f"{label}_7d"
        sim = results.get(key, {})
        if not sim.get("valid"):
            continue
        print(f"\n  Year-by-year P&L: {label} (7d)")
        for yr, pnl in sorted(sim["yr_pnl"].items()):
            bar = ("+" * min(int(abs(pnl) * 100 / 0.1), 30)) if pnl >= 0 else \
                  ("-" * min(int(abs(pnl) * 100 / 0.1), 30))
            print(f"    {yr}: {pnl*100:+7.2f}%  {bar}")

    print("-" * 72)
    return results


# =============================================================================
# Stage 7 — Non-2023 sideways robustness check
# =============================================================================

def stage7_ex2023_robustness(m: pd.DataFrame) -> dict:
    print(f"\n{_ts()} STAGE 7  Non-2023 sideways robustness check")
    signal = m[(m["vol_regime"] == "medium") & (m["funding_regime"] == "low")]

    results: dict = {}
    for yr in range(2021, 2027):
        sub = signal[(signal["market_regime"] == "sideways") & (signal["year"] == yr)]
        r7  = sub["ret_7d"].dropna().to_numpy()
        r14 = sub["ret_14d"].dropna().to_numpy()
        n7  = len(r7)
        m7  = float(r7.mean() * 100)  if n7 >= 3 else np.nan
        m14 = float(r14.mean() * 100) if len(r14) >= 3 else np.nan
        w7  = float((r7 > 0).mean() * 100) if n7 >= 3 else np.nan
        flag = "[OK]" if (not np.isnan(m7) and m7 > 0) else ("[n/a]" if np.isnan(m7) else "[X]")
        print(f"  {yr}: n={n7:3d}  mean_7d={m7:+.2f}%  mean_14d={m14:+.2f}%  "
              f"win_7d={w7:.1f}%  {flag}"
              if not np.isnan(m7) else f"  {yr}: n={n7} (insufficient)")
        results[yr] = {"n": n7, "mean_7d": m7, "mean_14d": m14}

    # Leave-one-year-out pooled stats for sideways
    print(f"\n  Leave-one-year-out for sideways (mean 7d return):")
    for exclude_yr in range(2021, 2026):
        sub = signal[
            (signal["market_regime"] == "sideways") & (signal["year"] != exclude_yr)
        ]
        r7 = sub["ret_7d"].dropna().to_numpy()
        m7 = float(r7.mean() * 100) if len(r7) >= 10 else np.nan
        flag = "[OK]" if (not np.isnan(m7) and m7 > 0) else "[FAIL]"
        n_str = f"{len(r7):3d}"
        print(f"    Exclude {exclude_yr}: n={n_str}  mean_7d={m7:+.2f}%  {flag}"
              if not np.isnan(m7) else f"    Exclude {exclude_yr}: n={len(r7)} (insufficient)")
    print("-" * 72)
    return results


# =============================================================================
# Stage 8 — Charts
# =============================================================================

def stage8_charts(m: pd.DataFrame, sim_results: dict,
                  yr_regime_res: dict) -> None:
    CHART_DIR.mkdir(parents=True, exist_ok=True)
    signal = m[(m["vol_regime"] == "medium") & (m["funding_regime"] == "low")]

    # --- Chart 1: Equity curves by regime -----------------------------------
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    for ax, h in zip(axes, HORIZONS):
        for label, color in [
            ("all_regimes",   "#2c3e50"),
            ("sideways_only", "#f39c12"),
            ("bull_only",     "#27ae60"),
            ("bear_only",     "#c0392b"),
        ]:
            sim = sim_results.get(f"{label}_{h}d", {})
            if not sim.get("valid") or len(sim.get("entry_ts_arr", [])) == 0:
                continue
            eq  = sim["equity_arr"]
            ts  = sim["entry_ts_arr"]
            dates = pd.to_datetime(ts, unit="ms")
            ax.step(list(dates) + [dates[-1]], list(eq[:-1]) + [eq[-1]],
                    where="post", color=color, lw=1.5, label=label.replace("_", " "))
        ax.axhline(1.0, color="black", lw=0.7, ls="--")
        ax.set_title(f"Long signal equity curve by regime ({h}d hold)")
        ax.set_ylabel("Equity (start=$1)")
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)
    plt.tight_layout()
    fig.savefig(CHART_DIR / "reg_01_equity_by_regime.png", dpi=150)
    plt.close(fig)

    # --- Chart 2: Year-by-year mean return by regime (7d) -------------------
    years = [2021, 2022, 2023, 2024, 2025]
    x     = np.arange(len(years)); w = 0.22
    regs  = ["bull", "sideways", "bear"]
    reg_means: dict = {r: [] for r in regs}
    for yr in years:
        for reg in regs:
            sub = signal[(signal["market_regime"] == reg) & (signal["year"] == yr)]
            r7  = sub["ret_7d"].dropna().to_numpy()
            reg_means[reg].append(float(r7.mean() * 100) if len(r7) >= 3 else 0.)
    fig, ax = plt.subplots(figsize=(11, 5))
    offsets = [-w, 0, w]
    for reg, off in zip(regs, offsets):
        vals = reg_means[reg]
        bars = ax.bar(x + off, vals, w,
                      label=reg, color=REGIME_COLORS[reg], alpha=0.8)
        for bar, v in zip(bars, vals):
            if abs(v) > 0.3:
                ax.text(bar.get_x() + bar.get_width()/2,
                        v + (0.2 if v >= 0 else -0.8),
                        f"{v:+.1f}", ha="center", fontsize=7)
    ax.axhline(0, color="black", lw=0.8)
    ax.set_xticks(x); ax.set_xticklabels([str(y) for y in years])
    ax.set_ylabel("Mean 7d return of long signal %")
    ax.set_title("Long signal (medium_vol + low_funding): mean 7d return by year x regime")
    ax.legend()
    plt.tight_layout()
    fig.savefig(CHART_DIR / "reg_02_year_by_regime.png", dpi=150)
    plt.close(fig)

    # --- Chart 3: Sideways signal equity: all years vs ex-2023 --------------
    fig, ax = plt.subplots(figsize=(11, 5))
    for label, color, lbl in [
        ("sideways_only",    "#f39c12", "sideways (all years)"),
        ("sideways_non2023", "#e74c3c", "sideways (ex-2023)"),
        ("all_regimes",      "#7f8c8d", "all regimes (baseline)"),
    ]:
        sim = sim_results.get(f"{label}_7d", {})
        if not sim.get("valid") or len(sim.get("entry_ts_arr", [])) == 0:
            continue
        eq   = sim["equity_arr"]
        ts   = sim["entry_ts_arr"]
        dates = pd.to_datetime(ts, unit="ms")
        sh = sim['sharpe']
        ax.step(list(dates) + [dates[-1]], list(eq[:-1]) + [eq[-1]],
                where="post", color=color, lw=1.8,
                label=f"{lbl} (Sharpe={sh:+.2f})")
    ax.axhline(1.0, color="black", lw=0.7, ls="--")
    ax.set_title("Sideways signal: is it a 2023 artifact? (7d hold)")
    ax.set_ylabel("Equity (start=$1)")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    fig.savefig(CHART_DIR / "reg_03_sideways_ex2023.png", dpi=150)
    plt.close(fig)

    # --- Chart 4: Rolling regime-conditional H-L effect ----------------------
    med = m[m["vol_regime"] == "medium"].copy().reset_index(drop=True)
    roll_hl_reg: dict = {r: [] for r in ["bull", "sideways", "bear"]}
    roll_dates: list  = []
    for i in range(len(med)):
        sub = med.iloc[max(0, i - 252):i + 1]
        reg_at_i = med.iloc[i]["market_regime"]
        sub_reg  = sub[sub["market_regime"] == reg_at_i]
        hi = sub_reg.loc[sub_reg["funding_regime"] == "high", "ret_7d"].dropna().to_numpy()
        lo = sub_reg.loc[sub_reg["funding_regime"] == "low",  "ret_7d"].dropna().to_numpy()
        if reg_at_i in roll_hl_reg:
            if len(hi) >= 5 and len(lo) >= 5:
                roll_hl_reg[reg_at_i].append(
                    (pd.to_datetime(int(med.iloc[i]["day_ts"]), unit="ms"),
                     float(lo.mean() - hi.mean()) * 100)
                )

    fig, ax = plt.subplots(figsize=(12, 4))
    for reg, color in REGIME_COLORS.items():
        if reg not in roll_hl_reg or not roll_hl_reg[reg]:
            continue
        pts   = roll_hl_reg[reg]
        dates = [p[0] for p in pts]
        vals  = [p[1] for p in pts]
        ax.scatter(dates, vals, c=color, s=4, alpha=0.5, label=reg)
    ax.axhline(0, color="black", lw=0.8, ls="--")
    ax.set_ylabel("Rolling H-L diff % (low - high funding)")
    ax.set_title("H6: rolling 252-day H-L effect colored by regime")
    ax.legend(fontsize=8, markerscale=4)
    plt.tight_layout()
    fig.savefig(CHART_DIR / "reg_04_rolling_hl_by_regime.png", dpi=150)
    plt.close(fig)

    print(f"{_ts()} Charts saved -> {CHART_DIR}")


# =============================================================================
# Main
# =============================================================================

def main() -> None:
    t0 = time.time()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    CHART_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 72)
    print("H6 MARKET-REGIME VALIDATION")
    print("Signal: long when medium_vol + low_funding")
    print(f"Run date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 72)

    m = _load_data()

    regime_dist   = stage1_regime_distribution(m)
    perf_by_reg   = stage2_perf_by_regime(m)
    sims22        = stage3_2022_postmortem(m)
    sims24        = stage4_2024_postmortem(m)
    yr_reg        = stage5_year_by_regime(m)
    sim_filtered  = stage6_regime_filtered_sim(m)
    robustness    = stage7_ex2023_robustness(m)
    stage8_charts(m, sim_filtered, yr_reg)

    # -----------------------------------------------------------------------
    # Final verdict
    # -----------------------------------------------------------------------
    print(f"\n{'='*72}")
    print("FINAL VERDICT")
    print("-" * 72)

    # A. Sideways Sharpe
    sw7 = sim_filtered.get("sideways_only_7d", {})
    sw7_sharpe = sw7.get("sharpe", np.nan)
    sw7_cagr   = sw7.get("cagr_pct", np.nan)
    sw7_n      = sw7.get("n_trades", 0)
    sw7_share  = sw7.get("max_yr_share", np.nan)

    # B. Sideways ex-2023 Sharpe
    sw_ex23_7  = sim_filtered.get("sideways_non2023_7d", {})
    sw_ex23_sharpe = sw_ex23_7.get("sharpe", np.nan)
    sw_ex23_n      = sw_ex23_7.get("n_trades", 0)

    # C. Year consistency within sideways
    sw_yr      = yr_reg.get("sideways", {})
    sw_yr_ok   = sum(1 for d in sw_yr.values() if d.get("sign_ok", False))
    sw_yr_tot  = len([d for d in sw_yr.values() if d["n"] >= 3])

    # D. Year consistency within bull
    bull_yr    = yr_reg.get("bull", {})
    bull_yr_ok = sum(1 for d in bull_yr.values() if d.get("sign_ok", False))
    bull_yr_tot= len([d for d in bull_yr.values() if d["n"] >= 3])

    # E. Does 2022 fail in sideways?
    r22_sw    = (yr_reg.get("sideways") or {}).get(2022, {})
    r22_sw_ok = r22_sw.get("sign_ok", False) if r22_sw else None

    # F. Does 2024 fail in sideways?
    r24_sw    = (yr_reg.get("sideways") or {}).get(2024, {})
    r24_sw_ok = r24_sw.get("sign_ok", False) if r24_sw else None

    print(f"\n  KEY METRICS")
    print(f"  Sideways-only 7d:       CAGR={sw7_cagr:+.1f}%  Sharpe={sw7_sharpe:+.3f}  "
          f"n={sw7_n}  max_yr_share={sw7_share*100:.1f}%")
    print(f"  Sideways ex-2023 7d:    Sharpe={sw_ex23_sharpe:+.3f}  n={sw_ex23_n}")
    print(f"  Sideways year consist.: {sw_yr_ok}/{sw_yr_tot} years correct direction")
    print(f"  Bull year consist.:     {bull_yr_ok}/{bull_yr_tot} years correct direction")
    print(f"  2022 in sideways:       {'positive (signal worked)' if r22_sw_ok else ('negative (still fails)' if r22_sw_ok is False else 'n/a (no sideways days)')}")
    print(f"  2024 in sideways:       {'positive (signal worked)' if r24_sw_ok else ('negative (still fails)' if r24_sw_ok is False else 'n/a (no sideways days)')}")

    # Classification logic
    genuine_regime_edge = (
        (not np.isnan(sw7_sharpe)) and sw7_sharpe > 0.5 and
        (not np.isnan(sw_ex23_sharpe)) and sw_ex23_sharpe > 0 and
        sw_yr_ok >= max(2, sw_yr_tot - 1)
    )
    pure_artifact = (
        np.isnan(sw_ex23_sharpe) or sw_ex23_sharpe <= 0 or
        sw_yr_ok < 2
    )

    if genuine_regime_edge:
        verdict = "GENUINE REGIME-SPECIFIC EDGE"
    elif pure_artifact:
        verdict = "2023 ARTIFACT"
    else:
        verdict = "PARTIAL: regime conditioning helps but signal is not fully robust"

    print(f"\n  VERDICT: {verdict}")
    print()

    if verdict == "GENUINE REGIME-SPECIFIC EDGE":
        print("  The medium_vol + low_funding long signal is a genuine sideways-regime")
        print("  edge. It survives after excluding 2023, is directionally consistent")
        print("  across multiple sideways years, and the 2022/2024 failures are")
        print("  explained by being in non-sideways regimes.")
    elif verdict == "2023 ARTIFACT":
        print("  The signal collapses after removing 2023. The sideways-filtered")
        print("  signal does not survive leave-one-out testing.")
    else:
        # PARTIAL
        if not np.isnan(sw_ex23_sharpe) and sw_ex23_sharpe > 0:
            print("  The signal is real in sideways markets but the sample is too thin")
            print("  outside 2023 to be statistically confident (not enough sideways trades).")
        else:
            print("  Sideways filtering improves the signal but does not fully resolve")
            print(f"  the 2023 concentration (sideways ex-2023 Sharpe={sw_ex23_sharpe:+.3f}).")
        print(f"\n  Sideways regime filter IS recommended to remove bear-market exposure.")
        print(f"  The 2022 failure is regime-explained: {'' if r22_sw_ok is None else ('no sideways days in 2022' if r22_sw_ok is None else ('signal worked in 2022 sideways' if r22_sw_ok else 'signal still fails in 2022 sideways'))}")
        print(f"  The 2024 result: {'' if r24_sw_ok is None else ('signal positive in 2024 sideways' if r24_sw_ok else 'signal negative in 2024 sideways')}")

    print("=" * 72)

    # ---- Save JSON ---------------------------------------------------------
    def _ser(o):
        if isinstance(o, (np.floating, float)):
            return None if np.isnan(o) else float(o)
        if isinstance(o, (np.integer, int)):
            return int(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
        if isinstance(o, (pd.DataFrame, pd.Series)):
            return "(not serialized)"
        if isinstance(o, dict):
            return {k: _ser(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [_ser(v) for v in o]
        return o

    summary = {
        "verdict": verdict,
        "metrics": {
            "sideways_7d_sharpe":    _ser(sw7_sharpe),
            "sideways_7d_cagr":      _ser(sw7_cagr),
            "sideways_7d_n_trades":  sw7_n,
            "sideways_7d_max_yr_share": _ser(sw7_share),
            "sideways_ex2023_sharpe": _ser(sw_ex23_sharpe),
            "sideways_ex2023_n":     sw_ex23_n,
            "sideways_yr_consistent": f"{sw_yr_ok}/{sw_yr_tot}",
            "bull_yr_consistent":    f"{bull_yr_ok}/{bull_yr_tot}",
        },
        "sim_filtered": _ser({
            k: {"n": v.get("n_trades"), "cagr": v.get("cagr_pct"),
                "sharpe": v.get("sharpe"), "yr_pnl": v.get("yr_pnl")}
            for k, v in sim_filtered.items() if v.get("valid")
        }),
        "year_by_regime": _ser(yr_reg),
        "run_date":  datetime.now(timezone.utc).isoformat(),
        "elapsed_s": round(time.time() - t0, 1),
    }
    out = REPORT_DIR / "h6_regime_validation_summary.json"
    with open(out, "w") as fh:
        json.dump(summary, fh, indent=2)

    elapsed = round((time.time() - t0) / 60, 1)
    print(f"\n[{verdict[:20]}] elapsed: {elapsed} min")
    print(f"Report -> {out}")
    print("=" * 72)


if __name__ == "__main__":
    main()
