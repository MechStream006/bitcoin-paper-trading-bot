# Milestone Verification Record

| Field | Value |
|---|---|
| **Milestone** | Full Corpus Conformance — Sprint D0b (funding, perp, spot) |
| **WBS item(s)** | 4.0, **Sprint D0b** |
| **Roadmap phase** | Phase 4 — Dataset Builder (`phase_4_dataset_builder_roadmap.md` v1.0.2) |
| **Authorisation** | `DS-DEC-001`, `DS-DEC-002`, `G-CORPUS` (ratified 2026-08-06); D0b authorised after `WBS 4.0.1` |
| **Standard version referenced** | 1.0.0 |
| **Date** | 2026-08-06 |
| **Implementer** | Claude Opus 5 |
| **Reviewer** | *(pending — Rohith)* |
| **Baseline** | `1cb74b3fa58fea38f7a364db4b7e88e29d96d028` |
| **Completes** | `ADR-I012` §12 Tier C prerequisite — every partition now carries a certification verdict |

**WBS 4.1 is not begun.** No `research/dataset_builder/` exists. This milestone extends `backfill_conformance.py` only.

---

## 0. Design Summary

### 0.1 The pilot-proven mechanism, extended additively

D0b registered three sources alongside dvol. Every constant is **read from the module that owns it**, never restated:

| Source | Constants from | Conditioning |
|---|---|---|
| `funding` | `ingest/funding.py` — `DATASET`, `SYMBOL_LAKE`, `CONDITIONING`, `NORMALIZATION_CONTRACT_VERSION` | own text |
| `spot` | `ingest/binance_ohlcv.py` + `build_corpus.SYMBOL` | `_conditioning("spot")` |
| `perp` | `ingest/binance_ohlcv.py` + `build_perp_corpus.DATASET`/`SYMBOL` | `_conditioning("perp")` |

**`binance_ohlcv._conditioning()` is a function of dataset, and spot's and perp's texts genuinely differ** (verified). Conflating them would have silently mislabelled 66 partitions' population statement — so a dedicated test asserts they stay distinct, and TV3 confirmed it by breaking it.

### 0.2 One hardcode removed rather than three added

D0a's source spec carried `ts_column`. Extending that pattern would have meant transcribing `"timestamp_ms"` for funding and `"open_time"` for spot/perp — three values restated away from their source.

Instead `conform_partition()` now reads `ts_column` from **the partition's own manifest**. Verified prerequisite: **all 274 manifests carry it**, uniform per dataset (`funding: timestamp_ms`, others: `open_time`). This is per-partition accurate, self-correcting, and removes the last transcribed value from the module. Two D0a tests were updated accordingly (§3 REG5).

### 0.3 The drift check became source-generic

`verify_sample()` was dvol-specific at D0a. Each source now supplies a `refetch(year, month)` callable, so the ratified hybrid method's sampled re-download applies to all four datasets rather than one. It remains **opt-in** (`--verify-sample N`), reports drift without landing it, and — as at D0a — was exercised against stubs, not live network.

## 1. Build Verification (BV)

- [x] **BV1** — `py_compile` clean.
- [x] **BV2** — No new dependency; the three new sources import modules already present.
- [x] **BV3** — Verified in `btc_bot`; correctly skipped in base env (no pandas).
- [x] **BV4** — Module-form invocation unchanged (`python -m ingest.backfill_conformance`), per `INC-0004`'s mechanism documented at D0a.

## 2. Test Verification (TV)

- [x] **TV1** — `tests/test_backfill_conformance.py` extended to **52 tests** (+17): D0bSourceRegistry (5), TsColumnFromManifest (2), RealCorpusFullyConformed (7 incl. 4 parametrised), plus 3 D0a tests updated.
- [x] **TV2** — House style unchanged. The new real-corpus tier reads **manifests only, never parquet** — a deliberate contrast with D0a's exhaustive `C4` test, which was the `F4` scaling concern. Suite time rose only 101s→102s despite the corpus growing 788 KB → 183 MB.
- [x] **TV3** — **Sabotage: conflate spot/perp conditioning** (`_conditioning(dataset)` → `_conditioning("spot")`). Result: `test_spot_and_perp_conditioning_are_not_conflated` and `test_every_source_has_a_distinct_conditioning_statement` both failed. Reverted; grep confirms no marker; 52/52 re-confirmed.
- [x] **TV4** — Refusal paths: an unregistered dataset still refuses **by name** (`nonsense`, `options`, `""`); `verify_sample` refuses the same way; `C4` violation still aborts before landing (D0a tests unchanged and passing).
- [x] **TV5** — Property/invariant tests: **constants sourced, not restated** (asserted equal to `funding.CONDITIONING`, `binance_ohlcv._conditioning("spot"/"perp")`); **spot ≠ perp conditioning**; **all four conditionings distinct**; **`ts_column` absent from every spec** and a source guard that `conform_partition` uses `man["ts_column"]`; **all 274 conformed**; **`C4` corpus-wide**; **each dataset carries exactly one conditioning and the four differ**; **`plan()` empty for every dataset**.

## 3. Regression Verification (REG)

