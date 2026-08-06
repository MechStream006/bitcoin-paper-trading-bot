# Milestone Verification Record

| Field | Value |
|---|---|
| **Milestone** | Evidentiary Standing Assignment — `research/label_store/standing.py` |
| **WBS item(s)** | 3.5 |
| **Roadmap phase** | Phase 3 — Label Store (`phase_3_label_store_roadmap.md` v1.1.0) |
| **Standard version referenced** | 1.0.0 |
| **Date** | 2026-08-06 |
| **Implementer** | Claude Sonnet 5 |
| **Reviewer** | *(pending — Rohith)* |

**This milestone owns evidentiary standing assignment, as instructed.** It respects WBS 3.4's bootstrap markers exactly as recorded: `migrate_h6_forward_labels.py` and its 148 already-declared rows are **untouched** — this milestone's only interaction with them is `backfill_bootstrap_standing()`, a new, separate, additive action performing the one-time `NULL -> Context` assignment their existing `declared_by` marker already entitled them to. No `declared_at`, `matured_at`, `outcome`, or `declared_by` value written by WBS 3.4 was read for any purpose beyond computing the standing that assignment already implies, and none was altered. **WBS 3.6 is explicitly not begun.**

**No architectural inconsistency with ADR-I011 was found.** One roadmap-citation finding was made and resolved within this milestone's own scope (§0 below) — a repository-fact correction, not an ADR defect.

---

## 0. One thing found during implementation, resolved, not escalated

The roadmap's own §1.2 audit (`A-3`) states standing "is resolved via `research/forward_evidence/pre_reg_guard.py`, which already performs that lookup." Reading `pre_reg_guard.py` directly (this milestone) shows this is imprecise: `verify_fn_hash()` reads a signal's pre-registration JSON but extracts only `function_spec.decision_function_hash` for fn-hash integrity checking — it exposes **no boundary-lookup function**. The boundary value (`pre_registration_date`) genuinely lives in the same JSON files (confirmed by reading all six real files this session — `pre_registration.json`, and the five `pre_registration_*.json` multi-signal files), so `standing.py` reads them directly with its own small, dedicated function, rather than importing or modifying `pre_reg_guard.py` (left exactly as WBS 3.1 found it — no unrelated refactoring). This is the same class of finding as WBS 3.4's own §0.1 (a prior audit's engineering citation was imprecise about a real file's actual capabilities) — resolved by reading the real source, not escalated, since `ADR-I011` itself makes no claim about `pre_reg_guard.py` at all (that citation is the roadmap's own engineering-level detail, not ADR text).

