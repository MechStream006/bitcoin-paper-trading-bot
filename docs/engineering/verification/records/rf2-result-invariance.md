# RF2 — Result-Invariance Transcript

| Field | Value |
|---|---|
| **Declared by** | [`phase_4_dataset_builder_roadmap.md`](../../phase_4_dataset_builder_roadmap.md) §13.3 — *"RF2 result-invariance transcript"*, WBS 4.6 |
| **Governing decision** | `DS-DEC-004` + amendment (Option B), ratified 2026-08-07 |
| **Milestone record** | [`wbs-4.6-rf2-sealed-region-relocation.md`](wbs-4.6-rf2-sealed-region-relocation.md) |
| **Baseline artifact** | [`BASELINE_46.json`](../../../../BASELINE_46.json) — committed at `9828d6e`, retained as evidence |
| **Created** | 2026-08-08 — closing audit finding `AF-3` (declared deliverable absent) |
| **Verified at** | `82ce7c0` (engineering freeze) |

---

## 1. Why this record exists separately

The roadmap names result invariance as *"the only Phase 4 item that can silently
invalidate existing research"* and requires a standalone transcript. The
evidence has existed since WBS 4.6 — inside that milestone's record and in
`BASELINE_46.json` — but the **declared artifact at this path was never
created**. This record supplies it. It introduces no new claim; it consolidates
and re-verifies the existing one.

## 2. The obligation

> **`P4-4`** — *Sealed-region exclusion is applied at resolution; the three
> analysis scripts no longer each own the boundary, and **every published result
> is bit-identical**.*

And the amendment's acceptance criteria:

> 1. Result invariance remains the primary acceptance criterion.
> 2. Existing published research outputs remain bit-identical.
> 3. Production forward-evidence behaviour remains unchanged.
> 4. Existing `fn_hash` remains identical.
> 5. No governance records are rewritten.

## 3. The baseline

`BASELINE_46.json` pins the SHA-256 of **10 artifacts** — every file that could
carry, consume, or evidence the sealed-region boundary:

| # | Artifact |
|---|---|
| 1 | `research/evidence/run_lockbox_validation.py` |
| 2 | `research/forward_evidence/decision_function.py` |
| 3 | `research/forward_evidence/decision_function_g1_market_regime.py` |
| 4 | `research/forward_evidence/decision_function_g3_atr_expansion.py` |
| 5 | `research/forward_evidence/decision_function_g3_tsmom.py` |
| 6 | `research/forward_evidence/decision_function_h5.py` |
| 7 | `research/forward_evidence/decision_function_ivrv.py` |
| 8 | `research/forward_evidence/m0_pre_register.py` |
| 9 | `research/forward_evidence/pre_registration.json` |
| 10 | `research/reports/evidence_library/lockbox_validation.json` |

Items **2** and **8** are the `DS-DEC-004` amendment's **frozen exceptions** —
`decision_function.py` and `m0_pre_register.py` were deliberately *not*
modified, and are pinned here precisely so that decision stays verifiable.

## 4. Verification

Re-verified at the freeze commit, independently of the WBS 4.6 run:

```
result invariance: artifacts=10  changed=0
```

Every artifact resolves and every SHA-256 matches the baseline.

### Continuous re-verification

This is not a one-time check. The same comparison has been re-run at **every**
milestone from 4.6 onward — 4.7, 4.8, 4.9, 4.10, 4.11, operational closure, the
`feature_store` backup fix, AF-1, and EB-1/EB-3 — and has reported **10
artifacts, 0 changed** on every occasion. Each result is recorded in that
milestone's own record under *"Invariants re-verified"*.

## 5. What this does and does not prove

**Proves:** the ten pinned files are byte-identical to their pre-relocation
state; the two frozen exceptions were not touched; the registered `fn_hash` and
`pre_registration.json` are unchanged, so the pre-registration lock enforced at
`daily_job.py:508` still matches.

**Does not prove:** that RF2 would catch a *future* declaration intersecting the
lockbox by a path outside the builder. `HZ-4.6-1` records that limitation — RF2
guards datasets declared through the Dataset Builder; a script reading the
corpus directly bypasses it. Result invariance and boundary enforcement are
different properties, and only the first is evidenced here.

**Not consumed:** the sealed lockbox (`2025-06-01 … 2026-05-31`) was not opened
during relocation or during any verification run. Criterion 4 of the amendment
holds.
