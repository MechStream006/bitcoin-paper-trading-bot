# Decision Brief — Phase 4 Entry Blockers (`DS-DEC-001`, `DS-DEC-002`, `G-CORPUS`, `DS-DEC-005`)

| Field | Value |
|---|---|
| **Decision IDs** | `DS-DEC-001` · `DS-DEC-002` · `G-CORPUS` · `DS-DEC-005` |
| **Status** | ✅ **ALL FOUR RATIFIED 2026-08-06 — every recommendation adopted as written** |
| **Owner** | Rohith Suresh |
| **Due** | Before Phase 4 sprint D0 (`DS-DEC-001`, `DS-DEC-002`, `G-CORPUS`) and D1 (`DS-DEC-005`) |
| **Document Type** | Engineering decision brief (not an ADR, not a roadmap) |
| **Author** | Claude Opus 5 |
| **Date** | 2026-08-06 |
| **References** | [Phase 4 Roadmap](phase_4_dataset_builder_roadmap.md) §4.2 · [Phase 4 Gap Matrix](verification/records/phase-4-gap-matrix.md) · [`ADR-I012`](../architecture/adr/ADR-I012-research-dataset-builder.md) · [`ADR-I009`](../architecture/adr/ADR-I009-research-data-platform.md) §16 |
| **Code written** | **None.** No ADR, roadmap, or production file modified |

> **What this document is not.** It does not modify `ADR-I012`, the Phase 4 roadmap, or any code. It resolves four decisions the roadmap already named and deferred to Rohith, using evidence gathered from the repository as it exists at commit `ffaae2b` / tag `phase-3-baseline-v1.1.0`.

---

## 0. Shared Evidence Base

Every option below rests on these facts, each verified this session, not taken from a milestone report:

| # | Fact |
|---|---|
| **F1** | **274 real partition manifests** exist under `research/data/raw/` — dvol 64, funding 78, perp 66, spot 66. Total **183 MB** (spot 100 MB, perp 82 MB, dvol 788 KB, funding 656 KB) |
| **F2** | Every one is `schema_version: 1`, written 2026-06-09/10, and carries **no** `admissibility`, **no** `conditioning`, **no** revision ordinal, **no** normalization-contract version |
| **F3** | **No `_raw_lake` directory exists anywhere on disk.** Phase 1's revisioning has never landed a real partition |
| **F4** | All three ingest modules **are** wired to `quality.admissibility.partition_ceiling` and pass an `admissibility={...}` block to `write_manifest()`. The code is current; the data predates it |
| **F5** | **Re-ingest as currently written requires network.** `dvol.py` calls `requests.get()` against Deribit; `binance_ohlcv.py` downloads monthly zips from Binance Vision with a CCXT fallback. **No local source archive exists** (the only CSV on disk is `macro_events.csv`) |
| **F6** | **`land_raw()` accepts a `DataFrame`**, not a URL. It is idempotent by content hash: landing byte-identical content twice writes once and appends no second revision |
| **F7** | **`partition_ceiling()` accepts a gauntlet `report` dict** — certification is computed **from the data**, not from the act of fetching. Identical data yields an identical verdict |
| **F8** | `ADR-I009` §16.1 requires revisions to **supersede**, never replace. Prior revisions must stay resolvable |

**The consequence that shapes three of the four decisions:** because of `F6` and `F7`, conformance can be achieved **without re-downloading anything**. That option was not visible when the roadmap was drafted.

---

## 1. `DS-DEC-001` — Does the corpus conformance backfill enter Phase 4?

### 1.1 The decision

Roadmap §3 `T-1`: *"The corpus conformance backfill enters Phase 4. It is Phase 1 completion work, but Phase 4 is its only consumer and the only phase that can verify it end-to-end."*

Without it, `ADR-I012` §12 Tier C (*"Certification verdict present for every partition"*) is unsatisfiable against real data, and a conforming builder must refuse to seal anything.

### 1.2 Architectural options

| Option | Description |
|---|---|
| **A. Accept as WBS 4.0** | Phase 4 owns the backfill as its first sprint, flagged as inherited Phase 1 work |
| **B. Re-open Phase 1 as a separate phase (1b)** | The backfill becomes its own phase with its own roadmap, closure review, and gate |
| **C. Reject — Phase 4 proceeds without it** | Build the whole builder; seal only against synthetic or partial corpora |

