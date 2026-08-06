"""
Label Registry (ADR-I011 §6, §7.1, §8.2, §10, §11.1, §11.2, §12.1, §16.1,
§17, LG6, LG8; Phase 3 Label Store roadmap v1.1.0, WBS 3.1 -- the first
Label Store milestone).

The Registry is the authority on WHAT an outcome is and how it is
measured. It holds Label Definitions (semantic statement, versioned,
human-authored) and Label Specifications (executable contract,
content-addressed) -- it NEVER holds values (LG6: "The Registry never
holds values; the Value Store never issues judgments"). No Label Value,
research or forward, is read, written, or referenced anywhere in this
module.

SCOPE OF THIS MILESTONE (WBS 3.1 only). Registry only. No Value Store
(WBS 3.2), no H6 wrapping (WBS 3.4), no real data of any kind. Every
Specification exercised against this module is hand-written, per this
milestone's own exit criterion. This module does not import, wrap, or
depend on `research/feature_store/registry.py` -- per the roadmap's I8
("deliberate non-integration"), the two registries are mirrored, not
shared, because a Label Specification carries conventions and a maturity
rule that have no feature analogue.

TEN TIER A FIELDS, NOT NINE. ADR-I011 §8.2 lists nine mandatory field
classes (Identity, Horizon, Fill convention, Cost convention, Direction
convention, Maturity rule, Validity condition, Conditioning propagation,
Binding). The roadmap's audit (LR7, promoted from a risk note while free)
adds a tenth: `horizon_governance`, resolving §17's "calendar trap" --
a Specification must state whether observation count or calendar span
governs maturity, because the two diverge for non-continuous markets.
Retrofitting this after Specifications exist would mean a new hash for
every one, so it is captured now rather than deferred.

WHAT THIS MODULE DELIBERATELY DOES NOT CARRY OVER FROM THE FEATURE STORE
PATTERN. `research/feature_store/registry.py` has `input_requirements`,
`warmup_periods`, and `sensitivity_variants` fields -- none of those
appear in ADR-I011 §8.2's mandatory-field list for a Label Specification.
Adding them here would invent Specification content the governing ADR
does not require. `parameters` is likewise omitted: §16.1 makes horizon
identity in its own right, and no other free parameter is named by §8.2.

"TIER B" NAMING COLLISION -- READ BEFORE EXTENDING THIS MODULE. ADR-I010's
own §13 Tier B is binding equivalence, demonstrated by
`activate_specification()`'s `equivalence_check` callable below, mirrored
unchanged from the Feature Store pattern for the same reason: §12.1 says
the Specification lifecycle is "Identical to ADR-I010 §11," and that
lifecycle's Registered -> Active arc requires demonstrating the one
declared binding resolves and behaves as intended. ADR-I011's OWN Tier B
(§12: "Declaration integrity" -- a placeholder written before its horizon
opens, standing assigned at declaration) is a DIFFERENT, value-level
concept that does not exist yet at this milestone's scope -- it is owned
by WBS 3.4 (H6 wrap) and WBS 3.5 (standing), per the roadmap's §14.1
ownership table. `activate_specification()` below is therefore NOT an
implementation of ADR-I011 §12 Tier B; it is the mechanical Specification
phase transition, exactly as `ADR-I010 §11` and `ADR-I011 §12.1` both
require, and is named without the term "Tier B" to avoid the collision.

CONDITIONING PROPAGATION VOCABULARY. `CONDITIONING_RULES` below is a
verbatim duplicate of `research/feature_store/registry.py`'s constant of
the same name, not an import -- ADR-I011 §9.1/PS7 says conditioning
propagates "by the Specification's declared rule," reusing `ADR-I010`'s
vocabulary without redefining it, and I8 forbids sharing registry code
between the two stores. The real propagation mechanism
(`feature_store/conditioning.py`, imported unchanged) is wired at WBS 3.7,
not here; this milestone only validates that a Specification names one of
the three known rules.

BINDING RESOLUTION mirrors `research/feature_store/registry.py`'s
`register_binding()`/`get_binding()` exactly: a Specification records only
a `(binding_id, engine_version)` identity; the callable is registered
in-process and resolved by exact pair, raising `UnknownBindingError` --
never substituting a different implementation -- if unresolved.

LG8 (horizons declared at registration, never selected at evaluation) is
satisfied structurally: `horizon_days` is part of the content-addressed
Specification, and no function in this module accepts a horizon at
resolution time.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

CONDITIONING_RULES = frozenset({"preserving", "narrowing", "cross-sectional"})

HORIZON_GOVERNANCE_VALUES = frozenset({"observation_count", "calendar_span"})

PHASES = frozenset({
    "Registered", "Active", "Deprecated", "Superseded", "Retired", "Rejected",
})

# ADR-I011 §12.1 ("Identical to ADR-I010 §11"), transcribed verbatim as an
# allowed-arc table. "Proposed" is not a persisted phase in this
# implementation, mirroring the Feature Store's own scoped simplification:
# nothing is written until Tier A either passes (phase="Registered") or
# fails (phase="Rejected", still a permanent record per LG4/IQROS-P15).
_ALLOWED_TRANSITIONS: frozenset[tuple[str, str]] = frozenset({
    ("Registered", "Active"),      # binding equivalence demonstrated
    ("Registered", "Rejected"),    # binding equivalence failed
    ("Active", "Deprecated"),
    ("Active", "Superseded"),
    ("Deprecated", "Retired"),
    ("Superseded", "Retired"),
})

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS label_definitions (
    name            TEXT NOT NULL,
    version         INTEGER NOT NULL,
    definition_hash TEXT NOT NULL UNIQUE,
    rationale       TEXT NOT NULL,
    interpretation  TEXT NOT NULL,
    registered_by   TEXT NOT NULL,
    registered_at   TEXT NOT NULL,
    PRIMARY KEY (name, version)
);

CREATE TRIGGER IF NOT EXISTS trg_label_definitions_no_update
BEFORE UPDATE ON label_definitions
BEGIN
    SELECT RAISE(ABORT, 'label_definitions is append-only: UPDATE forbidden');
END;

CREATE TRIGGER IF NOT EXISTS trg_label_definitions_no_delete
BEFORE DELETE ON label_definitions
BEGIN
    SELECT RAISE(ABORT, 'label_definitions is append-only: DELETE forbidden');
END;

CREATE TABLE IF NOT EXISTS label_specifications (
    spec_hash               TEXT PRIMARY KEY,
    definition_name         TEXT NOT NULL,
    definition_version      INTEGER NOT NULL,
    horizon_days            INTEGER NOT NULL,
    horizon_governance      TEXT NOT NULL,
    fill_convention         TEXT NOT NULL,
    cost_convention         TEXT NOT NULL,
    direction_convention    TEXT NOT NULL,
    maturity_rule           TEXT NOT NULL,
    validity_condition      TEXT NOT NULL,
    conditioning_propagation TEXT NOT NULL,
    binding_id              TEXT NOT NULL,
    engine_version           TEXT NOT NULL,
    phase                    TEXT NOT NULL,
    registered_by            TEXT NOT NULL,
    registered_at            TEXT NOT NULL,
    rejection_reason        TEXT,
    FOREIGN KEY (definition_name, definition_version)
        REFERENCES label_definitions(name, version)
);
"""


