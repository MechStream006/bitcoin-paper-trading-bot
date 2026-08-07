# Phase 4 (Dataset Builder) — Implementation Roadmap

| Field | Value |
|---|---|
| **Title** | Engineering Implementation Roadmap — Phase 4, Research Dataset Builder |
| **Status** | **Draft — planning artifact. WBS 4.0 COMPLETE (D0a+D0b). `E2` DISCHARGED 2026-08-07 — Sprint D1 OPEN; WBS 4.1 awaits owner authorisation** |
| **Version** | **1.0.2** |
| **Document Type** | Engineering Implementation Plan (not an ADR, not an architecture document) |
| **Governing ADR** | [`ADR-I012`](../architecture/adr/ADR-I012-research-dataset-builder.md) Research Dataset Builder v0.1.0 (**Draft**) |
| **Parent roadmap** | [`implementation_roadmap_v1.md`](implementation_roadmap_v1.md) v1.0.1, Phase 4 (WBS 4.1–4.7) |
| **Predecessors** | [Phase 4 Gap Matrix](verification/records/phase-4-gap-matrix.md) · [Phase 3 Closeout](verification/records/phase-3-closeout.md) |
| **Companion** | [Blocker Decision Brief](ds_dec_phase4_blockers_decision_brief.md) · [Phase 4 Readiness Revision](verification/records/phase-4-readiness-revision.md) |
| **Authors** | Rohith Suresh — Owner / Architect · Drafted with Claude Opus 5 |
| **Date** | 2026-08-06 |
| **Changelog** | **1.0.2 (2026-08-06):** records Rohith's formal ratification of `DS-DEC-001`, `DS-DEC-002`, `G-CORPUS` and `DS-DEC-005`. Applies each adopted recommendation to scope, method, sequencing and deliverables: WBS 4.0 becomes **local re-derivation + sampled verification** rather than re-download; `DR1` is re-rated from Moderate/**Severe** to **Low** on a structural, not procedural, basis; sprint D0 splits into **D0a (dvol pilot) / D0b (full)**; `G-CORPUS` opens under binding conditions `C1`–`C6`; the dataset registry is fixed at `research/dataset_builder/dataset_builder.db`. Discharges `E7`, `E8`, `E10`. **No architectural change; `ADR-I012` untouched.** **1.0.1 (2026-08-06):** factual update only. Discharges entry criterion `E3` and assumption `A9` — WBS 3.11 is committed (`ffaae2b`), tagged `phase-3-baseline-v1.1.0`, and pushed; local and remote are identical at `0 0` divergence. Re-rates `DR8`. **No architectural change, no scope change, no WBS change.** The four `DS-DEC` recommendations remain **unratified** and are deliberately NOT reflected in scope, method, or deliverable wording — see [Readiness Revision §3](verification/records/phase-4-readiness-revision.md). **1.0.0 (2026-08-06):** initial draft following the `ADR-I012` architecture audit |
| **Code written** | **None.** Documentation only |

> **What this document is not.** It designs no architecture and decides nothing `ADR-I012` has not already decided. Where it proposes a file path, table name, or signature, that is **engineering planning** — one conforming realisation of an already-written contract. The audit that grounds it is [`phase-4-gap-matrix.md`](verification/records/phase-4-gap-matrix.md); this roadmap does not restate its evidence, only its conclusions.

---

## 0. Standing Gates This Roadmap Does Not Open

`G-CONST` (IQROS constitutional adoption — `ADR-I001` unwritten) and `G-STAB` (stabilisation posture) both remain closed, unchanged from the parent roadmap §0 and Phase 3 §0. `G-PROD` (host-side execution) is not required by any item here — Phase 4 is entirely repository-side.

**One gate is specific to Phase 4:**

| Gate | Statement | State |
|---|---|---|
| **`G-CORPUS`** | WBS 4.0 conforms the real research corpus. 274 existing partitions gain conformed revisions. No prior phase has touched landed research data | ✅ **OPENED 2026-08-06** by Rohith, under binding conditions `C1`–`C6` below, and **staged**: dvol pilot (D0a) before the full corpus (D0b) |

### 0.1 `G-CORPUS` binding conditions *(v1.0.2, ratified)*

Every condition is a precondition of WBS 4.0, verifiable in its own verification record. Failing any one halts the sprint.

| # | Condition |
|---|---|
| **C1** | Full backup of `research/data/raw/` taken and verified before any write |
| **C2** | **Supersede, never replace** (`ADR-I009` §16.1) — original partitions remain byte-identical and resolvable, proven by SHA-256 before/after |
| **C3** | Dry-run default; `--commit` gated; idempotent across two consecutive runs |
| **C4** | **Per-partition invariant:** landed content hash **equals** the pre-existing manifest's `parquet_sha256` — available only under the ratified re-derivation method, and the reason `DR1` is now structural rather than procedural |
| **C5** | Result-invariance proof: the three lockbox-bearing evidence scripts re-run before and after, outputs **bit-identical** |
| **C6** | Full transcript committed in the same session (`P4-D4`) |

---

## 1. Audit Summary

