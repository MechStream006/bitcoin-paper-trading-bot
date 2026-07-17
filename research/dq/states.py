"""
Readiness states and actions — docs/governance/data_completeness_gate.md §2, §3.

Pure. No I/O, no clock, no dependency on any other dq module. This file and
policy.py are, per the spec's own words, "the entire safety argument".

The eight states are SEVERITY-ORDERED (later = worse). Aggregation across
multiple inputs, and across multiple checks on one input, is WORST-WINS.

Incompleteness is NEVER expressible as NULL/None. Every evaluation path
terminates in one of these eight strings; the absence of information is
UNKNOWN (severity 7), which blocks. That is L2 (fail closed): "a gate that
cannot tell you whether the data is good must never say yes."
"""
from __future__ import annotations

from typing import Final

READY: Final = "READY"
PARTIAL_DAY: Final = "PARTIAL_DAY"
STALE_DATA: Final = "STALE_DATA"
INCOMPLETE_DATA: Final = "INCOMPLETE_DATA"
BACKFILL_REQUIRED: Final = "BACKFILL_REQUIRED"
SCHEMA_MISMATCH: Final = "SCHEMA_MISMATCH"
CONFIGURATION_ERROR: Final = "CONFIGURATION_ERROR"
UNKNOWN: Final = "UNKNOWN"

# Severity order is normative (§2). The index IS the severity.
STATE_SEVERITY: Final[dict[str, int]] = {
    READY: 0,
    PARTIAL_DAY: 1,
    STALE_DATA: 2,
    INCOMPLETE_DATA: 3,
    BACKFILL_REQUIRED: 4,
    SCHEMA_MISMATCH: 5,
    CONFIGURATION_ERROR: 6,
    UNKNOWN: 7,
}

STATES: Final[tuple[str, ...]] = tuple(STATE_SEVERITY)

# §2 "Ordering of checks is normative": the first state produced at a
# severity >= 5 SHORT-CIRCUITS the remaining checks. This is what stops
# schema drift from masquerading as missing data — a 0-row partition caused
# by the Binance ms->us switch must surface as SCHEMA_MISMATCH, never as
# INCOMPLETE_DATA, or an operator will "backfill" a code bug forever.
SHORT_CIRCUIT_SEVERITY: Final = 5

# ---------------------------------------------------------------------------
# Actions (§3)
# ---------------------------------------------------------------------------

COMPUTE: Final = "COMPUTE"
DEGRADE: Final = "DEGRADE"
RETRY: Final = "RETRY"
DEFER: Final = "DEFER"
BLOCK: Final = "BLOCK"
HALT: Final = "HALT"

# Action precedence for aggregation across inputs. NOT the same ordering as
# state severity, and the difference is deliberate:
#
#   DEFER outranks DEGRADE and RETRY.
#     A required input whose period has not closed (PARTIAL_DAY -> DEFER)
#     must win over an optional input's declared degradation, and over a
#     retry. Writing ANYTHING for a period that is not finished is the H6
#     bug (L4). Time is the only fix; nothing is gained by degrading or
#     retrying a day that has not happened yet.
#
#   RETRY outranks DEGRADE.
#     A recoverable required input should be re-probed before the pipeline
#     settles for a degraded output.
ACTION_PRECEDENCE: Final[dict[str, int]] = {
    COMPUTE: 0,
    DEGRADE: 1,
    RETRY: 2,
    DEFER: 3,
    BLOCK: 4,
    HALT: 5,
}

ACTIONS: Final[tuple[str, ...]] = tuple(ACTION_PRECEDENCE)

# Actions under which a derivation body may be entered at all (§4). Every
# other action means: do not derive, do not write.
DERIVING_ACTIONS: Final[frozenset[str]] = frozenset({COMPUTE, DEGRADE})


class UnknownStateError(ValueError):
    """Raised when a string is used as a readiness state but is not one."""


def severity(state: str) -> int:
    try:
        return STATE_SEVERITY[state]
    except KeyError:
        raise UnknownStateError(
            f"'{state}' is not a readiness state; expected one of {list(STATES)}"
        ) from None


def worst(states) -> str:
    """
    Worst-wins aggregation (§2). An empty input sequence is NOT READY — it is
    UNKNOWN. A gate that was asked to aggregate nothing has not established
    that anything is complete, and L2 forbids reading absence of evidence as
    evidence of completeness.
    """
    materialised = list(states)
    if not materialised:
        return UNKNOWN
    return max(materialised, key=severity)


def is_short_circuiting(state: str) -> bool:
    return severity(state) >= SHORT_CIRCUIT_SEVERITY


def worst_action(actions) -> str:
    """Aggregate actions across inputs by ACTION_PRECEDENCE (see above)."""
    materialised = list(actions)
    if not materialised:
        return BLOCK  # fail closed: nothing evaluated => nothing permitted
    return max(materialised, key=lambda a: ACTION_PRECEDENCE[a])
