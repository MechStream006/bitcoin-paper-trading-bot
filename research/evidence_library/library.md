# Evidence Library
_Generated 2026-06-14 17:53 UTC_
_Families: 34_

## g1_market_regime_sma200

**Display name:** Market Regime (SMA200 slope)  
**State:** candidate (since 2026-06-13)  
**Role:** feature  
**Group:** G1 Market Structure  
**Hypothesis:** BTC above a rising SMA200 is in a structurally bullish regime; below a falling SMA200, bearish.  
**Mechanism:** trend/regime  
**Data source:** spot 1m -> daily close  

**Pinned run:** `e4074104-be78-46b0-896d-8f6e374f4981` (offline-corpus)  
**Metrics:** Sharpe +0.41 | OOS +0.49 | DSR - | Evidence 66.6 | Verdict Moderate | N=29  

**State reason:** Passes Feature-Candidate gate: OOS Sharpe +0.49 > 0, Sens 100% ≥ 60%. Also borderline Edge-Candidate: Sharpe 0.41 ≥ 0.30, WF 2/4=50% (borderline). Assigned Feature role per FINAL_AUDIT conditioning judgment.  

**Weaknesses:**
- WF 50% borderline (exactly at threshold); 2 of 4 years negative
- MaxDD 57.4% — high drawdown as standalone edge
- Pre-registered 2026-06-14; Validated gate requires live-forward collection

**Pre-registration:** `research/forward_evidence/pre_registration_g1_market_regime_sma200.json` (hash `a3310aa790b7722e...`)  

---

## g1_trend_regime_ma_cross

**Display name:** Trend Regime (MA cross)  
**State:** candidate (since 2026-06-13)  
**Role:** feature  
**Group:** G1 Market Structure  
**Hypothesis:** When a fast MA leads a slow MA, momentum tends to persist.  
**Mechanism:** trend/momentum  
**Data source:** spot 1m -> daily close  

**Pinned run:** `abc6078e-0dce-4c89-b73f-c1aa9467e795` (offline-corpus)  
**Metrics:** Sharpe +0.23 | OOS +0.87 | DSR - | Evidence 59.2 | Verdict Weak | N=23  

**State reason:** Passes Feature-Candidate gate: OOS Sharpe +0.87 > 0, Sens 100% ≥ 60%. FINAL_AUDIT excluded editorially ("weak, not worth ML inclusion"). Formal gate is authoritative. Structural sign failed (important weakness).  

**Weaknesses:**
- Structural-event sign NOT preserved (clean Sharpe -0.13); edge collapses around major BTC events
- Only 23 trades; n too low for robust per-trade distribution
- Likely correlated with g1_market_regime_sma200; VIF check needed

---

## g2_funding_level

**Display name:** Funding Level  
**State:** candidate (since 2026-06-13)  
**Role:** feature  
**Group:** G2 Funding & Derivatives  
**Hypothesis:** Persistently low/negative perp funding marks washed-out positioning and precedes upward drift.  
**Mechanism:** positioning/carry  
**Data source:** funding (Binance/CCXT) -> daily mean bps  

**Pinned run:** `cc916a89-ba48-4816-8bbe-73af6f3ae315` (offline-corpus)  
**Metrics:** Sharpe +0.23 | OOS +0.11 | DSR - | Evidence 56.3 | Verdict Weak | N=111  

**State reason:** Passes Feature-Candidate gate: OOS Sharpe +0.11 > 0, Sens 75% ≥ 60%. Standalone Sharpe 0.23 < 0.30 (fails Edge gate). Feature role confirmed by FINAL_AUDIT and H1 hypothesis study.  

**Weaknesses:**
- OOS Sharpe +0.11 is marginal; WF 3/6=50% borderline
- Bull regime Sharpe -0.36 (mean-reverts poorly in bull); sideways 2.21 (concentrated)
- Redundant at r≥0.70 with Funding-Basis Composite (cluster head)

---

## g3_momentum_tsmom

**Display name:** Momentum (TSMOM)  
**State:** candidate (since 2026-06-13)  
**Role:** edge  
**Group:** G3 Price Action  
**Hypothesis:** BTC prices that have risen over a medium horizon tend to continue rising.  
**Mechanism:** momentum/trend-following  
**Data source:** spot 1m -> daily close  

