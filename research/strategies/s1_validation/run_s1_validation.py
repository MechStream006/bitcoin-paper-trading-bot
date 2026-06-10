"""
S1 Strategy Validation
======================
Signal : medium_vol (RV21d tercile) + low_funding (tercile) + market_regime != bear
Entry  : Long only, next open after signal day
Hold   : Non-overlapping 7d (primary) and 14d (secondary)

Stages
------
 1. Walk-forward  (rolling annual OOS folds)
 2. Expanding-window (cumulative by year)
 3. Transaction-cost sensitivity  (0 -> 100 bps round-trip)
 4. Slippage sensitivity           (0 -> 100 bps per side)
 5. Position-sizing study          (fixed / vol-target / fractional-Kelly)
 6. Risk-overlay study             (portfolio DD stop / per-trade stop)
 7. Exposure analysis              (time-in-market / regime mix / BTC correlation)
 8. Trade-distribution analysis    (histogram / tail / skew / outliers)

Metrics reported
----------------
  CAGR  Sharpe  Sortino  Calmar  MaxDD  WinRate  ProfitFactor  Trades

Verdict
-------
  PAPER-TRADING READY   : Sharpe > 1.5 post 15-bps cost, 3+ WF folds pass,
                           Calmar > 1.0, no single year > 55% of P&L
  INSUFFICIENT EVIDENCE : promising but < 30 trades OR < 3 WF folds
  NOT READY             : Sharpe < 0.5 post cost OR most WF folds fail
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
from scipy import stats

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=RuntimeWarning)

# ---- paths -----------------------------------------------------------------
_THIS_DIR     = pathlib.Path(__file__).resolve().parent
_RESEARCH_DIR = _THIS_DIR.parent.parent
if str(_RESEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_DIR))

from storage.duck import open_db, query, register_view
from hypotheses.shared.market_regimes import build_market_regimes

SYMBOL    = "BTCUSDT"
BASE_DIR  = _RESEARCH_DIR / "data" / "raw"
REPORT_DIR = _RESEARCH_DIR / "reports" / "s1_strategy_validation"
CHART_DIR  = REPORT_DIR / "charts"

# ---- signal parameters (locked from H6 research) ---------------------------
RV_WINDOW      = 21
REGIME_WINDOW  = 252
REGIME_PCTILE  = 33
MIN_DAILY_FUND = 2
H_PRIMARY      = 7
H_ALT          = 14

# ---- cost / sizing / risk constants ----------------------------------------
REALISTIC_COST_BPS   = 15    # round-trip bps for paper-trading threshold
TC_GRID_BPS          = [0, 5, 10, 15, 20, 30, 50, 75, 100]
SLIP_GRID_BPS        = [0, 5, 10, 15, 25, 50, 75, 100]
VOL_TARGET           = 0.10  # 10% annualized volatility target
KELLY_CAP            = 1.0   # max full-Kelly size
DD_STOP_PCTS         = [10, 15, 20, 25]   # portfolio trailing stop levels
TRADE_STOP_PCTS      = [5, 7, 10, 15, 20] # per-trade max loss cap


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("[%H:%M:%S]")


# =============================================================================
# Data loading (identical pipeline to H6 regime validation)
# =============================================================================

def _load_data() -> pd.DataFrame:
    print(f"{_ts()} Loading data...")
    con = open_db()
    register_view(con, "spot_1m",     BASE_DIR, "spot",    SYMBOL)
    register_view(con, "funding_raw", BASE_DIR, "funding", SYMBOL)

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
            close  AS last_close,
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
    fund_reg[m["daily_mean_funding_bps"] > fr_hi] = "high"
    fund_reg[m["daily_mean_funding_bps"] < fr_lo] = "low"
    fund_reg[fr_lo.isna() | fr_hi.isna()]         = "unknown"
    m["funding_regime"] = fund_reg.values

    # market regime
    close_dt = pd.Series(m["daily_close"].values, index=m["dt"].values)
    mkt      = build_market_regimes(close_dt)
    mkt_map  = {k.normalize(): v for k, v in mkt.to_dict().items()}
    m["market_regime"] = (
        m["dt"].dt.normalize().dt.tz_localize(None).map(mkt_map).fillna("unknown")
    )

    # forward returns
    for h in [7, 14, 21]:
        m[f"ret_{h}d"] = m["daily_close"].shift(-h).values / m["daily_close"].values - 1.0

    # daily BTC return for correlation
    m["btc_daily_ret"] = log_ret.values

    return m


def _get_signal(m: pd.DataFrame) -> pd.DataFrame:
    """Filter to S1 signal days: medium_vol + low_funding + non_bear."""
    mask = (
        (m["vol_regime"]     == "medium") &
        (m["funding_regime"] == "low")    &
        (~m["market_regime"].isin(["bear", "unknown"]))
    )
    return m[mask].copy().reset_index(drop=True)


# =============================================================================
# Core metrics
# =============================================================================

def _metrics(rets: np.ndarray, h: int, cal_days: float = None) -> dict:
    """Compute all 8 reporting metrics from a sequence of trade returns."""
    NAN = {k: np.nan for k in [
        "cagr_pct", "sharpe", "sortino", "calmar",
        "max_dd_pct", "win_rate_pct", "profit_factor", "n_trades",
    ]}
    n = len(rets)
    NAN["n_trades"] = n
    if n < 3:
        return NAN

    equity = np.concatenate([[1.0], np.cumprod(1.0 + rets)])
    peak   = np.maximum.accumulate(equity)
    dd     = (peak - equity) / np.where(peak > 0, peak, 1.0)
    max_dd = float(dd.max())

    if cal_days is None:
        cal_days = float(n * h)   # conservative (under-estimates CAGR)
    final_eq = float(equity[-1])
    cagr = float((final_eq ** (365.0 / cal_days)) - 1.0) if cal_days > 0 else np.nan

    mean_r  = float(rets.mean())
    std_r   = float(rets.std(ddof=1)) if n > 1 else np.nan
    ann     = float(np.sqrt(252.0 / h))
    sharpe  = float(mean_r / std_r * ann) if (std_r and std_r > 0) else np.nan

    neg = rets[rets < 0]
    if len(neg) >= 2:
        down_std = float(np.sqrt(np.mean(neg ** 2)))
        sortino = float(mean_r / down_std * ann) if down_std > 0 else np.nan
    else:
        sortino = np.nan

    calmar = float(cagr / max_dd) if max_dd > 0 else np.nan

    win_rate = float((rets > 0).mean() * 100)
    gp = float(rets[rets > 0].sum()) if (rets > 0).any() else 0.0
    gl = float(abs(rets[rets < 0].sum())) if (rets < 0).any() else 0.0
    pf = float(gp / gl) if gl > 0 else np.nan

    return {
        "cagr_pct":      cagr * 100,
        "sharpe":        sharpe,
        "sortino":       sortino,
        "calmar":        calmar,
        "max_dd_pct":    max_dd * 100,
        "win_rate_pct":  win_rate,
        "profit_factor": pf,
        "n_trades":      n,
    }


def _print_metrics(label: str, d: dict) -> None:
    def _f(v, fmt="+.2f"):
        return f"{v:{fmt}}" if not np.isnan(v) else "   n/a"
    print(
        f"  {label:36s}  "
        f"CAGR={_f(d['cagr_pct'])}%  "
        f"Sh={_f(d['sharpe'],'6.3f')}  "
        f"So={_f(d['sortino'],'6.3f')}  "
        f"Ca={_f(d['calmar'],'5.2f')}  "
        f"MDD={_f(d['max_dd_pct'],'.1f')}%  "
        f"Win={_f(d['win_rate_pct'],'.1f')}%  "
        f"PF={_f(d['profit_factor'],'.2f')}  "
        f"N={d['n_trades']}"
    )


# =============================================================================
# Core simulation
# =============================================================================

def _simulate(signal: pd.DataFrame, h: int,
              cost_bps: float = 0.0,
              slippage_bps: float = 0.0,
              sizing: str = "fixed",
              dd_stop_pct: float | None = None,
              trade_stop_pct: float | None = None) -> dict:
    """
    Non-overlapping long simulation with optional cost/sizing/risk overlays.

    sizing: 'fixed' | 'vol_target' | 'full_kelly' | 'half_kelly' | 'quarter_kelly'
    """
    col  = f"ret_{h}d"
    sub  = signal[signal[col].notna()].sort_values("day_ts").reset_index(drop=True)
    if len(sub) < 3:
        return {"valid": False, "n": len(sub), "metrics": _metrics(np.array([]), h)}

    total_cost_frac = (cost_bps + slippage_bps * 2.0) / 10_000.0
    h_ms            = h * 86_400_000

    trades: list[dict] = []
    last_exit   = -1
    cum_rets: list[float] = []    # for trailing Kelly

    portfolio_equity = 1.0
    portfolio_peak   = 1.0
    stopped          = False
    stopped_until_ts = -1

    for _, row in sub.iterrows():
        ts = int(row["day_ts"])
        if ts <= last_exit:
            continue
        if stopped and ts < stopped_until_ts:
            continue
        stopped = False

        raw = float(row[col])

        # per-trade stop: cap loss
        if trade_stop_pct is not None:
            raw = max(raw, -(trade_stop_pct / 100.0))

        # position sizing
        if sizing == "vol_target":
            rv = float(row["rv_21d"]) if not np.isnan(row["rv_21d"]) else VOL_TARGET
            size = float(np.clip(VOL_TARGET / rv, 0.1, 5.0)) if rv > 0 else 1.0
        elif sizing in ("full_kelly", "half_kelly", "quarter_kelly"):
            frac = {"full_kelly": 1.0, "half_kelly": 0.5, "quarter_kelly": 0.25}[sizing]
            if len(cum_rets) >= 10:
                arr = np.array(cum_rets)
                mu  = float(arr.mean())
                var = float(arr.var(ddof=1))
                fk  = mu / var if var > 0 else 0.0
                size = float(np.clip(fk * frac, 0.0, KELLY_CAP))
            else:
                size = 1.0  # warm-up period: fixed sizing
        else:
            size = 1.0

        net_ret = size * raw - total_cost_frac

        trades.append({
            "entry_ts": ts,
            "raw_ret":  raw,
            "size":     size,
            "net_ret":  net_ret,
            "win":      net_ret > 0,
            "year":     int(row["year"]),
            "mkt_reg":  str(row["market_regime"]),
            "rv_entry": float(row["rv_21d"]) if not np.isnan(row["rv_21d"]) else np.nan,
        })
        last_exit = ts + h_ms
        cum_rets.append(raw)

        # portfolio DD stop
        portfolio_equity *= (1.0 + net_ret)
        portfolio_peak    = max(portfolio_peak, portfolio_equity)
        if dd_stop_pct is not None:
            cur_dd = (portfolio_peak - portfolio_equity) / portfolio_peak
            if cur_dd >= dd_stop_pct / 100.0:
                stopped       = True
                # resume next calendar month after exit
                stopped_until_ts = ts + h_ms + 30 * 86_400_000
                portfolio_peak = portfolio_equity   # reset peak after cooldown

    if not trades:
        return {"valid": False, "n": 0, "metrics": _metrics(np.array([]), h)}

    df_t  = pd.DataFrame(trades)
    rets  = df_t["net_ret"].to_numpy()
    first = int(df_t["entry_ts"].iloc[0])
    last  = int(df_t["entry_ts"].iloc[-1]) + h_ms
    cal   = (last - first) / 86_400_000

    return {
        "valid":    True,
        "metrics":  _metrics(rets, h, cal),
        "df":       df_t,
        "rets":     rets,
        "equity":   np.concatenate([[1.0], np.cumprod(1.0 + rets)]),
        "entry_ts": df_t["entry_ts"].to_numpy(),
        "yr_pnl":   {int(yr): float(g["net_ret"].sum())
                     for yr, g in df_t.groupby("year")},
    }


# =============================================================================
# Stage 1 — Walk-forward (annual OOS folds)
# =============================================================================

def stage1_walkforward(m: pd.DataFrame, signal: pd.DataFrame) -> dict:
    print(f"\n{_ts()} STAGE 1  Walk-forward (annual OOS folds, no costs)")
    print(f"\n  Baseline (all years, non-bear, 7d):")
    base = _simulate(signal, H_PRIMARY)
    _print_metrics("Baseline", base["metrics"])

    print(f"\n  Annual OOS folds (7d hold):")
    hdr = (f"  {'Year':5s}  {'n_sig':5s}  {'n_trd':5s}  "
           f"{'CAGR%':7s}  {'Sharpe':7s}  {'MDD%':6s}  {'Win%':6s}  {'PF':5s}  {'Flag':6s}")
    print(hdr)
    results: dict = {}
    for yr in range(2021, 2027):
        fold = signal[signal["year"] == yr]
        sim  = _simulate(fold, H_PRIMARY)
        m_d  = sim["metrics"]
        n_sig = len(fold)
        flag  = ("[OK]" if m_d["sharpe"] > 0 else "[X]") if m_d["n_trades"] >= 5 else "[-]"
        cagr_s = f"{m_d['cagr_pct']:+.1f}%" if not np.isnan(m_d["cagr_pct"]) else "  n/a"
        sh_s   = f"{m_d['sharpe']:+.3f}"    if not np.isnan(m_d["sharpe"])    else "  n/a"
        mdd_s  = f"{m_d['max_dd_pct']:.1f}%" if not np.isnan(m_d["max_dd_pct"]) else " n/a"
        win_s  = f"{m_d['win_rate_pct']:.1f}%" if not np.isnan(m_d["win_rate_pct"]) else " n/a"
        pf_s   = f"{m_d['profit_factor']:.2f}" if not np.isnan(m_d["profit_factor"]) else " n/a"
        print(f"  {yr:5d}  {n_sig:5d}  {m_d['n_trades']:5d}  "
              f"{cagr_s}  {sh_s}  {mdd_s}  {win_s}  {pf_s}  {flag}")
        results[yr] = {"metrics": m_d, "flag": flag}

    testable = [yr for yr, d in results.items() if d["metrics"]["n_trades"] >= 5]
    n_pass   = sum(1 for yr in testable if results[yr]["flag"] == "[OK]")
    print(f"\n  Testable years: {testable}  Passing (Sharpe > 0): {n_pass}/{len(testable)}")
    print("-" * 80)
    return {"folds": results, "n_pass": n_pass, "n_testable": len(testable),
            "baseline": base["metrics"]}


# =============================================================================
# Stage 2 — Expanding-window (cumulative by year)
# =============================================================================

def stage2_expanding(m: pd.DataFrame, signal: pd.DataFrame) -> dict:
    print(f"\n{_ts()} STAGE 2  Expanding-window (cumulative metrics by year)")
    print(f"\n  {'Through':8s}  {'n_trd':5s}  {'CAGR%':7s}  {'Sharpe':7s}  "
          f"{'Sortino':8s}  {'Calmar':7s}  {'MDD%':6s}")
    results: dict = {}
    for through_yr in range(2021, 2027):
        sub = signal[signal["year"] <= through_yr]
        sim = _simulate(sub, H_PRIMARY)
        d   = sim["metrics"]
        cagr_s = f"{d['cagr_pct']:+.1f}%" if not np.isnan(d["cagr_pct"]) else "  n/a"
        sh_s   = f"{d['sharpe']:+.3f}"    if not np.isnan(d["sharpe"])    else "  n/a"
        so_s   = f"{d['sortino']:+.3f}"   if not np.isnan(d["sortino"])   else "  n/a"
        ca_s   = f"{d['calmar']:+.2f}"    if not np.isnan(d["calmar"])    else "  n/a"
        mdd_s  = f"{d['max_dd_pct']:.1f}%" if not np.isnan(d["max_dd_pct"]) else " n/a"
        print(f"  {through_yr:8d}  {d['n_trades']:5d}  {cagr_s}  {sh_s}  "
              f"{so_s}  {ca_s}  {mdd_s}")
        results[through_yr] = d
    print("-" * 80)
    return results


# =============================================================================
# Stage 3 — Transaction-cost sensitivity
# =============================================================================

def stage3_tc_sensitivity(signal: pd.DataFrame) -> dict:
    print(f"\n{_ts()} STAGE 3  Transaction-cost sensitivity (7d hold, no slippage)")
    print(f"\n  {'Cost(bps)':9s}  {'CAGR%':7s}  {'Sharpe':7s}  {'MDD%':6s}  "
          f"{'Win%':6s}  {'PF':5s}  {'N':4s}  {'Viable':6s}")
    results: dict = {}
    for bps in TC_GRID_BPS:
        sim = _simulate(signal, H_PRIMARY, cost_bps=bps)
        d   = sim["metrics"]
        viable = (not np.isnan(d["sharpe"])) and d["sharpe"] > 0.5
        cagr_s = f"{d['cagr_pct']:+.1f}%" if not np.isnan(d["cagr_pct"]) else " n/a"
        sh_s   = f"{d['sharpe']:+.3f}"    if not np.isnan(d["sharpe"])    else " n/a"
        mdd_s  = f"{d['max_dd_pct']:.1f}%" if not np.isnan(d["max_dd_pct"]) else " n/a"
        win_s  = f"{d['win_rate_pct']:.1f}%" if not np.isnan(d["win_rate_pct"]) else " n/a"
        pf_s   = f"{d['profit_factor']:.2f}" if not np.isnan(d["profit_factor"]) else " n/a"
        flag   = "[OK]" if viable else "[X]"
        print(f"  {bps:9d}  {cagr_s}  {sh_s}  {mdd_s}  {win_s}  {pf_s}  "
              f"{d['n_trades']:4d}  {flag}")
        results[bps] = d
    # breakeven
    break_bps = max((b for b, d in results.items()
                     if not np.isnan(d["sharpe"]) and d["sharpe"] > 0.5),
                    default=None)
    print(f"\n  Last viable cost level (Sharpe > 0.5): {break_bps} bps")
    print(f"  Realistic paper-trading cost ({REALISTIC_COST_BPS} bps):")
    _print_metrics(f"  cost={REALISTIC_COST_BPS}bps", results[REALISTIC_COST_BPS])
    print("-" * 80)
    return results


# =============================================================================
# Stage 4 — Slippage sensitivity
# =============================================================================

def stage4_slippage_sensitivity(signal: pd.DataFrame) -> dict:
    print(f"\n{_ts()} STAGE 4  Slippage sensitivity (7d hold, +{REALISTIC_COST_BPS}bps base cost)")
    print(f"\n  {'Slip/side(bps)':14s}  {'Total(bps)':10s}  {'Sharpe':7s}  "
          f"{'CAGR%':7s}  {'MDD%':6s}  {'Viable':6s}")
    results: dict = {}
    for slip in SLIP_GRID_BPS:
        total = REALISTIC_COST_BPS + slip * 2
        sim   = _simulate(signal, H_PRIMARY,
                          cost_bps=REALISTIC_COST_BPS, slippage_bps=slip)
        d     = sim["metrics"]
        viable = (not np.isnan(d["sharpe"])) and d["sharpe"] > 0.5
        sh_s   = f"{d['sharpe']:+.3f}"    if not np.isnan(d["sharpe"])    else " n/a"
        cagr_s = f"{d['cagr_pct']:+.1f}%" if not np.isnan(d["cagr_pct"]) else " n/a"
        mdd_s  = f"{d['max_dd_pct']:.1f}%" if not np.isnan(d["max_dd_pct"]) else " n/a"
        flag   = "[OK]" if viable else "[X]"
        print(f"  {slip:14d}  {total:10d}  {sh_s}  {cagr_s}  {mdd_s}  {flag}")
        results[slip] = d
    print("-" * 80)
    return results


# =============================================================================
# Stage 5 — Position-sizing study
# =============================================================================

def stage5_sizing(signal: pd.DataFrame) -> dict:
    print(f"\n{_ts()} STAGE 5  Position-sizing study (7d, {REALISTIC_COST_BPS}bps cost)")
    sizings = ["fixed", "vol_target", "full_kelly", "half_kelly", "quarter_kelly"]
    results: dict = {}
    print()
    for sz in sizings:
        sim = _simulate(signal, H_PRIMARY, cost_bps=REALISTIC_COST_BPS, sizing=sz)
        d   = sim["metrics"]
        results[sz] = d
        _print_metrics(sz, d)
    # 14d comparison for vol_target
    print(f"\n  14d hold comparison:")
    for sz in ["fixed", "vol_target", "half_kelly"]:
        sim = _simulate(signal, H_ALT, cost_bps=REALISTIC_COST_BPS, sizing=sz)
        d   = sim["metrics"]
        _print_metrics(f"{sz} (14d)", d)
    print("-" * 80)
    return results


# =============================================================================
# Stage 6 — Risk-overlay study
# =============================================================================

def stage6_risk_overlay(signal: pd.DataFrame) -> dict:
    print(f"\n{_ts()} STAGE 6  Risk-overlay study (7d, {REALISTIC_COST_BPS}bps cost)")
    results: dict = {}

    # baseline
    base = _simulate(signal, H_PRIMARY, cost_bps=REALISTIC_COST_BPS)
    results["baseline"] = base["metrics"]
    print(f"\n  Portfolio trailing drawdown stops:")
    _print_metrics("baseline (no stop)", base["metrics"])
    for dd_pct in DD_STOP_PCTS:
        sim = _simulate(signal, H_PRIMARY, cost_bps=REALISTIC_COST_BPS,
                        dd_stop_pct=dd_pct)
        d = sim["metrics"]
        results[f"dd_stop_{dd_pct}"] = d
        _print_metrics(f"portfolio DD stop {dd_pct}%", d)

    print(f"\n  Per-trade loss caps (max loss per trade):")
    for ts_pct in TRADE_STOP_PCTS:
        sim = _simulate(signal, H_PRIMARY, cost_bps=REALISTIC_COST_BPS,
                        trade_stop_pct=ts_pct)
        d = sim["metrics"]
        results[f"trade_stop_{ts_pct}"] = d
        _print_metrics(f"trade stop {ts_pct}%", d)

    print(f"\n  Combined: best sizing + trade stop:")
    for sz, ts_pct in [("vol_target", 10), ("half_kelly", 10),
                        ("vol_target", 15), ("half_kelly", 15)]:
        sim = _simulate(signal, H_PRIMARY, cost_bps=REALISTIC_COST_BPS,
                        sizing=sz, trade_stop_pct=ts_pct)
        d = sim["metrics"]
        key = f"{sz}+stop{ts_pct}"
        results[key] = d
        _print_metrics(f"{sz} + trade_stop {ts_pct}%", d)
    print("-" * 80)
    return results


# =============================================================================
# Stage 7 — Exposure analysis
# =============================================================================

def stage7_exposure(m: pd.DataFrame, signal: pd.DataFrame) -> dict:
    print(f"\n{_ts()} STAGE 7  Exposure analysis")
    sim   = _simulate(signal, H_PRIMARY, cost_bps=REALISTIC_COST_BPS)
    df_t  = sim.get("df")

    total_days = len(m)
    total_cal  = (int(m["day_ts"].iloc[-1]) - int(m["day_ts"].iloc[0])) / 86_400_000

    # time in market
    if df_t is not None and len(df_t) > 0:
        n_trades = len(df_t)
        days_in  = n_trades * H_PRIMARY
        pct_in   = days_in / total_cal * 100
    else:
        pct_in = 0.; days_in = 0; n_trades = 0

    print(f"\n  Universe: {total_days} calendar days "
          f"({total_cal:.0f} actual days, 2021-2026 YTD)")
    print(f"  Signal days (medium_vol + low_funding + non_bear): {len(signal)}")
    print(f"  Non-overlapping trades (7d): {n_trades}")
    print(f"  Days in market: ~{days_in} of {total_cal:.0f} calendar days "
          f"({pct_in:.1f}%)")

    # regime breakdown of signal days
    print(f"\n  Signal-day regime mix:")
    for reg in ["bull", "sideways"]:
        n_reg = (signal["market_regime"] == reg).sum()
        print(f"    {reg:10s}: {n_reg:4d}  ({n_reg/len(signal)*100:.1f}%)")

    # BTC buy-and-hold baseline
    btc_start = float(m["daily_close"].iloc[0])
    btc_end   = float(m["daily_close"].iloc[-1])
    btc_ret   = btc_end / btc_start - 1.0
    btc_cagr  = ((btc_end / btc_start) ** (365. / total_cal)) - 1.0
    print(f"\n  BTC buy-and-hold baseline ({total_cal:.0f} days):")
    print(f"    Total return: {btc_ret*100:+.1f}%  CAGR: {btc_cagr*100:+.1f}%")

    # BTC correlation of strategy returns vs BTC daily returns
    if df_t is not None and len(df_t) > 5:
        strat_yr_pnl = {yr: float(g["net_ret"].sum())
                        for yr, g in df_t.groupby("year")}
        btc_yr_ret   = {
            yr: float((m[m["year"] == yr]["btc_daily_ret"].dropna()).sum())
            for yr in strat_yr_pnl.keys()
        }
        common_yrs = sorted(set(strat_yr_pnl) & set(btc_yr_ret))
        if len(common_yrs) >= 3:
            sx = np.array([strat_yr_pnl[yr] for yr in common_yrs])
            sy = np.array([btc_yr_ret[yr]   for yr in common_yrs])
            r, p = stats.pearsonr(sx, sy)
            print(f"\n  Strategy vs BTC annual return correlation:")
            print(f"    Pearson r={r:+.3f}  p={p:.3f}  "
                  f"({'correlated' if abs(r) > 0.5 else 'independent'})")
        # year P&L vs BTC
        print(f"\n  Year P&L comparison:")
        print(f"  {'Year':5s}  {'S1_P&L':8s}  {'BTC_ret':8s}  {'same_sign':9s}")
        for yr in common_yrs:
            sp = strat_yr_pnl[yr] * 100
            bp = btc_yr_ret[yr] * 100
            same = "[OK]" if (sp >= 0) == (bp >= 0) else "[X]"
            print(f"  {yr:5d}  {sp:+8.2f}%  {bp:+8.2f}%  {same}")

    # monthly return heatmap (summary)
    if df_t is not None and len(df_t) > 0:
        df_t["entry_dt"] = pd.to_datetime(df_t["entry_ts"], unit="ms")
        df_t["month"]    = df_t["entry_dt"].dt.month
        print(f"\n  Monthly trade count distribution:")
        mnth_cnt = df_t.groupby("month").size()
        for mo in range(1, 13):
            cnt = mnth_cnt.get(mo, 0)
            bar = "." * cnt
            print(f"    Month {mo:2d}: {cnt:2d}  {bar}")

    print("-" * 80)
    return {"pct_in_market": pct_in, "btc_cagr": btc_cagr * 100,
            "btc_total": btc_ret * 100, "n_signal_days": len(signal)}


# =============================================================================
# Stage 8 — Trade-distribution analysis
# =============================================================================

def stage8_distribution(signal: pd.DataFrame) -> dict:
    print(f"\n{_ts()} STAGE 8  Trade-distribution analysis (7d, {REALISTIC_COST_BPS}bps cost)")
    sim  = _simulate(signal, H_PRIMARY, cost_bps=REALISTIC_COST_BPS)
    rets = sim.get("rets", np.array([]))
    df_t = sim.get("df")

    if len(rets) < 5:
        print("  Insufficient trades.")
        return {}

    print(f"\n  Distribution summary (n={len(rets)}):")
    print(f"    Mean   : {rets.mean()*100:+.2f}%")
    print(f"    Median : {np.median(rets)*100:+.2f}%")
    print(f"    Std    : {rets.std(ddof=1)*100:.2f}%")
    print(f"    Skew   : {float(stats.skew(rets)):+.3f}")
    print(f"    Kurtosis: {float(stats.kurtosis(rets)):+.3f}  "
          f"({'fat-tailed' if stats.kurtosis(rets) > 1 else 'normal-ish'})")
    print(f"    Min    : {rets.min()*100:+.2f}%")
    print(f"    Max    : {rets.max()*100:+.2f}%")
    print(f"    P5     : {np.percentile(rets,5)*100:+.2f}%")
    print(f"    P25    : {np.percentile(rets,25)*100:+.2f}%")
    print(f"    P75    : {np.percentile(rets,75)*100:+.2f}%")
    print(f"    P95    : {np.percentile(rets,95)*100:+.2f}%")

    # outlier trades (top and bottom 3)
    if df_t is not None:
        df_t["ret_pct"] = df_t["net_ret"] * 100
        top3 = df_t.nlargest(3, "net_ret")[["entry_ts", "year", "mkt_reg", "ret_pct"]]
        bot3 = df_t.nsmallest(3, "net_ret")[["entry_ts", "year", "mkt_reg", "ret_pct"]]
        print(f"\n  Top 3 trades:")
        for _, r in top3.iterrows():
            dt = pd.to_datetime(int(r["entry_ts"]), unit="ms").strftime("%Y-%m-%d")
            print(f"    {dt}  {r['year']}  {r['mkt_reg']:10s}  {r['ret_pct']:+.2f}%")
        print(f"  Bottom 3 trades:")
        for _, r in bot3.iterrows():
            dt = pd.to_datetime(int(r["entry_ts"]), unit="ms").strftime("%Y-%m-%d")
            print(f"    {dt}  {r['year']}  {r['mkt_reg']:10s}  {r['ret_pct']:+.2f}%")

    # what-if: remove top-1 trade
    if len(rets) > 3:
        rets_ex1 = np.delete(rets, np.argmax(rets))
        m_ex1    = _metrics(rets_ex1, H_PRIMARY)
        rets_ex3 = rets.copy()
        top3_idx = np.argsort(rets)[-3:]
        rets_ex3 = np.delete(rets, top3_idx)
        m_ex3    = _metrics(rets_ex3, H_PRIMARY)
        print(f"\n  Outlier sensitivity:")
        _print_metrics("ex-top-1-trade", m_ex1)
        _print_metrics("ex-top-3-trades", m_ex3)

    # normality test
    if len(rets) >= 8:
        _, p_sw = stats.shapiro(rets)
        print(f"\n  Shapiro-Wilk normality: p={p_sw:.4f}  "
              f"({'likely normal' if p_sw > 0.05 else 'non-normal distribution'})")

    print("-" * 80)
    return {
        "skew":     float(stats.skew(rets)),
        "kurtosis": float(stats.kurtosis(rets)),
        "p5":       float(np.percentile(rets, 5) * 100),
        "p95":      float(np.percentile(rets, 95) * 100),
    }


# =============================================================================
# Charts
# =============================================================================

def _make_charts(signal: pd.DataFrame) -> None:
    CHART_DIR.mkdir(parents=True, exist_ok=True)

    # --- Chart 1: equity curves (baseline, +cost, +vol_target) --------------
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    for ax, h in zip(axes, [H_PRIMARY, H_ALT]):
        for label, kw, color in [
            ("no cost",            dict(cost_bps=0),  "#2c3e50"),
            (f"{REALISTIC_COST_BPS}bps",
             dict(cost_bps=REALISTIC_COST_BPS),       "#27ae60"),
            ("vol_target",         dict(cost_bps=REALISTIC_COST_BPS,
                                        sizing="vol_target"), "#e67e22"),
            ("half_kelly",         dict(cost_bps=REALISTIC_COST_BPS,
                                        sizing="half_kelly"),  "#8e44ad"),
        ]:
            sim = _simulate(signal, h, **kw)
            if not sim.get("valid") or len(sim.get("entry_ts", [])) == 0:
                continue
            eq  = sim["equity"]
            ts  = sim["entry_ts"]
            d   = sim["metrics"]
            sh  = d["sharpe"]
            dates = pd.to_datetime(ts, unit="ms")
            ax.step(list(dates)+[dates[-1]], list(eq[:-1])+[eq[-1]],
                    where="post", color=color, lw=1.5,
                    label=f"{label} (Sh={sh:+.2f})")
        ax.axhline(1.0, color="black", lw=0.7, ls="--")
        ax.set_title(f"S1 equity curves ({h}d hold)")
        ax.set_ylabel("Equity ($1 start)")
        ax.legend(fontsize=8); ax.grid(alpha=0.3)
    plt.tight_layout()
    fig.savefig(CHART_DIR / "s1_01_equity_curves.png", dpi=150)
    plt.close(fig)

    # --- Chart 2: TC sensitivity (Sharpe vs cost) ---------------------------
    tc_sharpes  = []
    tc_bps_vals = TC_GRID_BPS
    for bps in tc_bps_vals:
        sim = _simulate(signal, H_PRIMARY, cost_bps=bps)
        tc_sharpes.append(sim["metrics"]["sharpe"])
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.plot(tc_bps_vals, tc_sharpes, "o-", color="#2980b9", lw=2)
    ax.axhline(0, color="red", lw=1.0, ls="--", label="break-even")
    ax.axhline(0.5, color="orange", lw=1.0, ls=":", label="min viable (0.5)")
    ax.axvline(REALISTIC_COST_BPS, color="green", lw=1.0, ls="--",
               label=f"realistic ({REALISTIC_COST_BPS}bps)")
    ax.set_xlabel("Round-trip transaction cost (bps)")
    ax.set_ylabel("Sharpe ratio")
    ax.set_title("S1: Sharpe vs transaction cost")
    ax.legend(fontsize=9); ax.grid(alpha=0.3)
    plt.tight_layout()
    fig.savefig(CHART_DIR / "s1_02_tc_sensitivity.png", dpi=150)
    plt.close(fig)

    # --- Chart 3: walk-forward fold performance ----------------------------
    years = list(range(2021, 2027))
    fold_sharpes = []
    fold_cagrs   = []
    fold_ns      = []
    for yr in years:
        sim = _simulate(signal[signal["year"] == yr], H_PRIMARY)
        d   = sim["metrics"]
        fold_sharpes.append(d["sharpe"] if not np.isnan(d["sharpe"]) else 0.)
        fold_cagrs.append(d["cagr_pct"] if not np.isnan(d["cagr_pct"]) else 0.)
        fold_ns.append(d["n_trades"])
    x = np.arange(len(years))
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 7), sharex=True)
    colors = ["#27ae60" if v >= 0 else "#c0392b" for v in fold_sharpes]
    ax1.bar(x, fold_sharpes, color=colors, alpha=0.8)
    ax1.axhline(0, color="black", lw=0.8)
    ax1.set_ylabel("Sharpe (per fold)"); ax1.set_title("S1 walk-forward: per-year Sharpe")
    ax1.grid(alpha=0.3)
    for i, (v, n) in enumerate(zip(fold_sharpes, fold_ns)):
        ax1.text(i, v + (0.05 if v >= 0 else -0.15), f"n={n}", ha="center", fontsize=8)
    colors2 = ["#27ae60" if v >= 0 else "#c0392b" for v in fold_cagrs]
    ax2.bar(x, fold_cagrs, color=colors2, alpha=0.8)
    ax2.axhline(0, color="black", lw=0.8)
    ax2.set_xticks(x); ax2.set_xticklabels([str(y) for y in years])
    ax2.set_ylabel("CAGR %"); ax2.grid(alpha=0.3)
    plt.tight_layout()
    fig.savefig(CHART_DIR / "s1_03_walkforward_folds.png", dpi=150)
    plt.close(fig)

    # --- Chart 4: return distribution histogram -----------------------------
    sim  = _simulate(signal, H_PRIMARY, cost_bps=REALISTIC_COST_BPS)
    rets = sim.get("rets", np.array([]))
    if len(rets) >= 5:
        fig, ax = plt.subplots(figsize=(9, 4))
        ax.hist(rets * 100, bins=15, color="#3498db", edgecolor="white", alpha=0.8)
        ax.axvline(0, color="black", lw=1.5, ls="--")
        ax.axvline(float(np.mean(rets) * 100), color="green", lw=1.5,
                   label=f"mean={np.mean(rets)*100:+.2f}%")
        ax.set_xlabel("7d net return %")
        ax.set_ylabel("Trade count")
        ax.set_title(f"S1 return distribution (n={len(rets)}, {REALISTIC_COST_BPS}bps cost)")
        ax.legend(); ax.grid(alpha=0.3)
        plt.tight_layout()
        fig.savefig(CHART_DIR / "s1_04_return_distribution.png", dpi=150)
        plt.close(fig)

    print(f"{_ts()} Charts saved -> {CHART_DIR}")


# =============================================================================
# Main
# =============================================================================

def main() -> None:
    t0 = time.time()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    CHART_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 80)
    print("S1 STRATEGY VALIDATION")
    print("Signal: medium_vol (RV21d) + low_funding + market_regime != bear")
    print(f"Run date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 80)

    m      = _load_data()
    signal = _get_signal(m)
    print(f"{_ts()} Signal days: {len(signal)} "
          f"(of {len(m)} total | {len(signal)/len(m)*100:.1f}% signal frequency)")

    wf_res    = stage1_walkforward(m, signal)
    exp_res   = stage2_expanding(m, signal)
    tc_res    = stage3_tc_sensitivity(signal)
    slip_res  = stage4_slippage_sensitivity(signal)
    size_res  = stage5_sizing(signal)
    risk_res  = stage6_risk_overlay(signal)
    exp_anal  = stage7_exposure(m, signal)
    dist_res  = stage8_distribution(signal)

    _make_charts(signal)

    # -----------------------------------------------------------------------
    # Verdict
    # -----------------------------------------------------------------------
    print(f"\n{'='*80}")
    print("S1 VALIDATION VERDICT")
    print("-" * 80)

    base       = wf_res["baseline"]
    real_cost  = tc_res.get(REALISTIC_COST_BPS, {})

    # Criteria
    c1 = (not np.isnan(real_cost.get("sharpe", np.nan))) and real_cost.get("sharpe", 0) > 1.5
    c2 = wf_res["n_pass"] >= 3
    c3 = (not np.isnan(real_cost.get("calmar", np.nan))) and real_cost.get("calmar", 0) > 1.0
    c4 = base.get("n_trades", 0) >= 25
    n_criteria = sum([c1, c2, c3, c4])

    # Best sizing post-cost
    best_sz  = max(size_res.items(),
                   key=lambda kv: kv[1].get("sharpe", -999) if not np.isnan(kv[1].get("sharpe", np.nan)) else -999)
    best_risk = max(
        {k: v for k, v in risk_res.items() if k != "baseline"}.items(),
        key=lambda kv: kv[1].get("sharpe", -999) if not np.isnan(kv[1].get("sharpe", np.nan)) else -999,
        default=("none", risk_res.get("baseline", {}))
    )

    print(f"\n  CRITERIA FOR PAPER-TRADING READINESS:")
    print(f"  C1 Sharpe > 1.5 @ {REALISTIC_COST_BPS}bps cost:  "
          f"{'[PASS]' if c1 else '[FAIL]'}  "
          f"(actual: {real_cost.get('sharpe', np.nan):.3f})")
    print(f"  C2 Walk-forward 3+ folds pass:     "
          f"{'[PASS]' if c2 else '[FAIL]'}  "
          f"({wf_res['n_pass']}/{wf_res['n_testable']} testable folds)")
    print(f"  C3 Calmar > 1.0 @ realistic cost:  "
          f"{'[PASS]' if c3 else '[FAIL]'}  "
          f"(actual: {real_cost.get('calmar', np.nan):.2f})")
    print(f"  C4 Trade count >= 25:              "
          f"{'[PASS]' if c4 else '[FAIL]'}  "
          f"(n={base.get('n_trades', 0)})")

    if n_criteria == 4:
        verdict = "PAPER-TRADING READY"
    elif n_criteria >= 2 and base.get("n_trades", 0) >= 20:
        verdict = "CONDITIONAL: PROCEED WITH CAUTION"
    else:
        verdict = "INSUFFICIENT EVIDENCE"

    print(f"\n  {'='*58}")
    print(f"  Criteria met: {n_criteria}/4")
    print(f"  VERDICT: {verdict}")
    print(f"  {'='*58}")

    print(f"\n  BEST CONFIGURATION:")
    print(f"    Sizing:         {best_sz[0]}")
    _print_metrics(f"    {best_sz[0]}", best_sz[1])
    print(f"    Risk overlay:   {best_risk[0]}")
    _print_metrics(f"    {best_risk[0]}", best_risk[1])

    print(f"\n  STRUCTURAL NOTES:")
    print(f"    n_trades (no cost):    {base['n_trades']}")
    print(f"    Signal frequency:      {len(signal)/len(m)*100:.1f}% of days")
    print(f"    Time in market:        ~{exp_anal.get('pct_in_market', 0):.1f}%")
    print(f"    BTC CAGR (benchmark):  {exp_anal.get('btc_cagr', 0):+.1f}%")
    sh_no_cost  = base.get("sharpe", np.nan)
    sh_w_cost   = real_cost.get("sharpe", np.nan)
    cost_decay  = sh_no_cost - sh_w_cost if not (np.isnan(sh_no_cost) or np.isnan(sh_w_cost)) else np.nan
    print(f"    Sharpe (no cost):      {sh_no_cost:+.3f}")
    print(f"    Sharpe ({REALISTIC_COST_BPS}bps):         {sh_w_cost:+.3f}")
    print(f"    Sharpe decay vs cost:  {cost_decay:+.3f}")
    print("=" * 80)

    # ---- JSON report -------------------------------------------------------
    def _ser(o):
        if isinstance(o, float) and np.isnan(o): return None
        if isinstance(o, (np.floating,)):  return float(o)
        if isinstance(o, (np.integer,)):   return int(o)
        if isinstance(o, np.ndarray):      return o.tolist()
        if isinstance(o, dict):            return {k: _ser(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):   return [_ser(v) for v in o]
        return o

    report = {
        "verdict":    verdict,
        "n_criteria": n_criteria,
        "baseline_no_cost": _ser(base),
        "baseline_realistic_cost": _ser(real_cost),
        "walkforward_folds": _ser(
            {yr: d["metrics"] for yr, d in wf_res["folds"].items()}
        ),
        "expanding_window": _ser(exp_res),
        "tc_sensitivity":   _ser({k: v for k, v in tc_res.items()}),
        "slippage_sensitivity": _ser({k: v for k, v in slip_res.items()}),
        "sizing_study":     _ser(size_res),
        "risk_overlay":     _ser(risk_res),
        "exposure":         _ser(exp_anal),
        "distribution":     _ser(dist_res),
        "run_date":  datetime.now(timezone.utc).isoformat(),
        "elapsed_s": round(time.time() - t0, 1),
    }
    out = REPORT_DIR / "s1_validation_summary.json"
    with open(out, "w") as fh:
        json.dump(report, fh, indent=2)

    elapsed = round((time.time() - t0) / 60, 1)
    print(f"\n[{verdict[:30]}] elapsed: {elapsed} min")
    print(f"Report -> {out}")
    print("=" * 80)


if __name__ == "__main__":
    main()
