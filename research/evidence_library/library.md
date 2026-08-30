# Evidence Library
_Generated 2026-08-30 14:12 UTC · as-of 2026-08-30_
_Families: 35_

## g1_market_regime_sma200

**Display name:** Market Regime (SMA200 slope)  
**State:** candidate (since 2026-06-13)  
**Role:** feature  
**Group:** G1 Market Structure  
**Hypothesis:** BTC above a rising SMA200 is in a structurally bullish regime; below a falling SMA200, bearish.  
**Mechanism:** trend/regime  
**Data source:** spot 1m -> daily close  

### Decision anchor

**Pinned run:** `e4074104-be78-46b0-896d-8f6e374f4981` (lane L0, offline-corpus/discovery)  
**Pinned metrics:** Sharpe +0.41 | DSR not-computed | BH-p not-computed | N=29  

### Evidence by lane

- **L0 Discovery:** +0.41 / +0.49 / 29 (run `e4074104`)
- **L1 Confirmatory:** +0.00 (2026-06-14) (run `f551e746`)
- **L2 Forward:** —
- **L3 Production:** —

**State reason:** Passes Feature-Candidate gate: OOS Sharpe +0.49 > 0, Sens 100% ≥ 60%. Also borderline Edge-Candidate: Sharpe 0.41 ≥ 0.30, WF 2/4=50% (borderline). Assigned Feature role per FINAL_AUDIT conditioning judgment.  

**Weaknesses:**
- WF 50% borderline (exactly at threshold); 2 of 4 years negative
- MaxDD 57.4% — high drawdown as standalone edge
- Pre-registered 2026-06-14; Validated gate requires live-forward collection

**Pre-registration:** `research/forward_evidence/pre_registration_g1_market_regime_sma200.json` (hash `693ef379b5e44d38...`)  

---

## g1_trend_regime_ma_cross

**Display name:** Trend Regime (MA cross)  
**State:** candidate (since 2026-06-13)  
**Role:** feature  
**Group:** G1 Market Structure  
**Hypothesis:** When a fast MA leads a slow MA, momentum tends to persist.  
**Mechanism:** trend/momentum  
**Data source:** spot 1m -> daily close  

### Decision anchor

**Pinned run:** `abc6078e-0dce-4c89-b73f-c1aa9467e795` (lane L0, offline-corpus/discovery)  
**Pinned metrics:** Sharpe +0.23 | DSR not-computed | BH-p not-computed | N=23  

### Evidence by lane

- **L0 Discovery:** +0.23 / +0.87 / 23 (run `abc6078e`)
- **L1 Confirmatory:** +0.00 (2026-06-14) (run `6a2e7da2`)
- **L2 Forward:** —
- **L3 Production:** —

**State reason:** Passes Feature-Candidate gate: OOS Sharpe +0.87 > 0, Sens 100% ≥ 60%. FINAL_AUDIT excluded editorially ("weak, not worth ML inclusion"). Formal gate is authoritative. Structural sign failed (important weakness).  

**Weaknesses:**
- Structural-event sign NOT preserved (clean Sharpe -0.13); edge collapses around major BTC events
- Only 23 trades; n too low for robust per-trade distribution
- Correlated with g1_market_regime_sma200: |r| = 0.6030 on train positions (VIF 1.882; 53.1% unexplained). Measured at Gate 6A.15-PRE. Clears max_clone_r 0.90 and the 0.70 collecting-family bar, and would become the highest pair in the collecting set (existing max 0.4432). Forward-return independence is NOT established by this — max_pairwise_r is enforced on forward returns at Validated.

**Pre-registration:** `research/forward_evidence/pre_registration_g1_trend_regime.json` (hash `2d3cbd210dd56449...`)  

---

## g2_funding_level

**Display name:** Funding Level  
**State:** candidate (since 2026-06-13)  
**Role:** feature  
**Group:** G2 Funding & Derivatives  
**Hypothesis:** Persistently low/negative perp funding marks washed-out positioning and precedes upward drift.  
**Mechanism:** positioning/carry  
**Data source:** funding (Binance/CCXT) -> daily mean bps  

### Decision anchor

**Pinned run:** `cc916a89-ba48-4816-8bbe-73af6f3ae315` (lane L0, offline-corpus/discovery)  
**Pinned metrics:** Sharpe +0.23 | DSR not-computed | BH-p not-computed | N=111  

### Evidence by lane

- **L0 Discovery:** +0.23 / +0.11 / 111 (run `cc916a89`)
- **L1 Confirmatory:** +0.00 (2026-06-14) (run `288df1a1`)
- **L2 Forward:** —
- **L3 Production:** —

**State reason:** Passes Feature-Candidate gate: OOS Sharpe +0.11 > 0, Sens 75% ≥ 60%. Standalone Sharpe 0.23 < 0.30 (fails Edge gate). Feature role confirmed by FINAL_AUDIT and H1 hypothesis study.  

