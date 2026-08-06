"""
Label Governance Receipts (ADR-I011 LG1, §9 "registration receipt",
IQROS-P8, IQROS-P10; Phase 3 Label Store roadmap v1.1.0, WBS 3.8a -- the
eighth Label Store milestone).

LG1: "Registration is a human act emitting a receipt (IQROS-P8, P10)."
IQROS-P10: "Registration and adjudication emit receipts." §8's own table
names the registration receipt as part of a Specification's identity
("Specification | Everything in §8.2; registration receipt; lifecycle
phase"). Two real events in this repository need one: `registry.
register_specification()` (Tier A completeness, §12) and `value_store.
adjudicate_forward_label()` (Flagged -> Adjudicated, a human act per
§10.3/§14.1's "Human adjudicates IQROS-P8").

REUSES THE EXISTING RECEIPT SUBSTRATE, PER INSTRUCTION -- BUILT AT PHASE 0
(receipts.py, receipt_ledger.py) AND ALREADY EXERCISED THREE TIMES
(contract_receipts.py: PR-4, evidence_governance.py: PR-6,
transition_governance.py: PR-8). This module is a FOURTH, thin consumer,
not a fourth reimplementation:
  - canonicalisation, hashing, chaining, append-only enforcement, and the
    verify/replay/fold_reconstruct interfaces are receipts.py/
    receipt_ledger.py's, called here unchanged.
  - the DERIVED-vs-DISCRETIONARY split, and the engine-pinned
    substitution-point discipline for a derived rule's replay (a
    RegistrationRule Protocol + a (rule_id, engine_version) registry,
    resolved at replay time -- never "whatever the current implementation
    happens to be"), mirror evidence_governance.py's `AdmissibilityRule`/
    `CandidateGateRule`/`get_rule()` shape exactly. That shape exists
    because a first version of THIS repository's own PR-6 shipped without
    it and a post-review audit found the defect (see evidence_governance.
    py's own docstring, "Correction"): replay silently re-executing
    "whatever check_candidate_gate is now" rather than the historical
    implementation. Reusing the fix, not re-discovering it, is the point
    of "reuse existing governance receipt infrastructure wherever
    appropriate."
  - the "human act, never derived" discipline for adjudication (a service
    actor is refused; replay verifies the envelope, never re-derives the
    judgment) mirrors transition_governance.py's `promote_with_receipt()`
    verbatim in spirit: "Evidence admissibility is a computation.
    Promotion is a judgment." Adjudication is the same category of act.

ZERO CHANGES TO `registry.py` OR `value_store.py`. Both are called via
their existing, real, public functions
(`register_specification()`, `adjudicate_forward_label()`) exactly as
built at WBS 3.1/3.2 -- this milestone's receipts ATTEST to what those
functions already do; they do not change what those functions do. One
private helper is reached into directly:
`registry._validate_tier_a()`, reused rather than duplicated, so Tier A
has exactly one enforcement point (register_specification()'s live check)
and one honestly-labelled REPLAY of it (this module's `TierARule`) -- not
two independently-maintained copies that could silently drift apart. This
is not a new convention: `transition_governance.py` already reaches into
`evidence_governance.py`'s `_decimalize`/`_REQUIRED_RECEIPT_TYPE_FOR_STATE`
the same way, for the same reason (§7.3's "two enforcement points must
never diverge").

WHY NON-ATOMIC, UNLIKE `transition_governance.transition_with_receipt()`.
PR-8's atomic transition+receipt write exists to defend against a real,
documented TOCTOU race: two concurrent transitioners both reading a
family's CURRENT state and both writing a permanently inconsistent chain
to an append-only table. No analogous concurrent-writer scenario is
documented anywhere for the Label Store (`S-1`'s single-writer
justification governs `label_value_forward`'s own storage-layer
immutability, not receipt-emission timing) -- introducing PR-8's locking
machinery here would be exactly the "architectural redesign" / "unrelated
refactoring" this milestone is instructed to avoid, defending against a
risk that has not been identified. Instead this module follows PR-6's
simpler, equally-accepted shape: call the existing write function first
(already atomic and immutability-guarded at its own storage layer), then
append a single-table receipt row -- "atomic by construction" in exactly
the sense evidence_governance.py's own `admit_evidence()` docstring uses
the phrase. The resulting non-atomicity gap (a registration or
adjudication written with no receipt, e.g. via a crash between the two
calls, or via a caller bypassing this module and calling `registry.py`/
`value_store.py` directly) is DETECTED, not prevented -- by
`validate_label_governance()` below, mirroring `evidence_governance.
validate_evidence_governance()` (EG1/EG2) and `transition_governance.
validate_transition_governance()` (TG1-TG6)'s own accepted "detect, don't
silently allow" precedent for the identical class of gap.

SCOPE BOUNDARY, STATED EXPLICITLY (found during implementation, not
guessed past). The roadmap's WBS 3.8b is scoped, by its own text, to
backfilling Phase 2's 17 `feature.register` receipts (closing
`FS-GAP-004`) -- not to the Label Store's own 8 real, pre-existing
Specifications (WBS 3.3/3.4), which predate this module in exactly the
same way. Those 8 have the identical governance gap `FS-GAP-004`
describes, but no roadmap WBS item is assigned to backfill them, and
"Do not begin WBS 3.8b" forecloses treating this as 3.8b's job. Inventing
an unassigned backfill script here would be scope expansion this
milestone's instructions forbid ("No architectural redesign." /
"No unrelated refactoring."). This is logged as discovered technical
debt (this milestone's verification record), demonstrated honestly by
`validate_label_governance()` reporting `LREC1` for all 8 real
Specifications when run read-only against a disposable copy of the real
`label_store.db` -- not silently worked around, and not silently
expanded past what was asked.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

_THIS_DIR = Path(__file__).resolve().parent
_EVIDENCE_LIBRARY_DIR = _THIS_DIR.parent / "evidence_library"

ENGINE_VERSION = "label_receipts.v1"
SUBJECT_KIND = "label"

DECISION_REGISTER = "label.register"
DECISION_ADJUDICATE = "label.adjudicate"


def _load_sibling(module_file_name: str, registered_name: str):
    """Collision-safe sibling loader -- see migrate_h6_forward_labels.py's
    identical helper and its docstring."""
    if registered_name in sys.modules:
        return sys.modules[registered_name]
    spec = importlib.util.spec_from_file_location(registered_name, _THIS_DIR / module_file_name)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[registered_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_registry():
    return _load_sibling("registry.py", "label_store_registry")


def _load_value_store():
    return _load_sibling("value_store.py", "label_store_value_store")


def _import_receipt_infra():
    """research/evidence_library/{receipts,receipt_ledger}.py -- both pure
    stdlib (hashlib/json/sqlite3/dataclasses/datetime/decimal), no pandas,
    so this is a plain module-level-safe import once the directory is on
    sys.path. No naming collision risk (neither module name is duplicated
    elsewhere in this repository)."""
    if str(_EVIDENCE_LIBRARY_DIR) not in sys.path:
        sys.path.insert(0, str(_EVIDENCE_LIBRARY_DIR))
    import receipts  # noqa: PLC0415
    import receipt_ledger  # noqa: PLC0415
    return receipts, receipt_ledger


class LabelReceiptError(ValueError):
    """Base class for label governance-receipt failures."""


class DuplicateRegistrationReceiptError(LabelReceiptError):
    """More than one label.register receipt exists for one spec_hash --
    should never happen through this module's own functions; indicates a
    direct/duplicate ledger write."""


class DuplicateAdjudicationReceiptError(LabelReceiptError):
    """More than one label.adjudicate receipt exists for one Label Value
    -- should never happen through this module's own functions."""


