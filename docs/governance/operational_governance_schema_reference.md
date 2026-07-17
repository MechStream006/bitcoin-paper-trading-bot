# Operational Governance — Schema & Verification Reference

**Status:** Implemented (Governance Architecture PR-9, 2026-07-18).
**Modules:** `research/evidence_library/governance_verification.py`,
`research/evidence_library/readiness_gate.py`.
**Modifies:** `transition_governance.py` (state-machine hash split + armed
readiness gate), `record.py` (a lock-safe `derive_state_on_connection` was
already present from PR-8; no change here).
**Tests:** `tests/test_governance_verification.py` (26),
`tests/test_readiness_gate.py` (14).

PR-9 **operationalises** the governance system PR-1…PR-8 built. It introduces no
new governance concept and no new receipt type. It adds a read-only verification
engine, arms one deferred gate, and implements one deferred diagnostic.

---

## 1. Dependency check (performed before implementation)

Per the task's explicit stop condition. **The design fits PR-1…PR-8 with no
architectural change.** Three facts, each verified against the real ledger
before a line was written:

| Concern | Finding |
|---|---|
| Would bumping the transition engine version break live receipts? | **No** — all 13 real transition receipts are `attestation='bootstrap'` with `engine_version=None`. There are **zero live v1 receipts** to reinterpret. |
| Would arming the readiness gate at Validated break existing families? | **No** — the real ledger has **zero** validated / promotion_ready / production_ready families (9 candidate, 22 discovery, 4 disqualified). G2 names the Validated gate specifically, so arming it is backward-compatible *by construction*. |
| Does the state-machine hash split need a `receipts.py` change? | **No** — `RuleRef` already carries `rule_hash` **and** `thresholds_hash` as distinct fields. PR-8 set both (and `authority.policy_hash`) to the same combined digest; PR-9 makes them mean what their names say, under a bumped `engine_version`. |

The one existing-file change of substance is the engine-version bump — which is
**the mechanism `contract_versioning.md` §3 mandates** for a semantics change, not
a workaround. See §3.

---

## 2. The read-only verification engine

`governance_verification.py`. It **reads and reports**; it writes nothing,
mutates nothing, emits no receipt (asserted by a test that scans the module for
`INSERT`/`UPDATE`/`DELETE`/`DROP`/`ALTER`, and by a before/after ledger snapshot).

It is a **fold over the existing subsystem verifiers**, never a reimplementation
— reimplementing any would create the divergence-between-enforcement-points
`evidence_reporting_architecture.md` §7.3 forbids:

```
verify_governance(db, registry?, thresholds?, as_of?)
  ├─ receipt_ledger.verify_chain()                       (PR-1) hash chain
  ├─ lifecycle.validate_registry()                       (PR-5) C1–C9
  ├─ evidence_governance.validate_evidence_governance()  (PR-6) EG1/EG2
  ├─ transition_governance.validate_transition_governance() (PR-8) TG1–TG6
  ├─ dq.validation.validate_ledger()                     (PR-7) DQ-L*  [if substrate exists]
  └─ _cross_subsystem_checks()                            (PR-9) GV1–GV3
```

### Public interface

```python
verify_governance(db, registry=None, thresholds=None, as_of=None) -> VerificationReport
verify_receipt_chain(db) -> VerificationReport          # chain only, the fundamental check
diagnose_state_machine_evolution(db) -> list[StateMachineDiagnosis]
state_machine_evolution_findings(db) -> list[Finding]

VerificationReport(.ok, .as_of, .findings, .counts, .subsystems_run,
                   .errors, .by_subsystem(), .to_dict(), .summary())
Finding(.code, .severity, .message, .subsystem)         # severity: error|warning|info
```

No CLI (explicitly out of scope) — these are reusable APIs.

### Four properties, each enforced

- **Fail-closed.** A subsystem verifier that *raises* becomes a `GV-ERR` error
  and `ok` is False; the subsystem is marked *not* run. A check that could not
  run is a failure, never a silent pass. Tested by making a verifier explode.
