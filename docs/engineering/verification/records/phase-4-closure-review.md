# Phase 4 — Dataset Builder — Closure Review

| Field | Value |
|---|---|
| **Phase** | Phase 4 — Research Dataset Builder (WBS 4.0 – 4.11) |
| **Governing ADR** | [`ADR-I012`](../../../architecture/adr/ADR-I012-research-dataset-builder.md) Research Dataset Builder v0.1.0 (**Draft**) |
| **Roadmap** | [`phase_4_dataset_builder_roadmap.md`](../../phase_4_dataset_builder_roadmap.md) v1.0.2 |
| **Satisfies** | Exit criterion **`P4-10`** — Closure Review incl. Gap Matrix with an explicit **Unowned** bucket |
| **Date** | 2026-08-08 |
| **Author** | Claude Opus 5 |
| **Approver** | *(pending — Rohith)* |
| **Status** | **IMPLEMENTATION COMPLETE · OPERATIONAL CLOSURE COMPLETE.** `P4-1` met 2026-08-08 — see §0 |
| **Superseding evidence** | [Phase 4 Engineering Audit](phase-4-engineering-audit.md) — operational record |

---

## 0. Update — 2026-08-08, operational closure

**`P4-1` is now MET.** This section is appended rather than rewritten: §3's
original UNMET finding was correct when written, and erasing it would hide the
gap that motivated the work.

`research/dataset_builder/dataset_builder.db` was created and **one** real
Research Dataset was sealed against real Phase 1–3 data:

| | |
|---|---|
| Spec hash | `f444c132b2a5d666b9946e08e204b46720690fb79928892a066dce333d245444` |
| Lineage | `ivrv-vrp-substrate` v2 |
| Window | 2021-05-01 … 2025-05-31 (lockbox excluded and not intersected) |
| Inputs | real `dvol` + `spot` partitions, 3 Active Features, 1 Active Label |
| Ceiling | `OFFLINE_CORPUS / AMBER / READY / FULL / CLEAN` — **AMBER because the corpus is AMBER**, not asserted |
| Receipt | `dataset.seal` `871a4e54b5ab144f9963…`; chain `ok=True, 27 receipts` |
| Backup | ✅ verified, with SHA-256 manifest; healthcheck reports `dataset_builder_backup OK` |

**RF6 refused the first declaration on real data** (`rv30` / `vrp_spread`
warm-up predates DVOL inception). That specification is recorded permanently as
`Rejected` (v1, `2c9bf77f…`) and was deliberately not erased.

Full evidence, invariant results, and the corrected `P4-9` position are in the
[Phase 4 Engineering Audit](phase-4-engineering-audit.md). §3 and §4 below
remain as originally written except for the `P4-1` status line.

---

## 1. Headline

**All twelve WBS rows (4.0 – 4.11) are implemented, tested, verified, committed
and pushed.** The Dataset Builder exists as specified: seven-phase lifecycle,
bidirectional citation index, five-stage pipeline, all six refusal points, Tier
A–D validation, sealing with receipts, off-lifecycle materialisation, experiment
use-counting, training structure, and historical reconstruction.

**Phase 4 is not, however, closable on exit criteria alone.** One criterion —
**`P4-1`** — is **UNMET**, and its resolution requires an owner-authorised
action this session deliberately did not take. §3 and §8 state it precisely.

This review does not mark Phase 4 closed. That is Rohith's decision, and §8
sets out exactly what it rests on.

## 2. WBS completion — 4.0 through 4.11

