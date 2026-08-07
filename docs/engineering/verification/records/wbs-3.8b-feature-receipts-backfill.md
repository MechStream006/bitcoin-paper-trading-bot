# Milestone Verification Record

| Field | Value |
|---|---|
| **Milestone** | Mandatory Bootstrap Backfill of Phase 2's 17 `feature.register` Receipts — `research/evidence_library/migrate_bootstrap_feature_receipts.py` |
| **WBS item(s)** | 3.8b |
| **Roadmap phase** | Phase 3 — Label Store (`phase_3_label_store_roadmap.md` v1.1.0) |
| **Standard version referenced** | 1.0.0 |
| **Date** | 2026-08-06 |
| **Implementer** | Claude Sonnet 5 |
| **Reviewer** | Rohith Suresh — signed 2026-08-07 (`LS-DEC-003`) |

**WBS 3.4–3.8a semantics are unchanged, as instructed.** Every Label Store file (`registry.py`, `value_store.py`, `standing.py`, `superseded.py`, `propagation.py`, `label_receipts.py`) and every Feature Store file (`registry.py`, `value_store.py`, `conditioning.py`) show zero diff (`git diff --stat` confirms). `research/evidence_library/receipts.py` gains one more small, purely additive extension — a single new `DECISION_TAXONOMY` entry, `"feature.register": "derived"` — the same mechanism used at WBS 3.8a for `"label.register"`/`"label.adjudicate"` and at PR-7 for `"evaluate"`. `"feature"` (`SUBJECT_KINDS`) and `"register"` (`ACTIONS`) already existed (PR-1 and WBS 3.8a respectively) — no further taxonomy growth was needed.

**No architectural inconsistency with ADR-I011 was found.** One real implementation defect — introduced by this milestone's own first draft, not pre-existing — was found and fixed during verification (§0, §9). It is disclosed in full below, including the corrective action taken against the real local database.

**WBS 3.9 is explicitly not begun.**

---

## 0. Design Summary and a Disclosed, Corrected Defect (read before the checklist)

**Design.** `migrate_bootstrap_feature_receipts.py` mirrors `migrate_bootstrap_transition_receipts.py`'s established structure exactly (`plan()`/`apply()`/`print_table()`/`backup()`/`main()`, dry-run-by-default, `--commit`-gated, idempotent, timestamped backup before any write) and `evidence_governance.bootstrap_admission_receipt()`/`transition_governance.bootstrap_transition_receipt()`'s bootstrap-receipt shape exactly (`attestation="bootstrap"`, `authority.policy_id="bootstrap"`, no fabricated `evaluation[]`). No live `feature.register` emitter is built — the roadmap's own `I9` text scopes this milestone to "17 bootstrap receipts... additive; neither modifies `registry.py` or `value_store.py`", not a new capability.

