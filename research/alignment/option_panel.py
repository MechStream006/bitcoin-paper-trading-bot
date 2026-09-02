"""
option_panel.py
===============
Panel reductions over `option_quotes`: the BTC index, and the nearest-expiry
ATM implied vol.

Read-only. Writes nothing.

WHY A REDUCTION LAYER RATHER THAN A PANEL KEY
---------------------------------------------
`option_quotes` is a panel: ~56 instruments share every snapshot timestamp. The
feature layer resolves a panel by naming one member, which was fine while a
feature meant "this instrument". It cannot express "the index" or "the ATM
pair", because neither is a member -- both are functions *of* the panel at a
cycle. Rather than teach the feature layer to reduce panels, this module emits
already-reduced single series as ordinary `Observation`s, so `build_row` stays
unchanged and the as-of, tolerance and look-ahead machinery all still apply.

That also replaces the previous fixed `instrument_id` dependency. No option
instrument survives across dates: over 2026-08-19..09-02, 0 of 961 instruments
span the window and the longest life is ~3 days, because the collector's
universe is ATM+-5 over the nearest 3 expiries and rolls daily. A feature keyed
to one id is therefore unresolvable at any earlier date by construction; the
selection rule is resolved per cycle instead.

THE ATM RULE IS THE COLLECTOR'S OWN
-----------------------------------
`src/exchange/instruments.select_universe` picks ATM by ``min |strike - mark|``
at the earliest settlement times, and returns both the call and the put at each
strike. The rule below is the historical analogue of that live rule, so the
offline feature agrees with how the data came to exist.

TWO LOOK-AHEAD TRAPS, BOTH AVOIDED
----------------------------------
1. Candidate instruments are taken from the **quote panel at the cycle**, never
   from a standing instrument list. An instrument not yet listed has no quote
   row, so it cannot be selected. This is self-limiting rather than filtered.
2. `instruments.state` is NEVER consulted. It is a current-snapshot field: all
   961 instruments in the extracted window read ``state='live'``, including 439
   that had already expired by 2026-08-25. Filtering on it would silently admit
   knowledge of what is live *now* into a historical decision.

Nothing here interpolates or forward-fills. A cycle whose ATM pair is
incomplete simply yields no observation, and the feature layer reports
INCOMPLETE_DATA rather than reaching for a neighbouring cycle.
"""
from __future__ import annotations

import pathlib
import sqlite3
import statistics
import sys
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence

_HERE = pathlib.Path(__file__).resolve().parent
_RESEARCH_ROOT = _HERE.parent
if str(_RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_ROOT))

from alignment.align import Observation, SourceLoad  # noqa: E402
from alignment.timebase import to_utc  # noqa: E402

CALL = "call_options"
PUT = "put_options"

#: An implied vol of zero (or below) is not a measurement -- it is a degenerate
#: or absent quote. CLAUDE.md sec.11 documents exactly this on testnet ("ask_iv
#: as low as 0.000005, degenerate bid/ask"), and it reaches the money: 1,245 of
#: 20,870 cycles (5.97%) in 2026-08-19..09-02 have at least one ATM leg at 0.0.
#: The test must be per LEG, not on the mid: one cycle carries call 0.0184 with
#: put 0.0000, whose mid 0.0092 is positive and would otherwise pass.
MIN_VALID_IV = 0.0


@dataclass(frozen=True)
class PanelRow:
    """One `option_quotes` row joined to its instrument's static terms."""

    instrument_id: int
    ts: str
    exch_ts: Optional[int]
    spot_price: Optional[float]
    mark_iv: Optional[float]
    strike_price: Optional[float]
    settlement_time: Optional[str]
    contract_type: Optional[str]
    symbol: Optional[str] = None


