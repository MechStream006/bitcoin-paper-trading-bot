# Phase 4 — Final Engineering Audit

| Field | Value |
|---|---|
| **Phase** | Phase 4 — Research Dataset Builder (WBS 4.0 – 4.11) + operational closure |
| **Governing ADR** | [`ADR-I012`](../../../architecture/adr/ADR-I012-research-dataset-builder.md) v0.1.0 (Draft) |
| **Roadmap** | [`phase_4_dataset_builder_roadmap.md`](../../phase_4_dataset_builder_roadmap.md) v1.0.2 |
| **Companion** | [Phase 4 Closure Review](phase-4-closure-review.md) — updated with the operational evidence below |
| **Date** | 2026-08-08 |
| **Author** | Claude Opus 5 |
| **Approver** | *(pending — Rohith)* |
| **Status** | **Phase 4 engineering + operational closure complete.** Three owner decisions outstanding (§12) |

---

## 1. Scope of this audit

This audit covers Phase 4 end to end: twelve WBS rows, the ADR clauses each
satisfies, the governance decisions taken and outstanding, and — new in this
session — the conversion of the Dataset Builder from a tested implementation
into a **real governed operational component**.

The decisive change is that `P4-1`, recorded as **UNMET** in the closure
review, is now **MET**: a real Research Dataset has been sealed against real
Phase 1–3 data, with a real receipt on the platform chain.

## 2. WBS 4.0 – 4.11 completion matrix

| WBS | Deliverable | Tests | Record | Status |
|---|---|---|---|---|
| 4.0 | Corpus conformance backfill (D0a, D0b, 4.0.1 repair) | — | [D0a](wbs-4.0-d0a-dvol-conformance-pilot.md) · [D0b](wbs-4.0-d0b-full-corpus-conformance.md) · [4.0.1](wbs-4.0.1-builder-manifest-upgrade-repair.md) | ✅ |
| 4.1 | Dataset Registry (content-addressed, 7-phase, Tier A) | 128 | [record](wbs-4.1-dataset-registry.md) | ✅ |
| 4.2 | Bidirectional Citation Index | 80 | [record](wbs-4.2-citation-index.md) | ✅ |
| 4.3 | Declaration + Resolution; RF1, RF5, RF6 | 74 | [record](wbs-4.3-declaration-resolution.md) | ✅ |
| 4.4 | Admissibility composition + Tiers A–D; RF4 | 87 | [record](wbs-4.4-validation-tiers.md) | ✅ |
| 4.5 | Seal + `dataset.seal` receipts | 69 | [record](wbs-4.5-dataset-sealing.md) | ✅ |
| 4.6 | RF2 — sealed-region relocation | 45 | [record](wbs-4.6-rf2-sealed-region-relocation.md) | ✅ |
| 4.7 | RF3 — lane enforcement | 62 | [record](wbs-4.7-rf3-lane-enforcement.md) | ✅ |
| 4.8 | Materialiser; L1 / L2 | 69 | [record](wbs-4.8-materialiser.md) | ✅ |
| 4.9 | Experiment Dataset + use-counting; DG-8 | 72 | [record](wbs-4.9-experiment-datasets.md) | ✅ |
| 4.10 | Training Dataset structure; DG-7 | 65 | [record](wbs-4.10-training-dataset-structure.md) | ✅ |
| 4.11 | Retirement, supersession, `--as-of` (L3) | 91 | [record](wbs-4.11-retirement-supersession-as-of.md) | ✅ |

**842 dedicated Dataset Builder tests.** Full suites after operational closure:
`btc_bot` **3233 passed / 19 skipped**, `base` **2784 passed / 62 skipped**.

## 3. Exit-criteria coverage

| # | Criterion | Status |
|---|---|---|
| **P4-1** | A Research Dataset seals against **real** Phase 1–3 data, meet stated explicitly | ✅ **NOW MET** — §5 |
| **P4-2** | All six refusal points refuse by name, with negative tests | ✅ — and **RF6 refused for real** in production data (§5.3) |
| **P4-3** | Citation index answers both directions; upstream retirement refusals enforceable | ✅ as worded; opt-in (`TD-4.2-1`) |
| **P4-4** | Sealed-region exclusion at resolution; published results bit-identical | ✅ 10 artifacts, 0 changed |
| **P4-5** | Experiment Dataset records trial context and use-count | ✅ |
| **P4-6** | Training structure; RF4 correctly refuses every declaration today | ✅ proven both directions |
| **P4-7** | L1 / L2 / L3 each demonstrated separately | ✅ L1/L2 at 4.8, L3 at 4.11 |
| **P4-8** | Builder reads no operational data — import-boundary test | ✅ AST test in every builder test module |
| **P4-9** | `report.py --check` clean; chains clean; suites zero new failures | ⚠️ **Partial** — one ledger carries receipts (§9) |
| **P4-10** | Closure Review with Gap Matrix incl. Unowned bucket | ✅ [Closure Review](phase-4-closure-review.md) |

