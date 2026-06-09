# Evidence Framework II — Discretionary Concepts Under Test

**Status:** Approved in principle (2026-06-09)
**Purpose:** forcibly operationalize discretionary SMC/ICT concepts into mechanical,
falsifiable definitions and prove or **disprove** them on historical BTC data.

> The act of writing a mechanical definition is itself the first falsification test. Most
> SMC/ICT edge claims survive only because the trader keeps selection freedom (which swing,
> which order block, drawn in hindsight). Remove that freedom, test *every* instance, and the
> truth comes out.

**Governing stance:** treat every concept as a *defendant*, not a tool. The null is always
"indistinguishable from a context-matched random level/point." A concept earns adoption only
by beating that baseline out-of-sample, after multiple-testing correction. Slots into Phase B
(harness) and Phase D (validation); reuses the same hypothesis registry, stat library, and
Phase-C regime labels as the main program.

---

## Four cross-cutting safeguards (apply to all concepts)

1. **Mechanical definition kills selection bias.** Algorithmic detection with fixed parameters;
   no human "valid/invalid" filtering. Test the full population; report the fraction a
   discretionary trader would have post-hoc discarded — that discard rate measures subjectivity.
2. **Point-in-time, zero look-ahead.** Order blocks and FVGs are the classic hindsight traps —
   "discovered" only *after* the defining move. Detection timestamp = the bar at which the
   pattern is fully confirmed using only prior data. Forward returns from that bar's close on.
3. **Context-matched null, not a coin flip.** Controls = random levels/points matched on
   volatility regime, distance from price, round-number proximity, recent swing density. Beat *that*.
4. **Reflexivity vs. edge.** These concepts are widely watched → any edge may be self-fulfilling
   *and* decaying. Every supported result gets a rolling-window decay check (is effect size
   shrinking year-over-year as the concept popularized?).

---

## 1. Market Structure

All five concepts depend on one shared primitive: a **swing/pivot detector** + **trend-state machine**.

- **Swing high** = bar whose high exceeds the highs of the *N* bars before and after (fractal,
  N ∈ {2,3,5} tested). Swing low symmetric. Confirmed only N bars *after* the pivot (enforces no look-ahead).
- **Trend state** = sequence logic over confirmed swings: HH+HL → uptrend; LH+LL → downtrend; else range.

### 1.1 Break of Structure (BOS) — continuation
| | |
|---|---|
| **Hypothesis** | In an established uptrend, a close beyond the last confirmed swing high predicts continuation; forward return over *h* > matched control. |
| **Measurable def.** | Uptrend active **and** `close > last_confirmed_swing_high`. Detection bar = the breaking close. |
| **Evidence criteria** | Mean fwd return in break direction > control by economically meaningful margin; hit rate > context base rate; holds ≥2 regimes. |
| **Stat validation** | Bootstrap/permutation on conditional vs control fwd-return distributions; binomial on hit rate; **non-overlapping events only**. |
| **Regime interaction** | Edge expected in **trending / vol-expansion**; expect BOS to be a **failure trap** (false breakout → reversal) in **ranging** regimes — test explicitly. |

### 1.2 Change of Character (CHOCH) — reversal
| | |
|---|---|
| **Hypothesis** | First break of the last counter-swing (e.g. below last higher-low in uptrend) predicts trend transition; forward returns flip sign. |
| **Measurable def.** | Uptrend active **and** `close < last_confirmed_higher_low` (first occurrence) → CHOCH-down. |
| **Evidence criteria** | Post-CHOCH fwd return opposes prior trend, beats control; **and** elevated P(trend flips within k bars) vs base rate. |
| **Stat validation** | As BOS + **regime-transition test**: P(flip\|CHOCH) vs P(flip) unconditional (χ²/binomial). |
| **Regime interaction** | Confound: CHOCH at end of strong trend (exhaustion) vs noise in a range. Stratify by prior-trend strength + vol regime. |

