# C3 — Realized Semivariance Asymmetry: A7 In-Sample Screen Result

**Date: 2026-08-27 · Result: FAILS THE IN-SAMPLE SCREEN · Direction NOT revised**

Durable record, not an analysis scratch note: this result closes C3 as frozen,
and — more importantly — it permanently changes what the train slice can be used
for. That consequence must survive in the repository.

Evaluated exactly as frozen at A4/A5 and implemented at A6. No parameter,
direction, threshold, return convention or cost convention was changed, before
or after seeing any number.

---

## 1. Integrity (all verified before any metric was trusted)

| # | Check | Result |
|---|---|---|
| 1 | all evaluated dates in train slice | ✅ 2021-01-01 .. 2025-05-31, n = 1,605 |
| 2 | zero lockbox rows read | ✅ 0 |
| 3 | decision-function hash | ✅ `ef176e8fdc1319f0…` |
| 4 | input-builder hash | ✅ `4d17e4da9d8687d7…` |
| 5 | frozen parameters | ✅ (1440, 252, 126, 33, 7, 253) |
| 6 | no look-ahead | ✅ prefix invariance of SJ at a mid-sample cut |
| 7 | forward return strictly after signal date | ✅ `ret_fwd1 = close[t+1]/close[t] − 1` |
| 8 | cost charged exactly once per unit turnover | ✅ 941 turnover units reconcile exactly |
| 9 | incomplete-day / RS identity guards active | ✅ 1,439 returns every day; `RS⁻+RS⁺−RV = 0.0` |

## 2. Sample

```
train slice        2021-01-01 .. 2025-05-31   (1,612 master rows)
complete SJ days   1,605
days dropped       7        (the incomplete-bar rule; the known Binance outages)
raw active days    971      long 487   short 484
after hold_days=7  n_active_days 1,482   n_trades 471
```

## 3. Pre-declared metrics

`engine._metrics`, hold applied via `engine._apply_hold(pos, 7)`, cost
`engine.REALISTIC_COST = 15` bps per unit turnover.

| Metric | Value |
|---|---|
| **Sharpe** | **−0.3845** |
| Sortino | −0.3558 |
| Calmar | −0.3566 |
| CAGR % | −31.04 |
| **Max drawdown %** | **87.05** |
| Win rate % | 54.56 |
| Profit factor | 0.8873 |
| n_trades | 471 |
| n_active_days | 1,482 |
| **breakeven_cost_bps** | **5** |

**Implementation correction made during this gate, recorded because it changed
the reported number.** `engine._metrics` does not apply the holding period
internally — this project applies `_apply_hold` outside it. The first run passed
raw positions, silently evaluating hold_days = 1 and reporting Sharpe −0.8009.
Corrected to the frozen 7-day convention, giving **−0.3845**. Every figure in
this document is the corrected one.

**Cost convention.** Reported on the engine's per-turnover model, the registered
IS Specification (`research_evidence.engine.net_daily`), because that is what
every other candidate's IS number used and comparability requires it. Under the
forward once-per-trade Specification (`trade_returns_view`, label spec
`57ce016e51a0`) total net is −0.2431 versus −0.9481 per-turnover. Gate 6B.5
established these as two distinct registered conventions; neither was reopened.

## 4. Multiple testing

```
n_trials            34  (FROZEN)
library signals     34
sr_trials_std       0.018014
observed SR (daily) −0.020128      annualised −0.3845
SR* benchmark       0.038271
DSR probability     0.0095
PSR p (unadjusted)  0.2094
```

**BH-p: NOT COMPUTED.** BH-FDR is a multi-hypothesis *rank* statistic — a single
candidate's adjusted p-value depends on its rank among all 34 p-values, which
only `compute_dsr_for_registration.py` produces by re-evaluating the whole
battery. It is not derivable from C3 alone and was not fabricated.

## 5. Candidate gate (thresholds.yaml, applied as written)

