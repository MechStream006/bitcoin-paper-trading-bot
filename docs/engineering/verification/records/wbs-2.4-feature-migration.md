# Milestone Verification Record

| Field | Value |
|---|---|
| **Milestone** | Feature Specification migration — `research/feature_store/evidence_engine_bindings.py`, `migrate_evidence_engine_features.py` |
| **WBS item(s)** | 2.4 |
| **Roadmap phase** | Phase 2 — Feature Store |
| **Standard version referenced** | 1.0.0 |
| **Date** | 2026-08-05 |
| **Implementer** | Claude Sonnet 5 (paired session) |
| **Reviewer** | *(pending — Rohith)* |

**Scope discipline, stated up front.** `research/evidence/engine.py` is **not modified at all** — every migrated feature is a thin extraction function that calls the existing, unmodified `load_master()` and returns one already-computed column. 17 of `load_master()`'s 18 derived columns are migrated; `ret_fwd1` is deliberately excluded (a label, not a feature, per ADR-I011 §1.1's Central Asymmetry) — see `docs/engineering/feature_migration_register.md` for the full per-feature breakdown.

---

## 1. Build Verification (BV)

- [x] **BV1** — `python -m py_compile` clean on all four new files (`evidence_engine_bindings.py`, `migrate_evidence_engine_features.py`, both test files) in both environments.
- [x] **BV2** — `evidence_engine_bindings.py`'s import isolation (`_load_evidence_engine()`, `importlib.util.spec_from_file_location`) is a new, deliberate addition (see BC below); the registration/activation flow itself reuses `registry.py`'s existing `register_definition`/`register_binding`/`register_specification`/`activate_specification` exactly as WBS 2.1–2.3 built them — no new registry mechanism.
- [x] **BV3** — `evidence_engine_bindings.py` and the migration script require `pandas`/`engine.py`'s own dependencies (transitively, via `load_master()`); both new test files are gated with `pytest.importorskip("pandas")` so they collect cleanly (as a single module-level skip) in the base env with no `pandas` installed.
- [x] **BV4** — Verified under both the base env (Python 3.13.12, no pandas — collection-only path exercised) and `btc_bot` (Python 3.10.20 — full execution path, including real-data tests).

## 2. Test Verification (TV)

