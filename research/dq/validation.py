"""
Validation — item 8.

Four kinds, deliberately kept distinct because they fail for different reasons
and are fixed by different people:

  * **Framework validation** (`validate_framework`) — is the ENGINE internally
    coherent? Every state has a policy row; every default check_id resolves;
    the receipt taxonomy still admits what we emit. Broken by a code change.
    Fixed by an engineer.

  * **Contract validation** (`validate_contract_document`) — is this CONTRACT
    usable? Broken by a contract author. Fixed by a contract author. Never
    raised into the evaluation path: it surfaces there as CONFIGURATION_ERROR.

  * **Determinism validation** (`assert_deterministic`) — does the same input
    produce the same verdict, every time, in every process? Broken by a check
    that read a clock, a set-iteration order, or an unpinned default.

  * **Replay validation** (`validate_replay`, `validate_ledger`) — does history
    still reproduce? Broken by an in-place edit to a check, a pruned contract
    version, or a mutated observation set. This is the one that matters in five
    years.

`validate_ledger()` is the `--check` surface, with `DQ`-prefixed codes so its
findings are visibly distinct from `lifecycle.py`'s C1–C9 and
`evidence_governance`'s EG1–EG2.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

_EVIDENCE_LIBRARY = Path(__file__).resolve().parents[1] / "evidence_library"
if str(_EVIDENCE_LIBRARY) not in sys.path:
    sys.path.insert(0, str(_EVIDENCE_LIBRARY))

import contract_registry as cr  # noqa: E402
import receipt_ledger as rl  # noqa: E402
import receipts as rcp  # noqa: E402

import policy
import readiness
import rules
from checks import CHECK_REGISTRY
from contract import (
    DEFAULT_CONTINUITY_CHECK,
    DEFAULT_COVERAGE_CHECK,
    DEFAULT_FRESHNESS_CHECK,
    DEFAULT_SCHEMA_CHECK,
    DEFAULT_TEMPORAL_CHECK,
    Contract,
    ContractConfigurationError,
    parse_contract,
    validate_contract,
)
from engine import ENGINE_VERSION, evaluate
from observation import InputObservation
from states import ACTION_PRECEDENCE, STATES


def validate_framework() -> list[str]:
    """Is the engine internally coherent? Cheap enough to run in CI on every
    commit, and it fails loudly the moment someone adds a state without a
    policy row — which would otherwise surface as a KeyError at 3am, inside a
    gate, on a state nobody had thought about."""
    errors: list[str] = []

    for state in STATES:
        for criticality in (policy.REQUIRED, policy.OPTIONAL):
            if state not in policy.DEFAULT_MATRIX:
                errors.append(f"DQ-F1: state '{state}' has no row in the policy matrix")
                break
            if criticality not in policy.DEFAULT_MATRIX[state]:
                errors.append(
                    f"DQ-F1: policy matrix has no action for ('{state}', '{criticality}')"
                )
            else:
                action = policy.DEFAULT_MATRIX[state][criticality]
                if action not in ACTION_PRECEDENCE:
                    errors.append(
                        f"DQ-F2: policy matrix maps ('{state}','{criticality}') to unknown "
                        f"action '{action}'"
                    )

    for check_id in (
        DEFAULT_TEMPORAL_CHECK, DEFAULT_SCHEMA_CHECK, DEFAULT_FRESHNESS_CHECK,
        DEFAULT_COVERAGE_CHECK, DEFAULT_CONTINUITY_CHECK,
    ):
        if check_id not in CHECK_REGISTRY:
            errors.append(f"DQ-F3: default check_id '{check_id}' is not registered")

    if rcp.DECISION_TAXONOMY.get(readiness.DECISION_TYPE) != "derived":
        errors.append(
            f"DQ-F4: '{readiness.DECISION_TYPE}' must be a 'derived' decision in the receipt "
            f"taxonomy — replay must RECOMPUTE a readiness verdict, not merely verify an "
            f"envelope"
        )
    if readiness.ACTION not in rcp.ACTIONS:
        errors.append(f"DQ-F5: action '{readiness.ACTION}' is not in receipts.ACTIONS")
    if readiness.SUBJECT_KIND not in rcp.SUBJECT_KINDS:
        errors.append(f"DQ-F6: subject kind '{readiness.SUBJECT_KIND}' is not in receipts.SUBJECT_KINDS")

    if (rules.DEFAULT_RULE.rule_id, rules.DEFAULT_RULE.engine_version) not in rules.READINESS_RULES:
        errors.append("DQ-F7: the default readiness rule is not registered in READINESS_RULES")
    if rules.DEFAULT_RULE.engine_version != ENGINE_VERSION:
        errors.append(
            f"DQ-F8: default rule engine_version '{rules.DEFAULT_RULE.engine_version}' does not "
            f"match ENGINE_VERSION '{ENGINE_VERSION}'"
        )

    return errors


def validate_contract_document(doc: dict) -> list[str]:
    """Parse + semantically validate a contract document. Never raises."""
    try:
        contract = parse_contract(doc)
    except ContractConfigurationError as exc:
        return [f"DQ-C1: {exc}"]
    return [f"DQ-C2: {e}" for e in validate_contract(contract)]


def assert_deterministic(
    contract: Contract,
    observations: dict[str, InputObservation],
    period: str,
    as_of: str | datetime,
    iterations: int = 3,
) -> list[str]:
    """
    Evaluate the same inputs repeatedly and confirm the verdict is byte-stable.

    A check that reads a clock, iterates a set, or consults an unpinned default
    is a DEFECT, not a quirk: it makes every receipt it ever produced
    unverifiable, because replay would compare against a verdict that can no
    longer be reproduced.
    """
    baseline = evaluate(contract, observations, period, as_of).to_dict()
    errors: list[str] = []
    for i in range(1, iterations):
        again = evaluate(contract, observations, period, as_of).to_dict()
        if again != baseline:
            errors.append(
                f"DQ-D1: evaluation {i} differs from the baseline for "
                f"{contract.contract_id}/{period} — the engine is not deterministic"
            )
    return errors


def validate_replay(db_path: Path | str, receipt_id: str) -> list[str]:
    """Does this one receipt still reproduce?"""
    try:
        result = readiness.replay_readiness(db_path, receipt_id)
    except Exception as exc:  # noqa: BLE001 — a raising replay IS a validation failure
        return [f"DQ-R1: replay of {receipt_id[:12]}… raised {type(exc).__name__}: {exc}"]

    errors: list[str] = []
    if not result.hash_ok:
        errors.append(
            f"DQ-R2: receipt {receipt_id[:12]}… does not hash to its stored body (TAMPERING)"
        )
    if result.replay.mode == "refused":
        return errors  # bootstrap receipts refuse by design; not a failure
    if not result.replay.ok:
        errors.append(
            f"DQ-R3: receipt {receipt_id[:12]}… does not replay: {result.replay.detail}"
        )
    return errors


def validate_ledger(db_path: Path | str) -> list[str]:
    """
    The `--check` surface. Every invariant that must hold across the whole
    ledger, forever.
    """
    errors: list[str] = []

    chain = rl.verify_chain(db_path)
    if not chain.ok:
        errors.extend(f"DQ-L1: receipt chain broken: {e}" for e in chain.errors)

    receipts = rl.get_receipts_by_type(db_path, readiness.DECISION_TYPE)

    for receipt in receipts:
        rid = receipt["receipt_id"]
        body = receipt["body"]
        bootstrap = body.get("attestation") == rcp.ATTESTATION_BOOTSTRAP

        row = readiness.get_readiness_by_receipt(db_path, rid)
        if row is None:
            if not bootstrap:
                errors.append(
                    f"DQ-L2: readiness receipt {rid[:12]}… has no data_readiness row; its "
                    f"replay substrate is missing"
                )
            continue

        if row["state"] not in STATES:
            errors.append(f"DQ-L3: data_readiness row {rid[:12]}… has unknown state '{row['state']}'")
        if row["action"] not in ACTION_PRECEDENCE:
            errors.append(f"DQ-L4: data_readiness row {rid[:12]}… has unknown action '{row['action']}'")

        if bootstrap:
            continue

        contract_hash = body["subject"]["version_or_hash"]
        if cr.get_by_hash(db_path, contract_hash) is None:
            errors.append(
                f"DQ-L5: receipt {rid[:12]}… names contract_hash {str(contract_hash)[:12]}…, "
                f"which does not resolve in the registry. Its verdict can never be replayed."
            )

        rule_key = (body["rule"]["rule_id"], body["rule"]["engine_version"])
        if rule_key not in rules.READINESS_RULES:
            errors.append(
                f"DQ-L6: receipt {rid[:12]}… was rendered under rule {rule_key}, which is not "
                f"registered. Replay would refuse rather than substitute — but the historical "
                f"implementation must be restored, not the receipt rewritten."
            )

    # Every data_readiness row must be attested by a receipt. A substrate row
    # nothing attests to is a verdict nobody signed.
    for row in readiness.get_readiness_board(db_path):
        if rl.get_receipt(db_path, row["receipt_id"]) is None:
            errors.append(
                f"DQ-L7: data_readiness row {row['receipt_id'][:12]}… has no governance receipt"
            )

    return errors
