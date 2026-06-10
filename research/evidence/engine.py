"""
Evidence Engine — Phase B Evidence Discovery Program
====================================================
A single, reusable validation battery. Every candidate signal is expressed
as a daily, point-in-time target position in {-1, 0, +1} and run through the
identical 8-test battery so all scoreboard entries are apples-to-apples.

Design principles (from the program charter)
--------------------------------------------
  * Optimize for ROBUSTNESS, STABILITY, REPRODUCIBILITY — not profitability.
  * No lookahead: position[t] is decided from data available at close of day t;
    it earns the return of day t+1 (engine applies the shift, signals must not).
  * One return-stream definition for every signal type (regime, breakout,
    mean-reversion, candlestick) so metrics are directly comparable.

The 8 required validations
--------------------------
  1. In-sample (first 70% of history)
  2. Out-of-sample (last 30%)
  3. Walk-forward (per-year OOS folds)
  4. Sensitivity analysis (parameter perturbation)
  5. Regime analysis (bull / sideways / bear)
  6. Transaction-cost analysis
  7. Structural-event robustness (exclude +/-7d around 10 BTC events)
  8. Leave-one-year-out

Scoring (robustness-weighted; profitability is only 10/100)
-----------------------------------------------------------
  stability_score (0..1) blends WF pass-rate, LOO sign-consistency,
  sensitivity pass-rate, and OOS/IS sign agreement.

  evidence_score (0..100):
    40  stability_score
    20  OOS quality (sign + OOS/IS Sharpe ratio)
    15  structural robustness (sign preserved + magnitude retained)
    15  cost survival (break-even cost level)
    10  statistical strength (full-sample Sharpe, capped)

  verdict: Reject / Weak / Moderate / Strong
"""
from __future__ import annotations

import pathlib
import sys
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

_THIS_DIR     = pathlib.Path(__file__).resolve().parent
_RESEARCH_DIR = _THIS_DIR.parent
if str(_RESEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_DIR))

from storage.duck import open_db, query, register_view
from hypotheses.shared.market_regimes import build_market_regimes
from hypotheses.shared.structural_events import build_exclusion_mask_ms

SYMBOL    = "BTCUSDT"
BASE_DIR  = _RESEARCH_DIR / "data" / "raw"

# ---- constants -------------------------------------------------------------
ANN              = 365.0          # crypto trades 365 days/yr
RV_WINDOW        = 21
REGIME_WINDOW    = 252
REGIME_PCTILE    = 33
MIN_DAILY_FUND   = 2
IS_FRACTION      = 0.70
COST_GRID_BPS    = [0, 10, 15, 30, 50, 75, 100]
REALISTIC_COST   = 15             # bps per position change (round-trip-ish)
MIN_TRADES_TRUST = 15             # below this, verdict capped at Weak


# =============================================================================
# Signal specification
# =============================================================================

@dataclass
class SignalSpec:
    name:        str
    group:       str
    rationale:   str
    data_source: str
    feature_def: str
    fn:          Callable[..., pd.Series]      # fn(m, **params) -> daily position
    params:      dict = field(default_factory=dict)
    variants:    list[dict] = field(default_factory=list)   # for sensitivity
    hold_days:   int = 1          # 1 = continuous daily; >1 = event hold horizon
    hypothesis_verdict: str | None = None   # for seeds: original validated verdict


# =============================================================================
# Data loading (shared across all signals)
# =============================================================================

_CACHE: dict = {}

