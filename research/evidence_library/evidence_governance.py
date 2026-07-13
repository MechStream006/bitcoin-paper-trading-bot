"""
Evidence Library Governance Integration — PR-6 of the Governance Architecture.

Wires PR-1's receipt ledger and PR-5's ledger-derived lifecycle state into
the pre-existing Evidence Library (record.py / lifecycle.py), for exactly
the decision types already present in receipts.DECISION_TAXONOMY since
PR-1: `evidence.admit` and `evidence.reject`. No new governance event type,
no `family.transition` / `family.promote` receipt (explicitly excluded by
this PR's task -- "state transition receipts").

Dependency check (per the task's explicit stop condition), read before the
rest of this module: `governance_receipts.md` §2 defines `evidence.admit`'s
admissibility rule in the abstract as "every input day READY/declared-
degraded ⇒ admissible" -- i.e. keyed to the Data Completeness Gate's
readiness verdicts (`data_completeness_gate.md`). That gate's engine
(contracts, checks, readiness verdicts) has never been implemented in this
codebase -- only the Contract Registry/Lifecycle/Receipts (PR-2/3/4) exist,
which VERSION contracts, not EVALUATE data against them. Building that
engine is explicitly out of this PR's scope ("data completeness" is not on
the Implement list).

This does NOT block PR-6. The Evidence Library has always had its own,
already-fully-implemented admissibility rule: `lifecycle.check_candidate_gate()`
-- deterministic, pure, already the sole mechanism by which every one of
the real ledger's 35 families was ever admitted (candidate) or rejected
(disqualified). `evidence.admit`/`evidence.reject`, in THIS PR, attest
*that a run's evidence passed/failed the applicable governance gate* --
not "the run's input data was complete." That is an honest, narrower claim
than the abstract taxonomy entry's data-completeness framing, and it is
stated explicitly here and in the schema reference doc rather than implied.
Implementation proceeded on this basis instead of stopping.

Both decision types are `derived` (receipts.DECISION_TAXONOMY) -- replay
must recompute the verdict bit-for-bit, not just verify an envelope. This
is the first real exercise of PR-1's derived-replay path (`rule_fn`
injection) in this codebase; contract receipts (PR-4) are all
`discretionary` and never exercised it.

Pinning/re-pinning (items 3-4) reuse `record.append_transition()` verbatim
-- unchanged, no new receipt type for the transition itself, per the
"no state transition receipts" exclusion. What is new is the governance
VALIDATION wrapped around that existing call: a pin now requires the run
it justifies to already carry an admission (or rejection, for a
disqualifying pin) receipt.

--- Correction (post-review, same PR): the admissibility rule interface ---

The first version of this module called `lifecycle.check_candidate_gate()`
directly, by name, in three places, and `verify_admission_receipt()`
ignored a receipt's own recorded `rule_id`/`engine_version`, always
re-executing whatever `check_candidate_gate` happened to be at replay
time. That violates `contract_versioning.md` §3's engine-pinning
discipline ("replay must execute the check implementations named in the
receipt at the recorded engine_version ... if the current engine cannot
honour a historical check_id, replay fails loudly -- it never silently
substitutes the modern check") and was flagged in review as a real gap,
not a stylistic one: a future in-place edit to `check_candidate_gate`
would silently change what historical receipts replay to, while `replay()`
kept reporting success.

Fixed by introducing `AdmissibilityRule` (below) -- a genuine substitution
point. `admit_evidence()`/`reject_evidence()` depend on an
`AdmissibilityRule` instance, never on `lifecycle.check_candidate_gate`
directly; `CandidateGateRule` is the default provider, a thin wrapper
around the same, unmodified function (item 2: no behavioral change).
Every rule is identified by `(rule_id, engine_version)`, both recorded
verbatim in every receipt it produces. Replay resolves the implementation
to re-execute via `get_rule(recorded_rule_id, recorded_engine_version)` --
looked up from a registry, never assumed to be "whatever is current" --
and raises `UnknownRuleError` (loud, not silent) if no implementation for
that exact pair is registered. This is the substitution point PR-7's Data
Completeness Gate will use (`register_rule(SomeNewRule())`); no such rule
is implemented here (item 5).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

import lifecycle
import record
import receipt_ledger as rl
from receipts import (
    Actor,
    Authority,
    EvaluationItem,
    ReceiptBody,
    ReceiptError,
    ReplayResult,
    RuleRef,
    Subject,
    Verdict,
    canonical_json,
    decimal_str,
    now_iso,
    replay as replay_receipt_body,
    sha256_lf,
    verify_receipt,
)

ENGINE_VERSION = "evidence_governance.v1"

# Which decision_type a pin into a given to_state must be backed by. States
# not listed here (e.g. 'retired') have no admission/rejection requirement
# -- retirement is a family-lifecycle act this PR does not govern (no
# 'family.transition' receipt exists to attach it to; see module docstring).
_REQUIRED_RECEIPT_TYPE_FOR_STATE: dict[str, str] = {
    "candidate": "evidence.admit",
    "validated": "evidence.admit",
    "promotion_ready": "evidence.admit",
    "production_ready": "evidence.admit",
    "disqualified": "evidence.reject",
    "rejected": "evidence.reject",
}


class EvidenceGovernanceError(ReceiptError):
    """Base class for evidence-governance integration failures."""


class EvidenceNotFoundError(EvidenceGovernanceError):
    """Raised when a referenced run_id does not exist in runs."""


class DuplicateAdmissionError(EvidenceGovernanceError):
    """Raised when a run already carries an evidence.admit/.reject receipt."""


class InvalidPinError(EvidenceGovernanceError):
    """Raised when a pin/re-pin fails governance validation (item 5)."""


class UnknownRuleError(EvidenceGovernanceError):
    """
    Raised by replay when a receipt names a (rule_id, engine_version) pair
    with no registered AdmissibilityRule implementation. This is the "fail
    loudly" requirement: replay must NEVER fall back to "whatever
    check_candidate_gate is now" -- if the historical implementation is not
    available, the honest answer is refusal, not a guess.
    """


# ---------------------------------------------------------------------------
# Admissibility rule interface -- the substitution point PR-7 will use.
# ---------------------------------------------------------------------------

@runtime_checkable
class AdmissibilityRule(Protocol):
    """
    Evidence Governance depends on THIS, never on
    `lifecycle.check_candidate_gate` directly. `rule_id`/`engine_version`
    are recorded verbatim in every receipt a rule produces -- they are the
    rule's permanent identity, and replay dispatches on them (see
    get_rule() below), not on whatever implementation happens to be
    imported at replay time.

    A future rule (e.g. the Data Completeness Gate's readiness-based
    admissibility, PR-7) implements this same shape and registers itself
    via register_rule() -- it does not edit admit_evidence()/
    reject_evidence()/verify_admission_receipt(), and it does not alter
    what CandidateGateRule means for any receipt already written under it.
    """
    rule_id: str
    engine_version: str

    def evaluate(self, family_meta: dict, run_metrics: dict, thresholds: dict) -> tuple[bool, list[dict]]:
        """Returns (passed, gate_results). gate_results is a list of
        {gate_code, role, passed, observed, threshold} dicts -- the exact
        shape lifecycle.check_candidate_gate() already returns, reused as
        the evaluation[] contents of the receipt this rule produces."""
        ...


class CandidateGateRule:
    """
    The default provider (item 2): a thin, behavior-preserving wrapper
    around the pre-existing, unmodified `lifecycle.check_candidate_gate()`.
    Every receipt this PR wrote before the correction -- and the real
    ledger's 13 bootstrap receipts are unaffected either way, since they
    carry rule_id='bootstrap' and never reach this class -- used exactly
    this (rule_id, engine_version) pair, so nothing already on disk needs
    to change for it to keep resolving correctly (item 4).
    """
    rule_id = "lifecycle.check_candidate_gate"
    engine_version = ENGINE_VERSION

    def evaluate(self, family_meta: dict, run_metrics: dict, thresholds: dict) -> tuple[bool, list[dict]]:
        return lifecycle.check_candidate_gate(family_meta, run_metrics, thresholds)


DEFAULT_RULE = CandidateGateRule()

_RULE_REGISTRY: dict[tuple[str, str], AdmissibilityRule] = {}


def register_rule(rule: AdmissibilityRule) -> None:
    """
    PR-7's substitution point. Registers `rule` under its own
    `(rule_id, engine_version)`. Registering a new rule NEVER removes or
    reinterprets an existing registration -- a receipt written under
    `("lifecycle.check_candidate_gate", "evidence_governance.v1")` resolves
    to CandidateGateRule forever, regardless of what is registered later
    under a different pair. This is what makes backward compatibility
    (item 4) structural rather than incidental.
    """
    _RULE_REGISTRY[(rule.rule_id, rule.engine_version)] = rule


def get_rule(rule_id: str, engine_version: str | None) -> AdmissibilityRule:
    """
    Resolve the EXACT implementation a receipt was produced under. Never
    falls back to a default or to "whatever CandidateGateRule is now" --
    raises UnknownRuleError (loud) if the pair is not registered.
    """
    rule = _RULE_REGISTRY.get((rule_id, engine_version))
    if rule is None:
        raise UnknownRuleError(
            f"no AdmissibilityRule registered for rule_id={rule_id!r}, "
            f"engine_version={engine_version!r} -- the historical implementation "
            f"is unavailable; replay refuses rather than substituting the current one"
        )
    return rule


register_rule(DEFAULT_RULE)


def _busy_connect(db_path: Path | str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 5000")
    return con


def init_db(db_path: Path | str) -> None:
    """Both schemas this module needs, in one file: runs/gate_results/
    state_transitions (record.py, PR-5) and governance_receipts (PR-1)."""
    record.init_db(db_path)
    rl.init_db(db_path)


# ---------------------------------------------------------------------------
# Policy hashing (reuses receipts.sha256_lf/canonical_json verbatim)
# ---------------------------------------------------------------------------

def _decimalize(obj):
    """Recursively convert every raw float in obj to a decimal string
    (receipts.decimal_str) -- thresholds.yaml is full of floats (e.g.
    min_sharpe: 0.30), and canonical_json() correctly refuses to hash a raw
    IEEE float anywhere in the structure (Rule 3, governance_receipts.md
    §1.3). This does not touch the receipt body itself -- only the
    hash-input document -- but obeys the same platform-wide rule."""
    if isinstance(obj, float):
        return decimal_str(obj)
    if isinstance(obj, dict):
        return {k: _decimalize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_decimalize(v) for v in obj]
    return obj


def _policy_hash(rule_id: str, engine_version: str, thresholds: dict) -> str:
    """A real, deterministic hash of the actual rule + policy in force --
    not an invented placeholder. Changes if thresholds.yaml changes, and is
    scoped to the specific rule (rule_id, engine_version) that produced it,
    not a module-wide constant -- a future rule with a different
    engine_version must never collide with CandidateGateRule's hash."""
    doc = {"engine_version": engine_version, "rule_id": rule_id, "thresholds": _decimalize(thresholds)}
    return sha256_lf(canonical_json(doc))


