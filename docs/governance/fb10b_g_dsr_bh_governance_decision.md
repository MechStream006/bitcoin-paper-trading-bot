# FB-10b-G — DSR/BH Governance Decision Record

**Date: 2026-08-31 (UTC) · Gate FB-10b-G · Read-only · Verdict: STOP —
but two of four are now RESOLVED from frozen evidence.**

**U1 RESOLVED. U4 RESOLVED. U2 and U3 remain HUMAN DECISION REQUIRED**, each for
a precise and newly-articulated reason: every frozen DSR/BH convention on this
platform is IS-shaped (daily, `hold_days=1`, 34 library signals), the frozen
documents say the Validated gate "will recompute … on forward trade returns"
without saying how, and the two obvious bridges are each blocked by a *different*
frozen constraint.

No implementation, no statistic computed, no ledger/schema/threshold/
pre-registration/production change, no snapshot, no `pairwise_r` change, no
promotion.

---

## A. U1 — `sr_trials_std` population: **RESOLVED**

**Decision: the 34 Phase B library signals' per-period Sharpe values, from
`research/reports/evidence_library/evidence_library.json`. Library only — never
library-plus-candidates.**

### Frozen evidence

`research/forward_evidence/compute_dsr_for_registration.py`, header, under a
heading that is literally **"Governance constraints (FROZEN)"**:

```
n_trials   = 34   Phase B battery size — do NOT change
cost_bps   = 15   Matches the IS battery evaluation (REALISTIC_COST)
sr_denominator = std of all 34 per-period Sharpe values from evidence_library.json
```

and its implementation:

```python
daily_srs     = np.array([s / math.sqrt(ANN) for s in lib_sharpes.values()])
sr_trials_std = float(np.std(daily_srs, ddof=1))
```

Corroborated by `seed_from_scoreboard.py:154` — `n_trials = len(library)`, which
the reconciliation annotates *"the library **is** the 34-signal battery"* — and
verified concretely: `evidence_library.json` holds **34 entries, all 34 carrying
a Sharpe**, matching the frozen denominator exactly.

### Why the two discovery callers disagree — and why that is now settled

```
run_dvol_family.py:108   std(lib_daily_sr + dvol_daily_sr)   paired with n_trials = 38
run_harmonization.py:119 std(lib_daily_sr)                    paired with n_trials = 34
```

They are **matched pairs**, not free choices: each script's dispersion population
matches its own denominator. `docs/governance/n_trials_denominator_reconciliation.md`
already placed the 38 pairing in **"Group B — the discovery-screen artifact
(non-governance)"** (`n_trials = len(lib) + len(specs)`, paired with
`DSR_CONF = 0.95`, *"an exploratory screen … never written to the ledger"*), and
Group C lists documents that reference 38 *only to reject it*.

**Rejecting 38 as the denominator already rejected its dispersion population.**
The surviving pairing is library-only at 34, which is exactly what the FROZEN
block above states. This is settled by the existing freeze, **not** by
statistical preference.

---

## B. U2 — return unit / dispersion basis: **HUMAN DECISION REQUIRED**

**Not resolvable from frozen evidence. Two candidate resolutions exist and each
is blocked by a different frozen constraint.**

### What is frozen