class UnknownRegistrationRuleError(LabelReceiptError):
    """Raised by replay when a receipt names a (rule_id, engine_version)
    pair with no registered RegistrationRule implementation. Mirrors
    evidence_governance.UnknownRuleError: replay must fail loudly rather
    than silently substitute the current default rule."""


class HumanActRequiredError(LabelReceiptError):
    """Raised when adjudication is attempted with a non-human actor.
    ADR-I011 LG1/IQROS-P8: adjudication is a human act; the platform must
    not be able to adjudicate itself."""


# ---------------------------------------------------------------------------
# Registration rule -- the substitution point for label.register's
# derived-path replay (mirrors evidence_governance.AdmissibilityRule).
# ---------------------------------------------------------------------------

@runtime_checkable
class RegistrationRule(Protocol):
    rule_id: str
    engine_version: str

    def evaluate(self, db_path, content: dict) -> tuple[bool, list[dict]]:
        """content is the exact Tier-A field dict register_specification()
        hashes over. Returns (passed, checks), checks a list of
        {check_id, observed, expected, passed} dicts."""
        ...


_TIER_A_CONTENT_FIELDS = (
    "definition_name", "definition_version", "horizon_days", "horizon_governance",
    "fill_convention", "cost_convention", "direction_convention", "maturity_rule",
    "validity_condition", "conditioning_propagation", "binding_id", "engine_version",
)


