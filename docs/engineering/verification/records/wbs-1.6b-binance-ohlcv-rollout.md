# Milestone Verification Record

| Field | Value |
|---|---|
| **Milestone** | WBS 1.6 rollout — `research/ingest/binance_ohlcv.py` (third and final ingest path) |
| **WBS item(s)** | 1.6 (repeats 1.2–1.5's proven pattern) |
| **Roadmap phase** | Phase 1 — Storage Plane Conformance |
| **Standard version referenced** | 1.0.0 |
| **Date** | 2026-08-05 |
| **Implementer** | Claude Sonnet 5 (paired session) |
| **Reviewer** | *(pending — Rohith)* |

**Path selection.** `binance_ohlcv.py` (1,440 rows/day, ~43,200/month) is the largest-volume path, rolled out last, after `dvol.py` and `funding.py`. This completes WBS 1.6 for every ingest path under `research/ingest/` (per the roadmap's `9.2` Repository Impact table: "`research/ingest/*.py` (per path, one increment each)") — **Phase 1 is now WBS-complete**, in the sense defined by the roadmap's 2026-08-05 terminology reconciliation (§6): every item `1.1`–`1.6` has shipped for every path. (Phase 1 was already DoD-satisfied since the pilot, per §17 — this milestone closes the second, stronger sense.)

**What changed vs. `dvol.py`'s pattern.** Two structural adaptations, both disclosed:

1. Same as `funding.py`: kept the file's own pre-existing ad-hoc `_write_manifest()` rather than migrating to `storage.manifest.write_manifest()`.
2. `dataset` ("spot"/"perp") is a **per-call parameter** here, not a fixed module constant like `DATASET` in the other two files — so the mandatory conditioning statement (WBS 1.5's pattern) is implemented as a function, `_conditioning(dataset)`, rather than a module-level constant. Still mandatory in the same sense: always computed and always passed, never omitted or blank.

---

## 1. Build Verification (BV)

- [x] **BV1** — `"<btc_bot>/python.exe" -m py_compile research/ingest/binance_ohlcv.py tests/test_binance_ohlcv_pilot_conformance.py` → `COMPILE OK`
- [x] **BV2** — Imports match this file's own pre-existing convention, identical pattern to `dvol.py`/`funding.py`.
- [x] **BV3** — No new dependency.
- [x] **BV4** — Verified under `btc_bot` (Python 3.10.20).

## 2. Test Verification (TV)

- [x] **TV1** — `tests/test_binance_ohlcv_pilot_conformance.py` (new, 16 tests) — one class per WBS pattern plus `TestBackwardCompatibility`, plus two file-specific additions: `test_perp_dataset_lands_under_its_own_tree` (the `dataset` parameter has no analogue in `dvol.py`/`funding.py`) and `test_ccxt_fallback_path_still_reachable_when_bulk_returns_none` (this file's two-tier fetch, unique among the three writers).
- [x] **TV2** — House style confirmed.
- [x] **TV3** — Empirically verified: temporarily disabled `land_raw()`'s call (stub, `is_new_revision=False`); `test_reingest_different_content_creates_a_new_revision_never_overwriting` failed with `assert False is True`. Reverted; re-confirmed green.
- [x] **TV4** — Shared `land_raw()` refusal path already fully tested in `tests/test_raw_lake.py`; this file's own tests confirm it always supplies a non-blank, dataset-specific value (`TestConditioningMandatory`, including `test_spot_and_perp_conditioning_differ`).
- [x] **TV5** — "A low ceiling never blocks the write"; "the bulk/CCXT fallback still reaches the full new pipeline, not just the bulk path" (`test_ccxt_fallback_path_still_reachable_when_bulk_returns_none` — proves `land_raw`/certification/conditioning all fire correctly even on the fallback branch, not only the primary one).

## 3. Regression Verification (REG)

- [x] **REG1** — `btc_bot`: **100 passed, 0 failed**, across every research-dependent test file this whole Phase 1 effort produced (`test_raw_lake.py`, `test_quality_admissibility.py`, `test_dvol_pilot_conformance.py`, `test_funding_pilot_conformance.py`, `test_binance_ohlcv_pilot_conformance.py`, `test_storage_manifest.py`, `test_ingest_normalization_contract_version.py`). Base env: **1515 passed, 27 skipped, 0 failed**.
- [x] **REG2** — Skip delta from the pre-WBS-1.6 baseline (1515/25): +2 total across `funding` (+1) and this file (+1), both fully `pandas`-gated at module level, 0 passes contributed in the base env — reconciled exactly.
- [x] **REG3** — No flakes.
- [x] **REG4** — CI unchanged.

## 4. Performance Verification (PERF)

**Applicability: borderline N/A, addressed rather than skipped** — this is the highest-volume path (~43,200 rows/month). `content_hash()` (Parquet write + SHA-256) and `run_gauntlet()` (8 checks) both run once per monthly partition, not per row — confirmed O(rows) per partition, same complexity class as the pre-existing `_normalize()`/`write_partition()` calls already in this hot path. No new per-row Python loop was introduced; all new operations are vectorised pandas/pyarrow calls. Local test evidence: the full 43,200-row synthetic-month test class runs in ~3 seconds total across 16 tests, no individual test exceeding a fraction of a second.

## 5. Database Verification (DBV)

**N/A** — no `.db` file touched. Real ledger re-verified unchanged (70/26/13/142 rows).

## 6. Backward Compatibility (BC)

- [x] **BC1** — `ingest_month()`/`ingest_range()` signatures unchanged.
- [x] **BC2** — No rename/removal; manifest gains keys, loses none.
- [x] **BC3** — CLI unchanged (`--dataset`, `--symbol`, `--start`, `--end`, `--base-dir`, `--force`).
- [x] **BC4** — New keys additive, confirmed directly (`TestBackwardCompatibility`).
- [x] **BC5** — No known real downstream consumer beyond this file's own CLI/tests (no `build_binance_corpus.py`-equivalent found in a repo-wide search); the bulk/CCXT fallback behaviour itself — the one piece of pre-existing logic most likely to interact with the new code — was directly re-verified end to end (`test_ccxt_fallback_path_still_reachable_when_bulk_returns_none`).

## 7. Rollback Verification (RBV)

- [x] **RBV1** — Revert the commit to `binance_ohlcv.py`; the four new imports and their call sites are removed together.
- [x] **RBV2** — No production data touched.
- [x] **RBV3** — Not exercised; stated explicitly.
- [x] **RBV4** — No governance record in scope.

## 8. Technical Debt Assessment (TD)

| What | Why deferred | Closed by | Blast radius if never closed |
|---|---|---|---|
| Ad-hoc `_write_manifest()` retained, not migrated to the formal hashed writer | Same reasoning as the `funding.py` record — out of WBS 1.6's scope | Not scheduled | None today |
| `_ROWS_PER_DAY = 1440` (pre-existing module constant) remains unused within this file itself (it duplicates `gauntlet.py`'s own internal `_RECORDS_PER_DAY["spot"/"perp"]`) | Pre-existing, not introduced by this milestone; removing it would be unrelated cleanup | Not scheduled | None — dead but harmless |

- [x] **TD3/TD4** — No placeholder work.

**Phase 1 WBS-completion note.** With this path shipped, every item in `docs/engineering/implementation_roadmap_v1.md` §6's Phase 1 table (`1.1`–`1.6`, across all three ingest paths) is complete. Per the roadmap's own 2026-08-05 reconciliation (§6), this does **not** newly unblock Phase 2 — Phase 2 was never gated on it, only on Phase 0 (already complete). This note exists to close the loop the reconciliation opened, not to claim a new dependency was satisfied.

## 9. Definition of Done (DOD)

All eleven criteria satisfied, identical reasoning to the `dvol.py`/`funding.py` records.

## 10. Reviewer Checklist (RC)

*Pending — Rohith.*

## Summary

`binance_ohlcv.py` — the largest-volume, most structurally distinct (dual fetch path, per-call `dataset` parameter) of the three ingest writers — now carries the identical raw-landing, certification, and conditioning pattern as `dvol.py` and `funding.py`. 16 new tests, all passing, including two path-specific additions (per-dataset landing, fallback-path coverage). TV3 empirically confirmed. Zero behaviour change to the pre-existing canonical output, including the bulk→CCXT fallback. This is the third and final path — WBS 1.6, and with it Phase 1's full WBS item list, is complete.
