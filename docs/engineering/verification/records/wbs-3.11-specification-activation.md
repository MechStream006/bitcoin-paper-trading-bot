# Milestone Verification Record

| Field | Value |
|---|---|
| **Milestone** | Label Specification Activation via Behavioural Equivalence — `research/label_store/activation.py` |
| **WBS item(s)** | 3.11 |
| **Authorisation** | ⚠️ **Owner-authorised, NOT roadmap-ratified** — see §0.1 |
| **Roadmap phase** | Phase 3 — Label Store (`phase_3_label_store_roadmap.md` v1.1.0) |
| **Standard version referenced** | 1.0.0 |
| **Date** | 2026-08-06 |
| **Implementer** | Claude Opus 5 |
| **Reviewer** | Rohith Suresh — signed 2026-08-07 (`LS-DEC-003`) |
| **Baseline** | `phase-3-baseline-v1.0.0` = `aae322e77802cc76c7bb70688f87c433518281a3` |
| **Closes** | `LS-GAP-002` (partially — 4 of 8; see §8) · `ADR-I011` §12.1, DI4 |

---

## 0. Authorisation Status and Design Summary (read before the checklist)

### 0.1 This milestone is owner-authorised, not roadmap-ratified

**Recorded explicitly, as instructed, rather than implied.**

`phase_3_label_store_roadmap.md` v1.1.0 **does not contain a WBS 3.11.** A repository-wide search returns zero matches for "3.11", and the roadmap's own changelog fixes its item set: *"WBS numbering 3.1–3.9 preserved; 3.8 split into 3.8a/3.8b as approved; 3.10 added for backup wiring."*

