"""
M6 — Forward Evidence Reporter.

Queries forward_evidence.db and prints structured evidence snapshots.

Reports:
  --summary    Running summary: n trades, Sharpe, DSR proxy, win rate,
               regime-presence count, power analysis, source-divergence stats.
  --weekly     Last 7-day window: signal activity and open-trade status.
  --monthly    Full monthly evidence snapshot (for monthly cron).
  --horizons   Multi-horizon label summary (5d/7d/14d/30d forward returns).
  --paths      MAE/MFE path statistics across all closed trades.
  --regime     Regime-stability and divergence breakdown.

Run from research/:
    python forward_evidence/report.py --summary
    python forward_evidence/report.py --monthly
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import sys
from datetime import datetime, timezone

import numpy as np

_HERE     = pathlib.Path(__file__).resolve().parent
_RESEARCH = _HERE.parent

sys.path.insert(0, str(_RESEARCH))

DB_PATH = _RESEARCH / "data" / "forward_evidence.db"
PRE_REG = _HERE / "pre_registration.json"

PRE_REGISTRATION_DAY_TS = int(
    datetime(2026, 6, 10, tzinfo=timezone.utc).timestamp() * 1000
)
HOLD_DAYS = 7
LABEL_HORIZONS = [5, 7, 14, 30]


def _open_db() -> sqlite3.Connection:
    if not DB_PATH.exists():
        print(f"[ERROR] DB not found: {DB_PATH}  (run init_db.py first)")
        sys.exit(1)
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    return con


def _ts_to_date(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def _sharpe(returns: list[float]) -> float:
    r = np.array(returns)
    if len(r) < 2 or r.std(ddof=1) == 0:
        return float("nan")
    return float(r.mean() / r.std(ddof=1) * np.sqrt(252.0 / HOLD_DAYS))


def _dsr_proxy(returns: list[float], n_trials: int = 38) -> float:
    """Quick DSR using governance.py logic."""
    try:
        sys.path.insert(0, str(_HERE.parent / "evidence"))
        from governance import deflated_sr
        from scipy.stats import norm
        r = np.array(returns)
        sr_std = abs(r.std(ddof=1)) if len(r) > 1 else 1e-6
        dsr, _, _ = deflated_sr(r, sr_std * 0.5, n_trials)
        return float(dsr)
    except Exception:
        return float("nan")


def _section(title: str, width: int = 70) -> None:
    print(f"\n{'─'*width}")
    print(f"  {title}")
    print(f"{'─'*width}")


# ============================================================================
# Summary report
# ============================================================================

def report_summary(con: sqlite3.Connection, forward_only: bool = False) -> None:
    ts_filter = f"AND te.entry_day_ts >= {PRE_REGISTRATION_DAY_TS}" if forward_only else ""

    rows = con.execute(f"""
        SELECT tx.net_return, te.entry_day_ts, te.vol_regime_entry,
               te.funding_regime_entry, te.market_regime_entry
        FROM trade_entries te JOIN trade_exits tx ON te.trade_id = tx.trade_id
        WHERE 1=1 {ts_filter}
        ORDER BY te.entry_day_ts
    """).fetchall()

    label = "FORWARD ONLY" if forward_only else "HISTORICAL FULL"
    _section(f"H6 EVIDENCE SUMMARY — {label} — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}")

    if not rows:
        print("  No completed trades yet.")
        if forward_only:
            print("  (Forward evidence collection starts after 2026-06-10)")
        return

    returns = [float(r["net_return"]) for r in rows]
    n  = len(returns)
    mu = np.mean(returns) * 100
    wr = np.mean([r > 0 for r in returns]) * 100
    sh = _sharpe(returns)
    dsr = _dsr_proxy(returns)
    tot = (np.prod([1 + r for r in returns]) - 1) * 100
    eq = np.cumprod([1 + r for r in returns])
    pk = np.maximum.accumulate([1.0] + list(eq))
    mdd = float(((pk - np.concatenate([[1.0], eq])) / pk).max()) * 100
    pf_num = sum(r for r in returns if r > 0)
    pf_den = sum(-r for r in returns if r < 0)
    pf = pf_num / pf_den if pf_den > 0 else float("inf")

    print(f"  Trades         : {n}")
    print(f"  Mean / trade   : {mu:+.2f}%")
    print(f"  Win rate       : {wr:.0f}%")
    print(f"  Sharpe (7d)    : {sh:.2f}")
    print(f"  DSR proxy      : {dsr:.3f}" + ("  ✓ p>0.95" if dsr > 0.95 else ""))
    print(f"  Total return   : {tot:.1f}%")
    print(f"  Max drawdown   : {mdd:.1f}%")
    print(f"  Profit factor  : {pf:.2f}" if np.isfinite(pf) else f"  Profit factor  : ∞")

    # Regime presence
    non_bear = sum(1 for r in rows if r["market_regime_entry"] not in ("bear", "unknown"))
    print(f"\n  Regime presence:")
    print(f"    Non-bear entries   : {non_bear} / {n} ({non_bear/n*100:.0f}%)")

    # Year breakdown
    print(f"\n  Year breakdown:")
    years: dict[str, list[float]] = {}
    for r in rows:
        yr = _ts_to_date(int(r["entry_day_ts"]))[:4]
        years.setdefault(yr, []).append(float(r["net_return"]))
    for yr in sorted(years):
        yr_r = years[yr]
        print(f"    {yr}: n={len(yr_r):2d}  mean={np.mean(yr_r)*100:+.2f}%"
              f"  pnl={sum(yr_r)*100:+.2f}%  win={np.mean([r>0 for r in yr_r])*100:.0f}%")

    # Power analysis
    _section("POWER ANALYSIS")
    per_trade_sr = np.mean(returns) / np.std(returns, ddof=1) if len(returns) > 1 else 0
    n_req_p10 = int(np.ceil((1.282 / per_trade_sr) ** 2)) if per_trade_sr > 0 else 999
    n_req_p05 = int(np.ceil((1.645 / per_trade_sr) ** 2)) if per_trade_sr > 0 else 999
    print(f"  Per-trade SR       : {per_trade_sr:.3f}")
    print(f"  Current n          : {n}")
    print(f"  n for p<0.10 (one-sided): {n_req_p10}")
    print(f"  n for p<0.05          : {n_req_p05}")
    power_flag = "[UNDERPOWERED]" if n < n_req_p10 else "[POWERED p<0.10]"
    print(f"  Status             : {power_flag}")

    # Promotion gate check
    _section("PROMOTION GATE CHECK")
    g1 = mu > 0
    from scipy.stats import ttest_1samp
    tstat, pval = ttest_1samp(returns, 0)
    g2 = pval / 2 < 0.10 and tstat > 0   # one-sided
    g3 = non_bear >= 15
    g4 = mdd < 25.0
    stable = con.execute(
        "SELECT AVG(vol_regime_stable) FROM attribution" +
        (f" WHERE trade_id IN (SELECT trade_id FROM trade_entries WHERE entry_day_ts >= {PRE_REGISTRATION_DAY_TS})"
         if forward_only else "")
    ).fetchone()[0]
    g5 = (stable or 0) >= 0.80

    for label_g, gate, val in [
        ("G1 mean > 0",        g1, f"{mu:+.2f}%"),
        (f"G2 t-test p<0.10",  g2, f"p={pval/2:.3f}"),
        ("G3 non-bear ≥ 15",   g3, f"n={non_bear}"),
        ("G4 maxDD < 25%",     g4, f"{mdd:.1f}%"),
        ("G5 regime stable ≥80%", g5, f"{(stable or 0)*100:.0f}%"),
    ]:
        tick = "✓" if gate else "✗"
        print(f"  {tick}  {label_g:28s}  ({val})")

    all_pass = all([g1, g2, g3, g4, g5])
    verdict  = "PROMOTE" if all_pass else ("INSUFFICIENT EVIDENCE" if n < 30 else "FAIL — do not promote")
    print(f"\n  Verdict: {verdict}")


# ============================================================================
# Multi-horizon labels
# ============================================================================

def report_horizons(con: sqlite3.Connection) -> None:
    _section("MULTI-HORIZON FORWARD LABELS")
    for h in LABEL_HORIZONS:
        rows = con.execute("""
            SELECT tl.forward_return
            FROM trade_labels tl
            JOIN trade_entries te ON tl.trade_id = te.trade_id
            WHERE tl.horizon_days = ? AND tl.label_available = 1
        """, (h,)).fetchall()
        if not rows:
            print(f"  {h:2d}d: no labels yet")
            continue
        r = [float(row[0]) for row in rows]
        print(f"  {h:2d}d: n={len(r):2d}  mean={np.mean(r)*100:+.2f}%"
              f"  win={np.mean([x>0 for x in r])*100:.0f}%"
              f"  Sharpe={_sharpe(r):.2f}")


# ============================================================================
# MAE/MFE path analysis
# ============================================================================

def report_paths(con: sqlite3.Connection) -> None:
    _section("MAE / MFE PATH STATISTICS")
    rows = con.execute("""
        SELECT a.signal_alpha, a.market_return,
               (SELECT MIN(cum_return) FROM mae_mfe_path WHERE trade_id=a.trade_id) AS mae,
               (SELECT MAX(cum_return) FROM mae_mfe_path WHERE trade_id=a.trade_id) AS mfe
        FROM attribution a
    """).fetchall()
    if not rows:
        print("  No closed trades with path data.")
        return
    maes = [float(r["mae"]) for r in rows if r["mae"] is not None]
    mfes = [float(r["mfe"]) for r in rows if r["mfe"] is not None]
    alphas = [float(r["signal_alpha"]) for r in rows if r["signal_alpha"] is not None]
    print(f"  Closed trades       : {len(rows)}")
    if maes:
        print(f"  Median MAE          : {np.median(maes)*100:.2f}%")
        print(f"  Median MFE          : {np.median(mfes)*100:.2f}%")
        print(f"  Mean MAE            : {np.mean(maes)*100:.2f}%")
        print(f"  Mean MFE            : {np.mean(mfes)*100:.2f}%")
    if alphas:
        print(f"  Mean signal alpha   : {np.mean(alphas)*100:+.2f}%")
        print(f"  Positive alpha      : {np.mean([a>0 for a in alphas])*100:.0f}%")


# ============================================================================
# Regime / divergence
# ============================================================================

def report_regime(con: sqlite3.Connection) -> None:
    _section("REGIME STATE BREAKDOWN")
    for col, label in [("vol_regime","Vol"), ("funding_regime","Funding"), ("market_regime","Market")]:
        rows = con.execute(
            f"SELECT {col} AS r, COUNT(*) AS n FROM regime_states GROUP BY {col} ORDER BY n DESC"
        ).fetchall()
        vals = ", ".join(f"{r['r']}={r['n']}" for r in rows)
        print(f"  {label:10s}: {vals}")

    # Divergence stats
    n_div = con.execute(
        "SELECT COUNT(*) FROM regime_states WHERE regime_diverges IS NOT NULL"
    ).fetchone()[0]
    if n_div == 0:
        print(f"\n  Source divergence: no shadow data available yet (StrataTrade not connected)")
        return
    agree = con.execute(
        "SELECT AVG(1 - regime_diverges) FROM regime_states WHERE regime_diverges IS NOT NULL"
    ).fetchone()[0]
    print(f"\n  Source divergence  : {n_div} shadow days  agreement={agree*100:.1f}%")


# ============================================================================
# Weekly summary
# ============================================================================

def report_weekly(con: sqlite3.Connection) -> None:
    from datetime import timedelta
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    week_ago = now_ms - 7 * 86_400_000
    _section("WEEKLY ACTIVITY")
    signals = con.execute("""
        SELECT day_ts, h6_canonical, vol_regime, funding_regime, market_regime
        FROM signal_log
        JOIN regime_states USING(day_ts)
        WHERE day_ts >= ?
        ORDER BY day_ts
    """, (week_ago,)).fetchall()
    if not signals:
        print("  No data for last 7 days.")
        return
    for s in signals:
        flag = "→ SIGNAL" if s["h6_canonical"] else ""
        print(f"  {_ts_to_date(int(s['day_ts']))}  "
              f"vol={s['vol_regime']:6s}  fund={s['funding_regime']:6s}  "
              f"mkt={s['market_regime']:8s}  {flag}")

    active_id, active_exit, active_entry = _get_active_trade(con)
    if active_id:
        print(f"\n  Open trade: id={active_id} "
              f"entry={_ts_to_date(active_entry)}  "
              f"exit={_ts_to_date(int(active_exit))}")
    else:
        print("\n  No open trade.")


def _get_active_trade(con: sqlite3.Connection):
    row = con.execute("""
        SELECT te.trade_id, te.planned_exit_day_ts, te.entry_day_ts
        FROM trade_entries te
        LEFT JOIN trade_exits tx ON te.trade_id = tx.trade_id
        WHERE tx.trade_id IS NULL
        ORDER BY te.entry_day_ts DESC LIMIT 1
    """).fetchone()
    if row:
        return int(row[0]), int(row[1]), int(row[2])
    return None, None, None


# ============================================================================
# Main
# ============================================================================

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary",  action="store_true")
    ap.add_argument("--weekly",   action="store_true")
    ap.add_argument("--monthly",  action="store_true")
    ap.add_argument("--horizons", action="store_true")
    ap.add_argument("--paths",    action="store_true")
    ap.add_argument("--regime",   action="store_true")
    ap.add_argument("--forward",  action="store_true",
                    help="Restrict summary to post-pre-registration trades only")
    args = ap.parse_args()

    con = _open_db()

    if args.weekly:
        report_weekly(con)
    if args.summary or args.monthly:
        report_summary(con, forward_only=args.forward)
    if args.horizons or args.monthly:
        report_horizons(con)
    if args.paths or args.monthly:
        report_paths(con)
    if args.regime or args.monthly:
        report_regime(con)

    if not any(vars(args).values()):
        report_summary(con)
        report_horizons(con)
        report_paths(con)
        report_regime(con)

    con.close()


if __name__ == "__main__":
    main()
