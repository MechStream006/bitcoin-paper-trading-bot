"""Derives InputAdmissibility from resolved references — WBS 4.12, TD-4.4-3.

`TD-4.4-3`: `InputAdmissibility` was caller-supplied everywhere in this
builder; nothing derived it from resolved references. The one real caller
(`bootstrap_first_dataset.py`) had, in practice, grown a second, hand-rolled
admissibility implementation (`_CERT_ORDER`/`_READY_ORDER`/manual `min()`)
that duplicated — imperfectly — what the platform's real authorities already
compute.

This module derives instead of asserting, by calling those authorities
directly and composing their output with the platform's one `meet()`. It
adds no new admissibility rule and no new certification model. RF4
(`dataset_builder/validation.py::check_tier_c`) remains the sole gate; this
module only supplies its input.

Authorities reused, not reimplemented:

    evidence  ->  research/quality/admissibility.py::corpus_ceiling()
                  (= composition.meet() over each partition's own,
                  already-certified manifest verdict — WBS 4.0's own output)
    feature   ->  a Dataset-Builder-owned composition (this module,
                  `derive_feature_ceiling()`) of the feature's own static
                  assessment with the same `corpus_ceiling()` reading above,
                  over the real sources the feature declares — ratified
                  `OD-10A`, 2026-08-09. Deliberately does **not** call
                  `research/label_store/propagation.py::feature_specification_ceiling()`
                  or `source_ceiling()` — those answer a different question
                  (the safe assumption when no real certification data is
                  reachable at all) and are neither modified nor called by
                  this module. `propagation.py`'s own consumers and their
                  tests are unaffected.
    label     ->  `research/label_store/set_hygiene.py::set_admissibility_ceiling()`
                  (WBS 3.9, unmodified), scoped to exactly the subject_ids
                  the declared window covers — enumerated via
                  `set_hygiene.py::list_subject_ids()` (WBS 4.12 addition,
                  pure enumeration) and refusal-gated by
                  `request_fully_matured()` for the declaration's stated
                  maturity_requirement, mirroring RF5 (ADR-I011 §16.3)
                  exactly. This module does not aggregate per-subject
                  ceilings itself — `set_admissibility_ceiling()` already
                  does, including the `Maturity.PARTIAL` handling a
                  hand-rolled loop here would have had to duplicate.

No label specification-level ceiling exists, or is invented here (per
ADR-I011, maturity is a property of a label *value*, not the specification).
A window with no governed label value in it, or windowed subjects that do
not meet the declaration's stated maturity requirement, raise
`NoGovernedLabelDataForWindowError` / `set_hygiene.PartialMaturityRefusalError`
respectively — never a silent proxy. `validation.py::validate_with_derivation()`
converts either into a persisted `TC4` rejection (`OD-10B`, 2026-08-09)
before it propagates.
"""

from __future__ import annotations

import glob
import json
import sys
from datetime import date
from pathlib import Path
from typing import Iterable

_RESEARCH_ROOT = Path(__file__).resolve().parents[1]
if str(_RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_ROOT))

_ADMISSIBILITY_DIR = _RESEARCH_ROOT / "admissibility"
if str(_ADMISSIBILITY_DIR) not in sys.path:
    sys.path.insert(0, str(_ADMISSIBILITY_DIR))

from composition import meet  # noqa: E402
from lattice import (  # noqa: E402
    Admissibility, Certification, Dimension, Integrity, Maturity, Provenance,
    Readiness,
)


class DerivationError(Exception):
    """Base class for admissibility-derivation failures."""


class NoGovernedEvidenceForWindowError(DerivationError):
    """No certified partition manifest overlaps the declared window."""


class NoGovernedLabelDataForWindowError(DerivationError):
    """No label value, by subject_id, falls within the declared window.

    This is not a defect in the derivation logic. It means the governed
    Label Store has no per-date value for this specification covering this
    window, so nothing honest can be derived. A proxy ceiling (e.g. reusing
    an unrelated evidence source's ceiling, as the pre-WBS-4.12 hand-rolled
    path did) would hide exactly the condition DA3/DG-2 exist to surface.
    """


