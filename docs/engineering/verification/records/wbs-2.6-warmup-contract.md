# Milestone Verification Record

| Field | Value |
|---|---|
| **Milestone** | Warm-up Contract — `research/feature_store/warmup.py`, `warmup_policy.yaml` |
| **WBS item(s)** | 2.6 |
| **Roadmap phase** | Phase 2 — Feature Store |
| **Standard version referenced** | 1.0.0 |
| **Date** | 2026-08-05 |
| **Implementer** | Claude Sonnet 5 (paired session) |
| **Reviewer** | *(pending — Rohith)* |

**Scope discipline, stated up front.** `research/feature_store/registry.py` and `value_store.py` are **not modified** — `warmup_periods` was already a mandatory, Tier-A-validated Specification field since WBS 2.1, and `value_store.py`'s own docstring already, deliberately, scoped warm-up enforcement out of that module as a future consumer-side concern. This milestone builds exactly that consumer-side check, wired into the one real resolution-shaped consumer that exists today (`migrate_evidence_engine_features.py`), behind a governed rollout flag shipped at the roadmap's own recommended default (`warn`, not `refuse`).

---

## 1. Build Verification (BV)

- [x] **BV1** — `python -m py_compile` clean on `research/feature_store/warmup.py` and the modified `migrate_evidence_engine_features.py`, in both environments.
- [x] **BV2** — `warmup.py` is a new, standalone module — no existing function signature changed. `migrate_evidence_engine_features.py`'s `run()` gained one new keyword-only parameter (`warmup_mode: str | None = None`, default preserves prior behaviour exactly since the default reads the shipped `warn`-mode policy file, under which every currently-registered feature is sufficient) and `main()` gained one new optional CLI flag (`--warmup-mode`).
- [x] **BV3** — `warmup.py` depends only on stdlib + PyYAML (`yaml`) — confirmed already a dependency in both environments (`research/evidence_library/lifecycle.py`/`report.py` already import it). `warmup.py` collects and runs standalone in the base env with **zero** `pytest.importorskip` needed (unlike every pandas-dependent Feature Store test file so far) — confirmed by `tests/test_warmup.py` passing 22/22 in the base env directly.
- [x] **BV4** — Verified under both the base env (Python 3.13.12) and `btc_bot` (Python 3.10.20).

## 2. Test Verification (TV)

