"""Lane resolution and RF3 conflict detection — ADR-I012 §8.1 RF3,
IQROS-P6; resolves PS4.

PS4, stated in the ADR's own words:

> *"Lane disjointness is convention. Nothing structurally prevents a
> confirmatory evaluation from resolving discovery-lane inputs."*
> **Consequence:** *"The confirmatory lane can be contaminated by the thing it
> exists to confirm."*

DG4 asks to *"make lane disjointness structural"*. RF3 is how: a declaration
whose lane conflicts with its inputs' lanes **cannot be resolved**, so it can
never be validated, sealed, or cited.

SINGLE GOVERNING AUTHORITY
    The lane vocabulary is **not defined here.** It is read from
    `evidence_library/record.py`'s frozen `LANES` table, which has governed the
    evidence ledger since PR-5:

        L0  (offline-corpus, discovery)     L1  (offline-corpus, confirmatory)
        L2  (live-forward,   forward)       L3  (mainnet,        production)

    A lane is a **pair**, not a single word. Defining a second vocabulary here
    is exactly the duplication that lets two authorities drift apart, so this
    module imports the table and adds no members to it.

WHAT COUNTS AS A CONFLICT
    `FORBIDDEN_INPUT_LANES` holds **one** rule, because the ADR names exactly
    one: a confirmatory dataset must not resolve discovery-lane inputs. It is
    deliberately not generalised into a lane-compatibility lattice — inventing
    additional conflicts would be inventing governance. Adding an entry is a
    governance act, not an engineering one.

WHEN RF3 CANNOT BE EVALUATED
    §7.2 names the declaration's field simply `lane`, and the platform's
    existing fixtures carry the **provenance** half (`offline-corpus`). That
    half alone does not determine a lane: `offline-corpus` maps to **both** L0
    and L1. When the `run_kind` half is not determinable, RF3 reports
    `not_evaluable` rather than passing — "we could not tell" must never be
    indistinguishable from "we checked and it was clean".

    This is why RF3 does **not** silently start refusing every existing
    declaration: those declarations genuinely do not state which side of the
    discovery/confirmatory divide they sit on, and guessing would be worse
    than reporting.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

_EVIDENCE_LIB_DIR = Path(__file__).resolve().parents[1] / "evidence_library"
if str(_EVIDENCE_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_EVIDENCE_LIB_DIR))

# The single governing authority. Imported, never redefined.
from record import LANES  # noqa: E402

#: provenance -> the lane codes it can belong to. Derived from LANES, so it
#: cannot drift from the frozen table.
_CODES_BY_PROVENANCE: dict[str, list[str]] = {}
#: run_kind -> lane code. A run_kind determines a lane uniquely; a provenance
#: may not (offline-corpus is both L0 and L1). That asymmetry is the whole
#: reason RF3 sometimes cannot be evaluated.
_CODE_BY_RUN_KIND: dict[str, str] = {}
for _code, (_prov, _kind) in LANES.items():
    _CODES_BY_PROVENANCE.setdefault(_prov, []).append(_code)
    _CODE_BY_RUN_KIND[_kind] = _code

VALID_PROVENANCES: frozenset[str] = frozenset(_CODES_BY_PROVENANCE)
VALID_RUN_KINDS: frozenset[str] = frozenset(_CODE_BY_RUN_KIND)
VALID_LANE_CODES: frozenset[str] = frozenset(LANES)

#: The ADR names exactly one conflict (§8.1 RF3). This table holds that rule
#: and no more. Keyed by the *dataset's* run_kind; values are input run_kinds
#: it may not consume.
FORBIDDEN_INPUT_LANES: dict[str, frozenset[str]] = {
    "confirmatory": frozenset({"discovery"}),
}


@dataclass(frozen=True)
class Lane:
    """A resolved lane. `code` is None when the expression determined only one
    half — which is a legitimate outcome, not an error."""
    code: str | None
    provenance: str | None
    run_kind: str | None

    @property
    def is_complete(self) -> bool:
        return self.code is not None

    def __str__(self) -> str:
        if self.code:
            return f"{self.code} ({self.provenance}, {self.run_kind})"
        parts = [p for p in (self.provenance, self.run_kind) if p]
        return f"partial({', '.join(parts)})" if parts else "unknown"


UNKNOWN_LANE = Lane(None, None, None)


def resolve_lane(expression: object) -> Lane:
    """Interpret a lane expression against the frozen table.

    Accepts a lane code (`"L1"`), a run_kind (`"confirmatory"`), or a
    provenance (`"offline-corpus"`). A provenance that maps to more than one
    lane resolves only its own half — deliberately, because guessing the other
    half is precisely the contamination RF3 exists to prevent.
    """
    text = str(expression or "").strip()
    if not text:
        return UNKNOWN_LANE

    upper = text.upper()
    if upper in VALID_LANE_CODES:
        prov, kind = LANES[upper]
        return Lane(upper, prov, kind)

    lowered = text.lower()
    if lowered in VALID_RUN_KINDS:
        code = _CODE_BY_RUN_KIND[lowered]
        return Lane(code, LANES[code][0], lowered)

    if lowered in VALID_PROVENANCES:
        codes = _CODES_BY_PROVENANCE[lowered]
        if len(codes) == 1:
            code = codes[0]
            return Lane(code, lowered, LANES[code][1])
        # Ambiguous: offline-corpus is both L0 and L1. Resolve the half we
        # know and leave the other None rather than choosing one.
        return Lane(None, lowered, None)

    return UNKNOWN_LANE


def forbidden_for(run_kind: str | None) -> frozenset[str]:
    """Input run_kinds a dataset of this run_kind may not consume."""
    if run_kind is None:
        return frozenset()
    return FORBIDDEN_INPUT_LANES.get(run_kind, frozenset())


@dataclass(frozen=True)
class LaneConflict:
    """One RF3 conflict, fully diagnosable."""
    subject: str            # what carried the offending lane
    dataset_lane: Lane
    input_lane: Lane
    detail: str


def check_lane_conflicts(
    declaration: dict,
    input_lanes: dict[str, object] | None = None,
) -> tuple[list[LaneConflict], list[str]]:
    """RF3. Returns `(conflicts, not_evaluable)`.

    Two sources are checked, in this order:

    1. **Declaration-internal** — the dataset's own `lane` against
       `evidence_references.required_lane`. A confirmatory dataset that
       *requires* discovery-lane evidence is self-evidently contaminated and is
       refusable from the declaration alone, before any resolver is consulted.

    2. **Resolved inputs** — `input_lanes` maps a reference to the lane it
       actually resolved to. Catches the case where the declaration's stated
       requirement and the input's real lane disagree.

    `not_evaluable` names each check that could not run because a lane's
    `run_kind` half was not determinable. It is never silently treated as a
    pass.
    """
    conflicts: list[LaneConflict] = []
    not_evaluable: list[str] = []

    dataset_lane = resolve_lane(declaration.get("lane"))
    if dataset_lane.run_kind is None:
        not_evaluable.append(
            f"dataset lane {declaration.get('lane')!r} does not determine a "
            f"run_kind ({dataset_lane}); RF3 cannot be evaluated"
        )
        return conflicts, not_evaluable

    forbidden = forbidden_for(dataset_lane.run_kind)
    if not forbidden:
        return conflicts, not_evaluable

    evidence = declaration.get("evidence_references")
    if isinstance(evidence, dict):
        required = resolve_lane(evidence.get("required_lane"))
        if required.run_kind is None:
            not_evaluable.append(
                f"evidence_references.required_lane "
                f"{evidence.get('required_lane')!r} does not determine a "
                f"run_kind ({required}); RF3 cannot be evaluated for it"
            )
        elif required.run_kind in forbidden:
            conflicts.append(LaneConflict(
                subject="evidence_references.required_lane",
                dataset_lane=dataset_lane,
                input_lane=required,
                detail=(
                    f"a {dataset_lane.run_kind} dataset (lane {dataset_lane}) "
                    f"requires {required.run_kind}-lane evidence (lane "
                    f"{required}). The {dataset_lane.run_kind} lane would be "
                    f"contaminated by the lane it exists to confirm "
                    f"(§8.1 RF3, IQROS-P6, PS4)"
                ),
            ))

    for reference, expression in sorted((input_lanes or {}).items()):
        lane = resolve_lane(expression)
        if lane.run_kind is None:
            not_evaluable.append(
                f"input {reference!r} lane {expression!r} does not determine a "
                f"run_kind ({lane}); RF3 cannot be evaluated for it"
            )
        elif lane.run_kind in forbidden:
            conflicts.append(LaneConflict(
                subject=reference,
                dataset_lane=dataset_lane,
                input_lane=lane,
                detail=(
                    f"a {dataset_lane.run_kind} dataset (lane {dataset_lane}) "
                    f"resolves input {reference!r} from lane {lane}. "
                    f"{lane.run_kind}-lane inputs may not feed a "
                    f"{dataset_lane.run_kind} dataset (§8.1 RF3, IQROS-P6, PS4)"
                ),
            ))

    return conflicts, not_evaluable
