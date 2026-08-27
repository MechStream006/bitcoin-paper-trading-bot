# C2 — Jump vs Diffusive Decomposition: A8 Decision Record

**Date: 2026-08-27 · U1–U5 RESOLVED from the primary source · Direction BLOCKED**

Written before any performance computation. No Sharpe, DSR, BH-p, backtest,
optimisation or return statistic appears in this gate.

**Independence from C3.** C3 is closed after A7 and nothing from it is used here.
No convention below is chosen because C3 failed, because C3's inverse would have
succeeded, or because C3 used it. The one place C3 is mentioned is §7, where its
result is the *reason for excluding* an option — not a reason for selecting one.

---

## 1. Source and provenance

**Barndorff-Nielsen, O.E. and Shephard, N. (2006), "Econometrics of Testing for
Jumps in Financial Economics Using Bipower Variation", *Journal of Financial
Econometrics* 4(1), 1–30.**

| | |
|---|---|
| Retrieved | `https://public.econ.duke.edu/~get/browse/courses/883/Spr15/COURSE-MATERIALS/Z_Papers/BNSJFEC2006.pdf` |
| SHA-256 | `460c77d5…` *(A2 fetch; see §9 note)* |
| Extraction | content streams inflated; math glyph byte-codes decoded and cross-verified against contexts where the mathematics fixes the meaning |

Glyph map established and verified in this gate:

| Code | Meaning | Fixing context |
|---|---|---|
| `\x04` | `−` | `m\x0421` = μ₁⁻², `d\x041` = δ⁻¹ |
| `\x74` (`\274`) | `=` | throughout |
| `\275 … \003` | `[ … ]` | `fYdg\275 1,1 \003 t` = `{Y_δ}^[1,1]_t` |
| `\360 … \336` | `( … )` | `LN\360 0,1 \336` = `N(0,1)` |
| `X` | `Σ` | summation operator in the math font |
| `\210` / `\230` | tilde / check accents | `ṽ` = RV, `v̌` = BV (see §3) |

A2 could read this paper's prose but not its mathematics. A3 developed the
byte-code decoding while resolving C3's U6; applying it here is what closed
U1–U5. That is the only methodological carry-over from the C3 track.

## 2. U1–U5 — final decisions

| | Question | Decision | Basis |
|---|---|---|---|
| **U1** | staggered vs adjacent BV | **ADJACENT** | **[SOURCE]** |
| **U2** | `N/(N−2)` correction | **NOT APPLIED** | **[SOURCE]** — absent from BNS |
| **U3** | quarticity estimator | **realized quadpower**, adjacent 4-term | **[SOURCE]** |
| **U4** | sampling frequency | **5-minute (n = 288/day)** | **[PLATFORM-NEW]**, source-anchored |
| **U5** | jump test | **adjusted ratio, BNS eq. (14)** | **[SOURCE]** — the authors' own recommendation |

### U1 — ADJACENT, and the secondary source was wrong for our purpose

BNS state it in words, which removes all doubt:

> *"The ratio {Y_δ}^[1,1]_t / [Y_δ]_t is asymptotically equivalent to the
> realized correlation between **|y_{j−1}| and |y_j|**."*

Adjacent returns. The `Σ_{i=3..N} |r_{i−2}|·|r_i|` form quoted at A2 came from a
**secondary** source (Baruník & Vacha), where staggering is a deliberate
market-microstructure-noise correction introduced by later authors — not BNS's
definition. A2 flagged this as a material ambiguity; it is now closed against the
primary text, and the secondary reading is rejected.

### U2 — no finite-sample correction

BNS define the estimators as plain sums with no `N/(N−2)` factor. The correction
in the secondary source is a later modification. **Not applied.**

### U3 — realized quadpower variation

Verbatim from the paper's own justification:

> *"…it is preferable to have an estimator of integrated quarticity that is also
> consistent under the alternative BSMJ. This is straightforward using realized
> quadpower variation."*

### U4 — the one genuine platform choice, and its cost

**BNS prescribe no frequency.** Their empirical work uses 5-minute Olsen FX
midquotes (n = 288/day) and their Monte Carlo tabulates n ∈ {12, 24, 72, 288,
1152}, concluding the test is reliable *"if δ is moderately small"*.