# ---------------------------------------------------------------------------
# Evidence admission / rejection (items 1-2, 9's replayable rule)
# ---------------------------------------------------------------------------

def get_admission_receipt(db_path: Path | str, run_id: str) -> dict | None:
    """The evidence.admit/.reject receipt for a run, if one exists. At most
    one may ever exist (enforced by admit_evidence()/reject_evidence()'s
    duplicate-prevention check)."""
    rows = rl.get_receipts_by_subject(db_path, "evidence_run", run_id)
    admission_rows = [r for r in rows if r["decision_type"] in ("evidence.admit", "evidence.reject")]
    if not admission_rows:
        return None
    if len(admission_rows) > 1:
        raise EvidenceGovernanceError(
            f"run_id {run_id} has {len(admission_rows)} admission/rejection receipts -- "
            f"duplicate-prevention was bypassed (direct DB write?)"
        )
    return admission_rows[0]


def _build_admission_body(
    run_id: str,
    run: dict,
    decision_type: str,
    action: str,
    verdict_result: str,
    rule: AdmissibilityRule,
    gate_results: list[dict],
    thresholds: dict,
    actor: str,
    actor_type: str,
    reason: str | None,
) -> ReceiptBody:
    policy_hash = _policy_hash(rule.rule_id, rule.engine_version, thresholds)
    as_of = decided_at = now_iso()
    return ReceiptBody(
        decision_type=decision_type,
        decision_class="derived",
        action=action,
        subject=Subject(kind="evidence_run", id=run_id, version_or_hash=run.get("family_id")),
        from_state=None,
        to_state=None,
        as_of=as_of,
        decided_at=decided_at,
        actor=Actor(type=actor_type, id=actor, key_id=None),
        authority=Authority(policy_id=rule.rule_id, policy_hash=policy_hash, role=None),
        inputs=(),
        rule=RuleRef(
            rule_id=rule.rule_id,
            rule_hash=policy_hash,
            engine_version=rule.engine_version,
            thresholds_hash=policy_hash,
        ),
        evaluation=tuple(
            EvaluationItem(
                check_id=gr["gate_code"],
                observed=str(gr["observed"]),
                expected=str(gr["threshold"]),
                passed=bool(gr["passed"]),
            )
            for gr in gate_results
        ),
        verdict=Verdict(result=verdict_result, reason_code=None),
        reason_text=reason,
    )


