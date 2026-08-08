# Phase 4 — Governance Closure Report

| Field | Value |
|---|---|
| **Phase** | Phase 4 — Research Dataset Builder (WBS 4.0 – 4.11) + operational closure |
| **Governing ADR** | [`ADR-I012`](../../../architecture/adr/ADR-I012-research-dataset-builder.md) v0.1.0 (Draft) |
| **Roadmap** | [`phase_4_dataset_builder_roadmap.md`](../../phase_4_dataset_builder_roadmap.md) v1.0.2 |
| **Inputs** | [Engineering Audit](phase-4-engineering-audit.md) · [Closure Review](phase-4-closure-review.md) · 14 WBS verification records |
| **Date** | 2026-08-08 |
| **Author** | Claude Opus 5 |
| **Approver** | *(pending — Rohith)* |
| **Verdict** | **Engineering COMPLETE · Operationally ACTIVATED (canonical host only) · Governance INCOMPLETE · NOT ready for Phase 5** — §14 |

---

## 0. Two corrections to previously reported findings

Both change conclusions stated in the Closure Review and Engineering Audit, so
they are stated first rather than buried.

**C-1 — `P4-9` was assessed against the wrong files.** The Closure Review §3 and
Engineering Audit §9 report *"only one ledger carries governance receipts."*
That is **wrong**. It was derived from `research/data/ledger.db` (a different
database that has no receipt table) and a Label Store **backup** file, not the
live stores. Re-verified against the live stores, **three chains verify clean**
(§10). `P4-9`'s real status is in §12.

**C-2 — the production divergence is not a one-receipt gap, and deploying code
would not close it.** Both prior documents frame it as *"26 ↔ 27, reconcile by
deployment."* Production's `feature_store.db` and `label_store.db` contain
**zero tables** — they are empty placeholder files. The entire research and
governance layer above the evidence ledger exists on **one machine**. §13
replaces the earlier framing.

## 1. WBS 4.0 – 4.11 completion matrix

| WBS | Deliverable | Tests | Verification record | Engineering | Reviewed |
|---|---|---|---|---|---|
| 4.0 D0a | dvol corpus conformance pilot | — | `wbs-4.0-d0a-…` | ✅ | ❌ |
| 4.0 D0b | Full corpus conformance (274 partitions) | — | `wbs-4.0-d0b-…` | ✅ | ❌ |
| 4.0.1 | Builder manifest-upgrade repair (F1, F2) | — | `wbs-4.0.1-…` | ✅ | ❌ |
| 4.1 | Dataset Registry | 128 | `wbs-4.1-…` | ✅ | ❌ |
| 4.2 | Bidirectional Citation Index | 80 | `wbs-4.2-…` | ✅ | ❌ |
| 4.3 | Declaration + Resolution; RF1/RF5/RF6 | 74 | `wbs-4.3-…` | ✅ | ❌ |
| 4.4 | Composition + Tiers A–D; RF4 | 87 | `wbs-4.4-…` | ✅ | ❌ |
| 4.5 | Seal + `dataset.seal` receipts | 69 | `wbs-4.5-…` | ✅ | ❌ |
| 4.6 | RF2 — sealed-region relocation | 45 | `wbs-4.6-…` | ✅ | ❌ |
| 4.7 | RF3 — lane enforcement | 62 | `wbs-4.7-…` | ✅ | ❌ |
| 4.8 | Materialiser; L1 / L2 | 69 | `wbs-4.8-…` | ✅ | ❌ |
| 4.9 | Experiment Dataset + use-counting; DG-8 | 72 | `wbs-4.9-…` | ✅ | ❌ |
| 4.10 | Training Dataset structure; DG-7 | 65 | `wbs-4.10-…` | ✅ | ❌ |
| 4.11 | Retirement, supersession, `--as-of` (L3) | 91 | `wbs-4.11-…` | ✅ | ❌ |