- [x] **TV1** — `tests/test_evidence_engine_bindings.py` (new, 40 tests: mocked column-extraction correctness, NaN round-tripping, the `BINDINGS`/`ret_fwd1`-exclusion registry contract, real-data equivalence for all 17 features) and `tests/test_migrate_evidence_engine_features.py` (new, 16 tests: `FEATURES`-table/`BINDINGS` consistency, `plan()`/`print_plan()` purity, `run()` against a throwaway `tmp_path` database, the INC-0002 idempotency regression, `_equivalence_check()` positive/negative cases, a real-data end-to-end run) — **56 new tests**.
- [x] **TV2** — House style confirmed (`class TestXxx:`, `tmp_path`/`monkeypatch` fixtures, plain `assert`, `pytest.importorskip` gating matching `test_storage_manifest.py`'s and prior Phase 2 test files' precedent).
- [x] **TV3** — Empirically verified, not merely reasoned: temporarily miswired `evidence_engine_bindings.rv_21d()` to extract `log_ret` instead of `rv_21d`. Both `TestAgainstRealLocalData` real-data equivalence tests (in `test_evidence_engine_bindings.py` and `test_migrate_evidence_engine_features.py`) correctly failed — the latter with `registry.SpecificationRejected: ... Tier B binding equivalence check returned False`, proving Tier B activation genuinely rejects a wrong binding rather than rubber-stamping it. Reverted; `diff` against the pre-sabotage file confirmed byte-identical; full targeted re-run passed clean.
- [x] **TV4** — Every refusal/failure path this milestone introduces has a dedicated test: a mismatched-column equivalence check (`TestEquivalenceCheck::test_equivalence_check_fails_for_a_mismatched_column`), the idempotency regression closing INC-0002 (`test_migration_is_actually_idempotent_on_a_real_run`), and the `ret_fwd1` exclusion enforced both by a test and by a module-level `assert` in the production script itself (fails at import time, not just at test time, if ever violated).
- [x] **TV5** — Property/invariant tests: `FEATURES` and `BINDINGS` cannot silently drift apart (`test_features_table_matches_bindings_registry_exactly`, plus the production module's own `assert`); re-running the migration twice produces identical `spec_hash`es and stable row counts (`TestMigrationIsActuallyIdempotent`); `_get_or_register_definition()` reuses an unchanged Definition version but creates a new one when content genuinely changes (both directions tested).

## 3. Regression Verification (REG)

- [x] **REG1** — `btc_bot` env (full suite, `--ignore=tests/test_api.py`, the pre-existing unrelated `fastapi`-environment gap): **1627 passed, 19 skipped, 0 failed** (56 of the 1627 are this milestone's new tests, confirmed by running with/without the two new files: 1571 passed without them, 1627 with — exactly +56, zero pre-existing test affected). Base env: **1486 passed, 29 skipped, 0 failed** — confirmed identical to the 1486-passed baseline measured with the two new files excluded entirely, so the new files contribute 0 passed (correctly module-skipped, no `pandas`) and +2 skips (one per new file's `importorskip`) in this environment.
- [x] **REG2** — Skip-count delta (base env: 27→29) fully accounted for: exactly the two new files' own `pytest.importorskip("pandas")` collection-time skips, one skip event per file (not per test function — confirmed directly: `pytest tests/test_evidence_engine_bindings.py tests/test_migrate_evidence_engine_features.py` alone reports "2 skipped", not 56).
- [x] **REG3** — **One real, deterministic full-suite-only regression was found and fixed within this milestone: `INC-0003`** (`sys.modules['engine']` name collision with `research/dq/engine.py`, discovered by REG1 itself — 37 failed + 16 errors on first full-suite run, 0 failed/errored after the fix). See §Technical Debt / Incidents below. No other flakes observed; the pre-existing, previously-documented `test_atr_expansion.py::test_DFA07_cross_validation_vs_signals_py` intermittent full-suite-only flake (Program P1 Increment 6) did not recur in this session's runs and was not touched by this milestone.
- [x] **REG4** — CI unchanged (no CI config touched).

## 4. Performance Verification (PERF)

**N/A** — no new corpus-scale loop. Each migrated feature's activation runs `load_master()` twice (binding + direct comparison) against the same in-process-cached DuckDB result (`engine.py`'s own `_CACHE`), same cost class as calling `load_master()` directly; this milestone adds no new computation, only registration/comparison around an existing call.

## 5. Database Verification (DBV)

**Applicability: YES** — this milestone is the first real write activity against `research/feature_store/feature_store.db` (WBS 2.1–2.3 built the schema but exercised it only with hand-written test Specifications, never a real migration run).

- [x] **DBV1** — Additive only: 17 new rows in each of `feature_definitions` and `feature_specifications`. No schema change, no existing row touched.
- [x] **DBV2** — `migrate_evidence_engine_features.py` follows the established dry-run/`--commit` convention exactly (`main()` prints the plan and requires `--commit` to write anything).
- [x] **DBV3 — this is the check that found INC-0002.** Running `--commit` a second time against the real database, as this check requires, produced 17 *new* specification hashes on first implementation (34/34 rows total) rather than a no-op. Fixed (`registry.get_latest_definition()` + `_get_or_register_definition()`); re-verified genuinely idempotent across three consecutive real `--commit` runs post-fix (identical hashes, stable 17/17 row counts each time), including once more after the unrelated INC-0003 import-isolation fix landed (hashes unchanged, confirming the import fix has zero effect on registered content).
- [x] **DBV4** — N/A — `feature_store.db` is not `ledger.db`; no governance ledger touched. Independently re-confirmed: real `ledger.db` unchanged (70 runs / 26 receipts / 13 transitions / 142 gate_results, `receipt_ledger.verify_chain()` → `ok=True`) before and after this milestone's work.
- [x] **DBV5** — No backup taken before the real `--commit` runs — consistent with WBS 2.1–2.3's own stated rationale (a brand-new, self-contained database with, at the time, zero external consumers). This milestone is the first to actually populate it for real; a future milestone that builds a real consumer of `feature_store.db` should reassess whether backup discipline is now warranted.
- [x] **DBV6** — N/A — `governance_verification.py`'s `SCHEMA_FINGERPRINT` covers `ledger.db` only, by design (different zone, per IQROS-P2 single-writer-per-zone), unaffected by this milestone.

## 6. Backward Compatibility (BC)