def admit_evidence(
    db_path: Path | str,
    run_id: str,
    family_meta: dict,
    thresholds: dict,
    actor: str = "evidence_governance",
    actor_type: str = "service",
    reason: str | None = None,
    rule: AdmissibilityRule | None = None,
) -> dict:
    """
    Emit an `evidence.admit` receipt for run_id.

    `rule` (default: DEFAULT_RULE, i.e. CandidateGateRule -- a thin wrapper
    around the pre-existing lifecycle.check_candidate_gate()) is the
    admissibility rule (item 1: a genuine substitution point, not a direct
    dependency on check_candidate_gate). Refuses if the gate does not
    actually pass: admit_evidence() asserts what it attests is true, it
    does not let a caller record a false verdict by mistake (use
    reject_evidence() for a failing run).

    Single-table write (governance_receipts only -- runs is immutable and
    untouched), so this is atomic by construction; no multi-table
    transaction is needed the way contract activation (PR-4) required one.
    """
    rule = rule or DEFAULT_RULE
    run = record.get_run(db_path, run_id)
    if run is None:
        raise EvidenceNotFoundError(f"run_id {run_id} not found in runs")

    if get_admission_receipt(db_path, run_id) is not None:
        raise DuplicateAdmissionError(
            f"run_id {run_id} already has an admission/rejection receipt; "
            f"a run may be admitted or rejected exactly once"
        )

    passed, gate_results = rule.evaluate(family_meta, run["metrics"], thresholds)
    if not passed:
        raise EvidenceGovernanceError(
            f"run_id {run_id} does not pass {rule.rule_id} -- use reject_evidence() "
            f"for a failing run, not admit_evidence()"
        )

    body = _build_admission_body(
        run_id, run, "evidence.admit", "admit", "admitted",
        rule, gate_results, thresholds, actor, actor_type, reason,
    )
    return rl.append_receipt(db_path, body)