Critically, the paper states its own scope limit:

> *"…ignoring the possible impact of **market microstructure effects**."*

That is the whole reason later authors introduced staggering. So a 1-minute grid
would combine BNS's *unstaggered* estimator with a noise regime BNS explicitly
brackets out — taking the source's fidelity while discarding the condition under
which it was derived.

**Frozen: 5-minute, n = 288 per complete UTC day**, aggregated from our 1m
corpus. It matches the source's own empirical setting, sits inside its
Monte Carlo range, and materially reduces the noise the source does not model.
This is *not* inherited from any other candidate; it is chosen against BNS's
stated scope.

### U5 — adjusted ratio test, and the authors choose it

> *"Monte Carlo results suggest an **adjusted ratio jump statistic can be
> reliably used** to test for jumps if δ is moderately small and the test is
> carried out over relatively short periods, **such as a day**."*

The paper's own conclusion, and "such as a day" is exactly our aggregation unit.

## 3. Exact mathematical specification [SOURCE]

Per complete UTC day, over n intraday returns `y_1 … y_n`:

```
RV      ṽ = Σ_{j=1..n}  y_j²

BV      v̌ = Σ_{j=2..n}  |y_{j−1}| · |y_j|                     [ADJACENT]

QPV     q̌ = δ⁻¹ Σ_{j=4..n} |y_{j−3}||y_{j−2}||y_{j−1}||y_j|

constants
        μ₁   = E|Z| = √(2/π)          (Z ~ N(0,1))
        μ₁⁻² = π/2
        μ₁²  ≈ 0.6366                 → BV/RV converges to this under no jumps;
                                        "estimates below μ₁² provide evidence for jumps"
        θ    = π²/4 + π − 3 ≈ 2.6090  (recovered from the paper's Appendix)

ADJUSTED RATIO JUMP TEST — BNS equation (14)

              δ^(−1/2) · ( μ₁⁻² · v̌/ṽ  −  1 )
   Ĵ  =  ────────────────────────────────────────────      →L  N(0,1)
            sqrt(  θ · max(1,  q̌ / v̌² )  )

   Reject the null of NO JUMPS if Ĵ is significantly NEGATIVE (one-sided).
```

The `max(1, ·)` floor **is** the adjustment that distinguishes the adjusted
ratio from the plain ratio test (eq. 13). One-sidedness is the paper's own.

## 4. Data and aggregation contract [PLATFORM]

| Item | Value | Tag |
|---|---|---|
| Instrument | Binance BTCUSDT spot | [PLATFORM-CONV] |
| Base bars | 1-minute, `research/data/raw/spot/BTCUSDT` | [PLATFORM-CONV] |
| Sampling | **5-minute**, built from 1m closes | **[PLATFORM-NEW]** (§U4) |
| Day boundary | UTC calendar day | [PLATFORM-CONV] |
| Completeness | day requires exactly **1440** 1m bars → exactly **288** 5m bars | [PLATFORM-CONV] 6B.1 |
| Returns | **log** first differences; cross-day return **excluded** → **287** within-day 5m returns | **[PLATFORM-NEW]** |
| Incomplete day | dropped entirely; never partially computed | [PLATFORM-CONV] |
| δ | 1/288 (day normalised to [0,1], as BNS do) | [SOURCE] |

**Fail-closed behaviour** — all [PLATFORM-CONV], inherited from 6B.1/6B.3:

| Condition | Behaviour |
|---|---|
| Insufficient history | `DataUnavailableError` → transient skip, never a decision |
| Incomplete day (≠1440 bars) | day dropped; if it is the requested day, raise |
| `RV == 0` | statistic undefined → **NaN → FLAT**, never coerced to a decision |
| `BV == 0` | denominator undefined → NaN → FLAT |
| Any NaN input | → FLAT; missing data is never a decision |
| Lockbox / immutable-gap date | refused unconditionally (6B.3 policy) |

## 5. Direction — **BLOCKED**, and this is the finding of A8