class TierARule:
    """
    The default provider: a thin, behaviour-preserving wrapper around the
    pre-existing, unmodified `registry._validate_tier_a()`, plus the same
    definition-existence check `register_specification()` performs inline
    -- reproduced here at the SAME short-circuit points (skip, don't
    fail, when Tier A field errors already exist or identity fields are
    blank) so this rule's verdict can never disagree with what
    `register_specification()` itself decided (asserted by
    `register_specification_with_receipt()` before it ever persists a
    receipt).
    """
    rule_id = "registry._validate_tier_a+definition_exists"
    engine_version = ENGINE_VERSION

    def evaluate(self, db_path, content: dict) -> tuple[bool, list[dict]]:
        reg = _load_registry()
        field_errors = reg._validate_tier_a(*(content[f] for f in _TIER_A_CONTENT_FIELDS))
        checks = [{
            "check_id": "LREG-TIERA",
            "passed": not field_errors,
            "observed": "; ".join(field_errors) if field_errors else "all Tier A fields present and well-formed",
            "expected": "0 field errors (ADR-I011 §12 Tier A)",
        }]

        skip_def_check = bool(field_errors) or not content["definition_name"] or not content["definition_version"]
        if skip_def_check:
            def_check = {
                "check_id": "LREG-DEFEXISTS",
                "passed": True,
                "observed": "not evaluated (Tier A field errors present or identity fields blank)",
                "expected": f"{content['definition_name']!r} v{content['definition_version']} registered, when reachable",
            }
        else:
            con = sqlite3.connect(db_path)
            try:
                row = con.execute(
                    "SELECT 1 FROM label_definitions WHERE name = ? AND version = ?",
                    (content["definition_name"], content["definition_version"]),
                ).fetchone()
            finally:
                con.close()
            def_check = {
                "check_id": "LREG-DEFEXISTS",
                "passed": row is not None,
                "observed": "definition exists" if row is not None else "definition does not exist",
                "expected": f"{content['definition_name']!r} v{content['definition_version']} registered (ADR-I011 §8.2 Identity)",
            }
        checks.append(def_check)

        passed = (not field_errors) and def_check["passed"]
        return passed, checks


DEFAULT_REGISTRATION_RULE = TierARule()

_RULE_REGISTRY: dict[tuple[str, str], RegistrationRule] = {}


def register_rule(rule: RegistrationRule) -> None:
    """Registers `rule` under its own (rule_id, engine_version). Mirrors
    evidence_governance.register_rule() exactly."""
    _RULE_REGISTRY[(rule.rule_id, rule.engine_version)] = rule


def get_rule(rule_id: str, engine_version: str | None) -> RegistrationRule:
    rule = _RULE_REGISTRY.get((rule_id, engine_version))
    if rule is None:
        raise UnknownRegistrationRuleError(
            f"no RegistrationRule registered for rule_id={rule_id!r}, "
            f"engine_version={engine_version!r} -- the historical implementation "
            f"is unavailable; replay refuses rather than substituting the current one"
        )
    return rule


