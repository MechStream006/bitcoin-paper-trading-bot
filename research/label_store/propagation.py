"""
Conditioning Propagation + Store-Agnostic Admissibility Inheritance
(ADR-I011 §10.2 [the roadmap's own ownership table cites this as both
"§9.2" and "§10.2" -- the same provision under the ADR's own internal
subsection-numbering quirk, already documented at WBS 3.2/3.5's own
verification records: §9's subsections are themselves labelled 10.1/10.2],
DG6; roadmap S-2 amendment: "build store-agnostically and apply to BOTH
stores"; Phase 3 Label Store roadmap v1.1.0, WBS 3.7 -- the seventh Label
Store milestone).

Closes `FS-GAP-001` (admissibility lattice built at Phase 0, unused) in
BOTH stores at once, per the roadmap's own S-2 amendment: a label-only
implementation would leave Phase 4's DI5 meet (across evidence, features,
and labels) with a permanently missing leg, built later by a different
milestone against a different integration point.

`conditioning.py` IS IMPORTED UNCHANGED (verified by diff -- see this
milestone's verification record). This module calls its PUBLIC
`conditioning_for_specification()`/`propagate_conditioning()` functions
only; it never reaches into `_source_conditioning_map()` or any other
underscore-prefixed internal. `research/label_store/registry.py` and
`value_store.py`, and `research/feature_store/registry.py` and
`value_store.py`, are ALL untouched -- every function this module calls
on them (`get_specification`, `get_forward_label`) is used exactly as
already built (WBS 3.1-3.6, WBS 2.1-2.7), per this milestone's own
instruction to "use existing Label Store lifecycle wherever possible."

PURELY COMPUTE-ON-DEMAND -- NO NEW STORAGE, NO SCHEMA CHANGE. Neither
Value Store has an admissibility column anywhere in its real schema.
Adding one would be exactly the "architectural redesign" this milestone
is instructed to avoid. This module mirrors
`research/quality/admissibility.py`'s own precedent exactly: that module
computes a ceiling and hands it to whichever caller already had a place
to put it (Z3's pre-existing `write_manifest()` `admissibility` field,
not a new column that module invented). Neither Value Store has an
analogous field to write into, so this module simply returns computed
`Admissibility` values -- persistence, if ever wanted, is a future
caller's decision (most plausibly Phase 4's Dataset Builder, `ADR-I011`
§15, where DI5's meet is actually consumed), not this milestone's.

CONDITIONING FOR LABEL SPECIFICATIONS -- AN ADAPTER, NOT A SCHEMA CHANGE.
`ADR-I011` §8.2 does not require an `input_requirements` field on a Label
Specification (WBS 3.1's own `registry.py` docstring already explains
this deliberate divergence from Feature Specifications), so this module
cannot call `conditioning_for_specification()` with a Label Specification's
own stored fields directly. `_LABEL_BINDING_SOURCES` is a small, explicit,
declarative map from each of the Label Store's REAL, currently-registered
bindings to its true input source(s) -- all five derive from Binance spot
close, confirmed this session by re-reading `daily_job.py`/`engine.py`/
`runner_base.py`'s own logic at WBS 3.3/3.4 -- from which this module
builds the `{"source": ..., "fields": [...]}` shape
`conditioning_for_specification()` already expects. Only the real,
currently-registered bindings are populated, mirroring `standing.py`'s
own "only h6- populated" precedent -- a future Specification's binding
needs its own registration here when it exists, not invented ahead of
need.

NO GAUNTLET REPORT EXISTS FOR ANY SOURCE THIS MODULE'S REAL DATA ACTUALLY
DERIVES FROM. `research/quality/admissibility.py`'s own docstring states
it is "wired only into research/ingest/dvol.py so far." Every real Label
Specification this session has registered (WBS 3.3/3.4) and 16 of the 17
real Feature Specifications derive from Binance spot/perp close or
funding, not DVOL -- none of which has ever been run through
`research/quality/gauntlet.py`. Running the gauntlet against real market
data to manufacture a certification verdict would be a live, heavyweight,
out-of-scope side-action ("no unrelated refactoring"). `source_ceiling()`
therefore constructs `CERTIFICATION=UNKNOWN, READINESS=UNKNOWN` DIRECTLY
for every source -- never by calling
`certification_from_gauntlet()`/`readiness_from_gauntlet()` with a
fabricated empty report, which would silently return `GREEN`/`READY`
(verified this session: `certification_from_gauntlet({})` returns
`GREEN`, the OPPOSITE of "never assessed"). This is a low but ACCURATE
ceiling (Ph6: absence caps a dimension, it does not default to a passing
value) -- exactly `§10.2`'s own words: "A label computed on an uncertified
span inherits that cap."

MATURITY AND INTEGRITY -- THE LABEL STORE'S OWN MATURITY LIFECYCLE MAPS
DIRECTLY ONTO THE LATTICE, BY A CORRESPONDENCE THE LATTICE ALREADY HAD.
`research/admissibility/lattice.py`'s own `Integrity` chain docstring
cites the Label Store's superseded-input flagging discipline BY NAME
(under the ADR's own internal numbering, "ADR-I011 §10.3" there =
`ADR-I011` §11.3 here) -- Phase 0 built this dimension anticipating
exactly the Label Store's Flagged/Adjudicated states, before the Label
Store existed. This module is the first to actually exercise that
correspondence:

    Declared / Pending -> Maturity.DECLARED, Integrity.CLEAN
    Matured             -> Maturity.FULL,     Integrity.CLEAN
    Flagged             -> Maturity.FULL,     Integrity.FLAGGED
    Adjudicated         -> Maturity.FULL,     Integrity.ADJUDICATED
                            (never restored to CLEAN -- lattice.py's own
                            documented Choice 2, preserving "a flag is
                            neither silently rewritten nor silently
                            trusted")
    Void                -> Maturity.UNKNOWN,  Integrity.UNKNOWN (nothing
                            was ever established -- the safest,
                            most conservative reading, not a fabricated
                            "clean absence")

FEATURE SPECIFICATIONS: MATURITY IS NOT-APPLICABLE. Mirrors
`quality/admissibility.py`'s own `partition_ceiling()` reasoning exactly:
a Feature is a pure function of the past (`ADR-I010` §1.1), always fully
computed the instant its inputs exist -- it has no maturity lifecycle to
report, so `Maturity` is marked not-applicable (its required top rung,
`FULL`, per `lattice.py`'s own `__post_init__` invariant), never
fabricated as a real value nor left as a misleading `UNKNOWN` deficiency.

ADMISSIBILITY IS RECORDED, NOT ENFORCED (roadmap §10's own rollback
lever for this item: "admissibility_enforcement: false -> computed and
recorded, not gating"). Nothing in this module blocks, refuses, or gates
on a low ceiling -- mirrors `ADR-I012` DA3 ("A dataset seals successfully
at any ceiling. A low ceiling is a label, not a failure."), already the
operative discipline in `quality/admissibility.py`.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_FEATURE_STORE_DIR = _THIS_DIR.parent / "feature_store"
_ADMISSIBILITY_DIR = _THIS_DIR.parent / "admissibility"

BOOTSTRAP_MARKER = "bootstrap"

# Only the Label Store's real, currently-registered bindings (WBS 3.3/3.4)
# -- see module docstring.
_LABEL_BINDING_SOURCES: dict[str, list[str]] = {
    "forward_evidence.daily_job.close_trade": ["spot_1m"],
    "research_evidence.engine.net_daily": ["spot_1m"],
    "forward_evidence.runner_base.trade_returns_view": ["spot_1m"],
    "research_evidence.engine.ret_fwd1": ["spot_1m"],
    "forward_evidence.daily_job.trade_labels": ["spot_1m"],
}


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


def _load_feature_store_registry():
    """Collision-safe loader for research/feature_store/registry.py --
    distinct registered name from the Label Store's own registry.py,
    which shares the bare module name "registry" (the same collision
    class documented and fixed at WBS 3.1/3.4)."""
    registered_name = "feature_store_registry"
    if registered_name in sys.modules:
        return sys.modules[registered_name]
    spec = importlib.util.spec_from_file_location(registered_name, _FEATURE_STORE_DIR / "registry.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[registered_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _import_conditioning():
    """research/feature_store/conditioning.py -- imported unchanged, no
    bare-name collision risk (only one conditioning.py exists in this
    repository)."""
    if str(_FEATURE_STORE_DIR) not in sys.path:
        sys.path.insert(0, str(_FEATURE_STORE_DIR))
    import conditioning  # noqa: PLC0415
    return conditioning


def _import_lattice_and_composition():
    """research/admissibility/{lattice,composition}.py -- no bare-name
    collision risk (only one of each exists)."""
    if str(_ADMISSIBILITY_DIR) not in sys.path:
        sys.path.insert(0, str(_ADMISSIBILITY_DIR))
    import lattice  # noqa: PLC0415
    import composition  # noqa: PLC0415
    return lattice, composition


class UnknownBindingSourceError(ValueError):
    """Raised when a Label Specification's binding_id has no registered
    entry in _LABEL_BINDING_SOURCES -- refuses rather than guessing an
    input source (Ph6)."""


# ---------------------------------------------------------------------------
# Conditioning
# ---------------------------------------------------------------------------

def conditioning_for_label_specification(spec: dict):
    """
    Propagates a Label Specification's declared conditioning rule
    (`spec["conditioning_propagation"]`) using `conditioning.py`'s public
    `conditioning_for_specification()`, via the `_LABEL_BINDING_SOURCES`
    adapter described in the module docstring.
    """
    cond = _import_conditioning()
    sources = _LABEL_BINDING_SOURCES.get(spec["binding_id"])
    if sources is None:
        raise UnknownBindingSourceError(
            f"binding_id {spec['binding_id']!r} has no registered input source(s) -- "
            f"known bindings: {sorted(_LABEL_BINDING_SOURCES)}"
        )
    input_requirements = [{"source": s, "fields": []} for s in sources]
    return cond.conditioning_for_specification(spec["conditioning_propagation"], input_requirements)


def conditioning_for_feature_specification(spec: dict):
    """
    Propagates a real Feature Specification's declared conditioning rule.
    Unlike Label Specifications, Feature Specifications already carry a
    real `input_requirements` list in the exact shape
    `conditioning_for_specification()` expects (WBS 2.1) -- no adapter
    needed.
    """
    cond = _import_conditioning()
    return cond.conditioning_for_specification(spec["conditioning_rule"], spec["input_requirements"])


# ---------------------------------------------------------------------------
# Admissibility ceilings
# ---------------------------------------------------------------------------

_MATURITY_INTEGRITY_BY_STATE: dict[str, tuple[str, str]] = {
    "Declared": ("DECLARED", "CLEAN"),
    "Pending": ("DECLARED", "CLEAN"),
    "Matured": ("FULL", "CLEAN"),
    "Flagged": ("FULL", "FLAGGED"),
    "Adjudicated": ("FULL", "ADJUDICATED"),
    "Void": ("UNKNOWN", "UNKNOWN"),
}


def source_ceiling(source_name: str):
    """
    The honest, uncertified admissibility ceiling for a real ingest source
    with no gauntlet report read by this module (module docstring). Every
    call with a real source name currently in use by this codebase's
    Label or Feature Specifications returns the same shape: no
    certification/readiness verdict exists, so both dimensions are
    UNKNOWN; Maturity is not-applicable (a raw source has no maturity
    concept, mirroring `quality/admissibility.py`'s own
    `partition_ceiling()`); Integrity is CLEAN (no flag has ever been
    raised against source_name itself -- flagging is WBS 3.6's own,
    label-value-scoped mechanism, not a source-level one).
    """
    lattice, _ = _import_lattice_and_composition()
    return lattice.Admissibility(
        provenance=lattice.Provenance.OFFLINE_CORPUS,
        certification=lattice.Certification.UNKNOWN,
        readiness=lattice.Readiness.UNKNOWN,
        maturity=lattice.Maturity.FULL,
        integrity=lattice.Integrity.CLEAN,
        not_applicable=frozenset({lattice.Dimension.MATURITY}),
    )


def label_value_own_ceiling(maturity_state: str, declared_by: str):
    """
    The Label Value's OWN assessment -- Maturity and Integrity are real,
    derived from `maturity_state` (module docstring's correspondence
    table); Provenance reflects how the label itself was declared
    (bootstrap-attested -> OFFLINE_CORPUS, matching the Evidence
    Library's own "offline-corpus" lane convention for historically
    collected, non-live data; anything else -> LIVE_FORWARD, the only
    other provenance a Label Store declaration could plausibly have
    today). Certification and Readiness are set to their TOP rung
    (GREEN/READY) deliberately -- this function has no opinion on data
    quality, only on the label's own bookkeeping, so it must not drag
    those dimensions down; `label_value_ceiling()`'s meet() with the real
    input-source ceiling is what actually determines them.
    """
    lattice, _ = _import_lattice_and_composition()
    if maturity_state not in _MATURITY_INTEGRITY_BY_STATE:
        raise ValueError(f"unrecognised maturity_state {maturity_state!r}")
    maturity_name, integrity_name = _MATURITY_INTEGRITY_BY_STATE[maturity_state]
    provenance = (
        lattice.Provenance.OFFLINE_CORPUS if BOOTSTRAP_MARKER in declared_by
        else lattice.Provenance.LIVE_FORWARD
    )
    return lattice.Admissibility(
        provenance=provenance,
        certification=lattice.Certification.GREEN,
        readiness=lattice.Readiness.READY,
        maturity=getattr(lattice.Maturity, maturity_name),
        integrity=getattr(lattice.Integrity, integrity_name),
    )


def label_value_ceiling(label_store_db, spec_hash: str, subject_id: str, declared_at: str):
    """
    The full admissibility ceiling for one Label Value: the meet
    (IQROS-P4) of its own maturity/integrity assessment and the ceiling
    of every real input source its Specification's binding derives from
    -- "A label computed on an uncertified span inherits that cap"
    (§10.2), realised exactly.

    Uses `research/label_store/registry.py`'s and `value_store.py`'s
    existing, unmodified `get_specification()`/`get_forward_label()` --
    no new storage, no schema change.
    """
    vs = _load_sibling("value_store.py", "label_store_value_store")
    reg = _load_sibling("registry.py", "label_store_registry")
    _, composition = _import_lattice_and_composition()

    label = vs.get_forward_label(label_store_db, spec_hash, subject_id, declared_at)
    if label is None:
        raise LookupError(
            f"no forward label declared at (spec_hash={spec_hash[:12]}..., "
            f"subject_id={subject_id!r}, declared_at={declared_at!r})"
        )
    spec = reg.get_specification(label_store_db, spec_hash)
    if spec is None:
        raise LookupError(f"no specification registered for spec_hash={spec_hash[:12]}...")

    own = label_value_own_ceiling(label["maturity_state"], label["declared_by"])
    sources = _LABEL_BINDING_SOURCES.get(spec["binding_id"])
    if sources is None:
        raise UnknownBindingSourceError(
            f"binding_id {spec['binding_id']!r} has no registered input source(s) -- "
            f"known bindings: {sorted(_LABEL_BINDING_SOURCES)}"
        )
    input_ceiling = composition.meet(*(source_ceiling(s) for s in sources))
    return composition.meet(own, input_ceiling)


def feature_specification_ceiling(spec: dict):
    """
    The full admissibility ceiling for one real Feature Specification:
    the meet of a Feature's own (Maturity-not-applicable) assessment and
    the ceiling of every real input source its `input_requirements`
    name. `spec` is a specification dict as returned by
    `feature_store/registry.py`'s `get_specification()` -- unmodified,
    used as already built.
    """
    lattice, composition = _import_lattice_and_composition()
    own = lattice.Admissibility(
        provenance=lattice.Provenance.OFFLINE_CORPUS,
        certification=lattice.Certification.GREEN,
        readiness=lattice.Readiness.READY,
        maturity=lattice.Maturity.FULL,
        integrity=lattice.Integrity.CLEAN,
        not_applicable=frozenset({lattice.Dimension.MATURITY}),
    )
    sources = [req["source"] for req in spec["input_requirements"]]
    input_ceiling = composition.meet(*(source_ceiling(s) for s in sources))
    return composition.meet(own, input_ceiling)


def all_active_feature_specification_ceilings(feature_store_db):
    """
    Computes the ceiling for every Active Feature Specification in the
    real Feature Store registry -- the concrete demonstration this
    milestone's own exit criterion names: "the same module computes the
    ceiling for all 17 Feature Specifications, closing FS-GAP-001 in both
    stores." Returns {spec_hash: Admissibility}.
    """
    freg = _load_feature_store_registry()
    import sqlite3
    con = sqlite3.connect(feature_store_db)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT spec_hash FROM feature_specifications WHERE phase = 'Active'"
        ).fetchall()
    finally:
        con.close()

    return {
        row["spec_hash"]: feature_specification_ceiling(freg.get_specification(feature_store_db, row["spec_hash"]))
        for row in rows
    }
