# FB-10b-L — DSR Minimum Decision Matrix (FDB-8)

**Date: 2026-08-31 (UTC) · Gate FB-10b-L · Read-only · No DSR/BH computation, no
lockbox access, no amendment, no threshold or production change.**

**Four families lack a frozen DSR minimum. No value is recommended or selected
in this record.**

---

## 0. Settled upstream — not reopened

FDB-2a: recomputation window = the **frozen training / non-lockbox window**
(`2021-01-01 … 2025-05-31`); **no lockbox spending**; same **34** Phase B library
signals; **forward/trade-return** basis; `hold_days = 7`; `n_trials = 34`.

---

## 1. Per-family records

### 1.1 `ivrv_vrp_spread`

**A. Frozen evidence**
- `promotion_criteria.dsr_bh_requirement`: *"dsr_prob >= 0.50 AND bh_p <= 0.10
  on forward trade returns (thresholds.yaml `validated.feature`)"* — thresholds
  only; **no sample size**.
- `promotion_criteria.minimum_trades`: **30** (observation floor).
- `evidence_basis.dsr_prob = 0.1646`, `bh_p = 0.2893`, noted *"IS only,
  n_trials=34 (FROZEN), cost_bps=15, hold_days=1, lockbox-train slice"* — an
  **in-sample** record, explicitly not the Validated gate.
- `lockbox_status`: window sealed, `spent: false`.

**Frozen DSR minimum: NONE.**

### 1.2 `g1_trend_regime_ma_cross`

**A. Frozen evidence**
- `promotion_criteria.gates.G2_governance_stats`: *"dsr_prob >= 0.50 AND
  bh_p <= 0.10, computed at the FROZEN n_trials=34 on forward returns"* —
  fixes thresholds and the denominator, **never a sample size**.
- `promotion_criteria.minimum_trades`: **30**.
- `evidence_basis.dsr_prob = None`, `bh_p = None`; known_risks: *"NO IN-SAMPLE
  DSR OR BH-p EXISTS … forward evidence carries the entire statistical burden."*
- `lockbox_status`: window sealed, `spent: false`.

**Frozen DSR minimum: NONE.**

### 1.3 `g3_atr_expansion`

**A. Frozen evidence**
- `promotion_criteria.dsr_bh_requirement`: *"dsr_prob >= 0.50 AND bh_p <= 0.10
  on forward trade returns (thresholds.yaml `validated.edge`)"* — thresholds
  only.
- `promotion_criteria.minimum_trades`: **26** — the only family whose floor is
  not 30.
- `evidence_basis.dsr_prob = 0.2704`, `bh_p = 0.2073`, noted *"IS only …
  hold_days=1, full corpus IS portion. Fails Validated gate"*, and separately
  *"do not interpret as Validated-level evidence."*
- `lockbox_status`: **no key present.**

**Frozen DSR minimum: NONE.**

### 1.4 `g2_funding_level`

**A. Frozen evidence**
- `promotion_criteria.gates.G2_governance_stats`: identical wording to §1.2 —
  thresholds and denominator, **no sample size**.
- `promotion_criteria.minimum_trades`: **30**.
- `evidence_basis.dsr_prob_note`: *"**NOT COMPUTED.** No DSR was recorded for
  this family by the Phase B battery, and **none is fabricated here.** Under R1
  … a NULL governance stat is a hard failure"*; notable_flags: *"DSR and BH-p are
  NULL **by design, not by omission**. Validated is unreachable until both are
  computed at n_trials=34."*
- `lockbox_status`: window sealed, `spent: false`, *"All gating numbers … are
  train-slice."*

**Frozen DSR minimum: NONE.**

---

## 2. Governance-valid choices — properties (identical for all four families)

| | choice | conflicts with FR-5? | pre-registration amendment / re-hash? | changes the meaning of the observation floor? | creates a new frozen constant? |
|---|---|---|---|---|---|
| **1** | adopt the family's **existing observation floor** (30 / 30 / 26 / 30) as its DSR minimum | **Conditionally.** FR-5 names the observation floor as a *third, distinct* minimum that "must not be conflated" with statistical sufficiency. Adopting the **value** is not forbidden; adopting it **because it is available** is precisely the conflation FR-5 bars. Permissible **only** as an explicit act stating the two criteria remain distinct and merely coincide numerically | **Yes if** recorded in the pre-registration (keeps one source of truth). **No if** recorded only in a governance record — at the cost of a second source of truth for a per-family parameter | **Yes, unless expressly prevented.** The floor would acquire a second role; the act must state it does not merge the two criteria | **No new number** — but a **new binding** (that value becomes this family's DSR minimum) |
| **2** | amend to a **newly specified family-specific** DSR minimum | **No** | **Yes** — one amendment + re-hash per family, under the A2a convention | **No** | **Yes** — one new constant per family (up to four values) |
| **3** | amend all four to **one common** DSR minimum | **No**, provided the value is not taken from another family's 10 / 15 (FR-5 bars borrowing) and not derived from current trade counts | **Yes if** pre-registered in all four (4 amendments + re-hash). **No if** frozen only in a governance record — second source of truth | **No** | **Yes** — one shared constant |
| **4** | **permanently exclude** the family from DSR/BH validation | **No** | **Not strictly required**, but the exclusion must be recorded explicitly so it is a decision rather than an omission | **No** | **No** |