**Pinned run:** `c75f6a4e-256a-47c0-8840-7fe8714a9dbf` (offline-corpus)  
**Metrics:** Sharpe +0.35 | OOS +0.07 | DSR - | Evidence 54.0 | Verdict Moderate | N=178  

**State reason:** Passes Edge-Candidate gate: Sharpe 0.35 ≥ 0.30, WF 3/6=50% ≥ 50%, Sens 100% ≥ 60%, LOO 83% ≥ 67%, break-even 30bps ≥ 15bps, structural ✓. FINAL_AUDIT excluded as redundant with 4H Trend (r≥0.70). Clone DQ at Candidate gate is r≥0.90 only; r=0.70 fires at Validated gate.  

**Weaknesses:**
- Correlated at r≥0.70 with 4H Trend; independence check fires at Validated gate
- Sideways regime Sharpe -0.95 (trend-following collapses in choppy markets)
- OOS Sharpe +0.07 marginal; 2026 YTD -1.33 (recent degradation)

**Pre-registration:** `research/forward_evidence/pre_registration_g3_tsmom.json` (hash `90865666cf83c6a1...`)  

---

## g5_trend_4h

**Display name:** 4H Trend  
**State:** candidate (since 2026-06-13)  
**Role:** feature  
**Group:** G5 Multi-Timeframe  
**Hypothesis:** Intermediate (4-hour) trend state carries information about the next day's direction.  
**Mechanism:** trend/momentum  
**Data source:** spot 1m -> 4H bars -> 50/200 MA cross, end-of-day sample  

**Pinned run:** `ca05220e-457d-4735-b009-40a8746ad5a4` (offline-corpus)  
**Metrics:** Sharpe +0.26 | OOS +0.54 | DSR - | Evidence 64.1 | Verdict Weak | N=71  

**State reason:** Passes Feature-Candidate gate: OOS Sharpe +0.54 > 0, Sens 100% ≥ 60%. FINAL_AUDIT excluded editorially ("weak, not worth ML inclusion") before Candidate gate was frozen. Formal gate is authoritative.  

**Weaknesses:**
- Excluded from FINAL_AUDIT editorially; ML inclusion re-evaluated at Validation
- Correlated at r≥0.70 with Momentum TSMOM (cluster head in FINAL_AUDIT); independence check fires at Validated gate
- Sideways regime Sharpe -0.75 (mean-reverts badly in choppy markets)

---

## h5_vol_regime

**Display name:** H5 Volatility Regimes  
**State:** candidate (since 2026-06-13)  
**Role:** feature  
**Group:** Seed (validated)  
**Hypothesis:** Top-tercile realized-vol regimes carry poor risk-adjusted forward returns; avoiding them harvests a vol-managed premium.  
**Mechanism:** vol-regime/risk-management  
**Data source:** spot 1m -> daily close (Binance Vision)  

**Pinned run:** `036349a1-d49f-465e-9533-62d547d566db` (offline-corpus)  
**Metrics:** Sharpe +0.13 | OOS +0.68 | DSR - | Evidence 53.4 | Verdict Weak | N=30  

**State reason:** Passes Feature-Candidate gate: OOS Sharpe +0.68 > 0, Sens 100% ≥ 60%. Original hypothesis study H5 labeled 'Validated B' (conditioning variable). IS/OOS sign disagrees (IS -0.06, OOS +0.68) — effect visible in OOS not IS.  

**Weaknesses:**
- IS Sharpe -0.06 (IS/OOS sign disagreement); effect only visible in OOS window
- LOO consistency 67% (exactly at threshold); Sharpe CV 2.45 (high instability)
- MaxDD 79.6% — very high drawdown as standalone rule

**Pre-registration:** `research/forward_evidence/pre_registration_h5_vol_regime.json` (hash `fc1720ccb3d9adf9...`)  

---

## h6_funding_vol_regime

**Display name:** H6 Funding x Vol x Regime  
**State:** candidate (since 2026-06-13)  
**Role:** edge  
**Group:** Seed (validated)  
**Hypothesis:** Medium-vol + low-funding + non-bear market precedes positive BTC drift.  
**Mechanism:** positioning/carry  
**Data source:** spot 1m + funding (Binance/CCXT)  

**Pinned run:** `6b29cb25-d5c0-458a-913b-de714344802b` (offline-corpus)  
**Metrics:** Sharpe +0.90 | OOS -0.15 | DSR - | Evidence 72.3 | Verdict Strong | N=22  