def load_panel(db_path, lo: str | None = None, hi: str | None = None
               ) -> list[PanelRow]:
    """Read the quote panel joined to instrument terms, oldest first.

    The join is on `instrument_id` only. `instruments.state` is deliberately
    not selected, so it cannot be used downstream by accident.
    """
    p = pathlib.Path(db_path).resolve()
    con = sqlite3.connect(f"{p.as_uri()}?mode=ro", uri=True, timeout=10.0)
    con.row_factory = sqlite3.Row
    try:
        sql = ("SELECT q.instrument_id, q.ts, q.exch_ts, q.spot_price, q.mark_iv, "
               "       i.strike_price, i.settlement_time, i.contract_type, i.symbol "
               "FROM option_quotes q LEFT JOIN instruments i ON i.id = q.instrument_id")
        where, params = [], []
        if lo:
            where.append("q.ts >= ?")
            params.append(lo)
        if hi:
            where.append("q.ts <= ?")
            params.append(hi)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY q.ts, q.instrument_id"
        return [PanelRow(**{k: r[k] for k in (
            "instrument_id", "ts", "exch_ts", "spot_price", "mark_iv",
            "strike_price", "settlement_time", "contract_type", "symbol")})
            for r in con.execute(sql, params)]
    finally:
        con.close()


def _by_cycle(rows: Iterable[PanelRow]) -> list[tuple[str, list[PanelRow]]]:
    cycles: dict[str, list[PanelRow]] = {}
    for r in rows:
        cycles.setdefault(str(r.ts), []).append(r)
    return [(ts, cycles[ts]) for ts in sorted(cycles)]


def cycle_spot(rows: Sequence[PanelRow]) -> Optional[float]:
    """Median `spot_price` across the cycle.

    Each ticker carries its own snapshot of the index, so a cycle can hold more
    than one value -- 259 of 5,000 sampled instants held two, differing by
    0.2-5.8 points. The median is robust to that and, unlike naming one
    instrument, is defined for every cycle.
    """
    vals = sorted(r.spot_price for r in rows if r.spot_price is not None)
    return statistics.median(vals) if vals else None


def spot_index_observations(rows: Iterable[PanelRow]) -> list[Observation]:
    """The BTC index per cycle, as a single instrument-independent series."""
    out: list[Observation] = []
    for ts, cyc in _by_cycle(rows):
        spot = cycle_spot(cyc)
        if spot is None:
            continue
        avail = to_utc(ts, "iso")
        if avail is None:
            continue
        events = [to_utc(r.exch_ts, "us") for r in cyc if r.exch_ts is not None]
        out.append(Observation(
            source="spot_index",
            event_ts=max([e for e in events if e is not None], default=None),
            available_ts=avail,
            payload={"spot_price": spot},
            provenance={"cycle_ts": ts, "panel_size": len(cyc),
                        "spot_observations": len([r for r in cyc
                                                  if r.spot_price is not None])},
        ))
    out.sort(key=lambda o: o.available_ts)
    return out


@dataclass(frozen=True)
class AtmPair:
    call: PanelRow
    put: PanelRow
    strike: float
    spot: float
    expiry: str
    tau_hours: float
    abs_moneyness: float


def select_atm_pair(rows: Sequence[PanelRow], cycle_ts: str) -> Optional[AtmPair]:
    """The nearest-expiry ATM call/put pair at one cycle, or None.

    Deterministic throughout: the expiry is the minimum `settlement_time`
    represented in this cycle, the strike is the call strike minimising
    ``|K - spot|`` with the LOWER strike winning a tie, and the put must exist
    at that exact strike and expiry. Any missing leg yields None -- the caller
    reports absence rather than substituting the other side.
    """
    spot = cycle_spot(rows)
    if spot is None:
        return None
    dated = [r for r in rows if r.settlement_time and r.strike_price is not None]
    if not dated:
        return None
    expiry = min(r.settlement_time for r in dated)
    at_expiry = [r for r in dated if r.settlement_time == expiry]
    calls = [r for r in at_expiry if r.contract_type == CALL]
    puts = {float(r.strike_price): r for r in at_expiry if r.contract_type == PUT}
    if not calls:
        return None
    # Lower strike wins a tie: sorting by (distance, strike) makes that explicit
    # rather than depending on the input order.
    call = sorted(calls, key=lambda r: (abs(float(r.strike_price) - spot),
                                        float(r.strike_price)))[0]
    strike = float(call.strike_price)
    put = puts.get(strike)
    if put is None:
        return None
    expiry_dt, cycle_dt = to_utc(expiry, "iso"), to_utc(cycle_ts, "iso")
    if expiry_dt is None or cycle_dt is None:
        return None
    return AtmPair(
        call=call, put=put, strike=strike, spot=spot, expiry=expiry,
        tau_hours=(expiry_dt - cycle_dt).total_seconds() / 3600.0,
        abs_moneyness=abs(strike - spot),
    )


