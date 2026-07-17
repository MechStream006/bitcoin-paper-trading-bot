# State Transition Governance — Schema & Replay Reference

**Status:** Implemented, reviewed, corrected, and **merged to `main` 2026-07-17**
(Governance Architecture PR-8) via `feat/state-transition-governance`. Approved
after an independent architecture audit; the one required correction (the TOCTOU
race, §3) was implemented and re-verified before merge.
**Module:** `research/evidence_library/transition_governance.py`
**Implements:** `governance_receipts.md` §2's `family.transition` / `family.promote`
/ `family.demote` decision types — reserved as **discretionary** since PR-1,
unused until now.
**Tests:** `tests/test_transition_governance.py` — 68 tests.

This PR introduces **no new governance concept**. It makes the platform's
pre-existing state-transition system *consume* the architecture PR-1…PR-7 built.

---

## 1. Dependency check (performed before implementation)

Per the task's explicit stop condition. **The existing transition model
integrates with no architectural change to PR-1…PR-7.**

| Needed | Already existed? |
|---|---|
| `family.transition` / `.promote` / `.demote` decision types | **Yes** — in `DECISION_TAXONOMY` since PR-1, already `discretionary` |
| `transition` / `promote` / `demote` actions | **Yes** — in `receipts.ACTIONS` |
| `family` subject kind | **Yes** — in `receipts.SUBJECT_KINDS` |
| `from_state` / `to_state` on a receipt | **Yes** — `ReceiptBody` fields, unused until now |
| a link from receipt → the exact transition row | **Yes** — `Subject.version_or_hash`, which maps to the **indexed** `subject_version` column. **No schema change to PR-5's `state_transitions`.** |

**One refactor was required**, and it is precedented rather than novel:
`record.append_transition()` opened and committed its own connection, so a
transition and its receipt could not share a transaction. Extracted
`record.append_transition_on_connection()` — byte-for-byte the same validation,
now callable inside a caller's transaction. **This is exactly what PR-4 did to
`receipt_ledger.append_receipt()`, for exactly this reason.** It is also why the
arc / re-pin / grandfathering rules are *not* duplicated here: two enforcement
points that can diverge is the failure `evidence_reporting_architecture.md` §7.3
warns against.

---

## 2. Why promotion is verified but never derived

`family.promote` is **discretionary**, and that is load-bearing, not a label.
`receipts.replay()` dispatches on `decision_class`: a discretionary receipt is
`envelope-verified` — its **preconditions** are re-checked, but the **choice**
is attested, never recomputed.

This module therefore has **no function that decides whether to promote**.
`validate_transition()` answers *"is this promotion permitted?"*; a human answers
*"should it happen?"*, and their reason is recorded verbatim. Asserted by test:
`auto_promote`, `promote_if_eligible`, `maybe_promote`, `promote_all`,
`evaluate_promotions` must not exist. `promote_with_receipt()` additionally
**refuses a service actor** — the platform must not be able to promote itself.

**Passing the gates makes a promotion legal, never automatic.** The gates are
necessary conditions, not a decision procedure.

The asymmetry with PR-6 is deliberate: `evidence.admit` is **derived** (replay
recomputes the gate bit-for-bit); `family.promote` is **discretionary** (replay
verifies the envelope and stops). *Evidence admissibility is a computation.
Promotion is a judgment.*

A direct consequence, tested: a promotion **survives a later threshold change**.
Re-running today's thresholds against a 2026 promotion would not *audit* that
decision — it would silently *replace* it with a different one and call the
difference a failure.

---

## 3. The governed front door

```
transition_with_receipt()          the governed path — validate → execute → receipt
record.append_transition()         the RAW primitive — no receipt; flagged as TG1
```

This mirrors exactly the relationship `contract_lifecycle.activate_contract()`
(raw) has to `contract_receipts.activate_contract_with_receipt()` (governed).
The raw primitive **must remain** — bootstrap and migration need it — so
"every transition emits a receipt" is enforced as a **detected invariant (TG1)**
rather than by making the primitive uncallable.

**Validation happens before execution.** An invalid transition raises
`IllegalTransitionError` and writes **nothing** — no transition row, no receipt.
There is no state in which an illegal transition sits in the ledger "flagged as
bad": it never enters.

**Transition and receipt commit in one transaction.** There is no state where a
transition exists without its receipt, or a receipt without its transition
(tested by forcing the receipt write to fail and asserting the transition row
rolled back too).

### Everything authoritative happens under the write lock

```
BEGIN IMMEDIATE                  <- serialize writers FIRST
  derive_state_on_connection()   <- authoritative state, on the LOCKED connection
  ts = now_iso()                 <- stamped under the lock (see below)
  validate_transition()
  persist transition
  persist receipt
COMMIT
```

