# Phase 4 (Dataset Builder) — `ADR-I012` Architecture Audit and Gap Matrix

| Field | Value |
|---|---|
| **Title** | `ADR-I012` Architecture Audit and Phase 4 Gap Matrix |
| **Status** | **Complete — audit artifact. `ADR-I012` requires NO revision; two optional clarifications proposed** |
| **Version** | 1.0.0 |
| **Document Type** | Engineering audit artifact (not an ADR, not a roadmap) |
| **Governing ADR** | [`ADR-I012`](../../../architecture/adr/ADR-I012-research-dataset-builder.md) Research Dataset Builder v0.1.0 (**Draft**) |
| **Companion** | [Phase 4 Roadmap](../../phase_4_dataset_builder_roadmap.md) · [Phase 3 Closeout](phase-3-closeout.md) · [Phase 4 Readiness Resolution](phase-4-readiness-resolution.md) |
| **Authors** | Rohith Suresh — Owner / Architect · Audited with Claude Opus 5 |
| **Date** | 2026-08-06 |
| **Code written** | **None.** Read-only audit |

> **Method.** Every verdict below was established by reading source, querying real databases, or inspecting real files on disk in this session. Where a prior milestone record and the repository disagreed, **the repository won** and the disagreement is stated by name (§1.3). No prior report was accepted without checking.

---

## 0. Status Correction — Read First

The task brief states *"WBS 3.11 is complete, verified, committed, tagged, and pushed."* **Verified against `git`: the first two are true; the last three are not.**

```
HEAD = aae322e (tag: phase-3-baseline-v1.0.0, origin/main)   ← unchanged since the Phase 3 baseline

 M tests/test_migrate_bootstrap_label_receipts.py
?? research/label_store/activation.py
?? tests/test_label_store_activation.py
?? docs/engineering/verification/records/wbs-3.11-specification-activation.md
```

WBS 3.11's **data effect is real and durable** — the four activations were written to `label_store.db` and are present now (§1.1). What is *not* durable is **3.11's own source code**, which exists only as uncommitted working-tree state on one machine. This is a narrower recurrence of blocker `B-1`, and it should be discharged before Phase 4 begins, for the same reason `B-1` was: Phase 4 is a hard join point over all prior phases.

**Not blocking this audit** (documentation-only), but recorded because the brief asserted otherwise.

---

## 1. Substrate Audit — What Actually Exists

### 1.1 Governed specification layer — **strong**

| Asset | Verified state |
|---|---|
| Feature Specifications | **17, all `Active`**, content-addressed, with 17 chain-valid `feature.register` receipts |
| Label Specifications | **8 total: 4 `Active`** (H6 horizons 5/7/14/30, activated at WBS 3.11 on 148/148 behavioural equivalence), **4 `Registered`**, 8 chain-valid `label.register` receipts, **0 `Rejected`** |
| Label Values (forward tier) | **148 rows**, 100% `Matured`, 100% `Context` standing |
| Label Values (research tier) | **0 rows** (compute-on-read; correct) |
| Admissibility lattice | `research/admissibility/` — 5 dimensions, `meet`/`join`, pinned lattice version |
| Admissibility composition | `label_store/propagation.py` — per-value and set-level ceilings for **both** stores |
| Tier D statistics | `label_store/set_hygiene.py` — all four §12 Tier D checks |
| Receipt substrate | 3 chain-valid ledgers: label 8, feature 17, governance 26 |
| Warm-up | `feature_store/warmup.py` + `warmup_policy.yaml` |

### 1.2 Evidence/corpus layer — **materially weaker than the specification layer**

This is the audit's central finding, and it is not visible from any milestone record.

| ADR-I012 need | Verified reality |
|---|---|
| Certification verdict per partition (§12 Tier C) | **0 of 274 real partitions carry one** |
| Conditioning per partition (§7.2, §9.1) | **0 of 274** |
| Corpus revision set (§7.2, §14) | **No `_raw_lake` directory exists anywhere on disk** |
| Corpus version (§7.2 temporal scope) | Does not exist — `FS-GAP-005`, still open |
| Lane per source (§12 Tier C) | Lanes exist in `ledger.db` for **evidence runs**, not for **partitions** |

**Evidence.** 274 `_manifest.json` files exist under `research/data/raw/` (dvol 64, funding 78, perp 66, spot 66). Every one is `schema_version: 1`, written 2026-06-09/10, and carries exactly these keys:

