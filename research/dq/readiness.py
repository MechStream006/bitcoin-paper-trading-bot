"""
Readiness receipts, the data_readiness projection, and point-in-time replay.

Implements items 5 (evaluation receipts), 6 (point-in-time replay), and the
gate lifecycle of data_completeness_gate.md §4.

## No new receipt architecture

Every readiness verdict is emitted as a `readiness.verdict` row in the EXISTING
`governance_receipts` ledger (PR-1) — hash-chained, append-only, chain-verified
by the existing `receipt_ledger.verify_chain()`, replayed through the existing
`receipts.replay()`. `readiness.verdict` has been reserved in
`receipts.DECISION_TAXONOMY` as a `derived` decision since PR-1 and is finally
used here; `derived` is exactly right, and it is load-bearing: replay must
RECOMPUTE the verdict bit-for-bit, not merely verify an envelope.

## data_readiness is a receipt PROJECTION, not a second ledger

`data_completeness_gate.md` §9 specifies a `data_readiness` table;
`governance_receipts.md` deferred "unifying `data_readiness` as a receipt
projection". Both are satisfied here, and the deferral is resolved: the table
exists exactly as specified, but it is **keyed by the governance receipt's own
`receipt_id`** and holds no governance authority of its own. It carries the
REPLAY SUBSTRATE — the `[InputObservation]` set the verdict was rendered from —
which is too large and too structured to live inside a receipt body, and whose
content hash IS committed inside the receipt (`inputs[] -> observations`). A
mutated `data_readiness` row is therefore detected at replay rather than
silently changing what a historical verdict "was computed from".

The receipt is the governance record. The projection is the substrate and the
board. Neither can be tampered with without the other noticing.

## The receipt is written BEFORE the action is taken, and REGARDLESS of it

Even on DEFER. Even on READY. "A day with no receipt is a day nobody looked at,
and that must be visible" (§4). Today, a correctly-skipped day and a day where
nothing ran are indistinguishable — which is exactly why the H6 outage was
silent for as long as it was.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

_EVIDENCE_LIBRARY = Path(__file__).resolve().parents[1] / "evidence_library"
if str(_EVIDENCE_LIBRARY) not in sys.path:
    sys.path.insert(0, str(_EVIDENCE_LIBRARY))

import contract_registry as cr  # noqa: E402
import receipt_ledger as rl  # noqa: E402
from receipts import (  # noqa: E402
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
    sha256_lf,
    replay as replay_receipt_body,
    verify_receipt,
)

import contract_loader
import probes
import rules
from checks import CHECK_REGISTRY, CheckResult, UnknownCheckError
from contract import Contract
from engine import ENGINE_VERSION, ReadinessVerdict, normalise_as_of, observations_hash
from observation import (
    InputObservation,
    observations_from_list,
    observations_to_list,
)
from states import UNKNOWN

DECISION_TYPE = "readiness.verdict"
ACTION = "evaluate"
SUBJECT_KIND = "feature"


class ReadinessError(ReceiptError):
    """Base class for readiness receipt failures."""


class ReceiptNotFoundError(ReadinessError):
    """Raised when a receipt_id does not resolve."""


class ObservationsTamperError(ReadinessError):
    """
    The stored observations do not hash to what the receipt committed to.

    A hard stop, never a warning. The receipt says "this verdict was computed
    from observations whose hash is X"; if the stored observations now hash to
    Y, then either the substrate was mutated or the receipt was — and in either
    case, replaying would produce a verdict the receipt never rendered while
    reporting success.
    """


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS data_readiness (
    receipt_id        TEXT PRIMARY KEY,   -- the governance_receipts receipt_id. This
                                          -- table is a PROJECTION of that ledger, not
                                          -- an authority of its own.
    contract_id       TEXT NOT NULL,
    contract_version  INTEGER,
    contract_hash     TEXT NOT NULL,
    engine_version    TEXT NOT NULL,      -- contract_versioning.md §3: freezing the
    rule_id           TEXT NOT NULL,      -- contract alone is NOT sufficient.
    feature_id        TEXT NOT NULL,
    period            TEXT NOT NULL,
    as_of             TEXT NOT NULL,      -- the INJECTED clock (L6), never a syscall
    state             TEXT NOT NULL,      -- one of the 8 states; NEVER NULL
    action            TEXT NOT NULL,
    checks_json       TEXT NOT NULL,      -- [CheckResult] -- observed vs expected
    observations_json TEXT NOT NULL,      -- [InputObservation] -- the REPLAY SUBSTRATE
    observations_hash TEXT NOT NULL,      -- committed inside the receipt body
    remediation       TEXT,               -- a LITERAL RUNNABLE COMMAND, or NULL
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_readiness_feature_period
    ON data_readiness(feature_id, period, as_of);
CREATE INDEX IF NOT EXISTS ix_readiness_state ON data_readiness(state, as_of);

CREATE TRIGGER IF NOT EXISTS trg_readiness_no_update
BEFORE UPDATE ON data_readiness
BEGIN
    SELECT RAISE(ABORT, 'data_readiness is append-only: UPDATE forbidden');
END;

CREATE TRIGGER IF NOT EXISTS trg_readiness_no_delete
BEFORE DELETE ON data_readiness
BEGIN
    SELECT RAISE(ABORT, 'data_readiness is append-only: DELETE forbidden');
END;
"""


