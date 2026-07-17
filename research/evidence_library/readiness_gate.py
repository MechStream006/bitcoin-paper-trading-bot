"""
Readiness enforcement — PR-9 of the Governance Architecture.

Arms `data_completeness_gate.md` §7 **G2**: *"The Validated gate gains a
readiness precondition. `check_validated_preconditions()` gains one more
requirement alongside DSR and BH-FDR: the pinned run's evidence window must be
100% READY. A signal cannot be Validated on evidence derived from data we know
was incomplete. Rationale is identical to the existing NULL-DSR hard-fail:
**there is no silent bypass**."*

PR-8 built the hook (`TG-RDY`) and deliberately left it disarmed, because
enforcing it then would have invalidated all 35 real families. PR-9 arms it —
and it is backward compatible **by construction**, not by concession:

    G2 names the **Validated** gate specifically, and the real ledger has
    ZERO validated families (9 candidate, 22 discovery, 4 disqualified).
    Enforcing at Validated therefore breaks nothing that exists, while
    guaranteeing that nothing is EVER validated on data we knew was incomplete.

Transitions into other states are unaffected: readiness is reported, never
blocking. That asymmetry is G2's, not an invention here.

--- Fail-closed, and what that costs ---

At the Validated gate, **absence of readiness evidence BLOCKS** (L2: *"absence
of evidence of completeness is not evidence of completeness"*). This is the
whole point and it has a real, intended consequence: **until a readiness
producer exists, no family can be promoted to Validated.** That is correct. The
alternative — letting a promotion through because nobody computed readiness — is
exactly the silent bypass G2 forbids, and is how H6's NULL reached a derivation.

The gate never fabricates a verdict. It reports what it found.

--- Two evidence sources, in precedence order ---

  1. **The run's `readiness` block** (`runs.metrics_json`, G3's shape:
     `{days_total, days_ready, days_degraded, days_non_ready[], contract_versions}`).
     G3's producer does not exist yet; when it lands, this path lights up with
     no change here.
  2. **PR-7's `data_readiness` receipts**, queried per day across the run's
     evidence window via `dq.readiness.get_readiness()` — the genuine use of the
     PR-7 framework, and point-in-time correct: each day is read **as of the run's
     own `run_ts`**, never as of now, so a readiness receipt written AFTER the run
     cannot retroactively make that run look ready. Without that, every backfill
     would silently acquire look-ahead.

A run with neither is `UNKNOWN` — which blocks at Validated and reports
elsewhere.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable

_DQ = Path(__file__).resolve().parents[1] / "dq"
if str(_DQ) not in sys.path:
    sys.path.insert(0, str(_DQ))

# The dependency direction is the designed one: data_completeness_gate.md §7 has
# the Evidence Library CONSUME readiness. PR-7's framework deliberately does not
# import the Evidence Library (asserted by its own test), so this import cannot
# create a cycle.
import readiness as dq_readiness  # noqa: E402
import states as dq_states  # noqa: E402

# The states whose entry requires a READY evidence window. G2 names Validated.
# promotion_ready / production_ready are downstream of Validated and cannot be
# reached without passing through it, so listing Validated alone is sufficient
# AND minimal -- adding the others would be belt-and-braces that quietly widens
# the blast radius beyond what governance actually approved.
STATES_REQUIRING_READINESS: frozenset[str] = frozenset({"validated"})

# A day may be declared-degraded and still admissible -- but ONLY along a path
# the contract declared in advance (§3). The gate does not decide this; it reads
# what the readiness receipt already recorded.
_ADMISSIBLE_READINESS_STATES: frozenset[str] = frozenset({dq_states.READY})


@dataclass(frozen=True)
class ReadinessAssessment:
    """What the gate found. `source` is never guessed: it says where the answer
    came from, or that there was no answer."""
    ok: bool
    source: str            # "run_block" | "readiness_receipts" | "none"
    days_total: int | None
    days_ready: int | None
    days_non_ready: tuple[str, ...]
    detail: str

    @property
    def computed(self) -> bool:
        return self.source != "none"


@runtime_checkable
class ReadinessProvider(Protocol):
    """
    The substitution point, mirroring PR-6's `AdmissibilityRule` pattern rather
    than inventing a second one: an assessor is identified, injectable, and the
    caller depends on THIS, never on a concrete implementation.

    PR-9 supplies `DefaultReadinessProvider`. A future provider (e.g. one reading
    a Feature Store's own readiness closure) implements this shape and is passed
    in -- it does not edit `validate_transition()`.
    """
    provider_id: str

    def assess(self, db_path: Path | str, run: dict) -> ReadinessAssessment:
        ...


class DefaultReadinessProvider:
    provider_id = "readiness_gate.default.v1"

    def assess(self, db_path: Path | str, run: dict) -> ReadinessAssessment:
        block = (run.get("metrics") or {}).get("readiness")
        if block is not None:
            return _from_run_block(block)
        return _from_readiness_receipts(db_path, run)


DEFAULT_PROVIDER = DefaultReadinessProvider()


def _from_run_block(block: dict) -> ReadinessAssessment:
    """G3's shape. Read as recorded -- the gate does not recompute it."""
    non_ready = tuple(block.get("days_non_ready") or ())
    total = block.get("days_total")
    ready = block.get("days_ready")
    return ReadinessAssessment(
        ok=not non_ready,
        source="run_block",
        days_total=total,
        days_ready=ready,
        days_non_ready=non_ready,
        detail=(
            f"{len(non_ready)} non-READY day(s) in the run's recorded readiness block: "
            f"{list(non_ready[:5])}"
            if non_ready else
            f"run's readiness block reports {ready}/{total} days READY"
        ),
    )


def _from_readiness_receipts(db_path: Path | str, run: dict) -> ReadinessAssessment:
    """
    PR-7's data_readiness receipts, one day at a time across the run's evidence
    window.

    Point-in-time correct: each day is read **as of the run's own run_ts**. A
    readiness receipt written after the run cannot retroactively make it look
    ready -- that would be look-ahead, and it is precisely the failure the whole
    evidence apparatus exists to prevent.
    """
    window_start, window_end = run.get("window_start"), run.get("window_end")
    if not window_start or not window_end:
        return ReadinessAssessment(
            ok=False, source="none", days_total=None, days_ready=None, days_non_ready=(),
            detail="the run declares no evidence window (window_start/window_end), so its "
                   "readiness cannot be established",
        )

    try:
        days = _utc_days(window_start, window_end)
    except ValueError as exc:
        return ReadinessAssessment(
            ok=False, source="none", days_total=None, days_ready=None, days_non_ready=(),
            detail=f"the run's evidence window is unreadable: {exc}",
        )

    as_of = run.get("run_ts")
    feature_id = run["family_id"]

    non_ready: list[str] = []
    ready = 0
    found_any = False
    for day in days:
        try:
            row = dq_readiness.get_readiness(db_path, feature_id, day, as_of=as_of)
        except Exception:  # noqa: BLE001 -- e.g. no data_readiness table at all
            row = None
        if row is None:
            non_ready.append(f"{day}=NO_RECEIPT")
            continue
        found_any = True
        if row["state"] in _ADMISSIBLE_READINESS_STATES:
            ready += 1
        else:
            non_ready.append(f"{day}={row['state']}")

    if not found_any:
        return ReadinessAssessment(
            ok=False, source="none", days_total=len(days), days_ready=0, days_non_ready=(),
            detail=(
                f"no readiness receipt exists for any of the {len(days)} day(s) in this run's "
                f"evidence window ({window_start}..{window_end}, feature {feature_id!r}) as of "
                f"the run's own run_ts. A day nobody looked at is not a day that passed."
            ),
        )

    return ReadinessAssessment(
        ok=not non_ready,
        source="readiness_receipts",
        days_total=len(days),
        days_ready=ready,
        days_non_ready=tuple(non_ready),
        detail=(
            f"{len(non_ready)} of {len(days)} day(s) in the evidence window are not READY: "
            f"{list(non_ready[:5])}"
            if non_ready else
            f"all {len(days)} day(s) in the evidence window are READY "
            f"(per PR-7 readiness receipts, as of the run's run_ts)"
        ),
    )


def _utc_days(window_start: str, window_end: str) -> list[str]:
    """Inclusive list of YYYY-MM-DD days spanned by the window. Accepts a bare
    date or a full ISO datetime."""
    start = _to_date(window_start)
    end = _to_date(window_end)
    if end < start:
        raise ValueError(f"window_end {window_end!r} precedes window_start {window_start!r}")
    out, cur = [], start
    while cur <= end:
        out.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return out


def _to_date(value: str) -> datetime:
    text = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        dt = datetime.strptime(text[:10], "%Y-%m-%d")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def requires_readiness(to_state: str) -> bool:
    return to_state in STATES_REQUIRING_READINESS


def assess(
    db_path: Path | str, run: dict | None, provider: ReadinessProvider | None = None
) -> ReadinessAssessment:
    if run is None:
        return ReadinessAssessment(
            ok=False, source="none", days_total=None, days_ready=None, days_non_ready=(),
            detail="no pinned run, so there is no evidence window to assess",
        )
    return (provider or DEFAULT_PROVIDER).assess(db_path, run)
