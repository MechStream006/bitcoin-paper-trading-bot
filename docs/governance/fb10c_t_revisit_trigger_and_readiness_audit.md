# FB-10c-T — Pairwise Revisit Trigger and Pre-Implementation Readiness Audit

**Date: 2026-08-31 (UTC) · Gate FB-10c-T · Read-only · Outcome: the revisit
trigger is NOT purely time-driven. Two structural findings mean waiting alone
will never make the pre-registered pairs measurable.**

No implementation, no schema change, no correlation computed or stored, no
pre-registration / threshold / registry / scheduler / ledger / production
change. Corpus figures below are **observation counts and dates only**.

Production observed at `run_date` 2026-08-30 (latest collected), files as of
2026-08-31.

---

## 1. Correction to FB-10c-G's projection

FB-10c-G projected *"roughly one coincident observation per pair per week …
n = 10 by early November 2026."* **That projection was too optimistic and is
withdrawn.** It assumed uniform weekly accrual across all pairs. The post-gap
data does not support it:

- `g1_market_regime_sma200` **stopped entering after 2026-08-13** and has been
  flat since 2026-08-20. Its realised post-gap rate is **0.58 entries/week**,
  not ~1.
- `h5_vol_regime` closed its last trade on 2026-08-27 and is **currently
  flat**.
- Three families accrue **zero** coincident observations with the main cohort
  for a structural reason (§2), not a temporal one.

Of the four families that resumed together on 2026-08-06, only
`g3_momentum_tsmom` and `ivrv_vrp_spread` are still in-market as of 2026-08-30.

---

## 2. Structural finding — the collecting universe has split into weekday cohorts

Post-INC-0005 entry weekdays, all seven collecting families:

| cohort | families | post-gap entries |
|---|---|---|
| **Thursday** | `g1_market_regime_sma200`, `g3_momentum_tsmom`, `h5_vol_regime`, `ivrv_vrp_spread` | 08-06, 08-13, 08-20, 08-27 |
| **Wednesday** | `g3_atr_expansion`, `g2_funding_level` | 08-19, 08-26 |
| **Friday** | `g1_trend_regime_ma_cross` | 08-28 |

Post-gap coincident entry dates, all 21 pairs:

| n | pairs |
|---|---|
| 4 | `g3_momentum_tsmom` x `ivrv_vrp_spread` |
| 3 | `g3_momentum_tsmom` x `h5_vol_regime`; `h5_vol_regime` x `ivrv_vrp_spread` |
| 2 | the three `g1_market_regime_sma200` pairs within the Thursday cohort |
| 1 | `g2_funding_level` x `g3_atr_expansion` (both Wednesday) |
| **0** | **all 15 remaining pairs — every cross-cohort pair** |

**Under complete-case coincident-date alignment (FB-10c-D), cross-cohort pairs
accrue at a rate of exactly zero.** Time does not fix weekday misalignment: a
7-day non-overlapping cycle preserves its weekday anchor for as long as the
family stays continuously in-market, so two families on different anchors never
produce a coincident observation.

Anchors re-set only when a family goes flat and later re-enters on whatever day
its signal next fires. That is **stochastic and uncontrolled** — it is how the
Thursday cohort formed (all resumed together after the outage) and it is
already dissolving (`g1_market_regime_sma200` and `h5_vol_regime` are flat).

**Consequence for the revisit trigger: coincident-observation accrual is not a
predictable function of elapsed time.** Any date-based trigger would be a
guess. The trigger must be stated as an *observed-count* condition, checked
against the corpus, never as a calendar date.

---

## 3. Can the pre-registered counterparties ever become measurable?

### 3.1 `h6` — collected, live, and structurally unable to fire

`h6` is **not** a collection gap. `btcbot-evidence.service` writes its corpus
daily (75 rows, 2026-06-16 .. 2026-08-31, file written today). It is
functioning correctly. But:

- `h6_canonical` has exactly **one distinct value across all 75 days: `0`**.
- `h6_shadow` is `NULL` on all 75 days; `in_hold` is `0` on all 75.
- **0 trade entries, 0 exits.** Ever.

The cause is visible in `regime_states`. `h6` is a funding x volatility
interaction requiring both inputs, and the observed joint distribution is:

