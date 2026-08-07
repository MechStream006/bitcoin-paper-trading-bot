"""Governed Sealed Regions — ADR-I012 §8.1 RF2, IQROS-P7; resolves PS3.

**The single governed source of truth for sealed regions in the Dataset
Builder path.** Before this module the lockbox window existed only as a date
literal repeated across analysis scripts, which is exactly the failure §8.1
describes:

> *"RF2 is the structural relocation of lockbox discipline. The audited
> platform enforces its lockbox inside the script that would otherwise read
> it. Here, exclusion is applied at resolution: a consumer **cannot** read a
> sealed region from a dataset that excludes it, regardless of what the
> consumer's code does. Enforcement stops depending on every future script
> remembering."*

WHAT CHANGED, AND WHAT DELIBERATELY DID NOT (`DS-DEC-004`, amended 2026-08-07)

    The original RF2 plan was to make three analysis scripts read from one
    governed constant. Investigation before implementation found that two of
    the three **cannot** be edited without breaking governance that is
    currently live:

    `forward_evidence/decision_function.py`
        Cryptographically hash-locked. `pre_registration.json` records
        `decision_function_hash = f78f0a52…`, and `daily_job.py` compares the
        file's SHA-256 against it on **every** forward-evidence run, raising
        `CRITICAL: fn_hash mismatch` and setting `status="error"` on any
        difference. Editing even a descriptive string would break daily
        collection in production. The file itself says: *"Do not modify
        decision_function.py after this registration."*

    `forward_evidence/m0_pre_register.py`
        Writes the frozen pre-registration record. Changing how its
        `lockbox_status.window` is produced would edit a historical governance
        artifact.

    Both are therefore **intentionally frozen governance artifacts** and are
    recorded here as explicit, documented exceptions to RF2 rather than being
    modified. `FROZEN_EXCEPTIONS` below names them, with the reason, so the
    exception is tracked in code rather than remembered.

    The amendment also scoped RF2 to the Dataset Builder path only: **no
    analysis script is modified at all.** Result invariance is therefore
    structural — no file that produces published research output is touched,
    so no published value can change.

WHY ENFORCEMENT AT RESOLUTION IS THE POINT
    A constant read from one place is still a convention: a future script can
    forget to read it. Refusing at *resolution* is different in kind — a
    dataset whose window intersects a sealed region without declaring an
    explicit exclusion **cannot be resolved**, so it can never be validated,
    sealed, or cited. The consumer's own code cannot opt out, because the
    consumer never receives the data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable


@dataclass(frozen=True)
class SealedRegion:
    """One sealed span. Immutable: a sealed region that could be edited would
    be no seal at all."""
    name: str
    start: str                  # ISO date, inclusive
    end: str                    # ISO date, inclusive
    reason: str
    spent: bool                 # single-use regions record whether consumed
    spent_on: str | None = None
    authority: str | None = None

    def as_dates(self) -> tuple[date, date]:
        return (
            datetime.fromisoformat(self.start).date(),
            datetime.fromisoformat(self.end).date(),
        )


#: The governed registry. Values are transcribed from the **published**
#: lockbox validation record (`research/reports/evidence_library/
#: lockbox_validation.json`, `"lockbox": ["2025-06-01", "2026-05-31"]`,
#: run_date 2026-06-10) — the authoritative published artifact, not from any
#: script's literal. `test_governed_source_matches_published_record` asserts
#: the agreement so drift is caught rather than assumed.
SEALED_REGIONS: tuple[SealedRegion, ...] = (
    SealedRegion(
        name="d5_lockbox",
        start="2025-06-01",
        end="2026-05-31",
        reason=(
            "Phase D.5 out-of-sample lockbox for the frozen H6 strategy "
            "family. Single-use; opened once on 2026-06-10 and never reused."
        ),
        spent=True,
        spent_on="2026-06-10",
        authority="Phase D.5 pre-registration",
    ),
)

#: Files that hold the sealed-region constant but are **deliberately not**
#: migrated to this module, with the governing reason. Recorded in code so the
#: exception is tracked rather than remembered. Ratified under `DS-DEC-004`
#: (amended 2026-08-07).
FROZEN_EXCEPTIONS: dict[str, str] = {
    "research/forward_evidence/decision_function.py": (
        "Hash-locked: pre_registration.json pins decision_function_hash and "
        "daily_job.py enforces it on every run. Editing breaks live "
        "forward-evidence collection. Frozen by DS-DEC-004 (amended)."
    ),
    "research/forward_evidence/m0_pre_register.py": (
        "Writes the frozen pre-registration record; editing would rewrite a "
        "historical governance artifact. Frozen by DS-DEC-004 (amended)."
    ),
    "research/evidence/run_lockbox_validation.py": (
        "One-time analysis script for an already-spent lockbox. Not migrated "
        "under the amended scope, which confines RF2 to the Dataset Builder "
        "path so that no published research output can change."
    ),
}


def get_region(name: str) -> SealedRegion | None:
    for region in SEALED_REGIONS:
        if region.name == name:
            return region
    return None


def _parse(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text[:10]).date()
    except ValueError:
        return None


def spans_intersect(a_start: date, a_end: date, b_start: date, b_end: date) -> bool:
    """Inclusive intersection. Two spans sharing a single day intersect —
    a sealed region leaking one observation is still a leak."""
    return a_start <= b_end and b_start <= a_end


def covers(exclusion: dict, region: SealedRegion) -> bool:
    """Does a declared exclusion fully cover a sealed region?

    **Full coverage is required, not partial.** An exclusion overlapping only
    part of a sealed region still leaves the remainder readable, which is the
    leak RF2 exists to prevent. Refusing partial coverage is the conservative
    reading, and the only one consistent with "a consumer cannot read a sealed
    region from a dataset that excludes it".
    """
    ex_start, ex_end = _parse(exclusion.get("start")), _parse(exclusion.get("end"))
    if ex_start is None or ex_end is None:
        return False
    r_start, r_end = region.as_dates()
    return ex_start <= r_start and ex_end >= r_end


def intersecting_regions(
    window_start: object,
    window_end: object,
    regions: Iterable[SealedRegion] | None = None,
) -> list[SealedRegion]:
    """Every governed sealed region the window touches."""
    w_start, w_end = _parse(window_start), _parse(window_end)
    if w_start is None or w_end is None:
        return []
    out = []
    for region in (SEALED_REGIONS if regions is None else regions):
        r_start, r_end = region.as_dates()
        if spans_intersect(w_start, w_end, r_start, r_end):
            out.append(region)
    return out


def unexcluded_regions(
    declaration: dict,
    regions: Iterable[SealedRegion] | None = None,
) -> list[SealedRegion]:
    """Sealed regions the declaration's window touches **without** declaring an
    explicit exclusion that fully covers them. These are RF2 refusals.

    An unparseable or malformed exclusion does not count as coverage — Tier B's
    TB4 reports the malformation separately, and RF2 must not treat a broken
    exclusion as a working one.
    """
    scope = declaration.get("temporal_scope")
    if not isinstance(scope, dict):
        return []
    touched = intersecting_regions(
        scope.get("window_start"), scope.get("window_end"), regions
    )
    if not touched:
        return []

    exclusions = declaration.get("sealed_region_exclusions")
    exclusions = exclusions if isinstance(exclusions, list) else []
    out = []
    for region in touched:
        if not any(covers(e, region) for e in exclusions if isinstance(e, dict)):
            out.append(region)
    return out