## 4. ADR-I012 requirement coverage

| Clause | Owner | Status |
|---|---|---|
| §6 Registry · §6.1 Citation Index | 4.1, 4.2 | ✅ |
| §7.1 Six Distinctions · §7.2 Mandatory Declaration Content | 4.1, 4.9, 4.10 | ✅ — all §7.2 fields present in the real declaration |
| §8 five-stage pipeline | 4.3–4.5 | ✅ — all five stages executed on real data |
| §8.1 RF1–RF6 | 4.3, 4.4, 4.6, 4.7 | ✅ all six |
| §8.2 Materialisation off-lifecycle | 4.8 | ✅ built; **not exercised on real data** (§10) |
| §9 / §9.1 Metadata + meet | 4.4 | ✅ |
| §10.1 Redefinition = new lineage | 4.1 | ✅ |
| §10.2 Supersession never invalidates | 4.11 | ✅ |
| §10.3 Superseded-input propagation | **Unowned** | ⚠️ flag-only (`HZ-4.11-1/2`) |
| §11 Seven-phase lifecycle | 4.1, 4.11 | ✅ |
| §12 Tiers A–D | 4.1, 4.4 | ✅ |
| §13.1 / §13.2 DG-1…DG-8 | across | ✅ all eight |
| §14 Provenance | 4.4, 4.5 | ✅ provenance closure persisted on the real seal |
| §15 Research vs Training | 4.10 | ✅ structure; no real instance possible (`A-3`) |
| §16 L1 / L2 / L3 | 4.8, 4.11 | ✅ distinct |

## 5. Operational closure — the first governed Research Dataset

### 5.1 The store

| Property | Value |
|---|---|
| Path | `research/dataset_builder/dataset_builder.db` (`DS-DEC-005`) |
| Created | 2026-08-08, by `bootstrap_first_dataset.py --commit` |
| Tables | `dataset_specifications`, `citations`, `sealed_datasets`, `materialisations`, `snapshot_pins`, `dataset_transitions`, `ceiling_staleness_flags`, `sqlite_sequence` |
| `PRAGMA integrity_check` | **ok** |
| Gitignored | Yes — the store is data, not source |
| Backup | ✅ `dataset_builder_20260808_120017.db` + SHA-256 manifest, via the pre-existing `backup.py --dataset-builder` |
| Healthcheck | ✅ `dataset_builder_backup OK (0.0h ago)` — the wiring built at 4.1 now has a store to watch |

### 5.2 The dataset

| Property | Value |
|---|---|
| Lineage | `ivrv-vrp-substrate`, version ordinal **2** |
| Spec hash | `f444c132b2a5d666b9946e08e204b46720690fb79928892a066dce333d245444` |
| Research question | Does the DVOL-implied minus realised volatility spread carry information about subsequent spot direction? |
| Window | **2021-05-01 … 2025-05-31**, `as_of` 2026-08-08, UTC, `exchange_daily_1200Z` |
| Evidence | `dvol` (49 partitions, cert meet **GREEN**, readiness **READY**, pin `rev-62ee257eee22939b`) · `spot` (49 partitions, cert meet **AMBER**, readiness **READY**, pin `rev-a87ba2697ad6bf83`) |
| Features | `dvol_frac`, `rv30`, `vrp_spread` — resolved to real **Active** Feature Specifications at run time |
| Label | `spot_one_day_forward_return` @ 5d, real **Active** Label Specification, `matured` |
| Sealed-region exclusion | lockbox 2025-06-01 … 2026-05-31 — **declared, and not intersected** |
| Computed meet | `OFFLINE_CORPUS / AMBER / READY / FULL / CLEAN` |
| Persisted ceiling | identical to the Tier-D capped value (**`HZ-4.4-1` honoured**) |
| Validation fingerprint | `81b080ac91a269ae68c4ba9c6289af56…` |
| Sealed | 2026-08-08T11:57:35Z by `rohith`, `actor_type=human` (**DG-1**) |
| Receipt | `dataset.seal` — `871a4e54b5ab144f9963…` |

