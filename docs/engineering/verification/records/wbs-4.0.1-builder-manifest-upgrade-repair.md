# Milestone Verification Record

| Field | Value |
|---|---|
| **Milestone** | Corpus Builder Manifest-Upgrade Repair — `build_corpus.py`, `build_perp_corpus.py`, `smoke_test_phaseA1.py` |
| **WBS item(s)** | **4.0.1** (new; authorised by ruling `R1`, 2026-08-06) |
| **Roadmap phase** | Phase 4 — Dataset Builder (`phase_4_dataset_builder_roadmap.md` v1.0.2) |
| **Authorisation** | `R1` APPROVED (WBS 4.0.1) · `R2` APPROVED (F2 via option **d**) |
| **Standard version referenced** | 1.0.0 |
| **Date** | 2026-08-06 |
| **Implementer** | Claude Opus 5 |
| **Reviewer** | *(pending — Rohith)* |
| **Baseline** | `ea3a8c630300aa08b3bb7d5583c62bff650bb661` |
| **Resolves** | `F1` (conformance stripping) · `F2` (three partitions lacking `parquet_sha256`) |

**D0b and WBS 4.1 are not begun.** `backfill_conformance._SOURCES` still registers dvol alone; no `research/dataset_builder/` exists.

---

## 0. Design Summary

### 0.1 `F1` — two parts, both minimal

**Part A — the constant, never a literal.** Seven sites compared `schema_version` against the hardcoded `1`. `storage.manifest.SCHEMA_VERSION` is **3** (bumped `1 → 3` by Phase 1, commit `771e1a5`), so the gate's polarity had silently inverted: current-schema manifests stopped being skipped and were re-upgraded instead. All seven now read the imported constant — `validate_wi6.py`'s already-correct pattern — making the check **self-maintaining across any future bump**.

**Part B — carry conformance forward.** `write_manifest()` overwrites the sidecar and defaults the four conformance parameters to `None`, so an upgrade that omitted them destroyed whatever stage 1 or `backfill_conformance.py` had recorded. Both builders now recover those four from `old_m`, extending the *"Recover provenance from old manifest"* pattern that already existed three lines above for `source`/`source_url`.

**Part B is the load-bearing half.** Part A alone makes conformed manifests *skipped*; Part B makes an upgrade *non-destructive* — which protects the **pre-existing pipeline**, not only Phase 4's output. `binance_ohlcv.py` and `funding.py` have written conformance at stage 1 since Phase 1, so this defect was already live before Phase 4 existed.

### 0.2 `F2` — resolved through the designed path, filtered

Option (d) as ruled: the three stage-1-only partitions were upgraded by **running the builders' own `stage_manifest_upgrade()`**, unmodified. No hash was invented, no bypass was added, and **no special case was introduced into `backfill_conformance.py`** — as instructed.

**One scope decision, made explicitly rather than silently.** With Part A applied, a *full* `stage_manifest_upgrade()` would now re-upgrade **all 210** spot/funding/perp partitions (they sit at `schema_version 1`, which is no longer current), not just the three. The ruling approved **the three**. So the run was narrowed by filtering the partition list the builders iterate — their upgrade logic ran untouched — and the 210-partition consequence is reported below (§8) rather than absorbed.

### 0.3 Provenance of the three — recorded as weaker, per instruction

Their `parquet_sha256` was computed **now**, from bytes already on disk, by the designed writer at the designed stage. That gives **forward** protection (future corruption, serialisation drift) but **no retroactive assurance** that those bytes are what was originally fetched. Unavoidable under any option — (a) and (c) would have given neither — and named here, as `WBS 3.11` named its four deferred Specifications.

## 1. Build Verification (BV)

- [x] **BV1** — `py_compile` clean on all three modified modules.
- [x] **BV2** — No new dependency; `SCHEMA_VERSION` added to imports both builders already had.
- [x] **BV3** — Verified in `btc_bot`; test module correctly skipped in base env (no pandas).
- [x] **BV4** — Zero stale literals repo-wide, verified by grep across `research/`, `scripts/`, `src/`.

## 2. Test Verification (TV)

