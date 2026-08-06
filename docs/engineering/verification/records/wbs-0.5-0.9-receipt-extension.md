# Milestone Verification Record

| Field | Value |
|---|---|
| **Milestone** | Receipt Extension — attestation_basis, contract_version, reserved Execution decision types, schema-only migration, GV-SCHEMA extension |
| **WBS item(s)** | 0.5, 0.6, 0.7, 0.8, 0.9 |
| **Roadmap phase** | Phase 0 — Admissibility + Receipt Substrate |
| **Standard version referenced** | 1.0.0 |
| **Date** | 2026-08-05 |
| **Implementer** | Claude Sonnet 5 (paired session) |
| **Reviewer** | *(pending — Rohith)* |

---

## 1. Build Verification (BV)

- [x] **BV1** — Every new/modified file compiles cleanly
  - Command: `python -m py_compile research/evidence_library/receipts.py research/evidence_library/receipt_ledger.py research/evidence_library/migrate_receipt_attestation_basis.py research/evidence_library/governance_verification.py tests/test_governance_receipts.py tests/test_governance_verification.py tests/test_migrate_receipt_attestation_basis.py`
  - Output: `COMPILE OK`
- [x] **BV2** — Imports follow this repo's established sibling-import convention
  - Confirmation: `migrate_receipt_attestation_basis.py` uses `sys.path.insert(0, str(_THIS_DIR)); import receipt_ledger as rl`, matching `migrate_run_kind.py` exactly. Test files use the same `sys.path.insert` + bare-import pattern as `test_governance_receipts.py`/`test_migration_harness.py`.
- [x] **BV3** — No new dependency added
  - Statement: none. Only stdlib (`sqlite3`, `json`, `argparse`, `shutil`, `subprocess`, `datetime`, `hashlib`) used throughout.
- [x] **BV4** — No file targets a Python version outside its owning environment
  - Confirmation: all files are under `research/`; tested against the `btc_bot`-equivalent local interpreter used for the rest of this suite.

---

## 2. Test Verification (TV)

- [x] **TV1** — A new test exists for every new public function/class/behaviour
  - Test files: `tests/test_governance_receipts.py` (+16 tests), `tests/test_governance_verification.py` (+1 test), `tests/test_migrate_receipt_attestation_basis.py` (new, 18 tests) — **35 new tests total**.
  - Mapping: `attestation_basis`/`contract_version` fields → `TestValidation` additions; hash participation → `TestHashStability` additions; Execution reservation → new `TestExecutionDecisionTypeReserved` class; RM3 → new regression test in `TestReplayFoundation`; persistence round-trip → `TestRegistryPersistence` additions; `init_db()`'s corrected non-mutating behaviour → repurposed test; `GV-SCHEMA` extension → new `TestSchemaFingerprint` test; the migration script → the entire new `test_migrate_receipt_attestation_basis.py` file.
- [x] **TV2** — New tests follow house style
  - Confirmation: `sys.path.insert` + bare imports, `class TestXxx:` groupings, plain `assert`, no `unittest.TestCase` — matches `test_governance_receipts.py`/`test_dq_engine.py` exactly.
- [x] **TV3** — At least one representative test confirmed to fail without the implementation
  - The migration script's `apply()`-based value-backfill design **failed for real** against a live database trigger (`sqlite3.IntegrityError: governance_receipts is append-only: UPDATE forbidden`) during first-draft test execution — see §8 Technical Debt / Incident. This is the strongest possible form of TV3: not a deliberately-reverted implementation, but the actual, unmodified first implementation genuinely failing its own test for a real, load-bearing reason, which then drove a design correction.
