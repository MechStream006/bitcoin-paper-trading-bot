# Milestone Verification Record

| Field | Value |
|---|---|
| **Milestone** | Label Governance Receipts — `research/label_store/label_receipts.py` |
| **WBS item(s)** | 3.8a |
| **Roadmap phase** | Phase 3 — Label Store (`phase_3_label_store_roadmap.md` v1.1.0) |
| **Standard version referenced** | 1.0.0 |
| **Date** | 2026-08-06 |
| **Implementer** | Claude Sonnet 5 |
| **Reviewer** | *(pending — Rohith)* |

**WBS 3.4–3.7 semantics are unchanged, as instructed.** `migrate_h6_forward_labels.py`, `standing.py`, `superseded.py`, `propagation.py`, `conditioning.py`, `lattice.py`, `composition.py`, and both stores' `registry.py`/`value_store.py` are all untouched (`git diff --stat` confirms zero diff to every one). **`research/evidence_library/receipts.py` gains one small, purely additive extension** (three new frozenset/dict entries — `"label"` in `SUBJECT_KINDS`, `"register"`/`"adjudicate"` in `ACTIONS`, `"label.register"`/`"label.adjudicate"` in `DECISION_TAXONOMY`) — the same, already-precedented extension mechanism used at PR-7 to add `"evaluate"`. No existing receipt substrate function (`receipt_ledger.py`, `evidence_governance.py`, `transition_governance.py`, `contract_receipts.py`) was modified. **Reuses the existing governance receipt infrastructure, per instruction** — see §0 below.

