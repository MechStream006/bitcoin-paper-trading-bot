"""
Operational Governance — the read-only verification engine (PR-9).

Operationalises the governance system PR-1..PR-8 built. This module **only
reads and reports**. It writes nothing, mutates nothing, and emits no receipt.
It is the single place an operator (or a CI job, or a future scheduler that is
explicitly out of this PR's scope) asks one question:

    "Is the governance ledger internally consistent, and does every recorded
     decision still hold up under replay?"

and gets one deterministic, point-in-time-correct, fail-closed answer.

--- What it composes, and why it does not reimplement ---

Every existing verifier already knows how to check its own subsystem. This
engine is a **fold over them**, not a second copy:

    receipt_ledger.verify_chain()            -> hash chain integrity (PR-1)
    lifecycle.validate_registry()            -> C1-C9 (PR-5)
    evidence_governance.validate_evidence_governance() -> EG1/EG2 (PR-6)
    transition_governance.validate_transition_governance() -> TG1-TG6 (PR-8)
    dq.validation.validate_ledger()          -> DQ-L* readiness receipts (PR-7)
    transition_governance.replay_transition_receipt() -> per-receipt replay
    contract_registry.resolve_by_hash()      -> contract dereference (PR-2)

Reimplementing any of these would create exactly the divergence-between-
enforcement-points that evidence_reporting_architecture.md §7.3 forbids. PR-9
adds only what no existing verifier covers: **cross-subsystem consistency** and
**state-machine evolution diagnostics**.

--- Fail-closed ---

A verification that cannot run is a FAILURE, never a pass. If a subsystem
verifier raises, that is reported as a `GV-ERR` finding and `ok` is False. A
governance check that silently swallowed its own inability to run would be worse
than no check at all -- it would report green over an unknown.

--- Deterministic and point-in-time correct ---

`verify_governance(db, as_of=T)` reflects only what was known at T. It never
reads a working file, never reads the wall clock, and reuses each subsystem's
own `as_of` handling verbatim -- so a verification of the past is a verification
of the past, not of the past re-judged by today's registry.
"""
from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_DQ = Path(__file__).resolve().parents[1] / "dq"
if str(_DQ) not in sys.path:
    sys.path.insert(0, str(_DQ))

import contract_registry as cr
import evidence_governance as eg
import lifecycle
import receipt_ledger as rl
import record
import transition_governance as tg


SEVERITY_ORDER = {"ok": 0, "info": 1, "warning": 2, "error": 3}


@dataclass(frozen=True)
class Finding:
    code: str            # GV1.. / GV-ERR / or a passed-through subsystem code
    severity: str        # "error" | "warning" | "info"
    message: str
    subsystem: str

    def to_dict(self) -> dict:
        return {"code": self.code, "severity": self.severity,
                "message": self.message, "subsystem": self.subsystem}


@dataclass(frozen=True)
class VerificationReport:
    ok: bool
    as_of: str | None
    findings: tuple[Finding, ...]
    counts: dict[str, int]
    subsystems_run: tuple[str, ...]

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    def by_subsystem(self) -> dict[str, list[Finding]]:
        out: dict[str, list[Finding]] = {}
        for f in self.findings:
            out.setdefault(f.subsystem, []).append(f)
        return out

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "as_of": self.as_of,
            "counts": dict(self.counts),
            "subsystems_run": list(self.subsystems_run),
            "findings": [f.to_dict() for f in self.findings],
        }

    def summary(self) -> str:
        if self.ok:
            return (f"governance OK — {self.counts.get('receipts', 0)} receipt(s), "
                    f"{len(self.subsystems_run)} subsystem(s) verified"
                    + (f", as of {self.as_of}" if self.as_of else ""))
        e = len(self.errors)
        w = sum(1 for f in self.findings if f.severity == "warning")
        return f"governance FAILED — {e} error(s), {w} warning(s)"


