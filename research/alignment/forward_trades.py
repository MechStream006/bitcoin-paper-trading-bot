"""
forward_trades.py
=================
The forward trade ledger reduced to one series: which direction was being
*held* at each instant, and therefore which direction a decision taken at that
instant actually consumed.

Read-only. Writes nothing.

WHY THIS EXISTS ALONGSIDE `signal_position`
-------------------------------------------
`signal_position` reads `signal_log.position` -- the latest DAILY signal. The
live execution path does not read that. `bridge.latest_direction()` reads::

    SELECT direction FROM trade_entries WHERE signal_id=? AND open=1
    ORDER BY entry_date DESC LIMIT 1

which is the direction of the trade currently HELD. The two are equal at an
entry, because `runner_base` sets ``direction = int(position)`` for
``entry_date == run_date`` inside one transaction, and they drift apart during
the hold, because the collector never exits early ("hold until
planned_exit_date regardless of signal changes"). Measured over the forward
corpus they disagree on 25 of 236 held-days (10.6%), and 3 of the 8 real
execution decisions were taken inside such a window.

Neither reading is wrong. They answer different questions, and the corpus
previously recorded only the first. This module supplies the second, so a
decision can be studied against the value it actually consumed.

WHY THE CLOSING INSTANT COMES FROM `run_log`, NOT FROM `planned_exit_date`
--------------------------------------------------------------------------
A trade is closed by the first collector run whose `run_date` reaches its
`planned_exit_date` -- not by the calendar. When the collector stops, the trade
stays open and `latest_direction()` keeps returning it. That is not
hypothetical: `trade_entries.id=18` (g3, entry 2026-07-13) has
``planned_exit_date='2026-07-20'`` and ``trade_exits.exit_date='2026-07-20'``,
but INC-0005 silenced the collector from 2026-07-18 to 2026-08-05, so the first
run able to close it was ``run_date='2026-08-06'`` and the position was
genuinely held until 2026-08-07 01:00 UTC -- 17 days past its nominal exit.
`exit_date` records the date an exit is PRICED at, never the date it happened.

Using `planned_exit_date` as the closing instant would therefore invent a flat
interval the runtime never had, and would misreport the held direction across
the whole outage.

CADENCE: ONE OBSERVATION PER RUN, NOT ONE PER CHANGE
----------------------------------------------------
The held direction is a step function, so sampling it only where it changes
would give identical as-of values. It is sampled at EVERY successful run
instead, because that makes `age_s` mean the same thing here as it does for
`signal_position` -- how long since the collector last spoke -- so the two
features share one staleness vocabulary and go stale together when collection
stops. Sampling only at changes would have made a six-day-old hold look stale
while the system was working perfectly.

FLAT IS 0.0, ABSENT IS ABSENT
-----------------------------
A run that leaves nothing held emits ``direction = 0.0``, not NULL. Flat is an
observed state of the ledger, not missing data, and `strategy_jobs` already
treats `None` and `0` identically (``if direction is None or direction == 0``).
Before the first run of a signal there is no observation at all, which resolves
to INCOMPLETE_DATA -- the ledger genuinely had nothing to say yet. Encoding
flat as NULL would have collapsed those two distinct answers into one state.
"""
from __future__ import annotations

import pathlib
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Optional, Sequence

_HERE = pathlib.Path(__file__).resolve().parent
_RESEARCH_ROOT = _HERE.parent
if str(_RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_ROOT))

from alignment.align import Observation, SourceLoad  # noqa: E402
from alignment.timebase import DATE, FORWARD_COLLECTOR_UTC_HOUR, to_utc  # noqa: E402

#: A run for `run_date` D executes at D+1 at the collector's UTC hour, so
#: everything it wrote becomes readable then. Identical to the offset the
#: timebase declares for `signal_log` and `forward_trade_entries`.
RUN_AVAILABILITY_OFFSET = timedelta(hours=24 + FORWARD_COLLECTOR_UTC_HOUR)

