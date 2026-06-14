# Evidence Library — State Board
_Generated 2026-06-14 17:53 UTC_

| Family | Role | State | Provenance | Sharpe | OOS Sh | DSR | n | Evidence | Verdict | Notes |
|---|---|---|---|---|---|---|---|---|---|---|
| g1_market_regime_sma200 | feature | **candidate** | offline-corpus | +0.41 | +0.49 | - | 29 | 66.6 | Moderate | WF 50% borderline (exactly at threshold); 2 of 4 years negat |
| g1_trend_regime_ma_cross | feature | **candidate** | offline-corpus | +0.23 | +0.87 | - | 23 | 59.2 | Weak | Structural-event sign NOT preserved (clean Sharpe -0.13); ed |
| g2_funding_level | feature | **candidate** | offline-corpus | +0.23 | +0.11 | - | 111 | 56.3 | Weak | OOS Sharpe +0.11 is marginal; WF 3/6=50% borderline |
| g3_momentum_tsmom | edge | **candidate** | offline-corpus | +0.35 | +0.07 | - | 178 | 54.0 | Moderate | Correlated at r≥0.70 with 4H Trend; independence check fires |
| g5_trend_4h | feature | **candidate** | offline-corpus | +0.26 | +0.54 | - | 71 | 64.1 | Weak | Excluded from FINAL_AUDIT editorially; ML inclusion re-evalu |
| h5_vol_regime | feature | **candidate** | offline-corpus | +0.13 | +0.68 | - | 30 | 53.4 | Weak | IS Sharpe -0.06 (IS/OOS sign disagreement); effect only visi |
| h6_funding_vol_regime | edge | **candidate** | offline-corpus | +0.90 | -0.15 | - | 22 | 72.3 | Strong | OOS Sharpe -0.15 (IS/OOS sign disagreement); IS likely rich  |
| g1_range_regime_bb | - | **discovery** | offline-corpus | -0.30 | +0.00 | - | 185 | 30.2 | Weak | Two independent constructions (RSI + BB fade) both fail → do |
| g2_basis_compression | - | **discovery** | offline-corpus | -0.26 | -0.08 | - | 54 | 30.4 | Weak | Negative Sharpe and OOS; WF 1/5 |
| g2_basis_expansion | - | **discovery** | offline-corpus | +0.05 | -0.31 | - | 174 | 40.5 | Weak | OOS -0.31 negative; IS/OOS sign disagrees |
| g2_basis_level | - | **discovery** | offline-corpus | -0.22 | -0.97 | - | 219 | 20.2 | Reject | OOS -0.97; IS/OOS severely disagree |
| g2_funding_acceleration | - | **discovery** | offline-corpus | +0.13 | -1.00 | - | 465 | 33.7 | Weak | IS/OOS gap: IS +0.53 → OOS -1.00; classic overfitting to IS  |
| g2_funding_divergence | - | **discovery** | offline-corpus | -0.16 | -0.16 | - | 127 | 28.4 | Reject | Negative Sharpe and OOS; structural sign not preserved |
| g3_atr_compression | feature | **discovery** | offline-corpus | +0.19 | +0.57 | - | 149 | 53.8 | Moderate | Sensitivity 50% fails Candidate gate (gray zone 40-60%) |
| g3_atr_expansion | edge | **discovery** | offline-corpus | +0.48 | +0.67 | - | 152 | 63.3 | Moderate | Sensitivity 50% fails Candidate gate (gray zone 40-60%) |
| g3_mean_reversion_rsi | - | **discovery** | offline-corpus | -0.29 | -0.19 | - | 73 | 33.7 | Weak | Two independent constructions (RSI + BB fade) both fail → do |
| g3_trend_continuation_er | - | **discovery** | offline-corpus | -0.24 | -0.11 | - | 115 | 32.5 | Weak | Sensitivity 50% fails Candidate gate (gray zone 40-60%) |
| g4_engulfing | - | **discovery** | offline-corpus | -0.62 | -0.12 | - | 287 | 31.1 | Weak | Sharpe -0.62; WF 1/6; active loser |
| g4_evening_star | - | **discovery** | offline-corpus | -0.05 | -0.16 | - | 52 | 32.5 | Weak | LOO sign consistency 50% (below 67% threshold); Sharpe CV 5. |
| g4_morning_star | - | **discovery** | offline-corpus | -0.38 | -0.19 | - | 44 | 37.8 | Weak | Negative Sharpe -0.38 and OOS -0.19 |
| g4_outside_bar | - | **discovery** | offline-corpus | -0.12 | -0.32 | - | 177 | 33.8 | Weak | Negative Sharpe and OOS; LOO sign consistency only 67% |
| g4_pin_bar | - | **discovery** | offline-corpus | -0.19 | -0.25 | - | 239 | 36.2 | Weak | Negative Sharpe -0.19 and OOS -0.25 across all regimes |
| g5_trend_1d | - | **discovery** | offline-corpus | +0.04 | -0.25 | - | 11 | 22.8 | Reject | LOO sign consistency 50%; n=11 trades (lowest in battery) |
| g5_trend_1h | - | **discovery** | offline-corpus | -0.18 | -0.34 | - | 284 | 31.1 | Weak | Negative Sharpe and OOS; WF 1/6 |
| g5_trend_alignment | - | **discovery** | offline-corpus | +0.03 | -0.15 | - | 86 | 22.4 | Reject | LOO sign consistency 33% — collapses when any year removed |
| g5_trend_misalignment | - | **discovery** | offline-corpus | -0.19 | -0.50 | - | 88 | 33.7 | Weak | Negative Sharpe and OOS; WF 2/6 |
| g6_chaikin_money_flow | - | **discovery** | offline-corpus | -0.06 | -0.38 | - | 228 | 23.6 | Reject | OOS -0.38; volume/order-flow negative finding confirmed by m |
| g6_clv_pressure | - | **discovery** | offline-corpus | -0.61 | -0.54 | - | 344 | 31.1 | Weak | Sharpe -0.61; MaxDD 95% (effectively ruins portfolio); WF 1/ |
| g6_funding_basis_composite | feature | **discovery** | offline-corpus | +0.16 | +0.35 | - | 80 | 52.5 | Weak | Sensitivity 50% fails Candidate gate (gray zone 40-60%) |
| g6_volume_surge | - | **discovery** | offline-corpus | -0.41 | -0.91 | - | 166 | 33.7 | Weak | Negative Sharpe -0.41 and OOS -0.91; all volume proxies nega |
| g1_breakout_donchian | - | **disqualified** | offline-corpus | +0.14 | -0.04 | - | 46 | 25.9 | Reject | E3: sensitivity 25% < 40% knife-edge threshold |
| g1_volatility_regime | - | **disqualified** | offline-corpus | +0.13 | +0.68 | - | 30 | 53.4 | Weak | Clone of h5_vol_regime: metrics identical, r≈1.0 ≥ 0.90 clone threshold |
| g4_inside_bar | - | **disqualified** | offline-corpus | +0.66 | +0.53 | - | 148 | 65.4 | Moderate | E3: sensitivity 0% < 40% knife-edge threshold |
| g6_obv_trend | - | **disqualified** | offline-corpus | -0.52 | -0.30 | - | 213 | 26.1 | Reject | E3: sensitivity 33% < 40% knife-edge threshold |