- [x] **BC1–BC3** — `research/evidence/engine.py` itself: **zero lines changed** (confirmed by `git status`/diff — file does not appear as modified). `research/feature_store/registry.py`: zero lines changed in this milestone (its `get_latest_definition()` addition landed as part of the INC-0002 fix, itself additive-only — no existing function's signature or behaviour altered, confirmed by WBS 2.1–2.3's 50 pre-existing tests all still passing unmodified).
- [x] **BC4** — The one genuinely new cross-cutting mechanism this milestone introduces — `evidence_engine_bindings._load_evidence_engine()`'s process-unique-key import isolation — is additive and local to that one file; it does not change how any other file in the repository performs a bare `import engine` (confirmed: `research/dq/engine.py`, `research/dq/rules.py`, the three `test_dq_*.py` files, and `research/forward_evidence/compute_dsr_for_registration.py` are all byte-unchanged).
- [x] **BC5** — Confirmed by direct search: no existing file outside this milestone's own four new files imports `evidence_engine_bindings` or `migrate_evidence_engine_features` — no existing consumer to break.

## 7. Rollback Verification (RBV)

- [x] **RBV1** — Delete `research/feature_store/evidence_engine_bindings.py`, `migrate_evidence_engine_features.py`, both new test files, and the real local `feature_store.db`. `registry.py`'s `get_latest_definition()` addition can be left in place harmlessly (pure, additive, read-only, unused by anything else) or removed together — either is a clean rollback.
- [x] **RBV2** — No production data touched. Real ledger re-verified byte-for-byte-equivalent in row counts and chain validity (§DBV4) before and after.
- [x] **RBV3** — Not exercised; no downstream consumer of the newly-Active Feature Specifications exists yet (the Dataset Builder, ADR-I012, is a later phase) — stated explicitly per the standard's requirement, same as WBS 2.1–2.3's own §7.
- [x] **RBV4** — N/A — no governance record (`ledger.db`) in scope for this milestone.

## 8. Technical Debt Assessment (TD)

| What | Why deferred | Closed by (WBS ID) | Blast radius if never closed |
|---|---|---|---|
| `research/forward_evidence/compute_dsr_for_registration.py` retains the same bare `import engine` collision risk this milestone fixed in its own four files (`INC-0003`) | Editing that file is unrelated to WBS 2.4's own scope — would be unrelated-file refactoring | Registered as [`EH-002`](../../hazard_register.md#eh-002--bare-import-engine-is-not-collision-safe-across-subsystems); not scheduled | None today — dormant, never imported by any test; would surface as the identical `AttributeError` the day someone writes a test that imports it into a process where `research/dq/engine.py` was already cached |
| No citation-index check when a Specification would be retired (inherited from WBS 2.1–2.3, unchanged here) | `ADR-I012`'s Dataset Builder does not exist yet | Phase 4 | None today — nothing retires a Specification yet |
| `feature_store.db` still has no backup discipline despite now holding real, non-test data for the first time (see DBV5) | Out of this milestone's declared scope; flagged for reassessment, not decided here | Not scheduled | Low today (regenerable in seconds by re-running `migrate_evidence_engine_features.py --commit`, unlike `ledger.db`'s irreplaceable governance history) |

- [x] **TD3** — No placeholder work; every migrated feature has a complete, activated Specification with a real, passing equivalence check. `INC-0002` and `INC-0003` were real defects found and fully closed within this milestone, not debt left open.
- [x] **TD4** — None of the above is long-lived urgent debt. `EH-002` is tracked in the Hazard Register per its own governance (§3 there), not promoted to `CLAUDE.md` §19 (that section is reserved for Program P1 maintenance items specifically).

## 9. Definition of Done (DOD)

