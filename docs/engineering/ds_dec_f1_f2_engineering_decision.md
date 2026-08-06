# Engineering Decision Document — F1 (Conformance Stripping) and F2 (Missing `parquet_sha256`)

| Field | Value |
|---|---|
| **Findings** | `F1` — corpus builders strip conformance fields · `F2` — three partitions lack `parquet_sha256` |
| **Status** | **Investigation complete — awaiting rulings. Nothing implemented** |
| **Owner** | Rohith Suresh |
| **Blocks** | Sprint D0b |
| **Document Type** | Engineering decision document (not an ADR, not a roadmap) |
| **Author** | Claude Opus 5 |
| **Date** | 2026-08-06 |
| **Baseline** | `ea3a8c630300aa08b3bb7d5583c62bff650bb661` |
| **References** | [D0b Pre-Flight Review](verification/records/d0b-preflight-review.md) · [D0a Record](verification/records/wbs-4.0-d0a-dvol-conformance-pilot.md) · [Phase 4 Roadmap v1.0.2](phase_4_dataset_builder_roadmap.md) |
| **Code written** | **None.** Read-only investigation — no code, no roadmap edit, no ADR edit, no schema or database change |

> **Fact vs inference.** Everything under **VERIFIED** was executed or read against the repository at `ea3a8c6` in this session. Everything under **INFERRED** is reasoning from those facts and is labelled as such.

---

## 0. The Finding That Reframes Both

**VERIFIED — the corpus uses a two-stage manifest architecture that no prior analysis in this programme had identified.**

| Stage | Who writes | How | What it produces |
|---|---|---|---|
| **Stage 1 — ingest** | `binance_ohlcv.py` (spot, perp), `funding.py` | Their **own local** `_write_manifest(part_dir, meta)` — a raw `json.dumps`, *not* `storage.manifest` | A **provisional** sidecar: `interval`, `skipped`, `source`, `ingest_ts`, and (since Phase 1) the four conformance fields. **No `schema_version`. No `parquet_sha256`.** |
| **Stage 2 — corpus build** | `build_corpus.py` (spot, funding), `build_perp_corpus.py` (perp) | `storage.manifest.write_manifest()` | Adds `parquet_sha256`, `schema_version`, `min_ts`/`max_ts`/`ts_column`/`config_version` |

Stage 1's own docstring states the design explicitly:

> *"Write a JSON provenance sidecar alongside data.parquet. **WI-6 (storage/manifest.py) will add SHA-256 and formalise the schema.**"*

**`dvol.py` is the exception** — it calls `storage.manifest.write_manifest()` directly and is therefore single-stage. That is why D0a encountered none of this.

**INFERRED:** this two-stage split is why F1 and F2 both exist, and why both are invisible from the dvol pilot alone.

---

## 1. `F1` — Root Cause

### 1.1 Verified facts

| # | Fact |
|---|---|
| **V1** | `storage.manifest.SCHEMA_VERSION` is **`1`** at commit `c14ba38` (pre-Phase-1) and **`3`** at commit `771e1a5` (Phase 1 — ingest conformance, contract versioning, raw lake) |
| **V2** | Four sites compare `schema_version` against the **hardcoded literal `1`**: `build_corpus.py:217` (skip gate), `build_corpus.py:378` (reporting), `build_perp_corpus.py:163` (skip gate), `build_perp_corpus.py:445` (reporting) |
| **V3** | A conformed manifest carries `schema_version: 3`. Evaluated against the real gate: **`False` → falls through to re-upgrade** |
| **V4** | The re-upgrade calls `write_manifest(... ingest_ts, config_version=1)`. The four conformance parameters are omitted, default to `None`, and **overwrite** the sidecar |
| **V5** | Both builders already contain a *"Recover provenance from old manifest"* block that carries `source` and `source_url` forward from `old_m` — but not the conformance fields |
| **V6** | `validate_wi6.py:130` performs the same check **correctly**, importing and comparing against `SCHEMA_VERSION` rather than a literal |
| **V7** | `src/archive/` has its own, unrelated `MANIFEST_SCHEMA_VERSION = 1` and imports nothing from `research/storage` — a **separate system, out of scope** |