def _partition_admissibility_from_manifest(manifest_admissibility: dict) -> Admissibility:
    """Reconstruct one partition's ceiling from its manifest's stored verdict.

    `research/quality/admissibility.py::partition_ceiling()` computes this
    from a live gauntlet report; only the *result* (certification, readiness)
    is persisted per manifest, the report itself is not. This applies
    `partition_ceiling()`'s own documented constants (provenance
    OFFLINE_CORPUS, Maturity not-applicable, Integrity CLEAN) to the
    already-computed verdict — it is not a new rule.
    """
    return Admissibility(
        provenance=Provenance.OFFLINE_CORPUS,
        certification=Certification[manifest_admissibility["certification"]],
        readiness=Readiness[manifest_admissibility["readiness"]],
        maturity=Maturity.FULL,
        integrity=Integrity.CLEAN,
        not_applicable=frozenset({Dimension.MATURITY}),
    )


def _iso(ms: int) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime("%Y-%m-%d")


def derive_evidence_ceiling(
    raw_dir: Path, source: str, window_start: str, window_end: str,
) -> tuple[Admissibility, dict]:
    """The corpus ceiling for `source`, composed across every partition
    manifest whose coverage overlaps `[window_start, window_end]`.

    Composition is `research/quality/admissibility.py::corpus_ceiling()`
    (itself `composition.meet()`) over each partition's own, already-computed
    verdict. No admissibility computation happens in this function — only
    reading and composing what WBS 4.0 already certified.

    Returns `(ceiling, detail)` where `detail` carries the window actually
    covered and the content-addressed revision, for populating
    `InputAdmissibility.window`/`certification_verdict`.
    """
    from quality.admissibility import corpus_ceiling

    ceilings: list[Admissibility] = []
    lo = hi = None
    for f in glob.glob(str(raw_dir / source / "**" / "_manifest.json"), recursive=True):
        m = json.loads(Path(f).read_text())
        part_lo, part_hi = _iso(m["min_ts"]), _iso(m["max_ts"])
        if part_hi < window_start or part_lo > window_end:
            continue
        adm = m.get("admissibility")
        if not adm:
            continue
        ceilings.append(_partition_admissibility_from_manifest(adm))
        lo = part_lo if lo is None else min(lo, part_lo)
        hi = part_hi if hi is None else max(hi, part_hi)

    if not ceilings:
        raise NoGovernedEvidenceForWindowError(
            f"no certified partition manifest for source {source!r} overlaps "
            f"[{window_start}, {window_end}]"
        )

    ceiling = corpus_ceiling(*ceilings)
    return ceiling, {"window": (lo, hi), "n_partitions": len(ceilings)}


#: A feature's own assessment: full certification, ready, Maturity
#: not-applicable, no integrity flag. A static constant, not a computation —
#: this is the same "own" component `propagation.feature_specification_ceiling()`
#: uses internally (mirrored by value, not imported, since `propagation.py`
#: is neither modified nor called here — `OD-10A`).
_FEATURE_OWN_CEILING = Admissibility(
    provenance=Provenance.OFFLINE_CORPUS,
    certification=Certification.GREEN,
    readiness=Readiness.READY,
    maturity=Maturity.FULL,
    integrity=Integrity.CLEAN,
    not_applicable=frozenset({Dimension.MATURITY}),
)


def _corpus_source_for(declared_source: str, raw_dir: Path) -> str:
    """Maps a Feature Specification's declared `input_requirements` source
    string (e.g. `"dvol_daily"`) to the real top-level corpus directory it
    reads from (e.g. `"dvol"`) by prefix match against the directories that
    actually exist under `raw_dir` — the same mapping
    `bootstrap_first_dataset.py`'s retired `_sources_of()` performed for a
    hard-coded source list, made general instead of hard-coded.
    """
    known = sorted(
        p.name for p in Path(raw_dir).iterdir()
        if p.is_dir() and not p.name.startswith("_")
    )
    for k in known:
        if declared_source.startswith(k):
            return k
    raise DerivationError(
        f"declared source {declared_source!r} matches no known corpus "
        f"directory under {raw_dir!r} ({known})"
    )