- [x] **D1** — Compiles/imports cleanly in both environments
- [x] **D2** — Full suite passes, zero failures in either environment (§REG1)
- [x] **D3** — 56 new tests, all passing where the environment supports them (btc_bot: 56/56; base: correctly skipped, not silently ignored)
- [x] **D4** — N/A, explicitly stated (§4)
- [x] **D5** — Zero imports from `research/` into any operational-plane (`src/`) module — confirmed, this milestone touches only `research/` and `tests/`
- [x] **D6** — Rollback lever named (§7)
- [x] **D7** — No existing signature/table/column removed or changed in an incompatible way — `registry.py`'s one addition (`get_latest_definition`) is purely additive
- [x] **D8** — No `.env`, credential, or secret referenced
- [x] **D9** — Independently deployable — the real `feature_store.db` this milestone populates has no consumer yet; nothing downstream breaks if this milestone were reverted
- [x] **D10** — Every migrated feature demonstrates real behavioural equivalence against its unmodified source, verified against the real local corpus, not only synthetic fixtures (per this session's explicit requirement)
- [x] **D11** — All sections of this template completed, including explicit N/A statements (§4) and the full incident/hazard cross-references (§8)

## 10. Reviewer Checklist (RC)

*Pending — Rohith.*

- [ ] **RC1** — Milestone corresponds to WBS 2.4 exactly, no scope creep into 2.5+
- [ ] **RC2** — `research/evidence/engine.py` confirmed untouched (`git diff` shows no changes to that file)
- [ ] **RC3** — "Files Changed" list matches `git status` exactly
- [ ] **RC4** — At least two tests read in full, confirmed to exercise real behaviour (including one from `TestAgainstRealLocalData`)
- [ ] **RC5** — Full-suite counts reproduced independently in both environments
- [ ] **RC6** — Rollback lever confirmed
- [ ] **RC7** — `INC-0002`, `INC-0003`, and `EH-002` read and judged appropriately scoped (not silently expanded, not silently suppressed)
- [ ] **RC8** — Sign-off

  **Reviewer:** _______________  **Date:** _______________

---

## Files Changed

| File | Type |
|---|---|
| `research/feature_store/evidence_engine_bindings.py` | New — 17 thin binding functions wrapping `engine.load_master()`, process-unique-key import isolation |
| `research/feature_store/migrate_evidence_engine_features.py` | New — declarative `FEATURES` table, dry-run/`--commit` migration script, idempotency guard, Tier B equivalence check |
| `research/feature_store/registry.py` | Modified — additive only: `get_latest_definition()` (INC-0002 fix) |
| `tests/test_evidence_engine_bindings.py` | New — 40 tests |
| `tests/test_migrate_evidence_engine_features.py` | New — 16 tests |
| `research/feature_store/feature_store.db` (+ `-wal`/`-shm`) | New, gitignored — real local database, 17 Definitions/17 Specifications, all Active |
| `.gitignore` | Modified — `research/feature_store/feature_store.db` entries (from WBS 2.1–2.3, unchanged this milestone) |
| `docs/engineering/incidents/INC-0002-feature-migration-idempotency.md` | New |
| `docs/engineering/incidents/INC-0003-engine-module-name-collision.md` | New |
| `docs/engineering/hazard_register.md` | Modified — added `EH-002` |
| `docs/engineering/feature_migration_register.md` | New |
| `docs/engineering/verification/records/wbs-2.4-feature-migration.md` | New — this record |

## Summary

Migrated 17 of `research/evidence/engine.py`'s 18 derived columns into registered, Active Feature Specifications, wrapping the existing, completely unmodified `load_master()` computation rather than rewriting it. The one excluded column (`ret_fwd1`) is structurally a label under ADR-I011's Central Asymmetry, not a feature — excluded with both documentation and an enforced module-level assertion, not left as a silent gap. Every migrated feature's Tier B activation ran a real behavioural-equivalence check against the real local research corpus, and that check was empirically proven to have real discriminating power via a deliberate sabotage-and-revert (TV3). Two real engineering defects were found and fully closed within this milestone's own verification steps — `INC-0002` (migration idempotency, caught by the standard's own DBV3 "run it twice" requirement) and `INC-0003` (a `sys.modules` name collision, caught by the standard's own REG1 full-suite requirement) — with one generalisable, currently-dormant instance of the latter's risk pattern registered as `EH-002` for future remediation rather than fixed out-of-scope now. Full regression is clean in both environments: 1627 passed / 19 skipped / 0 failed (`btc_bot`), 1486 passed / 29 skipped / 0 failed (base env). Real `ledger.db` (70/26/13/142, chain valid) confirmed untouched throughout. Next: WBS 2.5 — equivalence demonstration for the audited dual-implementation risk (PS1: research vs. frozen-live bindings for the 5 hash-pinned decision functions, roadmap §8.3), completing Sprint N+2 alongside this milestone. Not started, per this milestone's explicit stop instruction.