register_rule(DEFAULT_REGISTRATION_RULE)


def init_db(db_path) -> None:
    """All three schemas this module reads/writes: label_specifications +
    label_value_forward (registry.py/value_store.py) and
    governance_receipts (receipt_ledger.py). Mirrors
    evidence_governance.init_db()'s equivalent convenience wrapper."""
    _load_registry().init_db(db_path)
    _load_value_store().init_db(db_path)
    _import_receipt_infra()[1].init_db(db_path)


def _tier_a_policy_hash() -> str:
    """A real hash of the actual constraint sets Tier A validates against
    -- mirrors transition_governance.state_machine_hash()'s discipline of
    hashing the real governing structure, not a placeholder string."""
    receipts, _ = _import_receipt_infra()
    reg = _load_registry()
    doc = {
        "horizon_governance_values": sorted(reg.HORIZON_GOVERNANCE_VALUES),
        "conditioning_rules": sorted(reg.CONDITIONING_RULES),
    }
    return receipts.sha256_lf(receipts.canonical_json(doc))


def get_registration_receipt(db_path, spec_hash: str) -> dict | None:
    """The label.register receipt for spec_hash, if one exists. At most
    one may ever exist."""
    _, rl = _import_receipt_infra()
    rows = rl.get_receipts_by_subject(db_path, SUBJECT_KIND, spec_hash)
    reg_rows = [r for r in rows if r["decision_type"] == DECISION_REGISTER]
    if not reg_rows:
        return None
    if len(reg_rows) > 1:
        raise DuplicateRegistrationReceiptError(
            f"spec_hash {spec_hash[:12]}... has {len(reg_rows)} label.register receipts; "
            f"exactly one is permitted (direct DB write?)"
        )
    return reg_rows[0]


def _build_registration_body(spec_hash, content, passed, checks, rule, registered_at, actor, actor_type):
    receipts, _ = _import_receipt_infra()
    policy_hash = _tier_a_policy_hash()
    return receipts.ReceiptBody(
        decision_type=DECISION_REGISTER,
        decision_class="derived",
        action="register",
        subject=receipts.Subject(kind=SUBJECT_KIND, id=spec_hash, version_or_hash=None),
        as_of=registered_at,
        decided_at=receipts.now_iso(),
        actor=receipts.Actor(type=actor_type, id=actor, key_id=None),
        authority=receipts.Authority(policy_id=rule.rule_id, policy_hash=policy_hash, role=None),
        rule=receipts.RuleRef(
            rule_id=rule.rule_id, rule_hash=policy_hash,
            engine_version=rule.engine_version, thresholds_hash=None,
        ),
        evaluation=tuple(
            receipts.EvaluationItem(
                check_id=c["check_id"], observed=c["observed"], expected=c["expected"], passed=c["passed"],
            )
            for c in checks
        ),
        verdict=receipts.Verdict(result="registered" if passed else "rejected", reason_code=None),
    )