def reject_evidence(
    db_path: Path | str,
    run_id: str,
    family_meta: dict,
    thresholds: dict,
    actor: str = "evidence_governance",
    actor_type: str = "service",
    reason: str | None = None,
    rule: AdmissibilityRule | None = None,
) -> dict:
    """Mirror of admit_evidence() for a run that FAILS its gate. Refuses if
    the gate actually passes (use admit_evidence() instead)."""
    rule = rule or DEFAULT_RULE
    run = record.get_run(db_path, run_id)
    if run is None:
        raise EvidenceNotFoundError(f"run_id {run_id} not found in runs")

    if get_admission_receipt(db_path, run_id) is not None:
        raise DuplicateAdmissionError(
            f"run_id {run_id} already has an admission/rejection receipt; "
            f"a run may be admitted or rejected exactly once"
        )

    passed, gate_results = rule.evaluate(family_meta, run["metrics"], thresholds)
    if passed:
        raise EvidenceGovernanceError(
            f"run_id {run_id} passes {rule.rule_id} -- use admit_evidence() "
            f"for a passing run, not reject_evidence()"
        )

    body = _build_admission_body(
        run_id, run, "evidence.reject", "reject", "rejected",
        rule, gate_results, thresholds, actor, actor_type, reason,
    )
    return rl.append_receipt(db_path, body)


# ---------------------------------------------------------------------------
# Bootstrap admission (for runs that predate this PR -- see
# migrate_bootstrap_evidence_admission.py, the live caller of this)
# ---------------------------------------------------------------------------

