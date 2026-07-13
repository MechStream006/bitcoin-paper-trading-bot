# ML-Readiness Dashboard
_Generated 2026-07-13 04:04 UTC · as-of 2026-07-13_

**Validated families:** 0
**Target band:** 10–20
**Status:** BELOW THRESHOLD — 0/10 validated families

**Candidate families (forward collection in progress):** 9

_No validated families yet._

_Forward evidence collection is in progress for candidate families._

## Candidate families — blocked-on worklist
_What is missing before each candidate can reach Validated._

| Family | Role | Blocked on |
|---|---|---|
| g1_market_regime_sma200 | feature | Validated gate ['feature']: requires live-forward or mainnet provenance; pinned run provenance is 'offline-corpus'; DSR probability is absent from pinned run — must be computed (governance.dsr()) before Validated promotion; BH-FDR p-value is absent from pinned run — must be computed (governance.bh_fdr()) before Validated promotion |
| g1_trend_regime_ma_cross | feature | Validated gate ['feature']: requires live-forward or mainnet provenance; pinned run provenance is 'offline-corpus'; DSR probability is absent from pinned run — must be computed (governance.dsr()) before Validated promotion; BH-FDR p-value is absent from pinned run — must be computed (governance.bh_fdr()) before Validated promotion |
| g2_funding_level | feature | Validated gate ['feature']: requires live-forward or mainnet provenance; pinned run provenance is 'offline-corpus'; DSR probability is absent from pinned run — must be computed (governance.dsr()) before Validated promotion; BH-FDR p-value is absent from pinned run — must be computed (governance.bh_fdr()) before Validated promotion |
| g3_atr_expansion | edge | Validated gate ['edge']: requires live-forward or mainnet provenance; pinned run provenance is 'offline-corpus'; DSR probability 0.270 < 0.5 (thresholds.yaml validated.<role>.min_dsr_prob); BH-FDR p 0.2073 > q=0.1 (thresholds.yaml validated.<role>.max_bh_p) |
| g3_momentum_tsmom | edge | Validated gate ['edge']: requires live-forward or mainnet provenance; pinned run provenance is 'offline-corpus'; DSR probability is absent from pinned run — must be computed (governance.dsr()) before Validated promotion; BH-FDR p-value is absent from pinned run — must be computed (governance.bh_fdr()) before Validated promotion |
| g5_trend_4h | feature | Validated gate ['feature']: requires live-forward or mainnet provenance; pinned run provenance is 'offline-corpus'; DSR probability is absent from pinned run — must be computed (governance.dsr()) before Validated promotion; BH-FDR p-value is absent from pinned run — must be computed (governance.bh_fdr()) before Validated promotion |
| h5_vol_regime | feature | Validated gate ['feature']: requires live-forward or mainnet provenance; pinned run provenance is 'offline-corpus'; DSR probability is absent from pinned run — must be computed (governance.dsr()) before Validated promotion; BH-FDR p-value is absent from pinned run — must be computed (governance.bh_fdr()) before Validated promotion |
| h6_funding_vol_regime | edge | Validated gate ['edge']: requires live-forward or mainnet provenance; pinned run provenance is 'offline-corpus'; DSR probability is absent from pinned run — must be computed (governance.dsr()) before Validated promotion; BH-FDR p-value is absent from pinned run — must be computed (governance.bh_fdr()) before Validated promotion |
| ivrv_vrp_spread | feature | Validated gate ['feature']: requires live-forward or mainnet provenance; pinned run provenance is 'offline-corpus'; DSR probability 0.165 < 0.5 (thresholds.yaml validated.<role>.min_dsr_prob); BH-FDR p 0.2893 > q=0.1 (thresholds.yaml validated.<role>.max_bh_p) |

## Governance

- ML-readiness review is triggered when **Validated** family count reaches 10.
- Review consumes the **Validated** set (edge + feature roles), rendered on L2/L3 only.
- Promotion-ready and Production-ready are NOT required for ML review.
- After ML review, model must beat rule-based baseline OOS before deployment.