**PR-8 review correction.** The state was previously derived *before* the lock,
which is a lost update: the architecture audit reproduced two concurrent
transitioners both reading `discovery`, both validating against it, and both
writing — leaving a permanently inconsistent chain in an **append-only** table.
TG4 detected it afterwards; nothing could repair it.

Reading under the lock is verbatim `contract_lifecycle.next_version()`'s
contract (PR-3): the second caller's read does not proceed until the first has
committed or rolled back, so it validates against reality rather than a stale
snapshot. The audit's exact reproduction now yields `#1 discovery→candidate`,
`#2 candidate→candidate` (a legal re-pin) and a clean ledger.

`validate_transition()` opens its own read connections. That is safe under the
lock: SQLite's `RESERVED` admits concurrent *readers* but no other *writer*, and
nothing has been written in the transaction at that point — so those reads
observe exactly the committed state `current` was derived from, and no writer can
move it before `COMMIT`.

**A second bug, found by this PR's own 5-writer test:** `ts` was also stamped
before the lock, so a writer that acquired the lock *second* could carry an
*earlier* timestamp. TG4 reads the chain in `(ts, id)` order, so it reported a
broken chain for a ledger that was in fact correctly serialised. `ts` is now
stamped under the lock; `id` breaks any same-instant tie in commit order. An
explicitly supplied `ts` (bootstrap/backfill) is still honoured.

**`promote_with_receipt()`'s arc check** necessarily runs outside the lock (it
decides whether to call at all), so it passes its observed `current` as
`from_state`, making the check binding via `TG-CUR`. Without that, a promotion
validated as `candidate→validated` could — if the family reached `validated`
concurrently — land inside the lock as a legal `validated→validated` re-pin and
be recorded as a `family.transition`: a promotion request quietly writing a
non-promotion receipt. It now **refuses**, which is the only correct answer.

Guarded by real-thread concurrency tests (2-writer, 5-writer, and the promotion
variant, re-run 5× clean) plus two AST tests asserting `BEGIN IMMEDIATE` precedes
the authoritative derivation and that the connection-less `derive_state_at()` is
never used in the write path — so this cannot silently regress.

---

## 4. Validation (the seven checks)

Every check is recorded in the receipt's `evaluation[]` — these are what replay
re-verifies. The **decision** is not among them: it is the human's, attested in
`reason_text`.

| check_id | Verifies |
|---|---|
| `TG-CUR.current_state` | the ledger's derived state matches the caller's asserted `from_state` — a **lost-update guard** |
| `TG-DST.destination_state` | `to_state` is a known lifecycle state |
| `TG-LEG.legality` | the arc exists in `ALLOWED_TRANSITIONS` |
| `TG-PIN.pinned_evidence` | the run exists and belongs to this family |
| `TG-ADM.admissibility` | PR-6's `evidence.admit`/`.reject` receipt of the type the destination requires |
| `TG-RDY.readiness` | PR-7's readiness block — **when present** (see §5) |
| `TG-PRE.prerequisites` | e.g. Validated's DSR / BH-FDR / provenance preconditions |

`validate_transition()` is **side-effect free** and safe to call speculatively —
e.g. to render *"why can't I promote this?"* without attempting the promotion.

`reason` is **required and non-empty**: every decision type here is
discretionary, and `validate_body()` enforces "reason_text is required for
discretionary decisions" (§1.1: *"BECAUSE reason_text"*). The ledger records
**why**, not merely what.

---

## 5. Readiness is hooked, not yet armed — stated explicitly

`data_completeness_gate.md` §7 **G2** requires the Validated gate to gain a
100%-READY precondition. PR-7 built the readiness *engine*; **G3** (the
`readiness` block in `runs.metrics_json`) is that document's own PR-4 and **does
not exist** — no run on this platform carries one.

Therefore `TG-RDY`:
- **enforces** a readiness block when the pinned run has one (tested: 2 non-READY
  days → the transition is refused);
- reports **`not_computed`** when absent, and does **not** block.

Making it mandatory now would instantly invalidate all 35 real families — a
**backward-compatibility break masquerading as rigour**. The hook is here and
tested; arming it is PR-9. This follows PR-5's rendering discipline for the same
reason: *"we did not look"* must never be indistinguishable from *"we looked and
it was fine"*.

---

## 6. Replay

`replay_transition_receipt()` verifies exactly three things, and deliberately
not a fourth:

1. **Receipt integrity** — `receipt_id` recomputes from the stored body.
2. **Transition legality** — the recorded arc is still legal under the state machine.
3. **Governance envelope** — via `receipts.replay()`, which for a discretionary
   decision checks the envelope (known decision_type, an actor, a non-empty
   `reason_text`) and stops.

