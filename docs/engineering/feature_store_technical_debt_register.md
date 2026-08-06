# Feature Store — Technical Debt Register

| Field | Value |
|---|---|
| **Title** | Technical Debt Register — Feature Store (`research/feature_store/`) |
| **Status** | **Active — living register** |
| **Version** | 1.0.0 |
| **Document Type** | Engineering Process Register (not an ADR, not an architecture document) |
| **Opened** | 2026-08-05, at Phase 2 Closeout |
| **Companion** | [Phase 2 Closeout Report](verification/records/phase-2-closeout.md) · [Phase 2 Closure Review](verification/records/phase-2-closure-review.md) · [Hazard Register](hazard_register.md) · [Engineering Verification Standard](verification/engineering_verification_standard.md) §10 |
| **Authors** | Rohith Suresh — Owner / Architect · Drafted with Claude Opus 5 |

---

## 0. Purpose and Boundaries

This register is the **durable home** for everything the Feature Store deferred, simplified, or left partial — consolidated from five milestone verification records and the Phase 2 Closure Review into one place with stable identifiers.

It exists because the Closure Review's `RR-2` named a specific failure mode: *unowned work in a roadmap-driven programme tends to stay unowned*, while every future report truthfully says "Phase 2 complete." Four `ADR-I010` requirements had **no owning WBS item anywhere in the roadmap**. Without a register they would have survived only as prose inside superseded milestone records.

**Three classes, deliberately separated:**

| Class | ID prefix | Definition |
|---|---|---|
| **Technical debt** | `FS-TD-nnn` | A milestone-scoped simplification. The thing was built; a cheaper form was chosen deliberately, with a stated boundary. |
| **Realisation gap** | `FS-GAP-nnn` | An `ADR-I010` requirement with **no implementation and no owning WBS item**. Not a milestone failure — a gap between the roadmap's Phase 2 scope and the ADR's requirement set. |
| **Decision owed** | `FS-DEC-nnn` | A deliberate, staged choice that requires an explicit human act to complete or confirm. Not debt (nothing is missing) and not a gap (nothing is unimplemented) — but it will silently become one if never revisited. |

**Relationship to the [Hazard Register](hazard_register.md).** A *hazard* is a generalisable risk pattern recurring **beyond** the files a milestone touched (`EH-001`, `EH-002`). Debt here is scoped to the Feature Store. Where the two meet — `FS-TD-005` — this register points at the hazard register and does not duplicate it.

**This register does not remediate anything.** No entry here is authorised for implementation by its presence. Closing an entry requires separately-scoped, separately-approved work.

---

## 1. Register — Summary

### 1.1 Realisation gaps (`ADR-I010` requirements with no owning WBS item)

