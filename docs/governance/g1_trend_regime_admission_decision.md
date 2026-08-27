# g1_trend_regime_ma_cross — Gate 6A.17 Admission Review

**Date: 2026-08-27 (UTC) · Decision: ADMITTED for forward collection · NOT wired, NOT deployed**

Governance record. No performance statistic was computed at this gate, the sealed
lockbox (2025-06-01..2026-05-31) was never read, and `collection_config.yaml`,
`run_daily.py`, `thresholds.yaml`, the frozen 6A.15 specification and the
validated 6A.16 implementation are all byte-unchanged.

---

## 1. What "admission" is on this platform — established before deciding

This had to be settled first, because the answer is not what the question
implies. Three findings, each verified against the code and the registry rather
than assumed:

**1.1 — There is no "admitted" lifecycle state.** `lifecycle.VALID_STATES` is
`discovery / insufficient_sample / disqualified / candidate / validated /
promotion_ready / production_ready / rejected / retired`. From `candidate` the
only forward arc is `validated`, and reaching it requires forward evidence,
which requires collection. **Collection is therefore what happens *between*
candidate and validated — it is not itself a governed state transition.** This
family has been `candidate` since 2026-06-13. Admission here changes no state
and no `state_since`.

**1.2 — There is no registry field that marks a family as collecting.**
`g2_funding_level` has been collecting since 2026-08-26 with
`preregistration: null`, and its registry entry is schema-identical to this
candidate's. The `preregistration` pointer is set on 4 of the 6 collecting
families and absent on 2 — it is an inconsistently maintained convenience
pointer, not a gate. **Nothing in `registry.yaml` distinguishes a collecting
family from a non-collecting one.**

**1.3 — What actually admits a family to collection is being wired**: an entry
in `collection_config.yaml`, a branch in `run_daily.py`, and an active
pre-registration at `research/forward_evidence/pre_registration_<id>.json`.

**Consequence, and it is the load-bearing point of this record:** this gate's
decision is a *governance judgement recorded in prose*, because the platform
offers no other place to put it. The only authorized registry write is the U-2
metadata correction (§4). The decision's operative effect is that the wiring
gate is unblocked.

## 2. The admission criteria, applied exactly as written

### 2.1 Candidate gate — PASS (re-evaluated, not recomputed)

`lifecycle.check_candidate_gate()` is a pure function over metrics already
recorded in the ledger. Re-running it against the pinned run
`abc6078e-0dce-4c89-b73f-c1aa9467e795` and the current `thresholds.yaml`
produces results **identical to the three `gate_results` rows already stored**:

| Gate | Role | Observed | Threshold | Result |
|---|---|---|---|---|
| `E3_KNIFE_EDGE` | — | 1.00 | ≥ 0.40 | PASS |
| `B_SENSITIVITY` | feature | 1.00 | ≥ 0.60 | PASS |
| `B_OOS_POSITIVE` | feature | 0.8690 | > 0 | PASS |

`check_hard_dq()` → `(False, [])`.

No new statistic was generated: every input was read from `runs.metrics_json`.

### 2.2 The feature path does not test what R1/R3 describe

This is the decisive structural fact, and it is why the gate's own instruction —
*do not reject merely because the historical IS signal is weak if the admission
framework does not require an IS-performance pass at this stage* — applies
literally rather than as a courtesy.

In `thresholds.yaml`, `min_sharpe`, `min_wf_agreement`, `min_loo_consistency`,
`min_breakeven_bps`, **`structural_sign_required`**, `min_active_days`,
`min_calendar_years` and `min_regimes` all live under **`candidate.edge`**.
`candidate.feature` contains only `min_sensitivity`,
`min_wf_sign_consistency`, `oos_positive_proxy` and `max_clone_r`. And
`check_candidate_gate()` applies the edge block only `if "edge" in roles`.

This family's role is `['feature']`. **`structural_sign_required` is therefore
not a criterion that applies to it, and never was** — the family was admitted to
candidacy on 2026-06-13 with `struct_sign_ok = False` recorded, on that basis.

### 2.3 Clone and independence — PASS on both bars

| Measure | Value | Bar | Source | Result |
|---|---|---|---|---|
| Max \|r\| vs the six **currently collecting** families | **0.6030** (`g1_market_regime_sma200`) | < 0.70 (6A.14 determination) | 6A.15-PRE | PASS |
| Max \|r\| overall | **0.6030** | < 0.90 (`candidate.feature.max_clone_r`, `hard_dq.clone_r`) | 6A.15-PRE | PASS |
| VIF vs the collecting set | **1.882** | — | 6A.15-PRE | informational |
| Unexplained variance | **53.1%** | — | 6A.15-PRE | informational |

