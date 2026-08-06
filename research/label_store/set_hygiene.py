"""
Set Hygiene (ADR-I011 §12 Tier D, §16.3, DG2, PS3; Phase 3 Label Store
roadmap v1.1.0, WBS 3.9 -- the tenth, Phase-3-completing Label Store
milestone).

§12 Tier D: "Maturity fraction; horizon coverage; outcome-distribution
disclosure including class imbalance; standing composition. These cap
admissibility rather than blocking." §16.3: "A label set reports its
maturity fraction per horizon. A consumer declaring 'fully matured
30-day' is refused when only shorter horizons have matured. A consumer
explicitly admitting partial maturity receives it with the fraction
recorded (§12 Tier D). Silent partial maturity is impossible -- resolving
PS3." DG2: "Make maturity a first-class, queryable, refusable property."

FOUR TIER D STATISTICS, ALL COMPUTE-ON-DEMAND, ZERO SCHEMA CHANGE --
mirroring WBS 3.7's own precedent exactly ("computed and recorded" means
returned by a real function exercised against real data, not persisted
to a new table; neither store gained a schema change at 3.7 and neither
does here):
  - maturity_fraction()      -- how much of a Specification's declared
                                 population has reached or passed
                                 maturity (Matured/Flagged/Adjudicated).
  - horizon_coverage()       -- across a Definition's registered horizon
                                 Specifications (§16.1: horizon IS
                                 identity), which ones actually have any
                                 declared population.
  - outcome_distribution()   -- basic distributional stats plus a
                                 sign-based class-imbalance disclosure
                                 over the matured population's outcome
                                 values.
  - standing_composition()   -- the Context / Forward evidence /
                                 Promotion evidence breakdown of a
                                 Specification's declared population.

PARTIAL-MATURITY REFUSAL (§16.3), request_fully_matured(): refuses BY
NAME -- the roadmap's own exit criterion -- naming spec_hash, the
observed fraction, and the specific not-yet-matured subject_ids, unless
the caller explicitly passes admit_partial=True, in which case the
matured-or-later subset is returned WITH the fraction recorded alongside
it. Never silent (Ph6, resolving PS3 exactly as §16.3 states).

NO `label_policy.yaml` IS INTRODUCED. The roadmap's own §10 rollout-config
table lists a `maturity_refusal: warn` flag for this milestone, mirroring
the `admissibility_enforcement`/`superseded_input_flagging`/`standing_
enforcement` flags listed for WBS 3.5/3.6/3.7 -- none of which were ever
actually built as a YAML-loaded toggle in this repository (verified: no
`label_policy.yaml` exists anywhere in `research/label_store/`, and 3.5's
`standing.py` and 3.7's `propagation.py` both satisfy their own
"non-blocking" requirement structurally, by simply never wiring a gate,
not via a config flag). This module follows that SAME established, real
precedent: `request_fully_matured()`'s `admit_partial` parameter (default
`False`, meaning refuse) is the "warn-first" discipline made concrete at
the call site, exactly as `§16.3`'s own text already specifies it --
introducing a new YAML-driven config layer here, unprecedented anywhere
else in this Label Store's actual code, would be the "architectural
redesign" this milestone is instructed to avoid.

TIER D FEEDS WBS 3.7's MEET -- ONE CONCRETE, ADR-DIAGRAMMED CONSEQUENCE.
`set_admissibility_ceiling()` is the one Tier D statistic with a direct
lattice-dimension mapping (the ADR's own Mermaid note: "FT -.->|maturity
fraction<br/>caps admissibility| SEAL"). `research/admissibility/
lattice.py`'s `Maturity` chain has carried an unused `PARTIAL` rung since
Phase 0 (`UNKNOWN, DECLARED, PARTIAL, FULL`) -- WBS 3.7's own
`propagation.py` never produced it (a single Label Value's maturity_state
is a discrete state, never a fraction), so it sat waiting for exactly
this milestone. A naive `composition.meet()` across a partially-matured
set's per-value ceilings would collapse ANY set with even one
Declared/Pending row to the same DECLARED floor as a 1%-matured set --
discarding precisely the continuous signal Tier D exists to disclose.
`set_admissibility_ceiling()` therefore meets the OTHER four dimensions
normally (weakest-link is correct there: one Flagged row correctly drags
the whole set's Integrity down) and REPLACES the resulting Maturity with
the fraction-derived rung. The other three statistics (horizon coverage,
outcome distribution, standing composition) have no analogous lattice
dimension in `ADR-I014`'s five -- they are DISCLOSURES, consumed by a
human or by Phase 4's Dataset Builder, exactly as `§12`'s own "cap
admissibility rather than blocking" reads for a report, not a gate.

ZERO CHANGES TO ANY EXISTING FILE. `registry.py`, `value_store.py`,
`standing.py`, `superseded.py`, `propagation.py`, `label_receipts.py`,
`lattice.py`, `composition.py` are all called through their existing,
unmodified public functions (or, for `label_value_ceiling()`'s per-value
composition, `propagation.py`'s own public function) exactly as built.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any

_THIS_DIR = Path(__file__).resolve().parent
_ADMISSIBILITY_DIR = _THIS_DIR.parent / "admissibility"

UNASSIGNED_STANDING = "(unassigned)"

# Matured, Flagged, and Adjudicated are all POST-maturation states --
# Flagged/Adjudicated are reachable only from Matured, per value_store.py's
# own five-arc state machine (WBS 3.2). Declared/Pending have not yet
# matured; Void never will (it is a permanent non-maturation record, LG4).
_MATURED_OR_LATER_STATES = frozenset({"Matured", "Flagged", "Adjudicated"})


def _load_sibling(module_file_name: str, registered_name: str):
    """Collision-safe sibling loader -- see migrate_h6_forward_labels.py's
    identical helper and its docstring."""
    if registered_name in sys.modules:
        return sys.modules[registered_name]
    spec = importlib.util.spec_from_file_location(registered_name, _THIS_DIR / module_file_name)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[registered_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_registry():
    return _load_sibling("registry.py", "label_store_registry")


def _load_propagation():
    return _load_sibling("propagation.py", "label_store_propagation")


def _import_lattice_and_composition():
    if str(_ADMISSIBILITY_DIR) not in sys.path:
        sys.path.insert(0, str(_ADMISSIBILITY_DIR))
    import lattice  # noqa: PLC0415
    import composition  # noqa: PLC0415
    return lattice, composition


class PartialMaturityRefusalError(ValueError):
    """
    ADR-I011 §16.3: raised by request_fully_matured() when a full-maturity
    request is made against a partially-matured (or empty) set and the
    caller has not explicitly admitted partial maturity. Names the
    spec_hash, the observed fraction, and the specific not-yet-matured
    subject_ids -- "refused by name", never a generic refusal.
    """


def _all_forward_rows(db_path, spec_hash: str, subject_ids: list[str] | None = None) -> list[dict]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        if subject_ids is None:
            rows = con.execute(
                "SELECT * FROM label_value_forward WHERE spec_hash = ?", (spec_hash,)
            ).fetchall()
        else:
            placeholders = ",".join("?" for _ in subject_ids)
            rows = con.execute(
                f"SELECT * FROM label_value_forward WHERE spec_hash = ? AND subject_id IN ({placeholders})",
                (spec_hash, *subject_ids),
            ).fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Tier D statistic 1: maturity fraction
# ---------------------------------------------------------------------------

def maturity_fraction(db_path, spec_hash: str, subject_ids: list[str] | None = None) -> dict:
    """
    The fraction of a label set that has reached or passed maturity.
    "Observable via a query" (the roadmap's own exit criterion) -- usable
    standalone, before ever calling request_fully_matured().

    fraction is None (never 0.0 or 1.0) when total == 0 -- absence is not
    permission (Ph6); a set with no declared population makes no claim
    about maturity at all.
    """
    rows = _all_forward_rows(db_path, spec_hash, subject_ids)
    total = len(rows)
    by_state = Counter(r["maturity_state"] for r in rows)
    matured_or_later = sum(by_state[s] for s in _MATURED_OR_LATER_STATES)
    fraction = (matured_or_later / total) if total else None
    return {
        "spec_hash": spec_hash,
        "total": total,
        "matured_or_later": matured_or_later,
        "fraction": fraction,
        "by_state": dict(by_state),
    }


# ---------------------------------------------------------------------------
# Tier D statistic 2: horizon coverage
# ---------------------------------------------------------------------------

def horizon_coverage(db_path, definition_name: str) -> dict:
    """
    ADR-I011 §16.1: horizon is identity -- multiple Specifications under
    one Definition, each its own horizon. Coverage asks whether every
    REGISTERED horizon Specification actually has any declared
    population, surfacing exactly the visible cost §16.1 names:
    "Evaluating four horizons is visibly four Specifications."
    """
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        specs = con.execute(
            "SELECT spec_hash, horizon_days FROM label_specifications WHERE definition_name = ?",
            (definition_name,),
        ).fetchall()
        counts = {
            s["spec_hash"]: con.execute(
                "SELECT COUNT(*) FROM label_value_forward WHERE spec_hash = ?", (s["spec_hash"],),
            ).fetchone()[0]
            for s in specs
        }
    finally:
        con.close()

    per_horizon = sorted(
        (
            {
                "spec_hash": s["spec_hash"], "horizon_days": s["horizon_days"],
                "declared_count": counts[s["spec_hash"]], "populated": counts[s["spec_hash"]] > 0,
            }
            for s in specs
        ),
        key=lambda h: h["horizon_days"],
    )
    total_horizons = len(per_horizon)
    populated_horizons = sum(1 for h in per_horizon if h["populated"])
    return {
        "definition_name": definition_name,
        "total_horizons": total_horizons,
        "populated_horizons": populated_horizons,
        "coverage_fraction": (populated_horizons / total_horizons) if total_horizons else None,
        "per_horizon": per_horizon,
    }


# ---------------------------------------------------------------------------
# Tier D statistic 3: outcome-distribution disclosure, including class
# imbalance
# ---------------------------------------------------------------------------

def _extract_numeric_field(outcome: Any, field: str) -> float | None:
    if not isinstance(outcome, dict):
        return None
    value = outcome.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _detect_outcome_field(outcomes: list[dict]) -> str | None:
    """The first key, alphabetically, present and numeric in EVERY given
    outcome dict -- deterministic, never a silent guess among several
    equally-valid candidates. Returns None if no such field exists (e.g.
    zero matured rows, or no field common to all of them)."""
    if not outcomes:
        return None
    candidate_keys: set[str] | None = None
    for o in outcomes:
        if not isinstance(o, dict):
            return None
        keys = {k for k, v in o.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
        candidate_keys = keys if candidate_keys is None else (candidate_keys & keys)
    if not candidate_keys:
        return None
    return sorted(candidate_keys)[0]


def outcome_distribution(db_path, spec_hash: str, outcome_field: str | None = None) -> dict:
    """
    ADR-I011 §12 Tier D: "outcome-distribution disclosure including class
    imbalance." Computed over the matured-or-later population only
    (outcome_json is NULL for Declared/Pending/Void rows, per WBS 3.2's
    own fill-once discipline). Class imbalance is disclosed via SIGN
    (positive/negative/zero) of the numeric outcome field -- the natural,
    non-invented binary partition of a continuous forward-return outcome;
    this module assumes no specific field name beyond what
    outcome_field/auto-detection finds in the Specification's own real
    outcome_json shape (Tier A does not mandate one, by design -- see
    registry.py's own docstring on what it deliberately does not carry
    over from the Feature Store pattern).
    """
    rows = _all_forward_rows(db_path, spec_hash)
    matured_rows = [
        r for r in rows
        if r["maturity_state"] in _MATURED_OR_LATER_STATES and r["outcome_json"] is not None
    ]
    outcomes = [json.loads(r["outcome_json"]) for r in matured_rows]

    field = outcome_field or _detect_outcome_field(outcomes)
    if field is None:
        return {
            "spec_hash": spec_hash, "n": len(outcomes), "outcome_field": None,
            "disclosed": False, "reason": "no common numeric outcome field found",
        }

    values = [v for v in (_extract_numeric_field(o, field) for o in outcomes) if v is not None]
    n = len(values)
    if n == 0:
        return {
            "spec_hash": spec_hash, "n": 0, "outcome_field": field,
            "disclosed": False, "reason": f"field {field!r} present in no matured outcome",
        }

    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / n
    std = variance ** 0.5
    positive = sum(1 for v in values if v > 0)
    negative = sum(1 for v in values if v < 0)
    zero = n - positive - negative
    return {
        "spec_hash": spec_hash, "n": n, "outcome_field": field, "disclosed": True,
        "mean": mean, "std": std, "min": min(values), "max": max(values),
        "class_imbalance": {
            "positive": positive, "negative": negative, "zero": zero,
            "positive_fraction": positive / n, "negative_fraction": negative / n, "zero_fraction": zero / n,
        },
    }


# ---------------------------------------------------------------------------
# Tier D statistic 4: standing composition
# ---------------------------------------------------------------------------

def standing_composition(db_path, spec_hash: str) -> dict:
    """ADR-I011 §12 Tier D: "standing composition." §10.1's three real
    standing values, plus an explicit unassigned bucket -- WBS 3.5 made
    standing optional at declaration; NULL means never assigned, never
    conflated with one of the three real values."""
    rows = _all_forward_rows(db_path, spec_hash)
    total = len(rows)
    counts = Counter(r["standing"] if r["standing"] is not None else UNASSIGNED_STANDING for r in rows)
    return {
        "spec_hash": spec_hash,
        "total": total,
        "by_standing": dict(counts),
        "fractions": ({k: v / total for k, v in counts.items()} if total else {}),
    }


# ---------------------------------------------------------------------------
# Aggregate report -- all four statistics for one Specification
# ---------------------------------------------------------------------------

def tier_d_report(db_path, spec_hash: str, definition_name: str | None = None) -> dict:
    """All four §12 Tier D statistics for spec_hash's declared population,
    in one call. horizon_coverage() is computed over spec_hash's own
    definition_name unless a caller explicitly overrides it (e.g. to
    compare against a DIFFERENT Definition's horizon set)."""
    reg = _load_registry()
    spec = reg.get_specification(db_path, spec_hash)
    if spec is None:
        raise LookupError(f"spec_hash {spec_hash[:12]}... is not a registered Specification")
    def_name = definition_name or spec["definition_name"]
    return {
        "spec_hash": spec_hash,
        "maturity_fraction": maturity_fraction(db_path, spec_hash),
        "horizon_coverage": horizon_coverage(db_path, def_name),
        "outcome_distribution": outcome_distribution(db_path, spec_hash),
        "standing_composition": standing_composition(db_path, spec_hash),
    }


# ---------------------------------------------------------------------------
# §16.3 partial-maturity refusal
# ---------------------------------------------------------------------------

def request_fully_matured(
    db_path, spec_hash: str, subject_ids: list[str] | None = None, *, admit_partial: bool = False,
) -> dict:
    """
    ADR-I011 §16.3: "A consumer declaring 'fully matured 30-day' is
    refused when only shorter horizons have matured. A consumer
    explicitly admitting partial maturity receives it with the fraction
    recorded."

    Refuses BY NAME (spec_hash, the observed fraction, and the specific
    not-yet-matured subject_ids) unless admit_partial=True, in which case
    the matured-or-later subset is returned WITH the fraction recorded
    alongside it -- never silently (Ph6).
    """
    rows = _all_forward_rows(db_path, spec_hash, subject_ids)
    matured_rows = [r for r in rows if r["maturity_state"] in _MATURED_OR_LATER_STATES]
    not_yet_matured = sorted(r["subject_id"] for r in rows if r["maturity_state"] not in _MATURED_OR_LATER_STATES)
    stats = maturity_fraction(db_path, spec_hash, subject_ids)

    if stats["total"] == 0:
        raise PartialMaturityRefusalError(
            f"spec_hash {spec_hash[:12]}... has no declared population "
            f"(subject_ids={subject_ids!r}) -- nothing to serve"
        )

    if stats["fraction"] < 1.0 and not admit_partial:
        raise PartialMaturityRefusalError(
            f"spec_hash {spec_hash[:12]}...: full maturity requested but only "
            f"{stats['matured_or_later']}/{stats['total']} ({stats['fraction']:.1%}) have matured. "
            f"Not yet matured: {not_yet_matured}. Call with admit_partial=True to receive the "
            f"partial set (ADR-I011 §16.3)."
        )

    return {
        "spec_hash": spec_hash,
        "rows": matured_rows,
        "maturity_fraction": stats["fraction"],
        "total": stats["total"],
        "matured_or_later": stats["matured_or_later"],
        "admitted_partial": stats["fraction"] < 1.0,
    }


# ---------------------------------------------------------------------------
# Set-level admissibility ceiling -- feeds WBS 3.7's meet
# ---------------------------------------------------------------------------

def _set_maturity_rung(fraction: float | None, total: int):
    lattice, _ = _import_lattice_and_composition()
    if total == 0:
        return lattice.Maturity.UNKNOWN
    if fraction == 1.0:
        return lattice.Maturity.FULL
    if fraction is not None and fraction > 0.0:
        return lattice.Maturity.PARTIAL
    return lattice.Maturity.DECLARED


def set_admissibility_ceiling(db_path, spec_hash: str, subject_ids: list[str] | None = None):
    """
    The set-level admissibility ceiling for spec_hash's declared
    population (or a caller-named subset), activating `lattice.py`'s
    previously-unused `Maturity.PARTIAL` rung -- see this module's own
    docstring for why a naive per-value meet() cannot express this.

    Reuses propagation.py's existing label_value_ceiling() per row and
    composition.meet()s them for Provenance/Certification/Readiness/
    Integrity (weakest-link is correct there); Maturity is replaced with
    the fraction-derived rung. Returns lattice.bottom() for an empty set
    -- absence is not permission (Ph6).
    """
    lattice, composition = _import_lattice_and_composition()
    prop = _load_propagation()
    rows = _all_forward_rows(db_path, spec_hash, subject_ids)
    if not rows:
        return lattice.bottom()

    per_value_ceilings = [
        prop.label_value_ceiling(db_path, spec_hash, r["subject_id"], r["declared_at"])
        for r in rows
    ]
    baseline = composition.meet(*per_value_ceilings)

    stats = maturity_fraction(db_path, spec_hash, subject_ids)
    set_maturity = _set_maturity_rung(stats["fraction"], stats["total"])
    return replace(
        baseline,
        maturity=set_maturity,
        not_applicable=frozenset(baseline.not_applicable - {lattice.Dimension.MATURITY}),
    )
