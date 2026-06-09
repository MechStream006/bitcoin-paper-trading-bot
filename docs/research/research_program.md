# BTC Historical Research & Evidence Discovery Program

**Status:** Approved in principle (2026-06-09)
**Scope:** Bitcoin only · 3–5 yrs history · local research box · evidence before models
**Governing principle:** every claim must survive out-of-sample, regime-stratified,
multiple-testing-corrected scrutiny *before* it earns a line of strategy code — and a
strategy must earn its keep before any ML is justified.

> **Separation note.** The Delta testnet is **not** a research data source (OI always 0,
> `close` stale, no multi-year history — see CLAUDE.md §11). All historical research data
> comes from external sources (Deribit / Binance / Coinglass) and lives in its own store
> (`research/data/`), never mixed with the live `btc_trading.db`. The `research/` tree
> imports nothing from `src/` runtime.

---

## 1. Historical Data Requirements

Two tiers per dataset: **Minimum** (enough to start credible work) and **Ideal**
(enough to make options-specific claims).

| Dataset | Min spec | Ideal spec | Primary source | Format | ~Size (5yr) | Research value |
|---|---|---|---|---|---|---|
| **BTC spot** | 1m OHLCV, Binance `BTCUSDT` | 1m + 1s trades across Binance/Coinbase/Kraken | CCXT / `data.binance.vision` | Parquet, monthly partitions | 1m: ~150 MB/venue; 1s trades: 30–80 GB | Price spine; realized vol; regime labels; return distributions |
| **BTC perpetuals** | 1m OHLCV + mark/index, Binance perp | + Bybit, OKX, Delta for basis dispersion | CCXT / Binance futures dumps | Parquet | ~200 MB | Perp basis, premium, leverage proxy |
| **BTC options** | Deribit daily EOD chain snapshots (all strikes/expiries) | Hourly+ chain snapshots w/ Greeks + IV + OI | Deribit API / **Tardis.dev** (paid) / Amberdata / Laevitas | Parquet, `date/expiry` partitions | EOD: 5–15 GB; hourly: 100–400 GB | **Core asset.** IV surface, skew, term structure, OI walls — maps directly to the options bot |
| **Funding rates** | 8h funding history, Binance perp | Multi-venue (Binance/Bybit/OKX/Delta) | CCXT `fetchFundingRateHistory` / Coinglass | Parquet / CSV | < 50 MB | Funding extremes, basis carry, positioning proxy |
| **Open interest** | Daily OI, perps | Hourly OI perps + option OI by strike | Coinglass / Deribit | Parquet | 1–5 GB | OI walls, OI shifts, crowding (**testnet OI=0 — external only**) |
| **Volatility** | Realized vol (computed) | + Deribit **DVOL**, Volmex **BVIV**, ATM IV term structure | Deribit DVOL / Volmex / computed | Parquet | < 100 MB | Vol regime classification, IV-RV spread, vol-of-vol |
| **Liquidations** | Aggregated daily totals | Per-event stream (side, size, price) | Coinglass / Binance `forceOrder` archives / Tardis | Parquet | 2–10 GB | Cascade detection, capitulation/exhaustion |
| **Macro/event overlays** | FOMC, CPI, halving dates | + ETF flow dates, expiry calendar, outage log | Fed/BLS calendars + curated CSV | CSV / SQLite | < 5 MB | Event-window studies |

**Storage architecture (local):**
- **Parquet on disk** (columnar, ~5–10× compression) as the immutable raw lake, partitioned `dataset/year/month/`.
- **DuckDB** as the analytical query engine over Parquet — zero-copy SQL, handles 100s of GB locally. The research workhorse.
- **SQLite** only for small curated tables (event calendar, regime labels, hypothesis registry).
- Strict split: `research/data/raw/` (immutable, as-downloaded) vs `research/data/derived/` (computed, regenerable).
- Realistic footprint: **~50 GB minimum tier, 300–600 GB ideal tier** (driven by options + tick data). Budget a 1 TB drive.

**Data-quality gauntlet (before any dataset is trusted):** gap detection (missing candles),
timestamp monotonicity + UTC normalization, deduplication, outlier/spike screening,
exchange-halt annotation, and **survivorship awareness** for options (expired instruments
stay in the corpus — same discipline the live settlement reconciliation already respects).

---

## 2. Regime Discovery Framework

Goal: a reproducible labeler tagging every timestamp with a regime vector, so every pattern
test can be **stratified by regime**. Regimes are *measurable*, not eyeballed.

