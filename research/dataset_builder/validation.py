"""Admissibility Composition + Validation Tiers A-D — ADR-I012 §8 stages 3-4,
§8.1 RF4, §9.1, §12.

Stage 3 ADMISSIBILITY COMPOSITION   the meet across every input
Stage 4 VALIDATION                  Tiers A, B, C, D in that order

THE TIER D DISTINCTION THAT SHAPES THIS MODULE
    §12: *"Tier D — Statistical hygiene ... These **cap** admissibility; they
    do not block."* Tiers A-C refuse. Tier D does **not** refuse: it lowers
    the dataset's ceiling. A class-imbalanced dataset is not invalid, it is
    *less admissible*, and a consumer with a high floor will be refused by
    RF4 rather than by Tier D. Conflating the two would either reject usable
    research or admit unusable research; the lattice exists so neither is
    necessary.

RF4 IS `satisfies()`, NOT A REIMPLEMENTATION
    §8.1 RF4: *"Declared admissibility floor exceeds the computed meet."*
    Phase 0 already built the lattice (`research/admissibility/`), whose
    `satisfies(ceiling, floor)` returns every unmet dimension (ADR-I014 AD4).
    RF4 calls it. Re-deriving the comparison here would create a second
    authority on admissibility, which is exactly what IQROS-P4 forbids.

DEFERRED, NEVER SILENT
    §12's Tier B text includes sealed-region intersection (RF2, WBS 4.6) and
    its Tier C text includes lane conflict (RF3, WBS 4.7). Those checks are
    **not** implemented here. Every `ValidationReport` carries a `deferred`
    map naming each and the milestone that owns it, and Tier D findings
    computed without statistics are recorded as `not_computed` rather than
    passing. A reader can never mistake "not checked" for "checked and clean".

SCOPE
    Stage 5 (seal, receipts) is WBS 4.5. `validate()` moves
    Resolved -> Validated on success, or Resolved -> Rejected on a blocking
    failure, and stops.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Sequence

# The admissibility lattice is imported the way every other consumer imports
# it -- bare, with `research/admissibility` on sys.path (see
# research/quality/admissibility.py). Package-qualified imports load the module
# a SECOND time under a different name, producing two distinct `Dimension`
# enums whose members are not equal, which fails deep inside `level()`.
_ADMISSIBILITY_DIR = Path(__file__).resolve().parents[1] / "admissibility"
if str(_ADMISSIBILITY_DIR) not in sys.path:
    sys.path.insert(0, str(_ADMISSIBILITY_DIR))

from composition import meet, satisfies  # noqa: E402
from lattice import Admissibility, Dimension  # noqa: E402

from dataset_builder import registry as reg
from dataset_builder import resolution as res

#: Checks §12 describes but a later milestone owns. Present on every report.
DEFERRED_CHECKS: dict[str, str] = {
    "RF2": "sealed-region intersection without explicit exclusion — Tier B (WBS 4.6)",
    "RF3": "lane conflict — confirmatory dataset resolving discovery-lane inputs — Tier C (WBS 4.7)",
}

#: Tier order is fixed by §12 and by the roadmap. A later tier may depend on an
#: earlier one having run, and a blocking failure short-circuits the rest.
TIER_ORDER: tuple[str, ...] = ("A", "B", "C", "D")

#: Certification verdicts that satisfy Tier C's "verdict present" requirement.
#: RED and UNKNOWN are *present* but block via the lattice, not via Tier C.
_KNOWN_VERDICTS: frozenset[str] = frozenset({"GREEN", "AMBER", "RED", "UNKNOWN"})


class ValidationError(reg.DatasetRegistryError):
    """Base class for validation failures."""


class ValidationRefused(ValidationError):
    """One or more blocking findings. The dataset was moved to Rejected and
    the reasons recorded permanently (§12, IQROS-P15) before this was raised."""

    def __init__(self, spec_hash: str, findings: list["Finding"]) -> None:
        self.spec_hash = spec_hash
        self.findings = list(findings)
        codes = sorted({f.code for f in findings})
        super().__init__(
            f"validation refused for {spec_hash[:12]}... "
            f"({len(findings)} blocking finding(s), {'/'.join(codes)}): "
            + "; ".join(f.detail for f in findings)
        )


# ---------------------------------------------------------------------------
# Findings and reports
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Finding:
    """One validation outcome, fully self-describing.

    Every field is required because a finding that cannot be traced to its
    governing clause and its failing field is not actionable — the reader has
    to go read this module to understand it, which is the failure mode
    `assert, never repair` is meant to avoid on the diagnostic side.
    """
    code: str                       # RF4, TB1-TB4, TC1-TC4, TD1-TD5
    tier: str                       # A | B | C | D
    adr_clause: str                 # e.g. "ADR-I012 §12 Tier B"
    fields: tuple[str, ...]         # the declaration field(s) at fault
    detail: str
    blocking: bool                  # Tier D findings are never blocking
    caps: dict | None = None        # Tier D only: the cap it imposes


@dataclass
class ValidationReport:
    spec_hash: str
    ok: bool
    tiers_run: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    #: Stage 3 — computed once, stated explicitly, never asserted (§9.1).
    computed_meet: Admissibility | None = None
    declared_floor: Admissibility | None = None
    #: The meet after Tier D caps. Equal to `computed_meet` when Tier D is clean.
    capped_admissibility: Admissibility | None = None
    deferred: dict[str, str] = field(default_factory=lambda: dict(DEFERRED_CHECKS))
    #: Tier D checks that could not run because no statistics were supplied.
    not_computed: list[str] = field(default_factory=list)

    @property
    def blocking(self) -> list[Finding]:
        return [f for f in self.findings if f.blocking]

    @property
    def advisory(self) -> list[Finding]:
        return [f for f in self.findings if not f.blocking]

    def by_tier(self, tier: str) -> list[Finding]:
        return [f for f in self.findings if f.tier == tier]

    def by_code(self, code: str) -> list[Finding]:
        return [f for f in self.findings if f.code == code]


@dataclass(frozen=True)
class InputAdmissibility:
    """One resolved input's contribution to the meet (stage 3)."""
    kind: str                       # 'evidence' | 'feature' | 'label'
    reference: str
    ceiling: Admissibility
    certification_verdict: str | None = None
    lane: str | None = None
    readiness_resolved: bool = True
    #: `(start, end)` ISO dates the bound partition actually covers. Used by
    #: Tier B's TB3. None means the span is unknown, and TB3 does not fire --
    #: refusing on absent information would be guessing.
    window: tuple[str, str] | None = None