Measured on the **train slice only** (1,612 rows, 2021-01-01..2025-05-31; 365
lockbox rows excluded), frozen methodology `analyze_mtf_independence.py` Test 1
plus `governance.vif_table`.

**The clone result is a PASS, not a positive case.** It establishes that this
family is not a duplicate. It establishes nothing whatsoever about whether the
signal works, and no part of this admission rests on it as evidence of merit.

### 2.4 Specification and implementation — complete

Frozen at 6A.15; implemented and validated at 6A.16. Decision-function hash
`2e6f95977503fc2aeaf4358253c83cc461ce87ce0bf2cad740b2ea072edeba3e`
(`pre_reg_guard._sha256_lf`), pinned in the draft and re-verified live at this
gate. Equivalence to `sig_trend_regime(m, 20, 100)` was *proven* — byte-equal
positions on synthetic series at four lengths, seven pathological inputs, the
real train slice, and against an independent per-row loop reference. 57 tests;
19 adversarial mutants, 16 killed behaviourally and 19/19 with the hash pin.

### 2.5 `n_trials = 34` — untouched

This family was inside the original Phase B battery and its pinned run already
records `n_trials = 34`. Admission adds no trial and does not reopen the frozen
denominator.

## 3. The four recorded risks — assessed against the criteria, not waved through

None of these is a criterion the feature path tests. All four are carried
forward, unchanged, into the promotion assessment where they *are* tested.

**R1 — `struct_sign_ok = False`, clean Sharpe ≈ −0.13.** Not a feature-path
criterion (§2.2). This is the family's most serious recorded weakness and the
most likely way forward evidence disappoints. It remains in the registry
weaknesses, verbatim.

**R2 — IS DSR and BH-p are both NULL.** Not a candidate-gate criterion at all;
DSR/BH-p are `validated.feature` thresholds, and the Validated gate is evaluated
on **forward** returns. **Direct precedent:** `g2_funding_level` — the most
recent admission, 2026-08-26 — was admitted to collection with `dsr_prob: null`
and `bh_p: null`, and its pre-registration defers both explicitly to
*"computed at the FROZEN n_trials=34 on forward returns."* R2 is not a bar to
collection; it means there is no in-sample statistical prior, which is a fact
about what collection will have to establish.

**R3 — 23 IS trades.** `n_trades` is not a feature-path criterion. It is the
reason §4's observation floor matters.

**R4 — a train-position correlation does not establish forward-return
independence.** Correct, and unresolved by anything at this gate. `max_pairwise_r
= 0.70` is enforced at Validated over **forward returns** (Proposal 1,
implemented 2026-07-19); 0.6030 is a train-slice *position* correlation. This is
the genuine promotion risk and is now recorded in the registry weakness itself
(§4.2) so it cannot be lost.

## 4. Governance items resolved

### 4.1 U-1 — family-specific thresholds

**Finding that changes the question: `thresholds.yaml` provides NO observation
floor for a feature-role family.** `min_active_days` and `min_calendar_years`
sit under `candidate.edge` only. "Governance floors unchanged" would therefore
leave this family with **no floor at all** — assessment could be attempted at
n = 1. Every one of the six collecting families sets its own floor precisely
because the YAML does not supply one.

**Resolution: adopt the platform-precedent floor. Introduce no tuned number.**

| Floor | Value | Basis |
|---|---|---|
| `minimum_trades` | **30** | Platform precedent, **identical** in `g1_market_regime_sma200` and `g2_funding_level` — both feature-role. Not chosen for this family |
| `minimum_both_long_and_short_trades` | **true** | Direction coverage implied by the frozen `long_short` direction; supported by the already-recorded 6A.15-PRE train activity split (804 long / 709 short). A coverage condition, not a threshold |

**Deliberately NOT set — new residual item U-3.**
`minimum_calendar_months_before_assessment` is genuinely family-specific and
genuinely differs between precedents (60 for `g1_market_regime_sma200`, 12 for
`g2_funding_level`). Choosing between them requires a trade-frequency
derivation, and producing one would be introducing a threshold from a derived
quantity — which this gate is instructed not to do. It is not needed until
observations exist.