**Weaknesses:**
- OOS Sharpe +0.11 is marginal; WF 3/6=50% borderline
- Bull regime Sharpe -0.36 (mean-reverts poorly in bull); sideways 2.21 (concentrated)
- Redundant at r≥0.70 with Funding-Basis Composite (cluster head)

**Pre-registration:** `research/forward_evidence/pre_registration_g2_funding_level.json` (hash `8355c16512cb5558...`)  

---

## g3_atr_expansion

**Display name:** ATR Expansion  
**State:** candidate (since 2026-06-26)  
**Role:** edge  
**Group:** G3 Price Action  
**Hypothesis:** Rising true-range accompanies conviction-driven moves; trading direction of price during vol expansion rides breakouts.  
**Mechanism:** price-action/volatility  
**Data source:** spot 1m -> daily OHLC  

### Decision anchor

**Pinned run:** `0c7e1bf9-31e2-4662-a843-eef7f98c9331` (lane L1, offline-corpus/confirmatory)  
**Pinned metrics:** Sharpe +0.49 | DSR 0.270 | BH-p 0.2073 | N=153  

### Evidence by lane

- **L0 Discovery:** +0.48 / +0.67 / 152 (run `be1849d4`)
- **L1 Confirmatory:** +0.01 (2026-06-25) (run `0c7e1bf9`)
- **L2 Forward:** —
- **L3 Production:** —

**State reason:** Candidate edge gate PASS on R2 (0c7e1bf9, 2026-06-25). Fifth sensitivity variant {n=21, lag=20} added in Phase 1 (2026-06-25, commit e3ca225) raised sens_pass_frac from 2/4=0.50 FAIL to 3/5=0.60 PASS, meeting the 60% edge threshold exactly. All 7 gate checks PASS: E3_KNIFE_EDGE (0.60>=0.40), B_SHARPE (0.49>=0.30), B_WF (67%>=50%), B_SENSITIVITY (60%>=60%), B_LOO (100%>=67%), B_BREAKEVEN (50bps>=15bps), B_STRUCTURAL. IS DSR=0.270, BH-p=0.207 (IS only; Validated gate evaluated on live-forward returns). Pre-registration locked.  

**Weaknesses:**
- IS DSR 0.270 < 0.50 Validated threshold — expected; Validated gate requires live-forward evidence
- IS BH-p 0.207 > 0.10 Validated threshold — expected; evaluated on forward returns at promotion
- OOS window ~1.6yr only; OOS Sharpe 0.71 > IS 0.42 is promising but short
- Sensitivity gate met exactly at 60% (3/5); v5 {n=21,lag=20} is the enabling variant

**Pre-registration:** `research/forward_evidence/pre_registration_g3_atr_expansion.json` (hash `b76c5af9455be3cf...`)  

---

## g3_momentum_tsmom

**Display name:** Momentum (TSMOM)  
**State:** candidate (since 2026-06-13)  
**Role:** edge  
**Group:** G3 Price Action  
**Hypothesis:** BTC prices that have risen over a medium horizon tend to continue rising.  
**Mechanism:** momentum/trend-following  
**Data source:** spot 1m -> daily close  

### Decision anchor

**Pinned run:** `c75f6a4e-256a-47c0-8840-7fe8714a9dbf` (lane L0, offline-corpus/discovery)  
**Pinned metrics:** Sharpe +0.35 | DSR not-computed | BH-p not-computed | N=178  

### Evidence by lane

- **L0 Discovery:** +0.35 / +0.07 / 178 (run `c75f6a4e`)
- **L1 Confirmatory:** +0.00 (2026-06-14) (run `9fa7906e`)
- **L2 Forward:** —
- **L3 Production:** —

**State reason:** Passes Edge-Candidate gate: Sharpe 0.35 ≥ 0.30, WF 3/6=50% ≥ 50%, Sens 100% ≥ 60%, LOO 83% ≥ 67%, break-even 30bps ≥ 15bps, structural ✓. FINAL_AUDIT excluded as redundant with 4H Trend (r≥0.70). Clone DQ at Candidate gate is r≥0.90 only; r=0.70 fires at Validated gate.  

**Weaknesses:**
- Correlated at r≥0.70 with 4H Trend; independence check fires at Validated gate
- Sideways regime Sharpe -0.95 (trend-following collapses in choppy markets)
- OOS Sharpe +0.07 marginal; 2026 YTD -1.33 (recent degradation)

**Pre-registration:** `research/forward_evidence/pre_registration_g3_tsmom.json` (hash `1cab78041d4c3560...`)  

---

## g5_trend_4h

**Display name:** 4H Trend  
**State:** candidate (since 2026-06-13)  
**Role:** feature  
**Group:** G5 Multi-Timeframe  
**Hypothesis:** Intermediate (4-hour) trend state carries information about the next day's direction.  
**Mechanism:** trend/momentum  
**Data source:** spot 1m -> 4H bars -> 50/200 MA cross, end-of-day sample  