```
schema_version, dataset, symbol, year, month, parquet_file, parquet_sha256,
rows, min_ts, max_ts, ts_column, source, source_url, ingest_ts, config_version
```

No `admissibility`, no `conditioning`, no revision ordinal, no normalization-contract version.

**The code is wired; the data is not.** All three ingest modules (`dvol.py`, `funding.py`, `binance_ohlcv.py`) import `quality.admissibility.partition_ceiling` and pass an `admissibility={...}` block to `write_manifest()`. `manifest.py` accepts `conditioning=` and `admissibility=` parameters. **But no real partition has been re-ingested since that wiring landed**, so every partition on disk predates it.

**Consequence for Phase 4:** `ADR-I012` §12 Tier C — *"Certification verdict present for every partition"* — is **unsatisfiable against real data today**. A conforming builder must refuse to seal any dataset over the existing corpus. This is correct ADR behaviour, and it is a **data prerequisite**, not a builder defect.

### 1.3 Prior-report corrections

Two milestone-record claims were checked and found **stale**:

| Claim | Source | Verified reality |
|---|---|---|
| *"only `dvol.py` is wired to `quality/admissibility.py`"* | WBS 3.7 TD, `LS-TD-011` | **Incorrect.** All three ingest modules are wired and record `certification` into their manifests. The real gap is different and larger: no partition has been *re-ingested* since, so 0/274 carry the block |
| *"WBS 3.11 … committed, tagged, and pushed"* | This task's brief | **Incorrect.** See §0 |

The `LS-TD-011` correction *narrows* the code gap and *widens* the data gap. Phase 4 planning must use the corrected version.

### 1.4 Sealed-region (lockbox) enforcement — PS3 confirmed exactly as the ADR describes

`ADR-I012` PS3 asserts sealed regions are *"enforced inside analysis code … One omission spends the lockbox silently."* **Confirmed literally**: the lockbox boundary is defined as a hardcoded constant in **three separate analysis scripts**, each re-implementing the same slice:

```
research/evidence/run_dvol_family.py:44    LOCKBOX_START_MS = 2025-06-01
research/evidence/run_harmonization.py:42  LOCKBOX_START_MS = 2025-06-01
research/evidence/run_phase_c.py:39        LOCKBOX_START_MS = 2025-06-01
    …each followed by: m_full[m_full["day_ts"] < LOCKBOX_START_MS]
```

Plus `run_lockbox_validation.py`, which deliberately opens it once. Nothing structural prevents a fourth script from omitting the slice. **RF2 is the relocation target, and the relocation is real work with a real payoff.**

### 1.5 Receipt taxonomy — partially pre-provisioned

| Element | State |
|---|---|
| `SUBJECT_KINDS` | **`"dataset"` already present** |
| `DECISION_TAXONOMY` | **`dataset.promote`, `dataset.retire` already present** (both `discretionary`) |
| `dataset.seal` | **Absent** — required by DG-1 |
| `ACTIONS` | `promote`/`retire`/`activate` present; **no `seal` verb** |

Phase 4 needs a small additive extension (`dataset.seal`, action `seal`), following the same precedent used four times already (PR-7's `evaluate`, WBS 3.8a's `register`/`adjudicate`).

---

## 2. Gap Matrix — Every `ADR-I012` Clause

**Legend:** ✅ Implemented · 🔨 Requires Phase 4 · 📋 Requires roadmap change (outside Phase 4) · ⛔ No longer applicable

### 2.1 Registry and Specifications (§§6–7)

