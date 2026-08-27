# G2 — Intraday Evidence-Lane Governance Decision

**Date: 2026-08-27 · Decision: DAILY-ONLY REMAINS IN FORCE · Intraday lane DEFERRED, not rejected**

Governance record. No implementation was designed, no performance statistic was
computed, and the sealed lockbox was not read at this gate.

This decision was made on research validity, governance integrity, evidence
comparability, engineering scope and future candidate coverage. It was **not**
made on expected profitability, and **not** on the merits of any individual
candidate.

---

## 1. Capability-gap finding

Four candidates, from four separate literatures, are blocked by one constraint:

| Candidate | Native holding period | Where closed |
|---|---|---|
| Mutanabby_AI Ultimate Algo | 4-hour bars | M-6 DECLINE |
| C1 — turn-of-the-candle | ~1 minute | A1 |
| C4 — intraday/session seasonality | 2 hours | A2 |
| C5 — Bitcoin intraday TSM | last half-hour | A10/A11 |

Four independent rejections with one common cause is a **platform capability
gap**, not four candidate defects. That finding is affirmed here.

**These four are cited as ARCHITECTURAL CONTEXT ONLY.** No performance result,
backtest, or economic claim from any of them supports this decision, and none
was computed. They evidence one thing: that the constraint recurs.

### 1.1 The gap is narrower than "intraday support"

The platform **already consumes intraday information**. `engine.load_master`
aggregates 1-hour (`//3600000`) and 4-hour (`//14400000`) bars from `spot_1m`
and maps them onto daily rows (`trend_1h`, `trend_4h`), and gate A6 built a
daily signal from 1-minute data with **no governance change whatsoever**.

None of the four candidates is blocked on intraday *information*. All four are
blocked on intraday **position holding** — the holding period, and therefore the
forward label, is intraday.

**The governance question is therefore: should the platform support intraday
execution and labelling semantics?** That is a materially larger commitment than
intraday feature extraction. Conflating the two would misprice this decision,
and this record separates them deliberately.

## 2. Decision matrix

| Criterion | **A — daily only** | **B — governed intraday lane** |
|---|---|---|
| Research validity | Sound; excludes a real candidate class | Sound **only if** overlapping labels are solved; otherwise systematically inflates significance |
| Governance integrity | Untouched; six amendments avoided | Six amendments, at least two touching governance-frozen constants |
| Evidence comparability | Single standard | **Two standards, never poolable** |
| Engineering scope | Zero | New lane, schema, cost specification, label discipline |
| Future candidate coverage | **Weak** — four blocked; the daily space already holds 38 registered signals, so clone risk on marginal daily candidates rises | **Strong** — opens the least-explored space |
| Reversibility | Trivially reversible | **Effectively irreversible** once intraday observations exist |

## 3. Risks

**Option A — daily only.** Permanently excludes a large and genuinely
under-explored candidate class. The daily signal space is crowded (38 registered
families), so marginal daily candidates face rising clone risk. The constraint
will keep recurring and consuming research gates.

**Option B — intraday lane.** Doubles the governance surface. Introduces the
**overlapping-label** failure mode, whose symptom is *optimistic* statistics —
the most dangerous kind, because nothing looks wrong. Creates two evidence
standards that can never be pooled or compared. Forces amendment of
governance-frozen constants. And is effectively irreversible: once intraday
observations are collected they are permanent evidence.

## 4. Reusable vs amended governance

### 4.1 Reusable UNCHANGED

- `run_date` policy (6B.3): sealed lockbox and immutable-gap refusal, backfill horizon
- Completeness-guard concept (6B.1): incomplete intervals rejected, never partially computed
- Fail-closed / NaN discipline (6B.1 defect B-02): missing data is never a decision
- Provenance stack (6B.6): `framework_hash`, `run_inputs`, partition SHA-256, row counts
- Lockbox seal (2025-06-01..2026-05-31)
- Clone methodology: Pearson on positions + `governance.vif_table`
- Thresholds `max_clone_r` 0.90, `min_dsr_prob` 0.50, `max_bh_p` 0.10, `max_pairwise_r` 0.70
- **`min_active_days` and `min_calendar_years` — provided they stay in CALENDAR units** (§4.3)
- Ledger lane separation: `runs.run_kind` and the frozen `(provenance, run_kind)` lane table (PR-5)

### 4.2 Genuinely REQUIRES amendment

- `hold_days` semantics — a day-denominated constant across all six collectors; cannot express an intraday hold
- `UNIQUE(signal_id, run_date)` on `run_log` and `UNIQUE(signal_id, day)` on `signal_log` — multiple observations per day violate both, and those constraints are load-bearing for idempotency (6B.3) and provenance (6B.6)
- Cost Specification for intraday turnover — gate 6B.5 established two registered Specifications (engine per-turnover; forward per-trade-once); intraday turnover is far higher and cost dominates, so a third must be registered or one explicitly adopted
- Annualisation basis (`ann: 365`) — intraday annualisation needs observations-per-year, not calendar days
- `n_trials` — **only** if a separate intraday battery denominator is chosen (§4.4)
- A non-overlapping-label rule — new; no current equivalent