**State reason:** Passes Edge-Candidate gate: Sharpe 0.90 ≥ 0.30, WF 2/3=67% ≥ 50%, Sens 100% ≥ 60%, LOO 100% ≥ 67%, break-even 100bps ≥ 15bps, structural ✓. Pre-registration exists; live-forward collection active.  

**Weaknesses:**
- OOS Sharpe -0.15 (IS/OOS sign disagreement); IS likely rich relative to battery population
- n=22 trades underpowered; lockbox verdict REJECT BOTH (only 2 trades in sealed window)
- Validated gate blocked until live-forward OOS accumulates

**Pre-registration:** `research/forward_evidence/pre_registration.json` (hash `f78f0a52bfcd8add...`)  

---

## g1_range_regime_bb

**Display name:** Range Regime (BB fade)  
**State:** discovery (since 2026-06-13)  
**Role:** -  
**Group:** G1 Market Structure  
**Hypothesis:** In range-bound conditions price mean-reverts to its moving average; fade Bollinger extremes.  
**Mechanism:** mean-reversion  
**Data source:** spot 1m -> daily close  

**Pinned run:** `17f0536f-f664-49eb-8882-68018aa51753` (offline-corpus)  
**Metrics:** Sharpe -0.30 | OOS +0.00 | DSR - | Evidence 30.2 | Verdict Weak | N=185  

**State reason:** Sharpe -0.30; OOS +0.00 (zero). High-confidence negative: BTC mean-reversion has no edge.  

**Weaknesses:**
- Two independent constructions (RSI + BB fade) both fail → domain-level negative
- WF 2/6; stability score 0.58

---

## g2_basis_compression

**Display name:** Basis Compression  
**State:** discovery (since 2026-06-13)  
**Role:** -  
**Group:** G2 Funding & Derivatives  
**Hypothesis:** Compressing contango marks leverage unwinding; deepening backwardation marks accelerating fear.  
**Mechanism:** derivatives/basis  
**Data source:** perp + spot -> daily basis bps, k-day change  

**Pinned run:** `399908e9-5ab3-4f74-b4e3-a780d6821598` (offline-corpus)  
**Metrics:** Sharpe -0.26 | OOS -0.08 | DSR - | Evidence 30.4 | Verdict Weak | N=54  

**State reason:** Sharpe -0.26 and OOS -0.08 (both negative). WF 1/5.  

**Weaknesses:**
- Negative Sharpe and OOS; WF 1/5

---

## g2_basis_expansion

**Display name:** Basis Expansion  
**State:** discovery (since 2026-06-13)  
**Role:** -  
**Group:** G2 Funding & Derivatives  
**Hypothesis:** An expanding contango reflects strengthening leverage demand and trend conviction.  
**Mechanism:** derivatives/basis  
**Data source:** perp + spot -> daily basis bps, k-day change  

**Pinned run:** `916567a7-c4e1-4e64-afed-153b15f96a5d` (offline-corpus)  
**Metrics:** Sharpe +0.05 | OOS -0.31 | DSR - | Evidence 40.5 | Verdict Weak | N=174  

**State reason:** OOS Sharpe -0.31 (negative); LOO sign consistency 67% barely meets threshold but OOS negative blocks Feature path.  

**Weaknesses:**
- OOS -0.31 negative; IS/OOS sign disagrees
- LOO Sharpe CV 3.58 (extremely high instability)

---

## g2_basis_level

**Display name:** Basis Level  
**State:** discovery (since 2026-06-13)  
**Role:** -  
**Group:** G2 Funding & Derivatives  
**Hypothesis:** Perp-spot basis measures leverage demand; rich contango signals crowded longs, backwardation signals fear.  
**Mechanism:** derivatives/basis  
**Data source:** perp 1m + spot 1m -> daily basis bps  

**Pinned run:** `58654db6-7753-42ef-914c-bc850de116d3` (offline-corpus)  
**Metrics:** Sharpe -0.22 | OOS -0.97 | DSR - | Evidence 20.2 | Verdict Reject | N=219  

**State reason:** Sharpe -0.22 and OOS -0.97 (severe OOS failure). Collinear with funding.  

