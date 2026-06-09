# Consolidated Research Roadmap (Authoritative)

**Status:** Approved (2026-06-09). Supersedes the phase plan in `research_program.md §7`.
**Inputs:** `research_program.md` (quantitative) + `discretionary_evidence_framework.md` (SMC/ICT).
**This document is the implementation reference.**

Cross-review finding: the two source documents are ~30% redundant, contain one genuine
methodological contradiction (two definitions of "trend"), and were over-scoped for a first
pass. Most importantly, a **free, ~few-hundred-MB dataset unlocks Phase B, Phase C, and ~two-thirds
of all hypotheses** — paid data gates only the back half.

---

## 1. Overlapping hypotheses (deduplicated)

| Quant program | Discretionary framework | Verdict | Consolidated entry |
|---|---|---|---|
| §4 #1 Funding extremes → reversion | §3 Causal: funding leads reversion | **Duplicate** | **H-FUND** (event-study + causal views) |
| §4 #4 Liquidation cascades | §3 Causal: liquidations | **Duplicate** | **H-LIQ** (causal view adds reverse-causality control) |
| §4 #7 Post-FOMC | §3 Causal: macro | **Duplicate** | **H-MACRO** (single event-study) |
| §4 #6 OI walls | §3 Causal: ΔOI 2×2 | **Partial** | **H-OIWALL** (option OI→pinning) + **H-OIFLOW** (perp ΔOI→conviction) kept distinct |
| §2 vol regimes + §4 #2/#3/#5 | §3 Causal: volatility | **Reframe** | Vol = regime axis + causal control. Only **H-VOLEXP** + **H-IVRV** survive as directional; vol-crush folds into **H-EXPIRY** |
| §4 #10 mean-rev vs momentum | §1 BOS vs CHOCH/Sweep | **Overlap** | Drop #10 standalone; answered by regime-stratifying BOS/CHOCH/sweep |

**Net:** 10 + 13 = 23 raw items → **~15 distinct hypotheses.**

---

## 2. Contradictions (resolved)

**C1 — Two definitions of "trend" (the real one).** Quant program uses MA slope + ADX + HH/HL;
discretionary uses fractal swings. Disagreement creates **circularity risk**: stratifying a
BOS test by a regime defined with the same swing logic guarantees spurious agreement.
**Resolution — two named, orthogonal layers:**
- **Regime layer** (Phase C, for stratification): vol + returns + MA/ADX only. **No swing/structure primitives.**
- **Structure layer** (BOS/CHOCH/sweep/OB/FVG): fractal swings only.
- **Hard rule:** the regime used to stratify a structure hypothesis is computed *independently*
  of structure events.

**C2 — Vol as signal vs. confounder.** **Resolution:** vol is **confounder-first** — primary
regime axis + control in every causal test. Only **H-VOLEXP** and **H-IVRV** remain directional.

**C3 — Multiple-testing scope.** More hypotheses → stricter correction → higher data/power
demand. **Resolution:** prune to a **first battery**; expand only on signal. FDR applied
**per pre-registered battery**.

No other true contradictions — documents are otherwise complementary.

---

## 3. Unnecessary complexity (cut / deferred)

| Item | Decision | Why |
|---|---|---|
| Tick/1s data; hourly options snapshots | **Defer to A.2** | Not needed for B/C or first battery; TB-scale/paid |
| Transfer entropy + VAR/IRF up front | **Defer** | Start causal with Granger + event-study; add TE/VAR only on promising relationships |
| HMM/GMM regime discovery | **Defer to Phase C stretch** | Rule-based first; unsupervised is a cross-check |
| Combinatorial Purged CV, PBO, Deflated Sharpe | **Phase E only** | Don't front-load Phase-E machinery |
| Order blocks (look-ahead-prone) | **Back of Phase D** | Highest subjectivity/look-ahead risk |
| Four MTF timeframes | **Start Daily + 1H** | Prove incremental edge before 4-TF complexity |
| 23-hypothesis Phase D | **Prune to ~6 first battery** | See §5 |
| Options/OI/liquidation datasets | **Defer to A.2** | Gate only the back half of D |

---

## 4. Minimum Viable Phase A dataset

Split Phase A into **A.1 (free, immediate)** and **A.2 (paid/external, deferred)**.

### Phase A.1 — MVP (unlocks Phase B + C + ~⅔ of hypotheses)
| Dataset | Source | Cost | Size |
|---|---|---|---|
| BTC spot + perp **1m OHLCV** (Binance) | `data.binance.vision` dumps | Free | ~300–400 MB |
| **Funding rate** history | CCXT `fetchFundingRateHistory` | Free | < 50 MB |
| **Event calendar** (FOMC/CPI/halving, UTC) | Hand-curated CSV | Free | < 1 MB |
| Realized vol, returns, ATR | *Computed from spot* | Free | — |

