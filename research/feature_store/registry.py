"""
Feature Registry (ADR-I010 §6, §8, §11; engineering roadmap Phase 2, WBS 2.1
+ 2.3 -- the first Feature Store milestone).

The Registry is the authority on WHAT a feature is. It holds Feature
Definitions (semantic statement, versioned, human-authored) and Feature
Specifications (executable contract, content-addressed) -- it NEVER holds
values (ADR-I010 §6, §14.1: "The Registry never computes values; the Value
Store never issues judgments").

SCOPE OF THIS MILESTONE. This module is generic and self-contained -- it
does not import, wrap, or depend on any real production feature. It is
proven here with hand-written test Specifications only, per the roadmap's
own Sprint exit criterion ("Feature registry accepts a hand-written test
Specification; Tier A/B validation refuses a malformed one"). Wrapping the
~20 existing shared derived features as registered Specifications is WBS
2.4, a later, separate milestone.

BINDING RESOLUTION mirrors research/evidence_library/evidence_governance.py's
AdmissibilityRule / register_rule() / get_rule() pattern exactly, reused
rather than reinvented: a Specification records only a (binding_id,
engine_version) identity; the actual callable is registered in-process via
register_binding() and resolved via get_binding(), which raises
UnknownBindingError -- never substitutes a different implementation -- if
the pair is unresolved. This is the same "resolve the exact historical
implementation, refuse rather than guess" discipline already audited in
this repository (governance receipt replay), applied here to feature
bindings.

SCOPED SIMPLIFICATIONS, stated explicitly rather than left implicit:
  - Phase transitions (Registered -> Active / Rejected, etc.) are guarded in
    Python (an explicit allowed-transitions table plus a WHERE-clause
    concurrency guard on UPDATE), not by a database-level trigger. This
    matches record.py's state_transitions precedent, not the stricter
    guarded-trigger precedent in contract_lifecycle.py -- appropriate for
    this milestone's scope; a future hardening pass could add the DB-level
    guard if this registry ever needs contract_lifecycle.py's stronger
    tamper-evidence property.
  - Content hashing uses plain sort_keys JSON + SHA-256, not
    receipts.py's stricter decimal-string canonicalisation (which exists
    for cross-platform governance-receipt permanence, a stronger
    requirement than feature-specification hashing needs at this stage).
  - Tier A's check of `input_requirements` confirms each entry is a
    non-empty dict naming at least a source and its required fields; it
    does not yet deep-validate admissibility-floor/lane sub-structure
    against research/admissibility/'s vocabulary -- that coupling belongs
    to whichever future milestone actually resolves inputs against real,
    certified data (WBS 2.4+), not to this generic registry.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

CONDITIONING_RULES = frozenset({"preserving", "narrowing", "cross-sectional"})

PHASES = frozenset({
    "Registered", "Active", "Deprecated", "Superseded", "Retired", "Rejected",
})

# ADR-I010 §11 lifecycle diagram, transcribed verbatim as an allowed-arc
# table. "Proposed" is not a persisted phase in this implementation -- see
# the module docstring's scoped-simplifications note: nothing is written
# until Tier A either passes (phase="Registered") or fails
# (phase="Rejected", still a permanent record per FG3/IQROS-P15).
_ALLOWED_TRANSITIONS: frozenset[tuple[str, str]] = frozenset({
    ("Registered", "Active"),      # Tier B equivalence demonstrated
    ("Registered", "Rejected"),    # Tier B equivalence failed
    ("Active", "Deprecated"),
    ("Active", "Superseded"),
    ("Deprecated", "Retired"),
    ("Superseded", "Retired"),
})

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS feature_definitions (
    name            TEXT NOT NULL,
    version         INTEGER NOT NULL,
    definition_hash TEXT NOT NULL UNIQUE,
    rationale       TEXT NOT NULL,
    intended_role   TEXT NOT NULL,
    registered_by   TEXT NOT NULL,
    registered_at   TEXT NOT NULL,
    PRIMARY KEY (name, version)
);

CREATE TRIGGER IF NOT EXISTS trg_feature_definitions_no_update
BEFORE UPDATE ON feature_definitions
BEGIN
    SELECT RAISE(ABORT, 'feature_definitions is append-only: UPDATE forbidden');
END;

CREATE TRIGGER IF NOT EXISTS trg_feature_definitions_no_delete
BEFORE DELETE ON feature_definitions
BEGIN
    SELECT RAISE(ABORT, 'feature_definitions is append-only: DELETE forbidden');
END;

CREATE TABLE IF NOT EXISTS feature_specifications (
    spec_hash                TEXT PRIMARY KEY,
    definition_name           TEXT NOT NULL,
    definition_version        INTEGER NOT NULL,
    parameters_json           TEXT NOT NULL,
    input_requirements_json   TEXT NOT NULL,
    warmup_periods             INTEGER NOT NULL,
    conditioning_rule         TEXT NOT NULL,
    binding_id                TEXT NOT NULL,
    engine_version              TEXT NOT NULL,
    validity_rule               TEXT NOT NULL,
    sensitivity_variants_json TEXT NOT NULL,
    phase                      TEXT NOT NULL,
    registered_by              TEXT NOT NULL,
    registered_at              TEXT NOT NULL,
    rejection_reason          TEXT,
    FOREIGN KEY (definition_name, definition_version)
        REFERENCES feature_definitions(name, version)
);
"""