**Weaknesses:**
- OOS -0.97; IS/OOS severely disagree
- Collinear with g2_funding_level (as expected from H1/H4 hypothesis studies)

---

## g2_funding_acceleration

**Display name:** Funding Acceleration  
**State:** discovery (since 2026-06-13)  
**Role:** -  
**Group:** G2 Funding & Derivatives  
**Hypothesis:** Rapidly heating funding precedes long squeezes; rapidly cooling funding precedes relief rallies.  
**Mechanism:** positioning/carry  
**Data source:** funding -> daily mean bps, k-day change  

**Pinned run:** `87d86090-f271-467d-8c33-32baaa3a3497` (offline-corpus)  
**Metrics:** Sharpe +0.13 | OOS -1.00 | DSR - | Evidence 33.7 | Verdict Weak | N=465  

**State reason:** OOS Sharpe -1.00 (severe IS/OOS gap: IS +0.53 vs OOS -1.00). Classic overfitting signal.  

**Weaknesses:**
- IS/OOS gap: IS +0.53 → OOS -1.00; classic overfitting to IS window
- Stability score 0.52; 2025-2026 sharply negative (-1.27 and -0.76)

---

## g2_funding_divergence

**Display name:** Funding Divergence  
**State:** discovery (since 2026-06-13)  
**Role:** -  
**Group:** G2 Funding & Derivatives  
**Hypothesis:** A rally on low funding is organic and continues; a rally on high funding is leverage-driven and exhausts.  
**Mechanism:** positioning/carry  
**Data source:** spot close + funding  

**Pinned run:** `9101b51b-73d9-4eca-822f-cfeb62d13952` (offline-corpus)  
**Metrics:** Sharpe -0.16 | OOS -0.16 | DSR - | Evidence 28.4 | Verdict Reject | N=127  

**State reason:** Sharpe -0.16 and OOS -0.16 (both negative). Structural sign not preserved.  

**Weaknesses:**
- Negative Sharpe and OOS; structural sign not preserved
- Redundant with g2_funding_level at r≥0.70 (FINAL_AUDIT)

---

## g3_atr_compression

**Display name:** ATR Compression  
**State:** discovery (since 2026-06-13)  
**Role:** feature  
**Group:** G3 Price Action  
**Hypothesis:** Contracting true-range marks consolidation that resolves in the prevailing trend direction (volatility squeeze).  
**Mechanism:** price-action/volatility  
**Data source:** spot 1m -> daily OHLC  

**Pinned run:** `3fa9d836-a011-4251-9ae3-c93b5d63925f` (offline-corpus)  
**Metrics:** Sharpe +0.19 | OOS +0.57 | DSR - | Evidence 53.8 | Verdict Moderate | N=149  

**State reason:** Sensitivity 50% — gray zone (above 40% auto-DQ, below 60% Candidate gate threshold). Same situation as g3_atr_expansion.  

**Weaknesses:**
- Sensitivity 50% fails Candidate gate (gray zone 40-60%)
- Bear regime Sharpe -0.27; does not work in trending-down markets

---

## g3_atr_expansion

**Display name:** ATR Expansion  
**State:** discovery (since 2026-06-13)  
**Role:** edge  
**Group:** G3 Price Action  
**Hypothesis:** Rising true-range accompanies conviction-driven moves; trading direction of price during vol expansion rides breakouts.  
**Mechanism:** price-action/volatility  
**Data source:** spot 1m -> daily OHLC  

**Pinned run:** `be1849d4-ada6-434d-8732-e1e836dae5f6` (offline-corpus)  
**Metrics:** Sharpe +0.48 | OOS +0.67 | DSR - | Evidence 63.3 | Verdict Moderate | N=152  

**State reason:** Sensitivity 50% — gray zone (above 40% auto-DQ, below 60% Candidate gate threshold). Battery profile otherwise strong: Sharpe 0.48, OOS 0.67, LOO 100%, WF 67%. Requires 3+ sensitivity variants all sign-stable to reach 60% threshold or re-parameterization study. Note: FINAL_AUDIT included this editorially before the Candidate gate was frozen.  

**Weaknesses:**
- Sensitivity 50% fails Candidate gate (gray zone 40-60%)
- FINAL_AUDIT included editorially; formal gate not met

---

## g3_mean_reversion_rsi