### 1.2 Classification

**This is not a simple implementation bug, and it is not an ADR or roadmap gap. It is an intentional design that became invalid at the schema bump — compounded by a second, independent defect in the same code path.**

| Candidate | Verdict |
|---|---|
| Implementation bug | ⚠️ **Partly.** The gate was *correct when written*: with `SCHEMA_VERSION = 1`, `== 1` meant "already at current schema" |
| Roadmap gap | ❌ **No.** No roadmap assigns ownership of the builders' schema gate |
| ADR gap | ❌ **No.** `ADR-I009` §13.3 owns the normalization contract; nothing specifies manifest *schema* versioning semantics. No clause is contradicted |
| **Intentional design invalidated by `schema_version 3`** | ✅ **Yes — this is the root cause.** Phase 1 bumped the constant `1 → 3` to accommodate four new fields and did not update the two consumers comparing against the literal |

**The generalisable root cause:** *a version check that hardcodes the version literal it is checking for is stale by construction the moment that version moves.* `validate_wi6.py` avoided this by importing the constant (`V6`); the two builders did not.

### 1.3 The second defect, which the first concealed

Even with the gate fixed, **stage 2 still drops the four conformance fields on any legitimate upgrade** (`V4`). This matters beyond Phase 4:

**VERIFIED:** `binance_ohlcv.py` and `funding.py` *currently* write `normalization_contract_version`, `conditioning`, `raw_content_hash` and `admissibility` at stage 1 (added by Phase 1 WBS 1.6a/1.6b).

**INFERRED, and important:** this means the **existing pipeline already loses conformance metadata today**, independently of Phase 4. Any freshly-ingested spot/perp/funding partition would carry conformance from stage 1 and lose it at the next corpus rebuild. Phase 4 did not create this defect; it discovered it.

*Not verified by execution* — no fresh ingest was run, since that would require network and would land data. The conclusion follows from `V4` plus the verified stage-1 meta contents.

### 1.4 Blast radius — every corpus builder

| Module | Writes manifests? | Gates on `schema_version`? | Affected |
|---|---|---|---|
| `research/build_corpus.py` | ✅ stage 2 — spot, funding | ✅ **2 stale literals** (217, 378) | 🔴 **Yes** |
| `research/build_perp_corpus.py` | ✅ stage 2 — perp | ✅ **2 stale literals** (163, 445) | 🔴 **Yes** |
| `research/ingest/binance_ohlcv.py` | ✅ stage 1 (own writer) | ❌ none | 🟡 Upstream of the loss |
| `research/ingest/funding.py` | ✅ stage 1 (own writer) | ❌ none | 🟡 Upstream of the loss |
| `research/ingest/dvol.py` | ✅ single-stage | ❌ none | ✅ Unaffected |
| `research/ingest/backfill_conformance.py` | ✅ (D0a) | ❌ none | ✅ Unaffected |
| `research/validate_wi6.py` | ✅ (self-test only) | ✅ **correct** — uses the constant | ✅ Unaffected |
| `src/archive/*` | Separate archive manifest system | n/a | ✅ Out of scope (`V7`) |

⚠️ **CORRECTED at WBS 4.0.1 implementation — this originally read "Exactly two files require change. Four call sites."** It is **three files, seven call sites**. `research/smoke_test_phaseA1.py` carries three more stale literals (lines 178, 485, 517) and was **missed by this document's own blast-radius grep**, which filtered with `grep -v test_` — a pattern that matches the *path* `research/smoke_test_phaseA1.py`, not merely test content. It is read-only (it never writes a manifest), so it could not have corrupted data; but its `schema_version == 1` assertions would have **falsely failed** the moment D0b conformed spot/funding. Fixed under WBS 4.0.1.

### 1.5 Minimal architecturally correct fix

Two parts. Both are minimal, additive, and follow patterns already present in the same functions.

**Part A — remove the stale literal (4 sites).** Import the constant both files already import siblings of:

```python
from storage.manifest import SIDECAR_NAME, SCHEMA_VERSION, read_manifest, verify_hash, write_manifest
...
if old_m.get("schema_version") == SCHEMA_VERSION and "parquet_sha256" in old_m:
```