- **Deterministic & point-in-time correct.** `as_of` is threaded verbatim into
  every subsystem that supports it. Chain integrity and per-receipt replay are
  inherently "now" (a hash matches or it does not), so a historical sweep
  **skips** them with an `info` finding rather than misapplying them — exactly
  as `report.py --check` does. Nothing reads a working file or the wall clock.
- **Native codes preserved.** A subsystem's own leading code (`C4`, `EG1`,
  `TG3`, `DQ-L5`) is passed through, so a reader sees the native code, not a
  re-labelling.
- **Read-only, even for PR-7.** If a ledger has no readiness substrate (no
  `data_readiness` table and no `readiness.verdict` receipt — the real ledger
  today), the DQ check is **skipped with an `info` finding**. PR-9 must not
  *create* the table to "check" it. Tested.

### PR-9's own cross-subsystem checks (what no single subsystem can see)

| Code | Severity | Checks |
|---|---|---|
| `GV1` | error | every transition receipt still **replays** (envelope + integrity), dispatching on the recorded `engine_version` so a v1 receipt is replayed as v1 |
| `GV2` | error | every `contract_hash` a receipt names **resolves** in the registry |
| `GV3` | error | the two independent state derivations (`state_transitions` vs the transition-receipt chain) **agree** — a ledger-wide restatement of TG5, run even when no registry is supplied |

---

## 3. State-machine evolution diagnostics (the deferred PR-8 enhancement)

PR-8's audit found that a transition receipt recorded a `policy_hash` covering
the state machine, but replay never compared it — so a widened machine replayed
green and a narrowed one misattributed a governance change as possible forgery.
PR-8 deferred the fix with the reason recorded: `_policy_hash()` mixed the state
machine *and* thresholds into one digest, and replay receives no thresholds.

PR-9 implements the recorded design — **the additive one**:

```python
state_machine_hash()   # ALLOWED_TRANSITIONS + STATES_REQUIRING_RUN, nothing else
thresholds_hash(thr)   # thresholds alone
_policy_hash(thr)      # the COMBINED digest — unchanged meaning, still authority.policy_hash
```

From `transition_governance.v2`, a transition receipt records:
- `rule.rule_hash` = **`state_machine_hash()`** (the state machine alone),
- `rule.thresholds_hash` = **`thresholds_hash()`** (thresholds alone),
- `authority.policy_hash` = the combined digest (unchanged from PR-8).

`state_machine_hash()` deliberately **excludes** `ENGINE_VERSION`: PR-8's combined
hash included it, so the digest changed on any module bump even when the machine
did not — which would make "did the state machine change?" unanswerable across an
engine bump, i.e. the exact question the hash exists to answer.

### Why an engine-version bump, not an in-place redefinition

`rule_hash` and `thresholds_hash` **change meaning** at v2 (v1: both = combined
digest; v2: separated). Redefining them silently under v1 would make every
historical receipt replay against semantics it was never rendered under, while
every hash still verified — the precise silent-substitution error
`contract_versioning.md` §3 forbids. So:

- **v2+ receipts** are *separable*: `diagnose_state_machine_evolution()` compares
  `rule_hash` to today's `state_machine_hash()` and reports a definitive
  `machine_changed` yes/no.
- **v1 / bootstrap receipts** are *non-separable*: their combined hash genuinely
  cannot answer the question, so it is reported as `separable=False,
  machine_changed=None` — **an honest non-answer over a confident wrong one.**

A changed machine is a **`warning`**, not an error: the arc is still legal and the
receipt still replays. It is a governance-visible fact a reviewer is entitled to
see, exactly as a contract-version span is (`contract_versioning.md` §8 E2).

---

## 4. Readiness enforcement — arming G2

`readiness_gate.py` arms `data_completeness_gate.md` §7 **G2**: *"the pinned
run's evidence window must be 100% READY … there is no silent bypass."* PR-8
built the `TG-RDY` hook and left it disarmed because arming it then would have
invalidated all 35 families. PR-9 arms it, backward-compatible **by
construction** (zero validated families exist).

### The asymmetry is G2's

`STATES_REQUIRING_READINESS = {"validated"}`. G2 names the Validated gate
specifically; `promotion_ready` / `production_ready` are downstream of Validated
and unreachable without passing through it, so listing Validated alone is
sufficient *and* minimal.