**Display name:** Mean Reversion (RSI)  
**State:** discovery (since 2026-06-13)  
**Role:** -  
**Group:** G3 Price Action  
**Hypothesis:** Short-horizon RSI overextensions revert; oversold marks capitulation, overbought marks exhaustion.  
**Mechanism:** mean-reversion  
**Data source:** spot 1m -> daily close  

**Pinned run:** `61d522e7-89b5-4379-b00d-5b2159810e5a` (offline-corpus)  
**Metrics:** Sharpe -0.29 | OOS -0.19 | DSR - | Evidence 33.7 | Verdict Weak | N=73  

**State reason:** Sharpe -0.29; OOS -0.19. High-confidence negative: BTC daily mean-reversion has no edge (see FINAL_AUDIT §5).  

**Weaknesses:**
- Two independent constructions (RSI + BB fade) both fail → domain-level negative finding

---

## g3_trend_continuation_er

**Display name:** Trend Continuation (ER)  
**State:** discovery (since 2026-06-13)  
**Role:** -  
**Group:** G3 Price Action  
**Hypothesis:** Trends persist only when price moves efficiently; Kaufman efficiency ratio filters choppy markets.  
**Mechanism:** trend/efficiency  
**Data source:** spot 1m -> daily close  

**Pinned run:** `3a80db88-11ea-4c44-b404-d94d56bad736` (offline-corpus)  
**Metrics:** Sharpe -0.24 | OOS -0.11 | DSR - | Evidence 32.5 | Verdict Weak | N=115  

**State reason:** Sensitivity 50% — gray zone. Also: negative Sharpe -0.24 fails edge threshold. No feature path (OOS -0.11 negative).  

**Weaknesses:**
- Sensitivity 50% fails Candidate gate (gray zone 40-60%)
- Negative Sharpe -0.24 and negative OOS -0.11; no viable role

---

## g4_engulfing

**Display name:** Engulfing  
**State:** discovery (since 2026-06-13)  
**Role:** -  
**Group:** G4 Candlestick  
**Hypothesis:** An engulfing candle marks a decisive shift in control; signals short-term reversal.  
**Mechanism:** price-action/reversal  
**Data source:** spot 1m -> daily OHLC  

**Pinned run:** `fa509875-4b6b-4f60-8413-8af2abb448b3` (offline-corpus)  
**Metrics:** Sharpe -0.62 | OOS -0.12 | DSR - | Evidence 31.1 | Verdict Weak | N=287  

**State reason:** Sharpe -0.62 and OOS -0.12 (both negative). WF 1/6 (worst in battery). Actively loses.  

**Weaknesses:**
- Sharpe -0.62; WF 1/6; active loser

---

## g4_evening_star

**Display name:** Evening Star  
**State:** discovery (since 2026-06-13)  
**Role:** -  
**Group:** G4 Candlestick  
**Hypothesis:** Three-candle topping pattern (euphoria, indecision, breakdown) signals bearish reversal.  
**Mechanism:** price-action/reversal  
**Data source:** spot 1m -> daily OHLC  

**Pinned run:** `779c82db-edef-4a0b-bf9b-ad55848ff37e` (offline-corpus)  
**Metrics:** Sharpe -0.05 | OOS -0.16 | DSR - | Evidence 32.5 | Verdict Weak | N=52  

**State reason:** Sharpe -0.05 and OOS -0.16. LOO sign consistency 50% (below threshold). Candlestick reversal negative.  

**Weaknesses:**
- LOO sign consistency 50% (below 67% threshold); Sharpe CV 5.30 (extreme instability)

---

## g4_morning_star

**Display name:** Morning Star  
**State:** discovery (since 2026-06-13)  
**Role:** -  
**Group:** G4 Candlestick  
**Hypothesis:** Three-candle bottoming pattern (capitulation, indecision, recovery) signals bullish reversal.  
**Mechanism:** price-action/reversal  
**Data source:** spot 1m -> daily OHLC  

**Pinned run:** `7a23ecbd-cd42-47d6-9ae5-54832da2b151` (offline-corpus)  
**Metrics:** Sharpe -0.38 | OOS -0.19 | DSR - | Evidence 37.8 | Verdict Weak | N=44  

**State reason:** Sharpe -0.38 and OOS -0.19 (both negative). No standalone edge; no conditioning use.  

**Weaknesses:**
- Negative Sharpe -0.38 and OOS -0.19
- Part of candlestick reversal family — high-confidence negative finding (see FINAL_AUDIT §5)