| WBS | Deliverable | Record | Status |
|---|---|---|---|
| **4.0** | Corpus conformance backfill (D0a pilot, D0b full) | [D0a](wbs-4.0-d0a-dvol-conformance-pilot.md) · [D0b](wbs-4.0-d0b-full-corpus-conformance.md) · [4.0.1 repair](wbs-4.0.1-builder-manifest-upgrade-repair.md) | ✅ Complete |
| **4.1** | Dataset Registry — content-addressed, 7-phase, Tier A | [record](wbs-4.1-dataset-registry.md) | ✅ Complete (128 tests) |
| **4.2** | Bidirectional Citation Index | [record](wbs-4.2-citation-index.md) | ✅ Complete (80 tests) |
| **4.3** | Declaration + Resolution; RF1, RF5, RF6 | [record](wbs-4.3-declaration-resolution.md) | ✅ Complete (74 tests) |
| **4.4** | Admissibility composition + Tiers A–D; RF4 | [record](wbs-4.4-validation-tiers.md) | ✅ Complete (87 tests) |
| **4.5** | Seal + `dataset.seal` receipts | [record](wbs-4.5-dataset-sealing.md) | ✅ Complete (69 tests) |
| **4.6** | RF2 — sealed-region relocation | [record](wbs-4.6-rf2-sealed-region-relocation.md) | ✅ Complete (45 tests) |
| **4.7** | RF3 — lane enforcement | [record](wbs-4.7-rf3-lane-enforcement.md) | ✅ Complete (62 tests) |
| **4.8** | Snapshot + Manifest materialiser; L1/L2 | [record](wbs-4.8-materialiser.md) | ✅ Complete (69 tests) |
| **4.9** | Experiment Dataset + use-counting; DG-8 | [record](wbs-4.9-experiment-datasets.md) | ✅ Complete (72 tests) |
| **4.10** | Training Dataset structure; DG-7 | [record](wbs-4.10-training-dataset-structure.md) | ✅ Complete (65 tests) |
| **4.11** | Retirement, supersession, `--as-of` (L3) | [record](wbs-4.11-retirement-supersession-as-of.md) | ✅ Complete (91 tests) |

**842 dedicated Dataset Builder tests.** Full suites: `btc_bot` **3233 passed /
19 skipped**, `base` **2784 passed / 62 skipped**, zero new failures at every
milestone.

**Every milestone was verified against production before and after.** No
milestone deployed anything; the builder is research-side and has never run on
the production host.

## 3. Exit criteria — `P4-1` through `P4-10`

| # | Criterion | Status |
|---|---|---|
| **P4-1** | A Research Dataset seals successfully against **real** Phase 1–3 data, with its admissibility meet stated explicitly | ✅ **MET 2026-08-08** (§0). *Original finding, retained:* ❌ UNMET — see below |
| **P4-2** | All six refusal points refuse by name, each with a negative test | ✅ RF1/RF5/RF6 (4.3), RF4 (4.4), RF2 (4.6), RF3 (4.7) |
| **P4-3** | Bidirectional index answers both directions; **both** upstream stores' retirement refusals are **enforceable** | ✅ Met as worded — `citation_check` exists on both `feature_store` and `label_store` `retire_specification()`. **Opt-in, defaults to `None`** (`TD-4.2-1`) |
| **P4-4** | Sealed-region exclusion applied at resolution; every published result bit-identical | ✅ 4.6 — **10 artifacts, 0 changed**, re-verified at every subsequent milestone |
| **P4-5** | Experiment Dataset records trial context and use-count; substrate reuse visible | ✅ 4.9 |
| **P4-6** | Training structure with three-partition siblings, single-use holdout; **RF4 correctly refuses every declaration today** | ✅ 4.10 — proven in both directions |
| **P4-7** | L1 / L2 / L3 each demonstrated **separately**, never conflated | ✅ L1/L2 at 4.8; **L3 at 4.11**; `verify_l3_composes()` checks composition without merging them |
| **P4-8** | The builder reads **no** operational data — proven by an import-boundary test | ✅ AST test in every builder test module, including 4.11 |
| **P4-9** | `report.py --check` clean; `verify_chain()` clean on all four ledgers; both suites zero new failures | ⚠️ **Partially met** — see below |
| **P4-10** | Phase 4 Closure Review with a Gap Matrix incl. explicit **Unowned** bucket | ✅ **This document** |

### `P4-1` — UNMET, and why it matters

**No Research Dataset has ever been sealed against real Phase 1–3 data.**

`research/dataset_builder/dataset_builder.db` — the store `DS-DEC-005` ratified
on 2026-08-06 — **does not exist**, locally or on the production host. Every one
of the 842 tests runs against a `tmp_path` store built from fixtures.

What this means precisely:

- the builder is **verified**, but **never exercised on real data**;
- `P4-1`'s explicit requirement — *"with its admissibility meet stated
  explicitly"* — has produced no artifact;
- the roadmap's own deliverable list (§ *"Dataset registry database …
  gitignored, plus a `--dataset-builder` backup unit"*) is half-built: the
  backup wiring landed at 4.1, the **store it backs up does not exist**.

This is **not** a defect in any milestone. Each WBS row built what its row
specified, and no row was assigned "instantiate the real store". It is
unassigned scope that the exit criteria assume and the WBS table never
allocated — recorded in §5 as **Unowned**.

**Why this session did not simply do it.** Creating the governed store is a new
persistent, backed-up, irreversible governance artifact — and sealing a real
dataset writes a permanent receipt into the chain. Both fall inside the standing
instruction to stop for irreversible actions and owner decisions. §8 states the
decision.