This is exactly `validate_wi6.py`'s already-correct pattern (`V6`), and it is **self-maintaining**: a future `SCHEMA_VERSION` bump cannot re-introduce the defect.

**Part B — carry conformance forward on upgrade.** Extend the existing *"Recover provenance from old manifest"* block (`V5`) to the four fields, and pass them through:

```python
write_manifest(
    ...,
    normalization_contract_version=old_m.get("normalization_contract_version"),
    conditioning=old_m.get("conditioning"),
    admissibility=old_m.get("admissibility"),
    raw_content_hash=old_m.get("raw_content_hash"),
)
```

**Part B is the load-bearing half.** Part A alone makes conformed manifests *skipped*; Part B makes an upgrade *non-destructive* — which is what protects the pre-existing pipeline (§1.3), not just Phase 4's output.

**INFERRED:** no other fix is smaller while remaining correct. Making `write_manifest()` itself merge-rather-than-overwrite was considered and rejected — it would change a shared, well-specified function's contract for every caller to compensate for two callers' omission.

---

## 2. `F2` — Root Cause

### 2.1 The exact three partitions — VERIFIED

| Path | Rows | `skipped` | `schema_version` | `parquet_sha256` |
|---|---|---|---|---|
| `research/data/raw/funding/BTCUSDT/year=2026/month=06` | 30 | `False` | **absent** | **absent** |
| `research/data/raw/perp/BTCUSDT/year=2026/month=06` | 14,009 | `False` | **absent** | **absent** |
| `research/data/raw/spot/BTCUSDT/year=2026/month=06` | 14,009 | `False` | **absent** | **absent** |

All three hold **real data** and a real `data.parquet`. All are the most recent month. `ingest_ts` values are 2026-06-10T17:28:5x.

Population context (all 274 verified): dvol `(3, True): 64`; funding `(1, True): 77` + 1 legacy; perp `(1, True): 65` + 1 legacy; spot `(1, True): 65` + 1 legacy.

### 2.2 Why `parquet_sha256` is absent

**VERIFIED, and it is not a defect.** Their key shape — `interval`, `skipped`, `ccxt_exchange`, `cadence_ms`, `source_url` — matches **stage-1 meta exactly** (§0). `parquet_sha256` is absent because **stage 1 deliberately does not write it**; stage 2 adds it. These three partitions were ingested and **never stage-2 upgraded**.

### 2.3 Classification

| Candidate | Verdict |
|---|---|
| Historical data | ❌ They are the *newest* partitions, not the oldest |
| **Migration debt / incomplete pipeline execution** | ✅ **Yes.** Stage 1 ran; stage 2 never did for these three |
| Architecture omission | ❌ **No.** The architecture defines the upgrade; it simply was not run |
| Corruption | ❌ **No.** Real data, real parquet, coherent stage-1 manifest |

**INFERRED:** the most likely cause is that the last corpus build predates these three partitions' ingest — they are the trailing edge of the corpus. Not verified; no build log was located, and I did not go looking beyond the repository.

### 2.4 Options evaluated

| # | Option | Evidence integrity | Verdict |
|---|---|---|---|
| **a** | **Skip by explicit exception** — refuse by name in `backfill_conformance.py`, record the reason | Preserves `C4`'s meaning perfectly. But leaves `ADR-I012` §12 Tier C unsatisfied for 3/274 **permanently**, for a reason that is fixable | Acceptable fallback, not best |
| **b** | **Reconstruct** the original hash | **Impossible.** No independent record of these partitions' content hash exists anywhere | ❌ Rejected |
| **c** | **Recompute** — have `backfill_conformance.py` adopt the on-disk hash when `parquet_sha256` is absent | **Defeats `C4`.** `C4`'s value is comparison against an *independently recorded* hash; self-comparison is tautological, and worse, it would make the conformance script silently accept a class of partition it cannot actually verify | ❌ **Rejected — do not do this** |
| **d** | **Regenerate — run the designed stage-2 upgrade**, then conform | The hash is written by the **designed writer at the designed pipeline stage**, from the actual file. Nothing invented, nothing skipped, no special case in the conformance script. The three then satisfy `C4` on identical terms to the other 271 | ✅ **Recommended** |
| **e** | **Re-ingest from source** | Reintroduces `DR1` — an upstream restatement would silently change content. This is precisely the method `DS-DEC-002` rejected | ❌ Rejected |