WBS 3.11 is defined only in [`phase-4-readiness-resolution.md`](phase-4-readiness-resolution.md) §4, as proposal **`LS-DEC-008`**. ✅ **Ratified 2026-08-07** — WBS 3.11 is now formally Phase 3 work. `LS-DEC-005` (behavioural equivalence replacing `P3-1`'s "shared-code-path test"), `LS-DEC-007` (WBS 3.8c) and `LS-DEC-009` (deferral of C3) were ratified in the same review; see the [Phase 3 Closure Approval Package](phase-3-closure-approval-package.md). *At the time this milestone was executed all four were unratified, and it proceeded on owner authorisation alone — recorded here unchanged.*

**This milestone proceeded on Rohith's explicit in-session authorisation of 2026-08-06**, which also directed that the authorisation basis be recorded here. The roadmap was **not** modified, no ADR was modified, and no claim of roadmap backing is made anywhere in this record or in the shipped code.

**Entry criterion satisfied.** WBS 3.11's own stated dependency — *"`B-1` discharged; WBS 3.8c merged"* — is met: Phases 0–3 were committed as 9 commits, tagged `phase-3-baseline-v1.0.0`, and pushed to `origin/main` (verified `0 0` divergence) before this milestone began.

### 0.2 The two design decisions that carry the milestone

**(a) The safety inversion.** `registry.activate_specification()` is fail-closed *and irreversible*: a check returning `False` (or raising) transitions the Specification `Registered → Rejected`, a permanent record (LG4), with **no arc back**. Re-registering byte-identical content returns the *same* content hash and leaves the row `Rejected` forever — verified empirically at [Phase 4 Readiness Resolution](phase-4-readiness-resolution.md) finding `E-2`.

A naive implementation passes the live equivalence check straight to the registry; a failing Specification is then **consumed permanently**. This module inverts that: `apply()` pre-screens on the plan's verdict, re-computes the report immediately before each activation, and **refuses to call `activate_specification()` at all** unless it passes — raising `ActivationError` instead. A failing Specification is skipped and reported, never consumed. The registry's own rejection path remains intact for any other caller; this module simply never reaches it.

**(b) Non-vacuous by construction.** A Specification with zero declared values would make any comparison pass trivially (`0 == 0`) and activate a binding that was never demonstrated. `equivalence_report()` requires a non-empty comparable population; an empty one is `ok=False` with a stated reason. This is what defers 4 of the 8 Specifications.

### 0.3 Why 4 activated and 4 deferred

Label Specifications carry `binding_id` as **declared identity only** — `get_binding()` resolves none of them (`LS-GAP-003`, inherited from `FS-GAP-003` per scope transfer `S-4`). There is therefore no executable label binding to invoke. The only available form of equivalence is **comparing materialised Label Store values against the source system's own values**, which requires declared values to exist.

| Specification | h | Values | Binding | Outcome |
|---|---|---|---|---|
| `ff680d93f01d` | 5 | 37 | `forward_evidence.daily_job.trade_labels` | ✅ **Active** — 37/37 identical |
| `0ecb0802dab4` | 7 | 37 | `forward_evidence.daily_job.trade_labels` | ✅ **Active** — 37/37 identical |
| `0c00e1091428` | 14 | 37 | `forward_evidence.daily_job.trade_labels` | ✅ **Active** — 37/37 identical |
| `4e1f2b736e9c` | 30 | 37 | `forward_evidence.daily_job.trade_labels` | ✅ **Active** — 37/37 identical |
| `e785df371f90` | 1 | 0 | `research_evidence.engine.ret_fwd1` | ⏸ Deferred — no equivalence source; no declared values |
| `0db88b4fc0dd` | 1 | 0 | `research_evidence.engine.net_daily` | ⏸ Deferred — same |
| `89869c534200` | 7 | 0 | `forward_evidence.daily_job.close_trade` | ⏸ Deferred — same |
| `57ce016e51a0` | 7 | 0 | `forward_evidence.runner_base.trade_returns_view` | ⏸ **Deferred by name, citing `A-4`** — `evidence_forward.db` is entirely empty locally; production state is not observable from this machine |

**Deferring is explicitly sanctioned** by WBS 3.11's own exit criterion (*"activated or explicitly deferred with a stated reason; `57ce016e51a0` deferred by name, citing `A-4`"*). Given irreversibility, activating a Specification whose binding cannot be demonstrated would assert a property that was never established — the opposite of what `ADR-I011` §12.1 gates activation on.

### 0.4 LR1 is preserved structurally, not by convention

`daily_job.py` is not imported, not called, and not modified. H6 is opened through a **`file:...?mode=ro` URI**, so read-only is enforced by SQLite itself; a test proves an `UPDATE` on that connection raises `readonly`. Equivalence is two *independent* reads of the same fact, compared NaN-safely — the same discipline `migrate_evidence_engine_features.py::_equivalence_check()` established at Phase 2, and strictly stronger than proving the same function ran.

## 1. Build Verification (BV)

- [x] **BV1** — `python -m py_compile research/label_store/activation.py tests/test_label_store_activation.py` → clean in both environments.
- [x] **BV2** — Pure stdlib (argparse/importlib/json/shutil/sqlite3/datetime/pathlib) plus `registry.py`. No pandas; imports and runs identically in the base env.
- [x] **BV3** — No new dependency.
- [x] **BV4** — Verified in both environments; identical pass counts (34/34).
- [x] **BV5** — Collision-safe sibling loading (`sys.modules` checked first), matching every other Label Store module. A structural test asserts the **only** sibling loaded is `registry.py` — activation touches no value-store or receipt code path.

## 2. Test Verification (TV)

- [x] **TV1** — `tests/test_label_store_activation.py` (new, **34 tests**): EquivalenceReport (9), **SafetyInversion (4)**, PlanAndApply (3), DryRun (4), SchemaAssertionAndH6ReadOnly (5), NoUnrelatedMutation (2), RealLabelStore (7).
- [x] **TV2** — House style confirmed (guarded `_load()`, `class TestXxx:`, plain `assert`), with the `_guard_real_h6_untouched` autouse hash fixture proving H6 is byte-identical after every test in the file.
  **Two test-construction errors of my own were found and fixed, neither a module defect, and the first is a positive result:** (a) simulating a corrupt outcome by `UPDATE`-ing a matured row was **blocked by WBS 3.2's fill-once trigger** — storage-layer immutability working exactly as designed; the test now `INSERT`s the malformed row instead, which is the only way that state could actually arise; (b) an over-strict assertion banned the literal string `daily_job` from the module, but it legitimately appears inside the Specification's own declared `binding_id` and in the docstring stating it is *not* imported. The corrected assertions ban imports and module loads, and require every occurrence of the name to be inside a quoted string.
- [x] **TV3** — **Two sabotages, both empirically confirmed, both reverted.**
  1. **The safety inversion.** Replaced `apply()`'s pre-screen and re-verify with the naive implementation — handing the live check straight to `activate_specification()`. Result: a failing Specification was driven to **`Rejected`** (`SpecificationRejected: binding equivalence check returned False`), permanently. **2 tests failed**, including `test_no_specification_is_ever_rejected_by_this_module`, the mixed-population case that is the only one able to catch this (`main()`'s own `ok` filter masks it when *every* Specification fails — an informative detail, since it shows exactly which test carries the property).
  2. **The non-vacuous guard.** Removed the empty-population check. Result: `1 Specification(s) would be activated; 0 deferred` → **a Specification with zero declared values was activated** on a vacuous pass. **2 tests failed.**
  Both reverted; a grep confirms no sabotage marker remains; full file re-confirmed 34/34.
