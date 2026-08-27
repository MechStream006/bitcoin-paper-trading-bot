# g1_trend_regime_ma_cross — Gate 6A.15 Specification Freeze

**Date: 2026-08-27 · Status: SPECIFICATION FROZEN — NOT ADMITTED, NOT IMPLEMENTED, NOT COLLECTING**

Pre-registration draft. This gate froze a specification and nothing else. No
decision function was written, no registry entry was created or modified, no
signal was wired into collection, nothing was deployed, and **no performance
statistic of any kind was computed**. The sealed lockbox (2025-06-01..2026-05-31)
was never read.

Machine-readable counterpart:
`research/forward_evidence/drafts/g1_trend_regime_ma_cross_prereg_draft.json`.

---

## 1. What is frozen

The candidate is the **already-registered** family `g1_trend_regime_ma_cross`
(registry state `candidate`, role `feature`, pinned run
`abc6078e-0dce-4c89-b73f-c1aa9467e795`). Nothing about it is redefined here.

| Element | Frozen value |
|---|---|
| Definition | `research/evidence/signals.py :: sig_trend_regime(m, fast, slow)` |
| `fast` | **20** |
| `slow` | **100** |
| Output | `{−1, 0, +1}`, used **raw** — no transformation, no re-signing, no shift |
| Input | Daily BTC spot close (Binance BTCUSDT 1m → UTC daily close) |
| `MIN_HISTORY` | **100** rows |
| `HOLD_DAYS` | **7** calendar days |
| `cost_bps` | **15.0**, charged once per completed trade |
| Direction | `long_short` |
| Role | `feature` |

Long when `SMA(20) > SMA(100)`; short when `SMA(20) < SMA(100)`; flat only when
the two are exactly equal (measure-zero) or a moving average is undefined — a
state the `MIN_HISTORY` guard makes unreachable in forward collection (§4).

**Nothing was tuned and nothing was reinterpreted.** 20/100 are the function's
own defaults and the exact parameters screened at Gate 6A.15-PRE.

## 2. Input and provenance contract

| | |
|---|---|
| Instrument | Binance BTCUSDT spot |
| Store | `research/data/raw/spot/BTCUSDT`, layout `year=YYYY/month=MM/data.parquet` |
| Bar interval | 1 minute |
| Daily close | last 1m bar of the UTC calendar day (`resample('D').last()`) |
| Loader | `runner_base.load_daily_closes(parquet_dir, run_date, min_rows=MIN_HISTORY)` |
| Completeness | `MIN_DAILY_BARS = 1440` on the **final** day (gate 6B.1) |
| Identity | `runner_base.resolve_input_identity(..., role='closes')` — one entry per Parquet partition carrying raw chunked `parquet_sha256` and `rows` |
| Framework identity | `runner_base.framework_hash()` over `runner_base.py`, `run_daily.py`, `run_date_policy.py` (gate 6B.6 / defect B-04a) |
| Persisted to | `run_provenance` (one row per `run_id`) and `run_inputs` |
| `run_date` policy | `run_date_policy.yaml` — lockbox and immutable-gap `run_date`s refused **unconditionally**, ahead of any authorization branch (gate 6B.3) |

This is a **single-input** signal. `aux_series` is unused, so `run_signal` takes
the unmodified close-only path — the simplest wiring on the platform.

Provenance resolution is **fail-closed**: `resolve_input_identity` raises
`DataUnavailableError` if a partition cannot be hashed. An unidentifiable input
must fail rather than be recorded as identified.

## 3. Source vs platform-new — every choice tagged

There is **no external primary source**. This candidate did not come from a
paper; it is a platform-native construct that entered the registry through the
Phase B battery. Nothing below may be presented as source-derived.