### 1.3 Liquidity Sweep (stop hunt)
| | |
|---|---|
| **Hypothesis** | Penetration of a prior swing extreme / equal-highs-lows pool then reversal back inside within *K* bars predicts continuation *against* the sweep direction. |
| **Measurable def.** | `high > prior_swing_high + ε` (ε in ATR) **and** `close < prior_swing_high` within K bars → bearish sweep. Pool = ≥2 swing highs within δ·ATR. |
| **Evidence criteria** | Reversal magnitude > control; **must beat the simpler null "any wick beyond a level reverts"** (add edge beyond generic mean reversion). |
| **Stat validation** | Bootstrap on reversal distribution; **second control**: wicks beyond *random* levels — if those revert equally, the "liquidity" story adds nothing. |
| **Regime interaction** | Strongest near **range boundaries** and **post-liquidation**; weakest mid-trend where breakouts are real. |

### 1.4 Order Blocks (OB)
| | |
|---|---|
| **Hypothesis** | The last opposing candle before a structure-breaking impulse defines a zone that, when revisited, reacts more than a random prior zone. |
| **Measurable def.** | Bullish OB = last down-candle before an up-move causing a BOS. Zone = body or high-low (param). Reaction = MFE−MAE over *h* after first touch. |
| **Evidence criteria** | Reaction asymmetry on first revisit > matched control zones; **and** first-touch > later-touch (zones weaken with use). |
| **Stat validation** | Paired vs control zones (bootstrap); **survivorship-free** (include OBs never revisited); valid only *after* BOS confirms. |
| **Regime interaction** | Expect OBs to hold in trend-continuation (pullback-to-demand), fail in transitions. Condition on whether original trend still intact at revisit. |

### 1.5 Fair Value Gap (FVG / imbalance)
| | |
|---|---|
| **Hypothesis** | A 3-candle imbalance fills at a rate above base, and/or acts as S/R on first revisit. |
| **Measurable def.** | Bullish FVG: `low[i+1] > high[i-1]` → gap `[high[i-1], low[i+1]]`, confirmed at i+1. Two claims: **(a) fill prob/time**, **(b) reaction on touch**. |
| **Evidence criteria** | (a) Fill rate & time vs control gaps of matched size/vol; (b) reaction asymmetry on first touch vs control. Gap size (ATR) as moderator. |
| **Stat validation** | Survival analysis (Kaplan–Meier) for time-to-fill vs control; bootstrap for reaction. Control must isolate *excess* fill propensity beyond ordinary vol. |
| **Regime interaction** | Trending (gaps stay open as runaway imbalances) vs ranging (fill fast). The contrast is itself a strong information test. |

---

## 2. Multi-Timeframe Alignment (D / 4H / 1H / 15m)

| | |
|---|---|
| **Hypothesis** | Setups where HTF and LTF structure agree are higher-expectancy; expectancy increases **monotonically** with degree of alignment. 15m = trigger; D/4H/1H = context filter. |
| **Measurable def.** | Trend-state (§1) per timeframe. **Alignment score** = number of TFs sharing trigger direction (0–3 context TFs). Setup = a 15m entry signal, tagged point-in-time with score + HTF states. |
| **Evidence criteria** | Fwd-return expectancy (and hit rate) rises with score; top-alignment beats bottom by economically meaningful, regime-robust margin. |
| **Stat validation** | **Jonckheere–Terpstra trend test** (ordered alternative). Ordered logistic / quantile regression. **Mandatory confound control:** nested TFs are autocorrelated, so alignment partly re-measures HTF strength — regress out HTF trend strength and test for *incremental* predictive power. |
| **Regime interaction** | Alignment edge expected to concentrate in trending regimes and **invert** in ranging/choppy (alignment = late, whipsaw). Test counter-trend asymmetry too. |

A sharper second test: **does HTF context improve a *known* LTF edge?** Take any §1 concept
that reached Supported on 15m, condition it on daily-trend agreement, and test the *interaction term*.

---

## 3. Causal Analysis (Funding · OI · Liquidations · Volatility · Macro)

