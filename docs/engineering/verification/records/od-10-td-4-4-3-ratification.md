# `OD-10` — `TD-4.4-3` Admissibility-Derivation Authority — Ratification

| Field | Value |
|---|---|
| **Subject** | `OD-10A`, `OD-10B`, `OD-10C` — the feature-ceiling authority, the label-data strategy, and the grandfathering treatment for the 2026-08-08 sealed dataset, arising from the WBS 4.12 / `TD-4.4-3` investigation |
| **Prepared by** | Claude Opus 5, across a first-principles investigation (2026-08-09) — see [WBS 4.12 record](wbs-4.12-td-4-4-3-admissibility-derivation.md) |
| **Decided by** | Rohith Suresh |
| **Status** | ✅ **RATIFIED 2026-08-09 — `OD-10A`, `OD-10B`, `OD-10C`, exactly as presented** |
| **Nature** | Governance decision only. **No code was written or modified to implement any of these.** WBS 4.12 implementation has not started. `TD-4.4-3` remains open |

---

## Why this ratification exists

The first attempt to implement `TD-4.4-3` (derive `InputAdmissibility` from resolved references) surfaced two findings that blocked closing it by implementation alone:

1. `propagation.feature_specification_ceiling()`'s `UNKNOWN`-certification output is not an oversight — it is explicitly, deliberately tested as correct by two passing WBS 3.x tests (`test_inherits_uncertified_cap_from_input_source`, `test_multi_source_specification_still_meets_correctly`). Any fix touching `propagation.py` reopens an already-ratified decision.
2. The real Label Store has no governed per-date value data for the 2026-08-08 sealed dataset's declared window — a real, separately-tracked gap (`LS-TD-017`, Label Store TD register, Low severity, "mechanism-only status is correct and honest").

Full investigation, evidence, and the rejected/superseded draft options are in the [WBS 4.12 record](wbs-4.12-td-4-4-3-admissibility-derivation.md). This record is the ratification itself.

---

## `OD-10A` — RATIFIED

> `OD-10A` — The Dataset Builder derives feature-input admissibility ceilings via a new, explicitly and distinctly named, Dataset-Builder-owned function (in `research/dataset_builder/admissibility_derivation.py`), reading real partition manifests through `quality.admissibility.corpus_ceiling()`, composed with the feature's own top-rung, Maturity-not-applicable assessment. `research/label_store/propagation.py` — including `source_ceiling()` and `feature_specification_ceiling()` — is **not modified** and is **not called** by this derivation. Both existing functions continue to serve their existing Label Store consumers exactly as today. The two paths answer deliberately different questions and neither supersedes the other.

**Authorizes:** a new function in `admissibility_derivation.py` composing real per-source manifest data into a feature ceiling; rewriting `admissibility_derivation.derive_feature_ceiling()` so it no longer calls `propagation.feature_specification_ceiling()`.
**Forbids:** any modification to `propagation.py`; any change to `feature_specification_ceiling()`'s or `source_ceiling()`'s observed behavior for any existing caller; reusing `propagation.py`'s identifiers for the new derivation.

## `OD-10B` — RATIFIED

> `OD-10B` — Future Dataset Builder label citations are restricted to windows where governed per-value label data exists. A window with none is refused, with the rejection persisted via `registry.reject()` **before** the refusal propagates, mirroring RF1/RF5's existing pattern exactly — never a bare, unpersisted exception. No proxy of any kind — evidence-ceiling or otherwise — is permitted as a substitute for missing label data. `LS-TD-017` (the Label Store's empty research-value tier) and bulk historical label materialization are **not authorized, scheduled, or discharged** by this decision and remain entirely separate Label Store work. Whether the missing-data refusal is implemented as a new refusal point or folded into an existing Tier C/D finding is an implementation-design decision, to be resolved against `ADR-I012` source text directly before coding — not decided by this ratification.

**Authorizes:** restricting future Dataset Builder label citations to windows with real, governed per-value data; wiring `derive_label_ceiling()`'s refusal into a persisted `registry.reject()` call before it propagates.
**Forbids:** any label-ceiling derivation that substitutes another input's ceiling as a proxy; beginning, scheduling, or scoping bulk historical label materialization; treating `LS-TD-017` as discharged or reprioritized; coding the RF7-vs-Tier-fold mechanism before that question is separately resolved — **not decided by this record**.

## `OD-10C` — RATIFIED

> `OD-10C` — The Research Dataset sealed 2026-08-08 (`spec_hash f444c132b2a5d666…`) remains valid and `Sealed`, permanently. Its admissibility ceiling is interpreted under the authority in force at seal time — the caller-supplied `InputAdmissibility` model, with `TD-4.4-3` already filed as accepted open debt at that time. It is **not required** to reproduce under `OD-10A`/`OD-10B`'s authority, and no future check may treat its non-reproduction as a defect. Every Dataset Builder resolution performed **after** `TD-4.4-3`'s ratified implementation lands must use the new authority. No re-seal, ceiling correction, or chain/receipt mutation of this record shall occur except by a separate, explicit governance decision naming this record specifically.

**Authorizes:** permanent exemption of the 2026-08-08 sealed dataset from any future reproducibility requirement.
**Forbids:** any re-seal, ceiling recomputation, or receipt rewrite of that record short of a new, separately-named governance decision; treating its non-reproduction under the new authority as a defect.

---

## Corrected WBS 4.12 completion criterion (recorded here verbatim, applies going forward)

> Evidence and feature derivation must be proven **deterministic and independently reproducible from the governed inputs** — re-running either derivation against the same real corpus/Feature Store state, independently, yields identical results. **This is not the same test as matching the 2026-08-08 sealed dataset's historical feature ceiling.** Per `OD-10C`, that dataset is permanently grandfathered and explicitly exempt from reproduction under the new authority — a different feature-ceiling value for that historical declaration is expected and correct, not a defect. **No test, check, or acceptance criterion may treat non-equality to the old sealed feature ceiling as a failure.** The label dimension will also legitimately still refuse for that same real declaration (`OD-10B`) — also correct, not a gap.

## Remaining implementation decisions — not resolved by this ratification

1. **RF7 vs. Tier C/D fold** (`OD-10B`) — to be resolved directly against `ADR-I012` §8.1/§12 source text before any code is written. Not decided here.
2. The exact `Refusal`/`Finding` code name and wording for the missing-label-data refusal, once (1) is resolved.
3. The new feature-ceiling function's exact name and signature in `admissibility_derivation.py`.
4. The import-boundary test design proving `research/label_store/` has zero references to the new feature-ceiling function.

## What this ratification does not do

It does not implement any part of `TD-4.4-3`. It does not touch `research/`, `src/`, `scripts/`, any schema, any database, any dataset, or production. It does not create a governance receipt — `OD-10` is a roadmap/decision-record-level act, not an entry in `receipts.DECISION_TAXONOMY`, and none was added to it. `TD-4.4-3` remains **open** until the WBS 4.12 completion criteria (above, and in full in the WBS 4.12 record) are independently verified.