### Decision anchor

**Pinned run:** `ca05220e-457d-4735-b009-40a8746ad5a4` (lane L0, offline-corpus/discovery)  
**Pinned metrics:** Sharpe +0.26 | DSR not-computed | BH-p not-computed | N=71  

### Evidence by lane

- **L0 Discovery:** +0.26 / +0.54 / 71 (run `ca05220e`)
- **L1 Confirmatory:** +0.00 (2026-06-14) (run `33f2a7a2`)
- **L2 Forward:** —
- **L3 Production:** —

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

### Decision anchor

**Pinned run:** `036349a1-d49f-465e-9533-62d547d566db` (lane L0, offline-corpus/discovery)  
**Pinned metrics:** Sharpe +0.13 | DSR not-computed | BH-p not-computed | N=30  

### Evidence by lane

- **L0 Discovery:** +0.13 / +0.68 / 30 (run `036349a1`)
- **L1 Confirmatory:** +0.00 (2026-06-14) (run `d4f7baa9`)
- **L2 Forward:** —
- **L3 Production:** —

**State reason:** Passes Feature-Candidate gate: OOS Sharpe +0.68 > 0, Sens 100% ≥ 60%. Original hypothesis study H5 labeled 'Validated B' (conditioning variable). IS/OOS sign disagrees (IS -0.06, OOS +0.68) — effect visible in OOS not IS.  

**Weaknesses:**
- IS Sharpe -0.06 (IS/OOS sign disagreement); effect only visible in OOS window
- LOO consistency 67% (exactly at threshold); Sharpe CV 2.45 (high instability)
- MaxDD 79.6% — very high drawdown as standalone rule

**Pre-registration:** `research/forward_evidence/pre_registration_h5_vol_regime.json` (hash `0a27671bd19afd10...`)  

---

## h6_funding_vol_regime

**Display name:** H6 Funding x Vol x Regime  
**State:** candidate (since 2026-06-13)  
**Role:** edge  
**Group:** Seed (validated)  
**Hypothesis:** Medium-vol + low-funding + non-bear market precedes positive BTC drift.  
**Mechanism:** positioning/carry  
**Data source:** spot 1m + funding (Binance/CCXT)  

### Decision anchor

**Pinned run:** `6b29cb25-d5c0-458a-913b-de714344802b` (lane L0, offline-corpus/discovery)  
**Pinned metrics:** Sharpe +0.90 | DSR not-computed | BH-p not-computed | N=22  

### Evidence by lane

- **L0 Discovery:** +0.90 / -0.15 / 22 (run `6b29cb25`)
- **L1 Confirmatory:** +0.00 (2026-06-14) (run `0f07ceba`)
- **L2 Forward:** —
- **L3 Production:** —

**State reason:** Passes Edge-Candidate gate: Sharpe 0.90 ≥ 0.30, WF 2/3=67% ≥ 50%, Sens 100% ≥ 60%, LOO 100% ≥ 67%, break-even 100bps ≥ 15bps, structural ✓. Pre-registration exists; live-forward collection active.  

**Weaknesses:**
- OOS Sharpe -0.15 (IS/OOS sign disagreement); IS likely rich relative to battery population
- n=22 trades underpowered; lockbox verdict REJECT BOTH (only 2 trades in sealed window)
- Validated gate blocked until live-forward OOS accumulates

**Pre-registration:** `research/forward_evidence/pre_registration.json` (hash `9201bd3a9d90d324...`)  

---

## ivrv_vrp_spread

**Display name:** VRP Spread (HV-2, IV-RV)  
**State:** candidate (since 2026-06-19)  
**Role:** feature  
**Group:** DVOL Options-Vol  
**Hypothesis:** A rich variance risk premium (DVOL/100 - RV30 in its top trailing tercile) precedes positive forward BTC spot returns; a cheap premium precedes negative.  
**Mechanism:** variance risk premium (implied-vs-realized vol)  
**Data source:** Deribit DVOL + Binance BTCUSDT spot RV30  

### Decision anchor

**Pinned run:** `b98c9685-134b-410f-ac98-684b0ee01da6` (lane L1, offline-corpus/confirmatory)  
**Pinned metrics:** Sharpe +0.26 | DSR 0.165 | BH-p 0.2893 | N=109  

### Evidence by lane

- **L0 Discovery:** +0.26 / +0.50 / 109 (run `efbe6acb`)
- **L1 Confirmatory:** +0.00 (2026-06-19) (run `b98c9685`)
- **L2 Forward:** —
- **L3 Production:** —

**State reason:** Passes Feature-Candidate gate on the lockbox-train slice (2021-01..2025-05): sensitivity 100% ≥ 60%, OOS Sharpe +0.50 > 0, WF 60%, clone_r 0.37 < 0.90. Edge-ineligible: train Sharpe 0.265 < 0.30 (full-sample 0.329 would pass but spends the sealed lockbox, so not used). Governance classification 2026-06-19.  

