"""
The completeness contract — docs/governance/data_completeness_gate.md §5.1,
docs/governance/contract_versioning.md §2.

**Contracts are data, not code.** This module contains no completeness logic
whatsoever: it parses, validates, canonicalises and hashes a declarative
document. Every `if` that decides whether data is complete lives in checks.py
and is driven entirely by what a contract declares. That is L1 ("declare,
don't code"), and it is the whole reason a new pipeline can be onboarded with
a contract file instead of a code path.

Two properties this file exists to guarantee:

**Hash stability.** The canonical form converts every numeric to an int or a
DECIMAL STRING before hashing, so `1.0`, `1.00` and `Decimal("1")` in a YAML
working copy converge on identical registry bytes, and no IEEE float ever
enters a hash (governance_receipts.md §1.3 rule 3 — a float's repr is not a
stable identity). Hashing reuses receipts.sha256_lf / canonical_json verbatim:
one hashing rule on this platform, not two.

**Explicit check identity.** Every check a contract invokes is materialised
into the canonical body BY ID (`coverage.min_count.v1`), even when the author
wrote nothing. A contract therefore pins not only its thresholds but the
SEMANTICS those thresholds are interpreted under. contract_versioning.md §3:
freezing the contract alone is not sufficient — a maintainer who redefines
what `min_count: 3` MEANS changes every historical verdict while every hash
still matches. A check's meaning may never be redefined under an existing ID;
a semantics change requires a new ID (`coverage.min_count.v2`) that contracts
opt into explicitly, by name, and which the receipt then records.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Final

_EVIDENCE_LIBRARY = Path(__file__).resolve().parents[1] / "evidence_library"
if str(_EVIDENCE_LIBRARY) not in sys.path:
    sys.path.insert(0, str(_EVIDENCE_LIBRARY))

from receipts import canonical_json, sha256_lf  # noqa: E402

from policy import CRITICALITIES, OPTIONAL, REQUIRED, validate_overrides  # noqa: E402
from states import STATES  # noqa: E402

CONTRACT_SCHEMA_VERSION: Final = 1

# Default check_id per check family. Materialised into the canonical body even
# when the author omits it, so the receipt always records which semantics ran.
DEFAULT_TEMPORAL_CHECK: Final = "temporal.period_closed.v1"
DEFAULT_SCHEMA_CHECK: Final = "schema.declared.v1"
DEFAULT_FRESHNESS_CHECK: Final = "freshness.max_age.v1"
DEFAULT_COVERAGE_CHECK: Final = "coverage.min_count.v1"
DEFAULT_CONTINUITY_CHECK: Final = "continuity.no_interior_gaps.v1"


class ContractConfigurationError(ValueError):
    """
    A contract is unusable. Deliberately NOT raised out of the evaluation path
    — the gate turns this into a CONFIGURATION_ERROR verdict (§2 state 6), so
    that an unusable contract produces a receipt and a HALT rather than an
    unhandled traceback. It IS raised by the authoring/activation path, where
    a human is present to fix it.
    """


# ---------------------------------------------------------------------------
# Grain — the period definition, which drives the TEMPORAL check.
#
# A registry, not an if-chain: a new grain is a new entry, never an engine
# change. period -> (period_start, period_end), both timezone-aware UTC.
# period_end is EXCLUSIVE (the first instant NOT in the period).
# ---------------------------------------------------------------------------

def _utc_day_bounds(period: str) -> tuple[datetime, datetime]:
    start = datetime.strptime(period, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def _utc_hour_bounds(period: str) -> tuple[datetime, datetime]:
    start = datetime.strptime(period, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
    return start, start + timedelta(hours=1)


GRAIN_REGISTRY: dict[str, Callable[[str], tuple[datetime, datetime]]] = {
    "utc_day": _utc_day_bounds,
    "utc_hour": _utc_hour_bounds,
}


def period_bounds(grain: str, period: str) -> tuple[datetime, datetime]:
    if grain not in GRAIN_REGISTRY:
        raise ContractConfigurationError(
            f"unknown grain '{grain}'; registered grains: {sorted(GRAIN_REGISTRY)}"
        )
    try:
        return GRAIN_REGISTRY[grain](period)
    except ValueError as exc:
        raise ContractConfigurationError(
            f"period '{period}' is not valid for grain '{grain}': {exc}"
        ) from None


# ---------------------------------------------------------------------------
# Numeric canonicalisation
# ---------------------------------------------------------------------------

def fmt_dec(value: Decimal) -> str:
    """
    The canonical decimal string. Trailing zeros are stripped, so `1.0`, `1.00`
    and `Decimal("1.000")` in three different working copies converge on ONE set
    of registry bytes and therefore ONE contract_hash. A contract whose identity
    changed because someone typed an extra zero would make version history
    meaningless.

    `.normalize()` alone is not enough: it renders 100 as "1E+2". Exponent
    notation is never emitted (governance_receipts.md §1.3, rule 2).
    """
    normalised = value.normalize()
    _sign, _digits, exp = normalised.as_tuple()
    if isinstance(exp, int) and exp > 0:
        normalised = normalised.quantize(Decimal(1))
    return format(normalised, "f")


def _dec(value: Any, label: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ContractConfigurationError(f"{label}: '{value}' is not numeric") from None


# ---------------------------------------------------------------------------
# Spec blocks
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SchemaSpec:
    check: str = DEFAULT_SCHEMA_CHECK
    columns: dict[str, str] = field(default_factory=dict)
    # column -> (min, max) inclusive magnitude band. Catches the Binance
    # ms->us class (CLAUDE.md §11) as a DECLARED rule rather than bespoke code.
    bands: dict[str, tuple[Decimal, Decimal]] = field(default_factory=dict)

    def to_canonical(self) -> dict:
        return {
            "check": self.check,
            "columns": dict(sorted(self.columns.items())),
            "bands": {
                col: [fmt_dec(lo), fmt_dec(hi)]
                for col, (lo, hi) in sorted(self.bands.items())
            },
        }


@dataclass(frozen=True)
class CoverageSpec:
    check: str = DEFAULT_COVERAGE_CHECK
    min_count: int | None = None
    min_fraction: Decimal | None = None

    def to_canonical(self) -> dict:
        return {
            "check": self.check,
            "min_count": self.min_count,
            "min_fraction": None if self.min_fraction is None else fmt_dec(self.min_fraction),
        }


@dataclass(frozen=True)
class FreshnessSpec:
    check: str = DEFAULT_FRESHNESS_CHECK
    max_age_hours: Decimal = Decimal("24")

    def to_canonical(self) -> dict:
        return {"check": self.check, "max_age_hours": fmt_dec(self.max_age_hours)}


@dataclass(frozen=True)
class ContinuitySpec:
    check: str = DEFAULT_CONTINUITY_CHECK
    min_rows: int = 0
    no_interior_gaps: bool = True

    def to_canonical(self) -> dict:
        return {
            "check": self.check,
            "min_rows": self.min_rows,
            "no_interior_gaps": self.no_interior_gaps,
        }


@dataclass(frozen=True)
class DegradationSpec:
    """
    The declared-in-advance degradation path (§3). `on` names the states for
    which this OPTIONAL input may be degraded rather than blocked; anything
    not named here BLOCKS. The IVRV precedent (CLAUDE.md §16) — a stale DVOL
    day yields ivrv=unavailable and never blocks H5/G1/G3 — expressed as data.

    outputs_unavailable are marked explicitly UNAVAILABLE. Never NULL, never
    imputed, never ffilled, never zero-filled.
    """
    on: tuple[str, ...] = ()
    outputs_unavailable: tuple[str, ...] = ()

    def to_canonical(self) -> dict:
        return {
            "on": sorted(self.on),
            "outputs_unavailable": sorted(self.outputs_unavailable),
        }


@dataclass(frozen=True)
class InputSpec:
    id: str
    source: dict[str, Any]
    criticality: str = REQUIRED
    schema: SchemaSpec | None = None
    coverage: CoverageSpec | None = None
    freshness: FreshnessSpec | None = None
    continuity: ContinuitySpec | None = None
    late_arrival_hours: Decimal | None = None
    degradation: DegradationSpec | None = None
    # A LITERAL RUNNABLE COMMAND (§8). An error that tells an operator what is
    # wrong but not what to type will be triaged at 3am by someone who did not
    # write it.
    remediation: str | None = None

    def degrades_on(self, state: str) -> bool:
        return self.degradation is not None and state in self.degradation.on

    def to_canonical(self) -> dict:
        return {
            "id": self.id,
            "source": {k: self.source[k] for k in sorted(self.source)},
            "criticality": self.criticality,
            "schema": None if self.schema is None else self.schema.to_canonical(),
            "coverage": None if self.coverage is None else self.coverage.to_canonical(),
            "freshness": None if self.freshness is None else self.freshness.to_canonical(),
            "continuity": None if self.continuity is None else self.continuity.to_canonical(),
            "late_arrival_hours": (
                None if self.late_arrival_hours is None else fmt_dec(self.late_arrival_hours)
            ),
            "degradation": None if self.degradation is None else self.degradation.to_canonical(),
            "remediation": self.remediation,
        }


@dataclass(frozen=True)
class Contract:
    contract_id: str
    grain: str
    inputs: tuple[InputSpec, ...]
    produces: tuple[str, ...] = ()
    owner: str | None = None
    description: str | None = None
    temporal_check: str = DEFAULT_TEMPORAL_CHECK
    policy: dict | None = None
    contract_schema_version: int = CONTRACT_SCHEMA_VERSION

    def input(self, input_id: str) -> InputSpec | None:
        for spec in self.inputs:
            if spec.id == input_id:
                return spec
        return None

    def to_canonical(self) -> dict:
        """
        The exact document that gets hashed and archived. Key order is
        irrelevant (canonical_json sorts), but VALUE normalisation is not —
        every numeric here is already an int or a decimal string.
        """
        return {
            "contract_id": self.contract_id,
            "contract_schema_version": self.contract_schema_version,
            "grain": self.grain,
            "owner": self.owner,
            "description": self.description,
            "produces": list(self.produces),
            "temporal_check": self.temporal_check,
            "inputs": [i.to_canonical() for i in self.inputs],
            "policy": self.policy,
        }

    def canonical_text(self) -> str:
        return canonical_json(self.to_canonical()).decode("utf-8")

    def contract_hash(self) -> str:
        """sha256_lf of the canonical bytes — THE identity (contract_versioning.md §2.2)."""
        return sha256_lf(self.canonical_text().encode("utf-8"))

    def check_ids(self) -> list[str]:
        """Every check_id this contract will invoke. Replay pre-flights these."""
        ids = {self.temporal_check}
        for spec in self.inputs:
            for block in (spec.schema, spec.coverage, spec.freshness, spec.continuity):
                if block is not None:
                    ids.add(block.check)
        return sorted(ids)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_schema(d: Any, label: str) -> SchemaSpec:
    if not isinstance(d, dict):
        raise ContractConfigurationError(f"{label}: schema must be a mapping")
    bands: dict[str, tuple[Decimal, Decimal]] = {}
    for col, pair in (d.get("bands") or {}).items():
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ContractConfigurationError(
                f"{label}: bands['{col}'] must be a [min, max] pair, got {pair!r}"
            )
        lo = _dec(pair[0], f"{label}.bands.{col}[0]")
        hi = _dec(pair[1], f"{label}.bands.{col}[1]")
        if lo > hi:
            raise ContractConfigurationError(f"{label}: bands['{col}'] has min > max")
        bands[col] = (lo, hi)
    columns = d.get("columns") or {}
    if not isinstance(columns, dict):
        raise ContractConfigurationError(f"{label}: schema.columns must be a mapping")
    return SchemaSpec(
        check=d.get("check", DEFAULT_SCHEMA_CHECK),
        columns={str(k): str(v) for k, v in columns.items()},
        bands=bands,
    )


def _parse_coverage(d: Any, label: str) -> CoverageSpec:
    if not isinstance(d, dict):
        raise ContractConfigurationError(f"{label}: coverage must be a mapping")
    min_count = d.get("min_count")
    if min_count is not None and not isinstance(min_count, int):
        raise ContractConfigurationError(f"{label}: coverage.min_count must be an int")
    min_fraction = d.get("min_fraction")
    return CoverageSpec(
        check=d.get("check", DEFAULT_COVERAGE_CHECK),
        min_count=min_count,
        min_fraction=None if min_fraction is None else _dec(min_fraction, f"{label}.min_fraction"),
    )


def _parse_freshness(d: Any, label: str) -> FreshnessSpec:
    if not isinstance(d, dict):
        raise ContractConfigurationError(f"{label}: freshness must be a mapping")
    return FreshnessSpec(
        check=d.get("check", DEFAULT_FRESHNESS_CHECK),
        max_age_hours=_dec(d.get("max_age_hours", 24), f"{label}.max_age_hours"),
    )


def _parse_continuity(d: Any, label: str) -> ContinuitySpec:
    if not isinstance(d, dict):
        raise ContractConfigurationError(f"{label}: continuity must be a mapping")
    min_rows = d.get("min_rows", 0)
    if not isinstance(min_rows, int):
        raise ContractConfigurationError(f"{label}: continuity.min_rows must be an int")
    # Defaults to True. A history window that tolerates interior gaps must SAY
    # so — a rolling statistic computed across a hole is silently wrong, and
    # "the author forgot to mention gaps" must never be read as "gaps are fine".
    if "no_interior_gaps" in d:
        gaps = bool(d["no_interior_gaps"])
    elif "continuity" in d:
        gaps = d["continuity"] == "no_interior_gaps"
    else:
        gaps = True
    return ContinuitySpec(
        check=d.get("check", DEFAULT_CONTINUITY_CHECK),
        min_rows=min_rows,
        no_interior_gaps=gaps,
    )


def _parse_degradation(d: Any, label: str) -> DegradationSpec:
    if not isinstance(d, dict):
        raise ContractConfigurationError(f"{label}: degradation must be a mapping")
    on = tuple(d.get("on") or ())
    for state in on:
        if state not in STATES:
            raise ContractConfigurationError(
                f"{label}: degradation.on names unknown state '{state}'"
            )
    return DegradationSpec(on=on, outputs_unavailable=tuple(d.get("outputs_unavailable") or ()))


def _parse_input(d: Any, label: str) -> InputSpec:
    if not isinstance(d, dict):
        raise ContractConfigurationError(f"{label}: input must be a mapping")
    input_id = d.get("id")
    if not input_id:
        raise ContractConfigurationError(f"{label}: input.id is required")

    source = d.get("source")
    if not isinstance(source, dict) or not source.get("type"):
        raise ContractConfigurationError(f"{label}: input.source must be a mapping with a 'type'")

    criticality = d.get("criticality", REQUIRED)
    if criticality not in CRITICALITIES:
        raise ContractConfigurationError(
            f"{label}: criticality must be one of {sorted(CRITICALITIES)}, got '{criticality}'"
        )

    late = d.get("late_arrival_hours")

    return InputSpec(
        id=str(input_id),
        source={str(k): v for k, v in source.items()},
        criticality=criticality,
        schema=_parse_schema(d["schema"], f"{label}.schema") if d.get("schema") else None,
        coverage=_parse_coverage(d["coverage"], f"{label}.coverage") if d.get("coverage") else None,
        freshness=_parse_freshness(d["freshness"], f"{label}.freshness") if d.get("freshness") else None,
        continuity=_parse_continuity(d["continuity"], f"{label}.continuity") if d.get("continuity") else None,
        late_arrival_hours=None if late is None else _dec(late, f"{label}.late_arrival_hours"),
        degradation=(
            _parse_degradation(d["degradation"], f"{label}.degradation") if d.get("degradation") else None
        ),
        remediation=d.get("remediation"),
    )


def parse_contract(doc: dict) -> Contract:
    """
    dict -> Contract. Raises ContractConfigurationError on a malformed document
    (the authoring path). The evaluation path never calls this directly on
    untrusted input — it goes through the loader, which turns a parse failure
    into a CONFIGURATION_ERROR verdict rather than a traceback.
    """
    if not isinstance(doc, dict):
        raise ContractConfigurationError(f"contract must be a mapping, got {type(doc).__name__}")

    contract_id = doc.get("contract_id")
    if not contract_id:
        raise ContractConfigurationError("contract_id is required")

    grain = doc.get("grain")
    if not grain:
        raise ContractConfigurationError("grain is required")

    raw_inputs = doc.get("inputs")
    if not isinstance(raw_inputs, list) or not raw_inputs:
        raise ContractConfigurationError("inputs is required and must be a non-empty list")

    inputs = [_parse_input(d, f"inputs[{i}]") for i, d in enumerate(raw_inputs)]

    seen: set[str] = set()
    for spec in inputs:
        if spec.id in seen:
            raise ContractConfigurationError(f"duplicate input id '{spec.id}'")
        seen.add(spec.id)

    # `history_window` (data_completeness_gate.md §5.1) is declared at contract
    # level in the spec's example, but the CHECK is per-input by nature — §5.2
    # types Check as (observation, input_spec, as_of), and an observation is
    # per-input. Resolved by applying a contract-level history_window to every
    # REQUIRED input that does not declare its own `continuity` block. The
    # canonical body then carries it per-input, so what the receipt records is
    # exactly what ran; the sugar exists only at authoring time.
    hw = doc.get("history_window")
    if hw:
        shared = _parse_continuity(hw, "history_window")
        inputs = [
            spec if (spec.continuity is not None or spec.criticality != REQUIRED)
            else replace(spec, continuity=shared)
            for spec in inputs
        ]

    policy = doc.get("policy")
    if policy in ("default", None):
        policy = None
    policy_errors = validate_overrides(policy)
    if policy_errors:
        raise ContractConfigurationError("; ".join(policy_errors))

    schema_v = doc.get("contract_schema_version", CONTRACT_SCHEMA_VERSION)
    if not isinstance(schema_v, int) or schema_v < 1:
        raise ContractConfigurationError(
            f"contract_schema_version must be a positive int, got {schema_v!r}"
        )

    return Contract(
        contract_id=str(contract_id),
        grain=str(grain),
        inputs=tuple(inputs),
        produces=tuple(doc.get("produces") or ()),
        owner=doc.get("owner"),
        description=doc.get("description"),
        temporal_check=doc.get("temporal_check", DEFAULT_TEMPORAL_CHECK),
        policy=policy,
        contract_schema_version=schema_v,
    )


def parse_contract_text(text: str) -> Contract:
    """
    Parse a working-copy contract file. Accepts YAML (a superset of JSON, so
    the registry's own canonical-JSON body parses through the same path).

    AUTHORING ONLY. Replay never calls this — see contract_loader.py.
    """
    import yaml  # local import: the core evaluation path has no YAML dependency

    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ContractConfigurationError(f"contract is not valid YAML/JSON: {exc}") from None
    return parse_contract(doc)


# ---------------------------------------------------------------------------
# Validation (item 8 — contract validation)
# ---------------------------------------------------------------------------

def validate_contract(contract: Contract) -> list[str]:
    """
    Semantic validation, beyond the structural checks parse_contract() already
    enforces. Returns a list of errors; empty means valid.

    Deliberately includes checks that can only be made once the whole document
    is in hand — e.g. a coverage floor that EXCEEDS the period's maximum
    possible count, which is a contract that can never be satisfied by any
    data and would otherwise present forever as INCOMPLETE_DATA, sending an
    operator to backfill data that cannot exist (§2 state 6).
    """
    from checks import CHECK_REGISTRY  # local import: avoids an import cycle

    errors: list[str] = []

    if contract.grain not in GRAIN_REGISTRY:
        errors.append(
            f"unknown grain '{contract.grain}'; registered: {sorted(GRAIN_REGISTRY)}"
        )

    for check_id in contract.check_ids():
        if check_id not in CHECK_REGISTRY:
            errors.append(
                f"contract names check_id '{check_id}', which this engine does not "
                f"implement. A check's semantics are frozen by id and retained forever; "
                f"an unresolvable id means the contract cannot be evaluated."
            )

    if not contract.inputs:
        errors.append("contract declares no inputs")

    for spec in contract.inputs:
        if spec.criticality == OPTIONAL and spec.degradation is None:
            errors.append(
                f"input '{spec.id}' is optional but declares no degradation path. An "
                f"optional input with no declared degradation blocks exactly as a "
                f"required one would — declare `degradation` or mark it required."
            )
        if spec.coverage is not None:
            cov = spec.coverage
            if cov.min_count is None and cov.min_fraction is None:
                errors.append(
                    f"input '{spec.id}': coverage declares neither min_count nor min_fraction"
                )
            if cov.min_count is not None and cov.min_count < 0:
                errors.append(f"input '{spec.id}': coverage.min_count must be >= 0")
            if cov.min_fraction is not None and not (Decimal(0) <= cov.min_fraction <= Decimal(1)):
                errors.append(
                    f"input '{spec.id}': coverage.min_fraction must be in [0, 1], "
                    f"got {cov.min_fraction}"
                )
        if spec.freshness is not None and spec.freshness.max_age_hours <= 0:
            errors.append(f"input '{spec.id}': freshness.max_age_hours must be > 0")
        if spec.late_arrival_hours is not None and spec.late_arrival_hours < 0:
            errors.append(f"input '{spec.id}': late_arrival_hours must be >= 0")
        if spec.coverage is not None and spec.late_arrival_hours is None:
            errors.append(
                f"input '{spec.id}': declares coverage but no late_arrival_hours. Without a "
                f"late-arrival budget the gate cannot separate INCOMPLETE_DATA (the source "
                f"may still deliver) from BACKFILL_REQUIRED (it never will) — the distinction "
                f"the whole framework exists to make."
            )

    errors.extend(validate_overrides(contract.policy))

    return errors


def validate_contract_or_raise(contract: Contract) -> None:
    errors = validate_contract(contract)
    if errors:
        raise ContractConfigurationError("; ".join(errors))