| Regime | Measurable criteria (starting definitions — to be calibrated) |
|---|---|
| **Bull** | Price > 200-period MA *and* MA slope > 0 *and* rolling 90d return > +X% (top tercile) |
| **Bear** | Price < 200-period MA *and* slope < 0 *and* 90d return < −X% |
| **Ranging** | \|MA slope\| below threshold *and* ADX < 20 *and* price contained in N-day band > p% of time |
| **Vol expansion** | Realized vol crosses above rolling 30d 80th pct; or DVOL day-over-day jump > threshold |
| **Vol compression** | Realized vol below rolling 20th pct *and* IV term in contango *and* ATR contracting N days |
| **Trend continuation** | Sustained HH/HL (or inverse) over K bars *and* pullbacks hold prior structure |
| **Trend exhaustion** | Momentum divergence *and* volume/OI climax *and* funding extreme |
| **Post-liquidation** | Within T hours of a liquidation event > 95th pct of rolling liq volume |

**Methodology layers (in order):**
1. **Rule-based labeler first** — transparent, debuggable, the criteria above. Baseline.
2. **Unsupervised cross-check** — HMM (3–5 state) or GMM on returns + realized vol; does
   data-driven state agree with rule-based labels? (Statistical regime *discovery*, not ML modeling.)
3. **Stability test** — regimes must persist (median dwell > noise), transitions sparse.
4. **Output:** a `regime_labels` table keyed by timestamp; regimes can co-occur
   (bull + vol-compression is valid). Every downstream test joins against this.

**Volatility sub-framework** (because this is an options bot): track **RV (multiple windows),
IV (DVOL/ATM), IV−RV spread (vol risk premium), and skew (25Δ put−call)** as continuous
regime axes, not just binary labels.

---

## 3. Evidence Program

Every candidate pattern gets a registered record *before* it is tested (pre-registration kills
hindsight bias). Maintain a `hypothesis_registry` (SQLite + markdown), one row per hypothesis.

**Required fields per hypothesis:**
1. **Hypothesis** — falsifiable directional claim. *Good:* "When 8h funding > 99th-pct for
   ≥3 consecutive periods, forward 24h BTC perp return is negative, mean < −0.5%."
2. **Null hypothesis** — explicit (forward return drawn from the unconditional distribution).
3. **Measurement** — exact computation: signal definition, entry-timestamp convention
   (no look-ahead), forward-return horizon, metric (mean fwd return, hit rate, conditional Sharpe).
4. **Success criteria** — pre-set thresholds: effect size (economically > costs), significance
   after correction, and consistency (holds in ≥2 of 3 regimes or both sample halves).
5. **Minimum sample size** — power-analysis driven (~200 independent events for d≈0.2 at 80%
   power; ~30 for d≈0.5). **Events must be independent** — overlapping forward windows inflate N;
   use non-overlapping windows or event-clustering and report *effective* N.
6. **Statistical tests** — matched to data:
   - Mean effect: **bootstrap / permutation** by default (returns are fat-tailed); t-test only if well-behaved.
   - Distributional shift: Mann–Whitney U, Kolmogorov–Smirnov.
   - Hit rate vs base rate: binomial test.
   - **Multiple-testing correction mandatory** — Benjamini–Hochberg FDR across the family;
     **White's Reality Check / Hansen's SPA** for trading-rule selection.
   - Report **confidence intervals on effect size**, not just p-values.
7. **Result + verdict** — Supported / Rejected / Inconclusive, with actual numbers + regime breakdown.

**Anti-fooling rules:** no tuning on the test set; effect must be economically meaningful *after*
costs; a "supported" pattern with N=15 overlapping windows is logged **Inconclusive**, not a win.

---

## 4. Pattern Discovery Roadmap (ranked)

Ranking = blend of **expected edge (E)**, **ease of testing (T)**, **data availability (D)**,
each 1–5 (5 best).

