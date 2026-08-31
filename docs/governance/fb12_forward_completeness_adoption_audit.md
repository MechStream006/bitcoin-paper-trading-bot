# FB-12 — Forward Collection Data-Completeness Adoption Audit

**Date: 2026-08-31 (UTC) · Gate FB-12 · Read-only · Verdict: PR-7 is reusable
and needs exactly ONE addition (a `parquet` probe) that the framework itself
anticipates. Adoption belongs at PROVENANCE, never at signal evaluation. The
defect is confined to ONE of the two collectors.**

Nothing implemented or modified: PR-7, forward collectors, h6, funding
ingestion, scheduler, ledger, registry, thresholds, pre-registrations and
production are all untouched. Runtime claims below use fresh production
evidence.

---

## 1. The central finding: two collectors, two different contracts

The forward layer is not one architecture. It is two, with materially different
completeness discipline, and **the defect FB-11 recorded exists in only one.**

| | `run_daily.py` → `btcbot-fwd-hg` | `daily_job.py` → `btcbot-evidence` |
|---|---|---|
| Signals | the 7 collecting families | `h6` only |
| Corpus | `evidence_forward.db` | `forward_evidence.db` |
| Unavailable input | raises `DataUnavailableError` | returns the **string `"unknown"`** |
| What is written | **nothing — no run_log row, no signal row** | a full row: `h6_canonical=0`, `status='ok'` |
| Distinguishable from a real flat? | **Yes** — absence vs. presence | **No** |

`runner_base.py`'s frozen design note states the contract outright:

> *"DataUnavailableError NOT written to run_log (transient; retry is
> automatic). Unexpected exceptions DO write 'error' to run_log."*

That is **already PR-7's L4** — *"the absence of a row is the retry
mechanism"* — implemented informally and correctly. `run_daily` further carries
`MIN_DAILY_BARS = 1440` enforced by strict equality with a measured
justification, `_assert_final_day_complete()`, per-signal `MIN_HISTORY`
constants (g1_market 220, g1_trend 100, c3 253), and NaN guards — all
fail-closed.

**So the audit's scope collapses: `run_daily` does not need PR-7 to become
correct. It needs PR-7 to become *legible*. `daily_job` needs it to become
correct.**

---

## 2. Exactly where the state is lost — three distinct points

### Loss point 1 — `_funding_regime()` returns a value, not a verdict

```python
def _funding_regime(funding_bps) -> str:
    valid = pd.Series(funding_bps).dropna()
    if len(valid) < REGIME_WINDOW:      # 252
        return "unknown"                # <-- a VALUE, flowing into the signal
```

An unevaluable input becomes an ordinary string in the same domain as the real
answers (`low`/`medium`/`high`).

### Loss point 2 — `h6_decide()` treats `unknown` asymmetrically

```python
if (vol_regime == "medium"
        and funding_regime == "low"
        and market_regime not in ("bear", "unknown")):
    return 1
return 0
```

**`market_regime` handles `unknown` explicitly. `vol_regime` and
`funding_regime` do not.** They are tested by positive equality, so `"unknown"`
is indistinguishable from *"computed, and not the required value"*. A
four-valued input collapses into a two-valued output with no third channel.

This asymmetry is the defect in one line. It is also proof the authors knew
`unknown` was a real possibility — they guarded one of the three inputs against
it.

### Loss point 3 — the `error` field is computed and discarded

`compute_h6_decision()` returns an `error` key, set on the insufficient-history
path. **`daily_job.py` never reads it** (its `errors` counter counts raised
exceptions in the day loop, a different thing). So even the *loud* path would
write `h6_canonical=0, status='ok'`.

Note this makes the actual failure the quieter of the two available ones: the
2026-06-16 → 2026-08-26 window took the path where `error` is **`None`**,
because the window had 273 rows — the row count was fine, only the funding
*values* inside it were not.

---

## 3. Exact information currently lost

**One bit, three times over: "was this determined?"**

| layer | records | discriminating? |
|---|---|---|
| `regime_states.funding_regime` | `'unknown'` | **Yes — the only surviving discriminator** |
| `signal_log.h6_canonical` | `0` | No |
| `run_log.status` / `anomalies_json` | `ok` / `[]` | No |
| decision `error` field | computed | Discarded before persistence |

