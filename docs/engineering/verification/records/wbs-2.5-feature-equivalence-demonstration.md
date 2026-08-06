# Milestone Verification Record

| Field | Value |
|---|---|
| **Milestone** | Feature Equivalence Demonstration — research vs. frozen-live bindings, PS1 resolution |
| **WBS item(s)** | 2.5 |
| **Roadmap phase** | Phase 2 — Feature Store (completes Sprint N+2 alongside WBS 2.4) |
| **Standard version referenced** | 1.0.0 |
| **Date** | 2026-08-05 |
| **Implementer** | Claude Sonnet 5 (paired session) |
| **Reviewer** | *(pending — Rohith)* |

**Scope discipline, stated up front.** Neither side of any equivalence comparison is modified in production form. `research/evidence/signals.py` and all 5 `research/forward_evidence/decision_function_*.py` files are read, never edited, for the entire milestone (each was temporarily sabotaged and reverted exactly once, for TV3, with `git diff` confirming byte-identical restoration each time). This milestone demonstrates equivalence per ADR-I010 §8.3; it does not retire, deprecate, or modify any existing implementation — see §Retirement Eligibility below.

---

## 1. Build Verification (BV)

- [x] **BV1** — `python -m py_compile` clean on `tests/test_decision_function_research_equivalence.py` and the modified `research/forward_evidence/tests/test_atr_expansion.py`, in the `btc_bot` env (the only env either file collects in — both require `pandas`).
- [x] **BV2** — Reuses `research/feature_store/evidence_engine_bindings.py`'s import-isolation mechanism (WBS 2.4/`INC-0003`) rather than inventing a new one; the `_scoped_engine_module()` context manager is a direct, deliberate extension of that same technique to a second vulnerable import site (`signals.py`'s own `from engine import SignalSpec`).
- [x] **BV3** — New test file requires `pandas` (transitively, via `engine.load_master()`); gated with `pytest.importorskip("pandas")` so it collects cleanly (as a single module-level skip) in the base env.
- [x] **BV4** — Verified under both the base env (Python 3.13.12, collection-only path — correctly skipped) and `btc_bot` (Python 3.10.20, full execution — all comparisons run for real against the real corpus).

## 2. Test Verification (TV)

- [x] **TV1** — `tests/test_decision_function_research_equivalence.py` (new, 10 tests: one equivalence test + one non-trivial-signal sanity test per each of the 5 decision functions).
- [x] **TV2** — House style confirmed (`class TestXxx:`, module-scoped `master` fixture, `pytest.importorskip` gating, `pytestmark` for the real-corpus skip condition matching WBS 2.4's own `TestAgainstRealLocalData` precedent).
- [x] **TV3** — Empirically verified, not merely reasoned: `decision_function_g1_market_regime.py`'s bull/bear position assignment was temporarily swapped (`+1.0`/`-1.0` exchanged) and reverted. The equivalence test correctly failed — 1444 of 1987 positions (72.7%) mismatched, `Max absolute difference among violations: 2.` — before the fix, and passed clean after reverting; `git diff`/`git status --short` confirmed byte-identical restoration. A second, independent TV3 exercise was performed on the pre-existing `DFA07` collision fix itself (see TV4).
- [x] **TV4** — The `DFA07` import-isolation fix was verified with its own before/after empirical proof: `git stash push` on `test_atr_expansion.py` reproduced the documented Program P1 Increment 6 flake deterministically (`ImportError: cannot import name 'SignalSpec' from 'engine'`, run via `pytest tests/test_dq_engine.py research/forward_evidence/tests/test_atr_expansion.py::...::test_DFA07...`), then `git stash pop` restored the fix and the same command passed clean (104/104). This is a genuine regression-fix verification, not a new-feature TV3 — recorded here since it is the milestone's second empirical falsifiability exercise.
- [x] **TV5** — Property/invariant: every one of the 5 comparisons is checked with `numpy.testing.assert_array_equal` (exact equality, not approximate) — appropriate because both sides are deterministic, non-floating-point-accumulating formulas over the same finite-precision inputs; a genuine implementation divergence (as TV3 demonstrated) produces exact, not near, mismatches.

## 3. Regression Verification (REG)

- [x] **REG1 — corrected scope from WBS 2.4's own record.** WBS 2.4's REG1 numbers were measured against `pytest tests/` only. This milestone's investigation discovered the actual historical "full suite" convention (referenced throughout `CLAUDE.md` §18, e.g. Program P1 Increment 6's `test_atr_expansion.py` citation) is the **true repo-wide** `pytest` (bare, no path argument, `--ignore=tests/test_api.py`), which also collects `research/*/tests/`. Measured against the correct, wider scope: `btc_bot` env — **1819 passed, 19 skipped, 0 failed** (1838 collected). Base env at this same wide scope currently **cannot collect** — 6 pre-existing `research/forward_evidence/tests/*.py` files (`test_atr_expansion.py`, `test_atr_runner.py`, `test_ivrv_checkpoint{1,2,3}.py`, `test_runner_base.py`) hard-`import pandas` at module level with no `importorskip` guard, aborting collection entirely — confirmed pre-existing and unrelated to this milestone (none of these imports were added or touched here; only one line inside `test_atr_expansion.py` was changed, unrelated to its `pandas` import). Base env is therefore measured at the established `pytest tests/` scope instead, consistent with every prior milestone's own base-env convention: **1486 passed, 30 skipped, 0 failed**.
- [x] **REG2** — Skip-count delta (base env, `tests/` scope: 29→30) is exactly this milestone's one new test file's own `pytest.importorskip("pandas")` collection-time skip.
- [x] **REG3** — **One real, previously-undiagnosed regression was found and fixed: the `DFA07` full-suite flake (§TV4).** No other flakes observed in either environment across all runs performed this session.
- [x] **REG4** — CI unchanged (no CI config touched).