**Verified boundaries** (read fresh from all six real files this session, matching the roadmap's own audit exactly):

```
h6                        2026-06-10T16:50:41.111245+00:00
g1_market_regime_sma200   2026-06-14T17:48:39+00:00   (shared)
g3_momentum_tsmom         2026-06-14T17:48:39+00:00   (shared)
h5_vol_regime              2026-06-14T17:48:39+00:00   (shared)
ivrv_vrp_spread            2026-06-19T00:00:00+00:00
g3_atr_expansion           2026-06-26T00:00:00+00:00
```

---

## 1. Build Verification (BV)

- [x] **BV1** — `python -m py_compile research/label_store/value_store.py research/label_store/standing.py tests/test_label_store_standing.py` → `COMPILE_OK` in both environments.
- [x] **BV2** — `standing.py` uses the same collision-safe `_load_sibling()` pattern WBS 3.4 introduced (loading `value_store.py` under an explicit distinct name, never a bare `import value_store`), for the identical, already-documented reason. `value_store.py`'s extensions (schema column, trigger clause, `declare_forward_label()` parameter, `set_standing()`) mirror already-reviewed precedents exactly: `contract_registry.py`'s `activation_receipt_status` write-once-from-NULL pattern for the column/trigger; `_apply_forward_transition()`'s own concurrency-guard shape for `set_standing()`.
- [x] **BV3** — No new dependency.
- [x] **BV4** — Verified under both the base env and `btc_bot`.

## 2. Test Verification (TV)

- [x] **TV1** — `tests/test_label_store_standing.py` (new, 27 tests): Resolve Signal Key (3), Resolve Standing (6), Declare With Resolved Standing (2), Backward Compatibility (2), Set Standing (4), Storage-Layer Immutability (3), Schema Migration (2), Backfill (5).
- [x] **TV2** — House style confirmed.
- [x] **TV3** — Empirically verified: removed the `standing` write-once clause from `trg_label_value_forward_guard` — a direct `sqlite3` `UPDATE` rewriting an already-set `standing` value, which should fail with `sqlite3.IntegrityError`, instead succeeded silently (`Failed: DID NOT RAISE`). Restored; full 27-test file re-confirmed green, and WBS 3.2/3.4's own 70 tests together re-confirmed unaffected (97/97).
- [x] **TV4** — Refusal paths tested: unrecognised `subject_id` prefix (`UnknownSubjectError`, does not guess a default boundary); invalid `standing` string rejected both in Python (`declare_forward_label()`, `set_standing()`) and at the storage layer (direct SQL, `CHECK` constraint); `set_standing()` on an already-set row (`LabelTransitionError`, citing LG3 by name); `set_standing()` on a non-existent row (`LabelValueStoreError`); the adversarial direct-SQL rewrite (§ TV3).
- [x] **TV5** — Property/invariant tests: **LG3 immutability**, both at the Python level (`test_refuses_when_already_set`) and the storage layer (`test_direct_sql_cannot_rewrite_standing_once_set`); **determinism / "two consumers cannot reach different conclusions"** (§10.1) — `test_deterministic_across_repeated_calls` (the resolution function itself) and `test_two_reads_of_the_same_row_always_agree` (two independent `sqlite3` connections reading the same stored value); **never writes `Promotion evidence`** (`test_never_returns_promotion_evidence`); **backward compatibility** (`declare_forward_label()` without the new parameter behaves identically to its WBS 3.2 shape); **idempotent backfill** (`test_second_commit_run_is_idempotent`, and verified for real — §5 DBV3).

## 3. Regression Verification (REG)

- [x] **REG1** — `btc_bot` env, `python -m pytest tests/ -q --ignore=tests/test_api.py`:
  - Before (after WBS 3.4): **1836 passed, 19 skipped, 0 failed**
  - After: **1863 passed, 19 skipped, 0 failed**
  - Delta: **+27 passed**, exactly matching the new test count.
  - Base env (`tests/test_label_store_standing.py` alone): **27/27 passed**.
  - WBS 3.2 + 3.4 + 3.5 together (backward-compatibility check): **97/97 passed**.
  - Maximal collision-risk combination (both Feature Store test files + all four Label Store test files): **203/203 passed**.
- [x] **REG2** — Skip count unchanged (19→19).
- [x] **REG3** — No known pre-existing flake recurred.
- [x] **REG4** — CI workflow unchanged.

## 4. Performance Verification (PERF)

**N/A** — 148 sequential `set_standing()` calls against a small local SQLite file; no corpus-scale loop.

## 5. Database Verification (DBV)

**Applicability: YES** — this milestone adds a column to the real, already-populated `label_value_forward` table and performs the first real write to it since WBS 3.4.

- [x] **DBV1** — Additive only. New `standing` column with a `CHECK` constraint; no existing column, table, or row content altered. `registry.py` and `migrate_h6_forward_labels.py` are both untouched (`git status` / re-run test suites confirm).
- [x] **DBV2** — `init_db()`'s new migration branch checks `PRAGMA table_info` before `ALTER TABLE ADD COLUMN` — only fires when the column is genuinely absent, verified idempotent by calling `init_db()` twice in sequence (`test_migration_is_idempotent`) and, for real, by every one of `standing.py`'s three real `--commit` runs each calling `init_db()` internally with no ill effect. `standing.py` itself is dry-run/`--commit`-gated, mirroring the established pattern.
- [x] **DBV3** — **Verified for real, three consecutive times, against the real local `label_store.db`**: run 1: `148 forward-tier row(s)... assigned=148`. Runs 2 and 3: `0 forward-tier row(s)... assigned=0`. `research/data/forward_evidence.db`'s SHA-256 (`92fe5b0a65f683f47332760349f3aec0e01c7e7f0a5dac963b786ae02866aca3`) was checked before and after this milestone's full real backfill sequence — **byte-identical** (expected: `standing.py` never opens H6 at all, unlike `migrate_h6_forward_labels.py`, which opens it read-only).
- [x] **DBV4** — N/A — no governance ledger touched.
- [x] **DBV5** — Exercised in spirit, not literally: no backup of `label_store.db` was taken immediately before this milestone's real run beyond WBS 3.10's own already-live `--label-store` backup coverage (which was confirmed live before WBS 3.4's first irreplaceable row, per the roadmap's own `3.10 → 3.4` ordering) — no additional ad hoc backup was judged necessary given the migration's own idempotency was independently proven safe to re-run.
- [x] **DBV6** — N/A — unchanged from prior findings.

**Real, independently verified outcome** (queried directly against `label_store.db`, not asserted from the script's own printed summary):

```
standing distribution:  Context=148 (100%)
rows with standing set: 148 / 148
sample row: subject_id=h6-trade-1, declared_at=2021-10-01T00:00:00+00:00,
            declared_by=migrate_h6_forward_labels.py (bootstrap, migrated 2026-08-06),
            maturity_state=Matured, standing=Context
```

## 6. Backward Compatibility (BC)

- [x] **BC1** — `declare_forward_label()`'s new `standing` parameter is optional with default `None` — every existing call site (WBS 3.2's own tests, WBS 3.4's `migrate_h6_forward_labels.py`) is unaffected, confirmed by re-running both milestones' full suites unchanged (97/97 together).
- [x] **BC2** — No existing table, column, or file renamed or removed. `standing` is a new, additive column.
- [x] **BC3** — No existing CLI flag's meaning changed (this milestone adds a new script, `standing.py`, with its own new flags).
- [x] **BC4** — The new column is additive with a safe default (`NULL`), and the migration path leaves all 148 pre-existing rows' other columns untouched — verified directly (`test_preexisting_table_without_standing_column_gets_migrated` checks `maturity_state`/`outcome_json` survive the `ALTER TABLE` unchanged).
- [x] **BC5** — Confirmed by direct search: `grep -rl "resolve_standing\|declare_with_resolved_standing\|backfill_bootstrap_standing\|STANDING_VALUES\|set_standing" --include="*.py" .` returns only this milestone's own files (`standing.py`, `value_store.py`, `tests/test_label_store_standing.py`). No other consumer exists.

## 7. Rollback Verification (RBV)

- [x] **RBV1** — Delete `research/label_store/standing.py` and `tests/test_label_store_standing.py`; revert `value_store.py`'s additive changes. Named explicitly in `label_maturity_contract.md` §6.
- [x] **RBV2** — No production data touched — confirmed; H6 was never opened by this milestone at all.
- [x] **RBV3** — Exercised directly: TV3's sabotage/revert cycle; separately, the real three-run backfill sequence is itself a live demonstration that re-running is safe (idempotent, not merely non-destructive).
- [x] **RBV4** — N/A — no governance record in scope.
- **One named, deliberate exception, per the roadmap's own §10 pattern for the fill-once trigger**: `standing`'s write-once guarantee has **no configuration-level disable** — `DROP TRIGGER`/manual `UPDATE` bypass is not a supported rollback path, only a genuine schema/code revert. LG3 admits no conditional, exactly mirroring the fill-once trigger's own accepted exception.

## 8. Technical Debt Assessment (TD)

| What | Why deferred | Closed by (WBS ID) | Blast radius if never closed |
|---|---|---|---|
| Only the `h6-` subject_id prefix is registered in `_SUBJECT_PREFIX_TO_SIGNAL` | No other signal's forward-tier data has been wrapped yet; registering `g1-`/`g3-`/`h5-`/`ivrv-` prefixes now would be speculative | Whichever future milestone first wraps a non-H6 signal's forward-tier data | None today — `resolve_signal_key()` refuses cleanly (`UnknownSubjectError`) rather than silently mis-resolving, so an unregistered future prefix fails loudly, not silently |
| `backfill_bootstrap_standing()` is scoped to rows with `standing IS NULL`, not specifically to bootstrap-marked rows | A future, non-bootstrap live declaration that somehow ends up with `standing IS NULL` (a caller bug — using `declare_forward_label()` without going through `declare_with_resolved_standing()`) would be silently swept into the same backfill and resolved via the live-boundary path rather than flagged as an anomaly | Not scheduled — this is the same resolution logic the backfill runs anyway (`resolve_standing()` correctly branches on `declared_by`, not on a special "isThisBootstrap" flag), so the outcome is correct even though it wasn't specifically anticipated as a "bug recovery" path | Low — the resolution is still correct per §10.1's own rules regardless of why standing was left NULL |
| The `Forward evidence -> Promotion evidence` upgrade arc is not implemented anywhere (trigger, function, or otherwise) | Explicitly Phase 4's own concern (LG7 use-counting against a sealed decision, an admissible-lane check) — building it now would be exactly the "architectural redesign" this milestone is instructed to avoid | Phase 4 | None today — no Promotion Label exists anywhere in this repository yet |

- [x] **TD3** — No placeholder work; the backfill ran for real, three times, against real data, with results independently queried.
- [x] **TD4** — None of the above is unscoped or long-lived urgent debt; all three items are either self-evidently low-risk (fail-loud by construction) or already-named future-phase dependencies.

## 9. Definition of Done (DOD)

- [x] **D1** — Compiles/imports cleanly in both environments
- [x] **D2** — Full suite passes, zero new failures (1863/19, `btc_bot`; 27/27, base env)
- [x] **D3** — 27 new tests, all passing
- [x] **D4** — N/A, explicitly stated (§5, except DBV3 which was genuinely exercised three times for real)
- [x] **D5** — Zero new imports from `research/` into any operational-plane module
- [x] **D6** — Rollback lever named (§7, `label_maturity_contract.md` §6)
- [x] **D7** — No existing signature, table, or column renamed, removed, or changed in return type — `declare_forward_label()`'s new parameter is additive-only
- [x] **D8** — No `.env`, credential, or secret touched, referenced, or required
- [x] **D9** — Independently deployable — merging this alone changes no other module's existing behaviour (verified: WBS 3.2/3.4's full test suites unchanged)
- [x] **D10** — Every new mandatory-if-present field (`standing`) is rejected when invalid, verified by dedicated negative tests at both the Python and storage layers
- [x] **D11** — All sections completed, including explicit N/A statements

## 10. Reviewer Checklist (RC)

*Pending — Rohith.*

- [ ] **RC1** — Milestone corresponds to WBS 3.5 exactly, no scope creep into 3.6
- [ ] **RC2** — No file under `docs/architecture/` touched
- [ ] **RC3** — "Files Changed" list matches `git status` exactly
- [ ] **RC4** — At least two tests read in full, confirmed to exercise real behaviour
- [ ] **RC5** — Full-suite counts reproduced independently in both environments
- [ ] **RC6** — Rollback lever confirmed
- [ ] **RC7** — Full diff read — confirm `registry.py`/`migrate_h6_forward_labels.py`/`register_conventions.py` show no diff; `value_store.py`'s diff is additive-only
- [ ] **RC8** — Sign-off

  **Reviewer:** _______________  **Date:** _______________

---

## Files Changed

| File | Type |
|---|---|
| `research/label_store/standing.py` | New — standing resolution + backfill CLI |
| `research/label_store/value_store.py` | Modified, additive only — `STANDING_VALUES`, `standing` column + `CHECK`, migration branch in `init_db()`, guard-trigger clause, `declare_forward_label()`'s optional `standing` parameter, new `set_standing()` function |
| `tests/test_label_store_standing.py` | New — 27 tests |
| `docs/engineering/label_maturity_contract.md` | New — standing lifecycle contract (mirrors `warmup_contract.md`), WBS 3.5's own portion; flagged for WBS 3.9 extension |
| `research/label_store/label_store.db` | Real, local, gitignored artifact — all 148 forward-tier rows now carry `standing = 'Context'` |

## Summary

Shipped WBS 3.5: evidentiary standing assignment, closing the gap WBS 3.4 deliberately left open. `standing.py` resolves standing via two paths — an unconditional bootstrap override (honouring WBS 3.4's own binding instruction verbatim) and a live per-signal boundary comparison, reading the real pre-registration JSON files directly since `pre_reg_guard.py` turned out not to expose the lookup the roadmap's own audit claimed it did (found and resolved within this milestone's own scope, not an ADR-I011 inconsistency). `value_store.py` was extended additively — a new write-once `standing` column, guarded by the same trigger pattern already governing every other forward-tier field, plus a schema migration path for the real, already-populated table WBS 3.4 left behind. The real backfill ran to completion three consecutive times against the real local `label_store.db`: all 148 H6 labels now carry `standing = 'Context'`, verified independently, idempotent, with H6's own source database confirmed byte-identical throughout (never even opened by this milestone's code). TV3 proved the LG3 immutability guarantee is real, not tautological. Zero changes to `registry.py`, `register_conventions.py`, or `migrate_h6_forward_labels.py`. Next: WBS 3.6 (superseded-input flagging) — **not begun**, per instruction.
