"""
features.py
===========
Deterministic feature construction on top of the alignment layer.

    decision timestamp
      -> declared source requirements
      -> availability-safe aligned inputs (as-of, backward only)
      -> feature row, with per-input age_s and provenance
      -> explicit state when an input is missing, stale, or untimestampable

Read-only and pure: `build_row` performs no I/O and never reads a clock. Every
observation it uses is passed in. That is what makes offline and live parity
possible later -- a live caller supplies observations from its own in-memory
state, an offline caller from the loaders in `align`, and both run the identical
code over the identical contract.

STATES ARE NOT INVENTED HERE
----------------------------
Readiness reuses `research/dq`'s frozen vocabulary rather than a parallel one:
the eight severity-ordered states in `dq.states` and the (state, criticality)
matrix in `dq.policy`. That is deliberate. Those two modules are described in
their own docstring as "the entire safety argument", and a second, subtly
different set of statuses in the research tree would be exactly the second
source of truth this project keeps refusing to create.

The mapping this module is responsible for is only *observation -> state*:

    READY             a match inside tolerance, with a non-null value
    STALE_DATA        a match, but older than the declared tolerance
    INCOMPLETE_DATA   no observation available by the decision instant, or the
                      matched row's field is NULL
    UNKNOWN           rows exist but none can be placed in time -- the store
                      cannot say when anything arrived. Fails closed (L2),
                      because a gate that cannot tell must not say yes.
    CONFIGURATION_ERROR  the requirement names an unknown source, or one this
                      project declares dormant

An absent input is never NULL and never silently zero. It is a state.

WHAT THIS MODULE WILL NOT DO
----------------------------
It will not interpolate, forward-fill past the declared tolerance, or reach for
a different source to complete a row. A feature whose input is stale is a stale
feature, and the row says so. Nor will it substitute one venue's price for
another's to complete a row: the spot price embedded in the option feed and a
Binance bar are different measurements from different venues.

TOLERANCE IS MANDATORY AND EXPLICIT
-----------------------------------
Every requirement must state `tolerance_s`, or state `None` to mean deliberately
unbounded. There is no default, because the sensible bound differs by two orders
of magnitude across these sources -- the quote cycle drifts around 60s while a
signal observation is a day old by construction -- and a shared default would be
wrong for nearly all of them.
"""
from __future__ import annotations

import pathlib
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

_HERE = pathlib.Path(__file__).resolve().parent
_RESEARCH_ROOT = _HERE.parent
if str(_RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_ROOT))

from alignment.align import (  # noqa: E402
    Observation, SourceLoad, as_of_join, check_no_lookahead, observation_key,
)
from alignment.timebase import SOURCES, get_spec  # noqa: E402


def _load_dq() -> tuple[Any, Any]:
    """Load dq.states and dq.policy without leaving research/dq on sys.path.

    `research/dq` uses flat imports (`policy.py` does `from states import ...`),
    so the modules can only be loaded with that directory importable. The
    obvious fix -- inserting it into sys.path -- is what the test suite does and
    is exactly how `research/dq/engine.py` and `research/evidence/engine.py`
    came to collide on `sys.modules['engine']` (CLAUDE.md sec.18, Increment 6).
    Library code must not widen that. The directory is bound only for the
    duration of these two loads, and every sys.modules entry touched is
    restored, so importing this module changes no global import state.
    """
    import importlib.util

    dq_dir = _RESEARCH_ROOT / "dq"
    saved_path = list(sys.path)
    saved_modules = {n: sys.modules.get(n) for n in ("states", "policy")}
    sys.path.insert(0, str(dq_dir))
    try:
        loaded = {}
        for name in ("states", "policy"):
            spec = importlib.util.spec_from_file_location(name, dq_dir / f"{name}.py")
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            loaded[name] = module
        return loaded["states"], loaded["policy"]
    finally:
        sys.path[:] = saved_path
        for name, previous in saved_modules.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


states, policy = _load_dq()


class FeatureContractError(ValueError):
    """A malformed feature or input declaration."""