**842 dedicated tests.** Suites: `btc_bot` 3233 / 19 skipped, `base` 2784 / 62
skipped, zero failures. **Every milestone has a written verification record with
evidence. None has been reviewed** — see §11.

## 2. ADR-I012 compliance matrix

| Clause | Status | Evidence |
|---|---|---|
| §6 Registry · §6.1 Citation Index | ✅ | 4.1, 4.2; live store shows 6 citations, both directions, 0 orphans |
| §7.1 Six Distinctions · §7.2 Declaration content | ✅ | all §7.2 fields present in the real sealed declaration |
| §8 five-stage pipeline | ✅ | all five stages executed on real data |
| §8.1 RF1 · RF5 · RF6 | ✅ | **RF6 refused a real declaration** (§9) |
| §8.1 RF2 (sealed region) | ✅ | lockbox declared; result invariance 10/10 |
| §8.1 RF3 (lane) | ⚠️ | implemented; **inert** until declarations carry a lane code (`TD-4.7-1`) |
| §8.1 RF4 (admissibility floor) | ✅ | refuses every Training declaration today, by design (`A-3`) |
| §8.2 Materialisation off-lifecycle | ⚠️ | built and tested; **never run on real data** (`TD-4.12-1`) |
| §9 / §9.1 Metadata + meet | ✅ | meet composed from real corpus verdicts |
| §10.1 Redefinition = new lineage | ✅ | `redefinition` refused by the registry |
| §10.2 Supersession never invalidates | ✅ | 4.11, verified after the fact |
| §10.3 Superseded-input propagation | ❌ | **Unowned** — flag-only; nothing detects or alerts |
| §11 Seven-phase lifecycle | ✅ | live store holds `Sealed` and `Rejected` instances |
| §12 Tiers A–D | ✅ | Tier C satisfiable since 4.0 conformed 274 partitions |
| §13.1 / §13.2 DG-1…DG-8 | ✅ | all eight enforced; DG-1 verified on the real seal (`actor_type=human`) |
| §14 Provenance | ✅ | provenance closure persisted on the real seal |
| §15 / §15.1 / §15.2 Research vs Training | ✅ | structure only; no real instance possible (`A-3`) |
| §16 L1 / L2 | ⚠️ | demonstrated on fixtures only |
| §16 L3 | ✅ | demonstrated on the real dataset (4-transition history) |

**Two ADR clauses are not satisfied**: §10.3 (unowned) and §8.2/§16 L1–L2 on
real data (`TD-4.12-1`).

## 3. Roadmap compliance matrix — exit criteria

| # | Criterion | Verdict |
|---|---|---|
| P4-1 | Seal against real Phase 1–3 data, meet stated | ✅ **MET** 2026-08-08 |
| P4-2 | All six refusal points refuse by name, negative tests | ✅ MET |
| P4-3 | Citation index both directions; upstream refusals enforceable | ✅ MET as worded (opt-in — `TD-4.2-1`) |
| P4-4 | Sealed-region exclusion at resolution; results bit-identical | ✅ MET — 10/10 |
| P4-5 | Experiment Dataset trial context + use-count | ✅ MET |
| P4-6 | Training structure; RF4 refuses today | ✅ MET |
| P4-7 | L1/L2/L3 demonstrated separately | ⚠️ **PARTIAL** — L3 on real data; L1/L2 on fixtures |
| P4-8 | Builder reads no operational data | ✅ MET |
| P4-9 | `--check` clean; chains clean; suites zero new failures | ⚠️ **AMENDMENT REQUIRED** — §12 |
| P4-10 | Closure Review with Unowned bucket | ✅ MET |

**8 met, 2 partial, 0 failed.**

## 4. Governance decision matrix

