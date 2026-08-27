# C3 — Realized Semivariance Asymmetry: FINAL PRE-REGISTRATION SPECIFICATION

**Gate A5 · Status: SPECIFICATION FROZEN · NOT admitted, NOT implemented, NOT collecting**

This document freezes *what C3 is*. It is deliberately **not** an admission
dossier: unlike `pre_registration_g2_funding_level.json`, C3 has **no evidence
basis whatsoever** — no battery run, no Sharpe, no DSR, no BH-p, no OOS. The
only quantitative work done to date is a structural clone screen (Gate A4),
which measures overlap and says nothing about value.

Trail: A1 (universe) → A2 (source extraction) → A3 (U6 resolution + draft) →
A4 (direction frozen + clone screen) → **A5 (this)** → A6 (implementation).

---

## 1. Source vs platform — the line, stated once and enforced throughout

Every parameter below is tagged:

- **[SOURCE]** — stated by Barndorff-Nielsen, Kinnebrock & Shephard (2008).
- **[PLATFORM-CONV]** — fixed by an existing, already-registered project
  convention. Not a new choice.
- **[PLATFORM-NEW]** — a new choice this project makes and owns. BNKS does
  **not** support it and it must never be presented as source-derived.

There are exactly **four** `[PLATFORM-NEW]` items. They are listed together in
§6 so they cannot be lost in the detail.

## 2. What the source actually says

Primary source: **Barndorff-Nielsen, O.E., Kinnebrock, S. and Shephard, N.
(2008), "Measuring downside risk — realised semivariance."**

| | |
|---|---|
| Retrieved | `https://public.econ.duke.edu/~get/browse/courses/201/spr08/DOWNLOADS/New_Methods_and_JumpTests/BNKS-semivariance-2008.pdf` |
| Bytes | 251,638 |
| SHA-256 | `460c77d59daaaaa80ccc2222174b709c9a6d7b9fb3be7184f009275cd5f8f318` |

**[SOURCE]** definitions, recovered at A2/A3 by inflating the PDF content
streams and decoding the math glyph codes:

```
Day normalised to [0,1];  0 = t_0 < t_1 < … < t_n = 1
      "the times at which trade or quote prices are available"

RV  = Σ_{j : t_j ≤ 1}  (Y_{t_j} − Y_{t_{j−1}})²

RS- = Σ_{j : t_j ≤ 1}  (Y_{t_j} − Y_{t_{j−1}})² · 1{ Y_{t_j} − Y_{t_{j−1}} ≤ 0 }
RS+ = Σ_{j : t_j ≤ 1}  (Y_{t_j} − Y_{t_{j−1}})² · 1{ Y_{t_j} − Y_{t_{j−1}} ≥ 0 }

  1_y is "the indicator function taking the value 1 [if] the argument y is true"

QV:    [Y]_t = ∫₀ᵗ σ²_s ds + Σ_{s ≤ t} (ΔY_s)²
Limit: RS- →p ½ ∫₀ᵗ σ²_s ds + Σ_{s ≤ t} (ΔY_s)² 1{ΔY_s ≤ 0}
```

**How `≤` / `≥` were established (U6, closed).** The A2 extractor decoded to
latin-1 and dropped the operator glyphs. A3 dumped the raw font byte codes and
verified them against contexts where mathematics fixes the meaning:

| Code | Verified as | Fixing context |
|---|---|---|
| `\x00` | `−` | `p−lim`, `Y_t − Y_{t−}` |
| `\x14` | `≤` | `Σ_{s ≤ t}` (sum over jump times), `t_j ≤ t` |
| `\x15` | `≥` | `t ≥ 0` in `Y_t = ∫a ds + ∫σ dW_s, t ≥ 0` |

They also occur as a complementary pair in the paper's own functional
`g(x) = ( x²1{x ≥ 0} , x²1{x ≤ 0} )`, which settles the assignment.