class FeatureRegistryError(ValueError):
    """Base class for registry construction/validation failures."""


class UnknownDefinitionError(FeatureRegistryError):
    """Raised when a Specification references a Definition version that
    does not exist (ADR-I010 §6.3)."""


class SpecificationRejected(FeatureRegistryError):
    """
    Raised by register_specification() (Tier A failure) or
    activate_specification() (Tier B failure). The rejection is ALREADY
    persisted as a permanent record (FG3, IQROS-P15) before this is
    raised -- callers catching this are recovering from a recorded fact,
    not preventing one.
    """

    def __init__(self, spec_hash: str, errors: list[str]):
        self.spec_hash = spec_hash
        self.errors = errors
        super().__init__(f"specification {spec_hash[:12]}... rejected: {'; '.join(errors)}")


class UnknownBindingError(FeatureRegistryError):
    """
    Raised by get_binding() when no callable is registered for the exact
    (binding_id, engine_version) pair. Never falls back to "whatever the
    binding_id currently resolves to" -- mirrors evidence_governance.py's
    UnknownRuleError exactly.
    """


class TransitionError(FeatureRegistryError):
    """Raised when a phase transition is not in _ALLOWED_TRANSITIONS, or
    when the specification's current phase does not match what the caller
    expected (concurrent-modification guard)."""


def init_db(path: Path | str) -> None:
    """Idempotent CREATE-only schema-ensure. See research/evidence_library/
    receipt_ledger.py's init_db() docstring for why this deliberately never
    performs an ALTER: a column added later belongs in a dedicated,
    explicitly-invoked migration script, never behind a call reachable from
    a read path."""
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
# Feature Definitions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FeatureDefinitionHandle:
    name: str
    version: int


def register_definition(
    db_path: Path | str,
    name: str,
    rationale: str,
    intended_role: str,
    actor: str,
    registered_at: str,
) -> FeatureDefinitionHandle:
    """
    Register a new Feature Definition version. Always creates the NEXT
    ordinal version for `name` -- a Definition is versioned and immutable
    per version (ADR-I010 §6.1); there is no update path.

    Every field is required and refused if blank (Ph6).
    """
    for field_name, value in (
        ("name", name), ("rationale", rationale),
        ("intended_role", intended_role), ("actor", actor),
    ):
        if not value or not value.strip():
            raise FeatureRegistryError(f"{field_name} is required and must be non-empty (Ph6)")

    con = sqlite3.connect(db_path)
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT MAX(version) FROM feature_definitions WHERE name = ?", (name,)
        ).fetchone()
        next_version = (row[0] or 0) + 1
        definition_hash = _content_hash({
            "name": name, "version": next_version, "rationale": rationale,
            "intended_role": intended_role,
        })
        con.execute(
            "INSERT INTO feature_definitions "
            "(name, version, definition_hash, rationale, intended_role, registered_by, registered_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (name, next_version, definition_hash, rationale, intended_role, actor, registered_at),
        )
        con.commit()
    finally:
        con.close()
    return FeatureDefinitionHandle(name=name, version=next_version)


def get_definition(db_path: Path | str, name: str, version: int) -> dict | None:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT * FROM feature_definitions WHERE name = ? AND version = ?",
            (name, version),
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        con.close()