It supplies **no `rule_fn`** — asserted structurally by an AST test, not a
docstring. Supplying one would be a category error: it would recompute a human's
choice.

If the state machine ever changed under recorded history, replay says so
(`legality_ok=False`) rather than quietly accepting it.

**Bootstrap-attested receipts refuse replay** (they never captured a replayable
evaluation), but their **hash and legality are still checked** — those hold
regardless.

---

## 7. Point-in-time reconstruction — from receipts

`reconstruct_state_at(db, family_id, as_of)` returns `(state, justifying_run_id,
ts)`, **mirroring `record.derive_state_at()` exactly so the two are directly
comparable.**

That comparison is the point. They are **two independent derivations of the same
fact**, and `TG5` asserts they agree. A divergence means either a transition was
written without a receipt, or a receipt was forged — **both invisible if only one
source is consulted.**

Verified on the real ledger: all 13 transitions reconstruct identically from the
receipt chain and from `state_transitions` — **state and pinned run**.

---

## 8. Governance validation (`--check`)

`TG`-prefixed so they are visibly distinct from `lifecycle.py`'s C1–C9,
`evidence_governance`'s EG1/EG2, and `dq`'s DQ-*.

| Code | Detects |
|---|---|
| `TG1` | a transition with **no** governance receipt (the raw-primitive bypass) |
| `TG2` | **duplicate** receipts for one transition; **orphan** receipts pointing at a non-existent transition |
| `TG3` | an **illegal arc** recorded in the ledger |
| `TG4` | **ordering violations** — a `from_state` that doesn't match the previous `to_state` (broken chain), or a backdated `ts` |
| `TG5` | the two independent derivations **disagree** |
| `TG6` | a receipt that does not **replay** (envelope + integrity) |

Wired into `report.py --check` as a **third additive hook**, appended after C1–C9
and EG1/EG2, never folded into or renumbering either. Skipped for `--as-of` for
the same reason EG is: `governance_receipts` has no as-of-derivation equivalent,
so applying it to a historical run would misreport rather than inform.

---

## 9. Public interfaces

```python
# execution
transition_with_receipt(db, family_id, to_state, justifying_run_id=None,
                        actor="operator", actor_type="human", reason="",
                        family_meta=None, thresholds=None, from_state=None,
                        ts=None) -> dict
promote_with_receipt(db, family_id, to_state, justifying_run_id, actor, reason,
                     family_meta=None, thresholds=None, from_state=None,
                     actor_type="human") -> dict          # refuses a service actor

# validation
validate_transition(db, family_id, to_state, justifying_run_id,
                    family_meta=None, thresholds=None,
                    from_state=None) -> TransitionValidation   # side-effect free
validate_transition_governance(db, registry=None) -> list[str]  # TG1..TG6

# replay
replay_transition_receipt(db, receipt_id) -> TransitionReplay
    (.receipt_id, .transition_id, .hash_ok, .legality_ok, .replay, .errors, .ok)

# point-in-time
reconstruct_state_at(db, family_id, as_of=None) -> (state, run_id, ts)
reconstruct_all_states_at(db, as_of=None) -> {family_id: (state, run_id, ts)}

# queries
get_transition_receipts(db, family_id) -> list[dict]
get_receipt_for_transition(db, family_id, transition_id) -> dict | None
get_promotions(db, family_id=None) -> list[dict]

# bootstrap
bootstrap_transition_receipt(db, transition, attestation_note, actor="bootstrap")

# pure
classify_arc(from_state, to_state) -> str    # family.promote|.demote|.transition

# exceptions
TransitionGovernanceError, IllegalTransitionError,
TransitionNotFoundError, DuplicateTransitionReceiptError
```

Arc classification (pure, no I/O): promotion arcs are
`candidate→validated`, `validated→promotion_ready`,
`promotion_ready→production_ready`; demotion arcs are
`promotion_ready→validated`, `production_ready→validated`. Everything else is a
plain transition. Promotion and demotion are not "a transition with a nicer
name" — they are the arcs a reviewer will search the ledger for, and the
taxonomy already separates them, so the ledger should too.

---

## 10. Migration notes

`migrate_bootstrap_transition_receipts.py` — dry-run by default, `--commit`-gated,
timestamped backup, idempotent. Mirrors PR-5's `migrate_run_kind.py` and PR-6's
`migrate_bootstrap_evidence_admission.py`.

**Run against the real ledger:** 13 bootstrap receipts written (11 from PR-1's own
bootstrap transitions, plus G3-ATR and IVRV). Receipt count 13 → 26; chain valid;
`--check` clean; idempotent re-run reports 0 pending.

