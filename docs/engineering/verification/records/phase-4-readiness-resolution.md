# Phase 4 (Dataset Builder) — Readiness Resolution

| Field | Value |
|---|---|
| **Title** | Phase 4 Readiness Resolution — blocker resolution, decision log, and pre-Phase-4 action list |
| **Status** | **Complete — Phase 4 remains NOT AUTHORISED; the path to authorisation is now fully specified** |
| **Version** | 1.0.0 |
| **Document Type** | Engineering readiness resolution (not an ADR, not a roadmap) |
| **Governing ADR** | [`ADR-I011`](../../../architecture/adr/ADR-I011-label-store-architecture.md) v0.1.0 (**Draft**) · [`ADR-I012`](../../../architecture/adr/ADR-I012-research-dataset-builder.md) |
| **Roadmap** | [`phase_3_label_store_roadmap.md`](../../phase_3_label_store_roadmap.md) v1.1.0 |
| **Predecessors** | [Phase 3 Closeout](phase-3-closeout.md) · [Phase 3 Closure Review](phase-3-closure-review.md) · [Gap Matrix](phase-3-gap-matrix.md) · [TD Register](../../label_store_technical_debt_register.md) |
| **Authors** | Rohith Suresh — Owner / Architect · Resolved with Claude Opus 5 |
| **Date** | 2026-08-06 |
| **Code written** | **None.** Documentation only — no implementation, no commit, no roadmap edit applied |

---

## 1. Executive Resolution

The Phase 3 Closeout named two blockers. This resolution establishes new evidence against the repository and settles both, plus the two open questions attached to them.

| Item | Entering state | Resolved state |
|---|---|---|
| **B-1** — Phases 0–3 uncommitted | Blocker | 🔴 **UPHELD as blocking.** Only Rohith can discharge it. Plan already exists; nothing engineering-side can substitute |
| **B-2** — Zero `Active` Label Specifications | Blocker, unowned, undefined scope | 🟡 **DOWNGRADED to scoped, pre-de-risked work.** Behavioral equivalence for the four Specifications Phase 4 actually needs is **empirically verified today, 148/148 exact match**. Becomes **WBS 3.11**, ~1 session |
| **WBS 3.8c** — label registration receipts | Proposed | ✅ **RULED: formally add.** The architecture is *not* already complete — LG1/§9 are unrealised for all 8 Specifications. Zero taxonomy change needed |
| **`LS-DEC-005`** — `P3-1`'s "shared-code-path test" | Open contradiction with `LR1` | ✅ **RULED: replace with behavioral equivalence.** Already this repository's proven pattern, empirically satisfied for labels, and **strictly stronger** than the wording it replaces |

**The single most useful discovery:** resolving `LS-DEC-005` and resolving `B-2` require *the same mechanism*. `activate_specification()`'s `equivalence_check` parameter is exactly the behavioral-equivalence callable that `P3-1`'s first half needs. One ~1-session milestone (**WBS 3.11**) discharges both.

**Phase 4 is not authorised today.** After this resolution the remaining path is **one commit, two small milestones, and four sign-offs** — all specified in §9, with no open design questions.

---

## 2. New Evidence Established This Session

Four findings, none of which existed in the Phase 3 Closeout. Each was produced by execution against the real repository, not inference.

### `E-1` — Behavioral equivalence between the Label Store and H6 is exact, today

Compared all 148 forward-tier `outcome_json.forward_return` values against `forward_evidence.db`'s `trade_labels.forward_return`, joined on (trade id, horizon), using NaN-safe JSON-string comparison:

```
EQUIVALENCE: matched=148  mismatched=0  unresolved_key=0
```

**Significance.** The claim `P3-1` was reaching for — that the Label Store faithfully reproduces H6's declare-then-fill outcome — is **true and now demonstrated**, without any shared code path. This is the empirical foundation for both rulings below.

### `E-2` — A failed activation permanently and irrecoverably bricks a Specification

Verified by execution against a disposable database:

| Step | Result |
|---|---|
| `activate_specification(..., lambda: False)` | `Registered → Rejected`, `SpecificationRejected` raised |
| Re-register byte-identical content | Returns the **same hash**; row **stays `Rejected`** (content addressing makes the idempotent branch a no-op) |
| `activate_specification(...)` again | **Refused** — `TransitionError: phase 'Rejected', not 'Registered'` |