class LabelRegistryError(ValueError):
    """Base class for registry construction/validation failures."""


class UnknownDefinitionError(LabelRegistryError):
    """Raised when a Specification references a Definition version that
    does not exist (ADR-I011 §6.3-equivalent lookup)."""


class SpecificationRejected(LabelRegistryError):
    """
    Raised by register_specification() (Tier A failure) or
    activate_specification() (binding-equivalence failure). The rejection
    is ALREADY persisted as a permanent record (LG4, IQROS-P15) before
    this is raised -- callers catching this are recovering from a
    recorded fact, not preventing one.
    """

    def __init__(self, spec_hash: str, errors: list[str]):
        self.spec_hash = spec_hash
        self.errors = errors
        super().__init__(f"specification {spec_hash[:12]}... rejected: {'; '.join(errors)}")


class UnknownBindingError(LabelRegistryError):
    """
    Raised by get_binding() when no callable is registered for the exact
    (binding_id, engine_version) pair. Never falls back to "whatever the
    binding_id currently resolves to."
    """


class TransitionError(LabelRegistryError):
    """Raised when a phase transition is not in _ALLOWED_TRANSITIONS, or
    when the specification's current phase does not match what the caller
    expected (concurrent-modification guard)."""


def init_db(path: Path | str) -> None:
    """Idempotent CREATE-only schema-ensure. Deliberately never performs an
    ALTER -- a column added later belongs in a dedicated, explicitly
    invoked migration script, never behind a call reachable from a read
    path (mirrors research/evidence_library/receipt_ledger.py's
    init_db())."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    try:
        con.executescript(_SCHEMA_SQL)
        con.commit()
    finally:
        con.close()


def _canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _content_hash(obj: Any) -> str:
    return hashlib.sha256(_canonical_json(obj)).hexdigest()


# ---------------------------------------------------------------------------
# Label Definitions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LabelDefinitionHandle:
    name: str
    version: int


def register_definition(
    db_path: Path | str,
    name: str,
    rationale: str,
    interpretation: str,
    actor: str,
    registered_at: str,
) -> LabelDefinitionHandle:
    """
    Register a new Label Definition version. Always creates the NEXT
    ordinal version for `name` -- a Definition is versioned and immutable
    per version (ADR-I011 §10, "Definition version | Ordinal within a
    lineage | The semantic statement changes"); there is no update path.

    `rationale` and `interpretation` correspond to §8.1's Label Definition
    description: "what outcome is measured, why it is the right outcome
    for the claim being tested, and its interpretation." No horizon, no
    conventions, no binding belong here (§8.1).

    Every field is required and refused if blank (Ph6).
    """
    for field_name, value in (
        ("name", name), ("rationale", rationale),
        ("interpretation", interpretation), ("actor", actor),
    ):
        if not value or not value.strip():
            raise LabelRegistryError(f"{field_name} is required and must be non-empty (Ph6)")

    con = sqlite3.connect(db_path)
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT MAX(version) FROM label_definitions WHERE name = ?", (name,)
        ).fetchone()
        next_version = (row[0] or 0) + 1
        definition_hash = _content_hash({
            "name": name, "version": next_version, "rationale": rationale,
            "interpretation": interpretation,
        })
        con.execute(
            "INSERT INTO label_definitions "
            "(name, version, definition_hash, rationale, interpretation, registered_by, registered_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (name, next_version, definition_hash, rationale, interpretation, actor, registered_at),
        )
        con.commit()
    finally:
        con.close()
    return LabelDefinitionHandle(name=name, version=next_version)


def get_definition(db_path: Path | str, name: str, version: int) -> dict | None:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT * FROM label_definitions WHERE name = ? AND version = ?",
            (name, version),
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        con.close()


def get_latest_definition(db_path: Path | str, name: str) -> dict | None:
    """
    Read-only: the highest-version row for `name`, or None if it has never
    been registered. register_definition() always creates the NEXT
    version by design; an idempotent caller (e.g. a migration script
    re-run) that wants to reuse an existing Definition when its content
    has not changed needs this read first -- that idempotency decision
    belongs to the caller, not to register_definition() itself.
    """
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT * FROM label_definitions WHERE name = ? ORDER BY version DESC LIMIT 1",
            (name,),
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Executable binding resolution -- mirrors feature_store/registry.py exactly
# ---------------------------------------------------------------------------

_BINDING_REGISTRY: dict[tuple[str, str], Callable[..., Any]] = {}


def register_binding(binding_id: str, engine_version: str, fn: Callable[..., Any]) -> None:
    """Registers `fn` under its own (binding_id, engine_version). Registering
    a new binding never removes or reinterprets an existing registration."""
    _BINDING_REGISTRY[(binding_id, engine_version)] = fn


def get_binding(binding_id: str, engine_version: str) -> Callable[..., Any]:
    """Resolve the EXACT implementation a Specification names. Raises
    UnknownBindingError (loud) if the pair is not registered -- never
    substitutes a different implementation."""
    fn = _BINDING_REGISTRY.get((binding_id, engine_version))
    if fn is None:
        raise UnknownBindingError(
            f"no binding registered for binding_id={binding_id!r}, "
            f"engine_version={engine_version!r} -- refusing rather than substituting"
        )
    return fn


# ---------------------------------------------------------------------------
# Label Specifications -- Tier A (completeness) at registration
# ---------------------------------------------------------------------------

def _validate_tier_a(
    definition_name: str,
    definition_version: int,
    horizon_days: int | None,
    horizon_governance: str,
    fill_convention: str,
    cost_convention: str,
    direction_convention: str,
    maturity_rule: str,
    validity_condition: str,
    conditioning_propagation: str,
    binding_id: str,
    engine_version: str,
) -> list[str]:
    """ADR-I011 §12 Tier A: every mandatory field (§8.2, plus the LR7
    horizon-governance field) present and well-formed. Returns a list of
    errors; empty means valid. Never raises -- register_specification()
    decides what to do with the result."""
    errors: list[str] = []

    # Field 1/10 -- Identity: Definition version reference (§8.2).
    if not definition_name or not definition_name.strip():
        errors.append("definition_name is required (ADR-I011 §8.2 Identity)")
    if definition_version is None or definition_version < 1:
        errors.append("definition_version is required and must be >= 1 (ADR-I011 §8.2 Identity)")

    # Field 2/10 -- Horizon (§8.2; identity per §16.1).
    if horizon_days is None or horizon_days < 1:
        errors.append("horizon_days is required and must be >= 1 (ADR-I011 §8.2 Horizon)")

    # Field 3/10 -- Horizon governance (§17 calendar trap; LR7).
    if horizon_governance not in HORIZON_GOVERNANCE_VALUES:
        errors.append(
            f"horizon_governance must be one of {sorted(HORIZON_GOVERNANCE_VALUES)}, "
            f"got {horizon_governance!r} (ADR-I011 §17 calendar trap)"
        )

    # Field 4/10 -- Fill convention (§8.2; resolves PS1 with §8.3).
    if not fill_convention or not fill_convention.strip():
        errors.append("fill_convention is required (ADR-I011 §8.2 Fill convention)")

    # Field 5/10 -- Cost convention (§8.2).
    if not cost_convention or not cost_convention.strip():
        errors.append("cost_convention is required (ADR-I011 §8.2 Cost convention)")

    # Field 6/10 -- Direction convention (§8.2).
    if not direction_convention or not direction_convention.strip():
        errors.append("direction_convention is required (ADR-I011 §8.2 Direction convention)")

    # Field 7/10 -- Maturity rule (§8.2; §12.2's Pending -> Matured condition).

    # Field 7/10 -- Maturity rule (§8.2; §12.2's Pending -> Matured condition).
    if not maturity_rule or not maturity_rule.strip():
        errors.append("maturity_rule is required (ADR-I011 §8.2 Maturity rule)")

    # Field 8/10 -- Validity condition (§8.2).
    if not validity_condition or not validity_condition.strip():
        errors.append("validity_condition is required (ADR-I011 §8.2 Validity condition)")

    # Field 9/10 -- Conditioning propagation (§8.2; PS7, IQROS-P12).
    if conditioning_propagation not in CONDITIONING_RULES:
        errors.append(
            f"conditioning_propagation must be one of {sorted(CONDITIONING_RULES)}, "
            f"got {conditioning_propagation!r} (ADR-I011 §8.2 Conditioning propagation)"
        )

    # Field 10/10 -- Binding: exactly one implementation identity + engine
    # version (§8.2, §8.3).
    if not binding_id or not binding_id.strip():
        errors.append("binding_id is required (ADR-I011 §8.2 Binding)")
    if not engine_version or not engine_version.strip():
        errors.append("engine_version is required (ADR-I011 §8.2 Binding)")

    return errors


def register_specification(
    db_path: Path | str,
    *,
    definition_name: str,
    definition_version: int,
    horizon_days: int,
    horizon_governance: str,
    fill_convention: str,
    cost_convention: str,
    direction_convention: str,
    maturity_rule: str,
    validity_condition: str,
    conditioning_propagation: str,
    binding_id: str,
    engine_version: str,
    actor: str,
    registered_at: str,
) -> str:
    """
    Register a Label Specification. Runs Tier A (completeness) validation
    first (ADR-I011 §12). On success, the row is written at phase
    "Registered" (not yet "Active" -- §12.1: binding equivalence must
    still be demonstrated via activate_specification()).

    On Tier A failure: a permanent Rejected row IS written (LG4,
    IQROS-P15 -- rejected specifications are permanent records, not
    silently refused), and SpecificationRejected is then raised.

    Returns the content-addressed spec_hash on success. The hash is
    computed ONLY over the fields above -- `actor` and `registered_at` are
    metadata, never content, which is what makes §11.2 ("Maturation Is
    Not a Version Change") hold structurally: nothing about when a value
    matures can ever enter this hash, because no maturity-state field
    exists on a Specification at all.
    """
    content = {
        "definition_name": definition_name,
        "definition_version": definition_version,
        "horizon_days": horizon_days,
        "horizon_governance": horizon_governance,
        "fill_convention": fill_convention,
        "cost_convention": cost_convention,
        "direction_convention": direction_convention,
        "maturity_rule": maturity_rule,
        "validity_condition": validity_condition,
        "conditioning_propagation": conditioning_propagation,
        "binding_id": binding_id,
        "engine_version": engine_version,
    }
    spec_hash = _content_hash(content)

    errors = _validate_tier_a(
        definition_name, definition_version, horizon_days, horizon_governance,
        fill_convention, cost_convention, direction_convention, maturity_rule,
        validity_condition, conditioning_propagation, binding_id, engine_version,
    )

    con = sqlite3.connect(db_path)
    try:
        con.execute("BEGIN IMMEDIATE")

        if not errors and definition_name and definition_version:
            exists = con.execute(
                "SELECT 1 FROM label_definitions WHERE name = ? AND version = ?",
                (definition_name, definition_version),
            ).fetchone()
            if exists is None:
                errors.append(
                    f"definition_name={definition_name!r} version={definition_version!r} "
                    f"does not exist (ADR-I011 §8.2 Identity)"
                )

        phase = "Rejected" if errors else "Registered"

        try:
            con.execute(
                "INSERT INTO label_specifications "
                "(spec_hash, definition_name, definition_version, horizon_days, "
                " horizon_governance, fill_convention, cost_convention, direction_convention, "
                " maturity_rule, validity_condition, conditioning_propagation, binding_id, "
                " engine_version, phase, registered_by, registered_at, rejection_reason) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    spec_hash, definition_name, definition_version, horizon_days,
                    horizon_governance, fill_convention, cost_convention, direction_convention,
                    maturity_rule, validity_condition, conditioning_propagation, binding_id,
                    engine_version, phase, actor, registered_at,
                    "; ".join(errors) if errors else None,
                ),
            )
        except sqlite3.IntegrityError:
            # Same content hash already registered -- idempotent no-op,
            # matching the content-addressing precedent elsewhere: a
            # byte-identical resubmission is the same specification, not
            # a new one.
            con.rollback()
            if errors:
                raise SpecificationRejected(spec_hash, errors)
            return spec_hash

        con.commit()
    finally:
        con.close()

    if errors:
        raise SpecificationRejected(spec_hash, errors)
    return spec_hash


def get_specification(db_path: Path | str, spec_hash: str) -> dict | None:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT * FROM label_specifications WHERE spec_hash = ?", (spec_hash,)
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        con.close()


def _transition_phase(
    db_path: Path | str, spec_hash: str, from_phase: str, to_phase: str, rejection_reason: str | None = None,
) -> None:
    if (from_phase, to_phase) not in _ALLOWED_TRANSITIONS:
        raise TransitionError(
            f"{from_phase!r} -> {to_phase!r} is not an allowed arc (ADR-I011 §12.1)"
        )
    con = sqlite3.connect(db_path)
    try:
        cur = con.execute(
            "UPDATE label_specifications SET phase = ?, rejection_reason = ? "
            "WHERE spec_hash = ? AND phase = ?",
            (to_phase, rejection_reason, spec_hash, from_phase),
        )
        if cur.rowcount != 1:
            raise TransitionError(
                f"refusing to proceed: specification {spec_hash[:12]}... was not in phase "
                f"{from_phase!r} at write time (concurrent modification, or unknown spec_hash)"
            )
        con.commit()
    finally:
        con.close()


def activate_specification(
    db_path: Path | str, spec_hash: str, equivalence_check: Callable[[], bool],
) -> None:
    """
    Binding-equivalence activation (ADR-I010 §13 Tier B mechanism, reused
    unchanged -- see the module docstring's "TIER B NAMING COLLISION" note
    for why this is NOT ADR-I011's own §12 Tier B). Runs
    `equivalence_check` -- the caller's own definition of what
    "equivalent" means for this Specification's one declared binding.

    On success: Registered -> Active (citable by new datasets, §12.1). On
    failure -- including an exception raised BY the check itself, treated
    as a failure, not propagated (fail-closed) -- Registered -> Rejected,
    a permanent record (LG4), and SpecificationRejected is raised.
    """
    spec = get_specification(db_path, spec_hash)
    if spec is None:
        raise LabelRegistryError(f"no such specification: {spec_hash}")
    if spec["phase"] != "Registered":
        raise TransitionError(
            f"specification {spec_hash[:12]}... is in phase {spec['phase']!r}, "
            f"not 'Registered' -- activate_specification() only applies to a freshly "
            f"registered specification"
        )

    try:
        passed = bool(equivalence_check())
        detail = None
    except Exception as exc:  # noqa: BLE001 -- fail-closed, never propagate
        passed = False
        detail = f"{type(exc).__name__}: {exc}"

    if passed:
        _transition_phase(db_path, spec_hash, "Registered", "Active")
    else:
        reason = detail or "binding equivalence check returned False"
        _transition_phase(db_path, spec_hash, "Registered", "Rejected", rejection_reason=reason)
        raise SpecificationRejected(spec_hash, [reason])


def retire_specification(db_path: Path | str, spec_hash: str, from_phase: str) -> None:
    """Deprecated/Superseded -> Retired. No citation-index check is
    performed here (§12.1: "Retirement requires zero citations" -- the
    citation index is ADR-I012's Dataset Builder, not built yet; this
    milestone's retire_specification() is the mechanical transition only,
    correctly deferring the citation check to whichever future milestone
    builds the index -- mirrors LG5)."""
    _transition_phase(db_path, spec_hash, from_phase, "Retired")
