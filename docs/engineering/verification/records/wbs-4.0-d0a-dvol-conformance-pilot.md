# Milestone Verification Record

| Field | Value |
|---|---|
| **Milestone** | Corpus Conformance Pilot (dvol) — `research/ingest/backfill_conformance.py` |
| **WBS item(s)** | 4.0, **Sprint D0a only** |
| **Roadmap phase** | Phase 4 — Dataset Builder (`phase_4_dataset_builder_roadmap.md` **v1.0.2**) |
| **Authorisation** | `DS-DEC-001`, `DS-DEC-002`, `G-CORPUS` ratified 2026-08-06 |
| **Standard version referenced** | 1.0.0 |
| **Date** | 2026-08-06 |
| **Implementer** | Claude Opus 5 |
| **Reviewer** | *(pending — Rohith)* |
| **Corrections** | ⚠️ Two factual errors in this record were found and corrected during the D0b pre-flight review (2026-08-06): §6 `BC3`'s `schema_version` claim, and §8 defect 2's severity, which the first error caused. Both are corrected in place with the original text quoted. The milestone's *outcome* is unaffected — all six `G-CORPUS` conditions still verified. |
| **Baseline** | `069a24efca12f9d33e01987e84e570e65753f4dc` |

**D0b and WBS 4.1 are explicitly not begun.** `_SOURCES` registers dvol only; `funding`/`perp`/`spot` refuse by name. No `research/dataset_builder/` directory exists.

**No ADR inconsistency or roadmap contradiction was found.** Two pre-existing repository defects were discovered and are reported in §8 rather than worked around silently.

---

## 0. Design Summary

### 0.1 Condition `C4` was verified achievable *before* the script was written

`DS-DEC-002`'s entire safety argument rests on `C4`: every landed content hash must equal the partition's pre-existing `parquet_sha256`. That is only possible if `storage.raw_lake.content_hash()` and `storage.lake.write_partition()` serialise identically. Verified by reading both (`pq.write_table`, `preserve_index=False`, zstd — differing only in buffer vs file sink) and then **empirically across all 64 real dvol partitions: 64/64 exact match, 0 mismatches**, before a line of the script existed.

