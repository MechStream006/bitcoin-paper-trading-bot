# Milestone Verification Record

| Field | Value |
|---|---|
| **Milestone** | WBS 1.6 rollout — `research/ingest/funding.py` (second ingest path) |
| **WBS item(s)** | 1.6 (repeats 1.2–1.5's proven pattern) |
| **Roadmap phase** | Phase 1 — Storage Plane Conformance |
| **Standard version referenced** | 1.0.0 |
| **Date** | 2026-08-05 |
| **Implementer** | Claude Sonnet 5 (paired session) |
| **Reviewer** | *(pending — Rohith)* |

**Path selection.** `funding.py` (3 rows/day, ~90/month) is the second-smallest-volume remaining path, rolled out after `dvol.py` (1/day, done) and before `binance_ohlcv.py` (1,440/day, largest) — same smallest-volume-first ordering used to select the original pilot, extended consistently rather than re-derived.

**What changed vs. `dvol.py`'s pattern.** One structural adaptation, explicitly disclosed: `funding.py` retains its own pre-existing local `_write_manifest()` ad-hoc JSON writer (not `storage.manifest.write_manifest()`, which `dvol.py` already used before this rollout). This milestone adds the four new pieces of information (`conditioning`, `admissibility`, `raw_content_hash`, plus WBS 1.1's `normalization_contract_version`) as new keys in `funding.py`'s own `meta` dict, exactly as WBS 1.1 already did for this same file — not a migration to the formal hashed writer, which would be a separate, out-of-scope unification (see Technical Debt). Everything else — `land_raw()`, `run_gauntlet()`, `partition_ceiling()` — is the identical, unmodified module reused verbatim from `dvol.py`'s rollout.

---

## 1. Build Verification (BV)

- [x] **BV1** — `"<btc_bot>/python.exe" -m py_compile research/ingest/funding.py tests/test_funding_pilot_conformance.py` → `COMPILE OK`
- [x] **BV2** — Imports match `funding.py`'s own pre-existing root-relative dotted convention (`storage.raw_lake`, `quality.gauntlet`, `quality.admissibility`), identical to `dvol.py`'s.
- [x] **BV3** — No new dependency. `storage.raw_lake`/`quality.admissibility` are reused, not reimplemented.
- [x] **BV4** — Verified under `btc_bot` (Python 3.10.20).

## 2. Test Verification (TV)

- [x] **TV1** — `tests/test_funding_pilot_conformance.py` (new, 13 tests), one class per WBS pattern (1.2/1.3/1.4/1.5) plus `TestBackwardCompatibility`, mirroring `test_dvol_pilot_conformance.py`'s structure exactly.
- [x] **TV2** — House style confirmed (`class TestXxx:`, `monkeypatch`, `tmp_path`).
- [x] **TV3** — Empirically verified: temporarily disabled `funding.py`'s `land_raw()` call (stub returning `is_new_revision=False`); `test_reingest_different_content_creates_a_new_revision_never_overwriting` failed with `assert False is True`, exactly as expected. Reverted; re-confirmed green.
- [x] **TV4** — `land_raw()`'s mandatory-`conditioning` refusal path is the shared module already tested in full by `tests/test_raw_lake.py`; not re-tested here (would duplicate, not add, coverage) — `funding.py`'s own tests confirm it always *supplies* a non-blank value (`TestConditioningMandatory`), which is the behaviour specific to this file.
- [x] **TV5** — "A low ceiling never blocks the write" (`test_a_low_ceiling_never_blocks_the_write`); "raw landing captures the un-normalised fetch, faithfully" (implicit in the raw/canonical separation tests).

## 3. Regression Verification (REG)

- [x] **REG1** — `btc_bot`: all research-dependent test files this phase together → **100 passed, 0 failed**. Base env: **1515 passed, 27 skipped** (was 1515/25 before this file existed — `test_funding_pilot_conformance.py` is fully `pandas`-gated at module level, +1 skip; `test_binance_ohlcv_pilot_conformance.py`, added in the same working session, +1 more — see the `binance_ohlcv` record for its own accounting).
- [x] **REG2** — Skip delta reconciled exactly: +1 for this file (fully gated, 0 tests runnable without `pandas`).
- [x] **REG3** — No flakes observed.
- [x] **REG4** — CI unchanged.