def get_latest_definition(db_path: Path | str, name: str) -> dict | None:
    """
    Read-only: the highest-version row for `name`, or None if it has never
    been registered.

    register_definition() always creates the NEXT version -- by design, for
    a human deliberately choosing to version something (ADR-I010 §6.1). An
    idempotent caller (e.g. a migration script re-run) that wants to reuse
    an existing Definition when its content has not changed needs this
    read first; that idempotency decision belongs to the caller, not to
    register_definition() itself, which must remain unconditional.
    """
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT * FROM feature_definitions WHERE name = ? ORDER BY version DESC LIMIT 1",
            (name,),
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Executable binding resolution -- mirrors evidence_governance.py exactly
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
# Feature Specifications -- Tier A (completeness) at registration
# ---------------------------------------------------------------------------

def _validate_tier_a(
    definition_name: str,
    definition_version: int,
    parameters: dict,
    input_requirements: list,
    warmup_periods: int | None,
    conditioning_rule: str,
    binding_id: str,
    engine_version: str,
    validity_rule: str,
    sensitivity_variants: list | None,
) -> list[str]:
    """ADR-I010 §13 Tier A: every mandatory field (§8.2) present and
    well-formed. Returns a list of errors; empty means valid. Never raises
    -- register_specification() decides what to do with the result."""
    errors: list[str] = []

    if not definition_name or not definition_name.strip():
        errors.append("definition_name is required")
    if definition_version is None or definition_version < 1:
        errors.append("definition_version is required and must be >= 1")

    if not isinstance(parameters, dict):
        errors.append("parameters must be a dict (may be empty for a parameterless feature)")

    if not input_requirements:
        errors.append("input_requirements is required and must be non-empty (ADR-I010 §8.2)")
    else:
        for i, req in enumerate(input_requirements):
            if not isinstance(req, dict) or not req.get("source") or not req.get("fields"):
                errors.append(
                    f"input_requirements[{i}] must be a dict naming a non-empty "
                    f"'source' and non-empty 'fields'"
                )

    if warmup_periods is None or warmup_periods < 0:
        errors.append("warmup_periods is required and must be >= 0 (ADR-I010 §8.4)")

    if conditioning_rule not in CONDITIONING_RULES:
        errors.append(
            f"conditioning_rule must be one of {sorted(CONDITIONING_RULES)}, "
            f"got {conditioning_rule!r} (ADR-I010 §9.1)"
        )

    if not binding_id or not binding_id.strip():
        errors.append("binding_id is required (ADR-I010 §8.3: exactly one executable binding)")
    if not engine_version or not engine_version.strip():
        errors.append("engine_version is required")

    if not validity_rule or not validity_rule.strip():
        errors.append("validity_rule is required")

    if sensitivity_variants is None:
        errors.append("sensitivity_variants must be declared -- an empty list is valid, "
                       "omission is not (FG7)")

    return errors


def register_specification(
    db_path: Path | str,
    *,
    definition_name: str,
    definition_version: int,
    parameters: dict,
    input_requirements: list,
    warmup_periods: int,
    conditioning_rule: str,
    binding_id: str,
    engine_version: str,
    validity_rule: str,
    sensitivity_variants: list,
    actor: str,
    registered_at: str,
) -> str:
    """
    Register a Feature Specification. Runs Tier A (completeness) validation
    first (ADR-I010 §13). On success, the row is written at phase
    "Registered" (not yet "Active" -- ADR-I010 §11: binding equivalence
    must still be demonstrated via activate_specification()).

    On Tier A failure: a permanent Rejected row IS written (FG3,
    IQROS-P15 -- rejected specifications are permanent records, not
    silently refused), and SpecificationRejected is then raised.

    Returns the content-addressed spec_hash on success.
    """
    content = {
        "definition_name": definition_name,
        "definition_version": definition_version,
        "parameters": parameters,
        "input_requirements": input_requirements,
        "warmup_periods": warmup_periods,
        "conditioning_rule": conditioning_rule,
        "binding_id": binding_id,
        "engine_version": engine_version,
        "validity_rule": validity_rule,
        "sensitivity_variants": sensitivity_variants,
    }
    spec_hash = _content_hash(content)

    errors = _validate_tier_a(
        definition_name, definition_version, parameters, input_requirements,
        warmup_periods, conditioning_rule, binding_id, engine_version,
        validity_rule, sensitivity_variants,
    )

    con = sqlite3.connect(db_path)
    try:
        con.execute("BEGIN IMMEDIATE")

        if not errors and definition_name and definition_version:
            exists = con.execute(
                "SELECT 1 FROM feature_definitions WHERE name = ? AND version = ?",
                (definition_name, definition_version),
            ).fetchone()
            if exists is None:
                errors.append(
                    f"definition_name={definition_name!r} version={definition_version!r} "
                    f"does not exist (ADR-I010 §6.3)"
                )

        phase = "Rejected" if errors else "Registered"

        try:
            con.execute(
                "INSERT INTO feature_specifications "
                "(spec_hash, definition_name, definition_version, parameters_json, "
                " input_requirements_json, warmup_periods, conditioning_rule, binding_id, "
                " engine_version, validity_rule, sensitivity_variants_json, phase, "
                " registered_by, registered_at, rejection_reason) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    spec_hash, definition_name, definition_version,
                    _canonical_json(parameters).decode("utf-8"),
                    _canonical_json(input_requirements).decode("utf-8"),
                    warmup_periods, conditioning_rule, binding_id, engine_version,
                    validity_rule, _canonical_json(sensitivity_variants).decode("utf-8"),
                    phase, actor, registered_at,
                    "; ".join(errors) if errors else None,
                ),
            )
        except sqlite3.IntegrityError:
            # Same content hash already registered -- idempotent no-op,
            # matching the content-addressing precedent elsewhere
            # (receipts.py DuplicateReceiptError family): a byte-identical
            # resubmission is the same specification, not a new one.
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
            "SELECT * FROM feature_specifications WHERE spec_hash = ?", (spec_hash,)
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["parameters"] = json.loads(d["parameters_json"])
        d["input_requirements"] = json.loads(d["input_requirements_json"])
        d["sensitivity_variants"] = json.loads(d["sensitivity_variants_json"])
        return d
    finally:
        con.close()