def load_master() -> pd.DataFrame:
    """Daily spot OHLCV + funding + regimes + realized vol. Cached per process."""
    if "master" in _CACHE:
        return _CACHE["master"].copy()

    con = open_db()
    register_view(con, "spot_1m",     BASE_DIR, "spot",    SYMBOL)
    register_view(con, "funding_raw", BASE_DIR, "funding", SYMBOL)

    df_ohlc = query(con, """
    SELECT day_ts, o AS open, h AS high, l AS low, c AS close, v AS volume
    FROM (
        SELECT
            (open_time // 86400000) * 86400000 AS day_ts,
            FIRST(open  ORDER BY open_time ASC)  AS o,
            MAX(high)                            AS h,
            MIN(low)                             AS l,
            LAST(close ORDER BY open_time ASC)   AS c,
            SUM(volume)                          AS v
        FROM spot_1m
        GROUP BY open_time // 86400000
    )
    ORDER BY day_ts
    """)

    df_fund = query(con, f"""
    SELECT
        (timestamp_ms // 86400000) * 86400000 AS day_ts,
        AVG(funding_rate) * 10000.0           AS daily_mean_funding_bps
    FROM funding_raw
    GROUP BY timestamp_ms // 86400000
    HAVING COUNT(*) >= {MIN_DAILY_FUND}
    ORDER BY day_ts
    """)

    register_view(con, "perp_1m", BASE_DIR, "perp", SYMBOL)
    df_perp = query(con, """
    SELECT day_ts, perp_close
    FROM (
        SELECT
            (open_time // 86400000) * 86400000 AS day_ts,
            LAST(close ORDER BY open_time ASC) AS perp_close
        FROM perp_1m
        GROUP BY open_time // 86400000
    )
    ORDER BY day_ts
    """)

    # higher-timeframe closes for multi-timeframe structure (Group 5)
    df_1h = query(con, """
    SELECT (open_time // 3600000) * 3600000 AS h_ts,
           LAST(close ORDER BY open_time ASC) AS c
    FROM spot_1m GROUP BY open_time // 3600000 ORDER BY h_ts
    """)
    df_4h = query(con, """
    SELECT (open_time // 14400000) * 14400000 AS h_ts,
           LAST(close ORDER BY open_time ASC) AS c
    FROM spot_1m GROUP BY open_time // 14400000 ORDER BY h_ts
    """)
    con.close()

    m = df_ohlc.merge(df_fund, on="day_ts", how="left").merge(df_perp, on="day_ts", how="left")
    # perp basis vs spot (bps); positive = contango (perp rich)
    m["basis_bps"] = (m["perp_close"] / m["close"] - 1.0) * 10_000.0

    # ---- higher-timeframe trend states (50/200 MA cross), sampled to end-of-day ----
    def _tf_trend_daily(df_tf: pd.DataFrame, fast: int, slow: int) -> pd.Series:
        c = df_tf["c"]
        f = c.rolling(fast, min_periods=fast).mean()
        s = c.rolling(slow, min_periods=slow).mean()
        t = pd.Series(0.0, index=df_tf.index)
        t[f > s] = 1.0
        t[f < s] = -1.0
        t[f.isna() | s.isna()] = 0.0
        day = (df_tf["h_ts"] // 86_400_000) * 86_400_000
        return t.groupby(day).last()

    tr_1h = _tf_trend_daily(df_1h, 50, 200)
    tr_4h = _tf_trend_daily(df_4h, 50, 200)
    m["trend_1h"] = m["day_ts"].map(tr_1h).fillna(0.0)
    m["trend_4h"] = m["day_ts"].map(tr_4h).fillna(0.0)
    # 1D trend on daily bars (same 50/200 construction)
    f1d = m["close"].rolling(50, min_periods=50).mean()
    s1d = m["close"].rolling(200, min_periods=200).mean()
    td = pd.Series(0.0, index=m.index)
    td[f1d > s1d] = 1.0; td[f1d < s1d] = -1.0
    td[f1d.isna() | s1d.isna()] = 0.0
    m["trend_1d"] = td.values
    m["dt"]   = pd.to_datetime(m["day_ts"], unit="ms", utc=True)
    m["year"] = m["dt"].dt.year
    m = m.sort_values("day_ts").reset_index(drop=True)

    # daily log return + forward 1d simple return (the building block)
    m["log_ret"]  = np.log(m["close"] / m["close"].shift(1))
    m["ret_fwd1"] = m["close"].shift(-1) / m["close"] - 1.0

    # realized vol + vol regime (rolling tercile, backward-looking)
    rv = m["log_ret"].rolling(RV_WINDOW, min_periods=RV_WINDOW).std() * np.sqrt(ANN)
    m["rv_21d"] = rv.values
    lo_q = rv.rolling(REGIME_WINDOW, min_periods=REGIME_WINDOW).quantile(REGIME_PCTILE / 100)
    hi_q = rv.rolling(REGIME_WINDOW, min_periods=REGIME_WINDOW).quantile((100 - REGIME_PCTILE) / 100)
    vr = pd.Series("medium", index=m.index, dtype=object)
    vr[rv > hi_q] = "high"; vr[rv < lo_q] = "low"
    vr[lo_q.isna() | hi_q.isna()] = "unknown"
    m["vol_regime"] = vr.values

    # funding regime
    fl = m["daily_mean_funding_bps"].rolling(REGIME_WINDOW, min_periods=REGIME_WINDOW).quantile(REGIME_PCTILE / 100)
    fh = m["daily_mean_funding_bps"].rolling(REGIME_WINDOW, min_periods=REGIME_WINDOW).quantile((100 - REGIME_PCTILE) / 100)
    fr = pd.Series("medium", index=m.index, dtype=object)
    fr[m["daily_mean_funding_bps"] > fh] = "high"
    fr[m["daily_mean_funding_bps"] < fl] = "low"
    fr[fl.isna() | fh.isna()] = "unknown"
    m["funding_regime"] = fr.values

    # market regime (SMA200 + slope)
    close_dt = pd.Series(m["close"].values, index=m["dt"].values)
    mkt      = build_market_regimes(close_dt)
    mkt_map  = {k.normalize(): v for k, v in mkt.to_dict().items()}
    m["market_regime"] = m["dt"].dt.normalize().dt.tz_localize(None).map(mkt_map).fillna("unknown")

    _CACHE["master"] = m.copy()
    return m


# =============================================================================
# Core metric computation from a daily position series
# =============================================================================

def _apply_hold(raw_pos: np.ndarray, hold_days: int) -> np.ndarray:
    """
    Convert sparse entry signals into a held position.
    A nonzero raw_pos[t] opens/refreshes a position held for `hold_days` bars
    (a fresh signal during a hold resets the clock). hold_days<=1 is a no-op,
    so continuous regime signals pass through unchanged.
    """
    if hold_days <= 1:
        return raw_pos
    out = np.zeros(len(raw_pos))
    remaining = 0
    cur = 0.0
    for t in range(len(raw_pos)):
        if raw_pos[t] != 0 and not np.isnan(raw_pos[t]):
            cur = raw_pos[t]
            remaining = hold_days
        if remaining > 0:
            out[t] = cur
            remaining -= 1
    return out


def _net_daily(position: np.ndarray, ret_fwd1: np.ndarray, cost_bps: float) -> np.ndarray:
    """
    Net daily strategy return.
    position[t] is held into day t+1 and earns ret_fwd1[t]; costs charged on
    turnover |position[t] - position[t-1]|.
    """
    held = position.copy()
    gross = held * ret_fwd1
    turnover = np.abs(np.diff(held, prepend=0.0))
    cost = turnover * (cost_bps / 10_000.0)
    net = gross - cost
    return net


def _runs(held: np.ndarray, net: np.ndarray) -> list[float]:
    """Per-trade returns: maximal runs of constant nonzero held position."""
    trades: list[float] = []
    i = 0
    n = len(held)
    while i < n:
        if held[i] == 0 or np.isnan(held[i]):
            i += 1
            continue
        j = i
        cur = held[i]
        acc = 1.0
        while j < n and held[j] == cur:
            r = net[j]
            if not np.isnan(r):
                acc *= (1.0 + r)
            j += 1
        trades.append(acc - 1.0)
        i = j
    return trades


def _metrics(position: np.ndarray, ret_fwd1: np.ndarray, day_ts: np.ndarray,
             cost_bps: float = 0.0) -> dict:
    """Full metric block from a daily position series."""
    NAN = {k: np.nan for k in
           ["cagr_pct", "sharpe", "sortino", "calmar", "max_dd_pct",
            "win_rate_pct", "profit_factor", "n_trades", "n_active_days"]}
    valid = ~np.isnan(ret_fwd1)
    pos = np.where(np.isnan(position), 0.0, position)
    net = _net_daily(pos, np.where(valid, ret_fwd1, 0.0), cost_bps)
    net = net[valid]
    held = pos[valid]

    n_active = int(np.sum(held != 0))
    if n_active < 3:
        NAN["n_active_days"] = n_active
        NAN["n_trades"] = 0
        return NAN

    equity = np.cumprod(1.0 + net)
    peak   = np.maximum.accumulate(np.concatenate([[1.0], equity]))
    eq_ext = np.concatenate([[1.0], equity])
    dd     = (peak - eq_ext) / peak
    max_dd = float(np.max(dd))

    ts = day_ts[valid]
    cal_days = (ts[-1] - ts[0]) / 86_400_000 if len(ts) > 1 else len(net)
    final_eq = float(equity[-1])
    cagr = float(np.sign(final_eq) * (abs(final_eq) ** (365.0 / cal_days)) - 1.0) \
        if (cal_days > 0 and final_eq > 0) else (float(final_eq - 1.0) if cal_days > 0 else np.nan)

    mean_d = float(net.mean())
    std_d  = float(net.std(ddof=1)) if len(net) > 1 else np.nan
    sharpe = float(mean_d / std_d * np.sqrt(ANN)) if (std_d and std_d > 0) else np.nan
    neg    = net[net < 0]
    dstd   = float(np.sqrt(np.mean(neg ** 2))) if len(neg) >= 2 else np.nan
    sortino = float(mean_d / dstd * np.sqrt(ANN)) if (dstd and dstd > 0) else np.nan
    calmar = float(cagr / max_dd) if (max_dd > 0 and not np.isnan(cagr)) else np.nan

    trades = _runs(held, net)
    n_tr   = len(trades)
    tr_arr = np.array(trades) if trades else np.array([])
    win    = float((tr_arr > 0).mean() * 100) if n_tr > 0 else np.nan
    gp     = float(tr_arr[tr_arr > 0].sum()) if (tr_arr > 0).any() else 0.0
    gl     = float(abs(tr_arr[tr_arr < 0].sum())) if (tr_arr < 0).any() else 0.0
    pf     = float(gp / gl) if gl > 0 else (np.inf if gp > 0 else np.nan)

    return {
        "cagr_pct": cagr * 100, "sharpe": sharpe, "sortino": sortino,
        "calmar": calmar, "max_dd_pct": max_dd * 100, "win_rate_pct": win,
        "profit_factor": pf, "n_trades": n_tr, "n_active_days": n_active,
    }


# =============================================================================
# Full validation battery for one signal
# =============================================================================

def evaluate_signal(m: pd.DataFrame, spec: SignalSpec) -> dict:
    pos_full = spec.fn(m, **spec.params)
    pos_full = pd.Series(pos_full, index=m.index).reindex(m.index).fillna(0.0)
    position = _apply_hold(pos_full.to_numpy(dtype=float), spec.hold_days)
    ret      = m["ret_fwd1"].to_numpy(dtype=float)
    ts       = m["day_ts"].to_numpy()
    years    = m["year"].to_numpy()
    mreg     = m["market_regime"].to_numpy()

    # ----- baseline (no cost) and realistic cost -----
    base   = _metrics(position, ret, ts, 0.0)
    real   = _metrics(position, ret, ts, REALISTIC_COST)

    # ----- 1 & 2  IS / OOS -----
    n = len(m)
    split = int(n * IS_FRACTION)
    is_m  = _metrics(position[:split], ret[:split], ts[:split], REALISTIC_COST)
    oos_m = _metrics(position[split:], ret[split:], ts[split:], REALISTIC_COST)
    oos_sign_ok = (not np.isnan(oos_m["sharpe"])) and oos_m["sharpe"] > 0
    is_sign     = np.sign(is_m["sharpe"]) if not np.isnan(is_m["sharpe"]) else 0
    oos_sign    = np.sign(oos_m["sharpe"]) if not np.isnan(oos_m["sharpe"]) else 0
    oos_is_agree = (is_sign == oos_sign) and is_sign != 0
    oos_ratio = (oos_m["sharpe"] / is_m["sharpe"]) if (not np.isnan(is_m["sharpe"]) and is_m["sharpe"] > 0) else np.nan

    # ----- 3  Walk-forward (per-year folds) -----
    wf: dict = {}
    for yr in range(2021, 2027):
        mask = years == yr
        if mask.sum() < 30:
            continue
        fm = _metrics(position[mask], ret[mask], ts[mask], REALISTIC_COST)
        if fm["n_trades"] >= 3:
            wf[int(yr)] = fm
    wf_testable = len(wf)
    wf_pass = sum(1 for d in wf.values() if (not np.isnan(d["sharpe"])) and d["sharpe"] > 0)
    wf_pass_frac = wf_pass / wf_testable if wf_testable > 0 else 0.0

    # ----- 4  Sensitivity (parameter perturbation) -----
    sens_results: list[dict] = []
    base_sign = np.sign(real["sharpe"]) if not np.isnan(real["sharpe"]) else 0
    sens_pass = 0; sens_total = 0
    for vp in spec.variants:
        vpos = spec.fn(m, **vp)
        vpos = pd.Series(vpos, index=m.index).reindex(m.index).fillna(0.0).to_numpy(float)
        vpos = _apply_hold(vpos, spec.hold_days)
        vm = _metrics(vpos, ret, ts, REALISTIC_COST)
        sens_results.append({"params": vp, "sharpe": vm["sharpe"], "cagr_pct": vm["cagr_pct"]})
        if not np.isnan(vm["sharpe"]):
            sens_total += 1
            if np.sign(vm["sharpe"]) == base_sign and base_sign != 0:
                sens_pass += 1
    sens_pass_frac = sens_pass / sens_total if sens_total > 0 else (1.0 if not spec.variants else 0.0)

    # ----- 5  Regime analysis -----
    regime_m: dict = {}
    for reg in ["bull", "sideways", "bear"]:
        rmask = mreg == reg
        if rmask.sum() < 20:
            continue
        regime_m[reg] = _metrics(position[rmask], ret[rmask], ts[rmask], REALISTIC_COST)

    # ----- 6  Transaction-cost analysis -----
    cost_curve: dict = {}
    for bps in COST_GRID_BPS:
        cm = _metrics(position, ret, ts, bps)
        cost_curve[bps] = cm["sharpe"]
    breakeven_cost = max((b for b, s in cost_curve.items()
                          if (not np.isnan(s)) and s > 0), default=0)

    # ----- 7  Structural-event robustness -----
    excl = build_exclusion_mask_ms(ts)
    keep = ~excl
    clean_m = _metrics(position[keep], ret[keep], ts[keep], REALISTIC_COST)
    struct_sign_ok = (not np.isnan(clean_m["sharpe"])) and \
                     np.sign(clean_m["sharpe"]) == base_sign and base_sign != 0
    struct_ratio = (clean_m["sharpe"] / real["sharpe"]) \
        if (not np.isnan(real["sharpe"]) and real["sharpe"] > 0 and not np.isnan(clean_m["sharpe"])) else np.nan

    # ----- 8  Leave-one-year-out -----
    loo: dict = {}
    loo_signs = []
    for yr in sorted(set(int(y) for y in years)):
        mask = years != yr
        lm = _metrics(position[mask], ret[mask], ts[mask], REALISTIC_COST)
        if not np.isnan(lm["sharpe"]):
            loo[int(yr)] = lm["sharpe"]
            loo_signs.append(np.sign(lm["sharpe"]))
    loo_sign_consistency = (np.mean([s == base_sign for s in loo_signs])
                            if loo_signs and base_sign != 0 else 0.0)
    loo_sharpe_cv = (float(np.std(list(loo.values())) / abs(np.mean(list(loo.values()))))
                     if loo and abs(np.mean(list(loo.values()))) > 1e-9 else np.nan)

    # ===================== Scoring =====================
    # Robustness-weighted. Profitability (raw Sharpe) is only 10/100; the bulk
    # rewards stability, out-of-sample/walk-forward persistence, risk-adjusted
    # quality (so drawdown-monsters cannot rank highly), and cost/event survival.
    stability_score = float(np.mean([
        wf_pass_frac,
        loo_sign_consistency,
        sens_pass_frac,
        1.0 if oos_is_agree else 0.0,
    ]))

    calmar_v = real["calmar"]
    maxdd_v  = real["max_dd_pct"]
    # walk-forward-aware OOS: tolerate a single regime-unlucky split if the
    # per-year walk-forward folds are mostly positive.
    effective_oos = bool(oos_sign_ok or wf_pass_frac >= 0.60)

    # OOS quality (0..18): split sign + walk-forward persistence
    oos_q = (10.0 if oos_sign_ok else 0.0) + 8.0 * wf_pass_frac
    # risk-adjusted quality (0..18): Calmar, full credit at Calmar>=1.0
    risk_q = 18.0 * float(np.clip((calmar_v if not np.isnan(calmar_v) else 0.0) / 1.0, 0.0, 1.0))
    # structural robustness (0..12)
    struct_q = 0.0
    if struct_sign_ok:
        struct_q += 6.0
        if not np.isnan(struct_ratio):
            struct_q += 6.0 * float(np.clip(struct_ratio, 0.0, 1.0))
    # cost survival (0..12): scaled by break-even cost up to 100bps
    cost_q = 12.0 * float(np.clip(breakeven_cost / 100.0, 0.0, 1.0))
    # statistical strength (0..10): full-sample Sharpe capped at 1.0
    stat_q = 10.0 * float(np.clip((real["sharpe"] if not np.isnan(real["sharpe"]) else 0) / 1.0, 0.0, 1.0))

    evidence_score = float(
        30.0 * stability_score + oos_q + risk_q + struct_q + cost_q + stat_q
    )

    # verdict gates — a Strong signal must be robust AND risk-efficient
    n_trades = base.get("n_trades", 0)
    calmar_ok = (not np.isnan(calmar_v)) and calmar_v >= 0.50
    dd_ok     = (not np.isnan(maxdd_v)) and maxdd_v < 50.0
    if evidence_score >= 70 and stability_score >= 0.60 and effective_oos and calmar_ok and dd_ok:
        verdict = "Strong"
    elif evidence_score >= 50 and effective_oos and (not np.isnan(calmar_v)) and calmar_v > 0:
        verdict = "Moderate"
    elif evidence_score >= 30:
        verdict = "Weak"
    else:
        verdict = "Reject"
    # trust cap: too few trades -> can't be better than Weak
    if n_trades < MIN_TRADES_TRUST and verdict in ("Strong", "Moderate"):
        verdict = "Weak"

    return {
        "name": spec.name, "group": spec.group, "rationale": spec.rationale,
        "data_source": spec.data_source, "feature_def": spec.feature_def,
        # headline metrics (realistic cost)
        "cagr_pct": real["cagr_pct"], "sharpe": real["sharpe"],
        "sortino": real["sortino"], "calmar": real["calmar"],
        "max_dd_pct": real["max_dd_pct"], "win_rate_pct": real["win_rate_pct"],
        "profit_factor": real["profit_factor"], "n_trades": real["n_trades"],
        # no-cost reference
        "sharpe_no_cost": base["sharpe"],
        # validations
        "is_sharpe": is_m["sharpe"], "oos_sharpe": oos_m["sharpe"],
        "oos_sign_ok": bool(oos_sign_ok), "oos_is_agree": bool(oos_is_agree),
        "wf_pass": f"{wf_pass}/{wf_testable}", "wf_pass_frac": wf_pass_frac,
        "wf_folds": {y: d["sharpe"] for y, d in wf.items()},
        "sens_pass_frac": sens_pass_frac, "sens_results": sens_results,
        "regime_sharpe": {r: d["sharpe"] for r, d in regime_m.items()},
        "breakeven_cost_bps": breakeven_cost, "cost_curve": cost_curve,
        "struct_sign_ok": bool(struct_sign_ok), "struct_ratio": struct_ratio,
        "clean_sharpe": clean_m["sharpe"],
        "loo_sign_consistency": loo_sign_consistency, "loo_sharpe_cv": loo_sharpe_cv,
        "loo_sharpe": loo,
        # scores
        "stability_score": round(stability_score, 3),
        "evidence_score": round(evidence_score, 1),
        "verdict": verdict,
        "hypothesis_verdict": spec.hypothesis_verdict,
    }
