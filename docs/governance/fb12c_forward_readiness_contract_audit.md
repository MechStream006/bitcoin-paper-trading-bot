# FB-12c — Forward Readiness Contract & Producer Generalization Audit

**Date: 2026-08-31 (UTC) · Gate FB-12c · Read-only · Verdict: readiness must be
EVIDENCE-WINDOW-bounded, not activation-bounded — because contemporaneous
readiness evidence already exists for every day of every existing L2 window.
Exactly one governance decision blocks everything, and it is about INC-0005.**

No fix applied, no contract activated, no receipt emitted, no scheduler wiring.
`readiness_gate`, `transition_governance`, PR-7, collectors, h6, registry,
thresholds, ledger and pre-registrations untouched. Production was read only.

---

## 1. The finding that reframes the question

FB-12b concluded the readiness-receipt path was unsatisfiable and implied
readiness would have to start at activation. **That framing was wrong, and this
audit withdraws it.**

`runner_base.load_daily_closes()` raises `DataUnavailableError` when the day is
absent, when it has fewer than `MIN_DAILY_BARS = 1440` one-minute bars, **or
when `len < min_rows`** — and `run_daily` passes `min_rows = <family>.MIN_HISTORY`
for every family. `DataUnavailableError` writes **no `run_log` row**.

Therefore, for the seven `run_daily` families:

> **An `ok` `run_log` row on day D is a contemporaneous, fail-closed record that
> D's inputs were complete, fresh, and deep enough for that family's declared
> lookback.**

That is a readiness verdict in all but name, recorded **on the day**, by a
process that refused to write when the inputs were insufficient. The readiness
evidence is not missing. It is already there, in the wrong shape.

**Measured on production** — days in each L2 evidence window lacking a
contemporaneous `ok` row:

| family | window days | ok | missing | INC-0005 | other |
|---|---|---|---|---|---|
| `g1_market_regime_sma200` | 76 | 57 | 19 | **19** | **0** |
| `g3_momentum_tsmom` | 76 | 57 | 19 | **19** | **0** |
| `h5_vol_regime` | 76 | 57 | 19 | **19** | **0** |
| `ivrv_vrp_spread` | 72 | 53 | 19 | **19** | **0** |
| `g3_atr_expansion` | 63 | 44 | 19 | **19** | **0** |

**Zero non-INC-0005 gaps, in any family.** The entire distance between these
five families and a satisfiable readiness assessment is the 19-day INC-0005
outage — an absence that is already permanent, documented and immutable.

---

## 2. Why retroactive probing is forbidden, demonstrated by h6

The gate's principle — *a readiness producer must never manufacture historical
completeness merely because it is being run later* — is not abstract here. It
has a concrete counter-example in this repository.

The parquet probe counts valid funding days **in the lake as it is now**. The
production lake now holds 608 funding days because 19 partitions
(2025-01 … 2026-05) were backfilled on **2026-08-26**. So a probe run today for
period 2026-07-01 would find a full 273-day window and return **`READY`** — for
one of the exact 70 days FB-11 permanently recorded as **`BACKFILL_REQUIRED`**,
when `funding_regime` was contemporaneously `'unknown'` and h6 could not be
evaluated at all.

**Retroactive probing would therefore invert a frozen governance record.** Any
readiness producer must source historical verdicts from contemporaneous records
(`run_log` presence, `regime_states.funding_regime`), never from a present-day
probe of a mutable lake.

---

## 3. Are receipts evidence at the time of the run? — already settled, frozen

`get_readiness(db, feature, period, as_of)` *"selects the LATEST verdict whose
`as_of` is <= T — never the latest verdict outright"*, and
`_from_readiness_receipts` passes `as_of = run["run_ts"]`.

So a receipt written after a run is **structurally invisible** to it. Late
accumulation is writable but unconsumable. **The framework already enforces the
gate's principle at the consumption boundary** — no new rule is needed, and this
question requires no decision.

---

## 4. Two routes, and why the embedded block is the only viable one

| | receipt path (`_from_readiness_receipts`) | run-block path (`_from_run_block`) |
|---|---|---|
| Order | fallback | **checked first, short-circuits** |
| Requires | a per-day receipt, written at the time, `as_of <= run_ts` | a `readiness` block in `runs.metrics_json` |
| Existing windows | **impossible** — 731 day-receipts for elapsed days | **buildable now** from contemporaneous `run_log` |
| Future windows | still impossible: `_first_run_date` sets `window_start = MIN(run_date)`, so every future run spans back to 2026-06-15 | buildable |
| Strength | hash-chained receipt per day, replayable | *"read as recorded — the gate does not recompute it"*; trusts the producer |
| Verdict rule | every day must be `READY` | `ok = not days_non_ready` |