- [x] **TV4** — Refusal paths, one negative test each: mismatched value; subject absent from H6; wrong horizon (a 5-day Specification is *not* satisfied by 7-day H6 values); `label_available = 0` rows excluded; empty population; unregistered binding; missing H6 database; outcome-shape change (`ActivationError`, never a silent `None` comparison); non-Label-Store database (`LR4`); missing `--db`.
- [x] **TV5** — Property/invariant tests: **no Specification is ever Rejected by this module** (the central property, tested on a mixed ok/failing population); **TOCTOU refusal** — if equivalence regresses between plan and apply, `apply()` raises rather than letting the registry Reject; **H6 read-only enforced by SQLite** (`UPDATE` raises `readonly`) and **byte-identical after a full commit**; **only the `phase` column changes** — every other column, including `spec_hash`, is compared before/after and proven identical, so activation is not a content change (§11.2); **values, definitions and receipts untouched**; **idempotency** (second `--commit` is a byte-identical no-op); **LR1 structural guards** (no `daily_job` import/load; only `registry.py` loaded).

## 3. Regression Verification (REG)

- [x] **REG1** — `btc_bot`, `pytest tests/ research/ --ignore=tests/test_api.py`: before **2255 passed / 19 skipped / 0 failed**; after **2289 / 19 / 0** — delta **+34**, exactly this file's test count.
- [x] **REG2** — base env, `pytest tests/ --ignore=tests/test_api.py`: before **1876 / 60 / 0**; after **1910 / 60 / 0** — delta **+34**. Zero skips contributed in either environment.
- [x] **REG3** — No pre-existing flake recurred; `INC-0003`'s engine-collision failure did not appear.
- [x] **REG4** — CI workflow unchanged.
- [x] **REG5** — **One pre-existing test required updating — the intended consequence of this milestone, not a regression.** `tests/test_migrate_bootstrap_label_receipts.py::test_real_specification_phases_are_untouched_by_this_milestone` asserted `phases == {"Registered"}` on the real database. WBS 3.8c genuinely does not activate anything — that is proven directly by its own synthetic test `test_no_specification_phase_is_changed` and its AST guard `test_module_never_references_activation`, both untouched and still passing. The failing assertion read *global* state that WBS 3.11 legitimately changed. It now asserts the invariant 3.8c actually preserves on real data: **no Specification is ever `Rejected`**, and phases are drawn from `{Registered, Active}`. 11 insertions, 2 deletions; nothing else in that file touched.

## 4. Performance Verification (PERF)