@dataclass(frozen=True)
class DatasetStatistics:
    """Tier D inputs. Supplied by the caller because the dataset is not
    materialised at this stage (§8.2 — materialisation is off-lifecycle, WBS
    4.8). When absent, Tier D is recorded as `not_computed`, never as passed.
    """
    row_count: int | None = None
    observed_days: int | None = None
    calendar_years_spanned: int | None = None
    degenerate_columns: tuple[str, ...] = ()
    label_positive_fraction: float | None = None
    matured_fraction: float | None = None


# ---------------------------------------------------------------------------
# Stage 3 — admissibility composition (§9.1, IQROS-P4)
# ---------------------------------------------------------------------------

def compose_admissibility(inputs: Sequence[InputAdmissibility]) -> Admissibility:
    """The meet across every input — computed once, never asserted.

    Delegates to `admissibility.composition.meet()`, the platform's single
    authority. That function is order-independent and monotone decreasing, so
    composition order cannot change the result and derivation can only weaken
    admissibility, never strengthen it.
    """
    if not inputs:
        raise ValidationError(
            "cannot compose admissibility with no inputs: a dataset that cites "
            "nothing has no ceiling to compute (§9.1)"
        )
    return meet(*[i.ceiling for i in inputs])


# ---------------------------------------------------------------------------
# Tier helpers
# ---------------------------------------------------------------------------

def _parse_date(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text[:10]).date()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Tier A — structural (§12)
# ---------------------------------------------------------------------------

def check_tier_a(declaration: dict) -> list[Finding]:
    """Re-runs WBS 4.1's Tier A rather than duplicating it.

    Tier A already ran at declaration time; running it again here is not
    redundant — §12 defines Tier A as part of the validation battery, and a
    declaration reaching stage 4 through any path must have satisfied it.
    Reimplementing the field rules would create a second authority that could
    drift from the first.
    """
    ok, errors = reg.validate_tier_a(declaration)
    if ok:
        return []
    return [
        Finding(
            code="TA1", tier="A", adr_clause="ADR-I012 §12 Tier A / §7.2",
            fields=(err.split(":")[0],), detail=err, blocking=True,
        )
        for err in errors
    ]


# ---------------------------------------------------------------------------
# Tier B — temporal integrity (§12)
# ---------------------------------------------------------------------------