| vol_regime | funding_regime | days |
|---|---|---|
| low | **unknown** | 54 |
| high | **unknown** | 12 |
| high | high | 5 |
| medium | **unknown** | 4 |

**`funding_regime` is `unknown` on 70 of 75 days (93%)**, including all four
days when `vol_regime` was `medium`. `h6` cannot fire without a known funding
regime, so it has produced — and on current inputs will continue to produce —
**no forward returns at all**.

This traces directly to a **documented, already-gated gap**: CLAUDE.md §11,
*"Dormant: funding_snapshots not wired"* — the live runtime subscribes only
`v2/ticker` over the option universe, with no `funding_rate` channel and no
`BTCUSD` perp, and wiring it is recorded there as *"a capture-path expansion —
gated, do not implement without an explicit decision."*

**So `h6` requires the funding capture-path decision, not a collection gate**,
plus a corpus-integration step (it lives in a different database with a
different schema — `h6_canonical`, not `signal_id`/`position`).

### 3.2 `g5_trend_4h` — genuinely not collected

A registered `candidate` (role `feature`) with **no forward collection of any
kind**. It requires a genuine, separate collection gate before its
pre-registered pair with `g3_momentum_tsmom` can exist.

### 3.3 Answer

**No — not under the current collection universe.** Of the four pre-registered
structural pairs, **none** can become measurable by waiting:

| pre-registered pair | blocker | fixed by time? |
|---|---|---|
| `h5_vol_regime` x `h6` | h6 never fires (funding unknown 93%) | **No** — needs the §11 funding decision |
| `g1_market_regime_sma200` x `h6` | same | **No** |
| `g3_momentum_tsmom` x `g5_trend_4h` | g5 not collected | **No** — needs a collection gate |
| `g1_market_regime_sma200` x `g1_trend_regime_ma_cross` | different cohorts (Thu vs Fri), 0 coincident | **No** — needs cadence realignment, which is uncontrolled |

---

## 4. Can g3 / h5 / g1 be evaluated without violating the two-segment constraint?

Segment-wise only, and the answer differs per pair:

| pair | pre-gap n | post-gap n | segment-wise evaluable? |
|---|---|---|---|
| `g1_market_regime_sma200` x `g3_momentum_tsmom` | 5 | 2 | both segments exist; post is at the n = 2 degeneracy point |
| `g3_momentum_tsmom` x `h5_vol_regime` | **0** | 3 | **post-gap only, permanently** |
| `g1_market_regime_sma200` x `h5_vol_regime` | **0** | 2 | **post-gap only, permanently** |

`h5_vol_regime` entered on Tuesdays before the outage while `g1`/`g3` entered
on Mondays, so **no pre-gap coincident observation exists and none ever can** —
that history is closed. Both h5 relationships are therefore permanently
single-segment. Future accrual can only extend the post-gap segment.

This is a **permanent provenance limitation, not a waiting problem**, and it
must be carried explicitly wherever those pairs are reported (CLAUDE.md §20's
"carry it explicitly in any analysis").

---

## 5. Producer behaviour and schema — both already settled, reaffirmed

**Operate only when the required pairs have sufficient overlap.** The producer
must **not** record non-measurable states. Unchanged from FB-10c-D §4 and
FB-10c-G §3: `append_pairwise_r` refuses a non-real `r` precisely so that
*"an unmeasurable pair cannot masquerade as a measured one"*, and a missing
pair against a Validated peer is **already** a hard failure (schema reference
§4.1). Absence is both honest and already fail-closed; a placeholder row could
only create the appearance of coverage.

**`pairwise_r` must remain untouched** until the minimum n, segment semantics
and status vocabulary are frozen. The extension is permitted and necessary but
**not sufficient** (FB-10c-G §4): `measurement_status` is inert unless
`check_validated_independence` becomes status-aware, and the required
`segment` field has no meaning until segment governance is frozen. Applying
columns now would freeze a vocabulary that three unmade decisions determine.

---

## 6. REVISIT CONTRACT

### REOPEN FB-10c WHEN — all five hold