---

## g4_outside_bar

**Display name:** Outside Bar  
**State:** discovery (since 2026-06-13)  
**Role:** -  
**Group:** G4 Candlestick  
**Hypothesis:** An outside bar engulfs the prior range on expanding volatility; close color signals the winning side.  
**Mechanism:** price-action/reversal  
**Data source:** spot 1m -> daily OHLC  

**Pinned run:** `5a0003ff-3256-416a-b3c7-debd6a4517e5` (offline-corpus)  
**Metrics:** Sharpe -0.12 | OOS -0.32 | DSR - | Evidence 33.8 | Verdict Weak | N=177  

**State reason:** Sharpe -0.12 and OOS -0.32. Negative finding: candlestick reversal family.  

**Weaknesses:**
- Negative Sharpe and OOS; LOO sign consistency only 67%

---

## g4_pin_bar

**Display name:** Pin Bar  
**State:** discovery (since 2026-06-13)  
**Role:** -  
**Group:** G4 Candlestick  
**Hypothesis:** A long rejection wick signals failed continuation; hammers mark demand, shooting stars mark supply.  
**Mechanism:** price-action/reversal  
**Data source:** spot 1m -> daily OHLC  

**Pinned run:** `1d111f3e-5a19-4f94-9ee1-3486dc755fda` (offline-corpus)  
**Metrics:** Sharpe -0.19 | OOS -0.25 | DSR - | Evidence 36.2 | Verdict Weak | N=239  

**State reason:** Sharpe -0.19 and OOS -0.25 (both negative). Negative finding: candlestick reversal family.  

**Weaknesses:**
- Negative Sharpe -0.19 and OOS -0.25 across all regimes

---

## g5_trend_1d

**Display name:** 1D Trend  
**State:** discovery (since 2026-06-13)  
**Role:** -  
**Group:** G5 Multi-Timeframe  
**Hypothesis:** Daily 50/200 MA cross; included as the multi-timeframe redundancy reference point.  
**Mechanism:** trend/momentum  
**Data source:** spot 1m -> daily close -> 50/200 MA cross  

**Pinned run:** `4446b8b7-97f0-4741-8c9e-f525e5172de7` (offline-corpus)  
**Metrics:** Sharpe +0.04 | OOS -0.25 | DSR - | Evidence 22.8 | Verdict Reject | N=11  

**State reason:** Sharpe +0.04 (near zero) and OOS -0.25. LOO sign consistency 50% (below threshold). Only 11 trades.  

**Weaknesses:**
- LOO sign consistency 50%; n=11 trades (lowest in battery)
- Redundant with g1_market_regime_sma200 at r≥0.70 (FINAL_AUDIT)

---

## g5_trend_1h

**Display name:** 1H Trend  
**State:** discovery (since 2026-06-13)  
**Role:** -  
**Group:** G5 Multi-Timeframe  
**Hypothesis:** Intraday hourly trend state carries information at the daily decision point.  
**Mechanism:** multi-timeframe/trend  
**Data source:** spot 1m -> 1H bars -> 50/200 MA cross, end-of-day sample  

**Pinned run:** `775db096-06f1-4aea-8163-53c816b699d5` (offline-corpus)  
**Metrics:** Sharpe -0.18 | OOS -0.34 | DSR - | Evidence 31.1 | Verdict Weak | N=284  

**State reason:** Sharpe -0.18 and OOS -0.34. Multi-timeframe structure adds no incremental information.  

**Weaknesses:**
- Negative Sharpe and OOS; WF 1/6

---

## g5_trend_alignment

**Display name:** Trend Alignment  
**State:** discovery (since 2026-06-13)  
**Role:** -  
**Group:** G5 Multi-Timeframe  
**Hypothesis:** Multi-timeframe confluence: when all three timeframes agree, conviction is higher.  
**Mechanism:** multi-timeframe/trend  
**Data source:** trend_1h + trend_4h + trend_1d  

**Pinned run:** `9a906f33-bbe1-40cf-8159-c294b2dcaf3d` (offline-corpus)  
**Metrics:** Sharpe +0.03 | OOS -0.15 | DSR - | Evidence 22.4 | Verdict Reject | N=86  

