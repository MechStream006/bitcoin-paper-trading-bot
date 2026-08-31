# FB-10b-N — FINAL HUMAN DECISION FORM

**Date: 2026-08-31 (UTC) · Gate FB-10b-N · Read-only · No implementation, no
amendment, no computation, no lockbox access, no production write.**

**No recommendation is made anywhere in this document.**

---

## 0. A mechanical fact that shapes every amendment

The three existing DSR minima are recorded as **prose inside
`collection_spec.evidence_snapshots.content[]`**:

| family | location | text |
|---|---|---|
| `h5_vol_regime` | `content[3]` | *"DSR on non-overlapping 7-day return blocks (compute when n >= 15)"* |
| `g1_market_regime_sma200` | `content[2]` | *"DSR on non-overlapping 7-day return blocks across all trades (when n >= 10)"* |
| `g3_momentum_tsmom` | `content[3]` | *"DSR on completed-period returns (when n >= 15)"* |

**There is no structured minimum-n key anywhere in any pre-registration.** (The
only structural near-match is `governance.validated_gate.min_dsr_prob = 0.5` — a
*threshold*, not a sample size.)

Any amendment under Options A/B/C therefore carries a **sub-decision about
form**, presented without recommendation:

- **Form 1 — prose**, matching the three existing families. Precedent-consistent;
  the value remains machine-unreadable, so battery membership (FDB-5) would be
  enforced by the operator, not by code.
- **Form 2 — a structured key** (e.g. `promotion_criteria.dsr_minimum_n`).
  Machine-readable, so FDB-5 becomes automatically checkable; but it is a
  **schema change** to the pre-registration format and would make the four
  families structurally unlike the three that carry prose.

---

## 1. FDB-8 — DSR minimum

### Option A — adopt existing observation floors as explicit DSR minima

`ivrv_vrp_spread = 30` · `g1_trend_regime_ma_cross = 30` ·
`g3_atr_expansion = 26` · `g2_funding_level = 30`

**Must include the express statement** that the DSR minimum and the observation
floor remain **separate governance criteria** that coincide numerically for these
families and do not merge — without it, FR-5's non-conflation rule is breached.

**Changes required:**

| artifact | change |
|---|---|
| 4 pre-registration JSONs | add the DSR minimum (Form 1 or Form 2) + the non-conflation statement |
| `registry.yaml` | update **4** `preregistration.hash` values — `ivrv` `7d1e897e…`, `g1_trend` `2d3cbd21…`, `g3_atr` `b76c5af9…`, `g2_funding` `8355c165…` |
| new governance record | records the decision and the non-conflation statement |
| *(alternative)* | governance record **only**, no pre-registration/registry change — at the cost of a second source of truth for a per-family parameter |

### Option B — one common new DSR minimum `N`

**`N` is left blank for you to supply.**

**Changes required:** identical to Option A's table (4 JSONs + 4 registry hashes
+ governance record), except the value is one shared `N` and no non-conflation
statement is needed. Alternative: a governance record alone, same second-source
cost.

**Constraints on `N`:** not 10 or 15 (FR-5 bars borrowing); not `n >= 5`; not
derived from current sample sizes. Applies **only** to families stating none —
the existing 15 / 15 / 10 stay authoritative.

### Option C — four family-specific DSR minima

**All four values are left blank for you to supply.**

**Changes required:** 4 pre-registration JSONs + **4** `registry.yaml` hash
updates + governance record. A governance-record-only route is **not** offered
here: a per-family value belongs in the per-family document.

Same constraints on each value as Option B.

### Option D — permanent exclusion of selected families

**Changes required:**

| artifact | change |
|---|---|
| pre-registration JSONs | **none required** |
| `registry.yaml` | **no hash change** |
| new governance record | must record the exclusion explicitly, per family, so it is a decision and not an omission |

**Consequence:** under **R1**, a NULL DSR/BH is *"a hard failure … no silent
bypass"*, so an excluded family is **permanently unable to reach Validated**
while excluded. `g2_funding_level`'s own pre-registration already states this:
*"Validated is unreachable until both are computed."*

*(D may be applied to some families and A/B/C to others; the checklist in §4
allows a per-family answer.)*

---

## 2. FDB-5 — battery membership

**Currently derived rule — NOT yet human-confirmed.** FB-10b-I derived it from
your U4 selection; it has never been explicitly approved:

> A family is a DSR/BH battery member at a given `as_of` **iff**:
> 1. it holds a **frozen DSR minimum**;
> 2. it **meets** that minimum at that `as_of`;
> 3. `n >= 5`.
>
> Membership is dynamic across time but fully determined at any `as_of`. Every
> stored result must freeze its realized `battery_members[]` and
> `battery_excluded[]`.