- [x] **REG1** — `btc_bot`: before **2342 / 19 / 0**; after **2359 / 19 / 0** — delta **+17**, exactly the net new tests.
- [x] **REG2** — base env: **1910 / 62 / 0**, unchanged.
- [x] **REG3** — No pre-existing flake recurred.
- [x] **REG4** — CI workflow unchanged.
- [x] **REG5** — **Three D0a tests updated, all because D0b legitimately changed the facts they asserted:**
  - `test_dvol_is_registered` — dropped the `ts_column` assertion (§0.2), added `refetch` callable check.
  - `test_unregistered_dataset_refuses_by_name` — funding/perp/spot are now *registered*; the test now uses genuinely unregistered names.
  - `test_verify_sample_refuses_an_unwired_dataset` → `..._an_unregistered_dataset` — the drift check is no longer dvol-only.
  No other pre-existing test was touched.

## 4. Performance Verification (PERF)

- [x] **PERF1** — Real conformance: funding 2.3 s, perp 11.3 s, spot 12.7 s — **26 s for 210 partitions / 182 MB**. The `F4` scaling concern proved to be about *test-suite* cost, not the conformance run; addressed by the manifest-only real-corpus tier (§2 TV2).

## 5. Data Verification (DBV)

- [x] **DBV1** — **No database touched.** All four governance DBs `integrity_check=ok`, 0 FK violations; chains valid (8/17/26); `validate_label_governance` CLEAN; `report.py --check` OK (35 families).
- [x] **DBV2** — Dry run proven read-only across all three datasets: whole-tree SHA-256 unchanged, `_raw_lake` unchanged.
- [x] **DBV3** — **Full rehearsal on a disposable copy of all 210 partitions** before touching real data: 210/210 conformed, `C2` byte-identical, `C4` true, idempotent second run (0 pending, tree unchanged, 3 backups), real corpus verified unchanged throughout.
- [x] **DBV4** — **Real execution, all six `G-CORPUS` conditions verified:**

| Condition | Result |
|---|---|
| **C1** backup before write | ✅ 3 backups: `_backup_{funding,perp,spot}_BTCUSDT_2026-08-06T1148xxZ` |
| **C2** supersede never replace | ✅ all 210 `data.parquet` **byte-identical** before/after |
| **C3** dry-run default, `--commit` gated, idempotent | ✅ rehearsal run 2: `0 of N need conformance` for all three |
| **C4** landed hash == `parquet_sha256` | ✅ **274/274 corpus-wide** |
| **C5** result invariance | ✅ evidence-facing read hash `fbee4259…` **identical** before and after |
| **C6** transcript | ✅ this record |

- [x] **DBV5** — **Corpus-wide outcome:**

| Dataset | n | Conformed | C4 | Hash matches disk | schema | Distinct `ingest_ts` | Certification |
|---|---|---|---|---|---|---|---|
| dvol | 64 | 64 | 64 | 64 | {3} | 62 | 60 G / 2 A / 2 R |
| funding | 78 | 78 | 78 | 78 | {3} | **78** | 56 G / 21 A / 1 R |
| perp | 66 | 66 | 66 | 66 | {3} | **66** | 59 G / 6 A / 1 R |
| spot | 66 | 66 | 66 | 66 | {3} | **66** | 53 G / 12 A / 1 R |
| **Total** | **274** | **274** | **274** | **274** | | | |

Every dataset carries **exactly one** conditioning statement, and **all four differ** — no conflation. `_raw_lake` holds 64/78/66/66 revisions, one per partition. Every original `ingest_ts` preserved (all distinct within each dataset). Certification is genuinely differentiated per dataset, not uniform.

- [x] **DBV6** — **D0a's dvol conformance untouched**: 64/64 still conformed, 64/64 still satisfy `C4`.
- [x] **DBV7** — All new artifacts (`_raw_lake/`, three `_backup_*`) confirmed gitignored.

## 6. Backward Compatibility (BC)