### 2.5 Recommendation — option (d), with an honest caveat

**Complete the pipeline stage that was never run.** It is the only option that neither invents evidence nor abandons a partition, and it requires **no special case** in `backfill_conformance.py` — the three simply become ordinary `schema_version 3` partitions.

**The caveat, stated rather than buried:** a hash recorded *now* gives **forward** protection (against future corruption or serialisation drift) but **no retroactive assurance** that the bytes are what was originally fetched. That is unavoidable for these three under *any* option — (a) and (c) provide neither forward nor retroactive assurance — but it must be recorded, and the three should be **named** in the D0b verification record as carrying weaker provenance, exactly as WBS 3.11 named its four deferred Specifications.

**Hard sequencing constraint:** option (d) **requires `F1` Part A + Part B first**. Running stage 2 today against spot/funding/perp is safe *only* because none of them currently carries conformance fields to lose. Once D0b conforms them, an unfixed stage 2 becomes destructive. Doing (d) before the F1 fix would work but would leave a live foot-gun.

---

## 3. Governance, Roadmap, ADR, Incident and Debt Review

| Question | Answer |
|---|---|
| **ADR revision required?** | ❌ **No.** `ADR-I012` is untouched by both findings. `ADR-I009` §13.3 owns the normalization contract but specifies nothing about manifest schema-version gating. No clause is contradicted or invalidated |
| **Roadmap revision required?** | ⚠️ **Yes, sequencing only.** A prerequisite must precede D0b. No WBS item changes scope; D0b's population stays 210 (207 + the 3 via option d). Recommend a `v1.0.3` sequencing note, **not applied here** |
| **New WBS item required?** | ✅ **Yes — recommend `WBS 4.0.1`, "Corpus builder manifest-upgrade repair"**, sized **S**, ordered **before D0b**. It is genuinely separate work: it repairs shipped Phase-1-era code, its beneficiary is the pre-existing pipeline as much as Phase 4, and it is independently reviewable |
| **Engineering incident required?** | ✅ **Yes — recommend `INC-0005`.** The mechanism is novel in this repository's incident history: a **hardcoded version literal surviving a constant bump**, silently inverting a safety gate's polarity. Distinct from `INC-0003` (`sys.modules` collision) and `INC-0004` (`sys.path` stdlib shadow). It also has a reusable lesson — `validate_wi6.py` got it right by importing the constant |
| **Technical debt item required?** | ✅ **Yes — two.** (1) **Two competing manifest writers**: `binance_ohlcv.py`/`funding.py` maintain a local `_write_manifest` that duplicates `storage.manifest`'s role and cannot stay in sync by construction. (2) The **three stage-1-only partitions** remain weaker-provenance even after option (d) |
| **Governance decision required?** | ✅ **Yes.** `WBS 4.0.1` modifies two shipped scripts outside WBS 4.0's sole deliverable — the same class of change `T-3`/`DS-DEC-004` gates for RF2's modification of the analysis scripts. It must be authorised, not absorbed |

---

## 4. Impact Assessment

### 4.1 Implementation impact

| Item | Impact |
|---|---|
| Files changed | ⚠️ **CORRECTED: 3, not 2** — `build_corpus.py`, `build_perp_corpus.py`, `smoke_test_phaseA1.py`. **Seven** literal sites + two `write_manifest` call sites (§1.4 correction) |
| New files | None for the fix; a verification record and tests for `WBS 4.0.1` |
| `backfill_conformance.py` | **Unchanged.** Option (d) needs no special case |
| `storage/manifest.py` | **Unchanged.** Its contract is correct; the callers were wrong |
| Test surface | Both builders currently have no dedicated test file (**VERIFIED** — no `tests/test_build_corpus.py` exists). `WBS 4.0.1` should add focused tests for the gate and the carry-forward, not a full builder suite |