**State reason:** Sharpe +0.03 (near zero) and OOS -0.15. LOO sign consistency 33% (far below threshold). Structural sign not preserved.  

**Weaknesses:**
- LOO sign consistency 33% — collapses when any year removed
- Multi-timeframe family negative finding

---

## g5_trend_misalignment

**Display name:** Trend Misalignment  
**State:** discovery (since 2026-06-13)  
**Role:** -  
**Group:** G5 Multi-Timeframe  
**Hypothesis:** When shorter timeframes diverge from the daily trend, fade the divergence in the daily direction.  
**Mechanism:** multi-timeframe/mean-reversion  
**Data source:** trend_1h + trend_4h + trend_1d  

**Pinned run:** `563f905a-77ae-42b6-9a4c-a720e9a20a50` (offline-corpus)  
**Metrics:** Sharpe -0.19 | OOS -0.50 | DSR - | Evidence 33.7 | Verdict Weak | N=88  

**State reason:** Sharpe -0.19 and OOS -0.50. Multi-timeframe structure negative finding.  

**Weaknesses:**
- Negative Sharpe and OOS; WF 2/6
- Multi-timeframe family high-confidence negative (see FINAL_AUDIT §5)

---

## g6_chaikin_money_flow

**Display name:** Chaikin Money Flow  
**State:** discovery (since 2026-06-13)  
**Role:** -  
**Group:** G6 Positioning/Order-Flow  
**Hypothesis:** Volume-weighted close-location flow is the canonical OHLCV order-flow proxy.  
**Mechanism:** order-flow/volume  
**Data source:** spot 1m -> daily OHLC + volume  

**Pinned run:** `65293bbc-113f-42eb-900b-5b905e5633c0` (offline-corpus)  
**Metrics:** Sharpe -0.06 | OOS -0.38 | DSR - | Evidence 23.6 | Verdict Reject | N=228  

**State reason:** Sharpe -0.06 and OOS -0.38. Sensitivity 67% (passes gate) but negative effect; no edge.  

**Weaknesses:**
- OOS -0.38; volume/order-flow negative finding confirmed by multiple constructions

---

## g6_clv_pressure

**Display name:** CLV Pressure  
**State:** discovery (since 2026-06-13)  
**Role:** -  
**Group:** G6 Positioning/Order-Flow  
**Hypothesis:** Close location within the daily range proxies intraday buying vs selling pressure.  
**Mechanism:** order-flow/price-structure  
**Data source:** spot 1m -> daily OHLC  

**Pinned run:** `daaab03a-de52-43da-85d1-e8b224eca150` (offline-corpus)  
**Metrics:** Sharpe -0.61 | OOS -0.54 | DSR - | Evidence 31.1 | Verdict Weak | N=344  

**State reason:** Sharpe -0.61 and OOS -0.54. Volume/order-flow family negative finding.  

**Weaknesses:**
- Sharpe -0.61; MaxDD 95% (effectively ruins portfolio); WF 1/6

---

## g6_funding_basis_composite

**Display name:** Funding-Basis Composite  
**State:** discovery (since 2026-06-13)  
**Role:** feature  
**Group:** G6 Positioning/Order-Flow  
**Hypothesis:** Combined funding+basis positioning index adds information beyond either alone.  
**Mechanism:** positioning/carry  
**Data source:** funding + perp/spot basis  

**Pinned run:** `a6bed269-1d07-4e83-a2f0-56bc77b02f1e` (offline-corpus)  
**Metrics:** Sharpe +0.16 | OOS +0.35 | DSR - | Evidence 52.5 | Verdict Weak | N=80  

**State reason:** Sensitivity 50% — gray zone. Additionally, FINAL_AUDIT identified it as redundant with g2_funding_level (r≥0.70). Independence check would fire at Validated gate even if Candidate gate were passed.  

**Weaknesses:**
- Sensitivity 50% fails Candidate gate (gray zone 40-60%)
- Redundant with g2_funding_level at r≥0.70; independence check at Validated gate

---

## g6_volume_surge

**Display name:** Volume Surge  
**State:** discovery (since 2026-06-13)  
**Role:** -  
**Group:** G6 Positioning/Order-Flow  
**Hypothesis:** A volume spike marks conviction; trading the spike-day direction tests volume-confirmed initiation.  
**Mechanism:** order-flow/volume  
**Data source:** spot 1m -> daily OHLC + volume  

