# FB-10b-I — DSR/BH Governance Amendment: DRAFT

**Date: 2026-08-31 (UTC) · Gate FB-10b-I · Read-only drafting · Verdict: STOP —
three values remain HUMAN DECISION REQUIRED.**

The selected decisions (U1 resolved, U2-B, U3 explicit-battery, U4
explicit-minimum) are drafted below as amendment-ready governance language.
**Three values the selections did not specify cannot be derived from frozen
evidence and are marked inline.** Nothing is implemented; no code, threshold,
pre-registration, registry, ledger or production change.

---

## 1. Final decisions as selected

| | decision |
|---|---|
| **U1** | `sr_trials_std` population = **all 34 Phase B library per-period Sharpes**. `n_trials = 34` remains frozen |
| **U2** | **U2-B** — recompute the 34-family dispersion at the forward/trade-return basis. The `× √7` conversion is **rejected**. Forward trade returns remain the observed series |
| **U3** | BH operates over an **explicitly frozen forward-validation battery**, never implicitly over the 34 library families. Every result freezes `battery_members[]` and `battery_excluded[]` |
| **U4** | `n >= 5` is **only** the computability guard. The four families lacking a DSR minimum **must receive an explicit frozen minimum before participating**. No borrowing; no silent default to 30 |

---

## 2. Frozen evidence

**U1** — `compute_dsr_for_registration.py`, "Governance constraints (FROZEN)":
*"n_trials = 34 Phase B battery size — do NOT change"*; *"sr_denominator = std of
all 34 per-period Sharpe values from evidence_library.json"*. Verified: the file
holds 34 entries, all 34 carrying a Sharpe. Corroborated by
`seed_from_scoreboard.py:154` (`n_trials = len(library)`).

**U2** — `deflated_sr` docstring: benchmark is *"expected maximum Sharpe under
n_trials independent trials given the cross-trial SR dispersion"*, results *"all
per-period"*. `psr(returns, sr_benchmark)` derives the observed SR **from the
returns array**, so the benchmark must be in the returns' own units. Every
pre-registration: *"each 7-day evaluation window is one trade; trades are
sequential and non-overlapping"*; `hold_days_actual = 7` on all 31 production
trades. Four pre-registrations: *"IS and forward Sharpe/DSR are NOT directly
comparable"* — which is why the `√7` bridge is rejected.

**U3** — `bh_fdr(pvals, q)` ranks within the dict it is given; membership changes
every member's threshold. The 34-family IS precedent
(`compute_dsr_for_registration.py` §4) approximates 31 signals with a formula
that *"ignores skew/kurtosis"* — informational, not gating.