@dataclass(frozen=True)
class InputRequirement:
    """One input a feature needs, and how fresh it has to be."""

    name: str
    source: str
    field: str
    criticality: str = policy.REQUIRED
    tolerance_s: Optional[float] = None
    key_field: Optional[str] = None
    key: Any = None
    degradation_declared: bool = False
    #: A hashed, human-readable statement of what this input requires to count
    #: as present, beyond being non-NULL. Declarative only: the condition is
    #: ENFORCED where it can be, which for a reduction is inside the reduction
    #: -- the feature layer sees one value and cannot test the legs behind it.
    #: Declaring it here is what moves the contract hash when the rule changes,
    #: so a validity change cannot silently reuse an existing version.
    validity: Optional[str] = None

    def __post_init__(self) -> None:
        if self.criticality not in policy.CRITICALITIES:
            raise FeatureContractError(
                f"{self.name}: criticality must be required|optional")
        if self.tolerance_s is not None and self.tolerance_s < 0:
            raise FeatureContractError(f"{self.name}: negative tolerance")
        if self.key_field is not None and self.key is None:
            raise FeatureContractError(
                f"{self.name}: key_field {self.key_field!r} given without a key; "
                "a panel source must name the member it means")


@dataclass(frozen=True)
class FeatureSpec:
    """A feature: what it needs, and a pure function of those values."""

    name: str
    inputs: tuple[InputRequirement, ...]
    compute: Callable[[Mapping[str, Any]], Any]
    description: str = ""

    def __post_init__(self) -> None:
        if not self.inputs:
            raise FeatureContractError(f"{self.name}: declares no inputs")
        seen = [i.name for i in self.inputs]
        if len(set(seen)) != len(seen):
            raise FeatureContractError(f"{self.name}: duplicate input names")


@dataclass(frozen=True)
class InputRecord:
    """What was resolved for one input, and everything needed to audit it."""

    name: str
    source: str
    state: str
    action: str
    value: Any
    event_ts: Optional[datetime]
    available_ts: Optional[datetime]
    age_s: Optional[float]
    tolerance_s: Optional[float]
    criticality: str
    provenance: dict[str, Any] = field(default_factory=dict)
    detail: str = ""


@dataclass(frozen=True)
class FeatureValue:
    name: str
    value: Any
    state: str
    action: str
    inputs: tuple[InputRecord, ...]

    @property
    def available(self) -> bool:
        return self.action == policy.COMPUTE and self.value is not None


@dataclass(frozen=True)
class FeatureRow:
    decision_ts: datetime
    features: dict[str, FeatureValue]
    state: str
    action: str

    def values(self) -> dict[str, Any]:
        """Only the features that were actually computed. Absent means absent."""
        return {n: f.value for n, f in self.features.items() if f.available}

    def unavailable(self) -> dict[str, str]:
        return {n: f.state for n, f in self.features.items() if not f.available}


def _resolve_input(decision_ts: datetime, req: InputRequirement,
                   loads: Mapping[str, SourceLoad]) -> InputRecord:
    def rec(state: str, *, value=None, event_ts=None, available_ts=None,
            age_s=None, provenance=None, detail="") -> InputRecord:
        action = policy.resolve(state, req.criticality, req.degradation_declared)
        return InputRecord(
            name=req.name, source=req.source, state=state, action=action,
            value=value, event_ts=event_ts, available_ts=available_ts,
            age_s=age_s, tolerance_s=req.tolerance_s,
            criticality=req.criticality, provenance=dict(provenance or {}),
            detail=detail)

    if req.source not in SOURCES:
        return rec(states.CONFIGURATION_ERROR,
                   detail=f"unknown source {req.source!r}")
    spec = get_spec(req.source)
    if not spec.populated:
        return rec(states.CONFIGURATION_ERROR,
                   detail=f"{req.source} is declared dormant: {spec.notes}")

    load = loads.get(req.source)
    if load is None:
        return rec(states.INCOMPLETE_DATA, detail="source not supplied")
    if load.unusable_clock:
        # Rows exist, none can be placed in time. Never date them from the
        # event clock -- that is the substitution this layer exists to refuse.
        return rec(states.UNKNOWN,
                   detail=f"{load.rows_without_availability} of "
                          f"{load.rows_seen} rows have no usable arrival time")

    obs: Sequence[Observation] = load.observations
    if req.key_field is not None:
        obs = [o for o in obs if observation_key(o, req.key_field) == req.key]

    joined = as_of_join([decision_ts], obs, tolerance_s=None)[0]
    if not joined["matched"]:
        return rec(states.INCOMPLETE_DATA,
                   detail="no observation available at the decision instant")

    age = joined["age_s"]
    prov = joined["provenance"]
    if req.tolerance_s is not None and age is not None and age > req.tolerance_s:
        # Reported, never forward-filled: the value exists, it is simply too
        # old to stand for the decision instant.
        return rec(states.STALE_DATA, event_ts=joined["event_ts"],
                   available_ts=joined["available_ts"], age_s=age,
                   provenance=prov,
                   detail=f"age {age:.1f}s exceeds tolerance {req.tolerance_s:g}s")

    value = joined["payload"].get(req.field)
    if value is None:
        return rec(states.INCOMPLETE_DATA, event_ts=joined["event_ts"],
                   available_ts=joined["available_ts"], age_s=age,
                   provenance=prov,
                   detail=f"field {req.field!r} is NULL on the matched row")

    return rec(states.READY, value=value, event_ts=joined["event_ts"],
               available_ts=joined["available_ts"], age_s=age, provenance=prov)