## 4. Performance Verification (PERF)

**N/A** — no new corpus-scale loop. Each comparison runs `engine.load_master()` once per test module (via a `scope="module"` fixture, computed once and reused across all 10 tests in the file) plus the 5 lightweight `sig_*()`/`compute_signal()` calls, same cost class as any single research script invocation.

## 5. Database Verification (DBV)

**Applicability: NO write activity.** This milestone reads `feature_store.db` (to confirm WBS 2.4's `vrp_spread` Specification is Active, for the `ivrv` equivalence narrative) but writes nothing to it. No schema change, no new row, no migration script.

- [x] **DBV4** — Real `ledger.db` re-confirmed unchanged (70 runs / 26 receipts / 13 transitions / 142 gate_results, `receipt_ledger.verify_chain()` → `ok=True`) before and after this milestone.
- [x] **DBV5** — `feature_store.db` reviewed per this milestone's explicit instruction; see `docs/engineering/feature_store_backup_strategy.md` (new, documentation-only recommendation, no code change).

## 6. Backward Compatibility (BC)

- [x] **BC1–BC3** — `research/evidence/signals.py` and all 5 `decision_function_*.py` files: **zero net lines changed** (confirmed by `git status`/`git diff` — none appear as modified; each was sabotaged and reverted, verified byte-identical each time).
- [x] **BC4** — `research/forward_evidence/tests/test_atr_expansion.py`'s one changed test method (`test_DFA07_cross_validation_vs_signals_py`) is a pure fix to that test's own import robustness — identical assertions, identical synthetic data, identical pass/fail semantics; every other test in the 322-line file is untouched, confirmed by running the full file (129/129 passed) both before and after.
- [x] **BC5** — Confirmed by direct search: no existing file imports `tests/test_decision_function_research_equivalence.py` — no existing consumer to break.

## 7. Rollback Verification (RBV)

- [x] **RBV1** — Delete `tests/test_decision_function_research_equivalence.py`; revert the one hunk in `research/forward_evidence/tests/test_atr_expansion.py` (`git diff` isolates it precisely — a single test method).
- [x] **RBV2** — No production data touched. Real ledger re-verified unchanged (§DBV4).
- [x] **RBV3** — N/A — this milestone adds no new consumer-facing interface; it is a verification artifact only.
- [x] **RBV4** — N/A — no governance record (`ledger.db`) in scope.

## 8. Technical Debt Assessment (TD)

| What | Why deferred | Closed by (WBS ID) | Blast radius if never closed |
|---|---|---|---|
| The remaining `EH-002` instance (`compute_dsr_for_registration.py`) is still unfixed | Out of this milestone's declared file scope — see `EH-002`'s own "Why not fixed now" | Not scheduled | None today — dormant, never imported by any test |
| The 6 pre-existing `research/forward_evidence/tests/*.py` files still lack `pytest.importorskip("pandas")` gating, so the true repo-wide full suite cannot run in the base env | Fixing 6 files with no relationship to this milestone's own declared scope (only 1 of the 6 was touched, for an unrelated reason) would be exactly the "unrelated refactoring" this milestone was told to avoid | Not scheduled | Low today — the base-env `tests/` scope remains a fully valid, established regression signal; this only means the base env's "full suite" and the true repo-wide "full suite" are not currently the same command, which is now documented rather than silently assumed |
| `feature_store.db` has no backup, wiring recommended but not implemented (§`feature_store_backup_strategy.md`) | Explicitly instructed as documentation-only for this milestone | Not scheduled; trigger condition documented | Low today — content is 100% regenerable via `migrate_evidence_engine_features.py --commit`, verified this session |

- [x] **TD3** — No placeholder work; every equivalence claim in the Feature Migration Register §5 is backed by a passing, real-corpus test. The `DFA07` fix is a complete resolution, not a partial one (verified both directions, §TV4).
- [x] **TD4** — None of the above is long-lived urgent debt. The `EH-002` remaining instance is tracked in the Hazard Register per its own governance.

## 9. Definition of Done (DOD)

- [x] **D1** — Compiles/imports cleanly in the environment that supports it; correctly skips in the one that doesn't
- [x] **D2** — Full suite passes, zero failures in either environment at each environment's valid scope (§REG1)
- [x] **D3** — 10 new tests, all passing; 1 pre-existing test's robustness genuinely improved (not just re-tested)
- [x] **D4** — N/A, explicitly stated (§4)
- [x] **D5** — Zero imports from `research/` into any operational-plane (`src/`) module
- [x] **D6** — Rollback lever named (§7)
- [x] **D7** — No existing signature/table/column changed
- [x] **D8** — No `.env`, credential, or secret referenced
- [x] **D9** — Independently deployable — this milestone adds verification artifacts only, no runtime behavior change to anything the bot executes
- [x] **D10** — Equivalence demonstrated for all 5 decision functions named in ADR-I010 §1/§8.3, against real corpus data, with TV3 empirical falsifiability proof
- [x] **D11** — All sections completed, including the Feature Migration Register update (§5 there) and the backup-strategy documentation the instruction required

## 10. Reviewer Checklist (RC)

*Pending — Rohith.*

- [ ] **RC1** — Milestone corresponds to WBS 2.5 exactly, no scope creep into 2.6+
- [ ] **RC2** — `signals.py` and all 5 `decision_function_*.py` files confirmed untouched (`git diff` shows no changes)
- [ ] **RC3** — The `test_atr_expansion.py` diff is confirmed to be exactly the one test method, nothing else
- [ ] **RC4** — At least two equivalence tests read in full, confirmed to compare real, non-trivial computations
- [ ] **RC5** — Full-suite counts reproduced independently in both environments at each one's valid scope
- [ ] **RC6** — Rollback lever confirmed
- [ ] **RC7** — Retirement-eligibility conclusion (§ below) judged appropriate — no implementation retired
- [ ] **RC8** — Sign-off

  **Reviewer:** _______________  **Date:** _______________

---

## Files Changed

| File | Type |
|---|---|
| `tests/test_decision_function_research_equivalence.py` | New — 10 tests, equivalence demonstration for all 5 decision functions |
| `research/forward_evidence/tests/test_atr_expansion.py` | Modified — one test method (`test_DFA07_cross_validation_vs_signals_py`) fixed for import-order robustness (`EH-002` third instance) |
| `docs/engineering/feature_migration_register.md` | Modified — new §5 (WBS 2.5 equivalence status, verification evidence, retirement eligibility) |
| `docs/engineering/hazard_register.md` | Modified — `EH-002` updated (3 of 4 known instances now fixed) |
| `docs/engineering/feature_store_backup_strategy.md` | New — documentation-only backup recommendation |
| `docs/engineering/verification/records/wbs-2.5-feature-equivalence-demonstration.md` | New — this record |

## Retirement Eligibility

Per the explicit instruction "verify equivalence before considering any legacy implementation retired": equivalence is now demonstrated for all 5 frozen decision functions, over real corpus data, with empirical falsifiability proof. **None are recommended for retirement.** ADR-I010 §8.3's model is coexistence, not replacement — a frozen implementation exists "for legitimate operational reasons" (independence from `engine.py`'s preprocessing pipeline, SHA-256 hash-pinning for forward-collection reproducibility) and remains a registered *conformant binding* once equivalence is shown, not a candidate for deletion. This milestone closes PS1's testing gap; it does not and should not trigger any retirement action.