Crypto's drivers are **endogenous and simultaneous** — liquidations are caused *by* price as
much as they cause it; OI follows price; everything co-moves with vol. The framework separates
genuine lead-lag information flow from reflexive co-movement, and is honest when a relationship
is bidirectional.

### Shared methodology
- **Stationarity first:** ADF + KPSS; difference/normalize (returns for price, ΔOI for OI).
- **Lead-lag battery:** cross-correlation at multiple lags → **Granger** (BIC lag selection)
  → **transfer entropy** (model-free, nonlinear). Agreement across all three = strong; Granger-only = weak.
- **Confound control:** vol is the master confounder — **conditional Granger / partial transfer
  entropy** controlling for vol, or a joint **VAR + impulse-response** holding others fixed.
- **Directionality honesty:** always test X→price *and* price→X; report both. Most are bidirectional.
- **OOS predictive check:** in-sample Granger overfits; the relationship must predict OOS.
- **Multiple-testing:** correct across variables × lags × directions (BH-FDR).

| Driver | Hypothesis | Measurable definition | Stat validation | Regime interaction |
|---|---|---|---|---|
| **Funding** | Extreme/persistent funding leads price mean-reversion | 8h funding, pct-normalized; "extreme" = rolling 95/99th; persistence = consecutive periods; fwd perp return | Granger funding→return ctrl vol; event-study on extremes; directionality (does price lead?) | Strongest near trend extremes/euphoria; weak mid-range |
| **Open Interest** | ΔOI + price jointly signal conviction vs squeeze | ΔOI (perp + option OI by strike); 2×2 of {OI↑/↓}×{price↑/↓}. **External only — testnet OI=0** | VAR(price, ΔOI, vol) + IRF; 2×2 fwd-return cells vs base | Squeeze in vol-expansion + post-liquidation; OI-walls near expiry |
| **Liquidations** | Cascade → short-term continuation then reversal | Per-event liq vol; cascade = rolling > 95th pct; fwd return +1h/+4h/+1d | Event-study vs matched controls; **reverse-causality acute** — test *incremental* content beyond triggering price move (TE conditional on returns) | Defines post-liquidation regime; test bull vs bear cascades (long vs short liqs) |
| **Volatility** | Vol is a *state variable + confounder* more than a directional signal | RV (multi-window), DVOL/ATM IV, IV−RV spread, vol-of-vol | Granger vol→vol (clustering); vol→\|return\|; IV−RV→fwd straddle P&L. Vol enters every other test as control | The axis everything is stratified on — primary moderator of all concepts |
| **Macro events** | Scheduled releases cause vol expansion + drift in window | Curated UTC calendar; windows [−1d,+1d]; RV, return, IV crush post-event | Event-study vs matched non-event controls; exogenous timing → closest to a natural experiment | Impact varies by macro regime (hiking vs cutting); halving N≈3, illustrative not powered |

**Central causal honesty principle:** for funding, OI, liquidations, the realistic finding is
often *bidirectional/simultaneous* with price dominant. That is a legitimate, valuable result —
it **disproves** the common claim that these are leading indicators. Report "X does not lead
price after controlling for vol and reverse causality" as a *win* for the evidence program.

---

## How this plugs into the roadmap

- **Phase B** gains: swing/trend detector, MTF state builder, causal-stats module
  (Granger/TE/VAR). Validate detectors on a labeled visual sample before trusting downstream results.
- **Phase D** runs these alongside the quantitative patterns — same registry, same FDR family
  (correction now spans both documents; the penalty is correctly larger).
- **Phase C** regime labels are a hard dependency — every concept has an explicit
  regime-interaction prediction, often the strongest test.

**Test order (cheapest/cleanest → hardest):** FVG, BOS (price-only) → liquidity sweep, CHOCH →
order blocks (look-ahead-prone) → MTF alignment → causal funding/vol (free data) →
causal OI/liquidations (paid external, reverse-causality-heavy). **Start with FVG.**