| Clause | Requirement | Verdict | Evidence / owner |
|---|---|---|---|
| §6 | Registry holds specifications, never content | 🔨 | `research/dataset_builder/` does not exist |
| §6 | Content addressing, append-only, phase-tracked, storage-level immutability, single writer | 🔨 | Pattern proven twice (Feature/Label registries); mirror it |
| **§6.1** | **Bidirectional citation index** | 🔨 | **Absent.** The single highest-value Phase 4 deliverable — it discharges obligations `ADR-I009` §15.1, `ADR-I010` §11 and `ADR-I011` §12.1/LG5 each assert but none can enforce |
| §6.2 | Refuse unresolvable/non-Active reference | 🔨 | Enforceable today: 17 Active features, 4 Active labels, 4 Registered labels correctly ineligible |
| §6.2 | Refuse sealed-region intersection without exclusion | 🔨 | See §1.4 |
| §6.2 | Refuse missing conditioning / `as_of` / lane | 🔨 | |
| §6.2 | Refuse retirement with live citation | 🔨 | Both stores' `retire_specification()` explicitly defer this to the index |
| §7.1 | Six object classes | 🔨 | None exist |
| §7.2 | Intent, temporal scope | 🔨 | |
| §7.2 | Conditioning statement | 🔨 + 📋 | Propagation exists both stores; **but 0/274 partitions carry conditioning** (§1.2) |
| §7.2 | Evidence refs: revision sets, certification verdict, lane | 🔨 + 📋 | **Blocked on the corpus backfill.** No `_raw_lake`; 0/274 verdicts |
| §7.2 | Feature Specification hashes | ✅ | 17 Active, resolvable by hash |
| §7.2 | Label Specification hashes **+ maturity requirement** | ✅ | 4 Active; `set_hygiene.request_fully_matured(admit_partial=)` **is** the DI2 contract |
| §7.2 | Sealed-region exclusions | 🔨 | |
| §7.2 | Admissibility floor | 🔨 | Lattice exists; the floor field does not |
| §7.2 | Lane | 🔨 + 📋 | Lanes exist per *run* in `ledger.db`, not per *partition* |
| §7.2 | No field defaultable | 🔨 | Tier A precedent in both registries |

### 2.2 Assembly Pipeline (§8)

| Clause | Requirement | Verdict | Evidence / owner |
|---|---|---|---|
| §8 stage 1 | Declaration | 🔨 | |
| §8 stage 2 | Resolution; pin corpus revisions; apply exclusions | 🔨 + 📋 | Pinning needs revision sets that do not exist |
| §8 stage 3 | Admissibility composition (meet) | ✅ substrate / 🔨 wiring | `composition.meet()` + `propagation.py` supply both legs; the dataset-level composition point does not exist |
| §8 stage 4 | Validation battery | 🔨 | |
| §8 stage 5 | Seal: freeze, hash, receipt, publish | 🔨 | Receipt substrate ready; needs `dataset.seal` taxonomy row |
| **RF1** | Unresolvable/non-Active reference | 🔨 | Directly enforceable today |
| **RF2** | Sealed-region intersection | 🔨 | **The relocation PS3 describes.** Triplicated constant today (§1.4) |
| **RF3** | Lane conflict | 🔨 + 📋 | Needs per-partition lane |
| **RF4** | Declared floor exceeds computed meet | 🔨 | Lattice ready |
| **RF5** | Label maturity unsatisfiable | ✅ substrate / 🔨 wiring | `PartialMaturityRefusalError` already refuses by name |
| **RF6** | Warm-up unavailable in window | ✅ substrate / 🔨 wiring | `warmup.py` |
| §8.2 | Materialisation off-lifecycle; divergence events | 🔨 | |
| §8.3 | Builder never reads operational data | ✅ *(invariant to preserve)* | No `research/` module imports `src/`; verified repeatedly through Phases 0–3 |

### 2.3 Metadata, Versioning, Lifecycle, Validation (§§9–12)

| Clause | Requirement | Verdict | Notes |
|---|---|---|---|
| §9 | Four metadata levels | 🔨 | |
| §9.1 | Conditioning composed; admissibility is the meet | 🔨 + 📋 | Will compute honestly to a low ceiling until the corpus backfill lands |
| §10 | Declaration hash / lineage / ordinal | 🔨 | |
| §10.1 | Extension / Refinement / Redefinition | 🔨 | |
| §10.2 | Supersession never invalidates | 🔨 | Precedent: `ADR-I010` §10.2, `ADR-I011` §11.1 |
| §10.3 | Label maturation is not a version change | ✅ *(upstream)* | Proven at WBS 3.1 (spec hash stable across maturation) |
| §11 | 7-phase lifecycle, `Rejected` permanent | 🔨 | Both registries' allowed-arc pattern transfers |
| §12 Tier A | Structural | 🔨 | |
| §12 Tier B | Temporal integrity | 🔨 | |
| **§12 Tier C** | **Certification per partition; lane; readiness; meet stated; floor ≤ meet** | 🔨 + 📋 | **Unsatisfiable on real data today — 0/274.** The single hardest Phase 4 dependency |
| §12 Tier D | Statistical hygiene, caps not blocks | ✅ partial / 🔨 | `set_hygiene.py` already computes maturity fraction, horizon coverage, outcome distribution incl. class imbalance, standing composition. Dataset-level coverage/regime-span/degenerate-column checks remain |
| §12 | Assert, never repair | ✅ *(invariant)* | Established discipline across Phases 0–3 |

### 2.4 Governance, Provenance, Research vs Training (§§13–16)