**The ambiguity is numerically inert.** The summand is `r²`, so the only
overlapping point (`r = 0`) contributes exactly zero to either sum. Hence
`RS- + RS+ = RV` **exactly** — verified empirically at A4 with max absolute
error `0.000e+00` over 2,031 days. Choosing `<` instead of `≤` could not change
any computed value.

**What the source does NOT say** (searched; zero hits):

| Missing | Evidence |
|---|---|
| Log vs arithmetic price | `logprice`, `logarithm`, `efficientprice` → 0 hits |
| Any sampling frequency | `minute`, `5minute`, `sparse`, `subsampl` → 0 hits; it uses *all available tick times* |
| Any trading rule | `tradingrule`, `portfolio`, `threshold` → none. Abstract: *"We propose a new measure of risk… The theory of these new measures is spelt out."* |
| Any direction of effect | none |
| Any scaling constant | none — unlike bipower variation, RS carries no `μ` factor |

**C3 is therefore a measurement, not a strategy.** Everything that turns it
into a position is this project's own, per §6.

## 3. Frozen parameters

### 3.1 Input data

| Parameter | Value | Tag |
|---|---|---|
| Instrument | Binance BTCUSDT **spot** | [PLATFORM-CONV] existing corpus |
| Store | `research/data/raw/spot/BTCUSDT`, `year=YYYY/month=MM/data.parquet` | [PLATFORM-CONV] |
| Sampling | **1-minute bars** | **[PLATFORM-NEW]** — source prescribes none |
| Day boundary | **UTC calendar day** | [PLATFORM-CONV] |
| Completeness | day admitted only if it holds **exactly 1,440** 1m bars | [PLATFORM-CONV] `MIN_DAILY_BARS`, gate 6B.1 |
| Incomplete days | **dropped**, never partially computed | [PLATFORM-CONV] |
| Return definition | **log first differences**, `r = ln(C_i) − ln(C_{i−1})` | **[PLATFORM-NEW]** — resolves U6b; BNKS does not state it |
| Day-boundary return | **cross-day close→open return EXCLUDED** | **[PLATFORM-NEW]** — see §3.2 |
| Returns per day | **1,439** within-day returns from 1,440 bars | consequence of the two rows above |

### 3.2 The day-boundary rule (new at A5)

**Surfaced during the A4 clone screen and not present in the A3 draft.** It is
recorded here rather than left implicit.

`RS-` and `RS+` are computed from **within-day returns only**. The return
spanning the boundary between one UTC day's final bar and the next day's first
bar is **excluded** from both days.

*Rationale, and its honest limit:* BNKS normalise each day to `[0,1]` with
`t_0 = 0` and `t_n = 1` and sum over `t_j ≤ 1`, which is consistent with
within-day returns. But BTC trades 24/7, so there is no session break and no
overnight gap in the equities sense; the exclusion is therefore a **choice**,
not a necessity, and is tagged `[PLATFORM-NEW]`. Including the boundary return
would be a different, also-defensible specification. It is excluded here for
one reason only: it keeps the measure a pure *within-day* variance signature,
so a day's value cannot be dominated by a single cross-boundary move.

### 3.3 Measure and transformation

| Parameter | Value | Tag |
|---|---|---|
| `RS-` | `Σ r² · 1{r ≤ 0}` over the 1,439 within-day returns | [SOURCE] |
| `RS+` | `Σ r² · 1{r ≥ 0}` over the same | [SOURCE] |
| `RV` | `RS- + RS+` (identity, exact) | [SOURCE] |
| `SJ` | `(RS+ − RS-) / RV`, range `[−1, +1]` | **[PLATFORM-NEW]** |
| `RV = 0` | `SJ := NaN` → position 0 | [PLATFORM-CONV] fail-safe flat |

`SJ` is scale-free by construction: it carries *which side* the variance came
from, not how much there was. That is the whole reason it is preferred to `RS-`
alone, which is a volatility-**level** measure and would structurally duplicate
the registered vol families.