**Unlocks:** full Phase-C regime labeler; structure layer (BOS/CHOCH/sweep/FVG, +OB by choice);
MTF (Daily+1H, 4H/15m resampled from 1m); H-FUND, H-MACRO, H-VOLEXP, weekend/monthly seasonality;
≈ **two-thirds of the program, for free, in a few hundred MB.**

### Phase A.2 — Expansion (gates the back half of Phase D only)
| Dataset | Source | Cost | Gates |
|---|---|---|---|
| Options chain history + Greeks/IV | Deribit (free, gappy) → Tardis.dev (paid) | $ | H-EXPIRY, H-IVRV, H-OIWALL |
| Open interest (perp + per-strike) | Coinglass / Deribit | $ | H-OIFLOW, H-OIWALL |
| Liquidation events | Coinglass / Tardis | $ | H-LIQ |
| DVOL / BVIV | Deribit / Volmex | Free-ish | Enhances vol regime (optional) |

**Decision rule:** don't buy A.2 until the A.1 first battery shows signal. Zero free-tier edge is
itself a signal to fix data/method before spending.

---

## 5. Master hypothesis registry (deduplicated, batteries marked)

| ID | Hypothesis | Data tier | Battery |
|---|---|---|---|
| H-FVG | FVG fill / reaction beats matched control | A.1 | **1 (shakedown)** |
| H-WKND | Weekend / seasonality effect | A.1 | **1 (shakedown)** |
| H-BOS | BOS → continuation (regime-conditional) | A.1 | **2** |
| H-CHOCH | CHOCH → trend transition | A.1 | **2** |
| H-SWEEP | Liquidity sweep → reversal beyond baseline reversion | A.1 | **2** |
| H-FUND | Funding extremes → reversion (event + causal) | A.1 | **2** |
| H-VOLEXP | Compression → expansion breakout | A.1 | **2** |
| H-MACRO | FOMC/CPI event-window vol + drift | A.1 | **2** |
| H-MTF | MTF alignment adds incremental edge (D+1H) | A.1 | **3** |
| H-OB | Order blocks hold on revisit | A.1 | **3** |
| H-EXPIRY | IV crush around expiry | A.2 | **4** |
| H-IVRV | IV−RV spread → short-vol edge | A.2 | **4** |
| H-LIQ | Liquidation cascade → overshoot/snapback (causal) | A.2 | **4** |
| H-OIFLOW | Perp ΔOI 2×2 conviction/squeeze | A.2 | **4** |
| H-OIWALL | Option OI walls → expiry pinning | A.2 | **4** |

---

## 6. Consolidated phase plan

| Phase | Focus | Data | Effort | Success gate |
|---|---|---|---|---|
| **A.1** | Free data lake (spot/perp/funding/calendar) + DuckDB/Parquet harness + quality gauntlet | Free | 2–3 wks | All A.1 datasets pass quality checks, UTC-normalized, gap-free |
| **B** | Evidence harness: registry, stat library (bootstrap/permutation/FDR), event-study engine, swing/trend structure primitives | A.1 | 2–3 wks | Battery-1 (H-FVG, H-WKND) runs end-to-end with correct reproducible verdicts incl. FDR |
| **C** | Regime labeler (**regime layer only — no structure primitives**); stability tests; HMM deferred | A.1 | 2 wks | Stable reproducible `regime_labels`; orthogonality to structure layer verified |
| **D.1** | Battery 2 → 3 on free data, regime-stratified | A.1 | 4–6 wks | ≥1 hypothesis Supported under full evidence program |
| **A.2 + D.2** | Buy/ingest options/OI/liquidation **only if D.1 showed signal**; run Battery 4 | A.2 | 3–4 wks data + 3–4 wks tests | Decision-gated; same evidence bar |
| **E** | Backtesting (walk-forward, purged CV, costs/slippage, DSR/PBO) on Supported hypotheses | as needed | 4–6 wks | Clears model-readiness criteria 2–5, rule-based |
| **F** | Codify validated edge → rule-based strategy → paper-trade on existing Delta testnet runtime | — | 4–6 wks | Live paper tracks backtest within tolerance |
| **G** | ML **only if** model-readiness gate fully passed and ML beats rule-based baseline OOS | — | open | ML > baseline on vaulted set, else abandon ML |

**Critical-path change:** all paid data is deferred behind a **D.1 signal gate** — a first
Supported/Rejected verdict on real BTC data is reachable for **$0 and ~3 months part-time**.

**Two hard rules carried into implementation:**
1. **Orthogonality (C1):** regime labels never use structure primitives; structure tests are
   stratified only by the independent regime layer.
2. **Battery-gated FDR (C3):** correction applied per pre-registered battery; the next battery
   (or A.2 spend) does not unlock until the current one is adjudicated.