### 4.2 Rollback impact

| Item | Impact |
|---|---|
| `F1` fix | **Trivially revertible** — pure code change, `git revert`. No data implication |
| `F2` option (d) | Stage-2 upgrade **rewrites 3 sidecars**; `data.parquet` untouched. Rollback = restore 3 JSON files from a backup taken first. **Additive and reversible** |
| Interaction with D0a | **None.** dvol is untouched by both builders |
| Worst case if the fix is wrong | A conformed manifest is skipped when it should be upgraded, or vice versa — detectable by re-running conformance, which is idempotent and refuses on `C4` violation |

### 4.3 Roadmap impact

| Item | Impact |
|---|---|
| D0b scope | **Unchanged** — 210 partitions |
| D0b sequencing | **Changed** — `WBS 4.0.1` inserted before it |
| `G-CORPUS` conditions `C1`–`C6` | **Unchanged and still adequate for the conformance act.** But `F1` exposed that they govern *performing* conformance, not the *durability* of its output. Worth an explicit `C7` (*"conformance survives a corpus rebuild"*) at the roadmap owner's discretion — **recommended, not applied** |
| Phase 4 exit criterion `P4-1` | Unaffected; still reachable |

### 4.4 Risk assessment

| Risk | Likelihood / Impact | Assessment |
|---|---|---|
| Fixing the gate breaks an existing corpus build | **Low / Medium** | The change makes the gate *stricter* only where it was wrongly permissive. Unconformed `schema_version 1` manifests would now **fail** the gate and be upgraded to 3 — a behaviour change worth an explicit test |
| Carry-forward propagates a **stale** conformance verdict | **Low / Medium** | If a partition's parquet changed but its manifest's `admissibility` did not, carry-forward preserves a stale verdict. Mitigated: `verify_hash` runs in the same path, and conformance is idempotent and re-derivable |
| Option (d) masks a genuine content problem in the 3 | **Low / Medium** | Real but unavoidable (§2.5). Mitigated by naming the three explicitly in the D0b record |
| Doing nothing | **Certain / High** | D0b's output would be silently destroyed by the next corpus rebuild, and the pre-existing pipeline continues losing conformance on every rebuild |

---

## 5. Recommendation Summary

| # | Recommendation | Requires |
|---|---|---|
| **R1** | Add **`WBS 4.0.1`** — repair both builders: Part A (constant, not literal) + Part B (carry conformance forward). Size **S**, ordered before D0b | 🔴 Governance decision — authorises touching two shipped scripts |
| **R2** | Resolve `F2` by **option (d)** — run the designed stage-2 upgrade on the three, **after** `WBS 4.0.1`, then conform them normally in D0b | 🔴 Ruling |
| **R3** | File **`INC-0005`** for the stale-version-literal mechanism | Rohith's call |
| **R4** | Open two technical-debt items: two competing manifest writers; three weaker-provenance partitions | Rohith's call |
| **R5** | Consider a roadmap `C7` condition — *conformance must survive a corpus rebuild* | Roadmap owner |
| **R6** | **Do not** adopt `F2` option (c) — recomputing the hash in `backfill_conformance.py` would defeat `C4` | Standing |

**Nothing above is implemented. D0b remains stopped.**

---

## 6. Investigation Method

Read-only throughout. Every **VERIFIED** claim rests on: enumerating all 15 `write_manifest` call sites and all `schema_version` readers repo-wide; reading both builders' gate and upgrade blocks; reading both stage-1 local `_write_manifest` implementations and their meta dicts; `git show c14ba38:` and `git show 771e1a5:` to establish the `1 → 3` bump; evaluating the real gate predicate against a real conformed manifest; and parsing all 274 manifests to characterise the population.

**No file was modified. No code was written. No database, schema, roadmap, or ADR was touched.** The only artifact produced is this document.

---

| Role | Name | Date |
|---|---|---|
| Investigation | Claude Opus 5 | 2026-08-06 |
| **`R1` — WBS 4.0.1 authorisation** | **Rohith Suresh** | **_______________** |
| **`R2` — F2 resolution** | **Rohith Suresh** | **_______________** |