**Significance.** Activation is a **one-shot, irreversible act per Specification**, and there is no recovery arc. The only escapes are fabricating different Specification content (dishonest — it would invent a convention nothing uses) or manual DDL against an append-only table. **This hazard is documented nowhere in the roadmap, the ADR, or any milestone record.** It is the reason WBS 3.11 must carry a mandatory dry-run-on-a-copy gate.

### `E-3` — Only four of eight Specifications are activatable from this machine

| Spec | h | Rows | Binding | Equivalence target | Locally verifiable? |
|---|---|---|---|---|---|
| `ff680d93f01d` | 5 | 37 | `forward_evidence.daily_job.trade_labels` | H6 `trade_labels` | ✅ **verified 37/37** |
| `0ecb0802dab4` | 7 | 37 | `forward_evidence.daily_job.trade_labels` | H6 `trade_labels` | ✅ **verified 37/37** |
| `0c00e1091428` | 14 | 37 | `forward_evidence.daily_job.trade_labels` | H6 `trade_labels` | ✅ **verified 37/37** |
| `4e1f2b736e9c` | 30 | 37 | `forward_evidence.daily_job.trade_labels` | H6 `trade_labels` | ✅ **verified 37/37** |
| `e785df371f90` | 1 | 0 | `research_evidence.engine.ret_fwd1` | `engine.load_master()` | ⚙️ Feasible — `corpus_daily` has 1,987 rows |
| `0db88b4fc0dd` | 1 | 0 | `research_evidence.engine.net_daily` | `engine._net_daily()` | ⚙️ Feasible |
| `89869c534200` | 7 | 0 | `forward_evidence.daily_job.close_trade` | `trade_exits.net_return` | ⚙️ Feasible — 37 non-null rows |
| `57ce016e51a0` | 7 | 0 | `forward_evidence.runner_base.trade_returns_view` | `evidence_forward.db` `trade_returns` | ❌ **Blocked** — every table is 0 rows locally |

**Significance.** `57ce016e51a0` (convention C3) cannot be activated from this machine. This is roadmap risk `A-4`/`LR3` — *"production state is not observable from the dev machine"* — materialising exactly as predicted. It is **not** a defect, and it does **not** block Phase 4: C3 has no declared values, so no dataset can cite it regardless.

### `E-4` — WBS 3.8c requires no taxonomy change

`receipts.DECISION_TAXONOMY` already carries `"label.register": "derived"` (added by WBS 3.8a). A label-side backfill is therefore purely mechanical — no `receipts.py` edit, no new decision class, no new subject kind.

---

## 3. Blocker B-1 — Uncommitted Phases 0–3

### Ruling: 🔴 **UPHELD as blocking. Not resolvable by engineering action.**

Re-verified this session: `git log` head is still `c14ba38` (Program P1). Phases 0, 1, 2, and 3 exist solely as uncommitted working-tree state — 26 modified tracked files plus ~30 untracked paths including all of `research/label_store/`, `research/feature_store/`, `research/admissibility/`, `docs/architecture/`, and `docs/engineering/`.

**Why nothing here can substitute for the commit.** Every other item in this document is reversible precisely *because* version control exists. Without it, WBS 3.8c and 3.11 would write to real databases with no diffable record of the code that did so — inverting this repository's own established discipline, in which every migration is preceded by a backup and a reviewable diff.

**Why it must be first.** WBS 3.11 performs an *irreversible* registry transition (`E-2`). Performing an irreversible act on an unversioned tree removes the last recovery layer.