**T1 ≡ T2 (no free choice here).** Because `RS- + RS+ = RV` exactly,
`SJ = 1 − 2·(RS-/RV)`. The "signed asymmetry" and "downside share" formulations
are affine transforms of one another, so a trailing-quantile tercile gate on
either induces the **identical partition**. This is recorded so a future reader
does not mistake it for a discretionary choice that was made.

### 3.4 Rolling normalisation

| Parameter | Value | Tag |
|---|---|---|
| Window `RW` | **252** completed days | [PLATFORM-CONV] G2, IVRV |
| `min_periods` `MINP` | **126** | [PLATFORM-CONV] G2, IVRV |
| Lower percentile | **33** | [PLATFORM-CONV] |
| Upper percentile | **67** (`100 − PCTILE`) | [PLATFORM-CONV] |
| Quantile basis | trailing, completed days only, inclusive of the current day | [PLATFORM-CONV] |
| Warm-up `MIN_HISTORY` | **253** daily rows (`RW + 1`) | [PLATFORM-CONV] G2, IVRV |

### 3.5 Position mapping — FROZEN at A4

```
SJ ≤ 33rd percentile of trailing 252 days   ->  +1  LONG
SJ ≥ 67th percentile of trailing 252 days   ->  -1  SHORT
otherwise                                   ->   0  FLAT
SJ NaN, or either quantile NaN              ->   0  FLAT
```

**Direction: CONTRARIAN.** [PLATFORM-NEW — frozen at A4]

Full hypothesis, rejected alternatives and falsification criterion:
`docs/research/c3_semivariance_a4_direction_decision.md`, frozen before any
computation and not revisable by anything observed downstream.

### 3.6 Trade lifecycle

| Parameter | Value | Tag |
|---|---|---|
| `hold_days` | **7** calendar days | [PLATFORM-CONV] all six collectors |
| Exit | scheduled at `entry_date + 7d`; no early exit | [PLATFORM-CONV] |
| Entry price | spot close of `run_date` | [PLATFORM-CONV] |
| Exit price | spot close of `planned_exit_date` | [PLATFORM-CONV] |
| Trade counting | one entry→exit pair = one trade | [PLATFORM-CONV] |
| Concurrency | at most one open trade per signal | [PLATFORM-CONV] |
| `cost_bps` | **15**, charged **once per completed trade** | [PLATFORM-CONV] registered label spec `57ce016e51a0` |

The single-charge cost convention is **registered**, not incidental — Gate 6B.5
established it as a deliberate Specification distinct from the engine's
turnover model. C3 does not reopen it.

### 3.7 Integrity rules

| Rule | Value | Tag |
|---|---|---|
| No look-ahead | inputs filtered to `run_date` inclusive before computation | [PLATFORM-CONV] |
| Completed observations only | within-day sum over a finished UTC day | [SOURCE] + [PLATFORM-CONV] |
| Missing / NaN input | `DataUnavailableError` → transient skip; **never** a flat decision | [PLATFORM-CONV] gate 6B.1 (B-02) |
| Incomplete final day | rejected by the 1,440-bar guard | [PLATFORM-CONV] gate 6B.1 (B-01) |
| `run_date` policy | sealed lockbox and immutable gaps refused unconditionally | [PLATFORM-CONV] gate 6B.3 |
| Provenance | framework hash + input partition SHA-256 + row counts per observation | [PLATFORM-CONV] gate 6B.6 |
| State / recursion | **none** — each day is independent given its own 1m bars | [SOURCE] |
| Repaint | **none** | [SOURCE] |

### 3.8 Governance

| Parameter | Value |
|---|---|
| Role | **feature** (no edge claim is made or supported) |
| `n_trials` | **34 — FROZEN.** Not reopened by this candidate |
| Validated gate | `validated.feature`: DSR ≥ 0.50, BH-p ≤ 0.10, \|pairwise r\| ≤ 0.70, non-offline provenance |
| Lockbox | **2025-06-01 .. 2026-05-31 — SEALED, unspent, never read** |
| Clone methodology | `analyze_mtf_independence.py` Test 1 + `governance.vif_table` |
| `max_clone_r` | 0.90 |