| Decision | Subject | State |
|---|---|---|
| `DS-DEC-001` | Corpus conformance method | ✅ Ratified 2026-08-06 |
| `DS-DEC-002` | Local re-derivation | ✅ Ratified 2026-08-06 |
| `DS-DEC-005` | Store location `dataset_builder.db` | ✅ Ratified 2026-08-06 · **now realised** |
| `G-CORPUS` | Corpus gate, conditions `C1`–`C6` | ✅ Opened; all six verified, none waived |
| `DS-DEC-004` + amendment | RF2 via Dataset Builder (Option B) | ✅ Ratified; frozen exceptions honoured |
| `LS-DEC-003/004/005/007/008/009` | Phase 3 closure | ✅ Ratified 2026-08-07 (`E2` discharged) |
| `DS-DEC-003` | — | Do-not-invent stands; not scheduled |
| `DS-DEC-006` | Retro-binding 70 historical runs | **OWED**, untouched |
| `T-2` | Scope transfer | **Unratified** |
| `T-4` | Training scope → structure only | **Unratified**; non-gating reasoning recorded at 4.10 §1 |
| `T-5` | `ADR-I011` LG7/DI8 → 4.9 | **Unratified**; discharged in substance |
| `T-6` | → 4.2 | **Unratified**; discharged in substance |
| `TD-4.5-1` ruling | Seal receipt targets the platform chain | ✅ Settled — "the ledger must not fork" |

**No new governance decision was required to reach operational closure.**
Four transfers (`T-2`, `T-4`, `T-5`, `T-6`) and `DS-DEC-006` remain open.

## 5. Technical debt register — 41 items

| Milestone | Items | High-severity |
|---|---|---|
| 4.2 | `TD-4.2-1..3` | `TD-4.2-1` — `citation_check` is opt-in upstream |
| 4.3 | `TD-4.3-1..4` | — |
| 4.4 | `TD-4.4-1..4` | `TD-4.4-3` — `InputAdmissibility` caller-supplied |
| 4.5 | `TD-4.5-1..4` | `TD-4.5-1` — cross-database seal/receipt not atomic |
| 4.6 | `TD-4.6-1..3` | — |
| 4.7 | `TD-4.7-1..3` | `TD-4.7-1` — RF3 inert without a lane code |
| 4.8 | `TD-4.8-1..4` | — |
| 4.9 | `TD-4.9-1..4` | `TD-4.9-2` — `trial_registration` free text |
| 4.10 | `TD-4.10-1..4` | `TD-4.10-1` — `computed_meet` optional (blocked by `TD-4.4-3`) |
| 4.11 | `TD-4.11-1..5` | `TD-4.11-3` — L3 is forward-only |
| closure | `TD-4.12-1..3` | `TD-4.12-1` — sealed dataset never materialised |

**Seven High-severity items.** One dependency: `TD-4.4-3` must precede
`TD-4.10-1`.

## 6. Hazard register — 19 items

`HZ-4.4-1` resolved at 4.5. `HZ-4.5-2` resolved at 4.11. Seventeen remain.

**The dominant hazard is a single recurring pattern, not seventeen separate
problems.** `HZ-4.8-1`, `HZ-4.9-1`, `HZ-4.10-2`, `HZ-4.11-1` all state: *the
platform records a high-consequence governance event faithfully and nothing
surfaces it.* This was confirmed in practice — the first `dataset.seal` receipt
was written with no alert, no report entry and no operator-visible signal.

**Recommendation:** scope governance observability as one work item. Patching
four hazards separately will produce four inconsistent notification paths.

## 7. Deferred work register

| Item | Origin | State |
|---|---|---|
| §10.3 superseded-input propagation | ADR-I012 | **Unowned** — no WBS row, no decision |
| Governance alerting | four hazards | **Unowned** |
| Materialising a real dataset | `TD-4.12-1` | **Unowned** — needs a corpus-reading `ValueProvider` |
| `DS-DEC-006` retro-binding | 4.9 | Owed |
| `TD-4.4-3` → `TD-4.10-1` | 4.4, 4.10 | Open, ordered |
| `TD-4.2-1` mandatory citation check | 4.2 | Open |
| `TD-4.7-1` lane code in declarations | 4.7 | Open |
| BK2 restore drill · BK3 off-host | Program P1 | **NOT ESTABLISHED** — now materially worse (§13) |

