"""
Meet, join, and the admission test (ADR-I014 §4, engineering roadmap
WBS 0.2).

Meet composes SUPPLY: an artifact derived from inputs carries the meet of
their admissibilities -- componentwise minimum. This is IQROS-P4
verbatim: nothing may be declared more admissible than its weakest input.

Join composes DEMAND: when several consumers share one artifact, the
binding requirement is the join of their floors -- componentwise maximum,
the weakest tuple satisfying all of them.

Admission: an artifact is admissible to a consumer iff its ceiling is at
or above the consumer's floor on every dimension. A refusal names every
unmet dimension, never just the first (AD4).

No storage. No artifact model. This module composes `Admissibility`
values only.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, FrozenSet, Iterable, Set, Tuple

from lattice import Admissibility, Dimension, require_same_lattice_version


def meet(*ceilings: Admissibility) -> Admissibility:
    """Componentwise minimum (ADR-I014 §4.1).

    Three properties hold, each already relied upon by the ADRs this
    module serves:
      * Monotone decreasing -- derivation can only weaken admissibility,
        never strengthen it (ADR-I010 §9.2, ADR-I011 §9.2).
      * Order-independent -- associative and commutative, so composition
        order does not matter (ADR-I012 §8 stage 3).
      * Dimension-preserving -- a weakness on one dimension never
        contaminates another, so every deficiency can be named
        separately (ADR-I013 §16 PR2).

    A dimension not-applicable on every input remains not-applicable on
    the result (ADR-I014 §5.1): composing several Feature value sets,
    none of which possesses Maturity, yields a result that still does not
    possess Maturity, rather than manufacturing a deficiency none of the
    inputs could have had. If even one input carries a concrete value on
    a dimension, the result is concrete on that dimension.
    """
    if not ceilings:
        raise ValueError("meet() requires at least one input")
    require_same_lattice_version(*ceilings)
    return _compose(ceilings, pick=min)


def join(*floors: Admissibility) -> Admissibility:
    """Componentwise maximum (ADR-I014 §4.2) -- the weakest tuple that
    still satisfies every one of several declared floors."""
    if not floors:
        raise ValueError("join() requires at least one input")
    require_same_lattice_version(*floors)
    return _compose(floors, pick=max)


def _compose(
    values: Tuple[Admissibility, ...],
    pick: Callable[[Iterable], object],
) -> Admissibility:
    levels = {dim: pick(v.level(dim) for v in values) for dim in Dimension}
    not_applicable: Set[Dimension] = {
        dim for dim in Dimension if all(v.is_not_applicable(dim) for v in values)
    }
    return Admissibility(
        provenance=levels[Dimension.PROVENANCE],
        certification=levels[Dimension.CERTIFICATION],
        readiness=levels[Dimension.READINESS],
        maturity=levels[Dimension.MATURITY],
        integrity=levels[Dimension.INTEGRITY],
        not_applicable=frozenset(not_applicable),
        lattice_version=values[0].lattice_version,
    )


@dataclass(frozen=True)
class AdmissionResult:
    """The outcome of an admission test (ADR-I014 §4.3, AD4)."""

    admitted: bool
    unmet_dimensions: FrozenSet[Dimension]

    def __bool__(self) -> bool:
        return self.admitted


def satisfies(ceiling: Admissibility, floor: Admissibility) -> AdmissionResult:
    """An artifact is admissible to a consumer iff its ceiling is at or
    above the consumer's floor, componentwise (ADR-I014 §4.3):

      AD1 -- the ceiling is computed, never asserted (enforced by callers:
             this function only ever receives values already produced by
             `meet()` or a direct construction, never a self-reported one).
      AD2 -- the floor is declared, never inferred (same: caller-supplied).
      AD3 -- a floor is never relaxed to permit admission -- failure here
             is always a refusal, never a downgrade of the floor.
      AD4 -- a refusal names every unmet dimension, not the first.
    """
    require_same_lattice_version(ceiling, floor)
    unmet = frozenset(
        dim for dim in Dimension if ceiling.level(dim) < floor.level(dim)
    )
    return AdmissionResult(admitted=not unmet, unmet_dimensions=unmet)