- [x] **PERF1** — **N/A, explicitly.** A one-time migration comparing 148 values and writing 4 phase transitions. Real `--commit` including backup and both integrity checks completed in under a second. No hot path, no runtime consumer.

## 5. Database Verification (DBV)

- [x] **DBV1** — No schema change of any kind. One column (`phase`) updated on 4 rows; no table, index, trigger, or column added or altered.
- [x] **DBV2** — Dry run is the default **and is the point**: because activation is irreversible, the dry run *runs every equivalence check* and prints each verdict without transitioning anything. Proven byte-safe against **both** real databases before any commit — `label_store.db` and `forward_evidence.db` SHA-256 identical before and after.
- [x] **DBV3** — **Full rehearsal against a disposable copy of the real database**, as WBS 3.11's own mandatory safety gate requires: 4 activated / 4 deferred; receipts 8 → 8; forward rows 148 → 148; `integrity_check` ok; second `--commit` a byte-identical no-op with exactly 1 backup; `verify_chain` ok/8; `validate_label_governance` CLEAN. Both real files confirmed unchanged throughout.
- [x] **DBV4** — **Real execution.** `PRAGMA integrity_check` = `ok` before **and** after. Pre-counts `(specs, defs, forward, receipts) = (8, 2, 148, 8)`; post-counts **identical**. Phases `Registered × 8` → `Active × 4, Registered × 4`. Backup taken automatically: `label_store.pre-activation.20260806T065730Z.db` (gitignored by the C1 pattern — verified with `git check-ignore`).
- [x] **DBV5** — Idempotency proven on the real post-state and on copies: a re-run reports `0 Specification(s) would be activated` and writes nothing.
- [x] **DBV6** — Cross-store integrity after activation: `verify_chain` **ok** on all three — `label_store.db` (8), `feature_store.db` (17), `ledger.db` (26). `validate_label_governance()` **CLEAN**. `report.py --check` **OK**, 35 families unchanged. H6 `forward_evidence.db` byte-identical.
- [x] **DBV7** — **Activation state transitions verified**: every transition was `Registered → Active`, the only arc this milestone uses, and one of the six in `registry._ALLOWED_TRANSITIONS`. **Zero `Registered → Rejected` transitions occurred**, on real data or in any test outside the deliberate TV3 sabotage.

## 6. Backward Compatibility (BC)

- [x] **BC1** — No existing function signature, table, or column renamed, removed, or changed in return type.
- [x] **BC2** — `registry.py` **unmodified** — `activate_specification()` is consumed exactly as WBS 3.1 shipped it, never altered.
- [x] **BC3** — No other Label Store or Feature Store module modified. `receipts.py` unmodified; no taxonomy change (activation is not in `DECISION_TAXONOMY`, and `ADR-I011` LG1/§13.1 name registration, adjudication and promotion as the receipt-emitting human acts — not activation).
- [x] **BC4** — Content addressing preserved: `spec_hash` is byte-identical before and after activation, asserted by `test_only_the_phase_column_changes`. Activation is a lifecycle transition, not a content change (§11.2).
- [x] **BC5** — Repo-wide grep for `activation`, `equivalence_report`, `BINDING_H6_TRADE_LABELS`: referenced only by the new module, its own test file, and this record. **No production module outside `research/label_store/` references it.**
- [x] **BC6** — Diff against `phase-3-baseline-v1.0.0`: **3 files** — 2 new, 1 modified (+11/−2). No unrelated file changed.

## 7. Rollback Verification (RBV)

- [x] **RBV1** — **Code lever:** revert the commit. The module has no runtime consumer; its absence changes nothing.
- [x] **RBV2** — **Data lever:** restore `label_store.pre-activation.20260806T065730Z.db`, taken automatically before the write and confirmed present. This is the *only* data lever, because `Active → Registered` is not an allowed arc — activation is irreversible by design (`ADR-I011` §12.1).
- [x] **RBV3** — **Repository lever:** `phase-3-baseline-v1.0.0` (`aae322e`) is committed, tagged and pushed, so the pre-3.11 code state is recoverable independently of the working tree — the durability layer `B-1` existed to provide.
- [x] **RBV4** — No configuration flag, deliberately: a switch that disables an equivalence gate is a switch that will eventually be left off, and roadmap §10 already refuses that pattern for the storage-layer mechanisms. The lever is a human act with a verified backup.