The loss is **partial, not total** — which is exactly why FB-11 could be
written from evidence. But `regime_states` is a sibling table no consumer
reads, so the bit is preserved for archaeology and absent from governance.

For `run_daily` the same bit **is** carried, structurally, as row absence.

---

## 4. Can PR-7 surface this without changing signal calculations?

**Yes — and this is the decisive property.** A readiness verdict is computed
from **input metadata** (row counts, validity counts, bounds, hashes) *before
and independently of* the decision function. PR-7's L3 is explicit: *"probe
metadata, not payload."*

Concretely: "the funding input has 61 valid days in a 273-day window against a
floor of 252" is derivable without calling `_funding_regime`, `h6_decide`, or
any signal code. **No decision function changes. No trading decision changes.**

**And changing them is forbidden anyway.** `decision_function.py` is
hash-pinned by `run_log.fn_hash` and by the pre-registration; altering
`h6_decide` to return a third state would require a new pre-registration and a
new function version. **That constraint alone settles §5.**

---

## 5. Where adoption belongs: provenance, not signal evaluation

| candidate | verdict |
|---|---|
| **Signal evaluation** — teach `h6_decide` a third state | **Excluded outright.** Frozen, hash-pinned, pre-registered |
| **Collection** — gate the write, emit no row when inputs are not READY | **Correct in principle** (it is `run_daily`'s existing behaviour and PR-7's L4) but for h6 it *changes what gets collected*, mid-flight, on a live signal |
| **Provenance** — emit a readiness verdict alongside, leave the row as-is | **Correct starting point.** Adds the missing discriminator, changes nothing collected |

**Recommendation: provenance-first, gating later.** Emitting a verdict is purely
additive and reversible; gating h6's writes is a behavioural change to a live
collector and should be a separate, later decision once verdicts have been
observed in production for a period.

---

## 6. Is legitimate `position = 0` distinguishable from unevaluable?

| | today | after provenance-only adoption |
|---|---|---|
| `run_daily`'s 7 signals | **Yes** — absence vs. row | Yes, plus an explicit verdict |
| `h6` | **No** | **Yes** — verdict is `READY` vs `BACKFILL_REQUIRED` |

Worth recording: `run_daily`'s degradation path has **never fired in
production**. `ivrv_vrp_spread` holds 54 rows over 2026-06-19…2026-08-30 — 73
calendar days minus INC-0005's 19 — so **zero DVOL-unavailable skips have ever
occurred**. The positive precedent is implemented and untested by events.

---

## 7. Historical intervals

**Must remain untouched, and adoption must be forward-only.** No retroactive
verdicts for 2026-06-16 → 2026-08-26 (FB-11 §6; PR-7's L4; the reserved
`NO_RECEIPT` count for "days nobody evaluated"). The 70 rows stay exactly as
collected, governed by FB-11's record rather than by a manufactured verdict.

Adoption changes what is recorded **from its start date onward**, nothing before.

---

## 8. Downstream governance impact

| consumer | impact |
|---|---|
| **Observation floors** (FB-7) | **None.** h6's only floor is `minimum_trades = 30`; the 7 families' floors count trades and active days already correctly represented by row presence |
| **Readiness** (PR-9) | **This is the significant one — see below** |
| **Forward admissibility** (FB-8) | None directly; the payload is built from closed trades |
| **DSR / BH** | None today. Prospectively valuable: a verdict lets a statistical window exclude non-observation days by construction rather than by memory |
| **Pairwise** | Same — window boundaries only |

### The readiness link is a designed integration point, not a coincidence

`readiness_gate.py` already reads **two** sources: the run's own `readiness`
block in `metrics_json`, **and** *"PR-7's `data_readiness` receipts, queried per
day across the run's evidence window via `dq.readiness.get_readiness()` — the
genuine use of the framework."*

`STATES_REQUIRING_READINESS = {"validated"}` is **armed and blocking**, and
**no forward collector writes a readiness block** — the producers that exist
(`dataset_builder`, `build_perp_corpus`) are all corpus-side. So today the
Validated gate blocks every family for want of a producer that was designed but
never built.