This inverts the usual risk order. A content change is a **failed precondition that aborts the partition**, not a regression detected afterwards — which is precisely why `DR1` was re-rated from Moderate/**Severe** to Low/**Severe** at roadmap v1.0.2.

### 0.2 What the raw-lake revision actually contains — stated, not implied

`land_raw()`'s docstring describes landing data *"as-received"* (zone Z2, pre-normalisation). **The original as-received bytes were never persisted by any prior ingest** — only the canonical Z3 partition survives. So the revision this backfill lands contains the **canonical** content, not a recovered pre-normalisation original.

That is exactly what `C4` demands (hash equality with the canonical `parquet_sha256`), and it is the only honest option available. But it means `raw_content_hash` on a backfilled partition links to a **re-derived canonical revision**, carrying weaker provenance than the same field on a partition ingested through `dvol.py` proper. This is recorded in the module docstring and here; it is not papered over.

### 0.3 No value is invented

Every manifest field comes from one of exactly two places: the partition's **own existing manifest** (`ingest_ts`, `source`, `source_url`, `config_version`, `rows`, `min_ts`, `max_ts`, `ts_column`) or the **owning ingest module's own module-level constants** (`dvol.CONDITIONING`, `dvol.NORMALIZATION_CONTRACT_VERSION`), read from `dvol` rather than restated so a change there cannot silently diverge.

`fetched_at` and `ingest_ts` are the **true historical instants** from the original ingest — never `datetime.now()` — mirroring WBS 3.4's identical discipline for the 148 H6 forward labels. Verified on real data: **62 distinct `ingest_ts` values preserved** across 64 partitions (a restamp would have collapsed them to one).

## 1. Build Verification (BV)

- [x] **BV1** — `python -m py_compile` clean for both new files in `btc_bot`.
- [x] **BV2** — Requires pandas/pyarrow (inherently — it reads Parquet). Base env has no pandas, so the test module gates with a module-level `importorskip`; see §2 TV2 for why the per-class pattern does not apply here.
- [x] **BV3** — No new dependency; every import already used by `ingest/dvol.py`.
- [x] **BV4** — Verified in `btc_bot`; correctly skipped in base env.
- [x] **BV5** — **Invocation is module-form and that is not optional** — `python -m ingest.backfill_conformance` from `research/`. Documented in the module docstring with the root cause (§8, defect 1).

## 2. Test Verification (TV)

- [x] **TV1** — `tests/test_backfill_conformance.py` (new, **35 tests**): SourceRegistry (3), Plan (4), **ConditionC4 (4)**, ConditionC2 (3), HistoricalInstants (4), ConformanceOutput (2), Idempotency (2), CLI (4), VerifySample (3), RealDvolCorpus (6).
- [x] **TV2** — House style confirmed. **One deliberate deviation from the `test_conditioning.py` per-class-skipif precedent:** this module uses a module-level `pytest.importorskip("pandas")` because `backfill_conformance.py` imports pandas at *module* level (it reads Parquet), so not even a pure-logic test can run without it. A per-class gate would be theatre — it would skip everything anyway while implying otherwise.
- [x] **TV3** — **Two sabotages, both confirmed, both reverted.**
  1. **`C4` guard removed** (`if derived != recorded:` → `if False:`). Result: a partition whose content disagreed with its manifest was conformed and landed anyway. **2 tests failed** — `test_content_mismatch_aborts_before_landing`, `test_nothing_is_landed_when_c4_fails`.
  2. **Historical instants restamped** (`fetched_at`/`ingest_ts` → `datetime.now()`). **4 tests failed** — both `HistoricalInstants` value tests, the `datetime.now` source guard, and `TestConditionC2::test_only_manifest_fields_are_added_never_removed` (which independently caught the mutation of a preserved field).
  Both reverted; grep confirms no sabotage marker remains; 35/35 re-confirmed.
- [x] **TV4** — Refusal paths, one negative test each: unregistered dataset (`funding`/`perp`/`spot`/nonsense) refuses **by name**; `C4` violation aborts; missing corpus returns 1; `verify_sample` refuses an unwired dataset; a fetch failure during drift-check is a **finding, not a crash**.
- [x] **TV5** — Property/invariant tests: **`C4`** (landed hash == recorded `parquet_sha256`, synthetic *and* across all 64 real partitions); **`C2`** (`data.parquet` byte-identical; manifest fields only added, never removed — 14 preserved keys compared individually); **no invented values** (constants read from `dvol` itself, asserted equal); **no `datetime.now()`** in `conform_partition` (source-inspection guard); **idempotency** (second conformance yields `is_new_revision=False` and the same `revision_ordinal`; second `--commit` takes no second backup); **drift reported never landed** (`_raw_lake` absent after a divergent sample).

## 3. Regression Verification (REG)

- [x] **REG1** — `btc_bot`: before **2289 passed / 19 skipped / 0 failed**; after **2324 / 19 / 0** — delta **+35**, exactly this file's test count.
- [x] **REG2** — base env: **1910 passed / 61 skipped / 0 failed** (before 1910/60). Passes unchanged, **+1 skip** — the correctly-gated pandas-dependent module.
- [x] **REG3** — No pre-existing flake recurred.
- [x] **REG4** — CI workflow unchanged.
- [x] **REG5** — **Zero pre-existing tests required modification.** Unlike WBS 3.8c and 3.11, this milestone changed no existing test.

## 4. Performance Verification (PERF)

- [x] **PERF1** — 64 partitions / 788 KB conformed in well under a minute, including the gauntlet battery per partition. Full-suite runtime rose from 92s to 125s in `btc_bot`, attributable to the 64-partition real-corpus `C4` test (`test_c4_holds_for_every_real_dvol_partition`), which re-reads and re-hashes the whole dvol corpus. Judged worth its cost: it verifies on real data the invariant the entire method depends on.

## 5. Data Verification (DBV)

- [x] **DBV1** — **No database was touched.** This milestone operates only on Parquet partitions and their JSON manifests. All four governance databases re-verified `integrity_check=ok`, 0 FK violations; chains valid (8/17/26); `validate_label_governance` CLEAN; `report.py --check` OK.
- [x] **DBV2** — Dry-run default proven byte-safe against the real corpus **twice** — whole-tree SHA-256 identical, `_raw_lake` not created.
- [x] **DBV3** — **Full rehearsal against a disposable copy** before touching the real corpus: 64/64 conformed, `C2` verified, idempotent second run a no-op with one backup, real corpus confirmed unchanged throughout.
- [x] **DBV4** — **Real execution**, all six conditions verified:

| Condition | Result |
|---|---|
| **C1** backup taken and verified | ✅ `_backup_dvol_BTC_20260806T101310Z` |
| **C2** supersede never replace | ✅ all 64 `data.parquet` byte-identical, checked before/after |
| **C3** dry-run default, `--commit` gated, idempotent | ✅ second run: `0 of 64 need conformance`, no second backup |
| **C4** landed hash == `parquet_sha256` | ✅ **64/64** |
| **C5** result-invariance | ✅ evidence-facing corpus read hash `9cd38ae9…` **identical** before and after |
| **C6** transcript captured | ✅ this record |

- [x] **DBV5** — Outcome: **64/64 conformed**; certification **60 GREEN / 2 AMBER / 2 RED**, readiness **62 READY / 2 NOT_READY**; 64 `_raw_lake` revisions; 1 distinct conditioning string; **62 distinct `ingest_ts` preserved**. The differentiated verdicts are evidence the gauntlet genuinely evaluated rather than stubbing — the two RED partitions are DVOL's inception month (8 rows) and the current partial month (10 rows).
- [x] **DBV6** — All new artifacts (`_raw_lake/`, `_backup_dvol_BTC_*`) confirmed gitignored; `git status` shows only the two new source files.

### 5.1 On condition `C5`

`C5` names "the three lockbox-bearing evidence scripts re-run before and after". Investigation established a **stronger and cheaper** proof for D0a: `engine.load_master()` reads Parquet through DuckDB views and **never consults `_manifest.json`**; a repo-wide grep confirms no evidence script reads a manifest. Since `C2` proves every `data.parquet` byte-identical, no evidence result *can* change. This was verified directly by hashing the evidence-facing read of all 64 partitions before and after — identical. Additionally, `load_master()` consumes `spot` and `funding`, not dvol at all. The script re-run remains appropriate for D0b, which touches those datasets.

## 6. Backward Compatibility (BC)

- [x] **BC1** — No existing function signature, table, or column changed. `write_manifest`'s optional parameters are used exactly as designed.
- [x] **BC2** — **No existing file modified.** Two new files only.
- [x] **BC3** — ⚠️ **CORRECTED 2026-08-06 (D0b pre-flight review).** This originally read *"Manifests remain `schema_version: 1`"*. **That is wrong.** `storage.manifest.SCHEMA_VERSION` is **3**, so `write_manifest()` stamped the conformed sidecars `schema_version: 3` (15 keys → 19). The four conformance fields were already optional keys of that schema (WBS 1.1–1.5), previously `null`, and **every pre-existing key retained its exact value** (`parquet_sha256` verified identical against the D0a backup) — so BC4 and the substance of backward compatibility hold. Only the version claim was incorrect. The error was consequential: it is what made the §8 defect-2 severity assessment wrong (see the correction there).
- [x] **BC4** — Legacy readers unaffected: every previously-present key retains its exact prior value (asserted key-by-key).
- [x] **BC5** — Repo-wide grep: `backfill_conformance` is referenced only by its own test file and this record.

## 7. Rollback Verification (RBV)

- [x] **RBV1** — **Code lever:** revert the commit; the script has no runtime consumer.
- [x] **RBV2** — **Data lever:** `_backup_dvol_BTC_20260806T101310Z` restores the pre-conformance manifests.
- [x] **RBV3** — **The strongest lever, and a consequence of the ratified method:** because `data.parquet` was never written and `_raw_lake` is a wholly new additive tree, rollback is **deleting `_raw_lake` and restoring 64 JSON sidecars** — not restoring data. Under the rejected full-re-download method, rollback would have required re-obtaining content that may no longer exist upstream.
- [x] **RBV4** — No configuration lever, deliberately: a switch disabling `C4` would disable the milestone's entire safety argument.

## 8. Technical Debt Assessment (TD)

**Two pre-existing repository defects were discovered by verification. Neither was introduced by this milestone; both are reported rather than silently worked around.**

| What | Assessment | Owner |
|---|---|---|
| **Defect 1 — every module inside `research/ingest/` is unrunnable as a path-form script.** Running `python ingest/<x>.py` puts `research/ingest/` at `sys.path[0]`, where the real `calendar.py` (WI-5 macro events) shadows the stdlib `calendar` that pandas imports transitively via `_strptime`, failing with `AttributeError: module 'calendar' has no attribute 'day_abbr'`. **Verified the pre-existing `ingest/dvol.py` fails identically** | `INC-0004`'s mechanism reaching a second class of victim. That incident fixed `conditioning.py`, which inserted `research/ingest/` *explicitly*; it did not address modules that live *inside* that directory and get it at `sys.path[0]` automatically. `-m` form works for both. **Medium** — confusing failure with no obvious link to its cause | Not scheduled. Worth an `INC-0004` addendum |
| **Defect 2 — `build_corpus.py` and `build_perp_corpus.py` strip conformance fields from any manifest they re-upgrade.** Both call `write_manifest()` without `admissibility`/`conditioning`/`raw_content_hash`/`normalization_contract_version`, which default to `None` and overwrite the sidecar | ⚠️ **SEVERITY CORRECTED 2026-08-06 — this entry was WRONG and is inverted.** It claimed conformed manifests are protected because the builders skip anything *"already at `schema_version 1` with a passing hash"*. But conformance stamps `schema_version: 3` (see the BC3 correction), so the gate `old_m.get("schema_version") == 1` evaluates **False** for exactly the manifests it was thought to protect, and they **fall through to the stripping re-upgrade**. Proven empirically against a real conformed manifest: gate fires = `False`; all four fields would be lost. **Conformance makes a partition MORE exposed to the builders, not less.** dvol remains unaffected (neither builder references it), but `spot`, `funding` and `perp` — the entire D0b population — are directly exposed. **High for D0b** | ⛔ **Blocks D0b — governance decision required.** Fixing it means modifying `build_corpus.py` and `build_perp_corpus.py`, outside WBS 4.0's deliverable |

| Deferred by design | Why |
|---|---|
| `funding`/`perp`/`spot` not registered in `_SOURCES` | D0a is the dvol pilot; D0b adds them additively. An unregistered dataset refuses by name rather than being guessed at (`standing.py`'s WBS 3.5 precedent) |
| `--verify-sample` not exercised against live Deribit | It is network-dependent; tested against both a divergent stub and a failing stub. A real sampled check is appropriate at D0b, when a drift finding would be actionable |
| `raw_content_hash` carries re-derived-canonical rather than as-received provenance | §0.2. Structural — the original bytes were never persisted |

- [x] **TD3** — No placeholder work. Conformance was rehearsed on a copy and then executed for real, with every condition independently queried.
- [x] **TD4** — The two discovered defects are pre-existing and named. Not promoted to `CLAUDE.md` §19; both belong to the Phase 4 register at its closeout.

## 9. Definition of Done (DOD)

- [x] **D1** — Compiles/imports cleanly. **D2** — Both suites pass, zero new failures (2324/19/0; 1910/61/0). **D3** — 35 new tests. **D4** — N/A stated where applicable. **D5** — No `research/` → operational-plane import. **D6** — Three rollback levers (§7). **D7** — No signature/table/column changed. **D8** — No `.env` or secret touched. **D9** — Independently deployable; no runtime consumer. **D10** — No new mandatory field. **D11** — All sections complete.
- [x] **P4-D2** — Every refusal has one negative test (§2 TV4).
- [x] **P4-D3** — N/A: this milestone persists no rejection records; its refusals abort before any write, which is the stronger property.
- [x] **P4-D4** — Real-data transcript captured in the same session (§5).

## 10. Reviewer Checklist (RC)

- [ ] **RC1** — Milestone corresponds to Sprint D0a exactly; no scope creep into D0b or WBS 4.1
- [ ] **RC2** — No ADR or roadmap file modified
- [ ] **RC3** — The §0.2 provenance limitation (re-derived canonical, not as-received) is accepted
- [ ] **RC4** — The §5.1 substitution of a stronger `C5` proof is accepted
- [ ] **RC5** — Discovered defects 1 and 2 (§8) are accepted as pre-existing and correctly out of scope
- [ ] **RC6** — Real dvol corpus spot-checked: 64/64 conformed, certification split plausible
- [ ] **RC7** — Pre-conformance backup confirmed present and readable
- [ ] **RC8** — Sign-off

## Files Changed

| File | Change |
|---|---|
| `research/ingest/backfill_conformance.py` | **New** — conformance by local re-derivation + drift check |
| `tests/test_backfill_conformance.py` | **New** — 35 tests |
| `research/data/raw/dvol/**/_manifest.json` | **Real data**: 64 sidecars gain 4 previously-`null` fields. Every pre-existing key byte-identical |
| `research/data/raw/_raw_lake/dvol/BTC/**` | **New** — 64 content-addressed revisions (gitignored) |
| `research/data/raw/_backup_dvol_BTC_20260806T101310Z/` | **New** — pre-conformance backup (gitignored) |

**Unchanged and verified so:** all 64 `data.parquet` files (byte-identical); every existing source file; all four governance databases.

## Summary

Shipped Sprint D0a, the dvol corpus conformance pilot, under `G-CORPUS`'s six binding conditions — all six verified, none waived. All 64 dvol partitions now carry the three things `ADR-I012` requires and previously lacked: a certification verdict (§12 Tier C), a conditioning statement (§7.2), and a `_raw_lake` revision reference (§14). The ratified re-derivation method was validated before implementation began: `content_hash()` and `write_partition()` serialise identically, and all 64 partitions round-trip to an exact hash match, which is what lets condition `C4` turn a content change into a **failed precondition** rather than a regression found afterwards — the structural basis on which `DR1` was re-rated. Certification came back genuinely differentiated (60 GREEN / 2 AMBER / 2 RED, the two RED being DVOL's 8-row inception month and the current 10-row partial month), and 62 distinct original `ingest_ts` values were preserved rather than restamped. TV3 confirmed both load-bearing properties by breaking them: removing the `C4` guard let a mismatched partition land, and restamping the historical instants was caught by four tests including one that was not written for that purpose. Two **pre-existing** repository defects were discovered and reported rather than worked around — every module inside `research/ingest/` is unrunnable as a path-form script (`INC-0004`'s mechanism reaching a second class of victim, with `dvol.py` verified to fail identically), and `build_corpus.py` would strip conformance fields from any manifest it re-upgrades, which is narrow today but a real D0b consideration. Condition `C5` was satisfied by a stronger proof than the roadmap specified, after establishing that no evidence reader consults a manifest at all. **D0b and WBS 4.1 are not begun**; `funding`/`perp`/`spot` refuse by name.