**Weaknesses:**
- IS DSR 0.165 / BH-p 0.289 fail the Validated gate in-sample (expected); validation is a multi-year forward proposition
- P&L is spot-directional VRP timing, NOT literal short-vol P&L
- Edge-ineligible on the disciplined basis (train Sharpe 0.265 < 0.30); registered as feature
- DVOL ingest is non-critical — ivrv coverage gaps on stale/missing DVOL days (fail-safe skip)

**Pre-registration:** `research/forward_evidence/pre_registration_ivrv.json` (hash `7d1e897efb475224...`)  

---

## g1_range_regime_bb

**Display name:** Range Regime (BB fade)  
**State:** discovery (since ?)  
**Role:** -  
**Group:** G1 Market Structure  
**Hypothesis:** In range-bound conditions price mean-reverts to its moving average; fade Bollinger extremes.  
**Mechanism:** mean-reversion  
**Data source:** spot 1m -> daily close  

### Decision anchor

**Pinned run:** — (no governance decision has been made yet)  

### Evidence by lane

- **L0 Discovery:** -0.30 / +0.00 / 185 (run `17f0536f`)
- **L1 Confirmatory:** +0.00 (2026-06-14) (run `f1eecc8d`)
- **L2 Forward:** —
- **L3 Production:** —

**State reason:** Sharpe -0.30; OOS +0.00 (zero). High-confidence negative: BTC mean-reversion has no edge.  

**Weaknesses:**
- Two independent constructions (RSI + BB fade) both fail → domain-level negative
- WF 2/6; stability score 0.58

---

## g2_basis_compression

**Display name:** Basis Compression  
**State:** discovery (since ?)  
**Role:** -  
**Group:** G2 Funding & Derivatives  
**Hypothesis:** Compressing contango marks leverage unwinding; deepening backwardation marks accelerating fear.  
**Mechanism:** derivatives/basis  
**Data source:** perp + spot -> daily basis bps, k-day change  

### Decision anchor

**Pinned run:** — (no governance decision has been made yet)  

### Evidence by lane

- **L0 Discovery:** -0.26 / -0.08 / 54 (run `399908e9`)
- **L1 Confirmatory:** +0.00 (2026-06-14) (run `c588d90a`)
- **L2 Forward:** —
- **L3 Production:** —

**State reason:** Sharpe -0.26 and OOS -0.08 (both negative). WF 1/5.  

**Weaknesses:**
- Negative Sharpe and OOS; WF 1/5

---

## g2_basis_expansion

**Display name:** Basis Expansion  
**State:** discovery (since ?)  
**Role:** -  
**Group:** G2 Funding & Derivatives  
**Hypothesis:** An expanding contango reflects strengthening leverage demand and trend conviction.  
**Mechanism:** derivatives/basis  
**Data source:** perp + spot -> daily basis bps, k-day change  

### Decision anchor

**Pinned run:** — (no governance decision has been made yet)  

### Evidence by lane

- **L0 Discovery:** +0.05 / -0.31 / 174 (run `916567a7`)
- **L1 Confirmatory:** +0.00 (2026-06-14) (run `d0c2ff6e`)
- **L2 Forward:** —
- **L3 Production:** —

**State reason:** OOS Sharpe -0.31 (negative); LOO sign consistency 67% barely meets threshold but OOS negative blocks Feature path.  

**Weaknesses:**
- OOS -0.31 negative; IS/OOS sign disagrees
- LOO Sharpe CV 3.58 (extremely high instability)

---

## g2_basis_level

**Display name:** Basis Level  
**State:** discovery (since ?)  
**Role:** -  
**Group:** G2 Funding & Derivatives  
**Hypothesis:** Perp-spot basis measures leverage demand; rich contango signals crowded longs, backwardation signals fear.  
**Mechanism:** derivatives/basis  
**Data source:** perp 1m + spot 1m -> daily basis bps  

### Decision anchor

**Pinned run:** — (no governance decision has been made yet)  

### Evidence by lane

- **L0 Discovery:** -0.22 / -0.97 / 219 (run `58654db6`)
- **L1 Confirmatory:** +0.00 (2026-06-14) (run `6551c14b`)
- **L2 Forward:** —
- **L3 Production:** —

**State reason:** Sharpe -0.22 and OOS -0.97 (severe OOS failure). Collinear with funding.  

**Weaknesses:**
- OOS -0.97; IS/OOS severely disagree
- Collinear with g2_funding_level (as expected from H1/H4 hypothesis studies)

---

## g2_funding_acceleration

**Display name:** Funding Acceleration  
**State:** discovery (since ?)  
**Role:** -  
**Group:** G2 Funding & Derivatives  
**Hypothesis:** Rapidly heating funding precedes long squeezes; rapidly cooling funding precedes relief rallies.  
**Mechanism:** positioning/carry  
**Data source:** funding -> daily mean bps, k-day change  