| Clause | Requirement | Verdict | Notes |
|---|---|---|---|
| §13.1 | Separated authorities | 🔨 | |
| DG-1 | Sealing is a human act emitting a receipt | 🔨 | Needs `dataset.seal`; human-actor refusal precedent exists (`label_receipts`) |
| DG-2 | Resolution/validation are computations and replay | 🔨 | `derived` receipt replay proven at PR-6 and WBS 3.8a |
| DG-3 | Sealed spec never edited | 🔨 | Trigger precedent |
| DG-4 | Rejected datasets permanent | 🔨 | ⚠️ See `LS-TD-002` caveat in §3.2 |
| DG-5 | Cited dataset never retired | 🔨 | Requires §6.1 |
| DG-6 | Registry holds no content | 🔨 | |
| DG-7 | Holdout single-use | 🔨 | |
| DG-8 | Experiment Datasets use-counted | 🔨 | Also closes `ADR-I011` LG7/DI8 |
| §14 | Provenance closure (9 elements) | 🔨 + 📋 | 5 available now; corpus revisions, verdicts/lanes unavailable until backfill |
| §15 | Research vs Training obligations | 🔨 | |
| **§15.2** | **Training Dataset floor ≥ forward lane** | 🔨 **but unbuildable** | **All 148 labels are `Context`.** No Forward-evidence label exists anywhere. A Training Dataset is therefore *structurally impossible today* — correctly, per RF4. See §3.1 |
| §15.1 | Three separate sealed partitions | 🔨 | |
| §16 L1/L2/L3 | Three reproducibility guarantees | 🔨 | L3 precedent: `record.derive_state_at()` |
| §17 | Multi-asset; declared alignment | 🔨 partial | Single-asset (BTC) only today; alignment rule field still required |

### 2.5 Obsolete assumptions in `ADR-I012`

**None found.** Every factual claim `ADR-I012` makes about the platform was checked and holds:

| Claim | Verified |
|---|---|
| §1 — 70 ledger evaluation runs recording source revision, function hash, window, engine version, frozen denominator | ✅ Consistent with `ledger.db` (35 families, 70 runs) |
| PS3 — sealed regions enforced inside analysis code | ✅ Confirmed literally (§1.4) |
| PS7 — no citation index exists | ✅ Confirmed absent |
| §20.2 — `ADR-I014` *"now written"* | ✅ Present and consumed |
| R9 — nothing in the platform cites a dataset today | ✅ Confirmed |

**This is a materially better starting position than Phase 3 had.** `ADR-I011` contained two verified factual errors about this repository (`A-2`, `A-3`) that would have produced wrong work. `ADR-I012` contains none.

---

## 3. Determination — Does `ADR-I012` Require Revision?

### ⛔ **No. `ADR-I012` does NOT require revision before implementation.**

No architectural inconsistency, no governance conflict, and no factual error about this repository was found. Every gap identified is an **implementation or data gap**, not a specification defect.

Two **optional clarifications** are proposed below. Neither changes a decision; both make an already-correct reading explicit so a future implementer does not have to re-derive it. **Neither is applied — the ADR is unmodified.**

### 3.1 Proposed clarification A — Training Datasets are gated on forward-evidence accrual

**Why.** §15.2 requires a Training Dataset's admissibility floor to be *"≥ forward lane"*. Every label in the platform is `Context` standing (148/148, verified), because WBS 3.4's 148 H6 labels were bootstrap-attested and — correctly, per `ADR-I011` §4.4 and risk `LR2` — may never be reclassified. **No Forward-evidence label exists anywhere.** A Training Dataset is therefore structurally unbuildable until forward evidence accrues under the Label Store, which is a calendar-time dependency no engineering effort can shorten.

This is the ADR working exactly as designed. But a reader planning Phase 4 could easily assume Training Datasets are in scope.

**Exact proposed addition — new paragraph at the end of §15.2:**

> **Ordering consequence.** A Training Dataset cannot exist before Forward-evidence labels do. Where a platform's entire label population carries `Context` standing — for example, labels wrapped from a pre-existing system under `ADR-I011` §4.4's bootstrap attestation — RF4 will refuse every Training Dataset declaration, correctly. Research Datasets and Experiment Datasets are unaffected and may be sealed immediately. This is a calendar-time dependency, not an engineering one: the Training Dataset layer becomes reachable only once forward evidence has accrued.

### 3.2 Proposed clarification B — `Rejected` permanence depends on the substrate registry

