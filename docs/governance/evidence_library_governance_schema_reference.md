# Evidence Library Governance Integration — Implemented Schema Reference (PR-6)

**Status:** Implemented, **corrected post-review (same PR, 2026-07-13)**.
Wires the pre-existing Evidence Library (`record.py`/`lifecycle.py`/
`report.py`, PR-5) to PR-1's governance receipt ledger, for exactly the
two decision types already present in `receipts.DECISION_TAXONOMY` since
PR-1: `evidence.admit` / `evidence.reject`. No new governance event type.
No `family.transition`/`family.promote` receipt — explicitly excluded by
this PR's task ("state transition receipts").

**Post-review correction:** the first implementation called
`lifecycle.check_candidate_gate()` directly by name in three places, and
`verify_admission_receipt()` ignored a receipt's own recorded
`rule_id`/`engine_version`, always re-executing whatever
`check_candidate_gate` happened to be at replay time — violating
`contract_versioning.md` §3's engine-pinning discipline. Corrected by
introducing `AdmissibilityRule`, a genuine substitution point — see
"Admissibility rule interface" below. This section and "Evidence admission
/ rejection" describe the **corrected** design; the coupling described in
early review commentary no longer exists in the code.
**Design authority:** `governance_receipts.md`, `evidence_reporting_architecture.md`,
`contract_versioning.md`, `data_completeness_gate.md`. This document
describes the Python/SQLite implementation and the scoping decision made
while implementing it; it is a reference, not a second design decision.

Module: `research/evidence_library/evidence_governance.py`, built on PR-1's
`receipt_ledger.py`/`receipts.py` and PR-5's `record.py`/`lifecycle.py`.

---

## Dependency check (per the task's explicit stop condition)

The task instructed: *"If any Evidence Library operation cannot be cleanly
integrated without changing governance architecture, STOP and explain the
dependency rather than expanding scope."*

`governance_receipts.md` §2 defines `evidence.admit`'s abstract
admissibility rule as *"every input day READY/declared-degraded ⇒
admissible"* — i.e. keyed to the Data Completeness Gate's readiness
verdicts (`data_completeness_gate.md`). **That engine does not exist in
this codebase.** PR-2/3/4 built the Contract *Registry* — which versions
contracts — never the Contract *evaluation* engine (`dq/checks.py`,
readiness verdicts, `readiness.verdict` receipts). Building it is not on
this PR's Implement list ("data completeness" is absent from it).

This does not block PR-6, because the Evidence Library has always had its
**own**, already-fully-implemented admissibility rule:
`lifecycle.check_candidate_gate()` — deterministic, pure, and the sole
mechanism by which every one of the real ledger's 35 families was ever
actually admitted (candidate) or rejected (disqualified), long before any
of this governance work began. `evidence.admit`/`evidence.reject`, **in
this PR**, attest *"this run's evidence passed/failed the applicable
governance gate"* — a narrower, honest claim than the abstract taxonomy
entry's data-completeness framing, stated explicitly rather than implied.
Implementation proceeded on this basis instead of stopping.

One consequence, also stated plainly: replaying one of these receipts
re-executes `check_candidate_gate()`, not a readiness check. If/when the
Data Completeness Gate is eventually built, it will need its own
`readiness.verdict` receipts (a decision type already reserved in the
taxonomy, unused) — it should not retroactively redefine what
`evidence.admit` already means here.

---

## Admissibility rule interface (item 1's substitution point)

`AdmissibilityRule` (`typing.Protocol`) is what Evidence Governance
actually depends on — **not** `lifecycle.check_candidate_gate` directly.
Shape: `rule_id: str`, `engine_version: str`,
`evaluate(family_meta, run_metrics, thresholds) -> (passed, gate_results)`.
Both identity fields are recorded verbatim in every receipt a rule
produces; replay dispatches on that recorded pair (see below), never on
whichever implementation happens to be imported at replay time.

`CandidateGateRule` is the **default provider**: a thin, behavior-preserving
wrapper — `evaluate()` is a one-line pass-through to the unmodified
`lifecycle.check_candidate_gate()`. Verified byte-for-byte identical to
calling the function directly. `rule_id = "lifecycle.check_candidate_gate"`,
`engine_version = "evidence_governance.v1"` — unchanged from what the
uncorrected version hardcoded, so every receipt already on disk (including
the real ledger's 13 bootstrap receipts, which don't reach this path at
all) continues to resolve correctly.