def register_specification_with_receipt(
    db_path,
    *,
    definition_name: str,
    definition_version: int,
    horizon_days: int,
    horizon_governance: str,
    fill_convention: str,
    cost_convention: str,
    direction_convention: str,
    maturity_rule: str,
    validity_condition: str,
    conditioning_propagation: str,
    binding_id: str,
    engine_version: str,
    actor: str,
    registered_at: str,
    actor_type: str = "human",
    rule: RegistrationRule | None = None,
) -> dict:
    """
    Calls the EXISTING, unmodified `registry.register_specification()`,
    then emits a `label.register` receipt attesting to its Tier A
    verdict.

    Preserves `register_specification()`'s own raise contract exactly:
    on Tier A failure this function ALSO raises `SpecificationRejected`
    (after the receipt for that rejection has already been durably
    written) -- so a caller written against the bare registry function
    behaves identically, and additionally gets a receipt.

    Idempotent, mirroring `register_specification()`'s own content-
    addressed idempotency: a byte-identical resubmission returns the
    EXISTING receipt rather than writing a duplicate.
    """
    reg = _load_registry()
    rule = rule or DEFAULT_REGISTRATION_RULE
    content = {
        "definition_name": definition_name, "definition_version": definition_version,
        "horizon_days": horizon_days, "horizon_governance": horizon_governance,
        "fill_convention": fill_convention, "cost_convention": cost_convention,
        "direction_convention": direction_convention, "maturity_rule": maturity_rule,
        "validity_condition": validity_condition, "conditioning_propagation": conditioning_propagation,
        "binding_id": binding_id, "engine_version": engine_version,
    }

    rejection_exc = None
    try:
        spec_hash = reg.register_specification(
            db_path,
            definition_name=definition_name, definition_version=definition_version,
            horizon_days=horizon_days, horizon_governance=horizon_governance,
            fill_convention=fill_convention, cost_convention=cost_convention,
            direction_convention=direction_convention, maturity_rule=maturity_rule,
            validity_condition=validity_condition, conditioning_propagation=conditioning_propagation,
            binding_id=binding_id, engine_version=engine_version,
            actor=actor, registered_at=registered_at,
        )
        outcome_passed = True
    except reg.SpecificationRejected as exc:
        spec_hash = exc.spec_hash
        outcome_passed = False
        rejection_exc = exc

    existing = get_registration_receipt(db_path, spec_hash)
    if existing is not None:
        receipt = existing
    else:
        passed, checks = rule.evaluate(db_path, content)
        if passed != outcome_passed:
            raise LabelReceiptError(
                f"rule {rule.rule_id!r} verdict (passed={passed}) disagrees with "
                f"registry.register_specification()'s own persisted phase "
                f"({'Registered' if outcome_passed else 'Rejected'}) for "
                f"spec_hash={spec_hash[:12]}... -- the receipted rule and the storage "
                f"layer must never diverge"
            )
        body = _build_registration_body(spec_hash, content, passed, checks, rule, registered_at, actor, actor_type)
        _, rl = _import_receipt_infra()
        receipt = rl.append_receipt(db_path, body)

    if rejection_exc is not None:
        raise rejection_exc
    return receipt


# ---------------------------------------------------------------------------
# Adjudication -- discretionary, human-gated, never derived.
# ---------------------------------------------------------------------------

def _adjudication_subject_id(subject_id: str, declared_at: str) -> str:
    """Composite id pinning the exact (subject_id, declared_at) half of a
    Label Value's compound key -- spec_hash is carried separately in
    Subject.version_or_hash, mirroring transition_governance.py's use of
    Subject.version_or_hash to pin an exact state_transitions row."""
    return f"{subject_id}::{declared_at}"


def get_adjudication_receipt(db_path, spec_hash: str, subject_id: str, declared_at: str) -> dict | None:
    _, rl = _import_receipt_infra()
    composite_id = _adjudication_subject_id(subject_id, declared_at)
    rows = rl.get_receipts_by_subject(db_path, SUBJECT_KIND, composite_id)
    adj_rows = [
        r for r in rows
        if r["decision_type"] == DECISION_ADJUDICATE and r["subject_version"] == spec_hash
    ]
    if not adj_rows:
        return None
    if len(adj_rows) > 1:
        raise DuplicateAdjudicationReceiptError(
            f"label ({spec_hash[:12]}..., {subject_id!r}, {declared_at}) has "
            f"{len(adj_rows)} label.adjudicate receipts; exactly one is permitted"
        )
    return adj_rows[0]


def _maturity_policy_hash() -> str:
    """A real hash of the maturity-state arc table -- mirrors
    transition_governance.state_machine_hash()'s discipline."""
    receipts, _ = _import_receipt_infra()
    vs = _load_value_store()
    doc = {"allowed_maturity_arcs": sorted(f"{a}->{b}" for a, b in vs._ALLOWED_MATURITY_ARCS)}
    return receipts.sha256_lf(receipts.canonical_json(doc))