### Decision anchor

**Pinned run:** — (no governance decision has been made yet)  

### Evidence by lane

- **L0 Discovery:** +0.13 / -1.00 / 465 (run `87d86090`)
- **L1 Confirmatory:** +0.00 (2026-06-14) (run `1a86fee8`)
- **L2 Forward:** —
- **L3 Production:** —

**State reason:** OOS Sharpe -1.00 (severe IS/OOS gap: IS +0.53 vs OOS -1.00). Classic overfitting signal.  

**Weaknesses:**
- IS/OOS gap: IS +0.53 → OOS -1.00; classic overfitting to IS window
- Stability score 0.52; 2025-2026 sharply negative (-1.27 and -0.76)

---

## g2_funding_divergence

**Display name:** Funding Divergence  
**State:** discovery (since ?)  
**Role:** -  
**Group:** G2 Funding & Derivatives  
**Hypothesis:** A rally on low funding is organic and continues; a rally on high funding is leverage-driven and exhausts.  
**Mechanism:** positioning/carry  
**Data source:** spot close + funding  

### Decision anchor

**Pinned run:** — (no governance decision has been made yet)  

### Evidence by lane

- **L0 Discovery:** -0.16 / -0.16 / 127 (run `9101b51b`)
- **L1 Confirmatory:** +0.00 (2026-06-14) (run `29372223`)
- **L2 Forward:** —
- **L3 Production:** —

**State reason:** Sharpe -0.16 and OOS -0.16 (both negative). Structural sign not preserved.  

**Weaknesses:**
- Negative Sharpe and OOS; structural sign not preserved
- Redundant with g2_funding_level at r≥0.70 (FINAL_AUDIT)

---

## g3_atr_compression

**Display name:** ATR Compression  
**State:** discovery (since ?)  
**Role:** feature  
**Group:** G3 Price Action  
**Hypothesis:** Contracting true-range marks consolidation that resolves in the prevailing trend direction (volatility squeeze).  
**Mechanism:** price-action/volatility  
**Data source:** spot 1m -> daily OHLC  

### Decision anchor

**Pinned run:** — (no governance decision has been made yet)  

### Evidence by lane

- **L0 Discovery:** +0.19 / +0.57 / 149 (run `3fa9d836`)
- **L1 Confirmatory:** +0.00 (2026-06-14) (run `ff2e6fd2`)
- **L2 Forward:** —
- **L3 Production:** —

**State reason:** Sensitivity 50% — gray zone (above 40% auto-DQ, below 60% Candidate gate threshold). Same situation as g3_atr_expansion.  

**Weaknesses:**
- Sensitivity 50% fails Candidate gate (gray zone 40-60%)
- Bear regime Sharpe -0.27; does not work in trending-down markets

---

## g3_mean_reversion_rsi

**Display name:** Mean Reversion (RSI)  
**State:** discovery (since ?)  
**Role:** -  
**Group:** G3 Price Action  
**Hypothesis:** Short-horizon RSI overextensions revert; oversold marks capitulation, overbought marks exhaustion.  
**Mechanism:** mean-reversion  
**Data source:** spot 1m -> daily close  

### Decision anchor

**Pinned run:** — (no governance decision has been made yet)  

### Evidence by lane

- **L0 Discovery:** -0.29 / -0.19 / 73 (run `61d522e7`)
- **L1 Confirmatory:** +0.00 (2026-06-14) (run `006f5829`)
- **L2 Forward:** —
- **L3 Production:** —

**State reason:** Sharpe -0.29; OOS -0.19. High-confidence negative: BTC daily mean-reversion has no edge (see FINAL_AUDIT §5).  

**Weaknesses:**
- Two independent constructions (RSI + BB fade) both fail → domain-level negative finding

---

## g3_trend_continuation_er

**Display name:** Trend Continuation (ER)  
**State:** discovery (since ?)  
**Role:** -  
**Group:** G3 Price Action  
**Hypothesis:** Trends persist only when price moves efficiently; Kaufman efficiency ratio filters choppy markets.  
**Mechanism:** trend/efficiency  
**Data source:** spot 1m -> daily close  

### Decision anchor

**Pinned run:** — (no governance decision has been made yet)  

### Evidence by lane

- **L0 Discovery:** -0.24 / -0.11 / 115 (run `3a80db88`)
- **L1 Confirmatory:** +0.00 (2026-06-14) (run `bda2b49e`)
- **L2 Forward:** —
- **L3 Production:** —

**State reason:** Sensitivity 50% — gray zone. Also: negative Sharpe -0.24 fails edge threshold. No feature path (OOS -0.11 negative).  

**Weaknesses:**
- Sensitivity 50% fails Candidate gate (gray zone 40-60%)
- Negative Sharpe -0.24 and negative OOS -0.11; no viable role

---

## g4_engulfing