| ID | `ADR-I010` ref | Title | Severity | Nearest owner | Status |
|---|---|---|---|---|---|
| [`FS-GAP-001`](#fs-gap-001--admissibility-inheritance-is-not-implemented) | §9.2, §12.4, §13 Tier C | Admissibility inheritance is not implemented; Phase 0's lattice is unused by Phase 2 | **High** (constitutional — IQROS-P4) | Phase 4 (WBS 4.4 / RF4) | **Open — unowned** |
| [`FS-GAP-002`](#fs-gap-002--tier-d-output-hygiene-is-not-implemented) | §13 Tier D | Output hygiene — validity fraction, degenerate detection, warm-up boundary exactness | **Medium** | **None — actionable today** | **Open — unowned** |
| [`FS-GAP-003`](#fs-gap-003--binding-equivalence-is-test-enforced-not-registration-enforced) | §8.3 | Equivalence demonstrated but not a registration precondition; the 5 frozen bindings are unregistered | **Medium** | **None — actionable today** | **Open — unowned** |
| [`FS-GAP-004`](#fs-gap-004--registration-emits-no-governance-receipt) | §14.2 FG1 | Registration emits no governance receipt; `feature_store.db` and `ledger.db` are disconnected | **Medium** | **None — actionable today** | **Open — unowned** |
| [`FS-GAP-005`](#fs-gap-005--no-real-corpus_version-and-no-value-set-provenance-closure) | §7.2, §15 | No real `corpus_version` producer; value-set provenance closure unrealised | **Medium** | Phase 1 (WBS 1.3) + Phase 4 | **Open — partially owned** |

### 1.2 Technical debt

| ID | Origin | Title | Severity | Owner | Status |
|---|---|---|---|---|---|
| [`FS-TD-001`](#fs-td-001--phase-transitions-are-python-guarded-not-trigger-guarded) | WBS 2.1–2.3 | Phase transitions Python-guarded, not DB-trigger-guarded | Low | Unscheduled | Open — accepted |
| [`FS-TD-002`](#fs-td-002--specification-hashing-uses-plain-json-canonicalisation) | WBS 2.1–2.3 | Plain `sort_keys` JSON hashing, not `receipts.py` decimal canonicalisation | Low | Unscheduled | Open — **downgraded** |
| [`FS-TD-003`](#fs-td-003--input_requirements-are-shape-validated-only) | WBS 2.1–2.3 | `input_requirements` shape-validated only, no floor/lane sub-structure | Low | → `FS-GAP-001` | **Reclassified** |
| [`FS-TD-004`](#fs-td-004--no-citation-index-check-on-retirement) | WBS 2.1–2.3, 2.4 | No citation-index check in `retire_specification()` | Low | **Phase 4** | Open — owned |
| [`FS-TD-005`](#fs-td-005--eh-002s-last-instance-remains-unfixed) | WBS 2.4, 2.5 | `EH-002`'s last instance (`compute_dsr_for_registration.py`) | Low | Hazard Register | Open — dormant |
| [`FS-TD-006`](#fs-td-006--feature_storedb-has-no-backup-wiring) | WBS 2.4, 2.5 | `feature_store.db` has no backup wiring | Low → Medium on trigger | Trigger-conditioned | Open — accepted |
| [`FS-TD-007`](#fs-td-007--six-forward-evidence-test-files-block-the-base-env-full-suite) | WBS 2.5 | 6 `research/forward_evidence/tests/*.py` lack `importorskip("pandas")` | Low | Unscheduled | Open — accepted |
| [`FS-TD-008`](#fs-td-008--warm-up-is-enforced-only-at-the-migration-scripts-resolution-point) | WBS 2.6 | Warm-up enforced only at the migration script's resolution point | Low | **Phase 4** | Open — owned |
| [`FS-TD-009`](#fs-td-009--warn-mode-warning-lines-have-no-durable-capture) | WBS 2.6 | Warn-mode `WARNING:` lines have no durable capture | Low | Unscheduled | Open — **couples to `FS-DEC-001`** |
| [`FS-TD-010`](#fs-td-010--conditioning-refusal-has-no-rollout-flag) | WBS 2.7 | Conditioning refusal has no rollout flag | Low | Deliberate — none | Open — accepted deviation |
| [`FS-TD-011`](#fs-td-011--conditioning-is-not-attached-to-resolvedvalue) | WBS 2.7 | Conditioning not attached to `ResolvedValue` at resolve time | Low | **Phase 4** | Open — owned |
| [`FS-TD-012`](#fs-td-012--narrowing-and-cross-sectional-rules-are-unexercised) | WBS 2.7 | `narrowing` / `cross-sectional` unexercised by any real Specification | None | On first real case | Open — accepted |
| [`FS-TD-013`](#fs-td-013--every-specification-declares-an-empty-sensitivity-variant-set) | Closure Review | All 17 Specifications declare `sensitivity_variants=[]` | **Medium** | Before Phase 4 consumes it | **Open — new** |
| [`FS-TD-014`](#fs-td-014--the-value-store-has-no-tenant) | Closure Review | Value Store implemented but never exercised against real data | Low (by design) | Phase 4 | **Open — new** |

### 1.3 Decisions owed

| ID | Title | Owner | Trigger | Status |
|---|---|---|---|---|
| [`FS-DEC-001`](#fs-dec-001--flip-warm-up-mode-from-warn-to-refuse) | Flip warm-up `mode: warn` → `refuse` | **Rohith** | One full increment elapsed (satisfied) | **Owed — unscheduled** |
| [`FS-DEC-002`](#fs-dec-002--reviewer-sign-off-on-five-phase-2-verification-records) | Reviewer sign-off (RC1–RC8) on five Phase 2 verification records | **Rohith** | Phase 2 closeout | **Owed — blocks formal closure** |

---

## 2. Realisation Gaps — Detail

### `FS-GAP-001` — Admissibility inheritance is not implemented

| Field | Value |
|---|---|
| **`ADR-I010` ref** | §9.2 (Admissibility Inheritance), §12.4 (resolve flowchart, first refusal), §13 Tier C; DG6; IQROS-P4 |
| **Severity** | **High** — this is a constitutional principle (IQROS-P4 Admissibility Lattice), not a convenience |
| **Owning WBS item** | **None.** Phase 2's WBS 2.1–2.7 never claimed it |
| **Absorbs** | `FS-TD-003` |

**What the ADR requires.** *"A value set's admissibility ceiling is the meet over its inputs' ceilings (IQROS-P4). A feature computed on an uncertified span inherits that cap; it does not become admissible by being computed."* §12.4's flowchart makes the admissibility-floor check the **first** refusal point, before warm-up.

**Current state.** Zero references to `research/admissibility/` anywhere in `research/feature_store/` (grep-confirmed at Phase 2 Closeout). Phase 0 built the lattice — `meet()`, `join()`, `satisfies()`, `AdmissionResult` with per-dimension refusal naming — and Phase 2 does not import it. No feature value set carries an admissibility ceiling, because no ceiling is ever computed. `registry.py`'s Tier A validates that `input_requirements` names a `source` and `fields`, but not the *"required admissibility floor, required lane"* that §8.2 also names.

**Why it is not a milestone failure.** WBS 2.1–2.7's item text never mentions admissibility. The roadmap's own §6 Phase 2 table assigns it to nothing. This is a planning gap surfaced at closure, not an execution gap — the same class of finding the roadmap itself had to record at §6 for Phase 1.

**Blast radius if never closed.** A dataset could compose features whose inputs were never certified, and nothing in the Feature Store would object. IQROS-P4's guarantee — that computing confers no admissibility — would be architecturally stated and operationally absent. This is the single highest-consequence open item in this register.

**Nearest natural owner.** Phase 4 WBS 4.4 (refusal points RF1–RF6, of which RF4 is floor enforcement). **This is a placement suggestion, not an assignment** — assigning it is Rohith's call, and doing it at the Feature Store layer rather than only at the dataset boundary would be a legitimate alternative reading of §12.4.

---

### `FS-GAP-002` — Tier D output hygiene is not implemented

| Field | Value |
|---|---|
| **`ADR-I010` ref** | §13 Tier D |
| **Severity** | Medium |
| **Owning WBS item** | **None** |

**What the ADR requires.** *"Tier D — Output hygiene. Validity fraction; degenerate-output detection; warm-up boundary correctness — the first valid index must match the declared warm-up exactly, neither early nor late. These cap admissibility rather than blocking."*

**Current state.** No implementation (grep-confirmed: no validity-fraction computation, no degenerate-output check anywhere in `research/feature_store/`). Tiers A and B are complete in `registry.py`; Tier C is partial (warm-up only, `warmup.py`).

**Why this one is notable.** Unlike the other gaps, Tier D's warm-up-boundary check is **actionable today against real data with no new dependency**. All 17 Specifications declare a `warmup_periods`, and `engine.load_master()` produces the real 1987-row series each is computed from — so *"the first valid index must match the declared warm-up exactly"* is directly checkable for all 17 right now. It would either confirm the declared warm-ups are exactly right or find a real off-by-N in a declaration. **Neither outcome is currently known.**

**Blast radius if never closed.** Warm-up declarations are trusted but unverified against the actual first-valid-index of the computation they describe. A declaration that is too small silently emits partially-warmed values; too large silently discards valid rows. Tier D exists precisely to catch both, and does not run.

---

### `FS-GAP-003` — Binding equivalence is test-enforced, not registration-enforced

| Field | Value |
|---|---|
| **`ADR-I010` ref** | §8.3 (One Authoritative Binding — Resolution of PS1); §11 lifecycle |
| **Severity** | Medium |
| **Owning WBS item** | **None** — WBS 2.5 delivered the demonstration, which is what its item text asked for |

**What the ADR requires.** §8.3's closing sentence states the intent exactly: *"This converts the audited dual-implementation risk from a testing question into a registration precondition."* A second implementation is a *conformant binding* that *"must demonstrate equivalence against the authoritative one as a condition of registration."*

**Current state.** WBS 2.5 demonstrated equivalence for all 5 hash-pinned decision functions — exact array match over the real 1987-row corpus, with a TV3 sabotage proving discriminating power. **But none of the 5 frozen bindings is registered.** `feature_store.db` holds 17 Specifications, all `binding_id = evidence_engine.*` (verified by direct query); zero `decision_function.*` bindings exist. Equivalence is therefore guarded by `tests/test_decision_function_research_equivalence.py` — a test — which is the *"testing question"* §8.3 exists to escape.

**Honest assessment.** The practical protection is real and should not be understated: the tests run in every full-suite invocation, and the frozen files are additionally SHA-256 hash-pinned by `pre_reg_guard.verify_fn_hash()` at forward-collection time. PS1's *risk* is well-controlled. What is unrealised is PS1's *structural* resolution — the registry does not know the second implementations exist, so nothing at the registry level could refuse a future divergent one.

**Blast radius if never closed.** Low today. Rises if the test is ever skipped, quarantined, or if a sixth frozen implementation is added without a corresponding test — at which point the guarantee silently reverts to the pre-`ADR-I010` state.

**Note for Phase 3.** `ADR-I011` §8.3 uses the identical mechanism for fill conventions (*"the same mechanism ADR-I010 §8.3 uses for feature bindings"*). If Phase 3 copies Phase 2's pattern faithfully, it will inherit this gap. See the Phase 3 Handoff Package.

---

### `FS-GAP-004` — Registration emits no governance receipt

| Field | Value |
|---|---|
| **`ADR-I010` ref** | §14.2 FG1; §9 (Specification metadata: *"registration receipt"*); IQROS-P8, P10 |
| **Severity** | Medium |
| **Owning WBS item** | **None** |

**What the ADR requires.** *"FG1 — Registration is a human act and emits a receipt (IQROS-P8, P10)."* §9's metadata table lists *"registration receipt"* among what a Specification carries.

**Current state.** Registration is human-invoked (`migrate_evidence_engine_features.py --commit`, dry-run-gated) ✅ — the "human act" half is satisfied. The receipt half is not: `feature_store.db` has no column, table, or foreign key referencing `ledger.db`'s `governance_receipts`, and no Feature Store code path imports `receipts.py` or `receipt_ledger.py`.

**Why this is squarely actionable.** The receipt infrastructure is complete and audited (Phase 0 / governance PR-1 through PR-8: canonical serialisation, content-addressed hashing, append-only chained ledger, `verify_chain()`, replay). `feature.register` would be a new decision type in `DECISION_TAXONOMY` — precisely the additive pattern PR-4 and PR-6 already established twice. The work is integration, not design.

**Blast radius if never closed.** Feature Specification registrations are outside the governance chain entirely. A dataset citing a Specification hash years from now can resolve *what was registered* but has no receipt establishing *that it was registered, by whom, under which rule version* — the exact reconstruction property the receipt architecture exists to provide.

---

### `FS-GAP-005` — No real `corpus_version`, and no value-set provenance closure

| Field | Value |
|---|---|
| **`ADR-I010` ref** | §7.2 (addressing triple), §15 (Provenance) |
| **Severity** | Medium |
| **Owning WBS item** | **Partially owned** — Phase 1 WBS 1.3 (partition revision identity), Phase 4 (dataset resolution) |

**Current state.** `value_store.py`'s addressing triple `(spec_hash, corpus_version, as_of)` is the primary key of both tiers, but `corpus_version` is an **opaque caller-supplied string** with no producer anywhere in the repository (grep-confirmed). Nothing composes a real corpus version from `ADR-I009` §16's partition revision set. Consequently §7.2's *"the same triple always yields the same numbers, or the platform has a defect"* is structurally sound but **untested against reality**, and §15's eight provenance elements are only partially available (spec hash, definition version, binding + engine version exist; corpus version, `as_of`, admissibility inputs, conditioning statement, and computation record are not persisted on any value set — because no value set exists, see `FS-TD-014`).

**Coupling.** This gap cannot close before Phase 1 WBS 1.6 completes for every ingest path — one path (`research/ingest/calendar.py`, the WI-5 macro event calendar) still carries no conditioning statement and no normalisation-contract version.

---

## 3. Technical Debt — Detail

### `FS-TD-001` — Phase transitions are Python-guarded, not trigger-guarded
**Origin:** WBS 2.1–2.3 · **Severity:** Low · **Owner:** Unscheduled

`_transition_phase()` enforces `_ALLOWED_TRANSITIONS` in Python plus a `WHERE phase = ?` concurrency guard on `UPDATE`. This follows `record.py`'s `state_transitions` precedent rather than `contract_lifecycle.py`'s stricter DB-trigger guard. `feature_definitions` *does* carry append-only triggers; `feature_specifications`' phase column does not.

**Blast radius:** Low — single writer, no concurrent-writer scenario exists. The Python guard correctly refuses stale-phase and concurrent writes (tested). Would matter if the registry ever needed `contract_lifecycle.py`'s tamper-evidence property against a writer that bypasses `registry.py`.

### `FS-TD-002` — Specification hashing uses plain JSON canonicalisation
**Origin:** WBS 2.1–2.3 · **Severity:** Low · **Owner:** Unscheduled · **Status: downgraded at closeout**

`_content_hash()` uses `json.dumps(sort_keys=True)` + SHA-256, not `receipts.py`'s decimal-string canonicalisation. WBS 2.1 flagged a float-instability risk *"worth revisiting once real (WBS 2.4) parameter values are known."*

**They are now known, and the risk did not materialise:** all 17 real Specifications carry integer-only `parameters`. Spec hashes proved stable across three consecutive `--commit` runs and through two live-script sabotage-and-revert cycles (WBS 2.6, 2.7). **Downgraded** — re-open only if a Specification with float parameters is ever registered.

### `FS-TD-003` — `input_requirements` are shape-validated only
**Origin:** WBS 2.1–2.3 · **Status: RECLASSIFIED → [`FS-GAP-001`](#fs-gap-001--admissibility-inheritance-is-not-implemented)**

WBS 2.1 logged this as debt owned by *"WBS 2.4+"*. WBS 2.4 through 2.7 did not close it, and could not have: it is not a validation-depth shortcut, it is the same missing admissibility wiring as `FS-GAP-001`. Reclassified so it is not tracked twice under two severities. **Retained as an ID rather than deleted** — a milestone record cites it.

### `FS-TD-004` — No citation-index check on retirement
**Origin:** WBS 2.1–2.3, 2.4 · **Severity:** Low · **Owner: Phase 4**

`retire_specification()` performs the mechanical `Deprecated`/`Superseded` → `Retired` transition without checking `ADR-I010` §11's *"Retirement requires zero citations."* The citation index is `ADR-I012`'s Dataset Builder. Correctly deferred and documented in the function's own docstring. **Blast radius: none today** — nothing cites a Specification, and nothing retires one.

### `FS-TD-005` — `EH-002`'s last instance remains unfixed
**Origin:** WBS 2.4, 2.5 · **Severity:** Low · **Owner:** [Hazard Register `EH-002`](hazard_register.md#eh-002--bare-import-engine-is-not-collision-safe-across-subsystems)

`research/forward_evidence/compute_dsr_for_registration.py` still uses a bare `import engine`. **Dormant** — a standalone CLI never imported by any test, so the two colliding modules never share a process. **Phase 2 net-reduced this hazard** (3 of 4 instances fixed) and converted a long-standing "intermittent" flake into a closed root cause. Tracked in the Hazard Register per its own §3 governance; not duplicated here.

### `FS-TD-006` — `feature_store.db` has no backup wiring
**Origin:** WBS 2.4, 2.5 · **Severity:** Low today, Medium once triggered · **Owner:** Trigger-conditioned

Fully specified in [`feature_store_backup_strategy.md`](feature_store_backup_strategy.md), including a mechanical §4 implementation plan requiring no design work. **Two trigger conditions now defined**: (§3) a second, non-script caller of `register_definition`/`register_specification`; and (§3.1, added at this closeout) **the first row written to `feature_value_materialized`**, which expires the regenerability argument regardless of who wrote it. Neither has fired.

### `FS-TD-007` — Six forward-evidence test files block the base-env full suite
**Origin:** WBS 2.5 · **Severity:** Low · **Owner:** Unscheduled

Six files under `research/forward_evidence/tests/` hard-import `pandas` with no `importorskip` guard, so the true repo-wide suite cannot run in the base env. **Consequence, precisely:** the base env's "full suite" (`pytest tests/ --ignore=tests/test_api.py`, 1525 passed) and the `btc_bot` env's true repo-wide suite (`pytest --ignore=tests/test_api.py`, 1881 passed) are **not the same command** — now documented rather than silently assumed. Both remain valid regression signals. Fixing 6 unrelated files was correctly judged out of scope.

### `FS-TD-008` — Warm-up is enforced only at the migration script's resolution point
**Origin:** WBS 2.6 · **Severity:** Low · **Owner: Phase 4**

`check_warmup()` is called from `migrate_evidence_engine_features.py`'s Tier-B-equivalence-shaped resolution, using `len(engine.load_master())` as available history — not from a general `resolve()` with `as_of`-scoped history. This is honest, not a shortcut: it is the **only** real resolve()-shaped consumer of Feature Specifications in the codebase. `value_store.py`'s own WBS 2.2 docstring had already scoped this to a future consumer.

### `FS-TD-009` — Warn-mode `WARNING:` lines have no durable capture
**Origin:** WBS 2.6 · **Severity:** Low · **Owner:** Unscheduled · **Couples to [`FS-DEC-001`](#fs-dec-001--flip-warm-up-mode-from-warn-to-refuse)**

Warn-mode warnings print to stdout only. **This is not merely cosmetic:** `warmup_contract.md` §5 makes *"a review of any `WARNING:` lines actually logged during that period"* a **precondition for flipping to `refuse` mode** — and there is currently no durable place to review them from. Zero warnings are produced against the real corpus today, so nothing has been lost; but the flip decision's own evidence requirement is unsatisfiable as written until either this closes or the precondition is restated.

### `FS-TD-010` — Conditioning refusal has no rollout flag
**Origin:** WBS 2.7 · **Severity:** Low · **Owner:** Deliberate — none · **Status: accepted deviation**

Roadmap §14 states *"every new enforcement mechanism must be disableable by configuration."* Conditioning refusal has no lever. **The reasoning is sound** — refusal fires only on a genuinely unresolvable declared source, a malformed-input condition with no legitimate reason to admit, per `ADR-I010` §9.1's unconditional wording. Recorded here as a **knowing deviation from a stated blanket rule**, so it is visible rather than buried.

### `FS-TD-011` — Conditioning is not attached to `ResolvedValue`
**Origin:** WBS 2.7 · **Severity:** Low · **Owner: Phase 4**

Propagation is computed at migration time and reported, but `value_store.ResolvedValue` was deliberately **not** extended with a `conditioning` field — that would have been an unauthorised API/schema change outside WBS 2.7's scope. `ADR-I010` §9's requirement that a *value set* carries a conditioning statement is therefore satisfied in computation but not in the served object.

### `FS-TD-012` — `narrowing` and `cross-sectional` rules are unexercised
**Origin:** WBS 2.7 · **Severity:** None · **Owner:** On first real case

Both are implemented and unit-tested against synthetic inputs; all 17 real features are genuinely `preserving`. Inventing a narrowing or cross-sectional example to "exercise" them would have been fabrication. **Correct as-is** — recorded so the limitation is not mistaken for coverage.

### `FS-TD-013` — Every Specification declares an empty sensitivity-variant set
**Origin:** Phase 2 Closure Review (new) · **Severity:** **Medium** · **Owner:** Before Phase 4 consumes the field

All 17 Specifications declare `sensitivity_variants=[]`. The field is correctly mandatory and non-omittable (Tier A refuses omission; an empty list is valid) — but the consequence is that two `ADR-I010` guarantees are **vacuous for every real Specification**:

- §13 Tier B — *"The binding reproduces the authoritative implementation across the declared sensitivity variants"* — is satisfied over an empty set.
- §14.2 FG7 — *"Sensitivity variants are declared at registration, not chosen at evaluation time — preventing post-hoc selection of a favourable robustness set"* — protects an empty robustness surface.

**Not a defect:** WBS 2.4 never claimed to author variants, and the audited `research/evidence/signals.py` already carries its own sensitivity machinery outside the Feature Store. **The risk is inheritance** — Phase 4's robustness assessment reading this field and treating `[]` as a considered declaration rather than an unpopulated one.

### `FS-TD-014` — The Value Store has no tenant
**Origin:** Phase 2 Closure Review (new) · **Severity:** Low (by design) · **Owner: Phase 4**

`feature_value_cache` and `feature_value_materialized` **do not exist as tables** in the real `feature_store.db` — `value_store.init_db()` has never been called against it. WBS 2.2 is proven entirely by test.

**This is the ADR's intended steady state,** not a shortfall: §12.1 makes compute-on-read the default requiring no justification and materialisation the exception requiring one, and no citing artifact exists yet to require it. Two consequences are recorded rather than assumed: **PS5 (exact-bytes recoverability) is unproven end-to-end**, and the divergence machinery (§7.3) — genuinely well-built, `DivergenceEvent` on both re-materialisation conflict and stored-hash mismatch — has never fired against real data.

---

## 4. Decisions Owed — Detail

### `FS-DEC-001` — Flip warm-up `mode` from `warn` to `refuse`
| Field | Value |
|---|---|
| **Owner** | **Rohith** — explicitly reserved as a governed act by `warmup_contract.md` §5 |
| **Status** | **Owed, unscheduled** |
| **Rollback** | Edit one key in `research/feature_store/warmup_policy.yaml`. No code change. Exercised and verified |

`warmup_contract.md` §5 names three flip conditions: (1) one full increment run in `warn` mode — **satisfied** by WBS 2.6→2.7; (2) a review of warnings actually logged — **currently unsatisfiable as written**, see `FS-TD-009`; (3) explicit authorisation — **owed**.

**Why this is tracked as a decision rather than left ambient.** `RR-1` of the Closure Review: if the flip never happens, the staged rollout becomes a permanent no-op and warm-up silently proceeds exactly as it did pre-WBS-2.6 — the precise silent degradation `ADR-I010` PS4 exists to eliminate. Zero features are warm-up-deficient today, so the cost of deciding either way is currently zero. **That is an argument for deciding now, not for deferring.**

### `FS-DEC-002` — Reviewer sign-off on five Phase 2 verification records
| Field | Value |
|---|---|
| **Owner** | **Rohith** — cannot be delegated or self-granted |
| **Status** | **Owed — blocks formal Phase 2 closure** |

All five Phase 2 verification records carry `*Pending — Rohith*` with RC1–RC8 unchecked and Reviewer/Date blank. The Verification Standard's §12 pass criterion and `D11` are therefore unsatisfied for every Phase 2 milestone.

The Closure Review discharged RC4 and RC5 independently (two tests read in full; both suite counts reproduced from fresh runs) and RC1/RC2/RC6/RC7 by inspection. **The remaining act is the sign-off itself** — §12's purpose is that *"the entity that built a thing does not solely certify it."*

---

## 5. Adding to This Register

1. Allocate the next identifier in the appropriate class (`FS-TD-`, `FS-GAP-`, `FS-DEC-`), sequential, **never reused** even if an entry is later closed.
2. Add a summary row in §1 and a full entry in §2/§3/§4.
3. State, for every entry: what it is, why it was deferred, who or which WBS item owns it, and the blast radius if it is never closed (Verification Standard §10, TD2).
4. **Do not remediate an entry as part of registering it** unless explicitly authorised.
5. An entry moves to `Closed` only via separately-scoped, separately-approved work — never silently, and never as an incidental side effect of an unrelated milestone (`hazard_register.md` §3's rule, adopted here verbatim).

## 6. Governance of This Register

Same model as the [Engineering Verification Standard](verification/engineering_verification_standard.md) §13 and the [Hazard Register](hazard_register.md) §3: an engineering process document, not part of the IQROS constitutional series, amendable by Rohith or explicit instruction, no constitutional session required.

**One scope note.** `FS-GAP-001`–`005` describe `ADR-I010` requirements that are unimplemented. Recording them here is **not** an amendment to `ADR-I010`, a re-scoping of the roadmap, or an authorisation to build them. It is the register doing its stated job: making sure work that no milestone owns does not disappear because every milestone truthfully reported itself complete.