- [x] **BC1** — No signature, table, or column changed. `storage/manifest.py`, `storage/raw_lake.py`, `quality/*` all unmodified.
- [x] **BC2** — **No ingest writer modified.** `funding.py`, `binance_ohlcv.py`, `dvol.py` are read for their constants only.
- [x] **BC3** — **WBS 4.0.1's builders unmodified** — the carry-forward fix protects this milestone's output on any future corpus rebuild, and needed no change here.
- [x] **BC4** — Manifests gain four previously-`null` fields; every pre-existing key retains its value (D0a's key-by-key test still passing).
- [x] **BC5** — Repo-wide grep: `backfill_conformance` referenced only by its own test file and the milestone records.

## 7. Rollback Verification (RBV)

- [x] **RBV1** — **Code lever:** `git revert`; the module has no runtime consumer.
- [x] **RBV2** — **Data lever:** three timestamped backups restore all 210 original sidecars.
- [x] **RBV3** — **Strongest lever, and a consequence of the ratified method:** `data.parquet` was never written and `_raw_lake` is a wholly additive tree, so rollback is *deleting `_raw_lake` and restoring 210 JSON sidecars* — not restoring data. Under the rejected re-download method it would have meant re-obtaining content that may no longer exist upstream.
- [x] **RBV4** — No configuration lever, deliberately: disabling `C4` would disable the milestone's safety argument.

## 8. Technical Debt Assessment (TD)

| What | Assessment | Owner |
|---|---|---|
| `--verify-sample` never exercised against live upstream for any dataset | Wired and stub-tested for all four; a live run needs network and would fetch up to 182 MB. The ratified method calls for sampled drift detection, so this remains genuinely owed | Not scheduled — recommend before any future re-ingest |
| `raw_content_hash` carries **re-derived-canonical** provenance for all 274 | Structural (D0a §0.2): the pre-normalisation originals were never persisted. Now corpus-wide rather than dvol-only | Named; unchanged in nature |
| 3 partitions (funding/perp/spot 2026-06) carry the additional weaker provenance from `WBS 4.0.1` F2 | Their `parquet_sha256` was first recorded 2026-08-06, so `C4` gives forward protection only | Named at 4.0.1 |
| `INC-0005`, TD items, roadmap `C7` still unruled (`R3`/`R4`/`R5`) | Unchanged by this milestone | Awaiting rulings |

- [x] **TD3** — No placeholder work: rehearsed on a full disposable copy, then executed for real, with every condition independently queried.
- [x] **TD4** — Nothing new and unscoped; the first item is the one genuine carry-forward.

## 9. Definition of Done (DOD)

- [x] **D1** compiles · **D2** both suites green (2359/19/0; 1910/62/0) · **D3** 17 net new tests · **D4** N/A stated · **D5** no cross-plane import · **D6** three rollback levers · **D7** nothing renamed/removed · **D8** no secret touched · **D9** independently revertible · **D10** no new mandatory field · **D11** all sections complete.
- [x] **P4-D2** — Every refusal has a negative test.
- [x] **P4-D4** — Real-data transcript captured in-session (§5).

## 10. Reviewer Checklist (RC)

- [x] **RC1** — Milestone corresponds to Sprint D0b exactly; WBS 4.1 not begun
- [x] **RC2** — No ADR or roadmap file modified
- [x] **RC3** — The §0.2 decision (drop `ts_column` from specs, read it per-partition) is accepted *(basis: `OD-8`, Owner Decision Pack 2026-08-08)*
- [x] **RC4** — The three D0a test updates (§3 REG5) are accepted *(basis: `OD-8`)*
- [x] **RC5** — Corpus spot-checked: 274/274 conformed, four distinct conditionings
- [x] **RC6** — Three backups confirmed present and readable
- [x] **RC7** — Sign-off

  **Reviewer:** Rohith Suresh  **Date:** 2026-08-08

  *Signature block added 2026-08-08 (audit finding `E-1` / `TD-4.12-5`). These three WBS 4.0 records carried a full RC checklist but no place to record sign-off, so they could not be signed as they stood. No existing content was altered.*


## Files Changed

| File | Change |
|---|---|
| `research/ingest/backfill_conformance.py` | 3 new sources; `ts_column` from manifest; `verify_sample` made source-generic |
| `tests/test_backfill_conformance.py` | +17 net tests; 3 D0a tests updated |
| `docs/.../wbs-4.0-d0b-full-corpus-conformance.md` | **New** — this record |
| `research/data/raw/{funding,perp,spot}/**/_manifest.json` | **Real data**: 210 sidecars gain 4 previously-`null` fields (gitignored) |
| `research/data/raw/_raw_lake/{funding,perp,spot}/**` | **New** — 210 content-addressed revisions (gitignored) |
| `research/data/raw/_backup_{funding,perp,spot}_*` | **New** — pre-write backups (gitignored) |

**Unchanged and verified so:** all 210 `data.parquet` files; every ingest writer; both corpus builders; `storage/*`; `quality/*`; all four governance databases.

## Summary

Shipped Sprint D0b, completing WBS 4.0. **All 274 corpus partitions are now conformed** — each carrying a certification verdict, a conditioning statement, and a `_raw_lake` revision reference — which removes `ADR-I012` §12 Tier C's blocking prerequisite: a conforming Dataset Builder can now seal against real data. All six `G-CORPUS` conditions were verified, none waived, with `C4` holding **274/274 corpus-wide** and `C5` proven by an identical evidence-facing read hash before and after. Three sources were registered additively on the pilot-proven mechanism, every constant read from its owning module rather than restated; because `binance_ohlcv._conditioning()` is a function of dataset and spot's and perp's texts genuinely differ, a dedicated test guards against conflating them — and TV3 confirmed that guard by breaking it. Rather than transcribe three `ts_column` values away from their source, the module now reads it from each partition's own manifest, removing the last hardcoded value; this required updating three D0a tests, each because D0b legitimately changed the fact they asserted. The `F4` scaling concern proved to be about test-suite cost rather than the conformance run — 210 partitions across 182 MB conformed in 26 seconds, and the new real-corpus tier reads manifests only, so suite time rose by one second. `WBS 4.0.1`'s carry-forward fix now protects this output against any future corpus rebuild. **WBS 4.1 is not begun.**