| Choice | Value | Tag |
|---|---|---|
| Fast/slow SMA crossover form | `SMA(fast)` vs `SMA(slow)` | **PLATFORM-ORIGIN** — the registry's own `feature_def`, seeded by the Phase B battery |
| `fast = 20`, `slow = 100` | 20 / 100 | **PLATFORM-ORIGIN (frozen)** — `SignalSpec.params` in `signals.py`; the exact battery parameters and the ones screened at 6A.15-PRE |
| Polarity (fast above slow ⇒ long) | as written | **PLATFORM-ORIGIN** — trend-persistence hypothesis, in the registry since 2026-06-13 |
| Instrument / store / 1m bars | Binance BTCUSDT spot | **PLATFORM-CONV** — all six collectors |
| UTC calendar day boundary | UTC | **PLATFORM-CONV** |
| Daily close = last 1m bar | `resample('D').last()` | **PLATFORM-CONV** — `load_daily_closes` |
| `MIN_DAILY_BARS = 1440` | 1440 | **PLATFORM-CONV** — gate 6B.1 |
| `MIN_HISTORY = 100` | 100 | **DERIVED** — equals `slow`; the exact minimum at which the undefined-MA branch is unreachable (§4). Not padded, not chosen |
| `HOLD_DAYS = 7` | 7 | **PLATFORM-CONV** — unanimous across all seven decision functions |
| `cost_bps = 15.0` | 15.0 | **PLATFORM-CONV** — unanimous in `collection_config.yaml` |
| Cost/return convention | spec `57ce016e51a0` | **PLATFORM-CONV** — §6 |
| `ANN = 365.0` | 365.0 | **PLATFORM-CONV** |
| Role `feature` | feature | **REGISTRY** — set 2026-06-13; not reopened here |
| Sensitivity variants | 10/50, 50/200, 30/150, 20/80 | **PLATFORM-ORIGIN** — the battery's own `SignalSpec.variants`, restated, not re-chosen |

**No PLATFORM-NEW choice was required by this specification.** Every value is
either the frozen candidate's own, an existing platform convention, or
mechanically derived. That is the reason this freeze is clean: unlike C3, this
gate had nothing left to invent.

## 4. MIN_HISTORY and warm-up semantics

`MIN_HISTORY = 100`, equal to `slow`.

`rolling(100, min_periods=100)` produces its first non-NaN value on row 100, so
at exactly 100 rows the final position is decidable; `SMA(20)` is satisfied far
earlier. Verified over every series length from 100 to 400: **zero** undefined
finals (V3.4). At 99 rows `SMA(100)` is NaN and the function's
`pos[sf.isna() | ss.isna()] = 0.0` line fires, returning a **flat position that
is really "unknown"** (V3.3).

**That branch is the manufactured-FLAT hazard, and `MIN_HISTORY` is what removes
it.** The guard is enforced twice and independently:

1. `load_daily_closes` raises `DataUnavailableError` when `len(daily) < min_rows`;
2. `run_signal` re-checks `len(closes_to_date) < min_history` at its own boundary.

Warm-up is therefore never *observed*: a run with insufficient history produces
no `run_log` row at all, not a flat one.

