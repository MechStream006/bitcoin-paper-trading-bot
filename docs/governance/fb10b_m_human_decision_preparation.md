# FB-10b-M — Human Decision Preparation (FDB-8)

**Date: 2026-08-31 (UTC) · Gate FB-10b-M · Read-only · No implementation, no
DSR/BH computation, no production write, no lockbox access.**

FDB-8 is the only unresolved FB-10b question. Four options are prepared below for
explicit decision. **No numerical value is recommended, inferred, or borrowed.**

---

## 0. Frozen position

| family | observation floor | frozen DSR minimum |
|---|---|---|
| `ivrv_vrp_spread` | 30 | **NONE** |
| `g1_trend_regime_ma_cross` | 30 | **NONE** |
| `g3_atr_expansion` | **26** | **NONE** |
| `g2_funding_level` | 30 | **NONE** |

Families with frozen DSR minima — `h5_vol_regime` 15, `g3_momentum_tsmom` 15,
`g1_market_regime_sma200` 10 — **remain authoritative and are not touched by any
option below** (FR-4).

### A linkage that applies to Options A, B and C

All four families carry a `preregistration.hash` in **`registry.yaml`** (the
A2a/A2b convention: LF-normalised SHA-256 of the JSON at `path`). Amending any
pre-registration therefore **forces a matching `registry.yaml` update**.
`observation_floors.resolve_pinned_prereg()` verifies that hash, and FB-12g
embedded floor evaluation into every snapshot — so a stale hash would **break
`forward_snapshot` production**, not merely fail a check.

---

## 1. The four options

### Properties common to **all four**

| property | value |
|---|---|
| Effect on the frozen **34-signal population** | **None.** The dispersion population is `sr_trials_std`'s concern; a minimum-n never touches it |
| Effect on **`n_trials = 34`** | **None** |
| Effect on **U2-B** forward trade-return methodology | **None.** Basis, `hold_days = 7`, and the frozen train-slice window are untouched |
| **`governance.py`** methodology change | **None** under every option. It remains the sole statistical authority, unmodified |
| **Lockbox implications** | **None.** FDB-2a settled on the non-lockbox train slice; no option here reaches lockbox data |
| **New statistical assumption** | **None** in the distributional sense — the assumptions live in `psr`'s skew/kurtosis adjustment. Each option does encode a **sufficiency judgment** (§1.5) |

### 1.1 Option A — coincident values (30 / 30 / 26 / 30)

| | |
|---|---|
| **Governance meaning** | Each family's DSR minimum is set to the number its own observation floor already carries, with a formal statement that the two remain **separate criteria that coincide numerically** |
| **FR-5 compatibility** | **Conditional.** FR-5 bars treating the observation floor *as* the DSR minimum. It does not bar the two governance minima sharing a value. Compatible **only** with the explicit non-conflation statement; adopting the numbers because they are available is the barred conflation |
| **Pre-registration changes** | Four amendments if recorded there (single source of truth), **or** a governance record alone (creates a second source of truth for a per-family parameter) |
| **Hashes** | If pre-registered: 4 re-hashes **+ 4 `registry.yaml` hash updates** (see §0) |
| **New frozen constant** | **No new number** — but a new *binding*: that value becomes the family's DSR minimum |

### 1.2 Option B — one common constant `N`

| | |
|---|---|
| **Governance meaning** | A single DSR minimum `N`, applying **only** to families that state none. Existing 15 / 15 / 10 remain authoritative |
| **FR-5 compatibility** | **Compatible**, provided `N` is not 10 or 15 (borrowing), not `n >= 5`, and not derived from current sample sizes |
| **Pre-registration changes** | Four amendments if pre-registered, **or** one governance record |
| **Hashes** | If pre-registered: 4 re-hashes + 4 `registry.yaml` updates |
| **New frozen constant** | **Yes** — one shared value |
| **Note** | One value would span four heterogeneous families: one `edge` role (`g3_atr_expansion`) and three `feature`, with differing trade cadences and one differing floor (26) |

### 1.3 Option C — four family-specific values

| | |
|---|---|
| **Governance meaning** | Each family receives an independently specified DSR minimum. Maximal fidelity to the existing per-family pattern that FR-4 makes authoritative |
| **FR-5 compatibility** | **Compatible**, same provisos per value as Option B |
| **Pre-registration changes** | **Four amendments, necessarily** — a per-family value belongs in the per-family document |
| **Hashes** | 4 re-hashes + 4 `registry.yaml` updates |
| **New frozen constant** | **Yes** — up to four values |