def derive_feature_ceiling(
    feature_store_db: Path | str,
    spec_hash: str,
    raw_dir: Path,
    window_start: str,
    window_end: str,
) -> tuple[Admissibility, dict]:
    """The ceiling for one Feature Specification, composed from the real,
    already-certified manifests of every source it declares.

    Composes `_FEATURE_OWN_CEILING` with `derive_evidence_ceiling()`'s real
    per-partition manifest reading for each declared source — the same
    authority evidence-kind inputs already use, not a new one. Distinct
    from, and does not call, `propagation.feature_specification_ceiling()`
    (`OD-10A`): that function's own `source_ceiling()` component always
    returns `UNKNOWN` by design (its own docstring — "no gauntlet report
    read by this module"), which is the correct, deliberately conservative
    answer for a consumer with no direct manifest access. This function
    exists because the Dataset Builder *does* have that access.
    """
    from feature_store import registry as fs

    spec = fs.get_specification(feature_store_db, spec_hash)
    if spec is None:
        raise DerivationError(
            f"feature specification {spec_hash[:12]}... not found in "
            f"{feature_store_db!r} — cannot derive its ceiling"
        )

    declared_sources = sorted({
        str(req.get("source", "")) for req in (spec.get("input_requirements") or [])
    })
    if not declared_sources:
        raise DerivationError(
            f"feature specification {spec_hash[:12]}... declares no input sources"
        )

    ceilings = [_FEATURE_OWN_CEILING]
    lo = hi = None
    n_partitions = 0
    corpus_sources = []
    for declared in declared_sources:
        corpus_source = _corpus_source_for(declared, raw_dir)
        corpus_sources.append(corpus_source)
        c, detail = derive_evidence_ceiling(raw_dir, corpus_source, window_start, window_end)
        ceilings.append(c)
        w = detail["window"]
        lo = w[0] if lo is None else min(lo, w[0])
        hi = w[1] if hi is None else max(hi, w[1])
        n_partitions += detail["n_partitions"]

    ceiling = meet(*ceilings)
    return ceiling, {
        "window": (lo, hi), "n_partitions": n_partitions, "sources": corpus_sources,
    }


def _is_iso_date(s: str) -> bool:
    try:
        date.fromisoformat(s)
        return True
    except (TypeError, ValueError):
        return False


def derive_label_ceiling(
    label_store_db: Path | str,
    spec_hash: str,
    window_start: str,
    window_end: str,
    *,
    maturity_requirement: str,
) -> Admissibility:
    """The ceiling for one Label Specification, over the subjects its
    declared window actually covers.

    Enumerates every declared subject via
    `set_hygiene.list_subject_ids()` (regardless of maturity state — the
    window filter must not silently see only the already-matured ones),
    keeps subjects whose `subject_id` is an ISO date within
    `[window_start, window_end]` (labels are declared per calendar day;
    a non-date-shaped subject_id, e.g. an unrelated trial identifier, is not
    a member of any date window and is excluded, not guessed about), then
    calls the platform's own `request_fully_matured()` scoped to exactly
    that subject set — the existing RF5-shape refusal machinery, not a new
    one — before composing `label_value_ceiling()` per subject with
    `meet()`.

    Raises `NoGovernedLabelDataForWindowError` if zero declared subjects fall
    in the window. Raises `set_hygiene.PartialMaturityRefusalError` if the
    window's subjects exist but the declaration's maturity requirement is
    not met by all of them (mirroring RF5 exactly).

    Aggregation is `set_hygiene.set_admissibility_ceiling()` (WBS 3.9),
    unmodified, scoped to the window's subject_ids — not a hand-rolled
    per-subject `meet()` loop. That function already composes
    `label_value_ceiling()` per row and correctly activates
    `Maturity.PARTIAL` for a partially-matured set; duplicating it here
    would be exactly the second implementation `TD-4.4-3` exists to remove.
    """
    from label_store import set_hygiene as sh

    all_subjects = sh.list_subject_ids(label_store_db, spec_hash)
    windowed = [
        s for s in all_subjects
        if _is_iso_date(s["subject_id"]) and window_start <= s["subject_id"] <= window_end
    ]
    if not windowed:
        raise NoGovernedLabelDataForWindowError(
            f"label specification {spec_hash[:12]}... has {len(all_subjects)} "
            f"declared subject(s), none of which is a date within "
            f"[{window_start}, {window_end}] — no governed label value exists "
            f"for this window; nothing can be honestly derived"
        )

    window_subject_ids = [s["subject_id"] for s in windowed]
    admit_partial = maturity_requirement not in {"matured", "mature", "complete"}
    # Refusal gate only -- mirrors RF5 (ADR-I011 §16.3) exactly, scoped to
    # this window's subjects. Raises set_hygiene.PartialMaturityRefusalError
    # on failure; the return value is not used, aggregation happens below.
    sh.request_fully_matured(
        label_store_db, spec_hash, subject_ids=window_subject_ids,
        admit_partial=admit_partial,
    )

    return sh.set_admissibility_ceiling(
        label_store_db, spec_hash, subject_ids=window_subject_ids,
    )


