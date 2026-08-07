"""Dataset Declaration + Resolution — ADR-I012 §8 stages 1-2, §8.1 RF1/RF5/RF6, §14.

Stage 1 DECLARATION  capture intent and all references; index the citations
Stage 2 RESOLUTION   bind every reference to a hash at `as_of`; pin corpus
                     revisions; refuse RF1, RF5, RF6

REFUSAL SCOPE (WBS 4.3 owns three of the six)
    RF1  unresolvable or non-Active specification reference   <- here
    RF2  sealed-region intersection without exclusion         <- here (WBS 4.6)
    RF3  lane conflict                                        <- here (WBS 4.7)
    RF4  declared floor exceeds computed meet                 -> WBS 4.4
    RF5  label maturity requirement unsatisfiable             <- here
    RF6  warm-up unavailable within the resolved window       <- here

    §8's stage table lists sealed-region intersection and lane conflict as
    Resolution refusals; both now land here. RF2 (WBS 4.6) reads the governed
    registry in `sealed_regions.py`; RF3 (WBS 4.7) reads the frozen lane table
    in `lanes.py`, which imports it from the evidence ledger rather than
    redefining it. §8.1's whole point is that these are applied *at
    resolution*, so a consumer cannot read a sealed region or a contaminated
    lane regardless of what the consumer's code does.

    RF4 alone remains assigned elsewhere (WBS 4.4, stage 4) and is named in
    `ResolutionReport.deferred` on every run, so a reader can never mistake
    "not checked here" for "checked and passed".

DEPENDENCY DIRECTION
    The builder is downstream of the Feature Store and the Label Store, so
    importing them is the correct direction -- the inversion WBS 4.2 avoided
    was the opposite one. Even so, the refusal logic depends on a
    `ReferenceResolver` Protocol rather than on those modules directly:
    `StoreResolver` is the concrete adapter that imports them. This keeps the
    refusals testable without either store on disk, and keeps the rule
    "the builder reads no operational data" (§8.3) verifiable by inspecting
    one small class instead of the whole module.

WHAT RESOLUTION DOES NOT DO
    It does not compute the admissibility meet (stage 3, WBS 4.4), run the
    Tier B-D battery (stage 4, WBS 4.4), or seal (stage 5, WBS 4.5). A
    successful `resolve()` moves Declared -> Resolved and nothing further.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Protocol, runtime_checkable

from dataset_builder import citation_index as ci
from dataset_builder import registry as reg
from dataset_builder import lanes as ln
from dataset_builder import sealed_regions as sr

#: Milestones that own the refusals this module deliberately does not run.
#: Surfaced on every ResolutionReport so an unchecked refusal is never read as
#: a passed one.
DEFERRED_REFUSALS: dict[str, str] = {
    # RF2 (WBS 4.6) and RF3 (WBS 4.7) were here until implemented; both are
    # now enforced below. RF4 is a stage-4 check and genuinely does not run at
    # resolution -- it is the only refusal still deferred from this stage.
    "RF4": "declared admissibility floor exceeds the computed meet (WBS 4.4, stage 4)",
}

#: Maturity requirements that demand labels be fully matured by `as_of`.
#: A declaration asking for anything else is not making a maturity claim RF5
#: can falsify.
_MATURITY_DEMANDING: frozenset[str] = frozenset({"matured", "mature", "complete"})


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class ResolutionError(reg.DatasetRegistryError):
    """Base class for declaration/resolution failures."""


class DuplicateLineageError(ResolutionError):
    """The same declaration content already exists under a different lineage.

    Content addressing makes the second declaration a no-op that silently
    returns the first one's handle -- with the *original* lineage. A caller
    who believes they are opening a new research line would be wrong and
    would never find out. Refusing is the honest outcome: either cite the
    existing dataset, or change something the declaration actually declares.
    """


class ResolutionRefused(ResolutionError):
    """One or more refusal points fired. The dataset was moved to Rejected and
    the reasons recorded permanently (§12, IQROS-P15) before this was raised."""

    def __init__(self, spec_hash: str, refusals: list[Refusal]) -> None:
        self.spec_hash = spec_hash
        self.refusals = list(refusals)
        codes = sorted({r.code for r in refusals})
        super().__init__(
            f"resolution refused for {spec_hash[:12]}... "
            f"({len(refusals)} refusal(s), {'/'.join(codes)}): "
            + "; ".join(r.detail for r in refusals)
        )


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Refusal:
    """One refusal, named and diagnosable. `subject` is the exact reference
    that failed so a caller never has to guess which of twenty it was."""
    code: str
    subject: str
    detail: str


@dataclass(frozen=True)
class ResolvedReference:
    kind: str               # 'feature' | 'label' | 'evidence'
    reference: str          # as declared
    bound_hash: str         # what it resolved to at as_of
    phase: str | None = None
    detail: dict = field(default_factory=dict)


@dataclass
class ResolutionReport:
    spec_hash: str
    ok: bool
    resolved: list[ResolvedReference] = field(default_factory=list)
    refusals: list[Refusal] = field(default_factory=list)
    pinned_revisions: dict[str, str] = field(default_factory=dict)
    #: Refusal points not evaluated here, with the milestone that owns each.
    deferred: dict[str, str] = field(default_factory=lambda: dict(DEFERRED_REFUSALS))
    #: Checks that ran but could not reach a verdict -- e.g. RF3 when a lane
    #: expression does not determine its run_kind half. Never a silent pass.
    not_evaluable: list[str] = field(default_factory=list)

    def refusals_by_code(self, code: str) -> list[Refusal]:
        return [r for r in self.refusals if r.code == code]


# ---------------------------------------------------------------------------
# Reference resolution interface
# ---------------------------------------------------------------------------

@runtime_checkable
class ReferenceResolver(Protocol):
    """What Resolution needs to know about the world outside the registry.

    Deliberately narrow. Every method answers a question about *one*
    reference, so a test double is a few lines and a real adapter cannot
    smuggle in operational data (§8.3).
    """

    def get_feature(self, spec_hash: str) -> dict | None:
        """Feature Specification row, or None if it does not exist."""

    def get_label(self, spec_hash: str) -> dict | None:
        """Label Specification row, or None if it does not exist."""

    def corpus_coverage(self, source: str) -> tuple[str, str] | None:
        """`(earliest, latest)` ISO dates the corpus holds for a source, or
        None if the source is unknown."""

    def pinned_revision(self, source: str) -> str | None:
        """Current revision id for a source, or None if unknown."""


class StoreResolver:
    """Concrete adapter over the Feature Store, Label Store and a corpus
    coverage map.

    This is the only place the builder imports the upstream registries. The
    corpus map is passed in rather than read from disk: WBS 4.0 conformed the
    corpus and its manifests are the authority, and re-deriving coverage here
    would duplicate that authority in a second place.
    """

    def __init__(
        self,
        feature_store_db: Path | str | None = None,
        label_store_db: Path | str | None = None,
        corpus_coverage_map: dict[str, tuple[str, str]] | None = None,
        revision_map: dict[str, str] | None = None,
    ) -> None:
        self._fs_db = feature_store_db
        self._ls_db = label_store_db
        self._coverage = dict(corpus_coverage_map or {})
        self._revisions = dict(revision_map or {})

    def get_feature(self, spec_hash: str) -> dict | None:
        if self._fs_db is None:
            return None
        from feature_store import registry as fs
        return fs.get_specification(self._fs_db, spec_hash)

    def get_label(self, spec_hash: str) -> dict | None:
        if self._ls_db is None:
            return None
        from label_store import registry as ls
        return ls.get_specification(self._ls_db, spec_hash)

    def corpus_coverage(self, source: str) -> tuple[str, str] | None:
        return self._coverage.get(source)

    def pinned_revision(self, source: str) -> str | None:
        return self._revisions.get(source)


# ---------------------------------------------------------------------------
# Stage 1 — Declaration
# ---------------------------------------------------------------------------

def declare(
    db_path: Path | str,
    declaration: dict,
    *,
    lineage: str,
    declared_by: str,
) -> reg.SpecificationHandle:
    """Stage 1: declare a dataset and index its citations in one call.

    Tier A runs inside `registry.declare_dataset()`; a failing declaration is
    recorded as Rejected there and raises before anything is indexed -- a
    rejected declaration has no citations because it never became a dataset.

    On success the citation index is populated **automatically** from the
    declaration's three reference classes. That closes `TD-4.2-2`: after 4.2
    `index_declaration()` had to be called by hand, so a declaration could
    exist with no citation edges and the reverse index would under-report.
    Declaration and indexing are now one operation.

    Refuses `DuplicateLineageError` when byte-identical content already exists
    under a different lineage -- see that exception for why silence would be
    worse.
    """
    reg._assert_dataset_builder_schema(db_path)

    spec_hash = reg.declaration_hash(declaration)
    existing = reg.get_specification(db_path, spec_hash)
    if existing is not None and existing["lineage"] != lineage:
        raise DuplicateLineageError(
            f"declaration {spec_hash[:12]}... already exists under lineage "
            f"{existing['lineage']!r} (phase {existing['phase']}); refusing to "
            f"re-declare it under {lineage!r}. Content addressing means this "
            f"would silently return the original, not create a new line."
        )

    handle = reg.declare_dataset(
        db_path, declaration, lineage=lineage, declared_by=declared_by
    )
    ci.index_declaration(db_path, handle.spec_hash, declaration)
    return handle


# ---------------------------------------------------------------------------
# Refusal points
# ---------------------------------------------------------------------------

def _parse_date(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text[:10]).date()
    except ValueError:
        return None


def _check_rf1(declaration: dict, resolver: ReferenceResolver) -> tuple[list[Refusal], list[ResolvedReference]]:
    """RF1 — unresolvable or non-Active specification reference.

    Enforces `ADR-I010` §11 and `ADR-I011` §11.1: a dataset may only bind
    specifications that are **Active**. `Registered` is not enough -- a
    Registered Label Specification has not had its binding equivalence
    demonstrated (WBS 3.11), so citing it would assert a correspondence that
    was never established. Every refusal names the offending hash and the
    phase it was actually in.
    """
    refusals: list[Refusal] = []
    resolved: list[ResolvedReference] = []

    for ref in declaration.get("feature_references") or []:
        spec = resolver.get_feature(str(ref))
        if spec is None:
            refusals.append(Refusal(
                "RF1", str(ref),
                f"feature specification {str(ref)[:12]}... is unresolvable "
                f"(not present in the Feature Store)",
            ))
            continue
        phase = spec.get("phase")
        if phase != "Active":
            refusals.append(Refusal(
                "RF1", str(ref),
                f"feature specification {str(ref)[:12]}... is in phase "
                f"{phase!r}, not 'Active' (ADR-I010 §11)",
            ))
            continue
        resolved.append(ResolvedReference("feature", str(ref), str(ref), phase))

    for ref in declaration.get("label_references") or []:
        if not isinstance(ref, dict):
            continue  # Tier A already refused this shape
        spec_hash = str(ref.get("spec_hash") or "")
        spec = resolver.get_label(spec_hash)
        if spec is None:
            refusals.append(Refusal(
                "RF1", spec_hash,
                f"label specification {spec_hash[:12]}... is unresolvable "
                f"(not present in the Label Store)",
            ))
            continue
        phase = spec.get("phase")
        if phase != "Active":
            refusals.append(Refusal(
                "RF1", spec_hash,
                f"label specification {spec_hash[:12]}... is in phase "
                f"{phase!r}, not 'Active' (ADR-I011 §11.1); a Registered "
                f"specification has no demonstrated binding equivalence",
            ))
            continue
        resolved.append(ResolvedReference("label", spec_hash, spec_hash, phase))

    return refusals, resolved


def _check_rf5(declaration: dict, resolver: ReferenceResolver) -> list[Refusal]:
    """RF5 — label maturity requirement unsatisfiable (`ADR-I011` §16.3).

    A label observed at time `t` matures at `t + horizon_days`. The latest
    observation a dataset can contain is at `window_end`, so **every** label
    in the window is matured only if:

        window_end + horizon_days <= as_of

    If not, the declaration is asking for labels that cannot have matured by
    its own knowledge cutoff. This is unsatisfiable in principle, not merely
    unsatisfied today -- waiting does not help, because `as_of` is declared,
    not current time. Refusing is correct; silently truncating the window to
    the satisfiable part would answer a question nobody asked.

    Only fires when the declaration actually demands maturity. A declaration
    requesting, say, `partial` makes no claim RF5 can falsify.
    """
    refusals: list[Refusal] = []
    scope = declaration.get("temporal_scope")
    if not isinstance(scope, dict):
        return refusals
    window_end = _parse_date(scope.get("window_end"))
    as_of = _parse_date(scope.get("as_of"))
    if window_end is None or as_of is None:
        return refusals  # Tier A already refused a missing/blank field

    for ref in declaration.get("label_references") or []:
        if not isinstance(ref, dict):
            continue
        requirement = str(ref.get("maturity_requirement") or "").strip().lower()
        if requirement not in _MATURITY_DEMANDING:
            continue
        spec_hash = str(ref.get("spec_hash") or "")
        spec = resolver.get_label(spec_hash)
        if spec is None:
            continue  # RF1 already refused it; do not double-report
        horizon = spec.get("horizon_days")
        if horizon is None:
            continue
        matures_on = window_end + timedelta(days=int(horizon))
        if matures_on > as_of:
            refusals.append(Refusal(
                "RF5", spec_hash,
                f"label {spec_hash[:12]}... requires maturity {requirement!r} but "
                f"its horizon is {int(horizon)}d: an observation at window_end "
                f"{window_end.isoformat()} matures {matures_on.isoformat()}, after "
                f"as_of {as_of.isoformat()} (ADR-I011 §16.3)",
            ))
    return refusals


def _check_rf2(declaration: dict) -> list[Refusal]:
    """RF2 -- sealed-region intersection without explicit exclusion
    (§8.1, IQROS-P7; resolves PS3).

    Reads the governed registry rather than a literal. A window touching a
    sealed region must declare an exclusion that **fully covers** it; partial
    coverage leaves the remainder readable, which is the leak RF2 exists to
    prevent.

    This is the refusal that makes the lockbox structural. Before WBS 4.6 the
    discipline lived inside whichever script happened to read the data; now a
    non-excluding dataset cannot be resolved at all, so it can never be
    validated, sealed, or cited -- and the consumer never receives the rows to
    ignore the rule with.
    """
    refusals: list[Refusal] = []
    for region in sr.unexcluded_regions(declaration):
        refusals.append(Refusal(
            "RF2", region.name,
            f"window intersects sealed region {region.name!r} "
            f"({region.start}..{region.end}) with no explicit exclusion covering "
            f"it. {region.reason} Declare a sealed_region_exclusions entry that "
            f"fully covers the region, or narrow the window (§8.1 RF2, IQROS-P7)",
        ))
    return refusals


def _check_rf3(
    declaration: dict, resolver: ReferenceResolver
) -> tuple[list[Refusal], list[str]]:
    """RF3 -- lane conflict (§8.1, IQROS-P6; resolves PS4).

    PS4: *"Lane disjointness is convention. Nothing structurally prevents a
    confirmatory evaluation from resolving discovery-lane inputs"* -- so *"the
    confirmatory lane can be contaminated by the thing it exists to confirm."*
    DG4 asks to make that structural, and refusing at resolution does: a
    conflicting dataset never resolves, so it can never be sealed or cited.

    The lane vocabulary comes from the evidence ledger's frozen `LANES` table
    via `lanes.py`; nothing is redefined here.

    A resolver may optionally expose `input_lane(kind, reference)` to report
    the lane an input actually resolved to. It is read with `getattr` rather
    than added to the Protocol, so every existing resolver -- including WBS
    4.3's `StoreResolver` and the test doubles -- keeps working unchanged.

    Returns `(refusals, not_evaluable)`. A lane whose `run_kind` half is not
    determinable yields `not_evaluable`, never a silent pass.
    """
    input_lanes: dict[str, object] = {}
    lookup = getattr(resolver, "input_lane", None)
    if callable(lookup):
        evidence = declaration.get("evidence_references")
        for source in ((evidence or {}).get("sources") or []) if isinstance(evidence, dict) else []:
            lane = lookup("evidence", str(source))
            if lane is not None:
                input_lanes[str(source)] = lane
        for ref in declaration.get("feature_references") or []:
            lane = lookup("feature", str(ref))
            if lane is not None:
                input_lanes[str(ref)] = lane
        for ref in declaration.get("label_references") or []:
            if isinstance(ref, dict) and ref.get("spec_hash"):
                lane = lookup("label", str(ref["spec_hash"]))
                if lane is not None:
                    input_lanes[str(ref["spec_hash"])] = lane

    conflicts, not_evaluable = ln.check_lane_conflicts(declaration, input_lanes)
    refusals = [Refusal("RF3", c.subject, c.detail) for c in conflicts]
    return refusals, not_evaluable


def _check_rf6(declaration: dict, resolver: ReferenceResolver) -> list[Refusal]:
    """RF6 — warm-up unavailable within the resolved corpus window
    (`ADR-I010` §8.4).

    A feature with `warmup_periods = W` cannot produce a valid value at
    `window_start` unless the corpus holds W periods **before** it. Periods
    are treated as calendar days here, matching the daily corpus this platform
    collects; a feature declared on a different cadence would need its own
    period-to-days mapping, which is recorded as TD rather than guessed.

    Checked against the earliest coverage across every cited evidence source,
    because a feature reads whichever sources its inputs require and the
    binding constraint is the source that starts latest.
    """
    refusals: list[Refusal] = []
    scope = declaration.get("temporal_scope")
    if not isinstance(scope, dict):
        return refusals
    window_start = _parse_date(scope.get("window_start"))
    if window_start is None:
        return refusals

    evidence = declaration.get("evidence_references")
    sources = list((evidence or {}).get("sources") or []) if isinstance(evidence, dict) else []

    latest_start: date | None = None
    latest_source = ""
    for source in sources:
        coverage = resolver.corpus_coverage(str(source))
        if coverage is None:
            continue
        start = _parse_date(coverage[0])
        if start is None:
            continue
        if latest_start is None or start > latest_start:
            latest_start, latest_source = start, str(source)

    if latest_start is None:
        return refusals  # no coverage information; RF6 cannot be evaluated

    for ref in declaration.get("feature_references") or []:
        spec = resolver.get_feature(str(ref))
        if spec is None:
            continue  # RF1 already refused it
        warmup = spec.get("warmup_periods")
        if warmup is None:
            continue
        required_from = window_start - timedelta(days=int(warmup))
        if latest_start > required_from:
            refusals.append(Refusal(
                "RF6", str(ref),
                f"feature {str(ref)[:12]}... needs {int(warmup)} warm-up period(s) "
                f"before window_start {window_start.isoformat()}, i.e. data from "
                f"{required_from.isoformat()}; corpus source {latest_source!r} "
                f"begins {latest_start.isoformat()} (ADR-I010 §8.4)",
            ))
    return refusals


# ---------------------------------------------------------------------------
# Stage 2 — Resolution
# ---------------------------------------------------------------------------

def resolve(
    db_path: Path | str,
    spec_hash: str,
    resolver: ReferenceResolver,
) -> ResolutionReport:
    """Stage 2: bind every reference at `as_of`, pin corpus revisions, and
    apply RF1/RF5/RF6.

    On success: Declared -> Resolved, with the report's `resolved` listing
    every bound reference.

    On refusal: Declared -> **Rejected**, reasons recorded permanently, and
    `ResolutionRefused` raised. The rejection is written **before** the
    exception propagates -- a caller that swallows the error still cannot
    erase the negative record (P4-D3, the `LS-TD-002` failure mode).

    All three refusal points run before any decision is made, so a caller sees
    every problem at once rather than fixing them one resolution at a time.
    """
    reg._assert_dataset_builder_schema(db_path)

    spec = reg.get_specification(db_path, spec_hash)
    if spec is None:
        raise ResolutionError(f"no such specification: {spec_hash}")
    if spec["phase"] != "Declared":
        raise reg.TransitionError(
            f"specification {spec_hash[:12]}... is in phase {spec['phase']!r}, "
            f"not 'Declared'; resolve() only applies to a freshly declared dataset"
        )

    declaration = spec["declaration"]
    refusals: list[Refusal] = []
    resolved: list[ResolvedReference] = []

    rf1_refusals, rf1_resolved = _check_rf1(declaration, resolver)
    refusals += rf1_refusals
    resolved += rf1_resolved
    refusals += _check_rf2(declaration)
    rf3_refusals, rf3_not_evaluable = _check_rf3(declaration, resolver)
    refusals += rf3_refusals
    refusals += _check_rf5(declaration, resolver)
    refusals += _check_rf6(declaration, resolver)

    # Pin corpus revisions (§8 stage 2, §14 "Corpus revision set"). The
    # declaration's own pins win; the resolver supplies any it did not state.
    pinned: dict[str, str] = {}
    evidence = declaration.get("evidence_references")
    if isinstance(evidence, dict):
        declared_pins = evidence.get("partition_revision_sets")
        declared_pins = declared_pins if isinstance(declared_pins, dict) else {}
        for source in evidence.get("sources") or []:
            source = str(source)
            revision = declared_pins.get(source) or resolver.pinned_revision(source)
            if revision is not None:
                pinned[source] = str(revision)
                resolved.append(ResolvedReference(
                    "evidence", source, str(revision),
                    detail={"pinned_at": "resolution"},
                ))

    report = ResolutionReport(
        spec_hash=spec_hash,
        ok=not refusals,
        resolved=resolved,
        refusals=refusals,
        pinned_revisions=pinned,
        not_evaluable=rf3_not_evaluable,
    )

    if refusals:
        reason = "; ".join(f"[{r.code}] {r.detail}" for r in refusals)
        reg.reject(db_path, spec_hash, "Declared", reason)
        raise ResolutionRefused(spec_hash, refusals)

    reg.mark_resolved(db_path, spec_hash)
    return report


def declare_and_resolve(
    db_path: Path | str,
    declaration: dict,
    *,
    lineage: str,
    declared_by: str,
    resolver: ReferenceResolver,
) -> ResolutionReport:
    """Stages 1-2 in one call, for callers that have everything to hand.

    Not a shortcut around either stage: `declare()` and `resolve()` run
    unchanged, so Tier A, automatic indexing, the duplicate-lineage refusal
    and all three refusal points apply identically.
    """
    handle = declare(db_path, declaration, lineage=lineage, declared_by=declared_by)
    return resolve(db_path, handle.spec_hash, resolver)


# ---------------------------------------------------------------------------
# Integrity
# ---------------------------------------------------------------------------

def find_unindexed_declarations(db_path: Path | str) -> list[str]:
    """Datasets with no citation edges — an orphaned declaration.

    `declare()` makes this impossible going forward by indexing in the same
    call. This exists so the invariant is auditable for rows written before
    4.3, or by `registry.declare_dataset()` called directly.

    A declaration whose three reference classes are genuinely all empty would
    also appear here; Tier A forbids that, so any hit is a real defect.
    """
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT d.spec_hash FROM dataset_specifications d "
            "LEFT JOIN citations c ON c.dataset_hash = d.spec_hash "
            "WHERE c.dataset_hash IS NULL AND d.phase != 'Rejected' "
            "ORDER BY d.spec_hash"
        ).fetchall()
    finally:
        con.close()
    return [r["spec_hash"] for r in rows]