| Criterion | Threshold | Observed | Result |
|---|---|---|---|
| edge.min_sharpe | ≥ 0.30 | **−0.3845** | **FAIL** |
| edge.min_breakeven_bps | ≥ 15 | **5** | **FAIL** |
| validated.feature.min_dsr_prob | ≥ 0.50 | **0.0095** | **FAIL** |
| edge.min_active_days | ≥ 60 | 1,482 | PASS |
| edge.min_calendar_years | ≥ 2 | 4.41 | PASS |
| edge.min_discrete_events | ≥ 10 | 471 | PASS |
| feature.max_clone_r | ≤ 0.90 | 0.0323 | PASS |

**Not computed at A7** — sensitivity (`sens_pass_frac`), walk-forward agreement,
LOO consistency, structural sign test, regime count. These require the Phase B
battery harness, a different apparatus from this screen. They are moot: the
three failures above are decisive and none of these could reverse them.

## 6. The result, stated precisely

**C3 as frozen FAILS the in-sample screen.** Not marginally: the Sharpe is
negative, the breakeven cost of 5 bps does not cover the registered 15 bps
assumption, and DSR is 0.0095 against a 0.50 bar. The sample is large — 1,605
days, 471 trades, 1,482 active days — so this is not a small-n artifact.

**The A4 hypothesis is falsified.** It predicted low SJ → subsequent strength
and high SJ → subsequent weakness. The data contradicts it, and A4 said in
advance that this outcome would be the correct result rather than an occasion to
revise.

## 7. What must NOT be concluded — and the temptation, named

A negative Sharpe under a *contrarian* mapping implies the opposite mapping
would have had the opposite sign. **That is not a finding, and I did not compute
it.** Flipping the direction now would mean selecting it by looking at the
outcome — precisely the researcher discretion the whole A3→A6 sequence exists to
prevent. A4 states it plainly: *"No revision of direction is permitted after
observing any outcome."* Naming the temptation here is the point: it is recorded
and closed, not left implicit for a later reader to rediscover and act on.

**Consequence, and it is permanent: the train slice is now spent for SJ
direction selection.** Any future pre-registration of a momentum-on-SJ signal
cannot be honestly evaluated on 2021-01-01..2025-05-31, because the direction
would have been chosen with knowledge of these results. A clean test would need
either the sealed lockbox — which must NOT be spent for this — or genuinely
forward evidence.

## 8. Fails screening ≠ measure disproven

Three distinct statements, kept separate:

1. **The C3 signal as frozen is rejected.** Decisive, on pre-declared criteria.
2. **The A4 economic hypothesis is falsified.** Mechanical-deleveraging-implies-
   reversal is not supported on this sample.
3. **Realized semivariance asymmetry as a *measure* is neither disproven nor
   exonerated.** BNKS's estimator is a measurement, and this gate tested one
   specific signal built on it, in one direction, at one horizon, with one
   tercile rule. That is not a test of the measure.

Statement 3 is not a consolation and must not be used as one. Acting on it would
require a new pre-registration, on an uncontaminated basis, with the direction
declared in advance — and per §7 the train slice can no longer provide that.

## 9. Governance

| | |
|---|---|
| Registry entry | **none** — C3 was never admitted |
| Ledger | **untouched** — no run, no gate_result, no transition |
| `thresholds.yaml` | **unmodified** — read only, applied as written |
| `collection_config.yaml` | **unmodified** — C3 is not collecting |
| `n_trials = 34` | **unspent, unchanged** |
| Lockbox 2025-06-01..2026-05-31 | **SEALED — never read by any C3 gate, including this one** |
| Tuning / optimisation | **none** — no parameter varied, no threshold introduced |
| A4/A5 frozen artifacts | **byte-identical** |

## 10. Trail

| Gate | Outcome |
|---|---|
| A1 | C3 shortlisted from the candidate universe |
| A2 | Primary source located; formulas not extractable by then-available tooling |
| A3 | U6 resolved from primary-source glyph codes (`≤` / `≥`); draft spec |
| A4 | Direction frozen CONTRARIAN before any computation; clone screen max \|r\| = 0.0323 |
| A5 | Final specification frozen; committed `e1d27ed` |
| A6 | Implemented and validated; 31 tests, 20/20 adversarial; committed `cfac08f` |
| **A7** | **FAILS in-sample screen. Direction not revised. C3 closed.** |
