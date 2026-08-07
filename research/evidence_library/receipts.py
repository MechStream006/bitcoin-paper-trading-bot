"""
Governance Receipt Foundation — data model, canonicalisation, hashing, validation.

Implements PR-1 of docs/governance/governance_receipts.md. This module is pure
(no I/O, no SQLite) so hash stability and canonicalisation can be tested without
a database. Persistence lives in receipt_ledger.py.

No existing call site imports this module yet. Nothing in the platform emits a
receipt as of this PR — that wiring is out of scope here (see the design doc's
per-subsystem PRs). This file only makes it possible.

Hashing convention: SHA-256 of LF-normalised bytes, identical to
forward_evidence/pre_reg_guard.py's _sha256_lf(). One hashing convention on this
platform, not two. receipt_id is a bare hex digest (no "sha256:" prefix), matching
contract_hash / fn_hash elsewhere in the codebase.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Iterable

SCHEMA_VERSION = 1

DECISION_CLASSES = frozenset({"derived", "discretionary"})

ACTIONS = frozenset({
    "activate", "retire", "admit", "reject", "promote",
    "demote", "transition", "reverse",
    # PR-7 (Data Completeness Framework). `readiness.verdict` has been in
    # DECISION_TAXONOMY since PR-1, but no verb described what a readiness
    # evaluation DOES -- a gap that only surfaced when something finally
    # emitted one. Purely ADDITIVE: no existing receipt uses it, no existing
    # validation is loosened, and every receipt written under PR-1..PR-6
    # verifies and replays byte-identically.
    #
    # Deliberately NOT reusing "admit"/"reject": a READY verdict means
    # COMPLETE, not ADMISSIBLE AS EVIDENCE. data_completeness_gate.md §7 G5 is
    # explicit that readiness and admissibility are INDEPENDENT gates, both
    # required -- Delta testnet data can be perfectly complete and still be
    # economically synthetic. Overloading the admission verbs here would
    # encode exactly the conflation the spec warns against.
    "evaluate",
    # Phase 3 Label Store roadmap v1.1.0 (WBS 3.8a). Purely additive, same
    # discipline as "evaluate" above.
    "register", "adjudicate",
    # Phase 4 Dataset Builder roadmap v1.0.2 (WBS 4.5), which names
    # `dataset.seal` receipts in its own acceptance criteria. ADR-I012 DG-1:
    # "Sealing is a human act emitting a receipt (IQROS-P8, P10)." No existing
    # verb described sealing -- "promote" is a different decision (DG-1 vs the
    # promotion authority in §13.1) and reusing it would conflate making a
    # dataset citable with advancing it through a research gate. Purely
    # ADDITIVE, same discipline as "evaluate" and "register" above: no existing
    # receipt uses it, no existing validation is loosened.
    "seal",
})

SUBJECT_KINDS = frozenset({
    "contract", "family", "evidence_run", "feature", "dataset", "model",
    # Phase 3 Label Store roadmap v1.1.0, WBS 3.8a. One "label" kind
    # covers both Specification-registration and Value-adjudication
    # events -- generic over subject kind (the roadmap's own phrase),
    # mirroring how "feature" above already covers every feature-related
    # event without a separate feature_specification/feature_value split.
    # Purely additive: no existing member is touched.
    "label",
})

ACTOR_TYPES = frozenset({"human", "service"})

# docs/governance/governance_receipts.md §2 — decision taxonomy.
# decision_type -> decision_class. Extending the taxonomy is a new row here,
# never a relaxation of an existing one (a type's class does not change).
DECISION_TAXONOMY: dict[str, str] = {
    "contract.activate": "discretionary",
    "contract.retire": "discretionary",
    "readiness.verdict": "derived",
    "evidence.admit": "derived",
    "evidence.reject": "derived",
    "family.transition": "discretionary",
    "family.promote": "discretionary",
    "family.demote": "discretionary",
    "feature.promote": "discretionary",
    "feature.retire": "discretionary",
    # Phase 4 WBS 4.5. DISCRETIONARY, not derived, and the distinction is
    # ADR-I012 DG-1/DG-2 stated exactly: resolution and validation are
    # COMPUTATIONS and replay (DG-2, IQROS Ph3); sealing is a HUMAN ACT (DG-1,
    # IQROS-P8). A seal receipt is therefore envelope-verified, never re-derived
    # -- re-running the battery years later must not be able to manufacture a
    # seal that no human granted.
    "dataset.seal": "discretionary",
    "dataset.promote": "discretionary",
    "dataset.retire": "discretionary",
    "model.promote": "discretionary",
    "model.retire": "discretionary",
    "reversal": "discretionary",
    # Engineering roadmap Phase 0 (WBS 0.6) / ADR-I015 §12.1: RESERVED, NOT YET
    # EMITTED. No call site constructs a body with any of these three decision
    # types -- the Trading Platform series that would (ADR-I018) does not exist.
    # Reserving before emission is ADR-I015 RG1 ("A decision type is reserved by
    # constitutional act, then emitted later when the corresponding capability
    # exists") and RG3 ("Reserved-but-unemitted types are normal, not defects").
    # All three are "discretionary" per ADR-I015 §12.1's own table: a venue fill
    # is an external fact the platform records, not a computation it can
    # re-derive, so it is envelope-verified rather than replayed.
    "execution.order": "discretionary",
    "execution.fill": "discretionary",
    "execution.reconcile": "discretionary",
    # -----------------------------------------------------------------
    # Phase 3 Label Store roadmap v1.1.0 (WBS 3.8a) / ADR-I011 LG1, §9
    # ("registration receipt"), IQROS-P8, IQROS-P10. Purely additive, same
    # discipline as "evaluate" above: no existing receipt uses either of
    # these two types, no existing validation is loosened, and a type's
    # class never changes once added.
    #
    # `label.register` is DERIVED: ADR-I011 §12 Tier A completeness is a
    # deterministic rule over a Specification's own declared fields,
    # replayable exactly like `evidence.admit`/`.reject`.
    #
    # `label.adjudicate` is DISCRETIONARY: adjudicating a Flagged label is
    # a human act (LG1, IQROS-P8) -- replay verifies the envelope, never
    # re-derives the human's judgment, exactly like
    # `family.promote`/`.demote`.
    "label.register": "derived",
    "label.adjudicate": "discretionary",
    # Phase 3 Label Store roadmap v1.1.0 (WBS 3.8b) / ADR-I010 FG1
    # (`FS-GAP-004` closure). Purely additive -- "feature" (SUBJECT_KINDS)
    # and "register" (ACTIONS) already existed (the former since PR-1, the
    # latter added at WBS 3.8a), so this is the one remaining taxonomy row
    # needed to receipt Phase 2's 17 pre-existing Feature Specifications.
    # DERIVED for the same reason `label.register` is: ADR-I010 §13 Tier A
    # completeness is a deterministic rule over a Specification's own
    # declared fields. Every receipt this migration writes is
    # `attestation='bootstrap'`, so `replay()`'s bootstrap check refuses
    # before decision_class is ever consulted (receipts.py's own,
    # unmodified control flow) -- no live `feature.register` emitter
    # exists or is implied by this addition.
    "feature.register": "derived",
}

ATTESTATION_BOOTSTRAP = "bootstrap"

# ADR-I015 §5.3 -- attestation BASIS, orthogonal to decision_class's derived/
# discretionary VERIFICATION split (§5.2, preserved exactly, unchanged by this
# addition). Attestation concerns *where the recorded fact came from*;
# decision_class concerns *how it is verified*. Distinct from the pre-existing
# `attestation`/`attestation_note` pair below, which predates ADR-I015 and
# marks only ONE thing -- "this receipt is a migration backfill" -- and is left
# completely unchanged by this addition (see ReceiptBody.attestation_basis).
ATTESTATION_BASES = frozenset({
    "platform-observed", "operator-attested", "external-venue", ATTESTATION_BOOTSTRAP,
})


class ReceiptError(ValueError):
    """Base class for receipt construction / validation failures."""


class NonDecimalNumberError(ReceiptError):
    """Raised when a raw IEEE float is found anywhere in a receipt body.

    Rule 3 (governance_receipts.md §1.3): no IEEE float is ever hashed. All
    numerics must be pre-converted to decimal strings via decimal_str().
    """


class BodyValidationError(ReceiptError):
    """Raised by validate_body() callers that choose to raise on errors."""


# ---------------------------------------------------------------------------
# Canonicalisation
# ---------------------------------------------------------------------------

def decimal_str(value: Any, places: int | None = None) -> str:
    """
    Convert a number to a fixed decimal string suitable for hashing.

    Never emits exponent notation or a bare float repr. Accepts int, str,
    Decimal, or float (float is accepted here only as a convenience for the
    caller converting *at the boundary*; it is normalised immediately via
    Decimal(str(value)) and never touches canonical_json as a float).
    """
    if isinstance(value, bool):
        raise NonDecimalNumberError(f"bool is not a valid numeric receipt field: {value!r}")
    if isinstance(value, Decimal):
        dec = value
    elif isinstance(value, int):
        dec = Decimal(value)
    elif isinstance(value, str):
        dec = Decimal(value)
    elif isinstance(value, float):
        dec = Decimal(str(value))
    else:
        raise NonDecimalNumberError(f"cannot convert {type(value)!r} to a decimal string")

    if places is not None:
        quantum = Decimal(1).scaleb(-places)
        dec = dec.quantize(quantum)

    return format(dec, "f")


def _reject_floats(obj: Any, path: str = "$") -> None:
    """Recursively walk obj and raise if a raw float is found anywhere."""
    if isinstance(obj, float):
        raise NonDecimalNumberError(
            f"raw IEEE float at {path}: {obj!r}. Convert with decimal_str() first."
        )
    if isinstance(obj, dict):
        for k, v in obj.items():
            _reject_floats(v, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _reject_floats(v, f"{path}[{i}]")


def canonical_json(obj: Any) -> bytes:
    """
    Canonical JSON per governance_receipts.md §1.3, rule 1:
    lexicographically sorted keys, no insignificant whitespace, UTF-8,
    no trailing newline. Raises NonDecimalNumberError if any raw float
    appears anywhere in obj (rule 3).
    """
    _reject_floats(obj)
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return text.encode("utf-8")


def sha256_lf(data: bytes) -> str:
    """
    SHA-256 hex digest of data after CRLF -> LF normalisation.

    Byte-identical convention to forward_evidence/pre_reg_guard.py's
    _sha256_lf(). One hashing convention on this platform, not two.
    """
    normalised = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(normalised).hexdigest()


def compute_receipt_id(body: dict) -> str:
    """receipt_id = H(canonical(BODY)) — the content-addressed identity."""
    return sha256_lf(canonical_json(body))


# ---------------------------------------------------------------------------
# Body sub-structures (governance_receipts.md §1.1)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Subject:
    kind: str
    id: str
    version_or_hash: str | None = None

    def to_dict(self) -> dict:
        return {"kind": self.kind, "id": self.id, "version_or_hash": self.version_or_hash}


@dataclass(frozen=True)
class Actor:
    type: str  # human | service
    id: str
    key_id: str | None = None

    def to_dict(self) -> dict:
        return {"type": self.type, "id": self.id, "key_id": self.key_id}


@dataclass(frozen=True)
class Authority:
    policy_id: str
    policy_hash: str
    role: str | None = None

    def to_dict(self) -> dict:
        return {"policy_id": self.policy_id, "policy_hash": self.policy_hash, "role": self.role}


@dataclass(frozen=True)
class RuleRef:
    rule_id: str | None = None
    rule_hash: str | None = None
    engine_version: str | None = None
    thresholds_hash: str | None = None

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "rule_hash": self.rule_hash,
            "engine_version": self.engine_version,
            "thresholds_hash": self.thresholds_hash,
        }


@dataclass(frozen=True)
class InputRef:
    """A content-addressed reference to an antecedent: a run, a readiness
    receipt, a contract hash, a dataset manifest, or a prior receipt."""
    kind: str
    id: str
    hash: str

    def to_dict(self) -> dict:
        return {"kind": self.kind, "id": self.id, "hash": self.hash}


@dataclass(frozen=True)
class EvaluationItem:
    check_id: str
    observed: str
    expected: str
    passed: bool

    def to_dict(self) -> dict:
        return {
            "check_id": self.check_id,
            "observed": self.observed,
            "expected": self.expected,
            "passed": self.passed,
        }


@dataclass(frozen=True)
class Verdict:
    result: str
    reason_code: str | None = None

    def to_dict(self) -> dict:
        return {"result": self.result, "reason_code": self.reason_code}


@dataclass(frozen=True)
class ReceiptBody:
    """
    The immutable body of a governance receipt. Every field here is an input
    to receipt_id (governance_receipts.md §1.1) except where noted.

    decided_at is wall-clock and is NOT semantically an input to the decision
    (as_of is), but it IS hashed — two receipts made at different wall-clock
    times for otherwise-identical decisions are, correctly, different receipts.
    """
    decision_type: str
    decision_class: str
    action: str
    subject: Subject
    as_of: str
    decided_at: str
    actor: Actor
    authority: Authority
    verdict: Verdict
    schema_version: int = SCHEMA_VERSION
    from_state: str | None = None
    to_state: str | None = None
    inputs: tuple[InputRef, ...] = field(default_factory=tuple)
    rule: RuleRef = field(default_factory=RuleRef)
    evaluation: tuple[EvaluationItem, ...] = field(default_factory=tuple)
    reason_text: str | None = None
    prev_receipt_hash: str | None = None
    attestation: str | None = None
    attestation_note: str | None = None
    # --- Engineering roadmap Phase 0 (WBS 0.5) / ADR-I015 §5.3, §6, RVX1 ---
    # Both nullable and additive: every existing caller (contract_receipts.py,
    # evidence_governance.py, transition_governance.py, and the three
    # migrate_bootstrap_*.py scripts) constructs a ReceiptBody without these
    # two keywords and is completely unaffected -- they default to None,
    # participate in hashing like any other field (a receipt differing only in
    # one of these has a different receipt_id, per IQROS-P5), and are validated
    # only when set.
    #
    # attestation_basis: "where the recorded fact came from" (ADR-I015 §5.3),
    # distinct from `attestation` above ("is this receipt a migration
    # backfill"). The two are NOT cross-validated against each other in this
    # milestone -- see the technical-debt note in this milestone's verification
    # record.
    #
    # contract_version: the receipt CONTRACT version (ADR-I015 RVX1), distinct
    # from `schema_version` above (this module's pre-existing, narrower body-
    # shape counter, unchanged and untouched). No historical receipt can be
    # retroactively assigned a contract_version it was never written against
    # (Ph6) -- it is populated only by future emissions.
    attestation_basis: str | None = None
    contract_version: str | None = None

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "decision_type": self.decision_type,
            "decision_class": self.decision_class,
            "action": self.action,
            "subject": self.subject.to_dict(),
            "from_state": self.from_state,
            "to_state": self.to_state,
            "as_of": self.as_of,
            "decided_at": self.decided_at,
            "actor": self.actor.to_dict(),
            "authority": self.authority.to_dict(),
            "inputs": [i.to_dict() for i in self.inputs],
            "rule": self.rule.to_dict(),
            "evaluation": [e.to_dict() for e in self.evaluation],
            "verdict": self.verdict.to_dict(),
            "reason_text": self.reason_text,
            "prev_receipt_hash": self.prev_receipt_hash,
            "attestation": self.attestation,
            "attestation_note": self.attestation_note,
            "attestation_basis": self.attestation_basis,
            "contract_version": self.contract_version,
        }


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Validation (item 8 — receipt validation)
# ---------------------------------------------------------------------------

def validate_body(body: ReceiptBody) -> list[str]:
    """
    Structural + taxonomy validation of a receipt body. Returns a list of
    human-readable errors; empty list means valid. Never raises — callers
    that want a hard failure should use validate_body_or_raise().
    """
    errors: list[str] = []

    if body.decision_type not in DECISION_TAXONOMY:
        errors.append(
            f"unknown decision_type '{body.decision_type}'; not in DECISION_TAXONOMY. "
            "A new decision type must be added as a taxonomy row, not inferred."
        )
    else:
        expected_class = DECISION_TAXONOMY[body.decision_type]
        if body.decision_class != expected_class:
            errors.append(
                f"decision_class '{body.decision_class}' does not match taxonomy for "
                f"'{body.decision_type}' (expected '{expected_class}')"
            )

    if body.decision_class not in DECISION_CLASSES:
        errors.append(f"decision_class must be one of {sorted(DECISION_CLASSES)}, got '{body.decision_class}'")

    if body.action not in ACTIONS:
        errors.append(f"action must be one of {sorted(ACTIONS)}, got '{body.action}'")

    if body.subject.kind not in SUBJECT_KINDS:
        errors.append(f"subject.kind must be one of {sorted(SUBJECT_KINDS)}, got '{body.subject.kind}'")
    if not body.subject.id:
        errors.append("subject.id is required and must be non-empty")

    if body.actor.type not in ACTOR_TYPES:
        errors.append(f"actor.type must be one of {sorted(ACTOR_TYPES)}, got '{body.actor.type}'")
    if not body.actor.id:
        errors.append("actor.id is required and must be non-empty")

    if not body.as_of:
        errors.append("as_of is required (point-in-time coordinate)")
    else:
        _require_iso8601(body.as_of, "as_of", errors)

    if not body.decided_at:
        errors.append("decided_at is required (wall clock)")
    else:
        _require_iso8601(body.decided_at, "decided_at", errors)

    if not body.authority.policy_id or not body.authority.policy_hash:
        errors.append("authority.policy_id and authority.policy_hash are required")

    if body.decision_class == "discretionary" and not (body.reason_text or "").strip():
        errors.append(
            "reason_text is required and must be non-empty for discretionary decisions "
            "(governance_receipts.md §1.1: 'BECAUSE reason_text')"
        )

    if not body.verdict.result:
        errors.append("verdict.result is required")

    if body.attestation is not None and body.attestation != ATTESTATION_BOOTSTRAP:
        errors.append(f"attestation, if set, must be '{ATTESTATION_BOOTSTRAP}', got '{body.attestation}'")
    if body.attestation == ATTESTATION_BOOTSTRAP and not (body.attestation_note or "").strip():
        errors.append("attestation_note is required and must be non-empty when attestation='bootstrap'")

    if body.attestation_basis is not None and body.attestation_basis not in ATTESTATION_BASES:
        errors.append(
            f"attestation_basis, if set, must be one of {sorted(ATTESTATION_BASES)}, "
            f"got '{body.attestation_basis}' (ADR-I015 §5.3)"
        )

    # Rule 3 defense-in-depth: confirm the body is hashable before it is ever
    # handed to a ledger. This also catches accidental raw floats early with
    # a clear message rather than a hash-time exception deep in the ledger.
    try:
        canonical_json(body.to_dict())
    except NonDecimalNumberError as exc:
        errors.append(str(exc))

    return errors


def validate_body_or_raise(body: ReceiptBody) -> None:
    errors = validate_body(body)
    if errors:
        raise BodyValidationError("; ".join(errors))


def _require_iso8601(value: str, field_name: str, errors: list[str]) -> None:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{field_name} is not a valid ISO-8601 timestamp: '{value}'")


# ---------------------------------------------------------------------------
# Replay foundation interfaces (item 9)
#
# This PR defines the *shape* of replay only. No engine is wired to it yet —
# that is per-subsystem integration work for later PRs (Data Completeness
# Gate, Contract Registry, Evidence Library). What is implemented here is
# real and independently useful today:
#   - verify_receipt(): recomputes receipt_id from a stored body; needs
#     nothing but the receipt itself. Works forever, offline.
#   - replay(): dispatches on decision_class / attestation and either (a)
#     refuses for bootstrap receipts, (b) verifies only the envelope for
#     discretionary receipts, or (c) requires an injected rule_fn for
#     derived receipts (no such rule_fn exists on the platform yet).
#   - fold_reconstruct(): a generic, subsystem-agnostic last-write-wins fold
#     over a receipt stream, keyed by subject. Per-cache reconciliation
#     against registry.yaml etc. is later-PR integration work.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReplayResult:
    ok: bool
    mode: str  # "verified" | "envelope-verified" | "refused" | "diverged"
    detail: str


def verify_receipt(body: dict, receipt_id: str) -> bool:
    """
    verify(receipt) per governance_receipts.md §5: recompute receipt_id from
    the stored body and compare. Detects any tampering with body_json.
    Needs nothing but the receipt itself — no database, no source data.
    """
    return compute_receipt_id(body) == receipt_id


def replay(
    body: dict,
    rule_fn: Callable[[dict], Verdict] | None = None,
) -> ReplayResult:
    """
    replay(receipt) per governance_receipts.md §5.

    body: the canonical dict form of a receipt's body (as returned by
    ReceiptBody.to_dict() or read back from storage).
    rule_fn: for a 'derived' receipt, a callable that re-executes the rule
    against the recorded inputs and returns a fresh Verdict. Not supplied by
    this PR for any decision type — that wiring belongs to the subsystem
    that owns the rule (e.g. the Data Completeness Gate for
    readiness.verdict).
    """
    if body.get("attestation") == ATTESTATION_BOOTSTRAP:
        return ReplayResult(
            ok=False,
            mode="refused",
            detail="bootstrap receipt: inputs/rule versions were not captured at the "
                   "time and cannot be recovered. replay() refuses rather than "
                   "approximate.",
        )

    decision_class = body.get("decision_class")
    if decision_class == "discretionary":
        errors = _validate_envelope_only(body)
        if errors:
            return ReplayResult(ok=False, mode="diverged", detail="; ".join(errors))
        return ReplayResult(
            ok=True,
            mode="envelope-verified",
            detail="preconditions/envelope verified; the choice itself is attested, "
                   "not recomputed",
        )

    if decision_class == "derived":
        if rule_fn is None:
            raise NotImplementedError(
                "replay() of a 'derived' receipt requires a rule_fn supplied by the "
                "owning subsystem. No such rule_fn is wired in PR-1 (foundation only)."
            )
        fresh_verdict = rule_fn(body)
        recorded = body.get("verdict") or {}
        if fresh_verdict.to_dict() == recorded:
            return ReplayResult(ok=True, mode="verified", detail="verdict reproduced bit-for-bit")
        return ReplayResult(
            ok=False,
            mode="diverged",
            detail=f"recomputed verdict {fresh_verdict.to_dict()!r} != recorded {recorded!r}",
        )

    raise ReceiptError(f"unknown decision_class '{decision_class}'")


def _validate_envelope_only(body: dict) -> list[str]:
    """Minimal envelope check usable with only the body dict (no dataclass)."""
    errors: list[str] = []
    if body.get("decision_type") not in DECISION_TAXONOMY:
        errors.append(f"unknown decision_type '{body.get('decision_type')}'")
    if not body.get("actor", {}).get("id"):
        errors.append("actor.id missing")
    if not (body.get("reason_text") or "").strip():
        errors.append("reason_text missing for discretionary receipt")
    return errors


def fold_reconstruct(receipts: Iterable[dict], as_of: str) -> dict[tuple[str, str], dict]:
    """
    reconstruct(as_of) per governance_receipts.md §5, generic core.

    Folds every receipt body with as_of <= T, in seq order, into
    {(subject_kind, subject_id): last_receipt_body}. This is last-write-wins
    per subject — the generic fold every subsystem-specific cache
    (registry.yaml, contract_registry.phase, feature/model pointers) is a
    specialisation of. Reconciling a specific cache against this fold is
    later-PR integration work; this function is the reusable core.

    receipts must already be in seq order (callers own ordering — this
    module has no database access).
    """
    cutoff = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    state: dict[tuple[str, str], dict] = {}
    for body in receipts:
        body_as_of = datetime.fromisoformat(body["as_of"].replace("Z", "+00:00"))
        if body_as_of > cutoff:
            continue
        subject = body["subject"]
        key = (subject["kind"], subject["id"])
        state[key] = body
    return state