def _transition_phase(
    db_path: Path | str, spec_hash: str, from_phase: str, to_phase: str, rejection_reason: str | None = None,
) -> None:
    if (from_phase, to_phase) not in _ALLOWED_TRANSITIONS:
        raise TransitionError(
            f"{from_phase!r} -> {to_phase!r} is not an allowed arc (ADR-I010 §11)"
        )
    con = sqlite3.connect(db_path)
    try:
        cur = con.execute(
            "UPDATE feature_specifications SET phase = ?, rejection_reason = ? "
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
    Tier B (ADR-I010 §13): binding equivalence. Runs `equivalence_check` --
    the caller's own definition of what "equivalent" means for this
    Specification (for a sole/authoritative binding, this is typically "the
    binding executes deterministically across its declared sensitivity
    variants"; for a conformant second binding, per §8.3, this compares
    against the authoritative implementation's output).

    On success: Registered -> Active (citable by new datasets, ADR-I010
    §11). On failure -- including an exception raised BY the check itself,
    treated as a failure, not propagated (fail-closed, matching
    governance_verification.py's _run() precedent) -- Registered ->
    Rejected, a permanent record (FG3), and SpecificationRejected is raised.
    """
    spec = get_specification(db_path, spec_hash)
    if spec is None:
        raise FeatureRegistryError(f"no such specification: {spec_hash}")
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
        reason = detail or "Tier B binding equivalence check returned False"
        _transition_phase(db_path, spec_hash, "Registered", "Rejected", rejection_reason=reason)
        raise SpecificationRejected(spec_hash, [reason])


class SpecificationCited(FeatureRegistryError):
    """Refusing to retire a Specification that a sealed dataset cites
    (ADR-I010 §11, DG-5)."""


def retire_specification(
    db_path: Path | str,
    spec_hash: str,
    from_phase: str,
    citation_check: Callable[[str], bool] | None = None,
) -> None:
    """Deprecated/Superseded -> Retired.

    ADR-I010 §11: *"Retirement requires zero citations. A Specification cited
    by any sealed artifact cannot be retired -- the citation index (ADR-I012)
    is the authority."* WBS 4.2 built that index, so the check is no longer
    deferred: pass `citation_check`, a `(spec_hash) -> bool` callable that
    reports whether a citable dataset cites this Specification. True refuses.

    The callable is **injected, never imported**. This module has no knowledge
    of the Dataset Builder, so the dependency arrow keeps pointing one way;
    `dataset_builder.citation_index.make_citation_check(db, "feature")` builds
    the callable a caller passes in.

    `citation_check=None` preserves the pre-4.2 signature and behaviour for
    existing callers. That is a deliberate backward-compatibility choice, not
    an escape hatch: a caller that omits the check is asserting there is no
    citation index to consult, which was literally true before 4.2.
    """
    if citation_check is not None and citation_check(spec_hash):
        raise SpecificationCited(
            f"refusing to retire {spec_hash[:12]}...: cited by at least one "
            f"sealed dataset (ADR-I010 §11, the citation index is the authority)"
        )
    _transition_phase(db_path, spec_hash, from_phase, "Retired")