**Declared limitation — row-indexed, not calendar-indexed.** `load_daily_closes`
ends in `.dropna()`, so the returned series contains only days that exist in the
corpus. `rolling(100)` counts **rows**, not calendar days: if the corpus is
missing a day, the 100-row window silently spans more than 100 calendar days.
This is a property of the shared loader and applies identically to every
collecting family (`g1_market_regime_sma200`'s SMA200 included). It is
**inherited deliberately, not resolved here** — changing it for one family would
diverge this candidate from the platform. Recorded as a known limitation, not a
defect of this specification.

**Declared limitation — short interior days.** `_assert_final_day_complete`
checks only the final day. An interior day with fewer than 1440 bars passes
through with a possibly-intraday value labelled as the daily close. `runner_base`
documents the seven such days in the real corpus (2021-02-11, 2021-03-06,
2021-04-20, 2021-04-25, 2021-08-13, 2021-09-29, 2023-03-24); all predate the
first forward `run_date` (2026-06-15), so none can affect a forward observation.

## 5. Missing-data and fail-closed behaviour

**Rule: missing required data must never be recorded as a FLAT decision.** This
is gate 6B.1 defect B-02, and it is a runner-boundary invariant, not something
the decision function can enforce.

Verified: feeding a NaN close at `run_date` to the frozen definition yields
`0.0` — the function **cannot** distinguish absent data from a genuine flat view
(V5.1). Every guard therefore sits upstream:

| Condition | Behaviour |
|---|---|
| `run_date` absent from the close series | `DataUnavailableError` |
| Close present but **NaN** at `run_date` | `DataUnavailableError` — explicit B-02 guard |
| Final UTC day has < 1440 1m bars | `DataUnavailableError` (loader) |
| History shorter than `MIN_HISTORY` | `DataUnavailableError` (loader **and** runner) |
| A Parquet partition cannot be hashed | `DataUnavailableError` (provenance fail-closed, 6B.6) |
| A framework file is missing | raises — no fallback identity |
| `run_date` in the lockbox or the immutable gap | refused unconditionally (6B.3) |

`DataUnavailableError` is **transient**: no `run_log` row is written and the
next scheduled run retries. The corpus records a clean absence, never a
fabricated decision — the same discipline that made the 19-day INC-0005 outage
recoverable as provenance rather than corruption.

Interior corpus gaps cannot produce a NaN moving average, because the loader
drops missing days rather than delivering them as NaN (V5.6). The manufactured-
FLAT surface is therefore exactly two cases — NaN at `run_date` and insufficient
history — and both are closed.

## 6. Forward return, cost and evaluation convention

**Adopted registered label specification: `57ce016e51a0d55097e8fb38396f8628b15a8532ada9af1eb6671a99d75a2736`**
(`binding_id = forward_evidence.runner_base.trade_returns_view`, phase
`Registered`).

| Field | Value |
|---|---|
| `horizon_days` | 7 — matches frozen `HOLD_DAYS` exactly |
| `horizon_governance` | `calendar_span` |
| Fill | close-to-close: `entry_close_price` and `exit_close_price`, both spot close |
| Gross | `(exit/entry − 1) × direction` |
| Net | `gross − cost_bps / 10000.0` — **charged once per completed trade** |
| Direction | explicit `±1` column, `CHECK`-constrained — **supports short** |
| Maturity | when `trade_exits` is written (`UNIQUE(entry_id)`) |
| Served by | the `trade_returns` view in `schema_forward.sql` |

Entry price is the spot close of `run_date`; exit price is the spot close of
`run_date + 7` calendar days; one entry-to-exit pair is one trade; at most one
open trade at a time.

**Why the other two registered specifications are inadmissible here, stated
explicitly rather than left implied:**

- `89869c534200` (`forward_evidence.daily_job.close_trade`) is **long-only and
  hardcoded** (`direction = 1`). This candidate is `long_short`; adopting it
  would silently discard every short. Rejected on that ground alone.
- `0db88b4fc0dd` (`research_evidence.engine.net_daily`) is the **offline battery**
  convention: per-turnover cost on every daily position change, horizon 1 day.
  It is the convention under which this family's *recorded in-sample* statistics
  were produced, and it is deliberately **not** the forward convention (gate
  6B.5 established the two as distinct registered Specifications, not a defect).

**Consequence, declared loudly: in-sample and forward statistics for this family
are not directly comparable.** The Phase B `SignalSpec` for "Trend Regime (MA
cross)" sets no `hold_days`, so it inherited `engine.py`'s default
`hold_days = 1`; forward collection will use `HOLD_DAYS = 7`. Different hold,
different cost convention, different horizon. Any future comparison must say so.

## 7. Decision-function provenance — placeholder until implementation

| Field | Value |
|---|---|
| `decision_function_file` | **null** — not written |
| `decision_function_hash` | **null** — not computed |
| `fn_hash` enforcement | active from the moment the file exists |

**This specification is not yet hash-pinned.** It becomes pinned at the
implementation gate and only then.

The implementation gate must produce a **standalone** module — the established
platform convention, stated verbatim in `decision_function_g1_market_regime.py`:
*"DO NOT IMPORT or call `sig_market_regime` from `signals.py`. The function there
operates on the full engine DataFrame; this file accepts a plain `pd.Series` so
the hash covers the exact computation that will run in forward collection,
independent of the engine's preprocessing pipeline."*