**Display name:** Engulfing  
**State:** discovery (since ?)  
**Role:** -  
**Group:** G4 Candlestick  
**Hypothesis:** An engulfing candle marks a decisive shift in control; signals short-term reversal.  
**Mechanism:** price-action/reversal  
**Data source:** spot 1m -> daily OHLC  

### Decision anchor

**Pinned run:** — (no governance decision has been made yet)  

### Evidence by lane

- **L0 Discovery:** -0.62 / -0.12 / 287 (run `fa509875`)
- **L1 Confirmatory:** +0.00 (2026-06-14) (run `061c6119`)
- **L2 Forward:** —
- **L3 Production:** —

**State reason:** Sharpe -0.62 and OOS -0.12 (both negative). WF 1/6 (worst in battery). Actively loses.  

**Weaknesses:**
- Sharpe -0.62; WF 1/6; active loser

---

## g4_evening_star

**Display name:** Evening Star  
**State:** discovery (since ?)  
**Role:** -  
**Group:** G4 Candlestick  
**Hypothesis:** Three-candle topping pattern (euphoria, indecision, breakdown) signals bearish reversal.  
**Mechanism:** price-action/reversal  
**Data source:** spot 1m -> daily OHLC  

### Decision anchor

**Pinned run:** — (no governance decision has been made yet)  

### Evidence by lane

- **L0 Discovery:** -0.05 / -0.16 / 52 (run `779c82db`)
- **L1 Confirmatory:** +0.00 (2026-06-14) (run `d5808c79`)
- **L2 Forward:** —
- **L3 Production:** —

**State reason:** Sharpe -0.05 and OOS -0.16. LOO sign consistency 50% (below threshold). Candlestick reversal negative.  

**Weaknesses:**
- LOO sign consistency 50% (below 67% threshold); Sharpe CV 5.30 (extreme instability)

---

## g4_morning_star

**Display name:** Morning Star  
**State:** discovery (since ?)  
**Role:** -  
**Group:** G4 Candlestick  
**Hypothesis:** Three-candle bottoming pattern (capitulation, indecision, recovery) signals bullish reversal.  
**Mechanism:** price-action/reversal  
**Data source:** spot 1m -> daily OHLC  

### Decision anchor

**Pinned run:** — (no governance decision has been made yet)  

### Evidence by lane

- **L0 Discovery:** -0.38 / -0.19 / 44 (run `7a23ecbd`)
- **L1 Confirmatory:** +0.00 (2026-06-14) (run `1e02964d`)
- **L2 Forward:** —
- **L3 Production:** —

**State reason:** Sharpe -0.38 and OOS -0.19 (both negative). No standalone edge; no conditioning use.  

**Weaknesses:**
- Negative Sharpe -0.38 and OOS -0.19
- Part of candlestick reversal family — high-confidence negative finding (see FINAL_AUDIT §5)

---

## g4_outside_bar

**Display name:** Outside Bar  
**State:** discovery (since ?)  
**Role:** -  
**Group:** G4 Candlestick  
**Hypothesis:** An outside bar engulfs the prior range on expanding volatility; close color signals the winning side.  
**Mechanism:** price-action/reversal  
**Data source:** spot 1m -> daily OHLC  

### Decision anchor

**Pinned run:** — (no governance decision has been made yet)  

### Evidence by lane

- **L0 Discovery:** -0.12 / -0.32 / 177 (run `5a0003ff`)
- **L1 Confirmatory:** +0.00 (2026-06-14) (run `f163b9c9`)
- **L2 Forward:** —
- **L3 Production:** —

**State reason:** Sharpe -0.12 and OOS -0.32. Negative finding: candlestick reversal family.  

**Weaknesses:**
- Negative Sharpe and OOS; LOO sign consistency only 67%

---

## g4_pin_bar

**Display name:** Pin Bar  
**State:** discovery (since ?)  
**Role:** -  
**Group:** G4 Candlestick  
**Hypothesis:** A long rejection wick signals failed continuation; hammers mark demand, shooting stars mark supply.  
**Mechanism:** price-action/reversal  
**Data source:** spot 1m -> daily OHLC  

### Decision anchor

**Pinned run:** — (no governance decision has been made yet)  

### Evidence by lane

- **L0 Discovery:** -0.19 / -0.25 / 239 (run `1d111f3e`)
- **L1 Confirmatory:** +0.00 (2026-06-14) (run `642639a7`)
- **L2 Forward:** —
- **L3 Production:** —

**State reason:** Sharpe -0.19 and OOS -0.25 (both negative). Negative finding: candlestick reversal family.  

**Weaknesses:**
- Negative Sharpe -0.19 and OOS -0.25 across all regimes

---

## g5_trend_1d

**Display name:** 1D Trend  
**State:** discovery (since ?)  
**Role:** -  
**Group:** G5 Multi-Timeframe  
**Hypothesis:** Daily 50/200 MA cross; included as the multi-timeframe redundancy reference point.  
**Mechanism:** trend/momentum  
**Data source:** spot 1m -> daily close -> 50/200 MA cross  