**Why.** DG-4 and §12 require a Rejected dataset to be a permanent record. The Label Store's registry has a verified defect (`LS-TD-002`, established empirically at the Phase 3 Closeout): a `None` on any `NOT NULL` column raises `SpecificationRejected` correctly but **persists no `Rejected` row**, because the `IntegrityError` is mis-attributed to the duplicate-hash branch. A Dataset Registry mirroring that pattern would inherit the same hole in DG-4.

**Exact proposed addition — footnote to DG-4 in §13.2:**

> **DG-4 implementation note.** A rejection that raises without persisting its negative record satisfies the caller contract while violating this rule. Implementations must verify that every rejection path — including constraint violations raised by the storage layer itself, not only application-level validation failures — writes the permanent record before propagating the error.

### 3.3 Items deliberately **not** proposed as ADR changes

| Considered | Why not |
|---|---|
| Weakening §12 Tier C's per-partition certification requirement | The requirement is correct. The gap is that no partition has been re-ingested. Relaxing the ADR to match deficient data would invert evidence-first architecture |
| Adding a `corpus_version` definition | `FS-GAP-005` is blocked on Phase 1 + Phase 4 jointly, and Phase 3's roadmap §4.3 carries a **binding instruction not to invent one**. It remains binding |
| Adding `dataset.seal` to the ADR | The ADR specifies *that* sealing emits a receipt (DG-1); the taxonomy row is an implementation detail, added additively as four prior milestones did |

---

## 4. Roadmap Implications

| # | Implication | Consequence for Phase 4 |
|---|---|---|
| **I1** | **The corpus backfill is a hard prerequisite, not a Phase 4 item.** 0/274 partitions carry certification, conditioning, or a revision reference; no `_raw_lake` exists | Tier C and RF3 cannot pass on real data. Either a **Phase 1 completion sprint** re-ingests the corpus through the conformed writers, or Phase 4 seals only against synthetic/partial corpora and defers real sealing |
| **I2** | **Training Datasets are out of reach** (§3.1) | Phase 4 should scope to Research + Experiment Datasets and build the Training Dataset layer's *structure* without expecting a real instance |
| **I3** | **The citation index is the highest-leverage deliverable** | It retro-discharges obligations in three prior ADRs. It should land early, not last |
| **I4** | **RF2's lockbox relocation touches existing analysis scripts** | Three scripts hold a triplicated constant. Relocation is a genuine behavioural change to shipped code and needs its own gate — the first time Phase 4 modifies anything outside `research/dataset_builder/` |
| **I5** | **`FS-GAP-005` (corpus version) finally becomes ownable** | Phase 3's roadmap blocked it on "Phase 1 + Phase 4". Phase 4 is now the counterparty; the do-not-invent instruction stands until it is *jointly* decided |
| **I6** | **`LS-GAP-002` residue** — 4 Label Specifications remain `Registered` | Correct and non-blocking: they carry no values, so no dataset could cite them meaningfully. RF1 will refuse them by name |
| **I7** | **13 verification records remain unsigned** (`LS-DEC-003`); `LS-DEC-004/005/007/008/009` unratified | Phase 3 is not formally closed. Phase 4 should not begin implementation while its predecessor's governance is open |

---

## 5. Audit Method

Read-only. Every verdict rests on one of:

1. **Full read of `ADR-I012`** (574 lines, all 20 sections).
2. **Live database queries** — `label_store.db` (8 specs: 4 Active / 4 Registered, 148 values, 8 receipts), `feature_store.db` (17 Active specs, 17 receipts, **no value-store tables**), `ledger.db` (26 receipts).
3. **Filesystem inspection of 274 real partition manifests** — parsed every one; confirmed `admissibility` and `conditioning` absent in all; dumped two in full to establish the actual key set.
4. **Absence proofs by path** — `research/dataset_builder/` and `_raw_lake` confirmed non-existent.
5. **Source reading** — all three ingest modules' certification wiring, `manifest.py`'s signature, `propagation.py`'s deliberate `UNKNOWN` construction, `receipts.py`'s taxonomy, `raw_lake.py`'s API.
6. **Grep-based enforcement survey** — located the triplicated `LOCKBOX_START_MS` constant across three analysis scripts.
7. **`git` state verification** — established the §0 status correction.

**No file was created, modified, or deleted** other than this document and its roadmap companion. No database was written to. No production code was written.

---

| Role | Name | Date |
|---|---|---|
| Architecture audit | Claude Opus 5 | 2026-08-06 |
| **Owner acceptance** | **Rohith Suresh** | **_______________** |