**FB-12 adoption would supply exactly that producer**, through the integration
point PR-9 already names. That is the largest governance consequence of this
audit and it is not incidental — it is what `dq.readiness.get_readiness()` was
put in `readiness_gate.py` for.

---

## 9. Smallest safe implementation boundary

Three items. Two are pure additions through the framework's own extension
points; one is data.

1. **A `parquet` probe.** `probes.py` registers `static` and `sqlite` only, and
   its own header says a further *"probe is a natural addition and would live
   here as one more `@register_probe`."* The forward layer's inputs (spot,
   funding, perp, dvol) are parquet, so this is the single genuine code
   addition — and it is an addition **by design**, not a modification of PR-7.
2. **Declared input contracts, as data** (L1: *"declare, don't code"*).
   `ContinuitySpec.min_rows` expresses each signal's existing `MIN_HISTORY`, and
   a funding-validity floor of 252 expresses h6's actual requirement. No new
   spec type is needed.
3. **Emit the verdict as provenance**, per `(signal, run_date)`, via the
   existing `dq.readiness` path. **No gating. No decision-function change. No
   write-suppression.**

**Explicitly outside the boundary:** any edit to `decision_function.py`,
`h6_decide`, `runner_base`'s existing guards, or the schemas of `signal_log` /
`run_log` / `regime_states`; any retroactive verdict; any change to what rows
are written.

Scope is genuinely small because `run_daily` already satisfies the contract
behaviourally — adoption makes its correctness *explicit and queryable*, and
makes `daily_job`'s incorrectness *visible*.

---

## 10. Should this precede FB-10b?

**No — they are independent, and FB-10b is not blocked by this.**

FB-10b (forward DSR/BH) covers the five families with closed trades, all
collected by `run_daily`, whose return series are **already clean**: an
unavailable day produces no row and therefore no trade, so no non-observation
can enter a return series. FB-10b's actual blockers are governance decisions
U1/U2/U3 (FB-10a-R), which this audit does not touch.

Two honest qualifications:

- **FB-12 must precede any h6 statistical work**, since h6 is the one signal
  whose corpus cannot currently distinguish observation from non-observation.
  h6 has no trades, so this is not pressing.
- **FB-12 is on the critical path to Validated**, which FB-10b is not solely.
  Even a perfect DSR/BH result cannot promote a family while PR-9's readiness
  gate blocks for want of a producer (§8). If the goal is a first Validated
  family, FB-12 unblocks a gate that FB-10b alone cannot.

**Recommended ordering: FB-12 and FB-10b are parallel-safe.** If only one
proceeds, FB-12 has the better ratio — it is smaller, its blockers are
technical rather than governance, and it removes a structural blocker that
nothing else addresses.

---

## 11. Closing summary

| question | answer |
|---|---|
| Existing mechanism reusable or extension needed? | **Reusable.** One addition — a `parquet` probe — through the framework's own documented extension point. No change to PR-7's states, policy, contracts or receipts |
| Exact information lost | The single bit *"was this determined?"* — collapsed at `_funding_regime`'s `"unknown"` return, erased by `h6_decide`'s asymmetric handling, and discarded again when `daily_job` ignores the `error` field. Survives only in `regime_states.funding_regime`, which no consumer reads |
| Downstream governance impact | Floors, admissibility, DSR/BH and pairwise: none today. **Readiness: supplies the missing producer for PR-9's armed Validated gate** |
| Smallest safe gate | `parquet` probe + declared input contracts as data + emit verdicts as provenance. No gating, no decision-function change, no retroactive verdicts |
| Precede FB-10b? | **No — independent and parallel-safe.** But FB-12 must precede h6 statistical work, and it is on the critical path to a first Validated family in a way FB-10b is not |

**Stopped at the specification boundary. Nothing implemented.**

---

## 12. Trail

| Gate | Outcome |
|---|---|
| FB-10c-H6 | h6 root cause: 70 days dependency-missing, 5 days legitimate |
| FB-11 pre-gate | PR-7 vocabulary sufficient; documentation-only correct |
| FB-11 | Permanent provenance record; recomputation and retroactive receipts prohibited |
| **FB-12** | **This audit. PR-7 reusable + one parquet probe; adoption at provenance; readiness producer is the prize** |
| (proposed) | FB-12-impl — provenance-only adoption, per §9 |