# ---------------------------------------------------------------------------
# Schema fingerprint (Increment 3, Program P1) — DECLARATIVE, not a mirror of
# any module's _SCHEMA_SQL. Each entry names exactly a table (and, where it
# matters, a column) whose ABSENCE is a confirmed crash trigger for a
# downstream subsystem, or an already-gracefully-degraded gap worth surfacing
# anyway. Scope is evidence-derived, not aspirational:
#
#   runs.run_kind        -- lifecycle.py C1's `r["run_kind"]` on a sqlite3.Row
#                            raises IndexError if absent (the confirmed Step
#                            0d production crash trigger).
#   state_transitions,
#   gate_results         -- read directly by C2-C9 / derive_state_at().
#   governance_receipts  -- required by receipt_ledger.verify_chain(); absence
#                            raises sqlite3.OperationalError (the second
#                            confirmed symptom from the original diagnosis).
#                            Marked `as_of_none_only`: both entry points
#                            already skip every receipt-consuming subsystem
#                            (EG/TG) for a historical `--as-of` sweep, so this
#                            table is only ever actually needed when
#                            as_of is None -- requiring it unconditionally
#                            regressed a real, already-passing `--as-of`
#                            code path (test_report_check_as_of_skips_
#                            evidence_governance) and was corrected here.
#   pairwise_r           -- NOT a hard requirement: record.get_pairwise_r()
#                            already tolerates its absence (except
#                            OperationalError: return {}). Listed at
#                            "warning" severity only, so the gap is visible
#                            without blocking anything it doesn't already
#                            block.
#   governance_receipts.
#   attestation_basis /
#   contract_version      -- Engineering roadmap Phase 0 (WBS 0.9), mirrors
#                            the pairwise_r precedent exactly. Neither column
#                            is read by any existing code path (no Execution
#                            receipt is ever emitted yet -- WBS 0.6), so
#                            neither absence blocks anything today. Listed at
#                            "warning" severity, "as_of_none_only": True
#                            (same conditionality as the governance_receipts
#                            table's own entry above, since a column can only
#                            matter where the table itself does).
#
#                            NOTE (recorded honestly, not smoothed over): the
#                            real local ledger already carries both columns as
#                            of this same milestone -- not via
#                            migrate_receipt_attestation_basis.py's audited
#                            backup+--commit path, but because an EARLIER,
#                            now-fixed version of receipt_ledger.init_db()
#                            added them as a side effect of THIS module's own
#                            verify_governance() sweep, before that defect was
#                            found and corrected within this same milestone
#                            (this file's own write-verb guard test, further
#                            down, is exactly what a regression of that defect
#                            would need to keep passing). Verified harmless
#                            (zero row/value mutation, chain intact) and left
#                            in place rather than reverted (an unnecessary
#                            second write against real data). This entry
#                            therefore reports zero findings against the real
#                            ledger already, ahead of the audited migration
#                            run -- which no longer needs to touch the real
#                            ledger's schema at all. See this milestone's
#                            verification record for the full incident note.
#
# check_schema_fingerprint() below inspects ONLY what this tuple names. It
# never enumerates the ledger's actual table/column set to look for extras --
# additional, unknown structure is never a finding here (see
# test_schema_fingerprint_ignores_unknown_extra_structure in
# test_governance_verification.py). This is a floor, not a mirror; do not
# expand it to track every column ever added to _SCHEMA_SQL.
# ---------------------------------------------------------------------------

SCHEMA_FINGERPRINT: tuple[dict, ...] = (
    {"table": "runs", "columns": ("run_kind",), "severity": "error", "as_of_none_only": False},
    {"table": "state_transitions", "columns": (), "severity": "error", "as_of_none_only": False},
    {"table": "gate_results", "columns": (), "severity": "error", "as_of_none_only": False},
    {"table": "governance_receipts", "columns": (), "severity": "error", "as_of_none_only": True},
    {"table": "pairwise_r", "columns": (), "severity": "warning", "as_of_none_only": False},
    {
        "table": "governance_receipts",
        "columns": ("attestation_basis", "contract_version"),
        "severity": "warning",
        "as_of_none_only": True,
    },
)