def init_db(db_path: Path | str) -> None:
    """Idempotent. Brings up every table this framework touches: the contract
    registry (PR-2/3/4), the governance receipt ledger (PR-1), and the
    data_readiness projection."""
    cr.init_db(db_path)
    rl.init_db(db_path)
    con = sqlite3.connect(db_path)
    try:
        con.executescript(_SCHEMA_SQL)
        con.commit()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Receipt construction
# ---------------------------------------------------------------------------

def rule_hash(rule: rules.ReadinessRule, contract: Contract) -> str:
    """
    Identity of the exact SEMANTICS a verdict was rendered under: the rule, the
    engine version, and the specific check implementations the contract named.

    contract_versioning.md §3, restated because it is the subtlest trap in the
    whole design: freeze the contract perfectly, and a maintainer can STILL
    change what `min_count: 3` MEANS by editing checks.py. Every historical
    receipt would then replay to a different verdict while every hash still
    matched. Replay would report green. History would have silently changed
    underneath a perfect audit trail. Recording the check_ids — and refusing to
    redefine a check under an existing id — is what closes that hole.
    """
    return sha256_lf(canonical_json({
        "rule_id": rule.rule_id,
        "engine_version": rule.engine_version,
        "check_ids": contract.check_ids(),
    }))


def _subject_id(feature_id: str, period: str) -> str:
    return f"{feature_id}:{period}"


def build_readiness_body(
    verdict: ReadinessVerdict,
    observations: dict[str, InputObservation],
    rule: rules.ReadinessRule,
    contract: Contract,
    actor: str,
    actor_type: str,
    obs_hash: str,
) -> ReceiptBody:
    return ReceiptBody(
        decision_type=DECISION_TYPE,
        decision_class="derived",
        action=ACTION,
        subject=Subject(
            kind=SUBJECT_KIND,
            id=_subject_id(verdict.feature_id, verdict.period),
            version_or_hash=verdict.contract_hash,
        ),
        # as_of is the INJECTED point-in-time coordinate the verdict was
        # rendered at — not the wall clock. decided_at is the wall clock. The
        # two differ on every backfill, and conflating them is how a replay
        # silently acquires look-ahead.
        as_of=verdict.as_of,
        decided_at=now_iso(),
        actor=Actor(type=actor_type, id=actor, key_id=None),
        authority=Authority(
            policy_id=f"contract:{verdict.contract_id}",
            policy_hash=verdict.contract_hash,
            role=None,
        ),
        inputs=(
            InputRef(kind="contract", id=verdict.contract_id, hash=verdict.contract_hash),
            InputRef(
                kind="observations",
                id=_subject_id(verdict.feature_id, verdict.period),
                hash=obs_hash,
            ),
        ),
        rule=RuleRef(
            rule_id=rule.rule_id,
            rule_hash=rule_hash(rule, contract),
            engine_version=rule.engine_version,
            thresholds_hash=verdict.contract_hash,  # the contract IS the thresholds
        ),
        evaluation=tuple(
            EvaluationItem(
                check_id=(f"{c.input_id}/{c.check_id}" if c.input_id else c.check_id),
                observed=c.observed,
                expected=c.expected,
                passed=c.passed,
            )
            for c in verdict.checks
        ),
        # Incompleteness is a VERDICT, never a NULL. `result` is always one of
        # the eight states; `reason_code` is always one of the six actions.
        verdict=Verdict(result=verdict.state, reason_code=verdict.action),
        reason_text=verdict.remediation,
    )