- [x] **TV1** — `tests/test_corpus_builder_manifest_upgrade.py` (new, **18 tests**): StaleVersionRegression (4), ConformancePreservation (4), StageUpgrade (3), Stage2Idempotence (3), RealLegacyPartitions (4).
- [x] **TV2** — House style confirmed; module-level `importorskip("pandas")` for the same reason as D0a (the builders import pandas/pyarrow at module level). Builders are driven **as real code** via monkeypatched `BASE_DIR`/`SYMBOL`, not reimplemented.
- [x] **TV3** — **Two sabotages, both confirmed, both reverted.**
  1. **Gate reverted to the stale literal** (`== SCHEMA_VERSION` → `== 1`): `test_current_schema_partition_is_skipped_not_reupgraded` and the source guard `test_gate_is_bound_to_the_constant_not_a_literal` both failed.
  2. **Carry-forward removed** (four kwargs deleted): `test_upgrade_carries_conformance_forward` and `test_forced_reupgrade_of_a_conformed_partition_loses_nothing` both failed — the builder's own output confirmed it (`upgraded=1`, fields gone).
  Both reverted; grep confirms no marker remains; 18/18 re-confirmed.
- [x] **TV4** — Refusal/edge paths: absent conformance stays absent and is **not fabricated**; a forced re-upgrade (hash deliberately broken) still preserves all four fields; a partition at a *future* schema is upgraded rather than skipped.
- [x] **TV5** — Property/invariant tests, covering every area required by the ruling:

| Required coverage | Test |
|---|---|
| Stale-version regression | `test_current_schema_partition_is_skipped_not_reupgraded`, `test_perp_current_schema_partition_is_skipped`, `test_gate_is_bound_to_the_constant_not_a_literal` |
| Conformance-field preservation | `test_upgrade_carries_conformance_forward`, `test_perp_upgrade_carries_conformance_forward` |
| Stage-1 → Stage-2 upgrade | `test_stage1_partition_gains_schema_and_hash`, `test_upgrade_preserves_the_original_ingest_ts`, `test_upgrade_never_touches_the_parquet` |
| Stage-2 idempotence | `test_second_run_upgrades_nothing`, `test_second_run_leaves_the_manifest_byte_identical`, `test_perp_second_run_upgrades_nothing` |
| The three legacy partitions | `test_all_three_are_now_at_current_schema`, `test_their_original_ingest_ts_survived_the_upgrade`, `test_no_partition_in_the_corpus_lacks_a_hash_any_more` |
| Carry-forward behaviour | `test_forced_reupgrade_of_a_conformed_partition_loses_nothing`, `test_absent_conformance_stays_absent_not_fabricated`, `test_dvol_conformance_from_d0a_is_untouched` |

**`test_a_bumped_schema_version_would_not_silently_invert_the_gate`** deserves separate mention: it simulates the *next* bump by writing `SCHEMA_VERSION + 1` into a manifest and asserting the partition is upgraded rather than skipped. It reads the constant rather than restating it, so a future bump cannot make it pass vacuously — this is the test that makes the fix self-maintaining rather than merely currently-correct.

## 3. Regression Verification (REG)

- [x] **REG1** — `btc_bot`: before **2324 / 19 / 0**; after **2342 / 19 / 0** — delta **+18**, exactly this file's count.
- [x] **REG2** — base env: **1910 / 62 / 0** (before 1910/61). Passes unchanged, **+1 skip** — correctly gated.
- [x] **REG3** — No pre-existing flake recurred.
- [x] **REG4** — CI workflow unchanged.
- [x] **REG5** — **Zero pre-existing tests modified.** The builders had no test file before this milestone (verified).

## 4. Performance Verification (PERF)

- [x] **PERF1** — Suite time 104 s (from 125 s at D0a — the reduction is run-to-run variance, not attributable). The new tests operate on 5-row synthetic partitions; the real-corpus tier reads manifests only, never parquet, so it adds negligible cost. **Deliberate contrast with D0a**, whose exhaustive real-corpus `C4` test was the D0b scaling concern (`F4`).

## 5. Data Verification (DBV)

- [x] **DBV1** — **No database touched.** Manifest sidecars only; all four governance DBs re-verified `integrity_check=ok`, chains valid (8/17/26), `validate_label_governance` CLEAN, `report.py --check` OK.
- [x] **DBV2** — **C1 backup taken** before the F2 write: `research/data/raw/_backup_wbs401_stage2_upgrade/` holding all three original sidecars.
- [x] **DBV3** — **F2 execution, verified:**