def check_tier_b(declaration: dict, inputs: Sequence[InputAdmissibility]) -> list[Finding]:
    """Temporal integrity.

    §12 Tier B: *"No feature reference depends on data after its own
    timestamp; every label horizon terminates within the declared maturity;
    sealed regions do not intersect the window unless explicitly excluded; no
    partition outside the declared window is bound; `as_of` closure honoured
    throughout."*

    Implemented here: `as_of` closure (TB1), label horizon termination (TB2),
    no bound partition outside the window (TB3), exclusion spans well-formed
    and inside the window (TB4).

    **Not implemented here:** sealed-region *intersection* — that is RF2 and
    WBS 4.6 owns it. TB4 validates that declared exclusions are coherent; it
    does not decide whether an exclusion was *required*.
    """
    findings: list[Finding] = []
    scope = declaration.get("temporal_scope")
    if not isinstance(scope, dict):
        return findings

    window_start = _parse_date(scope.get("window_start"))
    window_end = _parse_date(scope.get("window_end"))
    as_of = _parse_date(scope.get("as_of"))

    # TB1 — as_of closure. Nothing in the dataset may postdate its own
    # declared knowledge cutoff.
    if as_of is not None and window_end is not None and window_end > as_of:
        findings.append(Finding(
            code="TB1", tier="B", adr_clause="ADR-I012 §12 Tier B (as_of closure)",
            fields=("temporal_scope.window_end", "temporal_scope.as_of"),
            detail=(
                f"window_end {window_end.isoformat()} postdates as_of "
                f"{as_of.isoformat()}: the dataset would contain observations "
                f"later than its own knowledge cutoff"
            ),
            blocking=True,
        ))

    # TB2 — every label horizon terminates within the declared maturity.
    # Distinct from RF5: RF5 refuses an unsatisfiable *maturity requirement*;
    # TB2 refuses a horizon that runs past as_of regardless of what maturity
    # was requested, because the observation window itself would be truncated.
    for i, ref in enumerate(declaration.get("label_references") or []):
        if not isinstance(ref, dict):
            continue
        horizon = ref.get("horizon_days")
        if horizon is None or window_end is None or as_of is None:
            continue
        try:
            terminates = window_end + timedelta(days=int(horizon))
        except (TypeError, ValueError):
            continue
        if terminates > as_of:
            findings.append(Finding(
                code="TB2", tier="B",
                adr_clause="ADR-I012 §12 Tier B (label horizon termination)",
                fields=(f"label_references[{i}].horizon_days",),
                detail=(
                    f"label horizon {int(horizon)}d from window_end "
                    f"{window_end.isoformat()} terminates {terminates.isoformat()}, "
                    f"after as_of {as_of.isoformat()}"
                ),
                blocking=True,
            ))

    # TB3 — no partition bound outside the declared window.
    for inp in inputs:
        if inp.kind != "evidence":
            continue
        span = getattr(inp, "window", None)
        if not span:
            continue
        start, end = _parse_date(span[0]), _parse_date(span[1])
        if start and window_start and start < window_start:
            findings.append(Finding(
                code="TB3", tier="B",
                adr_clause="ADR-I012 §12 Tier B (no partition outside window)",
                fields=("evidence_references.sources",),
                detail=(
                    f"evidence {inp.reference!r} binds data from "
                    f"{start.isoformat()}, before window_start "
                    f"{window_start.isoformat()}"
                ),
                blocking=True,
            ))
        if end and window_end and end > window_end:
            findings.append(Finding(
                code="TB3", tier="B",
                adr_clause="ADR-I012 §12 Tier B (no partition outside window)",
                fields=("evidence_references.sources",),
                detail=(
                    f"evidence {inp.reference!r} binds data to {end.isoformat()}, "
                    f"after window_end {window_end.isoformat()}"
                ),
                blocking=True,
            ))

    # TB4 — declared exclusion spans must be well-formed and lie inside the
    # window. An exclusion outside the window excludes nothing, which almost
    # always means the span was mistyped.
    for i, span in enumerate(declaration.get("sealed_region_exclusions") or []):
        if not isinstance(span, dict):
            findings.append(Finding(
                code="TB4", tier="B",
                adr_clause="ADR-I012 §12 Tier B (exclusion well-formedness)",
                fields=(f"sealed_region_exclusions[{i}]",),
                detail=f"exclusion entry must be a mapping, got {type(span).__name__}",
                blocking=True,
            ))
            continue
        start, end = _parse_date(span.get("start")), _parse_date(span.get("end"))
        if start is None or end is None:
            findings.append(Finding(
                code="TB4", tier="B",
                adr_clause="ADR-I012 §12 Tier B (exclusion well-formedness)",
                fields=(f"sealed_region_exclusions[{i}].start",
                        f"sealed_region_exclusions[{i}].end"),
                detail="exclusion span needs a parseable start and end",
                blocking=True,
            ))
            continue
        if start >= end:
            findings.append(Finding(
                code="TB4", tier="B",
                adr_clause="ADR-I012 §12 Tier B (exclusion well-formedness)",
                fields=(f"sealed_region_exclusions[{i}]",),
                detail=f"exclusion span is empty or inverted ({start} >= {end})",
                blocking=True,
            ))
            continue
        # A non-intersecting exclusion is deliberately NOT a finding. Declaring
        # the sealed lockbox even when the window does not reach it is the
        # defensive practice this platform actually uses -- H-IVRV's own
        # 2025-06-01..2026-05-31 lockbox is declared against training windows
        # that end before it. Flagging that would penalise the careful
        # declaration and reward the silent one. RF2 (WBS 4.6) decides whether
        # an exclusion was *required*; TB4 only checks the span is coherent.

    return findings


