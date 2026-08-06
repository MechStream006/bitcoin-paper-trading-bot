# Milestone Verification Record

| Field | Value |
|---|---|
| **Milestone** | Bootstrap Backfill of the Label Store's 8 `label.register` Receipts — `research/label_store/migrate_bootstrap_label_receipts.py` |
| **WBS item(s)** | 3.8c |
| **Roadmap phase** | Phase 3 — Label Store (`phase_3_label_store_roadmap.md` v1.1.0) |
| **Standard version referenced** | 1.0.0 |
| **Date** | 2026-08-06 |
| **Implementer** | Claude Opus 5 |
| **Reviewer** | *(pending — Rohith)* |
| **Closes** | `LS-GAP-001` · `ADR-I011` LG1 and §9 ("registration receipt" as Specification metadata) |

**WBS 3.1–3.10 semantics are unchanged, as instructed.** No Label Store module (`registry.py`, `value_store.py`, `standing.py`, `superseded.py`, `propagation.py`, `label_receipts.py`, `set_hygiene.py`, `register_conventions.py`, `migrate_h6_forward_labels.py`) was modified. No Feature Store module was modified. **`research/evidence_library/receipts.py` was NOT modified** — `label.register`, subject kind `label`, and action `register` all already existed from WBS 3.8a, so this milestone needed zero taxonomy growth (the first Label Store milestone since 3.7 to need none).

**Activation logic is untouched.** `registry.activate_specification()` is neither called nor modified; a structural test (`test_module_never_references_activation`) asserts the migration's own source contains no reference to it or to `_transition_phase`. All 8 real Specifications remain at phase `Registered`. **WBS 3.11 is explicitly not begun.**

**No architectural inconsistency with `ADR-I011` was found.**

---

## 0. Design Summary (read before the checklist)

**What this milestone does.** Writes one bootstrap-attested `label.register` governance receipt for each of the 8 real Label Specifications that predate WBS 3.8a's receipt emitter — the mirror of what WBS 3.8b did for the Feature Store's 17. It closes the asymmetry the Phase 3 Closeout identified: features carried all 17 receipts while labels carried none, which is precisely the ambiguity scope transfer `S-3` exists to prevent, pointing the other way.

**Genuine infrastructure reuse, not shape-copying.** WBS 3.8b had to mirror `label_receipts.py`'s lookup *by shape*, because its subject kind and decision type differed. This milestone has neither problem: it imports `label_receipts.py` and uses `lr.DECISION_REGISTER`, `lr.SUBJECT_KIND`, `lr.get_registration_receipt()`, and `lr.init_db()` directly. There is exactly one definition of what a `label.register` receipt is, and this script consumes it.

**Why no `evaluation[]` is fabricated.** `label_receipts._build_registration_body()` records a real Tier A `evaluation[]` because it *observes the check as it happens*. Re-running Tier A today over the stored Specification content would produce a verdict, but recording it as the original registration's evaluation would be a lie with a valid hash — the receipt would claim to attest a 2026-08-05 decision while actually carrying a 2026-08-06 recomputation. The bootstrap attestation states the honest thing instead: the registration fact and outcome are attested; the computation is not reproducible. `verify()` holds forever; `replay()` refuses. This follows all four established precedents.

**WBS 3.8b's defect was treated as a known hazard, not a surprise.** 3.8b's first draft silently created an empty `governance_receipts` table on a real database during a documented dry run. `label_store.db` was the *identical* exposure — it had no such table either. That failure mode was therefore designed against from the first draft (`_governance_receipts_table_exists()`, `init_db()` gated behind `--commit` *and* placed after the backup) and is now **regression-tested** (`TestDryRunWritesNothing`, 3 tests) rather than merely avoided by inspection. It was also used as this milestone's primary TV3 sabotage target (§2).