> **U-3 (open): `minimum_calendar_months_before_assessment` must be fixed at the
> wiring gate, before the first observation is collected.** After that it can
> never be set honestly.

Nothing in `thresholds.yaml` is modified. The governance floors that *do* apply
to a feature family — `validated.feature` `min_dsr_prob 0.50`, `max_bh_p 0.10`,
`max_pairwise_r 0.70`, `requires_non_offline_provenance` — apply unchanged.

### 4.2 U-2 — registry metadata

The stale weakness read:

> `"Likely correlated with g1_market_regime_sma200; VIF check needed"`

The check was performed at 6A.15-PRE. Replaced with the measured result, and
deliberately **not deleted** — the measurement resolves the *question*, not the
*concern*:

> `"Correlated with g1_market_regime_sma200: |r| = 0.6030 on train positions
> (VIF 1.882; 53.1% unexplained). Measured at Gate 6A.15-PRE. Clears
> max_clone_r 0.90 and the 0.70 collecting-family bar, and would become the
> highest pair in the collecting set (existing max 0.4432). Forward-return
> independence is NOT established by this — max_pairwise_r is enforced on
> forward returns at Validated."`

This is the **only** authorized registry write. `state`, `state_since`,
`state_reason`, `role`, `pinned_run` and `preregistration` are all untouched —
per §1.1/§1.2 none of them represents admission, and `preregistration` must
continue to point at nothing while the pre-registration is still a draft.

## 5. Decision

**ADMITTED for forward collection.**

Every criterion the platform actually applies is met: the feature candidate gate
passes on recorded metrics, hard DQ does not fire, the clone bar and the 6A.14
collecting-family bar both pass, the specification is frozen and the
implementation is proven equivalent and hash-pinned.

The case rests on **the criteria as written and nothing else.** No positive case
is constructed from the clone result, and no rejection is manufactured from IS
weakness that the feature path does not test. R1–R4 are real, recorded, and
carried into the promotion assessment where they are actually adjudicated.

What this authorizes: the wiring gate. What it does not authorize: any change to
`collection_config.yaml`, `run_daily.py`, the deployed host, or the frozen
specification and implementation.

## 6. Governance gap surfaced — not closed here

**The 6A.14 collecting-family determination has no durable record.** The rule
applied in §2.3 — *a candidate whose \|r\| against a currently-collecting family
reaches 0.70 is inadmissible for collection* — was established when
`g5_trend_4h` was rejected at \|r\| = 0.7380, and is currently cited only
second-hand, inside
`docs/research/g1_trend_regime_ma_cross_6a15_specification.md`. There is no
`docs/governance/` record for it.

This admission **applies** that rule and depends on it. Writing 6A.14's record
retroactively is not this gate's authority — reconstructing another gate's
reasoning from a citation is precisely the fabrication discipline this project
forbids. Recorded here so the gap is visible and can be closed by a gate that
owns it.

## 7. Verification at this gate

| | |
|---|---|
| `thresholds.yaml` | **unmodified** — read only |
| `collection_config.yaml` | **unmodified** — signal still absent |
| `run_daily.py`, `runner_base.py`, `signals.py`, `engine.py`, `run_date_policy.py` | **unmodified** — hashes re-verified byte-identical |
| `decision_function_g1_trend_regime.py` | **unmodified** — hash re-verified `2e6f9597…` |
| Ledger | **untouched** — 70 runs / 13 transitions / 27 receipts; `report.py --check` OK (35 families, 9/22/4); `verify_chain` ok |
| `registry.yaml` | **one weakness string replaced** (§4.2); state and every other field unchanged |
| Production | `6eb12bd`, unchanged; nothing deployed |
| Lockbox 2025-06-01..2026-05-31 | **SEALED — not read** |
| Performance statistics | **none computed**; recorded values reviewed only |
| `n_trials = 34` | unspent, unchanged |

## 8. Trail

| Gate | Outcome |
|---|---|
| 2026-06-13 | Admitted `candidate`, role `feature`, on the Feature gate |
| 6A.14 | `g5_trend_4h` rejected for collection; trend branch falls back here |
| 6A.15-PRE | Clone screen: max \|r\| 0.6030, VIF 1.882 — admissible |
| 6A.15 | Specification frozen; U-1 and U-2 opened |
| 6A.16 | Implementation validated, equivalence proven, hash-pinned |
| **6A.17** | **ADMITTED. U-1 and U-2 resolved; U-3 opened.** |
| Next | Wiring — and U-3 must close there, before the first observation |
