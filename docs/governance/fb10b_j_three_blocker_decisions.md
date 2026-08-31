# FB-10b-J — DSR/BH Three Blocker Decisions

**Date: 2026-08-31 (UTC) · Gate FB-10b-J · Read-only · No computation performed,
no lockbox read, spent or altered.**

**FDB-2 (interpretation): RESOLVED by frozen evidence — Option A. Option B is
excluded by already-frozen constraints; no Option C exists.**
**FDB-2a (window) and FDB-8 (four values): HUMAN DECISION REQUIRED.**
**FDB-1 (lockbox): the *rule* is resolved by frozen evidence; its application
depends entirely on the FDB-2a window choice.**

No decision is taken on the human's behalf. No statistic computed. No
pre-registration, threshold, `governance.py`, registry, ledger or production
change.

---

## 0. Already frozen — must not be reopened

| item | status |
|---|---|
| **U1** | `sr_trials_std` population = **the 34 Phase B library Sharpes** |
| **`n_trials`** | **34** |
| **Observed returns** | `trade_returns.net_return` — 7-day non-overlapping trade basis |
| **U2-B** | the selected direction; **its population/window is not yet frozen** |
| **BH** | must record realized battery membership in every result |
| **`n >= 5`** | computability guard only — **never** statistical sufficiency |

---

## 1. FDB-2 — U2-B interpretation: **RESOLVED (Option A)**

### Option A — recompute the same 34 library signals at the forward/trade basis

| question | answer |
|---|---|
| Frozen evidence | U1 (resolved): population = the 34 library Sharpes. `compute_dsr_for_registration.py` "Governance constraints (FROZEN)": *"sr_denominator = std of all 34 per-period Sharpe values"*. `engine._apply_hold(raw_pos, hold_days)` accepts any hold length, so every library signal is representable at `hold_days = 7` |
| Can all 34 be represented at the forward basis? | **Yes.** All 34 are daily-position signals; the engine's hold mechanism is parameterised, not fixed at 1 |
| Relationship to `n_trials = 34` | **Preserved exactly.** Same 34 trials, re-measured at a different hold convention. `deflated_sr`'s same-population requirement is satisfied |
| Spends an unspent lockbox? | **Depends entirely on the window — see §2.** Train slice: no. Full corpus: yes, under the frozen rule |

### Option B — dispersion from collecting forward families only

**Excluded by already-frozen constraints, not by preference:**

1. **Contradicts U1**, which is *resolved* and out of scope here: the population
   is the 34 library Sharpes, not the collecting cohort.
2. **Breaks `deflated_sr`'s same-population requirement**: the dispersion would
   span ≤ 7 families against a denominator of 34. Repairing that requires
   changing `n_trials`, which is frozen and which the selection explicitly
   forbids ("Do not silently redefine the denominator or n_trials").
3. **Not computable as stated**: only 7 of the 34 collect forward and only 5 have
   any closed trade.

### Option C — another frozen-evidence interpretation

**None found.** A dispersion computed from *forward returns of the 34* is
impossible (27 never collect forward). No other construction is supported by any
frozen artifact.

**FDB-2 therefore resolves to A on frozen evidence.** The remaining open value is
**not the interpretation but the window**, carried below as FDB-2a.

---

## 2. FDB-1 — lockbox posture and consequence

### 2.1 Exact posture

| family | library entry | lockbox window | spent |
|---|---|---|---|
| `h6` | H6 Funding x Vol x Regime | 2025-06-01 … 2026-05-31 | **True (spent)** |
| **`g1_trend_regime_ma_cross`** | **Trend Regime (MA cross)** | 2025-06-01 … 2026-05-31 | **False — UNSPENT** |
| **`g2_funding_level`** | **Funding Level** | 2025-06-01 … 2026-05-31 | **False — UNSPENT** |
| `ivrv_vrp_spread` | **not a library member** | 2025-06-01 … 2026-05-31 | False — UNSPENT |
| `g1_market_regime_sma200` | Market Regime (SMA200 slope) | none held | — |
| `g3_momentum_tsmom` | Momentum (TSMOM) | none held | — |
| `h5_vol_regime` | H5 Volatility Regimes | none held | — |
| `g3_atr_expansion` | ATR Expansion | **no `lockbox_status` key at all** | unknown |

**Two of the 34 library signals hold unspent lockboxes**: *Trend Regime (MA
cross)* and *Funding Level*. `ivrv` also holds one but is **not** among the 34
(CLAUDE.md §16: the first registry entry *outside* the original Phase B seeding),
so it is irrelevant to the dispersion.

### 2.2 What the precedent actually requires — resolved

The ambiguity flagged at FB-10b-I is settled by `g2_funding_level`'s own
pre-registration:

> *"Not spent by this signal. All gating numbers in evidence_basis are
> train-slice (2021-01-01..2025-05-31). The pinned battery run's full-sample
> Sharpe **spans the lockbox and is recorded for transparency only, never used
> for gating**. The lockbox remains the final pre-promotion OOS test."*

and by `ivrv`: *"must remain unspent for **interim gating**"*; and CLAUDE.md §16
H-IVRV, where the full-sample Sharpe **0.329 is quoted** yet declined.