#: Only an 'ok' run reaches the transaction that opens and closes trades;
#: `runner_base._record_mini` writes other statuses without touching the ledger.
OK = "ok"

FLAT = 0.0


@dataclass(frozen=True)
class TradeRow:
    """One `trade_entries` row, with the open flag it carries."""

    entry_id: int
    signal_id: str
    entry_date: str
    direction: int
    planned_exit_date: str
    open: int


@dataclass(frozen=True)
class RunRow:
    signal_id: str
    run_date: str
    status: str


@dataclass(frozen=True)
class HoldInterval:
    """When one trade was actually held: ``[open_from, open_to)``.

    `open_to` is None while the trade is still held -- an interval with no end
    yet, not an interval that never ends.
    """

    trade: TradeRow
    open_from: datetime
    open_to: Optional[datetime]
    closing_run_date: Optional[str]

    def contains(self, t: datetime) -> bool:
        return self.open_from <= t and (self.open_to is None or t < self.open_to)


def _avail(run_date: str) -> Optional[datetime]:
    ev = to_utc(run_date, DATE)
    return None if ev is None else ev + RUN_AVAILABILITY_OFFSET


def _connect_ro(db_path) -> sqlite3.Connection:
    p = pathlib.Path(db_path).resolve()
    con = sqlite3.connect(f"{p.as_uri()}?mode=ro", uri=True, timeout=10.0)
    con.row_factory = sqlite3.Row
    return con


def load_trades(db_path, signal_id: str | None = None) -> list[TradeRow]:
    con = _connect_ro(db_path)
    try:
        sql = ("SELECT id, signal_id, entry_date, direction, planned_exit_date, open "
               "FROM trade_entries")
        params: tuple = ()
        if signal_id is not None:
            sql += " WHERE signal_id = ?"
            params = (signal_id,)
        sql += " ORDER BY signal_id, entry_date, id"
        return [TradeRow(entry_id=r["id"], signal_id=r["signal_id"],
                         entry_date=r["entry_date"], direction=int(r["direction"]),
                         planned_exit_date=r["planned_exit_date"], open=int(r["open"]))
                for r in con.execute(sql, params)]
    finally:
        con.close()


def load_runs(db_path, signal_id: str | None = None) -> list[RunRow]:
    con = _connect_ro(db_path)
    try:
        sql = "SELECT signal_id, run_date, status FROM run_log"
        params: tuple = ()
        if signal_id is not None:
            sql += " WHERE signal_id = ?"
            params = (signal_id,)
        sql += " ORDER BY signal_id, run_date"
        return [RunRow(signal_id=r["signal_id"], run_date=r["run_date"],
                       status=r["status"]) for r in con.execute(sql, params)]
    finally:
        con.close()


def holding_intervals(trades: Iterable[TradeRow], runs: Iterable[RunRow]
                      ) -> list[HoldInterval]:
    """The interval each trade was genuinely held, one per trade.

    Closure is resolved against the run ledger: the earliest successful run
    whose `run_date` reaches the trade's `planned_exit_date`. A trade with no
    such run yet is still held and gets `open_to=None`.
    """
    ok_runs: dict[str, list[str]] = {}
    for r in runs:
        if r.status == OK and _avail(r.run_date) is not None:
            ok_runs.setdefault(r.signal_id, []).append(r.run_date)
    for dates in ok_runs.values():
        dates.sort()

    out: list[HoldInterval] = []
    for t in trades:
        start = _avail(t.entry_date)
        if start is None:
            continue
        closing = next((d for d in ok_runs.get(t.signal_id, ())
                        if d >= t.planned_exit_date), None)
        out.append(HoldInterval(trade=t, open_from=start,
                                open_to=None if closing is None else _avail(closing),
                                closing_run_date=closing))
    out.sort(key=lambda h: (h.trade.signal_id, h.open_from, h.trade.entry_id))
    return out