### `P4-9` — partially met

| Component | Status |
|---|---|
| `report.py --check` clean | ✅ 35 families (9 candidate / 22 discovery / 4 disqualified) |
| Both full suites, zero new failures | ✅ 3233 / 2784 |
| `verify_chain()` clean on **all four** ledgers | ⚠️ **Only one ledger carries governance receipts** |

Verified directly: `research/evidence_library/ledger.db` → `ok=True, 26
receipts`. `research/data/ledger.db` and the Label Store backup have **no
`governance_receipts` table**; `research/dataset_builder/dataset_builder.db`
does not exist (above). The "four ledgers" phrasing describes an end state the
platform has not reached. Reported rather than reinterpreted to fit.

## 4. `ADR-I012` Gap Matrix

| ADR clause | Owner | Status |
|---|---|---|
| §6 Registry, §6.1 Citation Index | 4.1, 4.2 | ✅ Built |
| §7.1 Six Distinctions, §7.2 Mandatory Declaration Content | 4.1 (Tier A), 4.9, 4.10 | ✅ Built |
| §8 Five-stage pipeline | 4.3, 4.4, 4.5 | ✅ Built |
| §8.1 RF1–RF6 | 4.3, 4.4, 4.6, 4.7 | ✅ All six |
| §8.2 Materialisation off-lifecycle | 4.8 | ✅ Built |
| §9 / §9.1 Metadata + meet | 4.4 | ✅ Built |
| §10.1 Redefinition = new lineage | 4.1 | ✅ Enforced (`redefinition` refused) |
| §10.2 Supersession never invalidates | **4.11** | ✅ Built + **verified after the fact** |
| §10.3 Superseded-input propagation (`ADR-I011`) | **— Unowned —** | ⚠️ **Partial** — 4.11 flags; nothing recomputes or alerts (`HZ-4.11-1`, `HZ-4.11-2`) |
| §11 Seven-phase lifecycle | 4.1, 4.11 | ✅ Complete incl. terminal phases |
| §12 Tiers A–D | 4.1, 4.4 | ✅ Built |
| §13.1 / §13.2 DG-1…DG-8 | across | ✅ All eight enforced |
| §14 Provenance | 4.4, 4.5 | ✅ Built |
| §15 / §15.1 / §15.2 Research vs Training | 4.10 | ✅ Structure; **no real instance possible** (`A-3`) |
| §15.3 Experiment orthogonality | 4.9 | ✅ Built |
| §16 L1 / L2 | 4.8 | ✅ Demonstrated |
| §16 **L3** | **4.11** | ✅ Demonstrated — **forward-only** (`TD-4.11-3`) |
| **Sealing against real data** | operational closure | ✅ **`P4-1` performed 2026-08-08** (§0) |
| **Materialising a real dataset** | **— Unowned —** | ❌ needs a corpus-reading `ValueProvider`; none exists (`TD-4.12-1`) |
| **Alerting on any recorded governance event** | **— Unowned —** | ⚠️ Four hazards, one pattern (§7) |

### Unowned bucket — explicit

Three items belong to no WBS row and to no ratified decision:

1. ~~**`P4-1` — real-data instantiation and seal.**~~ ✅ **Closed 2026-08-08**
   (§0). Superseded by: **materialising** that dataset, which still belongs to
   no row and needs a corpus-reading `ValueProvider` (`TD-4.12-1`).
2. **§10.3 superseded-input propagation.** `ADR-I011` specifies it; 4.11 makes
   the blast radius *computable* and *recordable*, but nothing invokes detection
   when an upstream specification is superseded.
3. **Governance alerting.** Four separate hazards (`HZ-4.8-1`, `HZ-4.9-1`,
   `HZ-4.10-2`, `HZ-4.11-1`) all say the same thing: the event is recorded and
   queryable, and **nothing notices it**. No row ever owned "surface governance
   events to an operator".

## 5. Deferred items still open

| Item | Origin | State |
|---|---|---|
| **`T-2`, `T-4`, `T-5`, `T-6`** scope transfers | Roadmap §3 | **Unratified.** `T-4`'s non-gating status was reasoned and recorded at 4.10 §1; `T-5`/`T-6` were discharged in substance by 4.9/4.2 without formal ratification |
| **`DS-DEC-003`** | Blocker brief | Do-not-invent stands; not scheduled |
| **`DS-DEC-006`** — retro-binding the 70 historical runs | 4.9 | **Owed**, untouched |
| **`TD-4.4-3`** — `InputAdmissibility` is caller-supplied | 4.4 | Open; **blocks `TD-4.10-1`** |
| **`TD-4.2-1`** — make `citation_check` mandatory upstream | 4.2 | Open; keeps `P4-3` opt-in |
| **`TD-4.7-1`** — RF3 inert until declarations carry a lane code | 4.7 | Open |

