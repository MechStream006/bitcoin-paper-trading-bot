# Milestone Verification Record

| Field | Value |
|---|---|
| **Milestone** | Convention Specifications — `research/label_store/register_conventions.py` |
| **WBS item(s)** | 3.3 |
| **Roadmap phase** | Phase 3 — Label Store (`phase_3_label_store_roadmap.md` v1.1.0) |
| **Standard version referenced** | 1.0.0 |
| **Date** | 2026-08-05 |
| **Implementer** | Claude Sonnet 5 |
| **Reviewer** | Rohith Suresh — signed 2026-08-07 (`LS-DEC-003`) |

**Scope discipline, stated up front.** New, additive registration script + tests only, corresponding to WBS 3.3. Does not modify `research/label_store/registry.py` or `value_store.py` (WBS 3.1/3.2) — restored to byte-identical state after two deliberate TV3 sabotage/revert cycles (verified: full WBS 3.1+3.2+3.3 test suites, 106/106, plus both Feature Store suites and their own migration script's tests, 188/188, all green). No forward data touched — no read of `forward_evidence.db`/`evidence_forward.db`, no call into `value_store.py`, no `register_binding()`/`activate_specification()` call. **WBS 3.4 is explicitly not begun.**

**No architectural inconsistency with ADR-I011 was found.** One factual correction to the ADR's own text was *inherited*, not newly discovered — WBS 3.1's audit finding `A-2` already established that ADR-I011 §1's third named convention ("next-open with per-side slippage") does not exist anywhere in this repository, and that the real inventory is C1/C2/C3 as characterized in the roadmap. This milestone verified that characterization directly against the current, unmodified source for all four bindings (`daily_job.py`, `engine.py`'s `_net_daily()` and `ret_fwd1`, `runner_base.py` + `schema_forward.sql`'s `trade_returns` view) before writing a single field — every convention field below is a transcription of code actually read this session, not copied from the roadmap's summary table. It matched exactly; no divergence to report.

---

## 1. Build Verification (BV)

- [x] **BV1** — `python -m py_compile research/label_store/register_conventions.py tests/test_register_conventions.py` → `COMPILE_OK` (base env + `btc_bot`).
- [x] **BV2** — `register_conventions.py`'s structure (`plan()`/`print_plan()`/`run()`/`main()`, `--commit`/`--db` CLI, idempotent `_get_or_register_definition()`) mirrors `research/feature_store/migrate_evidence_engine_features.py` exactly, the established `migrate_*.py` precedent for "wrap existing, unmodified production code as registered Specifications." **One deliberate divergence, documented in the module docstring and explained below (BC5)**: no `register_binding()`/equivalence check, since this milestone's own exit criterion is Tier A registration only.
- [x] **BV3** — No new dependency. Pure stdlib (`argparse`, `sqlite3` via `registry.py`).
- [x] **BV4** — Verified under both the base env (3.13.12) and `btc_bot` (3.10.20).

## 2. Test Verification (TV)

- [x] **TV1** — `tests/test_register_conventions.py` (new, 14 tests): Dry Run (3), Commit (5), Idempotency (2), Reconvention-Produces-Distinct-Hashes (2), No-Forward-Data-Touched (2).
- [x] **TV2** — House style confirmed, with one explained deviation: the script-level tests invoke `register_conventions.py` via `subprocess`, not an in-process bare import — see BC5 for why (a real, reproduced `sys.modules` collision with the pre-existing `tests/test_migrate_evidence_engine_features.py`, which this milestone does not modify). The one test needing direct in-process access to `registry.py` (`test_isolated_fill_cost_direction_alone_are_sufficient_to_diverge`) uses the WBS 3.1-established `importlib`-under-a-distinct-name pattern.
- [x] **TV3** — Empirically verified **twice**: (a) temporarily replaced C2's `cost_convention` text with C1's identical flat-cost text — full 14-test file re-confirmed green regardless (fill/direction still diverged), so this alone was not a meaningful sabotage and was reverted without asserting a claim from it; superseded by (b): temporarily removed `fill_convention`/`cost_convention`/`direction_convention` from `registry.py`'s `register_specification()` content-hash dict (the actual mechanism `SPECIFICATIONS`' isolated-diff test depends on) — `test_isolated_fill_cost_direction_alone_are_sufficient_to_diverge` failed with `AssertionError: assert 1 == 3` (all three specs collapsed to one hash). Restored; `registry.py` confirmed byte-identical to its WBS 3.2-final state (diff verified line-for-line); full 106-test label-store suite and the 188-test cross-store combination re-confirmed green.
- [x] **TV4** — This milestone makes no new refusal claim (Tier A refusal is WBS 3.1's, already tested there) — stated explicitly rather than left blank. The module-level `assert` statements (`_SPEC_LABELS == {"C1","C2","C3","ret_fwd1"}`, shared-definition check) are import-time guards against future drift, not a refusal path with a dedicated negative test; a broken one would fail collection for every test in the file, which is itself the enforcement.
- [x] **TV5** — Property/invariant tests, the exit criterion's own claims: **idempotency** (`TestIdempotency`, two commit runs produce identical row counts and identical hashes); **Reconvention** (`TestReconventionProducesDistinctHashes` — both the end-to-end real-C1/C2/C3 hashes and the isolated, "otherwise-identical inputs" unit-level proof); **ret_fwd1 no longer unclaimed** (`test_ret_fwd1_is_no_longer_unclaimed`); **no forward data touched** (`TestNoForwardDataTouched`, AST-based, not a naive substring search — see below).

## 3. Regression Verification (REG)

- [x] **REG1** — `btc_bot` env, `python -m pytest tests/ -q --ignore=tests/test_api.py`:
  - Before (after WBS 3.2): **1791 passed, 19 skipped, 0 failed**
  - After: **1805 passed, 19 skipped, 0 failed**
  - Delta: **+14 passed**, exactly the new test file's count.
  - Base env (`tests/test_register_conventions.py` alone): **14/14 passed**.
  - Full label-store suite (3.1+3.2+3.3) together: **106/106 passed**. Combined with both Feature Store test files and their own migration script's test file (the maximum collision-risk combination in the repository): **188/188 passed**.
- [x] **REG2** — Skip count unchanged (19→19).
- [x] **REG3** — No known pre-existing flake recurred in any run performed for this milestone.
- [x] **REG4** — CI workflow unchanged.

## 4. Performance Verification (PERF)

**N/A** — four single-row registrations against a fresh SQLite database; same cost class as every other registry operation in this codebase.

## 5. Database Verification (DBV)

**Applicability: YES** — this milestone is the first real write of *data* (not just schema) into `label_store.db`, via a migration-style script.

- [x] **DBV1** — Additive only: writes new rows into WBS 3.1's existing `label_definitions`/`label_specifications` tables. No schema change, no existing row touched.
- [x] **DBV2** — `research/label_store/register_conventions.py`, dry-run/`--commit`-gated (default: dry run; `--commit` required to write), matching the established `migrate_*.py` pattern exactly.
- [x] **DBV3** — Verified directly, not merely reasoned: ran `--commit` twice in sequence against the same throwaway `.db` path (scratch directory, outside the repo). Row counts after both runs: **2 definitions, 4 specifications** — identical. Second run's printed hashes matched the first run's byte-for-byte (`test_second_commit_run_produces_identical_hashes`).
- [x] **DBV4** — N/A — no governance ledger touched; `label_store.db` is independent of `ledger.db`.
- [x] **DBV5** — N/A — `label_store.db` does not exist as a real, populated database anywhere in the repository; every write in this milestone's testing and manual verification targeted a throwaway path in the session scratch directory, never a real one.
- [x] **DBV6** — N/A — unchanged from the WBS 3.1/3.2 finding; `governance_verification.py`'s manifest covers `ledger.db` only.

## 6. Backward Compatibility (BC)

- [x] **BC1–BC4** — N/A: zero existing functions, tables, columns, or CLI flags touched. `git status` confirms only new, untracked files — `registry.py` and `value_store.py` from WBS 3.1/3.2 are unmodified in the final diff (the two TV3 sabotage/revert cycles left no net change, confirmed by re-running both modules' full test suites clean).
- [x] **BC5** — Confirmed by direct search: `grep -rl "register_conventions\|trade_forward_return\|spot_one_day_forward_return" --include="*.py" .` returns only this milestone's own two new files. **A third instance of the `sys.modules` bare-name collision class was identified before it could cause a failure, not after.** `register_conventions.py`'s own `import registry as reg` (an unqualified, sys.path-inserted import, matching `migrate_evidence_engine_features.py`'s established convention exactly) is safe for its real, standalone invocation (`python register_conventions.py`), but would collide if bare-imported in-process in the same pytest session as `research/feature_store/registry.py`'s own tests — which the pre-existing `tests/test_migrate_evidence_engine_features.py` does. Rather than modify that pre-existing test file (unrelated refactoring, out of scope) or `register_conventions.py`'s correct standalone-script convention, this milestone's test file exercises the script via `subprocess` — its actual real-world invocation mode — sidestepping the collision entirely by construction. Confirmed by running all three label-store test files together with both Feature Store test files and `test_migrate_evidence_engine_features.py`: **188/188 passed.**

## 7. Rollback Verification (RBV)

- [x] **RBV1** — Delete `research/label_store/register_conventions.py` and `tests/test_register_conventions.py`. `registry.py`/`value_store.py` (WBS 3.1/3.2) are unaffected.
- [x] **RBV2** — No production data touched — `label_store.db` does not exist as a real database anywhere.
- [x] **RBV3** — Not exercised in the "toggle and confirm" sense; the natural rollback for a registration script is simply not running `--commit` again, or deleting the resulting rows' owning database file (which is gitignored and, in practice, not yet real). Stated explicitly.
- [x] **RBV4** — N/A — no governance record in scope; `label_definitions`/`label_specifications` are append-only registry rows, not governance receipts.

## 8. Technical Debt Assessment (TD)

| What | Why deferred | Closed by (WBS ID) | Blast radius if never closed |
|---|---|---|---|
| No `register_binding()` call or `activate_specification()` equivalence check for any of the four Specifications — all remain at phase `Registered`, never `Active` | This milestone's own exit criterion is Tier A registration only ("No forward data touched"); a real equivalence check would need to execute the real bindings against real corpus data | Not scheduled by name — a natural fit for whichever milestone first needs an `Active` Label Specification (possibly WBS 3.4, not decided here) | None today — nothing requires `Active` phase yet; §12.1 only gates *new dataset citation* on it, and no dataset layer exists |
| `binding_id` strings (e.g. `"forward_evidence.daily_job.close_trade"`) are declared identity only, not resolvable via `get_binding()` | Consistent with "no forward data touched" — resolving a binding means importing and calling real pipeline code | WBS 3.4+, if and when a real binding is needed | None today |

- [x] **TD3** — No placeholder work; every Specification field is a complete, source-verified transcription, not a stub.
- [x] **TD4** — Neither item is long-lived urgent debt; both are correctly-deferred, narrowly-scoped follow-ons. Not promoted to `CLAUDE.md` §19.

## 9. Definition of Done (DOD)

- [x] **D1** — Compiles/imports cleanly in both environments
- [x] **D2** — Full suite passes, zero new failures (1805/19, `btc_bot`; 14/14, base env)
- [x] **D3** — 14 new tests, all passing
- [x] **D4** — N/A, explicitly stated (§5)
- [x] **D5** — Zero imports from `research/` into any operational-plane module
- [x] **D6** — Rollback lever named (§7): delete the two new files
- [x] **D7** — No existing signature/table/column touched
- [x] **D8** — No `.env`, credential, or secret referenced
- [x] **D9** — Independently deployable — merging this alone changes no existing behaviour
- [x] **D10** — N/A for this milestone specifically (mandatory-field rejection is WBS 3.1's `registry.py` concern, already tested there); this milestone's own claim (idempotency, distinct hashes) is verified per TV5
- [x] **D11** — All sections completed, including explicit N/A statements for §4 and the applicable rows of §5/§6

## 10. Reviewer Checklist (RC)

*Signed — Rohith Suresh, 2026-08-07 (`LS-DEC-003`, Approval Package `A1`).*

- [x] **RC1** — Milestone corresponds to WBS 3.3 exactly, no scope creep into 3.4+
- [x] **RC2** — No file under `docs/architecture/` touched
- [x] **RC3** — "Files Changed" list matches `git status` exactly
- [x] **RC4** — At least two tests read in full, confirmed to exercise real behaviour
- [x] **RC5** — Full-suite counts reproduced independently in both environments
- [x] **RC6** — Rollback lever confirmed
- [x] **RC7** — Full diff read — should be 100% new files; confirm `registry.py`/`value_store.py` show no diff
- [x] **RC8** — Sign-off

  **Reviewer:** Rohith Suresh  **Date:** 2026-08-07

---

## Files Changed

| File | Type |
|---|---|
| `research/label_store/register_conventions.py` | New — registers `trade_forward_return` (C1/C2/C3, three Specifications, one shared Definition — Reconvention) and `spot_one_day_forward_return` (`ret_fwd1`, one Specification, its own Definition) |
| `tests/test_register_conventions.py` | New — 14 tests |

## Summary

Shipped WBS 3.3, the third Label Store milestone: `register_conventions.py`, a `migrate_*.py`-style script that registers the four Label Specifications this milestone exists to claim. Every convention field was transcribed from reading the real, current, unmodified source this session — `daily_job.py`'s fixed-HOLD_DAYS flat-cost long-only convention (C1), `engine.py`'s `_net_daily()` turnover-cost signed-position convention (C2), `runner_base.py`'s per-trade flat-cost ±1-direction convention (C3), and `ret_fwd1`'s zero-cost, no-position, no-direction raw spot differential — not copied from the roadmap's own summary table, which this session independently confirmed accurate. C1/C2/C3 share one Definition (`trade_forward_return`, Reconvention per §11.1); `ret_fwd1` gets its own (`spot_one_day_forward_return`), resolving `S-6`. Idempotency and the "three distinct hashes from otherwise-identical inputs" claim were both verified two ways: end-to-end (the real script, run twice, against a real fresh database) and in isolation (a unit test holding every field but fill/cost/direction constant). TV3 caught a non-mechanism sabotage that didn't actually break anything (informative, correctly not claimed as evidence) before finding and confirming the real one. A third instance of the `sys.modules` bare-import collision was anticipated and avoided by construction (subprocess invocation) rather than fixed after failing, since fixing it in-place would have meant touching a pre-existing, out-of-scope test file. Zero existing files left modified. Next: WBS 3.4 (wrapping the 148 real H6 forward labels) — **not begun**, per instruction.