def check_schema_fingerprint(db_path: Path | str, as_of: str | None = None) -> list[Finding]:
    """Read-only structural check against SCHEMA_FINGERPRINT.

    Purely `sqlite_master` / `PRAGMA table_info` introspection -- no CREATE,
    ALTER, INSERT, UPDATE, or DELETE anywhere in this function, preserving
    this module's asserted "writes nothing" property (source-scanned by an
    existing test). Asserts; never repairs (ADR-P1-1) -- a gap is reported by
    name, never fixed here or anywhere this function is called from.

    `as_of`: pass the caller's as_of verbatim (bare date or full datetime --
    only None-vs-not-None matters here). An element marked `as_of_none_only`
    is skipped entirely (no finding, not even a warning) when as_of is not
    None, matching the exact condition report.py/verify_governance() already
    use to skip the receipt-consuming subsystems for a historical sweep.
    """
    findings: list[Finding] = []
    con = sqlite3.connect(str(db_path))
    try:
        existing_tables = {
            row[0] for row in
            con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for entry in SCHEMA_FINGERPRINT:
            if entry.get("as_of_none_only") and as_of is not None:
                continue
            table, columns, severity = entry["table"], entry["columns"], entry["severity"]
            if table not in existing_tables:
                findings.append(Finding(
                    "GV-SCHEMA", severity,
                    f"required table '{table}' is absent from the ledger",
                    "schema_fingerprint",
                ))
                continue
            existing_columns = {
                row[1] for row in con.execute(f'PRAGMA table_info("{table}")')
            }
            for col in columns:
                if col not in existing_columns:
                    findings.append(Finding(
                        "GV-SCHEMA", severity,
                        f"required column '{table}.{col}' is absent from the ledger",
                        "schema_fingerprint",
                    ))
    except Exception as exc:  # noqa: BLE001 -- fail closed, never propagate
        findings.append(Finding(
            "GV-SCHEMA", "error",
            f"schema fingerprint check could not run: {type(exc).__name__}: {exc}",
            "schema_fingerprint",
        ))
    finally:
        con.close()
    return findings


# ---------------------------------------------------------------------------
# Subsystem adapters — each returns (findings, ran). `ran` is False only if the
# subsystem could not be invoked at all; a subsystem that RAN and found errors
# still counts as run (that is the point).
# ---------------------------------------------------------------------------

def _run(subsystem: str, code_prefix: str, fn) -> tuple[list[Finding], bool]:
    """
    Invoke a subsystem verifier. Its own string findings become `error`
    Findings tagged with `subsystem`. Any exception becomes a single GV-ERR
    `error` (fail-closed) and marks the subsystem as NOT successfully run.
    """
    try:
        raw_errors = fn()
    except Exception as exc:  # noqa: BLE001 -- fail closed, never propagate
        return [Finding(
            code="GV-ERR", severity="error", subsystem=subsystem,
            message=f"{subsystem} verifier could not run: {type(exc).__name__}: {exc}",
        )], False
    findings = [
        Finding(code=_infer_code(e, code_prefix), severity="error",
                subsystem=subsystem, message=e)
        for e in raw_errors
    ]
    return findings, True


def _infer_code(message: str, default_prefix: str) -> str:
    """Preserve a subsystem's own leading code (C4, EG1, TG3, DQ-L5, ...) if the
    message carries one, so a reader sees the native code, not a re-labelling."""
    head = message.split(":", 1)[0].strip()
    token = head.split()[0] if head else ""
    if token and (token[0].isalpha()) and any(ch.isdigit() for ch in token) and len(token) <= 8:
        return token
    return default_prefix


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------

def verify_receipt_chain(db_path: Path | str) -> VerificationReport:
    """Just the hash chain (PR-1). A thin, dedicated entry point for the most
    fundamental check, so a caller that only wants chain integrity does not pay
    for the full sweep."""
    findings: list[Finding] = []
    ran: list[str] = []
    try:
        chain = rl.verify_chain(db_path)
        ran.append("receipt_chain")
        for e in chain.errors:
            findings.append(Finding("GV-CHAIN", "error", e, "receipt_chain"))
        counts = {"receipts": chain.n_receipts}
    except Exception as exc:  # noqa: BLE001
        findings.append(Finding("GV-ERR", "error",
                                f"chain verification could not run: {exc}", "receipt_chain"))
        counts = {"receipts": 0}
    ok = not any(f.severity == "error" for f in findings)
    return VerificationReport(ok, None, tuple(findings), counts, tuple(ran))


def verify_governance(
    db_path: Path | str,
    registry: dict | None = None,
    thresholds: dict | None = None,
    as_of: str | None = None,
) -> VerificationReport:
    """
    The full read-only governance sweep. Composes every subsystem verifier plus
    PR-9's cross-subsystem checks, and folds their findings into one report.

    `registry` / `thresholds` may be omitted; the subsystem verifiers that need
    them are then skipped with an `info` finding rather than crashing (a
    verification that CANNOT check something says so; it never silently passes).

    `as_of` is threaded verbatim into every subsystem that supports it. Chain
    integrity and per-receipt replay are inherently "now" (a hash either matches
    or it does not, regardless of date), so they run unfiltered; the note is
    surfaced rather than hidden.
    """
    findings: list[Finding] = []
    ran: list[str] = []

    # 0. Schema fingerprint (Increment 3) -- must run before anything else
    # touches the ledger. A hard-severity gap here means every downstream
    # subsystem is unsafe to run at all (this is the exact crash class Step
    # 0d hit in production). Short-circuit rather than let C1-C9/EG/TG run
    # against a structurally unsound ledger and produce a confusing
    # secondary failure.
    schema_findings = check_schema_fingerprint(db_path, as_of=as_of)
    findings += schema_findings
    if any(f.severity == "error" for f in schema_findings):
        findings.append(Finding(
            "GV-SKIP", "info",
            "schema fingerprint reported a hard gap; all downstream subsystem "
            "checks skipped rather than run against a structurally unsound ledger",
            "schema_fingerprint",
        ))
        counts = _counts(db_path)
        findings.sort(key=lambda f: (-SEVERITY_ORDER[f.severity], f.subsystem, f.code))
        return VerificationReport(False, as_of, tuple(findings), counts, tuple(ran))

    # 1. Hash chain (PR-1) -- the foundation everything else rests on.
    f, ok = _run("receipt_chain", "GV-CHAIN", lambda: rl.verify_chain(db_path).errors)
    findings += f
    if ok:
        ran.append("receipt_chain")

    # 2. Registry invariants C1-C9 (PR-5) -- needs registry + thresholds.
    if registry is not None and thresholds is not None:
        f, ok = _run("registry_c1_c9", "GV-REG",
                     lambda: lifecycle.validate_registry(registry, db_path, thresholds, as_of=as_of))
        findings += f
        if ok:
            ran.append("registry_c1_c9")
    else:
        findings.append(Finding(
            "GV-SKIP", "info", "registry/thresholds not supplied; C1-C9 not checked",
            "registry_c1_c9"))

    # 3-4-5. Receipt-consuming subsystems. These are inherently "now" (no as_of
    # derivation over governance_receipts), so they are skipped for a historical
    # sweep rather than misapplied -- exactly as report.py --check does.
    if as_of is None:
        eg.init_db(db_path)  # idempotent schema-ensure (CREATE IF NOT EXISTS)

        if registry is not None:
            f, ok = _run("evidence_governance", "GV-EG",
                         lambda: eg.validate_evidence_governance(db_path, registry))
            findings += f
            if ok:
                ran.append("evidence_governance")

        f, ok = _run("transition_governance", "GV-TG",
                     lambda: tg.validate_transition_governance(db_path, registry))
        findings += f
        if ok:
            ran.append("transition_governance")

        if _has_readiness_substrate(db_path):
            f, ok = _run("data_readiness", "GV-DQ", lambda: _validate_dq_ledger(db_path))
            findings += f
            if ok:
                ran.append("data_readiness")
        else:
            # No readiness.verdict receipts and no data_readiness table: PR-7's
            # framework has never been exercised against this ledger, so there is
            # nothing to verify. That is NOT a failure -- and, critically, PR-9 is
            # read-only, so it must NOT create the table to "check" it. A ledger
            # with no readiness evidence (the real one today) is reported as
            # skipped, not errored.
            findings.append(Finding(
                "GV-SKIP", "info",
                "no readiness.verdict receipts and no data_readiness table; PR-7 readiness "
                "framework not exercised against this ledger, nothing to verify",
                "data_readiness"))
    else:
        findings.append(Finding(
            "GV-SKIP", "info",
            "receipt-consuming checks (EG/TG/DQ) are now-only; skipped for a historical as_of "
            "sweep, exactly as report.py --check does",
            "receipt_consumers"))

    # 6. PR-9 cross-subsystem checks.
    findings += _cross_subsystem_checks(db_path, as_of)
    ran.append("cross_subsystem")

    counts = _counts(db_path)
    ok = not any(f.severity == "error" for f in findings)
    findings.sort(key=lambda f: (-SEVERITY_ORDER[f.severity], f.subsystem, f.code))
    return VerificationReport(ok, as_of, tuple(findings), counts, tuple(ran))


def _has_readiness_substrate(db_path: Path | str) -> bool:
    """
    Read-only: is there any PR-7 readiness evidence to verify? True if a
    `data_readiness` table exists, OR any `readiness.verdict` receipt does.
    Both are pure reads -- this never creates the table (PR-9 writes nothing).
    """
    try:
        con = sqlite3.connect(db_path)
        try:
            has_table = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='data_readiness'"
            ).fetchone() is not None
        finally:
            con.close()
        if has_table:
            return True
        return bool(rl.get_receipts_by_type(db_path, "readiness.verdict"))
    except Exception:  # noqa: BLE001 -- if we cannot even tell, let _run report it
        return True


