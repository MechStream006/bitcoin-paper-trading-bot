# Milestone Verification Record

| Field | Value |
|---|---|
| **Milestone** | H6 Forward-Label Migration — `research/label_store/migrate_h6_forward_labels.py` |
| **WBS item(s)** | 3.4 |
| **Roadmap phase** | Phase 3 — Label Store (`phase_3_label_store_roadmap.md` v1.1.0) |
| **Standard version referenced** | 1.0.0 |
| **Date** | 2026-08-06 |
| **Implementer** | Claude Sonnet 5 |
| **Reviewer** | *(pending — Rohith)* |

**Repository-side implementation only, as instructed.** `G-PROD` was not opened. No production rollout was performed. **Every row count in this record is real, freshly verified against `research/data/forward_evidence.db` — H6's own local, gitignored, dev-machine research database — never against, or claimed to represent, the production EC2 host's live collector.** This is the same category of local-machine, non-production data this session has already operated on legitimately at WBS 3.3 (`register_conventions.py --commit`) and WBS 3.10 (the restore drill) — distinct from the EC2 host `deploy/btcbot*.service` units run, which this milestone never touches. **WBS 3.5 is explicitly not begun.**

**No architectural inconsistency with ADR-I011 was found.** Two genuine reconciliations were required and are documented below (§0), both resolved within this milestone's own legitimate scope using only already-decided ADR-I011 rules — neither is a defect in the ADR.

---

## 0. Two things found during implementation, resolved, not escalated

### 0.1 A genuine gap in WBS 3.1's original audit, resolved within WBS 3.4's own scope