# ---------------------------------------------------------------------------
# Tier C — admissibility (§12) + RF4 (§8.1)
# ---------------------------------------------------------------------------

def check_tier_c(
    declaration: dict,
    inputs: Sequence[InputAdmissibility],
    computed_meet: Admissibility,
    declared_floor: Admissibility | None,
) -> list[Finding]:
    """Admissibility.

    §12 Tier C: *"Certification verdict present for every partition; lane
    recorded per source; readiness resolved; the computed meet **stated
    explicitly** on the dataset, never implied; declared floor ≤ computed
    meet."*

    TC1 verdict present · TC2 lane recorded · TC3 readiness resolved ·
    **RF4** declared floor ≤ computed meet.

    "The meet stated explicitly" is satisfied structurally: the meet is a
    required field on `ValidationReport` and is carried onto the dataset by
    WBS 4.5's seal. It cannot be implied because there is nowhere to imply it
    from.

    **Lane *conflict* (RF3) is not checked here** — TC2 verifies a lane was
    recorded, not that it is compatible. WBS 4.7 owns the conflict rule.
    """
    findings: list[Finding] = []

    for inp in inputs:
        if inp.kind == "evidence":
            verdict = (inp.certification_verdict or "").strip().upper()
            if not verdict:
                findings.append(Finding(
                    code="TC1", tier="C",
                    adr_clause="ADR-I012 §12 Tier C (certification verdict present)",
                    fields=("evidence_references.required_certification_verdict",),
                    detail=f"evidence {inp.reference!r} has no certification verdict",
                    blocking=True,
                ))
            elif verdict not in _KNOWN_VERDICTS:
                findings.append(Finding(
                    code="TC1", tier="C",
                    adr_clause="ADR-I012 §12 Tier C (certification verdict present)",
                    fields=("evidence_references.required_certification_verdict",),
                    detail=(
                        f"evidence {inp.reference!r} has unrecognised verdict "
                        f"{verdict!r}; expected one of {sorted(_KNOWN_VERDICTS)}"
                    ),
                    blocking=True,
                ))
            if not (inp.lane or "").strip():
                findings.append(Finding(
                    code="TC2", tier="C",
                    adr_clause="ADR-I012 §12 Tier C (lane recorded per source)",
                    fields=("evidence_references.required_lane",),
                    detail=f"evidence {inp.reference!r} has no lane recorded",
                    blocking=True,
                ))
        if not inp.readiness_resolved:
            findings.append(Finding(
                code="TC3", tier="C",
                adr_clause="ADR-I012 §12 Tier C (readiness resolved)",
                fields=("evidence_references.sources",),
                detail=f"input {inp.reference!r} has unresolved readiness",
                blocking=True,
            ))

    # RF4 — declared floor must not exceed the computed meet.
    if declared_floor is not None:
        result = satisfies(computed_meet, declared_floor)
        if not result.admitted:
            unmet = sorted(d.name for d in result.unmet_dimensions)
            findings.append(Finding(
                code="RF4", tier="C",
                adr_clause="ADR-I012 §8.1 RF4 / IQROS-P4 / ADR-I014 AD3",
                fields=("admissibility_floor",),
                detail=(
                    f"declared admissibility floor exceeds the computed meet on "
                    f"{len(unmet)} dimension(s): {', '.join(unmet)}. The floor is "
                    f"never relaxed to permit admission (AD3) — either lower the "
                    f"declared floor or cite stronger inputs"
                ),
                blocking=True,
            ))

    return findings


