# Milestone Verification Record

| Field | Value |
|---|---|
| **Milestone** | Label Registry — `research/label_store/registry.py` |
| **WBS item(s)** | 3.1 |
| **Roadmap phase** | Phase 3 — Label Store (`phase_3_label_store_roadmap.md` v1.1.0) |
| **Standard version referenced** | 1.0.0 |
| **Date** | 2026-08-05 |
| **Implementer** | Claude Sonnet 5 |
| **Reviewer** | *(pending — Rohith)* |

**Scope discipline, stated up front.** This milestone is **entirely new, additive code**, corresponding to WBS 3.1 only. It does not modify any existing source file. It does not import, wrap, or depend on `research/feature_store/registry.py` — the roadmap's I8 ("deliberate non-integration") requires the two registries to be mirrored, not shared. No Value Store (WBS 3.2), no H6 wrapping (WBS 3.4), no real convention (WBS 3.3), and no real data of any kind are touched. Every Specification exercised in testing is hand-written, per the roadmap's own exit criterion for this item. **WBS 3.2 is explicitly not begun.**

---

## 1. Build Verification (BV)

- [x] **BV1** — `python -m py_compile research/label_store/registry.py tests/test_label_store_registry.py` → `COMPILE_OK` (verified in both the base env, Python 3.13.12, and `btc_bot`, Python 3.10.20).
- [x] **BV2** — Registry structure (`init_db()`, content-addressed hashing, append-only triggers, binding resolution via `register_binding`/`get_binding`/`UnknownBindingError`) mirrors `research/feature_store/registry.py` exactly, per §6's "No new governance mechanism is introduced." **One deliberate deviation from the house sys.path-insert convention**, confined to the test file only — see BC5/TD below.
- [x] **BV3** — No new dependency. The module is pure stdlib (`sqlite3`, `json`, `hashlib`, `dataclasses`) — runs in any environment.
- [x] **BV4** — Verified under both the base env and `btc_bot` — no environment-specific code.

## 2. Test Verification (TV)

- [x] **TV1** — `tests/test_label_store_registry.py` (new, 42 tests: Label Definitions, Binding Resolution, Tier A Completeness — one test per each of the ten mandatory fields, plus multi-failure/idempotency/hash-difference tests, the §11.2 maturation-invariant tests, Specification Activation, Phase Transitions).
- [x] **TV2** — House style confirmed (`class TestXxx:`, `tmp_path` fixture, plain `assert`, no `unittest.TestCase`).
- [x] **TV3** — Empirically verified, not merely reasoned: temporarily removed the `maturity_rule` blank-check from `_validate_tier_a()` in `registry.py`. `test_field_07_maturity_rule_is_rejected_when_blank` failed with `Failed: DID NOT RAISE`, confirming the test is not tautological. The check was restored and the full 42-test file re-confirmed green before proceeding.
- [x] **TV4** — Every refusal ADR-I011 §12 Tier A and §6.1 specify has a dedicated negative test: ten Tier A field-completeness refusals, one per field (`test_field_01`…`test_field_10`, `TestTierACompleteness`); illegal phase-transition arcs and stale-phase concurrency guards (`TestPhaseTransitions`); unresolved binding pairs (`TestBindingResolution`); activation failure by returned-`False` and by raised-exception, and re-activating a non-`Registered` Specification (`TestSpecificationActivation`).
- [x] **TV5** — Property/invariant tests: content-addressing (`test_registering_identical_content_twice_is_idempotent`, `test_different_horizons_produce_different_hashes`, `test_different_conventions_produce_different_hashes`); append-only Definitions (`test_definitions_are_never_updated_in_place`/`_deleted`); **§11.2 "Maturation Is Not a Version Change"** — `test_spec_hash_is_invariant_to_registration_time` (identical content registered at two different `registered_at` timestamps produces the same `spec_hash`) and `test_specification_content_carries_no_maturity_field` (structural: no `matur*`-named column exists on a Specification row except the declared `maturity_rule` itself).

## 3. Regression Verification (REG)

- [x] **REG1** — `btc_bot` env, `python -m pytest tests/ -q --ignore=tests/test_api.py` (excluded per the established, documented gap — no local conda env has `fastapi`; CI installs `requirements-api.txt` and covers it there):
  - Before: **1699 passed, 19 skipped, 0 failed** (2026-08-05, pre-change)
  - After: **1741 passed, 19 skipped, 0 failed** (2026-08-05, post-change)
  - Delta: **+42 passed**, exactly the new test file's count; zero pre-existing test affected.
  - Base env (`tests/test_label_store_registry.py` alone): **42/42 passed**, confirming environment-independence directly.