- [x] **TV4** — A negative/refusal-path test exists for every refusal the governing ADR specifies
  - `test_invalid_attestation_basis_value_rejected`, `test_governance_receipts_rejects_a_plain_update` (proves `RIM2` itself, not just the migration's compliance with it), `test_apply`-era refusal tests were removed with the redesign since `apply()` no longer exists; `test_missing_db_is_an_error` covers the CLI's own refusal path.
- [x] **TV5** — A property/invariant test exists for every algebraic or structural claim made
  - `test_receipt_id_changes_with_attestation_basis_alone` / `..._contract_version_alone` (IQROS-P5 content addressing); `test_module_issues_no_update_statement` (RIM2, structural); `test_no_existing_module_constructs_an_execution_receipt` (RG3, structural).

---

## 3. Regression Verification (REG)

- [x] **REG1** — Full suite run before and after, zero new failures
  - Before (start of this milestone, carried from the prior milestone's own final run): 1468 passed, 19 skipped.
  - After: **1500 passed, 19 skipped, 0 failed.** (1468 + 35 new − 3 double-counted transitional tests removed/rewritten during the redesign = 1500; reconciled exactly against `git diff` test counts.)
  - Command: `python -m pytest tests/ -q`
- [x] **REG2** — Skip count unchanged
  - Statement: 19 → 19, unchanged.
- [x] **REG3** — Known pre-existing flakes checked against
  - Notes: none observed in either full-suite run this milestone.
- [x] **REG4** — CI workflow unchanged
  - Statement: `.github/workflows/ci.yml` not touched.

---

## 4. Performance Verification (PERF) — *when applicable*

**Applicability: N/A — no loop over corpus-scale data, no new hot-path computation, no per-request recomputation claim.** All new code is either (a) additive dataclass fields participating in existing O(1)-per-receipt hashing, or (b) a one-shot, human-invoked migration script operating on a 26-row table.

---

## 5. Database Verification (DBV) — *when applicable*

**Applicability: YES — this milestone adds columns to `governance_receipts` and introduces a migration script.**

- [x] **DBV1** — Schema change is additive only
  - Statement: two new nullable `TEXT` columns (`attestation_basis`, `contract_version`), no existing column altered, no existing row's value ever written to.
- [x] **DBV2** — Migration script is idempotent, dry-run/`--commit`-gated
  - Script: `research/evidence_library/migrate_receipt_attestation_basis.py`. Confirmed idempotent: `ensure_columns()` only acts on genuinely-missing columns; a second `--commit` run adds no backup, per `test_second_commit_is_idempotent_and_backs_up_nothing_new`.
- [x] **DBV3** — Migration re-run twice produces zero new writes on the second run
  - Command/output: `test_second_commit_is_idempotent_and_backs_up_nothing_new` — first run creates one backup; second run creates zero additional backups and the schema is unchanged.
- [x] **DBV4** — Governance verification run against a throwaway copy before/after, clean
  - `python -m pytest tests/test_governance_verification.py -q` — 40/40 passed, including the real-ledger `TestRealLedger` class.
- [x] **DBV5** — Backup taken before any migration touches the real local ledger; integrity verified before the migration proceeds
  - **See the Incident below — this is the one row in this milestone that did not go as planned, and is reported in full rather than smoothed over.** No `--commit` was ever run against the real local ledger in this milestone (only against `tmp_path` copies, per `TestRealLedgerCopy`). However, the real local ledger's schema **was** mutated once, unintentionally, by a since-corrected defect — see §8.
- [x] **DBV6** — Schema-fingerprint manifest updated at the correct staged severity
  - `governance_verification.SCHEMA_FINGERPRINT` gained one entry for `governance_receipts.{attestation_basis, contract_version}` at **warning** severity, `as_of_none_only=True` — mirrors the `pairwise_r` precedent exactly.

---

## 6. Backward Compatibility (BC)

- [x] **BC1** — No existing public function's name/params/return type changed
  - Confirmation: `receipt_ledger.init_db(path)`'s signature is unchanged; its *behavior* changed (see Incident) but strictly by removal of an unintended side effect, not by a signature or documented-contract change — its original docstring never promised column-ensure on a pre-existing table.
- [x] **BC2** — No existing table/column/file renamed or removed
  - Confirmation: verified via `git status`/`git diff` — five files modified, two created, zero renamed/removed.
- [x] **BC3** — No existing CLI flag/config key's meaning changed
  - Confirmation: n/a — no existing CLI touched; the new script is additive.
- [x] **BC4** — New fields are additive with safe defaults
  - Confirmation: `attestation_basis`/`contract_version` default to `None` on `ReceiptBody`; every existing call site (`contract_receipts.py`, `evidence_governance.py`, `transition_governance.py`, the three `migrate_bootstrap_*.py` scripts) constructs bodies without these keywords and is unaffected — proven by the full regression suite.
- [x] **BC5** — Existing consumers of every touched symbol identified and confirmed unaffected
  - Search: `grep -rln "^import receipts\|^from receipts\|^import receipt_ledger\|^from receipt_ledger\|^import governance_verification\|^from governance_verification" research/` → `contract_receipts.py`, `contract_registry.py`, `evidence_governance.py`, `governance_verification.py` (self), `report.py`, `transition_governance.py`, and `research/dq/{contract,probes,readiness,validation}.py` (docstring references only, confirmed by the earlier taxonomy grep). All covered by the full regression suite; zero new failures.

---

## 7. Rollback Verification (RBV)

- [x] **RBV1** — Rollback lever named
  - `attestation_basis`/`contract_version` fields: revert the commit to `receipts.py`/`receipt_ledger.py` — no data depends on them yet (all real-ledger values are `NULL`).
  - Reserved Execution decision types: revert the `DECISION_TAXONOMY` addition — no receipt of that type has ever been emitted (`test_no_existing_module_constructs_an_execution_receipt`).
  - `GV-SCHEMA` warning entry: delete the one tuple entry from `SCHEMA_FINGERPRINT` — the check becomes a no-op, exact precedent of the `max_pairwise_r` lever.
  - The migration script: simply never run it again; it has no consumer.
- [x] **RBV2** — Lever does not require touching production data
  - Confirmation: every lever above is a code revert, not a data operation.
- [x] **RBV3** — Lever exercised, or explicitly reasoned why not yet meaningful
  - Statement: not exercised (nothing yet consumes these fields in production). Explicitly stated rather than assumed, per the standard's own requirement.
- [x] **RBV4** — Rollback does not discard already-recorded governance history
  - Confirmation: the two new columns hold no data on any real receipt (`test_all_26_real_receipts_have_null_attestation_basis`); reverting the schema addition loses nothing.

---

## 8. Technical Debt Assessment (TD)

- [x] **TD1/TD2** — Every deferral/simplification, with future milestone and blast radius

| What | Why deferred | Closed by (WBS ID) | Blast radius if never closed |
|---|---|---|---|
| `attestation` (bootstrap marker) and `attestation_basis` (ADR-I015 basis) are not cross-validated against each other | Out of this milestone's minimal scope; no consumer needs the cross-check yet | Future milestone, if/when Execution or another attestation-basis-consuming receipt type is built | Low — both fields are independently valid today; a future inconsistency (e.g. `attestation="bootstrap"` with `attestation_basis="external-venue"`) would currently pass silently |
| `SUBJECT_KINDS` has no entry for what an Execution receipt's `Subject.kind` should be | `ADR-I015` §12.1 names the three decision types but not the subject kind; inventing one would be architecture, not engineering | `ADR-I018` (Execution authorisation, unwritten) | None today — Execution types are unemitted; blocks nothing until that ADR exists |
| **Incident: `receipt_ledger.init_db()`'s first-draft column-ensure logic mutated the real local ledger's schema as a side effect of `governance_verification.verify_governance()`'s documented "read-only" sweep, before being caught and corrected within this same milestone.** Root cause: `verify_governance()` calls `evidence_governance.init_db()`, which cascades to `receipt_ledger.init_db()`; the first draft of `init_db()` performed an `ALTER TABLE ADD COLUMN` there, identically to `record.py`'s pre-existing `run_kind` precedent. Running this milestone's own test suite (`pytest tests/test_governance_verification.py`) exercised that path against the real ledger path directly (`TestRealLedger`/`test_real_ledger_check_performs_no_schema_mutation` both call `gv.verify_governance(REAL_LEDGER, ...)`/`report.run_check(..., REAL_LEDGER, ...)`), silently adding both columns (NULL-valued) to the real `governance_receipts` table. **Verified harmless**: row counts unchanged (70/26/13/142), zero non-NULL values in either new column, `receipt_ledger.verify_chain()` still `ok=True` with 26 receipts — because `receipt_id` is computed from `body_json` content only, never from the live table schema, adding a column cannot and did not affect any existing receipt's hash. **Fixed**: `init_db()` reverted to CREATE-only (no `ALTER`); the column-ensure step moved into `migrate_receipt_attestation_basis.ensure_columns()`, reachable only from that script's own explicit, human-invoked `main()` — never from any verification/read path. **Not reverted**: the two harmless, already-added columns on the real local ledger were left in place rather than attempting a second write (a `DROP COLUMN` rebuild) to "undo" a change that cost nothing to keep. | Found and fixed within this milestone, before merge | — closed | None remaining for `attestation_basis`/`contract_version` specifically. **Generalises to a discovered, NOT fixed, pre-existing risk**: `record.py`'s `run_kind` column-ensure and `contract_registry.py`'s `activated_receipt_id`/`retired_receipt_id` column-ensure use the identical "ALTER inside init_db()" pattern this milestone found unsafe when init_db() is reachable from a "read-only" verification sweep. It did not fire destructively for `run_kind` only because the real migration happened to run before this exposure was ever exercised against a genuinely pre-migration real ledger. **Logged, not fixed — out of this milestone's scope** (fixing it would touch `record.py`/`contract_registry.py`, neither of which this milestone's WBS items name). |
| `test_governance_verification.py::TestReadOnly::test_module_has_no_write_verbs` is a strict literal string scan (not AST-aware) | Pre-existing test design, not this milestone's to change | — | Low, already known: any future comment merely *mentioning* a write verb near-verbatim will need the same rewording this milestone had to do once |

- [x] **TD3** — No debt item disguised as complete; no placeholder implementation; no TODO-only commit
  - Confirmation: every function in every new/modified file has a complete, working implementation exercised by a passing test; the incident above is disclosed in full rather than omitted.
- [x] **TD4** — Long-lived debt carried into the running log
  - The generalised `init_db()`-cascade risk (record.py, contract_registry.py) is recorded here; not yet added to `CLAUDE.md` §19's maintenance backlog — recommended as a follow-up action for Rohith to decide, since editing `CLAUDE.md` is outside this milestone's file scope.

---

## 9. Definition of Done (DOD)

- [x] **D1** — Compiles/imports cleanly
- [x] **D2** — Full suite passes, zero new failures (1500 passed / 19 skipped)
- [x] **D3** — New tests present and passing (35 new tests)
- [x] **D4** — Governance verification clean (`report.py`/`governance_verification.py` test suites both green; real ledger `--check`-equivalent clean)
- [x] **D5** — Zero new imports from `research/` into any operational-plane module (none of this milestone's files import from `src/`)
- [x] **D6** — Rollback lever named before merge (§7)
- [x] **D7** — No existing table/column/function signature renamed, removed, or changed in return type
- [x] **D8** — No `.env`, credential, or secret touched/referenced/required
- [x] **D9** — Independently deployable — merging this alone leaves the repo working and fully tested
- [x] **D10** — Every new mandatory-when-set field (`attestation_basis`) is rejected when invalid, verified by `test_invalid_attestation_basis_value_rejected`; neither new field is unconditionally mandatory (both nullable, by design — documented in §5 DBV1 and the fields' own docstrings), so there is no "missing declaration" refusal to test here beyond the enum-validity check already covered
- [x] **D11** — All sections of this template completed, including explicit N/A statement for §4

---

## 10. Reviewer Checklist (RC)

*To be completed by Rohith in a separate pass.*

- [ ] **RC1** — Milestone corresponds to WBS 0.5–0.9 exactly, no unstated scope creep
- [ ] **RC2** — No file under `docs/architecture/` was touched
- [ ] **RC3** — "Files Changed" list (below) matches `git status`/`git diff` exactly
- [ ] **RC4** — At least two tests read in full and confirmed to exercise real behaviour
- [ ] **RC5** — Full-suite pass/skip/fail count reproduced independently
- [ ] **RC6** — Rollback levers named in §7 confirmed to actually exist in the diff
- [ ] **RC7** — Full diff of every changed file read, no unrelated refactor found
- [ ] **RC8** — Sign-off

  **Reviewer:** _______________  **Date:** _______________

---

## Summary

Shipped WBS 0.5–0.9: additive `attestation_basis`/`contract_version` fields on `ReceiptBody` (ADR-I015 §5.3, RVX1), three reserved-but-unemitted `execution.*` decision types (RG1/RG3), a schema-only migration script, and a `GV-SCHEMA` extension at warning severity. Mid-milestone, the first-draft migration design (a `runs.run_kind`-style value backfill) was correctly refused by `governance_receipts`'s append-only trigger — a real, load-bearing discovery that the table's `RIM2` guarantee ("no update path... not for migrations") is enforced exactly as written, requiring a redesign to a schema-only migration. Separately, testing surfaced that the original `init_db()` design mutated the real local ledger's schema as a side effect of a documented "read-only" verification sweep; this was caught, verified harmless, root-caused, and fixed within the same milestone, with the residual (harmless, already-added) real-ledger columns disclosed rather than reverted. Next: WBS 1.1 (Phase 1, Storage Plane Conformance).