**Explicit answer required: CONFIRM or AMEND.** This is not treated as settled
merely because it was derived.

---

## 3. FDB-1 … FDB-12 — adoption status

| # | subject | status |
|---|---|---|
| **FDB-1** | dispersion population = the 34 Phase B library signals | **RESOLVED FROM FROZEN EVIDENCE** (`compute_dsr_for_registration.py` "Governance constraints (FROZEN)") — and human-confirmed in this gate's preamble |
| **FDB-2** | recompute those 34 at the forward/trade basis; `√7` rejected | **HUMAN-SELECTED** (U2-B); interpretation **RESOLVED FROM FROZEN EVIDENCE** (FB-10b-J: Option A; B excluded by U1 + the `n_trials` freeze) |
| **FDB-2a** | recomputation window = frozen train slice `2021-01-01 … 2025-05-31` | **RESOLVED FROM FROZEN EVIDENCE** (FB-10b-K) — and **HUMAN-CONFIRMED** ("Lockbox-spanning full-corpus gating is NOT selected") |
| **FDB-3** | observed series = forward trade returns | **RESOLVED** + human-confirmed |
| **FDB-4** | units invariant (dispersion, `sr_star`, returns all per-trade) | **RESOLVED FROM FROZEN EVIDENCE** (`psr` derives SR from the returns array) |
| **FDB-5** | BH battery membership | **STILL OPEN — awaiting CONFIRM / AMEND** (§2) |
| **FDB-6** | library families without forward returns: never BH members; **no approximation** | **RESOLVED FROM FROZEN EVIDENCE** |
| **FDB-7** | families below their minimum: excluded and recorded, never a "failure" | **RESOLVED FROM FROZEN EVIDENCE** |
| **FDB-8** | minimum-n **semantics** | **RESOLVED FROM FROZEN EVIDENCE** (FR-4 / FR-5) |
| **FDB-8** | the four **values** | **STILL OPEN** (§1) |
| **FDB-9** | frozen result metadata | **RESOLVED FROM FROZEN EVIDENCE** (`deflated_sr` / `bh_fdr` signatures) |
| **FDB-10** | point-in-time `exit_date <= as_of` | **RESOLVED** + human-confirmed |
| **FDB-11** | battery-wide, operator-invoked, no governance act | **RESOLVED FROM FROZEN EVIDENCE** |
| **FDB-12** | no result valid until family **and** battery conditions met | **RESOLVED** (follows from FDB-5 + FDB-8) |

**Open: FDB-5 (confirmation) and FDB-8 (four values). Nothing else.**

---

## 4. FINAL APPROVAL CHECKLIST

```
FDB-8 choice:
[ ]  A — adopt observation floors (30 / 30 / 26 / 30) as explicit DSR minima,
         with the express non-conflation statement
[ ]  B — one common new minimum N
[ ]  C — four family-specific minima
[ ]  D — permanent exclusion (specify which families below)

If B: common minimum N = ______

If C:
    ivrv_vrp_spread          = ______
    g1_trend_regime_ma_cross = ______
    g3_atr_expansion         = ______
    g2_funding_level         = ______

If D: exclude (tick each that applies)
    [ ] ivrv_vrp_spread
    [ ] g1_trend_regime_ma_cross
    [ ] g3_atr_expansion
    [ ] g2_funding_level

If A, B or C — amendment form:
[ ]  Form 1 — prose in collection_spec.evidence_snapshots.content[]
             (matches the three existing families; not machine-readable)
[ ]  Form 2 — structured key (machine-readable; schema change)

If A, B or C — recording location:
[ ]  pre-registrations + registry.yaml hash updates  (single source of truth)
[ ]  governance record only                          (second source of truth)


FDB-5 battery rule:
[ ]  CONFIRM
[ ]  AMEND  → ________________________________________________


Governance adoption of FDB-1 … FDB-12:
[ ]  APPROVE
[ ]  DO NOT APPROVE
```

---

## 5. Status

**FB-10b remains UNAUTHORIZED until these fields are explicitly decided.**

Sample-size availability is a separate **execution** condition and is not part of
this form: no family currently meets any candidate minimum, so authorization
would not by itself permit any computation.

**STOP.**

---

## 6. Trail

| Gate | Outcome |
|---|---|
| FB-10b-L | Per-family FDB-8 matrix; both structural questions answered |
| FB-10b-M | Four options prepared; one decision, two confirmations remain |
| **FB-10b-N** | **This form. FDB-5 and the four FDB-8 values are the only open fields** |