| # | Condition | Kind |
|---|---|---|
| 1 | `min_pairwise_n` formally frozen in `thresholds.yaml`, and a decision taken on whether `check_validated_independence` enforces it | **Governance** |
| 2 | Segment treatment formally frozen: which segment governs promotion, whether §20's scope extends beyond G3, and how a permanently single-segment pair is reported | **Governance** |
| 3 | Required counterparties have forward evidence — for `h6` the §11 funding capture-path decision **and** corpus integration; for `g5_trend_4h` a collection gate | **Governance, then time** |
| 4 | Required post-gap overlap exists — stated as an **observed coincident count** for a named pair, never as a calendar date (§2) | **Time, and not guaranteed** |
| 5 | Schema and status vocabulary frozen: `as_of`, `window_start`, `window_end`, `n`, `return_basis`, `alignment_method`, `measurement_status`, `segment` — plus authorization for the enforcement change they imply | **Governance** |

### Time-driven versus governance-driven

- **Governance-driven (1, 2, 5):** decidable **today**, at a desk, with no new
  data. Nothing about the corpus prevents closing these now.
- **Governance-then-time (3):** a decision must come first; only then does
  waiting help. Waiting *before* the decision accomplishes nothing.
- **Time-driven but not guaranteed (4):** the only genuinely temporal
  condition, and it is **conditional, not scheduled** — accrual requires both
  families to be simultaneously in-market on a shared weekday anchor, which is
  uncontrolled and has already broken once (§2).

**The headline: four of the five conditions are governance, not time.** The
earlier framing — "wait until December" — was wrong. Waiting is the *smallest*
part of what stands between here and a pairwise measurement.

---

## 7. Useful work that can proceed now, unrelated to pairwise

Ranked by value and independence from evidence accumulation:

1. **`h6` zero-evidence audit (recommended next gate).** `h6` is a registered
   **edge** candidate that has accumulated **75 consecutive days of position 0**
   and can produce no evidence at all while `funding_regime` is unknown 93% of
   the time. It currently *looks* like it is collecting while being
   structurally incapable of producing evidence — the same class of silent
   non-progress as INC-0005, though here the job is behaving correctly.
   Read-only, time-independent, and it is the gating dependency for **two** of
   the four pre-registered pairwise pairs. **This is the highest-value gate
   available today.**
2. **FB-10a U1 / U2 / U3** (`sr_trials_std` population, trade-level scaling,
   BH battery universe). Pure governance decisions, decidable now. They will
   not yield a computation yet — DSR still needs n >= 10/15 — but they are on
   the critical path and are not time-gated.
3. **TD-P1-002 — WAL-aware restore** (§19, Medium). Real engineering with no
   evidence dependency; `restore.py` remains unsafe by default for any
   WAL-mode restore outside the one-off Increment 5 runbook path.
4. **Two uncommitted documents** still in the working tree
   (`deployment-runbook-phase4-…`, `evidence_accumulation_baseline_2026-08-11`).

**Recommended next gate: the `h6` zero-evidence audit (read-only).** It is the
one piece of work that is simultaneously high-value, immediately actionable,
and directly unblocking for the pairwise track.

---

## 8. Summary

| question | answer |
|---|---|
| Earliest defensible trigger | **Not a date.** An observed-count condition on a named pair, plus four governance decisions |
| Post-gap cadence | Thursday cohort of 4 (already dissolving), Wednesday cohort of 2, Friday singleton |
| Cross-cohort accrual | **Exactly zero**, structurally |
| Can all pre-registered counterparties become measurable? | **No** — none by waiting |
| Do h6 / g5 need separate gates? | `g5_trend_4h` yes (collection). `h6` **no** — it needs the §11 funding decision plus corpus integration |
| g3 / h5 / g1 under two-segment rule | Segment-wise only; both h5 pairs are **permanently post-gap-only** |
| Producer on non-measurable pairs | Record nothing; operate only when overlap suffices |
| Schema | **Leave untouched** until n, segment and status semantics are frozen |
| FB-10c implementation | **Still blocked** |

---

## 9. Trail

| Gate | Outcome |
|---|---|
| FB-10c-D | B-1 resolved (complete-case); B-3 opened |
| FB-10c-G | Pooling inadmissible; minimum n needs a frozen constant; implementation not authorized |
| **FB-10c-T** | **This record. Trigger is governance-dominated, not temporal; h6 structurally cannot fire; FB-10c-G's date projection withdrawn** |
| (recommended) | `h6` zero-evidence audit — read-only |