`register_rule(rule)` / `get_rule(rule_id, engine_version)` — a module-level
registry (`_RULE_REGISTRY`), `CandidateGateRule` pre-registered at import
time. `get_rule()` raises `UnknownRuleError` (not a fallback) if the exact
pair is not registered. **This is the entire PR-7 substitution point**: a
future Data Completeness Gate rule implements `AdmissibilityRule` and calls
`register_rule(MyRule())` — it does not edit `admit_evidence()`,
`reject_evidence()`, or `verify_admission_receipt()`, and registering it
never disturbs `CandidateGateRule`'s resolution for receipts already
written under it (proved directly: a fake rule and the default rule
coexist in the same registry, receipts from each replay correctly against
their own recorded rule).

---

## Evidence admission / rejection (items 1–2)

`admit_evidence(db_path, run_id, family_meta, thresholds, actor, actor_type,
reason, rule=None)` / `reject_evidence(...)` — `rule` defaults to
`DEFAULT_RULE` (`CandidateGateRule`):

1. Loads the run (`record.get_run`); raises `EvidenceNotFoundError` if absent.
2. Checks for an existing receipt (`get_admission_receipt`); raises
   `DuplicateAdmissionError` if one already exists — **a run may be
   admitted or rejected exactly once.**
3. Runs `rule.evaluate(family_meta, run['metrics'], thresholds)`.
4. **Refuses to record a false verdict**: `admit_evidence()` raises if the
   rule does not actually pass; `reject_evidence()` raises if it does. This
   prevents a caller from recording the wrong receipt type by mistake —
   there is no path to an `evidence.admit` receipt for a failing run.
5. Builds and persists a `ReceiptBody` (`decision_type` = `evidence.admit`/
   `.reject`, `decision_class='derived'`, `subject={kind: evidence_run, id:
   run_id}`, `rule={rule_id: <rule.rule_id>, rule_hash: <see below>,
   engine_version: <rule.engine_version>}`, `evaluation[]` = the *actual*
   `gate_results` the rule produced) via `receipt_ledger.append_receipt()`
   — reused verbatim.

Single-table write (`governance_receipts` only — `runs` is immutable and
untouched), so this is atomic by construction; no multi-table transaction
is needed the way contract activation (PR-4) required one.

**`rule_hash`/`authority.policy_hash`**: a real, deterministic hash of
`{engine_version, rule_id, thresholds}` — scoped to the *specific* rule
that produced it, not a module-wide constant — computed via
`receipts.sha256_lf`/`canonical_json` (reused, not reimplemented). Because
`thresholds.yaml` contains raw floats and `canonical_json()` correctly
refuses to hash one anywhere in a structure (Rule 3,
`governance_receipts.md` §1.3), this module recursively decimal-strings the
threshold values (`receipts.decimal_str`) before hashing — the first
real-world trip over that rule outside `receipts.py`'s own tests, and it
fired on the first attempt, confirming the rule earns its keep. Changing
this hash formula does not invalidate any existing receipt:
`verify_receipt()` recomputes `receipt_id` from the entire stored body
(which already contains the old hash baked into `rule.rule_hash`), it
never recomputes `rule_hash` independently and compares.

---

## Evidence pinning / re-pinning (items 3–4) — validation only, no new receipt type

`pin_evidence(db_path, family_id, to_state, run_id, actor, reason)` /
`repin_evidence(db_path, family_id, run_id, actor, reason)` both call
`record.append_transition()` **verbatim, unchanged** — the same function
PR-5 built, including its X→X re-pin arc. **No `family.transition` receipt
is emitted**, per this PR's explicit exclusion.

What is new is the governance validation wrapped around that existing call
(item 5):

1. The run must exist and belong to `family_id`.
2. For any `to_state` requiring one
   (`_REQUIRED_RECEIPT_TYPE_FOR_STATE`: `candidate`/`validated`/
   `promotion_ready`/`production_ready` → `evidence.admit`;
   `disqualified`/`rejected` → `evidence.reject`), the run must already
   carry a receipt of **exactly that type** — pinning a family into
   `candidate` on a run that was never admitted, or DQ'ing a family on a
   run that was actually admitted, both fail loudly with `InvalidPinError`
   before `append_transition()` is ever called.

`validate_pin(db_path, family_id)` is the same check as a **standalone**
function — usable at any time, independent of the act of pinning, to audit
a pin made by any means (including a raw `record.append_transition()` call
that bypassed this module entirely — tested directly).

`retired` has no entry in `_REQUIRED_RECEIPT_TYPE_FOR_STATE`: retirement is
a family-lifecycle act this PR does not govern (there is no
`family.transition` receipt to attach a requirement to; see the dependency
check above).

