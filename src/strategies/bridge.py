"""Strategy -> execution boundary.

Read-only adapter over ``evidence_forward.db``. It reads the latest
``g3_momentum_tsmom`` position and turns it into at most one ``OrderIntent`` per
UTC day. It has no authority to execute: it emits intents, and the risk manager
decides.

**Disabled by default** (``DEMO_STRATEGY_ENABLED=0``). The execution, risk and
reconciliation layers must be proven before any signal is allowed to originate
an order. Enabling this is a separate, explicit operator decision.

Deliberately does NOT: recompute signals, touch the research pipeline, write to
``evidence_forward.db``, or size positions. Sizing is fixed at one contract by
``limits.MAX_CONTRACTS_PER_ORDER``.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import pathlib
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from src.risk import limits
from src.risk.manager import (
    affordable_stop_distance as _affordable_stop_distance,
    OrderIntent, compute_trade_risk_usd, round_trip_fees_usd,
)

log = logging.getLogger(__name__)

SIGNAL_ID = "g3_momentum_tsmom"

#: Bound on the float-error correction below. One or two ticks is all
#: that is ever needed; a bound stops a silent infinite loop if the
#: budget arithmetic is ever changed incoherently.
_MAX_STOP_SHRINK_TICKS = 8

#: MAXIMUM stop distance as a percentage of price -- a ceiling, not a target.
#:
#: Sized so NET risk -- adverse move PLUS round-trip fees -- fits the $1.00
#: per-trade budget. At a $64,187.50 mark, 1 contract (0.001 BTC):
#:
#:     budget                       $1.0000
#:     round-trip taker fees        $0.0642
#:     available for price movement $0.9358
#:     max stop distance            $935.80  = 1.458% of mark
#:
#: 1.5% was tried first and is WRONG: it yields $1.027 net and every intent is
#: correctly vetoed by the risk manager. 1.4% leaves headroom for fee/mark
#: drift. The risk manager recomputes this per intent regardless and remains
#: the authority -- this constant only has to keep the bridge from emitting
#: orders that are certain to be rejected.
STOP_DISTANCE_PCT = 1.4


class StrategyDisabled(RuntimeError):
    """The bridge is off. This is the default and expected state."""


def _utc_today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def read_latest_signal(forward_db_path: str, signal_id: str = SIGNAL_ID) -> dict | None:
    """Latest run_log row for `signal_id`, READ-ONLY. Never raises on absence."""
    p = pathlib.Path(forward_db_path)
    if not p.exists():
        return None
    uri = p.resolve().as_uri() + "?mode=ro"
    conn = None
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM run_log WHERE signal_id = ? "
            "ORDER BY run_date DESC LIMIT 1",
            (signal_id,),
        ).fetchone()
        return dict(row) if row else None
    except Exception as exc:
        log.warning("[bridge] could not read %s: %s", forward_db_path, exc)
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def latest_direction(forward_db_path: str, signal_id: str = SIGNAL_ID) -> int | None:
    """Most recent open trade direction: +1 long, -1 short, None if flat/absent."""
    p = pathlib.Path(forward_db_path)
    if not p.exists():
        return None
    uri = p.resolve().as_uri() + "?mode=ro"
    conn = None
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
        row = conn.execute(
            "SELECT direction FROM trade_entries WHERE signal_id = ? AND open = 1 "
            "ORDER BY entry_date DESC LIMIT 1",
            (signal_id,),
        ).fetchone()
        return int(row[0]) if row else None
    except Exception as exc:
        log.warning("[bridge] could not read direction: %s", exc)
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


#: `direction` was not supplied, so re-read it from the ledger (the original
#: behaviour). A distinct sentinel rather than None, because None is a
#: meaningful direction -- flat -- and a caller must be able to pin that too.
_REREAD_DIRECTION = object()


def _signal_availability_delay() -> timedelta:
    """The declared delay before a `signal_log` day becomes readable.

    Taken from `alignment.timebase`, which is where this project states when
    each source became available, so the capture cannot disagree with the
    research layer about a signal's vintage. Imported lazily and inside the
    caller's try block, following `src/api/services.py`'s stance on the
    research tree: this is the live order path, and a research-tree import
    problem must never reach it at module load.
    """
    research_root = pathlib.Path(__file__).resolve().parents[2] / "research"
    if str(research_root) not in sys.path:
        sys.path.insert(0, str(research_root))
    from alignment.timebase import get_spec

    return timedelta(seconds=get_spec("signal_log").availability_offset_s)


def capture_signal_context(conn, client_order_id: str, forward_db_path: str,
                           signal_id: str = SIGNAL_ID,
                           direction: Any = _REREAD_DIRECTION,
                           decision_ts: datetime | None = None) -> bool:
    """Record the signal state that motivated one live entry, at submit time.

    The forward-evidence database moves on: a reader months later cannot
    recover what the signal said when the order was sent, only what it says
    now. This pins signal_id, direction, the raw signal value and the day it
    was computed to the entry's client_order_id, plus every other signal's
    latest position as regime/context.

    Read-only against the forward database and best-effort by construction:
    returns False and logs rather than raising, because a research record must
    never be able to interfere with an order. Called by
    `strategy_jobs.evaluate_once`, and only once `executor.submit()` has
    returned ACKED -- a BLOCKED, REJECTED or UNKNOWN submission must leave no
    record of an entry the exchange never accepted. Any other caller that
    submits an entry is responsible for invoking it on the same condition.

    `direction` pins the direction the caller actually consumed, and
    `strategy_jobs` passes the submitted intent's own side. Left unset it is
    re-read here, which is a DIFFERENT question once time has passed: this runs
    after the quote, wallet, risk and HTTP round-trips, and the 01:00 UTC
    collector can close or open a trade inside that window, so a re-read can
    record a direction the submitted order never carried.

    `decision_ts` bounds the signal fields to what had actually become
    AVAILABLE by the decision instant: a day-D observation is written by the
    collector at 01:00 UTC on D+1 (measured 01:00:06-01:00:42), so the latest
    admissible day is ``(decision_ts - 25h).date()``, inclusive. Without it the
    fields are whatever is newest at capture time, which is a few seconds later
    and can straddle that commit -- an entry decided at 00:59 would then record
    a signal day it could not have seen. `strategy_jobs` passes the instant it
    already holds; omitting it keeps the original unbounded behaviour for any
    caller with no decision instant to offer.

    All three signal reads share one snapshot and one bound, so `signal_day`,
    `raw_value` and `context_json` cannot come from different vintages even if
    the collector commits mid-call.
    """
    p = pathlib.Path(forward_db_path)
    if not p.exists():
        log.warning("[bridge] forward db absent; no signal context for %s",
                    client_order_id)
        return False
    fwd = None
    try:
        fwd = sqlite3.connect(p.resolve().as_uri() + "?mode=ro", uri=True, timeout=5.0)
        bound = None
        if decision_ts is not None:
            at = (decision_ts if decision_ts.tzinfo is not None
                  else decision_ts.replace(tzinfo=timezone.utc))
            bound = (at - _signal_availability_delay()).date().isoformat()
        # One read transaction for every signal query below. Without it each
        # SELECT takes its own snapshot, so a collector commit landing between
        # them could pair a signal_day with a context_json from the next day.
        fwd.execute("BEGIN")
        try:
            if direction is _REREAD_DIRECTION:
                row = fwd.execute(
                    "SELECT direction FROM trade_entries WHERE signal_id = ? AND open = 1 "
                    "ORDER BY entry_date DESC LIMIT 1", (signal_id,)).fetchone()
                direction = int(row[0]) if row else None
            if bound is None:
                sig = fwd.execute(
                    "SELECT day, position, raw_value FROM signal_log WHERE signal_id = ? "
                    "ORDER BY id DESC LIMIT 1", (signal_id,)).fetchone()
                latest_day = fwd.execute("SELECT MAX(day) FROM signal_log").fetchone()
            else:
                sig = fwd.execute(
                    "SELECT day, position, raw_value FROM signal_log WHERE signal_id = ? "
                    "AND day <= ? ORDER BY day DESC LIMIT 1",
                    (signal_id, bound)).fetchone()
                latest_day = fwd.execute(
                    "SELECT MAX(day) FROM signal_log WHERE day <= ?", (bound,)).fetchone()
            signal_day = sig[0] if sig else None
            raw_value = float(sig[2]) if sig and sig[2] is not None else None
            context = {}
            if latest_day and latest_day[0]:
                for sid, pos in fwd.execute(
                        "SELECT signal_id, position FROM signal_log WHERE day = ?",
                        (latest_day[0],)):
                    context[sid] = pos
        finally:
            fwd.execute("COMMIT")
        from src.data import storage as _storage
        _storage.write_trade_signal_context(
            conn, client_order_id=client_order_id, signal_id=signal_id,
            direction=direction, raw_value=raw_value, signal_day=signal_day,
            context_json=json.dumps(context, sort_keys=True),
            captured_ts=datetime.now(timezone.utc).isoformat())
        return True
    except Exception as exc:
        log.warning("[bridge] could not capture signal context for %s: %s",
                    client_order_id, exc)
        return False
    finally:
        if fwd is not None:
            try:
                fwd.close()
            except Exception:
                pass


#: Delta refuses a longer client_order_id. Measured, not assumed -- Gate 5C.5
#: captured the exact rejection:
#:   HTTP 400 error.code=bad_schema
#:   schema_errors: {"message": "String length must be at most 32",
#:                   "param": "client_order_id"}
#: The 32-character id `supervised-gate-20260818T084125Z` was ACCEPTED, so the
#: bound is inclusive. No restriction on underscores was established and none
#: is assumed here.
CLIENT_ORDER_ID_MAX_LEN = 32

#: Hex digest characters appended to the readable prefix. 6 hex = 24 bits over
#: the FULL signal id, which is what makes two signals sharing a prefix
#: distinct -- plain truncation would silently collapse them.
_SIGNAL_DIGEST_LEN = 6


def _signal_token(signal_id: str, budget: int) -> str:
    """A compact, deterministic, collision-safe stand-in for `signal_id`.

    Readable prefix + digest of the WHOLE id. Truncation alone is unsafe:
    "g3_momentum_tsmom" and "g3_momentum_tsmom_v2" share every character of any
    prefix short enough to fit, so a truncating scheme would map two distinct
    signals to one client_order_id -- and since the id is the idempotency key,
    that would make one signal's order silently suppress the other's.
    """
    digest = hashlib.sha256(signal_id.encode("utf-8")).hexdigest()[:_SIGNAL_DIGEST_LEN]
    alnum = "".join(ch for ch in signal_id.lower() if ch.isalnum())
    prefix = alnum[: max(0, budget - len(digest))]
    return prefix + digest


def client_order_id(signal_id: str, utc_day: str, side: str, seq: int) -> str:
    """Deterministic client_order_id, guaranteed <= 32 characters.

        <signal-prefix><digest>-<YYYYMMDD>-<side-initial><seq>

    Every component the old format carried is retained -- signal, day, side,
    sequence -- with only the signal name compressed, because it is the one
    unbounded field. The date, side and sequence are kept verbatim so the id
    stays traceable and so two orders differing in any of them can never share
    an id. The signal prefix absorbs the length budget, so a longer sequence
    number shortens the prefix rather than overflowing the limit.
    """
    day = utc_day.replace("-", "")
    tail = f"-{day}-{side[:1]}{int(seq)}"
    token = _signal_token(signal_id, CLIENT_ORDER_ID_MAX_LEN - len(tail))
    coid = token + tail
    if len(coid) > CLIENT_ORDER_ID_MAX_LEN:          # unreachable by construction
        raise ValueError(
            f"client_order_id {coid!r} is {len(coid)} chars, "
            f"exceeds Delta's limit of {CLIENT_ORDER_ID_MAX_LEN}")
    return coid


def executable_limit_price(direction: int, best_bid: float, best_ask: float) -> float:
    """A limit price that actually crosses, tick-aligned in the safe direction.

    `round(mark / tick) * tick` -- the previous rule -- is NOT marketable. Gate
    5C measured it: a sell built from mark 64400.36 produced 64400.4 against a
    bid of 64400.0, so it sat ABOVE the bid and would have rested rather than
    filled. The intent that gate existed to test could never have produced a
    fill.

    To cross, a BUY must reach the ask and a SELL must reach the bid. Rounding
    must therefore go AWAY from the touch (ceil for a buy, floor for a sell):
    rounding to nearest can land a buy below the ask or a sell above the bid,
    which is exactly the failure being fixed.
    """
    tick = limits.BTCUSD_TICK_SIZE
    if best_bid <= 0 or best_ask <= 0:
        raise ValueError(f"invalid book: bid={best_bid} ask={best_ask}")
    if best_ask < best_bid:
        raise ValueError(f"crossed book: bid={best_bid} ask={best_ask}")
    if direction == 1:
        return round(math.ceil(best_ask / tick) * tick, 1)
    return round(math.floor(best_bid / tick) * tick, 1)


#: Re-exported from src.risk.manager, where it now lives beside the fee model
#: it inverts. The protective sizing needs the identical function, and
#: execution must not import a strategy module to get it.
affordable_stop_distance = _affordable_stop_distance


def stop_distance(limit_price: float, mark_price: float, size: int = 1) -> float:
    """The stop distance to actually use: the strategy's ceiling, made affordable.

    STOP_DISTANCE_PCT is the strategy's MAXIMUM, not a mandatory distance. A
    fixed percentage against a fixed dollar budget breaks as price rises --
    Gate 5C.3 measured it: at BTC 69548 a 1.4% stop costs $0.9737 which, with
    $0.0821 of fees, is $1.0558 against a $1.00 budget, so every intent was
    correctly vetoed and the strategy could not trade at all above ~$65,876.

    Taking the MINIMUM of the two keeps the strategy's intent intact whenever it
    is affordable and tightens the stop only when the budget demands it, rather
    than raising the budget or widening the guard.

    The result is floored to a whole tick: rounding a distance UP could push the
    trade over the budget, which is the one direction that must never happen.
    """
    tick = limits.BTCUSD_TICK_SIZE
    strategy_max = limit_price * STOP_DISTANCE_PCT / 100.0
    affordable = affordable_stop_distance(mark_price, size)
    distance = min(strategy_max, affordable)
    distance = math.floor(distance / tick) * tick
    if distance < tick:
        raise ValueError(
            f"no affordable stop distance at mark {mark_price}: budget "
            f"${limits.MAX_TRADE_RISK_USD:.2f} minus fees "
            f"${round_trip_fees_usd(mark_price, size):.4f} leaves less than one tick"
        )
    return round(distance, 1)


def build_intent(
    *,
    direction: int,
    mark_price: float,
    enabled: bool,
    best_bid: float,
    best_ask: float,
    utc_day: str | None = None,
    seq: int = 1,
) -> OrderIntent:
    """Turn a direction into a single-contract, genuinely marketable limit intent.

    Raises StrategyDisabled unless explicitly enabled -- the guard lives here so
    a caller cannot forget it.

    best_bid/best_ask are REQUIRED: the price must be built from the book, not
    from the mark, or the order does not cross (see executable_limit_price).
    """
    if not enabled:
        raise StrategyDisabled(
            "DEMO_STRATEGY_ENABLED=0: the strategy bridge is disabled until the "
            "execution stack is proven"
        )
    if direction not in (1, -1):
        raise ValueError(f"direction must be +1 or -1, got {direction!r}")

    day = utc_day or _utc_today()
    side = "buy" if direction == 1 else "sell"

    limit_price = executable_limit_price(direction, best_bid, best_ask)
    size = limits.MAX_CONTRACTS_PER_ORDER
    distance = stop_distance(limit_price, mark_price, size)
    tick = limits.BTCUSD_TICK_SIZE

    def _at(dist: float) -> OrderIntent:
        stop = round((limit_price - dist) if direction == 1
                     else (limit_price + dist), 1)
        return OrderIntent(
            client_order_id=client_order_id(SIGNAL_ID, day, side, seq),
            symbol="BTCUSD",
            product_id=limits.BTCUSD_PRODUCT_ID,
            side=side,
            size=size,
            order_type="limit_order",
            limit_price=limit_price,
            stop_price=stop,
            strategy=SIGNAL_ID,
        )

    # Verify against the guard's OWN function rather than trusting the
    # arithmetic above. round(limit - distance, 1) reintroduces binary
    # floating-point error, so the distance RiskManager reconstructs from
    # |limit - stop| can exceed the one computed here by a few 1e-15 -- enough
    # to fail a boundary comparison when the affordable distance lands exactly
    # on a tick (observed at mark 90000: risk 1.0000000000000029 > 1.00).
    # Shrinking by whole ticks is bounded and always converges downward.
    for _ in range(_MAX_STOP_SHRINK_TICKS):
        candidate = _at(distance)
        if compute_trade_risk_usd(candidate, mark_price) <= limits.MAX_TRADE_RISK_USD:
            return candidate
        distance = round(distance - tick, 1)
        if distance < tick:
            break
    raise ValueError(
        f"could not build an in-budget intent at mark {mark_price}: "
        f"budget ${limits.MAX_TRADE_RISK_USD:.2f}, fees "
        f"${round_trip_fees_usd(mark_price, size):.4f}"
    )