### 1.3 Trade-offs

| | A — WBS 4.0 | B — Phase 1b | C — Reject |
|---|---|---|---|
| **Phase boundary honesty** | Blurred: Phase 4 carries Phase 1 work | **Clean**: work sits in the phase that owns the requirement | Clean |
| **Ceremony cost** | Low — one WBS item | **High** — full roadmap, readiness review, closure review, closeout | None |
| **Verification quality** | **Best** — Phase 4 is the only consumer that can prove conformance end-to-end (a sealed dataset is the acceptance test) | Weaker — Phase 1b would verify against no consumer | N/A |
| **Risk of drift** | Low | **Moderate** — a phase built against no consumer tends to satisfy the letter | High — the gap persists indefinitely |
| **Phase 4 exit criterion `P4-1`** | Achievable | Achievable, later | **Unachievable** — nothing seals against real data |

### 1.4 Recommendation — **Option A, accept as WBS 4.0**

Option C is not viable: it makes Phase 4's own exit criterion unreachable and would ship a builder that has never sealed a real dataset. Option B is architecturally cleaner but pays a full phase's governance overhead for a single, well-scoped item — and, more importantly, would verify the backfill against no consumer, which is exactly how Phase 2's `FS-GAP-002` and `FS-GAP-005` came to be satisfied "in form" but not in use.

Option A keeps the work where its acceptance test lives. **The honesty cost is paid by labelling, not by placement** — the roadmap already flags 4.0 as inherited Phase 1 work rather than absorbing it silently, which is the same treatment scope transfers `S-2` and `S-3` received in Phase 3.

### 1.5 Impacts

| Dimension | Impact |
|---|---|
| **Implementation** | One WBS item (4.0, size L), one new script `research/ingest/backfill_conformance.py`. No builder code depends on the *decision*, only on the *outcome* |
| **Rollback** | Independent of the builder: 4.0 is revertible by restoring `research/data/raw/` from backup, with new `_raw_lake` revisions additive |
| **Verification** | Adds `P4-D4` (host/data transcript capture) to sprint D0 and makes `DBV5` non-N/A from D0 onward. Its true acceptance test is `P4-1` — a real Research Dataset sealing |

---

## 2. `DS-DEC-002` — By what method is the corpus conformed?

### 2.1 The decision

This is the decision the roadmap did **not** anticipate in this form. It framed 4.0 as *"re-ingest"*; `F5`–`F7` show at least two materially different methods exist, with very different risk profiles.

### 2.2 Architectural options

| Option | Description | Network? | Content change? |
|---|---|---|---|
| **A. Full re-download** | Re-fetch all 274 partitions from Deribit / Binance Vision through the current conformed writers | **Yes** | **Possible** |
| **B. Local re-derivation** | Read each existing parquet, feed the `DataFrame` to `land_raw()` + `write_manifest()` with certification computed by `partition_ceiling()` over the same data | **No** | **None, by construction** |
| **C. Hybrid** | Option B for all 274, plus a sampled re-download (e.g. 10 partitions) compared against the landed content as an upstream-drift check | Partial | None in the corpus; drift *reported* |
| **D. Forward-only** | No backfill. Only newly-ingested partitions carry conformance | No | None |

### 2.3 Trade-offs

| | A — Re-download | B — Re-derivation | C — Hybrid | D — Forward-only |
|---|---|---|---|---|
| **Risk to published results (`DR1`)** | **High.** Binance Vision has restated data before — `CLAUDE.md` §11 documents a silent ms→µs precision change. A restatement would alter partition content and could change any ledger number derived from it | **None.** Content is byte-identical by construction; certification is computed from unchanged data (`F7`) | **None** to the corpus; drift surfaces as a *report*, not a mutation | None |
| **Network dependency** | Full — 183 MB, subject to Binance Vision availability and rate limits | **None** | Small | None |
| **Provenance honesty** | Strongest: `fetched_at` is genuinely fresh | Weaker: `fetched_at`/`source_url` are carried forward historically; the landing is "as previously stored", not "as received" | Strongest available without mutation risk | N/A |
| **Detects upstream drift** | Yes — but by *absorbing* it | No | **Yes — by reporting it** | No |
| **Satisfies Tier C** | Yes | **Yes** (`F7`) | Yes | **No** — only for the conformed window |
| **Effort** | Medium | **Low** | Medium | Trivial |

