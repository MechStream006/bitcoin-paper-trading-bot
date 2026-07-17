"""
The policy matrix — docs/governance/data_completeness_gate.md §3.

Pure. The downstream pipeline DOES NOT DECIDE what to do about a readiness
state; it executes the Action this table returns. Ad-hoc reaction is what
produced two divergent, informally-implemented completeness contracts in
this codebase in the first place.

Along with states.py, this file is the entire safety argument. Every branch
here is covered by tests/test_dq_engine.py.
"""
from __future__ import annotations

from typing import Final

from states import (
    ACTIONS,
    BACKFILL_REQUIRED,
    BLOCK,
    COMPUTE,
    CONFIGURATION_ERROR,
    DEFER,
    DEGRADE,
    HALT,
    INCOMPLETE_DATA,
    PARTIAL_DAY,
    READY,
    RETRY,
    SCHEMA_MISMATCH,
    STALE_DATA,
    STATES,
    UNKNOWN,
)

REQUIRED: Final = "required"
OPTIONAL: Final = "optional"
CRITICALITIES: Final[frozenset[str]] = frozenset({REQUIRED, OPTIONAL})


class PolicyError(ValueError):
    """Raised for a malformed policy override."""


# §3, "The matrix". (state, criticality) -> Action.
#
# Two entries deserve their justification restated at the point of use,
# because both look wrong until you know why:
#
#   SCHEMA_MISMATCH / optional -> HALT (not DEGRADE). The one state where an
#   OPTIONAL input still halts the pipeline. A unit change (ms->us) or a
#   silently-unwritten column corrupts everything downstream, retroactively,
#   with no symptom. There is no safe degraded computation against data whose
#   MEANING has changed.
#
#   UNKNOWN / * -> BLOCK. Fail closed (L2). A gate that does not know must
#   not be a gate that says yes.
DEFAULT_MATRIX: Final[dict[str, dict[str, str]]] = {
    READY:               {REQUIRED: COMPUTE, OPTIONAL: COMPUTE},
    PARTIAL_DAY:         {REQUIRED: DEFER,   OPTIONAL: DEFER},
    STALE_DATA:          {REQUIRED: RETRY,   OPTIONAL: DEGRADE},
    INCOMPLETE_DATA:     {REQUIRED: RETRY,   OPTIONAL: DEGRADE},
    BACKFILL_REQUIRED:   {REQUIRED: BLOCK,   OPTIONAL: DEGRADE},
    SCHEMA_MISMATCH:     {REQUIRED: HALT,    OPTIONAL: HALT},
    CONFIGURATION_ERROR: {REQUIRED: HALT,    OPTIONAL: HALT},
    UNKNOWN:             {REQUIRED: BLOCK,   OPTIONAL: BLOCK},
}

# The states for which an OPTIONAL input may degrade rather than block — but
# ONLY if the contract declared that degradation path in advance (§3, "What
# is explicitly forbidden": "Degradation is only ever available along a path
# the contract *declared in advance*. Ad-hoc 'compute with what we have' is
# exactly what wrote a 00:05 tick into spot_close.").
DEGRADABLE_STATES: Final[frozenset[str]] = frozenset(
    {STALE_DATA, INCOMPLETE_DATA, BACKFILL_REQUIRED}
)

# What an undeclared optional degradation collapses to. Not COMPUTE — an
# optional input that is missing, with no declared degradation path, means
# the contract author never said what the derivation should do without it.
UNDECLARED_DEGRADATION_ACTION: Final = BLOCK


def validate_overrides(overrides: dict | None) -> list[str]:
    """Structural validation of a per-contract policy override table."""
    errors: list[str] = []
    if overrides is None:
        return errors
    if not isinstance(overrides, dict):
        return [f"policy override must be a mapping, got {type(overrides).__name__}"]
    for state, row in overrides.items():
        if state not in STATES:
            errors.append(f"policy override names unknown state '{state}'")
            continue
        if not isinstance(row, dict):
            errors.append(f"policy override for '{state}' must be a mapping of criticality -> action")
            continue
        for criticality, action in row.items():
            if criticality not in CRITICALITIES:
                errors.append(
                    f"policy override for '{state}' names unknown criticality '{criticality}'"
                )
            if action not in ACTIONS:
                errors.append(
                    f"policy override for '{state}'/'{criticality}' names unknown action '{action}'"
                )
    return errors


def resolve(
    state: str,
    criticality: str,
    degradation_declared: bool = False,
    overrides: dict | None = None,
) -> str:
    """
    (state, criticality) -> Action.

    degradation_declared: whether THIS input's contract declared a degradation
    path covering THIS state. Only consulted where the matrix would otherwise
    return DEGRADE — an undeclared degradation is never silently taken.

    overrides: a per-contract override table, {state: {criticality: action}}.
    Sparse; unlisted cells fall through to DEFAULT_MATRIX.
    """
    if state not in STATES:
        raise PolicyError(f"'{state}' is not a readiness state")
    if criticality not in CRITICALITIES:
        raise PolicyError(f"'{criticality}' is not a criticality; expected required|optional")

    if overrides:
        override = overrides.get(state, {}).get(criticality)
        if override is not None:
            return override

    action = DEFAULT_MATRIX[state][criticality]

    if action == DEGRADE and not degradation_declared:
        return UNDECLARED_DEGRADATION_ACTION

    return action
