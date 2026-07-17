"""
The generic evaluation engine — docs/governance/data_completeness_gate.md §4, §5.2.

`evaluate()` is a PURE, TOTAL function of (contract, observations, period, as_of).
No I/O. No clock. No pipeline-specific logic — grep this file for "corpus",
"funding", "h6", "spot", "dvol": there is nothing. Every threshold, floor,
band, budget and degradation path comes from the contract. That is L1
(declare, don't code), and it is what lets a new pipeline be onboarded with a
contract file and zero engine change.

Reproducibility guarantee, stated precisely (contract_versioning.md §3):

    (contract_hash, engine_version, check_ids, observations, as_of) -> verdict

is a pure, total, byte-stable function, replayable with NO access to the
original sources, forever.

## Check ordering: what short-circuits, and what deliberately does not

Checks run in the normative family order (§2): configuration, probe, schema,
temporal, freshness, coverage, continuity. Two short-circuit rules, and the
difference between them is load-bearing:

  * **CONFIGURATION_ERROR short-circuits EVERYTHING.** The contract is
    unusable, so nothing the gate might say about the data can be trusted.

  * **A severity->=5 state short-circuits THAT INPUT's remaining checks — not
    the other inputs'.** This is what kills the masquerade: schema runs before
    coverage, so a 0-row partition caused by the Binance ms->us unit switch
    surfaces as SCHEMA_MISMATCH (a code problem) and never as INCOMPLETE_DATA
    (which would send an operator to "backfill" the same broken month forever).

    Short-circuiting *globally* on the first >=5 state would be a defect, not
    an optimisation: input A's UNKNOWN probe (severity 7 -> BLOCK) would mask
    input B's SCHEMA_MISMATCH (severity 5 -> **HALT**), and the pipeline would
    BLOCK where it was required to HALT. Worst-wins on STATE does not imply
    worst-wins on ACTION, because the policy matrix is not monotonic in
    severity. Every input is therefore always evaluated.

## Aggregation

  state  = worst state across every check (worst-wins, §2).
  action = worst action across the per-input actions, by ACTION_PRECEDENCE.

An input's action is resolved from (its own worst state, its criticality,
whether the contract DECLARED a degradation path for that state). The temporal
check is contract-level and resolves as REQUIRED: a period that has not closed
has not closed for anybody.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Final

import policy
from checks import (
    CONFIGURATION_CHECK,
    CheckContext,
    CheckResult,
    check_configuration,
    check_probe,
    check_temporal,
    get_check,
)
from contract import (
    DEFAULT_CONTINUITY_CHECK,
    DEFAULT_COVERAGE_CHECK,
    DEFAULT_FRESHNESS_CHECK,
    DEFAULT_SCHEMA_CHECK,
    Contract,
)
from observation import InputObservation, observations_to_list
from states import (
    BACKFILL_REQUIRED,
    DEGRADE,
    DERIVING_ACTIONS,
    READY,
    UNKNOWN,
    is_short_circuiting,
    worst,
    worst_action,
)

# contract_versioning.md §11 PR-B: recorded in EVERY receipt, alongside
# contract_hash. Freezing the contract alone is not sufficient — a maintainer
# can still change what `min_count: 3` MEANS by editing checks.py. Bump this
# on any change to check semantics, and register the new semantics under a new
# check_id (never in place).
ENGINE_VERSION: Final = "dq.v1"


@dataclass(frozen=True)
class ReadinessVerdict:
    """
    The complete, self-describing result of one gate evaluation.

    `state` is NEVER None and NEVER NULL. Incompleteness is a VERDICT, not a
    value (§3, "What is explicitly forbidden"). A NULL that reaches a
    derivation is indistinguishable from a legitimate missing value — that is
    F2, and it is the difference between H6's funding query (which behaved
    correctly, dropping the day) and H6's derivation (which consumed the
    resulting NULL as if it were a number).
    """
    contract_id: str
    contract_hash: str
    contract_version: int | None
    engine_version: str
    feature_id: str
    period: str
    as_of: str
    state: str
    action: str
    checks: tuple[CheckResult, ...]
    input_states: dict[str, str]
    remediation: str | None = None
    outputs_unavailable: tuple[str, ...] = ()

    @property
    def may_derive(self) -> bool:
        """A derivation body is never entered unless this is True (§4)."""
        return self.action in DERIVING_ACTIONS

    @property
    def is_ready(self) -> bool:
        return self.state == READY

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "contract_hash": self.contract_hash,
            "contract_version": self.contract_version,
            "engine_version": self.engine_version,
            "feature_id": self.feature_id,
            "period": self.period,
            "as_of": self.as_of,
            "state": self.state,
            "action": self.action,
            "checks": [c.to_dict() for c in self.checks],
            "input_states": dict(sorted(self.input_states.items())),
            "remediation": self.remediation,
            "outputs_unavailable": list(self.outputs_unavailable),
        }


def normalise_as_of(as_of: str | datetime) -> datetime:
    """
    `now` is an argument, never a syscall (L6). This function is the only
    place the engine converts an as_of, and it never invents one — there is
    no default, and no call to datetime.now() anywhere in this package's
    evaluation path.
    """
    if isinstance(as_of, datetime):
        dt = as_of
    else:
        dt = datetime.fromisoformat(str(as_of).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def evaluate(
    contract: Contract,
    observations: dict[str, InputObservation],
    period: str,
    as_of: str | datetime,
    *,
    feature_id: str | None = None,
    contract_version: int | None = None,
    contract_hash: str | None = None,
) -> ReadinessVerdict:
    """
    The single entry point (§5.2, `Gate.evaluate`). Pure and total: it does not
    raise for bad data OR for a bad contract — a malformed contract becomes a
    CONFIGURATION_ERROR verdict and a failed probe becomes UNKNOWN, because a
    gate that dies with a traceback writes no receipt, and "a day nobody looked
    at must not look like a day that passed" (§6).

    The one thing it WILL raise for is an unresolvable check_id
    (checks.UnknownCheckError) — but validate_contract() reports that as a
    configuration error first, so evaluate() short-circuits to
    CONFIGURATION_ERROR before ever dispatching. Replay pre-flights the same
    ids explicitly and fails loudly there (contract_versioning.md §3).
    """
    as_of_dt = normalise_as_of(as_of)
    feature = feature_id or contract.contract_id
    chash = contract_hash or contract.contract_hash()

    def finish(checks: list[CheckResult], actions: list[str], input_states: dict[str, str]) -> ReadinessVerdict:
        state = worst(c.state for c in checks)
        action = worst_action(actions)
        return ReadinessVerdict(
            contract_id=contract.contract_id,
            contract_hash=chash,
            contract_version=contract_version,
            engine_version=ENGINE_VERSION,
            feature_id=feature,
            period=period,
            as_of=as_of_dt.isoformat(),
            state=state,
            action=action,
            checks=tuple(checks),
            input_states=input_states,
            remediation=_remediation(contract, input_states, action),
            outputs_unavailable=_outputs_unavailable(contract, input_states),
        )

    contract_ctx = CheckContext(contract=contract, period=period, as_of=as_of_dt)

    # 1. CONFIGURATION — short-circuits everything. An unusable contract makes
    #    every subsequent statement about the data untrustworthy.
    config = check_configuration(contract_ctx)
    if is_short_circuiting(config.state):
        action = policy.resolve(config.state, policy.REQUIRED, False, contract.policy)
        return finish([config], [action], {})

    checks: list[CheckResult] = [config]
    contexts = {
        spec.id: CheckContext(
            contract=contract, period=period, as_of=as_of_dt,
            spec=spec, observation=observations.get(spec.id),
        )
        for spec in contract.inputs
    }
    per_input: dict[str, list[CheckResult]] = {spec.id: [] for spec in contract.inputs}

    def settled(input_id: str) -> bool:
        """Has this input already produced a short-circuiting (>=5) state?"""
        return any(is_short_circuiting(r.state) for r in per_input[input_id])

    def run(input_id: str, result: CheckResult) -> None:
        per_input[input_id].append(result)
        checks.append(result)

    # 2. PROBE — could we look at all?
    for spec in contract.inputs:
        run(spec.id, check_probe(contexts[spec.id]))

    # 3. SCHEMA — is the data shaped as declared? (Before coverage. Always.)
    for spec in contract.inputs:
        if settled(spec.id):
            continue
        check_id = spec.schema.check if spec.schema else DEFAULT_SCHEMA_CHECK
        run(spec.id, get_check(check_id)(contexts[spec.id]))

    # 4. TEMPORAL — contract-level: the period has closed, or it has not.
    temporal = check_temporal(contract_ctx)
    checks.append(temporal)

    # 5-7. FRESHNESS, COVERAGE, CONTINUITY — per input, in order.
    for family, default_id in (
        ("freshness", DEFAULT_FRESHNESS_CHECK),
        ("coverage", DEFAULT_COVERAGE_CHECK),
        ("continuity", DEFAULT_CONTINUITY_CHECK),
    ):
        for spec in contract.inputs:
            if settled(spec.id):
                continue
            block = getattr(spec, family)
            check_id = block.check if block is not None else default_id
            run(spec.id, get_check(check_id)(contexts[spec.id]))

    # --- aggregate ---------------------------------------------------------
    input_states = {
        spec.id: worst(r.state for r in per_input[spec.id]) for spec in contract.inputs
    }

    actions = [
        # The temporal check is contract-level and resolves as REQUIRED: an
        # unfinished period is unfinished for every input, and no degradation
        # path can make a day that has not happened yet be complete.
        policy.resolve(temporal.state, policy.REQUIRED, False, contract.policy)
    ]
    for spec in contract.inputs:
        state = input_states[spec.id]
        actions.append(
            policy.resolve(state, spec.criticality, spec.degrades_on(state), contract.policy)
        )

    return finish(checks, actions, input_states)


def _remediation(contract: Contract, input_states: dict[str, str], action: str) -> str | None:
    """
    §8: a BACKFILL_REQUIRED error must carry a LITERAL RUNNABLE COMMAND. "An
    error that tells an operator what is wrong but not what to type will be
    triaged at 3am by someone who did not write it."
    """
    commands = [
        spec.remediation
        for spec in contract.inputs
        if input_states.get(spec.id) == BACKFILL_REQUIRED and spec.remediation
    ]
    if not commands:
        return None
    return "; ".join(commands)


def _outputs_unavailable(contract: Contract, input_states: dict[str, str]) -> tuple[str, ...]:
    """
    Outputs that a DEGRADE must mark explicitly UNAVAILABLE — never NULL,
    never imputed, never ffilled, never zero-filled (§3). The corpus rule
    already in force — "no DVOL -> NaN -> drop, never impute" (CLAUDE.md §11)
    — promoted from a note about DVOL to a declared, enforced contract term.
    """
    out: list[str] = []
    for spec in contract.inputs:
        state = input_states.get(spec.id)
        if state and state != READY and spec.degrades_on(state):
            out.extend(spec.degradation.outputs_unavailable)
    return tuple(sorted(set(out)))


def observations_hash(observations: dict[str, InputObservation]) -> str:
    """
    Content address of the exact observation set a verdict was rendered from.
    Committed inside the governance receipt, so a later mutation of the stored
    observations is detectable at replay rather than silently changing what a
    historical verdict "was computed from".
    """
    from receipts import canonical_json, sha256_lf

    return sha256_lf(canonical_json(observations_to_list(observations)))