Full evidence in the [Gap Matrix](verification/records/phase-4-gap-matrix.md). Five conclusions change what Phase 4 must build:

| # | Finding | Consequence |
|---|---|---|
| **A-1** | **`ADR-I012` contains no factual error about this repository.** Every claim it makes — PS3's script-level lockbox, PS7's absent index, §1's 70 runs — was verified true | Unlike Phase 3, no "build to the verified repository, not the ADR text" carve-out is needed. **Build to the ADR** |
| **A-2** | **0 of 274 real partitions carry a certification verdict, conditioning, or a revision reference.** No `_raw_lake` exists. The ingest code is wired; the data predates the wiring | §12 Tier C and RF3 are **unsatisfiable on real data**. Owned by **WBS 4.0** |
| **A-3** | **All 148 labels are `Context`.** No Forward-evidence label exists | §15.2's forward-lane floor makes a **Training Dataset structurally unbuildable**. WBS 4.10 builds the structure; no real instance is expected |
| **A-4** | **The lockbox constant is triplicated** across three analysis scripts | RF2's relocation is real behavioural change to shipped code — **WBS 4.6**, separately gated |
| **A-5** | **`dataset` subject kind and `dataset.promote`/`.retire` already exist** in the receipt taxonomy; `dataset.seal` does not | Sealing needs one additive taxonomy row, the same mechanism used four times before |

---

## 2. Assumption Verification — Phase 3 Closeout Handover Re-checked

| # | Assumption as handed over | Verdict |
|---|---|---|
| **A1** | 17 Feature Specifications, all `Active`, resolvable by hash | ✅ **Holds** |
| **A2** | Label Specifications resolvable; ≥1 `Active` | ✅ **Holds** — 4 Active after WBS 3.11 |
| **A3** | 148 forward labels, `Context`, never Forward evidence | ✅ **Holds, and is load-bearing** — see `A-3` |
| **A4** | Admissibility composition available for both stores | ✅ **Holds** |
| **A5** | Receipt substrate chain-valid | ✅ **Holds** — 8 / 17 / 26 |
| **A6** | Every real ceiling caps at `Certification.UNKNOWN` | ⚠️ **Holds, with a correction.** `LS-TD-011` said only `dvol.py` was gauntlet-wired; **all three sources are**. The real cause is that no partition has been re-ingested |
| **A7** | `conditioning.py` and `engine.py` unmodified | ✅ **Holds** |
| **A8** | Both suites green | ✅ **Holds** — 2289 / 1910 |
| **A9** | WBS 3.11 committed, tagged, pushed | ✅ **Now holds** *(v1.0.1)*. Commit `ffaae2b`, tag `phase-3-baseline-v1.1.0`, both on `origin/main`; divergence `0 0`. `E3` discharged |

---

## 3. Scope Transfers — `T-1`, `T-3` Ratified; `T-2`, `T-4`–`T-6` Pending

| ID | Transfer | Recommendation | Lands in |
|---|---|---|---|
| **`T-1`** | **The corpus conformance backfill enters Phase 4.** It is Phase 1 completion work, but Phase 4 is its only consumer and the only phase that can verify it end-to-end | ✅ **ACCEPTED — ratified 2026-08-06 (`DS-DEC-001`).** Enters as WBS 4.0, flagged as inherited Phase 1 work rather than silently absorbed | WBS 4.0 |
| **`T-2`** | **`FS-GAP-005` (corpus version) becomes jointly ownable.** Phase 3 §4.3 blocked it on "Phase 1 + Phase 4" with a binding do-not-invent instruction | ⚠️ **ACCEPT, scoped to 4.0 + 4.3.** The do-not-invent instruction **stands until jointly decided**: a corpus version is defined by what the raw lake actually lands, not by the builder | WBS 4.0, 4.3 |
| **`T-3`** | **RF2 modifies existing analysis scripts.** The first Phase 4 change outside `research/dataset_builder/` | ✅ **ACCEPTED — ratified 2026-08-07 (`DS-DEC-004`), scoped.** Analysis scripts producing published output only; `evidence_library` migration/seed/replay/historical scripts excluded. Relocation must not change any published result, and the lockbox must not be opened to prove it | WBS 4.6 |
| **`T-4`** | **Training Dataset scope reduces to structure only** | ✅ **ACCEPT.** Building the layer is correct; expecting a real instance is not (`A-3`) | WBS 4.10 |
| **`T-5`** | **`ADR-I011` LG7/DI8 use-counting lands here**, as Phase 3 §4.3 assigned | ✅ **ACCEPT as proposed** | WBS 4.9 |
| **`T-6`** | **`ADR-I010` §11 / `ADR-I011` LG5 retirement checks land here**, via the citation index | ✅ **ACCEPT as proposed** | WBS 4.2, 4.11 |

---

## 4. WBS Breakdown

Sizes are t-shirt (S = one focused session, M = 2–4, L = 5+), calibrated against Phase 3's actual pace.

### 4.1 Items