### 2.4 Recommendation — **Option C, hybrid; falling back to Option B if sampling is impractical**

**Option A should be rejected.** It converts a governance-completion task into a research-integrity risk. Re-downloading absorbs any upstream restatement silently into the corpus, and the platform's own documented experience (`CLAUDE.md` §11, the Binance Vision timestamp change) shows restatements are not hypothetical. `DR1` is rated Moderate/**Severe** precisely for this.

**Option D should be rejected.** It leaves 2021–2026 unconformed, so no dataset can span the history the research programme actually uses.

**Option B is the safe core**, and it is *sufficient*: `F7` establishes that certification is a function of the data, so a verdict derived from the existing parquet is exactly the verdict a fresh download of identical bytes would produce. Its only weakness is that it cannot tell you whether upstream still agrees.

**Option C closes that weakness without taking on `DR1`.** Re-derive all 274 partitions locally, then re-download a sample and *compare* — reporting any divergence as a finding rather than landing it. This is the same "flag, never silently reconcile" discipline `ADR-I012` §8.2 requires of the materialiser and `ADR-I011` §11.3 requires of superseded inputs. A confirmed upstream divergence then becomes its own scoped decision, with evidence, rather than an accident.

### 2.5 Impacts

| Dimension | Impact |
|---|---|
| **Implementation** | Option B is the bulk: read parquet → `land_raw()` → `write_manifest()` with `conditioning` and `admissibility`. Option C adds a `--verify-sample N` mode that fetches and diffs without landing. Idempotency comes free from `F6` |
| **Rollback** | **Strong.** Under B/C the original 274 partitions are untouched and `_raw_lake` is a new tree — rollback is deleting `_raw_lake` and restoring manifests from backup. Under A, rollback requires restoring content that may no longer be obtainable upstream |
| **Verification** | B/C admit a **structural** guarantee A cannot: assert that every landed partition's `parquet_sha256` equals the pre-existing manifest's. That converts `DR1`'s mitigation from a procedural re-run-and-diff into a per-partition invariant. The sampled re-download in C needs its own transcript (`P4-D4`) |

---

## 3. `G-CORPUS` — Should the gate be opened, and on what conditions?

### 3.1 The decision

Roadmap §0: *"WBS 4.0 re-ingests the real research corpus. 274 existing partitions would be superseded by conformed revisions. No prior phase has rewritten landed research data."*

`G-CORPUS` is the authorisation wrapper for §2's method. The two decisions are separable: the gate governs *whether and under what conditions*, `DS-DEC-002` governs *how*.

### 3.2 Architectural options

| Option | Description |
|---|---|
| **A. Open unconditionally** | Authorise 4.0 to proceed on the operator's judgement |
| **B. Open with binding conditions** | Authorise, subject to explicit, verifiable preconditions |
| **C. Keep closed pending a pilot** | Authorise conformance of one small source (e.g. dvol, 64 partitions / 788 KB) first; re-evaluate before the remaining 210 |
| **D. Keep closed** | Phase 4 proceeds without real-data sealing (equivalent to `DS-DEC-001` Option C) |

### 3.3 Trade-offs

| | A | B | C — Pilot | D |
|---|---|---|---|---|
| **Blast radius if wrong** | All 274 partitions, 183 MB | All 274, but bounded by stated conditions | **64 partitions, 788 KB** — recoverable in minutes | None |
| **Time to Phase 4 `P4-1`** | Fastest | Fast | +1 short sprint | Never |
| **Precedent quality** | Poor — first-ever research-data rewrite, ungated | **Good** — conditions become the reusable template | **Best** — proves the mechanism before scaling it | N/A |
| **Consistency with repo practice** | Inconsistent — every prior real-data migration was dry-run gated and rehearsed on a copy | Consistent | **Most consistent** — mirrors WBS 1.2–1.5's own dvol *pilot* precedent | N/A |

### 3.4 Recommendation — **Option C then B: pilot on dvol, then open with conditions**

This repository already has a precedent for exactly this shape: Phase 1's WBS 1.2–1.5 was a **dvol pilot** before the funding and binance rollouts (WBS 1.6a/1.6b). Repeating that sequence for conformance is consistent, cheap, and de-risking — dvol is 788 KB across 64 partitions, so a full conform-verify-rollback cycle costs minutes.