**The frozen rule is therefore: a lockbox-spanning statistic may EXIST and be
recorded for transparency. It is SPENT when used for gating.** Computation alone
does not spend it; gating use does.

### 2.3 Does a forward-validation dispersion necessarily spend an unspent lockbox?

**No — it depends on the window, and the frozen rule decides each case:**

| window | spends? | reasoning |
|---|---|---|
| **Train slice** 2021-01-01 … 2025-05-31 | **No** | no lockbox-window data enters the computation at all |
| **Full corpus** 2021-01-01 … 2026-05-31 | **Yes** | `sr_trials_std` sets `sr_star`, which gates DSR at the Validated bar. That is precisely *"used for gating"*. Lockbox-window returns of *Trend Regime (MA cross)* and *Funding Level* would enter a gating computation |

**A sharper consequence of the full-corpus option**, worth stating explicitly:
both affected signals are themselves forward families awaiting validation. Their
own sealed out-of-sample data would contribute to the benchmark against which
they are later judged — the lockbox's purpose inverted.

**Is new governance authorization required?** **Yes, if and only if the
full-corpus window is chosen.** The train-slice window requires none.

---

## 3. FDB-8 — the four unspecified DSR minimums

### 3.1 The four families

`ivrv_vrp_spread`, `g2_funding_level`, `g1_trend_regime_ma_cross`,
`g3_atr_expansion` — none states a DSR compute-minimum. (Stated minima exist only
for `h5_vol_regime` 15, `g3_momentum_tsmom` 15, `g1_market_regime_sma200` 10.)

### 3.2 Can a single common minimum be legitimately derived? **No.**

The only numbers in frozen evidence are:

- **10 / 15** — other families' DSR minima. **FR-5 forbids borrowing.**
- **26 / 30** — FB-7 `minimum_trades` observation floors. A **different**
  minimum, which FR-5 says must not be conflated, and which the selection says
  must not be silently imported.

**No frozen artifact supports any particular value for these four.** Any value is
new governance, not a derivation.

### 3.3 Evidence-supported candidate interpretations

| | interpretation | consequence | individual pre-registration amendment? |
|---|---|---|---|
| **8-i** | each family receives its own value by pre-registration amendment | maximal per-family authority; preserves the existing per-family pattern | **Yes — 4 amendments**, each re-hashing under the A2a convention and requiring re-registration |
| **8-ii** | one common minimum frozen in a governance record, applying **only** to families that state none | no pre-registration touched; creates a **second source of truth** for a per-family parameter | **No** |
| **8-iii** | leave unamended | the four are **permanently excluded** from DSR/BH and therefore from Validated. Status quo, but by omission rather than decision | **No** |

### 3.4 Can a governance amendment freeze the values without changing methodology?

**Yes.** A minimum-n is an **eligibility parameter**, not a statistical method: it
governs *when* DSR may be computed, never *how*. `governance.py` is untouched
under all three interpretations, and `n >= 5` remains solely the computability
guard in every case.

---

## 4. HUMAN DECISION MATRIX

| Decision | Exact unresolved value | Evidence-supported options | Irreversible consequence | Required governance action |
|---|---|---|---|---|
| **FDB-2** | *(interpretation)* | **RESOLVED — Option A.** B excluded by U1 + the `n_trials` freeze; no C exists | — | record A in the contract |
| **FDB-2a** | **the recomputation window** | **(a)** train slice 2021-01-01 … 2025-05-31 · **(b)** full corpus 2021-01-01 … 2026-05-31 | **(a)** none — no lockbox touched. **(b)** **permanently spends two unspent lockboxes** (*Trend Regime (MA cross)*, *Funding Level*), and their sealed data enters the benchmark that later judges them | **(a)** state the window in the contract. **(b)** additionally a formal authorization to spend both lockboxes, and amendment of both `lockbox_status` records to `spent: true` |
| **FDB-1** | *(rule)* | **RESOLVED** — existence ≠ spending; **gating use = spending** | none by itself | record the rule in the contract |
| **FDB-8** | **the four minimum values** | **8-i** per-family amendment · **8-ii** one common frozen constant · **8-iii** leave unamended | **8-i/8-ii** the chosen values gate every future DSR for those families. **8-iii** permanent exclusion from Validated | **8-i** 4 pre-registration amendments + re-hash. **8-ii** new frozen constant in a governance record. **8-iii** record the exclusion explicitly |
| **FDB-8 values** | **the numbers themselves** | **none derivable** — 10/15 forbidden (FR-5 borrowing), 26/30 forbidden (different minimum, no silent import) | any value chosen becomes permanently binding | human-supplied values |

---

## 5. Status

- **FDB-2 interpretation: resolved.**
- **FDB-1 rule: resolved.**
- **Outstanding: FDB-2a (window) and FDB-8 (four values, plus which of 8-i/ii/iii).**

FB-10b implementation remains **not authorized**. Nothing was computed, no
lockbox was read or altered, and no value was selected by inference or
preference.

**STOP.**

---

## 6. Trail

| Gate | Outcome |
|---|---|
| FB-10b-H | Decision matrix; twelve options |
| FB-10b-I | FDB-1…FDB-12 drafted; three values outstanding |
| **FB-10b-J** | **This record. FDB-2 interpretation and the lockbox rule RESOLVED; FDB-2a window and FDB-8 values outstanding** |