def adjudicate_forward_label_with_receipt(
    db_path,
    spec_hash: str,
    subject_id: str,
    declared_at: str,
    *,
    adjudicated_at: str,
    adjudication_decision: str,
    actor: str,
    actor_type: str = "human",
) -> dict:
    """
    Calls the EXISTING, unmodified `value_store.adjudicate_forward_label()`
    (Flagged -> Adjudicated), then emits a discretionary `label.adjudicate`
    receipt attesting to the human decision.

    Refuses a non-human actor (LG1/IQROS-P8: adjudication is a human act;
    the platform must not adjudicate itself), mirroring
    `transition_governance.promote_with_receipt()`'s identical refusal for
    promotion.
    """
    if actor_type != "human":
        raise HumanActRequiredError(
            f"adjudication requires a human actor (got actor_type={actor_type!r}) -- "
            f"ADR-I011 LG1/IQROS-P8: adjudication is a human act"
        )

    vs = _load_value_store()
    current = vs.get_forward_label(db_path, spec_hash, subject_id, declared_at)
    if current is None:
        raise LabelReceiptError(
            f"no forward label declared at (spec_hash={spec_hash[:12]}..., "
            f"subject_id={subject_id!r}, declared_at={declared_at!r})"
        )
    precondition_ok = current["maturity_state"] == "Flagged"
    checks = [{
        "check_id": "LADJ-PRECONDITION",
        "passed": precondition_ok,
        "observed": f"maturity_state={current['maturity_state']!r}",
        "expected": "maturity_state='Flagged' (ADR-I011 §12.2: Flagged -> Adjudicated)",
    }]

    # The storage layer remains authoritative regardless of the pre-check
    # above -- this call raises LabelTransitionError if the arc is illegal,
    # exactly as it always has (WBS 3.2, unmodified).
    vs.adjudicate_forward_label(
        db_path, spec_hash, subject_id, declared_at,
        adjudicated_at=adjudicated_at, adjudication_decision=adjudication_decision,
    )

    receipts, rl = _import_receipt_infra()
    policy_hash = _maturity_policy_hash()
    body = receipts.ReceiptBody(
        decision_type=DECISION_ADJUDICATE,
        decision_class="discretionary",
        action="adjudicate",
        subject=receipts.Subject(
            kind=SUBJECT_KIND, id=_adjudication_subject_id(subject_id, declared_at), version_or_hash=spec_hash,
        ),
        from_state="Flagged",
        to_state="Adjudicated",
        as_of=adjudicated_at,
        decided_at=receipts.now_iso(),
        actor=receipts.Actor(type=actor_type, id=actor, key_id=None),
        authority=receipts.Authority(
            policy_id="value_store.adjudicate_forward_label", policy_hash=policy_hash, role=None,
        ),
        rule=receipts.RuleRef(
            rule_id="value_store._ALLOWED_MATURITY_ARCS", rule_hash=policy_hash,
            engine_version=ENGINE_VERSION, thresholds_hash=None,
        ),
        evaluation=tuple(
            receipts.EvaluationItem(
                check_id=c["check_id"], observed=c["observed"], expected=c["expected"], passed=c["passed"],
            )
            for c in checks
        ),
        verdict=receipts.Verdict(result="Adjudicated", reason_code=None),
        reason_text=adjudication_decision,
    )
    return rl.append_receipt(db_path, body)


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LabelRegistrationReplay:
    spec_hash: str
    receipt_id: str
    hash_ok: bool
    replay: "object"  # receipts.ReplayResult