**U4** — FB-10a-R **FR-4** (*"per-family pre-registered minima are
authoritative … governance sufficiency criteria"*) and **FR-5** (`n >= 5` is
*"a floor on computability, never on sufficiency, and must never be cited as
one"*). Only three families state a DSR minimum (15 / 15 / 10).

---

## 3. Proposed governance language

### 3.1 New governance record — `docs/governance/forward_dsr_bh_contract.md`

> **FDB-1 — Dispersion population.** `sr_trials_std` is the standard deviation
> (`ddof=1`) of the per-period Sharpe ratios of **all 34 Phase B library
> signals**. The population is fixed at 34 and is identical to the population
> `n_trials = 34` counts. Neither may be changed to accommodate the other.
>
> **FDB-2 — Dispersion basis (U2-B).** For forward validation the 34 library
> Sharpes are **recomputed at the forward trade-return basis** — sequential,
> non-overlapping 7-day evaluation windows, `hold_days = 7`, `cost_bps = 15` —
> so that the dispersion is natively in per-trade units. The stored
> `evidence_library.json` Sharpes (`hold_days = 1`, per-day) are **not** used for
> forward DSR, and **no scaling conversion of any kind is applied**. The
> `sr_trials_std * sqrt(7.0)` conversion in `run_harmonization.py` is
> **explicitly rejected** for all governance use.
>
> **FDB-2a — Recomputation window.** `[HUMAN DECISION REQUIRED — see §8.1]`
>
> **FDB-3 — Observed series.** The observed returns are forward **trade**
> returns (`trade_returns.net_return`), filtered `exit_date <= as_of`. Because a
> trade *is* a non-overlapping 7-day block, no conversion between "trade
> returns" and "7-day blocks" exists or is permitted.
>
> **FDB-4 — Units invariant.** `sr_trials_std`, `sr_star` and the observed
> returns must all be per-trade. A result whose recorded
> `sr_trials_std_basis` does not match its `return_basis` is invalid.
>
> **FDB-5 — BH battery.** BH-FDR operates over an explicitly frozen
> **forward-validation battery**, never over the 34 library families. A family
> is a member at a given `as_of` if and only if all of:
> 1. it carries a **frozen DSR minimum** in its pre-registration (FDB-8);
> 2. its closed-trade count with `exit_date <= as_of` **meets that minimum**;
> 3. that count satisfies `n >= 5`.
>
> Membership is therefore *dynamic across time but fully determined at any
> `as_of`*. Every stored result must freeze the realized membership (FDB-9).
> `n_trials = 34` is **not** a BH universe and must never be adjusted to make BH
> membership convenient.
>
> **FDB-6 — Families without forward returns.** A library signal that is not
> collected forward contributes to `sr_trials_std` **only** through FDB-2's
> recomputation (which uses historical data, not forward data) and is **never**
> a BH battery member. It has no forward p-value and none may be approximated,
> imputed or estimated. The §4 IS approximation
> (`psr_approx = Φ(sr_daily × √(n−1))`, "ignores skew/kurtosis") is
> **not permitted** for any gating statistic.
>
> **FDB-7 — Families below their own minimum.** Excluded from the battery,
> recorded in `battery_excluded[]` with reason `below_minimum(n=<n>, min=<m>)`.
> No DSR or BH-p is computed, stored or reported for them. Absence is not a
> failure and must never be recorded as one.
>
> **FDB-8 — Minimum-n semantics.** A family's pre-registered DSR minimum governs
> eligibility. `n >= 5` is solely the mathematical computability guard
> (`psr` returns `NaN` below it) and never establishes sufficiency. **A family
> without a frozen DSR minimum may not participate in DSR or BH.** A minimum is
> never borrowed from another family and never defaulted.
>
> **FDB-9 — Frozen result metadata.** Every stored DSR/BH result must record:
> `battery_members[]` (with order), `battery_excluded[]` (with a reason each),
> `sr_trials_std`, `sr_trials_std_basis` (population, units, recomputation
> window, and the statement that no conversion was applied), `n_trials = 34`,
> `q = 0.10`, `psr0`, `sr_star`, `return_basis`, `n`, `as_of`, `calc_version`.
> A result missing any of these is uninterpretable and invalid.
>
> **FDB-10 — Point-in-time.** Every return satisfies `exit_date <= as_of`. No
> later corpus growth may change a recorded result; results are frozen into
> append-only rows, never recomputed on read.
>
> **FDB-11 — Scope and invocation.** DSR/BH is **battery-wide and
> operator-invoked**. It must never run from `run_daily.py`, a timer, or any
> unattended process, and must refuse service actors. It performs **no**
> governance act — no admission, pinning, transition, promotion or registry
> change — and appends new runs rather than rewriting existing ones.
>
> **FDB-12 — Validity.** No DSR/BH result is valid until **both** the
> family conditions (FDB-8: a frozen minimum, met) and the battery conditions
> (FDB-5, FDB-9) are satisfied at the result's own `as_of`.

### 3.2 Affected artifacts

| artifact | change | why |
|---|---|---|
| **`docs/governance/forward_dsr_bh_contract.md`** | **NEW** governance record | FDB-1 … FDB-12 |
| **4 pre-registrations** — `ivrv`, `g2_funding_level`, `g1_trend_regime`, `g3_atr_expansion` | **AMENDMENT** adding an explicit DSR minimum | required by FDB-8; values are `[HUMAN DECISION REQUIRED — §8.3]`. Pre-registrations are hash-pinned, so each re-hashes and must be re-registered under the A2a convention |
| **`CLAUDE.md` §16** | add FDB-1…FDB-12 to the governance-frozen list; record that `√7` is rejected | keeps the frozen-constraint index complete |
| **`thresholds.yaml`** | **NO CHANGE REQUIRED.** `min_dsr_prob` and `max_bh_p` already exist; `n_trials` relocation remains optional housekeeping | avoid touching a frozen file unnecessarily |
| **`registry.yaml`** | **NO CHANGE** | no state transition implied |
| **`research/evidence/governance.py`** | **NO CHANGE** — remains the sole statistical authority | FR-3 |

---

## 4. Consequences for future computation

- Forward DSR uses a **recomputed per-trade dispersion**; IS and forward DSR
  values are permanently non-comparable and must never be presented side by side
  as if they were.
- All 11 existing live-forward rows keep `dsr_prob` / `bh_p` **NULL**
  permanently. New statistics arrive only as **new** rows at a **new** `as_of`.
- `bh_p` becomes meaningful only relative to its own recorded battery. Two
  results at different `as_of` values are comparable only if their
  `battery_members[]` match.
- Today: **zero** families satisfy FDB-5, so the battery is empty and no result
  may be produced. That is an execution state, not a defect.

---

## 5. Treatment summary

| case | treatment |
|---|---|
| library family not collected forward | contributes to `sr_trials_std` via FDB-2 only; **never** a BH member; no p-value, no approximation (FDB-6) |
| family below its own minimum | excluded, recorded in `battery_excluded[]` with reason; no statistic computed or stored (FDB-7) |
| family with no frozen minimum | **may not participate at all** until amended (FDB-8) |
| family meeting its minimum with `n >= 5` | battery member; DSR and BH computed and frozen with FDB-9 metadata |

---

## 6. Point-in-time, scope, validity

Restated as required, and drafted above as FDB-10, FDB-11 and FDB-12:
`exit_date <= as_of` throughout; **battery-wide and operator-invoked**, never
scheduled, never a governance act; **no result is valid** until the family and
battery conditions are both met at that result's `as_of`.

---

## 7. What is fully specified and ready

FDB-1, FDB-3, FDB-4, FDB-5, FDB-6, FDB-7, FDB-8 (semantics), FDB-9, FDB-10,
FDB-11, FDB-12 — and FDB-2 except for its window.

---

## 8. HUMAN DECISION REQUIRED — three values

### 8.1 FDB-2a — the recomputation window, with a lockbox consequence

U2-B requires re-evaluating the 34 library signals at `hold_days = 7`. **The
window is not specified and cannot be derived.** It matters for a reason the
selection did not address:

- The Phase B library corpus is **2021-01-01 … 2026-05-31**, which **spans** the
  sealed lockbox window **2025-06-01 … 2026-05-31**.
- Lockbox posture is **heterogeneous across the library**: `h6` records
  `spent: true`; `g1_trend_regime` records `spent: false` with an unspent window;
  `g1_market_regime_sma200` records *"No lockbox was held."*
- CLAUDE.md §16's H-IVRV precedent states that computing the full-sample Sharpe
  *"**spends** the sealed lockbox, so it is not used."*

**Under that existing precedent, recomputing over the full corpus would spend
at least one currently-unspent lockbox.** The alternatives (train slice
2021-01 … 2025-05; or another window) avoid this but produce a different
dispersion. **This is a governance choice with irreversible consequences and is
not derivable.**

### 8.2 FDB-2 — a reading to confirm

The selection says *"recompute the 34-family **forward** dispersion"* and asks
what happens *"when a library family has no **forward returns**."* Two readings:

- **(i)** recompute the 34 signals at the **forward basis** (`hold_days = 7`) on
  historical data — FB-10b-H's option B as written, and what §3 drafts. All 34
  are computable; `n_trials = 34` stays valid.
- **(ii)** compute the dispersion **from forward returns** of the 34 — **not
  implementable**: only 7 families collect forward, so 27 have no data.
  Excluding them would leave a dispersion over ~7 against a denominator of 34,
  violating `deflated_sr`'s same-population requirement — which the selection
  explicitly forbids resolving by changing `n_trials`.

**§3 is drafted under (i). Confirmation required**, because (ii) as literally
worded is self-contradictory under the selection's own constraints.

### 8.3 FDB-8 — the four minimum values

`ivrv_vrp_spread`, `g2_funding_level`, `g1_trend_regime_ma_cross`,
`g3_atr_expansion` must each receive an explicit frozen DSR minimum. The
selection forbids borrowing another family's value and forbids silently using
30. **The four values are therefore human decisions**, each requiring a
pre-registration amendment and re-hash.

*(For reference only, not a proposal: the three stated DSR minima are 15 / 15 /
10; the separate FB-7 observation floors are 30 for six families and 26 for
`g3_atr_expansion`.)*

---

## 9. VERDICT: **STOP**

The amendment is **substantially drafted and internally consistent**, but three
values remain outstanding:

1. **FDB-2a** — the recomputation window, with the lockbox-spending consequence
   (§8.1).
2. **FDB-2** — confirmation of reading (i) versus (ii) (§8.2).
3. **FDB-8** — the four per-family DSR minimum values (§8.3).

Not **AMENDMENT READY**. No value was selected by inference, preference or
engineering judgment.

---

## 10. Trail

| Gate | Outcome |
|---|---|
| FB-10b-G | U1 and U4-core resolved |
| FB-10b-H | Decision matrix; twelve options, none chosen |
| **FB-10b-I** | **This draft. FDB-1…FDB-12 drafted; three values outstanding; STOP** |