---

## Governance-aware evidence queries (item 6)

`get_admitted_runs` / `get_rejected_runs` (all `run_id`s with a receipt of
that type). `get_evidence_lineage(db_path, family_id)` — every run,
transition, and admission/rejection receipt for a family, interleaved
chronologically: the governance-aware counterpart to `report.py`'s
`evidence_timeline.md`, which predates receipts and does not know about
them.

## Point-in-time evidence reconstruction (item 7)

`reconstruct_evidence_at(db_path, family_id, as_of=None)` — combines
`record.derive_state_at()` (PR-5, pure ledger derivation) with every
admission/rejection receipt `decided_at <= as_of`. Pure function of
`(ledger, as_of)`, matching PR-5's determinism discipline exactly.

## Replay compatibility (item 8) — the first real derived-receipt replay

`verify_admission_receipt(db_path, run_id, family_meta, thresholds)` uses
PR-1's replay framework directly: `receipts.verify_receipt` (hash check)
and `receipts.replay(body, rule_fn=...)`. Because `evidence.admit`/`.reject`
are **`derived`** (unlike PR-4's `discretionary` contract receipts, which
never exercised this branch), a **real `rule_fn`** is supplied.

**Post-correction**: `rule_fn` does not call `lifecycle.check_candidate_gate()`
directly. It reads `body["rule"]["rule_id"]`/`["engine_version"]` — the
values *this specific receipt* was produced under — resolves the matching
implementation via `get_rule()`, and re-executes *that*. If nothing is
registered for that exact pair, `get_rule()` raises `UnknownRuleError`,
which propagates out of `replay()` uncaught (`receipts.replay()` does not
swallow `rule_fn` exceptions) — **replay fails loudly, it never falls back
to whichever rule is currently the default.** Verified directly: a receipt
produced under a non-default rule, with that rule then deregistered,
raises `UnknownRuleError` on replay rather than silently reproducing a
(possibly opposite) verdict under `CandidateGateRule`.

A receipt still replays clean against its original thresholds, and still
**diverges** (`mode="diverged"`) when replayed against mutated
thresholds — proving the comparison is live, not a tautology; that
property is unchanged by the correction.

Bootstrap receipts (`bootstrap_admission_receipt`, see below) correctly
**refuse** replay (`mode="refused"`) — checked by `receipts.replay()`
before `rule_fn` is ever called, so an unregistered legacy `rule_id`
(`"bootstrap"`) on a bootstrap receipt never reaches `get_rule()` — while
still passing hash verification — exactly the two-tier guarantee
`governance_receipts.md` §6 specifies.

## Governance validation hook (item 9)

`validate_evidence_governance(db_path, registry)` — for every family whose
**current** state requires a receipt, confirms its pinned run carries the
correct type. Errors are prefixed `EG1` (missing) / `EG2` (wrong type),
visibly distinct from `lifecycle.py`'s `C1`–`C9`. **Additive only**: no
existing invariant was renamed, renumbered, or modified.

Wired into `report.py`'s `run_check()` as a clearly-labeled, **optional**
(`check_evidence_governance=True`, default on) final step, appended after
`C1`–`C9`. It is skipped for a historical `--check --as-of` run — this
check is inherently "now"-only (there is no `as_of`-aware derivation for
`governance_receipts` equivalent to `state_transitions`' in this PR), so it
is skipped rather than silently misapplied to a point in time it cannot
correctly reason about. `run_check()` now calls
`evidence_governance.init_db()` first (idempotent, `CREATE TABLE IF NOT
EXISTS` only) so a ledger that predates this PR — or a fresh test fixture —
does not crash `--check` with a missing-table error merely because nothing
has written a receipt yet.

---

## Bootstrap admission (`migrate_bootstrap_evidence_admission.py`)

Mirrors PR-5's `migrate_run_kind.py` pattern (dry-run default,
`--commit`-gated, timestamped backup first) for exactly the scenario
`governance_receipts.md` §6 ("bootstrap honesty") describes: the real
ledger's 13 pre-existing `state_transitions` rows predate this PR and can
never have a live `evidence.admit`/`.reject` receipt. Each gets one
via `bootstrap_admission_receipt()`, `attestation='bootstrap'`,
`attestation_note` explaining exactly what was reconstructed and why
`replay()` on it will refuse — the same honesty PR-1's own
`migrate_bootstrap_transitions.py` established for `state_transitions`.

