"""
research/dq — the Data Completeness Framework.

The canonical readiness engine for every derived pipeline on the platform:
forward evidence, feature engineering, datasets, labels, ML training sets,
inference sets, archive generation, and whatever comes next. H6 is the
motivating example, not the scope.

Implements docs/governance/data_completeness_gate.md (the engine) and the
engine/check-versioning half of docs/governance/contract_versioning.md, on top
of the governance receipt ledger (governance_receipts.md PR-1) and the contract
registry (contract_versioning.md PR-A/PR-2..4) that already exist.

The core thesis, restated because every design decision in this package follows
from it:

    Completeness is not a property a pipeline CHECKS. It is a CONTRACT an input
    must satisfy before a derivation is permitted to exist — and the verdict of
    that check is itself EVIDENCE: persisted, immutable, provenance-carrying,
    and consumed by governance.

Modules (flat imports, matching the convention already established by
research/evidence_library/ — add this directory to sys.path and `import
engine`, `import readiness`):

    states.py           the 8 readiness states, severity order, worst-wins;
                        the 6 actions and their precedence
    policy.py           the (state, criticality) -> action matrix
    contract.py         the contract MODEL — data, not code
    observation.py      InputObservation: metadata only, and the replay substrate
    checks.py           the 6 check families, pure, frozen by versioned check_id
    engine.py           evaluate(contract, observations, period, as_of) -> Verdict
    rules.py            the rule provider + RuleRegistry (dispatch, fail-loud)
    probes.py           source-type probe registry (metadata only)
    contract_loader.py  the THREE resolution operations (§5), never conflated
    readiness.py        receipts, the data_readiness projection, replay
    validation.py       framework / contract / determinism / replay validation

No pipeline-specific logic lives anywhere in this package. A new pipeline is
onboarded with ONE contract file and ZERO framework changes; if that ever stops
being true, L1 has been violated and the framework has quietly become another
H6-shaped `if`.
"""

ENGINE_VERSION = "dq.v1"
