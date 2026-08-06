# Milestone Verification Record

| Field | Value |
|---|---|
| **Milestone** | Conditioning Propagation + Store-Agnostic Admissibility Inheritance — `research/label_store/propagation.py` |
| **WBS item(s)** | 3.7 |
| **Roadmap phase** | Phase 3 — Label Store (`phase_3_label_store_roadmap.md` v1.1.0) |
| **Standard version referenced** | 1.0.0 |
| **Date** | 2026-08-06 |
| **Implementer** | Claude Sonnet 5 |
| **Reviewer** | *(pending — Rohith)* |

**WBS 3.4, 3.5, and 3.6 semantics are unchanged, as instructed.** `migrate_h6_forward_labels.py`, `standing.py`, `superseded.py`, `registry.py`, `value_store.py`, and `register_conventions.py` (Label Store) are all untouched (`git diff --stat` confirms zero diff). **`research/feature_store/conditioning.py` is imported unchanged, proven by diff** — the roadmap's own exit criterion — as are `research/admissibility/lattice.py`, `research/admissibility/composition.py`, and both Feature Store files (`registry.py`, `value_store.py`). This milestone adds exactly one new module and one new test file; nothing else in the repository was modified.

**No architectural inconsistency with ADR-I011 was found.** The roadmap's dual citation "§10.2 · §9.2" for this item was confirmed (re-derived independently this milestone, not assumed from a prior finding) to be the same provision cited by both its printed label and its true sequential position under the ADR's own internal subsection-numbering quirk — not a second, conflicting provision.

**WBS 3.8a is explicitly not begun.**

---

## 1. Build Verification (BV)