## 8. Database inventory

| Database | Location | Tables | Purpose | Backed up | On production |
|---|---|---|---|---|---|
| `btc_trading.db` | `data/` | — | Live collection | ✅ daily | ✅ live |
| `evidence_forward.db` | `research/data/` | — | Forward evidence | ✅ daily | ✅ live |
| `ledger.db` | `research/evidence_library/` | 6 | Governance chain | ✅ daily | ✅ 26 receipts |
| `feature_store.db` | `research/feature_store/` | 3 | 17 Active Feature Specs | ⚠️ not in `--all` | ❌ **empty file, 0 tables** |
| `label_store.db` | `research/label_store/` | 5 | 4 Active Label Specs | ✅ `--label-store` | ❌ **empty file, 0 tables** |
| `dataset_builder.db` | `research/dataset_builder/` | 8 | Dataset Registry | ✅ `--dataset-builder` | ❌ absent |
| `forward_evidence.db` | `research/data/` | — | `corpus_daily` | — | ✅ |

All research stores are **gitignored**: they are data, not source, and are
never shipped by a deployment.

**Finding:** `feature_store.db` has no dedicated backup flag in `backup.py`,
unlike the Label Store and Dataset Builder. It holds 17 Active Feature
Specifications and 17 `feature.register` receipts. **This is a gap, logged as
`TD-4.12-4`.**

## 9. Dataset Builder inventory

| Property | Value |
|---|---|
| Store | `research/dataset_builder/dataset_builder.db`, 8 tables, `integrity_check: ok` |
| Specifications | **2** — one `Sealed`, one `Rejected` |
| Sealed dataset | `f444c132b2a5d666…`, lineage `ivrv-vrp-substrate` v2 |
| Rejected dataset | `2c9bf77fbcf0cf1c…` v1 — **RF6**, warm-up predates DVOL inception |
| Window | 2021-05-01 … 2025-05-31; lockbox declared, not intersected |
| Ceiling | `OFFLINE_CORPUS / AMBER / READY / FULL / CLEAN` |
| Citations | 6 — 2 evidence, 3 feature, 1 label; bidirectional; 0 orphans |
| History (L3) | 4 transitions, complete, `--as-of` verified |
| Materialisations / pins | **0 / 0** (`TD-4.12-1`) |
| Experiments / training partitions | 0 / 0 — expected (`R9`, `A-3`) |
| Append-only guards | `DATASET_TAMPER`, `HISTORY_TAMPER`, `DG-3` all verified live |

**The `Rejected` record is the most valuable single artifact in the store.** It
is proof that the refusal points act on real data rather than only on fixtures.

## 10. Receipt inventory

| Ledger | Receipts | Types | Chain |
|---|---|---|---|
| `evidence_library/ledger.db` | **27** | `family.transition` 13 · `evidence.admit` 9 · `evidence.reject` 4 · **`dataset.seal` 1** | ✅ `ok=True, n=27` |
| `feature_store.db` | **17** | `feature.register` 17 | ✅ `ok=True, n=17` |
| `label_store.db` | **8** | `label.register` 8 | ✅ `ok=True, n=8` |
| `dataset_builder.db` | — | none — **by design** (`TD-4.5-1`) | n/a |

**52 receipts across three chains; all three verify clean.** All 27 evidence
ledger receipts have `attestation_basis IS NULL` — no receipt has ever been
backfilled.

Reserved but never emitted: `dataset.promote`, `dataset.retire`,
`contract.activate`, `contract.retire`, `readiness.verdict`.

## 11. Reviewer checklist review (14 records, 146 items)