## 8. Technical Debt Assessment (TD)

| What | Why deferred | Closed by (WBS ID) | Blast radius if never closed |
|---|---|---|---|
| **4 of 8 Specifications remain `Registered`** — `LS-GAP-002` is closed only for the H6 trade-label family | Their bindings have no independently-readable counterpart with declared values, so equivalence cannot be demonstrated; activating them would assert an unestablished property, irreversibly | Not scheduled. `89869c53` (C1) and the two `engine.*` bindings could be activated by a future milestone that first defines and demonstrates their equivalence sources; `57ce016e` (C3) needs host data (`A-4`) | **None for Phase 4's stated exit criterion** — the 4 Active Specifications carry all 148 real values, so they are the only ones a dataset could meaningfully cite. A future dataset needing C1/C2/C3 would be refused by DI4, loudly and correctly |
| **No equivalence source exists for `research_evidence.engine.*` or `runner_base.*` bindings** | Building them means executing real pipeline code against real corpus data — a materially larger scope than "activate what is eligible", and explicitly outside this milestone | Whichever milestone first needs one of those Specifications Active | Low — `equivalence_report()` refuses by name (`no equivalence source is registered for binding …`) rather than guessing, per Ph6 |
| **Activation emits no governance receipt** | `ADR-I011` LG1 and §13.1 name registration, adjudication and promotion as the receipt-emitting human acts; activation is not among them, and `DECISION_TAXONOMY` reserves no `label.activate` type. Adding one would be an architecture change outside this milestone's authority | Would require an `ADR-I011`/`ADR-I015` decision, not an engineering one | Low today — the transition is recorded in `label_specifications.phase`, and the Specification's own `label.register` receipt (WBS 3.8c) is intact and chain-valid |
| **`LS-GAP-003` (bindings are declared identity only, not resolvable) is untouched** | Deliberately inherited via scope transfer `S-4`; neither `LR8` re-open trigger has fired | `FS-GAP-003` | Unchanged. Note this milestone *demonstrates* the consequence: with no resolvable binding, equivalence must compare stored values, which is why unpopulated Specifications cannot be activated |

- [x] **TD3** — No placeholder work. Every equivalence check ran against real data; the activation executed for real after a full rehearsal.
- [x] **TD4** — None is urgent or unscoped debt. The first is a deliberate, honest consequence of irreversibility; the rest are named ADR/scope boundaries. Not promoted to `CLAUDE.md` §19.

## 9. Definition of Done (DOD)

- [x] **D1** — Compiles/imports cleanly in both environments.
- [x] **D2** — Full suite passes, zero new failures: 2289/19/0 (`btc_bot`), 1910/60/0 (base).
- [x] **D3** — 34 new tests, all passing in both environments.
- [x] **D4** — N/A explicitly stated (§4 PERF).
- [x] **D5** — No new import from `research/` into any operational-plane module.
- [x] **D6** — Rollback levers named (§7): code, data, and repository.
- [x] **D7** — No existing signature, table, or column renamed, removed, or changed in return type.
- [x] **D8** — No `.env`, credential, or secret touched, referenced, or required.
- [x] **D9** — Independently deployable; merging alone changes no existing behaviour.
- [x] **D10** — No new mandatory Specification/Value field introduced.
- [x] **D11** — All sections completed, including explicit N/A statements and the authorisation-basis disclosure (§0.1).
- [x] **L-D1** — Immutability claims proven at the storage layer, adversarially: TV2's finding that WBS 3.2's fill-once trigger **refused** a direct `UPDATE` of a matured row is the property holding under an actual attempt, not an assertion about it.
- [x] **L-D2** — N/A: no production host involved. All real execution was local and is transcribed in §5.
- [x] **L-D3** — N/A: no standing assigned or altered; all 148 forward labels remain `Context` (verified post-activation).