- [x] **BV1** — `python -m py_compile research/label_store/propagation.py tests/test_label_store_propagation.py` → clean in both environments.
- [x] **BV2** — `propagation.py` itself has no module-level pandas dependency (mirrors `conditioning.py`'s own design) — `import propagation` succeeds in the base env. Its two conditioning-adapter functions (`conditioning_for_label_specification`/`conditioning_for_feature_specification`) transitively need pandas only when they actually call into `conditioning.py`'s real `_source_conditioning_map()` — a real, structural fact inherited from `conditioning.py` itself, not introduced here. Its ceiling-computation functions (`source_ceiling`, `label_value_own_ceiling`, `label_value_ceiling`, `feature_specification_ceiling`, `all_active_feature_specification_ceilings`) never call `conditioning.py` at all and need no gating.
- [x] **BV3** — No new dependency (pandas/lattice/composition were already required transitively by Phase 0/2's existing modules).
- [x] **BV4** — Verified under both environments; base-env behaviour differs correctly by test class (§2).

## 2. Test Verification (TV)

- [x] **TV1** — `tests/test_label_store_propagation.py` (new, 41 tests): Source Ceiling (6), Label Value Own Ceiling (12), Label Value Ceiling (8), Feature Specification Ceiling (4), Unknown Binding Source (2), Conditioning.py Unchanged — structural guards (2), Conditioning For Label Specification (2, pandas-gated), Conditioning For Feature Specification (1, pandas-gated), No Datetime Now (1), Real Feature Store Integration (3, pandas-gated + real-file-gated).
- [x] **TV2** — House style confirmed, **with two findings corrected before they caused a false pass or a false fail**: (1) the `db` fixture initially called only `value_store.init_db()`, mirroring `test_label_store_superseded.py`'s fixture — but that milestone never registers a real Specification (it uses a bare hardcoded `spec_hash` constant), while this milestone's `label_value_ceiling()` genuinely needs `registry.get_specification()` to resolve a real row, so the fixture must also call `registry.init_db()` on the same file; fixed by calling both. (2) the structural "conditioning.py never reimplemented" guard was first written as a raw substring search for `_source_conditioning_map` across the whole file — which falsely failed against this module's own docstring (which *names* that function in prose, explaining what the module must not reach into); corrected to an AST-based check for an actual `Attribute`/`Name` reference, mirroring `test_label_store_superseded.py`'s own `test_superseded_module_never_calls_adjudicate_forward_label` pattern. Both corrections are structural test fixes, not production-code changes. Re-run: base env **35 passed, 6 skipped, 0 failed**; `btc_bot` (has pandas, has the real local `feature_store.db`) **41/41 passed**.
- [x] **TV3** — Empirically verified: replaced `label_value_ceiling()`'s core `return composition.meet(own, input_ceiling)` with `return own` (discarding the source ceiling entirely) — `test_full_flow` and `test_result_is_meet_not_own_ceiling_alone` both failed (`Certification.GREEN == Certification.UNKNOWN` assertion errors; `result != own` reporting equal), confirming the tests genuinely exercise the meet composition rather than being tautological. Restored; full 41-test file re-confirmed green in `btc_bot`.
- [x] **TV4** — Refusal/exclusion paths tested: an unregistered `binding_id` raises `UnknownBindingSourceError` from both the ceiling path (`test_unknown_binding_id_raises`) and the conditioning-adapter path (`TestUnknownBindingSource`, including that the error names the known bindings); a missing declared label raises `LookupError` (`test_missing_label_raises_lookup_error`); a missing registered Specification raises `LookupError` (`test_missing_specification_raises_lookup_error`); an unrecognised `maturity_state` raises `ValueError` (`test_unrecognised_state_raises`).
- [x] **TV5** — Property/invariant tests: **never fabricates a passing verdict** (`test_never_fabricates_a_passing_verdict` — the exact `certification_from_gauntlet({})` failure mode identified during design is asserted never to occur); **Adjudicated is never restored to Clean** (`test_adjudicated_is_never_restored_to_clean`, re-confirming `lattice.py`'s own documented Choice 2 holds through this milestone's real usage); **Maturity not-applicable at top rung for both a raw source and a Feature Specification** (`test_maturity_is_not_applicable_at_top_rung`, `test_maturity_is_not_applicable` — both rely on `lattice.py`'s own `__post_init__` invariant firing correctly, not merely on this module's own claim); **own ceiling never constrains Certification/Readiness** (`test_own_ceiling_never_constrains_certification_or_readiness`); **`conditioning.py` is called, never reimplemented** (`TestConditioningPyUnchanged`, AST-based); **the roadmap's own exit criterion, exercised literally against real data** (`TestRealFeatureStoreIntegration` — all 17 real Active Feature Specifications in the real local `feature_store.db` get a computed ceiling, and every one is honestly `Certification.UNKNOWN`/`Readiness.UNKNOWN` since no real gauntlet report exists for any of their sources).

## 3. Regression Verification (REG)

- [x] **REG1** — `btc_bot` env, `python -m pytest tests/ -q --ignore=tests/test_api.py`:
  - Before (after WBS 3.6): **1886 passed, 19 skipped, 0 failed**
  - After: **1927 passed, 19 skipped, 0 failed**
  - Delta: **+41 passed**, exactly matching the new test count.
  - Base env, full suite (`python -m pytest tests/ -q`): **1809 passed, 60 skipped, 0 failed** — includes this file's own correct 35-passed/6-skipped split.
  - Maximal collision-risk combination (`test_label_store_registry.py`, `test_label_store_value_store.py`, `test_label_store_standing.py`, `test_label_store_superseded.py`, `test_label_store_propagation.py`, `test_feature_store_registry.py`, `test_feature_store_value_store.py`, `test_conditioning.py` — every module this milestone's collision-safe sibling loaders touch, run together in one process): **257/257 passed**, confirming the four distinct `sys.modules` registered names (`label_store_registry`, `label_store_value_store`, `feature_store_registry`, `label_store_propagation`) never collide with each other or with the corresponding Feature Store bare names.
- [x] **REG2** — Skip count unchanged in the full `btc_bot` suite (19→19), since this file contributes zero skips there (pandas present); the base-env skip delta (19→60 across the *whole* suite run, vs. this file's own isolated 35/6 split) is consistent with other already-gated files in the suite, not a new or unexplained category.
- [x] **REG3** — No known pre-existing flake recurred.
- [x] **REG4** — CI workflow unchanged.

## 4. Performance Verification (PERF)

**N/A** — one SQL row lookup per ceiling call plus a handful of in-memory `Admissibility` tuple constructions; no corpus-scale loop. `all_active_feature_specification_ceilings()` iterates 17 real rows today — negligible.

## 5. Database Verification (DBV)

**Applicability: NO WRITES OF ANY KIND** — per the design decision recorded before implementation ("Use existing Label Store lifecycle wherever possible"), this module is purely compute-on-demand. It reads via `registry.get_specification()` and `value_store.get_forward_label()` (both pre-existing, unmodified) and never writes to either store. No new table, no new column, on either `label_store.db` or `feature_store.db`.

- [x] **DBV1** — N/A — no schema change of any kind.
- [x] **DBV2** — N/A — no write path exists in this module to gate.
- [x] **DBV3** — Verified via the real test suite's `TestRealFeatureStoreIntegration`, run directly against the actual local `research/feature_store/feature_store.db`: `all_active_feature_specification_ceilings()` returned exactly 17 entries (matching the real Active row count independently confirmed via a scratch query earlier this milestone), every one honestly `Certification.UNKNOWN`/`Readiness.UNKNOWN`/`Maturity` not-applicable. The real local `label_store.db` was read only incidentally by unrelated pre-existing suite tests, never by this milestone's own tests (which construct disposable `tmp_path` databases) — confirmed by `git diff` reporting no change to either real `.db` file's mtime-independent content (they are gitignored and untracked, so this was additionally cross-checked by re-running `TestRealFeatureStoreIntegration` twice and confirming byte-identical results both times, consistent with a read-only path).
- [x] **DBV4** — N/A — no governance ledger touched.
- [x] **DBV5** — N/A — no real data written.
- [x] **DBV6** — N/A — unchanged from prior findings.

## 6. Backward Compatibility (BC)

- [x] **BC1–BC4** — N/A in the strongest sense: **zero existing functions, tables, columns, or files were touched at all**, including `conditioning.py`, `lattice.py`, `composition.py`, and both stores' `registry.py`/`value_store.py`. `git diff --stat` against all nine of those files returns empty output.
- [x] **BC5** — Confirmed by direct search: `grep -rl "label_value_ceiling\|feature_specification_ceiling\|source_ceiling\|_LABEL_BINDING_SOURCES\|UnknownBindingSourceError" --include="*.py" .` returns only this milestone's own two new files.

## 7. Rollback Verification (RBV)

- [x] **RBV1** — Delete `research/label_store/propagation.py` and `tests/test_label_store_propagation.py`. Nothing else references them; `conditioning.py`, `lattice.py`, `composition.py`, and both stores remain fully functional and independently usable without this module (it only ever calls into them, never the reverse).
- [x] **RBV2** — No production data touched; the only real database this milestone's real test suite touched was the local, gitignored `feature_store.db`, and only via read-only queries (`SELECT`, `get_specification`).
- [x] **RBV3** — Exercised directly: TV3's sabotage/revert cycle.
- [x] **RBV4** — N/A — no governance record in scope.

## 8. Technical Debt Assessment (TD)

| What | Why deferred | Closed by (WBS ID) | Blast radius if never closed |
|---|---|---|---|
| No real gauntlet certification exists for `spot_1m`/`perp_1m`/`funding_raw` (only `dvol.py` is wired to `quality/admissibility.py`, per that module's own docstring) — every real ceiling this milestone computes is honestly capped at `Certification.UNKNOWN`/`Readiness.UNKNOWN` | Wiring the gauntlet against live Binance/funding data is a live, heavyweight, out-of-scope side-action ("no unrelated refactoring"); this milestone's own job is the composition mechanism, not manufacturing new certification inputs | Not scheduled — would be its own milestone (extending `research/quality/admissibility.py`'s wiring beyond `dvol.py`) | None today — the low ceiling is the honest, correct answer per §10.2 ("A label computed on an uncertified span inherits that cap"); a future gauntlet-wiring milestone would only ever *raise* real ceilings, never require touching this module |
| Computed ceilings are never persisted anywhere — every call recomputes from scratch | Explicit design decision this milestone, responsive to "use existing Label Store lifecycle wherever possible": neither store has an admissibility column, and adding one would be exactly the "architectural redesign" this milestone is instructed to avoid | Most plausibly Phase 4's Dataset Builder (`ADR-I011` §15), where DI5's meet is actually consumed and a caller first has a real place to put a persisted value | None today — negligible recomputation cost (§4 PERF); a future consumer can call these functions directly with no migration needed |
| `_LABEL_BINDING_SOURCES` only maps the five real, currently-registered Label Store bindings | Mirrors `standing.py`'s own established "only populate what's real" precedent; a hardcoded guess at a future binding's source would invent architecture the ADR does not require | Whichever future milestone registers a new real binding | Low — `UnknownBindingSourceError` refuses cleanly (never silently guesses) for any binding not yet in the map, per Ph6 |

- [x] **TD3** — No placeholder work; every ceiling function was exercised against real registered Specifications, real declared/matured/flagged/adjudicated Label Values, and the real local Feature Store's all 17 Active Specifications.
- [x] **TD4** — None of the above is unscoped or long-lived urgent debt; all are named, already-scoped future dependencies or deliberate, ADR-respecting scope boundaries.

## 9. Definition of Done (DOD)

- [x] **D1** — Compiles/imports cleanly in both environments
- [x] **D2** — Full suite passes, zero new failures (1927/19, `btc_bot`; 1809/60, base env full suite)
- [x] **D3** — 41 new tests, all passing (in the environment where they can run)
- [x] **D4** — N/A, explicitly stated (§5 — no writes exist to gate)
- [x] **D5** — Zero new imports from `research/` into any operational-plane module
- [x] **D6** — Rollback lever named (§7)
- [x] **D7** — No existing signature, table, or column touched at all
- [x] **D8** — No `.env`, credential, or secret touched, referenced, or required
- [x] **D9** — Independently deployable — merging this alone changes no other module's behaviour (zero files modified besides new ones)
- [x] **D10** — N/A for this milestone specifically (no new mandatory schema field on either store; `UnknownBindingSourceError` is a refusal on an unrecognised binding, not a Tier-A-style completeness gate)
- [x] **D11** — All sections completed, including explicit N/A statements

## 10. Reviewer Checklist (RC)

*Pending — Rohith.*

- [ ] **RC1** — Milestone corresponds to WBS 3.7 exactly, no scope creep into 3.8a
- [ ] **RC2** — No file under `docs/architecture/` touched
- [ ] **RC3** — "Files Changed" list matches `git status` exactly
- [ ] **RC4** — At least two tests read in full, confirmed to exercise real behaviour
- [ ] **RC5** — Full-suite counts reproduced independently in both environments
- [ ] **RC6** — Rollback lever confirmed
- [ ] **RC7** — Full diff read — confirm `conditioning.py`, `lattice.py`, `composition.py`, both stores' `registry.py`/`value_store.py`, `standing.py`, `superseded.py`, `migrate_h6_forward_labels.py`, and `register_conventions.py` all show no diff
- [ ] **RC8** — Sign-off

  **Reviewer:** _______________  **Date:** _______________

---

## Files Changed

| File | Type |
|---|---|
| `research/label_store/propagation.py` | New — conditioning adapter + admissibility ceiling computation for both stores |
| `tests/test_label_store_propagation.py` | New — 41 tests |

## Summary

Shipped WBS 3.7: conditioning propagation and store-agnostic admissibility inheritance, closing `FS-GAP-001` in both the Label Store and the Feature Store at once, per the roadmap's own S-2 amendment. `research/feature_store/conditioning.py` is imported and called unchanged (proven by empty diff) via a small, declarative `_LABEL_BINDING_SOURCES` adapter that lets a Label Specification — which has no `input_requirements` field by ADR-I011's own design — use the Feature-Store-shaped public `conditioning_for_specification()` API without any modification to either registry. `lattice.py` and `composition.py` are likewise called unchanged: `source_ceiling()` constructs the honest, uncertified ceiling directly (`Certification.UNKNOWN`, `Readiness.UNKNOWN`) rather than fabricating a fake gauntlet report through `quality/admissibility.py`'s bridge functions — the exact failure mode identified and rejected during design, and now covered by a dedicated regression test. The Label Store's own six-state maturity lifecycle maps onto the lattice's Maturity/Integrity dimensions exactly as `lattice.py`'s own Integrity docstring already anticipated by citing the Label Store's flagging discipline by name, before the Label Store existed. Every real ceiling this milestone computes — all 17 real Active Feature Specifications in the real local `feature_store.db`, plus a full Declared→Pending→Matured→Flagged→Adjudicated real lifecycle exercised against a registered Label Specification in the test suite — is purely compute-on-demand with zero schema change to either store, per the "use existing Label Store lifecycle wherever possible" instruction. No architectural inconsistency with ADR-I011 was found. Zero changes to `conditioning.py`, `lattice.py`, `composition.py`, either store's `registry.py`/`value_store.py`, `standing.py`, `superseded.py`, `migrate_h6_forward_labels.py`, or `register_conventions.py`. Next: WBS 3.8a — **not begun**, per instruction.