### Decision anchor

**Pinned run:** — (no governance decision has been made yet)  

### Evidence by lane

- **L0 Discovery:** +0.04 / -0.25 / 11 (run `4446b8b7`)
- **L1 Confirmatory:** +0.00 (2026-06-14) (run `23699872`)
- **L2 Forward:** —
- **L3 Production:** —

**State reason:** Sharpe +0.04 (near zero) and OOS -0.25. LOO sign consistency 50% (below threshold). Only 11 trades.  

**Weaknesses:**
- LOO sign consistency 50%; n=11 trades (lowest in battery)
- Redundant with g1_market_regime_sma200 at r≥0.70 (FINAL_AUDIT)

---

## g5_trend_1h

**Display name:** 1H Trend  
**State:** discovery (since ?)  
**Role:** -  
**Group:** G5 Multi-Timeframe  
**Hypothesis:** Intraday hourly trend state carries information at the daily decision point.  
**Mechanism:** multi-timeframe/trend  
**Data source:** spot 1m -> 1H bars -> 50/200 MA cross, end-of-day sample  

### Decision anchor

**Pinned run:** — (no governance decision has been made yet)  

### Evidence by lane

- **L0 Discovery:** -0.18 / -0.34 / 284 (run `775db096`)
- **L1 Confirmatory:** +0.00 (2026-06-14) (run `5b076633`)
- **L2 Forward:** —
- **L3 Production:** —

**State reason:** Sharpe -0.18 and OOS -0.34. Multi-timeframe structure adds no incremental information.  

**Weaknesses:**
- Negative Sharpe and OOS; WF 1/6

---

## g5_trend_alignment

**Display name:** Trend Alignment  
**State:** discovery (since ?)  
**Role:** -  
**Group:** G5 Multi-Timeframe  
**Hypothesis:** Multi-timeframe confluence: when all three timeframes agree, conviction is higher.  
**Mechanism:** multi-timeframe/trend  
**Data source:** trend_1h + trend_4h + trend_1d  

### Decision anchor

**Pinned run:** — (no governance decision has been made yet)  

### Evidence by lane

- **L0 Discovery:** +0.03 / -0.15 / 86 (run `9a906f33`)
- **L1 Confirmatory:** +0.00 (2026-06-14) (run `c314e5ec`)
- **L2 Forward:** —
- **L3 Production:** —

**State reason:** Sharpe +0.03 (near zero) and OOS -0.15. LOO sign consistency 33% (far below threshold). Structural sign not preserved.  

**Weaknesses:**
- LOO sign consistency 33% — collapses when any year removed
- Multi-timeframe family negative finding

---

## g5_trend_misalignment

**Display name:** Trend Misalignment  
**State:** discovery (since ?)  
**Role:** -  
**Group:** G5 Multi-Timeframe  
**Hypothesis:** When shorter timeframes diverge from the daily trend, fade the divergence in the daily direction.  
**Mechanism:** multi-timeframe/mean-reversion  
**Data source:** trend_1h + trend_4h + trend_1d  

### Decision anchor

**Pinned run:** — (no governance decision has been made yet)  

### Evidence by lane

- **L0 Discovery:** -0.19 / -0.50 / 88 (run `563f905a`)
- **L1 Confirmatory:** +0.00 (2026-06-14) (run `07fc5f17`)
- **L2 Forward:** —
- **L3 Production:** —

**State reason:** Sharpe -0.19 and OOS -0.50. Multi-timeframe structure negative finding.  

**Weaknesses:**
- Negative Sharpe and OOS; WF 2/6
- Multi-timeframe family high-confidence negative (see FINAL_AUDIT §5)

---

## g6_chaikin_money_flow

**Display name:** Chaikin Money Flow  
**State:** discovery (since ?)  
**Role:** -  
**Group:** G6 Positioning/Order-Flow  
**Hypothesis:** Volume-weighted close-location flow is the canonical OHLCV order-flow proxy.  
**Mechanism:** order-flow/volume  
**Data source:** spot 1m -> daily OHLC + volume  

### Decision anchor

**Pinned run:** — (no governance decision has been made yet)  

### Evidence by lane

- **L0 Discovery:** -0.06 / -0.38 / 228 (run `65293bbc`)
- **L1 Confirmatory:** +0.00 (2026-06-14) (run `b389b27b`)
- **L2 Forward:** —
- **L3 Production:** —

**State reason:** Sharpe -0.06 and OOS -0.38. Sensitivity 67% (passes gate) but negative effect; no edge.  

**Weaknesses:**
- OOS -0.38; volume/order-flow negative finding confirmed by multiple constructions

---

## g6_clv_pressure

**Display name:** CLV Pressure  
**State:** discovery (since ?)  
**Role:** -  
**Group:** G6 Positioning/Order-Flow  
**Hypothesis:** Close location within the daily range proxies intraday buying vs selling pressure.  
**Mechanism:** order-flow/price-structure  
**Data source:** spot 1m -> daily OHLC  

