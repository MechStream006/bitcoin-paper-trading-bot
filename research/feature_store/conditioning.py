"""
Conditioning propagation (ADR-I010 §9.1, §9's Feature Metadata table;
engineering roadmap Phase 2, WBS 2.7 -- Conditioning Propagation Rule).

ADR-I010 §9.1: "Every value set carries a conditioning statement derived
from its inputs by the Specification's declared rule (IQROS-P12)":

    preserving       -- output conditioning unchanged from input
    narrowing        -- input conditioning plus the feature's own restriction
    cross-sectional  -- a universe statement replacing per-instrument
                        conditioning

"Conditioning is never dropped. A Specification that cannot state its
propagation rule cannot be registered."

WHAT ALREADY EXISTED (WBS 2.1/2.4), WHAT THIS MODULE ADDS. registry.py's
`conditioning_rule` field and `CONDITIONING_RULES` constant have existed
since WBS 2.1 -- Tier A already refuses a Specification whose
`conditioning_rule` is not one of the three named cases. WBS 2.4's
migration hardcoded `conditioning_rule="preserving"` for all 17 features.
What was missing -- exactly parallel to WBS 2.6's warm-up gap -- was the
PROPAGATION ITSELF: actually deriving an output conditioning statement
from a Specification's declared inputs, per its declared rule. This
module is that computation. It does not modify registry.py (whose
`conditioning_rule` field is unchanged and sufficient) or value_store.py
(whose docstring already scopes resolution-time concerns like this one to
a future consumer, exactly as it does for warm-up -- see warmup.py's own
docstring for the identical reasoning, not repeated here).

SOURCE CONDITIONING TEXT IS READ, NEVER RE-AUTHORED. The population
statement for each of this codebase's real ingest sources already exists,
written once by the ingest writer that produces it (ADR-I009 §13.2,
WBS 1.5/1.6): `research/ingest/dvol.py`'s `CONDITIONING`,
`research/ingest/funding.py`'s `CONDITIONING`, `research/ingest/
binance_ohlcv.py`'s `_conditioning(dataset)`. `_source_conditioning_map()`
below reads these directly (a live import, not a copied string) so a
future change to an ingest writer's own conditioning text is picked up
automatically, never silently drifting out of sync with this module's own
copy of it.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

RULES = frozenset({"preserving", "narrowing", "cross-sectional"})

_INGEST_DIR = Path(__file__).resolve().parents[1] / "ingest"


class ConditioningPropagationError(ValueError):
    """
    Raised when a conditioning rule is unrecognised, a required parameter
    for that rule is missing (a restriction for narrowing, a universe
    statement for cross-sectional), or an input's `source` has no known
    conditioning statement. Per ADR-I010 §9.1's own words -- "a
    Specification that cannot state its propagation rule cannot be
    registered" -- this is a registration precondition, not a warning.
    """


@dataclass(frozen=True)
class PropagatedConditioning:
    rule: str
    statement: str
    input_statements: tuple[str, ...]  # every distinct input conditioning consulted, for audit


def _source_conditioning_map() -> dict[str, str]:
    """
    Maps the symbolic `source` names used in Feature Specifications'
    `input_requirements` (e.g. "spot_1m") to the real, currently-recorded
    conditioning text for that source. Imports the real ingest writers
    directly rather than hardcoding a copy of their text.
    """
    # Appended, NEVER inserted at the front: research/ingest/calendar.py is a
    # real, unrelated module (WI-5 macro event calendar) that happens to
    # share its name with the Python stdlib's own `calendar` module.
    # sys.path.insert(0, ...) here would put research/ingest/ ahead of the
    # stdlib on sys.path, so ANY subsequent bare `import calendar` --
    # including ones deep inside pandas's own stdlib usage (_strptime.py) --
    # would silently resolve to the wrong module and break pandas for the
    # rest of the process (reproduced: pandas import itself fails with
    # AttributeError: module 'calendar' has no attribute 'day_abbr'
    # once research/ingest/ is inserted at position 0). Appending instead
    # guarantees the real stdlib module is always found first.
    if str(_INGEST_DIR) not in sys.path:
        sys.path.append(str(_INGEST_DIR))
    import dvol as _dvol  # noqa: PLC0415
    import funding as _funding  # noqa: PLC0415
    import binance_ohlcv as _binance  # noqa: PLC0415

    return {
        "spot_1m": _binance._conditioning("spot"),
        "perp_1m": _binance._conditioning("perp"),
        "funding_raw": _funding.CONDITIONING,
        "dvol_daily": _dvol.CONDITIONING,
    }


def propagate_conditioning(
    rule: str,
    input_conditionings: list[str],
    *,
    restriction: str | None = None,
    universe_statement: str | None = None,
) -> PropagatedConditioning:
    """
    ADR-I010 §9.1's three cases, generically:

      preserving      -- output = input conditioning(s), unchanged. With
                          more than one DISTINCT input statement, ALL are
                          carried forward (joined, deduplicated,
                          order-preserving) -- conditioning is never
                          dropped, so "preserving" over multiple genuinely
                          different inputs cannot silently pick just one.
      narrowing        -- output = input conditioning(s) PLUS the
                          feature's own stated `restriction` (required).
      cross-sectional  -- output = `universe_statement` (required),
                          REPLACING per-instrument conditioning entirely
                          -- but the replaced inputs are still recorded on
                          the result's `input_statements` for audit; only
                          the output STATEMENT drops them, never the
                          record of what was consulted.

    Raises ConditioningPropagationError for an unrecognised rule, a
    missing required parameter, or (preserving/narrowing) an empty
    `input_conditionings`.
    """
    if rule not in RULES:
        raise ConditioningPropagationError(
            f"unknown conditioning rule {rule!r}; must be one of {sorted(RULES)}"
        )

    distinct_inputs = tuple(dict.fromkeys(input_conditionings))  # dedupe, preserve order

    if rule in ("preserving", "narrowing") and not distinct_inputs:
        raise ConditioningPropagationError(
            f"{rule!r} requires at least one input conditioning statement"
        )

    if rule == "preserving":
        statement = distinct_inputs[0] if len(distinct_inputs) == 1 else " | ".join(distinct_inputs)
        return PropagatedConditioning(rule=rule, statement=statement, input_statements=distinct_inputs)

    if rule == "narrowing":
        if not restriction:
            raise ConditioningPropagationError("narrowing requires a non-empty `restriction`")
        base = distinct_inputs[0] if len(distinct_inputs) == 1 else " | ".join(distinct_inputs)
        statement = f"{base} -- FURTHER RESTRICTED: {restriction}"
        return PropagatedConditioning(rule=rule, statement=statement, input_statements=distinct_inputs)

    # rule == "cross-sectional"
    if not universe_statement:
        raise ConditioningPropagationError("cross-sectional requires a non-empty `universe_statement`")
    return PropagatedConditioning(rule=rule, statement=universe_statement, input_statements=distinct_inputs)


def conditioning_for_specification(
    conditioning_rule: str,
    input_requirements: list[dict],
    *,
    restriction: str | None = None,
    universe_statement: str | None = None,
    source_map: dict[str, str] | None = None,
) -> PropagatedConditioning:
    """
    Resolves a Specification's declared `input_requirements` (a list of
    {"source": ..., "fields": [...]} dicts, per registry.py's Tier A
    shape) to their real conditioning statements via
    `_source_conditioning_map()`, then propagates per `conditioning_rule`.

    Raises ConditioningPropagationError (never silently substitutes) if
    any declared source has no known conditioning statement -- this is
    the concrete meaning of ADR-I010 §9.1's "a Specification that cannot
    state its propagation rule cannot be registered" for this codebase's
    real, currently-known sources.
    """
    if source_map is None:
        source_map = _source_conditioning_map()

    input_conditionings = []
    for req in input_requirements:
        source = req["source"]
        if source not in source_map:
            raise ConditioningPropagationError(
                f"no known conditioning statement for source {source!r} -- "
                f"known sources: {sorted(source_map)}"
            )
        input_conditionings.append(source_map[source])

    return propagate_conditioning(
        conditioning_rule, input_conditionings,
        restriction=restriction, universe_statement=universe_statement,
    )