WBS 3.1's audit (`A-2`) characterized `daily_job.py`'s only registrable convention as `trade_exits.net_return` ("C1": flat-cost, long-only, fixed 7-day exit) and never separately examined `trade_labels.forward_return` — the actual 148-row table this milestone wraps. Reading `_update_forward_labels()` shows `trade_labels` uses a **different, previously-unregistered** convention: raw, zero-cost, unsigned, at four horizons (5/7/14/30 days) — the same family as `ret_fwd1` (WBS 3.3), just multi-horizon. Per `ADR-I011` §16.1 (horizon is identity) and §11.1 (Rehorizon: same outcome, different horizon → different Specification, **same** Definition), this milestone registered **four new Specifications under `ret_fwd1`'s existing Definition** rather than reusing C1/C2/C3 or leaving the gap unfilled. This is squarely WBS 3.4's own named responsibility (`§16.2` Declaration Fan-Out is explicitly WBS 3.4's ownership) — a placeholder cannot be declared under a Specification that does not exist.

### 0.2 Standing is deliberately not assigned by this milestone

The roadmap's own §4.1 exit-criterion **prose** ("148 labels wrapped, attestation=bootstrap, standing Context") predates the more precise §14.1 **ownership table**, which assigns "§10.1 · §12 Tier B (standing-at-declaration half) · LG3" to **WBS 3.5**, not 3.4, and makes WBS 3.5 explicitly depend on 3.4 ("Entry: 3.4 merged"). Adding a `standing` column to `value_store.py` here — ahead of WBS 3.5's own `standing.py` mechanism, which resolves per-signal against `pre_reg_guard.py`'s four real boundaries — would be exactly the "architectural redesign" this milestone is instructed to avoid, and would special-case these 148 rows instead of letting WBS 3.5's generic mechanism handle them uniformly alongside future live declarations. **What this milestone does instead, satisfying the underlying safety intent without a standing column:** every wrapped row's `declared_by` carries a permanent, structurally-checkable bootstrap marker (`BOOTSTRAP_DECLARED_BY`); the migration script contains **zero code capable of writing "Forward evidence" or "Promotion evidence"** anywhere (verified by a dedicated AST test, not just reasoned about); and a binding instruction is recorded here, in the migration script's own docstring, for WBS 3.5: it **must** treat `declared_by` carrying the bootstrap marker as forcing `Context`, never `Forward evidence`.

Similarly, full governance-receipt-shaped `attestation="bootstrap"` (matching the three precedent bootstrap migrations elsewhere in this repo) is **not** emitted — the Label Store's own receipt mechanism (`label_receipts.py`) is WBS 3.8a's deliverable and does not exist yet. `declared_by`'s marker is the honest, minimal, non-inventing substitute available at this point in the sequence — mirroring exactly how WBS 3.3 recorded its own provenance via the pre-existing `actor` field before any receipt mechanism existed either.

---

## 1. Build Verification (BV)

- [x] **BV1** — `python -m py_compile research/label_store/migrate_h6_forward_labels.py tests/test_migrate_h6_forward_labels.py` → `COMPILE_OK` in both environments.
- [x] **BV2** — `plan()`/`print_plan()`/`run()`/`main()` shape mirrors `register_conventions.py`'s established pattern. **One deliberate, source-level improvement over that precedent**: this module loads `registry.py`/`value_store.py` via `importlib.util.spec_from_file_location(...)` under explicit distinct names (`_load_sibling()`), never the bare `import registry`/`import value_store` `register_conventions.py` uses — because a bare import here was reproduced colliding with `research/feature_store/registry.py`'s identical bare name whenever both are loaded in the same pytest process (see §6 BC5). `register_conventions.py` itself is untouched (correct for its own real, standalone invocation, already verified in its own record) — this is a new module written collision-safe from the start, not a retrofit.
- [x] **BV3** — No new dependency.
- [x] **BV4** — Verified under both the base env and `btc_bot`.

## 2. Test Verification (TV)

- [x] **TV1** — `tests/test_migrate_h6_forward_labels.py` (new, 20 tests): Plan (3), Specification Registration (4), Wrapping (6), Idempotency (2), Read-Only-Against-H6 (2), No-Forward-Evidence-Capability (3).
- [x] **TV2** — House style confirmed. Uses a **synthetic, hand-built H6-shaped fixture** (two trades, four horizons, matching `schema.sql`'s real column set) rather than the real local `forward_evidence.db`, so the suite is hermetic and reproducible in CI, where that gitignored file does not exist — mirrors `tests/test_migration_harness.py`'s identical synthetic-fixture discipline.
- [x] **TV3** — Empirically verified: removed the idempotency skip-check (`if existing is not None and existing["maturity_state"] == "Matured": skipped += 1; continue`) — a second `run()` call against already-wrapped fixture data failed with `sqlite3.IntegrityError: UNIQUE constraint failed: label_value_forward.spec_hash, label_value_forward.subject_id, label_value_forward.declared_at`, exactly the failure mode the check exists to prevent. Restored; full 20-test file re-confirmed green, and the fix was re-verified for real against the real local H6 database (three consecutive `--commit` runs — see §5 DBV3).
- [x] **TV4** — Refusal paths tested: missing H6 source is a hard error (`test_missing_h6_source_is_a_hard_error`); missing `ret_fwd1` Definition fails closed with a named error, never silently creating a new Definition (`test_fails_closed_if_ret_fwd1_definition_missing`); the read-only H6 connection refuses a write attempt at the SQLite layer, not by convention (`test_readonly_connection_refuses_a_write_attempt`, asserting `sqlite3.OperationalError`).
- [x] **TV5** — Property/invariant tests: **horizon-as-identity** (four distinct Specification hashes, `test_four_horizons_produce_four_distinct_specifications`); **Rehorizon** (all four share one Definition, no second Definition version created on re-registration, `test_all_four_specifications_share_the_ret_fwd1_definition` / `test_registering_twice_does_not_create_a_second_definition_version`); **true historical instants, never `datetime.now()`** (`test_wrapped_row_uses_true_historical_declared_at_not_now`, plus a dedicated AST guard `test_no_datetime_now_anywhere_in_the_migration`, mirroring WBS 3.2's `LR5` discipline); **zero mutation of H6** (`test_h6_source_hash_unchanged_after_commit`); **structural absence of any Forward-evidence/Promotion-evidence-writing capability** (`test_script_never_writes_forward_evidence_or_promotion_evidence`, AST-based, docstring-exempted) and **no `standing` column exists** (`test_label_value_forward_has_no_standing_column`) — together the concrete, mechanistic proof standing §0.2 above.

## 3. Regression Verification (REG)

- [x] **REG1** — `btc_bot` env, `python -m pytest tests/ -q --ignore=tests/test_api.py`:
  - Before (after WBS 3.10): **1816 passed, 19 skipped, 0 failed**
  - After: **1836 passed, 19 skipped, 0 failed**
  - Delta: **+20 passed**, exactly matching the new test count.
  - Base env (`tests/test_migrate_h6_forward_labels.py` alone): **20/20 passed**.
  - Collision-risk combination (`test_feature_store_registry.py` + `test_feature_store_value_store.py` + this file + all three prior label-store test files): **176/176 passed** — the exact combination that failed with 12 errors before `_load_sibling()`'s fix (confirmed reproducible before fixing).
- [x] **REG2** — Skip count unchanged (19→19).
- [x] **REG3** — No known pre-existing flake recurred.
- [x] **REG4** — CI workflow unchanged.

## 4. Performance Verification (PERF)

**N/A** — 148 sequential declare/observe/mature calls against a small local SQLite file (label_store.db reached ~148 KB); no corpus-scale loop, no per-tick computation.

## 5. Database Verification (DBV)

**Applicability: YES** — this is the first real write to `label_value_forward` from a real, non-drill, non-test source (WBS 3.10's own drill rows were synthetic and cleaned up).

- [x] **DBV1** — Additive only. `research/data/forward_evidence.db` (H6) is opened via a `mode=ro` file URI — SQLite refuses any write attempt at the connection layer (verified: `test_readonly_connection_refuses_a_write_attempt`). No schema change to `registry.py`/`value_store.py` (both untouched — confirmed after the TV3 sabotage/revert cycle: `git status` shows no diff to either).
- [x] **DBV2** — `migrate_h6_forward_labels.py` is dry-run/`--commit`-gated, mirroring the established pattern exactly.
- [x] **DBV3** — **Verified for real, not just in tests**: ran `--commit` three consecutive times against the real local `research/label_store/label_store.db` and the real local `research/data/forward_evidence.db`. Run 1: `wrapped=148, skipped_already_present=0`. Runs 2 and 3: `wrapped=0, skipped_already_present=148`, identical Specification hashes across all three runs. `research/data/forward_evidence.db`'s SHA-256 (`92fe5b0a65f683f47332760349f3aec0e01c7e7f0a5dac963b786ae02866aca3`) was checked before the first run and after the third — **byte-identical**, confirming zero mutation of the real H6 source across the full real idempotency drill, not merely the synthetic-fixture test.
- [x] **DBV4** — N/A — no governance ledger touched.
- [x] **DBV5** — N/A — no real, pre-existing `label_value_forward` data existed before this milestone (WBS 3.10's drill rows were synthetic and explicitly cleaned up); nothing to back up before this migration's first real run. The forward tier's backup coverage itself (WBS 3.10) was already live before this run, per the roadmap's own `3.10 → 3.4` sprint ordering.
- [x] **DBV6** — N/A — unchanged from prior findings; `governance_verification.py`'s manifest covers `ledger.db` only.

**Real, independently verified outcome** (queried directly against `label_store.db` after the three-run drill, not asserted from the script's own printed summary):

```
label_definitions:      2   (unchanged from WBS 3.3)
label_specifications:   8   (4 from WBS 3.3 + 4 new horizon Specifications)
label_value_research:   0   (untouched -- no research-tier writes)
label_value_forward:  148   (all Matured)
distinct subject_id:   37   (matches 37 H6 trades exactly)
horizon distribution:   5d=37, 7d=37, 14d=37, 30d=37
declared_by:            100% carry BOOTSTRAP_DECLARED_BY, zero exceptions
```

A sample row (`h6-trade-1`, 5-day horizon) shows `declared_at="2021-10-01T00:00:00+00:00"` (a real historical H6 entry date) and `matured_at="2026-06-10T16:55:42.609000+00:00"` — a large real gap, honestly preserved rather than smoothed over: H6's `corpus_daily` spans a ~1,987-day backtest-simulated range, but `labeled_at` (which this migration maps to `matured_at`, per §14's "when the outcome became known") reflects when H6's own daily job **wrote** the row, consistent with the corpus having been backfilled in batches rather than filled one real calendar day at a time. This is an observed property of the real data, not a defect in this migration's design — `matured_at`'s ADR-I011 meaning ("when the outcome became known") is satisfied by preserving `labeled_at` exactly as H6 recorded it.

## 6. Backward Compatibility (BC)

- [x] **BC1–BC4** — N/A: zero existing functions, tables, columns, or CLI flags touched. `registry.py`/`value_store.py`/`register_conventions.py` are all unmodified (`git status` confirms only new, untracked files).
- [x] **BC5** — Confirmed by direct search: `grep -rl "migrate_h6_forward_labels\|BOOTSTRAP_DECLARED_BY\|h6-trade-" --include="*.py" .` returns only this milestone's own two new files. **A fourth instance of the `sys.modules` bare-import collision class was found and fixed at its source, not merely worked around** (§1 BV2): reproduced by running this file alongside both Feature Store test files (12 failures, all `RuntimeError`/wrong-module symptoms); fixed by writing `migrate_h6_forward_labels.py`'s own sibling-module loading to be collision-safe from the start (`_load_sibling()`), rather than adding another subprocess-based test workaround as WBS 3.3 did for `register_conventions.py`. Confirmed by re-running the maximal collision-risk combination: **176/176 passed**.

## 7. Rollback Verification (RBV)

- [x] **RBV1** — Delete `research/label_store/migrate_h6_forward_labels.py` and `tests/test_migrate_h6_forward_labels.py`. The wrapped rows in the real local `label_store.db` are this script's own output — deleting the script does not retroactively invalidate them, but re-running a future, corrected version would need to account for their presence (the idempotency check already handles this: a matching `Matured` row is skipped, not overwritten).
- [x] **RBV2** — No production data touched — confirmed throughout; H6 was opened read-only at the connection layer, and the only writes were to the real local, gitignored `label_store.db`.
- [x] **RBV3** — Exercised directly: TV3's sabotage/revert cycle is itself a rollback-and-restore demonstration; separately, the three real `--commit` runs against real data are themselves evidence that re-running (a natural "rollback and retry" operator action) is safe and convergent.
- [x] **RBV4** — N/A — no governance record in scope; `label_value_forward` rows are not governance receipts (WBS 3.8a's job, not yet built).

## 8. Technical Debt Assessment (TD)

| What | Why deferred | Closed by (WBS ID) | Blast radius if never closed |
|---|---|---|---|
| No `standing` column / no standing assignment for the 148 wrapped labels | Explicitly WBS 3.5's own ownership (§0.2 above); adding it here would be premature architecture | WBS 3.5 — **binding instruction recorded**: must treat `declared_by` carrying the bootstrap marker as forcing `Context`, never `Forward evidence` | None today — nothing consumes standing yet; the negative-test safety property (no code path can write Forward/Promotion evidence) already holds structurally |
| No governance receipt (`label.register`-shaped, `attestation="bootstrap"`) emitted for this migration | Label Store receipt mechanism is WBS 3.8a's deliverable, not built yet | WBS 3.8a — **mandatory backfill already scoped there** (roadmap `S-3` amendment: "the Phase 2 backfill is MANDATORY... split out as 3.8b"; this migration's own bootstrap event should be backfilled the same way when 3.8a lands) | Low today — `declared_by`'s bootstrap marker is a real, permanent, queryable substitute in the interim |
| `input_revision_set` recorded as a descriptive placeholder string, not a real `raw_lake.py` revision reference | H6's `corpus_daily` predates Phase 1's revisioning; reconciling against a real index is WBS 3.6's stated job (already disclaimed identically in `value_store.py`'s own docstring) | WBS 3.6 | None today — no consumer resolves revision sets yet |
| `mae_mfe_path` (222 rows) and `attribution` (37 rows) are not wrapped | §16.4: path measurements are explicitly separable and out of Phase 3 scope per the roadmap's own §4.3 table ("Deferred — research-question-gated") | Deferred, research-question-gated, not any specific WBS ID | None — matches an already-approved scope exclusion, not a new gap |

- [x] **TD3** — No placeholder work; the migration ran for real against real data three times, producing verified, queried, non-asserted results.
- [x] **TD4** — The `standing`/WBS 3.5 item and the `label.register` receipt/WBS 3.8a item are both real, meaningful carry-forwards, but both are already-scoped, already-named future milestones in the roadmap itself — not new, unscoped debt requiring `CLAUDE.md` §19 promotion.

## 9. Definition of Done (DOD)

- [x] **D1** — Compiles/imports cleanly in both environments
- [x] **D2** — Full suite passes, zero new failures (1836/19, `btc_bot`; 20/20, base env)
- [x] **D3** — 20 new tests, all passing
- [x] **D4** — N/A, explicitly stated (§5, except DBV3 which was genuinely exercised three times for real)
- [x] **D5** — Zero new imports from `research/` into any operational-plane module
- [x] **D6** — Rollback lever named (§7)
- [x] **D7** — No existing signature/table/column touched
- [x] **D8** — No `.env`, credential, or secret touched, referenced, or required
- [x] **D9** — Independently deployable — merging this alone changes no existing behaviour of any other module
- [x] **D10** — N/A for this milestone specifically (no new Tier-A-validated mandatory field; horizon Specifications reuse WBS 3.1's existing Tier A validation unchanged)
- [x] **D11** — All sections completed, including explicit N/A statements

## 10. Reviewer Checklist (RC)

*Pending — Rohith.*

- [ ] **RC1** — Milestone corresponds to WBS 3.4 exactly, no scope creep into 3.5
- [ ] **RC2** — No file under `docs/architecture/` touched
- [ ] **RC3** — "Files Changed" list matches `git status` exactly
- [ ] **RC4** — At least two tests read in full, confirmed to exercise real behaviour
- [ ] **RC5** — Full-suite counts reproduced independently in both environments
- [ ] **RC6** — Rollback lever confirmed
- [ ] **RC7** — Full diff read — confirm `registry.py`/`value_store.py`/`register_conventions.py` show no diff
- [ ] **RC8** — Sign-off

  **Reviewer:** _______________  **Date:** _______________

---

## Files Changed

| File | Type |
|---|---|
| `research/label_store/migrate_h6_forward_labels.py` | New — H6 migration: 4 new horizon Specifications + 148-row forward-tier wrap |
| `tests/test_migrate_h6_forward_labels.py` | New — 20 tests, synthetic H6 fixture |
| `research/label_store/label_store.db` | Real, local, gitignored artifact — now holds 2 Definitions, 8 Specifications, 148 forward-tier rows (all Matured) |

## Summary

Shipped WBS 3.4: the first Label Store milestone to touch forward-tier data, wrapping 148 real, locally-verified H6 forward labels (37 trades × 4 horizons, 100% matured) as the forward tier's first population. Two genuine reconciliations were required and resolved within this milestone's own scope, neither an ADR-I011 inconsistency: (1) `trade_labels.forward_return` turned out to be a previously-unregistered convention distinct from WBS 3.3's C1/C2/C3, resolved by registering four new horizon Specifications under `ret_fwd1`'s existing Definition (Rehorizon, §11.1); (2) evidentiary standing is deliberately **not** assigned here, since the roadmap's more precise ownership table assigns it to WBS 3.5, with a binding instruction recorded for that milestone instead of a premature schema change. Every declared/observed/matured instant is the TRUE historical H6 timestamp, never `datetime.now()` — verified by a dedicated AST guard. The migration was run for real, three consecutive times, against the real local H6 database, with the source database's SHA-256 checked identical before and after all three runs. A fourth instance of the `sys.modules` bare-import collision class was found and, this time, fixed at its source rather than worked around. Zero changes to any existing Label Store module. Next: WBS 3.5 (evidentiary standing) — **not begun**, per instruction.
