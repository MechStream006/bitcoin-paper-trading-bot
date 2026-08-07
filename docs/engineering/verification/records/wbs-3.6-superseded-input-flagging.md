# Milestone Verification Record

| Field | Value |
|---|---|
| **Milestone** | Superseded-Input Flagging — `research/label_store/superseded.py` |
| **WBS item(s)** | 3.6 |
| **Roadmap phase** | Phase 3 — Label Store (`phase_3_label_store_roadmap.md` v1.1.0) |
| **Standard version referenced** | 1.0.0 |
| **Date** | 2026-08-06 |
| **Implementer** | Claude Sonnet 5 |
| **Reviewer** | Rohith Suresh — signed 2026-08-07 (`LS-DEC-003`) |

**WBS 3.4 and WBS 3.5 migration semantics are unchanged, as instructed.** `migrate_h6_forward_labels.py`, `standing.py`, `registry.py`, and `register_conventions.py` are all untouched (`git status` confirms no diff to any of them). **`value_store.py` is also untouched this milestone** — unlike WBS 3.5, this milestone required zero schema changes, because `flag_forward_label()` and `adjudicate_forward_label()` already existed, fully built, at WBS 3.2. This milestone builds the **detection layer only**: `find_affected_labels()` decides which labels are affected; the already-existing, unmodified transition functions do the actual flagging/adjudicating. **WBS 3.7 is explicitly not begun.**

**No architectural inconsistency with ADR-I011 was found.**

---

## 1. Build Verification (BV)

