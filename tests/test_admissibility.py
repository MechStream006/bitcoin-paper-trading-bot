"""
Admissibility lattice (ADR-I014) -- research/admissibility/lattice.py and
composition.py. Engineering roadmap (docs/engineering/implementation_roadmap_v1.md)
Phase 0, WBS 0.1-0.4, the first milestone in the Sprint Plan (P2-1).

Covers: the five dimensions and their fixed orderings including Choice 1
(bottom < RED) and Choice 2 (adjudicated < clean), the componentwise
partial order over Admissibility tuples, the not-applicable marker and
its ⊤-only construction rule, meet's algebraic laws and its
not-applicable propagation, join's algebraic laws, the admission test
naming every unmet dimension (never just the first), and lattice-version
mismatch refusal across all four operations that accept more than one
Admissibility value.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research" / "admissibility"))

import composition  # noqa: E402
import lattice  # noqa: E402
import lattice_version  # noqa: E402
from composition import AdmissionResult, join, meet, satisfies  # noqa: E402
from lattice import (  # noqa: E402
    Admissibility,
    Certification,
    Dimension,
    InapplicableDimensionError,
    Integrity,
    LatticeVersionMismatch,
    Maturity,
    Provenance,
    Readiness,
    bottom,
    top,
)


def make(
    provenance=Provenance.UNKNOWN,
    certification=Certification.UNKNOWN,
    readiness=Readiness.UNKNOWN,
    maturity=Maturity.UNKNOWN,
    integrity=Integrity.UNKNOWN,
    not_applicable=frozenset(),
    lattice_version=lattice_version.CURRENT_LATTICE_VERSION,
) -> Admissibility:
    return Admissibility(
        provenance=provenance,
        certification=certification,
        readiness=readiness,
        maturity=maturity,
        integrity=integrity,
        not_applicable=not_applicable,
        lattice_version=lattice_version,
    )


class TestDimensionOrderings:
    def test_provenance_chain_order(self):
        assert Provenance.UNKNOWN < Provenance.OFFLINE_CORPUS < Provenance.LIVE_FORWARD < Provenance.MAINNET

    def test_choice_1_bottom_below_red(self):
        # ADR-I014 §2.3 Choice 1: unknown and RED both block, but a
        # determination (RED) outranks mere absence (unknown).
        assert Certification.UNKNOWN < Certification.RED < Certification.AMBER < Certification.GREEN

    def test_choice_2_adjudicated_below_clean(self):
        # ADR-I014 §2.3 Choice 2: adjudicated is not restored to clean.
        assert Integrity.UNKNOWN < Integrity.FLAGGED < Integrity.ADJUDICATED < Integrity.CLEAN

    def test_readiness_and_maturity_chains(self):
        assert Readiness.UNKNOWN < Readiness.NOT_READY < Readiness.PARTIAL < Readiness.READY
        assert Maturity.UNKNOWN < Maturity.DECLARED < Maturity.PARTIAL < Maturity.FULL


class TestBottomAndTop:
    def test_bottom_is_all_unknown(self):
        b = bottom()
        assert b.provenance == Provenance.UNKNOWN
        assert b.certification == Certification.UNKNOWN
        assert b.readiness == Readiness.UNKNOWN
        assert b.maturity == Maturity.UNKNOWN
        assert b.integrity == Integrity.UNKNOWN
        assert b.not_applicable == frozenset()

    def test_top_is_maximal_and_not_flagged_not_applicable(self):
        t = top()
        assert t.provenance == Provenance.MAINNET
        assert t.certification == Certification.GREEN
        assert t.readiness == Readiness.READY
        assert t.maturity == Maturity.FULL
        assert t.integrity == Integrity.CLEAN
        assert t.not_applicable == frozenset()

    def test_bottom_satisfies_no_floor_above_itself(self):
        result = satisfies(ceiling=bottom(), floor=make(provenance=Provenance.OFFLINE_CORPUS))
        assert not result.admitted
        assert Dimension.PROVENANCE in result.unmet_dimensions

    def test_top_satisfies_any_floor(self):
        floor = make(
            provenance=Provenance.MAINNET,
            certification=Certification.GREEN,
            readiness=Readiness.READY,
            maturity=Maturity.FULL,
            integrity=Integrity.CLEAN,
        )
        assert satisfies(ceiling=top(), floor=floor).admitted


class TestPartialOrder:
    def test_reflexive(self):
        a = make(provenance=Provenance.LIVE_FORWARD, certification=Certification.AMBER)
        assert a <= a

    def test_dominated_true(self):
        weaker = make(provenance=Provenance.OFFLINE_CORPUS, certification=Certification.RED)
        stronger = make(provenance=Provenance.LIVE_FORWARD, certification=Certification.AMBER)
        assert weaker <= stronger
        assert not (stronger <= weaker)

    def test_transitive(self):
        a = make(provenance=Provenance.UNKNOWN)
        b = make(provenance=Provenance.OFFLINE_CORPUS)
        c = make(provenance=Provenance.LIVE_FORWARD)
        assert a <= b
        assert b <= c
        assert a <= c

    def test_incomparable_pair_is_false_both_ways(self):
        # Stronger on provenance, weaker on maturity -- versus the reverse.
        a = make(provenance=Provenance.MAINNET, maturity=Maturity.UNKNOWN)
        b = make(provenance=Provenance.UNKNOWN, maturity=Maturity.FULL)
        assert not (a <= b)
        assert not (b <= a)


class TestNotApplicable:
    def test_marking_top_rung_succeeds(self):
        a = make(maturity=Maturity.FULL, not_applicable=frozenset({Dimension.MATURITY}))
        assert a.is_not_applicable(Dimension.MATURITY)
        assert a.level(Dimension.MATURITY) == Maturity.FULL

    def test_marking_non_top_rung_raises(self):
        # ADR-I014 §5.1: not-applicable must be Top, never anything else --
        # marking a non-top rung would fabricate a deficiency for a
        # quality the artifact class does not possess.
        with pytest.raises(InapplicableDimensionError):
            make(maturity=Maturity.PARTIAL, not_applicable=frozenset({Dimension.MATURITY}))

    def test_not_applicable_dimension_never_blocks_admission(self):
        ceiling = make(
            provenance=Provenance.LIVE_FORWARD,
            maturity=Maturity.FULL,
            not_applicable=frozenset({Dimension.MATURITY}),
        )
        floor = make(maturity=Maturity.FULL)
        assert satisfies(ceiling, floor).admitted


class TestMeet:
    def test_idempotent(self):
        a = make(provenance=Provenance.LIVE_FORWARD, certification=Certification.AMBER)
        assert meet(a, a) == a

    def test_commutative(self):
        a = make(provenance=Provenance.LIVE_FORWARD)
        b = make(provenance=Provenance.OFFLINE_CORPUS, certification=Certification.GREEN)
        assert meet(a, b) == meet(b, a)

    def test_associative(self):
        a = make(provenance=Provenance.MAINNET)
        b = make(certification=Certification.RED)
        c = make(readiness=Readiness.READY)
        assert meet(meet(a, b), c) == meet(a, meet(b, c))

    def test_componentwise_minimum(self):
        a = make(provenance=Provenance.MAINNET, certification=Certification.RED)
        b = make(provenance=Provenance.OFFLINE_CORPUS, certification=Certification.GREEN)
        result = meet(a, b)
        assert result.provenance == Provenance.OFFLINE_CORPUS
        assert result.certification == Certification.RED

    def test_monotone_decreasing_never_exceeds_any_input(self):
        a = make(provenance=Provenance.MAINNET, certification=Certification.GREEN)
        b = make(provenance=Provenance.OFFLINE_CORPUS, certification=Certification.RED)
        result = meet(a, b)
        assert result <= a
        assert result <= b

    def test_meet_with_top_is_identity(self):
        a = make(provenance=Provenance.LIVE_FORWARD, certification=Certification.AMBER)
        assert meet(a, top()) == a

    def test_not_applicable_on_all_inputs_propagates(self):
        a = make(maturity=Maturity.FULL, not_applicable=frozenset({Dimension.MATURITY}))
        b = make(maturity=Maturity.FULL, not_applicable=frozenset({Dimension.MATURITY}))
        result = meet(a, b)
        assert result.is_not_applicable(Dimension.MATURITY)

    def test_not_applicable_on_only_one_input_does_not_propagate(self):
        # One input genuinely constrains Maturity; the composed artifact
        # does possess the dimension, so it must not be marked N/A.
        na = make(maturity=Maturity.FULL, not_applicable=frozenset({Dimension.MATURITY}))
        concrete = make(maturity=Maturity.PARTIAL)
        result = meet(na, concrete)
        assert not result.is_not_applicable(Dimension.MATURITY)
        assert result.maturity == Maturity.PARTIAL

    def test_requires_at_least_one_input(self):
        with pytest.raises(ValueError):
            meet()


class TestJoin:
    def test_idempotent(self):
        a = make(provenance=Provenance.LIVE_FORWARD)
        assert join(a, a) == a

    def test_commutative(self):
        a = make(provenance=Provenance.LIVE_FORWARD)
        b = make(certification=Certification.GREEN)
        assert join(a, b) == join(b, a)

    def test_associative(self):
        a = make(provenance=Provenance.OFFLINE_CORPUS)
        b = make(certification=Certification.AMBER)
        c = make(readiness=Readiness.PARTIAL)
        assert join(join(a, b), c) == join(a, join(b, c))

    def test_componentwise_maximum(self):
        a = make(provenance=Provenance.OFFLINE_CORPUS, certification=Certification.GREEN)
        b = make(provenance=Provenance.MAINNET, certification=Certification.RED)
        result = join(a, b)
        assert result.provenance == Provenance.MAINNET
        assert result.certification == Certification.GREEN

    def test_join_with_bottom_is_identity(self):
        a = make(provenance=Provenance.LIVE_FORWARD, certification=Certification.AMBER)
        assert join(a, bottom()) == a

    def test_requires_at_least_one_input(self):
        with pytest.raises(ValueError):
            join()


class TestAdmission:
    def test_admits_when_ceiling_meets_floor_on_every_dimension(self):
        ceiling = make(
            provenance=Provenance.LIVE_FORWARD,
            certification=Certification.GREEN,
            readiness=Readiness.READY,
            maturity=Maturity.FULL,
            integrity=Integrity.CLEAN,
        )
        floor = make(provenance=Provenance.OFFLINE_CORPUS, certification=Certification.AMBER)
        result = satisfies(ceiling, floor)
        assert isinstance(result, AdmissionResult)
        assert result.admitted
        assert bool(result) is True
        assert result.unmet_dimensions == frozenset()

    def test_refusal_names_every_unmet_dimension_not_just_the_first(self):
        # ADR-I013 §16 PR2 / ADR-I014 AD4: a family blocked three ways
        # must show three blockers, not the first one found.
        ceiling = make(
            provenance=Provenance.OFFLINE_CORPUS,
            certification=Certification.UNKNOWN,
            readiness=Readiness.UNKNOWN,
        )
        floor = make(
            provenance=Provenance.LIVE_FORWARD,
            certification=Certification.GREEN,
            readiness=Readiness.READY,
        )
        result = satisfies(ceiling, floor)
        assert not result.admitted
        assert result.unmet_dimensions == frozenset(
            {Dimension.PROVENANCE, Dimension.CERTIFICATION, Dimension.READINESS}
        )

    def test_exact_equality_on_a_dimension_is_satisfied(self):
        ceiling = make(provenance=Provenance.LIVE_FORWARD)
        floor = make(provenance=Provenance.LIVE_FORWARD)
        assert satisfies(ceiling, floor).admitted


class TestLatticeVersionMismatch:
    OTHER = "9.9.9"

    def test_le_refuses_cross_version_comparison(self):
        a = make(lattice_version=lattice_version.CURRENT_LATTICE_VERSION)
        b = make(lattice_version=self.OTHER)
        with pytest.raises(LatticeVersionMismatch):
            _ = a <= b

    def test_meet_refuses_cross_version(self):
        a = make(lattice_version=lattice_version.CURRENT_LATTICE_VERSION)
        b = make(lattice_version=self.OTHER)
        with pytest.raises(LatticeVersionMismatch):
            meet(a, b)

    def test_join_refuses_cross_version(self):
        a = make(lattice_version=lattice_version.CURRENT_LATTICE_VERSION)
        b = make(lattice_version=self.OTHER)
        with pytest.raises(LatticeVersionMismatch):
            join(a, b)

    def test_satisfies_refuses_cross_version(self):
        a = make(lattice_version=lattice_version.CURRENT_LATTICE_VERSION)
        b = make(lattice_version=self.OTHER)
        with pytest.raises(LatticeVersionMismatch):
            satisfies(a, b)


class TestLatticeVersionRegistry:
    def test_current_version_is_known(self):
        assert lattice_version.is_known(lattice_version.CURRENT_LATTICE_VERSION)

    def test_unknown_version_is_not_known(self):
        assert not lattice_version.is_known("0.0.1-does-not-exist")


class TestImmutability:
    def test_admissibility_is_frozen(self):
        a = make(provenance=Provenance.LIVE_FORWARD)
        with pytest.raises(Exception):
            a.provenance = Provenance.MAINNET  # type: ignore[misc]

    def test_admissibility_is_hashable(self):
        a = make(provenance=Provenance.LIVE_FORWARD)
        b = make(provenance=Provenance.LIVE_FORWARD)
        assert hash(a) == hash(b)
        assert {a, b} == {a}
