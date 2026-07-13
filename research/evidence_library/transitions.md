# Evidence Library — State Transition Audit Log
_Generated 2026-07-13 04:04 UTC · as-of 2026-07-13_
_Total transitions: 13_

| Timestamp | Family | From | To | Actor | Reason | Run |
|---|---|---|---|---|---|---|
| 2026-06-13T00:00:00 | g1_breakout_donchian | discovery | disqualified | bootstrap | Hard DQ E3: sensitivity 25% < 40% knife-edge threshold. Init | `4e6cae28` |
| 2026-06-13T00:00:00 | g1_market_regime_sma200 | discovery | candidate | bootstrap | Passes Feature-Candidate gate: OOS +0.49 > 0, Sens 100%. Ini | `e4074104` |
| 2026-06-13T00:00:00 | g1_trend_regime_ma_cross | discovery | candidate | bootstrap | Passes Feature-Candidate gate: OOS +0.87 > 0, Sens 100%. Ini | `abc6078e` |
| 2026-06-13T00:00:00 | g1_volatility_regime | discovery | disqualified | bootstrap | Clone DQ: identical metrics to h5_vol_regime, r≈1.0 ≥ 0.90 t | `8261cea1` |
| 2026-06-13T00:00:00 | g2_funding_level | discovery | candidate | bootstrap | Passes Feature-Candidate gate: OOS +0.11 > 0, Sens 75%. Init | `cc916a89` |
| 2026-06-25T19:01:26 | g3_atr_expansion | discovery | candidate | operator | Candidate edge gate: all 7 checks PASS on R2 (0c7e1bf9). sen | `0c7e1bf9` |
| 2026-06-13T00:00:00 | g3_momentum_tsmom | discovery | candidate | bootstrap | Passes Edge-Candidate gate: Sharpe 0.35, WF 50%, Sens 100%,  | `c75f6a4e` |
| 2026-06-13T00:00:00 | g4_inside_bar | discovery | disqualified | bootstrap | Hard DQ E3: sensitivity 0% < 40% knife-edge threshold. Initi | `3b8bcaee` |
| 2026-06-13T00:00:00 | g5_trend_4h | discovery | candidate | bootstrap | Passes Feature-Candidate gate: OOS +0.54 > 0, Sens 100%. Ini | `ca05220e` |
| 2026-06-13T00:00:00 | g6_obv_trend | discovery | disqualified | bootstrap | Hard DQ E3: sensitivity 33% < 40% knife-edge threshold. Init | `15a98761` |
| 2026-06-13T00:00:00 | h5_vol_regime | discovery | candidate | bootstrap | Passes Feature-Candidate gate: OOS +0.68 > 0, Sens 100%. Ini | `036349a1` |
| 2026-06-13T00:00:00 | h6_funding_vol_regime | discovery | candidate | bootstrap | Passes Edge-Candidate gate: Sharpe 0.90, WF 67%, Sens 100%,  | `6b29cb25` |
| 2026-06-21T14:08:41 | ivrv_vrp_spread | discovery | candidate | operator | Passes Feature-Candidate gate on lockbox-train slice (2021-0 | `b98c9685` |