- **Entering Validated:** the evidence window must be 100% READY. **Absence of
  readiness evidence BLOCKS** (L2, same no-silent-bypass semantics as the
  existing NULL-DSR hard-fail). This has a real, intended consequence: *until a
  readiness producer exists, nothing can be validated.* That is correct — the
  alternative is promoting on data nobody checked, which is how H6's NULL reached
  a derivation.
- **Every other state:** readiness is **reported, never blocking** — unchanged
  from PR-8, and a non-computed readiness renders as `not_computed`, never a bare
  pass (PR-5's rendering discipline).

### Two evidence sources, in precedence order

1. **The run's `readiness` block** (`runs.metrics_json`, G3's shape). G3's
   producer does not exist yet; when it lands, this path lights up with no change
   here.
2. **PR-7's `data_readiness` receipts**, queried per day across the run's
   evidence window via `dq.readiness.get_readiness()` — the genuine use of PR-7's
   framework, and **point-in-time correct**: each day is read *as of the run's own
   `run_ts`*, so a readiness receipt written *after* the run cannot retroactively
   make it look ready. Without that, every backfill would silently acquire
   look-ahead.

A run with neither is `UNKNOWN` → blocks at Validated.

### Provider substitution point

Mirrors PR-6's `AdmissibilityRule` pattern rather than inventing a second one:

```python
ReadinessProvider (Protocol)   # .provider_id, .assess(db, run) -> ReadinessAssessment
DefaultReadinessProvider       # run block, else PR-7 receipts
assess(db, run, provider=None) -> ReadinessAssessment
requires_readiness(to_state) -> bool
```

`validate_transition` / `transition_with_receipt` / `promote_with_receipt` all
accept an optional `readiness_provider`. A future provider (e.g. one reading a
Feature Store's own readiness closure) implements the Protocol and is passed in —
it does not edit the gate.

---

## 5. Backward compatibility

- **No table altered, no row rewritten, no taxonomy change.** The real
  `ledger.db` is untouched (verified: `--check` clean, chain valid at 26,
  TG1–TG6 clean, file unmodified in git).
- **The 13 real transition receipts are all v1/bootstrap** — the v2 bump does
  not reinterpret them; they replay identically and are honestly reported as
  non-separable by the SM diagnostic.
- **Arming G2 breaks nothing that exists** — zero validated families.
- **PR-8's suite passes with the gate armed**: the promotion fixtures now supply
  a READY readiness block (G3's shape) via the `forward_run` helper, and one
  PR-8 test that asserted enforcement at `candidate` was updated to the armed
  semantics (enforcement is at Validated; `candidate` reports). This is the same
  kind of fixture update PR-3 and PR-8 each documented when a new gate landed —
  the check working, not a regression.
- Full suite: **1027 passed**, the identical **14 pre-existing failures** as the
  baseline (12 missing-`apscheduler`, 2 date-dependent expiry tests). No new
  failures.

---

## 6. Remaining work

- **The readiness producer (G3).** `readiness_gate` consumes two sources and
  both are wired; neither is *populated* until something writes a `readiness`
  block into `runs.metrics_json` or a `data_readiness` receipt over a run's
  window. Until then, the Validated gate is correctly closed. Standing up the
  producer is the natural next step, and it needs no change to this PR.
- **The pairwise-independence enforcement** (CLAUDE.md §16, Proposal 1) remains
  the item that must land **before the first Candidate → Validated promotion**.
  `TG-PRE` and `promote_with_receipt()` are its natural home, and the readiness
  gate now sits beside it in the same validation pass.
- **Deferred by their own designs, unchanged here:** receipt signing, external
  anchoring, the `--verify-chain` CLI, the contract impact-report / change
  classifier (`contract_versioning.md` PR-C), reprocessing execution (PR-D).
- **One carried divergence from PR-7:** `evidence_governance`'s private
  `_RULE_REGISTRY` still hand-rolls the dispatch mechanism `dq/rules.py`'s
  `RuleRegistry` generalises. Still a zero-behaviour-change refactor; still
  proposed, not performed.