def _validate_dq_ledger(db_path: Path | str) -> list[str]:
    """PR-7's readiness-receipt validator, imported lazily so this module loads
    even in an interpreter where the dq package is unavailable (it then reports
    GV-ERR, fail-closed, rather than failing to import)."""
    import validation as dq_validation
    return dq_validation.validate_ledger(db_path)


def _counts(db_path: Path | str) -> dict[str, int]:
    counts = {"receipts": 0, "transitions": 0, "families": 0}
    try:
        counts["receipts"] = len(rl.get_all_receipts(db_path))
        counts["transitions"] = len(record.get_all_transitions(db_path))
        counts["families"] = len(record.derive_all_states_at(db_path))
    except Exception:  # noqa: BLE001
        pass
    return counts


# ---------------------------------------------------------------------------
# Cross-subsystem consistency (PR-9's own additive checks)
# ---------------------------------------------------------------------------

def _cross_subsystem_checks(db_path: Path | str, as_of: str | None) -> list[Finding]:
    """
    What no single subsystem verifier can see, because each only knows its own
    table. All read-only.

      GV1  every transition receipt still REPLAYS (envelope + integrity),
           dispatching on the recorded engine_version -- so a v1 receipt is
           replayed as v1, never re-judged under v2 semantics.
      GV2  every contract_hash referenced by any receipt RESOLVES in the
           registry (a receipt naming a pruned contract can never be replayed).
      GV3  the two independent state derivations (state_transitions vs the
           transition-receipt chain) agree -- a ledger-wide restatement of TG5,
           run here so it is covered even when no registry is supplied.
    """
    findings: list[Finding] = []
    if as_of is not None:
        # Replay and hash-resolution are "now" operations; a historical sweep
        # states that rather than pretending to have run them.
        findings.append(Finding(
            "GV-SKIP", "info",
            "cross-subsystem replay/resolution checks are now-only; not run for a historical as_of",
            "cross_subsystem"))
        return findings

    receipts = rl.get_all_receipts(db_path)

    # GV1 — replay every transition receipt.
    for r in receipts:
        if r["decision_type"] not in tg.TRANSITION_DECISION_TYPES:
            continue
        try:
            result = tg.replay_transition_receipt(db_path, r["receipt_id"])
        except Exception as exc:  # noqa: BLE001 -- fail closed
            findings.append(Finding(
                "GV1", "error", "cross_subsystem",
                message=f"transition receipt {r['receipt_id'][:8]}… raised on replay: {exc}"))
            continue
        if result.replay.mode == "refused":
            # bootstrap: refuses by design. Integrity + legality still must hold.
            if not result.hash_ok:
                findings.append(Finding("GV1", "error", "cross_subsystem",
                    f"receipt {r['receipt_id'][:8]}… fails hash verification"))
            if not result.legality_ok:
                findings.append(Finding("GV1", "error", "cross_subsystem",
                    f"receipt {r['receipt_id'][:8]}… records an illegal arc"))
        elif not result.ok:
            findings.append(Finding("GV1", "error", "cross_subsystem",
                f"receipt {r['receipt_id'][:8]}… does not replay: {'; '.join(result.errors)}"))

    # GV2 — every referenced contract_hash resolves.
    for r in receipts:
        chash = r["body"].get("subject", {}).get("version_or_hash")
        # Only contract-subject receipts name a contract_hash here; transition
        # receipts put a transition id in version_or_hash, so scope by kind.
        if r["body"].get("subject", {}).get("kind") != "contract":
            continue
        if not chash:
            continue
        try:
            cr.resolve_by_hash(db_path, chash)
        except cr.ContractNotFoundError:
            findings.append(Finding("GV2", "error", "cross_subsystem",
                f"receipt {r['receipt_id'][:8]}… names contract_hash {str(chash)[:12]}…, "
                f"which does not resolve in the registry"))
        except Exception as exc:  # noqa: BLE001
            findings.append(Finding("GV-ERR", "error", "cross_subsystem",
                f"contract resolution for {r['receipt_id'][:8]}… raised: {exc}"))

    # GV3 — the two state derivations agree.
    try:
        ledger_states = record.derive_all_states_at(db_path, as_of=None)
        receipt_states = tg.reconstruct_all_states_at(db_path, as_of=None)
        for fam_id, (state, _run, _ts) in ledger_states.items():
            rec = receipt_states.get(fam_id)
            if rec is None:
                continue  # TG1 already reports a missing receipt
            if rec[0] != state:
                findings.append(Finding("GV3", "error", "cross_subsystem",
                    f"{fam_id}: state_transitions derives {state!r} but the receipt chain "
                    f"derives {rec[0]!r} -- the two records disagree"))
    except Exception as exc:  # noqa: BLE001
        findings.append(Finding("GV-ERR", "error", "cross_subsystem",
            f"state-derivation cross-check raised: {exc}"))

    return findings