Every WBS record carries an RC checklist. **All 146 items are unchecked and
every sign-off line is blank.**

| Record | RC items | Checked |
|---|---|---|
| 4.0 D0a / D0b / 4.0.1 | 8 / 7 / 8 | 0 / 0 / 0 |
| 4.1 … 4.11 | 9, 10, 11, 11, 12, 11, 11, 11, 12, 11, 14 | 0 across all |
| **Total** | **146** | **0** |

**Findings:**

1. **No reviewer item is *unintentionally* open.** Every record marks its
   reviewer as *"pending — Rohith"* in its header. The open state is the
   designed state, not an oversight.
2. **Every milestone has evidence.** Each record contains test counts, TV3
   sabotage results, both full-suite figures, invariants re-verified, and a
   production check. No record asserts a result without a number behind it.
3. **Format inconsistency — the three 4.0 records have no signature block.**
   4.1–4.11 each end with `**Reviewer:** ______ **Date:** ______`; the 4.0
   family ends with a Summary and no place to sign. A reviewer cannot record
   sign-off on them without editing structure. **Logged as `TD-4.12-5`.**
4. **Twelve vs fourteen.** The task, the Closure Review and the Engineering
   Audit all say *"twelve WBS records."* There are **fourteen** — WBS 4.0
   produced three (D0a, D0b, 4.0.1). Any sign-off instruction naming twelve
   would leave two unsigned.