**A real, disclosed defect, found and fixed before this record was written.** The first draft's `main()` called `receipt_ledger.init_db(db_path)` **unconditionally, before checking `--commit`** — mirroring `migrate_bootstrap_transition_receipts.py`'s own identical unconditional call. For `ledger.db`, that call is always a true no-op in practice (the `governance_receipts` table has existed there since PR-1). For `feature_store.db`, it was **not** a no-op: it was the *first-ever* creation of that table, and an early manual dry-run smoke-check against the real local `feature_store.db` (run without `--db`, before this file's own test suite existed to catch it) silently left a real, empty `governance_receipts` table on the real file — a genuine, if inert (zero rows), mutation during what was supposed to be a dry run.

**Found and corrected before proceeding:**
1. `plan()` was rewritten to be side-effect-free even at the schema level: a new `_governance_receipts_table_exists()` check (a plain `sqlite_master` read) lets `plan()` return "every Specification needs a receipt" when the table is absent, with zero attempt to query — let alone create — it.
2. `main()` now calls `receipt_ledger.init_db()` only immediately before the actual write, after the backup, and only when there is something to commit.
3. The real local `feature_store.db` was restored to its exact pre-contamination state: `DROP TABLE governance_receipts` (0 rows existed — a lossless, schema-only correction), verified by re-querying `sqlite_master` and confirming `feature_specifications`/`feature_definitions` row counts (17/17) were untouched throughout. A safety copy was taken before the correction and deleted only after the fix was independently re-verified.
4. `TestMainCLI::test_dry_run_writes_nothing` (this milestone's own test) now asserts `_governance_receipts_table_exists(db_path) is False` after a dry run, closing the gap that let the defect through undetected in the first draft's test suite.

**The real backfill was then executed for real**, per this milestone's own roadmap-stated exit criterion ("17 receipts written... `FS-GAP-004` closed in both stores") — a stronger requirement than WBS 3.8a's, whose own deliverable was a live emitter with no backfill mandate. `python migrate_bootstrap_feature_receipts.py --commit`, run directly against the real local `feature_store.db` (auto-backed-up first to `feature_store.pre-feature-receipts-migration.20260806T032611Z.db`), wrote 17 bootstrap receipts; a second `--commit` run reported 0 pending; `verify_chain()` reports `ok=True, n_receipts=17`.

## 1. Build Verification (BV)

- [x] **BV1** — `python -m py_compile research/evidence_library/migrate_bootstrap_feature_receipts.py research/evidence_library/receipts.py tests/test_migrate_bootstrap_feature_receipts.py` → clean in both environments.
- [x] **BV2** — Pure stdlib throughout (argparse/shutil/sqlite3/datetime/pathlib, plus `receipts.py`/`receipt_ledger.py`/`feature_store/registry.py`, none of which import pandas) — no environment gating anywhere in this file.
- [x] **BV3** — No new dependency.
- [x] **BV4** — Verified under both environments; identical pass counts.

## 2. Test Verification (TV)

- [x] **TV1** — `tests/test_migrate_bootstrap_feature_receipts.py` (new, 25 tests): Plan (4), Apply (8), No Mutation Of Source Tables (1), Main CLI (7), Real Feature Store Copy (6, real-file-gated and hash-guarded).
- [x] **TV2** — House style confirmed, mirroring `test_migrate_receipt_attestation_basis.py`'s own conventions closely (a synthetic tmp_path tier plus a real-data tier under the identical `_guard_real_*_untouched` autouse hash-comparison fixture pattern). **One correction made mid-verification** (§0): the real-data tier's tests originally asserted a "would write 17" / "pre-migration" premise; once this milestone's own real `--commit` execution changed the real file's actual state, those tests were rewritten to assert the real, POST-migration state instead (`test_real_feature_store_db_has_exactly_17_bootstrap_receipts`, `test_real_feature_store_db_chain_is_valid`, `test_real_receipts_replay_refuses_as_bootstrap`, `test_a_pre_migration_backup_was_taken`) — mirroring `test_migrate_receipt_attestation_basis.py`'s own already-migrated-real-ledger tier (`test_all_26_real_receipts_have_null_attestation_basis`, `test_chain_still_verifies_on_the_real_ledger`) rather than inventing a new pattern.
- [x] **TV3** — Empirically verified: replaced `_bootstrap_feature_receipt()`'s real verdict computation (`verdict_result = "rejected" if spec["phase"] == "Rejected" else "registered"`) with the constant `"registered"` (ignoring `spec["phase"]` entirely) — `test_rejected_phase_verdict_is_rejected` failed (`assert 'registered' == 'rejected'`), confirming the test genuinely exercises the phase-to-verdict mapping rather than being tautological. Restored; full 25-test file re-confirmed green in both environments, including against the real (now correctly migrated) local database.
- [x] **TV4** — Refusal/exclusion paths tested: `plan()` never touches (creates or queries) `governance_receipts` before it exists (`test_fresh_db_has_nothing_to_plan`, `test_dry_run_writes_nothing`); a `Rejected` Specification still receives a receipt, with the correct `"rejected"` verdict, not silently skipped (`test_rejected_phase_verdict_is_rejected`); a missing `--db` path is a clean error, not a crash (`test_missing_db_is_an_error`); an already-receipted Specification is never re-planned (`test_already_receipted_specification_is_not_planned`).
- [x] **TV5** — Property/invariant tests: **bootstrap honesty** (`test_replay_refuses_a_bootstrap_receipt` — `receipts.replay()`'s bootstrap check fires before `decision_class` is ever consulted, matching the module's own central claim; `test_verify_still_holds_on_a_bootstrap_receipt` — hash verification holds regardless); **no source-table mutation** (`test_feature_specifications_table_is_never_written_to`, plus the structural AST-free substring guard `test_module_issues_no_update_statement_against_feature_tables`); **idempotency** (`test_second_commit_is_idempotent_and_backs_up_nothing_new`, `test_idempotent_commit_against_a_copy_writes_nothing_new`); **chaining** (`test_multiple_receipts_are_chained`, and for real: `test_real_feature_store_db_chain_is_valid`); **the roadmap's own exit criterion, exercised literally against real data** (the real local `feature_store.db` now genuinely carries 17 bootstrap `feature.register` receipts, chain-valid, every one `attestation="bootstrap"` and `verdict="registered"` — not merely provable against a disposable copy, as WBS 3.8a's own DBV3 was scoped to, but actually executed, per this milestone's own stronger mandate).

## 3. Regression Verification (REG)

- [x] **REG1** — `btc_bot` env, `python -m pytest tests/ -q --ignore=tests/test_api.py`:
  - Before (after WBS 3.8a): **1965 passed, 19 skipped, 0 failed**
  - After (final, post-real-backfill, post-test-rewrite): **1990 passed, 19 skipped, 0 failed**
  - Delta: **+25 passed**, exactly matching the final test count.
  - Base env, full suite (`python -m pytest tests/ -q`): **1872 passed, 60 skipped, 0 failed** (before: 1847/60 — delta **+25**, zero skips from this file in either environment).
  - Maximal collision-risk combination (`test_label_store_label_receipts.py`, this file, plus all four other evidence-library governance-receipt test files, plus both Feature Store test files): **394/394 passed**.
- [x] **REG2** — Skip count unchanged in both environments.
- [x] **REG3** — No pre-existing flake recurred this run (the timestamp-tie flake investigated at WBS 3.8a did not reappear).
- [x] **REG4** — CI workflow unchanged.

## 4. Performance Verification (PERF)

**N/A** — 17 rows read, 17 rows written, once. Negligible.

## 5. Database Verification (DBV)

**Applicability: YES, AND EXECUTED FOR REAL** — unlike WBS 3.8a (whose own DBV3 was deliberately scoped to a disposable copy, since that milestone built a live emitter with no backfill mandate), this milestone's own roadmap-stated deliverable required the real backfill to actually run.

- [x] **DBV1** — Additive only: one new table (`governance_receipts`, PR-1's own unmodified schema) added to `feature_store.db`; zero schema change to `feature_specifications`/`feature_definitions`; zero row mutation to either.
- [x] **DBV2** — Dry-run/`--commit`-gated CLI, exactly as designed; the dry-run path was corrected mid-milestone to also be schema-write-free (§0).
- [x] **DBV3** — Executed for real against the real local `feature_store.db`: 17 bootstrap receipts written, verified via a direct post-write query (`chain ok: True, n_receipts: 17`, all `attestation="bootstrap"`, all `verdict="registered"`, `decision_types={'feature.register'}`, `subject_kinds={'feature'}`) and independently re-confirmed by this milestone's own permanent regression tests (`TestRealFeatureStoreCopy`). A second `--commit` run against the same real file reported **0 Feature Specifications need a bootstrap receipt** — the idempotent re-run the roadmap's own exit criterion names.
- [x] **DBV4** — N/A — no governance ledger *other than* `feature_store.db`'s own new `governance_receipts` table is touched.
- [x] **DBV5** — A timestamped backup (`feature_store.pre-feature-receipts-migration.20260806T032611Z.db`) was taken automatically, immediately before the real write, confirmed present by this milestone's own test (`test_a_pre_migration_backup_was_taken`).
- [x] **DBV6** — A separate, one-time **corrective** backup/restore was also performed this milestone (§0, item 3) — for the accidental empty-table creation, not for the real backfill itself — verified by direct `sqlite_master`/row-count inspection before deleting the corrective backup, once the fix was independently confirmed via the full re-run test suite.

## 6. Backward Compatibility (BC)

- [x] **BC1–BC4** — `registry.py`/`value_store.py` (both stores), `standing.py`, `superseded.py`, `propagation.py`, `conditioning.py`, `label_receipts.py`, `receipt_ledger.py`, `evidence_governance.py`, `transition_governance.py`, `contract_receipts.py`, `record.py`, `lifecycle.py` — **zero diff to any of them**. `receipts.py`'s diff is exactly one additional additive `DECISION_TAXONOMY` entry (confirmed by reading the diff directly) plus the same pre-existing, pre-session `ADR-I015` hunks already disclosed at WBS 3.8a's own record.
- [x] **BC5** — Confirmed by direct search: `grep -rl "migrate_bootstrap_feature_receipts\|_governance_receipts_table_exists" --include="*.py" .` returns only this milestone's own two new files; `grep -rl "feature\.register" --include="*.py" .` returns exactly this milestone's two new files, `receipts.py`, and one pre-existing prose mention in `label_receipts.py`'s own WBS 3.8a docstring (unchanged, not new).

## 7. Rollback Verification (RBV)

- [x] **RBV1** — Delete `research/evidence_library/migrate_bootstrap_feature_receipts.py` and `tests/test_migrate_bootstrap_feature_receipts.py`; revert the one-line additive `DECISION_TAXONOMY` entry in `receipts.py`. `governance_receipts` is append-only by construction (PR-1) — the 17 real receipts already written are a **permanent, correct record** of a real registration fact and are not, and should not be, deleted by a code rollback; a future re-run of this script (if ever reinstated) would find them already present and correctly report 0 pending.
- [x] **RBV2** — The one real production-shaped write this milestone made (17 bootstrap receipts to the real local `feature_store.db`) is additive, backed up automatically beforehand, and independently verified. The one *unintended* write (the empty-table defect) was fully corrected and verified reverted before this write occurred.
- [x] **RBV3** — Exercised directly: TV3's sabotage/revert cycle.
- [x] **RBV4** — N/A — no governance ledger *beyond* the receipt substrate itself (already covered by RBV1) is in scope.

## 8. Technical Debt Assessment (TD)

| What | Why deferred | Closed by (WBS ID) | Blast radius if never closed |
|---|---|---|---|
| `migrate_bootstrap_transition_receipts.py` (PR-8) still calls `evidence_governance.init_db()` unconditionally before checking `--commit` — the exact pattern this milestone's own first draft copied and had to correct | `ledger.db` already has a populated `governance_receipts` table in every real and realistic test scenario, so the call has never yet been observed to create a first-time table there; fixing a working, already-shipped PR-8 script is "unrelated refactoring" of prior, unrelated work, out of this milestone's scope | Not scheduled — would need its own, separately-authorized engineering pass | Low today (the call is a true no-op in every known real scenario for `ledger.db`); would only manifest identically to this milestone's own defect if `migrate_bootstrap_transition_receipts.py` were ever pointed, via `--db`, at a genuinely fresh database with no prior receipts |

- [x] **TD3** — No placeholder work; the real backfill was executed for real against the real local database, not merely proven capable against a copy.
- [x] **TD4** — The one item above is a narrow, low-probability latent pattern in a *different*, already-shipped milestone's script, named and scoped rather than silently fixed or silently ignored — not urgent, unscoped debt of this milestone's own making.

## 9. Definition of Done (DOD)

- [x] **D1** — Compiles/imports cleanly in both environments
- [x] **D2** — Full suite passes, zero new failures (1990/19 `btc_bot`; 1872/60 base env)
- [x] **D3** — 25 new tests, all passing in both environments
- [x] **D4** — N/A, explicitly stated (§5 — real writes occurred and are fully verified, not merely gated)
- [x] **D5** — Zero new imports from `research/` into any operational-plane module
- [x] **D6** — Rollback lever named (§7)
- [x] **D7** — No existing signature, table, or column touched (one new additive `DECISION_TAXONOMY` row; `governance_receipts` itself is PR-1's own unmodified schema, newly instantiated on `feature_store.db`)
- [x] **D8** — No `.env`, credential, or secret touched, referenced, or required
- [x] **D9** — Independently deployable — this migration's own re-run is idempotent and safe regardless of what else has changed
- [x] **D10** — N/A for this milestone specifically
- [x] **D11** — All sections completed, including explicit N/A statements and the full, undiluted disclosure of the defect found and corrected in §0

## 10. Reviewer Checklist (RC)

*Signed — Rohith Suresh, 2026-08-07 (`LS-DEC-003`, Approval Package `A1`).*

- [x] **RC1** — Milestone corresponds to WBS 3.8b exactly, no scope creep into 3.9
- [x] **RC2** — No file under `docs/architecture/` touched
- [x] **RC3** — "Files Changed" list matches `git status` exactly
- [x] **RC4** — §0's disclosed defect and its correction read in full and understood
- [x] **RC5** — Full-suite counts reproduced independently in both environments
- [x] **RC6** — Rollback lever confirmed
- [x] **RC7** — Full diff read — confirm every WBS 3.1–3.8a file (both stores) shows no diff, and `receipts.py`'s diff is exactly one additive `DECISION_TAXONOMY` row plus the pre-existing, pre-session hunks
- [x] **RC8** — Real local `feature_store.db`'s 17 bootstrap receipts independently spot-checked
- [x] **RC9** — Sign-off

  **Reviewer:** Rohith Suresh  **Date:** 2026-08-07

---

## Files Changed

| File | Type |
|---|---|
| `research/evidence_library/migrate_bootstrap_feature_receipts.py` | New — one-time bootstrap backfill script |
| `tests/test_migrate_bootstrap_feature_receipts.py` | New — 25 tests |
| `research/evidence_library/receipts.py` | Modified — purely additive: one new `DECISION_TAXONOMY` entry, `"feature.register": "derived"` |
| `research/feature_store/feature_store.db` | Real data — 17 bootstrap `feature.register` receipts written (gitignored, not tracked by git); a corrective drop/restore also occurred mid-milestone (§0) |

## Summary

Shipped WBS 3.8b: the mandatory bootstrap backfill of Phase 2's 17 `feature.register` receipts, closing `FS-GAP-004` for real — not merely provably, as WBS 3.8a's own live-emitter deliverable was scoped to, but executed directly against the real local `feature_store.db`, per this milestone's own stronger roadmap-stated exit criterion. The migration mirrors the codebase's own three prior bootstrap-migration precedents (`migrate_bootstrap_transitions.py`, `migrate_bootstrap_evidence_admission.py`, `migrate_bootstrap_transition_receipts.py`) exactly in shape and in "bootstrap honesty" discipline: every receipt is `attestation="bootstrap"`, carries no fabricated `evaluation[]`, and correctly refuses replay while `verify()` holds forever. `receipts.py` gained exactly one additional additive taxonomy row; `"feature"` and `"register"` already existed from PR-1 and WBS 3.8a respectively. A real defect in this milestone's own first draft — an unconditional schema-creating call reachable from the dry-run path, harmless for `ledger.db` (where the table already exists) but a genuine first-time mutation for `feature_store.db` — was found via an accidental early smoke-check against the real local file, disclosed in full (§0), fixed at the source (`plan()` is now schema-write-free; `main()` defers `init_db()` until immediately before an actual commit), and the real file was restored to its exact pre-contamination state (a lossless, zero-row schema-only `DROP TABLE`, independently verified) before the real backfill was then executed correctly. The real local `feature_store.db` now carries 17 chain-valid, bootstrap-attested `feature.register` receipts; a second `--commit` run confirms 0 pending. TV3's sabotage/revert cycle confirmed the phase-to-verdict mapping is genuinely tested, not tautological. No architectural inconsistency with ADR-I011 was found. Zero changes to any WBS 3.1–3.8a file in either store. Next: WBS 3.9 — **not begun**, per instruction.