def build_feature(decision_ts: datetime, spec: FeatureSpec,
                  loads: Mapping[str, SourceLoad]) -> FeatureValue:
    records = tuple(_resolve_input(decision_ts, r, loads) for r in spec.inputs)
    state = states.worst(r.state for r in records)
    action = states.worst_action(r.action for r in records)

    value = None
    if action == policy.COMPUTE:
        try:
            value = spec.compute({r.name: r.value for r in records})
        except Exception as exc:  # noqa: BLE001 -- a bad compute is a bad row, not a crash
            return FeatureValue(spec.name, None, states.CONFIGURATION_ERROR,
                                policy.HALT, records + (InputRecord(
                                    name="__compute__", source="",
                                    state=states.CONFIGURATION_ERROR,
                                    action=policy.HALT, value=None, event_ts=None,
                                    available_ts=None, age_s=None, tolerance_s=None,
                                    criticality=policy.REQUIRED,
                                    detail=f"{type(exc).__name__}: {exc}"),))
    return FeatureValue(spec.name, value, state, action, records)


def build_row(decision_ts: datetime, specs: Sequence[FeatureSpec],
              loads: Mapping[str, SourceLoad],
              verify_no_lookahead: bool = True) -> FeatureRow:
    """One feature row for one decision instant.

    Pure: no I/O, no clock. `verify_no_lookahead` re-runs the independent
    checker from `align` over every resolved input, so a look-ahead cannot
    reach a feature row even if the join that produced it were wrong.
    """
    features = {s.name: build_feature(decision_ts, s, loads) for s in specs}

    if verify_no_lookahead:
        rows = [{"decision_ts": decision_ts, "matched": r.available_ts is not None,
                 "available_ts": r.available_ts, "age_s": r.age_s}
                for f in features.values() for r in f.inputs]
        violations = check_no_lookahead(rows)
        if violations:
            offenders = {r.name for f in features.values() for r in f.inputs
                         if r.available_ts is not None and r.available_ts > decision_ts}
            raise AssertionError(
                f"look-ahead in feature inputs {sorted(offenders)}: "
                f"{violations[0]['reason']}")

    return FeatureRow(
        decision_ts=decision_ts,
        features=features,
        state=states.worst(f.state for f in features.values()),
        action=states.worst_action(f.action for f in features.values()),
    )


def build_rows(decision_times: Sequence[datetime], specs: Sequence[FeatureSpec],
               loads: Mapping[str, SourceLoad]) -> list[FeatureRow]:
    return [build_row(t, specs, loads) for t in decision_times]


