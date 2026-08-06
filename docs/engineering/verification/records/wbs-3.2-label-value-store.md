# Milestone Verification Record

| Field | Value |
|---|---|
| **Milestone** | Label Value Store — `research/label_store/value_store.py` |
| **WBS item(s)** | 3.2 |
| **Roadmap phase** | Phase 3 — Label Store (`phase_3_label_store_roadmap.md` v1.1.0) |
| **Standard version referenced** | 1.0.0 |
| **Date** | 2026-08-05 |
| **Implementer** | Claude Sonnet 5 |
| **Reviewer** | *(pending — Rohith)* |

**Scope discipline, stated up front.** Entirely new, additive code, corresponding to WBS 3.2 only. Does not modify any existing source file, including `research/label_store/registry.py` (WBS 3.1, untouched). Does not import, wrap, or depend on `research/feature_store/value_store.py` or on `research/label_store/registry.py` itself (no `spec_hash` foreign-key check — mirrors Feature Store's identical scoped simplification). No H6 wrapping (WBS 3.4), no real convention (WBS 3.3), no real data of any kind. Every value exercised in testing is hand-written. **WBS 3.3 is explicitly not begun.**

**No architectural inconsistency with ADR-I011 was found.** One genuine ambiguity was identified and resolved by re-reading rather than escalated: §7's table describes the Research tier as a plain "Evictable cache," while §9.3 separately invokes "ADR-I010 §7.3 verbatim" (a verification/divergence rule) specifically for Research Labels. Feature Store keeps these as two *separate* tiers (cache: unverified; materialised: verified) — ADR-I011 gives Research Labels only one tier. Resolution: the research tier here folds cache-tier storage and materialisation-tier verification into the single concept ADR-I011 actually names, since a fixed, deterministic address disagreeing with its own recomputation is a bug signal regardless of retention tier. This is recorded in the module's docstring, not left implicit, and does not change any table, arc, or field the ADR specifies.

---

## 1. Build Verification (BV)

- [x] **BV1** — `python -m py_compile research/label_store/value_store.py tests/test_label_store_value_store.py` → `COMPILE_OK` (base env + `btc_bot`).
- [x] **BV2** — Structure mirrors `research/feature_store/value_store.py` (cache/verification split, `init_db()`, content hashing) and `research/evidence_library/contract_registry.py` (the guarded-`UPDATE`-trigger pattern for the forward tier, per WBS 3.1's own audit finding `A-1` naming this exact precedent). **New structural guard added, per roadmap §9's BV row (`LR5`)**: `test_no_datetime_now_anywhere_in_the_maturation_code_path` (AST-walks the module, fails on any `.now` attribute access or `datetime` import) — passes because the module never imports `datetime` at all; every timestamp is caller-supplied.
- [x] **BV3** — No new dependency. Pure stdlib (`sqlite3`, `json`, `hashlib`, `dataclasses`).
- [x] **BV4** — Verified under both the base env (3.13.12) and `btc_bot` (3.10.20).

## 2. Test Verification (TV)

- [x] **TV1** — `tests/test_label_store_value_store.py` (new, 50 tests): Research Tier (10), Resolve Research (2), Forward Declaration (7), Allowed Maturity Arcs (5 — one per arc), Forbidden Maturity Arcs (11), Forward Field Validation (5), Storage-Layer Fill-Once (9, incl. the AST guard).
- [x] **TV2** — House style confirmed.
- [x] **TV3** — Empirically verified **twice**, covering both of this milestone's central claims: (a) removed the fill-once `CASE` block from `_GUARD_TRIGGER_SQL` — `test_direct_sql_cannot_rewrite_outcome_of_a_matured_label` failed with `Failed: DID NOT RAISE <class 'sqlite3.IntegrityError'>`; restored, full 49-test (then 50, after adding (b)) file re-confirmed green. (b) temporarily added `import datetime` to the module — `test_no_datetime_now_anywhere_in_the_maturation_code_path` failed, naming the exact injected line; reverted, re-confirmed green.
- [x] **TV4** — Every refusal ADR-I011 §12.2/§9.2/Tier C specifies has a dedicated negative test: all eleven forbidden maturity arcs (one test each, `TestForbiddenMaturityArcs`), four blank-field refusals on the forward tier plus the missing-vs-empty `input_revision_set` distinction (`TestForwardFieldValidation`), research-tier divergence on differing content and on read-time corruption (`TestResearchTier`), and — the storage-layer backstop, not merely the Python-level one — nine adversarial raw-`sqlite3` `UPDATE` attempts, each asserting `sqlite3.IntegrityError` with `match="LABEL_TAMPER"` (`TestStorageLayerFillOnce`).
- [x] **TV5** — Property/invariant tests: content-addressing and idempotency on the research tier (`test_identical_content_rewrite_is_idempotent`); addressing-triple independence (`test_addressing_is_the_spec_subject_declared_at_triple`, `test_different_addresses_are_independent`); **fill-once** proven both at the Python level (forbidden-arc tests) and, the exit criterion's actual requirement, at the **storage layer** via 9 adversarial direct-`sqlite3` tests; **Void/Adjudicated permanence** (`test_void_permits_no_further_transition`, `test_adjudicated_permits_no_further_transition`, `test_direct_sql_cannot_move_out_of_void`, `test_void_is_recorded_permanently_end_to_end` — the last also asserts structurally that no `delete_forward_label` function exists in the module at all).

## 3. Regression Verification (REG)

- [x] **REG1** — `btc_bot` env, `python -m pytest tests/ -q --ignore=tests/test_api.py`:
  - Before (start of this milestone, i.e. after WBS 3.1): **1741 passed, 19 skipped, 0 failed**
  - After: **1791 passed, 19 skipped, 0 failed**
  - Delta: **+50 passed**, exactly the new test file's count.
  - Base env (`tests/test_label_store_value_store.py` alone): **50/50 passed**.
  - Both new WBS 3.1 + 3.2 test files run together with both Feature Store test files (the collision-risk combination): **141/141 passed**.
- [x] **REG2** — Skip count unchanged (19→19).
- [x] **REG3** — No known pre-existing flake recurred in any of the runs performed for this milestone.
- [x] **REG4** — CI workflow unchanged.

## 4. Performance Verification (PERF)

**N/A** — no corpus-scale loop; every operation is a single-row SQLite read/write/update keyed by content hash or the (spec_hash, subject_id, declared_at) address triple, same cost class as every other registry/store in this codebase.

## 5. Database Verification (DBV)

**Applicability: YES** — this milestone introduces two new tables and, for the first time in this store, a real `BEFORE UPDATE` guard trigger enforcing the milestone's central claim.

- [x] **DBV1** — Additive only: `label_value_research`, `label_value_forward`, and `trg_label_value_forward_guard` are all wholly new. No existing table (including WBS 3.1's `label_definitions`/`label_specifications`) touched.
- [x] **DBV2/DBV3** — N/A — no migration script exists or is needed; there is no pre-existing `label_store.db` anywhere in the repository. `init_db()`'s `CREATE TABLE IF NOT EXISTS` / `CREATE TRIGGER IF NOT EXISTS` is idempotently exercised by every test's `tmp_path` fixture creation (confirmed: re-running `init_db()` against an already-initialised path is a no-op, exactly the property `CREATE ... IF NOT EXISTS` guarantees, and every one of the 50 tests does exactly that via the shared fixture).
- [x] **DBV4** — N/A — no governance ledger touched; `label_store.db` is independent of `ledger.db`.
- [x] **DBV5** — N/A — no real/production database exists yet for this store.
- [x] **DBV6** — N/A — `governance_verification.py`'s `SCHEMA_FINGERPRINT` covers `ledger.db` only; unchanged from the WBS 3.1 record's identical finding.

## 6. Backward Compatibility (BC)

- [x] **BC1–BC4** — N/A: zero existing functions, tables, columns, or CLI flags touched. `git status` confirms only new, untracked files (`research/label_store/value_store.py`, `tests/test_label_store_value_store.py`) — `registry.py` from WBS 3.1 is untouched.
- [x] **BC5** — Confirmed by direct search: `grep -rl "label_value_forward\|label_value_research\|LabelDivergenceEvent\|LabelTransitionError" --include="*.py" .` returns only this milestone's own two new files. **The same `sys.modules` bare-name-collision hazard identified and fixed at WBS 3.1** (`research/feature_store/registry.py` vs `research/label_store/registry.py`) recurs in this milestone's own shape: `research/feature_store/value_store.py` and `research/label_store/value_store.py` share the literal module name `value_store`. Applied the identical, already-established fix from the WBS 3.1 record: `tests/test_label_store_value_store.py` loads the module via `importlib.util.spec_from_file_location(...)` under the distinct name `"label_store_value_store"`, registered in `sys.modules` before `exec_module()`. Confirmed by running all four registry/value-store test files together: **141/141 passed**.

## 7. Rollback Verification (RBV)

- [x] **RBV1** — Delete `research/label_store/value_store.py` and `tests/test_label_store_value_store.py`. `research/label_store/registry.py` (WBS 3.1) is unaffected — the two modules do not import each other.
- [x] **RBV2** — No production data touched — confirmed; no real database exists for this store.
- [x] **RBV3** — Not exercised; no consumer depends on this module yet (WBS 3.3/3.4, the first real consumers, are separate future milestones). Stated explicitly.
- [x] **RBV4** — N/A — no governance record in scope.

**One deliberate, named exception to the "configurable kill switch" default (roadmap §10)**: the forward tier's `trg_label_value_forward_guard` has **no configuration-level disable lever** — `DROP TRIGGER` is the only way off, a deliberate human DDL act, never a runtime flag. This mirrors the roadmap's own §10 table exactly ("A configurable switch that turns off falsification protection is a switch that will eventually be left off. §7.2 admits no conditional.") and is not a gap in this milestone's rollback story — it is the documented exception the roadmap itself specifies.

## 8. Technical Debt Assessment (TD)

| What | Why deferred | Closed by (WBS ID) | Blast radius if never closed |
|---|---|---|---|
| No check that `spec_hash` resolves against `registry.py`'s registered Specifications (no import, no foreign key) | Mirrors `feature_store/value_store.py`'s identical, already-reviewed scope decision: "this module holds no binding-resolution logic of its own" | Not scheduled — a future consumer's responsibility, exactly as in Feature Store | Low today — no consumer exists yet; a real caller (WBS 3.4) is expected to resolve against the registry before declaring |
| No validation that `mature_forward_label()` is called only once a horizon has genuinely elapsed (maturity-rule timing) | Tier B (declaration integrity) and real timing discipline belong to WBS 3.4/3.5, which apply this mechanism to real H6 data | WBS 3.4/3.5 | None today — only hand-written test data is exercised |
| `input_revision_set` is accepted and stored verbatim, not reconciled against `research/storage/raw_lake.py`'s actual revision index | That integration is WBS 3.6's, the first milestone to list `raw_lake.py` as a dependency | WBS 3.6 | None today — nothing consumes the recorded set yet |
| No citation-index / use-counting for Promotion Labels (LG7, DI8) | Explicitly out of Phase 3 scope per the roadmap §4.3 — belongs to Phase 4, once a sealed promotion decision exists to count against | Phase 4 | None today |

- [x] **TD3** — No placeholder work; every function has a complete, tested implementation, including the storage-layer trigger. All four debt items above are pre-existing, named scope boundaries stated in the module's own docstring.
- [x] **TD4** — None of the above is long-lived urgent debt; all are correctly-deferred future-milestone dependencies already named in the roadmap. Not promoted to `CLAUDE.md` §19.

## 9. Definition of Done (DOD)

- [x] **D1** — Compiles/imports cleanly in both environments
- [x] **D2** — Full suite passes, zero new failures (1791/19, `btc_bot`; 50/50, base env)
- [x] **D3** — 50 new tests, all passing
- [x] **D4** — N/A, explicitly stated (§5)
- [x] **D5** — Zero imports from `research/` into any operational-plane module
- [x] **D6** — Rollback lever named (§7): delete the two files; the trigger's own no-configurable-lever exception is separately, explicitly documented
- [x] **D7** — No existing signature/table/column touched, including WBS 3.1's `registry.py` schema
- [x] **D8** — No `.env`, credential, or secret referenced
- [x] **D9** — Independently deployable — merging this alone changes no existing behaviour
- [x] **D10** — Every mandatory forward-tier field is rejected when absent/blank, verified by dedicated negative tests; `input_revision_set`'s missing-vs-empty distinction is verified by two separate tests (`Ph6`: an empty list is a valid declaration, `None` is an omission)
- [x] **D11** — All sections of this template completed, including explicit N/A statements for §4 and the applicable rows of §5/§6

## 10. Reviewer Checklist (RC)

*Pending — Rohith.*

- [ ] **RC1** — Milestone corresponds to WBS 3.2 exactly, no scope creep into 3.3+
- [ ] **RC2** — No file under `docs/architecture/` touched
- [ ] **RC3** — "Files Changed" list matches `git status` exactly
- [ ] **RC4** — At least two tests read in full, confirmed to exercise real behaviour
- [ ] **RC5** — Full-suite counts reproduced independently in both environments
- [ ] **RC6** — Rollback lever confirmed, including the trigger's documented exception
- [ ] **RC7** — Full diff read — should be 100% new files
- [ ] **RC8** — Sign-off

  **Reviewer:** _______________  **Date:** _______________

---

## Files Changed

| File | Type |
|---|---|
| `research/label_store/value_store.py` | New — research tier (evictable cache + §9.3 write/read verification), forward tier (six-state maturity lifecycle, storage-layer trigger-enforced fill-once) |
| `tests/test_label_store_value_store.py` | New — 50 tests |

## Summary

Shipped WBS 3.2, the second Label Store milestone: the two-tier Value Store, split by recomputability (§1.1) rather than convenience. The research tier folds Feature Store's cache-tier storage and materialisation-tier verification into the single concept ADR-I011 actually gives Research Labels, documented explicitly as a considered reading rather than an invented one. The forward tier is genuinely new — no Feature Store analogue — implementing the exact five arcs of §12.2's stateDiagram-v2 and, as this milestone's central deliverable, enforcing fill-once (§9.2, Tier C, LG2) at the **storage layer**: a `BEFORE UPDATE` trigger mirroring `contract_registry.py`'s guarded-trigger precedent, proven by nine adversarial raw-`sqlite3` tests that bypass every Python function in the module. Both central claims (fill-once, and the LR5 injected-clock AST guard) were verified red-without-implementation, not merely asserted. A second instance of the WBS 3.1 `sys.modules` bare-name collision was found (`value_store` vs `value_store`) and fixed with the same, now-established `importlib` pattern. Zero existing files modified. Next: WBS 3.3 (registering the three real fill conventions) — **not begun**, per instruction.