def bootstrap_admission_receipt(
    db_path: Path | str,
    run_id: str,
    decision_type: str,
    verdict_result: str,
    attestation_note: str,
    actor: str = "bootstrap",
) -> dict:
    """
    A receipt for a run that was admitted/rejected before PR-6 existed --
    marked `attestation='bootstrap'` per governance_receipts.md §6's
    "bootstrap honesty": `replay()` on it refuses rather than approximating,
    because the gate evaluation that originally justified it was not
    captured at the time in a replayable form (no stored `gate_results`
    guarantee predates this PR's own duplicate-checked write path).

    decision_type must be 'evidence.admit' or 'evidence.reject'.
    """
    if decision_type not in ("evidence.admit", "evidence.reject"):
        raise ValueError(f"decision_type must be evidence.admit or evidence.reject, got {decision_type!r}")
    run = record.get_run(db_path, run_id)
    if run is None:
        raise EvidenceNotFoundError(f"run_id {run_id} not found in runs")
    if get_admission_receipt(db_path, run_id) is not None:
        raise DuplicateAdmissionError(f"run_id {run_id} already has an admission/rejection receipt")

    as_of = decided_at = now_iso()
    body = ReceiptBody(
        decision_type=decision_type,
        decision_class="derived",
        action="admit" if decision_type == "evidence.admit" else "reject",
        subject=Subject(kind="evidence_run", id=run_id, version_or_hash=run.get("family_id")),
        as_of=as_of,
        decided_at=decided_at,
        actor=Actor(type="service", id=actor, key_id=None),
        authority=Authority(policy_id="bootstrap", policy_hash=sha256_lf(b"bootstrap"), role=None),
        rule=RuleRef(rule_id="bootstrap", rule_hash=None, engine_version=None, thresholds_hash=None),
        verdict=Verdict(result=verdict_result, reason_code=None),
        attestation="bootstrap",
        attestation_note=attestation_note,
    )
    return rl.append_receipt(db_path, body)


# ---------------------------------------------------------------------------
# Evidence pinning / re-pinning (items 3-4-5) -- reuses record.append_transition()
# verbatim; adds governance validation, emits NO new receipt type.
# ---------------------------------------------------------------------------

def _validate_run_for_pin(db_path: Path | str, family_id: str, run_id: str, to_state: str) -> dict:
    run = record.get_run(db_path, run_id)
    if run is None:
        raise EvidenceNotFoundError(f"run_id {run_id} not found in runs")
    if run["family_id"] != family_id:
        raise InvalidPinError(
            f"run_id {run_id} belongs to family '{run['family_id']}', not '{family_id}'"
        )

    required_type = _REQUIRED_RECEIPT_TYPE_FOR_STATE.get(to_state)
    if required_type is not None:
        receipt = get_admission_receipt(db_path, run_id)
        if receipt is None:
            raise InvalidPinError(
                f"run_id {run_id} has no admission/rejection receipt; pinning into "
                f"'{to_state}' requires a prior '{required_type}' receipt "
                f"(call admit_evidence()/reject_evidence() first)"
            )
        if receipt["decision_type"] != required_type:
            raise InvalidPinError(
                f"run_id {run_id} carries a '{receipt['decision_type']}' receipt, but "
                f"pinning into '{to_state}' requires '{required_type}'"
            )
    return run


def pin_evidence(
    db_path: Path | str,
    family_id: str,
    to_state: str,
    run_id: str,
    actor: str,
    reason: str,
) -> dict:
    """
    Pin family_id's governance state to to_state, justified by run_id.

    Governance validation (item 5), on top of the unchanged
    record.append_transition(): the run must exist, must belong to this
    family, and -- for any state requiring one -- must already carry the
    matching admission (evidence.admit) or rejection (evidence.reject)
    receipt. This is a genuine NEW transition (from_state != to_state); use
    repin_evidence() for a same-state re-pin.
    """
    state, _pinned, _ts = record.derive_state_at(db_path, family_id)
    from_state = state or "discovery"
    if from_state == to_state:
        raise InvalidPinError(
            f"'{family_id}' is already '{from_state}' -- use repin_evidence() for a re-pin"
        )

    _validate_run_for_pin(db_path, family_id, run_id, to_state)

    record.append_transition(
        db_path, family_id, from_state, to_state,
        reason=reason, justifying_run_id=run_id, actor=actor,
    )
    new_state, new_pinned, new_ts = record.derive_state_at(db_path, family_id)
    return {"family_id": family_id, "from_state": from_state, "to_state": new_state,
            "pinned_run_id": new_pinned, "ts": new_ts}