**Consequence common to choice 4:** under **R1** (CLAUDE.md §16) a NULL DSR or
BH-p is *"a hard failure … no silent bypass."* Permanent exclusion from DSR/BH is
therefore **permanent exclusion from Validated** for that family. `g2_funding_level`'s
own pre-registration states this outcome directly: *"Validated is unreachable
until both are computed."*

---

## 3. FINAL HUMAN DECISION TABLE

| Family | Existing observation floor | Frozen DSR minimum | Decision required | Choices | Required amendment |
|---|---|---|---|---|---|
| `ivrv_vrp_spread` | **30** | **NONE** | set a DSR minimum, or exclude | 1 / 2 / 3 / 4 | 1: explicit act (+ pre-reg amendment if recorded there) · 2: pre-reg amendment + re-hash · 3: pre-reg amendment + re-hash, or governance record · 4: explicit exclusion record |
| `g1_trend_regime_ma_cross` | **30** | **NONE** | set a DSR minimum, or exclude | 1 / 2 / 3 / 4 | as above |
| `g3_atr_expansion` | **26** | **NONE** | set a DSR minimum, or exclude | 1 / 2 / 3 / 4 | as above — note its floor is **26**, so choice 1 yields a different value than for the other three, and choice 3 would override it |
| `g2_funding_level` | **30** | **NONE** | set a DSR minimum, or exclude | 1 / 2 / 3 / 4 | as above |

No numerical value is recommended. None is inferred from current trade counts,
borrowed from the 10 / 15 held by other families, or taken from `n >= 5`.

---

## 4. The two structural questions

### 4.1 Can the four minima be adopted as their existing observation floors without violating any frozen constraint, or does FR-5 require a distinct governance act?

**A distinct governance act is required.** FR-5 enumerates **three distinct
minima** that "must not be conflated":

| minimum | value | kind | source |
|---|---|---|---|
| computational guard | `n >= 5` | mathematical | `governance.py` |
| statistical sufficiency | `n >= 10 / 15` | governance | per-family pre-registration |
| **observation floor** | **26–30 trades** | governance | per-family `observation_floors` (FB-7) |

FR-5 does **not** forbid the two governance minima from *sharing a value*. What
it forbids is treating one **as** the other. Adopting 30 / 30 / 26 / 30 as DSR
minima **by default, because those numbers are already present**, is exactly that
conflation and is not available.

The same adoption **is** available as an explicit governance act that (a) states
each family's DSR minimum, and (b) records that it coincides numerically with,
but does not merge into, the observation floor — which continues to govern
admissibility independently. Without that act, the answer is no.

### 4.2 Can one common minimum be introduced for all four without changing the frozen methodology, provided it is explicitly frozen and pre-registered?

**Yes — the methodology is unchanged.** A minimum-n is an **eligibility
parameter** governing *when* DSR may be computed, never *how*. Under choice 3,
`research/evidence/governance.py` is untouched, the dispersion population remains
the 34 Phase B library signals, `n_trials` remains 34, the observed basis remains
7-day non-overlapping trade returns, and the recomputation window remains the
frozen train slice.

Three conditions attach:

1. The value must be **new** — not borrowed from another family's 10 / 15
   (FR-5), not `n >= 5`, and not derived from current sample sizes.
2. It applies **only** to families that state none. FR-4 keeps the existing
   15 / 15 / 10 authoritative for their own families.
3. Pre-registering it in all four (rather than a governance record alone) keeps a
   single source of truth for a per-family parameter, at the cost of four
   amendments and re-hashes.

---

## 5. Status

FDB-8 remains **HUMAN DECISION REQUIRED** for all four families. FB-10b
implementation is **not authorized**. Nothing was computed, no lockbox was
accessed, and no artifact was modified.

**STOP.**

---

## 6. Trail

| Gate | Outcome |
|---|---|
| FB-10b-J | FDB-2 interpretation and the lockbox rule resolved |
| FB-10b-K | FDB-2a resolved to the frozen train slice |
| **FB-10b-L** | **This record. Per-family FDB-8 matrix; four choices characterised; both structural questions answered; no value selected** |