### 1.4 Option D — permanent exclusion (one or more families)

| | |
|---|---|
| **Governance meaning** | The family is permanently outside DSR/BH validation |
| **FR-5 compatibility** | **Compatible** |
| **Pre-registration changes** | None strictly required; the exclusion must be **recorded explicitly** so it is a decision rather than an omission |
| **Hashes** | **None** if recorded in a governance record only |
| **New frozen constant** | **No** |
| **Consequence** | Under **R1**, a NULL DSR/BH is *"a hard failure … no silent bypass"* — so exclusion from DSR/BH is **permanent exclusion from Validated**. `g2_funding_level`'s own pre-registration already states this outcome: *"Validated is unreachable until both are computed."* |

### 1.5 Where a judgment is actually being made

No option introduces a distributional assumption. Each encodes a **sufficiency
judgment** about when a DSR estimate is trustworthy, and they differ in where
that judgment is smuggled or stated:

- **A** — asserts that the sample adequate for *admissibility* is also adequate
  for *DSR estimation*. Substantive, and invisible unless stated.
- **B** — one sufficiency judgment applied across four heterogeneous families.
- **C** — four explicit per-family judgments.
- **D** — asserts these families are not statistically validatable.

---

## 2. Recommendation-neutral table

| Option | What I must decide | Amendments required | Methodology change? | Reversible? | Consequence |
|---|---|---|---|---|---|
| **A** | whether the coincident values are adopted, and that the non-conflation statement is made | 4 pre-registrations + 4 `registry.yaml` hashes *(or a governance record, at the cost of a second source of truth)* | **No** | Governance-reversible **until** a DSR is computed under it; the resulting ledger rows are permanent | No new number. Creates a standing precedent that an observation floor may serve as a DSR minimum when explicitly stated |
| **B** | the single value `N` | 4 pre-registrations + 4 `registry.yaml` hashes *(or one governance record)* | **No** | Same | One constant governs four heterogeneous families; sets the precedent for every future family registered without a minimum |
| **C** | four independent values | 4 pre-registrations + 4 `registry.yaml` hashes *(mandatory)* | **No** | Same | Preserves the per-family pattern; four separate judgments to defend |
| **D** | which families (one or more) are excluded | none mandatory; explicit exclusion record | **No** | **Most reversible** — nothing is written; a minimum may be added later | Each excluded family is **permanently unable to reach Validated** while excluded |

**Reversibility, stated precisely:** every option is governance-reversible while
no DSR has been computed under it. Once a DSR/BH result is stored, that result is
in an append-only ledger and was gated by the then-current minimum — the value
becomes part of that result's permanent provenance even if later changed.

---

## 3. Smallest set of human decisions to make FB-10b implementation-authorizable

**One open decision, plus two confirmations.**

1. **DECISION — FDB-8.** For each of the four families: choose **A**, **B**, **C**
   or **D**; and if **B** or **C**, supply the value(s). *(This is the only
   genuinely unresolved question in FB-10b.)*

2. **CONFIRMATION — FDB-5 battery membership.** FB-10b-I *derived* the rule from
   your U4 selection rather than receiving it: a family is a member at an
   `as_of` iff it holds a frozen DSR minimum, meets it, and satisfies `n >= 5`.
   Confirm or amend.

3. **ACT — adopt the contract.** Freeze FB-10b-I's FDB-1 … FDB-12 as a governance
   record, incorporating what is already settled: FDB-2 = Option A
   (re-measure the same 34 at `hold_days = 7`); FDB-2a = the frozen train slice
   `2021-01-01 … 2025-05-31`; the lockbox rule (existence ≠ spending, gating use
   = spending); and the FDB-8 outcome from (1).

Nothing else is outstanding. Sample-size availability remains a separate
**execution** condition — no family currently meets any candidate minimum — and
is not a governance question.

**STOP.**

---

## 4. Trail

| Gate | Outcome |
|---|---|
| FB-10b-K | FDB-2a resolved to the frozen train slice |
| FB-10b-L | Per-family FDB-8 matrix; both structural questions answered |
| **FB-10b-M** | **This record. Four options prepared; one decision, two confirmations, remain** |