This creates one obligation the implementation gate must discharge and must not
finesse:

> The standalone `decision_function_g1_trend_regime.py` must be proven
> **behaviourally identical** to `sig_trend_regime(m, 20, 100)` on the same close
> series — an equivalence test, not an assertion. Reimplementation is a hashing
> requirement, never a licence to reinterpret.

Once written, the file is hashed with `pre_reg_guard._sha256_lf` (CRLF→LF
normalised) and `verify_fn_hash` aborts any run whose function bytes drift, with
a `fn_hash_violation` row recorded.

## 8. Independence — 6A.15-PRE findings, preserved

Measured on the train slice only (1,612 rows, 2021-01-01..2025-05-31), frozen
methodology `analyze_mtf_independence.py` Test 1 (`_arr` alignment, Pearson on
positions, both-std>0 guard) plus `governance.vif_table`.

| | |
|---|---|
| Max \|r\| vs the six **currently collecting** families | **0.6030** (`g1_market_regime_sma200`) |
| Max \|r\| overall | **0.6030** |
| VIF vs the collecting set | **1.882** (R² 0.469 — 53.1% unexplained) |
| `high_pairs(\|r\| ≥ 0.70)` | **none** |
| `max_clone_r = 0.90` | PASS |
| `max_pairwise_r = 0.70` vs a collecting family (6A.14 rule) | PASS |
| Verdict | **admissible for collection** |

Full tier-by-tier table in the Gate 6A.15-PRE report and in the JSON draft.

Two facts carried forward unchanged, because they matter and are easy to lose:

- **This would become the highest pair in the collecting set.** The 15 existing
  pairs among the collecting six top out at 0.4432 (`g3_momentum_tsmom` ×
  `g3_atr_expansion`). 0.6030 clears 0.70 with 0.097 of headroom — comfortable,
  but a new maximum, not a marginal addition.
- **Contrast with the 6A.14 rejection.** `g5_trend_4h` was blocked at 0.7380
  against a collecting family. This candidate's relationship to the collecting
  set is materially weaker, and its correlation with `g5_trend_4h` itself was
  0.3692 — it is not a repackaging of the rejected family.

**Registry weakness now resolved:** the entry's third listed weakness reads
*"Likely correlated with g1_market_regime_sma200; VIF check needed."* 6A.15-PRE
performed that check: r = +0.6030, VIF 1.882. `registry.yaml` still carries the
stale wording because this gate does not write to the registry; the registration
gate should update it.

## 9. Recorded prior evidence — retrieved, not computed

From the ledger's pinned run `abc6078e-0dce-4c89-b73f-c1aa9467e795`
(`run_kind = discovery`, `provenance = offline-corpus`, `engine_version =
phase_b_v1`, `n_trials = 34`, window 2021-01-01..2026-05-31). **These values were
read from existing records; nothing was recomputed at this gate.**

| | |
|---|---|
| `sharpe` | 0.2337 |
| `is_sharpe` | 0.0224 |
| `oos_sharpe` | 0.8690 |
| `n_trades` | 23 |
| `sens_pass_frac` | 1.00 |
| `wf_pass_frac` | 0.80 |
| `loo_sign_consistency` | 1.00 |
| `breakeven_cost_bps` | 100 |
| `stability_score` | 0.95 |
| `max_dd_pct` | 60.50 |
| `struct_sign_ok` | **False** |
| `evidence_score` / `verdict` | 59.2 / **"Weak"** |
| `dsr_prob`, `bh_p` | **NULL — never computed for this family** |

Candidate gate results on record: `E3_KNIFE_EDGE` pass (1.0 ≥ 0.4),
`B_SENSITIVITY` pass (1.0 ≥ 0.6), `B_OOS_POSITIVE` pass (0.869 > 0).

**Edge-ineligible:** `sharpe` 0.2337 is below `candidate.edge.min_sharpe = 0.30`.
Role stays `feature`. This is a restatement of the 2026-06-13 registry decision,
not a new determination.