def atm_iv_observations(rows: Iterable[PanelRow]) -> list[Observation]:
    """Nearest-expiry ATM implied vol per cycle, as the call/put pair mid.

    Both legs must quote a strictly positive implied vol; see `MIN_VALID_IV`.
    A cycle failing that yields no observation at all, so the feature reports
    INCOMPLETE_DATA rather than a spurious READY 0.0.

    The mid is used rather than either leg because put-call parity implies a
    single ATM implied vol: measured across the six real automated decisions the
    two legs differed by 0.005-0.020 against a cross-date range of 0.24, while
    each side's book is independently degenerate. Averaging is side-neutral, so
    the feature carries no directional bias.
    """
    out: list[Observation] = []
    for ts, cyc in _by_cycle(rows):
        pair = select_atm_pair(cyc, ts)
        if pair is None:
            continue
        legs = (pair.call.mark_iv, pair.put.mark_iv)
        if any(v is None or float(v) <= MIN_VALID_IV for v in legs):
            # Absent, not zero. Emitting no observation lets the feature layer
            # report INCOMPLETE_DATA, which is the honest answer; a 0.0 written
            # as READY would be a wrong number inside a feature corpus.
            continue
        avail = to_utc(ts, "iso")
        if avail is None:
            continue
        events = [to_utc(pair.call.exch_ts, "us"), to_utc(pair.put.exch_ts, "us")]
        known = [e for e in events if e is not None]
        out.append(Observation(
            source="atm_iv",
            # The pair is only complete once BOTH legs exist, so the later leg
            # is the event time of the reduction.
            event_ts=max(known) if known else None,
            available_ts=avail,
            payload={"atm_iv": (float(pair.call.mark_iv) + float(pair.put.mark_iv)) / 2.0,
                     "call_iv": float(pair.call.mark_iv),
                     "put_iv": float(pair.put.mark_iv)},
            provenance={
                "call_id": pair.call.instrument_id,
                "put_id": pair.put.instrument_id,
                "strike": pair.strike,
                "spot": pair.spot,
                "expiry": pair.expiry,
                "tau_hours": pair.tau_hours,
                "abs_moneyness": pair.abs_moneyness,
                "cycle_ts": ts,
            },
        ))
    out.sort(key=lambda o: o.available_ts)
    return out


def load_derived_sources(db_path, lo: str | None = None, hi: str | None = None
                         ) -> dict[str, SourceLoad]:
    """Both derived series from one pass over the panel.

    `rows_without_availability` counts ONLY cycles that could not be placed in
    time. A cycle dropped for an incomplete or invalid ATM pair is not one of
    them: its clock was fine and its value was not. The distinction is load
    bearing -- `SourceLoad.unusable_clock` drives the UNKNOWN state, which
    fails closed because the store cannot say WHEN anything arrived, whereas a
    validity drop is ordinary absence and must read INCOMPLETE_DATA.
    """
    rows = load_panel(db_path, lo, hi)
    spot = spot_index_observations(rows)
    atm = atm_iv_observations(rows)
    cycle_ts = {r.ts for r in rows}
    cycles = len(cycle_ts)
    unclocked = sum(1 for ts in cycle_ts if to_utc(ts, "iso") is None)
    return {
        "spot_index": SourceLoad("spot_index", tuple(spot), cycles, unclocked),
        "atm_iv": SourceLoad("atm_iv", tuple(atm), cycles, unclocked),
    }