- [x] **REG2** — Skip count unchanged (19→19) — expected, no `pytest.importorskip` needed.
- [x] **REG3** — The known, pre-existing `current_run_per_lane` tiebreak timing flake (`CLAUDE.md` §18, Increments 2 and 4) recurred once, as `tests/test_evidence_report.py::TestFlags::test_pin_stale_flag`, during one of the three full-suite runs performed for this milestone. Investigated per the established pattern: passed 1/1 in isolation; a second full-suite run immediately after passed clean (1741/19/0). `tests/test_evidence_report.py` has zero import relationship to `research/label_store/` (confirmed — the failing test's own file and dependency chain never reference this milestone's module). Confirmed unrelated, consistent with its two prior recorded occurrences. **A different, related-but-new hazard was found and fixed during this milestone, not merely checked against** — see BC5/TD below.
- [x] **REG4** — CI workflow unchanged.

## 4. Performance Verification (PERF)

**N/A** — no corpus-scale loop; every operation is a single-row SQLite read/write keyed by content hash, same cost class as `research/feature_store/registry.py` and `receipt_ledger.py`.

## 5. Database Verification (DBV)

**Applicability: YES in a narrow sense** — `init_db()` defines two new tables (`label_definitions`, `label_specifications`). Every row below is otherwise N/A, because this is a brand-new, non-production, non-ledger database with no migration script and no real data ever touched by this milestone.

- [x] **DBV1** — Additive only: two wholly new tables. No existing table touched (confirmed — nothing existing was touched at all this milestone).
- [x] **DBV2/DBV3** — N/A — no migration script exists or is needed; there is no pre-existing `label_store.db` anywhere in the repository. `init_db()`'s `CREATE TABLE IF NOT EXISTS` is the entire schema-ensure story, idempotently exercised by every one of the 42 tests' `tmp_path` fixture creation.
- [x] **DBV4** — N/A — no governance ledger touched; `label_store.db` is a new, independent database file, not `ledger.db`.
- [x] **DBV5** — N/A — no real/production database exists yet for this store; nothing to back up. `label_store.db` is never created outside a test's `tmp_path`.
- [x] **DBV6** — N/A — `governance_verification.py`'s `SCHEMA_FINGERPRINT` covers `ledger.db` only (5 entries, all ledger-scoped); `label_store.db` is out of that manifest's scope by design, exactly as `feature_store.db` already is.

## 6. Backward Compatibility (BC)

- [x] **BC1–BC4** — N/A in the strongest sense: **zero existing functions, tables, columns, or CLI flags were touched.** `git status` confirms only new, untracked files plus one line added to `.gitignore`.
- [x] **BC5** — Confirmed by direct search: `grep -rl "label_store" --include="*.py" .` returns only `tests/test_label_store_registry.py` itself — zero existing consumers. **A real, reproducible collision was found and fixed, not merely searched for**: `test_feature_store_registry.py` and the original draft of `test_label_store_registry.py` both used the house `sys.path.insert(...)` + bare `import registry as reg` convention. Since both sibling packages' modules share the literal name `registry`, the second import in any single pytest process silently reused the first module cached in `sys.modules['registry']` — reproduced concretely: `tests/test_feature_store_registry.py tests/test_label_store_registry.py` run together produced 34 failures (`TypeError: register_specification() got an unexpected keyword argument 'horizon_days'`), while each file passed 100% in isolation. This is the same *class* of hazard already logged in `CLAUDE.md` §18 Increment 6 for `research/dq/engine.py` vs `research/evidence/engine.py` (there, pre-existing and left as documented technical debt, out of that increment's scope). Here the collision is newly introduced by this milestone, so it was fixed at the point of introduction: `tests/test_label_store_registry.py` now loads `registry.py` via `importlib.util.spec_from_file_location(...)` under the distinct name `"label_store_registry"`, registered in `sys.modules` before `exec_module()` (required for `@dataclass` to resolve `cls.__module__` at class-definition time). `research/feature_store/registry.py` and its test file are untouched. Re-run together post-fix: **76/76 passed**.

## 7. Rollback Verification (RBV)

- [x] **RBV1** — Delete `research/label_store/` and `tests/test_label_store_registry.py`; revert the two added lines in `.gitignore`.
- [x] **RBV2** — No production data touched — confirmed; no real database exists for this store yet.
- [x] **RBV3** — Not exercised; no consumer depends on this module yet (WBS 3.2/3.3, the first real consumers, are separate future milestones). Stated explicitly per the standard's requirement.
- [x] **RBV4** — N/A — no governance record in scope; this module never touches `governance_receipts` or `state_transitions`.

## 8. Technical Debt Assessment (TD)