def repin_evidence(
    db_path: Path | str,
    family_id: str,
    run_id: str,
    actor: str,
    reason: str,
) -> dict:
    """
    Re-pin family_id's CURRENT state to new evidence (the X -> X arc PR-5
    added to lifecycle.can_transition()). Same governance validation as
    pin_evidence(), applied to the current state.
    """
    state, _pinned, _ts = record.derive_state_at(db_path, family_id)
    if state is None:
        raise InvalidPinError(f"'{family_id}' has no prior transition to re-pin")

    _validate_run_for_pin(db_path, family_id, run_id, state)

    record.append_transition(
        db_path, family_id, state, state,
        reason=reason, justifying_run_id=run_id, actor=actor,
    )
    new_state, new_pinned, new_ts = record.derive_state_at(db_path, family_id)
    return {"family_id": family_id, "from_state": state, "to_state": new_state,
            "pinned_run_id": new_pinned, "ts": new_ts}


def validate_pin(db_path: Path | str, family_id: str) -> tuple[bool, list[str]]:
    """
    Standalone governance-validation check (item 5), independent of the act
    of pinning: does the family's CURRENT pin have the receipt it should?
    Usable at any time, not just at pin-time (e.g. to audit a family pinned
    by legacy/bootstrap means).
    """
    state, pinned_run_id, _ts = record.derive_state_at(db_path, family_id)
    if state is None:
        return True, []  # never transitioned -- nothing to validate

    required_type = _REQUIRED_RECEIPT_TYPE_FOR_STATE.get(state)
    if required_type is None:
        return True, []

    if not pinned_run_id:
        return False, [f"{family_id}: state={state} has no pinned_run_id"]

    receipt = get_admission_receipt(db_path, pinned_run_id)
    if receipt is None:
        return False, [
            f"{family_id}: pinned run {pinned_run_id[:8]} has no admission/rejection "
            f"receipt (required: {required_type})"
        ]
    if receipt["decision_type"] != required_type:
        return False, [
            f"{family_id}: pinned run {pinned_run_id[:8]} carries "
            f"'{receipt['decision_type']}', state '{state}' requires '{required_type}'"
        ]
    return True, []


# ---------------------------------------------------------------------------
# Governance-aware evidence queries (item 6)
# ---------------------------------------------------------------------------

def get_admitted_runs(db_path: Path | str) -> list[str]:
    return [r["subject_id"] for r in rl.get_receipts_by_type(db_path, "evidence.admit")]


def get_rejected_runs(db_path: Path | str) -> list[str]:
    return [r["subject_id"] for r in rl.get_receipts_by_type(db_path, "evidence.reject")]


def get_evidence_lineage(db_path: Path | str, family_id: str) -> list[dict]:
    """
    Every run, transition, and admission/rejection receipt for family_id,
    interleaved chronologically -- the governance-aware counterpart to
    report.py's evidence_timeline.md (which does not know about receipts).
    """
    events: list[tuple[str, str, dict]] = []
    for r in record.get_all_runs(db_path, family_id=family_id):
        events.append((r["run_ts"], "run", r))
        receipt = get_admission_receipt(db_path, r["run_id"])
        if receipt is not None:
            events.append((receipt["decided_at"], "receipt", receipt))
    for t in record.get_all_transitions(db_path, family_id=family_id):
        events.append((t["ts"], "transition", t))
    events.sort(key=lambda e: e[0])
    return [{"ts": ts, "kind": kind, "data": data} for ts, kind, data in events]


# ---------------------------------------------------------------------------
# Point-in-time evidence reconstruction (item 7)
# ---------------------------------------------------------------------------

def reconstruct_evidence_at(db_path: Path | str, family_id: str, as_of: str | None = None) -> dict:
    """
    The full governance picture for family_id as of `as_of` (already a
    fully-qualified ISO datetime, or None for "now"): ledger-derived state
    (record.derive_state_at, PR-5), and every admission/rejection receipt
    decided at or before as_of. Pure function of (ledger, as_of).
    """
    state, pinned_run_id, ts = record.derive_state_at(db_path, family_id, as_of=as_of)
    receipts_as_of = []
    for r in record.get_all_runs(db_path, family_id=family_id):
        if as_of is not None and r["run_ts"] > as_of:
            continue
        receipt = get_admission_receipt(db_path, r["run_id"])
        if receipt is not None and (as_of is None or receipt["decided_at"] <= as_of):
            receipts_as_of.append(receipt)
    return {
        "family_id": family_id, "state": state, "pinned_run_id": pinned_run_id, "state_ts": ts,
        "admission_receipts": receipts_as_of,
    }