### Decision anchor

**Pinned run:** — (no governance decision has been made yet)  

### Evidence by lane

- **L0 Discovery:** -0.61 / -0.54 / 344 (run `daaab03a`)
- **L1 Confirmatory:** +0.00 (2026-06-14) (run `15e204cb`)
- **L2 Forward:** —
- **L3 Production:** —

**State reason:** Sharpe -0.61 and OOS -0.54. Volume/order-flow family negative finding.  

**Weaknesses:**
- Sharpe -0.61; MaxDD 95% (effectively ruins portfolio); WF 1/6

---

## g6_funding_basis_composite

**Display name:** Funding-Basis Composite  
**State:** discovery (since ?)  
**Role:** feature  
**Group:** G6 Positioning/Order-Flow  
**Hypothesis:** Combined funding+basis positioning index adds information beyond either alone.  
**Mechanism:** positioning/carry  
**Data source:** funding + perp/spot basis  

### Decision anchor

**Pinned run:** — (no governance decision has been made yet)  

### Evidence by lane

- **L0 Discovery:** +0.16 / +0.35 / 80 (run `a6bed269`)
- **L1 Confirmatory:** +0.00 (2026-06-14) (run `c3c10197`)
- **L2 Forward:** —
- **L3 Production:** —

**State reason:** Sensitivity 50% — gray zone. Additionally, FINAL_AUDIT identified it as redundant with g2_funding_level (r≥0.70). Independence check would fire at Validated gate even if Candidate gate were passed.  

**Weaknesses:**
- Sensitivity 50% fails Candidate gate (gray zone 40-60%)
- Redundant with g2_funding_level at r≥0.70; independence check at Validated gate

---

## g6_volume_surge

**Display name:** Volume Surge  
**State:** discovery (since ?)  
**Role:** -  
**Group:** G6 Positioning/Order-Flow  
**Hypothesis:** A volume spike marks conviction; trading the spike-day direction tests volume-confirmed initiation.  
**Mechanism:** order-flow/volume  
**Data source:** spot 1m -> daily OHLC + volume  

### Decision anchor

**Pinned run:** — (no governance decision has been made yet)  

### Evidence by lane

- **L0 Discovery:** -0.41 / -0.91 / 166 (run `4f29d74a`)
- **L1 Confirmatory:** +0.00 (2026-06-14) (run `88930ba9`)
- **L2 Forward:** —
- **L3 Production:** —

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

### Decision anchor

**Pinned run:** `4e6cae28-8a1a-4c9c-bd2b-1147b496a21b` (lane L0, offline-corpus/discovery)  
**Pinned metrics:** Sharpe +0.14 | DSR not-computed | BH-p not-computed | N=46  

### Evidence by lane

- **L0 Discovery:** +0.14 / -0.04 / 46 (run `4e6cae28`)
- **L1 Confirmatory:** +0.00 (2026-06-14) (run `9d868221`)
- **L2 Forward:** —
- **L3 Production:** —

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

### Decision anchor

**Pinned run:** `8261cea1-253d-4e37-a631-52a28fffbf65` (lane L0, offline-corpus/discovery)  
**Pinned metrics:** Sharpe +0.13 | DSR not-computed | BH-p not-computed | N=30  

### Evidence by lane

- **L0 Discovery:** +0.13 / +0.68 / 30 (run `8261cea1`)
- **L1 Confirmatory:** +0.00 (2026-06-14) (run `673d7555`)
- **L2 Forward:** —
- **L3 Production:** —

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

### Decision anchor

**Pinned run:** `3b8bcaee-a116-4b98-b631-ae7e1e22c11d` (lane L0, offline-corpus/discovery)  
**Pinned metrics:** Sharpe +0.66 | DSR not-computed | BH-p not-computed | N=148  

### Evidence by lane

- **L0 Discovery:** +0.66 / +0.53 / 148 (run `3b8bcaee`)
- **L1 Confirmatory:** +0.00 (2026-06-14) (run `dcc61461`)
- **L2 Forward:** —
- **L3 Production:** —

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

### Decision anchor

**Pinned run:** `15a98761-8ba1-4635-8dd2-9c0007abf042` (lane L0, offline-corpus/discovery)  
**Pinned metrics:** Sharpe -0.52 | DSR not-computed | BH-p not-computed | N=213  

### Evidence by lane

- **L0 Discovery:** -0.52 / -0.30 / 213 (run `15a98761`)
- **L1 Confirmatory:** +0.00 (2026-06-14) (run `467f48cb`)
- **L2 Forward:** —
- **L3 Production:** —

**State reason:** Hard DQ E3: sensitivity 33% < 40% knife-edge threshold.  

**DQ reasons:**
- E3: sensitivity 33% < 40% knife-edge threshold
- Negative Sharpe -0.52; consistently loses across all regimes

---
