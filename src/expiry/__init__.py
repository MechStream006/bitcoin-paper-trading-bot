"""src.expiry — Phase 1 expiry infrastructure (observe-and-tag only).

Public API surface for the expiry subsystem.  Import from here rather than
from individual submodules to insulate callers from internal reorganisation.
"""
from src.expiry.models import (
    ClassificationMode,
    ExpiryClass,
    ExpiryClassDrift,
    ExpiryConfig,
    ExpiryPhase,
    ExpiryPosture,
    ExpiryRecord,
    ExpirySnapshot,
)
from src.expiry.expiry_clock import (
    cycle_elapsed_sec,
    cycle_fraction,
    last_cycle_anchor,
    next_cycle_anchor,
    parse_settlement_time,
    seconds_to_settlement,
    utc_now,
)
from src.expiry.expiry_calendar import (
    ExpiryCalendar,
    build_calendar,
    classify_expiry_class,
    classify_expiry_phase,
)
from src.expiry.expiry_context import ExpiryContext

__all__ = [
    # models
    "ClassificationMode",
    "ExpiryClass",
    "ExpiryClassDrift",
    "ExpiryConfig",
    "ExpiryPhase",
    "ExpiryPosture",
    "ExpiryRecord",
    "ExpirySnapshot",
    # calendar
    "ExpiryCalendar",
    "build_calendar",
    "classify_expiry_class",
    "classify_expiry_phase",
    # context
    "ExpiryContext",
    # clock
    "cycle_elapsed_sec",
    "cycle_fraction",
    "last_cycle_anchor",
    "next_cycle_anchor",
    "parse_settlement_time",
    "seconds_to_settlement",
    "utc_now",
]
