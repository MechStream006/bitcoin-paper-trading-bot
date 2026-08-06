# INC-0003 — `sys.modules['engine']` Name Collision Broke Full-Suite Regression

| Field | Value |
|---|---|
| **Status** | Closed — root-caused, fixed, verified |
| **Severity** | Medium (no data loss; but a genuine full-suite regression — 37 failed, 16 errors — that would have shipped invisibly under per-file testing) |
| **Discovered during** | Engineering roadmap Phase 2, WBS 2.4 (Feature Specification migration milestone), REG1 full-regression-suite step |
| **Discovered by** | Running `pytest tests/` (full suite) after all WBS 2.4 tests passed individually — the exact REG1 step the Engineering Verification Standard requires before any milestone can be marked done |
| **Affected system** | `research/feature_store/evidence_engine_bindings.py`, `research/feature_store/migrate_evidence_engine_features.py`, `tests/test_evidence_engine_bindings.py`, `tests/test_migrate_evidence_engine_features.py` — all four use a bare `import engine` to reach `research/evidence/engine.py` |
| **Author** | Claude Sonnet 5 (paired session), recorded per standing instruction: incidents are recorded when verification reveals a real engineering failure |
| **Date filed** | 2026-08-05 |

---

## Summary

All four new WBS 2.4 files followed this repository's established bare-sibling-import convention (`sys.path.insert` + `import engine`) to reach `research/evidence/engine.py`. In isolation (single-file or few-file `pytest` invocations), every test passed. Under the **full** `pytest tests/` suite, 37 tests failed and 16 more errored at collection/setup, all with `AttributeError: module 'engine' has no attribute 'load_master'`.

