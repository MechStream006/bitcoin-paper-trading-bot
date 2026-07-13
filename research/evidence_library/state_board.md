# Evidence Library — Governance Board
_Generated 2026-07-13 04:04 UTC · as-of 2026-07-13 · ledger 70 runs / 13 transitions_

## Decision anchors
_What each state RESTS ON — never automated._

| Family | Role | State | Since | Decided on (pinned) | Lane | Sharpe | DSR | BH-p | Flags |
|---|---|---|---|---|---|---|---|---|---|
| g1_market_regime_sma200 | feature | **candidate** | 2026-06-13 | e4074104 | L0 | +0.41 | not-computed | not-computed |  |
| g1_trend_regime_ma_cross | feature | **candidate** | 2026-06-13 | abc6078e | L0 | +0.23 | not-computed | not-computed |  |
| g2_funding_level | feature | **candidate** | 2026-06-13 | cc916a89 | L0 | +0.23 | not-computed | not-computed |  |
| g3_atr_expansion | edge | **candidate** | 2026-06-25 | 0c7e1bf9 | L1 | +0.49 | 0.270 | 0.2073 |  |
| g3_momentum_tsmom | edge | **candidate** | 2026-06-13 | c75f6a4e | L0 | +0.35 | not-computed | not-computed |  |
| g5_trend_4h | feature | **candidate** | 2026-06-13 | ca05220e | L0 | +0.26 | not-computed | not-computed |  |
| h5_vol_regime | feature | **candidate** | 2026-06-13 | 036349a1 | L0 | +0.13 | not-computed | not-computed |  |
| h6_funding_vol_regime | edge | **candidate** | 2026-06-13 | 6b29cb25 | L0 | +0.90 | not-computed | not-computed |  |
| ivrv_vrp_spread | feature | **candidate** | 2026-06-21 | b98c9685 | L0 | +0.26 | 0.165 | 0.2893 |  |
| g1_breakout_donchian | - | **disqualified** | 2026-06-13 | 4e6cae28 | L0 | +0.14 | not-computed | not-computed |  |
| g1_volatility_regime | - | **disqualified** | 2026-06-13 | 8261cea1 | L0 | +0.13 | not-computed | not-computed |  |
| g4_inside_bar | - | **disqualified** | 2026-06-13 | 3b8bcaee | L0 | +0.66 | not-computed | not-computed |  |
| g6_obv_trend | - | **disqualified** | 2026-06-13 | 15a98761 | L0 | -0.52 | not-computed | not-computed |  |

## Evidence by lane
_What we CURRENTLY MEASURE, per lane, side by side. The nightly job can only ever change this table._

| Family | L0 Discovery (Sharpe/OOS/n) | L1 Confirmatory (Δ vs L0) | L2 Forward (Sharpe/n/days) | L3 Production | Flags |
|---|---|---|---|---|---|
| g1_market_regime_sma200 | +0.41 / +0.49 / 29 | +0.00 (2026-06-14) | — | — |  |
| g1_trend_regime_ma_cross | +0.23 / +0.87 / 23 | +0.00 (2026-06-14) | — | — |  |
| g2_funding_level | +0.23 / +0.11 / 111 | +0.00 (2026-06-14) | — | — |  |
| g3_atr_expansion | +0.48 / +0.67 / 152 | +0.01 (2026-06-25) | — | — |  |
| g3_momentum_tsmom | +0.35 / +0.07 / 178 | +0.00 (2026-06-14) | — | — |  |
| g5_trend_4h | +0.26 / +0.54 / 71 | +0.00 (2026-06-14) | — | — |  |
| h5_vol_regime | +0.13 / +0.68 / 30 | +0.00 (2026-06-14) | — | — |  |
| h6_funding_vol_regime | +0.90 / -0.15 / 22 | +0.00 (2026-06-14) | — | — |  |
| ivrv_vrp_spread | +0.26 / +0.50 / 109 | — | — | — |  |
| g1_range_regime_bb | -0.30 / +0.00 / 185 | +0.00 (2026-06-14) | — | — |  |
| g2_basis_compression | -0.26 / -0.08 / 54 | +0.00 (2026-06-14) | — | — |  |
| g2_basis_expansion | +0.05 / -0.31 / 174 | +0.00 (2026-06-14) | — | — |  |
| g2_basis_level | -0.22 / -0.97 / 219 | +0.00 (2026-06-14) | — | — |  |
| g2_funding_acceleration | +0.13 / -1.00 / 465 | +0.00 (2026-06-14) | — | — |  |
| g2_funding_divergence | -0.16 / -0.16 / 127 | +0.00 (2026-06-14) | — | — |  |
| g3_atr_compression | +0.19 / +0.57 / 149 | +0.00 (2026-06-14) | — | — |  |
| g3_mean_reversion_rsi | -0.29 / -0.19 / 73 | +0.00 (2026-06-14) | — | — |  |
| g3_trend_continuation_er | -0.24 / -0.11 / 115 | +0.00 (2026-06-14) | — | — |  |
| g4_engulfing | -0.62 / -0.12 / 287 | +0.00 (2026-06-14) | — | — |  |
| g4_evening_star | -0.05 / -0.16 / 52 | +0.00 (2026-06-14) | — | — |  |
| g4_morning_star | -0.38 / -0.19 / 44 | +0.00 (2026-06-14) | — | — |  |
| g4_outside_bar | -0.12 / -0.32 / 177 | +0.00 (2026-06-14) | — | — |  |
| g4_pin_bar | -0.19 / -0.25 / 239 | +0.00 (2026-06-14) | — | — |  |
| g5_trend_1d | +0.04 / -0.25 / 11 | +0.00 (2026-06-14) | — | — |  |
| g5_trend_1h | -0.18 / -0.34 / 284 | +0.00 (2026-06-14) | — | — |  |
| g5_trend_alignment | +0.03 / -0.15 / 86 | +0.00 (2026-06-14) | — | — |  |
| g5_trend_misalignment | -0.19 / -0.50 / 88 | +0.00 (2026-06-14) | — | — |  |
| g6_chaikin_money_flow | -0.06 / -0.38 / 228 | +0.00 (2026-06-14) | — | — |  |
| g6_clv_pressure | -0.61 / -0.54 / 344 | +0.00 (2026-06-14) | — | — |  |
| g6_funding_basis_composite | +0.16 / +0.35 / 80 | +0.00 (2026-06-14) | — | — |  |
| g6_volume_surge | -0.41 / -0.91 / 166 | +0.00 (2026-06-14) | — | — |  |
| g1_breakout_donchian | +0.14 / -0.04 / 46 | +0.00 (2026-06-14) | — | — |  |
| g1_volatility_regime | +0.13 / +0.68 / 30 | +0.00 (2026-06-14) | — | — |  |
| g4_inside_bar | +0.66 / +0.53 / 148 | +0.00 (2026-06-14) | — | — |  |
| g6_obv_trend | -0.52 / -0.30 / 213 | +0.00 (2026-06-14) | — | — |  |