**The certification meet is AMBER, and that is the honest value.** Eight of the
50 in-window spot partitions carry an AMBER verdict, so the meet cannot be
GREEN. The declaration's `required_certification_verdict` is derived from the
corpus rather than asserted — declaring GREEN would have been a claim the data
does not support, and the declaration would have been refused for it.

### 5.3 RF6 refused the first declaration, and the refusal is permanent

The first attempt declared `window_start = 2021-04-01`. **RF6 refused it**:
`rv30` and `vrp_spread` each need 30 warm-up periods before the window, i.e.
data from 2021-03-02, and DVOL's inception is 2021-03-24.

That specification is recorded in the store **permanently, in phase
`Rejected`** (lineage `ivrv-vrp-substrate` v1, hash `2c9bf77fbcf0cf1c…`) with
the RF6 reason attached. It was **not deleted** — WBS 4.3's `P4-D3` design
exists precisely so a refusal cannot be erased by re-running until something
passes, and erasing the platform's first real refusal would have been the exact
failure that design anticipates.

The window was moved to 2021-05-01, the first clean month boundary at or after
the RF6 floor of 2021-04-23. **This is the strongest single piece of evidence
in Phase 4**: a refusal point refused a real declaration on real data, for a
real reason, before any human noticed the problem.

One defect of mine contributed and is recorded: the first run also passed a
*window-clipped* corpus coverage map, which would have made RF6 refuse on an
artefact of the clipping. Full coverage is now tracked separately from the
window-restricted admissibility aggregate. The refusal was correct on the
corrected facts too, which is why the window moved rather than the check.

### 5.4 Invariants verified on the live store

| Invariant | Result |
|---|---|
| Registry — phase, citability | `Sealed`, `is_citable = True` |
| Registry — declaration hash reproducible from stored body | ✅ recomputes to the same hash |
| Citation index — forward | 6 citations: 2 evidence, 3 feature, 1 label |
| Citation index — reverse | every cited id resolves back to this dataset |
| Citation index — consistency / orphans | ✅ consistent · **0 orphans** |
| Sealing — orphan seal receipts | **0** |
| History (L3) | 4 transitions `Declared → Resolved → Validated → Sealed`; completeness ✅ |
| L3 `--as-of` | at first transition → `Declared`; now → `Sealed`; at 2020-01-01 → registry empty |
| Append-only — registry | `DATASET_TAMPER` blocked a direct phase `UPDATE` |
| Append-only — history | `HISTORY_TAMPER` blocked a direct `DELETE` |
| Append-only — sealed row | `DG-3` blocked a direct `UPDATE` |
| Ledger chain | `ok=True, n_receipts=27` |
| Result invariance | 10 artifacts, **0 changed** |

## 6. Governance decisions

| Decision | State |
|---|---|
| `DS-DEC-001`, `DS-DEC-002`, `DS-DEC-005`, `G-CORPUS` | ✅ Ratified 2026-08-06 |
| `DS-DEC-004` + amendment (RF2 via Dataset Builder, Option B) | ✅ Ratified; frozen exceptions honoured |
| `LS-DEC-003/004/005/007/008/009` | ✅ Ratified 2026-08-07 (Phase 3 `E2` discharged) |
| `DS-DEC-003` | Do-not-invent stands; not scheduled |
| `DS-DEC-006` — retro-binding the 70 historical runs | **Owed**, untouched |
| `T-2`, `T-4`, `T-5`, `T-6` scope transfers | **Unratified**; `T-5`/`T-6` discharged in substance by 4.9/4.2 |

**No new governance decision was required for operational closure.**
`TD-4.5-1` had already settled the one open question — the seal receipt goes to
`evidence_library/ledger.db`, because "the ledger is the platform-wide chain and
must not fork."

## 7. Database and schema additions

| Store | Addition | Milestone |
|---|---|---|
| `dataset_builder.db` | **new store**, 8 tables | operational closure |
| `dataset_builder.db` | `dataset_transitions` (append-only, trigger-populated) | 4.11 |
| `dataset_builder.db` | `ceiling_staleness_flags` (append-only) | 4.11 |
| `evidence_library/ledger.db` | +1 row (`dataset.seal`), 26 → **27** | operational closure |

**No migration was required or run.** The store was created fresh by the
idempotent `init_db()` of each module; there was no prior schema to migrate.

## 8. Receipt types