**The embedded block is the cleaner route, and for these five families it is the
only route that can ever work.** The cost is real and must be stated: the block
is trusted, not verified — its integrity rests on the append-only run row and on
the producer being honest, rather than on a per-day hash-chained receipt.

Both paths are usable **without modifying `DefaultReadinessProvider`**. What
blocks is not the mechanism but the policy: `ok = not non_ready`, and INC-0005
guarantees `non_ready` is non-empty.

---

## 5. Activation-bounded or evidence-window-bounded?

**Evidence-window-bounded.** Activation-bounding would be the wrong answer for a
concrete reason: it would declare days 2026-06-15 … 2026-08-30 out of scope even
though contemporaneous evidence for them exists and is good (§1). Discarding
real evidence to make a gate passable is the mirror image of manufacturing it.

Readiness should be assessed over the run's full evidence window, sourced from
contemporaneous records, with days that have no such record reported honestly as
non-ready. Under that rule the five families report **57/76, 57/76, 57/76,
53/72, 44/63 READY**, with the 19 INC-0005 days named explicitly.

Whether that constitutes a pass is §7 — and it is not a decision this audit
takes.

---

## 6. Is the feature-id mismatch a bug or a deeper identity problem?

**Both, and the deeper issue is benign — but it should be decided consciously.**

Three namespaces meet here: `family_id` (evidence library), `feature_id` (dq),
`contract_id` (contract registry). `engine.py:186` collapses two of them —
`feature = feature_id or contract.contract_id` — while `readiness_gate` assumes
`feature_id == family_id`. `Contract.produces` models the distinction correctly
and is never consulted by the default path.

Renaming `CONTRACT_ID` to the family_id makes all three coincide and needs no
framework change. It carries one permanent consequence: **one contract per
family, forever**, since two contracts for one family would need the same id.

That consequence is **aligned with PR-7's design**, not a workaround: a contract
holds `inputs: tuple[InputSpec, ...]` with worst-wins aggregation, so "one
contract, many inputs" is the intended shape and "many contracts, one feature"
is not. The alternative — threading `feature_id` through
`readiness.evaluate_and_record()` — is cleaner in principle but edits a PR-7
core module for no behavioural gain.

---

## 7. THE GOVERNANCE DECISION — and it is only one

Everything above converges on a single unresolved question:

> **Does a declared, immutable, documented provenance gap (INC-0005) render a run
> permanently non-READY, or may readiness be assessed over the assessable window
> with the gap carried explicitly?**

The tension is precise. PR-7's **L2** says *"absence of evidence of completeness
is not evidence of completeness"* — which argues the 19 days block. But INC-0005
is **evidence of absence, not absence of evidence**: the cause, the exact dates,
and the fail-closed behaviour are all documented in CLAUDE.md §20, and the job
demonstrably wrote nothing rather than writing something wrong.

Three options, none chosen here:

- **A — Strict.** Any non-READY day blocks. Consequence: the five families can
  **never** reach Validated on any run, because `window_start = MIN(run_date)`
  guarantees every future run also spans INC-0005. This is a permanent bar, and
  choosing it should be done knowingly.
- **B — Declared-gap tolerance.** A day inside a governance-recorded immutable
  gap is `EXCLUDED` rather than non-ready; readiness is assessed over the
  remainder and the gap is named in the block. Requires a frozen list of
  recognised gaps so tolerance can never be claimed ad hoc.
- **C — Change `window_start` semantics** so a run's window begins after the
  gap. **Rejected on sight**: it would silently discard 33 days of real forward
  evidence per family and rewrite what a run means.

**A and B are both defensible. This audit does not choose.** The choice belongs
to whoever owns the Validated bar, and it determines whether a first Validated
family is reachable at all.

---

## 8. Generalization surface

| component | status |
|---|---|
| `parquet_probe` | **generic** — dataset/symbol/ts/window declared in `source` |
| `_dq()` loader, historical-period refusal, activation/evaluation separation, fail-closed behaviour | **generic** |
| `build_contract_doc()`, `CONTRACT_ID`, `FEATURE_ID`, `MIN_DAILY_FUND` | **h6-specific** — the only such surface |