**No architectural inconsistency with ADR-I011 was found.** One narrow, pre-existing implementation gap in `registry.py` (unrelated to ADR-I011's own design) was discovered and is logged as technical debt, not fixed (§8, §0).

**WBS 3.8b is explicitly not begun.**

---

## 0. Design Summary (read before the checklist)

- **`label.register`** wraps the existing, unmodified `registry.register_specification()`. **Derived** — Tier A completeness (§12) is a deterministic rule, replayed via a `RegistrationRule` Protocol + `(rule_id, engine_version)`-keyed registry, mirroring `evidence_governance.py`'s `AdmissibilityRule`/`CandidateGateRule`/`get_rule()` shape exactly (the shape that exists because a first version of *that* module shipped without it and a post-review audit found the defect). `TierARule` (the default rule) calls the pre-existing, unmodified `registry._validate_tier_a()` directly — a private-but-precedented cross-module reach, mirroring `transition_governance.py`'s own reach into `evidence_governance._decimalize`/`_REQUIRED_RECEIPT_TYPE_FOR_STATE` — rather than duplicating Tier A logic a second time (§7.3's "two enforcement points must never diverge").
- **`label.adjudicate`** wraps the existing, unmodified `value_store.adjudicate_forward_label()`. **Discretionary** — adjudicating a Flagged label is a human act (LG1, IQROS-P8); replay verifies the envelope only, mirroring `transition_governance.promote_with_receipt()`'s "a computation vs. a judgment" distinction. A non-human actor is refused.
- **Non-atomic by design**, unlike `transition_governance.transition_with_receipt()`'s locked, single-transaction write: no concurrent-writer race is documented anywhere for the Label Store (unlike PR-8's real, fixed TOCTOU bug on `state_transitions`), so introducing that locking machinery here would itself be the "architectural redesign" this milestone is instructed to avoid. The resulting gap (a write with no receipt, e.g. a caller bypassing this module) is **detected**, not prevented, by `validate_label_governance()` (`LREC1`/`LREC2`), mirroring `evidence_governance.validate_evidence_governance()` (EG1/EG2) and `transition_governance.validate_transition_governance()` (TG1-TG6)'s identical accepted precedent.
- **Zero changes to `registry.py`/`value_store.py`.** Both are called through their existing public functions exactly as built at WBS 3.1/3.2.

## 1. Build Verification (BV)

- [x] **BV1** — `python -m py_compile research/label_store/label_receipts.py research/evidence_library/receipts.py tests/test_label_store_label_receipts.py` → clean in both environments.
- [x] **BV2** — `label_receipts.py`, `receipts.py`, and `receipt_ledger.py` are all pure stdlib (hashlib/json/sqlite3/dataclasses/datetime/decimal) — **no pandas gating anywhere in this milestone**, unlike WBS 3.6/3.7's real, structural pandas dependencies. Every test runs identically in both environments.
- [x] **BV3** — No new dependency.
- [x] **BV4** — Verified under both environments; identical pass counts (no environment-conditional test classes exist in this file).

## 2. Test Verification (TV)

- [x] **TV1** — `tests/test_label_store_label_receipts.py` (new, 38 tests): Receipts Taxonomy Extension (5), TierARule (4), Register Specification With Receipt (8), Adjudicate Forward Label With Receipt (7), Replay Registration Receipt (5), Replay Adjudication Receipt (2), Validate Label Governance (4), Real Label Store Integration (3, real-file-gated).
- [x] **TV2** — House style confirmed, **with one significant finding corrected before it caused a false full-suite-only failure** (§0 note, detailed in §9 TD): this file's own module-level `reg`/`vs` variables, cached once at collection time via the SAME `sys.modules`-guarded pattern this milestone's own `label_receipts.py` uses internally, could still go stale relative to what `label_receipts.py`'s *runtime* `_load_registry()`/`_load_value_store()` calls resolve to — because several **pre-existing** label-store test files (`test_label_store_registry.py`, `test_label_store_propagation.py`, and others) load `registry.py`/`value_store.py` **without** first checking `sys.modules`, unconditionally re-executing and replacing whichever copy was cached, so the *last-collected* file's copy wins regardless of which file a given test lives in. Reproduced only in the full suite (passed in isolation, in the label-store-only combination, and in a governance-receipts combination — all smaller than the full 1965-test collection); root-caused precisely, then fixed **in this file only** (two `pytest.raises`/`except` clauses now resolve the exception class via `lr._load_registry().SpecificationRejected`/`lr._load_value_store().LabelTransitionError` — the same resolution path the code under test itself uses — rather than this file's own possibly-stale top-level binding). Re-run: **38/38 passed** in both environments, in isolation and inside the full suite.
- [x] **TV3** — Empirically verified: replaced `TierARule.evaluate()`'s real verdict computation (`passed = (not field_errors) and def_check["passed"]`) with `passed = True` (ignoring both checks) — 5 tests failed (`TestTierARule` field/def-exists checks, both rejection-handling tests in `TestRegisterSpecificationWithReceipt`, and — critically — `TestReplayRegistrationReceipt::test_replay_of_a_rejection_also_matches`, the exact test guarding this milestone's own "a derived-path replay re-executes and matches" exit criterion), confirming the tests genuinely exercise the rule rather than being tautological. Restored; full 38-test file re-confirmed green in both environments.
- [x] **TV4** — Refusal/exclusion paths tested: a service actor attempting adjudication is refused (`test_service_actor_is_refused`) and writes **neither** a receipt **nor** a transition (`test_service_actor_refusal_writes_no_receipt_and_no_transition`); a non-Flagged label is refused by the (unmodified) storage-layer guard, not merely by this module's own pre-check (`test_non_flagged_label_is_refused_by_the_storage_layer`); a missing label/specification raises (`test_missing_label_raises`); an unregistered `(rule_id, engine_version)` pair refuses replay rather than substituting the current default (`test_unknown_rule_raises_on_replay`, mirroring `evidence_governance.UnknownRuleError`'s own guarantee); the append-only trigger still blocks in-place tampering through this milestone's own real usage path (`test_append_only_trigger_blocks_in_place_tampering`).
- [x] **TV5** — Property/invariant tests: **idempotent registration** (`test_idempotent_resubmission_returns_the_same_receipt`/`..._does_not_duplicate_the_receipt` — a byte-identical resubmission returns the existing receipt, never a duplicate, mirroring `register_specification()`'s own content-addressed idempotency); **the rule and the storage layer can never silently diverge** (the `passed != outcome_passed` defensive check inside `register_specification_with_receipt()`, exercised implicitly by every passing test and directly provable via TV3's sabotage); **discretionary replay never re-derives the human judgment** (`test_replay_never_re_derives_the_human_judgment`, AST-based — asserts `replay_adjudication_receipt()` never passes a `rule_fn`, mirroring `transition_governance.replay_transition_receipt()`'s identical discipline); **receipts chain correctly across both decision types together** (`test_receipt_is_chained_after_a_prior_registration_receipt`, `verify_chain().ok`); **the roadmap's own exit criterion, exercised literally against real data** (`TestRealLabelStoreIntegration` — `validate_label_governance()` run read-only against a disposable copy of the real local `label_store.db` correctly reports `LREC1` for all 8 real, pre-existing Specifications and zero `LREC2`, with the real file itself and its copy both proven byte-unchanged by the call).

## 3. Regression Verification (REG)

- [x] **REG1** — `btc_bot` env, `python -m pytest tests/ -q --ignore=tests/test_api.py`:
  - Before (after WBS 3.7): **1927 passed, 19 skipped, 0 failed**
  - After (first run): **1 failed** (`tests/test_evidence_lanes.py::TestCurrentRunPerLane::test_selects_latest_within_lane`) **, 1964 passed, 19 skipped** — investigated immediately: this is the exact, previously-documented flake class from `CLAUDE.md` §18 (Increment 2/Increment 4 records) — a timestamp tie in `current_run_per_lane`'s `run_id DESC` tiebreak from two back-to-back `datetime.now()` calls with no forced separation. Confirmed unrelated: passes 30/30 in isolation (`pytest tests/test_evidence_lanes.py -q`); zero changes to `record.py`/`lifecycle.py`/`test_evidence_lanes.py` this milestone.
  - After (second run, clean): **1965 passed, 19 skipped, 0 failed** — the flake did not recur, confirming it is timing-dependent and unrelated to this milestone.
  - Delta: **+38 passed**, exactly matching the new test count.
  - Base env, full suite (`python -m pytest tests/ -q`): **1847 passed, 60 skipped, 0 failed** (before: 1809/60 — delta **+38**, this file contributes zero skips in either environment).
  - Maximal collision-risk combination (all six Label Store test files + both Feature Store test files + `test_conditioning.py` + all four evidence-library governance-receipt test files — every module this milestone's own loaders, or its extension of `receipts.py`, could plausibly interact with): **560/560 passed**.
- [x] **REG2** — Skip count unchanged in both environments (19→19 `btc_bot`, 60→60 base — delta fully attributable to a pre-existing base-env split elsewhere, none from this file).
- [x] **REG3** — One known-class flake observed and non-recurring (above); no other pre-existing flake recurred.
- [x] **REG4** — CI workflow unchanged.

## 4. Performance Verification (PERF)

**N/A** — one or two SQL row reads/writes per receipt call; `validate_label_governance()` iterates real-scale row counts (8 Specifications, 148 Values in the real local data) — negligible.

## 5. Database Verification (DBV)

**Applicability: YES** — this milestone's real execution can write to `governance_receipts` (via `receipt_ledger.append_receipt()`), though the real local `label_store.db` was **not**, in fact, written to (§ below explains why).

- [x] **DBV1** — Additive only: zero schema change to `label_specifications`/`label_value_forward`; `governance_receipts`' schema (append-only, PR-1) is unchanged, exercised as designed.
- [x] **DBV2** — No dry-run/`--commit` CLI exists for this module (it is a library, not a standalone migration script, matching the roadmap's own framing of 3.8a as the emitter, not a backfill run) — every write happens through an explicit function call, never implicitly.
- [x] **DBV3** — Verified via the real test suite's `TestRealLabelStoreIntegration`, run directly against a **disposable copy** of the actual local `label_store.db`: `validate_label_governance()` reports `LREC1` for exactly the 8 real Specifications (independently confirmed via a scratch query: 8 rows, all `phase='Registered'`) and zero `LREC2` (0 Adjudicated, 0 Flagged real forward-tier rows — matching WBS 3.6's own DBV3 finding exactly). Both the copy (before/after byte comparison) and the real file itself (SHA-256 before/after) are proven unchanged by the read-only call.
- [x] **DBV4** — N/A — no governance ledger *other than* `governance_receipts` itself is touched, and that table received no real writes this milestone (§ above).
- [x] **DBV5** — N/A — no real forward-tier or specification data was actually receipted this milestone against the real database; WBS 3.10's backup coverage remains live regardless.
- [x] **DBV6** — N/A — unchanged from prior findings.

## 6. Backward Compatibility (BC)

- [x] **BC1–BC4** — `registry.py`, `value_store.py`, `standing.py`, `superseded.py`, `propagation.py`, `conditioning.py`, `lattice.py`, `composition.py`, `receipt_ledger.py`, `evidence_governance.py`, `transition_governance.py`, `contract_receipts.py` — **zero diff to any of them** (`git diff --stat` confirms). `receipts.py` carries exactly this milestone's own three additive blocks (confirmed by reading the full diff — every other hunk in that file's working-tree diff predates this session).
- [x] **BC5** — Confirmed by direct search: `grep -rl "register_specification_with_receipt\|adjudicate_forward_label_with_receipt\|validate_label_governance\|RegistrationRule\|LREC1\|LREC2" --include="*.py" .` returns only this milestone's own two new files.

## 7. Rollback Verification (RBV)

- [x] **RBV1** — Delete `research/label_store/label_receipts.py` and `tests/test_label_store_label_receipts.py`; revert the three additive blocks in `research/evidence_library/receipts.py` (a clean, isolated `git diff` region). Nothing else references any of the new symbols (BC5). `registry.py`/`value_store.py` remain fully functional and independently usable without this module, exactly as before.
- [x] **RBV2** — No production data touched; the only real database this milestone's real test suite touched was a **disposable copy** of the local, gitignored `label_store.db`, read-only.
- [x] **RBV3** — Exercised directly: TV3's sabotage/revert cycle.
- [x] **RBV4** — N/A — no governance ledger record (beyond the receipt substrate's own, already-append-only-guaranteed table) is in scope; no real receipts were committed to the real ledger this milestone.

## 8. Technical Debt Assessment (TD)

| What | Why deferred | Closed by (WBS ID) | Blast radius if never closed |
|---|---|---|---|
| The Label Store's own 8 real, pre-existing Specifications (WBS 3.3/3.4) have no `label.register` receipt and no roadmap-assigned backfill item | The roadmap's own WBS 3.8b text scopes the mandatory backfill exclusively to Phase 2's 17 `feature.register` receipts (`FS-GAP-004`); no WBS item names a Label-Store-side backfill, and inventing one would be scope expansion this milestone's instructions forbid ("Do not begin WBS 3.8b") | Not scheduled — would need an explicit roadmap decision | None today, demonstrated honestly rather than hidden: `validate_label_governance()` correctly reports `LREC1` for all 8 real Specifications when run against real data (§0, §5 DBV3) — the gap is visible and named, not silently closed or silently ignored |
| `registry.register_specification()`'s own docstring claim ("a permanent Rejected row IS written" on Tier A failure) does not hold for every failure mode — specifically, a Tier A field that is also a `NOT NULL` INTEGER column (e.g. `horizon_days=None`) trips a `sqlite3.IntegrityError` on INSERT, which `register_specification()`'s own exception handler mis-attributes to its "duplicate content hash, idempotent no-op" branch, silently rolling back with **no row ever persisted**, despite still raising `SpecificationRejected` | Pre-existing WBS 3.1 implementation gap, orthogonal to ADR-I011's own design (the ADR says nothing about SQL column nullability) — fixing it would be "unrelated refactoring" of a prior milestone's code, out of this milestone's scope | Not scheduled — would need its own, separately-authorized engineering fix to `registry.py` | Low today: this milestone's own `register_specification_with_receipt()` is unaffected (it builds its receipt from the caller-supplied `content` dict directly, never depending on reading the row back), and `replay_registration_receipt()` works correctly for every OTHER Tier A failure mode (confirmed: this milestone's own rejection-replay test uses a non-NULL-violating failure — an invalid `horizon_governance` string — specifically to avoid this gap); only a *replay* of a receipt for a `horizon_days=None`-class rejection would fail (with a clear `LabelReceiptError`, not silently), and no such receipt exists anywhere real |
| Five **pre-existing** label-store test files (`test_label_store_registry.py`, `test_label_store_propagation.py`, and others) load `registry.py`/`value_store.py` via `importlib` **without** first checking `sys.modules`, unconditionally re-executing and replacing whichever copy is cached — a latent test-isolation fragility, exposed for the first time by this milestone's own cross-module exception-identity assertions (§2 TV2) | Fixing every other test file's loader is "unrelated refactoring" of pre-existing test infrastructure, out of this milestone's scope; this milestone's own test file already works around it correctly (§2 TV2) | Not scheduled — a future milestone touching any of those five test files could add the guard opportunistically | Low today: worked around completely in this file; would only resurface if a *future* test elsewhere also compares cross-module exception identity against a stale top-level binding |

- [x] **TD3** — No placeholder work; every ceiling/receipt function was exercised against real registered Specifications, a real Declared→Pending→Matured→Flagged→Adjudicated lifecycle, and the real local Label Store's all 8 real Specifications.
- [x] **TD4** — None of the above is unscoped or long-lived urgent debt; all three are named, already-scoped future dependencies or deliberate, instruction-respecting scope boundaries.

## 9. Definition of Done (DOD)

- [x] **D1** — Compiles/imports cleanly in both environments
- [x] **D2** — Full suite passes, zero new failures (1965/19 `btc_bot`; 1847/60 base env; one pre-existing, non-recurring, unrelated flake investigated and cleared — §3)
- [x] **D3** — 38 new tests, all passing in both environments
- [x] **D4** — N/A, explicitly stated (§5 — no real writes to gate)
- [x] **D5** — Zero new imports from `research/` into any operational-plane module
- [x] **D6** — Rollback lever named (§7)
- [x] **D7** — No existing signature, table, or column touched (`receipts.py`'s three additions are new frozenset/dict entries, not changes to any existing one)
- [x] **D8** — No `.env`, credential, or secret touched, referenced, or required
- [x] **D9** — Independently deployable — merging this alone changes no other module's runtime behaviour (the `receipts.py` extension is inert until a caller emits `label.register`/`label.adjudicate`, which only this milestone's own code does)
- [x] **D10** — N/A for this milestone specifically (no new mandatory schema field on either store; `HumanActRequiredError`/`UnknownRegistrationRuleError` are refusals on malformed calls, not Tier-A-style completeness gates)
- [x] **D11** — All sections completed, including explicit N/A statements

## 10. Reviewer Checklist (RC)

*Pending — Rohith.*

- [ ] **RC1** — Milestone corresponds to WBS 3.8a exactly, no scope creep into 3.8b
- [ ] **RC2** — No file under `docs/architecture/` touched
- [ ] **RC3** — "Files Changed" list matches `git status` exactly
- [ ] **RC4** — At least two tests read in full, confirmed to exercise real behaviour
- [ ] **RC5** — Full-suite counts reproduced independently in both environments
- [ ] **RC6** — Rollback lever confirmed
- [ ] **RC7** — Full diff read — confirm `registry.py`, `value_store.py`, `standing.py`, `superseded.py`, `propagation.py`, `conditioning.py`, `lattice.py`, `composition.py`, `receipt_ledger.py`, `evidence_governance.py`, `transition_governance.py`, `contract_receipts.py` all show no diff, and that `receipts.py`'s diff is exactly the three additive blocks described in §0
- [ ] **RC8** — Sign-off

  **Reviewer:** _______________  **Date:** _______________

---

## Files Changed

| File | Type |
|---|---|
| `research/label_store/label_receipts.py` | New — `label.register`/`label.adjudicate` governance receipt emission, replay, and validation |
| `tests/test_label_store_label_receipts.py` | New — 38 tests |
| `research/evidence_library/receipts.py` | Modified — purely additive: `"label"` (`SUBJECT_KINDS`), `"register"`/`"adjudicate"` (`ACTIONS`), `"label.register"`/`"label.adjudicate"` (`DECISION_TAXONOMY`) |

## Summary

Shipped WBS 3.8a: `label.register`/`label.adjudicate` governance receipts, chained and replayable, reusing the existing Phase 0 receipt substrate (`receipts.py`/`receipt_ledger.py`) and mirroring the two established consumption patterns it already has — `evidence_governance.py`'s engine-pinned `AdmissibilityRule` substitution point for the derived path, and `transition_governance.py`'s "human act, never derived" discretionary path — rather than reinventing either. `registry.py` and `value_store.py` are called through their existing, unmodified public functions only; Tier A's own private validator (`registry._validate_tier_a()`) is reused directly for replay rather than duplicated, following the exact cross-module-reuse precedent `transition_governance.py` already set against `evidence_governance.py`. The one shared file touched, `receipts.py`, gained three small, purely additive taxonomy entries — the same extension mechanism already exercised once (PR-7's `"evaluate"`) — with zero change to any existing entry. `validate_label_governance()` (`LREC1`/`LREC2`) detects the accepted non-atomicity gap between a registration/adjudication and its receipt, mirroring the identical, already-accepted precedent in `evidence_governance.py`/`transition_governance.py`; run read-only against a disposable copy of the real local `label_store.db`, it honestly reports the 8 real, pre-existing Specifications (WBS 3.3/3.4) as receipt-less — a genuine gap the roadmap's own WBS 3.8b text does not assign anyone to close, logged rather than silently invented or silently hidden. TV3's sabotage/revert cycle confirmed the "derived-path replay re-executes and matches" exit criterion is genuinely tested, not tautological. A real, full-suite-only test-isolation defect was found and root-caused during regression testing — five pre-existing label-store test files load `registry.py`/`value_store.py` without a `sys.modules` guard, so the last one collected silently wins and an earlier file's cached exception-class reference can go stale — fixed entirely within this milestone's own new test file (resolving the exception class the same way the code under test does), without touching any pre-existing test file. No architectural inconsistency with ADR-I011 was found. Zero changes to `registry.py`, `value_store.py`, `standing.py`, `superseded.py`, `propagation.py`, `conditioning.py`, `lattice.py`, `composition.py`, `receipt_ledger.py`, `evidence_governance.py`, `transition_governance.py`, or `contract_receipts.py`. Next: WBS 3.8b — **not begun**, per instruction.
