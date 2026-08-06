# Milestone Verification Record

| Field | Value |
|---|---|
| **Milestone** | Conditioning Propagation Rule — `research/feature_store/conditioning.py` |
| **WBS item(s)** | 2.7 |
| **Roadmap phase** | Phase 2 — Feature Store (final item — Phase 2 WBS-complete as of this milestone) |
| **Standard version referenced** | 1.0.0 |
| **Date** | 2026-08-05 |
| **Implementer** | Claude Sonnet 5 (paired session) |
| **Reviewer** | *(pending — Rohith)* |

**Scope discipline, stated up front.** `research/feature_store/registry.py` and `value_store.py` are **not modified** — `conditioning_rule` was already a mandatory, Tier-A-validated Specification field since WBS 2.1. This milestone builds the propagation computation ADR-I010 §9.1 actually specifies (deriving a real, propagated conditioning statement from a Specification's declared inputs), wired into the same real resolution-shaped consumer WBS 2.6 already established (`migrate_evidence_engine_features.py`), following that milestone's architecture exactly rather than redesigning it.

---

## 1. Build Verification (BV)

- [x] **BV1** — `python -m py_compile` clean on `research/feature_store/conditioning.py` and the modified `migrate_evidence_engine_features.py`, in both environments.
- [x] **BV2** — `conditioning.py` is a new, standalone module, mirroring `warmup.py`'s own shape (pure logic + a real-source resolution function). `migrate_evidence_engine_features.py`'s `FEATURES` table gained one new key per entry (`conditioning_rule`, read from the table rather than hardcoded — the registered Specification content is byte-identical either way, confirmed by unchanged spec_hashes) and `run()` gained one new per-feature check.
- [x] **BV3** — `conditioning.py`'s pure-logic functions (`propagate_conditioning()`, `conditioning_for_specification()` with an injected `source_map`) have **no module-level pandas dependency** — confirmed by 17 of `tests/test_conditioning.py`'s 24 tests passing in the base env directly, with no `importorskip` gating needed for them. Only `_source_conditioning_map()` (which imports the real, pandas-dependent ingest writers) needs pandas — gated individually via `@pytest.mark.skipif`, not a blunt whole-file `importorskip`.
- [x] **BV4** — Verified under both the base env (Python 3.13.12) and `btc_bot` (Python 3.10.20).

## 2. Test Verification (TV)

- [x] **TV1** — `tests/test_conditioning.py` (new, 24 tests) + `tests/test_migrate_evidence_engine_features.py::TestConditioningIntegration` (4 new tests) + 2 new tests in `TestFeaturesTableConsistency` + 1 new real-corpus test in `TestAgainstRealLocalData` — **31 new tests**.
- [x] **TV2** — House style confirmed.
- [x] **TV3** — Empirically verified twice, at two different layers:
  - **Unit level:** `propagate_conditioning()`'s dedup step (`dict.fromkeys(...)`) was temporarily removed. `test_duplicate_inputs_are_deduplicated` correctly failed (`('statement A', 'statement A') != ('statement A',)`). Reverted; `tests/test_conditioning.py` confirmed 17 passed/7 skipped (base env) clean.
  - **Live-script level, against the real database:** `log_ret`'s `input_requirements` was temporarily changed to reference a nonexistent source (`spot_1m_bogus`) directly in `migrate_evidence_engine_features.py`, then the real script was run with `--commit` against the real corpus: `log_ret` was genuinely refused (`REFUSED (conditioning): ...`, `phase=Conditioning-Refused`), all other 16 features' spec_hashes unaffected. Reverted; `feature_store.db` deleted and cleanly regenerated; all 17 spec_hashes confirmed identical to their pre-sabotage values (including `log_ret`'s original `12dbdacd1a30ad49...`).
- [x] **TV4** — Every rule's distinct behaviour has a dedicated test (`preserving`/`narrowing`/`cross-sectional`, each with a required-parameter-missing negative test); an unresolvable source is tested both with an injected `source_map` (unit level) and via the live migration script (integration level, `test_unresolvable_source_is_refused_and_does_not_abort_the_batch`).
- [x] **TV5** — Property/invariant tests: duplicate inputs are deduplicated but order-preserving; `cross-sectional`'s replaced inputs are still recorded on the result for audit (conditioning is never *dropped*, only the output *statement* is replaced, per §9.1's own distinction); empty inputs are valid for `cross-sectional` (it doesn't depend on per-instrument inputs) but invalid for `preserving`/`narrowing`.

## 3. Regression Verification (REG)

- [x] **REG1** — `btc_bot` env (true repo-wide `pytest`, `--ignore=tests/test_api.py`): **1881 passed, 19 skipped, 0 failed** (31 more than WBS 2.6's 1850). Base env (`tests/` scope): **1525 passed, 37 skipped, 0 failed** (17 more passed, 7 more skipped than WBS 2.6's 1508/30 — exactly `test_conditioning.py`'s 17 base-env-runnable + 7 pandas-gated tests).
- [x] **REG2** — Skip-count delta (base env: 30→37, +7) fully accounted for: `test_conditioning.py`'s two pandas-gated classes, skipped individually (not as a whole file) — confirmed by exact count matching `TestSourceConditioningMap` (5 tests) + `TestConditioningForSpecificationRealSources` (2 tests) = 7.
- [x] **REG3** — **One real defect was found and fixed within this milestone's own initial verification, before any test existed: `INC-0004`** (a `sys.path.insert(0, ...)` shadowing the stdlib `calendar` module via `research/ingest/calendar.py`, breaking `pandas`'s own import chain). Caught by manually exercising the new function against real data, not by a named Verification Standard step — recorded as an incident per this session's own standing discipline regardless of which stage caught it. Fixed; a regression test (`test_does_not_shadow_stdlib_calendar`) added.
- [x] **REG4** — CI unchanged.

## 4. Performance Verification (PERF)

**N/A** — `propagate_conditioning()` is string concatenation/deduplication over a handful of short lists; `conditioning_for_specification()` is a dict lookup per declared input (at most 2 for any of the 17). `_source_conditioning_map()` imports three already-existing modules once per `run()` invocation — no new corpus-scale loop.

## 5. Database Verification (DBV)

**Applicability: YES** — real write activity against `feature_store.db`, though net content is unchanged (all 17 spec_hashes identical before and after this milestone, confirmed twice: once for the `conditioning_rule`-declaration-source change alone, once after the TV3 sabotage-and-revert).

- [x] **DBV1** — Zero schema change (no new table, no new column — `conditioning_rule` already existed since WBS 2.1). The `FEATURES` table's own restructuring (declaring `conditioning_rule` per entry rather than hardcoding it in `run()`) does not change what gets written to the database — confirmed by identical spec_hashes before and after.
- [x] **DBV2/DBV3** — `migrate_evidence_engine_features.py` remains dry-run/`--commit`-gated; re-verified idempotent post-change (two consecutive `--commit` runs post-TV3-revert produced identical hashes, stable 17/17 counts).
- [x] **DBV4** — Real `ledger.db` re-confirmed unchanged (70/26/13/142, `verify_chain()` → `ok=True`) before and after.
- [x] **DBV5** — `feature_store.db`'s backup posture is unchanged from the WBS 2.5 recommendation — this milestone's content remains 100% regenerable via `migrate_evidence_engine_features.py --commit`.

## 6. Backward Compatibility (BC)

- [x] **BC1–BC3** — `registry.py`, `value_store.py`, `evidence_engine_bindings.py`, `warmup.py`: **zero lines changed.** `migrate_evidence_engine_features.py`'s changes are additive (new import, per-`FEATURES`-entry field, new per-feature check block, new result-dict keys) — every existing call site continues to work identically, confirmed by all pre-existing WBS 2.4/2.5/2.6 tests still passing unmodified.
- [x] **BC4** — The new `conditioning_rule`/`conditioning_statement`/`conditioning_input_count` keys on each result dict are additive — no existing consumer reads `run()`'s return value by strict schema.
- [x] **BC5** — Confirmed by direct search: no existing file outside this milestone's own new/modified files imports `conditioning.py` — no existing consumer to break.

## 7. Rollback Verification (RBV)

- [x] **RBV1** — Delete `research/feature_store/conditioning.py`; revert the isolated hunks in `migrate_evidence_engine_features.py` (new import, per-entry `conditioning_rule` field, the per-feature check block, the three new result-dict keys). No governed rollout flag exists for this mechanism (unlike warm-up) — see §8 Technical Debt for why that asymmetry is deliberate, not an oversight.
- [x] **RBV2** — No production data implication — confirmed, real ledger and real `feature_store.db` both re-verified unchanged/stable.
- [x] **RBV3** — Exercised for real: the live-script TV3 exercise (§TV3) IS a rollback-and-recovery drill.
- [x] **RBV4** — N/A — no governance record (`ledger.db`) in scope.

## 8. Technical Debt Assessment (TD)

| What | Why deferred | Closed by (WBS ID) | Blast radius if never closed |
|---|---|---|---|
| No rollout flag for conditioning refusal (unlike warm-up's `mode` in `warmup_policy.yaml`) | Deliberate, not an oversight: conditioning refusal only fires on a genuinely unresolvable declared source, which is a Tier-A-adjacent hard-error condition (closer to "malformed input" than "real historical-data-availability constraint") — there is no plausible legitimate reason to want to register a Specification with an unstated conditioning propagation, per ADR-I010 §9.1's own unconditional wording | Not scheduled — would need a real, motivating case for a rollback lever to be added, none exists today | None today — no currently-registered feature is refused |
| Propagation runs only inside `migrate_evidence_engine_features.py`'s Tier-B-equivalence-shaped resolution, not a general `resolve()`-time attachment on `ResolvedValue` | Same reasoning as WBS 2.6's identical debt item: no other real consumer of Feature Specifications exists yet; `value_store.py`'s `ResolvedValue` was deliberately not extended with a `conditioning` field to avoid an unauthorised schema/API change | Phase 4 (Dataset Builder), when it calls `value_store.resolve()` for real | None today |
| `narrowing`/`cross-sectional` rules are implemented and unit-tested but have no real Specification exercising them | Correctly honest — all 17 migrated features are genuinely `preserving`; inventing a narrowing/cross-sectional example would be fabricated, not real | Whenever a future feature genuinely needs one | None — the logic is tested against synthetic inputs, which is the correct scope until a real case exists |

- [x] **TD3** — No placeholder work.
- [x] **TD4** — None of the above is long-lived urgent debt.

## 9. Definition of Done (DOD)

- [x] **D1** — Compiles/imports cleanly in both environments
- [x] **D2** — Full suite passes, zero failures in either environment (§REG1)
- [x] **D3** — 31 new tests, all passing
- [x] **D4** — N/A, explicitly stated (§4)
- [x] **D5** — Zero imports from `research/` into any operational-plane (`src/`) module
- [x] **D6** — Rollback lever named and exercised (§7)
- [x] **D7** — No existing signature/table/column changed incompatibly
- [x] **D8** — No `.env`, credential, or secret referenced
- [x] **D9** — Independently deployable — every one of the 17 currently-registered features' conditioning resolves successfully today; nothing changes observably
- [x] **D10** — Conditioning is now genuinely propagated (not just declared) for every migrated Specification, per ADR-I010 §9.1, verified at two layers (unit + live script)
- [x] **D11** — All sections completed, including the Feature Migration Register update (§7 there, with a per-feature status table) the instruction required

## 10. Reviewer Checklist (RC)

*Pending — Rohith.*

- [ ] **RC1** — Milestone corresponds to WBS 2.7 exactly; confirm Phase 2's WBS 2.1–2.7 is genuinely complete
- [ ] **RC2** — `registry.py`/`value_store.py` confirmed untouched
- [ ] **RC3** — `INC-0004`'s fix (`sys.path.append`, not `insert(0, ...)`) read and judged correct
- [ ] **RC4** — At least two tests read in full, including the live-script-style monkeypatch test and a real-source test
- [ ] **RC5** — Full-suite counts reproduced independently in both environments
- [ ] **RC6** — Rollback lever confirmed
- [ ] **RC7** — Feature Migration Register's §7 table read and confirmed accurate against the real `FEATURES` table
- [ ] **RC8** — Sign-off

  **Reviewer:** _______________  **Date:** _______________

---

## Files Changed

| File | Type |
|---|---|
| `research/feature_store/conditioning.py` | New — `propagate_conditioning()`, `conditioning_for_specification()`, `_source_conditioning_map()`, `ConditioningPropagationError` |
| `research/feature_store/migrate_evidence_engine_features.py` | Modified — additive: `conditioning` import, per-`FEATURES`-entry `conditioning_rule` field (17 entries), `run()`'s per-feature conditioning check |
| `tests/test_conditioning.py` | New — 24 tests |
| `tests/test_migrate_evidence_engine_features.py` | Modified — additive: `TestConditioningIntegration` (4 tests) + 2 in `TestFeaturesTableConsistency` + 1 real-corpus test |
| `docs/engineering/incidents/INC-0004-conditioning-sys-path-stdlib-shadow.md` | New |
| `docs/engineering/feature_migration_register.md` | Modified — new §7 + Phase 2 completion note |
| `docs/engineering/verification/records/wbs-2.7-conditioning-propagation.md` | New — this record |

## Summary

Implemented conditioning propagation as a real computation (ADR-I010 §9.1), not merely a declared field — closing the last named gap in ADR-I010 for the already-migrated 17 features. Every migrated feature's `conditioning_rule` is now explicitly declared per-`FEATURES`-entry (rather than hardcoded once for all 17) and its propagated conditioning statement is genuinely derived from real ingest-writer text at the one real resolution-shaped point in this codebase, following WBS 2.6's own established architecture (no changes to `registry.py`/`value_store.py`) rather than redesigning it. Unlike warm-up, conditioning refusal carries no rollout flag — a deliberate choice, since an unresolvable declared source is closer to a Tier-A malformed-input condition than a genuine historical-data-availability risk. One real defect was found and fixed during this milestone's own initial development, before any test existed: `INC-0004`, a `sys.path` shadowing of the stdlib `calendar` module. Full regression is clean: 1881 passed / 19 skipped / 0 failed (`btc_bot`, true repo-wide), 1525 passed / 37 skipped / 0 failed (base env). Real `ledger.db` and `feature_store.db` both confirmed unchanged/stable throughout. **This completes Phase 2 (Feature Store)'s full WBS 2.1–2.7.** Not started, per this milestone's explicit stop instruction: Phase 3 (Label Store) or any further roadmap work.
