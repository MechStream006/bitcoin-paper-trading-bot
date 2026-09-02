"""
timebase.py
===========
Per-source declaration of which timestamp means what, plus the normalisation
and cadence measurement the alignment layer is built on.

Read-only. Writes nothing.

EVENT TIME IS NOT AVAILABILITY TIME
-----------------------------------
Every source here carries two distinct instants:

- **event time** -- when the thing happened, as the originating system saw it.
  Correct for measuring market dynamics.
- **availability time** -- when this system could first have acted on it.
  Correct for building features, and the only one that is leakage-safe.

They differ by real, measured amounts. On production `option_quotes`, over a
3,000-row sample, ``wall_ts - exch_ts`` had median **3.58s** (min 0.58, max
6.93, and never negative). Treating `exch_ts` as the decision time would hand a
backtest roughly three and a half seconds of foresight on every quote. A daily
bar is worse: its `open_time` is the instant the bar *opened*, so using it as
availability leaks the entire bar.

Every spec below therefore states both, and `availability_offset_s` is the
measured or structural delay from one to the other. Where the delay is
structural (a bar must close before it is known) the offset is exact. Where it
is empirical (collector lag) the spec says so and the offset is 0, because the
store already records the arrival instant as its own column -- there is no need
to model a lag that was written down.

WHAT THE GRAIN NOTES ARE FOR
----------------------------
Three sources are declared here that hold no rows on the current production
database: `oi_snapshots` and `funding_snapshots` are dormant (CLAUDE.md sec.11),
and there is no `signal_evaluations` table at all -- the nearest real artefact
is `signal_log` in the forward-evidence store. They are declared anyway, with
`populated=False`, so a caller asking for OI gets an explicit empty result and
a pointer to where the data actually lives, rather than a silent zero-row join
that looks like "no OI happened".

NEVER ASSUME A GRID
-------------------
`cadence_profile` measures the observed spacing of a real timestamp series. It
is not a validator and has no expected value baked in: the OI/quote cycle is
nominally 60s, and on production only **1 of 299** consecutive gaps was exactly
60.000s, with a maximum of 160.6s. Callers that need a regular series must
resample explicitly from the observed grid, never assume one.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Optional, Sequence

ISO = "iso"
MICROS = "us"
MILLIS = "ms"
DATE = "date"

_UNITS = (ISO, MICROS, MILLIS, DATE)

# The forward-evidence collector runs at this UTC hour on the day AFTER the
# observation date, so a day-D signal does not exist until D+1 at this time.
FORWARD_COLLECTOR_UTC_HOUR = 1


@dataclass(frozen=True)
class SourceSpec:
    """How to read time out of one source, and what each field means."""

    name: str
    store: str
    table: str
    grain: str
    event_field: str
    event_unit: str
    availability_field: Optional[str]
    availability_offset_s: float
    availability_rule: str
    populated: bool = True
    provenance_fields: tuple[str, ...] = ()
    notes: str = ""

    def __post_init__(self) -> None:
        if self.event_unit not in _UNITS:
            raise ValueError(f"{self.name}: unknown unit {self.event_unit!r}")
        if self.availability_field is None and self.availability_offset_s < 0:
            raise ValueError(f"{self.name}: negative availability offset would "
                             "make data available before it existed")


SOURCES: dict[str, SourceSpec] = {
    "spot_1m": SourceSpec(
        name="spot_1m", store="parquet_lake", table="spot/BTCUSDT", grain="1m bar",
        event_field="open_time", event_unit=MILLIS,
        availability_field=None, availability_offset_s=60.0,
        availability_rule="bar open + 60s: a bar is only known once it closes",
        provenance_fields=("dataset", "symbol", "year", "month", "parquet_sha256"),
        notes="Binance klines. 43,200 rows/month confirms 60s bars.",
    ),
    "perp_1m": SourceSpec(
        name="perp_1m", store="parquet_lake", table="perp/BTCUSDT", grain="1m bar",
        event_field="open_time", event_unit=MILLIS,
        availability_field=None, availability_offset_s=60.0,
        availability_rule="bar open + 60s: a bar is only known once it closes",
        provenance_fields=("dataset", "symbol", "year", "month", "parquet_sha256"),
    ),
    "dvol_1d": SourceSpec(
        name="dvol_1d", store="parquet_lake", table="dvol/BTC", grain="1d bar",
        event_field="open_time", event_unit=MILLIS,
        availability_field=None, availability_offset_s=86400.0,
        availability_rule="bar open + 24h. Deribit's own publication lag past "
                          "the bar close is not documented, so this is a floor, "
                          "not a measured arrival time",
        provenance_fields=("dataset", "symbol", "year", "month", "parquet_sha256"),
    ),
    "funding_8h": SourceSpec(
        name="funding_8h", store="parquet_lake", table="funding/BTCUSDT",
        grain="8h realization",
        event_field="timestamp_ms", event_unit=MILLIS,
        availability_field=None, availability_offset_s=0.0,
        availability_rule="known at realization; event and availability coincide",
        provenance_fields=("dataset", "symbol", "year", "month", "parquet_sha256"),
        notes="Grid is 8h +/- ~1ms (CLAUDE.md sec.11); never test divisibility exactly.",
    ),
    "option_quotes": SourceSpec(
        name="option_quotes", store="btc_trading.db", table="option_quotes",
        grain="per instrument per snapshot cycle (~60s, drifting)",
        event_field="exch_ts", event_unit=MICROS,
        availability_field="ts", availability_offset_s=0.0,
        availability_rule="`ts` is the collector's write instant and is the "
                          "arrival time; measured median lag behind exch_ts is "
                          "3.58s and never negative",
        provenance_fields=("instrument_id",),
        notes="exch_ts is NULL on 0.234% of rows (out-of-band coercion, "
              "CLAUDE.md sec.11); those rows have event time unknown but "
              "availability time known.",
    ),
    "spot_index": SourceSpec(
        name="spot_index", store="derived (option_quotes panel)",
        table="option_quotes", grain="one value per snapshot cycle",
        event_field="exch_ts", event_unit=MICROS,
        availability_field="ts", availability_offset_s=0.0,
        availability_rule="the cycle's own write instant; the reduction is "
                          "available exactly when its panel is",
        provenance_fields=("cycle_ts", "panel_size", "spot_observations"),
        notes="Median spot_price across the cycle. Instrument-independent, so "
              "unlike a keyed quote it is defined at every cycle. Built by "
              "alignment.option_panel.spot_index_observations.",
    ),
    "atm_iv": SourceSpec(
        name="atm_iv", store="derived (option_quotes panel)",
        table="option_quotes", grain="one value per snapshot cycle",
        event_field="exch_ts", event_unit=MICROS,
        availability_field="ts", availability_offset_s=0.0,
        availability_rule="the cycle's own write instant. Event time is the "
                          "LATER of the two legs: the pair is not complete "
                          "until both exist",
        provenance_fields=("call_id", "put_id", "strike", "spot", "expiry",
                           "tau_hours", "abs_moneyness", "cycle_ts"),
        notes="Nearest-expiry ATM call/put mark_iv mid. NOT the vol-state "
              "feature -- DVOL owns that role (17 governed definitions, none "
              "from option mark_iv). This is Delta front-end IV at tau ~5-22h, "
              "a tenor DVOL cannot see. Built by "
              "alignment.option_panel.atm_iv_observations.",
    ),
    "oi_snapshots": SourceSpec(
        name="oi_snapshots", store="btc_trading.db", table="oi_snapshots",
        grain="nominally 180s",
        event_field="ts", event_unit=ISO,
        availability_field="ts", availability_offset_s=0.0,
        availability_rule="single timestamp; event and availability coincide",
        populated=False,
        provenance_fields=("instrument_id",),
        notes="EMPTY on production (0 rows), dormant like funding_snapshots. "
              "Live OI arrives inside option_quotes as oi/oi_contracts/"
              "oi_value_usd at the quote cadence -- use `oi_source()`.",
    ),
    "funding_snapshots": SourceSpec(
        name="funding_snapshots", store="btc_trading.db", table="funding_snapshots",
        grain="per funding message",
        event_field="ts", event_unit=ISO,
        availability_field="ts", availability_offset_s=0.0,
        availability_rule="single timestamp; event and availability coincide",
        populated=False,
        provenance_fields=("symbol",),
        notes="EMPTY: no funding_rate channel is subscribed (CLAUDE.md sec.11).",
    ),
    "signal_log": SourceSpec(
        name="signal_log", store="evidence_forward.db", table="signal_log",
        grain="1 day",
        event_field="day", event_unit=DATE,
        availability_field=None, availability_offset_s=(24 + FORWARD_COLLECTOR_UTC_HOUR) * 3600.0,
        availability_rule="day D is computed from D's close and written by the "
                          "01:00 UTC collector on D+1, so it does not exist "
                          "until D+1 01:00 UTC",
        provenance_fields=("signal_id", "run_id"),
        notes="The nearest thing to a `signal_evaluations` table; no table of "
              "that name exists.",
    ),
    "held_direction": SourceSpec(
        name="held_direction", store="derived (evidence_forward.db trade ledger)",
        table="trade_entries", grain="1 day (one per successful collector run)",
        event_field="entry_date", event_unit=DATE,
        availability_field=None,
        availability_offset_s=(24 + FORWARD_COLLECTOR_UTC_HOUR) * 3600.0,
        availability_rule="the completion instant of the run that last restated "
                          "the ledger: run_date D executes at D+1 at the "
                          "collector hour, opening and closing trades before "
                          "anything can read the result",
        provenance_fields=("signal_id", "run_date", "entry_id", "entry_date",
                           "planned_exit_date", "closing_run_date"),
        notes="Direction of the trade HELD at each run, which is what "
              "bridge.latest_direction() returns and therefore what a live "
              "decision consumes -- distinct from signal_log.position, the "
              "latest DAILY signal, which the hold lets it drift from. Closure "
              "is resolved against run_log, never planned_exit_date, because a "
              "silenced collector holds a trade past its nominal exit "
              "(INC-0005). Built by "
              "alignment.forward_trades.held_direction_observations.",
    ),
    "forward_trade_entries": SourceSpec(
        name="forward_trade_entries", store="evidence_forward.db",
        table="trade_entries", grain="1 day",
        event_field="entry_date", event_unit=DATE,
        availability_field=None, availability_offset_s=(24 + FORWARD_COLLECTOR_UTC_HOUR) * 3600.0,
        availability_rule="same collector cadence as signal_log",
        provenance_fields=("signal_id",),
    ),
    "orders": SourceSpec(
        name="orders", store="btc_trading.db", table="orders", grain="per order",
        event_field="placed_ts", event_unit=ISO,
        availability_field="placed_ts", availability_offset_s=0.0,
        availability_rule="the bot wrote it as it submitted; the two coincide",
        provenance_fields=("client_order_id", "delta_order_id", "strategy"),
    ),
    "order_fills": SourceSpec(
        name="order_fills", store="btc_trading.db", table="order_fills",
        grain="per fill",
        event_field="exch_ts", event_unit=ISO,
        availability_field="processed_ts", availability_offset_s=0.0,
        availability_rule="exch_ts is the exchange's fill instant; processed_ts "
                          "is when the reconciler saw it, up to one "
                          "reconciliation interval (~60s) later",
        provenance_fields=("fill_id", "delta_order_id", "role"),
    ),
    "positions": SourceSpec(
        name="positions", store="btc_trading.db", table="positions",
        grain="per position",
        event_field="entry_ts", event_unit=ISO,
        availability_field="entry_ts", availability_offset_s=0.0,
        availability_rule="projected from the fill ledger at reconcile time; "
                          "entry_ts already is the observation instant",
        provenance_fields=("id", "strategy"),
    ),
    "trades": SourceSpec(
        name="trades", store="btc_trading.db", table="trades", grain="per trade",
        event_field="ts", event_unit=ISO,
        availability_field="ts", availability_offset_s=0.0,
        availability_rule="exchange fill instant as recorded; the store keeps "
                          "no separate arrival time for this table",
        provenance_fields=("id", "order_id"),
        notes="Unlike order_fills, `trades` has no processed_ts, so its "
              "availability is optimistic by up to one reconciliation "
              "interval. Prefer order_fills for leakage-sensitive work.",
    ),
}


def get_spec(name: str) -> SourceSpec:
    try:
        return SOURCES[name]
    except KeyError:
        raise KeyError(f"unknown source {name!r}; known: {sorted(SOURCES)}") from None


def oi_source() -> SourceSpec:
    """Where OI actually is.

    `oi_snapshots` is declared but empty on production. The live open-interest
    series is carried by `option_quotes` (oi, oi_contracts, oi_value_usd) at the
    quote cadence, so OI inherits that source's timestamps -- including its
    drift. Callers get the populated source rather than a silent empty join.
    """
    return SOURCES["option_quotes"]


def to_utc(value: Any, unit: str) -> Optional[datetime]:
    """Normalise one timestamp to an aware UTC datetime.

    Handles the four spellings this project stores: ISO-8601 with either a 'Z'
    or a '+00:00' offset, integer microseconds, integer milliseconds, and a bare
    calendar date. A naive ISO value is read as UTC, because every writer in the
    runtime stamps UTC.
    """
    if value is None or value == "":
        return None
    if unit == MICROS or unit == MILLIS:
        try:
            n = float(value)
        except (TypeError, ValueError):
            return None
        secs = n / (1_000_000.0 if unit == MICROS else 1_000.0)
        try:
            return datetime.fromtimestamp(secs, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if unit == DATE:
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc)
        if isinstance(value, date):
            return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
        try:
            return datetime.fromisoformat(str(value).strip()).replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None \
            else value.astimezone(timezone.utc)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def event_time(row: Any, spec: SourceSpec) -> Optional[datetime]:
    return to_utc(_get(row, spec.event_field), spec.event_unit)


def availability_time(row: Any, spec: SourceSpec) -> Optional[datetime]:
    """When this system could first have used the row, or None if unknowable.

    Either a recorded arrival column, or -- only where the spec declares no such
    column -- the event time plus the structural delay. Never earlier than the
    event, and never guessed: a declared-but-NULL arrival returns None so the
    row is dropped rather than dated optimistically.
    """
    if spec.availability_field is not None:
        unit = ISO if spec.availability_field != spec.event_field else spec.event_unit
        # No silent fallback to event time. A source that records its own
        # arrival column and leaves it NULL has an UNKNOWN arrival, not an
        # arrival equal to the exchange's timestamp -- substituting the latter
        # would hand the caller the exact look-ahead this layer exists to stop.
        return to_utc(_get(row, spec.availability_field), unit)
    ev = event_time(row, spec)
    if ev is None:
        return None
    return ev + timedelta(seconds=spec.availability_offset_s)


def _get(row: Any, field: str) -> Any:
    if isinstance(row, dict):
        return row.get(field)
    try:
        return row[field]
    except (TypeError, KeyError, IndexError):
        return getattr(row, field, None)


def cadence_profile(times: Iterable[Any], unit: str = ISO,
                    nominal_seconds: float | None = None,
                    dedupe: bool = True) -> dict[str, Any]:
    """Observed spacing of a timestamp series. Descriptive only, never asserts.

    `nominal_seconds` is reported against, not enforced: the point is to show
    how far the real series is from any assumed grid, so a caller can decide
    whether resampling is needed. `exact_fraction` is deliberately computed on
    equality, which is what makes grid assumptions visibly false.

    `dedupe` defaults True because these stores are panels: one snapshot cycle
    writes ~56 option rows carrying the identical `ts`. Measuring gaps across
    the raw rows would report the width of the panel (mostly zero) rather than
    the spacing of the schedule, which is the thing a cadence describes. Pass
    False only when the series is genuinely one row per instant.
    """
    parsed = sorted(t for t in (to_utc(v, unit) for v in times) if t is not None)
    if dedupe:
        parsed = sorted(set(parsed))
    if len(parsed) < 2:
        return {"n": len(parsed), "gaps": 0, "insufficient": True}
    gaps = [(parsed[i + 1] - parsed[i]).total_seconds() for i in range(len(parsed) - 1)]
    out: dict[str, Any] = {
        "n": len(parsed),
        "deduped": dedupe,
        "gaps": len(gaps),
        "first": parsed[0].isoformat(),
        "last": parsed[-1].isoformat(),
        "min_s": min(gaps),
        "median_s": statistics.median(gaps),
        "mean_s": statistics.fmean(gaps),
        "max_s": max(gaps),
        "insufficient": False,
        "monotonic": all(g > 0 for g in gaps),
    }
    if nominal_seconds:
        exact = sum(1 for g in gaps if g == nominal_seconds)
        out["nominal_s"] = nominal_seconds
        out["exact_fraction"] = exact / len(gaps)
        out["within_1s_fraction"] = sum(
            1 for g in gaps if abs(g - nominal_seconds) <= 1.0) / len(gaps)
        out["max_drift_s"] = max(abs(g - nominal_seconds) for g in gaps)
        out["is_regular_grid"] = exact == len(gaps)
    return out


def describe_sources() -> list[dict[str, Any]]:
    return [
        {
            "source": s.name,
            "store": s.store,
            "table": s.table,
            "grain": s.grain,
            "event": f"{s.event_field} ({s.event_unit})",
            "availability": (s.availability_field
                             if s.availability_field is not None
                             else f"{s.event_field} + {s.availability_offset_s:g}s"),
            "rule": s.availability_rule,
            "populated": s.populated,
        }
        for s in sorted(SOURCES.values(), key=lambda x: x.name)
    ]
