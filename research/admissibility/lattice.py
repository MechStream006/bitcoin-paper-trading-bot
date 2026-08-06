"""
Admissibility lattice primitives (ADR-I014 §2-§3, engineering roadmap
WBS 0.1).

Admissibility is a tuple over five independent dimensions, ordered
componentwise -- not a scalar (ADR-I014 §2.1). Each dimension is a
bounded, totally ordered chain with a bottom element (unknown) meaning
"unknown or absent" (Ph6, made mechanical per ADR-I014 §2.4): a missing
verdict caps a dimension, it does not default to a passing value.

This module defines the five chains, the Admissibility tuple type, the
partial order over tuples (ADR-I014 §3.1), and the not-applicable marker
for dimensions that do not apply to a given artifact class (ADR-I014
§5.1). It performs no computation over multiple tuples (meet/join) --
that is research/admissibility/composition.py -- and holds no storage.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Dict, FrozenSet

from lattice_version import CURRENT_LATTICE_VERSION


class Dimension(Enum):
    """The five admissibility dimensions (ADR-I014 §2.2)."""

    PROVENANCE = "P"
    CERTIFICATION = "C"
    READINESS = "R"
    MATURITY = "M"
    INTEGRITY = "I"


class Provenance(IntEnum):
    """Where did this come from? (ADR-I014 §2.2, Table row P)."""

    UNKNOWN = 0
    OFFLINE_CORPUS = 1
    LIVE_FORWARD = 2
    MAINNET = 3


class Certification(IntEnum):
    """Was the data certified? (ADR-I014 §2.2, Table row C).

    Choice 1 (ADR-I014 §2.3): UNKNOWN and RED both block; neither is
    admissible. They are ordered UNKNOWN < RED so that a *determination*
    of non-admissibility outranks mere *absence* of one -- a
    determination is more diagnostically informative. Nothing in the
    lattice depends on this ordering beyond which of two blocking values
    a meet reports.
    """

    UNKNOWN = 0
    RED = 1
    AMBER = 2
    GREEN = 3


class Readiness(IntEnum):
    """Were the inputs complete? (ADR-I014 §2.2, Table row R)."""

    UNKNOWN = 0
    NOT_READY = 1
    PARTIAL = 2
    READY = 3


class Maturity(IntEnum):
    """Have outcomes matured? (ADR-I014 §2.2, Table row M)."""

    UNKNOWN = 0
    DECLARED = 1
    PARTIAL = 2
    FULL = 3


class Integrity(IntEnum):
    """Has any input been invalidated since? (ADR-I014 §2.2, Table row I).

    Choice 2 (ADR-I014 §2.3): an input that was FLAGGED and then
    ADJUDICATED is NOT restored to CLEAN. ADJUDICATED ranks below CLEAN,
    preserving ADR-I011 §10.3's discipline that a flag is neither
    silently rewritten nor silently trusted -- adjudication permits use,
    it does not erase history.
    """

    UNKNOWN = 0
    FLAGGED = 1
    ADJUDICATED = 2
    CLEAN = 3


_TOP_RUNG: Dict[Dimension, IntEnum] = {
    Dimension.PROVENANCE: Provenance.MAINNET,
    Dimension.CERTIFICATION: Certification.GREEN,
    Dimension.READINESS: Readiness.READY,
    Dimension.MATURITY: Maturity.FULL,
    Dimension.INTEGRITY: Integrity.CLEAN,
}

class LatticeVersionMismatch(ValueError):
    """Raised when an operation is attempted across two different lattice
    versions. ADR-I014 AV4: "Ceilings from different lattice versions are
    not comparable without an explicit mapping. Absent one, comparison is
    refused, never approximated." No mapping exists yet -- there is only
    one lattice version -- so this always refuses rather than guessing.
    """


class InapplicableDimensionError(ValueError):
    """Raised when a dimension is marked not-applicable but not
    constructed at that dimension's top rung. ADR-I014 §5.1: "An
    inapplicable dimension is Top on that dimension, not Bottom -- it
    cannot be a deficiency if it cannot exist." Marking a dimension
    not-applicable at any rung other than Top would make an artifact that
    structurally cannot possess a quality look deficient in it.
    """


def require_same_lattice_version(*values: "Admissibility") -> None:
    """Refuse (never approximate) an operation spanning two lattice
    versions (AV4)."""
    versions = {v.lattice_version for v in values}
    if len(versions) > 1:
        raise LatticeVersionMismatch(
            "cannot compare or compose admissibility values across "
            f"lattice versions {sorted(versions)!r} without an explicit "
            "mapping (ADR-I014 AV4)"
        )


@dataclass(frozen=True)
class Admissibility:
    """A tuple over the five admissibility dimensions (ADR-I014 §2.2),
    ordered componentwise (§3.1) -- a partial order, not a total one.
    Immutable: IQROS-P5 content addressing and ADR-I014 AR4 ("An
    artifact's ceiling is immutable once sealed") both depend on a
    ceiling never changing in place once constructed.

    `not_applicable` names dimensions that do not apply to the artifact
    class this tuple describes (ADR-I014 §5.1) -- e.g. Maturity for a
    Feature value set. Every dimension so marked must carry that
    dimension's top rung; `__post_init__` enforces this so "not
    applicable" can never silently manufacture a deficiency.
    """

    provenance: Provenance
    certification: Certification
    readiness: Readiness
    maturity: Maturity
    integrity: Integrity
    not_applicable: FrozenSet[Dimension] = field(default_factory=frozenset)
    lattice_version: str = CURRENT_LATTICE_VERSION

    def __post_init__(self) -> None:
        for dim in self.not_applicable:
            actual = self.level(dim)
            if actual != _TOP_RUNG[dim]:
                raise InapplicableDimensionError(
                    f"dimension {dim.value!r} marked not-applicable must "
                    f"carry its top rung ({_TOP_RUNG[dim].name}); got "
                    f"{actual.name} (ADR-I014 §5.1)"
                )

    def level(self, dimension: Dimension) -> IntEnum:
        """The rung on one dimension, regardless of applicability."""
        return {
            Dimension.PROVENANCE: self.provenance,
            Dimension.CERTIFICATION: self.certification,
            Dimension.READINESS: self.readiness,
            Dimension.MATURITY: self.maturity,
            Dimension.INTEGRITY: self.integrity,
        }[dimension]

    def is_not_applicable(self, dimension: Dimension) -> bool:
        return dimension in self.not_applicable

    def __le__(self, other: "Admissibility") -> bool:
        """Partial order (ADR-I014 §3.1): `a <= b` iff `a` is at or below
        `b` on every dimension. Two tuples may be incomparable -- both
        `a <= b` and `b <= a` are then False, which is correct: this is
        "dominated by", not a total ranking.
        """
        require_same_lattice_version(self, other)
        return all(self.level(d) <= other.level(d) for d in Dimension)


def bottom(lattice_version: str = CURRENT_LATTICE_VERSION) -> Admissibility:
    """Bottom: nothing is known. Satisfies no floor above itself
    (ADR-I014 §3.2)."""
    return Admissibility(
        provenance=Provenance.UNKNOWN,
        certification=Certification.UNKNOWN,
        readiness=Readiness.UNKNOWN,
        maturity=Maturity.UNKNOWN,
        integrity=Integrity.UNKNOWN,
        lattice_version=lattice_version,
    )


def top(lattice_version: str = CURRENT_LATTICE_VERSION) -> Admissibility:
    """Top: the maximal admissibility an artifact can carry. Not
    "approved" -- whether Top is good enough is a separate, merit
    question (ADR-I014 §3.2, §4.4)."""
    return Admissibility(
        provenance=Provenance.MAINNET,
        certification=Certification.GREEN,
        readiness=Readiness.READY,
        maturity=Maturity.FULL,
        integrity=Integrity.CLEAN,
        lattice_version=lattice_version,
    )