# ---------------------------------------------------------------------------
# Tier D — statistical hygiene (§12) — CAPS, NEVER BLOCKS
# ---------------------------------------------------------------------------

def check_tier_d(
    stats: DatasetStatistics | None,
    *,
    min_rows: int = 30,
    min_calendar_years: int = 1,
    max_class_imbalance: float = 0.95,
    min_matured_fraction: float = 0.5,
) -> tuple[list[Finding], list[str]]:
    """Statistical hygiene. Returns `(findings, not_computed)`.

    **Every finding here is non-blocking.** §12 is explicit: Tier D checks
    *"cap admissibility; they do not block"*. Each finding carries a `caps`
    dict naming the dimension and rung it lowers the ceiling to, and
    `apply_tier_d_caps()` applies them.

    When `stats` is None the checks are recorded in `not_computed` rather than
    passing. The dataset is not materialised at this stage (§8.2), so absent
    statistics are the normal case at 4.4 — but "we did not look" must never
    be indistinguishable from "we looked and it was fine".
    """
    codes = ["TD1", "TD2", "TD3", "TD4", "TD5"]
    if stats is None:
        return [], codes

    findings: list[Finding] = []
    not_computed: list[str] = []

    # TD1 — coverage sufficiency
    if stats.row_count is None:
        not_computed.append("TD1")
    elif stats.row_count < min_rows:
        findings.append(Finding(
            code="TD1", tier="D", adr_clause="ADR-I012 §12 Tier D (coverage sufficiency)",
            fields=("temporal_scope",), blocking=False,
            detail=f"only {stats.row_count} row(s), below the {min_rows}-row sufficiency floor",
            caps={"dimension": "READINESS", "to": "PARTIAL"},
        ))

    # TD2 — calendar-year span
    if stats.calendar_years_spanned is None:
        not_computed.append("TD2")
    elif stats.calendar_years_spanned < min_calendar_years:
        findings.append(Finding(
            code="TD2", tier="D", adr_clause="ADR-I012 §12 Tier D (regime/calendar span)",
            fields=("temporal_scope.window_start", "temporal_scope.window_end"),
            blocking=False,
            detail=(
                f"spans {stats.calendar_years_spanned} calendar year(s), below "
                f"{min_calendar_years}: too narrow to span regimes"
            ),
            caps={"dimension": "READINESS", "to": "PARTIAL"},
        ))

    # TD3 — degenerate columns
    if stats.degenerate_columns:
        findings.append(Finding(
            code="TD3", tier="D", adr_clause="ADR-I012 §12 Tier D (degenerate columns)",
            fields=("feature_references",), blocking=False,
            detail=f"degenerate column(s): {', '.join(stats.degenerate_columns)}",
            caps={"dimension": "INTEGRITY", "to": "ADJUDICATED"},
        ))

    # TD4 — class imbalance disclosure
    if stats.label_positive_fraction is None:
        not_computed.append("TD4")
    else:
        frac = stats.label_positive_fraction
        if frac >= max_class_imbalance or frac <= (1.0 - max_class_imbalance):
            findings.append(Finding(
                code="TD4", tier="D", adr_clause="ADR-I012 §12 Tier D (class imbalance)",
                fields=("label_references",), blocking=False,
                detail=(
                    f"label positive fraction {frac:.3f} exceeds the "
                    f"{max_class_imbalance:.2f} imbalance threshold — disclosed, "
                    f"not corrected (assert, never repair)"
                ),
                caps={"dimension": "READINESS", "to": "PARTIAL"},
            ))

    # TD5 — maturity fraction
    if stats.matured_fraction is None:
        not_computed.append("TD5")
    elif stats.matured_fraction < min_matured_fraction:
        findings.append(Finding(
            code="TD5", tier="D", adr_clause="ADR-I012 §12 Tier D (maturity fraction)",
            fields=("label_references",), blocking=False,
            detail=(
                f"only {stats.matured_fraction:.1%} of labels are matured, below "
                f"{min_matured_fraction:.0%}"
            ),
            caps={"dimension": "MATURITY", "to": "PARTIAL"},
        ))

    return findings, not_computed


