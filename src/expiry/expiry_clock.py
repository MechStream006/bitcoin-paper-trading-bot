"""ExpiryClock — pure wall-clock functions for the BTC 24-hour expiry cycle.

All functions accept an optional now_fn parameter (default: datetime.now(UTC)).
No side effects. No mutable state. Safe for replay, unit tests, and
deterministic recomputation.

The settlement cycle anchor for BTC options on Delta Exchange is 12:00:00 UTC.
Every calendar day has a settlement at this time; the posture clock resets on
each settlement.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

_UTC = timezone.utc
_NOW_FN = Callable[[], datetime]


def utc_now(now_fn: Optional[_NOW_FN] = None) -> datetime:
    """Current UTC datetime.  Inject now_fn for deterministic testing."""
    return now_fn() if now_fn is not None else datetime.now(_UTC)


def parse_settlement_time(raw: str) -> Optional[datetime]:
    """Parse a Delta API / SQLite settlement_time string to a UTC-aware datetime.

    Handles:
      - 'Z' suffix        →  normalised to '+00:00' before fromisoformat
      - '+00:00' suffix   →  kept as-is
      - Naive datetime    →  assumed UTC

    Returns None on any parse failure so callers can classify as UNKNOWN.
    """
    if not raw:
        return None
    s = raw.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_UTC)
    return dt.astimezone(_UTC)


def seconds_to_settlement(
    settlement_time: datetime,
    now_fn: Optional[_NOW_FN] = None,
) -> float:
    """Signed seconds from now until settlement_time.

    Negative: contract has already settled.
    """
    now = utc_now(now_fn)
    return (settlement_time - now).total_seconds()


def last_cycle_anchor(
    now: datetime,
    settlement_hour: int = 12,
    settlement_minute: int = 0,
) -> datetime:
    """Most recent settlement anchor at or before `now`.

    Examples (12:00 UTC anchor):
      now = 2026-05-20T15:00Z  →  2026-05-20T12:00Z
      now = 2026-05-20T10:00Z  →  2026-05-19T12:00Z
      now = 2026-05-20T12:00Z  →  2026-05-20T12:00Z  (exact boundary → same day)
    """
    anchor = now.replace(
        hour=settlement_hour,
        minute=settlement_minute,
        second=0,
        microsecond=0,
    )
    if now < anchor:
        anchor -= timedelta(days=1)
    return anchor


def next_cycle_anchor(
    now: datetime,
    settlement_hour: int = 12,
    settlement_minute: int = 0,
) -> datetime:
    """Next settlement anchor strictly after `now`."""
    anchor = now.replace(
        hour=settlement_hour,
        minute=settlement_minute,
        second=0,
        microsecond=0,
    )
    if now >= anchor:
        anchor += timedelta(days=1)
    return anchor


def cycle_elapsed_sec(
    now_fn: Optional[_NOW_FN] = None,
    settlement_hour: int = 12,
    settlement_minute: int = 0,
) -> float:
    """Seconds elapsed since the most recent settlement cycle anchor."""
    now = utc_now(now_fn)
    return (now - last_cycle_anchor(now, settlement_hour, settlement_minute)).total_seconds()


def cycle_fraction(
    now_fn: Optional[_NOW_FN] = None,
    settlement_hour: int = 12,
    settlement_minute: int = 0,
    cycle_duration_sec: float = 86_400.0,
) -> float:
    """Fraction of the current 24 h settlement cycle elapsed: [0.0, 1.0].

    0.0 = just after last settlement anchor.
    1.0 = just before next settlement anchor.
    """
    elapsed = cycle_elapsed_sec(now_fn, settlement_hour, settlement_minute)
    return max(0.0, min(1.0, elapsed / cycle_duration_sec))