def emit_readiness_receipt(
    db_path: Path | str,
    verdict: ReadinessVerdict,
    observations: dict[str, InputObservation],
    contract: Contract,
    rule: rules.ReadinessRule | None = None,
    actor: str = "dq",
    actor_type: str = "service",
) -> dict:
    """
    Persist a verdict: one `data_readiness` row and one `governance_receipts`
    row, in ONE transaction.

    §6, "No partial writes": a crash mid-write leaves neither, so the period is
    simply un-attempted — a state the system already handles correctly (it will
    be re-evaluated on the next cycle). What must never exist is a receipt
    without its substrate, or a substrate row nothing attests to.

    Reuses `receipt_ledger.append_receipt_on_connection()` — the extraction PR-4
    already made for exactly this pattern — so the chain-linking logic is shared,
    not reimplemented.
    """
    rule = rule or rules.DEFAULT_RULE
    obs_hash = observations_hash(observations)
    body = build_readiness_body(
        verdict, observations, rule, contract, actor, actor_type, obs_hash
    )

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 5000")
    try:
        con.execute("BEGIN IMMEDIATE")
        try:
            receipt = rl.append_receipt_on_connection(con, body)
            con.execute(
                """
                INSERT INTO data_readiness (
                    receipt_id, contract_id, contract_version, contract_hash,
                    engine_version, rule_id, feature_id, period, as_of,
                    state, action, checks_json, observations_json,
                    observations_hash, remediation, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    receipt["receipt_id"], verdict.contract_id, verdict.contract_version,
                    verdict.contract_hash, rule.engine_version, rule.rule_id,
                    verdict.feature_id, verdict.period, verdict.as_of,
                    verdict.state, verdict.action,
                    json.dumps([c.to_dict() for c in verdict.checks], sort_keys=True),
                    json.dumps(observations_to_list(observations), sort_keys=True),
                    obs_hash, verdict.remediation, now_iso(),
                ),
            )
        except Exception:
            con.rollback()
            raise
        else:
            con.commit()
    finally:
        con.close()

    return receipt


# ---------------------------------------------------------------------------
# The gate lifecycle (§4)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GateResult:
    verdict: ReadinessVerdict
    receipt: dict
    observations: dict[str, InputObservation]

    @property
    def may_derive(self) -> bool:
        """A derivation body is never entered unless this is True (§4)."""
        return self.verdict.may_derive


def evaluate_and_record(
    db_path: Path | str,
    contract_id: str,
    period: str,
    as_of: str | datetime,
    observations: dict[str, InputObservation] | None = None,
    rule: rules.ReadinessRule | None = None,
    actor: str = "dq",
    actor_type: str = "service",
) -> GateResult:
    """
    RESOLVE -> PROBE -> CHECK -> AGGREGATE -> RECEIPT -> (caller acts on Action).

    `as_of` is MANDATORY and has no default. There is no `datetime.now()` in
    this package's evaluation path — a gate that reads a clock cannot be
    replayed, and a gate that cannot be replayed cannot be audited (L6).

    `observations`, if supplied, replaces the probe step entirely. That is the
    path a pipeline takes when it already HAS the metadata: SnapshotWriter's
    `collection_metrics` already records per-cycle `coverage_pct` and
    `stale_count` with precisely-defined semantics (CLAUDE.md §11), and the gate
    should CONSUME those numbers rather than recompute them — a second,
    divergent definition of "coverage" is exactly the disease.

    A missing/unresolvable contract does NOT raise: it produces a
    CONFIGURATION_ERROR verdict and a receipt, because a gate that dies with a
    traceback writes nothing, and a period nobody looked at must not be
    indistinguishable from a period that passed.
    """
    as_of_dt = normalise_as_of(as_of)
    rule = rule or rules.DEFAULT_RULE

    try:
        resolved = contract_loader.resolve_for_derivation(db_path, contract_id)
    except contract_loader.ContractResolutionError as exc:
        return _unresolvable_contract_result(db_path, contract_id, period, as_of_dt, exc, actor, actor_type)

    if observations is None:
        observations = probes.probe_all(resolved.contract, period, as_of_dt)

    verdict = rule.evaluate(
        resolved.contract, observations, period, as_of_dt,
        contract_version=resolved.version, contract_hash=resolved.contract_hash,
    )
    receipt = emit_readiness_receipt(
        db_path, verdict, observations, resolved.contract, rule, actor, actor_type
    )
    return GateResult(verdict=verdict, receipt=receipt, observations=observations)


def _unresolvable_contract_result(
    db_path, contract_id, period, as_of_dt, exc, actor, actor_type
) -> GateResult:
    """
    A contract that cannot be resolved is a CONFIGURATION_ERROR -> HALT, and it
    still leaves a receipt. But the receipt cannot be a normal one: there IS no
    contract to hash, no rule to pin, no observations to record. It is written
    as a bootstrap-attested receipt — honest about the fact that there is
    nothing here to replay — rather than fabricating a contract identity.
    """
    from states import CONFIGURATION_ERROR, HALT

    verdict = ReadinessVerdict(
        contract_id=contract_id,
        contract_hash="",
        contract_version=None,
        engine_version=ENGINE_VERSION,
        feature_id=contract_id,
        period=period,
        as_of=as_of_dt.isoformat(),
        state=CONFIGURATION_ERROR,
        action=HALT,
        checks=(
            CheckResult(
                check_id="configuration.contract.v1",
                state=CONFIGURATION_ERROR,
                observed=str(exc),
                expected="a resolvable ACTIVE contract",
                detail="the contract could not be resolved from the registry",
            ),
        ),
        input_states={},
    )
    receipt = bootstrap_readiness_receipt(
        db_path, verdict,
        attestation_note=(
            f"contract '{contract_id}' could not be resolved at evaluation time: {exc}. "
            f"There is no contract_hash, no rule and no observation set to replay against, "
            f"so this receipt attests the CONFIGURATION_ERROR rather than claiming to be "
            f"reproducible. replay() refuses on it, by design."
        ),
        actor=actor,
    )
    return GateResult(verdict=verdict, receipt=receipt, observations={})


def bootstrap_readiness_receipt(
    db_path: Path | str,
    verdict: ReadinessVerdict,
    attestation_note: str,
    actor: str = "bootstrap",
) -> dict:
    """
    A readiness receipt for a verdict that CANNOT be replayed — a period derived
    before this framework existed, or a configuration error where there is no
    contract to dereference.

    `attestation='bootstrap'` (governance_receipts.md §6, "bootstrap honesty",
    the precedent PR-1's own `migrate_bootstrap_transitions.py` set and PR-6's
    `migrate_bootstrap_evidence_admission.py` followed): `replay()` REFUSES on
    these rather than approximating, because the inputs and rule versions were
    never captured in a replayable form and inventing them would be a lie with a
    valid hash. `verify()` — the hash check — still holds forever.
    """
    body = ReceiptBody(
        decision_type=DECISION_TYPE,
        decision_class="derived",
        action=ACTION,
        subject=Subject(
            kind=SUBJECT_KIND,
            id=_subject_id(verdict.feature_id, verdict.period),
            version_or_hash=verdict.contract_hash or None,
        ),
        as_of=verdict.as_of,
        decided_at=now_iso(),
        actor=Actor(type="service", id=actor, key_id=None),
        authority=Authority(policy_id="bootstrap", policy_hash=sha256_lf(b"bootstrap"), role=None),
        rule=RuleRef(rule_id="bootstrap", rule_hash=None, engine_version=None, thresholds_hash=None),
        evaluation=tuple(
            EvaluationItem(
                check_id=(f"{c.input_id}/{c.check_id}" if c.input_id else c.check_id),
                observed=c.observed, expected=c.expected, passed=c.passed,
            )
            for c in verdict.checks
        ),
        verdict=Verdict(result=verdict.state, reason_code=verdict.action),
        reason_text=verdict.remediation,
        attestation=ATTESTATION_BOOTSTRAP,
        attestation_note=attestation_note,
    )
    return rl.append_receipt(db_path, body)


# ---------------------------------------------------------------------------
# Point-in-time queries — the projection (§10's substrate)
# ---------------------------------------------------------------------------

def get_readiness(
    db_path: Path | str,
    feature_id: str,
    period: str,
    as_of: str | datetime | None = None,
) -> dict | None:
    """
    "What did we know about (feature, period), as of T?"

    Selects the LATEST verdict whose `as_of` is <= T — never the latest verdict
    outright. That distinction is the entire point: a period evaluated as
    PARTIAL_DAY at 00:05 and re-evaluated as READY at 00:05 the NEXT day must
    still answer PARTIAL_DAY to the question "what did we know at 00:06 on the
    first day?" A point-in-time read that quietly returns the later verdict has
    look-ahead baked into it, and every downstream training set inherits it.

    Returns None when nothing had been evaluated yet — which is NOT the same as
    READY, and NOT the same as a failure. It is `NO_RECEIPT`: a period nobody
    looked at (§6, "No silent success").
    """
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        if as_of is None:
            row = con.execute(
                "SELECT * FROM data_readiness WHERE feature_id = ? AND period = ? "
                "ORDER BY as_of DESC, rowid DESC LIMIT 1",
                (feature_id, period),
            ).fetchone()
        else:
            cutoff = normalise_as_of(as_of).isoformat()
            row = con.execute(
                "SELECT * FROM data_readiness WHERE feature_id = ? AND period = ? "
                "AND as_of <= ? ORDER BY as_of DESC, rowid DESC LIMIT 1",
                (feature_id, period, cutoff),
            ).fetchone()
        return _row_to_dict(row) if row is not None else None
    finally:
        con.close()


def get_readiness_history(db_path: Path | str, feature_id: str, period: str) -> list[dict]:
    """Every verdict ever rendered for (feature, period), oldest first. A
    PARTIAL_DAY superseded by a READY is not an erasure — both rows persist,
    and the transition is itself the evidence that the gate worked."""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT * FROM data_readiness WHERE feature_id = ? AND period = ? "
            "ORDER BY as_of, rowid",
            (feature_id, period),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        con.close()


def get_readiness_board(
    db_path: Path | str,
    feature_id: str | None = None,
    as_of: str | datetime | None = None,
) -> list[dict]:
    """The current (or as-of) verdict for every (feature, period) evaluated —
    the substrate for §10's readiness board."""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        clauses, params = [], []
        if feature_id is not None:
            clauses.append("feature_id = ?")
            params.append(feature_id)
        if as_of is not None:
            clauses.append("as_of <= ?")
            params.append(normalise_as_of(as_of).isoformat())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = con.execute(
            f"""
            SELECT * FROM data_readiness
            WHERE receipt_id IN (
                SELECT receipt_id FROM data_readiness {where}
                GROUP BY feature_id, period
                HAVING as_of = MAX(as_of)
            )
            ORDER BY feature_id, period
            """,
            params,
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        con.close()


def get_readiness_by_receipt(db_path: Path | str, receipt_id: str) -> dict | None:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT * FROM data_readiness WHERE receipt_id = ?", (receipt_id,)
        ).fetchone()
        return _row_to_dict(row) if row is not None else None
    finally:
        con.close()


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["checks"] = json.loads(d["checks_json"])
    d["observations"] = observations_from_list(json.loads(d["observations_json"]))
    return d


# ---------------------------------------------------------------------------
# Replay (item 6) — the historical contract, never the current one.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReadinessReplay:
    receipt_id: str
    hash_ok: bool
    replay: ReplayResult
    recomputed: ReadinessVerdict | None = None

    @property
    def ok(self) -> bool:
        return self.hash_ok and self.replay.ok


def replay_readiness(db_path: Path | str, receipt_id: str) -> ReadinessReplay:
    """
    Reproduce a historical verdict bit-for-bit, with NO source access.

    Every input to the original decision is dereferenced by HASH, never looked
    up by date and never read from the working tree:

      contract   <- contract_registry.resolve_by_hash(receipt.subject.version_or_hash)
                    the EXACT frozen bytes, even if that version was superseded
                    three times and retired years ago. NOT `contracts/*.yaml` —
                    a git checkout must not be able to change what a 2026 receipt
                    means.
      engine     <- rules.get_rule(receipt.rule.rule_id, receipt.rule.engine_version)
                    the EXACT rule implementation, resolved from the registry.
                    UnknownRuleError if unavailable — never a substitution.
      checks     <- every check_id the resolved contract names must exist in
                    CHECK_REGISTRY. UnknownCheckError if not — never a
                    substitution of the modern check.
      observations <- the data_readiness projection, whose hash the receipt
                    committed to. ObservationsTamperError on mismatch.
      as_of      <- the receipt's own as_of. NOT now(). A replay that used the
                    current clock would re-render a PARTIAL_DAY as READY and
                    call it a successful reproduction.

    Bootstrap-attested receipts REFUSE replay (they never captured a replayable
    rule) — `receipts.replay()` short-circuits on them before `rule_fn` is ever
    called, so their unresolvable `rule_id='bootstrap'` never reaches get_rule().
    """
    receipt = rl.get_receipt(db_path, receipt_id)
    if receipt is None:
        raise ReceiptNotFoundError(f"receipt_id {receipt_id} not found in governance_receipts")

    hash_ok = verify_receipt(receipt["body"], receipt["receipt_id"])
    recomputed: list[ReadinessVerdict] = []

    def rule_fn(body: dict) -> Verdict:
        row = get_readiness_by_receipt(db_path, receipt_id)
        if row is None:
            raise ReceiptNotFoundError(
                f"receipt {receipt_id[:12]}… has no data_readiness row; the replay substrate "
                f"(observations) is gone, so the verdict cannot be recomputed"
            )

        committed = _committed_observations_hash(body)
        actual = observations_hash(row["observations"])
        if committed != actual:
            raise ObservationsTamperError(
                f"stored observations for receipt {receipt_id[:12]}… hash to {actual[:12]}… "
                f"but the receipt committed to {committed[:12]}…. The replay substrate has "
                f"been mutated; the original verdict cannot be honestly reproduced."
            )

        contract_hash = body["subject"]["version_or_hash"]
        resolved = contract_loader.resolve_for_replay(db_path, contract_hash)

        # contract_versioning.md §3: if the current engine cannot honour a
        # historical check_id, replay FAILS LOUDLY. It never silently
        # substitutes the modern check — that would change what the receipt
        # means while every hash still verified.
        for check_id in resolved.contract.check_ids():
            if check_id not in CHECK_REGISTRY:
                raise UnknownCheckError(
                    f"receipt {receipt_id[:12]}… was rendered under check_id '{check_id}', "
                    f"which this engine no longer implements. Replay refuses rather than "
                    f"substituting a modern check of the same name."
                )

        rule = rules.get_rule(body["rule"]["rule_id"], body["rule"]["engine_version"])
        verdict = rule.evaluate(
            resolved.contract, row["observations"], row["period"], body["as_of"],
            contract_version=resolved.version, contract_hash=resolved.contract_hash,
        )
        recomputed.append(verdict)
        return Verdict(result=verdict.state, reason_code=verdict.action)

    result = replay_receipt_body(receipt["body"], rule_fn=rule_fn)
    return ReadinessReplay(
        receipt_id=receipt_id,
        hash_ok=hash_ok,
        replay=result,
        recomputed=recomputed[0] if recomputed else None,
    )


def _committed_observations_hash(body: dict) -> str:
    for ref in body.get("inputs") or []:
        if ref.get("kind") == "observations":
            return ref["hash"]
    raise ReadinessError(
        "receipt does not commit to an observations hash; it cannot be replayed safely"
    )