Each of the seven families already declares its own `MIN_HISTORY`
(`g1_market_regime` = WIN+LAG, `g1_trend_regime` = SLOW, `g3_tsmom` = K+1,
`h5` = RV_WINDOW+REGIME_WINDOW, `ivrv`/`g2_funding`/`c3` = RW+1,
`g3_atr_expansion` = N+LAG+1) and its own input set (spot closes; +DVOL for
ivrv; funding for g2; OHLCV for g3_atr). Generalisation is therefore a
per-family contract built from constants the modules already own — the same
import-don't-restate discipline FB-12a used — plus one function and three
constants parameterised.

**Should h6 be included?** **No, not initially.** h6 has 0 live-forward runs,
lives in a different corpus with a different schema, and is served by
`daily_job`, whose F1/F2/F3 defects are unfixed. Its contract should stay
defined and unactivated; FB-11 already governs its history, and it gains a
readiness need only if it ever trades.

---

## 9. Proposed readiness contract

1. **Scope**: the seven `run_daily` families. h6 defined, not activated.
2. **Bound**: evidence-window-bounded — the run's full `window_start..window_end`.
3. **Source**: **contemporaneous records only** — an `ok` `run_log` row for the
   family on that day. Never a present-day probe of a mutable lake (§2).
4. **Route**: an embedded `readiness` block in `runs.metrics_json`, written by
   the snapshot producer at run-creation time (§4).
5. **Shape**: `days_total`, `days_ready`, `days_non_ready` — the existing
   `_from_run_block` contract, unmodified.
6. **Honesty rule**: days without a contemporaneous record are always reported,
   never omitted. Whether a recorded gap is disqualifying is §7's decision.
7. **The parquet probe's role**: forward-looking only — it establishes readiness
   for days **as they occur**, and is never used to assess a past day.

---

## 10. What existing runs can and cannot satisfy

| | can satisfy? |
|---|---|
| Receipt path, existing 10 L2 runs | **Never** — 731 elapsed day-receipts, retroactive receipts forbidden |
| Receipt path, future runs | **Never** — `window_start = MIN(run_date)` always spans pre-receipt days |
| Run-block path, under option **A** | **Never** — 19 INC-0005 days are permanent |
| Run-block path, under option **B** | **Yes** — 57/76, 57/76, 57/76, 53/72, 44/63 READY with the gap declared |
| h6, any path | Not applicable — 0 live-forward runs |

---

## 11. Exact implementation changes required (not made here)

1. Rename `CONTRACT_ID` to the family id so the `feature = feature_id or
   contract_id` fallback resolves correctly; test that
   `get_readiness(db, family_id, day)` finds it.
2. Parameterise `build_contract_doc()` over family, inputs and `MIN_HISTORY`,
   importing each family's constants rather than restating them.
3. Add a `readiness` block builder that reads **`run_log` presence** for the
   run's window and emits `days_total`/`days_ready`/`days_non_ready`.
4. Wire that builder into `forward_snapshot.write_snapshot()` so the block is
   written when the run is created — the only moment at which it is
   contemporaneous.
5. Schedule the parquet-probe producer daily, forward-only, so future days
   accrue genuine receipts.

**Items 3 and 4 are blocked on §7.** Items 1, 2 and 5 are safe regardless.

---

## 12. Closing

**Is readiness activation-bounded or evidence-window-bounded?**
**Evidence-window-bounded**, sourced contemporaneously. Activation-bounding
would discard good evidence that already exists.

**Smallest governance decision required:** §7 — whether a declared immutable gap
(INC-0005) permanently disqualifies a run, or is excludable with the gap carried
explicitly. Nothing else needs deciding, and nothing can proceed past item 2
without it.

**Recommended next gate: FB-12d — INC-0005 readiness-tolerance decision,
decision-only, read-only.** Resolve §7 options A vs B. If B, freeze the list of
recognised gaps so tolerance can never be claimed ad hoc. Only then implement
items 1–5.

**Stopped at the decision boundary.**

---

## 13. Trail

| Gate | Outcome |
|---|---|
| FB-12a | `parquet_probe` + `h6_completeness.py`, CI green |
| FB-12b | Do not activate: feature-id mismatch; receipt path unsatisfiable |
| **FB-12c** | **This audit. Contemporaneous readiness evidence already exists in `run_log`; only INC-0005 blocks; withdraws FB-12b's activation-bounded framing** |
| FB-12d | Proposed: the INC-0005 readiness-tolerance decision |