| Check | Result |
|---|---|
| Partitions upgraded | **3** — spot, funding (via `build_corpus`), perp (via `build_perp_corpus`) |
| `schema_version` | all three now **3** |
| `parquet_sha256` | all three present, 64 hex chars |
| Original `ingest_ts` | **preserved** — `2026-06-10T17:28:5x` on all three |
| `data.parquet` | **byte-identical** on all three |

- [x] **DBV4** — **Builders are idempotent after upgrade.** Re-running the same filtered upgrade: `build_corpus` `{upgraded: 0, already_wi6: 2}`, `build_perp_corpus` `{upgraded: 0, already_wi6: 1}`.
- [x] **DBV5** — **No conformance metadata lost.** dvol's D0a output intact: 64/64 still carry `admissibility`, and 64/64 still satisfy `raw_content_hash == parquet_sha256`.
- [x] **DBV6** — **Landed hashes remain valid.** Across all 274 partitions, **0** have a `parquet_sha256` disagreeing with the file on disk, and **0** now lack a hash (was 3).
- [x] **DBV7** — **Result invariance holds.** `data.parquet` untouched everywhere; the evidence-facing read of the three upgraded partitions was hashed post-upgrade (`35b7b607…`) and no reader consults manifests (established at D0a §5.1).

## 6. Backward Compatibility (BC)

- [x] **BC1** — No signature, table, or column changed. `storage/manifest.py` **unmodified** — its contract was correct; the callers were wrong.
- [x] **BC2** — `backfill_conformance.py` **unmodified**, as the ruling required.
- [x] **BC3** — Every previously-present manifest key retains its value on the three upgraded partitions; four fields were added by the upgrade.
- [x] **BC4** — ⚠️ **One deliberate behaviour change, named:** with Part A, the 207 remaining spot/funding/perp partitions (at `schema_version 1`) will be re-upgraded to 3 on the next full corpus build. This is **correct** — they were never at the current schema — and **non-destructive**, proven by `test_absent_conformance_stays_absent_not_fabricated` and `test_upgrade_never_touches_the_parquet`. Named in §8, not absorbed.
- [x] **BC5** — Repo-wide grep: no module references the new test file; the three modified modules gained only an import and literal replacements.

## 7. Rollback Verification (RBV)

- [x] **RBV1** — **Code lever:** `git revert`. Pure code change across three files; no data implication.
- [x] **RBV2** — **Data lever:** `research/data/raw/_backup_wbs401_stage2_upgrade/` restores all three original sidecars.
- [x] **RBV3** — **Reverting is safe but re-exposes F1.** The gate would return to `== 1` and conformance stripping would resume. Rollback should therefore be paired with re-freezing D0b, not performed alone.
- [x] **RBV4** — No configuration lever, deliberately: a switch disabling conformance preservation is a switch that will eventually be left off.

## 8. Technical Debt Assessment (TD)

| What | Assessment | Owner |
|---|---|---|
| **207 partitions will be re-upgraded (schema 1 → 3) on the next full corpus build** | Correct and non-destructive (§6 BC4), but a real, observable behaviour change: a future `build_corpus.py` run will report ~144 upgrades and `build_perp_corpus.py` ~65, where both previously reported "already WI-6". **Not a defect** — they were genuinely stale — but an operator seeing it should not be surprised | Reported; no action proposed |
| **Two competing manifest writers** — `binance_ohlcv.py`/`funding.py` maintain a local `_write_manifest` duplicating `storage.manifest`'s role | The structural root cause behind both F1 and F2. They cannot stay in sync by construction. Consolidating them is a real refactor, well outside WBS 4.0.1 | **Recommended TD item** (was `R4`, not yet ruled) |
| **The three upgraded partitions carry weaker provenance** | §0.3 — forward protection only, no retroactive assurance | **Recommended TD item** (was `R4`, not yet ruled) |
| **`INC-0005` not filed** | `R3` was recommended but **not ruled on**. The stale-version-literal mechanism is documented in the decision document and in the code comments, but no incident record exists | Awaiting `R3` |
| **`C7` roadmap condition not added** | `R5` recommended a *"conformance must survive a corpus rebuild"* condition. Not ruled on; roadmap unmodified | Awaiting `R5` |