| Type | Class | State |
|---|---|---|
| `dataset.seal` | discretionary | ✅ **first emission** — 1 receipt |
| `evidence.admit` / `evidence.reject` | derived | 9 / 4 (bootstrap) |
| `family.transition` | discretionary | 13 (bootstrap) |
| `dataset.promote` / `dataset.retire` | discretionary | reserved, unused |
| `contract.activate` / `contract.retire` | discretionary | reserved, unused |
| `readiness.verdict` | derived | reserved, unused |

All 27 receipts have `attestation_basis IS NULL` — **no receipt has ever been
backfilled**, which is the invariant the migration script was written to
preserve.

## 9. Production changes

**None.** Nothing was deployed.

| Aspect | Production | Local |
|---|---|---|
| `HEAD` | `59e4136`, 0 modified | `a87b113` + this closure commit |
| Ledger chain | `ok=True, 26 receipts` | `ok=True, **27** receipts` |
| `dataset_builder.db` | **absent** | present, sealed |
| Health | `overall INFO`, 0 non-OK, 0 failed units | — |
| Collection | `quotes=64/64`, integrity ok/ok/ok | — |

**The two chains have legitimately diverged**, and this is a state to decide
about rather than a defect: the first real governance act happened on the
canonical working copy, and production still runs pre-4.11 code. Reconciling
them is a deployment, which was explicitly out of scope here (§12, item 1).

## 10. Technical debt

38 items across 4.2–4.11 (full register in the
[Closure Review §6](phase-4-closure-review.md)). Five are High:
`TD-4.2-1`, `TD-4.4-3`, `TD-4.7-1`, `TD-4.9-2`, `TD-4.10-1`, plus 4.11's
`TD-4.11-3`. `TD-4.4-3` blocks `TD-4.10-1`.

**New from operational closure:**

| # | Item | Severity |
|---|---|---|
| **TD-4.12-1** | The sealed dataset is **not materialised**. §8.2 materialisation needs a real `ValueProvider` reading the corpus; none exists, and building one is new engineering no WBS row owns. So L1/L2 are demonstrated on fixtures, never on this dataset | 🟠 High |
| **TD-4.12-2** | `bootstrap_first_dataset.py` hard-codes the window, feature names and label horizon as module constants | 🟢 Low — deliberate: they are the declaration, and a declaration is meant to be fixed and reviewable, not parameterised |
| **TD-4.12-3** | Two tests pinned the real ledger at exactly 26 receipts and broke on the first genuine governance act | 🟡 Medium — corrected to a floor (`>= 26`) with the load-bearing assertions untouched; the lesson is that live-chain tests must not pin length |

## 11. Hazards

19 items (register in the Closure Review §7). `HZ-4.4-1` was resolved at 4.5;
`HZ-4.5-2` at 4.11.

The recurring theme stands and now has a fifth instance in practice: the
platform **records** high-consequence governance events faithfully and
**nothing surfaces them**. The first `dataset.seal` receipt was written to the
chain with no alert, no report entry and no operator-visible signal.

## 12. Remaining owner decisions

| # | Decision | Why it is yours |
|---|---|---|
| **1** | **Deploy Phase 4 to production** — reconciling `HEAD` `59e4136` → current, and the 26 ↔ 27 chain divergence | A production deployment. Explicitly out of scope for this session |
| **2** | **Accept or re-word `P4-9`'s "all four ledgers"** | Unsatisfiable as written — only one ledger carries receipts |
| **3** | **Sign the twelve WBS verification records** (RC checklists, 4.0–4.11) | Same discipline Phase 3's `E2` required |
| **4** | **Whether to materialise the first dataset** (`TD-4.12-1`) | Needs a real corpus-reading `ValueProvider` — new engineering, arguably Phase 5 |

Carried from earlier sessions and unrelated to Phase 4: 80,860 stale
`option_quotes` rows · backup capacity (~12 GB on a 29 GB disk) · the
`ws_manager.health()` defect · BK2 restore drill never performed · BK3 off-host
replication NOT ESTABLISHED.

## 13. Statement

Phase 4 is engineering-complete and now operationally real. A governed store
exists, holds one sealed Research Dataset built entirely from real Phase 1–3
data, with a real receipt on the platform chain, real citations in both
directions, a reconstructable history, enforced append-only guarantees, and a
verified backup.

It also holds one **permanently rejected** specification, which is the better
evidence: the refusal points are not decoration.

**Phase 5 has not been started.**