def apply_tier_d_caps(ceiling: Admissibility, findings: Iterable[Finding]) -> Admissibility:
    """Lower `ceiling` by every Tier D cap. Never raises it.

    Implemented as a `meet` against a cap tuple, so the monotone-decreasing
    guarantee comes from the lattice rather than from this function being
    careful. A cap can only weaken.
    """
    from lattice import (
        Certification, Integrity, Maturity, Provenance, Readiness,
    )
    _RUNGS = {
        "PROVENANCE": Provenance, "CERTIFICATION": Certification,
        "READINESS": Readiness, "MATURITY": Maturity, "INTEGRITY": Integrity,
    }
    result = ceiling
    for f in findings:
        if not f.caps:
            continue
        dim_name = str(f.caps.get("dimension", "")).upper()
        rung_name = str(f.caps.get("to", "")).upper()
        enum = _RUNGS.get(dim_name)
        if enum is None or not hasattr(enum, rung_name):
            continue
        dim = Dimension[dim_name]
        if dim in result.not_applicable:
            continue  # never manufacture a deficiency on an N/A dimension
        cap_levels = {
            "provenance": result.provenance, "certification": result.certification,
            "readiness": result.readiness, "maturity": result.maturity,
            "integrity": result.integrity,
        }
        cap_levels[dim_name.lower()] = getattr(enum, rung_name)
        cap = Admissibility(
            **cap_levels,
            not_applicable=result.not_applicable,
            lattice_version=result.lattice_version,
        )
        result = meet(result, cap)
    return result


# ---------------------------------------------------------------------------
# Stage 4 — the battery
# ---------------------------------------------------------------------------

def validate(
    db_path: Path | str,
    spec_hash: str,
    inputs: Sequence[InputAdmissibility],
    *,
    declared_floor: Admissibility | None = None,
    stats: DatasetStatistics | None = None,
) -> ValidationReport:
    """Stages 3-4: compose the meet, then run Tiers A-D in order.

    On success: Resolved -> Validated.
    On any blocking finding: Resolved -> **Rejected**, reasons recorded
    permanently, `ValidationRefused` raised. The rejection is written **before**
    the exception propagates (P4-D3 — the `LS-TD-002` failure mode).

    Tiers A, B and C all run before any decision, so a caller sees every
    blocking problem at once. Tier D always runs — it cannot block, so
    short-circuiting it would only discard information.
    """
    reg._assert_dataset_builder_schema(db_path)

    spec = reg.get_specification(db_path, spec_hash)
    if spec is None:
        raise ValidationError(f"no such specification: {spec_hash}")
    if spec["phase"] != "Resolved":
        raise reg.TransitionError(
            f"specification {spec_hash[:12]}... is in phase {spec['phase']!r}, "
            f"not 'Resolved'; validate() only applies to a resolved dataset"
        )

    declaration = spec["declaration"]

    # Stage 3 — the meet, computed once and carried, never asserted.
    computed_meet = compose_admissibility(inputs)

    findings: list[Finding] = []
    findings += check_tier_a(declaration)
    findings += check_tier_b(declaration, inputs)
    findings += check_tier_c(declaration, inputs, computed_meet, declared_floor)
    tier_d, not_computed = check_tier_d(stats)
    findings += tier_d

    capped = apply_tier_d_caps(computed_meet, tier_d)

    report = ValidationReport(
        spec_hash=spec_hash,
        ok=not any(f.blocking for f in findings),
        tiers_run=list(TIER_ORDER),
        findings=findings,
        computed_meet=computed_meet,
        declared_floor=declared_floor,
        capped_admissibility=capped,
        not_computed=not_computed,
    )

    if not report.ok:
        reason = "; ".join(
            f"[{f.code}/{f.tier}] {f.detail}" for f in report.blocking
        )
        reg.reject(db_path, spec_hash, "Resolved", reason)
        raise ValidationRefused(spec_hash, report.blocking)

    reg.mark_validated(db_path, spec_hash)
    return report


def resolve_and_validate(
    db_path: Path | str,
    spec_hash: str,
    resolver: res.ReferenceResolver,
    inputs: Sequence[InputAdmissibility],
    *,
    declared_floor: Admissibility | None = None,
    stats: DatasetStatistics | None = None,
) -> tuple[res.ResolutionReport, ValidationReport]:
    """Stages 2-4 for callers that have everything to hand. Neither stage is
    bypassed: `resolve()` and `validate()` run unchanged."""
    resolution_report = res.resolve(db_path, spec_hash, resolver)
    validation_report = validate(
        db_path, spec_hash, inputs, declared_floor=declared_floor, stats=stats
    )
    return resolution_report, validation_report