**Discharge.** Execute the ordered eight-commit plan at [Phase 3 Closeout §4](phase-3-closeout.md#4-commit-plan). Pre-commit checks were re-verified this session: `.env` unstaged, all five `.db` files and their `-wal`/`-shm` sidecars gitignored, both suites green.

**This session did not commit and will not without an explicit instruction**, consistent with the standing rule.

---

## 4. Blocker B-2 — Zero `Active` Label Specifications

### Ruling: 🟡 **DOWNGRADED from blocker to scoped work — new WBS 3.11.**

The Closeout rated B-2 a hard blocker because it was unowned and its scope was undefined — *"genuine design work, not a mechanical backfill."* Evidence `E-1` and `E-3` change that assessment materially.

**Why it downgrades:**

1. **The design question is answered.** "What does equivalence mean for a label binding?" now has a concrete, precedented answer: compare the Label Store's stored outcome against the source system's own value, independently read. This is Phase 2's `_equivalence_check()` pattern applied unchanged.
2. **The answer is pre-verified, not hypothetical.** 148/148 exact match, measured. The risk that activation fails on the Specifications Phase 4 needs is not merely low — it is measured at zero for those four.
3. **Phase 4 does not need all eight.** DI4 requires only that a dataset cite `Active` Specifications. The four H6 horizon Specifications are the only ones carrying real values, so they are the only ones a dataset can meaningfully cite. Activating those four fully unblocks Phase 4's own exit criterion.

**Why it remains real work rather than nothing:** `E-2`'s irreversibility hazard means activation cannot be done casually, and three of the remaining four Specifications need their own equivalence definitions written against `engine.py` and `trade_exits`.

### Proposed WBS 3.11 — Specification activation via behavioral equivalence

| Field | Value |
|---|---|
| **Owns** | `ADR-I011` §12.1 (Specification lifecycle), DI4; discharges `P3-1`'s first half under the `LS-DEC-005` ruling |
| **Size** | S (one focused session) |
| **Depends on** | `B-1` discharged; **WBS 3.8c merged** |
| **Entry criterion** | Registration receipts exist for all 8 Specifications; a disposable copy of `label_store.db` available |
| **Exit criterion** | The four H6 horizon Specifications are `Active`, each activated through a behavioral-equivalence check that independently re-reads H6 and compares NaN-safely; **a dry run against a disposable copy precedes any real `--commit`**; `e785df371f90`, `0db88b4fc0dd`, `89869c534200` activated or explicitly deferred with a stated reason; `57ce016e51a0` **deferred by name**, citing `A-4` (no local data); zero H6 mutation, proven by SHA-256 |

**Why Phase 3 and not Phase 4.** Roadmap §14.1 assigns `ADR-I011` §12.1 — the Specification lifecycle — to **WBS 3.1**. Activation is therefore an unfinished Phase 3 obligation, not a new Phase 4 responsibility. Placing it in Phase 4 would transfer a Label Store requirement to the Dataset Builder, which is precisely the drift the Closure Review flagged when three requirements silently lost their owners.

**Mandatory safety gate, from `E-2`.** WBS 3.11 must run its full activation sequence against a disposable copy of `label_store.db` and confirm all intended transitions succeed **before** touching the real file. Unlike every prior Phase 3 migration, a failed run here cannot be re-run — it consumes the Specification permanently.

---

## 5. WBS 3.8c — Should It Be Formally Added?

### Ruling: ✅ **YES — formally add. The architecture is not already complete.**

The question posed was whether the current architecture is already complete, given that WBS 3.8a built a working receipt mechanism. It is not, and the distinction matters.

**The case that it is already complete, considered and rejected.** WBS 3.8a delivered `register_specification_with_receipt()`, replay, chain verification, and `validate_label_governance()`. Every *mechanism* LG1 requires exists and is tested. One could argue the remaining absence is merely historical data.

**Why that argument fails.** `ADR-I011` §9 places *"registration receipt"* in the **Specification metadata** table — it is a property of each Specification, not a capability of the store. Eight Specifications lack that property. `validate_label_governance()` confirms it, reporting `LREC1` for all eight against real data. A store that *can* emit receipts but holds eight Specifications without them has realised the mechanism and not the requirement.

**Three additional reasons to add it:**

1. **It restores the symmetry scope transfer `S-3` exists to enforce.** `S-3`'s stated rationale: *"If labels emit receipts and features do not, a reader encountering a receipt-less Specification cannot distinguish 'not required' from 'missing — defect'."* Today features have all 17 and labels have none — the same ambiguity, reversed. The Closeout named this; leaving it open would ratify the exact condition `S-3` was written to prevent.
2. **It must precede activation.** WBS 3.11 makes Specifications `Active` and therefore citable by sealed datasets. An `Active`, citable Specification with no record of its own registration is a worse provenance state than a `Registered` one, because Phase 4 will pin it into a sealed artifact.
3. **It is cheap and fully precedented.** `E-4` confirms no taxonomy change. The script is mechanically identical to `migrate_bootstrap_feature_receipts.py`, itself the fourth use of the `attestation="bootstrap"` pattern in this repository.

### Proposed WBS 3.8c — Label Specification bootstrap receipt backfill

| Field | Value |
|---|---|
| **Owns** | `ADR-I011` LG1, §9 (registration receipt); closes `LS-GAP-001` |
| **Size** | S (one focused session) |
| **Depends on** | `B-1` discharged; 3.8a merged (done) |
| **Deliverable** | `research/label_store/migrate_bootstrap_label_receipts.py` |
| **Exit criterion** | 8 receipts written with `attestation="bootstrap"`, `decision_type="label.register"`; dry-run default, `--commit`-gated, backup taken; idempotent re-run reports 0 pending; `verify_chain()` clean; `validate_label_governance()` reports **zero** `LREC1` errors against the real `label_store.db`; no `receipts.py` change |

**One design note carried forward, not decided here.** WBS 3.8b's own defect — an unconditional `init_db()` in the dry-run path that silently created a table on a real database — must not be repeated. `label_store.db` has **no** `governance_receipts` table today, making it the identical exposure. WBS 3.8c must gate schema creation behind `--commit`, exactly as 3.8b's correction did.

---

## 6. `LS-DEC-005` — "Shared-Code-Path Test" vs Behavioral Equivalence

### Ruling: ✅ **Replace "shared-code-path test" with behavioral equivalence. `LR1` is preserved unchanged.**

**The contradiction, restated.** Roadmap `P3-1` requires *"the declare-then-fill discipline is proven reused via a shared-code-path test."* Roadmap `LR1` — *"the single most important design decision in Phase 3"* — requires that H6 keep running unchanged in its own tables while the Label Store merely observes it. A test proving `daily_job.py` and the Label Store share a fill path cannot exist without undoing `LR1`.

**Four reasons behavioral equivalence is the correct resolution:**

1. **It is already this repository's proven pattern for exactly this problem.** `migrate_evidence_engine_features.py::_equivalence_check()` calls a wrapped binding *and* independently calls `engine.load_master()`, comparing outputs with NaN-safe JSON-string equality. It was built, reviewed, and shipped in Phase 2 under the explicit requirement that *"every migrated feature must demonstrate behavioural equivalence with its current implementation."* Adopting it label-side reuses a decision already made, rather than making a new one.
2. **It is empirically satisfied for labels today** — `E-1`, 148/148 exact, zero mismatches.
3. **It preserves `LR1` completely.** Equivalence compares two *independent* executions. It imports nothing from `daily_job.py`, adds no trigger to `trade_labels`, and requires no change to H6. Architectural separation is not merely retained; it is what makes the comparison meaningful.
4. **It is strictly stronger than the wording it replaces.** A shared-code-path test proves only that the same function was invoked. Behavioral equivalence proves the *outputs agree* — which is the property `P3-1` actually cares about, and the only one that would catch a wrong column, an off-by-one, or a silent transformation. **This is a strengthening of `P3-1`, not a relaxation.**

### Proposed roadmap amendment — prepared, **not applied**

The ruling requires a surgical edit to roadmap §8.2. Exact proposed replacement for `P3-1`:

> | **P3-1** | ⚠️ The parent roadmap's Phase 3 DoD — *"the H6 fill-once guard is proven reused (not reimplemented) via a shared-code-path test"* — **restated per `A-1` and `LR1`**: the declare-then-fill *discipline* is proven reused via a **behavioural-equivalence test** — the Label Store's stored outcomes and the source system's own values are read **independently** and compared for exact equality — **and** enforcement is proven strengthened to the storage layer. Both halves required. **A shared code path is explicitly NOT required and would violate `LR1`**; equivalence of behaviour is the stronger claim and the one that must hold. |

Accompanied by a changelog entry: *"1.1.1 — resolves `LS-DEC-005`: `P3-1` restated to require behavioural equivalence rather than a shared code path, removing an internal contradiction with `LR1`. No architectural decision changed; the criterion is strengthened, not relaxed."*

**Why it is not applied in this session.** `phase_3_label_store_roadmap.md` is an approved governance artifact whose §15 authorisation line carries Rohith's name, and the Phase 3 Closeout assigns `LS-DEC-005` to Rohith explicitly. Amending a Definition-of-Done criterion after the phase is built is the owner's act, not the auditor's. The edit above is exact and one instruction away from being applied.

**Consequence once ratified:** `P3-1` moves from ⚠️ CONDITIONAL to ✅ **MET** — its second half is already proven, and its first half is proven by `E-1` and re-proven mechanically by WBS 3.11's activation checks.

---

## 7. Phase 4 Readiness Decision

# ⛔ PHASE 4 REMAINS **NOT AUTHORISED**

**But the path is now fully specified, with no open design questions.**

| Condition | Entering this session | Now | Remaining effort |
|---|---|---|---|
| **B-1** Phases 0–3 committed | 🔴 Blocking, plan exists | 🔴 **Blocking, unchanged** | Rohith's act |
| **B-2** ≥1 `Active` Label Specification | 🔴 Blocking, unowned, undefined | 🟡 **Scoped: WBS 3.11, equivalence pre-verified** | ~1 session |
| `LS-GAP-001` Label registration receipts | ⚪ Recommended | 🟡 **Ruled in: WBS 3.8c** | ~1 session |
| `LS-DEC-005` `P3-1` reading | ⚪ Open contradiction | ✅ **RATIFIED 2026-08-07** | One signature |
| `LS-DEC-003` Reviewer sign-off ×13 | 🔴 Blocks Phase 3 closure | ✅ **RATIFIED 2026-08-07** | Review time |
| `LS-DEC-004` `label_policy.yaml` strategy | ⚪ Open | ⚪ **Unchanged** — recommendation stands (ratify the structural approach) | One signature |

**What changed materially:** B-2 was the only blocker carrying genuine unknown design risk. It no longer does. Every remaining item is either a signature, a commit, or a session of mechanically-precedented work with its exit criterion already written.

**What has not changed:** B-1. It is the same blocker, with the same discharge path, and it now gates two additional milestones that perform irreversible writes.

---

## 8. Decision Log

| ID | Decision | Ruling | Basis | Status |
|---|---|---|---|---|
| **`LS-DEC-003`** | Reviewer sign-off on **thirteen** Phase 3 records | **Required before Phase 3 closure.** No engineering substitute exists | Roadmap §8.1 named this explicitly and promised not to repeat Phase 2's five unsigned records | ✅ **RATIFIED 2026-08-07** |
| **`LS-DEC-004`** | `label_policy.yaml` rollback strategy | **Recommend ratifying the structural approach** and recording that §10's four config keys are superseded | Four milestones independently satisfied "non-blocking by default" structurally; §10 itself refuses a config switch for WBS 3.2 on the grounds that a switch disabling falsification protection will eventually be left off | ✅ **RATIFIED 2026-08-07** |
| **`LS-DEC-005`** | `P3-1` "shared-code-path test" | ✅ **DETERMINED: behavioural equivalence.** `LR1` preserved unchanged. Exact roadmap text prepared (§6), not applied | Phase 2 precedent; `E-1`'s 148/148 verification; strictly stronger than the wording replaced | ✅ **RATIFIED 2026-08-07** |
| **`LS-DEC-006`** | Commit Phases 0–3 | **Required. First action.** Gates two irreversible-write milestones | `LR13` materialised; `E-2` makes WBS 3.11 unrecoverable on failure | ✅ **RESOLVED 2026-08-06** — discharged by action (committed, pushed, tagged `phase-3-baseline-v1.1.0`), not by ratification. Not part of `E2` |
| **`LS-DEC-007`** *(new)* | Formally add **WBS 3.8c** — label registration receipt backfill | ✅ **DETERMINED: add.** §9 makes the receipt a per-Specification property; mechanism ≠ realisation | `S-3`'s symmetry rationale; `E-4` (no taxonomy change); must precede 3.11 | ✅ **RATIFIED 2026-08-07** |
| **`LS-DEC-008`** *(new)* | Formally add **WBS 3.11** — activation via behavioural equivalence | ✅ **DETERMINED: add, as Phase 3 work.** Roadmap §14.1 assigns §12.1 to WBS 3.1, so this is an unfinished Phase 3 obligation, not a Phase 4 one | `E-1` pre-verifies the four Specifications Phase 4 needs; `E-2` mandates a dry-run-on-copy gate | ✅ **RATIFIED 2026-08-07** |
| **`LS-DEC-009`** *(new)* | Disposition of `57ce016e51a0` (convention C3) | **Defer by name**, citing `A-4`. Do **not** attempt activation from this machine | `E-3`: every table in `evidence_forward.db` is empty locally; C3 has no declared values, so no dataset can cite it | ✅ **RATIFIED 2026-08-07** |

**Superseded from the Phase 3 Closeout:** `LS-GAP-002` is no longer "unowned" — `LS-DEC-008` assigns it to WBS 3.11. `LS-GAP-001` is no longer "unowned" — `LS-DEC-007` assigns it to WBS 3.8c. The Gap Matrix's Unowned bucket reduces from five items (`U1`–`U5`) to three (`U3`, `U4`, `U5` — the documentation deliverables).

---

## 9. Pre-Phase-4 Action List

Ordered by dependency. Every item's exit criterion is already written; none requires new design.

| # | Action | Type | Owner | Blocks | Notes |
|---|---|---|---|---|---|
| **1** | **Execute the eight-commit plan** ([Closeout §4](phase-3-closeout.md#4-commit-plan)) | Rohith's act | Rohith | Everything | Pre-commit checks re-verified this session. **Must be first** — actions 3 and 4 write irreversibly |
| **2** | **Ratify `LS-DEC-005`, `007`, `008`, `009`**; apply the §6 roadmap amendment | Signature | Rohith | 3, 4 | Adds WBS 3.8c and 3.11 to the roadmap; roadmap goes to v1.1.1 |
| **3** | **WBS 3.8c** — label registration receipt backfill | Engineering | — | 4 | ~1 session. Gate `init_db()` behind `--commit` (3.8b's own lesson). Exit: 0 `LREC1` errors |
| **4** | **WBS 3.11** — activation via behavioural equivalence | Engineering | — | Phase 4 `E5` | ~1 session. **Dry run against a disposable copy first — failure is irreversible (`E-2`)**. Exit: ≥4 `Active` |
| **5** | **Reviewer sign-off on all thirteen Phase 3 records** (`LS-DEC-003`) — ✅ **done 2026-08-07** | Signature | Rohith | Phase 3 closure | Thirteen including the two new records from actions 3–4 (WBS 3.8c, 3.11) |
| **6** | **Ratify `LS-DEC-004`**; schedule or waive the three missing §13 deliverables (`LS-GAP-006`) | Signature | Rohith | Phase 3 closure | `label_migration_register.md`, the maturity contract's Tier D extension, `label_policy.yaml` |
| **7** | **Declare Phase 3 closed**; sign the Closure Review and Closeout | Signature | Rohith | Phase 4 | |
| **8** | **Audit `ADR-I012` against the delivered Label Store** before writing a Phase 4 roadmap | Engineering | — | Phase 4 quality | Recommended, not blocking. Phase 3's own roadmap §1 did this for `ADR-I011` and found three material divergences (`A-1`, `A-2`, `A-3`) that would otherwise have produced wrong work |

**Critical path: actions 1 → 2 → 3 → 4.** Roughly two engineering sessions plus Rohith's signatures. Actions 5–7 can proceed in parallel with 3–4.

### Two things Phase 4 must carry forward regardless

- **The 148 labels are `Context` and must remain `Context`.** Risk `LR2` was rated High precisely because they are 100% matured and *look* like ideal forward evidence. `ADR-I011`'s Standing Note is unconditional: *"A label declared after its outcome is known is not forward evidence, whatever it is called."*
- **Every real admissibility ceiling currently caps at `Certification.UNKNOWN`** (`LS-TD-011`) — three of four sources have no gauntlet wiring. Phase 4's DI5 meet will be dominated by this on the evidence leg regardless of label quality. It should be a planning input, not a surprise.

---

## 10. Verification Method

Read-only. Findings rest on: direct source reading (`registry.py`'s `activate_specification`, `migrate_evidence_engine_features.py`'s `_equivalence_check`, `receipts.py`'s taxonomy); live queries against `label_store.db`, `forward_evidence.db`, and `evidence_forward.db`; a 148-row behavioural-equivalence comparison (`E-1`); an executed four-step irreversibility probe against a disposable database (`E-2`); and binding-by-binding feasibility mapping against real row counts (`E-3`).

**No source file, test, database, configuration, or roadmap was created, modified, or deleted** other than this document. **No commit was made.** The only writes were to disposable temporary databases under the session scratchpad.

---

| Role | Name | Date |
|---|---|---|
| Readiness resolution | Claude Opus 5 | 2026-08-06 |
| **Ratification of `LS-DEC-005`, `007`, `008`, `009`** | **Rohith Suresh** | **_______________** |
| **Phase 4 authorisation** | **Rohith Suresh** | **_____ WITHHELD pending actions 1–7 _____** |