def coverage(rows: Sequence[FeatureRow]) -> dict[str, Any]:
    """Per-feature availability and state counts over a set of rows."""
    if not rows:
        return {"rows": 0, "features": {}}
    out: dict[str, Any] = {"rows": len(rows), "features": {}}
    for name in sorted(rows[0].features):
        vals = [r.features[name] for r in rows]
        by_state: dict[str, int] = {}
        for v in vals:
            by_state[v.state] = by_state.get(v.state, 0) + 1
        ages = [i.age_s for v in vals for i in v.inputs
                if i.age_s is not None and i.state == states.READY]
        entry: dict[str, Any] = {
            "available": sum(1 for v in vals if v.available),
            "availability_rate": sum(1 for v in vals if v.available) / len(vals),
            "states": dict(sorted(by_state.items())),
        }
        if ages:
            s = sorted(ages)
            entry["input_age_s"] = {"min": s[0], "median": s[len(s) // 2],
                                    "max": s[-1]}
        out["features"][name] = entry
    return out


# ---------------------------------------------------------------------------
# The starter feature set: observed quantities and two mechanical ratios.
# No indicator is invented here -- every value is either a field the collector
# already records, or arithmetic over fields from a single observation.
# ---------------------------------------------------------------------------

def _identity(key: str) -> Callable[[Mapping[str, Any]], Any]:
    return lambda v: v[key]


QUOTE_TOLERANCE_S = 300.0
SIGNAL_TOLERANCE_S = 172_800.0


def starter_features(signal_id: str) -> list[FeatureSpec]:
    """Four features over sources this project already collects (contract v3).

    v3 removed `market_close_1m`; `REMOVED_IN_V3` records why. Removing it moved
    no other feature's contract hash -- each spec is hashed on its own.

    v1 took an `instrument_id` and keyed three option features to it. That is
    unresolvable across dates -- no option instrument survives more than ~3 days
    in a universe that rolls daily -- and the arbitrary pick also misreported
    open interest by two orders of magnitude (0 against an ATM 1,900-2,510).
    The option series are now panel reductions resolved per cycle by
    `alignment.option_panel`, so no instrument id enters the contract.

    Two v1 features are gone rather than re-specified:

    - `option_oi_contracts`. No consumer anywhere in the research tree, and the
      stated intent (CLAUDE.md sec.6, "OI walls at round strikes") is a
      concentration measure across strikes, which neither a single instrument
      nor a chain total expresses. sec.11 also holds that OI-based work cannot
      be validated on testnet at all.
    - `quote_spread_bps`. It measured an option's book while the strategy trades
      the BTCUSD perp, had no consumer, and real execution cost is already
      measured on the traded instrument by
      `execution_evidence.completed_trades` as entry/exit slippage against the
      actual order.

    Re-adding either is a new feature needing its own justification, not a
    restoration; `REMOVED_IN_V2` records that so it cannot happen silently.

    `consumed_direction` was added later, alongside
    `signal_position` rather than in place of it. `signal_position` is the
    latest DAILY signal; the live path consumes the direction currently HELD,
    and the collector's no-early-exit hold lets the two drift apart on 10.6% of
    held-days. Both are correct answers to different questions, so both are
    carried; `alignment.forward_trades` explains the reduction and why closure
    is resolved from `run_log`. `signal_position`'s contract is untouched, so
    its hash and every row already persisted under it stand unchanged.
    """
    return [
        FeatureSpec(
            name="spot_price",
            inputs=(InputRequirement(
                name="spot", source="spot_index", field="spot_price",
                tolerance_s=QUOTE_TOLERANCE_S),),
            compute=_identity("spot"),
            description="BTC index: median spot_price across the snapshot cycle.",
        ),
        FeatureSpec(
            name="option_atm_iv",
            inputs=(InputRequirement(
                name="iv", source="atm_iv", field="atm_iv",
                tolerance_s=QUOTE_TOLERANCE_S,
                validity="both ATM legs quote mark_iv > 0"),),
            compute=_identity("iv"),
            description="Nearest-expiry ATM call/put mark_iv mid. Front-end "
                        "implied vol; not the vol-state feature, which is DVOL.",
        ),
        FeatureSpec(
            name="signal_position",
            inputs=(InputRequirement(
                name="pos", source="signal_log", field="position",
                key_field="signal_id", key=signal_id,
                tolerance_s=SIGNAL_TOLERANCE_S),),
            compute=_identity("pos"),
            description="The signal family's position as of the decision.",
        ),
        FeatureSpec(
            name="consumed_direction",
            inputs=(InputRequirement(
                name="held", source="held_direction", field="direction",
                key_field="signal_id", key=signal_id,
                tolerance_s=SIGNAL_TOLERANCE_S),),
            compute=_identity("held"),
            description="The direction actually held at the decision instant, "
                        "i.e. what bridge.latest_direction() returns. 0.0 means "
                        "flat, which is a state, not a gap.",
        ),
    ]


#: Features that existed in contract v1 and were deliberately dropped in v2.
#: A guard test asserts none of these names reappears in `starter_features`.
REMOVED_IN_V2 = ("option_oi_contracts", "quote_spread_bps")

#: Dropped in v3. `market_close_1m` read the Parquet lake, which is refreshed
#: once a day at 00:05 UTC with the previous day's bars, while a live G3 entry is
#: decided in the hour after the 01:00 UTC collector commit. The bar it needs is
#: therefore never present, so it was INCOMPLETE_DATA at every live decision by
#: schedule rather than by market. Its declared availability is the venue's
#: publication time, so an offline rebuild of the same decision would resolve it
#: READY from bars the live path never had. It also duplicates `spot_price`: the
#: Delta index and the Binance close differ by about +/-2 bps. Re-adding it is a
#: new feature needing a source that is current at decision time.
REMOVED_IN_V3 = ("market_close_1m",)