**Run against the real ledger** (backup taken first, in the working tree,
git-ignored by the same `ledger.pre-*.db` pattern PR-5 added): 13 receipts
written — 9 `evidence.admit` (the 9 real `candidate` families) + 4
`evidence.reject` (the 4 real `disqualified` families) — matching the real
`state_transitions` table exactly, one receipt per transition. Idempotent
re-run reports zero pending. `receipt_ledger.verify_chain()` confirms all
13 receipts form a valid, unbroken chain.

**No separate `.audit.txt` file was written for this migration** — unlike
`migrate_run_kind.py`'s backfill (a bare column value with no independent
audit surface), each bootstrap receipt is *itself* the audit record:
permanently stored, hash-chained, queryable, and carrying its own
`attestation_note` in `governance_receipts.body_json` — a strictly stronger
audit trail than a companion text file would add.

---

## Public interfaces

`init_db`, `admit_evidence`, `reject_evidence`, `get_admission_receipt`,
`bootstrap_admission_receipt`, `pin_evidence`, `repin_evidence`,
`validate_pin`, `get_admitted_runs`, `get_rejected_runs`,
`get_evidence_lineage`, `reconstruct_evidence_at`,
`verify_admission_receipt`, `EvidenceReceiptVerification`,
`validate_evidence_governance`. Exceptions: `EvidenceGovernanceError`,
`EvidenceNotFoundError`, `DuplicateAdmissionError`, `InvalidPinError`.

**Added by the post-review correction** — the admissibility rule
interface: `AdmissibilityRule` (protocol), `CandidateGateRule` (default
provider), `DEFAULT_RULE` (the pre-registered `CandidateGateRule()`
instance), `register_rule`, `get_rule`, `UnknownRuleError`. `admit_evidence`/
`reject_evidence` gained an optional `rule: AdmissibilityRule | None`
parameter (replacing the former `gate_name: str`, which no caller —
production or test — ever passed explicitly).

`migrate_bootstrap_evidence_admission.py`: `plan`, `apply`, `backup`,
`main` (`--commit`-gated, dry-run default).

`report.py`: `run_check(..., check_evidence_governance=True)` — additive
parameter, default preserves the new (intended) behavior; existing
callers passing no keyword arguments are unaffected in signature but do
gain the new check by default, which is the correct backward-compatible
choice (a stricter check that only fires when there is something real to
flag, verified to be silent on any ledger this PR has not touched).

---

## What this PR deliberately does not do

- No `readiness.verdict` receipt, no Data Completeness Gate engine (see
  dependency check above).
- No `family.transition`/`family.promote`/`family.demote` receipt.
- No new governance decision type beyond the two already reserved in
  `receipts.DECISION_TAXONOMY` since PR-1.
- No Feature Store, ML, operational timer, Telegram, or Docker change.
- No signing, no anchoring beyond what PR-1's chain already provides.

---

## PR-6 review outcome (2026-07-13)

**Merge approved and completed**, after a post-review architectural
correction to the first implementation. Review identified that
`evidence_governance.py` called `lifecycle.check_candidate_gate()`
directly by name in three places, and that `verify_admission_receipt()`
ignored a receipt's own recorded `rule_id`/`engine_version`, always
re-executing whatever `check_candidate_gate` happened to be at replay
time — violating `contract_versioning.md` §3's engine-pinning discipline
and risking silent historical replay corruption if that function were
ever edited in place.

Corrected before merge by introducing `AdmissibilityRule` (a genuine
substitution point), `CandidateGateRule` (the default provider, verified
byte-for-byte behavior-identical to calling `check_candidate_gate`
directly), and a `(rule_id, engine_version)`-keyed registry
(`register_rule`/`get_rule`) that replay dispatches through. An
unregistered historical rule now raises `UnknownRuleError` rather than
being silently substituted — demonstrated directly with an adversarial
case where the substituted default would have produced the *opposite*
verdict from what was actually recorded. A standalone rule defined
entirely outside this module was registered and replayed successfully
alongside `CandidateGateRule`, in the same registry, without either
module needing to change — proving the substitution point works and is
the concrete seam PR-7's Data Completeness Gate will use.

14 new tests added for the correction (70 total in the file); all 56
pre-correction tests pass unmodified. Full suite post-merge: 727 passed,
same 14 pre-existing failures (missing `apscheduler` in this dev
interpreter, unrelated), no regressions, no merge conflicts. Real ledger
re-verified post-merge: `report.py --check` clean, `receipt_ledger
.verify_chain()` confirms all 13 bootstrap receipts still form a valid
chain, untouched by the correction (bootstrap receipts refuse replay
before `rule_fn` is ever called, so they never depended on the coupling
being fixed).