- [x] **TD3** — No placeholder work. Both fixes were exercised against real builder code and the F2 upgrade ran for real.
- [x] **TD4** — Nothing above is unscoped debt of this milestone's making; three items await rulings already tabled.

## 9. Definition of Done (DOD)

- [x] **D1** compiles · **D2** both suites green, zero new failures · **D3** 18 new tests · **D4** N/A stated · **D5** no cross-plane import · **D6** rollback levers named · **D7** no signature/table/column changed · **D8** no secret touched · **D9** independently revertible · **D10** no new mandatory field · **D11** all sections complete.
- [x] **P4-D2** — Every changed behaviour has a negative test.
- [x] **P4-D4** — Real-data transcript captured in-session (§5).

## 10. Reviewer Checklist (RC)

- [ ] **RC1** — Milestone corresponds to WBS 4.0.1 exactly; no D0b or 4.1 work
- [ ] **RC2** — No ADR or roadmap file modified
- [ ] **RC3** — The §0.2 scope decision (three partitions, not 210) is accepted
- [ ] **RC4** — The §6 BC4 behaviour change (207 future re-upgrades) is accepted
- [ ] **RC5** — The §0.3 weaker-provenance record for the three is accepted
- [ ] **RC6** — The third affected file (`smoke_test_phaseA1.py`), missed by the original blast-radius analysis, is accepted as in-scope
- [ ] **RC7** — Backup confirmed present and readable
- [ ] **RC8** — Sign-off

## Files Changed

| File | Change |
|---|---|
| `research/build_corpus.py` | Import `SCHEMA_VERSION`; 2 literals → constant; 4 carry-forward kwargs |
| `research/build_perp_corpus.py` | Same four changes |
| `research/smoke_test_phaseA1.py` | Import `SCHEMA_VERSION`; 3 literals → constant |
| `tests/test_corpus_builder_manifest_upgrade.py` | **New** — 18 tests |
| `docs/.../wbs-4.0.1-builder-manifest-upgrade-repair.md` | **New** — this record |
| `docs/engineering/ds_dec_f1_f2_engineering_decision.md` | **2 corrections** — blast radius was 2 files/4 sites; it is **3 files/7 sites** |
| `research/data/raw/{spot,funding,perp}/BTCUSDT/year=2026/month=06/_manifest.json` | **Real data** — 3 sidecars upgraded to schema 3 (gitignored) |
| `research/data/raw/_backup_wbs401_stage2_upgrade/` | **New** — pre-write backup (gitignored) |

**Unchanged and verified so:** `storage/manifest.py`, `backfill_conformance.py`, all ingest writers, every `data.parquet`, all four governance databases.

## Summary

Shipped WBS 4.0.1 under rulings `R1` and `R2`. **F1** is fixed in two parts: seven stale `schema_version == 1` literals across three files now read the authoritative `SCHEMA_VERSION` constant, and both builders carry the four conformance fields forward on upgrade — the half that protects the pre-existing pipeline, which had been losing conformance on every rebuild since Phase 1, independently of Phase 4. The fix is self-maintaining, and a dedicated test simulates the *next* schema bump to prove it. **F2** is resolved exactly as ruled — the three stage-1-only partitions were upgraded by running the builders' own `stage_manifest_upgrade()` unmodified, with no invented hash, no bypass, and no special case in `backfill_conformance.py`; their original `ingest_ts` survived and their parquet is byte-identical. Every partition in the corpus now carries a `parquet_sha256`, and all 274 recorded hashes match the files on disk. One scope decision was made explicitly rather than silently: Part A means a *full* upgrade would now touch all 210 partitions, so the F2 run was narrowed to the three that were approved, and the 210-partition consequence is reported rather than absorbed. One honest correction: the original blast-radius analysis said two files and four call sites; it was **three files and seven**, because that analysis's own grep filtered `research/smoke_test_phaseA1.py` out by path — the decision document is corrected in place. TV3 confirmed both fixes by breaking them. **D0b and WBS 4.1 remain not begun.**