**One safety property added beyond 3.8b:** `_assert_label_store_schema()` refuses to write to any database lacking `label_specifications`/`label_definitions`, per roadmap risk `LR4` (*"no default `--db` in any Phase 3 script; assert expected schema shape before any write; refuse on mismatch"*). 3.8b had no such guard.

## 1. Build Verification (BV)

- [x] **BV1** — `python -m py_compile research/label_store/migrate_bootstrap_label_receipts.py tests/test_migrate_bootstrap_label_receipts.py` → clean in both environments.
- [x] **BV2** — Pure stdlib (argparse/importlib/shutil/sqlite3/datetime/pathlib) plus `label_receipts.py`, `receipts.py`, `receipt_ledger.py`, `registry.py` — none of which import pandas. No environment gating needed; the file imports and runs identically in the base env.
- [x] **BV3** — No new dependency.
- [x] **BV4** — Verified under both environments; identical pass counts (40/40).
- [x] **BV5** — Collision-safe sibling loading: `_load_sibling()` checks `sys.modules` first, matching `migrate_h6_forward_labels.py`/`label_receipts.py`/`set_hygiene.py` exactly, so the `LS-TD-005` class of test-isolation fragility is not extended by this file.

## 2. Test Verification (TV)

- [x] **TV1** — `tests/test_migrate_bootstrap_label_receipts.py` (new, **40 tests**): Plan (5), Apply (7), Dry-Run-Writes-Nothing (3), Schema Assertion (2), No Mutation Of Label Store Tables (3), Idempotency (2), Chain And Replay (3), Governance Validation (2), Main CLI (4), Real Label Store (9, real-file-gated and hash-guarded).
- [x] **TV2** — House style confirmed (`sys.path` insertion, guarded `_load()`, `class TestXxx:`, plain `assert`, no `unittest.TestCase`), mirroring `test_migrate_bootstrap_feature_receipts.py`'s two-tier structure and its `_guard_real_*_untouched` autouse hash-comparison fixture.
  **Two test-construction errors of my own were found and fixed during verification, both incorrect API assumptions, neither a script defect:** (a) `receipts.verify(...)` does not exist — the real API is `receipts.verify_receipt(body, receipt_id) -> bool`; (b) `replay_registration_receipt()` **does not raise** on a bootstrap receipt — `receipts.replay()` returns `ReplayResult(ok=False, mode="refused", detail=...)`. The corrected tests now assert the real contract, which is stronger and more precise: `hash_ok is True` **and** `mode == "refused"` — verify holds, replay refuses, exactly as the bootstrap design intends.