## 10. Known risks

**R1 — structural-event sign not preserved (`struct_sign_ok = False`).** The
registry records clean Sharpe −0.13: the in-sample result does not survive
removal of major structural events. This is the family's most serious recorded
weakness. It is **not** a candidate-gate criterion and does not block collection,
but it is the single most likely way forward evidence disappoints.

**R2 — no in-sample DSR or BH-p exists.** Both are NULL on the pinned run,
weaker provenance than `g1_market_regime_sma200` (0.2788 / 0.1305) or H-IVRV
(0.1646 / 0.2893). Under R1 of §16, NULL is a hard failure — but the Validated
gate is evaluated on **forward** returns, so this does not block collection. It
does mean there is no in-sample statistical prior of any kind for this family.

**R3 — low trade count.** 23 in-sample trades. `n` is small for a per-trade
distribution, and the registry already flags it.

**R4 — pairwise independence is measured on train positions, not forward
returns.** `max_pairwise_r = 0.70` is enforced at Validated over forward returns
(Proposal 1, implemented 2026-07-19). The 0.6030 recorded here is a train-slice
position correlation and does **not** guarantee the forward-return pair will
clear 0.70 against `g1_market_regime_sma200`. This is a real promotion risk to
carry, not a resolved matter.

**R5 — two trend families collecting simultaneously.** Admitting this candidate
puts two trend-regime constructs in the collecting set. Permitted under the
6A.14 determination, and quantified at 0.6030 — recorded so the choice is
visible rather than incidental.

## 11. Unresolved items

**None blocks the freeze.** Both items below are for later gates, and neither is
silently decided here.

**U-1 — no family-specific observation floor is set.** `g1_market_regime_sma200`
carries an `observation_floors` / `promotion_criteria` block (minimum trades,
regime-coverage counts). No equivalent is set for this candidate, deliberately:
inventing per-family thresholds at a specification gate would introduce
unpre-registered free parameters chosen with knowledge of §9's recorded results.
The governance floors in `thresholds.yaml` (`min_active_days = 60`,
`min_calendar_years = 2`, `min_dsr_prob = 0.50`, `max_bh_p = 0.10`,
`max_pairwise_r = 0.70`) apply and are sufficient to gate promotion. **A
registration gate may set family-specific floors; if it does, they must be fixed
before any forward observation exists.**

**U-2 — the registry's stale independence weakness (§8).** Requires a
`registry.yaml` write, which this gate forbids. For the registration gate.

## 12. Governance status

| | |
|---|---|
| `registry.yaml` | **unmodified** — read only; no entry created, no state change |
| `thresholds.yaml` | **unmodified** |
| `collection_config.yaml` | **unmodified** — signal not added |
| `signals.py`, `engine.py`, `runner_base.py`, `run_daily.py` | **unmodified** |
| Ledger, `label_store.db`, sealed artifacts | **unmodified** — read only |
| Production | **unmodified**; nothing deployed |
| Decision function | **does not exist** |
| Lockbox 2025-06-01..2026-05-31 | **SEALED — not read at 6A.15-PRE or 6A.15** |
| Performance statistics | **none computed at either gate** |
| `n_trials = 34` | unspent, unchanged — this family is already inside the frozen denominator |

## 13. Trail

| Gate | Outcome |
|---|---|
| Phase B battery | Family evaluated; `run_kind = discovery`; verdict "Weak" |
| 2026-06-13 | Admitted `candidate`, role `feature`, on the Feature gate |
| 6A.14-PRE / 6A.14 | `g5_trend_4h` rejected for collection (0.7380 vs a collecting family); trend branch falls back to this candidate |
| 6A.15-PRE | Clone screen: max \|r\| 0.6030, VIF 1.882, no pair ≥ 0.70 — **admissible** |
| **6A.15** | **Specification FROZEN. Not admitted, not implemented, not collecting.** |
| Next | Implementation + validation — **only** because §11 lists no blocking item |