## 4. Performance Verification (PERF)

**N/A** — no corpus-scale loop; `funding.py` already operates per-month (≤~90 rows), same cost class as `dvol.py`.

## 5. Database Verification (DBV)

**N/A** — no `.db` file touched. Real ledger re-verified unchanged (70/26/13/142 rows) after this milestone.

## 6. Backward Compatibility (BC)

- [x] **BC1** — `ingest_month()`'s signature unchanged. Its local `_write_manifest()` helper's signature unchanged (still `(part_dir, meta) -> Path`).
- [x] **BC2** — No rename/removal. `funding.py`'s manifest shape gains keys, loses none.
- [x] **BC3** — CLI unchanged.
- [x] **BC4** — New `meta`/result-dict keys are strictly additive, confirmed by `TestBackwardCompatibility::test_all_pre_existing_manifest_fields_still_present` / `test_pre_existing_result_dict_keys_still_present`.
- [x] **BC5** — `research/ingest/funding.py` has no known real downstream consumer analogous to `build_dvol_corpus.py` (repo-wide search confirms no `build_funding_corpus.py` or equivalent exists yet) — nothing to check compatibility against beyond this file's own tests and the CLI.

## 7. Rollback Verification (RBV)

- [x] **RBV1** — Revert the commit to `funding.py`; `land_raw`/`run_gauntlet`/`partition_ceiling` imports and calls are removed together, restoring the exact pre-milestone `ingest_month()`.
- [x] **RBV2** — No production data touched.
- [x] **RBV3** — Not exercised; no downstream consumer depends on the new fields yet, stated explicitly.
- [x] **RBV4** — No governance record in scope.

## 8. Technical Debt Assessment (TD)

| What | Why deferred | Closed by | Blast radius if never closed |
|---|---|---|---|
| `funding.py` (and `binance_ohlcv.py`) keep their own ad-hoc `_write_manifest()` rather than migrating to `storage.manifest.write_manifest()` | A manifest-writer unification is a separate, larger, out-of-scope change (would add SHA-256 hashing + formal schema to files that never had it); WBS 1.6 asks only to repeat the *raw/certification/conditioning* pattern, not unify manifest mechanisms | Not scheduled — a future, separately-authorised milestone, if ever desired | None today — both ad-hoc writers work correctly and consistently for their own files; the only cost is that `dvol.py`'s manifests carry a `parquet_sha256`/formal `schema_version` that `funding.py`'s/`binance_ohlcv.py`'s do not (a pre-existing divergence, not introduced here) |
| No real downstream consumer exists for `funding.py`'s ingest results (unlike `build_dvol_corpus.py`) | Out of scope — nothing to build | N/A | None |

- [x] **TD3/TD4** — No placeholder work; nothing long-lived beyond the above, already logged in the `dvol.py`/1.2–1.5 record for the shared modules.

## 9. Definition of Done (DOD)

All eleven criteria satisfied, identical reasoning to the `dvol.py` record (§9 there) — D1–D9, D11 direct; D10 via the shared `land_raw()` mandatory-conditioning tests plus this file's own always-supplies-a-value tests.

## 10. Reviewer Checklist (RC)

*Pending — Rohith.*

## Summary

`funding.py` now reuses the exact `dvol.py`-proven pattern: raw landing (Z2, content-hash revisioned), certification wired to a real `Admissibility` ceiling via `run_gauntlet()`/`partition_ceiling()`, and mandatory conditioning. One disclosed, deliberate adaptation: kept the file's own pre-existing ad-hoc manifest writer rather than migrating to the formal hashed one (out of WBS 1.6's scope). 13 new tests, all passing; TV3 empirically confirmed; zero behaviour change to the pre-existing canonical output path.