- [x] **TV3** — **Two sabotages, both empirically confirmed, both reverted.**
  1. **Primary (this milestone's central safety property):** moved `_infra()[2].init_db(db_path)` to before the `--commit` check in `main()`, reintroducing WBS 3.8b's exact historical defect. **5 tests failed** — all three `TestDryRunWritesNothing` tests plus `TestMainCLI::test_commit_on_empty_database_returns_0_and_creates_nothing` and `test_dry_run_is_the_default`. Reverted.
  2. **Secondary (verdict derivation):** replaced `verdict_result = "rejected" if spec["phase"] == "Rejected" else "registered"` with the constant `"registered"`. `test_rejected_phase_yields_rejected_verdict` failed (`- rejected / + registered`). Reverted.
  Full 40-test file re-confirmed green after both reverts.
- [x] **TV4** — Refusal paths, one negative test each: a non-Label-Store database is refused before any write (`test_refuses_a_database_without_label_tables`, matching `LR4`); a missing `--db` path returns 1 rather than crashing (`test_missing_database_returns_1`); an already-receipted Specification is never re-planned (`test_skips_specifications_that_already_have_a_receipt`); a dry run refuses to write even schema (`TestDryRunWritesNothing`, 3 tests); a bootstrap receipt refuses replay (`test_replay_refuses_a_bootstrap_receipt`).
- [x] **TV5** — Property/invariant tests: **bootstrap honesty** (`test_receipt_is_bootstrap_attested`, `test_no_fabricated_evaluation_is_recorded` — asserts both the empty `evaluation[]` and `rule.rule_id == "bootstrap"`); **verify-holds/replay-refuses** (`test_every_receipt_verifies_by_hash`, `test_replay_refuses_a_bootstrap_receipt`); **taxonomy reuse, not redefinition** (`test_receipt_uses_the_reused_label_register_taxonomy` asserts against `lr.DECISION_REGISTER`/`lr.SUBJECT_KIND`, so a future divergence between this script and `label_receipts.py` fails the test); **source tables never mutated** (`test_specifications_and_definitions_are_unchanged` compares full row dumps before/after); **no activation** (`test_no_specification_phase_is_changed`, `test_module_never_references_activation`, `test_real_specification_phases_are_untouched_by_this_milestone`); **idempotency** (`test_second_commit_writes_nothing_new`, `test_second_commit_takes_no_backup`); **chaining** (`test_chain_is_valid_after_backfill`); **the requirement itself** (`test_lrec1_is_reported_before_the_backfill` → 2 errors, `test_lrec1_is_cleared_after_the_backfill` → `[]`).

## 3. Regression Verification (REG)

- [x] **REG1** — `btc_bot` env, `python -m pytest tests/ research/ -q --ignore=tests/test_api.py`:
  - Before (Phase 3 Closeout baseline, measured this session): **2215 passed, 19 skipped, 0 failed**
  - After: **2255 passed, 19 skipped, 0 failed**
  - Delta: **+40 passed**, exactly matching this file's test count.
  - Base env, `python -m pytest tests/ -q --ignore=tests/test_api.py`: before **1836 passed, 60 skipped, 0 failed**; after **1876 passed, 60 skipped, 0 failed** — delta **+40**, zero skips contributed by this file in either environment.
- [x] **REG2** — Skip count unchanged in both environments (19 / 60).
- [x] **REG3** — No pre-existing flake recurred. The `engine`-module-collision failure (`INC-0003`) did not appear in either full run.
- [x] **REG4** — CI workflow unchanged.
- [x] **REG5** — **Two pre-existing tests required updating — this is the milestone's intended consequence, not a regression.** Both hardcoded the *pre-3.8c* state as a fixed expectation:
  - `test_label_store_label_receipts.py::TestRealLabelStoreIntegration` — asserted `len(lrec1_errors) == 8`. Renamed to `test_lrec1_is_clear_for_all_real_specifications_after_the_3_8c_backfill` and now asserts `lrec1_errors == []`. The rename is deliberate: the old name (`..._honestly_reports_all_real_pre_existing_specifications`) asserted a condition that is no longer true, and leaving it would have made a passing test lie about what it checks.
  - `test_label_store_set_hygiene.py::TestEndToEndLabelStoreIntegration::test_real_data_end_to_end_snapshot` — one assertion changed from `== 8  # the known, disclosed WBS 3.8a gap` to `== []  # closed by WBS 3.8c`.
  Nothing else in either file was touched. WBS 3.8a's record framed that gap as *"visible and named, not silently closed or silently ignored"*; it has now been closed by an authorised milestone, and the tests say so.

## 4. Performance Verification (PERF)

- [x] **PERF1** — **N/A, explicitly.** A one-time migration writing 8 rows. Measured wall time for the real `--commit`, including backup and integrity checks: under one second. No hot path, no runtime consumer, no recurring cost.

## 5. Database Verification (DBV)

- [x] **DBV1** — Additive only: one new table (`governance_receipts`, created by `receipt_ledger.init_db()`'s unmodified schema) and 8 rows in it. Zero schema change to `label_specifications`, `label_definitions`, `label_value_forward`, `label_value_research`.
- [x] **DBV2** — Dry-run default, `--commit`-gated. **The dry run was proven byte-safe against the real file before any commit**: SHA-256 `dafd4e09…` identical before and after, and `governance_receipts` confirmed absent from `sqlite_master` afterwards.
- [x] **DBV3** — **Full rehearsal against a disposable copy of the real database before touching it**: 8 receipts written, `verify_chain` ok/8, `LREC1` and `LREC2` both empty, spec phases unchanged (`Registered` × 8), 148 forward rows intact, second `--commit` byte-identical no-op with exactly 1 backup, replay `hash_ok=True mode=refused`. Real file confirmed unchanged throughout the rehearsal.
- [x] **DBV4** — **Real execution.** `PRAGMA integrity_check` = `ok` before **and** after. Pre-counts `specs=8 defs=2 fwd=148 phases=[('Registered',8)]` — post-counts **identical**. 8 receipts written. Auto-backup taken: `label_store.pre-label-receipts-migration.20260806T055612Z.db`.
- [x] **DBV5** — Idempotency proven on real data (via the real file's own post-state) and on copies: a second `--commit` reports `0 Label Specifications need a bootstrap receipt`, writes nothing, and takes no second backup.
- [x] **DBV6** — Cross-store integrity re-verified after the migration: `verify_chain` ok on **all three** databases — `label_store.db` (8), `feature_store.db` (17), `ledger.db` (26). `report.py --check` clean (35 families: 9 candidate / 22 discovery / 4 disqualified — unchanged). `validate_label_governance()` against the real Label Store: **CLEAN** (both `LREC1` and `LREC2` empty).

## 6. Backward Compatibility (BC)

- [x] **BC1** — No existing function signature, table, or column was renamed, removed, or changed in return type.
- [x] **BC2** — `research/evidence_library/receipts.py` **unchanged** — verified: `label.register` was already present from WBS 3.8a. This milestone is the first since WBS 3.7 to require no taxonomy extension at all.
- [x] **BC3** — No Label Store or Feature Store module modified.
- [x] **BC4** — `.gitignore` gained **two lines** (§ Files Changed). One is squarely this milestone's (`label_store.pre-*.db`); the second is disclosed as a deliberate out-of-milestone fix below.
- [x] **BC5** — Repo-wide grep for the new module and its symbols (`migrate_bootstrap_label_receipts`, `_bootstrap_label_receipt`, `_assert_label_store_schema`): referenced only by the new script, its own test file, the two closeout documents that proposed it, and one docstring in `test_label_store_label_receipts.py`. **No production module outside `research/label_store/` references it.**
- [x] **BC6** — Independently deployable: the script is invoked manually and has no runtime consumer. Merging it alone changes no existing behaviour.

### A disclosed, deliberate out-of-milestone fix

Verification found that migration backup artifacts were **not gitignored**: neither `label_store.pre-*.db` (this milestone's) nor `feature_store.pre-*.db` (WBS 3.8b's, created 2026-08-06T03:26). Only `ledger.pre-*.db` had a pattern. Both files were untracked and would have been swept into the pending commit plan (`LS-DEC-006`, action #1 of the approved pre-Phase-4 list) — committing two real databases, one containing the 148 irreplaceable forward labels.

Strictly, the `feature_store.pre-*.db` line belongs to WBS 3.8b. It was fixed here anyway, as one line mirroring the existing `ledger.pre-*.db` precedent exactly, because leaving a known trap directly in the path of the next authorised action would be worse than the small scope excursion. **Disclosed rather than folded in silently.** Verified afterwards: `git status --untracked-files=all` reports no `.db` file of any kind.

## 7. Rollback Verification (RBV)

- [x] **RBV1** — **Code lever:** revert the commit. The script has no runtime consumer, so its absence changes nothing.
- [x] **RBV2** — **Data lever:** restore `label_store.pre-label-receipts-migration.20260806T055612Z.db`, taken automatically before the write and confirmed present.
- [x] **RBV3** — **Rollback is not required for correctness.** `governance_receipts` is append-only by DB trigger (PR-1), so the receipts cannot be deleted in place — matching every prior bootstrap migration. The honest lever is the backup, not a `DELETE`.
- [x] **RBV4** — No configuration flag is provided, deliberately and consistently with WBS 3.5–3.9: a receipt that can be switched off is not a governance record. Reverting is a human act with a backup, exactly as roadmap §10 prescribes for the storage-layer mechanisms.

## 8. Technical Debt Assessment (TD)

| What | Why deferred | Closed by (WBS ID) | Blast radius if never closed |
|---|---|---|---|
| The 8 receipts are `attestation="bootstrap"` and therefore permanently unreplayable — `replay()` refuses by design | The Tier A evaluation that justified each Specification was genuinely not captured at registration time; fabricating one now would be a lie with a valid hash (§0). This is the honest terminal state, not a deferral | **Never — by design.** Any *future* Specification registered through `label_receipts.register_specification_with_receipt()` gets a fully replayable receipt with a real `evaluation[]` | None. `verify()` holds forever; the gap is named in every receipt's own `attestation_note` |
| `LS-TD-002` (a `None` on any `NOT NULL` column rejects without persisting a `Rejected` row) is untouched by this milestone | Pre-existing `registry.py` defect, orthogonal to receipts; fixing it is "unrelated refactoring" of a prior milestone's code | Not scheduled — needs its own authorised fix | Unchanged from the existing entry. Note this milestone *would* correctly receipt such a row if one existed (`test_rejected_phase_yields_rejected_verdict`); the gap is that no such row is ever written |
| No live `label.register` receipt exists for a Specification registered via the bare `registry.register_specification()` (as `register_conventions.py` and `migrate_h6_forward_labels.py` both do) | Those two scripts predate WBS 3.8a and are not in this milestone's scope ("no unrelated refactoring"); re-pointing them at `label_receipts.register_specification_with_receipt()` would change two shipped milestones' behaviour | Not scheduled — a natural fit for whichever milestone next registers a real Label Specification | Low today: `validate_label_governance()` reports `LREC1` immediately for any such future Specification, so the gap is loud, not silent |

- [x] **TD3** — No placeholder work. The migration was rehearsed against a real-data copy and then executed for real, with results independently queried rather than asserted.
- [x] **TD4** — None of the above is unscoped or urgent debt. The first is a designed terminal state; the second is an already-tracked pre-existing entry; the third is loud-by-construction. Not promoted to `CLAUDE.md` §19.

## 9. Definition of Done (DOD)

- [x] **D1** — Compiles/imports cleanly in both environments.
- [x] **D2** — Full suite passes, zero new failures: 2255/19/0 (`btc_bot`), 1876/60/0 (base).
- [x] **D3** — 40 new tests, all passing in both environments.
- [x] **D4** — N/A explicitly stated where applicable (§4 PERF).
- [x] **D5** — No new import from `research/` into any operational-plane module.
- [x] **D6** — Rollback lever named (§7), with the backup verified to exist.
- [x] **D7** — No existing signature, table, or column renamed, removed, or changed in return type.
- [x] **D8** — No `.env`, credential, or secret touched, referenced, or required.
- [x] **D9** — Independently deployable; merging alone changes no existing behaviour.
- [x] **D10** — No new mandatory Specification/Value field introduced.
- [x] **D11** — All sections completed, including explicit N/A statements and the disclosed out-of-milestone `.gitignore` fix (§6).
- [x] **L-D1** — N/A: this milestone adds no immutability claim of its own; it relies on `governance_receipts`' existing append-only triggers, unmodified.
- [x] **L-D2** — N/A: no production host was involved. All real execution was local and is transcribed in §5.
- [x] **L-D3** — N/A: no standing is assigned or altered; all 148 forward labels remain `Context` (verified post-migration).

## 10. Reviewer Checklist (RC)

- [ ] **RC1** — Milestone corresponds to WBS 3.8c exactly, no scope creep into WBS 3.11
- [ ] **RC2** — No file under `docs/architecture/` touched
- [ ] **RC3** — Bootstrap attestation is the honest classification; no fabricated `evaluation[]`
- [ ] **RC4** — The disclosed out-of-milestone `.gitignore` fix (§6) is accepted
- [ ] **RC5** — The two pre-existing test updates (§3 REG5), including one rename, are accepted
- [ ] **RC6** — Real local `label_store.db`'s 8 bootstrap receipts independently spot-checked
- [ ] **RC7** — Pre-migration backup confirmed present and readable
- [ ] **RC8** — All 8 Specifications confirmed still at phase `Registered` (no activation)
- [ ] **RC9** — Sign-off

## Files Changed

| File | Change |
|---|---|
| `research/label_store/migrate_bootstrap_label_receipts.py` | **New** — the backfill script |
| `tests/test_migrate_bootstrap_label_receipts.py` | **New** — 40 tests |
| `tests/test_label_store_label_receipts.py` | 1 test updated + renamed (§3 REG5) |
| `tests/test_label_store_set_hygiene.py` | 1 assertion updated (§3 REG5) |
| `.gitignore` | +2 lines: `label_store.pre-*.db` (this milestone), `feature_store.pre-*.db` (disclosed out-of-milestone fix, §6) |
| `research/label_store/label_store.db` | **Real data**: +1 table, +8 receipts. Source tables byte-verified unchanged |
| `research/label_store/label_store.pre-label-receipts-migration.20260806T055612Z.db` | **New** — automatic pre-migration backup (gitignored) |

**Unchanged and verified so:** `research/evidence_library/receipts.py`, `receipt_ledger.py`, and every Label Store and Feature Store module.

## Summary

Shipped WBS 3.8c, closing `LS-GAP-001`: all 8 real Label Specifications now carry a bootstrap-attested `label.register` governance receipt, so `validate_label_governance()` reports **CLEAN** against the real database for the first time, and the receipt asymmetry between the two stores — features 17/17, labels 0/8, the exact ambiguity scope transfer `S-3` exists to prevent — is resolved. The milestone is genuine infrastructure reuse rather than shape-copying: unlike WBS 3.8b, which had to mirror `label_receipts.py`'s lookup by shape because its subject kind differed, this script imports `label_receipts.py` and consumes `DECISION_REGISTER`/`SUBJECT_KIND`/`get_registration_receipt()`/`init_db()` directly, and consequently required **no change to `receipts.py`** — the first Label Store milestone since 3.7 to need no taxonomy growth at all. WBS 3.8b's dry-run defect was treated as a known hazard rather than a surprise: `label_store.db` was the identical exposure (no `governance_receipts` table), so schema-write-free planning was designed in from the first draft, proven byte-safe against the real file before any commit, regression-tested by three dedicated tests, and used as TV3's primary sabotage target — where reintroducing the defect failed 5 tests. A second sabotage confirmed the phase-to-verdict mapping is genuinely exercised. Two test-construction errors of my own were found and corrected during verification (`receipts.verify` does not exist; `replay()` returns a refusal *result* rather than raising) — neither was a script defect, and the corrected assertions are stricter than the originals. Two pre-existing tests that hardcoded the pre-3.8c gap as a fixed expectation were updated to assert its closure, one with a rename, since a passing test asserting a no-longer-true condition would lie about what it checks. One deliberate out-of-milestone fix is disclosed rather than folded in silently: migration backup artifacts for **both** the Label and Feature Stores were not gitignored and would have been swept into the pending commit plan — two real databases, one holding the 148 irreplaceable forward labels. The migration was rehearsed end-to-end against a disposable copy before being executed for real, with `PRAGMA integrity_check` ok on both sides, all source-table counts byte-verified identical, and chains re-verified valid across all three governance databases. **Activation logic is untouched and all 8 Specifications remain `Registered`** — WBS 3.11 is **not begun**, per instruction.