- [x] **BV1** — `python -m py_compile research/label_store/superseded.py tests/test_label_store_superseded.py` → `COMPILE_OK` in both environments.
- [x] **BV2** — `raw_lake.py`'s module-level `import pandas as pd` (not function-local, unlike `conditioning.py`'s equivalent situation) is deferred to `_import_raw_lake()`, a function-local import — `import superseded` itself succeeds in the base env with zero pandas dependency (verified directly: `python -c "import superseded"` in the base env, no error). Calling `find_affected_labels()`/`flag_affected_labels()` does require pandas transitively (§2 TV2 below), which is a real, structural fact about `raw_lake.py`'s own design, not a defect introduced here.
- [x] **BV3** — No new dependency (pandas/pyarrow were already required transitively by Phase 1's `raw_lake.py`, not newly introduced).
- [x] **BV4** — Verified under both environments; base-env behaviour differs correctly by test class (§2).

## 2. Test Verification (TV)

- [x] **TV1** — `tests/test_label_store_superseded.py` (new, 23 tests): Is Structured Reference (4), Find Affected Labels (7), Flag Affected Labels (4), Neither Rewritten Nor Trusted (3), Adjudication Lifecycle (1) + Adjudication Never Automated (1), No Datetime Now (1), Real `raw_lake.py` Integration (2).
- [x] **TV2** — House style confirmed, **with one finding corrected before it caused a false pass**: the first draft mirrored `tests/test_conditioning.py`'s exact split (gate only the "real Parquet write" tests) and ran clean in `btc_bot`, but failed 15/23 in the base env with `ModuleNotFoundError: No module named 'pandas'` raised from *inside* `raw_lake.py` itself — even the hand-constructed-fixture detection tests, which need none of `raw_lake.py`'s pandas-dependent functions, failed, because `raw_lake.py` imports pandas at its own module top level (unlike `conditioning.py`, which defers pandas-dependent imports to function scope). Corrected: gating is class-level on "does this test call into `raw_lake.py` at all" (five classes), not on "does this test write real Parquet" (only `TestRealRawLakeIntegration`) — documented explicitly in the test file's own docstring as a finding, not silently worked around. Re-run: base env **6 passed, 17 skipped, 0 failed**; `btc_bot` (has pandas) **23/23 passed**.
- [x] **TV3** — Empirically verified: replaced the core detection condition's `entry["hash"] != latest_hash` with `False` — `test_correction_finds_exactly_the_stale_citing_label` failed (`assert 0 == 1`), confirming the test is not tautological. Restored; full 23-test file re-confirmed green in `btc_bot`, and the base-env split (6/17) re-confirmed unaffected.
- [x] **TV4** — Refusal/exclusion paths tested: an unstructured revision entry (WBS 3.4's own real placeholder string, verbatim) is never matched (`test_unstructured_revision_set_is_never_matched`); a non-Matured (`Declared`-only) label is never considered (`test_only_matured_labels_are_considered`); an already-Flagged label is not rediscovered by a second detection pass (`test_already_flagged_label_is_not_rediscovered`); an unrelated partition is not affected (`test_unrelated_partition_is_not_affected`); the direct-SQL fill-once guard still holds after flagging (`test_direct_sql_still_cannot_rewrite_outcome_after_flagging`).
- [x] **TV5** — Property/invariant tests: **idempotency** (`test_second_commit_run_is_idempotent`, and structurally guaranteed by the `WHERE maturity_state = 'Matured'` filter naturally excluding already-flagged rows — documented in the module's own docstring, not just asserted); **"neither rewritten"** (`test_flagging_does_not_change_outcome_or_matured_at`, re-confirming WBS 3.2's own trigger holds in this milestone's real usage path); **"nor trusted"** (`test_flagged_label_is_excluded_from_a_matured_only_query` — proven using only the pre-existing `maturity_state` column, no new admissibility machinery); **adjudication is human-gated, never automatic** (`test_superseded_module_never_calls_adjudicate_forward_label`, AST-based, mirroring the WBS 3.4/3.5 structural-guard pattern); **the roadmap's own entry criterion, exercised literally** (`TestRealRawLakeIntegration` uses `raw_lake.land_raw()` for real, genuine Parquet writes, in a disposable `tmp_path` location — "a disposable copy of a revisioned partition ... to simulate a correction against").

## 3. Regression Verification (REG)

- [x] **REG1** — `btc_bot` env, `python -m pytest tests/ -q --ignore=tests/test_api.py`:
  - Before (after WBS 3.5): **1863 passed, 19 skipped, 0 failed**
  - After: **1886 passed, 19 skipped, 0 failed**
  - Delta: **+23 passed**, exactly matching the new test count.
  - Base env (`tests/test_label_store_superseded.py` alone): **6 passed, 17 skipped, 0 failed** — correct, not a regression (§2 TV2).
  - WBS 3.2 + 3.4 + 3.5 + 3.6 together (backward-compatibility check): **120/120 passed**.
  - Maximal collision-risk combination (both Feature Store test files + all five Label Store test files): **226/226 passed**.
- [x] **REG2** — Skip count unchanged in the full suite (19→19); the base-env skip count *within this one file* (17) is new and expected, explained in §2, not a silent or unexplained change.
- [x] **REG3** — No known pre-existing flake recurred.
- [x] **REG4** — CI workflow unchanged.

## 4. Performance Verification (PERF)

**N/A** — a handful of SQL rows and JSON-parsed revision-set entries per detection call; no corpus-scale loop.

## 5. Database Verification (DBV)

**Applicability: YES** — this milestone's real execution can write to `label_value_forward` (via the already-existing `flag_forward_label()`), though the real local database was not, in fact, affected (§ below explains why).

- [x] **DBV1** — Additive only in principle (a `Matured -> Flagged` transition, already guarded since WBS 3.2); no schema change of any kind this milestone.
- [x] **DBV2** — `superseded.py` is dry-run/`--commit`-gated, mirroring the established pattern.
- [x] **DBV3** — Verified via the real test suite's `TestRealRawLakeIntegration` (genuine `land_raw()` writes, genuine correction, genuine flag) and, for real, via a direct CLI invocation against the actual local `research/label_store/label_store.db`: `python superseded.py --dataset dvol --symbol BTC --year 2026 --month 6 --flagged-at ... --base-dir research/data` reported **`0 label(s) cite a superseded revision of this partition`** — correct and expected, since no real `raw_lake.py` partition exists anywhere on this machine yet (`_raw_lake/` has never been created locally — WBS 1.2/1.3's dvol pilot has never landed real data here) and, independently, none of the 148 real H6 labels could ever match regardless (they carry WBS 3.4's placeholder string, not a structured reference, by design). `research/label_store/label_store.db`'s content was unaffected by this dry-run invocation.
- [x] **DBV4** — N/A — no governance ledger touched.
- [x] **DBV5** — N/A — no real forward-tier data was actually flagged this milestone (the only real invocation was a dry run that found nothing to flag); WBS 3.10's backup coverage remains live regardless.
- [x] **DBV6** — N/A — unchanged from prior findings.

## 6. Backward Compatibility (BC)

- [x] **BC1–BC4** — N/A in the strongest sense available to any Label Store milestone so far: **zero existing functions, tables, columns, or files were touched at all** — not even `value_store.py`. `git status` confirms only new, untracked files.
- [x] **BC5** — Confirmed by direct search: `grep -rl "find_affected_labels\|flag_affected_labels\|STRUCTURED_KEYS\|_is_structured_reference" --include="*.py" .` returns only this milestone's own two new files.

## 7. Rollback Verification (RBV)

- [x] **RBV1** — Delete `research/label_store/superseded.py` and `tests/test_label_store_superseded.py`. Nothing else references them; `value_store.py`'s `flag_forward_label()`/`adjudicate_forward_label()` (WBS 3.2) remain fully functional and independently usable without this module.
- [x] **RBV2** — No production data touched; the only real database this milestone's real CLI invocation touched was the local, gitignored `label_store.db`, and only as a dry run (zero writes).
- [x] **RBV3** — Exercised directly: TV3's sabotage/revert cycle.
- [x] **RBV4** — N/A — no governance record in scope.

## 8. Technical Debt Assessment (TD)

| What | Why deferred | Closed by (WBS ID) | Blast radius if never closed |
|---|---|---|---|
| No real forward-tier label anywhere in this repository actually uses the new structured revision-reference format yet | WBS 3.4's 148 real labels predate this format by design (their own migration's docstring named WBS 3.6 as the milestone that would define it, not retroactively apply it); no other real forward-tier declaration exists yet | Whichever future milestone first declares real forward-tier data with real `raw_lake.py`-backed inputs | None today — the format and detection logic are both real and tested against a genuine `raw_lake.py` partition (`TestRealRawLakeIntegration`), just not yet exercised by any real production-shaped label |
| No dedicated admissibility-exclusion flag/column for Flagged labels — "not trusted" is satisfied only by `maturity_state != 'Matured'` | Building dedicated admissibility machinery is explicitly WBS 3.7's own scope (conditioning + admissibility ceiling), not this milestone's | WBS 3.7 | None today — the existing property is sufficient and correctly tested; WBS 3.7 will incorporate `Flagged` status into its own ceiling computation, not invent a parallel mechanism |
| No re-flagging path for an already-Adjudicated label affected by a second, later correction | `ADR-I011`'s own five-arc state diagram draws no such arc; adding one would be exactly the "architectural redesign" this milestone is instructed to avoid | Not scheduled — would require an ADR-level decision, not an engineering one | Low today — no real data has ever reached `Adjudicated` state; a future occurrence would need a deliberate governance decision, correctly outside this milestone's authority |

- [x] **TD3** — No placeholder work; the detection and flagging logic was exercised for real, including genuine `raw_lake.py` Parquet writes.
- [x] **TD4** — None of the above is unscoped or long-lived urgent debt; all are named, already-scoped future dependencies or deliberate, ADR-respecting scope boundaries.

## 9. Definition of Done (DOD)

- [x] **D1** — Compiles/imports cleanly in both environments
- [x] **D2** — Full suite passes, zero new failures (1886/19, `btc_bot`; 6 passed/17 skipped, base env for this file)
- [x] **D3** — 23 new tests, all passing (in the environment where they can run)
- [x] **D4** — N/A, explicitly stated (§5, except DBV3 which was genuinely exercised)
- [x] **D5** — Zero new imports from `research/` into any operational-plane module
- [x] **D6** — Rollback lever named (§7)
- [x] **D7** — No existing signature, table, or column touched at all
- [x] **D8** — No `.env`, credential, or secret touched, referenced, or required
- [x] **D9** — Independently deployable — merging this alone changes no other module's behaviour (zero files modified besides new ones)
- [x] **D10** — N/A for this milestone specifically (no new mandatory schema field; `_is_structured_reference()` is a permissive recognition check, not a Tier-A-style rejection gate — an unstructured entry is silently skipped, not refused, since WBS 3.4's legitimate placeholder strings must coexist peacefully)
- [x] **D11** — All sections completed, including explicit N/A statements

## 10. Reviewer Checklist (RC)

*Signed — Rohith Suresh, 2026-08-07 (`LS-DEC-003`, Approval Package `A1`).*

- [x] **RC1** — Milestone corresponds to WBS 3.6 exactly, no scope creep into 3.7
- [x] **RC2** — No file under `docs/architecture/` touched
- [x] **RC3** — "Files Changed" list matches `git status` exactly
- [x] **RC4** — At least two tests read in full, confirmed to exercise real behaviour
- [x] **RC5** — Full-suite counts reproduced independently in both environments
- [x] **RC6** — Rollback lever confirmed
- [x] **RC7** — Full diff read — confirm `value_store.py`, `registry.py`, `migrate_h6_forward_labels.py`, `standing.py`, `register_conventions.py` all show no diff
- [x] **RC8** — Sign-off

  **Reviewer:** Rohith Suresh  **Date:** 2026-08-07

---

## Files Changed

| File | Type |
|---|---|
| `research/label_store/superseded.py` | New — corrected-revision detection + flagging CLI |
| `tests/test_label_store_superseded.py` | New — 23 tests |

## Summary

Shipped WBS 3.6: superseded-input flagging, detecting when a Forward Label's cited `raw_lake.py` corpus revision has been corrected and flagging exactly the affected labels — using the maturity-state machinery WBS 3.2 already built and left unchanged. A real, genuine test of the roadmap's own entry criterion ("a disposable copy of a revisioned partition ... to simulate a correction against") landed two real Parquet revisions via `raw_lake.land_raw()` in a disposable location and proved detection and flagging both work end to end. "Neither rewritten" is re-verified, not rebuilt, against WBS 3.2's own trigger; "nor trusted" is satisfied using only the pre-existing `maturity_state` column, deliberately not anticipating WBS 3.7's own admissibility scope. WBS 3.4's 148 real H6 labels are confirmed, both by design and by direct test, to never match this mechanism — they predate real revisioning and carry a placeholder string, exactly as that migration's own record disclosed it would. One real finding was caught and corrected before it caused a false pass: `raw_lake.py` imports pandas at module level (unlike `conditioning.py`), so the base-env test gating had to be class-level on "touches `raw_lake.py` at all," not just "writes real Parquet" — documented, not silently worked around. Zero changes to `value_store.py`, `registry.py`, `migrate_h6_forward_labels.py`, `standing.py`, or `register_conventions.py`. Next: WBS 3.7 (conditioning + admissibility) — **not begun**, per instruction.