def _readiness_resolved(ceiling: Admissibility) -> bool:
    """Whether this ceiling's readiness dimension was actually determined.

    `InputAdmissibility.readiness_resolved` and `ceiling.readiness` (the
    lattice value) are two different questions — TC1's phrasing in
    `ADR-I012` §12 ("certification verdict present"; "lane recorded";
    "readiness resolved") lists three *presence* checks, parallel in shape:
    was something recorded at all, not what it says. `Readiness.UNKNOWN` is
    the lattice's own, explicit "not determined" rung (`quality/admissibility.py::
    readiness_from_gauntlet()` never assigns it to a real certified
    partition — a real gauntlet report always reaches NOT_READY, PARTIAL, or
    READY). A *determined-but-bad* readiness (NOT_READY/PARTIAL) is real,
    governed data; it degrades the composed meet and is caught by RF4
    (§12: "declared floor ≤ computed meet") — the ADR's own designated
    mechanism for "was this good enough". TC3's job is narrower: catching an
    input for which readiness was never determined at all.

    Concretely: evidence/feature ceilings, built from real WBS 4.0 manifest
    certifications, are essentially always resolved (`readiness_from_gauntlet()`
    never emits UNKNOWN). Label ceilings, built through
    `set_hygiene.set_admissibility_ceiling()` -> `propagation.label_value_ceiling()`
    -> `propagation.source_ceiling()`, inherit `source_ceiling()`'s permanent
    `Readiness.UNKNOWN` (`propagation.py` is not modified, per `OD-10A`/`OD-10B`) --
    so a successfully-derived label input will, today, always report
    `readiness_resolved=False` and TC3 will fire for it. This is not a
    defect in this function; it is the direct, correct consequence of
    reusing `propagation.py`'s existing, conservative model for labels, as
    ratified. Recorded as WBS 4.12 debt, not routed around here.
    """
    return ceiling.readiness != Readiness.UNKNOWN


def derive_inputs(
    declaration: dict,
    *,
    feature_store_db: Path | str,
    label_store_db: Path | str,
    raw_dir: Path,
) -> list:
    """Derives every `InputAdmissibility` a declaration's references need,
    calling the per-kind derivations above. This is the WBS 4.12 replacement
    for a caller hand-constructing `inputs` (`bootstrap_first_dataset.py`'s
    retired `build_inputs()`).

    `readiness_resolved` is populated from the actual derived ceiling via
    `_readiness_resolved()` for every kind — never left at
    `InputAdmissibility`'s `True` dataclass default, which would silently
    misreport an undetermined readiness as resolved.

    Raises `NoGovernedEvidenceForWindowError`,
    `NoGovernedLabelDataForWindowError`, or
    `set_hygiene.PartialMaturityRefusalError` on the first input that cannot
    be honestly derived — never partial, never a proxy.
    `validation.py::validate_with_derivation()` is what converts a label
    failure into a persisted `TC4` rejection; this function only derives.
    """
    from dataset_builder.validation import InputAdmissibility

    scope = declaration.get("temporal_scope") or {}
    window_start = str(scope.get("window_start") or "")
    window_end = str(scope.get("window_end") or "")
    if not window_start or not window_end:
        raise DerivationError("declaration has no temporal_scope window to derive against")

    ev = declaration.get("evidence_references") or {}
    required_lane = str(ev.get("required_lane") or "")

    inputs: list = []

    for source in ev.get("sources") or []:
        ceiling, detail = derive_evidence_ceiling(raw_dir, str(source), window_start, window_end)
        inputs.append(InputAdmissibility(
            kind="evidence", reference=str(source), ceiling=ceiling,
            certification_verdict=ceiling.certification.name,
            lane=required_lane, window=detail["window"],
            readiness_resolved=_readiness_resolved(ceiling),
        ))

    for ref in declaration.get("feature_references") or []:
        ceiling, detail = derive_feature_ceiling(
            feature_store_db, str(ref), raw_dir, window_start, window_end,
        )
        inputs.append(InputAdmissibility(
            kind="feature", reference=str(ref), ceiling=ceiling,
            lane=required_lane, window=detail["window"],
            readiness_resolved=_readiness_resolved(ceiling),
        ))

    for ref in declaration.get("label_references") or []:
        if not isinstance(ref, dict):
            continue
        spec_hash = str(ref.get("spec_hash") or "")
        maturity_requirement = str(ref.get("maturity_requirement") or "")
        ceiling = derive_label_ceiling(
            label_store_db, spec_hash, window_start, window_end,
            maturity_requirement=maturity_requirement,
        )
        inputs.append(InputAdmissibility(
            kind="label", reference=spec_hash, ceiling=ceiling,
            lane=required_lane, window=(window_start, window_end),
            readiness_resolved=_readiness_resolved(ceiling),
        ))

    return inputs
