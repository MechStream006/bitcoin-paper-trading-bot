# Phase 4 — Closeout

| Field | Value |
|---|---|
| **Declared by** | [`phase_4_dataset_builder_roadmap.md`](../../phase_4_dataset_builder_roadmap.md) §13.3 — *"Phase 4 Closure Review + Closeout"* |
| **Companion** | [Closure Review](phase-4-closure-review.md) · [Engineering Audit](phase-4-engineering-audit.md) · [Governance Closure Report](phase-4-governance-closure-report.md) · [Governance Completion Package](phase-4-governance-completion-package.md) |
| **Engineering freeze** | `82ce7c0e382ed95ee0e15c8bfc0a77a6f831e62c` · tree `7e7e2cc4cad3c61bd825262d6cb65c8a741d95e5` |
| **Created** | 2026-08-08 — closing audit finding `AF-3` (declared deliverable absent) |
| **Status** | 🔶 **CLOSEOUT PREPARED — PHASE 4 NOT CLOSED.** See §4 |

---

## 1. What this document is

The roadmap declares a Closeout alongside the Closure Review. This is it.

**It does not close Phase 4.** Phase 3's closeout was written when its blockers
were discharged; Phase 4's are not. Writing a closeout that *asserted* closure
would make the declared deliverable exist while making it false — the failure
mode `AF-2` was raised for. This document therefore records the closeout
**position**: what is finished, what is not, and precisely what closure waits on.

## 2. Engineering — complete and frozen

| | |
|---|---|
| WBS delivered | **4.0 – 4.11**, twelve rows, fourteen verification records |
| Dataset Builder tests | **875** |
| Full suites | `btc_bot` **3290 passed / 19 skipped** · `base` **2841 / 62** |
| Governance chains | 27 / 17 / 8 — all `verify_chain ok=True` |
| Databases | seven, all `PRAGMA integrity_check: ok` |
| Store schema | `dataset_builder.db` sha256 `e071978a…`, 7 tables, 16 triggers |
| Refusal points | RF1–RF6 implemented, each with negative tests |
| Design guarantees | DG-1 … DG-8 enforced; DG-4 at the storage layer since `dc16a70`/`82ce7c0` |
| Result invariance | 10 artifacts, 0 changed — see [`rf2-result-invariance.md`](rf2-result-invariance.md) |
| Production impact | **none** — no runtime path imports the builder; nothing deployed |

Three engineering defects were found by audit **after** the milestones were
delivered, and all three are closed: `AF-1` (`rejection_reason` /
`superseded_by` mutable), `EB-1` (`change_class` mutable), `EB-3` (guard lost if
`init_db` was interrupted). Each was reproduced before fixing and re-proved
after.

## 3. The Phase 4 output

One governed store, holding two specifications:

```
v1  2c9bf77fbcf0cf1c…  Rejected   — refused by RF6 on real data, permanently recorded
v2  f444c132b2a5d666…  Sealed     — the platform's first Research Dataset
      window 2021-05-01 … 2025-05-31 · lockbox excluded, not intersected
      ceiling OFFLINE_CORPUS / AMBER / READY / FULL / CLEAN
      receipt dataset.seal 871a4e54… · chain 26 → 27
      pins dvol rev-62ee257e… · spot rev-a87ba269…
```

The **Rejected** record is the more informative of the two: it is evidence that
the refusal points act on real data, not only on fixtures.

## 4. Why Phase 4 is not closed

Nine blockers, none of them engineering.

### Governance

| # | Blocker | State |
|---|---|---|
| G1 | 146 RC items across 14 records | **0 signed** |
| G2 | `DS-DEC-007` (`P4-9` wording) | [package prepared](../../ds_dec_007_decision_package.md), **not ratified** |
| G3 | `AF-2` — rollback levers / `dataset_policy.yaml` | [deviation record open](af-2-rollback-lever-deviation.md), awaiting an owner option |
| G4 | `DS-DEC-006`, `T-2`, `T-4`, `T-5`, `T-6` | open |

### Operational

| # | Blocker | State |
|---|---|---|
| O1 | BK2 restore drill | never performed in full; `TD-P1-002` (WAL) unresolved |
| O2 | BK3 off-host replication | `not_configured` on every sidecar; no `--encrypt` in the deployed unit |
| O3 | Governance layer replication | **single-homed** — three chains + 548 MB corpus on one machine |
| O4 | `AF-4` — empty stores report a healthy backup | unchanged |
| O5 | Phase 4 deployment | 18 commits behind; **not an entry gate**, the builder is inert on production |

## 5. Declared-deliverable reconciliation

Every §13.1 and §13.3 deliverable, checked at the freeze commit:

| Deliverable | Path | State |
|---|---|---|
| Corpus conformance | `research/ingest/backfill_conformance.py` | ✅ |
| Dataset Registry | `registry.py` | ✅ |
| Registry database | `dataset_builder.db` | ✅ created at operational closure |
| Citation index | `citation_index.py` | ✅ |
| Declaration + Resolution | `resolution.py` | ✅ |
| Validation + composition | `validation.py` | ✅ |
| Sealing + receipts | `sealing.py` | ✅ |
| Sealed-region policy | `sealed_regions.py` | ✅ |
| Materialiser | declared `materialise.py` | ⚠️ built as **`materialisation.py`** — name deviation, recorded here |
| Experiment / Training | declared `experiment.py`, `training.py` | ⚠️ built as **`experiments.py`**, `training.py` |
| Rollout config | `dataset_policy.yaml` | ❌ **absent** — [`AF-2` deviation record](af-2-rollback-lever-deviation.md) |
| *(undeclared, delivered)* | `lanes.py` (4.7), `history.py` (4.11) | ℹ️ §13.1 has no row for either |
| Verification record per milestone | `wbs-4.<n>-*.md` | ✅ 14 records |
| Corpus backfill transcript | `docs/ops/corpus-conformance-backfill-*.md` | ✅ created 2026-08-08 |
| RF2 result-invariance transcript | `rf2-result-invariance.md` | ✅ created 2026-08-08 |
| Dataset Builder TD Register | `dataset_builder_technical_debt_register.md` | ✅ created 2026-08-08 |
| Closure Review | `phase-4-closure-review.md` | ✅ |
| Closeout | this file | ✅ created 2026-08-08 |

**One deliverable remains absent** (`dataset_policy.yaml`), with a deviation
record open against it. Two file-name deviations and two undeclared-but-delivered
modules are recorded above rather than left for a future reader to discover.

## 6. Carried into Phase 5

43 technical-debt items and 19 hazards, indexed in the
[TD Register](../../dataset_builder_technical_debt_register.md). Eight High.
One ordering constraint: `TD-4.4-3` before `TD-4.10-1`.

Two unowned scopes: ADR §10.3 superseded-input propagation, and governance
alerting — the latter being one pattern behind four separate hazards.

## 7. Closure condition

Phase 4 closes when **G1–G4** are discharged. **O1–O4** are operational
readiness for Phase 5 rather than Phase 4 closure conditions, but O2 and O3
grow more consequential with every artifact Phase 5 adds to a governance layer
that exists in one place.

**Nothing in this document marks Phase 4 closed.** That is the owner's act.
