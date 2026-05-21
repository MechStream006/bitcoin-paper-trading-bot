"""Expiry infrastructure data models for the BTC paper trading bot.

All enums, frozen configs, and dataclasses used across the expiry subsystem.
No logic, no I/O, no scheduler coupling.

Module dependency: stdlib only.
"""
from __future__ import annotations

import dataclasses
import enum
from datetime import datetime
from typing import Optional


class ExpiryClass(enum.Enum):
    """Tenor classification for a BTC option contract.

    Precedence (high to low): MONTHLY > WEEKLY > DAILY > UNKNOWN.
    Anti-drift guarantee: must be a pure function of (settlement_time.date(), config).
    """
    DAILY   = "DAILY"
    WEEKLY  = "WEEKLY"
    MONTHLY = "MONTHLY"
    UNKNOWN = "UNKNOWN"


class ExpiryPhase(enum.Enum):
    """Lifecycle phase of a single option contract at a point in time.

    Derived from seconds_to_settlement at snapshot time. Per-contract label,
    not a system posture.
    """
    DISTANT          = "DISTANT"           # > 7 days to settlement
    ACTIVE           = "ACTIVE"            # 1–7 days
    PRE_EXPIRY       = "PRE_EXPIRY"        # 12–24 h
    EXPIRY_DAY       = "EXPIRY_DAY"        # 4–12 h (expiry-day morning, UTC 12:00 cycle)
    EXPIRY_INTENSIVE = "EXPIRY_INTENSIVE"  # 1–4 h
    SETTLEMENT       = "SETTLEMENT"        # 0–1 h (final window open)
    SETTLED          = "SETTLED"           # past settlement time
    DELISTED         = "DELISTED"          # removed from exchange


class ExpiryPosture(enum.Enum):
    """System-level intra-cycle runtime posture.

    Describes where the bot is in the current 24-hour BTC expiry cycle.
    Owned exclusively by ExpiryContext. Never recomputed outside ExpiryContext.
    """
    NORMAL      = "NORMAL"       # standard window  (> approaching_window_sec to settlement)
    APPROACHING = "APPROACHING"  # 4–12 h to next settlement
    INTENSIVE   = "INTENSIVE"    # 1–4 h to next settlement
    SETTLEMENT  = "SETTLEMENT"   # < 1 h to next settlement
    ROLLOVER    = "ROLLOVER"     # 0–5 min after settlement (front-contract transition)


class ClassificationMode(enum.Enum):
    """Confidence level of an ExpiryClass assignment.

    CONFIRMED: derived from calendar identity — same date always gives same class.
               Satisfies the no-drift guarantee.
    DISCOVERY: unrecognized pattern, parse failure, or far-future option where
               data quality cannot be confirmed.
    """
    CONFIRMED = "CONFIRMED"
    DISCOVERY = "DISCOVERY"


@dataclasses.dataclass(frozen=True)
class ExpiryConfig:
    """Injectable thresholds for expiry classification and posture derivation.

    Defaults match the Delta Exchange BTC option microstructure:
      - Daily settlement at 12:00:00 UTC
      - Weekly expiries on Fridays
      - Monthly expiries on the last Friday of the calendar month

    All durations in seconds unless the field name says otherwise.
    """
    # Posture thresholds (seconds to nearest unsettled settlement)
    rollover_window_sec: float    = 300.0           # 5 min post-settlement
    settlement_window_sec: float  = 3_600.0         # 1 h pre-settlement
    intensive_window_sec: float   = 4 * 3_600.0     # 4 h pre-settlement
    approaching_window_sec: float = 12 * 3_600.0    # 12 h pre-settlement

    # Phase thresholds
    distant_threshold_days: float     = 7.0
    active_threshold_days: float      = 1.0
    pre_expiry_threshold_hours: float = 12.0
    expiry_day_threshold_hours: float = 4.0
    settlement_phase_hours: float     = 1.0

    # CONFIRMED vs DISCOVERY classification horizon
    confirmed_horizon_days: float = 30.0

    # Settlement cycle anchor (Delta BTC: every calendar day at 12:00 UTC)
    settlement_hour_utc: int   = 12
    settlement_minute_utc: int = 0
    cycle_duration_sec: float  = 86_400.0

    # Weekly/monthly day-of-week config
    weekly_weekday: int = 4                          # Friday (Mon=0 … Fri=4)
    monthly_is_last_weekday_of_month: bool = True


@dataclasses.dataclass(frozen=True)
class ExpiryRecord:
    """Classified state for one option instrument at snapshot time.

    All fields are set at construction; treat as read-only.
    seconds_to_settlement < 0 means the contract has already settled.
    """
    symbol: str
    contract_type: str           # "call_options" | "put_options"
    strike_price: float
    settlement_time: datetime    # always UTC-aware
    expiry_class: ExpiryClass
    expiry_phase: ExpiryPhase
    seconds_to_settlement: float
    atm_distance: float          # strike_price − mark_price at snapshot time
    classification_mode: ClassificationMode


@dataclasses.dataclass
class ExpirySnapshot:
    """Point-in-time posture snapshot produced by ExpiryContext.observe().

    Treat as immutable after construction — ExpiryContext replaces the whole
    object on each observe tick; it never mutates an existing snapshot.

    records: list[ExpiryRecord] sorted by (settlement_time ASC, strike_price ASC).
    n_by_class: list of (ExpiryClass, int) pairs (JSON-friendly, no import cycle).
    divergence_events: warning strings emitted during this observe tick
                       (parse failures, drift violations).
    """
    captured_at: datetime                  # UTC
    mark_price: float
    posture: ExpiryPosture
    records: list                          # list[ExpiryRecord]

    # Nearest-expiry summary (None when no active contracts in universe)
    nearest_settlement: Optional[datetime]
    seconds_to_nearest: Optional[float]
    nearest_expiry_class: Optional[ExpiryClass]
    nearest_expiry_phase: Optional[ExpiryPhase]

    # Per-class coverage counts: list of (ExpiryClass, int) pairs
    n_by_class: list

    # Cycle position metrics
    cycle_elapsed_sec: float
    cycle_fraction: float

    # Divergence events from the classify pass
    divergence_events: list                # list[str]

    # Monotonically increasing tick counter (set by ExpiryContext)
    observe_count: int


class ExpiryClassDrift(Exception):
    """Raised when the same settlement_time produces a different ExpiryClass
    on a subsequent observation, indicating a config mutation or logic bug.
    Only logged/counted in Phase 1 — never propagated to crash the runtime.
    """