Root cause: `sys.modules` is process-global, and `research/dq/engine.py` (a completely different module, part of PR-7's Data Completeness Framework) is *also* reachable under the bare name `"engine"` from `tests/test_dq_engine.py`'s own `sys.path.insert(0, .../research/dq)` + `import engine`. Under pytest's alphabetical collection order, `test_dq_engine.py` collects before `test_evidence_engine_bindings.py`, so by the time any WBS 2.4 code does `import engine`, Python returns the **already-cached** `research/dq/engine.py` module instead of loading `research/evidence/engine.py` — silently, with no error, until code tries to call a function (`load_master()`) that the wrong module doesn't have.

This exact collision class was already documented as pre-existing, unrelated technical debt at Program P1 Increment 6 (`CLAUDE.md` §18), where it manifested as a single intermittent full-suite-only failure in `test_atr_expansion.py`. WBS 2.4 is the first code to introduce *new* bare `import engine` sites, and it hit the same collision at far larger scale (37 failures + 16 errors, deterministic, not intermittent) — a real, newly-introduced regression, not a recurrence of the already-logged one.

---

## Timeline

1. **Implementation.** `evidence_engine_bindings.py`, `migrate_evidence_engine_features.py`, and both new test files each used a bare `import engine` after inserting `research/evidence/` onto `sys.path` — the same convention already used elsewhere in this codebase (`research/ingest/dvol.py` etc.).
2. **Per-file verification passed.** `pytest tests/test_evidence_engine_bindings.py` (40/40) and `pytest tests/test_migrate_evidence_engine_features.py` (16/16) both passed cleanly in isolation, including the real-data-gated equivalence tests.
3. **TV3 empirical sabotage-and-revert also passed** (temporarily miswiring `rv_21d` to extract `log_ret`; both real-data equivalence tests correctly failed with `SpecificationRejected`, confirming the check has real teeth), still run in isolation.
4. **REG1 — the trigger.** `pytest tests/ -q --ignore=tests/test_api.py` (the full suite, per the Verification Standard's mandatory regression step) produced **37 failed, 16 errors** — all in the two new WBS 2.4 test files, all `AttributeError: module 'engine' has no attribute 'load_master'`.
5. **Detection/isolation.** `pytest tests/test_dq_engine.py tests/test_evidence_engine_bindings.py -q` alone reproduced the same 37 failures, confirming the two-file interaction was sufficient — not something touching dozens of unrelated files.
6. **Root-cause investigation.** `grep -rl "^import engine"` found seven files across the repository using the bare name; `tests/test_dq_engine.py:23` inserts `research/dq/` onto `sys.path` and does `import engine`, which is a different physical file (`research/dq/engine.py`) from the one WBS 2.4 intends (`research/evidence/engine.py`). `sys.modules['engine']` can only ever hold one of them at a time per process.
7. **Fix.** `evidence_engine_bindings.py` now loads `research/evidence/engine.py` via `importlib.util.spec_from_file_location()` under a process-unique `sys.modules` key (`"research.evidence.engine (feature_store binding)"`) instead of the bare name, and exposes the loaded module as its own `engine` attribute. `migrate_evidence_engine_features.py`'s `_equivalence_check()` and both test files now obtain `engine` via `bindings.engine` (a single shared object) instead of their own independent bare imports — guaranteeing all WBS 2.4 code observes/patches the identical module object regardless of pytest collection order.
8. **Verification.** `pytest tests/test_dq_engine.py tests/test_evidence_engine_bindings.py tests/test_migrate_evidence_engine_features.py -q` → 159/159 passed. Full suite (`btc_bot` env): 1627 passed, 19 skipped, 0 failed. Full suite (base env, no pandas): 1486 passed, 29 skipped, 0 failed. Real `feature_store.db` re-run with the fix in place: identical 17 spec_hashes to the pre-fix run (including `rv_21d`'s `05b40acefc52a04f...`, matching the hash observed during step 3's TV3 sabotage test), row counts stable at 17/17.

---

## Root Cause

**A repository-wide convention (bare sibling-import via `sys.path.insert`) is not actually collision-safe once two different files anywhere in the codebase choose the same importable module name.** The convention was correct and sufficient for every prior milestone in this roadmap because none of them shared a name with another `sys.path`-inserted module. `engine.py` is a common enough filename that two unrelated subsystems (`research/dq/` and `research/evidence/`) both used it independently, and neither was wrong to do so in isolation — the collision is a property of the combination, only visible under full-suite collection order, which is exactly why REG1 (not BV or TV) is the check that caught it.

This is the same general lesson as `INC-0001` and `INC-0002`, restated for a third distinct mechanism: **a convention's local correctness in every file examined so far does not guarantee global correctness once the process-wide namespace it shares (here, `sys.modules`) is actually exercised at full-repository scale.**

---

## Detection

Not caught by:
- Any per-file test run, including the deliberately thorough TV3 sabotage-and-revert exercise — both looked at only 1–2 files at a time and never observed the collision.
- Code review of any single new file — each file's bare `import engine` is, read in isolation, unremarkable and matches house convention exactly.

Caught by:
- REG1, the mandatory full-suite regression step, on its very first application to this milestone — the third incident this session where a required Verification Standard step (not a special investigation) found the actual defect (`INC-0001`: the regression test's schema snapshot; `INC-0002`: DBV3's "run it twice"; this one: REG1's full-suite run).

---

## Resolution

1. `evidence_engine_bindings.py`'s `_load_evidence_engine()` loads `research/evidence/engine.py` via `importlib.util.spec_from_file_location()` under a process-unique `sys.modules` key, sidestepping the bare-name collision entirely without modifying `research/evidence/engine.py` or `research/dq/engine.py` at all.
2. `migrate_evidence_engine_features.py`'s `_equivalence_check()`, and both new test files, obtain `engine` via `bindings.engine` (the one shared, already-loaded module object) rather than each performing their own independent bare import — guaranteeing a single consistent module identity across all WBS 2.4 code regardless of import order.
3. No change to `research/dq/engine.py`, `research/evidence/engine.py`, `tests/test_dq_engine.py`, or any other pre-existing file — the fix is entirely contained within the four files this milestone itself introduced.

---

## Prevention

- **Applied in this milestone:** the specific fix above, scoped to the four files WBS 2.4 itself introduced.
- **Hazard Register entry filed: [`EH-002`](../hazard_register.md#eh-002--bare-import-engine-is-not-collision-safe-across-subsystems).** Unlike the initial assessment during this incident's investigation, the collision pattern *does* recur beyond the files this milestone touched: `research/forward_evidence/compute_dsr_for_registration.py` has the identical bare `import engine` targeting `research/evidence/engine.py`, unfixed, dormant only because it is never imported by any test today. This meets the Hazard Register's own registration bar (a pattern recurring beyond the current milestone's file scope, where fixing it everywhere is out of scope for this milestone) exactly as `EH-001` did — `EH-002` was registered rather than the pairing being dismissed as a one-off.
- **Generalisable habit, recorded here for future milestones:** any future module that adds a *new* bare `import <name>` against a `sys.path.insert`-based sibling-import convention should grep the repository for existing bare imports of the same name (`grep -rl "^import <name>"`) before assuming isolation-tested behaviour holds at full-suite scale — REG1 will catch a collision, but catching it *before* writing 50+ tests against the wrong assumption is cheaper. This is a process note, not a new architectural rule; it does not propose replacing the sibling-import convention itself, which remains correct everywhere it doesn't collide.

---

## Lessons Learned

1. **REG1 (full-suite regression) is not a formality distinct from per-file testing — it is the only check in the entire Verification Standard that can observe a process-global namespace collision.** Every other check in this session's discipline (BV, TV, TV3, even DBV3's idempotency re-run) operates within a scope too narrow to see this class of defect.
2. **A convention being correct in every file examined is not the same as the convention being collision-safe.** This is now the third time in this session's incident record that "locally correct, contextually wrong" has been the root cause (`INC-0001`: a read path with a write side-effect; `INC-0002`: a versioning primitive called from an idempotent context; `INC-0003`: a sibling-import convention colliding across two unrelated subsystems) — worth recognising as a recurring *shape* of defect in this codebase, even though each instance's specific fix was different and none individually rose to a Hazard Register generalisation.
3. **The fix belongs at the layer that actually needs isolation** — `evidence_engine_bindings.py`, the one module that needs a guaranteed-correct reference to `research/evidence/engine.py`, not a repository-wide change to how sibling-imports work everywhere (which would be exactly the kind of "no architectural redesign" this milestone was explicitly told to avoid).
