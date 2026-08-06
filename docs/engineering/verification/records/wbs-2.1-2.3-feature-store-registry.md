# Milestone Verification Record

| Field | Value |
|---|---|
| **Milestone** | Feature Registry + Value Store — `research/feature_store/registry.py`, `value_store.py` |
| **WBS item(s)** | 2.1, 2.2, 2.3 (the first Feature Store milestone — Sprint N+1 per the roadmap's Sprint Plan) |
| **Roadmap phase** | Phase 2 — Feature Store |
| **Standard version referenced** | 1.0.0 |
| **Date** | 2026-08-05 |
| **Implementer** | Claude Sonnet 5 (paired session) |
| **Reviewer** | *(pending — Rohith)* |

**Recorded as one combined record**, mirroring the precedent set for WBS 0.1–0.4 and 1.2–1.5: the roadmap's own Sprint Plan groups 2.1–2.3 as one exit criterion ("Feature registry accepts a hand-written test Specification; Tier A/B validation refuses a malformed one"), not three independent deliverables.

**Scope discipline, stated up front.** This milestone is **entirely new, additive code**. It does not modify any existing file — the first milestone in this roadmap's execution so far for which that is true. It does not import, wrap, or depend on any real production feature; every Specification exercised in testing is hand-written, per the roadmap's own exit criterion. Wrapping the ~20 existing shared derived features (WBS 2.4) is explicitly **not** part of this milestone.

---

## 1. Build Verification (BV)

- [x] **BV1** — `python -m py_compile research/feature_store/registry.py research/feature_store/value_store.py` → `COMPILE OK` (verified in both the base env and `btc_bot`)
- [x] **BV2** — Binding resolution (`register_binding`/`get_binding`/`UnknownBindingError`) is a deliberate, cited reuse of `research/evidence_library/evidence_governance.py`'s `AdmissibilityRule`/`register_rule()`/`get_rule()` pattern — not a new mechanism. Module structure (`init_db()`, content-addressed hashing, append-only triggers) follows the established `receipt_ledger.py`/`raw_lake.py` conventions.
- [x] **BV3** — No new dependency. Both modules are pure stdlib (`sqlite3`, `json`, `hashlib`, `dataclasses`) — they run in **any** environment, including the base env with no research dependencies installed at all. This is the first Phase 2 module with that property.
- [x] **BV4** — Verified under both the base env (Python 3.13.12) and `btc_bot` (Python 3.10.20) — no environment-specific code.

## 2. Test Verification (TV)

- [x] **TV1** — `tests/test_feature_store_registry.py` (new, 34 tests: Feature Definitions, Binding Resolution, Tier A Completeness, Tier B Equivalence, Phase Transitions) and `tests/test_feature_store_value_store.py` (new, 16 tests: Cache Tier, Materialisation Tier, `resolve()`) — **50 new tests**.
- [x] **TV2** — House style confirmed (`class TestXxx:`, `tmp_path` fixture, plain `assert`).
- [x] **TV3** — Empirically verified twice, not merely reasoned: (a) temporarily disabled the `input_requirements` completeness check in `registry.py` — `test_missing_input_requirements_is_rejected_and_persisted` failed with `Failed: DID NOT RAISE`. (b) temporarily disabled the divergence check in `value_store.py`'s `materialize()` — `test_differing_content_at_the_same_address_raises_divergence` failed identically. Both reverted; full suite re-confirmed green.
- [x] **TV4** — Every refusal path this milestone introduces has a dedicated negative test: nine distinct Tier A field-completeness refusals (`TestTierACompleteness`), Tier B failure by returned-`False` and by raised-exception (`TestTierBEquivalence`), illegal phase-transition arcs and stale-phase concurrency guards (`TestPhaseTransitions`), unresolved binding pairs (`TestBindingResolution`), and both `DivergenceEvent` triggers — a write-time content mismatch and a read-time corrupted-hash mismatch (`TestMaterializationTier`).
- [x] **TV5** — Property/invariant tests: content-addressing (`test_registering_identical_content_twice_is_idempotent`, `test_different_parameters_produce_different_hashes`); append-only Definitions (`test_definitions_are_never_updated_in_place`/`_deleted`); "materialised always wins over cache and over a fresh computation" (`TestResolve`'s priority-ordering tests, using an `AssertionError`-raising `compute_fn` to prove it is never even called).

## 3. Regression Verification (REG)

- [x] **REG1** — Base env: **1565 passed, 27 skipped, 0 failed** (before: 1515/27 — delta is exactly the 50 new tests, zero pre-existing test affected). `btc_bot` env: the 50 new tests, **50/50 passed**, confirming environment-independence directly rather than assuming it.
- [x] **REG2** — Skip count unchanged (27→27) — expected, since neither new file needs `pytest.importorskip` (no research dependency).
- [x] **REG3** — No flakes observed.
- [x] **REG4** — CI unchanged.

## 4. Performance Verification (PERF)

**N/A** — no corpus-scale loop; every operation is a single-row SQLite read/write keyed by content hash, same cost class as every other registry in this codebase (`receipt_ledger.py`, `contract_registry.py`).

## 5. Database Verification (DBV)

**Applicability: YES** — this milestone introduces two new SQLite schemas.

- [x] **DBV1** — Additive only: two wholly new tables (`feature_definitions`, `feature_specifications`) in `registry.py`, two more (`feature_value_cache`, `feature_value_materialized`) in `value_store.py`. No existing table touched.
- [x] **DBV2/DBV3** — No migration script exists or is needed — there is no pre-existing `feature_store.db` anywhere in the repository for a migration to apply to; `init_db()`'s `CREATE TABLE IF NOT EXISTS` is the entire schema-ensure story for a brand-new database, idempotently confirmed by `test_get_definition_missing_returns_none`-style repeated-fixture-creation across all 50 tests.
- [x] **DBV4** — N/A — no governance ledger touched; `feature_store.db` is a new, independent database file, not `ledger.db`.
- [x] **DBV5** — N/A — no real/production database exists yet for this store; nothing to back up.
- [x] **DBV6** — N/A — `governance_verification.py`'s `SCHEMA_FINGERPRINT` covers `ledger.db` only; `feature_store.db` is a separate database with its own lifecycle, out of that manifest's scope by design (per IQROS-P2 single-writer-per-zone, this is correctly a **different** zone).

## 6. Backward Compatibility (BC)

- [x] **BC1–BC3** — N/A in the strongest sense: **zero existing functions, tables, columns, or CLI flags were touched.** `git status` confirms only new, untracked files.
- [x] **BC4** — N/A for the same reason — nothing existing to be additive *to*.
- [x] **BC5** — Confirmed by direct search: no existing file anywhere in the repository imports `research/feature_store/` (it did not exist before this milestone), so there are zero existing consumers to check compatibility against.

## 7. Rollback Verification (RBV)

- [x] **RBV1** — Delete `research/feature_store/` and its two test files. Nothing else references them.
- [x] **RBV2** — No production data touched — confirmed, real ledger unchanged (70/26/13/142 rows) before and after.
- [x] **RBV3** — Not exercised; no consumer depends on this module yet (WBS 2.4, the first real consumer, is a separate future milestone). Stated explicitly per the standard's requirement.
- [x] **RBV4** — N/A — no governance record in scope.

## 8. Technical Debt Assessment (TD)

| What | Why deferred | Closed by (WBS ID) | Blast radius if never closed |
|---|---|---|---|
| Phase transitions guarded in Python (allowed-arc table + `WHERE`-clause concurrency check on `UPDATE`), not by a database-level trigger like `contract_lifecycle.py`'s stricter precedent | Appropriate for this milestone's scope; a DB-level guard is real, precedented extra work (`contract_lifecycle.py` was its own dedicated PR in this codebase's history) not asked for here | Not scheduled — future hardening, only if this registry needs `contract_lifecycle.py`'s stronger tamper-evidence property | Low today — single-writer, no concurrent-writer scenario exists yet; the Python guard already refuses concurrent/stale-phase writes correctly (tested) |
| Content hashing uses plain `sort_keys` JSON, not `receipts.py`'s stricter decimal-string canonicalisation | `receipts.py`'s convention exists for cross-platform governance-receipt permanence (years-long, cross-machine reproducibility); feature-specification hashing has no such requirement yet | Not scheduled | Low — `parameters` values in the hand-written test specs are simple (ints); a future Specification with float parameters could see hash instability across Python/platform versions, worth revisiting once real (WBS 2.4) parameter values are known |
| `Tier A`'s `input_requirements` check confirms shape (`source`/`fields` present) but does not deep-validate admissibility-floor/lane sub-structure against `research/admissibility/`'s vocabulary | That coupling belongs to whichever future milestone resolves inputs against real, certified data — premature here, before any real input exists | WBS 2.4+ | None today — no consumer relies on floor/lane validation yet |
| No citation-index check in `retire_specification()` (ADR-I010 §11: "Retirement requires zero citations") | The citation index is `ADR-I012`'s Dataset Builder, not built yet (Phase 4, much later) | Phase 4 | None today — nothing cites a Feature Specification yet |

- [x] **TD3** — No placeholder work; every function has a complete, tested implementation. All four debt items above are pre-existing scope boundaries stated in the module's own docstring, not gaps discovered after the fact.
- [x] **TD4** — None of the above is long-lived urgent debt; all are correctly-deferred future-phase dependencies already named in the roadmap itself (`ADR-I012` citation index, WBS 2.4's real bindings). Not promoted to `CLAUDE.md` §19.

## 9. Definition of Done (DOD)

- [x] **D1** — Compiles/imports cleanly in both environments
- [x] **D2** — Full suite passes, zero new failures (1565/27, both environments)
- [x] **D3** — 50 new tests, all passing
- [x] **D4** — N/A, explicitly stated (§5)
- [x] **D5** — Zero imports from `research/` into any operational-plane module (confirmed — this module imports nothing outside the stdlib)
- [x] **D6** — Rollback lever named (§7): delete the directory
- [x] **D7** — No existing signature/table/column touched — vacuously true, nothing existing was touched
- [x] **D8** — No `.env`, credential, or secret referenced
- [x] **D9** — Independently deployable — merging this alone changes no existing behaviour at all (confirmed the strongest way: zero existing files modified)
- [x] **D10** — Every mandatory field (`ADR-I010` §8.2) is rejected when absent, verified by nine dedicated Tier A negative tests plus the Tier B negative tests
- [x] **D11** — All sections of this template completed, including explicit N/A statements for §4 and the applicable rows of §5/§6/§7

## 10. Reviewer Checklist (RC)

*Pending — Rohith.*

- [ ] **RC1** — Milestone corresponds to WBS 2.1–2.3 exactly, no scope creep into 2.4+
- [ ] **RC2** — No file under `docs/architecture/` touched
- [ ] **RC3** — "Files Changed" list matches `git status` exactly (should show only new, untracked files)
- [ ] **RC4** — At least two tests read in full, confirmed to exercise real behaviour
- [ ] **RC5** — Full-suite counts reproduced independently in both environments
- [ ] **RC6** — Rollback lever confirmed
- [ ] **RC7** — Full diff read — should be 100% new files, zero modifications
- [ ] **RC8** — Sign-off

  **Reviewer:** _______________  **Date:** _______________

---

## Files Changed

| File | Type |
|---|---|
| `research/feature_store/__init__.py` | New — empty package marker |
| `research/feature_store/registry.py` | New — Feature Definitions, Feature Specifications, binding resolution, Tier A/B validation, lifecycle transitions |
| `research/feature_store/value_store.py` | New — cache tier, materialisation tier, `resolve()` |
| `tests/test_feature_store_registry.py` | New — 34 tests |
| `tests/test_feature_store_value_store.py` | New — 16 tests |

## Summary

Shipped the first Feature Store milestone: a generic, self-contained registry and value store proven entirely with hand-written test Specifications, exactly per the roadmap's own Sprint exit criterion. Zero existing files modified — the cleanest milestone in this roadmap's execution so far. Binding resolution deliberately reuses `evidence_governance.py`'s already-audited `register_rule()`/`get_rule()` pattern rather than inventing a new one. Four scoped simplifications are named explicitly in the module docstrings and this record's Technical Debt table, each tied to a specific, already-identified future milestone (WBS 2.4, `ADR-I012`'s citation index) rather than left as silent gaps. Next: WBS 2.4 (wrapping the ~20 existing shared derived features as registered Specifications).
