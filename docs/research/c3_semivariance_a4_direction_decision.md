# A4 Decision Record — C3 direction, FROZEN

**Written BEFORE any outcome, return, or performance quantity was computed.**
Nothing in this record was informed by data. It is falsifiable by construction:
if the mechanism below is wrong, the signal fails, and that failure is the
correct result.

---

## 1. Frozen source specification (from A3, unchanged)

```
RS- = Σ (Y_tj − Y_tj−1)² · 1{Y_tj − Y_tj−1 ≤ 0}     BNKS (2008)
RS+ = Σ (Y_tj − Y_tj−1)² · 1{Y_tj − Y_tj−1 ≥ 0}     ≤ / ≥ resolved at A3
RV  = RS- + RS+                                      (exact; r=0 contributes 0)

SJ  = (RS+ − RS-) / RV        ∈ [−1, +1]
```

Platform: spot/BTCUSDT 1m log first differences → completed UTC days requiring
1440 bars; rolling window 252, min_periods 126, terciles 33/67; hold_days 7;
cost_bps 15 charged once; existing no-lookahead and completeness rules.

## 2. What SJ means

- **SJ → +1**: the day's realized variance is almost entirely from *upward*
  moves. The book absorbed buying pressure; longs were rewarded.
- **SJ → −1**: variance almost entirely from *downward* moves. The stress was
  borne on the long side.
- **SJ ≈ 0**: variance symmetric in sign — no directional stress signature.

SJ is scale-free: it says nothing about how volatile the day was, only about
*which side* the variance came from. This is the whole reason to prefer it over
RS- alone, which is a volatility-level measure and would duplicate the
registered vol families.

## 3. Economic hypothesis (the falsifiable claim)

**Premise, and it is one this platform has already registered rather than one I
am importing:** BTC perpetual positioning is *structurally long-skewed*.
`decision_function_g2_funding_level.py` states the mechanism in its own locked
text — "perpetual funding is the price longs pay shorts to hold the position" —
and persistently positive funding is exactly what a long-skewed book produces.

Given a structurally long-skewed book:

- **Downside-dominated variance (SJ ≪ 0) is disproportionately MECHANICAL.**
  Margin calls and liquidation cascades fall on the crowded side. Mechanical
  flow is forced, price-insensitive, and unrelated to fundamental value, so the
  displacement it causes is a candidate for reversal. A very negative SJ day is
  therefore a day on which leverage was *removed* — positioning is cleaner
  afterwards. **Expect subsequent upward drift.**

- **Upside-dominated variance (SJ ≫ 0) marks leverage ACCUMULATING.** Longs
  being rewarded in a long-skewed book draws in the marginal leveraged buyer,
  so crowding increases and the book becomes more fragile to the next shock.
  **Expect subsequent weakness.**

**Direction: CONTRARIAN on SJ.**

## 4. Frozen position mapping

```
SJ ≤ 33rd percentile of trailing 252 days  ->  +1  (LONG)
SJ ≥ 67th percentile of trailing 252 days  ->  −1  (SHORT)
otherwise                                  ->   0  (FLAT)
NaN in SJ or either quantile               ->   0
```

Terciles are trailing and computed on completed days only.

## 5. How this is falsified

The hypothesis is wrong if, on forward evidence, low-SJ days are **not**
followed by relative strength and high-SJ days **not** by relative weakness.
No revision of direction is permitted after observing any outcome. If the
signal fails, it fails as recorded here.

## 6. Declared risk — economic-axis overlap with G2 (NOT hidden)

The mechanism above is *positioning crowdedness*, which is the same economic
axis as `g2_funding_level`. They measure it by different instruments — G2 the
**price** of positioning (funding rate), C3 the **variance signature** of
positioning stress — and a structural clone screen may well show them weakly
correlated. But the shared mechanism is declared here, in advance, exactly as
G2's own |r| = 0.8187 conflict with `g6_funding_basis_composite` was declared
at registration rather than discovered at promotion.

If the clone screen shows |r| ≥ 0.90 against G2, C3 is rejected.

## 7. Considered and rejected

- **Momentum on SJ** (SJ ≪ 0 → SHORT, continuation): defensible in an
  information-driven market where downside variance signals genuine bad news.
  Rejected for BTC because the long-skewed-book premise makes the *mechanical*
  interpretation the stronger prior, and because that premise is already
  evidenced on this platform rather than assumed.
- **One-sided (LONG on low SJ, FLAT otherwise)**: better motivated on the long
  leg alone, but breaks the platform's symmetric tercile convention and would
  introduce a new exposure convention as an extra free parameter. Rejected to
  keep the degrees of freedom at exactly one — which this record now closes.
- **RS- level instead of SJ**: rejected at A3 as a volatility-level measure
  structurally overlapping the registered vol families.

---

**FROZEN.** Direction may not be changed by anything observed downstream.