| Rank | Pattern | Hypothesis seed | E | T | D | Notes |
|---|---|---|---|---|---|---|
| 1 | **Funding extremes** | Extreme funding → perp mean-reversion | 4 | 5 | 5 | Cheapest, cleanest. Start here. |
| 2 | **Vol crush around expiry** | IV decays into expiry; post-expiry reset | 4 | 4 | 4 | Maps to options bot + daily-expiry reality. Needs IV history. |
| 3 | **Vol expansion (breakout)** | Compression → outsized move within N days | 4 | 4 | 5 | Compression measurable; tests straddle logic. |
| 4 | **Liquidation cascades** | Large liq → continuation then reversal | 4 | 3 | 3 | High edge if real; data is the constraint. |
| 5 | **IV–RV spread (VRP)** | IV systematically > RV → short-vol edge | 5 | 3 | 4 | Highest potential edge; careful tail accounting. |
| 6 | **Options OI walls** | Price pins at high-OI round strikes near expiry | 3 | 3 | 3 | **Cannot test on testnet (OI=0)** — needs Deribit OI. |
| 7 | **Post-FOMC / CPI** | Vol crush + directional drift in event window | 3 | 4 | 4 | Clean windows; small N limits power. |
| 8 | **Weekend effect** | Lower liquidity/vol Sat–Sun, gaps into Monday | 2 | 5 | 5 | Easy, likely weak; methodology warm-up. |
| 9 | **Monthly/turn-of-month** | Calendar seasonality | 2 | 5 | 5 | Easy; weak prior; warm-up. |
| 10 | **Mean reversion vs momentum by regime** | Which dominates per regime | 3 | 3 | 5 | Cross-cutting study, not a single pattern. |

**First three to actually run:** funding extremes (#1) → weekend effect (#8, methodology dry-run)
→ volatility-expansion breakout (#3).

---

## 5. Backtesting Architecture (research-grade)

The framework's job is to make self-deception hard.

1. **Strict train/test separation** — chronological; final ~12 months **vaulted**, touched once.
2. **Walk-forward validation** — rolling/anchored windows; OOS performance as a *time series*.
3. **Purged & embargoed CV** (López de Prado) — purge overlapping-label training samples, add embargo gap; Combinatorial Purged CV for robustness distributions.
4. **Regime-aware testing** — every backtest reports performance *per regime*.
5. **Transaction cost modeling** — Delta fees + **options bid/ask spread** (the dominant cost), modeled from historical quotes.
6. **Slippage modeling** — size-dependent; options at far touch / mid+spread fraction; perps via impact function vs size/OI.
7. **Overfitting protection (quantified):** Deflated Sharpe Ratio; Probability of Backtest
   Overfitting (PBO) via combinatorial CV; Minimum Backtest Length; parameter *plateau* not *spike*; trial counting feeding the §3 correction.
8. **Realism guards:** point-in-time data only; signal on bar close → execute next bar open; expired-instrument handling for options.

Architecturally: a `Backtester` consuming the same snapshot abstraction the live runtime uses,
kept in a **separate `research/` tree** importing nothing from `src/`.

---

## 6. Model Readiness Gate

**No ML model is built until ALL of the following are met.**
1. **Evidence threshold:** ≥1 pattern (ideally 2–3) Supported under §3, including FDR + costs.
2. **Statistical significance:** survives BH-FDR + White/SPA. Raw p<0.05 single test is **not** sufficient.
3. **Out-of-sample:** positive cost-adjusted performance on the vaulted segment; Deflated Sharpe > 0.
4. **Regime robustness:** holds in ≥2 distinct regimes, or is regime-conditional with the detector itself OOS-validated.
5. **Overfitting bound:** PBO < 50% (ideally < 20%); parameter plateau; trial count logged.
6. **Economic viability:** a simple **rule-based** version is already profitable after costs. *ML must beat this baseline.*

Spirit: **ML may only improve an edge already proven with simple statistics.** If criteria 1–6
pass, ML is justified for better regime classification, nonlinear feature combination, sizing,
or execution timing — never for conjuring edge from nothing.

---

## 7. Phased Roadmap (superseded by consolidated_roadmap.md)

| Phase | Focus | Effort | Success gate |
|---|---|---|---|
| **A** | Historical data acquisition + DuckDB/Parquet harness + quality gauntlet | 3–4 wks | Min-tier datasets pass quality checks |
| **B** | Evidence harness (registry, stat library, event-study) | 2–3 wks | Correct reproducible verdict on an easy known pattern |
| **C** | Regime classification (rule-based + unsupervised cross-check) | 2–3 wks | Stable, reproducible regimes |
| **D** | Pattern validation through §3 program, regime-stratified | 6–10 wks | ≥1 pattern Supported; honest verdicts on rest |
| **E** | Backtesting (walk-forward, purged CV, costs, overfitting metrics) | 4–6 wks | A pattern clears §6 criteria 2–5 |
| **F** | Strategy development (rule-based) + paper-trade on testnet | 4–6 wks | Live paper tracks backtest within tolerance |
| **G** | ML — only if §6 gate fully passed | open | ML beats rule-based baseline OOS |

**Total to a validated, paper-traded rule-based strategy (A–F): ~5–8 months part-time.**

> The phase plan above is the original. The **authoritative, deduplicated, A.1/A.2-split plan
> is in `consolidated_roadmap.md`**, which supersedes this section.
