"""
completed_trades.py
===================
The canonical completed-trade dataset: one row per closed position in
`btc_trading.db`, with every execution fact the live stack recorded about it.

Read-only. Writes nothing, anywhere.

SPINE AND CROSS-CHECK
---------------------
`positions` (status='closed') is the spine -- it is the runtime's own
completed-trade record and carries the authoritative realised PnL. `order_fills`
is joined onto it, and is *also* walked independently into position cycles so
the two derivations can be compared. Disagreement is reported as an integrity
finding, never silently reconciled: a dataset that quietly repairs its inputs
cannot be used to audit them.

The join key is exact. `positions.entry_ts` is written from the entry fill's
`processed_ts` and `positions.exit_ts` from the exit fill's, so equality holds
by construction. A row that fails to match is carried with `link_exact=False`
rather than dropped.

WHAT IS DERIVED, AND FROM WHAT
------------------------------
- **contract value** comes from the `instruments` row, not a constant. The
  research tree does not import `src.risk.limits`, so no live trading constant
  is duplicated here.
- **configured risk** is what the protective stop that actually existed was
  designed to cost: |entry - trigger| * contract_value * qty, plus the fees
  really paid. It is reconstructed from the recorded stop, not from the
  runtime's sizing config.
- **realised risk** is the loss actually taken, and is `None` for a winner.
  The two are only comparable when the stop was the thing that closed the
  trade; `risk_overrun_usd` is `None` otherwise.
- **risk budget** (`MAX_TRADE_RISK_USD`) appears nowhere in this module. It is
  a live trading limit, and a second copy of one inside the research tree is a
  second source of truth -- the same reason `trade_analysis.py` refuses to
  table the DSR minima. `execution_quality` takes it as an optional argument
  where the budget comparison is actually made.

LEAKAGE SAFETY
--------------
- Excursions use the window `(entry fill, exit fill]` only, measured on the
  exchange's own `exch_ts` so the reconciliation lag does not shift the window.
  The entry and exit fill prices are folded in, because the position really did
  trade at them. No price after the exit fill influences MAE/MFE, and none
  before the entry fill does.
- Signal context is the most recent observation that had become AVAILABLE by
  the entry instant: `day + 25h <= entry_ts`, the delay declared for
  `signal_log` in `alignment.timebase`. The day-D row is produced from day D's
  close at 01:00 UTC on D+1, so the admissible day is
  `(entry_ts - 25h).date()`. A calendar bound of `day < entry date` was used
  first and leaks for entries in the 00:00-01:00 UTC hour, where day D-1 has
  not been written yet. The exit-day signal is never read either.
- Open positions are excluded from the dataset entirely and reported only as
  coverage. An open trade has no outcome to describe.

DETERMINISM
-----------
Every query is explicitly ordered, nothing consults the wall clock, and all
arithmetic is on values read from the database. The same database yields a
byte-identical dataset on every run.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

_HERE = pathlib.Path(__file__).resolve().parent
_RESEARCH_ROOT = _HERE.parent
if str(_RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_ROOT))

from alignment.timebase import get_spec  # noqa: E402

_DEFAULT_TRADING_DB = _RESEARCH_ROOT.parent / "data" / "btc_trading.db"
_DEFAULT_FORWARD_DB = _RESEARCH_ROOT / "data" / "evidence_forward.db"

#: How long after a signal day begins before that day's row exists, taken from
#: the alignment layer's own declaration rather than restated here. `timebase`
#: models `signal_log` as event-dated on `day` with a structural availability
#: delay, and this module must not disagree with it about when an observation
#: became readable.
SIGNAL_AVAILABILITY_DELAY = timedelta(
    seconds=get_spec("signal_log").availability_offset_s)

MANUAL_STRATEGY = "manual"
PROTECTION_STRATEGY = "protection"


class DataUnavailable(RuntimeError):
    """A required table or row is absent from the trading database."""


@dataclass(frozen=True)
class CompletedTrade:
    trade_id: int
    strategy: str
    is_automated: bool
    instrument_id: int
    symbol: Optional[str]

    direction: int
    quantity: float
    contract_value: float

    entry_ts: str
    exit_ts: str
    entry_exch_ts: Optional[str]
    exit_exch_ts: Optional[str]
    holding_seconds: Optional[float]

    entry_price: float
    exit_price: float
    entry_order_price: Optional[float]
    exit_order_price: Optional[float]

    entry_fee: Optional[float]
    exit_fee: Optional[float]
    fees_total: Optional[float]
    realised_pnl: Optional[float]
    gross_pnl: Optional[float]

    configured_risk_usd: Optional[float]
    realised_risk_usd: Optional[float]
    risk_overrun_usd: Optional[float]

    entry_slippage_pts: Optional[float]
    exit_slippage_pts: Optional[float]

    mae_pts: Optional[float]
    mfe_pts: Optional[float]
    mae_pct: Optional[float]
    mfe_pct: Optional[float]
    excursion_samples: int

    stop_client_order_id: Optional[str]
    stop_trigger_price: Optional[float]
    stop_limit_price: Optional[float]
    stop_placed_ts: Optional[str]
    protection_latency_seconds: Optional[float]
    exited_via: str

    signal_id: Optional[str]
    signal_day: Optional[str]
    signal_position: Optional[float]
    signal_raw_value: Optional[float]
    linkage_source: Optional[str]

    entry_delta_order_id: Optional[str]
    exit_delta_order_id: Optional[str]
    entry_client_order_id: Optional[str]
    exit_client_order_id: Optional[str]
    fill_ids: tuple = field(default=())
    link_exact: bool = True


def connect_ro(db_path) -> sqlite3.Connection:
    """Open a database strictly read-only. Never creates a file."""
    p = pathlib.Path(db_path).resolve()
    if not p.exists():
        raise DataUnavailable(f"database not found: {p}")
    conn = sqlite3.connect(f"{p.as_uri()}?mode=ro", uri=True, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def parse_ts(value: Optional[str]) -> Optional[datetime]:
    """Parse the two ISO-8601 spellings the runtime writes.

    `processed_ts` carries a '+00:00' offset while exchange-sourced `exch_ts`
    ends in 'Z'. Both denote UTC; a naive value is assumed UTC rather than
    local, because every writer in the runtime stamps UTC.
    """
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def seconds_between(a: Optional[str], b: Optional[str]) -> Optional[float]:
    ta, tb = parse_ts(a), parse_ts(b)
    if ta is None or tb is None:
        return None
    return (tb - ta).total_seconds()


def _norm_ts(value: Optional[str]) -> Optional[str]:
    """Re-spell a timestamp in the '+00:00' form the stored columns use.

    `exch_ts` ends in 'Z' while every stored `ts` carries an offset. Comparing
    the two spellings as strings is only accidentally correct, so bounds are
    normalised before they reach a SQL comparison.
    """
    dt = parse_ts(value)
    return None if dt is None else dt.isoformat()


def has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _direction(side: str) -> int:
    return 1 if str(side).lower() == "buy" else -1


def favourable_slippage(order_side: str, order_price: Optional[float],
                        fill_price: Optional[float]) -> Optional[float]:
    """Signed slippage in price points, positive when the fill beat the order.

    Keyed on the order's own side, not on the position's direction: a buy is
    improved by filling lower, a sell by filling higher, and that holds whether
    the order opens or closes a position.
    """
    if order_price is None or fill_price is None:
        return None
    if str(order_side).lower() == "buy":
        return order_price - fill_price
    return fill_price - order_price


def contract_value_for(conn: sqlite3.Connection, instrument_id: int) -> Optional[float]:
    """Contract value from the instrument record, so no live constant is copied."""
    if not has_table(conn, "instruments"):
        return None
    row = conn.execute(
        "SELECT contract_value FROM instruments WHERE id=?", (instrument_id,)
    ).fetchone()
    if row is None or row["contract_value"] is None:
        return None
    return float(row["contract_value"])


def load_fills(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    if not has_table(conn, "order_fills"):
        return []
    return list(conn.execute(
        "SELECT * FROM order_fills ORDER BY processed_ts, fill_id"))


def _load_orders_by_delta_id(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    out: dict[str, sqlite3.Row] = {}
    for row in conn.execute(
            "SELECT * FROM orders WHERE delta_order_id IS NOT NULL ORDER BY id"):
        out[str(row["delta_order_id"])] = row
    return out


def _load_stops_by_delta_id(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    out: dict[str, sqlite3.Row] = {}
    if not has_table(conn, "protective_stops"):
        return out
    for row in conn.execute(
            "SELECT * FROM protective_stops ORDER BY placed_ts, client_order_id"):
        if row["delta_order_id"] is not None:
            out[str(row["delta_order_id"])] = row
    return out


def fill_cycles(fills: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    """Segment the fill ledger into position cycles, flat to flat.

    Independent of the `positions` table on purpose: this is the derivation the
    dataset is checked against. A cycle opens on the first fill off a flat book
    and closes when net size returns to zero.
    """
    cycles: list[dict[str, Any]] = []
    net = 0.0
    current: list[sqlite3.Row] = []
    for f in fills:
        signed = float(f["size"]) * _direction(f["side"])
        if net == 0.0 and not current:
            current = []
        current.append(f)
        net += signed
        if abs(net) < 1e-12 and current:
            cycles.append({
                "fills": list(current),
                "entry_processed_ts": current[0]["processed_ts"],
                "exit_processed_ts": current[-1]["processed_ts"],
                "realised_pnl": sum(
                    float(x["realised_pnl"]) for x in current
                    if x["realised_pnl"] is not None),
            })
            current = []
            net = 0.0
    if current:
        cycles.append({
            "fills": list(current),
            "entry_processed_ts": current[0]["processed_ts"],
            "exit_processed_ts": None,
            "realised_pnl": None,
        })
    return cycles


def _spot_window(conn: sqlite3.Connection, start_ts: str, end_ts: str
                 ) -> tuple[Optional[float], Optional[float], int]:
    """min/max spot strictly after `start_ts` and up to `end_ts`.

    Uses whichever spot surface the database carries: `spot_series` in an
    extracted slice, `option_quotes.spot_price` in the full production
    database. Both are the same field; the slice is a projection of it.
    """
    if has_table(conn, "spot_series"):
        sql = ("SELECT MIN(spot_price), MAX(spot_price), COUNT(*) FROM spot_series "
               "WHERE ts > ? AND ts <= ?")
    elif has_table(conn, "option_quotes"):
        sql = ("SELECT MIN(spot_price), MAX(spot_price), COUNT(*) FROM option_quotes "
               "WHERE ts > ? AND ts <= ? AND spot_price IS NOT NULL")
    else:
        return None, None, 0
    lo, hi, n = conn.execute(sql, (start_ts, end_ts)).fetchone()
    if n == 0 or lo is None or hi is None:
        return None, None, 0
    return float(lo), float(hi), int(n)


def _signal_context(fwd: Optional[sqlite3.Connection], signal_id: Optional[str],
                    entry_ts: str) -> tuple[Optional[str], Optional[float], Optional[float]]:
    """The most recent signal observation that existed when the trade entered.

    Bounded by AVAILABILITY, not by the calendar: a day-D observation is
    computed from day D's close and written by the collector at 01:00 UTC on
    D+1, so it is admissible only once ``day + SIGNAL_AVAILABILITY_DELAY`` has
    passed. The latest day that can satisfy that at `entry_ts` is therefore
    ``(entry_ts - SIGNAL_AVAILABILITY_DELAY).date()``, and the bound is
    inclusive because an observation available at exactly the entry instant was
    available.

    `day < entry date` was the previous rule, and it is wrong for the first
    hour of a UTC day: day D-1 does not exist until 01:00 on D, so an entry at
    00:xx on D could only have seen D-2. It is reachable -- the real order
    placed 2026-09-02T00:01:20Z would have been attributed signal day
    2026-09-01, written 58 minutes AFTER it was submitted. The alignment layer
    already models this correctly; this function used to disagree with it.
    """
    if fwd is None or not signal_id:
        return None, None, None
    entry_dt = parse_ts(entry_ts)
    if entry_dt is None:
        return None, None, None
    latest_available_day = (entry_dt - SIGNAL_AVAILABILITY_DELAY).date()
    row = fwd.execute(
        "SELECT day, position, raw_value FROM signal_log "
        "WHERE signal_id=? AND day<=? ORDER BY day DESC LIMIT 1",
        (signal_id, latest_available_day.isoformat()),
    ).fetchone()
    if row is None:
        return None, None, None
    return (row["day"],
            None if row["position"] is None else float(row["position"]),
            None if row["raw_value"] is None else float(row["raw_value"]))


def _stored_linkage(conn: sqlite3.Connection, client_order_id: Optional[str]
                    ) -> Optional[sqlite3.Row]:
    if not client_order_id or not has_table(conn, "trade_signal_context"):
        return None
    return conn.execute(
        "SELECT * FROM trade_signal_context WHERE client_order_id=?",
        (client_order_id,),
    ).fetchone()


def build_dataset(trading_db, forward_db=None) -> list[CompletedTrade]:
    """One CompletedTrade per closed position, oldest first."""
    conn = connect_ro(trading_db)
    fwd = None
    if forward_db is not None:
        fwd_path = pathlib.Path(forward_db)
        if fwd_path.exists():
            fwd = connect_ro(fwd_path)
    try:
        return _build(conn, fwd)
    finally:
        conn.close()
        if fwd is not None:
            fwd.close()


def _build(conn: sqlite3.Connection,
           fwd: Optional[sqlite3.Connection]) -> list[CompletedTrade]:
    if not has_table(conn, "positions"):
        raise DataUnavailable("positions table absent")

    fills = load_fills(conn)
    by_entry = {str(f["processed_ts"]): f for f in fills if f["role"] == "entry"}
    by_exit = {str(f["processed_ts"]): f for f in fills if f["role"] == "exit"}
    orders = _load_orders_by_delta_id(conn)
    stops = _load_stops_by_delta_id(conn)

    out: list[CompletedTrade] = []
    positions = conn.execute(
        "SELECT * FROM positions WHERE status='closed' "
        "AND exit_ts IS NOT NULL ORDER BY entry_ts, id").fetchall()

    for p in positions:
        entry_ts, exit_ts = str(p["entry_ts"]), str(p["exit_ts"])
        ef, xf = by_entry.get(entry_ts), by_exit.get(exit_ts)
        link_exact = ef is not None and xf is not None

        instrument_id = int(p["instrument_id"])
        cv = contract_value_for(conn, instrument_id)
        symbol_row = conn.execute(
            "SELECT symbol FROM instruments WHERE id=?", (instrument_id,)
        ).fetchone() if has_table(conn, "instruments") else None
        symbol = symbol_row["symbol"] if symbol_row else None

        # positions.quantity is 0.0 on the pre-execution-stack rows; the fill
        # ledger is authoritative for size wherever it is present.
        qty = float(ef["size"]) if ef is not None else float(p["quantity"] or 0.0)
        direction = _direction(p["side"])

        entry_price = float(p["entry_price"])
        exit_price = float(p["exit_price"])

        entry_order = orders.get(str(ef["delta_order_id"])) if ef is not None else None
        exit_order = orders.get(str(xf["delta_order_id"])) if xf is not None else None
        stop = stops.get(str(xf["delta_order_id"])) if xf is not None else None

        # The stop that protected this position, whether or not it closed it.
        stop_row = stop
        if stop_row is None and has_table(conn, "protective_stops"):
            stop_row = conn.execute(
                "SELECT * FROM protective_stops WHERE position_id=? "
                "ORDER BY placed_ts LIMIT 1", (p["id"],)).fetchone()

        entry_fee = float(ef["fee"]) if ef is not None and ef["fee"] is not None else None
        exit_fee = float(xf["fee"]) if xf is not None and xf["fee"] is not None else None
        fees_total = None
        if entry_fee is not None or exit_fee is not None:
            fees_total = (entry_fee or 0.0) + (exit_fee or 0.0)

        realised = None if p["pnl"] is None else float(p["pnl"])
        gross = None
        if cv is not None and qty:
            gross = (exit_price - entry_price) * direction * cv * qty

        configured_risk = None
        if stop_row is not None and cv is not None and qty:
            configured_risk = (abs(entry_price - float(stop_row["trigger_price"]))
                               * cv * qty + (fees_total or 0.0))

        exited_via = "protective_stop" if stop is not None else (
            "other" if xf is not None else "unknown")

        realised_risk = None if realised is None or realised >= 0 else -realised
        overrun = None
        if (realised_risk is not None and configured_risk is not None
                and exited_via == "protective_stop"):
            overrun = realised_risk - configured_risk

        entry_slip = favourable_slippage(
            p["side"],
            None if entry_order is None else entry_order["price"],
            None if ef is None else float(ef["price"]))
        exit_side = "sell" if direction == 1 else "buy"
        exit_reference = None
        if stop is not None:
            exit_reference = float(stop["trigger_price"])
        elif exit_order is not None and exit_order["price"] is not None:
            exit_reference = float(exit_order["price"])
        exit_slip = favourable_slippage(
            exit_side, exit_reference, None if xf is None else float(xf["price"]))

        # Bound the excursion by when the fills actually happened, not by when
        # the reconciler noticed them: processed_ts trails exch_ts by up to one
        # reconciliation interval (~60s), which would otherwise attribute the
        # opening minute of one trade and the closing minute of another to the
        # wrong window.
        exc_start = _norm_ts(ef["exch_ts"] if ef is not None else None) or entry_ts
        exc_end = _norm_ts(xf["exch_ts"] if xf is not None else None) or exit_ts
        lo, hi, n = _spot_window(conn, exc_start, exc_end)

        # The traded prices are themselves excursions: the position really did
        # experience its own exit price, and on a stop-out that is usually the
        # extreme. Sampled spot is minute-cadence, so the fill can easily fall
        # between two samples -- folding the fills in makes MAE/MFE correct at
        # the boundaries instead of dependent on sampling luck.
        prices = [entry_price, exit_price]
        if lo is not None and hi is not None:
            prices.extend((lo, hi))
        excursions = [(px - entry_price) * direction for px in prices]
        mae = min(min(excursions), 0.0)
        mfe = max(max(excursions), 0.0)
        mae_pct = mae / entry_price * 100.0
        mfe_pct = mfe / entry_price * 100.0

        entry_coid = ef["client_order_id"] if ef is not None else None
        if entry_coid is None and entry_order is not None:
            entry_coid = entry_order["client_order_id"]
        exit_coid = xf["client_order_id"] if xf is not None else None
        if exit_coid is None and exit_order is not None:
            exit_coid = exit_order["client_order_id"]

        stored = _stored_linkage(conn, entry_coid)
        if stored is not None:
            signal_id = stored["signal_id"]
            linkage_source = "trade_signal_context"
            signal_day = stored["signal_day"]
            signal_pos = None if stored["direction"] is None else float(stored["direction"])
            signal_raw = None if stored["raw_value"] is None else float(stored["raw_value"])
        else:
            strategy = str(p["strategy"] or "")
            signal_id = strategy if strategy and strategy != MANUAL_STRATEGY else None
            linkage_source = "strategy_name" if signal_id else None
            signal_day, signal_pos, signal_raw = _signal_context(fwd, signal_id, entry_ts)

        out.append(CompletedTrade(
            trade_id=int(p["id"]),
            strategy=str(p["strategy"] or ""),
            is_automated=bool(p["strategy"]) and p["strategy"] != MANUAL_STRATEGY,
            instrument_id=instrument_id,
            symbol=symbol,
            direction=direction,
            quantity=qty,
            contract_value=cv if cv is not None else float("nan"),
            entry_ts=entry_ts,
            exit_ts=exit_ts,
            entry_exch_ts=None if ef is None else ef["exch_ts"],
            exit_exch_ts=None if xf is None else xf["exch_ts"],
            holding_seconds=seconds_between(entry_ts, exit_ts),
            entry_price=entry_price,
            exit_price=exit_price,
            entry_order_price=None if entry_order is None or entry_order["price"] is None
            else float(entry_order["price"]),
            exit_order_price=None if exit_order is None or exit_order["price"] is None
            else float(exit_order["price"]),
            entry_fee=entry_fee,
            exit_fee=exit_fee,
            fees_total=fees_total,
            realised_pnl=realised,
            gross_pnl=gross,
            configured_risk_usd=configured_risk,
            realised_risk_usd=realised_risk,
            risk_overrun_usd=overrun,
            entry_slippage_pts=entry_slip,
            exit_slippage_pts=exit_slip,
            mae_pts=mae,
            mfe_pts=mfe,
            mae_pct=mae_pct,
            mfe_pct=mfe_pct,
            excursion_samples=n,
            stop_client_order_id=None if stop_row is None else stop_row["client_order_id"],
            stop_trigger_price=None if stop_row is None else float(stop_row["trigger_price"]),
            stop_limit_price=None if stop_row is None else float(stop_row["limit_price"]),
            stop_placed_ts=None if stop_row is None else stop_row["placed_ts"],
            protection_latency_seconds=None if stop_row is None
            else seconds_between(entry_ts, stop_row["placed_ts"]),
            exited_via=exited_via,
            signal_id=signal_id,
            signal_day=signal_day,
            signal_position=signal_pos,
            signal_raw_value=signal_raw,
            linkage_source=linkage_source,
            entry_delta_order_id=None if ef is None else ef["delta_order_id"],
            exit_delta_order_id=None if xf is None else xf["delta_order_id"],
            entry_client_order_id=entry_coid,
            exit_client_order_id=exit_coid,
            fill_ids=tuple(x["fill_id"] for x in (ef, xf) if x is not None),
            link_exact=link_exact,
        ))

    return out


def coverage(trading_db) -> dict[str, Any]:
    """What the dataset does and does not cover, counted from the same database."""
    conn = connect_ro(trading_db)
    try:
        def n(table: str, where: str = "") -> int:
            if not has_table(conn, table):
                return -1
            return conn.execute(
                f"SELECT COUNT(*) FROM {table} {where}").fetchone()[0]

        closed = n("positions", "WHERE status='closed' AND exit_ts IS NOT NULL")
        automated = -1
        if has_table(conn, "positions"):
            automated = conn.execute(
                "SELECT COUNT(*) FROM positions WHERE status='closed' "
                "AND exit_ts IS NOT NULL AND strategy IS NOT NULL AND strategy<>?",
                (MANUAL_STRATEGY,)).fetchone()[0]
        return {
            "positions_total": n("positions"),
            "positions_closed": closed,
            "positions_open": n("positions", "WHERE status='open'"),
            "completed_trades": closed,
            "automated_completed_trades": automated,
            "manual_completed_trades": (closed - automated) if closed >= 0 and automated >= 0 else -1,
            "orders": n("orders"),
            "order_fills": n("order_fills"),
            "order_rejections": n("order_rejections"),
            "protective_stops": n("protective_stops"),
            "trade_signal_context_rows": n("trade_signal_context"),
            "has_spot_surface": has_table(conn, "spot_series") or has_table(conn, "option_quotes"),
        }
    finally:
        conn.close()


def to_records(trades: Iterable[CompletedTrade]) -> list[dict[str, Any]]:
    records = []
    for t in trades:
        d = asdict(t)
        d["fill_ids"] = list(d["fill_ids"])
        records.append(d)
    return records


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[2])
    ap.add_argument("--trading-db", default=str(_DEFAULT_TRADING_DB))
    ap.add_argument("--forward-db", default=str(_DEFAULT_FORWARD_DB))
    ap.add_argument("--json", action="store_true", help="emit the dataset as JSON")
    args = ap.parse_args(argv)

    trades = build_dataset(args.trading_db, args.forward_db)
    cov = coverage(args.trading_db)

    if args.json:
        print(json.dumps({"coverage": cov, "trades": to_records(trades)},
                         indent=2, sort_keys=True))
        return 0

    print(f"completed trades: {len(trades)}  "
          f"(automated {cov['automated_completed_trades']}, "
          f"manual {cov['manual_completed_trades']}; "
          f"open excluded: {cov['positions_open']})")
    for t in trades:
        tag = "auto" if t.is_automated else "manual"
        print(f"\n  #{t.trade_id} [{tag}] {t.strategy or '-'}  "
              f"{'long' if t.direction == 1 else 'short'} x{t.quantity:g}")
        print(f"    {t.entry_ts} @ {t.entry_price:.1f}  ->  "
              f"{t.exit_ts} @ {t.exit_price:.1f}   "
              f"held {(t.holding_seconds or 0)/60:.1f} min")
        print(f"    pnl={_f(t.realised_pnl,'%+.5f')}  fees={_f(t.fees_total,'%.5f')}  "
              f"gross={_f(t.gross_pnl,'%+.5f')}")
        print(f"    risk: configured={_f(t.configured_risk_usd,'%.5f')}  "
              f"realised={_f(t.realised_risk_usd,'%.5f')}  "
              f"overrun={_f(t.risk_overrun_usd,'%+.5f')}")
        print(f"    slippage: entry={_f(t.entry_slippage_pts,'%+.1f')}pts  "
              f"exit={_f(t.exit_slippage_pts,'%+.1f')}pts   exited_via={t.exited_via}")
        print(f"    excursion: MAE={_f(t.mae_pts,'%+.1f')}pts "
              f"({_f(t.mae_pct,'%+.3f')}%)  MFE={_f(t.mfe_pts,'%+.1f')}pts "
              f"({_f(t.mfe_pct,'%+.3f')}%)  n={t.excursion_samples}")
        print(f"    protection: {t.stop_client_order_id or '-'} "
              f"trigger={_f(t.stop_trigger_price,'%.1f')} "
              f"latency={_f(t.protection_latency_seconds,'%.1f')}s")
        print(f"    linkage: {t.linkage_source or 'none'} "
              f"signal={t.signal_id or '-'} day={t.signal_day or '-'} "
              f"pos={_f(t.signal_position,'%+.1f')}")
    return 0


def _f(v, spec="%.4f", na="n/a"):
    return na if v is None else spec % v


if __name__ == "__main__":
    raise SystemExit(main())