**The source supports no direction whatsoever.** BNS is a test-statistic paper;
it defines no signal, no threshold, no position and no economic claim about
returns. So direction would be a separate platform decision.

**But there is a prior, structural problem, and it is why I am not freezing a
direction here.**

`Ĵ` — and the jump ratio generally — is **unsigned**. It measures *what fraction
of a day's variance was discontinuous*, not *which way price went*. A strongly
jump-dominated day is equally consistent with a jump up or a jump down. The
platform's signal contract requires `{−1, 0, +1}`.

Bridging an unsigned measure to a directional position needs an extra ingredient
the source does not supply. The available bridges, and why each is a problem:

| Bridge | Problem |
|---|---|
| `Ĵ` × sign(daily return) | The directional content then comes from the return sign, i.e. **momentum**. Risks being a reformulation of `sig_momentum` / `sig_trend_*`, which is exactly the clone failure mode gate 6A.2 already demonstrated |
| Signed jump variation (attribute `RV − BV` by return sign) | Signed intraday variance decomposition — **the same ground C3 occupied**. Adopting it now, immediately after C3's train-slice evaluation, would be building on knowledge of that result |
| `Ĵ` as an unsigned regime **gate** on an existing signal | Defensible and arguably the natural use — but it is a *conditioning feature*, not a standalone candidate, and the forward collector has no gate-only contract |

**I am not choosing among these.** Each is a materially different candidate with
different independence properties, and picking one silently would be precisely
the researcher discretion this gate sequence exists to prevent.

## 6. Clone / admissibility screen — not performed, and why

The frozen clone methodology (`analyze_mtf_independence` Test 1 + `vif_table`)
correlates **positions**. Positions require a direction. Direction is blocked per
§5. **The screen is therefore not possible at A8** — it is not being skipped for
convenience, it is undefined until §5 is resolved. Performing it would require
inventing the very bridge I am declining to invent.

## 7. Independence from C3 — explicit

- No C2 convention was chosen because of any C3 outcome. U1/U2/U3/U5 are the
  source's; U4 is chosen against BNS's stated microstructure scope limit.
- **U4 deliberately differs from C3**: C3 used 1-minute, C2 freezes 5-minute.
  Had C2 simply inherited C3's grid, that would have been convenience, not
  reasoning.
- C3's result is used in exactly one place — §5, to *exclude* the signed-jump
  bridge as contaminated. Excluding an option because prior work touched that
  ground is the opposite of selecting on outcomes.
- C2 measures variance **composition** (continuous vs discontinuous); C3 measured
  variance **sign asymmetry**. Different quantities, and C2's is unsigned.

## 8. Blockers before implementation

1. **BLOCKER — direction/bridge (§5).** Must be resolved by explicit decision.
   It is not a parameter choice; it determines *which candidate C2 is*.
2. **Open — microstructure at the chosen grid.** 5-minute mitigates but does not
   eliminate the effect BNS exclude. If a future gate wants 1-minute, the
   staggered estimator should be reconsidered *together* with it, since the two
   were designed as a pair. Changing one alone is not source-faithful.
3. **Note — significance threshold.** BNS give a one-sided test but prescribe no
   operating α for a trading context. Any threshold is [PLATFORM-NEW] and must be
   declared before evaluation, not tuned.

## 9. Provenance note, stated rather than glossed

The SHA-256 recorded in §1 is from the A2 fetch of the same URL. The bytes used
for this gate's extraction are the locally cached copy of that fetch. A
re-download was **not** performed at A8, so §1's hash attests what A2 retrieved,
not an independent re-verification today. Any implementation gate should
re-fetch and confirm the hash before pinning it into a pre-registration.

## 10. Governance

| | |
|---|---|
| Registry / ledger | **untouched** — C2 is not registered |
| `thresholds.yaml`, `collection_config.yaml`, sealed artifacts | **unmodified** |
| Production code | **unmodified** — nothing implemented |
| Lockbox 2025-06-01..2026-05-31 | **SEALED — never read by any C2 gate** |
| Performance statistics | **none computed at A8** |
| `n_trials = 34` | unspent, unchanged |

---

**Status: C2 specification is source-complete and frozen; C2 as a directional
candidate is BLOCKED pending §5.**