## 10. Reviewer Checklist (RC)

- [x] **RC1** — Milestone corresponds to WBS 3.11 exactly, no scope creep into Phase 4
- [x] **RC2** — No file under `docs/architecture/` touched; roadmap unmodified
- [x] **RC3** — **The owner-authorised (not roadmap-ratified) basis in §0.1 is accepted**
- [x] **RC4** — The 4/4 activate/defer split and its stated reasons are accepted
- [x] **RC5** — Deferral of `57ce016e51a0` (C3) citing `A-4` is accepted
- [x] **RC6** — The pre-existing test update in §3 REG5 is accepted
- [x] **RC7** — Real `label_store.db`'s 4 `Active` Specifications independently spot-checked
- [x] **RC8** — Pre-activation backup confirmed present and readable
- [x] **RC9** — Confirmed no Specification is in `Rejected` phase
- [x] **RC10** — Sign-off

## Files Changed

| File | Change |
|---|---|
| `research/label_store/activation.py` | **New** — behavioural-equivalence activation |
| `tests/test_label_store_activation.py` | **New** — 34 tests |
| `tests/test_migrate_bootstrap_label_receipts.py` | 1 assertion updated (§3 REG5), +11/−2 |
| `research/label_store/label_store.db` | **Real data**: 4 rows' `phase` column `Registered → Active`. All other columns, all other tables byte-verified unchanged |
| `research/label_store/label_store.pre-activation.20260806T065730Z.db` | **New** — automatic pre-write backup (gitignored) |

**Unchanged and verified so:** `registry.py`, `value_store.py`, and every other Label Store and Feature Store module; `receipts.py`; `receipt_ledger.py`; H6's `forward_evidence.db` (byte-identical).

## Summary

Shipped WBS 3.11 on Rohith's explicit authorisation, recorded here as **owner-authorised rather than roadmap-ratified** — the roadmap contains no WBS 3.11, and no claim of roadmap backing is made. Four Label Specifications were activated `Registered → Active`, each through a behavioural-equivalence check that independently re-reads H6's own `trade_labels.forward_return` from a structurally read-only SQLite connection and compares it NaN-safely against what the Label Store materialised at WBS 3.4: **148/148 values identical, zero mismatches**. This resolves `LS-GAP-002` for the only Specifications carrying real values, so `ADR-I011` DI4 no longer blocks a future dataset from citing the Label Store. `LR1` is preserved structurally rather than by convention — `daily_job.py` is never imported or called, H6 is opened `mode=ro`, and a test proves an `UPDATE` on that connection raises `readonly`. The milestone's defining design decision is the **safety inversion**: because `activate_specification()` is fail-closed *and* irreversible — a failing check permanently `Rejects` a Specification with no arc back — this module never hands a failing check to the registry, pre-screening and re-verifying instead, and raising rather than activating if equivalence regresses between plan and apply. TV3 confirmed both that property and the non-vacuous guard by breaking them: the first sabotage drove a Specification to permanent `Rejected`, and the second activated a Specification with zero declared values on a vacuous `0 == 0` pass. Four Specifications were deliberately **deferred by name with stated reasons** — three have no independently-readable equivalence source, and C3's `evidence_forward.db` is entirely empty locally, exactly the `A-4` exposure the roadmap predicted — because activating an undemonstrated binding irreversibly would assert a property that was never established. Two test-construction errors of my own were found and corrected, the first of them a positive result: WBS 3.2's fill-once trigger *refused* my attempt to corrupt a matured row, which is storage-layer immutability holding under a real attempt. One pre-existing WBS 3.8c assertion was updated to reflect the intended new state. The activation was rehearsed end-to-end against a disposable copy before execution, with `integrity_check` ok on both sides, all row counts byte-verified identical, chains valid across all three governance databases, and `validate_label_governance()` CLEAN. **Phase 4 not begun**, per instruction.