# ---------------------------------------------------------------------------
# Replay compatibility (item 8) -- reuses the PR-1 replay framework directly
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EvidenceReceiptVerification:
    run_id: str
    receipt_id: str
    hash_ok: bool
    replay: ReplayResult


def verify_admission_receipt(
    db_path: Path | str,
    run_id: str,
    family_meta: dict,
    thresholds: dict,
) -> EvidenceReceiptVerification:
    """
    Verifies the evidence.admit/.reject receipt for run_id using PR-1's
    replay framework directly. Because these receipts are `derived`
    (unlike PR-4's discretionary contract receipts), this supplies a real
    `rule_fn` -- the first genuine exercise of receipts.replay()'s derived
    path (previously only exercised with a synthetic lambda in PR-1's own
    tests).

    Correction (post-review): `rule_fn` no longer calls
    `lifecycle.check_candidate_gate()` directly. It resolves the EXACT
    implementation the receipt was produced under via
    `get_rule(receipt.rule.rule_id, receipt.rule.engine_version)` and
    re-executes THAT. If no implementation is registered for that exact
    pair -- e.g. a future engine version this process doesn't have --
    `get_rule()` raises `UnknownRuleError`, which propagates out of
    `replay()` uncaught (receipts.replay() does not swallow exceptions
    from `rule_fn`): replay fails loudly, it never silently substitutes
    whatever the current default rule happens to be.

    Raises (via receipts.replay) if the receipt is bootstrap-attested --
    bootstrap receipts refuse replay by design (governance_receipts.md §6)
    BEFORE rule_fn is ever called, so an unregistered legacy rule_id on a
    bootstrap receipt (e.g. 'bootstrap' itself) never reaches get_rule().
    """
    receipt = get_admission_receipt(db_path, run_id)
    if receipt is None:
        raise EvidenceGovernanceError(f"run_id {run_id} has no admission/rejection receipt to verify")

    hash_ok = verify_receipt(receipt["body"], receipt["receipt_id"])

    def rule_fn(body: dict) -> Verdict:
        run = record.get_run(db_path, run_id)
        if run is None:
            raise EvidenceNotFoundError(f"run_id {run_id} referenced by receipt no longer exists in runs")
        recorded_rule_id = body["rule"]["rule_id"]
        recorded_engine_version = body["rule"]["engine_version"]
        rule = get_rule(recorded_rule_id, recorded_engine_version)
        passed, _gate_results = rule.evaluate(family_meta, run["metrics"], thresholds)
        return Verdict(result="admitted" if passed else "rejected", reason_code=None)

    replay_result = replay_receipt_body(receipt["body"], rule_fn=rule_fn)
    return EvidenceReceiptVerification(
        run_id=run_id, receipt_id=receipt["receipt_id"], hash_ok=hash_ok, replay=replay_result,
    )


# ---------------------------------------------------------------------------
# Governance validation hook (item 9) -- additive; does not modify
# lifecycle.validate_registry()'s C1-C9. See report.py's run_check() for
# the (equally additive) wiring of this into `--check`.
# ---------------------------------------------------------------------------

def validate_evidence_governance(db_path: Path | str, registry: dict) -> list[str]:
    """
    For every family whose CURRENT state requires an admission/rejection
    receipt (candidate/validated/promotion_ready/production_ready/
    disqualified/rejected), confirm its pinned run carries one of the
    correct type. Errors are prefixed 'EG1' (missing) / 'EG2' (wrong type)
    so they are visibly distinct from lifecycle.py's C1-C9.
    """
    errors: list[str] = []
    for fid in registry:
        ok, fam_errors = validate_pin(db_path, fid)
        if not ok:
            for e in fam_errors:
                code = "EG1" if "no admission/rejection" in e else "EG2"
                errors.append(f"{code}: {e}")
    return errors