- **The forward return unit is settled and not reopened here**: forward trade
  returns, which *are* the non-overlapping 7-day blocks (every pre-registration:
  *"each 7-day evaluation window is one trade; trades are sequential and
  non-overlapping"*; `hold_days_actual = 7` on all 31 production trades).
- **The frozen DSR convention is entirely daily.** `compute_dsr_for_registration.py`
  de-annualises library Sharpes (`s / √365`), evaluates signals at
  `"hold_days": 1  # matches IS battery evaluation`, and feeds daily
  `E._net_daily` returns to `deflated_sr`. `deflated_sr`'s contract returns
  values *"all per-period"*, so dispersion and returns must share one period.
- **Forward is not that period**, and four pre-registrations say so:
  *"IS and forward Sharpe/DSR are **NOT directly comparable**; the Validated gate
  evaluates forward returns only"* (`g3_atr_expansion`, `ivrv`); *"not directly
  comparable across the two conventions"* (`g2_funding_level`, `g3_momentum_tsmom`).
- **The frozen artifact defers the question rather than answering it**:
  *"The Validated gate will recompute DSR/BH-FDR on forward trade returns."* It
  does not state the denominator for that recomputation.

### The deadlock

| candidate | blocked by |
|---|---|
| **(a)** keep the frozen 34-library **daily** dispersion, convert to per-trade units | the only conversion in existence is `run_harmonization.py:134`, `sr_trials_std * np.sqrt(7.0)`, commented **"rough per-7d scaling"** by its own author — and it would implement precisely the IS↔forward comparison four pre-registrations declare invalid |
| **(b)** compute a **forward-native** dispersion (SD of the collecting families' own forward per-trade Sharpes) | `deflated_sr` requires `sr_trials_std` and `n_trials` to describe the *same* trial population. A forward-native dispersion spans ~7 families, not 34 trials, so adopting it **reopens the frozen `n_trials = 34`** — which CLAUDE.md §16 lists as governance-frozen and the reconciliation says is *"re-openable only by a future session that explicitly puts the freeze on the table."* |

**Neither can be chosen without a human authorising something currently
forbidden.** That is the decision required: authorise a specific conversion for
(a), or explicitly put the `n_trials` freeze on the table for (b).

---

## C. U3 — BH battery membership: **HUMAN DECISION REQUIRED**

**A frozen precedent exists, and it is not computable on forward evidence.**

### Frozen evidence

`compute_dsr_for_registration.py` §4:

```python
# 4. BH-FDR across all 34 signals (approximation for non-registered 31)
bh_result = G.bh_fdr(all_pvals, q=0.10)          # all_pvals = all 34 library signals
...
print(f"  BH-FDR: rank={bhr['rank']}/{N_TRIALS}, ...")
```

So in the **governance** registration path (Group A of the reconciliation, not a
discovery screen) the BH universe is **all 34 library signals**, `q = 0.10`, with
rank reported out of 34 — BH membership coinciding with the `n_trials`
denominator.

### Why it cannot be transplanted

Those 34 signals have **no forward returns**. Only 7 families collect, only 5
have any closed trade, and none meets its own minimum. A forward BH over 34
members is not computable, and the IS path only managed it by **approximating**
the 31 non-registered signals (`psr_approx = Φ(sr_daily × √(n−1))`, explicitly
*"ignores skew/kurtosis"*) — an approximation acceptable for an informational IS
screen and plainly not for a gating forward statistic.

### The four populations, kept distinct as required

| population | size | role |
|---|---|---|
| registered families | 35 | the registry |
| collecting families | 7 | writing forward evidence |
| families meeting their own minimum | **0** | see §D/§H |
| `n_trials` denominator | 34 | frozen DSR multiple-testing count, **and** the IS BH universe |

**No frozen artifact defines a forward BH universe.** The decision required is
the membership *rule* — e.g. all collecting families, or only those meeting
their minimum — stated as a rule rather than a count, since BH ranks within its
set and membership changes every member's result.

---

## D. U4 — minimum-n semantics: **RESOLVED**

**Decision: a family's pre-registered minimum governs DSR/BH eligibility.
`governance.py`'s `n >= 5` is a computational safety floor only and never
establishes sufficiency.**

### Frozen evidence

`docs/governance/fb10a_forward_statistical_validation_decision.md`, §2, under
**"Frozen — not reopened by this record"**:

> **FR-4** — *"per-family pre-registered minima are authoritative. 15 / 15 / 10 …
> These are governance sufficiency criteria."*
>
> **FR-5** — *"`n >= 5` is a mathematical guard, not a governance criterion.
> `governance.py` lines 79 and 90 return `NaN` below five observations because
> the skew/kurtosis-adjusted PSR is undefined there. It is a floor on
> *computability*, never on *sufficiency*, and must never be cited as one."*

Confirmed in the pre-registrations themselves: `h5_vol_regime` *"compute when
n >= 15"*, `g3_momentum_tsmom` *"when n >= 15"*, `g1_market_regime_sma200`
*"when n >= 10"*.

**No new floor is invented here.** The three stated minima stand; `n >= 5`
remains a computability precondition that must be satisfied *in addition to*,
never *instead of*, the family's own minimum.

### Residual, NOT resolved

**Four collecting families state no minimum at all**: `ivrv_vrp_spread`,
`g2_funding_level`, `g3_atr_expansion`, `g1_trend_regime_ma_cross`. FR-4 makes a
stated minimum authoritative but is silent on silence, and FR-5 forbids
importing another family's number. **Whether an unstated minimum means "any n
above the computability floor" or "no authorization to compute" is a HUMAN
DECISION REQUIRED** before DSR may be computed for those four.

---

## E. What would change if each were left unresolved

| item | consequence of leaving it open |
|---|---|
| U1 | *(resolved)* — none |
| **U2** | **every DSR value on the platform**. `sr_star` scales linearly in `sr_trials_std`; a `√7` factor is ~2.65×, which moves `dsr_prob` across the 0.50 bar in either direction |
| **U3** | **every BH-p**. BH ranks within its universe, so adding or removing one member changes every other member's threshold and verdict |
| U4 core | *(resolved)* — none |
| U4 residual | the four families without a stated minimum can never be lawfully computed, whatever U2/U3 decide |

---

## F. Remaining human approvals

1. **U2** — authorise a specific IS→forward dispersion conversion, **or**
   explicitly put the `n_trials = 34` freeze on the table to permit a
   forward-native dispersion. One of the two is unavoidable.
2. **U3** — the forward BH membership rule.
3. **U4 residual** — the meaning of an unstated per-family minimum.

*(Not required: `n_trials = 34` is verified frozen — CLAUDE.md §16, ADR-0001
§17, the reconciliation's Group A, two `G2_governance_stats` gates reading
"computed at the FROZEN n_trials=34 on forward returns", and all 70 pre-existing
ledger runs storing 34. Not reopened here. Its relocation into `thresholds.yaml`
remains desirable, not necessary.)*

---

## G. Is FB-10b implementation governance-authorized?

**No.** U2 and U3 both change the numbers, and this gate's own rule forbids
choosing either by engineering judgment.

---

## H. Earliest legitimate computation condition, per family

Conditions are conjunctive: **U2 and U3 decided**, *and* the family's own
minimum met, *and* `n >= 5`.

| family | trades now | own minimum | earliest legitimate computation |
|---|---|---|---|
| `g3_momentum_tsmom` | 8 | 15 | U2+U3 decided **and** n ≥ 15 (7 more trades; ~7 weeks at one/week) |
| `h5_vol_regime` | 7 | 15 | U2+U3 decided **and** n ≥ 15 (8 more) |
| `g1_market_regime_sma200` | 7 | 10 | U2+U3 decided **and** n ≥ 10 (3 more) |
| `ivrv_vrp_spread` | 6 | — | **also** requires the U4 residual decision |
| `g3_atr_expansion` | 3 | — | U4 residual **and** n ≥ 5 |
| `g1_trend_regime_ma_cross` | 0 | — | U4 residual **and** n ≥ 5 |
| `g2_funding_level` | 0 | — | U4 residual **and** n ≥ 5 |

Trade counts are at `as_of = 2026-08-30`. **Today's sample size played no part in
any decision above** — it is recorded here solely as the execution condition it
is.

---

## I. VERDICT: **STOP**

Unresolved: **U2** (dispersion basis/units), **U3** (BH membership), and the
**U4 residual** (unstated minima).

Newly resolved and available to a successor gate: **U1** — the dispersion
population is the 34 Phase B library signals, settled by the existing `n_trials`
freeze rather than by preference — and **U4 core** — the pre-registered minimum
governs eligibility; `n >= 5` is a computability floor only.

---

## J. Trail

| Gate | Outcome |
|---|---|
| FB-10a-R | Deferred; FR-1..FR-7 frozen; U1/U2/U3 opened |
| FB-10b-pre | STOP; U4 opened; zero families eligible |
| **FB-10b-G** | **This record. U1 and U4-core RESOLVED; U2, U3, U4-residual require human decision. STOP** |
| FB-10b | **Not authorized** |
