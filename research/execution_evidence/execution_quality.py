"""
execution_quality.py
====================
Execution-quality metrics over the canonical completed-trade dataset, plus the
order-level facts that never become a trade -- rejections, duplicates and
reconciliation integrity.

Read-only. Writes nothing, anywhere.

THE THREE EVIDENCE CLASSES, KEPT APART
--------------------------------------
The report separates what the numbers can support, because the same table can
otherwise be read as three different kinds of claim:

1. **Infrastructure / execution evidence.** Did the machinery do what it was
   told -- latency, slippage, protection coverage, idempotency, reconciliation.
   These are properties of the *system*, and a handful of trades is already
   informative about them: a stop that fires late is late whether or not the
   strategy has an edge.
2. **Descriptive trade observations.** What happened -- PnL, excursions,
   holding periods. True of the sample, generalising to nothing.
3. **Statistically insufficient strategy evidence.** Anything that would be a
   claim about edge. Reported as counts and an explicit insufficiency notice,
   never as a performance statistic. No Sharpe, no DSR, no BH, no hit rate
   dressed up as a win probability.

The split is structural: `build_report` emits three separate blocks and no
aggregate that mixes them.

WHY LATENCY IS TWO NUMBERS
--------------------------
`exch_ts` is when the exchange says the fill happened; `processed_ts` is when
the reconciler saw it. Their difference is the runtime's observation lag, not
the exchange's execution speed, and conflating them would blame Delta for the
reconciliation interval. Observation lag uses `exch_ts -> processed_ts`.
Submit-to-fill uses `orders.placed_ts -> exch_ts`, and is reported separately
for marketable orders and for protective stops: a stop rests until the market
reaches it, so its time-to-fill measures the market, not the machinery.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys
from typing import Any, Optional, Sequence

_HERE = pathlib.Path(__file__).resolve().parent
_RESEARCH_ROOT = _HERE.parent
if str(_RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_ROOT))

from execution_evidence.completed_trades import (  # noqa: E402
    PROTECTION_STRATEGY, CompletedTrade, build_dataset, connect_ro, coverage,
    fill_cycles, has_table, load_fills, seconds_between,
)

_DEFAULT_TRADING_DB = _RESEARCH_ROOT.parent / "data" / "btc_trading.db"
_DEFAULT_FORWARD_DB = _RESEARCH_ROOT / "data" / "evidence_forward.db"


def _stats(values: Sequence[float]) -> Optional[dict[str, float]]:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return {
        "n": len(vals),
        "min": min(vals),
        "median": statistics.median(vals),
        "max": max(vals),
        "mean": statistics.fmean(vals),
    }


def latency(trading_db) -> dict[str, Any]:
    """Submit-to-fill and fill-to-observation latency, per fill.

    Both are computed off the fill ledger joined to orders on
    `delta_order_id`; `order_fills.client_order_id` is NULL for every row the
    runtime has written so far, so it is not a usable join key.
    """
    conn = connect_ro(trading_db)
    try:
        fills = load_fills(conn)
        orders = {}
        for row in conn.execute(
                "SELECT delta_order_id, placed_ts, client_order_id, status, strategy "
                "FROM orders WHERE delta_order_id IS NOT NULL ORDER BY id"):
            orders[str(row["delta_order_id"])] = row

        marketable, resting, observation_lag, unmatched = [], [], [], 0
        for f in fills:
            lag = seconds_between(f["exch_ts"], f["processed_ts"])
            if lag is not None:
                observation_lag.append(lag)
            o = orders.get(str(f["delta_order_id"]))
            if o is None:
                unmatched += 1
                continue
            elapsed = seconds_between(o["placed_ts"], f["exch_ts"])
            if elapsed is None:
                continue
            # A protective stop rests until the market reaches it, so its
            # submit-to-fill is a market outcome, not execution latency.
            # Averaging the two together makes an 87-minute wait look like a
            # slow fill.
            if str(o["strategy"] or "") == PROTECTION_STRATEGY:
                resting.append(elapsed)
            else:
                marketable.append(elapsed)
        return {
            "submit_to_fill_seconds": _stats(marketable),
            "resting_stop_time_to_fill_seconds": _stats(resting),
            "observation_lag_seconds": _stats(observation_lag),
            "fills_without_matching_order": unmatched,
            "fills_total": len(fills),
        }
    finally:
        conn.close()


def idempotency(trading_db) -> dict[str, Any]:
    """Duplicate-submission outcomes, measured rather than assumed.

    The strategy job derives `client_order_id` deterministically from
    (signal, UTC day, side, seq) and refuses to submit when that id is already
    in `orders`. A duplicate id in the table would mean the guard failed; a
    duplicate `delta_order_id` would mean the exchange accepted the same
    intent twice.
    """
    conn = connect_ro(trading_db)
    try:
        dup_coid = conn.execute(
            "SELECT client_order_id, COUNT(*) n FROM orders "
            "WHERE client_order_id IS NOT NULL GROUP BY client_order_id "
            "HAVING n > 1 ORDER BY client_order_id").fetchall()
        dup_delta = conn.execute(
            "SELECT delta_order_id, COUNT(*) n FROM orders "
            "WHERE delta_order_id IS NOT NULL GROUP BY delta_order_id "
            "HAVING n > 1 ORDER BY delta_order_id").fetchall()
        dup_fill = []
        if has_table(conn, "order_fills"):
            dup_fill = conn.execute(
                "SELECT delta_order_id, COUNT(*) n FROM order_fills "
                "WHERE delta_order_id IS NOT NULL GROUP BY delta_order_id "
                "HAVING n > 1 ORDER BY delta_order_id").fetchall()
        total = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT client_order_id) FROM orders "
            "WHERE client_order_id IS NOT NULL").fetchone()
        return {
            "orders_with_client_order_id": total[0],
            "distinct_client_order_ids": total[1],
            "duplicate_client_order_ids": [dict(r) for r in dup_coid],
            "duplicate_delta_order_ids": [dict(r) for r in dup_delta],
            "duplicate_fill_delta_order_ids": [dict(r) for r in dup_fill],
            "clean": not (dup_coid or dup_delta or dup_fill),
        }
    finally:
        conn.close()


def rejections(trading_db) -> dict[str, Any]:
    conn = connect_ro(trading_db)
    try:
        if not has_table(conn, "order_rejections"):
            return {"total": 0, "by_error_code": {}, "rows": []}
        rows = conn.execute(
            "SELECT client_order_id, ts, http_status, error_code "
            "FROM order_rejections ORDER BY ts").fetchall()
        by_code: dict[str, int] = {}
        for r in rows:
            key = r["error_code"] or "unknown"
            by_code[key] = by_code.get(key, 0) + 1
        return {
            "total": len(rows),
            "by_error_code": dict(sorted(by_code.items())),
            "rows": [dict(r) for r in rows],
        }
    finally:
        conn.close()


def reconciliation_integrity(trading_db, trades: Sequence[CompletedTrade]
                             ) -> dict[str, Any]:
    """Cross-derivation checks. Findings are reported, never repaired."""
    conn = connect_ro(trading_db)
    findings: list[str] = []
    try:
        fills = load_fills(conn)
        cycles = fill_cycles(fills)
        closed_cycles = [c for c in cycles if c["exit_processed_ts"] is not None]

        if len(closed_cycles) != len(trades):
            findings.append(
                f"closed fill-cycles ({len(closed_cycles)}) != completed trades "
                f"({len(trades)})")

        by_entry = {c["entry_processed_ts"]: c for c in closed_cycles}
        pnl_mismatches = []
        for t in trades:
            c = by_entry.get(t.entry_ts)
            if c is None:
                findings.append(f"trade {t.trade_id}: no fill-cycle at {t.entry_ts}")
                continue
            if t.realised_pnl is not None and c["realised_pnl"] is not None:
                if abs(t.realised_pnl - c["realised_pnl"]) > 1e-9:
                    pnl_mismatches.append({
                        "trade_id": t.trade_id,
                        "positions_pnl": t.realised_pnl,
                        "fills_pnl": c["realised_pnl"],
                    })

        unlinked = [t.trade_id for t in trades if not t.link_exact]
        if unlinked:
            findings.append(f"trades without an exact fill link: {unlinked}")

        filled_without_fill = conn.execute(
            "SELECT COUNT(*) FROM orders o WHERE o.status='filled' "
            "AND NOT EXISTS (SELECT 1 FROM order_fills f "
            "                WHERE f.delta_order_id = o.delta_order_id)"
        ).fetchone()[0] if has_table(conn, "order_fills") else -1
        if filled_without_fill > 0:
            findings.append(
                f"orders marked filled with no fill row: {filled_without_fill}")

        unprotected = [t.trade_id for t in trades
                       if t.is_automated and t.stop_client_order_id is None]
        if unprotected:
            findings.append(
                f"automated trades with no protective stop recorded: {unprotected}")

        return {
            "fill_cycles_closed": len(closed_cycles),
            "fill_cycles_open": len(cycles) - len(closed_cycles),
            "completed_trades": len(trades),
            "pnl_mismatches": pnl_mismatches,
            "orders_filled_without_fill_row": filled_without_fill,
            "findings": findings,
            "clean": not findings and not pnl_mismatches,
        }
    finally:
        conn.close()


def infrastructure_evidence(trading_db, trades: Sequence[CompletedTrade],
                            risk_budget_usd: float | None = None) -> dict[str, Any]:
    """Class 1 -- properties of the execution machinery."""
    auto = [t for t in trades if t.is_automated]
    protected = [t for t in trades if t.stop_client_order_id is not None]
    risk_rows = [t for t in trades
                 if t.configured_risk_usd is not None and t.realised_risk_usd is not None
                 and t.exited_via == "protective_stop"]
    out: dict[str, Any] = {
        "latency": latency(trading_db),
        "entry_slippage_pts": _stats([t.entry_slippage_pts for t in trades]),
        "exit_slippage_pts": _stats([t.exit_slippage_pts for t in trades]),
        "protection_latency_seconds": _stats(
            [t.protection_latency_seconds for t in protected]),
        "protection_coverage": {
            "trades": len(trades),
            "with_recorded_stop": len(protected),
            "automated_trades": len(auto),
            "automated_with_stop": len([t for t in auto if t.stop_client_order_id]),
        },
        "exit_route_counts": _counts([t.exited_via for t in trades]),
        "idempotency": idempotency(trading_db),
        "rejections": rejections(trading_db),
        "reconciliation": reconciliation_integrity(trading_db, trades),
    }
    out["realised_vs_configured_risk"] = {
        "comparable_trades": len(risk_rows),
        "configured_risk_usd": _stats([t.configured_risk_usd for t in risk_rows]),
        "realised_risk_usd": _stats([t.realised_risk_usd for t in risk_rows]),
        "overrun_usd": _stats([t.risk_overrun_usd for t in risk_rows]),
        "note": ("comparable only where a protective stop closed the trade; "
                 "a configured budget is not a guarantee, because the fill "
                 "price, not the trigger, determines the loss"),
    }
    if risk_budget_usd is not None:
        breaches = [{"trade_id": t.trade_id, "realised_risk_usd": t.realised_risk_usd}
                    for t in trades
                    if t.realised_risk_usd is not None
                    and t.realised_risk_usd > risk_budget_usd]
        out["realised_vs_configured_risk"]["supplied_budget_usd"] = risk_budget_usd
        out["realised_vs_configured_risk"]["over_supplied_budget"] = breaches
    return out


def descriptive_observations(trades: Sequence[CompletedTrade]) -> dict[str, Any]:
    """Class 2 -- what happened in this sample, generalising to nothing."""
    nets = [t.realised_pnl for t in trades if t.realised_pnl is not None]
    return {
        "trades": len(trades),
        "by_strategy": _counts([t.strategy or "-" for t in trades]),
        "by_direction": _counts(["long" if t.direction == 1 else "short" for t in trades]),
        "realised_pnl_usd": _stats(nets),
        "realised_pnl_sum_usd": sum(nets) if nets else None,
        "winners": len([v for v in nets if v > 0]),
        "losers": len([v for v in nets if v < 0]),
        "scratches": len([v for v in nets if v == 0]),
        "holding_seconds": _stats([t.holding_seconds for t in trades]),
        "mae_pts": _stats([t.mae_pts for t in trades]),
        "mfe_pts": _stats([t.mfe_pts for t in trades]),
        "fees_usd": _stats([t.fees_total for t in trades]),
        "trades_without_excursion_data": len(
            [t for t in trades if t.excursion_samples == 0]),
    }


def strategy_evidence_status(trades: Sequence[CompletedTrade]) -> dict[str, Any]:
    """Class 3 -- counts and an insufficiency notice. Deliberately no statistics.

    No Sharpe, no DSR, no BH, no win rate. A per-strategy count is the only
    honest summary at this sample size, and the notice says so rather than
    leaving the reader to infer it.
    """
    per: dict[str, dict[str, Any]] = {}
    for t in trades:
        if not t.is_automated:
            continue
        key = t.strategy or "-"
        slot = per.setdefault(key, {"completed_trades": 0, "first_entry_ts": None,
                                    "last_exit_ts": None})
        slot["completed_trades"] += 1
        if slot["first_entry_ts"] is None or t.entry_ts < slot["first_entry_ts"]:
            slot["first_entry_ts"] = t.entry_ts
        if slot["last_exit_ts"] is None or t.exit_ts > slot["last_exit_ts"]:
            slot["last_exit_ts"] = t.exit_ts
    return {
        "automated_completed_trades_by_strategy": dict(sorted(per.items())),
        "sufficient_for_inference": False,
        "notice": ("execution-path evidence only. No performance statistic is "
                   "computed here and none should be inferred from these counts. "
                   "DSR/BH belong to forward_dsr_bh under the frozen FB-10b "
                   "contract, on forward-evidence trades, once a family reaches "
                   "its operator-supplied minimum -- not to executed testnet "
                   "orders."),
    }


def _counts(values) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[str(v)] = out.get(str(v), 0) + 1
    return dict(sorted(out.items()))


def build_report(trading_db, forward_db=None,
                 risk_budget_usd: float | None = None) -> dict[str, Any]:
    trades = build_dataset(trading_db, forward_db)
    return {
        "coverage": coverage(trading_db),
        "infrastructure_evidence": infrastructure_evidence(
            trading_db, trades, risk_budget_usd),
        "descriptive_observations": descriptive_observations(trades),
        "strategy_evidence": strategy_evidence_status(trades),
    }


def print_report(rep: dict[str, Any]) -> None:
    cov = rep["coverage"]
    print("COVERAGE")
    print(f"  completed trades      : {cov['completed_trades']} "
          f"(automated {cov['automated_completed_trades']}, "
          f"manual {cov['manual_completed_trades']})")
    print(f"  open positions excluded: {cov['positions_open']}")
    print(f"  orders/fills/rejections: {cov['orders']}/{cov['order_fills']}/"
          f"{cov['order_rejections']}")
    linkage_note = ("available" if cov["trade_signal_context_rows"]
                    else "empty -- linkage falls back to the strategy name")
    print(f"  stored signal linkage  : {cov['trade_signal_context_rows']} rows "
          f"({linkage_note})")

    inf = rep["infrastructure_evidence"]
    print("\n1. INFRASTRUCTURE / EXECUTION EVIDENCE")
    _print_stats("  submit->fill (s)      ", inf["latency"]["submit_to_fill_seconds"])
    _print_stats("  stop rest->fill (s)   ",
                 inf["latency"]["resting_stop_time_to_fill_seconds"])
    _print_stats("  fill->observed (s)    ", inf["latency"]["observation_lag_seconds"])
    _print_stats("  entry slippage (pts)  ", inf["entry_slippage_pts"])
    _print_stats("  exit slippage (pts)   ", inf["exit_slippage_pts"])
    _print_stats("  protection latency (s)", inf["protection_latency_seconds"])
    pc = inf["protection_coverage"]
    print(f"  protection coverage   : {pc['automated_with_stop']}/"
          f"{pc['automated_trades']} automated trades had a recorded stop")
    print(f"  exit routes           : {inf['exit_route_counts']}")
    idem = inf["idempotency"]
    print(f"  idempotency           : {'CLEAN' if idem['clean'] else 'VIOLATION'} "
          f"({idem['distinct_client_order_ids']} distinct of "
          f"{idem['orders_with_client_order_id']} ids)")
    rej = inf["rejections"]
    print(f"  rejections            : {rej['total']} {rej['by_error_code'] or ''}")
    rv = inf["realised_vs_configured_risk"]
    print(f"  risk (stop-closed n={rv['comparable_trades']}):")
    _print_stats("    configured (USD)    ", rv["configured_risk_usd"])
    _print_stats("    realised   (USD)    ", rv["realised_risk_usd"])
    _print_stats("    overrun    (USD)    ", rv["overrun_usd"])
    if "supplied_budget_usd" in rv:
        print(f"    vs supplied budget {rv['supplied_budget_usd']:.2f}: "
              f"{len(rv['over_supplied_budget'])} trade(s) above it")
    rec = inf["reconciliation"]
    print(f"  reconciliation        : {'CLEAN' if rec['clean'] else 'FINDINGS'}")
    for f in rec["findings"]:
        print(f"      - {f}")
    for m in rec["pnl_mismatches"]:
        print(f"      - pnl mismatch on trade {m['trade_id']}: "
              f"positions={m['positions_pnl']} fills={m['fills_pnl']}")

    d = rep["descriptive_observations"]
    print("\n2. DESCRIPTIVE TRADE OBSERVATIONS (this sample only)")
    print(f"  trades   : {d['trades']}  by strategy {d['by_strategy']}  "
          f"by direction {d['by_direction']}")
    print(f"  outcome  : {d['winners']}W / {d['losers']}L / {d['scratches']}S   "
          f"sum PnL {_f(d['realised_pnl_sum_usd'], '%+.5f')} USD")
    _print_stats("  PnL (USD)             ", d["realised_pnl_usd"])
    _print_stats("  holding (s)           ", d["holding_seconds"])
    _print_stats("  MAE (pts)             ", d["mae_pts"])
    _print_stats("  MFE (pts)             ", d["mfe_pts"])
    if d["trades_without_excursion_data"]:
        print(f"  no excursion data for {d['trades_without_excursion_data']} trade(s)")

    s = rep["strategy_evidence"]
    print("\n3. STRATEGY EVIDENCE")
    print(f"  automated completed trades: {s['automated_completed_trades_by_strategy']}")
    print(f"  sufficient for inference  : {s['sufficient_for_inference']}")
    print(f"  {s['notice']}")


def _print_stats(label: str, st: Optional[dict[str, float]]) -> None:
    if st is None:
        print(f"{label}: n/a")
        return
    print(f"{label}: n={st['n']:<3d} min={st['min']:+.4f} "
          f"med={st['median']:+.4f} max={st['max']:+.4f} mean={st['mean']:+.4f}")


def _f(v, spec="%.4f", na="n/a"):
    return na if v is None else spec % v


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Execution-quality report.")
    ap.add_argument("--trading-db", default=str(_DEFAULT_TRADING_DB))
    ap.add_argument("--forward-db", default=str(_DEFAULT_FORWARD_DB))
    ap.add_argument("--risk-budget-usd", type=float, default=None,
                    help="optional live limit, supplied by the operator; the "
                         "research tree never assumes it")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    rep = build_report(args.trading_db, args.forward_db, args.risk_budget_usd)
    if args.json:
        print(json.dumps(rep, indent=2, sort_keys=True, default=str))
    else:
        print_report(rep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