- [x] **TV1** — `tests/test_warmup.py` (new, 22 tests: sufficient-history-in-every-mode, insufficient-history-per-mode, mode validation, policy-file loading, the real shipped policy file) + 8 new tests in `tests/test_migrate_evidence_engine_features.py::TestWarmupIntegration` + 1 new real-corpus test in `TestAgainstRealLocalData` — **31 new tests**.
- [x] **TV2** — House style confirmed (`class TestXxx:`, `tmp_path`/`monkeypatch` fixtures, plain `assert`).
- [x] **TV3** — Empirically verified twice, at two different layers:
  - **Unit level:** `warmup.py`'s boundary comparison (`available_periods >= warmup_periods`) was temporarily changed to strict `>`. 4 tests correctly failed (including the boundary-exactness test and all three "sufficient proceeds in every mode" parametrisations, since the >symbol change made the boundary case incorrectly refuse). Reverted; `tests/test_warmup.py` confirmed 22/22 clean.
  - **Live-script level, against the real database:** `rv_21d`'s `warmup_periods` was temporarily inflated from 21 to 5000 directly in `migrate_evidence_engine_features.py`, then the real script was run three ways against the real 1987-row corpus: `--warmup-mode refuse` genuinely skipped `rv_21d` (`phase=Warmup-Refused`, all 16 other features' spec_hashes unaffected); `--warmup-mode warn` registered `rv_21d` with a printed `WARNING:` line and a new spec_hash (content genuinely changed). Reverted to `warmup_periods=21`; `feature_store.db` deleted and cleanly regenerated; all 17 spec_hashes confirmed identical to their pre-sabotage values (including `rv_21d`'s original `05b40acefc52a04f...`).
- [x] **TV4** — Every mode's distinct behaviour has a dedicated test at both the unit level (`TestCheckWarmupInsufficientHistory`) and the integration level (`TestWarmupIntegration`), plus a dedicated test that `refuse` mode registers nothing for a refused feature (`test_refuse_mode_does_not_register_a_specification_for_refused_features`) and that an unknown mode is a hard error (`test_unknown_mode_raises_policy_error`, `test_unknown_mode_in_file_raises`).
- [x] **TV5** — Property/invariant tests: sufficient history proceeds identically in all three modes (parametrised); the exact boundary (`available == warmup_periods`) is sufficient, not refused; `refuse` mode never returns a result with `action == "refused"` — it always either raises or returns a `"proceed"`/`"proceed_with_warning"` result (`test_refuse_mode_never_returns_a_result`).

## 3. Regression Verification (REG)

- [x] **REG1** — `btc_bot` env (true repo-wide `pytest`, `--ignore=tests/test_api.py`): **1850 passed, 19 skipped, 0 failed** (31 more than WBS 2.5's 1819 — exactly this milestone's new tests, zero pre-existing test affected). Base env (`tests/` scope, per the convention established at WBS 2.5 §REG1): **1508 passed, 30 skipped, 0 failed** (22 more than WBS 2.5's 1486 — exactly `test_warmup.py`'s 22 tests; `test_migrate_evidence_engine_features.py`'s 9 new tests remain correctly gated behind the file's existing `pytest.importorskip("pandas")`, skip count unchanged for that file).
- [x] **REG2** — Skip-count delta (base env: 30→30, unchanged) — `test_warmup.py` needs no `importorskip`, consistent with its stdlib+yaml-only dependency profile.
- [x] **REG3** — No flakes observed. The `DFA07`/`EH-002` collision fixed at WBS 2.5 did not recur.
- [x] **REG4** — CI unchanged.

## 4. Performance Verification (PERF)

**N/A** — `check_warmup()` is an O(1) integer comparison; `run()`'s one `len(engine.load_master())` call reuses `engine.py`'s own process-level `_CACHE`, already paid for by the pre-existing Tier B equivalence checks in the same run. No new corpus-scale loop.

## 5. Database Verification (DBV)

**Applicability: YES** — real write activity against `feature_store.db`, though net content is unchanged (all 17 spec_hashes identical before and after this milestone).

- [x] **DBV1** — Additive only in the schema sense: zero schema change (no new table, no new column — `warmup_periods` already existed since WBS 2.1). The only real-database activity was the TV3 sabotage-and-revert exercise (§TV3), fully reverted and re-verified idempotent (three consecutive `--commit` runs post-revert, per the established INC-0002 discipline, produced identical hashes and stable 17/17 counts).
- [x] **DBV2/DBV3** — `migrate_evidence_engine_features.py` remains dry-run/`--commit`-gated, unchanged in that respect; re-verified idempotent post-change.
- [x] **DBV4** — Real `ledger.db` re-confirmed unchanged (70/26/13/142, `verify_chain()` → `ok=True`) before and after.
- [x] **DBV5** — `feature_store.db`'s backup posture is unchanged from the WBS 2.5 recommendation (`docs/engineering/feature_store_backup_strategy.md`) — this milestone's content is still 100% regenerable via `migrate_evidence_engine_features.py --commit`.

## 6. Backward Compatibility (BC)

- [x] **BC1–BC3** — `registry.py`, `value_store.py`, `evidence_engine_bindings.py`: **zero lines changed.** `migrate_evidence_engine_features.py`'s changes are additive (new import, new keyword-only parameter with a behaviour-preserving default, new CLI flag) — every existing call site (`run(db_path)` with no `warmup_mode` argument, every existing test that calls it that way) continues to work identically, confirmed by all pre-existing WBS 2.4/2.5 tests in `test_migrate_evidence_engine_features.py` still passing unmodified.
- [x] **BC4** — The new `warmup_action` key on each result dict is additive — no existing consumer reads `run()`'s return value by strict schema (`main()`, the only consumer, already iterates permissively).
- [x] **BC5** — Confirmed by direct search: no existing file outside this milestone's own new/modified files imports `warmup.py` — no existing consumer to break.

## 7. Rollback Verification (RBV)

- [x] **RBV1** — Set `mode: disabled` in `warmup_policy.yaml` (no code change) for a behavioural rollback; delete `warmup.py`/`warmup_policy.yaml` and revert the four isolated hunks in `migrate_evidence_engine_features.py` for a full code rollback. Both named explicitly in `docs/engineering/warmup_contract.md` §6.
- [x] **RBV2** — No production data implication — confirmed, real ledger and real `feature_store.db` both re-verified unchanged/stable (§DBV4, §DBV1).
- [x] **RBV3** — Exercised for real, not just asserted: the TV3 live-script exercise (§TV3) IS a rollback-and-recovery drill — sabotage, observe the new behaviour, revert, regenerate, re-verify identical to the pre-change state.
- [x] **RBV4** — N/A — no governance record (`ledger.db`) in scope.

## 8. Technical Debt Assessment (TD)

| What | Why deferred | Closed by (WBS ID) | Blast radius if never closed |
|---|---|---|---|
| Warm-up enforcement runs only inside `migrate_evidence_engine_features.py`'s Tier-B-equivalence-shaped resolution, not a general `resolve()`-time check with `as_of`-scoped history | No other real consumer of Feature Specifications exists yet in this codebase (the Dataset Builder, Phase 4, is the architecturally-intended general caller) | Phase 4 (Dataset Builder), when it calls `value_store.resolve()` for real | None today — the only real consumer is exactly the one this milestone wires |
| `mode: warn` is the shipped default, not `refuse` | Explicit roadmap instruction (§16 R5): run warn-mode for one full increment before flipping | A future, separate, explicitly-authorised decision (not scheduled by this milestone) | None today — every currently-registered feature is warm-up-sufficient regardless of mode |
| No automated alerting on `WARNING:` lines printed by warn-mode (they go to stdout only, not captured anywhere structured) | Out of this milestone's scope — the migration script has never had structured logging; adding it here would be unrelated-file scope creep | Not scheduled | Low today — zero warnings are currently produced against the real corpus; nothing to miss yet |

- [x] **TD3** — No placeholder work; every mode's behaviour is fully implemented and tested, not stubbed.
- [x] **TD4** — None of the above is long-lived urgent debt; all are correctly-deferred, already-named future dependencies (Phase 4, a future governance decision to flip the mode).

## 9. Definition of Done (DOD)

- [x] **D1** — Compiles/imports cleanly in both environments
- [x] **D2** — Full suite passes, zero failures in either environment (§REG1)
- [x] **D3** — 31 new tests, all passing
- [x] **D4** — N/A, explicitly stated (§4)
- [x] **D5** — Zero imports from `research/` into any operational-plane (`src/`) module
- [x] **D6** — Rollback lever named and exercised (§7)
- [x] **D7** — No existing signature/table/column changed incompatibly — additive only
- [x] **D8** — No `.env`, credential, or secret referenced
- [x] **D9** — Independently deployable — shipped at `mode: warn`, which is observably a no-op against the real corpus today (confirmed by `test_real_corpus_satisfies_every_declared_warmup_even_in_refuse_mode` passing even under hard-refuse)
- [x] **D10** — Warm-up is now a genuinely governed, enforced contract term, with lifecycle/refusal/activation/rollback all documented (`docs/engineering/warmup_contract.md`) and empirically verified at two layers (unit + live script)
- [x] **D11** — All sections completed, including the Feature Migration Register update (§6 there) and the dedicated `warmup_contract.md` document the instruction required

## 10. Reviewer Checklist (RC)

*Pending — Rohith.*

- [ ] **RC1** — Milestone corresponds to WBS 2.6 exactly, no scope creep into 2.7
- [ ] **RC2** — `registry.py`/`value_store.py` confirmed untouched (`git diff` shows no changes)
- [ ] **RC3** — Shipped `mode: warn`, not `refuse`, confirmed in the real `warmup_policy.yaml`
- [ ] **RC4** — At least two tests read in full, including one from `TestWarmupIntegration` and the real-corpus refuse-mode test
- [ ] **RC5** — Full-suite counts reproduced independently in both environments
- [ ] **RC6** — Rollback lever confirmed (set `mode: disabled`, observe no code change needed)
- [ ] **RC7** — `warmup_contract.md`'s four required sections (lifecycle, refusal behaviour, activation conditions, rollback conditions) all present and accurate
- [ ] **RC8** — Sign-off

  **Reviewer:** _______________  **Date:** _______________

---

## Files Changed

| File | Type |
|---|---|
| `research/feature_store/warmup.py` | New — `check_warmup()`, `load_warmup_mode()`, `WarmupRefusal`, `WarmupPolicyError`, `WarmupCheckResult` |
| `research/feature_store/warmup_policy.yaml` | New — the governed rollout flag, shipped at `mode: warn` |
| `research/feature_store/migrate_evidence_engine_features.py` | Modified — additive: `warmup` import, `run()`'s `warmup_mode` parameter + per-feature check, `main()`'s `--warmup-mode` flag |
| `tests/test_warmup.py` | New — 22 tests |
| `tests/test_migrate_evidence_engine_features.py` | Modified — additive: `TestWarmupIntegration` (8 tests) + 1 new real-corpus test |
| `docs/engineering/warmup_contract.md` | New — lifecycle, refusal behaviour, activation conditions, rollback conditions |
| `docs/engineering/feature_migration_register.md` | Modified — new §6 |
| `docs/engineering/verification/records/wbs-2.6-warmup-contract.md` | New — this record |

## Summary

Implemented warm-up as a governed, enforced Feature Specification contract term (ADR-I010 §8.4), closing PS4. `registry.py`'s already-existing `warmup_periods` field is now actually checked at the one real resolution-shaped point in this codebase, behind a three-state rollout flag (`disabled`/`warn`/`refuse`) shipped at the roadmap's own recommended, non-disruptive default (`warn`) — confirmed to be an observable no-op against the real corpus today (every one of the 17 migrated features' declared warm-up is satisfied by the real 1987-row corpus even under hard-refuse mode). The mechanism itself is real and empirically verified at two layers: unit-level TV3 (boundary-comparison sabotage) and a live-script TV3 against the actual database (temporarily inflating one feature's declared warm-up, observing genuine refusal/warning behaviour, then reverting and confirming byte-for-byte restoration). Neither `registry.py` nor `value_store.py` was modified — the architecture's own already-documented separation of concerns (warm-up enforcement as a consumer-side concern) was followed exactly, not redesigned. Full regression is clean: 1850 passed / 19 skipped / 0 failed (`btc_bot`, true repo-wide), 1508 passed / 30 skipped / 0 failed (base env). Real `ledger.db` and `feature_store.db` both confirmed unchanged/stable throughout. Next: WBS 2.7 (conditioning propagation rule per Specification) — not started, per this milestone's explicit stop instruction.
