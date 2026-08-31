# FB-10b — Forward DSR/BH Governance Contract (ADOPTED)

**Date: 2026-08-31 (UTC) · Gate FB-10b-O · Status: ADOPTED under explicit human
authorization.**

This record freezes the forward DSR/BH contract. It authorizes **no computation**
and **no producer implementation** — those remain separate gates.

---

## 1. The human decisions

| decision | value |
|---|---|
| **FDB-8** | **Option A** — adopt each family's observation-floor value as its explicit DSR minimum: `ivrv_vrp_spread = 30`, `g1_trend_regime_ma_cross = 30`, `g3_atr_expansion = 26`, `g2_funding_level = 30` |
| **Amendment form** | **Form 1** — existing prose convention in `collection_spec.evidence_snapshots.content[]`; container created where absent; **no structured minimum-n schema introduced** |
| **FDB-5** | **CONFIRMED** |
| **FDB-1 … FDB-12** | **APPROVED** |

---

## 2. FDB-1 … FDB-12 — the frozen contract

**FDB-1 — Dispersion population.** `sr_trials_std` is the standard deviation
(`ddof=1`) of the per-period Sharpe ratios of **all 34 Phase B library signals**.
The population is fixed at 34 and is identical to the population `n_trials = 34`
counts. Neither may be changed to accommodate the other.

**FDB-2 — Dispersion basis.** For forward validation the 34 library Sharpes are
**re-measured at the forward trade-return basis** — sequential, non-overlapping
7-day evaluation windows, `hold_days = 7`, `cost_bps = 15` — so the dispersion is
natively per-trade. The stored `evidence_library.json` Sharpes (`hold_days = 1`,
per-day) are **not** used for forward DSR, and **no scaling conversion is
applied**. `run_harmonization.py`'s `sr_trials_std * sqrt(7.0)` is **rejected for
all governance use**.

**FDB-2a — Recomputation window.** The **frozen training / non-lockbox window,
`2021-01-01 … 2025-05-31`**. Lockbox-spanning full-corpus gating is **not**
selected. No lockbox is accessed, consumed or spent by this contract.

**FDB-3 — Observed series.** Forward **trade** returns
(`trade_returns.net_return`), filtered `exit_date <= as_of`. A trade *is* a
non-overlapping 7-day block; no conversion between the two descriptions exists or
is permitted.

**FDB-4 — Units invariant.** `sr_trials_std`, `sr_star` and the observed returns
must all be per-trade. A result whose `sr_trials_std_basis` does not match its
`return_basis` is invalid.

**FDB-5 — BH battery membership (CONFIRMED).** A family is a DSR/BH battery
member at a given `as_of` **iff**:

1. it has a **frozen DSR minimum**;
2. it **meets that minimum** at the `as_of`;
3. `n >= 5`.

Membership is dynamic across time but fully determined at any `as_of`. Every
stored result must freeze its realized `battery_members[]` and
`battery_excluded[]`. `n_trials = 34` is **not** a BH universe and must never be
adjusted to make membership convenient.

**FDB-6 — Families without forward returns.** A library signal not collected
forward contributes to `sr_trials_std` only through FDB-2's re-measurement
(historical data, not forward data) and is **never** a BH member. It has no
forward p-value, and none may be approximated, imputed or estimated. The IS
approximation `psr_approx = Φ(sr_daily × √(n−1))` ("ignores skew/kurtosis") is
**not permitted** for any gating statistic.

**FDB-7 — Families below their minimum.** Excluded, recorded in
`battery_excluded[]` with reason `below_minimum(n=<n>, min=<m>)`. No DSR or BH-p
is computed, stored or reported. Absence is not a failure and must never be
recorded as one.

**FDB-8 — Minimum-n semantics and values.** A family's pre-registered DSR minimum
governs eligibility. `n >= 5` is solely the mathematical computability guard
(`psr` returns `NaN` below it) and **never** establishes sufficiency. A family
without a frozen DSR minimum may not participate. A minimum is never borrowed and
never defaulted.

