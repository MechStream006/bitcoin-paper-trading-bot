# ML-Readiness Dashboard
_Generated 2026-06-14 17:53 UTC_

**Validated families:** 0
**Target band:** 10–20
**Status:** BELOW THRESHOLD — 0/10 validated families

**Candidate families (forward collection in progress):** 7

_No validated families yet._

_Forward evidence collection is in progress for candidate families._

## Candidate families (pending Validated gate)

| Family | Role | State reason (brief) |
|---|---|---|
| g1_market_regime_sma200 | feature | Passes Feature-Candidate gate: OOS Sharpe +0.49 > 0, Sens 100% ≥ 60%. Also borde |
| g1_trend_regime_ma_cross | feature | Passes Feature-Candidate gate: OOS Sharpe +0.87 > 0, Sens 100% ≥ 60%. FINAL_AUDI |
| g2_funding_level | feature | Passes Feature-Candidate gate: OOS Sharpe +0.11 > 0, Sens 75% ≥ 60%. Standalone  |
| g3_momentum_tsmom | edge | Passes Edge-Candidate gate: Sharpe 0.35 ≥ 0.30, WF 3/6=50% ≥ 50%, Sens 100% ≥ 60 |
| g5_trend_4h | feature | Passes Feature-Candidate gate: OOS Sharpe +0.54 > 0, Sens 100% ≥ 60%. FINAL_AUDI |
| h5_vol_regime | feature | Passes Feature-Candidate gate: OOS Sharpe +0.68 > 0, Sens 100% ≥ 60%. Original h |
| h6_funding_vol_regime | edge | Passes Edge-Candidate gate: Sharpe 0.90 ≥ 0.30, WF 2/3=67% ≥ 50%, Sens 100% ≥ 60 |

## Governance

- ML-readiness review is triggered when **Validated** family count reaches 10.
- Review consumes the **Validated** set (edge + feature roles).
- Promotion-ready and Production-ready are NOT required for ML review.
- After ML review, model must beat rule-based baseline OOS before deployment.