def held_at(intervals: Sequence[HoldInterval], signal_id: str,
            t: datetime) -> Optional[HoldInterval]:
    """The trade held by `signal_id` at instant `t`, or None if flat.

    Ties break on the latest `entry_date`, mirroring `bridge.latest_direction`'s
    ``ORDER BY entry_date DESC LIMIT 1``. The collector's `_has_open_trade`
    guard means a tie should not arise; matching the live rule anyway keeps the
    reconstruction faithful if one ever does -- and `overlapping_holds` reports
    that case rather than hiding it.
    """
    live = [h for h in intervals if h.trade.signal_id == signal_id and h.contains(t)]
    if not live:
        return None
    return max(live, key=lambda h: (h.trade.entry_date, h.trade.entry_id))


def overlapping_holds(intervals: Sequence[HoldInterval]
                      ) -> list[tuple[HoldInterval, HoldInterval]]:
    """Pairs of same-signal holds whose intervals overlap.

    Should always be empty: `runner_base` refuses to open a trade while one is
    open. Exposed rather than asserted so a caller can report a violation
    against real data instead of crashing on it.
    """
    bad: list[tuple[HoldInterval, HoldInterval]] = []
    by_signal: dict[str, list[HoldInterval]] = {}
    for h in intervals:
        by_signal.setdefault(h.trade.signal_id, []).append(h)
    for group in by_signal.values():
        ordered = sorted(group, key=lambda h: h.open_from)
        for a, b in zip(ordered, ordered[1:]):
            if a.open_to is None or b.open_from < a.open_to:
                bad.append((a, b))
    return bad


def held_direction_observations(trades: Iterable[TradeRow], runs: Iterable[RunRow]
                                ) -> list[Observation]:
    """The held direction per successful run, as a signal-keyed series.

    One observation per (signal, successful run), available at that run's
    completion instant, carrying the direction held once the run had finished
    closing and opening trades. `signal_id` is in the provenance, so the feature
    layer selects a family with ``key_field='signal_id'``.
    """
    trades = list(trades)
    runs = list(runs)
    intervals = holding_intervals(trades, runs)

    out: list[Observation] = []
    for r in runs:
        if r.status != OK:
            continue
        avail = _avail(r.run_date)
        event = to_utc(r.run_date, DATE)
        if avail is None or event is None:
            continue
        hold = held_at(intervals, r.signal_id, avail)
        out.append(Observation(
            source="held_direction",
            event_ts=event,
            available_ts=avail,
            payload={"direction": float(hold.trade.direction) if hold else FLAT,
                     "signal_id": r.signal_id},
            provenance={
                "signal_id": r.signal_id,
                "run_date": r.run_date,
                "entry_id": hold.trade.entry_id if hold else None,
                "entry_date": hold.trade.entry_date if hold else None,
                "planned_exit_date": hold.trade.planned_exit_date if hold else None,
                "closing_run_date": hold.closing_run_date if hold else None,
            },
        ))
    out.sort(key=lambda o: (o.available_ts, str(o.provenance.get("signal_id"))))
    return out


def load_held_direction(db_path, signal_id: str | None = None
                        ) -> dict[str, SourceLoad]:
    """The `held_direction` series straight from the forward store.

    `rows_without_availability` counts ONLY successful runs that could not be
    placed in time. A run that left the book flat is not one of them: its clock
    was fine and its answer was "nothing held", which is a value rather than an
    absence. The distinction drives `SourceLoad.unusable_clock`, and so decides
    whether the feature fails closed as UNKNOWN or reports INCOMPLETE_DATA.
    """
    trades = load_trades(db_path, signal_id)
    runs = load_runs(db_path, signal_id)
    ok = [r for r in runs if r.status == OK]
    unclocked = sum(1 for r in ok if _avail(r.run_date) is None)
    obs = held_direction_observations(trades, runs)
    return {"held_direction": SourceLoad("held_direction", tuple(obs),
                                         len(ok), unclocked)}