**Proposed binding conditions for the full opening:**

| # | Condition |
|---|---|
| **C1** | Full backup of `research/data/raw/` taken and `PRAGMA`-equivalent integrity verified before any write |
| **C2** | **Supersede, never replace** (`ADR-I009` §16.1) — original partitions remain byte-identical and resolvable; verified by SHA-256 before/after |
| **C3** | Dry-run default; `--commit` gated; idempotent across two consecutive runs |
| **C4** | Per-partition invariant: landed content hash **equals** the pre-existing manifest's `parquet_sha256` (available under `DS-DEC-002` B/C only) |
| **C5** | Result-invariance proof: the three lockbox-bearing evidence scripts re-run before and after, outputs **bit-identical** (`DR1`) |
| **C6** | Full transcript committed in the same session (`P4-D4`) |

### 3.5 Impacts

| Dimension | Impact |
|---|---|
| **Implementation** | Pilot adds no new code — it is `--source dvol` on the same script. Conditions C1–C4 are assertions inside it; C5 is a separate verification step |
| **Rollback** | The pilot's value: a complete rollback rehearsal on 788 KB before committing to 183 MB. C2 makes rollback a deletion of `_raw_lake` rather than a data restore |
| **Verification** | C5 is the heavyweight item and should be built once, at the pilot, then reused. It is the only control standing between the backfill and a silently changed published result |

---

## 4. `DS-DEC-005` — Dataset registry storage location

### 4.1 The decision

Roadmap §4.2: *"Dataset storage location — one `dataset_registry.db`, or co-located."* Bears on `LR4` (the `forward_evidence.db` / `evidence_forward.db` transposition hazard) and on backup shape, exactly as `LS-DEC-001` did for the Label Store.

### 4.2 Verified naming context

Databases currently on disk: `forward_evidence.db`, `evidence_forward.db` (the documented `LR4` transposition pair), `ledger.db`, `feature_store.db`, `label_store.db`.

**The established per-store pattern is `<dir>/<dir>.db`:** `research/feature_store/feature_store.db`, `research/label_store/label_store.db`.

### 4.3 Architectural options

| Option | Description |
|---|---|
| **A. `research/dataset_builder/dataset_builder.db`** | New database, following the established `<dir>/<dir>.db` pattern |
| **B. Co-locate in `label_store.db`** | Datasets cite labels most heavily; one file |
| **C. Split — `dataset_builder.db` + separate `citation_index.db`** | The index has a different access pattern (read-heavy, cross-store) |
| **D. Co-locate in `ledger.db`** | Governance artifacts together |

### 4.4 Trade-offs

| | A | B | C | D |
|---|---|---|---|---|
| **`LR4` naming hazard** | **Low** — `dataset_builder.db` shares no stem with any existing file | Low (no new name) | **Moderate** — two new names, one of them generic | Low |
| **Pattern consistency** | **Exact** — third instance of `<dir>/<dir>.db` | Breaks it | Partial | Breaks it |
| **Single-writer clarity (IQROS-P2)** | **Clean** — one writer per store | **Violated** — two subsystems writing one file | Clean but two writers to reason about | **Violated** |
| **Backup shape** | One new `--dataset-builder` unit, mirroring `--label-store` | Free | Two units | Free |
| **Citation index cross-store reads** | Reads label/feature registries as separate files — already the norm (`propagation.py` does exactly this) | Same-file reads for labels only | Same as A | Same as A |
| **Blast radius of corruption** | Dataset layer only | **Dataset + all 148 irreplaceable forward labels** | Smallest per file | Dataset + governance ledger |

### 4.5 Recommendation — **Option A, `research/dataset_builder/dataset_builder.db`**

Option B is the one to actively avoid: co-locating the dataset registry with `label_store.db` would put a brand-new, high-churn subsystem in the same file as the **148 irreplaceable forward labels**, and would give that file two writers, violating IQROS-P2. `LS-DEC-001` accepted co-location within the Label Store because those tables share one subsystem and one writer — that reasoning does not extend across subsystems.

Option D has the same two-writer problem against the governance ledger.

Option C is defensible but premature: the citation index's access pattern is a guess until it exists, and a second new filename is a second `LR4` opportunity. If profiling later justifies a split, moving one table out is a contained migration.