| ID | Item | Owns (`ADR-I012` §) | Size | Depends on | Entry criterion | Exit criterion |
|---|---|---|---|---|---|---|
| **4.0** | ⚠️ **Corpus conformance backfill** (inherited Phase 1), by **local re-derivation** *(v1.0.2, `DS-DEC-002` ratified)*. Read each existing partition's Parquet, feed the `DataFrame` to `land_raw()` + `write_manifest()` with certification from `partition_ceiling()` over the same data, so each partition gains a verdict, conditioning, a `_raw_lake` revision and a normalization-contract version. **No re-download.** Plus a `--verify-sample N` mode that re-fetches a sample and **reports** upstream drift without landing it | §7.2 evidence refs · §12 Tier C · §14 (`T-1`, `T-2`) | **L** | — | `G-CORPUS` conditions `C1`–`C6` (§0.1); **D0a pilot passed** before D0b | Every partition carries a verdict, conditioning and a revision ordinal; `_raw_lake` populated; **original partitions byte-identical, superseded never replaced** (`ADR-I009` §16.1, condition `C2`); **every landed content hash equals the pre-existing manifest's `parquet_sha256`** (`C4`); idempotent across two runs; sampled drift reported, never absorbed |
| **4.1** | `research/dataset_builder/registry.py` — Dataset Specifications: content-addressed, append-only, 7-phase lifecycle, Tier A over every §7.2 field. Backed by **`research/dataset_builder/dataset_builder.db`** *(v1.0.2, `DS-DEC-005` ratified)* — the third instance of the established `<dir>/<dir>.db` pattern, one writer per store | §6 · §7.1 · §7.2 · §10 · §11 · §12 Tier A | M | 0.1–0.3 | Phase 3 formally closed | Registry accepts a hand-written declaration; **Tier A refuses on each mandatory field, one negative test per field**; a sealed record cannot be edited, proven by direct-`sqlite3` attempt; **no default `--db`**, and a schema assertion refuses a non-dataset database before any write (`LR4`) |
| **4.2** | `research/dataset_builder/citation_index.py` — **bidirectional** index | **§6.1** · DG-5 · `T-6` | M | 4.1 | 4.1 merged | Forward and reverse both answerable; `retire_specification()` in **both** upstream stores refuses while cited, closing `ADR-I010` §11 and `ADR-I011` LG5 |
| **4.3** | Declaration + Resolution (stages 1–2), incl. **RF1**, **RF5**, **RF6** | §8 stages 1–2 · §8.1 RF1/RF5/RF6 · §14 | **L** | 4.1, 4.2, 4.0 | 4.0 complete | Every reference bound to a hash at `as_of`; corpus revisions pinned; **RF1 refuses the 4 `Registered` Label Specifications by name**; RF5 refuses an unsatisfiable maturity requirement; RF6 refuses insufficient warm-up |
| **4.4** | Admissibility composition + Validation Tiers A–D (stages 3–4), incl. **RF4** | §8 stages 3–4 · §8.1 RF4 · §9.1 · §12 | **L** | 4.3 | 4.3 merged | The meet is computed once and **stated explicitly**, never asserted; RF4 refuses floor > meet; all four tiers implemented; a hard failure produces a **permanent `Rejected` record** (verified against `LS-TD-002`'s class of defect) |
| **4.5** | Seal (stage 5) + `dataset.seal` receipts | §8 stage 5 · §11 · DG-1 · DG-2 | M | 4.4 | 4.4 merged | Sealing emits a chained, replayable receipt; **a service actor is refused** (DG-1 is a human act); one additive `DECISION_TAXONOMY` row; `verify_chain()` clean |
| **4.6** | ⚠️ **RF2 — sealed-region relocation** from analysis scripts to the data boundary | §8.1 RF2 · IQROS-P7 · **resolves PS3** (`T-3`) | M | 4.3 | 4.3 merged; `T-3` accepted | Exclusion applied at resolution; the three scripts' triplicated constant reads from one governed source; **every published result reproduces bit-identically before and after**, proven by re-run |
| **4.7** | **RF3 — lane enforcement** | §8.1 RF3 · IQROS-P6 · **resolves PS4** | M | 4.0, 4.3 | 4.0 complete (lane per partition exists) | A confirmatory declaration resolving discovery-lane inputs is refused by name |
| **4.8** | Snapshot + Manifest materialiser (off-lifecycle) | §8.2 · §9 · §16 L1/L2 | **L** | 4.5 | 4.5 merged | Materialisation idempotent and verifiable; a Snapshot + Manifest is interpretable **without registry access**; a content mismatch is a reported **divergence event**, never reconciled |
| **4.9** | Experiment Dataset + use-counting | §7.1 · §15.3 · DG-8 · closes `ADR-I011` LG7/DI8 (`T-5`) | M | 4.5 | 4.5 merged | An Experiment Dataset binds trial context; reuse across trials is **visible, not inferred**; use-count recorded against the consuming decision |
| **4.10** | Training Dataset **structure** + partition roles | §7.1 · §15 · §15.1 · DG-7 (`T-4`) | M | 4.5, 4.8 | 4.5, 4.8 merged | Three separate sealed partitions with sibling hashes; **non-overlap verified at seal**; holdout single-use enforced; **RF4 correctly refuses every declaration today**, proven by a negative test — no real instance is expected (`A-3`) |
| **4.11** | Retirement, supersession, `as_of` reconstruction | §10.2 · §11 · §16 L3 · DG-5 | S | 4.2, 4.5 | 4.2, 4.5 merged | A superseded dataset stays resolvable and citable; retirement refuses while cited; `--as-of` reconstructs the registry at any past instant |

### 4.2 Decisions register

| ID | Decision | Owner | Due | Status |
|---|---|---|---|---|
| **`DS-DEC-001`** | Accept or reject `T-1` (corpus backfill enters Phase 4) | Rohith | Before D0 | ✅ **RATIFIED 2026-08-06 — ACCEPTED.** Enters as WBS 4.0 |
| **`DS-DEC-002`** | Conformance **method** — full re-download, local re-derivation, hybrid, or forward-only | Rohith | Before D0 | ✅ **RATIFIED 2026-08-06 — ADOPTED (hybrid).** Local re-derivation for all 274 + sampled re-download that reports drift; **full re-download rejected** |
| **`G-CORPUS`** | Open the corpus gate | Rohith | Before D0 | ✅ **RATIFIED 2026-08-06 — OPENED** under conditions `C1`–`C6`, staged D0a → D0b (§0.1) |
| **`DS-DEC-003`** | `FS-GAP-005` — define corpus version, or keep the do-not-invent instruction | Rohith + `T-2` | Before 4.3 | **Owed.** Do-not-invent instruction **stands** |
| **`DS-DEC-004`** | Accept `T-3` — authorise RF2 to modify three analysis scripts | Rohith | Before 4.6 | ✅ **RATIFIED 2026-08-07 — ACCEPTED, scoped.** RF2 may modify **only** analysis scripts that participate in published research output. `evidence_library` migration, seed, replay and historical-governance scripts are **excluded** absent a separate decision. Result invariance is the primary acceptance criterion; any observable difference is a failed milestone; **the sealed lockbox must not be opened or consumed during verification** |
| **`DS-DEC-005`** | Dataset storage location | Rohith | Before 4.1 | ✅ **RATIFIED 2026-08-06 — `research/dataset_builder/dataset_builder.db`** |
| **`DS-DEC-006`** | Whether the 70 historical ledger runs are retro-bound to Experiment Datasets | Rohith | Not scheduled | **Owed.** `ADR-I012` R9 makes this explicitly out of scope here |

### 4.3 Explicitly out of Phase 4 scope, with the owner named

| Item | Why | Owner |
|---|---|---|
| Model training, evaluation, serving, registry | `ADR-I012` §5 non-goal | `ADR-I022` (unwritten) |
| Wiring evaluation runs to cite datasets | `ADR-I012` R9 — *"gated and separate"* | `DS-DEC-006` |
| Statistical gates, trial denominators, promotion thresholds | Frozen by governance session (IQROS-P9) | Unchanged |
| Cross-asset / multi-asset datasets | Only BTC exists; §17's alignment rule needs a second asset to be meaningful | Deferred — asset-gated |
| A **real** Training Dataset instance | Structurally unbuildable (`A-3`) | Forward-evidence accrual — calendar-time |
| `label_policy.yaml`, `label_migration_register.md`, Tier D contract extension | Phase 3 deliverables (`LS-GAP-006`) | Phase 3 closeout follow-up |
| Fixing `LS-TD-002` | Pre-existing Label Store defect | Separately scoped; **4.4 must not replicate it** |

---

## 5. Sprint Sequencing

| Sprint | WBS | Exit criterion |
|---|---|---|
| **D0a** | 4.0 *(pilot)* | 🔄 **dvol only — 64 partitions / 788 KB** *(v1.0.2, `G-CORPUS` staged)*. Conditions `C1`–`C6` all demonstrated at small scale, including a full rollback rehearsal. **Gate: D0b does not begin until D0a passes** |
| **D0b** | 4.0 *(full)* | Remaining 210 partitions (funding, perp, spot) conformed on the pilot-proven mechanism. **Prerequisite — no builder work begins first** |
| **D1** | 4.1 → 4.2 | Registry + citation index; both upstream stores' retirement refusals now enforceable |
| **D2** | 4.3 | Declaration and resolution; RF1/RF5/RF6 refuse by name |
| **D3** | 4.4 → 4.5 | Meet composed and stated; Tiers A–D; sealing emits a human-gated receipt |
| **D4** | 4.6, 4.7 | RF2 relocation (results bit-identical); RF3 lane enforcement |
| **D5** | 4.8 | Materialiser; L1/L2; divergence events |
| **D6** | 4.9, 4.10, 4.11 | Experiment Datasets use-counted; Training structure; retirement/supersession/`as_of` |

**Ordering constraints:** `4.0 → 4.3` (resolution needs revisions), `4.1 → 4.2` (index needs the registry), `4.4 → 4.5` (never seal unvalidated), `4.0 → 4.7` (lane per partition), `4.5 → 4.8` (materialise only sealed). D4's two items are independent.

**D0a/D0b are separable and revertible**, touching only research data with full backup — they may be run and reviewed before any builder code exists. Under the ratified re-derivation method, condition `C4` makes reversion a deletion of `_raw_lake` rather than a data restore.

---

## 6. Dependencies

### 6.1 External to Phase 4

| Dependency | State | Blocking? |
|---|---|---|
| Phase 0 — lattice, receipt substrate | ✅ Complete | Hard dependency |
| Phase 1 — ingest conformance, raw lake **code** | ✅ Complete | — |
| Phase 1 — conformed corpus **data** | ❌ **Absent** | **Blocks 4.3, 4.7** → WBS 4.0 |
| Phase 2 — Feature Store | ✅ 17 Active | Blocks 4.3 |
| Phase 3 — Label Store | ✅ 4 Active | Blocks 4.3 |
| Phase 3 formal closure | ✅ **DISCHARGED 2026-08-07** — 13 records signed; all 6 decisions ratified | **D1 unblocked** (`E2`) |
| WBS 3.11 committed | ✅ `ffaae2b`, tagged and pushed | No longer blocking *(v1.0.1)* |

### 6.2 Internal

```
4.0 corpus backfill ─┬─────────────────────────────→ 4.3 resolution ─┬─→ 4.4 validation ─→ 4.5 seal ─┬─→ 4.8 materialiser
                     └─→ 4.7 lane (RF3) ────────────────┘            │                                ├─→ 4.9 experiment
4.1 registry ─→ 4.2 citation index ────────────────→ 4.3            └─→ 4.6 RF2 relocation           ├─→ 4.10 training
                     └──────────────────────────────────────────────────→ 4.11 retirement ───────────┘
```

### 6.3 Code reused rather than re-implemented

| Source | Used by | Basis |
|---|---|---|
| `research/admissibility/{lattice,composition}.py` | 4.4 | The meet already exists; 4.4 supplies the composition *point* |
| `label_store/propagation.py` | 4.4 | Supplies both the label and feature ceiling legs |
| `label_store/set_hygiene.py` | 4.4 | Four Tier D statistics already computed |
| `feature_store/warmup.py` | 4.3 | RF6's input |
| `label_store/set_hygiene.request_fully_matured()` | 4.3 | **Is** the DI2 maturity-requirement contract |
| `evidence_library/{receipts,receipt_ledger}.py` | 4.5, 4.9 | One additive taxonomy row |
| `evidence_library/record.derive_state_at()` | 4.11 | L3 `as_of` reconstruction precedent |
| `feature_store/registry.py`, `label_store/registry.py` | 4.1 | **Pattern only, not import** — a third registry, semantically distinct |

---

## 7. Entry Criteria

| # | Criterion | State |
|---|---|---|
| **E1** | Phase 0/1/2/3 code complete | ✅ |
| **E2** | **Phase 3 formally closed** — 13 records signed; `LS-DEC-003/004/005/007/008/009` ratified | ✅ **DISCHARGED 2026-08-07** — [Approval Package](verification/records/phase-3-closure-approval-package.md) |
| **E3** | **WBS 3.11 committed and pushed** | ✅ **Discharged** *(v1.0.1)* — `ffaae2b`, tag `phase-3-baseline-v1.1.0`, on remote |
| **E4** | `ADR-I012` audited against the repository | ✅ [Gap Matrix](verification/records/phase-4-gap-matrix.md) |
| **E5** | `ADR-I012` requires no revision | ✅ Determined — none required |
| **E6** | ≥1 Active Feature and ≥1 Active Label Specification | ✅ 17 and 4 |
| **E7** | `DS-DEC-001`, `DS-DEC-002` decided | ✅ **Discharged** *(v1.0.2)* — both ratified 2026-08-06 |
| **E8** | `DS-DEC-005` decided | ✅ **Discharged** *(v1.0.2)* — `dataset_builder.db` ratified |
| **E9** | Full-suite baselines established | ✅ 2289/19/0 and 1910/60/0 |
| **E10** | `G-CORPUS` opened | ✅ **Discharged** *(v1.0.2)* — opened under `C1`–`C6`, staged D0a → D0b |

✅ **All entry criteria are discharged** *(2026-08-07)*. `E2` — Phase 3's formal closure, the last remaining blocker — was discharged on 2026-08-07 via the [Phase 3 Closure Approval Package](verification/records/phase-3-closure-approval-package.md): 13 records signed, `LS-DEC-003/004/005/007/008/009` ratified. **Sprint D1 is open**; WBS 4.1 begins on explicit owner authorisation. *(v1.0.2: `E7`, `E10` discharged; v1.0.1: `E3` discharged.)*

---

## 8. Exit Criteria

### 8.1 Per-milestone

The Engineering Verification Standard's `D1`–`D11` apply unchanged, plus:

| # | Criterion |
|---|---|
| **P4-D1** | Every immutability claim proven at the **storage layer**, adversarially — a direct `sqlite3` write against a sealed record must fail |
| **P4-D2** | Every refusal (RF1–RF6, Tier A–C hard checks) has **one negative test each**, refusing **by name** |
| **P4-D3** | Every rejection path **persists its permanent record before propagating** — explicitly verified against `LS-TD-002`'s failure mode, which this phase must not replicate |
| **P4-D4** | Any item touching real research data (4.0, 4.6) captures its full transcript into `docs/engineering/verification/records/` in the same session |
| **P4-D5** | Reviewer sign-off obtained **per milestone** — Phase 3 accumulated 13 unsigned records; Phase 4 does not repeat that |

### 8.2 Phase-level

| # | Criterion |
|---|---|
| **P4-1** | A Research Dataset seals successfully against **real** Phase 1–3 data, with its admissibility meet stated explicitly |
| **P4-2** | All six refusal points refuse by name, each with a negative test (**resolves PS2, PS3, PS4**) |
| **P4-3** | The bidirectional citation index answers both directions, and **both** upstream stores' retirement refusals are enforceable (**resolves PS7**) |
| **P4-4** | Sealed-region exclusion is applied at resolution; the three analysis scripts no longer each own the boundary, and **every published result is bit-identical** before and after (**resolves PS3**) |
| **P4-5** | An Experiment Dataset records trial context and use-count; substrate reuse is visible (**resolves PS6**, closes `ADR-I011` LG7/DI8) |
| **P4-6** | Training Dataset structure exists with three-partition siblings and single-use holdout; **RF4 correctly refuses every declaration today**, proven by negative test (**resolves PS5** structurally) |
| **P4-7** | L1/L2/L3 reproducibility each demonstrated separately, never conflated |
| **P4-8** | The builder reads **no** operational data — proven by an import-boundary test |
| **P4-9** | `report.py --check` clean; `verify_chain()` clean on all four ledgers; both full suites at zero new failures |
| **P4-10** | A Phase 4 Closure Review is produced, including an `ADR-I012` Gap Matrix with an explicit **Unowned** bucket |

---

## 9. Verification Strategy

Governed by the [Engineering Verification Standard](verification/engineering_verification_standard.md) v1.0.0.

| Standard § | Phase 4 emphasis |
|---|---|
| **§3 BV** | Import-boundary AST test: no `research/dataset_builder/` module imports anything under `src/` (§8.3, DG9) |
| **§4 TV3** | Every milestone: unit-level sabotage; from D0, live-script sabotage against a **disposable copy** of real data, reverted with hash-identical confirmation |
| **§4 TV4** | `ADR-I012` specifies more refusals than any prior ADR: RF1–RF6, Tier A per-field, Tier B temporal, Tier C admissibility, plus DG-3/DG-5/DG-7 violations. **One negative test each** |
| **§4 TV5** | Content-address stability across lifecycle transitions; supersession never invalidates; sealed-record immutability; holdout single-use; **use-count monotonicity** |
| **§5 REG** | Both environments, both scopes, every milestone |
| **§7 DBV** | **Applies from 4.0 onward; `DBV5` has no N/A path.** Backup + `PRAGMA integrity_check` before any real write. Migrations dry-run/`--commit`-gated, idempotent, proven by running twice |
| **§12 RC** | Sign-off obtained per milestone (`P4-D5`) |

**Three obligations with no Phase 3 analogue:**

1. **Result-invariance proof for RF2.** Relocating the lockbox must not change any published number. Re-run the three affected scripts before and after; outputs must be bit-identical. This is the only Phase 4 item that can silently invalidate existing research.
2. **Corpus supersession, not replacement.** WBS 4.0 lands *new revisions*; the 274 original partitions remain resolvable (`ADR-I009` §16.1). A backfill that deletes history is a failure, not a success. 🔄 *(v1.0.2)* Under the ratified re-derivation method this is verified by **condition `C4`** — a per-partition hash-equality assertion — rather than by inspection alone.
3. **Negative-instance verification.** `P4-6` requires proving Training Datasets are correctly *refused*. A test that passes because the feature is missing is worthless; it must fail if RF4 stops refusing.

---

## 10. Rollback Strategy

| Mechanism | Lever | Effect when disabled |
|---|---|---|
| Corpus backfill (4.0) | 🔄 *(v1.0.2)* **Delete `_raw_lake`** — under re-derivation the original 274 partitions are never written to, so rollback is removing an additive tree, not restoring data. `C1`'s backup remains as defence in depth | Returns to today's unconformed corpus — a regression, never a default |
| Dataset Registry (4.1) | Revert; no runtime consumer until 4.3 | Nothing depends on it |
| Citation index (4.2) | `citation_enforcement: false` → index maintained, retirement not gated | Retirement falls back to today's unchecked behaviour, logged |
| Resolution + RF1/RF5/RF6 (4.3) | Revert; sealing does not yet exist | No data implication |
| Validation + RF4 (4.4) | `admissibility_enforcement: false` → meet computed and recorded, not gating. **Mirrors WBS 3.7's precedent** | Ceilings recorded, not enforced |
| Sealing (4.5) | Revert; the ledger is append-only, so emitted receipts persist | Emission stops; history never rewritten |
| **RF2 relocation (4.6)** | ⚠️ **Revert the commit.** The three scripts' original constants are restored from git | **Deliberate exception:** no configuration lever. A switch that disables sealed-region enforcement is a switch that will eventually be left off — the same reasoning Phase 3 §10 applied to its storage-layer triggers |
| RF3 (4.7) | `lane_enforcement: false` | Lane recorded, not gating |
| Materialiser (4.8) | Revert; Snapshots are disposable by design | Sealed datasets remain complete without bytes |
| Use-counting (4.9) | Revert; counts are append-only and persist | Counting stops; history preserved |
| Training structure (4.10) | Revert; no real instance exists to orphan | None |

**Lesson carried from Phase 2 and 3:** every `warn`-mode flag writes to a **queryable table**, not stdout, from its first commit (`FS-TD-009`).

---

## 11. Technical Risks

| ID | Risk | Likelihood / Impact | Mitigation |
|---|---|---|---|
| **`DR1`** | **The corpus backfill changes a published result** | 🔄 **Low / Severe** *(v1.0.2, likelihood re-rated)* | **Structurally mitigated, not procedurally.** The ratified re-derivation method (`DS-DEC-002`) never re-downloads, so partition content cannot change; condition `C4` asserts per-partition hash equality against the pre-existing manifest, making a content change a **failed precondition rather than a detected regression**. `C2` (supersede-never-replace) and `C5` (bit-identical script re-run) remain as defence in depth. Full re-download — the variant that made this Severe/likely — was explicitly rejected |
| **`DR2`** | **RF2 relocation silently changes lockbox semantics.** Three scripts may not slice identically | **Moderate / Severe** | Bit-identical result proof (§9); relocate only after proving the three constants and slices are equivalent |
| **`DR3`** | **Tier C is unsatisfiable, so nothing ever seals** | **Certain if 4.0 is skipped / Severe** | 4.0 is a hard prerequisite, gated at `E7`/`E10` |
| **`DR4`** | **`LS-TD-002`'s defect is replicated** in a third registry — a rejection that raises without persisting | **Moderate / Medium** | `P4-D3` makes it an explicit per-milestone criterion, with a dedicated test |
| **`DR5`** | **The citation index becomes load-bearing for three ADRs** (`ADR-I012` R7) | **Certain / Medium** | Accepted and deliberate. Land it early (D1) so downstream items exercise it continuously |
| **`DR6`** | **Scope inflation** — `ADR-I012` has six object classes, five pipeline stages, six refusals, four validation tiers, three reproducibility levels | **High / Medium** | §4.3's exclusion table restated each revision; every WBS item maps to a numbered ADR clause |
| **`DR7`** | **Training Dataset work produces no verifiable instance** | **Certain / Low** | Accepted (`A-3`). `P4-6` verifies the **refusal**, which is testable today |
| **`DR8`** | **Phase 4 begins on an unclosed Phase 3** | ✅ **RETIRED** *(2026-08-07)* | Both halves closed. `E3` made Phase 3's code durable at `ffaae2b`; `E2` was discharged on 2026-08-07 — all 13 records signed and all six decisions ratified in one review. Neither the durability nor the governance exposure remains |
| **`DR9`** | **`FS-GAP-005` gets invented under delivery pressure** | **Moderate / Severe** | Phase 3 §4.3's binding do-not-invent instruction **stands**; `DS-DEC-003` is the only path to changing it |

---

## 12. Integration Points

| # | Point | Nature | Direction |
|---|---|---|---|
| **J1** | **Registry pattern** | Mirrored, not imported — a third registry with genuinely different semantics | Feature/Label → Dataset, design-time |
| **J2** | **`research/admissibility/`** | Imported unchanged; 4.4 is the composition *point* the lattice always lacked | Shared dependency |
| **J3** | **`label_store/propagation.py`** | Imported; supplies both ceiling legs | Label Store → Builder |
| **J4** | **`set_hygiene.request_fully_matured()`** | Imported; **is** DI2's contract | Label Store → Builder |
| **J5** | **`feature_store/warmup.py`** | Imported; RF6's input | Feature Store → Builder |
| **J6** | **Receipt substrate** | One additive taxonomy row (`dataset.seal`) | Shared dependency |
| **J7** | ⚠️ **Authorised changes to upstream code** | 4.2 wires citation checks into **both** stores' `retire_specification()`; 4.6 modifies three analysis scripts. Both additive to behaviour, neither changes a registry's data model | Builder → upstream, **gated** (`T-3`, `T-6`) |
| **J8** | **Deliberate non-integration** | The builder never imports `src/`, never opens an operational database, and holds no privileged path (§8.3) | Stated to prevent erosion |

---

## 13. Deliverable Definitions

### 13.1 Code

| Deliverable | Path | WBS |
|---|---|---|
| Corpus conformance (re-derivation + `--verify-sample`) | `research/ingest/backfill_conformance.py` | 4.0 |
| Dataset Registry | `research/dataset_builder/registry.py` | 4.1 |
| Dataset registry database | **`research/dataset_builder/dataset_builder.db`** *(`DS-DEC-005`)* — gitignored, plus a `--dataset-builder` backup unit mirroring `--label-store` | 4.1 |
| Citation index | `research/dataset_builder/citation_index.py` | 4.2 |
| Declaration + Resolution | `research/dataset_builder/resolution.py` | 4.3 |
| Validation + composition | `research/dataset_builder/validation.py` | 4.4 |
| Sealing + receipts | `research/dataset_builder/sealing.py` | 4.5 |
| Sealed-region policy | `research/dataset_builder/sealed_regions.py` | 4.6 |
| Materialiser | `research/dataset_builder/materialise.py` | 4.8 |
| Experiment / Training layers | `research/dataset_builder/experiment.py`, `training.py` | 4.9, 4.10 |
| Rollout config | `research/dataset_builder/dataset_policy.yaml` | 4.2, 4.4, 4.7 |

### 13.2 Tests

One file per module under `tests/`, house style. Base-env importable where no pandas dependency exists, gated per-class where it does. **Every refusal in §9's TV4 list gets one negative test.**

### 13.3 Documentation

| Deliverable | Path | WBS |
|---|---|---|
| Verification record per milestone | `docs/engineering/verification/records/wbs-4.<n>-<slug>.md` | each |
| Corpus backfill transcript | `docs/ops/corpus-conformance-backfill-<date>.md` | 4.0 |
| RF2 result-invariance transcript | `docs/engineering/verification/records/rf2-result-invariance.md` | 4.6 |
| Dataset Builder TD Register | `docs/engineering/dataset_builder_technical_debt_register.md` | closeout |
| Phase 4 Closure Review + Closeout | `docs/engineering/verification/records/phase-4-{closure-review,closeout}.md` | closeout |

---

## 14. Self-Review

| Check | Result |
|---|---|
| Any architecture designed, amended, or newly decided? | **No.** Every item traces to a numbered `ADR-I012` clause. The audit found no ADR error, so no departure from the ADR's text was needed |
| Is every `ADR-I012` requirement owned? | **Yes** — §2 of the Gap Matrix maps every clause. Six are owned outside Phase 4 (`ADR-I022`, `DS-DEC-006`, asset-gated, forward-evidence-gated, Phase 3 follow-ups) |
| Does every WBS item have a separable exit criterion? | **Yes** (§4.1) |
| Was existing numbering preserved? | **Yes.** Parent 4.1–4.7 retain their intent; 4.0 is added for inherited Phase 1 work; 4.8–4.11 split the parent's 4.5/4.6 and add retirement |
| Is scope creep pre-refused? | **Yes** — §4.3 names seven exclusions with an owner each |
| Are Phase 3's mistakes carried forward as lessons? | **Yes** — unsigned records (`P4-D5`), uncommitted work (`E3`, now discharged and proven closeable in one commit), warn-mode durable capture (§10), rejection-record defect (`P4-D3`), stale TD entries (`A-6`) |
| Were any `docs/architecture/` files touched? | **No** |
| Does it begin Phase 4? | **No.** Planning only |

---

## 15. Status

**This roadmap is complete at v1.0.2. Phase 4 is not started. Sprint D0a is AUTHORISED; the builder itself is not.**

🔄 **v1.0.2 — partial authorisation.** All four entry decisions are ratified, so the corpus-conformance sprint may begin. The builder sprints (D1 onward) remain blocked on **one** item: Phase 3's formal closure.

| Blocker | State *(v1.0.2)* | Discharge |
|---|---|---|
| ~~`E7` — `DS-DEC-001`, `DS-DEC-002`~~ | ✅ **DISCHARGED** — ratified 2026-08-06 | Backfill accepted as WBS 4.0; hybrid re-derivation adopted |
| ~~`E10` — `G-CORPUS`~~ | ✅ **DISCHARGED** — opened 2026-08-06 | Conditions `C1`–`C6`; staged D0a → D0b |
| ~~`E8` — `DS-DEC-005`~~ | ✅ **DISCHARGED** — ratified 2026-08-06 | `research/dataset_builder/dataset_builder.db` |
| ~~`E3` — WBS 3.11 committed~~ | ✅ **DISCHARGED** | `ffaae2b`, tag `phase-3-baseline-v1.1.0`, pushed. **`B-1` fully closed** |
| **`E2` — Phase 3 formal closure** | ✅ **DISCHARGED 2026-08-07** | 13 verification records signed; `LS-DEC-003/004/005/007/008/009` ratified |

**What may begin now:** **Sprint D1** — WBS 4.1 (Dataset Registry) then 4.2 (citation index). `E2` is discharged and no entry criterion remains open. WBS 4.0 (D0a + D0b) is complete: all 274 partitions conformed.

**What may not:** D2 onward (declaration, resolution, validation, sealing, materialisation) until D1 completes — the ordering constraints in §5 are unchanged by `E2`'s discharge. `G-CORPUS` remains bound by conditions `C1`–`C6`, and per-milestone sign-off (`P4-D5`) applies to every Phase 4 item.

**Three findings must be read before implementation**, because each would produce wrong work if missed: **`A-2`** (0/274 partitions carry a certification verdict — Tier C cannot pass on real data), **`A-3`** (no Forward-evidence label exists — Training Datasets are structurally unbuildable), and **`A-4`** (the lockbox constant is triplicated — RF2's relocation is a real behavioural change to shipped research code).

| Role | Name | Date |
|---|---|---|
| Roadmap draft | Claude Opus 5 | 2026-08-06 |
| **Phase 4 authorisation** | **Rohith Suresh** | **_______________** |