| What | Why deferred | Closed by (WBS ID) | Blast radius if never closed |
|---|---|---|---|
| `conditioning_propagation`'s `CONDITIONING_RULES` vocabulary is a verbatim duplicate of `feature_store/registry.py`'s constant, not an import or a real propagation mechanism | The real propagation mechanism (`feature_store/conditioning.py`, imported unchanged) is wired at WBS 3.7; this milestone only validates that a Specification names one of the three known rule strings | WBS 3.7 | None today — no propagation is claimed or performed yet, only declared |
| `activate_specification()`'s `equivalence_check` is a caller-supplied callable with no real binding to check yet | No convention (WBS 3.3) or H6 binding (WBS 3.4) exists yet to demonstrate equivalence against | WBS 3.3/3.4 | None today — only exercised by hand-written test callables |
| No citation-index check in `retire_specification()` (§12.1: "Retirement requires zero citations") | The citation index is `ADR-I012`'s Dataset Builder, not built yet | Phase 4 | None today — nothing cites a Label Specification yet |
| Phase transitions guarded in Python (allowed-arc table + `WHERE`-clause concurrency check), not a database-level trigger | Matches the Feature Store's own identical, already-reviewed scope decision; single-writer, no concurrent-writer scenario exists yet | Not scheduled — mirrors `FS-TD-001`'s disposition | Low — the Python guard already refuses concurrent/stale-phase writes correctly (tested) |

- [x] **TD3** — No placeholder work; every function has a complete, tested implementation. All four debt items above are pre-existing, named scope boundaries stated in the module's own docstring, not gaps discovered after the fact.
- [x] **TD4** — None of the above is long-lived urgent debt; all are correctly-deferred future-phase dependencies already named in the roadmap itself. Not promoted to `CLAUDE.md` §19. The `sys.modules` collision fixed under BC5 is **not** carried as debt — it is resolved, not deferred.

## 9. Definition of Done (DOD)

- [x] **D1** — Compiles/imports cleanly in both environments
- [x] **D2** — Full suite passes, zero new failures (1741/19, `btc_bot`; 42/42, base env)
- [x] **D3** — 42 new tests, all passing
- [x] **D4** — N/A, explicitly stated (§5)
- [x] **D5** — Zero imports from `research/` into any operational-plane module (confirmed — this module imports nothing outside the stdlib)
- [x] **D6** — Rollback lever named (§7): delete the directory and test file, revert `.gitignore`
- [x] **D7** — No existing signature/table/column touched — vacuously true, nothing existing was touched
- [x] **D8** — No `.env`, credential, or secret referenced
- [x] **D9** — Independently deployable — merging this alone changes no existing behaviour (zero existing files modified; `.gitignore` addition is purely additive)
- [x] **D10** — Every mandatory field (`ADR-I011` §8.2, plus the `LR7` horizon-governance field) is rejected when absent, verified by ten dedicated Tier A negative tests, one per field
- [x] **D11** — All sections of this template completed, including explicit N/A statements for §4 and the applicable rows of §5/§6

## 10. Reviewer Checklist (RC)

*Pending — Rohith.*

- [ ] **RC1** — Milestone corresponds to WBS 3.1 exactly, no scope creep into 3.2+
- [ ] **RC2** — No file under `docs/architecture/` touched
- [ ] **RC3** — "Files Changed" list matches `git status` exactly
- [ ] **RC4** — At least two tests read in full, confirmed to exercise real behaviour
- [ ] **RC5** — Full-suite counts reproduced independently in both environments
- [ ] **RC6** — Rollback lever confirmed
- [ ] **RC7** — Full diff read — should be 100% new files plus the two-line `.gitignore` addition
- [ ] **RC8** — Sign-off

  **Reviewer:** _______________  **Date:** _______________

---

## Files Changed

| File | Type |
|---|---|
| `research/label_store/__init__.py` | New — empty package marker, mirroring `research/feature_store/__init__.py` |
| `research/label_store/registry.py` | New — Label Definitions, Label Specifications, binding resolution, Tier A validation (ten fields), lifecycle transitions |
| `tests/test_label_store_registry.py` | New — 42 tests |
| `.gitignore` | Modified — added `research/label_store/label_store.db{,-wal,-shm}`, mirroring the existing `feature_store.db` entry |

## Summary

Shipped WBS 3.1, the first Label Store milestone: a generic, self-contained registry proven entirely with hand-written test Specifications, per the roadmap's own exit criterion. Tier A validates all ten mandatory fields — the nine named in `ADR-I011` §8.2 plus the `LR7` horizon-governance field resolving §17's calendar trap — each with its own dedicated negative test. The §11.2 "maturation is not a version change" invariant is proven both structurally (no maturity-state field exists on a Specification) and empirically (identical content at two different registration times yields the same hash). The module's docstring records, deliberately and explicitly, why it does not carry over the Feature Store's `input_requirements`/`warmup_periods`/`sensitivity_variants` fields (not required by `ADR-I011` §8.2), and why `activate_specification()` — though mirroring `ADR-I010`'s binding-equivalence mechanism exactly — is *not* an implementation of `ADR-I011`'s own, differently-scoped §12 Tier B (owned by WBS 3.4/3.5). One real defect was found and fixed during verification, not merely reasoned about: a `sys.modules` name collision between this milestone's `registry.py` and the Feature Store's own `registry.py`, both bare-imported under the same name — reproduced concretely (34 failures running both test files together) and fixed by loading this milestone's module via `importlib` under a distinct name, touching only the new test file. Zero existing source files modified. Next: WBS 3.2 (Value Store) — **not begun**, per instruction.