**Bootstrap honesty, and one correction made during the run.** These receipts
carry `attestation='bootstrap'`: the validation that justified the transitions was
never captured in a replayable form, so `replay()` refuses rather than
approximating.

The first migration run wrote receipts with **no `inputs[]`**, which made
`reconstruct_state_at()` return `pinned_run_id=None` for every pre-PR-8 family
while `record.derive_state_at()` returned the real run. **TG5 did not catch it**
(it compares only `state`), and it would have quietly made the receipt chain a
weaker record of history than the table it governs. The `justifying_run_id` is a
fact **already recorded on the transition row** — carrying it fabricates nothing.
Fixed, the ledger restored from its backup (integrity-checked first), and
re-migrated; the two derivations now agree on **state and run**. Locked in by two
tests, one of which asserts equality on the **real** ledger.

What is still *not* carried is an `evaluation[]`: those preconditions were never
checked at the time, and inventing them would be a lie with a valid hash.

---

## 11. Backward compatibility

- `record.append_transition()` — **behaviour unchanged**; now a thin wrapper. It
  additionally **returns** the new row's `id`, which every pre-PR-8 caller
  ignores (`None` → `int` is additive). All arc, re-pin, and grandfathering
  validation still fires, tested.
- PR-6's `pin_evidence()` / `repin_evidence()` — **untouched**, still work.
- Every migration script calling `append_transition()` — unaffected.
- No table altered. No existing row rewritten. No taxonomy change.
- **One line changed in one existing test**
  (`test_evidence_governance.py::test_report_check_passes_with_admission`): it now
  passes `check_transition_governance=False`, scoping it to what it is actually
  about — PR-6's EG hook. Its fixture writes a transition via the raw primitive,
  which TG1 correctly flags. **That is the new check working, not a regression**,
  and it is the same situation PR-3 documented when it repointed a PR-2 test.
  PR-8's own suite covers the TG hook directly.

Full suite: **982 passed, 14 pre-existing failures** — the identical 14 as the
baseline (verified by stashing): 12 missing-`apscheduler`, 2 date-dependent expiry
tests. No regressions.

---

## 12. Remaining work for PR-9

**Compare the recorded `policy_hash` at replay (the audit's optional item —
deferred, and why).** The receipt *does* record the governing state machine
(`authority.policy_hash` covers `ALLOWED_TRANSITIONS`), but replay never compares
it to today's. Consequence: a **widened** machine replays green despite the rules
having changed; a **narrowed** one reports *"illegal arc — either the receipt is
forged, or the state machine changed"*, unable to distinguish the two.

It was assessed for inclusion in PR-8 and **is not trivial**, which is why it is
deferred rather than rushed: `_policy_hash()` mixes the state machine *and*
`thresholds` into one digest, and `replay_transition_receipt(db, receipt_id)`
receives no thresholds — so comparing would require either a signature change
(threading thresholds into replay) or a receipt-body change (recording a separate
`state_machine_hash`). The latter is the right design and is additive: existing
receipts keep verifying and replaying byte-identically; only new receipts gain the
field. **No data is lost meanwhile** — the hash is already recorded, so history
remains diagnosable retroactively once the check exists.

**Arm the readiness gate (G2/G3).** Land the `readiness` block in
`runs.metrics_json` (`data_completeness_gate.md`'s own PR-4), then flip `TG-RDY`
from *reported* to *blocking*. The hook and its tests already exist; PR-9 supplies
the data and removes the `not_computed` escape.

**`INADMISSIBLE` rather than refusal (G1).** A run whose window contains a
non-READY day should be **written and flagged**, not refused — "refusing to write
loses the fact that the day existed, and lineage loss is the one thing the
Evidence Library exists to prevent."

**The still-open pairwise-independence enforcement** (CLAUDE.md §16, Proposal 1)
is now reachable: `TG-PRE` is the natural place to call
`check_validated_independence()` once it exists. It must land **before the first
Candidate → Validated promotion**, and `promote_with_receipt()` is exactly the
function that would enforce it.

**Deferred by their own designs, unchanged by this PR:** receipt signing and
anchoring, the `--verify-chain` CLI, the impact report / change classifier
(`contract_versioning.md` PR-C), reprocessing execution (PR-D).

**One known, deliberate divergence carried from PR-7:** `evidence_governance`'s
private `_RULE_REGISTRY` still hand-rolls the dispatch mechanism that
`dq/rules.py`'s `RuleRegistry` generalises. Collapsing them is a
zero-behaviour-change refactor. Still proposed, not performed.