**Twenty-two RC items are decision-bearing** — they ask the reviewer to accept a
specific engineering judgement rather than confirm a fact (e.g. 4.11's RC7 on
the registry not being append-only, RC9 on DG-5 scope; 4.10's RC9 on the
provenance-only floor; 4.9's RC8 on the declared denominator). These are the
items that carry governance weight; the remainder are verifiable by inspection.

## 12. `P4-9` review

> **P4-9:** *"`report.py --check` clean; `verify_chain()` clean on all four
> ledgers; both full suites at zero new failures."*

| Component | Status |
|---|---|
| `report.py --check` clean | ✅ 35 families (9 candidate / 22 discovery / 4 disqualified) |
| Both suites, zero new failures | ✅ 3233 / 19 and 2784 / 62 |
| `verify_chain()` on "all four ledgers" | ⚠️ **three exist and all verify clean; the fourth does not exist** |

**Determination: the wording is stale. The implementation is not incomplete, and
the requirement is not unsatisfiable.**

Reasoning: the roadmap was written (2026-08-06) anticipating that the Dataset
Builder would carry its own receipt ledger — a fourth chain alongside evidence,
feature and label. **`TD-4.5-1` subsequently decided the opposite**, on stated
architectural grounds: *"the ledger is the platform-wide chain and must not
fork."* `dataset.seal` receipts therefore go to `evidence_library/ledger.db`,
which is why that chain moved 26 → 27.

So the criterion counts a ledger the architecture deliberately declined to
create. Every chain that exists verifies clean, and the receipts the fourth
would have held are present and verified in the first.

**This is a governance decision, not an editorial fix, because it changes a
ratified exit criterion. It is not applied here.**

### Recommended decision — `DS-DEC-007`

> **`DS-DEC-007` — Amend `P4-9` to name the chains that exist.**
>
> Replace: *"`verify_chain()` clean on all four ledgers"*
> With: *"`verify_chain()` clean on every governance chain in the platform —
> currently `evidence_library/ledger.db`, `feature_store.db` and
> `label_store.db`. The Dataset Builder deliberately has no separate chain per
> `TD-4.5-1`; its `dataset.seal` receipts are verified within the evidence
> ledger."*
>
> **Consequence of ratifying:** `P4-9` becomes MET; the criterion tracks the
> architecture instead of a superseded assumption, and stays correct if a
> fourth chain is added later.
> **Consequence of rejecting:** `P4-9` remains permanently unmeetable unless a
> Dataset Builder ledger is created — which would fork the platform chain and
> contradict `TD-4.5-1`.
> **Recommendation: RATIFY.**

## 13. Production / canonical divergence review

**The earlier framing of this divergence was wrong** (correction C-2). It is not
a one-receipt gap closable by deploying code.

### 13.1 What actually differs

| | Canonical (dev host) | Production (EC2) |
|---|---|---|
| `HEAD` | `7c13859` | `59e4136` — **9 commits behind** |
| `evidence_library/ledger.db` | 27 receipts | 26 receipts |
| `feature_store.db` | 3 tables, 17 specs, 17 receipts | **0 tables — empty file** |
| `label_store.db` | 5 tables, 4 Active specs, 8 receipts | **0 tables — empty file** |
| `dataset_builder.db` | 8 tables, 2 specs, 1 sealed | **absent** |
| Corpus `research/data/raw` | 274 conformed partitions | present |
| Health | — | `INFO`, 0 non-OK, 0 failed units, `quotes` flowing |

### 13.2 Why it exists

Two independent reasons, and only one is about deployment:

1. **Code lag** — production runs pre-4.11 code because no Phase 4 deployment
   has been authorised. Ordinary and expected.
2. **The research layer has never lived on production.** Every research store is
   **gitignored**. A deployment ships code, not data, so *no deployment has ever
   populated them and none ever will.* Production's copies are empty files
   created by directory scaffolding, not truncated databases.

### 13.3 Is it expected?

**The code lag: yes.** **The data asymmetry: yes by design, but its consequence
was never explicitly accepted.**

The consequence is that **the entire Phase 4 governance output exists on exactly
one machine** — 17 Feature Specifications, 4 Active Label Specifications, 274
conformed corpus partitions, the sealed Research Dataset, and 25 of the
platform's 52 governance receipts. Production replicates none of it.

Program P1 already recorded that **BK3 off-host replication is NOT ESTABLISHED**
and **BK2 (restore drill) has never been performed**. Phase 4 has substantially
increased what those gaps put at risk: previously the irreplaceable asset was
one ledger; now it is a governance layer of three chains and a corpus.

**This is the most consequential finding in this report.** It is a durability
finding, not a deployment finding.

### 13.4 The deployment that would reconcile the code lag

**Not performed.** Specified for authorisation only.

- **Scope:** `git fetch && git reset --hard origin/main` on `/opt/btcbot` as
  `btcbot` (9 commits: 4.2 → 4.11 → operational closure), then restart
  `btcbot.service` and `btcbot-api.service`.
- **What it changes on production:** Python source only. It adds the
  `dataset_builder` package and the `--dataset-builder` backup/healthcheck
  wiring. **It creates no database, seals no dataset and writes no receipt.**
  Production's ledger stays at 26.
- **Risks:** *Low.* No migration; no schema change; the Dataset Builder has no
  runtime entry point and no timer, so newly deployed code does not execute.
  The healthcheck gains a `dataset_builder_backup` check that will report
  INFO/absent — a **new non-OK-adjacent line** an operator should expect.
  Residual risk is the ordinary one of restarting the collector.
- **Rollback:** `git reset --hard 59e4136` and restart. No data rollback is
  possible or needed, because no data is written.
- **Verification after:** health `overall INFO` / 0 non-OK · both services
  active · 0 failed units · `quotes=N/N` within 3 min · three DB
  `integrity_check` ok · `report.py --check` clean · `verify_chain` still
  `ok=True, 26` · backup dry-run exit 0.
- **What it does *not* fix:** the data asymmetry in §13.1. That needs a
  decision about where canonical research state lives, not a deploy.

## 14. Phase 4 readiness verdict

| Question | Verdict |
|---|---|
| **Engineering complete?** | ✅ **YES.** 14 records, 842 tests, both suites green, all six refusal points live, all eight DGs enforced. Two ADR clauses unsatisfied (§10.3 unowned; L1/L2 not run on real data) — both are *unowned scope*, not incomplete milestones |
| **Operationally activated?** | ⚠️ **PARTIALLY.** Fully activated on the canonical host: real store, real sealed dataset, real receipt, verified backup, healthcheck coverage. **Not activated on production** — code 9 commits behind, store absent |
| **Governance complete?** | ❌ **NO.** 146/146 reviewer items unsigned; `P4-9` needs `DS-DEC-007`; four scope transfers and `DS-DEC-006` unratified |
| **Ready for Phase 5?** | ❌ **NO** — blocked on governance sign-off (§15), not on engineering |

**Summary:** Phase 4 built what it promised and proved it on real data. What
remains is entirely governance and durability: nothing is unbuilt, several
things are unsigned, and the output is single-homed.

## 15. Remaining owner decisions

| # | Decision | Blocking Phase 5? |
|---|---|---|
| **1** | **Sign the 14 WBS reviewer checklists** (146 items; 22 decision-bearing) | ✅ **Yes** |
| **2** | **`DS-DEC-007`** — amend `P4-9` (§12); recommendation: RATIFY | ✅ **Yes** |
| **3** | **Accept the single-homed governance layer, or fund BK3** (§13.3) | ⚠️ Should be |
| **4** | Authorise the Phase 4 production deployment (§13.4) | ❌ No |
| **5** | Ratify or close `T-2`, `T-4`, `T-5`, `T-6` | ❌ No — none gated any milestone |
| **6** | `DS-DEC-006` — retro-binding the 70 historical runs | ❌ No |
| **7** | Whether to materialise the first dataset (`TD-4.12-1`) | ❌ No — but it leaves §16 L1/L2 unproven on real data |
| **8** | Scope governance observability as one work item (§6) | ❌ No |

Carried and unrelated: 80,860 stale `option_quotes` rows · backup capacity
(~12 GB on a 29 GB disk) · `ws_manager.health()` defect · BK2 drill.

## 16. Recommended Phase 5 entry checklist

**Not an authorisation to begin Phase 5.** Offered because §14 finds every
*engineering* obligation complete; the entry gates below are governance and
durability gates.

| # | Gate | Rationale | Owner |
|---|---|---|---|
| **E1** | All 14 WBS records signed; 146 RC items resolved | Phase 3 required the identical discipline (`E2`) | Rohith |
| **E2** | `DS-DEC-007` decided | Phase 4 cannot be declared closed against an unmeetable criterion | Rohith |
| **E3** | Sign-off instruction names **fourteen** records, not twelve | Two would otherwise be silently skipped (§11.4) | Rohith |
| **E4** | 4.0 records given a signature block (`TD-4.12-5`) | They currently offer nowhere to sign | Engineering |
| **E5** | Durability decision on the single-homed governance layer (§13.3) | Phase 5 will add to a layer that exists in one place | Rohith |
| **E6** | `feature_store.db` added to `backup.py` (`TD-4.12-4`) | The only governed store with no backup flag | Engineering |
| **E7** | Phase 4 deployed to production, or the lag explicitly accepted | Avoids compounding a 9-commit gap | Rohith |
| **E8** | Phase 5 scope names an owner for §10.3 and governance alerting | Both are Unowned and both would otherwise silently persist | Rohith |
| **E9** | Decide `TD-4.12-1` (materialisation) | Determines whether Phase 5 may rely on L1/L2 for real datasets | Rohith |
| **E10** | Full-suite baseline re-established at Phase 5 entry | Same discipline as `E9` at Phase 4 entry | Engineering |

**Recommended minimum to open Phase 5: `E1`, `E2`, `E3`, `E5`, `E8`.** The
remainder can run in parallel with early Phase 5 work.

---

**Phase 5 has not been started. No production change was made. No code was
written. No dataset was created.**