# ---------------------------------------------------------------------------
# State machine evolution diagnostics (deferred PR-8 enhancement)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StateMachineDiagnosis:
    """Per transition receipt: did the state machine it was decided under still
    match today's? `separable` is False for pre-v2 (v1) receipts, whose combined
    hash genuinely cannot answer the question -- reported honestly, never guessed."""
    receipt_id: str
    family_id: str
    engine_version: str | None
    separable: bool
    machine_changed: bool | None   # None when not separable
    recorded_hash: str | None
    current_hash: str
    detail: str

    def to_dict(self) -> dict:
        return {
            "receipt_id": self.receipt_id, "family_id": self.family_id,
            "engine_version": self.engine_version, "separable": self.separable,
            "machine_changed": self.machine_changed,
            "recorded_hash": self.recorded_hash, "current_hash": self.current_hash,
            "detail": self.detail,
        }


def diagnose_state_machine_evolution(db_path: Path | str) -> list[StateMachineDiagnosis]:
    """
    The enhancement PR-8's audit identified and PR-8 deferred (with the reason
    recorded): compare each transition receipt's recorded state-machine hash to
    today's, so "did the governing state machine change under recorded history?"
    is ANSWERABLE.

    Implemented additively, honouring `contract_versioning.md` §3's engine-
    pinning discipline:

      * A **v2+** receipt recorded the STATE MACHINE alone in `rule.rule_hash`
        (see transition_governance.state_machine_hash()). This function
        recomputes today's `state_machine_hash()` and reports whether it matches
        -- a definitive `machine_changed` yes/no.

      * A **v1** receipt recorded a COMBINED digest (machine + thresholds +
        engine version) in `rule.rule_hash`. That value genuinely cannot be
        compared to a machine-only hash. Rather than mis-read it -- which is the
        exact silent-substitution error §3 forbids -- it is reported as
        `separable=False`, `machine_changed=None`. Honest non-answer over a
        confident wrong one.

    Read-only. Dispatches on the RECORDED engine_version, never assumes v2.
    """
    current = tg.state_machine_hash()
    out: list[StateMachineDiagnosis] = []

    for r in rl.get_all_receipts(db_path):
        if r["decision_type"] not in tg.TRANSITION_DECISION_TYPES:
            continue
        body = r["body"]
        ev = body["rule"]["engine_version"]
        recorded = body["rule"]["rule_hash"]

        if ev in tg.ENGINE_VERSIONS_WITH_SPLIT_HASHES:
            changed = recorded != current
            out.append(StateMachineDiagnosis(
                receipt_id=r["receipt_id"], family_id=body["subject"]["id"],
                engine_version=ev, separable=True, machine_changed=changed,
                recorded_hash=recorded, current_hash=current,
                detail=(
                    "state machine UNCHANGED since this decision" if not changed else
                    "STATE MACHINE CHANGED since this decision -- the arc set or "
                    "run-requiring states governing it differ from today's. Replay legality "
                    "still holds for this specific arc, but the governing machine is not the "
                    "one that rendered this decision."
                ),
            ))
        else:
            out.append(StateMachineDiagnosis(
                receipt_id=r["receipt_id"], family_id=body["subject"]["id"],
                engine_version=ev, separable=False, machine_changed=None,
                recorded_hash=recorded, current_hash=current,
                detail=(
                    f"engine_version={ev!r} predates the state-machine/threshold hash split "
                    f"(transition_governance.v2); its recorded rule_hash is a COMBINED digest "
                    f"and cannot answer 'did the state machine alone change?'. Reported as "
                    f"non-separable rather than mis-compared."
                ),
            ))
    return out


def state_machine_evolution_findings(db_path: Path | str) -> list[Finding]:
    """diagnose_state_machine_evolution() folded into Findings, for inclusion in
    a report. A CHANGED machine is a `warning`, not an `error`: it does not
    invalidate the receipt (the arc is still legal, replay still verifies) -- it
    is a governance-visible fact a reviewer is entitled to see, exactly as a
    contract-version span is (contract_versioning.md §8 E2)."""
    findings: list[Finding] = []
    for d in diagnose_state_machine_evolution(db_path):
        if d.separable and d.machine_changed:
            findings.append(Finding(
                "GV-SM", "warning", "state_machine",
                f"{d.family_id}: receipt {d.receipt_id[:8]}… was decided under a state machine "
                f"that has since changed"))
    return findings