def replay_registration_receipt(db_path, spec_hash: str) -> LabelRegistrationReplay:
    """
    Replays a `label.register` receipt using PR-1's replay framework,
    supplying a real `rule_fn` (the derived path). Resolves the EXACT
    historical rule implementation via `get_rule(receipt.rule.rule_id,
    receipt.rule.engine_version)` -- never "whatever TierARule is now" --
    mirroring `evidence_governance.verify_admission_receipt()`'s
    corrected design exactly.
    """
    receipts, _ = _import_receipt_infra()
    receipt = get_registration_receipt(db_path, spec_hash)
    if receipt is None:
        raise LabelReceiptError(f"spec_hash {spec_hash[:12]}... has no label.register receipt")

    hash_ok = receipts.verify_receipt(receipt["body"], receipt["receipt_id"])

    def rule_fn(body: dict):
        recorded_rule_id = body["rule"]["rule_id"]
        recorded_engine_version = body["rule"]["engine_version"]
        rule = get_rule(recorded_rule_id, recorded_engine_version)
        reg = _load_registry()
        spec = reg.get_specification(db_path, spec_hash)
        if spec is None:
            raise LabelReceiptError(f"spec_hash {spec_hash[:12]}... no longer exists in label_specifications")
        content = {f: spec[f] for f in _TIER_A_CONTENT_FIELDS}
        passed, _checks = rule.evaluate(db_path, content)
        return receipts.Verdict(result="registered" if passed else "rejected", reason_code=None)

    replay_result = receipts.replay(receipt["body"], rule_fn=rule_fn)
    return LabelRegistrationReplay(
        spec_hash=spec_hash, receipt_id=receipt["receipt_id"], hash_ok=hash_ok, replay=replay_result,
    )


@dataclass(frozen=True)
class LabelAdjudicationReplay:
    receipt_id: str
    hash_ok: bool
    replay: "object"  # receipts.ReplayResult


def replay_adjudication_receipt(db_path, receipt_id: str) -> LabelAdjudicationReplay:
    """
    Replays a `label.adjudicate` receipt. Discretionary -- no `rule_fn` is
    supplied; `receipts.replay()` verifies the envelope only and never
    re-derives the human's judgment, mirroring
    `transition_governance.replay_transition_receipt()`'s identical
    discipline for `family.promote`.
    """
    receipts, rl = _import_receipt_infra()
    receipt = rl.get_receipt(db_path, receipt_id)
    if receipt is None:
        raise LabelReceiptError(f"receipt_id {receipt_id} not found in governance_receipts")
    if receipt["decision_type"] != DECISION_ADJUDICATE:
        raise LabelReceiptError(f"receipt {receipt_id[:8]}... is a {receipt['decision_type']!r}, not label.adjudicate")

    hash_ok = receipts.verify_receipt(receipt["body"], receipt["receipt_id"])
    replay_result = receipts.replay(receipt["body"])
    return LabelAdjudicationReplay(receipt_id=receipt_id, hash_ok=hash_ok, replay=replay_result)


# ---------------------------------------------------------------------------
# Governance validation -- detects the non-atomicity gap named in the
# module docstring. LREC-prefixed, visibly distinct from lifecycle.py's
# C1-C9, evidence_governance's EG1/EG2, transition_governance's TG1-TG6.
# ---------------------------------------------------------------------------

def validate_label_governance(db_path) -> list[str]:
    """
    LREC1: a `label_specifications` row with no `label.register` receipt.
    LREC2: an Adjudicated `label_value_forward` row with no
    `label.adjudicate` receipt.

    Read-only; safe to call at any time, including against a disposable
    copy of a real database.
    """
    errors: list[str] = []
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        spec_rows = con.execute("SELECT spec_hash FROM label_specifications").fetchall()
        adjudicated_rows = con.execute(
            "SELECT spec_hash, subject_id, declared_at FROM label_value_forward "
            "WHERE maturity_state = 'Adjudicated'"
        ).fetchall()
    finally:
        con.close()

    for row in spec_rows:
        if get_registration_receipt(db_path, row["spec_hash"]) is None:
            errors.append(f"LREC1: spec_hash {row['spec_hash'][:12]}... has no label.register receipt")

    for row in adjudicated_rows:
        if get_adjudication_receipt(db_path, row["spec_hash"], row["subject_id"], row["declared_at"]) is None:
            errors.append(
                f"LREC2: label ({row['spec_hash'][:12]}..., {row['subject_id']!r}, "
                f"{row['declared_at']}) is Adjudicated but has no label.adjudicate receipt"
            )
    return errors