## 4. Clone screen result (Gate A4)

Train slice only (1,612 rows, 2021-01-01 .. 2025-05-31). Lockbox excluded.
**No performance quantity was computed.**

```
MAX |r| = 0.0323  (vs g2_funding_level)      threshold 0.90  -> PASS
VIF = 1.008   R² on others = 0.008   unexplained = 99.2%
positions: active 971/1612 (60.2%)   long 487   short 484
```

**Interpretive limit, stated deliberately:** a near-zero correlation with
everything **cannot** distinguish "genuinely orthogonal information" from "no
information at all". Both look identical to a structural screen. Which one C3
is remains an open question that only forward evidence can answer.

**Declared and not realised:** A4 predicted C3 might share `g2_funding_level`'s
positioning-crowdedness *mechanism*. Structurally the two are nearly
independent (|r| = 0.032). Shared mechanism with unshared measurement is
recorded here because it was predicted in advance, not because the outcome was
favourable.

## 5. Decision-function SPECIFICATION (specification only — no implementation)

To be implemented at A6 as a locked, hash-pinned file. **This section is the
contract; A6 writes code to satisfy it and nothing more.**

```
MODULE   decision_function_c3_semivariance_asymmetry
CONSTANTS (all explicit; no defaults anywhere)
    MIN_DAILY_BARS   = 1440
    RW               = 252
    MINP             = 126
    PCTILE           = 33
    HOLD_DAYS        = 7
    MIN_HISTORY      = 253          # = RW + 1

INPUT
    a daily series of SJ values, chronological, index 'YYYY-MM-DD' UTC,
    one row per COMPLETE day, ascending, unique.
    Upstream (loader, A6) is responsible for:
        - selecting only days with exactly MIN_DAILY_BARS 1m bars
        - log first differences
        - EXCLUDING the cross-day return (1,439 within-day returns)
        - RS-/RS+ per §3.3 and SJ = (RS+ − RS-)/RV
        - SJ := NaN when RV == 0

COMPUTE
    lo   = SJ.rolling(RW, min_periods=min(RW, MINP)).quantile(PCTILE/100)
    hi   = SJ.rolling(RW, min_periods=min(RW, MINP)).quantile((100-PCTILE)/100)
    pos  = 0.0
    pos[SJ <= lo] = +1.0
    pos[SJ >= hi] = -1.0
    pos[SJ isna OR lo isna OR hi isna] = 0.0

OUTPUT
    pd.Series of {-1.0, 0.0, +1.0}, same index as input.

FORBIDDEN
    - any use of data dated after the evaluated day
    - any parameter not listed above
    - any default argument
    - any change to the cost convention
```

## 6. The four `[PLATFORM-NEW]` choices, collected

Anyone auditing C3 later should check exactly these, because they are the only
places this project departs from or extends the source:

1. **1-minute sampling grid** — BNKS uses all available tick times and
   prescribes no frequency.
2. **Log first differences** — BNKS says only "trade or quote prices" (U6b).
3. **Cross-day return excluded** — 1,439 within-day returns per day (§3.2).
4. **`SJ` transformation and CONTRARIAN direction** — BNKS defines no signal,
   no threshold and no direction whatsoever.

Everything else is either the source verbatim or an existing project
convention carried over unchanged.

## 7. Degrees of freedom

18 parameters: **1 from source**, **12 fixed by existing convention**,
**4 declared new (§6)**, **1 derived** (T1≡T2). Direction — the only genuinely
free parameter — was closed at A4 *before* any computation and cannot be
revised.

## 8. What must NOT be inferred from this document

- **No performance claim.** No Sharpe, DSR, BH-p, drawdown, hit rate or return
  has been computed for C3 at any gate. `evidence_basis` in the JSON is
  explicitly `NOT COMPUTED` throughout.
- **No admission.** C3 is not in `registry.yaml`, has no ledger entry, no
  `pinned_run`, and is absent from `collection_config.yaml`.
- **No lockbox use.** The sealed window has never been read by any C3 gate.