**Pinned run:** `4f29d74a-86a2-41a0-b9b8-e527339f5646` (offline-corpus)  
**Metrics:** Sharpe -0.41 | OOS -0.91 | DSR - | Evidence 33.7 | Verdict Weak | N=166  

**State reason:** Sharpe -0.41 and OOS -0.91. Volume/order-flow family high-confidence negative finding.  

**Weaknesses:**
- Negative Sharpe -0.41 and OOS -0.91; all volume proxies negative

---

## g1_breakout_donchian

**Display name:** Breakout Regime (Donchian)  
**State:** disqualified (since 2026-06-13)  
**Role:** -  
**Group:** G1 Market Structure  
**Hypothesis:** Range breakouts signal regime shifts; new N-day highs/lows tend to extend.  
**Mechanism:** trend-following/breakout  
**Data source:** spot 1m -> daily OHLC  

**Pinned run:** `4e6cae28-8a1a-4c9c-bd2b-1147b496a21b` (offline-corpus)  
**Metrics:** Sharpe +0.14 | OOS -0.04 | DSR - | Evidence 25.9 | Verdict Reject | N=46  

**State reason:** Hard DQ E3: sensitivity 25% < 40% knife-edge threshold.  

**DQ reasons:**
- E3: sensitivity 25% < 40% knife-edge threshold
- OOS Sharpe -0.04 (near zero); structural sign not preserved

---

## g1_volatility_regime

**Display name:** Volatility Regime  
**State:** disqualified (since 2026-06-13)  
**Role:** -  
**Group:** G1 Market Structure  
**Hypothesis:** Top-tercile realized-vol regimes mark risk-off conditions with poor forward returns.  
**Mechanism:** vol-regime/risk-management  
**Data source:** spot 1m -> daily close  

**Pinned run:** `8261cea1-253d-4e37-a631-52a28fffbf65` (offline-corpus)  
**Metrics:** Sharpe +0.13 | OOS +0.68 | DSR - | Evidence 53.4 | Verdict Weak | N=30  

**State reason:** Clone DQ: identical metrics to h5_vol_regime (same RV21/252d construction, correlation r≈1.0). Absorbed into h5_vol_regime as cluster head. DQ threshold: clone r ≥ 0.90.  

**DQ reasons:**
- Clone of h5_vol_regime: metrics identical, r≈1.0 ≥ 0.90 clone threshold

---

## g4_inside_bar

**Display name:** Inside Bar  
**State:** disqualified (since 2026-06-13)  
**Role:** -  
**Group:** G4 Candlestick  
**Hypothesis:** An inside bar marks volatility contraction; price continues in the prevailing trend after consolidation.  
**Mechanism:** price-action/continuation  
**Data source:** spot 1m -> daily OHLC  

**Pinned run:** `3b8bcaee-a116-4b98-b631-ae7e1e22c11d` (offline-corpus)  
**Metrics:** Sharpe +0.66 | OOS +0.53 | DSR - | Evidence 65.4 | Verdict Moderate | N=148  

**State reason:** Hard DQ E3: sensitivity 0% — zero variants sign-stable. Knife-edge threshold < 40%. Additionally: FINAL_AUDIT note that Inside Bar is a disguised trend-follower (construction uses sign(close-SMA50) on inside-bar days).  

**DQ reasons:**
- E3: sensitivity 0% < 40% knife-edge threshold
- Disguised trend-follower: signal is sign(close-SMA50) on inside-bar filter days

---

## g6_obv_trend

**Display name:** OBV Trend  
**State:** disqualified (since 2026-06-13)  
**Role:** -  
**Group:** G6 Positioning/Order-Flow  
**Hypothesis:** Rising OBV signals accumulation; volume direction leads price.  
**Mechanism:** order-flow/volume  
**Data source:** spot 1m -> daily close + volume  

**Pinned run:** `15a98761-8ba1-4635-8dd2-9c0007abf042` (offline-corpus)  
**Metrics:** Sharpe -0.52 | OOS -0.30 | DSR - | Evidence 26.1 | Verdict Reject | N=213  

**State reason:** Hard DQ E3: sensitivity 33% < 40% knife-edge threshold.  

**DQ reasons:**
- E3: sensitivity 33% < 40% knife-edge threshold
- Negative Sharpe -0.52; consistently loses across all regimes

---
