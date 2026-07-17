"""
The readiness rule provider — item 7.

## Relationship to PR-6's admissibility-rule architecture

PR-6 (`evidence_library/evidence_governance.py`) established a rule
architecture whose *contract* is four properties, not four lines of code:

    1. a rule is identified by (rule_id, engine_version);
    2. both are recorded verbatim in every receipt the rule produces;
    3. replay DISPATCHES on the recorded pair — it never executes "whatever
       the current implementation happens to be";
    4. an unresolvable historical rule FAILS LOUDLY. Never a silent
       substitution.

This module implements exactly those four properties for readiness, via
`RuleRegistry` — which is the mechanism generalised, not a second copy of it.
PR-6's `AdmissibilityRule` is typed to the Evidence Library's gate signature
`(family_meta, run_metrics, thresholds) -> (bool, gate_results)`; a readiness
rule is `(contract, observations, as_of) -> ReadinessVerdict`. The two rule
SHAPES are genuinely different, so one Protocol cannot serve both — but the
registry/dispatch/fail-loud MECHANISM is identical, and is written once here,
as a shape-agnostic generic.

**Known, deliberate, non-blocking divergence** (see the PR summary and the
schema reference's "Remaining work for PR-8"): PR-6's private `_RULE_REGISTRY`
still hand-rolls that mechanism inline. Collapsing it onto `RuleRegistry` is a
zero-behaviour-change refactor, but it edits the Evidence Library, which this
PR is explicitly forbidden to touch. It is therefore proposed, not performed.

## The substitution point, working in the intended direction

PR-6 built `AdmissibilityRule` *as a substitution point for exactly this
framework* ("This is the substitution point PR-7's Data Completeness Gate will
use (`register_rule(SomeNewRule())`)"). That substitution — an admissibility
rule whose verdict is derived from readiness receipts (data_completeness_gate.md
§7 G1/G2) — is the Evidence Library's PR-4 in that document's own sequencing,
and is out of scope here. **This PR proves the seam exists and is sound; it
does not flip the Evidence Library over onto it.** Registering such a rule
would change how evidence is admitted, which is a governance change, not a
framework change.
"""
from __future__ import annotations

from datetime import datetime
from typing import Generic, Protocol, TypeVar, runtime_checkable

import engine
from contract import Contract
from engine import ENGINE_VERSION, ReadinessVerdict
from observation import InputObservation


class UnknownRuleError(KeyError):
    """
    Raised when a receipt names a (rule_id, engine_version) pair with no
    registered implementation.

    This is the "fail loudly" requirement (contract_versioning.md §3), and it
    is the entire point of the registry. Falling back to the current default
    would mean a historical receipt silently replays under semantics it was
    never rendered under — while every hash still verifies, and replay still
    reports green. That is the one failure mode an audit trail must not have.
    """


class _Identified(Protocol):
    rule_id: str
    engine_version: str


R = TypeVar("R", bound=_Identified)


class RuleRegistry(Generic[R]):
    """
    The rule dispatch mechanism, written once, shape-agnostic.

    Keyed by (rule_id, engine_version) — never by rule_id alone. Two engine
    versions of the same rule_id are two DIFFERENT implementations and must
    both remain resolvable forever, or every receipt written under the older
    one becomes unreplayable.
    """

    def __init__(self, name: str) -> None:
        self._name = name
        self._rules: dict[tuple[str, str], R] = {}

    def register(self, rule: R) -> None:
        """
        Additive. Registering a rule NEVER removes or reinterprets an existing
        registration: a receipt written under (X, v1) resolves to that exact
        implementation forever, regardless of what is registered later under
        (X, v2) or under any other id. Backward compatibility is therefore
        structural, not incidental.

        Re-registering the SAME key with a DIFFERENT implementation is
        refused — that is the silent-semantics-change attack, and it is the
        reason this class exists rather than a bare dict.
        """
        key = (rule.rule_id, rule.engine_version)
        existing = self._rules.get(key)
        if existing is not None and existing is not rule and type(existing) is not type(rule):
            raise UnknownRuleError(
                f"{self._name}: {key} is already registered to a different implementation "
                f"({type(existing).__name__}). A rule's semantics may never be redefined "
                f"under an existing (rule_id, engine_version) — bump engine_version."
            )
        self._rules[key] = rule

    def get(self, rule_id: str, engine_version: str | None) -> R:
        rule = self._rules.get((rule_id, engine_version))
        if rule is None:
            raise UnknownRuleError(
                f"{self._name}: no rule registered for rule_id={rule_id!r}, "
                f"engine_version={engine_version!r} — the historical implementation is "
                f"unavailable. Replay refuses rather than substituting the current one."
            )
        return rule

    def keys(self) -> list[tuple[str, str]]:
        return sorted(self._rules)

    def __contains__(self, key: tuple[str, str]) -> bool:
        return key in self._rules


@runtime_checkable
class ReadinessRule(Protocol):
    """
    The readiness admissibility rule. The gate depends on THIS, never on
    `engine.evaluate` directly — which is what makes the engine replaceable
    (a v2 with different check semantics) without any receipt written under v1
    changing meaning.
    """
    rule_id: str
    engine_version: str

    def evaluate(
        self,
        contract: Contract,
        observations: dict[str, InputObservation],
        period: str,
        as_of: str | datetime,
        *,
        contract_version: int | None = None,
        contract_hash: str | None = None,
    ) -> ReadinessVerdict:
        ...


class CompletenessGateRule:
    """
    The default provider: a thin, behaviour-preserving wrapper around
    engine.evaluate(). Its (rule_id, engine_version) pair is stamped into every
    receipt it produces, and is what replay dispatches on.
    """
    rule_id = "dq.completeness_gate"
    engine_version = ENGINE_VERSION

    def evaluate(
        self,
        contract: Contract,
        observations: dict[str, InputObservation],
        period: str,
        as_of: str | datetime,
        *,
        contract_version: int | None = None,
        contract_hash: str | None = None,
    ) -> ReadinessVerdict:
        return engine.evaluate(
            contract, observations, period, as_of,
            contract_version=contract_version, contract_hash=contract_hash,
        )


READINESS_RULES: RuleRegistry[ReadinessRule] = RuleRegistry("readiness")

DEFAULT_RULE = CompletenessGateRule()
READINESS_RULES.register(DEFAULT_RULE)


def register_rule(rule: ReadinessRule) -> None:
    READINESS_RULES.register(rule)


def get_rule(rule_id: str, engine_version: str | None) -> ReadinessRule:
    return READINESS_RULES.get(rule_id, engine_version)