## Summary

Demonstrated behavioural equivalence, per ADR-I010 §8.3, between all 5 of the platform's audited SHA-256 hash-pinned frozen decision functions and their authoritative research-implementation counterparts in `signals.py` — closing the "dual implementation without an equivalence guarantee" gap (PS1) `research/evidence/decision_function_g3_atr_expansion.py` alone had previously, partially covered (synthetic data only). Every comparison ran against the real local research corpus and was empirically proven falsifiable via a deliberate sabotage-and-revert. A second, real defect was found and fixed along the way: the pre-existing `DFA07` cross-validation test's own susceptibility to the `sys.modules['engine']` collision documented at `INC-0003`/`EH-002` — previously an unexplained "intermittent" full-suite flake (Program P1 Increment 6), now root-caused as a third instance of the same hazard and fixed with the identical, already-precedented technique. The Feature Migration Register and Hazard Register are both updated to reflect this. A documentation-only backup-strategy recommendation for `feature_store.db` was produced per the accompanying instruction, explicitly deferred (not implemented) pending the trigger condition it names. Full regression is clean: 1819 passed / 19 skipped / 0 failed (`btc_bot`, true repo-wide scope), 1486 passed / 30 skipped / 0 failed (base env, established `tests/` scope). Real `ledger.db` confirmed untouched throughout. Next: WBS 2.6 (warm-up as a contract term) or WBS 2.7 (conditioning propagation rule) — not started, per this milestone's explicit stop instruction.