### 4.3 `min_active_days` / `min_calendar_years` must NOT be silently weakened

These are **regime-coverage** constraints, not sample-size constraints: two years
of BTC spans multiple volatility regimes. Reinterpreting "60 active days" as "60
intraday observations" would collapse a 60-day standard into possibly two days.

**Ruling: they remain calendar-time constraints, in calendar units.** An intraday
candidate must still span ≥2 calendar years and be active on ≥60 distinct
calendar days. This preserves the standard exactly and requires **no amendment**
— only this interpretation note.

### 4.4 `n_trials`

An intraday battery is a new multiple-testing surface and must not be merged
into the frozen 34. Two admissible routes, to be chosen if the lane is ever
opened:

- **(a)** a separate, pre-declared intraday denominator — a new frozen constant, amendment required; or
- **(b)** admit intraday candidates individually at `n_trials = 34`, following the **H-IVRV and G2-family precedent** (registered after the battery, Validated evaluated on forward returns) — **no amendment required**.

Neither is adopted here.

### 4.5 The overlapping-label risk

If observations are frequent but labels span long horizons, labels overlap,
effective sample size falls far below nominal, and **DSR, BH-p and PSR are all
inflated**. This is the single largest statistical-integrity risk of an intraday
lane, and the one most likely to produce a false Validated promotion.

**Ruling: non-overlapping labels — or an explicit, pre-declared overlap
correction — must be settled BEFORE any intraday evidence is collected. It may
never be retrofitted.**

### 4.6 Cadence, if the lane is ever opened

The scope-limiting insight: a **single 01:00 UTC run emitting N observations for
the completed prior day** preserves the run_date policy (6B.3), the 1440-bar
completeness guard (6B.1) and the provenance stack (6B.6) entirely unchanged.
Intraday *collection* runs would reopen all three. Recorded so a future gate does
not assume intraday evidence requires intraday collection.

## 5. Decision

**DAILY-ONLY REMAINS IN FORCE. The intraday lane is DEFERRED, NOT REJECTED.**

The decisive fact is platform maturity. At this date the registry holds
**9 candidate, 22 discovery, 4 disqualified — and 0 validated, 0
promotion_ready**. Forward collection began 2026-06-15 with **54 observations
per signal and 28 completed trades**; `g2_funding_level` has one observation; and
gate 6B.7, the first collection audit under the complete provenance stack, has
not yet run.

**The daily lane has never carried a single family end to end to Validated.**
Opening a second lane — two non-poolable standards, six amendments, an
irreversible new evidence type — before the first has been proven once is
building the second storey before the first is load-tested. The M-6 DECLINE
rejected this reasoning when one strategy asked for it; four strategies asking
is a stronger case, but not sufficient while zero families have completed the
existing pipeline.

**This is a timing judgement, not a merit judgement.** The capability gap is
real and the lane is expected to be right eventually.

## 6. Reopening triggers

Reconsider Option B when **BOTH** hold:

**(a)** At least one family reaches state `validated` in the daily lane, proving
the pipeline end to end; **AND**

**(b)** At least two candidates with **V1-pinned sources** are blocked *solely*
by cadence — not by source access, not by clone risk, not by data availability.

> C5 does **not** currently satisfy (b): it is **V2**, its only open-access copy
> being machine-inaccessible (A11). C1, C4 and Mutanabby are architectural
> context only and do not by themselves satisfy (b).

**Design precondition, independent of both triggers:** the non-overlapping-label
rule (§4.5) must be settled before any intraday collection.

## 7. Non-goals

Out of scope even if the lane is later opened: intraday **execution** or live
trading; a second exchange or asset; tick data; sub-minute granularity; changing
`max_clone_r`, `min_dsr_prob`, `max_bh_p` or `max_pairwise_r`; pooling intraday
and daily evidence; and automatic re-opening of any closed candidate.

## 8. Resting state (in force from this date)

The platform remains **daily-only**.

Intraday **information** remains fully usable with no governance change —
`trend_1h` / `trend_4h` and the A6 1m-derived daily signal are the precedent.
Only intraday **position holding** is excluded.

Mutanabby, C1, C4 and C5 remain closed for a **documented, non-prejudicial
reason: platform scope, not candidate merit.** All four are recoverable if the
§6 triggers fire. No candidate is re-opened by this record.

## 9. Future gate if reopening occurs

**G3 — intraday lane charter.** Order is mandatory: settle the observation unit
and the non-overlapping-label rule **first**, then the cost Specification, then
the schema lane. **No collection until all three are frozen.**

## 10. Governance status at this record

| | |
|---|---|
| `thresholds.yaml` | **unmodified** — read only |
| `registry.yaml` | **unmodified** |
| `collection_config.yaml` | **unmodified** |
| Sealed artifacts, ledger | **unmodified** |
| Production | **unmodified**; nothing deployed |
| Lockbox 2025-06-01..2026-05-31 | **SEALED — not read** |
| Performance statistics | **none computed** |
| `n_trials = 34` | unspent, unchanged |
