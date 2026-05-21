"""ExpiryCalendar — classified option universe builder.

classify_expiry_class() is the anti-drift anchor for the whole expiry system:
  - Pure function of (settlement_time.date(), ExpiryConfig).
  - Same inputs always produce the same ExpiryClass (no-drift guarantee).
  - Calendar identity (which calendar date it is) is authoritative; market
    data is advisory only.

ExpiryCalendar wraps the classified record list and exposes query and posture-
derivation methods. build_calendar() is the top-level entry point; it is called
by ExpiryContext on every scheduler tick.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Callable, List, Optional

from src.expiry.expiry_clock import last_cycle_anchor, parse_settlement_time, utc_now
from src.expiry.models import (
    ClassificationMode,
    ExpiryClass,
    ExpiryConfig,
    ExpiryPhase,
    ExpiryPosture,
    ExpiryRecord,
    ExpirySnapshot,
)

logger = logging.getLogger(__name__)

_UTC = timezone.utc
_NOW_FN = Callable[[], datetime]

# Placeholder for instruments whose settlement_time cannot be parsed.
# Always in the past so it sorts first and is classified SETTLED.
_PARSE_FAIL_SENTINEL = datetime(2000, 1, 1, 12, 0, 0, tzinfo=_UTC)


# ---------------------------------------------------------------------------
# Pure classification functions (no side effects, deterministic, replay-safe)
# ---------------------------------------------------------------------------

def _is_last_weekday_of_month(d: date, weekday: int) -> bool:
    """True if `d` is the last occurrence of `weekday` in its calendar month."""
    return (d + timedelta(days=7)).month != d.month


def classify_expiry_class(
    settlement_time: datetime,
    config: ExpiryConfig,
) -> ExpiryClass:
    """Anti-drift classifier: pure function of (settlement_time.date(), config).

    Precedence: MONTHLY > WEEKLY > DAILY.
    Contract: same inputs always return the same ExpiryClass.

    Logic:
      1. If settlement date falls on the configured weekly_weekday AND is the
         last such weekday of its month  →  MONTHLY.
      2. If settlement date falls on the configured weekly_weekday           →  WEEKLY.
      3. Otherwise                                                            →  DAILY.
      4. None / naive settlement_time                                        →  UNKNOWN.
    """
    if settlement_time is None or settlement_time.tzinfo is None:
        return ExpiryClass.UNKNOWN
    expiry_date = settlement_time.date()
    weekday = expiry_date.weekday()     # 0=Mon … 4=Fri … 6=Sun
    if weekday == config.weekly_weekday:
        if (
            config.monthly_is_last_weekday_of_month
            and _is_last_weekday_of_month(expiry_date, config.weekly_weekday)
        ):
            return ExpiryClass.MONTHLY
        return ExpiryClass.WEEKLY
    return ExpiryClass.DAILY


def classify_expiry_phase(
    seconds_to_settle: float,
    config: ExpiryConfig,
) -> ExpiryPhase:
    """Pure function: signed seconds until settlement → ExpiryPhase."""
    if seconds_to_settle < 0:
        return ExpiryPhase.SETTLED
    days  = seconds_to_settle / 86_400.0
    hours = seconds_to_settle / 3_600.0
    if days > config.distant_threshold_days:
        return ExpiryPhase.DISTANT
    if days > config.active_threshold_days:
        return ExpiryPhase.ACTIVE
    if hours > config.pre_expiry_threshold_hours:
        return ExpiryPhase.PRE_EXPIRY
    if hours > config.expiry_day_threshold_hours:
        return ExpiryPhase.EXPIRY_DAY
    if hours > config.settlement_phase_hours:
        return ExpiryPhase.EXPIRY_INTENSIVE
    return ExpiryPhase.SETTLEMENT


def _classify_mode(
    settlement_time: datetime,
    now: datetime,
    config: ExpiryConfig,
) -> ClassificationMode:
    """CONFIRMED if within confirmed_horizon_days, else DISCOVERY."""
    delta_days = (settlement_time - now).total_seconds() / 86_400.0
    if delta_days <= config.confirmed_horizon_days:
        return ClassificationMode.CONFIRMED
    return ClassificationMode.DISCOVERY


# ---------------------------------------------------------------------------
# ExpiryCalendar
# ---------------------------------------------------------------------------

class ExpiryCalendar:
    """Classified snapshot of the current option universe.

    Constructed by build_calendar().  Treat as read-only after construction.
    Used by ExpiryContext to derive posture; extracted into ExpirySnapshot
    for storage. Not persisted to DB in Phase 1.

    Args:
        records:           list[ExpiryRecord], sorted (settlement_time ASC, strike ASC).
        mark_price:        BTC mark price at snapshot time.
        captured_at:       UTC datetime when this calendar was built.
        divergence_events: List of warning strings (parse failures, drift violations).
        cycle_elapsed_sec: Seconds since the last 12:00 UTC anchor.
        cycle_fraction:    cycle_elapsed_sec / cycle_duration_sec, clamped [0,1].
    """

    def __init__(
        self,
        records: List[ExpiryRecord],
        mark_price: float,
        captured_at: datetime,
        divergence_events: List[str],
        cycle_elapsed_sec: float,
        cycle_fraction: float,
    ) -> None:
        self.records = records
        self.mark_price = mark_price
        self.captured_at = captured_at
        self.divergence_events = divergence_events
        self.cycle_elapsed_sec = cycle_elapsed_sec
        self.cycle_fraction = cycle_fraction

    # -----------------------------------------------------------------------
    # Query methods
    # -----------------------------------------------------------------------

    def nearest_record(self) -> Optional[ExpiryRecord]:
        """ExpiryRecord for the nearest unsettled contract, or None."""
        unsettled = [r for r in self.records if r.seconds_to_settlement >= 0]
        if not unsettled:
            return None
        return min(unsettled, key=lambda r: r.seconds_to_settlement)

    def nearest_settlement(self) -> Optional[datetime]:
        rec = self.nearest_record()
        return rec.settlement_time if rec is not None else None

    def seconds_to_nearest(self) -> Optional[float]:
        rec = self.nearest_record()
        return rec.seconds_to_settlement if rec is not None else None

    def by_class(self, cls: ExpiryClass) -> List[ExpiryRecord]:
        return [r for r in self.records if r.expiry_class == cls]

    def coverage_by_class(self) -> dict:
        """Return {ExpiryClass: int} count of records per class."""
        counts: dict = {}
        for r in self.records:
            counts[r.expiry_class] = counts.get(r.expiry_class, 0) + 1
        return counts

    def just_settled_records(self, rollover_window_sec: float) -> List[ExpiryRecord]:
        """Records whose settlement_time is within the last rollover_window_sec."""
        return [
            r for r in self.records
            if -rollover_window_sec < r.seconds_to_settlement < 0
        ]

    # -----------------------------------------------------------------------
    # Posture derivation
    # -----------------------------------------------------------------------

    def derive_posture(self, config: ExpiryConfig) -> ExpiryPosture:
        """Derive runtime posture from this calendar's classified records.

        ROLLOVER takes precedence if any contract just settled.
        Otherwise posture is driven by the nearest unsettled contract.
        """
        if self.just_settled_records(config.rollover_window_sec):
            return ExpiryPosture.ROLLOVER

        nearest_sec = self.seconds_to_nearest()
        if nearest_sec is None:
            return ExpiryPosture.NORMAL

        if nearest_sec < config.settlement_window_sec:
            return ExpiryPosture.SETTLEMENT
        if nearest_sec < config.intensive_window_sec:
            return ExpiryPosture.INTENSIVE
        if nearest_sec < config.approaching_window_sec:
            return ExpiryPosture.APPROACHING
        return ExpiryPosture.NORMAL

    # -----------------------------------------------------------------------
    # Snapshot extraction
    # -----------------------------------------------------------------------

    def to_snapshot(self, posture: ExpiryPosture, observe_count: int) -> ExpirySnapshot:
        """Build an ExpirySnapshot from this calendar + derived posture."""
        nearest = self.nearest_record()
        n_by_class = list(self.coverage_by_class().items())
        return ExpirySnapshot(
            captured_at=self.captured_at,
            mark_price=self.mark_price,
            posture=posture,
            records=list(self.records),
            nearest_settlement=nearest.settlement_time if nearest else None,
            seconds_to_nearest=nearest.seconds_to_settlement if nearest else None,
            nearest_expiry_class=nearest.expiry_class if nearest else None,
            nearest_expiry_phase=nearest.expiry_phase if nearest else None,
            n_by_class=n_by_class,
            cycle_elapsed_sec=self.cycle_elapsed_sec,
            cycle_fraction=self.cycle_fraction,
            divergence_events=list(self.divergence_events),
            observe_count=observe_count,
        )


# ---------------------------------------------------------------------------
# build_calendar — top-level entry point called by ExpiryContext
# ---------------------------------------------------------------------------

def build_calendar(
    universe: List[dict],
    mark_price: float,
    config: Optional[ExpiryConfig] = None,
    now_fn: Optional[_NOW_FN] = None,
    prev_class_map: Optional[dict] = None,
) -> ExpiryCalendar:
    """Classify each instrument in universe and return an ExpiryCalendar.

    Args:
        universe:       Instrument dicts from select_universe() or
                        ChainRefresher.current_snapshot().universe.
                        Required dict keys: symbol, contract_type, strike_price,
                        settlement_time (str or datetime), atm_distance.
        mark_price:     BTC perpetual mark price at snapshot time.
        config:         ExpiryConfig; uses default if None.
        now_fn:         Injectable UTC clock for testing.
        prev_class_map: {settlement_time.isoformat(): ExpiryClass} from the
                        previous observe tick.  Used for no-drift assertion.
                        Pass None to skip drift detection.

    Returns:
        ExpiryCalendar with all records classified, sorted, and posture-ready.
    """
    if config is None:
        config = ExpiryConfig()

    now = utc_now(now_fn)
    divergence: List[str] = []
    records: List[ExpiryRecord] = []

    for inst in universe:
        symbol = inst.get("symbol", "")
        raw_settle = inst.get("settlement_time", "") or ""

        # --- Parse settlement_time ---
        if isinstance(raw_settle, datetime):
            settle_dt: Optional[datetime] = (
                raw_settle if raw_settle.tzinfo is not None
                else raw_settle.replace(tzinfo=_UTC)
            )
        else:
            settle_dt = parse_settlement_time(str(raw_settle))

        if settle_dt is None:
            divergence.append(f"PARSE_FAIL:{symbol}")
            records.append(ExpiryRecord(
                symbol=symbol,
                contract_type=inst.get("contract_type", ""),
                strike_price=float(inst.get("strike_price", 0) or 0),
                settlement_time=_PARSE_FAIL_SENTINEL,
                expiry_class=ExpiryClass.UNKNOWN,
                expiry_phase=ExpiryPhase.SETTLED,
                seconds_to_settlement=(_PARSE_FAIL_SENTINEL - now).total_seconds(),
                atm_distance=float(inst.get("atm_distance", 0.0) or 0.0),
                classification_mode=ClassificationMode.DISCOVERY,
            ))
            continue

        # --- Classify ---
        expiry_cls = classify_expiry_class(settle_dt, config)
        secs = (settle_dt - now).total_seconds()
        phase = classify_expiry_phase(secs, config)
        mode = _classify_mode(settle_dt, now, config)

        # --- No-drift assertion ---
        if prev_class_map is not None:
            key = settle_dt.isoformat()
            prev_cls = prev_class_map.get(key)
            if (
                prev_cls is not None
                and prev_cls != ExpiryClass.UNKNOWN
                and expiry_cls != ExpiryClass.UNKNOWN
                and prev_cls != expiry_cls
            ):
                msg = (
                    f"DRIFT:{symbol}:"
                    f"settle={settle_dt.date().isoformat()}"
                    f":was={prev_cls.value}:now={expiry_cls.value}"
                )
                divergence.append(msg)
                logger.error("[expiry] no-drift violation: %s", msg)

        records.append(ExpiryRecord(
            symbol=symbol,
            contract_type=inst.get("contract_type", ""),
            strike_price=float(inst.get("strike_price", 0) or 0),
            settlement_time=settle_dt,
            expiry_class=expiry_cls,
            expiry_phase=phase,
            seconds_to_settlement=secs,
            atm_distance=float(inst.get("atm_distance", 0.0) or 0.0),
            classification_mode=mode,
        ))

    # Sort: settlement_time ASC, then strike_price ASC
    records.sort(key=lambda r: (r.settlement_time, r.strike_price))

    # Cycle metrics (computed once from the same `now`)
    last_anchor = last_cycle_anchor(now, config.settlement_hour_utc, config.settlement_minute_utc)
    elapsed = (now - last_anchor).total_seconds()
    fraction = max(0.0, min(1.0, elapsed / config.cycle_duration_sec))

    return ExpiryCalendar(
        records=records,
        mark_price=mark_price,
        captured_at=now,
        divergence_events=divergence,
        cycle_elapsed_sec=elapsed,
        cycle_fraction=fraction,
    )
