"""
Admissibility lattice versioning (ADR-I014 §12, engineering roadmap WBS 0.3).

The lattice is constitutional (AG1): adding a dimension, adding an
element, or changing an ordering requires a constitutional session, never
an ADR and never a code change alone (ADR-I014 §11.2, §12). This module
records the single lattice version this codebase currently implements and
the compatibility rule that composition.py enforces across versions
(AV4).

No storage, no migration. A second entry may only be added by a future
constitutional session that also states whether the change is additive
(AV2, minor) or reorders/removes a dimension or element (AV3, major).
"""
from __future__ import annotations

# AV1: every recorded ceiling names the lattice version it was computed
# under. "1.0.0" is the version implemented by lattice.py as written
# against ADR-I014 v0.1.0 §2.2 -- five dimensions (Provenance,
# Certification, Readiness, Maturity, Integrity), Choice 1 (bottom < RED)
# and Choice 2 (adjudicated < clean) as stated in §2.3.
CURRENT_LATTICE_VERSION = "1.0.0"

# AV5: a lattice version is never retired while any live artifact cites
# it. Exactly one version has ever existed, so this set has one member.
KNOWN_LATTICE_VERSIONS = frozenset({CURRENT_LATTICE_VERSION})


def is_known(lattice_version: str) -> bool:
    """Whether `lattice_version` is a version this codebase can compute
    with. Does not imply it is the current version -- only that it is
    recognised (AV5)."""
    return lattice_version in KNOWN_LATTICE_VERSIONS