**Option A** is the third instance of a pattern already applied twice, introduces a filename sharing no stem with the existing transposition pair, keeps one writer per store, and bounds corruption blast radius to the layer that can be rebuilt from its own append-only history.

### 4.6 Impacts

| Dimension | Impact |
|---|---|
| **Implementation** | `registry.py` and `citation_index.py` both take an explicit `db_path`; no default that could hit the wrong file (`LR4` discipline, as every Phase 3 script does). A schema assertion refuses a non-dataset database before any write |
| **Rollback** | Contained: dropping `dataset_builder.db` removes the entire dataset layer with **zero** effect on the Feature Store, Label Store, ledger, or H6. Under B or D, rollback would require surgery inside a file holding irreplaceable data |
| **Verification** | Adds one `--dataset-builder` backup unit and one freshness check, mirroring WBS 3.10's already-reviewed shape. A restore drill should follow the `label-store-restore-drill` template. `DBV1` gains a fourth database to integrity-check |

---

## 5. Summary of Recommendations

| Decision | Recommendation | Confidence | Blocks |
|---|---|---|---|
| **`DS-DEC-001`** | **Accept** the backfill as WBS 4.0, labelled as inherited Phase 1 work | High | ✅ **RATIFIED — ADOPTED** |
| **`DS-DEC-002`** | **Option C — hybrid**: local re-derivation for all 274, plus a sampled re-download that *reports* drift. **Full re-download rejected** | High | ✅ **RATIFIED — ADOPTED** |
| **`G-CORPUS`** | **Pilot on dvol first (64 partitions / 788 KB)**, then open with binding conditions C1–C6 | High | ✅ **RATIFIED — GATE OPENED** |
| **`DS-DEC-005`** | **`research/dataset_builder/dataset_builder.db`** — third instance of the established per-store pattern | High | ✅ **RATIFIED — ADOPTED** |

> **Ratified in full on 2026-08-06.** All four recommendations were adopted exactly as written, with no variation. The roadmap was revised to **v1.0.2** accordingly; see the [Readiness Revision](verification/records/phase-4-readiness-revision.md) §3 for the applied decision log.

**The one recommendation that changes the roadmap's own framing:** `DS-DEC-002` Option B/C was not visible when the roadmap was written, which described 4.0 as *"re-ingest"*. Because `land_raw()` takes a DataFrame and `partition_ceiling()` computes from a report over the data, conformance is achievable **without network access and without any possibility of content change** — which converts `DR1` (Moderate/Severe) from a procedurally-mitigated risk into a structurally-impossible one.

**This is not a factual error in the roadmap**, so the roadmap is left unmodified per instruction. It is a better option discovered by deeper investigation. If Rohith adopts Option B or C, roadmap §4.1's WBS 4.0 exit criterion and §11's `DR1` mitigation would both be worth restating in a v1.0.1 revision — recommended, not applied.

---

## 6. What This Brief Does Not Decide

| Item | Why | Owner |
|---|---|---|
| `DS-DEC-003` — corpus version (`FS-GAP-005`) | Due before 4.3, not D0. Phase 3 §4.3's **binding do-not-invent instruction stands** until jointly decided | Rohith + `T-2` |
| `DS-DEC-004` — authorise RF2 to modify three analysis scripts | Due before 4.6, not D0 | Rohith |
| `DS-DEC-006` — retro-binding the 70 historical ledger runs | `ADR-I012` R9 puts it explicitly out of Phase 4 scope | Rohith |
| ~~Phase 3 formal closure — 13 unsigned records, `LS-DEC-003/004/005/007/008/009`~~ ✅ **DISCHARGED 2026-08-07** | Entry criterion `E2`, independent of these four | Rohith |

---

| Role | Name | Date |
|---|---|---|
| Decision brief | Claude Opus 5 | 2026-08-06 |
| **`DS-DEC-001` ruling** | **Rohith Suresh** | ✅ **ADOPTED 2026-08-06** |
| **`DS-DEC-002` ruling** | **Rohith Suresh** | ✅ **ADOPTED 2026-08-06** |
| **`G-CORPUS` ruling** | **Rohith Suresh** | ✅ **ADOPTED 2026-08-06** |
| **`DS-DEC-005` ruling** | **Rohith Suresh** | ✅ **ADOPTED 2026-08-06** |