## 6. Technical debt register — 38 items

| Milestone | Items | Highest severity |
|---|---|---|
| 4.2 | `TD-4.2-1..3` | 🟠 High (`TD-4.2-1`) |
| 4.3 | `TD-4.3-1..4` | 🟡 Medium |
| 4.4 | `TD-4.4-1..4` | 🟠 High (`TD-4.4-3`) |
| 4.5 | `TD-4.5-1..4` | 🟡 Medium |
| 4.6 | `TD-4.6-1..3` | 🟡 Medium |
| 4.7 | `TD-4.7-1..3` | 🟠 High (`TD-4.7-1`) |
| 4.8 | `TD-4.8-1..4` | 🟡 Medium |
| 4.9 | `TD-4.9-1..4` | 🟠 High (`TD-4.9-2`) |
| 4.10 | `TD-4.10-1..4` | 🟠 High (`TD-4.10-1`) |
| **4.11** | `TD-4.11-1..5` | 🟠 High (`TD-4.11-3`) |

**Five High-severity items**, of which two are coupled: `TD-4.4-3` must be
resolved before `TD-4.10-1` can be, because making `computed_meet` mandatory
requires the meet to be derivable rather than supplied.

## 7. Hazards — 19 items, and one recurring theme

| Hazard | Severity |
|---|---|
| `HZ-4.2-1`, `HZ-4.3-1..2`, `HZ-4.4-1..2`, `HZ-4.5-1..2`, `HZ-4.6-1..2`, `HZ-4.7-1..2`, `HZ-4.8-1..2`, `HZ-4.9-1..2`, `HZ-4.10-1..2`, `HZ-4.11-1..2` | mixed |

`HZ-4.4-1` (capped admissibility persisted verbatim) was **resolved at 4.5**.
`HZ-4.5-2` (stale sealed ceiling) was **resolved at 4.11**.

**The recurring theme.** Four hazards — `HZ-4.8-1` (divergence),
`HZ-4.9-1` (cross-trial substrate reuse), `HZ-4.10-2` (holdout consumption),
`HZ-4.11-1` (staleness flags) — are the same gap seen four times: *the platform
records high-consequence governance events faithfully and nothing surfaces
them.* Individually each is Medium-to-High. Together they are a **structural
observability gap** in the governance layer, and should be scoped as one piece
of work rather than four patches. Recommended as the first candidate for
whatever follows Phase 4 — flagged, not scheduled.

## 8. What Phase 4 closure requires — owner decision

Three items need Rohith, not more engineering:

| # | Decision | Consequence of deferring |
|---|---|---|
| **1** | ~~`P4-1` — authorise the store and first seal~~ → ✅ **done 2026-08-08**. Now: **deploy Phase 4 to production**, reconciling `HEAD` and the 26 ↔ 27 chain divergence | A production deployment, out of scope for the closure session. Until then the canonical chain and production's differ by the one real `dataset.seal` receipt |
| **2** | **Accept or amend `P4-9`'s "all four ledgers"** | The criterion as worded is unsatisfiable today; it needs re-wording or an explicit carve-out |
| **3** | **Sign the twelve WBS verification records** (RC checklists, 4.0–4.11) | Same discipline as Phase 3's `E2`; unsigned records leave the phase formally unclosed |

Outstanding operational decisions carried from earlier sessions remain open and
unrelated to Phase 4: the **80,860 stale `option_quotes` rows**, **backup
capacity** (14 backups ≈ 12 GB on a 29 GB disk), the **supervisor health defect**
(`ws_manager.health()` ignores `last_msg` age), **BK2** (restore drill never
performed) and **BK3** (off-host replication NOT ESTABLISHED).

## 9. Hidden scope — statement

Per the closure instruction, I checked for Phase 4 scope not visible in the WBS
table. **Three items were found and are recorded in §4's Unowned bucket** —
`P4-1` real-data instantiation, §10.3 propagation, and governance alerting.

Beyond those: no WBS row was partially implemented, no deliverable was silently
narrowed, no test was disabled to pass, and no `ADR-I012` clause is unaccounted
for in §4's matrix. The builder reads no operational data, deploys nothing, and
has never written to a production path.

**Phase 5 has not been started.**