| family | DSR minimum | observation floor | source |
|---|---|---|---|
| `h5_vol_regime` | 15 | 30 | pre-existing, unchanged |
| `g3_momentum_tsmom` | 15 | 30 | pre-existing, unchanged |
| `g1_market_regime_sma200` | 10 | 30 | pre-existing, unchanged |
| `ivrv_vrp_spread` | **30** | 30 | **adopted here** |
| `g1_trend_regime_ma_cross` | **30** | 30 | **adopted here** |
| `g3_atr_expansion` | **26** | 26 | **adopted here** |
| `g2_funding_level` | **30** | 30 | **adopted here** |

**The DSR minimum and the observation floor remain separate governance
criteria.** For the four adopted families they coincide numerically and are
**not conflated**: the observation floor governs admissibility, the DSR minimum
governs when DSR/BH may be computed. FR-5's three minima remain distinct.

**FDB-9 — Frozen result metadata.** Every stored DSR/BH result must record:
`battery_members[]` (with order), `battery_excluded[]` (with a reason each),
`sr_trials_std`, `sr_trials_std_basis` (population, units, recomputation window,
and that no conversion was applied), `n_trials = 34`, `q = 0.10`, `psr0`,
`sr_star`, `return_basis`, `n`, `as_of`, `calc_version`. A result missing any of
these is uninterpretable and invalid.

**FDB-10 — Point-in-time.** Every return satisfies `exit_date <= as_of`. No later
corpus growth may change a recorded result; results are frozen into append-only
rows, never recomputed on read.

**FDB-11 — Scope and invocation.** DSR/BH is **battery-wide and
operator-invoked**. It must never run from `run_daily.py`, a timer, or any
unattended process, and must refuse service actors. It performs **no** governance
act — no admission, pinning, transition, promotion or registry change — and
appends new runs rather than rewriting existing ones.
`research/evidence/governance.py` remains the **sole statistical authority**.

**FDB-12 — Validity.** No DSR/BH result is valid until **both** the family
conditions (FDB-8) and the battery conditions (FDB-5, FDB-9) are satisfied at the
result's own `as_of`.

---

## 3. What this gate changed

| artifact | change |
|---|---|
| `pre_registration_ivrv.json` | +2 prose entries in a newly created `collection_spec.evidence_snapshots.content[]` |
| `pre_registration_g1_trend_regime.json` | same |
| `pre_registration_g3_atr_expansion.json` | same |
| `pre_registration_g2_funding_level.json` | same |
| `registry.yaml` | **4** `preregistration.hash` values updated |
| this record | new |

Structural diff per pre-registration: **2 additions, 0 removals, 0 changes.**
`registry.yaml` diff: **4 lines, all `hash:` values.**

`evidence_snapshots` was created with `content` only; no `trigger` was invented.
It is documentary prose with **no code consumer** (verified: the sole reference,
`m0_pre_register.py`, is a writer).

---

## 4. Explicitly unchanged

`governance.py` · `thresholds.yaml` · observation floors · `n_trials = 34` ·
the 34-signal population · every `lockbox_status` · forward evidence ·
`evidence_library` · the ledger · production · the scheduler · collectors ·
the three pre-existing DSR minima (15 / 15 / 10).

**No lockbox was accessed, read, consumed or spent.** No DSR/BH was computed.
No producer was implemented.

---

## 5. Status

The forward DSR/BH **governance contract is complete**. Two conditions remain
before any result may exist, and neither is a governance question:

1. **Implementation** — the battery-wide, operator-invoked producer (a separate
   gate).
2. **Sample size** — no family currently meets its DSR minimum, so the battery
   under FDB-5 is presently **empty**.

**STOP.**

---

## 6. Trail

| Gate | Outcome |
|---|---|
| FB-10a-R | U1/U2/U3 opened; FR-1…FR-7 frozen |
| FB-10b-G | U1 and U4-core resolved |
| FB-10b-J | FDB-2 interpretation and the lockbox rule resolved |
| FB-10b-K | FDB-2a resolved to the frozen train slice |
| FB-10b-N | Final decision form issued |
| **FB-10b-O** | **This record. FDB-8 Option A adopted; FDB-5 confirmed; FDB-1…FDB-12 approved** |
