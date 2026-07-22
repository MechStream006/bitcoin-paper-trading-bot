"""
State Transition Governance — PR-8 of the Governance Architecture.

Makes the platform's state-transition system CONSUME the governance
architecture that PR-1..PR-7 built. Introduces no new governance concept:
`family.transition`, `family.promote` and `family.demote` have been reserved
in `receipts.DECISION_TAXONOMY` as **discretionary** since PR-1 and are
finally used here.

Dependency check (performed before implementation, per the task's explicit
stop condition): the existing transition model integrates with NO
architectural change to PR-1..PR-7.
  - taxonomy: family.transition/.promote/.demote already exist, already
    discretionary. Unchanged.
  - `ReceiptBody` already carries `from_state`/`to_state`. Unchanged.
  - `Subject.version_or_hash` already maps to the INDEXED `subject_version`
    column, so a receipt can pin the exact `state_transitions.id` it governs
    with NO schema change to PR-5's table. Unchanged.
  - the only refactor: `record.append_transition()` opened and committed its
    own connection, so a transition and its receipt could not share a
    transaction. Extracted `record.append_transition_on_connection()` --
    byte-for-byte the same validation, now callable inside a caller's
    transaction. This is exactly what PR-4 did to
    `receipt_ledger.append_receipt()` for exactly this reason, and it is why
    the arc / re-pin / grandfathering rules are NOT duplicated here (§7.3:
    two enforcement points must never diverge).

--- Why promotion is verified but never derived ---

`family.promote` is DISCRETIONARY, and that is load-bearing, not a label.
`receipts.replay()` dispatches on decision_class: a discretionary receipt is
`envelope-verified` -- its PRECONDITIONS are re-checked, but the CHOICE
itself is attested, never recomputed. This module therefore has no function
that decides whether to promote. `validate_transition()` answers "is this
promotion PERMITTED?"; a human answers "should it happen?", and their reason
is recorded verbatim. A system that derived promotions would be a system that
promotes on a threshold crossing at 3am with nobody accountable.

The asymmetry with PR-6 is deliberate and worth stating: `evidence.admit` is
`derived` (replay recomputes the gate bit-for-bit); `family.promote` is
`discretionary` (replay verifies the envelope and stops). Evidence
admissibility is a computation. Promotion is a judgment.

--- What this module does NOT do ---

Readiness (data_completeness_gate.md §7 G2 -- "the pinned run's evidence
window must be 100% READY") is CHECKED WHEN PRESENT and reported as
`not_computed` when absent. It is NOT yet blocking, because G3 (the
`readiness` block in `runs.metrics_json`) is that document's own PR-4 and
does not exist: no run on this platform carries a readiness block, so making
it mandatory would instantly invalidate all 35 real families -- a
backward-compatibility break masquerading as rigour. The hook is here and
tested; arming it is PR-9. This is stated explicitly rather than implied.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import evidence_governance as eg
import lifecycle
import record
import receipt_ledger as rl
from receipts import (
    ATTESTATION_BOOTSTRAP,
    Actor,
    Authority,
    EvaluationItem,
    InputRef,
    ReceiptBody,
    ReceiptError,
    ReplayResult,
    RuleRef,
    Subject,
    Verdict,
    canonical_json,
    now_iso,
    replay as replay_receipt_body,
    sha256_lf,
    verify_receipt,
)

# PR-9: bumped v1 -> v2. `rule.rule_hash` and `rule.thresholds_hash` change
# MEANING at this version -- under v1 both held the same combined policy digest;
# from v2 rule_hash is the STATE MACHINE alone and thresholds_hash is the
# THRESHOLDS alone (see state_machine_hash()).
#
# Bumping the version is not a workaround, it is the mechanism the platform
# already mandates: contract_versioning.md §3 -- "A check's semantics may never
# be redefined under an existing ID. A change in what a check means requires a
# new ID, which contracts then opt into explicitly. Old check IDs are retained
# and executable FOREVER." A silent redefinition would make every historical
# receipt replay against semantics it was never rendered under, while every hash
# still verified. So v1 receipts are never reinterpreted: verifiers dispatch on
# the RECORDED engine_version and report v1's hashes as non-separable rather
# than mis-reading them as a state-machine hash (see
# governance_verification.diagnose_state_machine_evolution()).
ENGINE_VERSION = "transition_governance.v2"

# The engine versions whose rule_hash is the STATE MACHINE alone. v1 predates
# the split and is deliberately absent -- not because v1 is untrustworthy, but
# because its rule_hash genuinely cannot answer "did the state machine change?".
ENGINE_VERSIONS_WITH_SPLIT_HASHES = frozenset({"transition_governance.v2"})

SUBJECT_KIND = "family"

DECISION_TRANSITION = "family.transition"
DECISION_PROMOTE = "family.promote"
DECISION_DEMOTE = "family.demote"

TRANSITION_DECISION_TYPES = frozenset({DECISION_TRANSITION, DECISION_PROMOTE, DECISION_DEMOTE})

# Which decision_type an arc is. Promotion and demotion are not "a transition
# with a nicer name" -- they are the arcs a reviewer will search the ledger
# for, and the taxonomy already separates them, so the ledger should too.
_PROMOTION_ARCS: frozenset[tuple[str, str]] = frozenset({
    ("candidate", "validated"),
    ("validated", "promotion_ready"),
    ("promotion_ready", "production_ready"),
})

_DEMOTION_ARCS: frozenset[tuple[str, str]] = frozenset({
    ("promotion_ready", "validated"),
    ("production_ready", "validated"),
})

_ACTION_FOR_DECISION = {
    DECISION_TRANSITION: "transition",
    DECISION_PROMOTE: "promote",
    DECISION_DEMOTE: "demote",
}


class TransitionGovernanceError(ReceiptError):
    """Base class for state-transition governance failures."""


class IllegalTransitionError(TransitionGovernanceError):
    """The transition failed validation and was refused BEFORE execution."""


class TransitionNotFoundError(TransitionGovernanceError):
    """A referenced state_transitions row does not exist."""


class DuplicateTransitionReceiptError(TransitionGovernanceError):
    """A transition already carries a governance receipt."""


def classify_arc(from_state: str, to_state: str) -> str:
    """The decision_type for an arc. Pure; no I/O."""
    if (from_state, to_state) in _PROMOTION_ARCS:
        return DECISION_PROMOTE
    if (from_state, to_state) in _DEMOTION_ARCS:
        return DECISION_DEMOTE
    return DECISION_TRANSITION


# ---------------------------------------------------------------------------
# Validation (item 3) — everything is checked BEFORE execution.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TransitionCheck:
    check_id: str
    passed: bool
    observed: str
    expected: str

    def to_dict(self) -> dict:
        return {"check_id": self.check_id, "passed": self.passed,
                "observed": self.observed, "expected": self.expected}


@dataclass(frozen=True)
class TransitionValidation:
    ok: bool
    checks: tuple[TransitionCheck, ...]
    errors: tuple[str, ...] = ()

    @property
    def failed(self) -> list[TransitionCheck]:
        return [c for c in self.checks if not c.passed]


def validate_transition(
    db_path: Path | str,
    family_id: str,
    to_state: str,
    justifying_run_id: str | None,
    family_meta: dict | None = None,
    thresholds: dict | None = None,
    from_state: str | None = None,
    readiness_provider: "readiness_gate.ReadinessProvider | None" = None,
) -> TransitionValidation:
    """
    Validate a transition BEFORE it is executed. Pure w.r.t. the ledger: it
    writes nothing, and is safe to call speculatively (e.g. to render "why
    can't I promote this?" without attempting the promotion).

    Checks, in order:
      TG-CUR   current state      -- the ledger's derived state is `from_state`
      TG-DST   destination state  -- to_state is a known lifecycle state
      TG-LEG   legality           -- the arc exists in ALLOWED_TRANSITIONS
      TG-PIN   pinned evidence    -- the run exists and belongs to this family
      TG-ADM   admissibility      -- PR-6's evidence.admit/.reject receipt of
                                     the type this destination requires
      TG-RDY   readiness          -- PR-7's readiness block, WHEN PRESENT
      TG-PRE   prerequisites      -- e.g. Validated's DSR/BH-FDR preconditions

    `from_state`, if given, must match the ledger's derived state -- supplying
    it is how a caller asserts what it BELIEVES the current state to be, so a
    concurrent transition that moved the family underneath it is caught rather
    than silently overwritten (a lost-update guard).
    """
    checks: list[TransitionCheck] = []
    errors: list[str] = []

    derived_state, _pinned, _ts = record.derive_state_at(db_path, family_id)
    current = derived_state or "discovery"

    # TG-CUR — current state
    cur_ok = from_state is None or from_state == current
    checks.append(TransitionCheck(
        "TG-CUR.current_state", cur_ok,
        observed=f"ledger-derived state={current!r}",
        expected=f"from_state={from_state!r}" if from_state is not None else "any (not asserted)",
    ))
    if not cur_ok:
        errors.append(
            f"{family_id}: caller asserts from_state={from_state!r} but the ledger derives "
            f"{current!r} -- the family moved underneath this call; re-read and retry"
        )

    # TG-DST — destination state
    dst_ok = to_state in lifecycle.VALID_STATES
    checks.append(TransitionCheck(
        "TG-DST.destination_state", dst_ok,
        observed=f"to_state={to_state!r}",
        expected=f"one of {sorted(lifecycle.VALID_STATES)}",
    ))
    if not dst_ok:
        errors.append(f"{family_id}: unknown destination state {to_state!r}")
        return TransitionValidation(False, tuple(checks), tuple(errors))

    # TG-LEG — transition legality
    leg_ok = lifecycle.can_transition(current, to_state)
    checks.append(TransitionCheck(
        "TG-LEG.legality", leg_ok,
        observed=f"{current!r} -> {to_state!r}",
        expected=f"an arc in ALLOWED_TRANSITIONS[{current!r}] = "
                 f"{sorted(lifecycle.ALLOWED_TRANSITIONS.get(current, frozenset()))}",
    ))
    if not leg_ok:
        errors.append(
            f"{family_id}: illegal arc {current!r} -> {to_state!r}. Allowed from {current!r}: "
            f"{sorted(lifecycle.ALLOWED_TRANSITIONS.get(current, frozenset()))}"
        )

    # TG-PIN — pinned evidence
    run = None
    needs_run = to_state in lifecycle.STATES_REQUIRING_RUN or current == to_state
    if justifying_run_id:
        run = record.get_run(db_path, justifying_run_id)
        if run is None:
            pin_ok, observed = False, f"run {justifying_run_id[:8]}… not found in runs"
        elif run["family_id"] != family_id:
            pin_ok, observed = False, f"run belongs to family {run['family_id']!r}"
        else:
            pin_ok, observed = True, f"run {justifying_run_id[:8]}… of {family_id!r}"
    else:
        pin_ok = not needs_run
        observed = "no justifying_run_id"

    checks.append(TransitionCheck(
        "TG-PIN.pinned_evidence", pin_ok, observed=observed,
        expected=("a run of this family" if needs_run else "a run of this family, or none"),
    ))
    if not pin_ok:
        errors.append(f"{family_id}: pinned evidence invalid -- {observed}")

    # TG-ADM — admissibility (PR-6)
    required_receipt = eg._REQUIRED_RECEIPT_TYPE_FOR_STATE.get(to_state)
    if required_receipt is None:
        checks.append(TransitionCheck(
            "TG-ADM.admissibility", True,
            observed="n/a", expected=f"no admission receipt required for {to_state!r}",
        ))
    elif not justifying_run_id or run is None:
        checks.append(TransitionCheck(
            "TG-ADM.admissibility", False,
            observed="no run to carry an admission receipt",
            expected=f"a {required_receipt!r} receipt",
        ))
        errors.append(f"{family_id}: {to_state!r} requires a {required_receipt!r} receipt but no valid run is pinned")
    else:
        receipt = eg.get_admission_receipt(db_path, justifying_run_id)
        if receipt is None:
            adm_ok, adm_obs = False, "run carries no admission/rejection receipt"
            errors.append(
                f"{family_id}: run {justifying_run_id[:8]}… has no admission receipt; "
                f"call evidence_governance.admit_evidence()/reject_evidence() before transitioning"
            )
        elif receipt["decision_type"] != required_receipt:
            adm_ok, adm_obs = False, f"run carries {receipt['decision_type']!r}"
            errors.append(
                f"{family_id}: {to_state!r} requires a {required_receipt!r} receipt but the pinned "
                f"run carries {receipt['decision_type']!r}"
            )
        else:
            adm_ok, adm_obs = True, f"{receipt['decision_type']} receipt {receipt['receipt_id'][:8]}…"
        checks.append(TransitionCheck(
            "TG-ADM.admissibility", adm_ok, observed=adm_obs, expected=f"a {required_receipt!r} receipt",
        ))

    # TG-RDY — readiness (PR-7 framework, armed at the Validated gate by PR-9).
    rdy_check = _readiness_check(db_path, run, to_state, readiness_provider)
    checks.append(rdy_check)
    if not rdy_check.passed:
        errors.append(f"{family_id}: {rdy_check.observed}")

    # TG-PRE — governance prerequisites
    if to_state == "validated" and run is not None and thresholds is not None:
        # Proposal 1: peers are derived from the LEDGER, not from a registry
        # argument. PR-8 made ledger-derived state authoritative for
        # transitions, and `family_meta` is optional here -- a caller that
        # passed no registry must not thereby promote into an unchecked peer
        # set. Deriving means the peer set is the same one TG-CUR just used.
        all_states = record.derive_all_states_at(db_path)
        validated_peers = {
            fid for fid, (st, *_ ) in all_states.items()
            if st == "validated" and fid != family_id
        }
        prec_ok, prec_errs = lifecycle.check_validated_preconditions(
            family_meta or {}, run, thresholds,
            pairwise_rows=record.get_pairwise_r(db_path),
            validated_peers=validated_peers,
            family_id=family_id,
        )
        checks.append(TransitionCheck(
            "TG-PRE.prerequisites", prec_ok,
            observed=("; ".join(prec_errs) if prec_errs
                      else f"DSR/BH-FDR/provenance satisfied; independent of "
                           f"{len(validated_peers)} Validated peer(s)"),
            expected="check_validated_preconditions() passes",
        ))
        for e in prec_errs:
            errors.append(f"{family_id}: {e}")
    else:
        checks.append(TransitionCheck(
            "TG-PRE.prerequisites", True,
            observed="n/a",
            expected=f"no structural preconditions declared for {to_state!r}",
        ))

    ok = all(c.passed for c in checks)
    return TransitionValidation(ok, tuple(checks), tuple(errors))


def _readiness_check(
    db_path: Path | str,
    run: dict | None,
    to_state: str,
    provider: "readiness_gate.ReadinessProvider | None" = None,
) -> TransitionCheck:
    """
    data_completeness_gate.md §7 **G2**, armed by PR-9.

    Two regimes, and the asymmetry is G2's, not an invention here:

      * Entering **validated** (`readiness_gate.STATES_REQUIRING_READINESS`):
        the evidence window MUST be 100% READY. Absence of readiness evidence
        **blocks** -- L2, "absence of evidence of completeness is not evidence of
        completeness", with the same no-silent-bypass semantics as the existing
        NULL-DSR hard-fail. This has a real, intended consequence: until a
        readiness producer exists, nothing can be validated. That is correct; the
        alternative is promoting on data nobody checked.

      * Every other state: readiness is REPORTED, never blocking -- unchanged
        from PR-8. A non-computed readiness renders as `not_computed`, never as a
        bare pass, preserving PR-5's rendering discipline: "we did not look" must
        never be indistinguishable from "we looked and it was fine".

    Backward compatible by construction: the real ledger has zero validated
    families, so arming this gate invalidates nothing that exists.
    """
    import readiness_gate

    enforced = readiness_gate.requires_readiness(to_state)
    expected = (
        "every day in the evidence window READY (or declared-degraded) -- required to enter "
        f"{to_state!r}"
        if enforced else
        "a readiness assessment, when one can be computed (reported, not enforced, for "
        f"{to_state!r})"
    )

    if run is None:
        return TransitionCheck(
            "TG-RDY.readiness", not enforced,
            observed=("n/a (no pinned run)" if not enforced
                      else "no pinned run, so no evidence window can be assessed"),
            expected=expected,
        )

    assessment = readiness_gate.assess(db_path, run, provider)

    if not enforced:
        return TransitionCheck(
            "TG-RDY.readiness", True,
            observed=(assessment.detail if assessment.computed
                      else f"not_computed -- {assessment.detail}"),
            expected=expected,
        )

    return TransitionCheck("TG-RDY.readiness", assessment.ok, observed=assessment.detail,
                           expected=expected)


# ---------------------------------------------------------------------------
# Receipt construction
# ---------------------------------------------------------------------------

def state_machine_hash() -> str:
    """
    Identity of the STATE MACHINE alone -- `ALLOWED_TRANSITIONS` and
    `STATES_REQUIRING_RUN`, with no thresholds mixed in.

    PR-9 (the enhancement PR-8's audit identified and PR-8 deferred). PR-8's
    `_policy_hash()` mixed the state machine AND thresholds into one digest, and
    `replay_transition_receipt(db, receipt_id)` receives no thresholds -- so it
    could not recompute the recorded hash, and therefore never compared it. The
    consequence, demonstrated in the PR-8 audit: a WIDENED state machine replayed
    green despite the governing rules having changed, and a NARROWED one reported
    "illegal arc -- either the receipt is forged, or the state machine changed",
    unable to distinguish the two.

    Separating the digests fixes both, and needs NO change to `receipts.py`:
    `RuleRef` already carries `rule_hash` AND `thresholds_hash` as distinct
    fields. PR-8 simply set both (and `authority.policy_hash`) to the same
    combined value. From `transition_governance.v2` they mean what their names
    say. `authority.policy_hash` remains the COMBINED digest -- unchanged in
    meaning -- so the receipt still pins the full policy in force.

    Deliberately EXCLUDES `ENGINE_VERSION`. PR-8's combined hash included it, so
    the digest changed whenever the module version changed even if the state
    machine did not -- which would make "did the state machine change?"
    unanswerable across an engine bump, i.e. exactly the question this hash
    exists to answer. The engine version is recorded separately, in
    `rule.engine_version`.
    """
    doc = {
        "allowed_transitions": {k: sorted(v) for k, v in sorted(lifecycle.ALLOWED_TRANSITIONS.items())},
        "states_requiring_run": sorted(lifecycle.STATES_REQUIRING_RUN),
    }
    return sha256_lf(canonical_json(doc))


def thresholds_hash(thresholds: dict | None) -> str:
    """
    Identity of the THRESHOLDS alone. Reuses PR-6's decimalizer verbatim --
    thresholds.yaml is full of floats, and canonical_json() correctly refuses to
    hash a raw IEEE float (governance_receipts.md §1.3 rule 3).
    """
    return sha256_lf(canonical_json(
        {"thresholds": eg._decimalize(thresholds) if thresholds is not None else None}
    ))


def _policy_hash(thresholds: dict | None) -> str:
    """
    The COMBINED policy digest -- state machine + thresholds + engine version.
    Recorded as `authority.policy_hash`; its meaning is unchanged from PR-8, so
    an operator comparing an old and a new receipt's policy_hash still compares
    like with like.
    """
    doc = {
        "engine_version": ENGINE_VERSION,
        "allowed_transitions": {k: sorted(v) for k, v in sorted(lifecycle.ALLOWED_TRANSITIONS.items())},
        "states_requiring_run": sorted(lifecycle.STATES_REQUIRING_RUN),
        "thresholds": eg._decimalize(thresholds) if thresholds is not None else None,
    }
    return sha256_lf(canonical_json(doc))


def build_transition_body(
    family_id: str,
    transition_id: int,
    from_state: str,
    to_state: str,
    decision_type: str,
    justifying_run_id: str | None,
    validation: TransitionValidation,
    admission_receipt_id: str | None,
    actor: str,
    actor_type: str,
    reason: str,
    thresholds: dict | None,
    ts: str,
) -> ReceiptBody:
    policy_hash = _policy_hash(thresholds)
    sm_hash = state_machine_hash()
    thr_hash = thresholds_hash(thresholds)
    inputs: list[InputRef] = []
    if justifying_run_id:
        inputs.append(InputRef(kind="evidence_run", id=justifying_run_id, hash=justifying_run_id))
    if admission_receipt_id:
        inputs.append(InputRef(kind="receipt", id=admission_receipt_id, hash=admission_receipt_id))

    return ReceiptBody(
        decision_type=decision_type,
        decision_class="discretionary",
        action=_ACTION_FOR_DECISION[decision_type],
        # version_or_hash pins the EXACT state_transitions row this receipt
        # governs. subject_version is already an indexed column (PR-1), so this
        # link needs no schema change to PR-5's table.
        subject=Subject(kind=SUBJECT_KIND, id=family_id, version_or_hash=str(transition_id)),
        from_state=from_state,
        to_state=to_state,
        as_of=ts,
        decided_at=now_iso(),
        actor=Actor(type=actor_type, id=actor, key_id=None),
        authority=Authority(policy_id="lifecycle.state_machine", policy_hash=policy_hash, role=None),
        inputs=tuple(inputs),
        # PR-9: rule_hash and thresholds_hash now mean what their names say.
        # `authority.policy_hash` (above) remains the COMBINED digest, unchanged
        # from PR-8, so the receipt still pins the whole policy in force while
        # ALSO making "did the state machine alone change?" answerable.
        rule=RuleRef(
            rule_id="lifecycle.validate_transition",
            rule_hash=sm_hash,
            engine_version=ENGINE_VERSION,
            thresholds_hash=thr_hash,
        ),
        # The PRECONDITIONS are recorded -- these are what replay re-verifies.
        # The DECISION is not among them: it is the human's, and it is attested
        # in reason_text, never recomputed.
        evaluation=tuple(
            EvaluationItem(check_id=c.check_id, observed=c.observed,
                           expected=c.expected, passed=c.passed)
            for c in validation.checks
        ),
        verdict=Verdict(result=to_state, reason_code=None),
        reason_text=reason,
    )


# ---------------------------------------------------------------------------
# Execution (items 1-2) — atomic: transition + receipt, or neither.
# ---------------------------------------------------------------------------

def transition_with_receipt(
    db_path: Path | str,
    family_id: str,
    to_state: str,
    justifying_run_id: str | None = None,
    actor: str = "operator",
    actor_type: str = "human",
    reason: str = "",
    family_meta: dict | None = None,
    thresholds: dict | None = None,
    from_state: str | None = None,
    ts: str | None = None,
    readiness_provider: "readiness_gate.ReadinessProvider | None" = None,
) -> dict:
    """
    The governed front door. Validate -> execute -> receipt, atomically.

    Returns {"transition_id", "receipt", "validation", "from_state", "to_state"}.

    Validation happens BEFORE execution (item 3): an invalid transition raises
    IllegalTransitionError and writes NOTHING -- neither a transition row nor a
    receipt. There is no state in which an illegal transition exists in the
    ledger "flagged as bad": it never enters.

    Transition and receipt commit in ONE transaction. There is no state where a
    transition exists without its receipt, or a receipt without its transition.

    `reason` is REQUIRED and must be non-empty: every decision type this module
    emits is discretionary, and validate_body() enforces "reason_text is
    required for discretionary decisions" (governance_receipts.md §1.1 --
    "BECAUSE reason_text"). Checked here first so the failure names the real
    problem rather than surfacing from deep inside the ledger.
    """
    if not (reason or "").strip():
        raise IllegalTransitionError(
            f"{family_id}: a state transition is a discretionary governance act and REQUIRES a "
            f"non-empty reason. The ledger records why, not merely what."
        )

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 5000")
    try:
        # BEGIN IMMEDIATE **first**. Everything authoritative -- the state
        # derivation, the validation, and both writes -- happens inside this one
        # transaction, under the write lock.
        #
        # PR-8 review correction. Previously the state was derived BEFORE the
        # lock, which is a lost update: two concurrent transitioners both read
        # 'discovery', both validated against it, and both wrote -- leaving a
        # permanently inconsistent chain in an append-only table (TG4 detected it
        # afterwards; nothing could repair it). Reading under the lock is
        # verbatim contract_lifecycle.next_version()'s contract (PR-3): the
        # second caller's read does not proceed until the first has committed or
        # rolled back, so it sees the first's transition and validates against
        # reality rather than against a stale snapshot.
        con.execute("BEGIN IMMEDIATE")
        try:
            # Authoritative state, read on the LOCKED connection (PR-3 pattern).
            derived_state, _pinned, _ts = record.derive_state_on_connection(con, family_id)
            current = derived_state or "discovery"

            # `ts` is stamped HERE, under the lock -- not when this function was
            # entered. Found by this PR's own 5-writer concurrency test: a ts
            # assigned before BEGIN IMMEDIATE can be EARLIER than that of a writer
            # who acquired the lock first, so ts order disagrees with commit
            # order, and TG4 (which reads the chain in (ts, id) order) reports a
            # broken chain for a ledger that is in fact correctly serialised.
            # Under the lock, ts order and commit order agree, and `id` breaks any
            # same-instant tie in commit order. An explicitly supplied ts
            # (bootstrap/backfill) is always honoured.
            effective_ts = ts or now_iso()

            # Validated under the same lock. validate_transition() opens its own
            # read connections; SQLite's RESERVED lock admits concurrent readers
            # but no other writer, and nothing has been written in this
            # transaction yet -- so those reads observe exactly the committed
            # state `current` was derived from, and no writer can move it
            # underneath us before COMMIT.
            validation = validate_transition(
                db_path, family_id, to_state, justifying_run_id,
                family_meta=family_meta, thresholds=thresholds, from_state=from_state,
                readiness_provider=readiness_provider,
            )
            if not validation.ok:
                raise IllegalTransitionError(
                    f"transition refused for {family_id!r} -> {to_state!r}:\n  "
                    + "\n  ".join(validation.errors
                                  or [f"failed: {c.check_id}" for c in validation.failed])
                )

            decision_type = classify_arc(current, to_state)

            admission_receipt_id = None
            if justifying_run_id:
                adm = eg.get_admission_receipt(db_path, justifying_run_id)
                admission_receipt_id = adm["receipt_id"] if adm else None

            transition_id = record.append_transition_on_connection(
                con, family_id, current, to_state,
                reason=reason, justifying_run_id=justifying_run_id, actor=actor,
                ts=effective_ts,
            )
            body = build_transition_body(
                family_id, transition_id, current, to_state, decision_type,
                justifying_run_id, validation, admission_receipt_id,
                actor, actor_type, reason, thresholds, effective_ts,
            )
            receipt = rl.append_receipt_on_connection(con, body)
        except Exception:
            con.rollback()
            raise
        else:
            con.commit()
    finally:
        con.close()

    return {
        "transition_id": transition_id, "receipt": receipt, "validation": validation,
        "from_state": current, "to_state": to_state,
    }


def promote_with_receipt(
    db_path: Path | str,
    family_id: str,
    to_state: str,
    justifying_run_id: str,
    actor: str,
    reason: str,
    family_meta: dict | None = None,
    thresholds: dict | None = None,
    from_state: str | None = None,
    actor_type: str = "human",
    readiness_provider: "readiness_gate.ReadinessProvider | None" = None,
) -> dict:
    """
    Promote a family. **Discretionary. Never derived.**

    This function does not decide anything. It refuses a promotion that is not
    PERMITTED, and records one that a human CHOSE. There is deliberately no
    `auto_promote()`, no `promote_if_eligible()`, and no scheduler entry point
    -- passing the gates makes a promotion legal, never automatic. The gates
    are necessary conditions, not a decision procedure.

    `actor_type` defaults to "human" and a service actor is refused: the
    platform must not be able to promote itself. If a future automation needs
    to *propose* promotions, it should open a review, not call this.

    Refuses any arc that is not a promotion arc -- use transition_with_receipt()
    for those -- so a demotion can never be mislabelled `family.promote` in the
    ledger.
    """
    if actor_type != "human":
        raise IllegalTransitionError(
            f"promotion requires a human actor (got actor_type={actor_type!r}). Promotion is a "
            f"discretionary governance act: it is never derived, and the platform must not be "
            f"able to promote itself."
        )

    derived_state, _pinned, _ts = record.derive_state_at(db_path, family_id)
    current = from_state if from_state is not None else (derived_state or "discovery")
    if (current, to_state) not in _PROMOTION_ARCS:
        raise IllegalTransitionError(
            f"{current!r} -> {to_state!r} is not a promotion arc. Promotion arcs: "
            f"{sorted(_PROMOTION_ARCS)}. Use transition_with_receipt() for other arcs."
        )

    # The arc check above necessarily runs OUTSIDE the write lock (it decides
    # whether to call at all). Passing `current` as from_state makes that check
    # binding: TG-CUR re-asserts it against the authoritative state read under
    # the lock, so if another writer moves the family in between, the transition
    # is REFUSED rather than silently re-classified.
    #
    # Without this, a promotion validated as candidate->validated could, if the
    # family reached 'validated' concurrently, land inside the lock as a legal
    # validated->validated re-pin and be recorded as a family.transition -- a
    # promotion request quietly writing a non-promotion receipt. Refusing is the
    # only correct answer: the operator asked to promote something that no
    # longer needs promoting.
    return transition_with_receipt(
        db_path, family_id, to_state, justifying_run_id=justifying_run_id,
        actor=actor, actor_type=actor_type, reason=reason,
        family_meta=family_meta, thresholds=thresholds, from_state=current,
        readiness_provider=readiness_provider,
    )


# ---------------------------------------------------------------------------
# Bootstrap (transitions that predate this PR)
# ---------------------------------------------------------------------------

def bootstrap_transition_receipt(
    db_path: Path | str,
    transition: dict,
    attestation_note: str,
    actor: str = "bootstrap",
) -> dict:
    """
    A receipt for a transition made before PR-8 existed.

    `attestation='bootstrap'` (governance_receipts.md §6, "bootstrap honesty")
    -- the precedent PR-1's migrate_bootstrap_transitions.py set and PR-6's
    evidence-admission migration followed. The validation that justified these
    transitions was never captured in a replayable form; fabricating an
    evaluation[] for them would be a lie with a valid hash. replay() refuses on
    them; verify() (the hash check) holds forever.

    The justifying run IS carried, when the transition row has one: that is a
    fact already recorded at the time, not a reconstruction, so omitting it
    would discard real lineage for no reason. Without it, reconstruct_state_at()
    would return pinned_run_id=None for every pre-PR-8 family while
    record.derive_state_at() returned the real run -- a gap that TG5 (which
    compares only `state`) would not catch, and that would quietly make the
    receipt chain a weaker record of history than the table it governs.
    What is NOT carried is an evaluation[]: those preconditions were never
    checked at the time, and inventing them is the lie bootstrap honesty exists
    to prevent.
    """
    decision_type = classify_arc(transition["from_state"], transition["to_state"])
    inputs: tuple[InputRef, ...] = ()
    if transition.get("justifying_run_id"):
        inputs = (InputRef(kind="evidence_run", id=transition["justifying_run_id"],
                           hash=transition["justifying_run_id"]),)

    body = ReceiptBody(
        decision_type=decision_type,
        decision_class="discretionary",
        action=_ACTION_FOR_DECISION[decision_type],
        subject=Subject(kind=SUBJECT_KIND, id=transition["family_id"],
                        version_or_hash=str(transition["id"])),
        inputs=inputs,
        from_state=transition["from_state"],
        to_state=transition["to_state"],
        as_of=transition["ts"],
        decided_at=now_iso(),
        actor=Actor(type="service", id=actor, key_id=None),
        authority=Authority(policy_id="bootstrap", policy_hash=sha256_lf(b"bootstrap"), role=None),
        rule=RuleRef(rule_id="bootstrap", rule_hash=None, engine_version=None, thresholds_hash=None),
        verdict=Verdict(result=transition["to_state"], reason_code=None),
        reason_text=transition.get("reason") or "(no reason recorded at the time)",
        attestation=ATTESTATION_BOOTSTRAP,
        attestation_note=attestation_note,
    )
    return rl.append_receipt(db_path, body)


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def get_transition_receipts(db_path: Path | str, family_id: str) -> list[dict]:
    """Every transition/promotion/demotion receipt for a family, in seq order."""
    rows = rl.get_receipts_by_subject(db_path, SUBJECT_KIND, family_id)
    return [r for r in rows if r["decision_type"] in TRANSITION_DECISION_TYPES]


def get_receipt_for_transition(db_path: Path | str, family_id: str, transition_id: int) -> dict | None:
    """The receipt governing one exact state_transitions row. Raises if more
    than one exists -- that is a duplicate, and silently returning the first
    would hide it."""
    matches = [
        r for r in get_transition_receipts(db_path, family_id)
        if r["subject_version"] == str(transition_id)
    ]
    if not matches:
        return None
    if len(matches) > 1:
        raise DuplicateTransitionReceiptError(
            f"transition #{transition_id} ({family_id}) has {len(matches)} governance receipts; "
            f"exactly one is permitted (direct DB write?)"
        )
    return matches[0]


def get_promotions(db_path: Path | str, family_id: str | None = None) -> list[dict]:
    rows = rl.get_receipts_by_type(db_path, DECISION_PROMOTE)
    return [r for r in rows if family_id is None or r["subject_id"] == family_id]


# ---------------------------------------------------------------------------
# Point-in-time reconstruction (item 5) — from RECEIPTS.
# ---------------------------------------------------------------------------

def reconstruct_state_at(
    db_path: Path | str, family_id: str, as_of: str | None = None
) -> tuple[str | None, str | None, str | None]:
    """
    Reconstruct a family's state at `as_of` **from transition receipts** --
    not from state_transitions, and never from registry.yaml.

    Returns (state, justifying_run_id, ts), mirroring record.derive_state_at()
    exactly so the two are directly comparable. That comparison is the point:
    they are two INDEPENDENT derivations of the same fact, and
    validate_transition_governance() (TG5) asserts they agree. A divergence
    means either a transition was written without a receipt, or a receipt was
    forged -- both of which are invisible if only one source is consulted.

    `as_of`, per the convention record.py established, must already be a
    fully-qualified ISO datetime (a bare "YYYY-MM-DD" would silently exclude
    same-day events).
    """
    receipts = get_transition_receipts(db_path, family_id)
    latest = None
    for r in receipts:
        body = r["body"]
        if as_of is not None and body["as_of"] > as_of:
            continue
        if latest is None or (body["as_of"], r["seq"]) >= (latest["body"]["as_of"], latest["seq"]):
            latest = r
    if latest is None:
        return None, None, None
    run_id = next(
        (i["id"] for i in (latest["body"].get("inputs") or []) if i["kind"] == "evidence_run"), None
    )
    return latest["body"]["to_state"], run_id, latest["body"]["as_of"]


def reconstruct_all_states_at(db_path: Path | str, as_of: str | None = None) -> dict[str, tuple]:
    """{family_id: (state, justifying_run_id, ts)} from receipts. See
    reconstruct_state_at()."""
    families = {
        r["subject_id"] for r in rl.get_all_receipts(db_path)
        if r["decision_type"] in TRANSITION_DECISION_TYPES
    }
    out = {}
    for fid in sorted(families):
        state, run_id, ts = reconstruct_state_at(db_path, fid, as_of)
        if state is not None:
            out[fid] = (state, run_id, ts)
    return out


# ---------------------------------------------------------------------------
# Replay (item 4)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TransitionReplay:
    receipt_id: str
    transition_id: int | None
    hash_ok: bool
    legality_ok: bool
    replay: ReplayResult
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.hash_ok and self.legality_ok and self.replay.ok


def replay_transition_receipt(db_path: Path | str, receipt_id: str) -> TransitionReplay:
    """
    Deterministically replay a historical transition.

    Verifies exactly three things, and deliberately not a fourth:
      1. RECEIPT INTEGRITY -- receipt_id recomputes from the stored body.
      2. TRANSITION LEGALITY -- the recorded from_state -> to_state arc is
         still legal under the state machine.
      3. GOVERNANCE ENVELOPE -- via receipts.replay(), which for a
         discretionary decision checks the envelope (known decision_type, an
         actor, a non-empty reason_text) and stops.

    It does NOT re-decide the promotion. `family.promote` is discretionary, so
    `receipts.replay()` returns mode='envelope-verified' -- "preconditions/
    envelope verified; the choice itself is attested, not recomputed". Replay
    must never attempt to reproduce discretionary human judgment: re-running
    today's thresholds against a 2026 promotion would not audit that decision,
    it would silently replace it with a different one and call the difference a
    failure.

    Bootstrap-attested receipts refuse replay by design (they never captured a
    replayable evaluation), but their hash and legality are still checked --
    those hold regardless.
    """
    receipt = rl.get_receipt(db_path, receipt_id)
    if receipt is None:
        raise TransitionNotFoundError(f"receipt_id {receipt_id} not found in governance_receipts")
    if receipt["decision_type"] not in TRANSITION_DECISION_TYPES:
        raise TransitionGovernanceError(
            f"receipt {receipt_id[:8]}… is a {receipt['decision_type']!r}, not a transition receipt"
        )

    body = receipt["body"]
    errors: list[str] = []

    hash_ok = verify_receipt(body, receipt["receipt_id"])
    if not hash_ok:
        errors.append("receipt does not hash to its stored body (TAMPERING)")

    from_state, to_state = body["from_state"], body["to_state"]
    legality_ok = lifecycle.can_transition(from_state, to_state)
    if not legality_ok:
        errors.append(
            f"arc {from_state!r} -> {to_state!r} is not legal under the current state machine. "
            f"Either the receipt is forged, or the state machine changed under recorded history."
        )

    # No rule_fn: a discretionary receipt is envelope-verified. Supplying one
    # would be a category error -- it would recompute a human's choice.
    result = replay_receipt_body(body)
    if not result.ok and result.mode != "refused":
        errors.append(result.detail)

    transition_id = None
    if body["subject"]["version_or_hash"] is not None:
        try:
            transition_id = int(body["subject"]["version_or_hash"])
        except (TypeError, ValueError):
            errors.append(f"subject.version_or_hash {body['subject']['version_or_hash']!r} is not a transition id")

    return TransitionReplay(
        receipt_id=receipt_id, transition_id=transition_id, hash_ok=hash_ok,
        legality_ok=legality_ok, replay=result, errors=tuple(errors),
    )


# ---------------------------------------------------------------------------
# Governance validation (item 6)
# ---------------------------------------------------------------------------

def validate_transition_governance(db_path: Path | str, registry: dict | None = None) -> list[str]:
    """
    Ledger-wide transition-governance invariants. Codes are TG-prefixed so they
    are visibly distinct from lifecycle.py's C1-C9, evidence_governance's
    EG1/EG2, and dq's DQ-*.

      TG1  every transition carries exactly one governance receipt
      TG2  no duplicate receipts for one transition; no orphan receipts
      TG3  every recorded arc is legal under the state machine
      TG4  transition ordering: each from_state equals the previous to_state,
           and ts is non-decreasing within a family
      TG5  receipt-derived state == ledger-derived state (two independent
           derivations of the same fact must agree)
      TG6  every transition receipt replays (envelope + integrity)
    """
    errors: list[str] = []
    transitions = record.get_all_transitions(db_path)

    by_family: dict[str, list[dict]] = {}
    for t in transitions:
        by_family.setdefault(t["family_id"], []).append(t)

    receipts_by_family: dict[str, list[dict]] = {}
    for r in rl.get_all_receipts(db_path):
        if r["decision_type"] in TRANSITION_DECISION_TYPES:
            receipts_by_family.setdefault(r["subject_id"], []).append(r)

    # TG1 / TG2 — receipt presence and uniqueness
    for family_id, rows in by_family.items():
        fam_receipts = receipts_by_family.get(family_id, [])
        by_transition: dict[str, list[dict]] = {}
        for r in fam_receipts:
            by_transition.setdefault(r["subject_version"], []).append(r)

        for t in rows:
            matches = by_transition.get(str(t["id"]), [])
            if not matches:
                errors.append(
                    f"TG1: transition #{t['id']} ({family_id}: {t['from_state']} -> {t['to_state']}) "
                    f"has no governance receipt -- written via the raw record.append_transition() "
                    f"rather than transition_governance.transition_with_receipt()?"
                )
            elif len(matches) > 1:
                errors.append(
                    f"TG2: transition #{t['id']} ({family_id}) has {len(matches)} governance "
                    f"receipts; exactly one is permitted"
                )

        known_ids = {str(t["id"]) for t in rows}
        for r in fam_receipts:
            if r["subject_version"] not in known_ids:
                errors.append(
                    f"TG2: receipt {r['receipt_id'][:8]}… ({family_id}) references transition "
                    f"#{r['subject_version']}, which does not exist in state_transitions"
                )

    for family_id in receipts_by_family:
        if family_id not in by_family:
            errors.append(
                f"TG2: receipts exist for family {family_id!r} but it has no state_transitions rows"
            )

    # TG3 / TG4 — legality and ordering
    for family_id, rows in by_family.items():
        ordered = sorted(rows, key=lambda t: (t["ts"], t["id"]))
        expected_from = "discovery"
        prev_ts = None
        for t in ordered:
            if not lifecycle.can_transition(t["from_state"], t["to_state"]):
                errors.append(
                    f"TG3: transition #{t['id']} ({family_id}) records an illegal arc "
                    f"{t['from_state']!r} -> {t['to_state']!r}"
                )
            if t["from_state"] != expected_from:
                errors.append(
                    f"TG4: transition #{t['id']} ({family_id}) starts from {t['from_state']!r} but the "
                    f"previous transition left it in {expected_from!r} -- the chain is broken"
                )
            if prev_ts is not None and t["ts"] < prev_ts:
                errors.append(
                    f"TG4: transition #{t['id']} ({family_id}) ts={t['ts']} precedes the previous "
                    f"transition's ts={prev_ts} -- ordering violation"
                )
            expected_from = t["to_state"]
            prev_ts = t["ts"]

    # TG5 — the two independent derivations must agree
    ledger_states = record.derive_all_states_at(db_path, as_of=None)
    receipt_states = reconstruct_all_states_at(db_path, as_of=None)
    for family_id, (state, _run, _ts) in ledger_states.items():
        rec = receipt_states.get(family_id)
        if rec is None:
            continue  # already reported as TG1
        if rec[0] != state:
            errors.append(
                f"TG5: {family_id} derives to {state!r} from state_transitions but {rec[0]!r} from "
                f"transition receipts -- the two records of the same decision disagree"
            )

    # TG6 — every transition receipt replays
    for family_id, fam_receipts in receipts_by_family.items():
        for r in fam_receipts:
            try:
                result = replay_transition_receipt(db_path, r["receipt_id"])
            except TransitionGovernanceError as exc:
                errors.append(f"TG6: receipt {r['receipt_id'][:8]}… ({family_id}) raised on replay: {exc}")
                continue
            if result.replay.mode == "refused":
                # bootstrap: refuses by design. Integrity/legality still apply.
                if not result.hash_ok:
                    errors.append(f"TG6: receipt {r['receipt_id'][:8]}… ({family_id}) fails hash verification")
                if not result.legality_ok:
                    errors.append(f"TG6: receipt {r['receipt_id'][:8]}… ({family_id}) records an illegal arc")
                continue
            if not result.ok:
                errors.append(
                    f"TG6: receipt {r['receipt_id'][:8]}… ({family_id}) does not replay: "
                    + "; ".join(result.errors)
                )

    return errors
